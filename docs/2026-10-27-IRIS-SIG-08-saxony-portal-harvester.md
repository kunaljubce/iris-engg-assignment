# IRIS-SIG-08 Saxony Citizen Participation Portal Harvester -- Implementation Plan

## Overview

Build a Python harvester that scrapes Bauleitplan (land-use zoning) procedures from the
Saxon state citizen participation portal, filters for industrial/commercial hearings, normalises
the data, and loads it into a PostGIS-backed `iris_staging.signal_ingest` table with
idempotent upserts and full auditability.

**Scope:** Dresden and Leipzig only; surrounding districts deferred.

**This is a sample solution for an exercise, not a production system.** The repo matters for
a few weeks and is demonstrated from a fresh database. Add something only if it serves the
brief's acceptance criteria or makes the demo work. Leave out production hardening for a
future the repo won't have (schema evolution over many migrations, multi-environment
deployment, scale), and name it under "What We Are NOT Doing" so the omission is deliberate.

**Branch:** `main`

---

## Current State Analysis

This is a greenfield repository. No existing code, schema, or configuration files.

### Key Discoveries

All verified against live pages on 2026-10-27 (fixtures in `tests/fixtures/`, provenance in
`SOURCES.md`).

1. **One portal, server-rendered, and everything needed is a plain GET.** Dresden and
   Leipzig are both on the Saxon state portal (`buergerbeteiligung.sachsen.de`, PrimeFaces).
   No browser automation is needed: `httpx` + BeautifulSoup.

2. **The state portal embeds per-procedure coordinates.** Each listing contains an
   OpenLayers call `map.drawMarkers([...])` whose JSON array has one entry per result, **across
   all pages**: `id`, `kategorie` (the title, HTML-escaped), `href`, `latitude`, `longitude`
   (WGS84), `adresse` (street address). All 83 markers captured have coordinates inside the city
   bounding boxes. Geometry comes from the source, not from a lookup table.

3. **EPSG zone mismatch.** Both cities are east of 12 deg E (Leipzig ~12.4, Dresden ~13.7), i.e.
   outside the nominal coverage of EPSG:25832 (UTM 32N, 6--12 deg E); their native zone is
   EPSG:25833. The brief mandates 25832, so points are transformed with PostGIS `ST_Transform`
   and the zone note goes in the README. The bounding-box check catches gross errors.

4. **The status filter is a GET parameter**: `&status=AKTUELLE` (the default view)
   or `&status=BEENDETE` (finished). Uppercase only; lowercase is silently ignored. Cards render
   10 per page behind a PrimeFaces AJAX paginator ("1 - 10 von 52"), but the marker JSON already
   lists all 52, so the adapter reads the markers and never pages. The "von N" count, when
   present, is a completeness check against the marker count.

5. **IDs come from one shared sequence across cities**, so `source_record_id` is
   unique on the portal. The detail URL must use the marker's `href`: a Dresden ID under
   `/portal/leipzig/` silently redirects to the Leipzig listing (HTTP 200).

6. **Publication date = start of the "Zeitraum".** There is no separate "published on" field.
   The detail page's `li.nbp-statistik--laufzeit` ("Zeitraum 12.10.2026 bis 13.11.2026") is the
   period in which the draft is publicly "veröffentlicht" (the detail text says so: "vom ... bis
   ... veröffentlicht"; BauGB § 3 (2)). Its start date is the publication date.

### Portal Details

| Attribute | Saxon State Portal (Dresden + Leipzig) |
|---|---|
| Listing URL | `/portal/{city}/beteiligung/themen?format=Bauleitplan&status={AKTUELLE\|BEENDETE}` |
| Framework | Jakarta Faces / PrimeFaces |
| What is read | `map.drawMarkers([...])` JSON (all results, no paging) |
| Pagination | Not needed (markers list every result) |
| Detail URL | marker `href`: `/portal/{city}/beteiligung/themen/{id}` |
| Stable ID | marker `id` (numeric) |
| Coordinates | marker `latitude` / `longitude` (WGS84) |
| Publication date | detail page `li.nbp-statistik--laufzeit`, start date |
| Items (2026-10-27) | Dresden 8 current + 52 finished; Leipzig 2 current + 21 finished |

---

## Desired End State

- `docker compose up` starts PostgreSQL 16 / PostGIS 3.4.
- `alembic upgrade head` creates `iris_staging` schema, `signal_ingest` + `ingest_run` tables, PostGIS extension.
- `harvester run` scrapes the state portal (per city and status), filters, upserts, and records each run in `ingest_run`.
- `pytest` passes, including idempotent re-run and duplicate detection, with no network access.
- `harvester report` prints the quality report from the database: the latest run with counts and errors, and the stored records by geocode status and quality flag.

### Verification

```bash
# Automated
uv run pytest --tb=short -q
uv run ruff check .
uv run mypy .

# Manual
docker compose up -d
uv run alembic upgrade head
uv run harvester run --dry-run   # prints every record with its filter decision, no DB write
uv run harvester run             # full ingest
uv run harvester report          # quality report
docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql
```

---

## What We Are NOT Doing

- Browser automation (Playwright). Every page needed is a plain GET (Key Discoveries 1, 4).
- Geocoding of any kind (Nominatim, Google, address lookup). The portal's markers supply
  coordinates; a marker without them is stored `unresolved` with the reason.
- Paging through the state portal's AJAX card paginator: the marker JSON lists every result.
- Matching keywords in descriptions. The filter reads titles:
  plan titles name the area type, and descriptions mention "Gewerbe" in passing (neighbouring
  uses, "Gewerbesteuer"), which would add false positives. Detail pages are then fetched only for
  matches, for the publication date.
- Scraping surrounding districts (Meissen, Nordsachsen, Leipzig-Land, etc.).
- Downloading or parsing PDF plan documents.
- Building a web UI or API layer.
- Handling BundID authentication (public pages only).
- Production hardening (see the scope note in the Overview). For example, no SQLAlchemy
  `naming_convention`: it only pays off when later migrations alter or drop constraints by
  name, and this repo is expected to have the single migration `001`. Constraints and
  indexes the code refers to (e.g. `uq_source_record` for `ON CONFLICT`) are named explicitly.
- ORM models (`schema.py`, GeoAlchemy2). Two tables and one migration don't justify a second
  copy of the schema: the SQL DDL in migration `001` is the single source of truth, and the
  `db`-marked tests run every query against it.
- Incremental retrieval (skipping unchanged detail pages). The brief asks for idempotent
  re-runs, not fast ones, and a full live run is ~13 requests (under a minute).
- Change detection (`content_hash`, `last_seen_*` columns, an "unchanged" counter). The
  brief asks for no duplicates on re-run, which the unique key plus `ON CONFLICT` guarantees
  on its own. Nothing consumes "what changed", so existing rows are simply rewritten.
- Storing non-industrial records. The objective is to *filter*; non-matches are only counted
  (`records_filtered_out`). `--dry-run` prints every record with its filter decision for
  tuning the keyword list.
- Columns the brief doesn't ask for (plan type, plan number, procedure step, status,
  participation end date). They are kept as scraped in `raw_data`.
- Secondary indexes. Under 1000 rows, PostgreSQL scans the table faster than it would use an
  index; only the unique index behind `uq_source_record` exists, because `ON CONFLICT` needs it.
- Deleting rows that vanish from the portal or stop matching the filter. They stay, with an
  older `ingest_run_id` (validation check 9).

---



---

## Project Layout

```
iris-engg-assignment/
  pyproject.toml                    # project metadata, dependencies, tool config
  alembic.ini                       # Alembic config (no URL; env.py uses DatabaseSettings)
  docker-compose.yml                # postgis/postgis:16-3.4, credentials from .env
  docker/
    initdb/
      20_create_test_db.sql         # creates iris_test database + postgis extension
  .env.example                      # placeholders only (no secrets committed)
  config/
    settings.yml                   # source and cities, keyword list, bounding boxes
  migrations/
    env.py
    versions/
      001_create_schema_and_tables.py
  src/
    harvester/
      __init__.py
      __main__.py                   # `python -m harvester`: calls cli.main()
      cli.py                        # click CLI; `harvester` console script (pyproject)
      runner.py                     # run_source(): one source end to end, tracked in ingest_run
      report.py                     # quality report from ingest_run + signal_ingest
      config.py                     # pydantic-settings: POSTGRES_* -> DB URL; settings.yml
      models.py                     # ScrapedRecord, NormalisedRecord
      db/
        __init__.py
        engine.py                   # SQLAlchemy engine from DatabaseSettings().url
        upsert.py                   # INSERT ... ON CONFLICT (source, source_record_id)
      adapters/
        __init__.py
        base.py                     # ScrapeResult, ScrapeError (no adapter class hierarchy)
        fetch.py                    # fetch(): httpx GET with retries, status checks, polite delay
        sachsen_portal.py           # state portal: marker JSON + detail page (Dresden + Leipzig)
      pipeline/
        __init__.py
        normalise.py                # ScrapedRecord -> NormalisedRecord, date parsing, quality flags
        filter.py                   # industrial keyword matcher
        geocode.py                  # bounding-box check of source coordinates
  .github/
    workflows/
      ci.yml                        # ruff, mypy, migrations, all tests on PRs and pushes to main
  sql/
    validation_queries.sql          # post-ingest verification
  tests/
    __init__.py
    conftest.py                     # fixture loader, httpx MockTransport, DB fixtures
    db_helpers.py                   # record / run builders for the db tests
    fixtures/
      SOURCES.md                    # provenance: source URL + capture date per fixture
      sachsen_dresden_listing_beendet.html     # marker JSON, "von 52"
      sachsen_dresden_detail.html              # "Zeitraum"
    test_sachsen_adapter.py
    test_fetch.py                   # retry on 5xx/429/timeouts, no retry on 4xx
    test_normalise.py
    test_filter.py
    test_geocode.py
    test_upsert.py                  # includes re-run idempotency + duplicate tests
    test_bounding_box.py            # out-of-bounds rejection + ST_Transform, against PostGIS
    test_pipeline_integration.py    # run_source over fixtures: counts, re-run, errors, crash
    test_cli.py                     # source/city selection, --dry-run without a database
    test_quality_report.py          # latest run, failed run + errors, empty database
  sample_output/
    signal_ingest_sample.csv        # the 9 live records of 2026-10-28
  plans/
    2026-10-27-IRIS-SIG-08-saxony-portal-harvester.md  # this file
  README.md
```

---

## Configuration (settings.yml)

```yaml
sources:
  - name: sachsen_portal
    type: sachsen
    base_url: "https://buergerbeteiligung.sachsen.de"
    cities:
      - slug: dresden
        label: "Landeshauptstadt Dresden"
      - slug: leipzig
        label: "Stadt Leipzig"
    format_filter: "Bauleitplan"
    # One listing request per status; BEENDETE is needed for finished procedures
    status_filters: ["AKTUELLE", "BEENDETE"]

scraping:
  request_delay_seconds: 2.0
  max_retries: 3
  retry_backoff_factor: 2.0
  # Override via HARVESTER_USER_AGENT env var to add an operator contact address
  user_agent: "IRIS-SIG-08-Harvester/1.0 (bot)"
  timeout_seconds: 30.0

industrial_filter:
  # Case-insensitive match anywhere inside a word, so German compounds hit
  # (Gewerbestandort, Kleingewerbe, Logistikzentrum)
  keywords:
    - "Gewerbegebiet"
    - "Industriegebiet"
    - "Gewerbe"
    - "Industrie"
    - "Logistik"
    - "Gewerbepark"
    - "Industriepark"
    - "Technologiepark"
    - "Gueterverkehrszentrum"
    - "Güterverkehrszentrum"
    - "Produktion"
  # Case-SENSITIVE whole-token match: short codes would hit ordinary words inside compounds
  case_sensitive_tokens:
    - "GE"
    - "GI"
    - "GVZ"

bounding_boxes:
  # WGS84 [min_lon, min_lat, max_lon, max_lat]
  dresden: [13.55, 50.95, 13.95, 51.18]
  leipzig: [12.20, 51.25, 12.55, 51.45]
```

---




## Coordinates and Bounding Box

The markers supply the coordinates (`latitude` / `longitude`, WGS84). The pipeline sets `geocode_status` per record:

| Case | `geocode_status` | `geometry` | `geocode_reason` |
|---|---|---|---|
| Marker coordinates parse as finite numbers and lie inside the city's bounding box | `source_coordinates` | EPSG:25832 point | NULL |
| Marker coordinates missing or blank (either one) | `unresolved` | NULL | `marker has no coordinates` |
| Coordinates non-numeric, non-finite or outside the box (e.g. lat/lon swapped) | `rejected_out_of_bounds` | NULL | e.g. `lon 51.05 outside dresden [13.55, 13.95]`, `lon 'abc' is not a number` |

**Bounding box rejection:** coordinates are checked in WGS84 against the per-city box from
`settings.yml` **before** they reach the database. Rejected rows are stored with
`geometry = NULL` and the raw values in `raw_data`, never with the bad point. The check is
written as "inside" (`min <= v <= max`), so NaN fails it too. `ck_geometry_matches_status`
makes the database refuse a point without `source_coordinates`. Validation check 6 re-checks
stored geometries in SQL.

**Transform:** in SQL, `ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 25832)` in the
upsert. That keeps pyproj out of the dependencies; the database tests check a control point.

**Note on EPSG:25832:** both cities are east of UTM zone 32N's nominal 12 deg E edge (Key
Discovery 3). The CRS is still usable there, but scale distortion grows; documented in the
README.

---



---

## Phase 1: Project Skeleton and Database ✅

### Overview

Set up the Python project, Docker environment, config and the Alembic migration (plain SQL
DDL, no ORM models). This phase produces a working `docker compose up` +
`alembic upgrade head` that creates the full schema.

### Changes Required

#### 1. `pyproject.toml` ✅

`uv.lock` and `.python-version` (3.12) are committed, so every checkout resolves the same
interpreter and package versions.

**Sync throughout.** The harvester is a sequential, deliberately throttled
crawler, so it uses:
- a sync `httpx.Client`
- a sync SQLAlchemy `Engine` / `Session` with psycopg 3
- plain synchronous pytest tests (no `pytest-asyncio`)

#### 2. `docker-compose.yml` ✅

#### 3. `.env.example` ✅

The repo commits no credential, not even a dev default. `.env` is already in `.gitignore`.

**No hand-written connection URL.** `config.py` builds both URLs from the `POSTGRES_*` parts with `sqlalchemy.engine.URL.create()`, which does the encoding. Compose and the harvester read the same variables.

The password never reaches logs: it is a `SecretStr` (masked in `repr()`), `str(URL)` renders it as `***`, and the engine is built with `hide_parameters=True`.

#### 4. Alembic setup ✅

#### 5. `src/harvester/config.py` ✅

- **Done:** `DatabaseSettings`, a pydantic-settings model reading `POSTGRES_HOST`, `_PORT`,
  `_USER`, `_PASSWORD` (`SecretStr`, required), `_DB` and `_TEST_DB` from the environment or
  `.env`. Its properties `url` and `test_url` return a `sqlalchemy.engine.URL`. It's covered by
  `tests/test_config.py`: a password with special characters survives the URL round trip, the
  password is masked in `str()`/`repr()`, and a missing password fails with a validation error.
- **Done:** `load_config(path) -> HarvesterConfig`, which validates `config/settings.yml` with
  pydantic models, one per section. Only checks that stop a config mistake from silently
  producing wrong data (the "errors are visible" criterion) are included:
  - `extra="forbid"`: a misspelt key (e.g. `keyowrds`) fails instead of leaving the filter
    empty and the run "succeeding" with zero matches
  - no blank keywords: an empty one would match every title
  - `status_filters` limited to `AKTUELLE` / `BEENDETE`: a typo would skip those plans
  - bounding boxes: exactly 4 values, min below max (a swapped box rejects every point)
  - a bounding box for every city a source covers (else a `KeyError` after the whole scrape)
  - field types (e.g. a quoted `"2"` delay becomes `2.0`)

  `HARVESTER_USER_AGENT` (environment or `.env`) overrides `scraping.user_agent`.
  `config.model_dump(mode="json")` is what goes into `ingest_run.config_snapshot`. Covered by
  `tests/test_config.py`.
- **Changed after the Phase 2 captures:** `scraping.headless` and `page_timeout_ms` removed
  (Playwright-only), `timeout_seconds: float` added.
- `rows_per_page` is not in the config: the page size is a property of the portal, and the
  marker JSON lists every result anyway.

### Choices You May Be Asked About

- **Why PostGIS in Docker instead of a local install?** One `docker compose up` gives the
  exact versions the brief names (PostgreSQL 16 / PostGIS 3.4) on any machine.
- **Why `platform: linux/amd64`?** The official `postgis/postgis` image is amd64-only; on
  Apple Silicon it runs under emulation. Fine for <1000 rows, and stated so it isn't a surprise.
- **Why mount the single init SQL file, not a folder?** A folder over
  `/docker-entrypoint-initdb.d` hides the image's own `10_postgis.sh`, so `iris` would get no
  PostGIS.
- **Why a separate `iris_test` database?** The db tests truncate and rewrite tables; they must
  never touch the data a demo run produced.
- **Why no `DATABASE_URL` in `.env`?** It repeated the password and broke on `@ : / %` unless
  encoded by hand. `URL.create()` builds it from the `POSTGRES_*` parts compose already uses.
- **How does the password stay out of logs?** `SecretStr` masks `repr()`, `str(URL)` shows
  `***`, and the engine uses `hide_parameters=True`.
- **Why hand-written SQL migrations, no ORM / autogenerate?** There are no ORM models, so
  autogenerate has nothing to compare. The DDL in migration 001 is the single source of truth
  and reads like the schema in this plan.
- **Why one `op.execute()` per statement?** An error points at the failing statement.
- **Why the Alembic version table in `iris_staging`?** A default `public.alembic_version`
  would collide with other Alembic projects in a shared database.
- **Why does `downgrade()` keep the `postgis` extension and the schema?** A migration only
  removes what it alone created; other schemas may use PostGIS, and the schema holds Alembic's
  own table.
- **Why was migration 001 edited instead of adding 002?** It was never deployed anywhere; the
  only database is the local demo one, rebuilt with `downgrade base && upgrade head`.
- **Why no secondary indexes?** At <1000 rows a sequential scan is faster; the only index is
  the one `ON CONFLICT` needs (`uq_source_record`).
- **Why `ck_geometry_matches_status`?** It makes the database itself refuse a point without
  `source_coordinates`, or that status without a point ("invalid coordinates are not silently
  accepted").
- **Why `extra="forbid"` on the config models?** A misspelt key (e.g. `keyowrds`) would
  otherwise be ignored, leaving the filter empty and the run "succeeding" with zero matches.
- **Why these config validators and no others?** Each one stops a mistake that would silently
  produce wrong data (blank keyword matches everything, swapped bounding box rejects every
  point, missing box fails only after the whole scrape).
- **Why sync, not async?** It's a sequential, deliberately throttled crawler; async adds
  complexity and gains nothing.
- **Why `uv` and a committed `uv.lock`?** Every checkout resolves the same interpreter and
  package versions.

---

## Phase 2: Adapters and HTML Parsing ✅

### Overview

Implement `fetch()` and the state portal adapter (httpx + BeautifulSoup), producing `ScrapedRecord`
instances. Test with saved HTML fixtures (no live network).

### Changes Required

#### 1. `src/harvester/models.py` ✅

Pydantic `ScrapedRecord` model (as specified above). Also `NormalisedRecord` with all
fields that map to the DB schema.

#### 2. `src/harvester/adapters/base.py` ✅

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

**No adapter base class.** The adapter is a module with one entry point,
`scrape(client, source, settings) -> ScrapeResult`, which the runner calls directly.

#### 3. `src/harvester/adapters/fetch.py` ✅

`fetch(client, url, settings) -> str` with the retry and status rules from **Resilience**. It
raises `FetchError(kind, url, status, message)` when retries run out or on a non-retryable
4xx; the adapter turns that into a `ScrapeError`.

- `max_retries: 3` = 3 retries **after** the first attempt (at most 4 requests).
- Waits: `request_delay_seconds` before the first attempt; `retry_backoff_factor ** n` before
  retry n (2, 4, 8 s). A 429's `Retry-After` in seconds replaces that wait; the HTTP-date form
  falls back to the backoff.
- `make_client(settings, transport=None)` builds the one `httpx.Client` (user agent,
  `timeout_seconds`, `follow_redirects=True`). The CLI and the test `mock_client` both use it,
  passing a `MockTransport` in tests.

#### 4. `src/harvester/adapters/sachsen_portal.py` ✅

- `scrape(client, source: SachsenSource, settings) -> ScrapeResult`: one listing request per
  city and status from the config, results merged. A listing that fails to fetch becomes a
  `ScrapeError` (`http_4xx` / `retries_exhausted`); the other listings still run.
- `parse_listing(html, page_url, city) -> tuple[list[ScrapedRecord], list[ScrapeError]]`:
  markers plus the count check.
- `parse_expected_count(html) -> int`
- `parse_detail(html, record_id) -> str | None`: the raw "Zeitraum ..." text, only from the
  record's own detail page.
- `fetch_detail(client, record, settings) -> ScrapedRecord`: used by the pipeline for matches
  only. It lets `FetchError` through; the pipeline turns it into `detail_page_failed`.

#### 5. Test fixtures ✅

**One real page per markup the code parses**; every edge case is a minimal HTML snippet
inside its test, so the test shows exactly what is wrong with the input. Captured 2026-10-27,
**once, by hand** with `curl` (harvester user agent, 2 s apart), kept whole; provenance in
`tests/fixtures/SOURCES.md`, which covers "source access ... must be verified". It also lists
the pages checked but not kept (other cities and statuses use the same markup). The tests' reproducibility comes from the committed files,
not from how they were downloaded.

- `sachsen_dresden_listing_beendet.html`: 52 markers (5 industrial, titles with `&quot;`),
  10 cards, "von 52"
- `sachsen_dresden_detail.html`: "Zeitraum 12.10.2026 bis 13.11.2026"

Inline snippets: empty listing, cards without marker JSON, marker without `id`, swapped / empty
/ non-numeric coordinates, "von 52" against 51 markers, and HTTP errors via `MockTransport`.

#### 6. `tests/conftest.py` ✅ (DB fixtures follow in Phase 4)

- `tests/__init__.py` makes `tests` a package, so test modules import `load_fixture` and
  `MockClient` from `tests.conftest` and mypy sees one module, not two
- `load_fixture(name) -> str`
- `mock_client(routes: dict[str, str | int])`: `make_client()` with an `httpx.MockTransport`
  that serves fixture text (or a status code) per URL and **fails the test on any unrouted
  URL**, so tests can never reach the live portal. No extra dependency (MockTransport is part
  of httpx).
- `scraping_config` fixture: no polite delay and no retries, so adapter tests run instantly.

#### 7. `tests/test_sachsen_adapter.py` ✅

```python
def test_parses_markers():
    """id, title, city-correct detail_url, lon/lat, address, raw_data from the fixture."""


def test_listing_has_all_results():
    """52 markers although only 10 cards are rendered; matches 'von 52'."""


def test_unescapes_title():
    """Snippet: '&quot;Am Werk&quot;' -> '"Am Werk"'."""


def test_count_mismatch_logged():
    """Snippet: 'von 3' but 2 markers -> count_mismatch error, markers still returned."""


def test_parses_zeitraum_from_detail():
    """'Zeitraum 12.10.2026 bis 13.11.2026' from the detail fixture."""


def test_redirect_to_listing_gives_no_zeitraum():
    """The listing fixture has Zeitraum items in its cards; they must not be taken."""


def test_other_records_detail_page_gives_no_zeitraum():
    """Detail page of 1069133 read for record 1066719 -> None."""


def test_handles_empty_listing():
    """Snippet: no markers, no cards -> no records, no errors, no exception."""


def test_cards_without_markers_is_an_error():
    """Snippet: cards present but no marker JSON -> markers_not_found error."""


def test_handles_malformed_markers():
    """Snippet: marker without id, marker with blank title -> ScrapeErrors with snippet;
    the others still parsed."""


def test_scrape_reads_every_city_and_status():
    """mock_client: one listing served, one 404 -> its records plus one http_4xx error."""


def test_fetch_detail_adds_zeitraum(): ...
def test_fetch_detail_failure_raises(): ...
```

#### 8. `tests/test_fetch.py` ✅

```python
def test_retries_on_503_then_succeeds(): ...
def test_retries_on_timeout(): ...
def test_honours_retry_after_on_429(): ...
def test_no_retry_on_404(): ...
def test_raises_fetch_error_when_retries_exhausted(): ...
```

### Choices You May Be Asked About

- **Why httpx and no browser (Playwright)?** The live checks on 2026-10-27 showed every page
  needed is a plain GET with the data in the HTML, including the state portal's map markers.
  A browser would add a heavy dependency and a slower, flakier test setup for nothing.
- **Why use the marker JSON instead of the listing cards?** `map.drawMarkers([...])` holds
  *all* results with coordinates in one response; cards show only 10 per page. No paging is
  needed, and the "von N" count / card count is checked against it (`count_mismatch`).
- **Why use the marker `href` for the detail URL?** A wrong city in the path redirects to the
  listing page instead of failing; the marker's own link is always right.
- **Why fetch detail pages only for filter matches?** Only the detail page has the
  "Zeitraum"; fetching it for non-matches would be wasted requests on a public portal.
- **Why only 2 fixture files?** One real page per markup the code parses. Edge cases (empty
  listing, malformed marker, count mismatch, HTTP errors) are small inline
  snippets in the tests. `tests/fixtures/SOURCES.md` records where each page came from and
  what the dropped pages showed.
- **Why `Field(default_factory=list)` and not `= []`?** Pydantic copies `[]` defaults, so it
  isn't a bug there, but the org rule forbids mutable defaults and `default_factory` is
  correct everywhere (dataclasses too).
- **Why is `geocode_status` a `Literal`?** It mirrors the migration's CHECK, so a typo fails in
  Python with a clear error before the INSERT.
- **Why are `lon`/`lat` `float | str | None` on `ScrapedRecord` but `float | None` on
  `NormalisedRecord`?** The scraped record keeps values as the source gives them; turning them
  into checked floats is the pipeline's job (Phase 3).
- **Why no validators for "point only with `source_coordinates`" or "at least one keyword"?**
  The database CHECKs already enforce both; repeating them in Python adds code to defend and
  nothing else.
- **Why are `ScrapeError` / `ScrapeResult` dataclasses, not pydantic?** They're built only by
  our own code, so there's nothing to validate. `dataclasses.asdict()` turns an error into
  JSON for `ingest_run.errors`.
- **Why is `ScrapeError` frozen?** It records something that happened; nothing should change it
  afterwards.
- **Why is `ScrapeError.kind` a plain `str`?** No database CHECK constrains it, unlike
  `geocode_status`; the allowed values are listed in a comment.
- **Why no `BaseAdapter` class?** There is one source, the one the brief names. An abstract
  base pays off when implementations are swapped or added; here it would only wrap one
  function. The runner calls `sachsen_portal.scrape()` directly.
- **Why does `scrape()` take the `httpx.Client` as a parameter?** So tests can pass a client
  backed by `MockTransport`. The adapter never creates its own client.
- **Why read the marker JSON with `raw_decode` instead of a regex?** A regex like
  `\[.*?\]\);` would stop early if a title ever contained `]);`. `raw_decode` parses exactly
  one JSON value and stops where it ends.
- **Why does `parse_detail` check the body class `nbp-beteiligung-{id}`?** Found in testing:
  listing cards carry the same Zeitraum element, so a detail URL redirected to a listing
  (HTTP 200) would have given the row another procedure's dates. Now it gets no date and is
  flagged `missing_publication_date`: wrong data is worse than missing data.
- **Why does the count check compare with the cards or "von N"?** The marker JSON is the data
  source; the number the page itself shows is an independent check that no results were lost.
  A mismatch is recorded, not fatal: the markers are still the most complete list.
- **Why is "cards but no markers" an error, but "no cards, no markers" not?** No cards means
  the portal has no results, which is normal. Cards without markers means the page layout
  changed and the adapter would silently return nothing.
- **Why keep coordinates as given and check them later?** The adapter only reads; deciding
  what is valid (bounding box, NaN) is one rule in the pipeline. Values of the
  wrong JSON type (null, list) count as missing, with the original kept in `raw_data`.
- **Why does `fetch_detail` let `FetchError` through?** A failed detail page isn't a scrape
  error: the record is still usable. The pipeline keeps it and adds `detail_page_failed`.
- **Why `source = "sachsen_portal"` as a module constant, not from the config `name`?** It is
  the identifier stored in the database's natural key; it must not change if someone renames
  the config entry.
- **Why `tests/__init__.py`?** It lets test modules import helpers from `tests.conftest`
  without mypy seeing the file under two module names.
- **Why return errors instead of raising?** One bad marker must not stop the run; bad
  entries are collected next to the good ones ("errors are visible").
- **Why `SNIPPET_MAX_CHARS = 2048`?** Enough to see what was wrong with an entry without
  bloating `ingest_run.errors`; one named constant instead of a bare 2048.
- **Why one `fetch()` function for every request?** Listings and detail pages need the same
  retry, 429 and delay rules; written once, tested once (`tests/test_fetch.py`).
- **Why no retry library (tenacity) or httpx's `retries=`?** httpx's built-in retries cover
  connection failures only, not 5xx or 429. tenacity would be a dependency to replace a short
  loop that can be explained line by line.
- **Which errors are retried?** Network errors and timeouts, 5xx and 429 can go away on their
  own. Any other 4xx (404, 403) won't, so it fails at once and is recorded.
- **What does `max_retries: 3` mean?** 3 retries after the first try, so at most 4 requests per
  URL, waiting 2, 4, 8 s between them: about 14 s worst case per URL.
- **Why honour only the seconds form of `Retry-After`?** The portal has never sent a 429;
  parsing the date form would be code for a case never seen. It falls back to the backoff.
- **Why does a retry wait only the backoff, not backoff plus polite delay?** The backoff is
  already at least as long; adding both makes the waits harder to reason about and test.
- **Why `follow_redirects=True`?** The state portal redirects a wrong-city detail URL to the
  listing page; following it lets `parse_detail` see the page isn't the record's own and flag
  the row, instead of failing on a 302.
- **Why `make_client()`?** One place sets the user agent, timeout and redirects. Tests build
  their client with it too (passing a `MockTransport`), so they run with production settings.
- **How do the tests avoid real sleeps?** They replace `time.sleep` in `fetch.py` with a list
  recorder: tests run instantly and can assert the exact waits (e.g. `Retry-After: 5` -> 5 s).

### Success Criteria

#### Automated Verification
- [x] `pytest tests/test_sachsen_adapter.py tests/test_fetch.py` -- all pass
- [x] `ruff check src/harvester/adapters/` passes
- [x] `mypy src/harvester/adapters/ src/harvester/models.py` passes

#### Manual Verification
- [x] Fixture HTML files are whole, unedited captures of the live portal (2026-10-27)
- [x] `tests/fixtures/SOURCES.md` lists every fixture and the pages checked but not kept
- [x] Status-filter mechanism, publication-date element and ID scope confirmed (Key Discoveries 4-6)
- [x] `ScrapedRecord` fields match expectations for each fixture (checked by hand against the pages, 2026-10-28)
- [x] Live smoke run of `scrape()` (2026-10-28): 83 records (Dresden 60, Leipzig 23), no
      errors; a live Leipzig detail page gave its Zeitraum (body-class check holds for
      Leipzig too)

**Checkpoint:** Pause for confirmation before proceeding to Phase 3.

---

## Phase 3: Pipeline (Filter, Detail Dates, Geocode, Normalise) ✅

### Overview

Turn `ScrapedRecord`s into `NormalisedRecord`s: filter (dropping non-matches), fetch the
detail page of each match, parse the date, check the coordinates.

### Changes Required

#### 1. `src/harvester/pipeline/filter.py` ✅

Industrial keyword filter as specified above. `IndustrialFilter(config.industrial_filter)`
takes the validated config section directly.

#### 2. `src/harvester/pipeline/geocode.py` ✅

- `check_coordinates(lon, lat, city, bbox) -> GeocodeResult` (a NamedTuple of `lon, lat,
  status, reason`) following the table in **Coordinates and Bounding Box**. Pure Python; the
  transform happens in the upsert SQL.

#### 3. `src/harvester/pipeline/normalise.py` ✅

- `normalise(scraped, client, config) -> NormaliseResult(records, filtered_out)`. It takes the
  HTTP client because only matches get their detail page fetched.
- Order per record: filter -> (match only) `fetch_detail` -> parse date -> check
  coordinates -> `NormalisedRecord`. Non-matches are dropped and counted as
  `records_filtered_out`.
- Publication date: the first `DD.MM.YYYY` in the "Zeitraum" text to `datetime.date`,
  following **Publication Dates**. Unparseable text becomes NULL plus `unparseable_date`.
- Quality flags: `missing_publication_date`, `missing_location`, `unparseable_date`,
  `detail_page_failed`. A failed detail page also gets `missing_publication_date` (the date is
  missing; the other flag says why). `missing_location` means neither a point nor a
  `location_text`.
- `raw_data` gains `filter_evidence`, `zeitraum` (raw text) and,
  if the detail page failed, `detail_error`.

#### 4. Tests

```python
# tests/test_filter.py
def test_matches_gewerbegebiet_in_title():
def test_matches_compounds():  # Gewerbestandort, Kleingewerbe, Logistikzentrum, Industriepark
def test_evidence_contains_full_compound_word():
def test_matches_GE_case_sensitive():
def test_no_false_positive_on_lowercase_ge():  # "gegen" should not match
def test_short_codes_not_matched_inside_words():  # "GIESSEN", "GEMEINDE" -> no match
def test_no_match_returns_false():
def test_keywords_from_config():

# tests/test_geocode.py
def test_in_bounds_is_source_coordinates():
def test_missing_coordinates_unresolved():
def test_rejects_out_of_bounds():
def test_rejects_swapped_lat_lon():
def test_rejects_non_numeric_and_non_finite():  # "abc", "13,76", NaN, inf

# tests/test_normalise.py
def test_publication_date_is_zeitraum_start():
def test_unparseable_date_flagged_not_raised():
def test_marker_without_coordinates_or_address_is_unresolved():  # missing_location
def test_detail_fetched_only_for_matches():      # mock client sees no request for non-matches
def test_detail_page_404_flagged():               # detail_page_failed, listing data kept
def test_out_of_bounds_marker_is_rejected():      # swapped lat/lon end to end
def test_non_industrial_records_dropped_and_counted():
```

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why does the filter run before the detail page is fetched?** Only matches cost a second
  request: on the Dresden fixture 5 of 52 records, instead of all 52. (Step 1, built)
- **What does the filter miss?** Plans whose title says nothing industrial (Nr. 422
  Radefelder Allee West). Reading descriptions would catch them but adds false positives; see
  "What We Are NOT Doing". (Step 1, checked on the fixtures)
- **Why do some rows list both "Gewerbegebiet" and "Gewerbe"?** Long keywords match inside words,
  so the general one hits too. Which rows are stored only depends on whether anything matched.
  (Step 1, built)
- **Why does `normalise` take the HTTP client?** The detail page is the only source of the
  date, and it's fetched for matches only (9 requests live instead of 83). (Step 3, built)
- **Why is a failed detail page a flag, not an error that drops the record?** The listing data
  (title, URL, point) is still valid; dropping the record would hide a real hearing. The flag
  and `raw_data.detail_error` make the gap visible. (Step 3, built)
- **Why match on the title only, not descriptions?** Descriptions mention
  neighbouring commercial areas and produce false positives.
- **Why substring matching for long keywords but whole tokens for GE/GI/GVZ?** German compounds
  ("Gewerbestandort") need substring hits; short codes as substrings would hit ordinary words
  ("gegen").
- **Why is a missing coordinate `unresolved` but a bad one `rejected`?** Missing means the
  source gave no location, which is normal. A value that is there but not a number or outside
  the box means the source data is wrong, and that should stand out in the report. (Step 2,
  built)
- **Why not fix swapped lat/lon automatically?** Guessing would store a point nobody can vouch
  for; the brief says invalid coordinates must not be silently accepted. The raw values stay
  in `raw_data`. (Step 2, built)
- **Why no geocoding of addresses?** The brief asks for points "where the source provides
  sufficient information", and the markers carry coordinates. An external geocoder adds a
  dependency and its own errors; see "What We Are NOT Doing". (Step 2)
- **Why check the bounding box in Python, before the database?** A bad point is never written;
  the row is stored with `geometry = NULL`, `rejected_out_of_bounds` and the reason.
- **Why is the check written as "inside" (`min <= v <= max`)?** NaN fails every comparison, so
  it's rejected too.
- **Why is `publication_date` the start of "Zeitraum"?** That's the first day the plan is
  publicly available (BauGB § 3 (2)); the raw text stays in `raw_data`.

### Success Criteria

#### Automated Verification
- [x] `pytest tests/test_normalise.py tests/test_filter.py tests/test_geocode.py` -- all pass
- [x] `ruff check src/harvester/pipeline/` passes
- [x] `mypy src/harvester/pipeline/` passes

#### Manual Verification
- [x] Filter decisions on the fixtures look right (no obvious misses or false hits). Checked
  with a script, since `--dry-run` arrives in Phase 4: Dresden 5 of 52
- [x] Live run of scrape + normalise (2026-10-28): 83 scraped, 9 kept (7 Dresden, 2 Leipzig),
  all with a detail-page date and `source_coordinates`, no flags, no errors

**Checkpoint:** Pause for confirmation before proceeding to Phase 4.

---

## Phase 4: Upsert, CLI, and Integration ✅

### Overview

Wire the adapter to the pipeline and the database. Implement the CLI entry point, upsert logic,
and integration tests including idempotent re-run and duplicate detection.

### Changes Required

#### 1. `src/harvester/db/engine.py` + db test fixtures ✅

- `make_engine(url=None)`: sync SQLAlchemy `Engine` (psycopg 3), the harvester database by
  default, `hide_parameters=True`. `load_database_settings()` turns a missing
  `POSTGRES_PASSWORD` into `DatabaseConfigError` naming the variable.
- `migrations/env.py` accepts a connection in `config.attributes["connection"]` (the standard
  Alembic hook), so the tests migrate `iris_test` without touching `iris`.
- `conftest.py`: a session fixture migrates `iris_test` to head (skips if the settings are
  missing, fails if they're present but the database is unreachable); `db_engine` empties
  both tables before each test. `--dry-run` never creates an engine.

#### 2. `src/harvester/db/upsert.py` ✅

- `upsert_records(conn, records, run_id) -> UpsertStats(inserted, updated, duplicates)`, on
  the caller's connection and transaction; `text()` statements, no ORM.
- Implements **Upsert Logic**: drop repeated keys (last kept, each one a `duplicate_in_batch`
  error), then one `INSERT ... ON CONFLICT ON CONSTRAINT uq_source_record DO UPDATE ...
  RETURNING (xmax = 0)` per record. `ST_Transform` to EPSG:25832 in the statement.

#### 3. `src/harvester/runner.py` ✅

- `scrape_and_normalise(client, source, config)`: the network half, no database (used by
  `--dry-run`).
- `run_source(engine, client, source, config) -> RunSummary`: never raises.
  1. Commit the `ingest_run` row as `running` (with `config_snapshot`).
  2. Scrape + normalise.
  3. In one transaction: upsert, then write the run's counts, errors and final status.
  4. On any exception that transaction rolls back and the run is marked `failed` with
     `error_message`.
- One `ingest_run` per invocation. `city` is set when the run covers one city (`--city`),
  NULL when it covers both.
- `records_rejected` counts the record-level errors (`missing_id`, `missing_title`);
  page-level ones are only in `errors`.

#### 4. `src/harvester/cli.py` (+ `__main__.py`) ✅

- `harvester run [--dry-run] [--source NAME] [--city SLUG]` (also `python -m harvester run`).
  An unknown source or city is a usage error, not an empty run.
- `--dry-run` prints every scraped record (KEEP with keywords, date, geocode, flags, or `-`)
  and the scrape errors; it needs no database.
- A normal run prints one summary line and exits 1 if the run failed.
- `harvester report` came with the report itself in Phase 5.

#### 5. Tests ✅

```python
# tests/test_upsert.py  (db)
def test_insert_new_records():
def test_rerun_creates_no_duplicates():        # 0 inserted, N updated; ids, created_at unchanged
def test_rerun_overwrites_changed_content():   # same id, new title, new ingest_run_id
def test_duplicate_keys_in_one_batch():        # one row (the last), duplicate_in_batch
def test_filter_config_change_updates_keywords():
def test_same_id_from_two_sources_is_two_rows():

# tests/test_bounding_box.py  (db)
def test_out_of_bounds_stored_with_null_geometry():
def test_in_bounds_stored_with_geometry():      # SRID 25832
def test_transform_control_point():             # Frauenkirche, pyproj value, 1 m tolerance

# tests/test_pipeline_integration.py  (db, mocked portal)
def test_full_pipeline_fixture_to_db():         # 52 -> 5, dates, flags, traced run
def test_full_pipeline_twice_is_idempotent():   # (0, 5); same ids
def test_errors_visible_in_ingest_run():        # missing_title in errors, records_rejected
def test_unreachable_source_is_a_failed_run():  # 503 -> failed, error_message
def test_crash_rolls_back_records_and_fails_run():

# tests/test_cli.py
def test_select_by_city_narrows_state_portal():
def test_unknown_source_or_city_is_an_error():
def test_dry_run_prints_decisions_without_database():
```

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **What does CI check, and when?** `.github/workflows/ci.yml` runs on every pull request to
  main and every push to main:
  ruff, mypy, the migrations (up, down, up) and the whole pytest suite, including the db tests
  against a `postgis/postgis:16-3.4` service container. The README badge shows the latest
  result on main (green "passing" / red "failing"). (Built before Phase 4)
- **Why trust auth in CI rather than a password secret?** The database is a throwaway container
  on the runner, so there is no credential to store or leak; `POSTGRES_PASSWORD` is set only
  because `DatabaseSettings` requires a value. (Built before Phase 4)
- **What does one `ingest_run` row cover?** One invocation. The scrape reads both cities in
  one go, so `city` is NULL unless `--city` narrows it; the report breaks rows down by
  city from `signal_ingest`. (Step 3, built)
- **Why is the run row committed before the scrape?** So a run that crashes or hangs is still
  on record (`running` / `failed`), not lost with a rolled-back transaction. (Step 3, built)
- **Why do the records and the run's `completed` status commit together?** A run is either
  fully written with its counts, or not at all and marked `failed`; never half-written. A test
  crashes after the upsert and checks no rows remain. (Step 3, built)
- **What happens when the portal is down?** The run is `failed` with the error, and the CLI
  exits 1 so a scheduler notices. (Step 3, built)
- **Why a separate `runner.py` instead of logic in the CLI?** The integration tests call the
  same `run_source` the CLI calls, without click; the CLI only parses options and prints.
  (Step 3, built)
- **Why do the db tests truncate instead of rolling back?** `run_source` commits twice on
  purpose (see above), which an outer test transaction can't contain. (Step 1, built)
- **Why a `source` column with a single source?** It is half of the natural key the brief
  asks for ("preserve source identifiers"), and it makes every row say where it came from.
  `test_same_id_from_two_sources_is_two_rows` checks the key is the pair, not the ID alone.
- **How is the run idempotent?** `UNIQUE (source, source_record_id)` plus `ON CONFLICT DO
  UPDATE`; a re-run can't add rows.
- **Why no change detection (hash column)?** Nothing in the brief consumes "what changed"; an
  existing row is simply rewritten with the latest values.
- **How are inserted vs updated counted?** `RETURNING (xmax = 0)`: true only for a freshly
  inserted row. Covered by the re-run tests. (Step 2, built)
- **Why de-duplicate the batch in Python first?** Otherwise one row would count as 1 inserted
  + 1 updated, and the repeat itself would go unseen; each dropped duplicate is logged as a
  `duplicate_in_batch` error. (Step 2, built)
- **Why the EPSG:25832 transform in SQL?** `ST_Transform` in the upsert keeps pyproj out of the
  dependencies; a test checks a control point computed independently with pyproj. (Step 2,
  built)
- **Why are rows never deleted?** A plan missing from one run may just be a portal hiccup;
  staging keeps what was seen, with `updated_at` as "last seen".

### Success Criteria

#### Automated Verification
- [x] `pytest tests/test_upsert.py tests/test_bounding_box.py tests/test_pipeline_integration.py` -- all pass
- [x] `python -m harvester run --dry-run` executes without error (mocked portal, `test_cli.py`)
- [x] `ruff check .` passes
- [x] `mypy .` passes

#### Manual Verification (live, 2026-10-28)
- [x] `python -m harvester run` against the live portal populates the DB: 83 scraped, 9
  inserted (7 Dresden, 2 Leipzig, all with point and date)
- [x] `SELECT count(*) FROM iris_staging.signal_ingest` shows 9
- [x] Re-run produces 0 inserts (9 updated) and the same row count
- [x] No `count_mismatch` errors (0 errors in both runs)

**Checkpoint:** Pause for confirmation before proceeding to Phase 5.

---

## Phase 5: Reporting, Validation, and Deliverables

### Overview

Build the quality report, validation SQL, sample output, and README. This phase
completes all deliverables.

### Changes Required

#### 1. `src/harvester/report.py` + `harvester report` ✅

`harvester report` (also `python -m harvester report`) prints four aligned tables, from
the database only:

- **Latest runs**, one row per source (`DISTINCT ON (source) … ORDER BY started_at
  DESC`): city (`all` when the run covered all the source's cities), status, start in
  UTC, scraped, industrial, rejected, inserted, updated, errors.
- **Run errors** of those runs: count per error kind with its first message as an
  example, plus a failed run's message as kind `run_failed`. The full list stays in
  `ingest_run.errors`.
- **Stored records** per source and city: records, with point, `unresolved`, and
  `rejected_out_of_bounds` ("rejected bbox"). This covers all rows, i.e. what a consumer
  of `signal_ingest` sees, not only the latest run's.
- **Quality flags** per source and city.

Aggregates only, each serving the acceptance criterion "errors and missing fields are
visible; invalid coordinates are not silently accepted". No notes next to the numbers:
the column names are the field names, and the README explains them once. Row-level
checks (e.g. filter evidence per record) belong in `sql/validation_queries.sql`.

Live output on 2026-10-28:

```
IRIS-SIG-08 quality report

Latest runs
  source          city  status     started (UTC)     scraped  industrial  rejected  inserted  updated  errors
  sachsen_portal  all   completed  2026-10-28 09:44       83           9         0         9        0       0

Run errors
  none

Stored records
  source          city     records  with point  unresolved  rejected bbox
  sachsen_portal  dresden        7           7           0              0
  sachsen_portal  leipzig        2           2           0              0

Quality flags
  none
```

Built differently from the first draft of this plan:
- One block per **source**, not per run and city ("sachsen_portal (dresden)"): since
  Phase 4 a run covers all cities, so the per-city view comes from `signal_ingest`.
- No "8 AKTUELLE + 52 BEENDETE" split of the scraped count: it isn't stored, and adding a
  column only for the report isn't worth it.
- `report.py` next to `runner.py` instead of a `reporting/` package holding one module.
- No per-record list and no keyword ranking: the brief asks for a concise report, and
  neither serves an acceptance criterion.
- Tables instead of labelled blocks per run; no run ID, finish time, filtered-out count or
  match percentage (scraped and industrial sit side by side; the rest is in `ingest_run`).

#### 2. `sql/validation_queries.sql` ✅

9 checks, each headed by an `\echo` so the output of
`docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql` is
readable. Checks marked "expect 0 rows" are violations when they return anything.

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

Live results on 2026-10-28: the "expect 0" checks return 0 rows. Check 6 was also tested by moving one point to Berlin inside a
rolled-back transaction: it returned that row.

Built differently from the first draft of this plan:
- Check 6 reads each city's box from the `config_snapshot` of the run that wrote the row,
  instead of one hardcoded Saxony envelope: it checks against the config actually used,
  with no second copy of the boxes to keep in sync.
- Check 9 compares with the latest run covering the row's city (`r.city IS NULL OR
  r.city = s.city`); see the choice below.
- Added check 8 (traceability, which also shows the filter evidence). Dropped the keyword
  distribution, which serves no acceptance criterion, and the duplicate-key and
  point-vs-status checks: `uq_source_record` and `ck_geometry_matches_status` make both
  impossible, so the checks could only ever return 0 rows. Idempotency shows in the
  re-run counts (check 1: 0 inserted).
- The README's inline queries moved here; the README lists the checks with the expected
  live results.

#### 3. `sample_output/signal_ingest_sample.csv` ✅

All 9 records from the live run on 2026-10-28, every column of `signal_ingest`. The
geometry is written as `x_25832` / `y_25832` (as stored) and `lon` / `lat` (WGS84), and
`raw_data` as JSON, so the sample also shows traceability and the filter evidence.

Made with one `COPY (SELECT …) TO STDOUT WITH (FORMAT csv, HEADER)` through
`docker compose exec`; the README has the command to regenerate it.

"Anonymized or public-source": the data is public-source. `raw_data` keeps only what the
portal publishes (marker, Zeitraum, listing URL, filter evidence); a search for e-mail, phone and contact patterns found nothing to anonymize.

Built differently from the first draft of this plan:
- No GeoJSON: the brief asks for sample output, and a second format of the same rows
  isn't needed; the points are in the CSV as coordinates.
- No `sql/export_samples.sql`: one `COPY` command in the README does the same.

#### 4. `README.md` ✅

Sections, each tied to the brief's "README explaining data source, mapping assumptions,
how to run, and known limitations" or to an acceptance criterion:

| Section | Serves |
|---|---|
| Intro + "How it works" (one table: step, code, what it does) | maintainable |
| Data source: the state portal, access, selection, pagination, counts | data source; "access verified rather than assumed" |
| Mapping assumptions: column table, filter, Zeitraum date, coordinates, flags, finished hearings | mapping assumptions |
| Setup, Run the harvester, Quality report, validation checks, Sample output | how to run; demonstrable |
| Configuration: `settings.yml`, `.env`, adding a city | configurable |
| Tests: fixtures, retries, the db tests incl. re-run / duplicates, CI | automated tests incl. re-run/duplicate behaviour |
| Known limitations | known limitations |

Known limitations listed: only the two cities, the portal lists only what a city publishes
there (no open industrial Leipzig hearing on 2026-10-28), title-only filter (Nr. 422
Radefelder Allee West), EPSG:25832 outside its zone, dependence on the portal's markup
(`markers_not_found`, `count_mismatch`), rows never deleted.

Built differently from the first draft of this plan:
- No separate "Quick start", "Architecture overview" or "Adding new cities" sections:
  "Run the harvester", the "How it works" table and one paragraph in Configuration cover
  them.
- No test count in the README; it would go stale with every test added.

#### 5. Tests ✅ (report)

`tests/test_quality_report.py`, 3 db tests:
- the latest run per source is shown (not an older one) with its UTC start and counts;
  stored records by city with point and geocode counts, and flag counts
- a failed run shows its failure as `run_failed` and its errors counted by kind, with an
  example message
- an empty database prints every section with "no runs yet" / "none" instead of failing

Rows are compared with whitespace collapsed, so the tests don't depend on column widths.

The tests set `started_at` explicitly: `NOW()` is the transaction start, so every run
made in one test transaction would otherwise have the same time.

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why EPSG:25832 although both cities lie east of 12° E?** The brief requires it; it's usable
  there with growing scale distortion, documented in the README.
- **Why does the report show the latest run, but all stored records?** The run
  block answers "did the last harvest work?"; the records block answers "what does
  `signal_ingest` hold?", which is what a consumer reads. Rows from older runs (e.g. a plan
  that has since left the portal) are still in the table, so they belong in that count.
- **Why only aggregates, no per-record list or keyword ranking?** The brief asks for a
  concise error/quality report; each section maps to "errors and missing fields are
  visible; invalid coordinates are not silently accepted". Inspecting rows (e.g. the
  filter evidence, to check false positives) is a validation query, and the evidence is
  stored per record in `raw_data->'filter_evidence'`.
- **Why errors by kind with one example, not the error list?** An outage gives one error
  per page; the count by kind says what happened, and the full list stays in
  `ingest_run.errors`.
- **Why read from the database instead of the run's in-memory summary?** The report then
  works at any time, after any run or a crash, and checks what was actually committed.
- **Why only a CSV sample, made by one `COPY` command?** The brief asks for sample output;
  one file with every column (points as coordinates) covers it. A GeoJSON copy or an
  export command would be extra code and files with nothing new to show.
- **Why does the bounding-box check read the boxes from `config_snapshot`?** It checks
  each point against the config the run actually used. A hardcoded box in SQL would be a
  second copy of `settings.yml` that can drift, and a changed box would wrongly flag
  older rows.
- **Why no duplicate-key check?** `uq_source_record` makes duplicates impossible, so the
  check could never fail; the re-run counts (0 inserted) and the idempotency tests show it.
- **Why are the validation checks SQL and not tests?** They check the real data after a
  live run, which the fixture tests can't see; anyone can run them with `psql`.
- **Why does the stale-rows query match the run's city?** A `--city dresden` run doesn't
  touch Leipzig rows; without the city condition they would all look stale.
- **Why keep finished (`BEENDETE`) hearings?** Each portal entry is a hearing record with
  its own Zeitraum, open or over. Live 2026-10-28, 7 of the 9 rows are finished hearings. Open vs finished shows in `raw_data` (listing URL status,
  Zeitraum end).
- **Why read "around Dresden and Leipzig" as the two cities?** The brief names the state
  portal, whose Dresden and Leipzig listings are the cities' own. Surrounding
  municipalities are a `cities` entry each (README limitation), not a code change.
- **Why does the live data have no flags or unresolved rows?** On 2026-10-28 all 9 records
  had coordinates in the box and a Zeitraum. The flagged, unresolved, rejected and failed
  cases are shown by the tests (marker without coordinates, out-of-bounds marker, detail
  page 404, unreachable portal), including the quality report's.

### Success Criteria

#### Automated Verification
- [x] `pytest` -- full suite passes (81 tests)
- [x] `ruff check .` passes
- [x] `mypy .` passes
- [x] `python -m harvester report` generates report without error (live, 2026-10-28)

#### Manual Verification
- [ ] README instructions produce a working setup from scratch
- [x] `sql/validation_queries.sql` runs against populated DB and returns sensible results
  (live, 2026-10-28)
- [x] Sample CSV parses with every row and column (9 rows, 2026-10-28)
- [ ] Quality report identifies any data issues

**Checkpoint:** Final review.

---

## Testing Strategy

### Unit Tests
- Adapter HTML parsing (from fixtures, no network)
- `fetch()` retry rules (httpx `MockTransport`)
- Normalisation functions (publication date parsing, quality flags, dropping non-matches)
- Industrial keyword filter (positive matches, negative matches, case sensitivity)
- Coordinate check (in bounds, missing, out of bounds, swapped, non-numeric)

### Integration Tests
- Full pipeline: fixture HTML -> adapter -> normalise -> filter -> geocode -> upsert -> DB query
- Idempotent re-run: same fixtures upserted twice, verify 0 new rows and stable ids
- Content change: modify fixture, verify the existing row is rewritten in place
- Out-of-bounds coordinates: verify NULL geometry + rejection status stored
- `ST_Transform` control point: WGS84 in, expected EPSG:25832 out

### Test Infrastructure
- **HTTP:** no browser. Parsing tests call the pure `parse_*` functions on fixture text;
  `scrape()` and pipeline tests use the `mock_client` from `conftest.py` (httpx
  `MockTransport`), which **fails on any URL not mapped to a fixture**, so tests can never reach
  the live portal.
- **Database:** the PostGIS `iris_test` database from `docker compose`, at
  `DatabaseSettings().test_url`. There is **no SQLite/SpatiaLite fallback**: the upsert depends on
  PostgreSQL-only features (`ON CONFLICT`, `xmax`, `ST_Transform`, `TEXT[]`), so a
  different engine would test different behaviour.
  - A session-scoped fixture runs `alembic upgrade head` against `iris_test`.
  - Each test starts by emptying both tables (`TRUNCATE`). Not a rolled-back transaction:
    `run_source` commits the run row and the upsert separately on purpose, so a test can't
    hold them in one outer transaction.
  - Tests marked `db` are **skipped with an explicit reason** if the database settings can't
    be loaded (no `POSTGRES_PASSWORD` in the environment or `.env`). If they load but the
    database is unreachable, the tests **fail**: configured-but-broken is an error, not a skip. The non-DB suite (parsing, filter, normalise, geocode, fetch) runs with no Docker.

---

## Performance Considerations

- **Request volume (2026-10-27):** 4 listings (2 cities x 2 statuses) + one detail page
  per match (~9) = ~13 requests, under a minute at a 2 s delay. The
  live run is quick enough to demo; re-run and idempotency demos also work against fixtures.
- **Database:** Small table (<1000 rows, only industrial matches). No indexes beyond the
  primary key and `uq_source_record`; see **Database Schema**.
- **Memory:** HTML pages are small (<700 KB each). No memory concerns.
- **HTTP:** one `httpx.Client` reused for every request (connection reuse).

---

## Migration Notes

- First run: `alembic upgrade head` creates everything from scratch.
- Subsequent schema changes: add new Alembic migration files.
- No existing data to migrate (greenfield).

---

## Nice to Have

Not required for acceptance. Pick up only once Phases 1-5 are done.

- **One-command setup via `Makefile` or `justfile`.** The README setup steps stay the
  source of truth; the task runner only wraps them. Suggested targets:
  - `setup`: `uv sync`
  - `db-up` / `db-reset`: `docker compose up -d` / `docker compose down -v && docker compose up -d`
  - `migrate`: `uv run alembic upgrade head`
  - `test`, `lint` (`ruff check .` + `mypy`), `run` (`uv run harvester run`)

  `make` ships with macOS and most Linux systems; `just` reads better but is an extra
  install. Either way, don't hide steps the README doesn't also list.
- **Scripted fixture capture** (`scripts/capture_fixtures.py`). Only worth it if fixtures
  have to be refreshed regularly; they are captured once, by hand (see Phase 2).
- **Stricter config validation.** Parked because each check guards against an unlikely
  mistake rather than an acceptance criterion: `https://` pattern on `base_url`, value ranges
  (delay >= 0, `retry_backoff_factor` >= 1, timeout > 0, lat/lon within -90..90 / -180..180),
  and non-empty `sources` / `cities` / `status_filters`.

---

## References

- Saxon state portal: `https://buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen`
- BauGB (Federal Building Code): procedure phases SS 2-10
- BauNVO (Building Use Ordinance): zone type definitions (GE, GI, SO, etc.)
- EPSG:25832: ETRS89 / UTM zone 32N
- EPSG:25833: ETRS89 / UTM zone 33N (Dresden's native zone)
