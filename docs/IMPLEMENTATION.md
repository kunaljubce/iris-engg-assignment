## Configuration and Secrets

The repo commits no credential, not even a dev default. `.env` (copied from `.env.example`) is in `.gitignore`.

**No hand-written connection URL.** `config.py` builds both URLs from the `POSTGRES_*` parts with `sqlalchemy.engine.URL.create()`, which does the encoding. Compose and the harvester read the same variables.

The password never reaches logs: it is a `SecretStr` (masked in `repr()`), `str(URL)` renders it as `***`, and the engine is built with `hide_parameters=True`.

`src/harvester/config.py`:

- `DatabaseSettings`, a pydantic-settings model reading `POSTGRES_HOST`, `_PORT`, `_USER`, `_PASSWORD` (`SecretStr`, required), `_DB` and `_TEST_DB` from the environment or `.env`. Its properties `url` and `test_url` return a `sqlalchemy.engine.URL`. It's covered by `tests/test_config.py`: a password with special characters survives the URL round trip, the password is masked in `str()`/`repr()`, and a missing password fails with a validation error.
- `load_config(path) -> HarvesterConfig`, which validates `config/settings.yml` with pydantic models, one per section. Only checks that stop a config mistake from silently producing wrong data (the "errors are visible" criterion) are included:
  - `extra="forbid"`: a misspelt key (e.g. `keyowrds`) fails instead of leaving the filter empty and the run "succeeding" with zero matches
  - no blank keywords: an empty one would match every title
  - `status_filters` limited to `AKTUELLE` / `BEENDETE`: a typo would skip those plans
  - bounding boxes: exactly 4 values, min below max (a swapped box rejects every point)
  - a bounding box for every city a source covers (else a `KeyError` after the whole scrape)
  - field types (e.g. a quoted `"2"` delay becomes `2.0`)

  `HARVESTER_USER_AGENT` (environment or `.env`) overrides `scraping.user_agent`. `config.model_dump(mode="json")` is what goes into `ingest_run.config_snapshot`. Covered by `tests/test_config.py`.

## Adapter Details

The adapter uses one shared `httpx.Client` (user agent and timeout from config) and goes through `fetch()` for every request (see [Resilience](#resilience)). Parsing is split into pure functions (`str` in, data out) so the tests can call them on fixture text directly.

#### Saxon State Portal Adapter (`sachsen_portal.py`)

Runs once per city in `cities` and status in `status_filters`:

1. GET `{base_url}/portal/{city}/beteiligung/themen?format={format_filter}&status={status}`.
2. `parse_listing(html, page_url, city)`: find `map.drawMarkers([` (the page also calls `drawMarkers(markerData)`) and decode the array with `json.JSONDecoder().raw_decode`, which stops at the end of the array. For each entry:
   - `id` -> `source_record_id` (as a string)
   - `kategorie` -> `title` (`html.unescape`, since the portal escapes quotes as `&quot;`)
   - `href` -> `detail_url` (joined to the listing URL; the city in the path matters, [Key Discovery 5](DESIGN.md#key-discoveries)). Missing `href`: built from the listing's city and the `id`
   - `latitude` / `longitude` -> kept as given; checked in the pipeline
   - `adresse` -> `location_text`
   - `raw_data` = `{"marker": <entry as given>, "listing_url": <page URL>}`
3. `parse_expected_count(html) -> int`: "von N" from `span.ui-paginator-current` when present (more than 10 results), else the number of `div.nbp-teaser` cards. `parse_listing` compares it with the marker count; a difference is a `count_mismatch` error in `ingest_run.errors`, and the markers are still used.
4. The pipeline filters on title. For each **match only**, GET the detail page and `parse_detail(html, record_id)` reads `li.nbp-statistik--laufzeit` ("Zeitraum DD.MM.YYYY bis DD.MM.YYYY"), **only if** the `<body>` has class `nbp-beteiligung-{id}`: listing cards carry the same `li`, so a redirect to a listing would otherwise yield another procedure's dates (found while testing on 2026-10-28). The raw text goes into `raw_data`; the start date becomes `publication_date` (see [Publication Dates](#publication-dates)). A detail page that fails keeps the listing data and gets `detail_page_failed`.

**Empty result:** no `drawMarkers` call **and** no cards -> empty result plus a warning, no exception; the `ingest_run` records 0 scraped. A listing that has cards but no parseable marker JSON is an error (`markers_not_found`), not an empty result: it means the page changed.

**Malformed markers:** an entry without `id` or `kategorie` becomes a `ScrapeError` (counted in `records_rejected`); the others are still parsed.

**Functions:**

- `scrape(client, source: SachsenSource, settings) -> ScrapeResult`: one listing request per city and status from the config, results merged. A listing that fails to fetch becomes a `ScrapeError` (`http_4xx` / `retries_exhausted`); the other listings still run.
- `parse_listing(html, page_url, city) -> tuple[list[ScrapedRecord], list[ScrapeError]]`: markers plus the count check.
- `parse_expected_count(html) -> int`
- `parse_detail(html, record_id) -> str | None`: the raw "Zeitraum ..." text, only from the record's own detail page.
- `fetch_detail(client, record, settings) -> ScrapedRecord`: used by the pipeline for matches only. It lets `FetchError` through; the pipeline turns it into `detail_page_failed`.

#### ScrapedRecord Model

`src/harvester/models.py` holds `ScrapedRecord` and `NormalisedRecord`, the latter with all fields that map to the DB schema.

```python
from typing import Any

from pydantic import BaseModel


class ScrapedRecord(BaseModel):
    source: str  # 'sachsen_portal'
    source_record_id: str  # stable ID from the source
    city: str  # 'dresden' | 'leipzig'
    title: str
    detail_url: str
    location_text: str | None = None
    lon: float | str | None = None  # as given by the source; checked in the pipeline
    lat: float | str | None = None
    zeitraum: str | None = None  # raw "Zeitraum ..." text from the detail page
    raw_data: dict[str, Any]  # all scraped fields as-is
```

#### Adapter Result (`adapters/base.py`)

```python
from dataclasses import dataclass, field

from harvester.models import ScrapedRecord

@dataclass(frozen=True)
class ScrapeError:
    url: str
    kind: str  # 'missing_id' | 'missing_title' | 'markers_not_found' | 'count_mismatch'
    # | 'http_4xx' | 'retries_exhausted'
    message: str
    snippet: str | None = None  # marker JSON entry, truncated to 2 KB

@dataclass
class ScrapeResult:
    records: list[ScrapedRecord] = field(default_factory=list)
    errors: list[ScrapeError] = field(default_factory=list)
```

**No adapter base class.** The adapter is a module with one entry point, `scrape(client, source, settings) -> ScrapeResult`, which the runner calls directly.

## Resilience

- **Retries with exponential backoff:** every request goes through one helper, `fetch()`, which retries on:
  - `httpx.TransportError` (network failures, DNS, connection reset, timeouts)
  - `status >= 500`
  - `429`, honouring `Retry-After` when present

  Any other `4xx` is not retried. It is recorded as a page-level error (e.g. a detail page that 404s gets `quality_flags = ['detail_page_failed']` and keeps its listing data).
- **Redirects:** followed. A detail URL that ends up on a listing page ([Key Discovery 5](DESIGN.md#key-discoveries)) is caught because that page's `<body>` lacks the record's `nbp-beteiligung-{id}` class, so `parse_detail` returns None and the row is flagged `missing_publication_date`.
- **Polite delay:** `request_delay_seconds` (default 2s) before every request.
- **User agent:** Neutral identifying string (`IRIS-SIG-08-Harvester/1.0 (bot)`), overridable via the `HARVESTER_USER_AGENT` env var so an operator can add a contact address without committing it to source.
- **Timeout:** `timeout_seconds` (default 30s) per request.
- **Empty response handling:** A listing with zero results gives an empty list and a logged warning. It does not raise an exception. The `ingest_run` row records the count.
- **Malformed records:** The adapter returns a `ScrapeResult(records, errors)`, not a bare list. The handling depends on how bad the record is:
  - **Unusable** (no stable ID or no title): becomes a `ScrapeError` with the raw snippet (the marker JSON entry) and the page URL. It is written to `ingest_run.errors` and counted in `records_rejected`.
  - **Usable but incomplete** (e.g. unparseable date, missing location, bad coordinates): stored, with the problem recorded in `quality_flags` / `geocode_status`.

  Nothing is silently dropped. The quality report surfaces both kinds.
- **Failed runs:** If the run fails entirely (e.g. the portal is down), its `ingest_run` records `status = 'failed'` and the CLI exits 1. "Fails entirely" means an exception anywhere in the run, or no records at all plus at least one error. Some pages failing while others work is a `completed` run with those errors.

#### `fetch()` (`adapters/fetch.py`)

`fetch(client, url, settings) -> str` applies the rules above. It raises `FetchError(kind, url, status, message)` when retries run out or on a non-retryable 4xx; the adapter turns that into a `ScrapeError`.

- `max_retries: 3` = 3 retries **after** the first attempt (at most 4 requests).
- Waits: `request_delay_seconds` before the first attempt; `retry_backoff_factor ** n` before retry n (2, 4, 8 s). A 429's `Retry-After` in seconds replaces that wait; the HTTP-date form falls back to the backoff.
- `make_client(settings, transport=None)` builds the one `httpx.Client` (user agent, `timeout_seconds`, `follow_redirects=True`). The CLI and the test `mock_client` both use it, passing a `MockTransport` in tests.

## Pipeline

Turns `ScrapedRecord`s into `NormalisedRecord`s: filter (dropping non-matches), fetch the detail page of each match, parse the date, check the coordinates.

- `pipeline/filter.py`: the [Industrial Keyword Filter](DESIGN.md#industrial-keyword-filter). `IndustrialFilter(config.industrial_filter)` takes the validated config section directly.
- `pipeline/geocode.py`: `check_coordinates(lon, lat, city, bbox) -> GeocodeResult` (a NamedTuple of `lon, lat, status, reason`) following the table in [Coordinates and Bounding Box](#coordinates-and-bounding-box). Pure Python; the transform happens in the upsert SQL.
- `pipeline/normalise.py`:
  - `normalise(scraped, client, config) -> NormaliseResult(records, filtered_out)`. It takes the HTTP client because only matches get their detail page fetched.
  - Order per record: filter -> (match only) `fetch_detail` -> parse date -> check coordinates -> `NormalisedRecord`. Non-matches are dropped and counted as `records_filtered_out`.
  - Publication date: the first `DD.MM.YYYY` in the "Zeitraum" text to `datetime.date`, following [Publication Dates](#publication-dates). Unparseable text becomes NULL plus `unparseable_date`.
  - Quality flags: `missing_publication_date`, `missing_location`, `unparseable_date`, `detail_page_failed`. A failed detail page also gets `missing_publication_date` (the date is missing; the other flag says why). `missing_location` means neither a point nor a `location_text`.
  - `raw_data` gains `filter_evidence`, `zeitraum` (raw text) and, if the detail page failed, `detail_error`.

## Coordinates and Bounding Box

The markers supply the coordinates (`latitude` / `longitude`, WGS84). The pipeline sets `geocode_status` per record:

| Case | `geocode_status` | `geometry` | `geocode_reason` |
|---|---|---|---|
| Marker coordinates parse as finite numbers and lie inside the city's bounding box | `source_coordinates` | EPSG:25832 point | NULL |
| Marker coordinates missing or blank (either one) | `unresolved` | NULL | `marker has no coordinates` |
| Coordinates non-numeric, non-finite or outside the box (e.g. lat/lon swapped) | `rejected_out_of_bounds` | NULL | e.g. `lon 51.05 outside dresden [13.55, 13.95]`, `lon 'abc' is not a number` |

**Bounding box rejection:** coordinates are checked in WGS84 against the per-city box from `settings.yml` **before** they reach the database. Rejected rows are stored with `geometry = NULL` and the raw values in `raw_data`, never with the bad point. The check is written as "inside" (`min <= v <= max`), so NaN fails it too. `ck_geometry_matches_status` makes the database refuse a point without `source_coordinates`. Validation check 6 re-checks stored geometries in SQL.

**Transform:** in SQL, `ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 25832)` in the upsert. That keeps pyproj out of the dependencies; the database tests check a control point.

**Note on EPSG:25832:** both cities are east of UTM zone 32N's nominal 12 deg E edge ([Key Discovery 3](DESIGN.md#key-discoveries)). The CRS is still usable there, but scale distortion grows; documented in the README.

## Publication Dates

The brief requires dates to be verified, not assumed. What was verified ([Key Discovery 6](DESIGN.md#key-discoveries)):

- The state portal has no separate "published on" field. The detail page's "Zeitraum A bis B" is the period in which the plan is publicly "veröffentlicht" (BauGB § 3 (2)), stated in the detail text itself. `publication_date` = **A**, the first day it is publicly available.
- It is taken from the detail page, never from the listing card (cards exist for only 10 results per page) and never derived from the run time.
- The raw "Zeitraum ..." text is kept in `raw_data`, so the derivation is traceable per row.
- A detail page without a "Zeitraum" (or one that fails to load) leaves `publication_date` NULL, and `quality_flags` gets `missing_publication_date`.
- Dates that are present but unparseable get `unparseable_date`, with the raw text in `raw_data`.

## Database, Runner and CLI

- `db/engine.py`: `make_engine(url=None)`: sync SQLAlchemy `Engine` (psycopg 3), the harvester database by default, `hide_parameters=True`. `load_database_settings()` turns a missing `POSTGRES_PASSWORD` into `DatabaseConfigError` naming the variable.
- `migrations/env.py` accepts a connection in `config.attributes["connection"]` (the standard Alembic hook), so the tests migrate `iris_test` without touching `iris`.
- `db/upsert.py`: `upsert_records(conn, records, run_id) -> UpsertStats(inserted, updated, duplicates)`, on the caller's connection and transaction; `text()` statements, no ORM. Implements the [Upsert Logic](DESIGN.md#upsert-logic): drop repeated keys (last kept, each one a `duplicate_in_batch` error), then one `INSERT ... ON CONFLICT ON CONSTRAINT uq_source_record DO UPDATE ... RETURNING (xmax = 0)` per record. `ST_Transform` to EPSG:25832 in the statement.
- `runner.py`:
  - `scrape_and_normalise(client, source, config)`: the network half, no database (used by `--dry-run`).
  - `run_source(engine, client, source, config) -> RunSummary`: never raises.
    1. Commit the `ingest_run` row as `running` (with `config_snapshot`).
    2. Scrape + normalise.
    3. In one transaction: upsert, then write the run's counts, errors and final status.
    4. On any exception that transaction rolls back and the run is marked `failed` with `error_message`.
  - One `ingest_run` per invocation. `city` is set when the run covers one city (`--city`), NULL when it covers both.
  - `records_rejected` counts the record-level errors (`missing_id`, `missing_title`); page-level ones are only in `errors`.
- `cli.py` (+ `__main__.py`):
  - `harvester run [--dry-run] [--source NAME] [--city SLUG]` (also `python -m harvester run`). An unknown source or city is a usage error, not an empty run.
  - `--dry-run` prints every scraped record (KEEP with keywords, date, geocode, flags, or `-`) and the scrape errors; it needs no database and never creates an engine.
  - A normal run prints one summary line and exits 1 if the run failed.
  - `harvester report` prints the [quality report](#quality-report).

## Quality Report

`report.py`, run as `harvester report` (also `python -m harvester report`), prints four aligned tables, from the database only:

- **Latest runs**, one row per source (`DISTINCT ON (source) … ORDER BY started_at DESC`): city (`all` when the run covered all the source's cities), status, start in UTC, scraped, industrial, rejected, inserted, updated, errors.
- **Run errors** of those runs: count per error kind with its first message as an example, plus a failed run's message as kind `run_failed`. The full list stays in `ingest_run.errors`.
- **Stored records** per source and city: records, with point, `unresolved`, and `rejected_out_of_bounds` ("rejected bbox"). This covers all rows, i.e. what a consumer of `signal_ingest` sees, not only the latest run's.
- **Quality flags** per source and city.

Aggregates only, each serving the acceptance criterion "errors and missing fields are visible; invalid coordinates are not silently accepted". No notes next to the numbers: the column names are the field names, and the README explains them once (with the live output). Row-level checks (e.g. filter evidence per record) belong in `sql/validation_queries.sql`.

## Validation Queries

`sql/validation_queries.sql` holds 9 checks, each headed by an `\echo` so the output of `docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql` is readable. Checks marked "expect 0 rows" are violations when they return anything.

| # | Check | Criterion it serves |
|---|---|---|
| 1 | Latest run per source: status and counts | errors visible |
| 2 | Run errors by kind, from `ingest_run.errors` | errors visible |
| 3 | Records by source, city, geocode status, with point / date | missing fields visible |
| 4 | Quality flag counts | missing fields visible |
| 5 | Records without a point, with `geocode_reason` | missing fields visible |
| 6 | Points outside the bounding box of the run that wrote them (expect 0) | invalid coordinates not silently accepted |
| 7 | Points in EPSG:25832 and back in WGS84 | valid staging records |
| 8 | Each record with its run, source URL and filter evidence | traceable records; filtering |
| 9 | Stale records: not written by the latest completed run covering their city | traceable records |

Live results on 2026-10-28: the "expect 0" checks return 0 rows. Check 6 was also tested by moving one point to Berlin inside a rolled-back transaction: it returned that row.

## Sample Output

`sample_output/signal_ingest_sample.csv` holds all 9 records from the live run on 2026-10-28, every column of `signal_ingest`. The geometry is written as `x_25832` / `y_25832` (as stored) and `lon` / `lat` (WGS84), and `raw_data` as JSON, so the sample also shows traceability and the filter evidence.

Made with one `COPY (SELECT …) TO STDOUT WITH (FORMAT csv, HEADER)` through `docker compose exec`; the README has the command to regenerate it.

"Anonymized or public-source": the data is public-source. `raw_data` keeps only what the portal publishes (marker, Zeitraum, listing URL, filter evidence); a search for e-mail, phone and contact patterns found nothing to anonymize.
