# IRIS-SIG-08 Saxony Citizen Participation Portal Harvester -- Implementation Plan

## Overview

Build a Python harvester that scrapes Bauleitplan (land-use zoning) procedures from two
Saxony citizen participation portals, filters for industrial/commercial hearings, normalises
the data, and loads it into a PostGIS-backed `iris_staging.signal_ingest` table with
idempotent upserts and full auditability.

**Scope:** Dresden and Leipzig city portals only; surrounding districts deferred.

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

1. **Two portals, both server-rendered, and everything needed is a plain GET.** Dresden and
   Leipzig are on the Saxon state portal (`buergerbeteiligung.sachsen.de`, PrimeFaces);
   Leipzig also has its own plan register (`www.leipzig.de`, TYPO3). No browser automation is
   needed: `httpx` + BeautifulSoup.

2. **The state portal embeds per-procedure coordinates.** Each listing contains an
   OpenLayers call `map.drawMarkers([...])` whose JSON array has one entry per result, **across
   all pages**: `id`, `kategorie` (the title, HTML-escaped), `href`, `latitude`, `longitude`
   (WGS84), `adresse` (street address). All 83 markers captured have coordinates inside the city
   bounding boxes. Geometry comes from the source, not from a lookup table.

3. **The Leipzig city portal has no coordinates and no dates.** Rows give status, one or more
   districts and one or more Thema values; the detail page adds nothing else (plan PDFs only).
   These rows are stored with `geocode_status = 'unresolved'` and no publication date.

4. **EPSG zone mismatch.** Both cities are east of 12 deg E (Leipzig ~12.4, Dresden ~13.7), i.e.
   outside the nominal coverage of EPSG:25832 (UTM 32N, 6--12 deg E); their native zone is
   EPSG:25833. The brief mandates 25832, so points are transformed with PostGIS `ST_Transform`
   and the zone note goes in the README. The bounding-box check catches gross errors.

5. **The state portal status filter is a GET parameter**: `&status=AKTUELLE` (the default view)
   or `&status=BEENDETE` (finished). Uppercase only; lowercase is silently ignored. Cards render
   10 per page behind a PrimeFaces AJAX paginator ("1 - 10 von 52"), but the marker JSON already
   lists all 52, so the adapter reads the markers and never pages. The "von N" count, when
   present, is a completeness check against the marker count.

6. **State-portal IDs come from one shared sequence across cities**, so `source_record_id` is
   unique per portal. The detail URL must use the marker's `href`: a Dresden ID under
   `/portal/leipzig/` silently redirects to the Leipzig listing (HTTP 200).

7. **Publication date = start of the "Zeitraum".** There is no separate "published on" field.
   The detail page's `li.nbp-statistik--laufzeit` ("Zeitraum 12.10.2026 bis 13.11.2026") is the
   period in which the draft is publicly "veröffentlicht" (the detail text says so: "vom ... bis
   ... veröffentlicht"; BauGB § 3 (2)). Its start date is the publication date.

8. **Most Leipzig city-portal plans are not hearings.** The register lists ~555 plans; ~93 %
   are "rechtskräftig" (in force, participation long finished). Only "im Verfahren" plans can
   have a hearing. The status filter is a GET parameter, `tx_lepurpose[filter][89][]=252`, which
   returns 39 rows on one page (10 industrial).

### Portal Details

| Attribute | Saxon State Portal (Dresden + Leipzig) | Leipzig City Portal |
|---|---|---|
| Listing URL | `/portal/{city}/beteiligung/themen?format=Bauleitplan&status={AKTUELLE\|BEENDETE}` | `/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene?tx_lepurpose[filter][89][]=252` |
| Framework | Jakarta Faces / PrimeFaces | TYPO3 + `tx_lepurpose` extension |
| What is read | `map.drawMarkers([...])` JSON (all results, no paging) | `tr[id^="project-"]` table rows |
| Pagination | Not needed (markers list every result) | "Load more" button `.start-ajax-pagination[data-next=N]`; GET `tx_lepurpose[filter][page]=N`, 50 rows/page |
| Detail URL | marker `href`: `/portal/{city}/beteiligung/themen/{id}` | `.../bebauungsplaene/detail/projekt/{slug}` (not fetched) |
| Stable ID | marker `id` (numeric) | `project-{id}` from the row |
| Coordinates | marker `latitude` / `longitude` (WGS84) | none |
| Publication date | detail page `li.nbp-statistik--laufzeit`, start date | none |
| Items (2026-10-27) | Dresden 8 current + 52 finished; Leipzig 2 current + 21 finished | 39 im Verfahren (of ~555) |

---

## Desired End State

- `docker compose up` starts PostgreSQL 16 / PostGIS 3.4.
- `alembic upgrade head` creates `iris_staging` schema, `signal_ingest` + `ingest_run` tables, PostGIS extension.
- `python -m harvester run` scrapes both sources (state portal per city and status), filters, upserts, and writes a quality report.
- `pytest` passes with 100% of HTML-fixture tests, including idempotent re-run and duplicate detection.
- A quality report (stdout + queryable `ingest_run` table) shows counts, errors, match rates.

### Verification

```bash
# Automated
pytest --tb=short -q
ruff check .
mypy .

# Manual
docker compose up -d
alembic upgrade head
python -m harvester run --dry-run   # prints normalised records, no DB write
python -m harvester run             # full ingest
docker compose exec -T db psql -U iris -d iris < sql/validation_queries.sql
```

---

## What We Are NOT Doing

- Merging duplicate rows across sources (overlap is documented, not resolved).
- Browser automation (Playwright). Every page needed is a plain GET (Key Discoveries 1, 5, 8).
- Geocoding of any kind: no online service (Nominatim, Google) and no district-centroid table.
  The state portal supplies coordinates; Leipzig city-portal rows give only districts (often
  several), an area rather than a point, so they are stored `unresolved` with the reason. An
  invented centroid point would be less honest than a documented NULL.
- Paging through the state portal's AJAX card paginator: the marker JSON lists every result.
- Fetching Leipzig city-portal detail pages: they hold nothing the listing row lacks.
- Matching keywords in descriptions. The filter reads titles (plus Thema on the city portal):
  plan titles name the area type, and descriptions mention "Gewerbe" in passing (neighbouring
  uses, "Gewerbesteuer"), which would add false positives. Detail pages are then fetched only for
  matches, for the publication date.
- Storing "rechtskräftig" Leipzig city-portal plans: they are in force, not hearings.
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
  re-runs, not fast ones, and a full live run is ~20 requests (~1 minute).
- Change detection (`content_hash`, `last_seen_*` columns, an "unchanged" counter). The
  brief asks for no duplicates on re-run, which the unique key plus `ON CONFLICT` guarantees
  on its own. Nothing consumes "what changed", so existing rows are simply rewritten.
- Storing non-industrial records. The objective is to *filter*; non-matches are only counted
  (`records_filtered_out`). `--dry-run` prints every record with its filter decision for
  tuning the keyword list.
- Columns the brief doesn't ask for (plan type, plan number, procedure step, status,
  participation end date, Thema). They are kept as scraped in `raw_data`.
- Secondary indexes. Under 1000 rows, PostgreSQL scans the table faster than it would use an
  index; only the unique index behind `uq_source_record` exists, because `ON CONFLICT` needs it.
- Deleting rows that vanish from a portal or stop matching the filter. They stay, with an
  older `ingest_run_id` (validation check 9).

---

## Implementation Approach

Two adapters (the state portal adapter runs once per city and status) behind a common
`ScrapedRecord` model. Each fetches pages with one shared `httpx.Client` and parses them with
BeautifulSoup. Parsing functions take an HTML string and return data, so tests feed them
fixture files directly. A shared pipeline applies the industrial keyword filter, fetches the
detail page of each state-portal match for its publication date, checks coordinates against
the city bounding box, and upserts into PostGIS, where `ST_Transform` produces the EPSG:25832
point.

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
    settings.yml                   # city/source list, keyword list, bounding boxes
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
        leipzig_stadtportal.py      # city portal: table rows, "im Verfahren" only
      pipeline/
        __init__.py
        normalise.py                # ScrapedRecord -> NormalisedRecord, date parsing, quality flags
        filter.py                   # industrial keyword matcher
        geocode.py                  # bounding-box check of source coordinates
  .github/
    workflows/
      ci.yml                        # ruff, mypy, migrations, all tests on push to main
  sql/
    validation_queries.sql          # post-ingest verification
    export_samples.sql              # writes sample_output/ CSV + GeoJSON via psql
  tests/
    __init__.py
    conftest.py                     # fixture loader, httpx MockTransport, DB fixtures
    db_helpers.py                   # record / run builders for the db tests
    fixtures/
      SOURCES.md                    # provenance: source URL + capture date per fixture
      sachsen_dresden_listing_beendet.html     # marker JSON, "von 52"
      sachsen_dresden_detail.html              # "Zeitraum"
      leipzig_stadt_listing_im_verfahren.html  # rows, districts, Thema
    test_sachsen_adapter.py
    test_leipzig_adapter.py
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
    signal_ingest_sample.csv
    signal_ingest_sample.geojson
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

  - name: leipzig_stadtportal
    type: leipzig_stadt
    base_url: "https://www.leipzig.de"
    listing_path: "/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene"
    # tx_lepurpose[filter][89][] value for "im Verfahren"; "rechtskräftig" (251) plans are
    # in force, not hearings
    status_filter_id: 252

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
  # Leipzig city-portal Thema value, matched exactly (a row can have several)
  thema_signals:
    - "Gewerbe"

bounding_boxes:
  # WGS84 [min_lon, min_lat, max_lon, max_lat]
  dresden: [13.55, 50.95, 13.95, 51.18]
  leipzig: [12.20, 51.25, 12.55, 51.45]
```

---

## Database Schema

Every column traces back to a line in the brief; anything else scraped stays in `raw_data`.

| Brief | Columns |
|---|---|
| "preserve source identifiers" | `source`, `source_record_id` |
| "titles" | `title` |
| "publication dates" + "verified rather than assumed" | `publication_date` (raw "Zeitraum" text kept in `raw_data`) |
| "source URLs" / "traceable staging records" | `detail_url`, `ingest_run_id` |
| "geographic context when available" | `city`, `location_text` |
| "EPSG:25832 point geometries" | `geometry` |
| "document ambiguous or unresolved locations" / "invalid coordinates are not silently accepted" | `geocode_status`, `geocode_reason` |
| "errors and missing fields are visible" | `quality_flags` |
| "filter industrial land-use zoning hearings" (why this row matched) | `matched_keywords` |
| "malformed data" / "mapping assumptions" (the values as scraped) | `raw_data` |

Only industrial matches are stored: the objective says *filter*. Non-matching records are
counted in `ingest_run.records_filtered_out`, not stored. Fields that are scraped but not
required (status, raw "Zeitraum" text, Thema, source coordinates as given) live in `raw_data`
only.

No secondary indexes: at <1000 rows a sequential scan beats any index, and nothing queries
the geometry spatially except validation, which reads every row anyway. The only index
besides the primary key is the one behind `uq_source_record`, which `ON CONFLICT` requires.

### `iris_staging.ingest_run`

Tracks each harvester execution for auditability and quality reporting.

```sql
CREATE TABLE iris_staging.ingest_run (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at          TIMESTAMPTZ,
    source               TEXT NOT NULL,         -- 'sachsen_portal' | 'leipzig_stadtportal'
    city                 TEXT,                  -- 'dresden' | 'leipzig' | NULL (for multi-city runs)
    status               TEXT NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'completed', 'failed')),
    -- NOT NULL so the quality report's sums never meet a NULL
    records_scraped      INTEGER NOT NULL DEFAULT 0,
    records_filtered_out INTEGER NOT NULL DEFAULT 0,  -- parsed fine, not industrial
    records_rejected     INTEGER NOT NULL DEFAULT 0,  -- unusable (no ID or title), see errors
    records_inserted     INTEGER NOT NULL DEFAULT 0,
    records_updated      INTEGER NOT NULL DEFAULT 0,  -- already present, rewritten
    errors               JSONB NOT NULL DEFAULT '[]'::jsonb,
    config_snapshot      JSONB,                 -- frozen copy of settings used
    error_message        TEXT
);
```

### `iris_staging.signal_ingest`

```sql
CREATE TABLE iris_staging.signal_ingest (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source           TEXT NOT NULL,             -- 'sachsen_portal' | 'leipzig_stadtportal'
    source_record_id TEXT NOT NULL,             -- portal-specific ID (numeric or slug)
    ingest_run_id    UUID NOT NULL REFERENCES iris_staging.ingest_run(id),  -- run that last wrote the row

    city             TEXT NOT NULL,             -- 'dresden' | 'leipzig'
    title            TEXT NOT NULL,
    detail_url       TEXT NOT NULL,             -- traceability: every row links back to its source
    location_text    TEXT,                      -- as the source gives it: street address (state
                                                -- portal) or district(s) (city portal)

    publication_date DATE,                      -- start of the detail page's "Zeitraum"; NULL if none

    -- Only industrial matches are stored, so there is always at least one keyword
    matched_keywords TEXT[] NOT NULL CHECK (cardinality(matched_keywords) > 0),

    geometry         GEOMETRY(Point, 25832),
    geocode_status   TEXT NOT NULL
                     CHECK (geocode_status IN
                            ('source_coordinates', 'unresolved', 'rejected_out_of_bounds')),
    geocode_reason   TEXT,                      -- why unresolved/rejected, e.g. 'source gives district only'

    quality_flags    TEXT[] NOT NULL DEFAULT '{}',  -- e.g. 'missing_publication_date', 'missing_location',
                                                    -- 'unparseable_date', 'detail_page_failed'
    raw_data         JSONB NOT NULL,            -- all scraped fields as-is, plus filter evidence

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- first seen
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- last written, i.e. last seen

    -- Named: ON CONFLICT and the tests refer to it
    CONSTRAINT uq_source_record UNIQUE (source, source_record_id),
    -- A point exists exactly when the source gave usable coordinates: the database itself
    -- refuses a point without that status, or that status without a point
    CONSTRAINT ck_geometry_matches_status
        CHECK ((geocode_status = 'source_coordinates') = (geometry IS NOT NULL))
);
```

### Upsert Logic

Idempotency comes from the natural key: `uq_source_record UNIQUE (source, source_record_id)`
plus `INSERT ... ON CONFLICT DO UPDATE`. Re-running over the same data cannot add rows. There
is **no change detection**: an existing row is always rewritten with the latest values. Nothing
in the brief consumes "what changed", so telling a real change apart from a re-scrape isn't
worth a hash column and its tests (see "What We Are NOT Doing").

**Step 0: de-duplicate the batch in Python.** Records are written one statement each, so a
repeated key would not fail, but it would be written twice and counted as 1 inserted + 1
updated for a single row, and the repeat (a source problem) would go unseen.
Records with a repeated `(source, source_record_id)` in one batch (e.g. a card shown on two
listing pages while the Leipzig register re-sorts) are collapsed to the last occurrence. Each one
dropped is logged to `ingest_run.errors` as `duplicate_in_batch`.

**Step 1: insert or overwrite.**

```sql
INSERT INTO iris_staging.signal_ingest (
    source, source_record_id, ingest_run_id,
    city, title, detail_url, location_text,
    publication_date, matched_keywords,
    geometry, geocode_status, geocode_reason, quality_flags, raw_data
)
VALUES (
    ...,
    -- WGS84 -> EPSG:25832 in PostGIS (no pyproj). NULL lon/lat (unresolved or rejected)
    -- gives a NULL geometry; the bounding box was checked in Python before this
    ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 25832),
    ...
)
ON CONFLICT (source, source_record_id) DO UPDATE SET
    ingest_run_id    = EXCLUDED.ingest_run_id,
    city             = EXCLUDED.city,
    title            = EXCLUDED.title,
    detail_url       = EXCLUDED.detail_url,
    location_text    = EXCLUDED.location_text,
    publication_date = EXCLUDED.publication_date,
    matched_keywords = EXCLUDED.matched_keywords,
    geometry         = EXCLUDED.geometry,
    geocode_status   = EXCLUDED.geocode_status,
    geocode_reason   = EXCLUDED.geocode_reason,
    quality_flags    = EXCLUDED.quality_flags,
    raw_data         = EXCLUDED.raw_data,
    updated_at       = NOW()
RETURNING (xmax = 0) AS inserted;
```

**Counting:** `inserted` = rows returned with `inserted = true`; `updated` = the rest. A
re-run over unchanged data therefore reports 0 inserted and N updated, with the row count
and every `id` and `created_at` unchanged.

`xmax = 0` is the standard PostgreSQL test for whether a row from an `ON CONFLICT` upsert
was newly inserted. It's covered by a test.

**How it's executed:** the statement is a SQLAlchemy `text()` with named parameters, run
with one parameter dict per record (no ORM models; see Phase 1). psycopg 3 adapts Python
lists to `TEXT[]`. Dicts for `JSONB` must be wrapped in `psycopg.types.json.Jsonb(...)`, or
binding fails. Records are executed one statement each, in a loop inside the batch's single
transaction, collecting each `RETURNING` row. That's simple, keeps the per-record counting
obvious, and is fast enough for <1000 rows; collecting `RETURNING` rows from one
`executemany` call isn't something to rely on with `text()`.

**Rows are never deleted.** A plan that disappears from a portal, or stops matching after the
keyword list changes, keeps its row; its `ingest_run_id` then points at an older run than the
latest one covering its city (validation check 9 lists these).

---

## Industrial Keyword Filter

Plan titles are usually German compounds ("Gewerbestandort Nord", "Logistikzentrum
Radefeld", "Kleingewerbe am Bahnhof"), so whole-word matching misses most real hits.
The filter uses two kinds of match:

- **Long keywords** (Gewerbe, Industrie, Logistik, ...) match case-insensitively **anywhere
  inside a word**. The whole word containing the hit is kept as evidence.
- **Short codes** (GE, GI, GVZ) match **case-sensitively as whole tokens only**. As
  substrings they would hit ordinary words ("gegen", "Gießen").

```python
import re
from dataclasses import dataclass, field

from harvester.config import IndustrialFilterConfig

# Marks a Leipzig Thema match in matched_keywords, e.g. "thema:Gewerbe"
THEMA_PREFIX = "thema:"


@dataclass(frozen=True)
class FilterResult:
    is_industrial: bool
    matched_keywords: list[str] = field(default_factory=list)  # keywords as written in config
    evidence: list[str] = field(default_factory=list)  # matched words, e.g. "Gewerbestandort"


class IndustrialFilter:
    def __init__(self, config: IndustrialFilterConfig) -> None:
        # \w* on both sides captures the whole compound word around the keyword as evidence
        self._patterns = [
            *((kw, re.compile(rf"\w*{re.escape(kw)}\w*", re.IGNORECASE)) for kw in config.keywords),
            *((tok, re.compile(rf"\b{re.escape(tok)}\b")) for tok in config.case_sensitive_tokens),
        ]
        self._thema_signals = set(config.thema_signals)

    def check(self, title: str, thema: list[str]) -> FilterResult:
        # A Leipzig row can carry several Thema values ("Wohnen", "Gewerbe", ...)
        keywords = [f"{THEMA_PREFIX}{t}" for t in thema if t in self._thema_signals]
        evidence: list[str] = []
        for keyword, pattern in self._patterns:
            hits = pattern.findall(title)
            if hits:
                keywords.append(keyword)
                evidence.extend(hits)

        unique = list(dict.fromkeys(keywords))
        return FilterResult(
            is_industrial=bool(unique),
            matched_keywords=unique,
            evidence=list(dict.fromkeys(evidence)),
        )
```

`evidence` goes into `raw_data` and the quality report, so false positives (e.g. "Wohn- und
Gewerbepark", a mixed area) can be spotted and the keyword list tuned.

Only the title (and the Leipzig Thema list) is read; see "What We Are NOT Doing" for why not
descriptions. The filter runs on listing data, **before** any detail page is fetched, so only
matches cost an extra request. Only records with `is_industrial = True` are stored;
`matched_keywords` is the stored evidence for why each row is there (a `thema:` prefix marks a
Leipzig Thema match). Non-matches are counted in `ingest_run.records_filtered_out`, and
`--dry-run` prints every record with its filter decision, so false negatives can be checked
while tuning the keyword list.

---

## Coordinates and Bounding Box

Only the state portal supplies coordinates (marker `latitude` / `longitude`, WGS84). The
pipeline sets `geocode_status` per record:

| Case | `geocode_status` | `geometry` | `geocode_reason` |
|---|---|---|---|
| Marker coordinates parse as finite numbers and lie inside the city's bounding box | `source_coordinates` | EPSG:25832 point | NULL |
| Marker coordinates missing or blank (either one) | `unresolved` | NULL | `marker has no coordinates` |
| Leipzig city-portal row | `unresolved` | NULL | `source gives district only: <districts>` |
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
Discovery 4). The CRS is still usable there, but scale distortion grows; documented in the
README.

---

## Adapter Details

Both adapters use one shared `httpx.Client` (user agent and timeout from config) and go
through `fetch()` for every request (see **Resilience**). Parsing is split into pure functions
(`str` in, data out) so the tests can call them on fixture text directly.

### Saxon State Portal Adapter (`sachsen_portal.py`)

Runs once per city in `cities` and status in `status_filters`:

1. GET `{base_url}/portal/{city}/beteiligung/themen?format={format_filter}&status={status}`.
2. `parse_listing(html, page_url, city)`: find `map.drawMarkers([` (the page also calls
   `drawMarkers(markerData)`) and decode the array with `json.JSONDecoder().raw_decode`, which
   stops at the end of the array. For each entry:
   - `id` -> `source_record_id` (as a string)
   - `kategorie` -> `title` (`html.unescape`, since the portal escapes quotes as `&quot;`)
   - `href` -> `detail_url` (joined to the listing URL; the city in the path matters, Key
     Discovery 6). Missing `href`: built from the listing's city and the `id`
   - `latitude` / `longitude` -> kept as given; checked in the pipeline
   - `adresse` -> `location_text`
   - `raw_data` = `{"marker": <entry as given>, "listing_url": <page URL>}`
3. `parse_expected_count(html) -> int`: "von N" from `span.ui-paginator-current` when present
   (more than 10 results), else the number of `div.nbp-teaser` cards. `parse_listing` compares
   it with the marker count; a difference is a `count_mismatch` error in `ingest_run.errors`,
   and the markers are still used.
4. The pipeline filters on title. For each **match only**, GET the detail page and
   `parse_detail(html, record_id)` reads `li.nbp-statistik--laufzeit` ("Zeitraum DD.MM.YYYY bis
   DD.MM.YYYY"), **only if** the `<body>` has class `nbp-beteiligung-{id}`: listing cards carry
   the same `li`, so a redirect to a listing would otherwise yield another procedure's dates
   (found while testing on 2026-10-28). The raw text goes into `raw_data`; the start date becomes
   `publication_date` (see **Publication Dates**). A detail page that fails keeps the listing
   data and gets `detail_page_failed`.

**Empty result:** no `drawMarkers` call **and** no cards -> empty result plus a warning, no
exception; the `ingest_run` records 0 scraped. A listing that has cards but no parseable marker
JSON is an error (`markers_not_found`), not an empty result: it means the page changed.

**Malformed markers:** an entry without `id` or `kategorie` becomes a `ScrapeError` (counted
in `records_rejected`); the others are still parsed.

### Leipzig City Portal Adapter (`leipzig_stadtportal.py`)

1. GET `{base_url}{listing_path}?tx_lepurpose[filter][89][]={status_filter_id}` (only "im
   Verfahren"; Key Discovery 8).
2. `parse_rows(html, page_url)` for each `tr[id^="project-"]`:
   - `source_record_id`: from the `tr` id (`project-1751` -> `1751`)
   - title and `detail_url`: `a.filterlist-detailpage` (text of its `span:not(.icon)`, i.e.
     without the trailing `arrow_forward` icon text, and href)
   - cells **by column position**, as in the table header (image, Titel, Status, Stadtbezirk/
     Ortsteil, Thema); each cell's `ul > li` texts. A row can have several districts
     ("Nord / Eutritzsch") and several Thema values, or none. Fewer cells than that ->
     `unexpected_row_layout` error
   - status must be exactly `["im Verfahren"]`, else `unexpected_status` error and the row is
     not stored: if the portal ever ignored the filter parameter, plans in force would
     otherwise be stored as hearings
   - `location_text`: the districts joined with `"; "`
   - `raw_data` = `{"status", "districts", "thema", "listing_url"}`
3. `parse_next_page(html)`: `data-next` of `.start-ajax-pagination`, or None. While there is
   one, GET the same URL plus `tx_lepurpose[filter][page]=N`; only forward (N greater than the
   current page), so a bad button can't loop. Today the filtered list fits on one page; the loop is tested with a snippet page carrying the real button markup.
4. No detail pages: they hold nothing the row lacks (Key Discovery 3). Every row is
   `unresolved` with no publication date.

### Common: ScrapedRecord Model

```python
from typing import Any

from pydantic import BaseModel, Field

class ScrapedRecord(BaseModel):
    source: str                     # 'sachsen_portal' | 'leipzig_stadtportal'
    source_record_id: str           # stable ID from the source
    city: str                       # 'dresden' | 'leipzig'
    title: str
    detail_url: str
    location_text: str | None = None
    lon: float | str | None = None  # as given by the source; checked in the pipeline
    lat: float | str | None = None
    zeitraum: str | None = None     # raw "Zeitraum ..." text from the detail page
    thema: list[str] = Field(default_factory=list)  # filter input only (Leipzig); stored in raw_data
    raw_data: dict[str, Any]        # all scraped fields as-is
```

---

## Resilience

- **Retries with exponential backoff:** every request goes through one helper, `fetch()`, which
  retries up to `max_retries` attempts with `retry_backoff_factor` between them on:
  - `httpx.TransportError` (network failures, DNS, connection reset, timeouts)
  - `status >= 500`
  - `429`, honouring `Retry-After` when present

  Any other `4xx` is not retried. It is recorded as a page-level error (e.g. a detail page
  that 404s gets `quality_flags = ['detail_page_failed']` and keeps its listing data).
- **Redirects:** followed. A detail URL that ends up on a listing page (Key Discovery 6) is
  caught because that page's `<body>` lacks the record's `nbp-beteiligung-{id}` class, so
  `parse_detail` returns None and the row is flagged `missing_publication_date`.
- **Polite delay:** `request_delay_seconds` (default 2s) before every request.
- **User agent:** Neutral identifying string (`IRIS-SIG-08-Harvester/1.0 (bot)`), overridable
  via the `HARVESTER_USER_AGENT` env var so an operator can add a contact address without
  committing it to source.
- **Timeout:** `timeout_seconds` (default 30s) per request.
- **Empty response handling:** An adapter that receives zero results returns an empty list
  and logs a warning. It does not raise an exception. The `ingest_run` row records the count.
- **Malformed records:** Adapters return a `ScrapeResult(records, errors)`, not a bare list.
  The handling depends on how bad the record is:
  - **Unusable** (no stable ID or no title): becomes a `ScrapeError` with the raw snippet
    (marker JSON entry or row HTML) and the page URL. It is written to `ingest_run.errors`
    and counted in `records_rejected`.
  - **Usable but incomplete** (e.g. unparseable date, missing location, bad coordinates):
    stored, with the problem recorded in `quality_flags` / `geocode_status`.

  Nothing is silently dropped. The quality report surfaces both kinds.
- **Graceful degradation:** If one adapter fails entirely (e.g., portal is down), the
  other adapters still run. The failed adapter's `ingest_run` records `status = 'failed'`.
  "Fails entirely" means an exception anywhere in the run, or no records at all plus at least
  one error. Some pages failing while others work is a `completed` run with those errors.

---

## Publication Dates

The brief requires dates to be verified, not assumed. What was verified (Key Discovery 7):

- The state portal has no separate "published on" field. The detail page's "Zeitraum A bis B"
  is the period in which the plan is publicly "veröffentlicht" (BauGB § 3 (2)), stated in the
  detail text itself. `publication_date` = **A**, the first day it is publicly available.
- It is taken from the detail page, never from the listing card (cards exist for only 10
  results per page) and never derived from the run time.
- The raw "Zeitraum ..." text is kept in `raw_data`, so the derivation is traceable per row.
- The Leipzig city portal publishes no dates: `publication_date` stays NULL and
  `quality_flags` gets `missing_publication_date`. Same for a state-portal detail page without
  a "Zeitraum".
- Dates that are present but unparseable get `unparseable_date`, with the raw text in
  `raw_data`.

---

## Source Overlap Documentation

The same Leipzig Bauleitplan may appear on both the Saxon state portal and the Leipzig city
portal, e.g. Nr. 911 "Industriegebiet am Flughafen Leipzig-Halle, 1. Änderung" (state portal,
finished; city portal, im Verfahren). These are stored as **separate rows** with different
`source` values: each portal's record is a signal in its own right, with its own URL.

Potential matching strategy (not implemented, documented for future work):
- Match on city + plan number from the title ("Nr. 911", "Nummer 320"); validation check 10
  lists these pairs.
- A future `iris_staging.signal_match` table could link `source_record_id` pairs.

The `source` column and `UNIQUE (source, source_record_id)` constraint ensure no
accidental merging.

---

## Phase 1: Project Skeleton and Database ✅

### Overview

Set up the Python project, Docker environment, config and the Alembic migration (plain SQL
DDL, no ORM models). This phase produces a working `docker compose up` +
`alembic upgrade head` that creates the full schema.

### Changes Required

#### 1. `pyproject.toml` ✅

```toml
[project]
name = "iris-sig-08-harvester"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.1",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "pyyaml>=6.0",
    "click>=8.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.7",
    "mypy>=1.13",
    "types-PyYAML",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
# No sqlalchemy mypy plugin: it's deprecated, and there are no ORM models to type anyway
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["db: needs the PostGIS test database (POSTGRES_* settings)"]
addopts = "--strict-markers"   # a typo'd marker fails instead of silently selecting nothing

[build-system]
# uv's own backend; capped at the next minor so a uv release can't change how we build
requires = ["uv_build>=0.11.15,<0.12"]
build-backend = "uv_build"

[tool.uv.build-backend]
# Default would be src/iris_sig_08_harvester/ (derived from the project name)
module-name = "harvester"

[project.scripts]
harvester = "harvester.cli:main"
```

`uv.lock` and `.python-version` (3.12) are committed, so every checkout resolves the same
interpreter and package versions.

**Changed after the Phase 2 captures:** `playwright` was replaced by `httpx` (every page is a
plain GET) and `pyproj` dropped (PostGIS does the transform):
`uv remove playwright pyproj && uv add httpx`.

**Sync, not async, throughout.** The harvester is a sequential, deliberately throttled
crawler, so async gains nothing and costs complexity. So it uses:
- a sync `httpx.Client`
- a sync SQLAlchemy `Engine` / `Session` with psycopg 3
- plain synchronous pytest tests (no `pytest-asyncio`)

#### 2. `docker-compose.yml` ✅

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    # The official image is amd64-only; on Apple Silicon it runs under emulation.
    # Fine for <1000 rows; stated explicitly so the choice is visible.
    platform: linux/amd64
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-iris}
      POSTGRES_USER: ${POSTGRES_USER:-iris}
      # No default: compose fails fast if .env doesn't set it
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
    ports:
      # Host port follows POSTGRES_PORT, the same variable config.py uses to connect
      - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      # Creates the separate iris_test database used by the db-marked tests.
      # Mount the single file: mounting a folder over /docker-entrypoint-initdb.d would
      # hide the image's own 10_postgis.sh, and PostGIS would never be installed in `iris`.
      - ./docker/initdb/20_create_test_db.sql:/docker-entrypoint-initdb.d/20_create_test_db.sql:ro
    healthcheck:
      # -h 127.0.0.1 checks over TCP. During first-start init, Postgres runs a temporary
      # socket-only server, so this stays unhealthy until the init scripts have finished.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

#### 3. `.env.example` ✅

```
# Copy to .env (gitignored) and replace the placeholder. Never commit .env.
# docker-compose.yml and the harvester (src/harvester/config.py) both read these, so the
# password is set once. The connection URL is built from them; no need to URL-encode it.
POSTGRES_USER=iris
POSTGRES_PASSWORD=<choose-a-local-password>
POSTGRES_DB=iris
# Database for the db-marked tests. Must match the name created in
# docker/initdb/20_create_test_db.sql
POSTGRES_TEST_DB=iris_test
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Optional: append an operator contact to the scraper's user agent
# HARVESTER_USER_AGENT="IRIS-SIG-08-Harvester/1.0 (bot; contact: you@example.org)"
```

The repo commits no credential, not even a dev default. `.env` is already in `.gitignore`.

**No hand-written connection URL.** An earlier draft had `DATABASE_URL` and
`TEST_DATABASE_URL` next to `POSTGRES_PASSWORD`. That put the password in three places that
could drift apart, and a password containing `@`, `:`, `/` or `%` broke the URL unless
percent-encoded by hand. Now `config.py` builds both URLs from the `POSTGRES_*` parts with
`sqlalchemy.engine.URL.create()`, which does the encoding. Compose and the harvester read the
same variables.

The password never reaches logs: it is a `SecretStr` (masked in `repr()`), `str(URL)` renders
it as `***`, and the engine is built with `hide_parameters=True`.

#### 4. Alembic setup ✅

- `alembic.ini` -- points to `migrations/` directory. It holds **no** `sqlalchemy.url`.
- `migrations/env.py` builds its engine from `DatabaseSettings().url` and passes the
  connection to Alembic. Don't copy the URL into the ini with `set_main_option()`: it
  needs the plain-text password, and configparser treats `%` in it as interpolation.
- **Offline mode** (`alembic upgrade head --sql`) configures only `dialect_name="postgresql"`,
  so the SQL script can be produced without a `.env` or a running database.
- **Version table in our schema:** `version_table_schema="iris_staging"`. A default
  `public.alembic_version` would collide with any other Alembic-managed project in a shared
  database. Alembic creates its table before migration 001 runs, so `env.py` runs
  `CREATE SCHEMA IF NOT EXISTS iris_staging` first (committed separately online; emitted at
  the top of the script offline).
- `alembic.ini` post-write hooks run `ruff check --fix` and `ruff format` on every generated
  revision. Generate with `--rev-id 001` etc. so file names follow `001_<slug>.py`.
- `target_metadata = None`: there are no ORM models, so no autogenerate (and no GeoAlchemy2
  helpers or schema filters that autogenerate would need).
- `migrations/versions/001_create_schema_and_tables.py`, created with
  `alembic revision --rev-id 001 -m "create schema and tables"` and written **by hand as
  plain SQL**, copied from **Database Schema**. This DDL is the single source of truth for the
  schema.
  - **One `op.execute()` per statement**, not one string with several `;`-separated
    statements, so an error points at the right statement. `'{}'::text[]`-style casts are
    fine: `text()` treats `:name` as a parameter but recognises `::`.
  - `CREATE EXTENSION IF NOT EXISTS postgis`
  - `CREATE SCHEMA IF NOT EXISTS iris_staging` (already created by `env.py`; kept so the
    migration's own SQL is complete when read on its own)
  - Create `iris_staging.ingest_run`, then `iris_staging.signal_ingest` (it references
    `ingest_run`), with all columns and constraints. No `CREATE INDEX`: see **Database Schema**
  - `downgrade()` drops **only the two tables**. It keeps the `postgis` extension, which other
    schemas in a shared database may depend on, and the `iris_staging` schema, which holds
    Alembic's version table and may hold other pipelines' tables. Same rule for both: a
    migration only removes what it alone owns.

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
  (Playwright-only), `timeout_seconds: float` added; `LeipzigStadtSource.status_filter_id: int`
  added (252 = "im Verfahren").
- `rows_per_page` is not in the config: page sizes are a property of the portals, and both
  adapters page until no results are left.

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

### Success Criteria

#### Automated Verification
- [ ] `docker compose up -d` starts PostgreSQL with PostGIS (and fails clearly if `.env` is missing)
- [ ] `SELECT PostGIS_Version()` works in both `iris` and `iris_test`; the logs show both
      `10_postgis.sh` and `20_create_test_db.sql` ran
- [ ] `alembic upgrade head` creates schema and tables without errors
- [ ] `alembic downgrade base` + `alembic upgrade head` is idempotent
- [ ] `alembic upgrade head --sql` prints the full DDL without a database or `.env`
- [ ] `ruff check .` passes
- [ ] `mypy src/harvester/config.py` passes

#### Manual Verification
- [ ] `psql` shows `iris_staging.signal_ingest` and `iris_staging.ingest_run` tables
- [ ] `SELECT PostGIS_Version();` returns `3.4`
- [ ] Geometry column has SRID 25832
- [ ] The constraints bite: an insert with `geocode_status = 'source_coordinates'`
      and a NULL geometry fails with `ck_geometry_matches_status`, an unknown
      `geocode_status` value fails its CHECK, an empty `matched_keywords` fails its CHECK,
      and a repeated `(source, source_record_id)` fails `uq_source_record`

**Checkpoint:** Pause for confirmation before proceeding to Phase 2.

---

## Phase 2: Adapters and HTML Parsing ✅

### Overview

Implement `fetch()` and the two adapters (httpx + BeautifulSoup), producing `ScrapedRecord`
instances. Test with saved HTML fixtures (no live network).

### Changes Required

#### 0. Follow-ups from the captures ✅

- `pyproject.toml`: `uv remove playwright pyproj && uv add httpx` (see Phase 1 step 1).
- `config/settings.yml` + `config.py`: drop `headless` / `page_timeout_ms`, add
  `timeout_seconds` and `status_filter_id` (see Phase 1 step 5); adjust `tests/test_config.py`.
- Migration `001` (not applied anywhere but local dev databases): `geocode_status` values
  `source_coordinates` / `unresolved` / `rejected_out_of_bounds`, `ck_geometry_matches_status`
  on `source_coordinates`, `district` -> `location_text`, `date_source` dropped. Re-apply with
  `alembic downgrade base && alembic upgrade head`.
- README: remove the `playwright install chromium` step.

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
    kind: str              # 'missing_id' | 'missing_title' | 'markers_not_found' | 'count_mismatch'
                           # | 'unexpected_row_layout' | 'unexpected_status' | 'http_4xx' | 'retries_exhausted'
    message: str
    snippet: str | None = None  # marker JSON entry or row HTML, truncated to 2 KB


@dataclass
class ScrapeResult:
    records: list[ScrapedRecord] = field(default_factory=list)
    errors: list[ScrapeError] = field(default_factory=list)
```

**No adapter base class.** Each adapter is a module with a function of the same signature,
`scrape(client, source, settings) -> ScrapeResult`. The CLI picks it by the source's `type`
from `settings.yml`:

```python
SCRAPERS = {"sachsen": sachsen_portal.scrape, "leipzig_stadt": leipzig_stadtportal.scrape}
result = SCRAPERS[source.type](client, source, config.scraping)
```

#### 3. `src/harvester/adapters/fetch.py` ✅

`fetch(client, url, settings) -> str` with the retry and status rules from **Resilience**. It
raises `FetchError(kind, url, status, message)` when retries run out or on a non-retryable
4xx; adapters turn that into a `ScrapeError`.

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

#### 5. `src/harvester/adapters/leipzig_stadtportal.py` ✅

- `scrape(client, source: LeipzigStadtSource, settings) -> ScrapeResult`: follows the next-page
  button until there is none.
- `listing_url(source, page=1) -> str`
- `parse_rows(html, page_url) -> tuple[list[ScrapedRecord], list[ScrapeError]]`
- `parse_next_page(html) -> int | None`
- A page that fails to fetch ends the loop with a `ScrapeError`; records already read are kept.

#### 6. Test fixtures ✅

**One real page per markup the code parses**; every edge case is a minimal HTML snippet
inside its test, so the test shows exactly what is wrong with the input. Captured 2026-10-27,
**once, by hand** with `curl` (harvester user agent, 2 s apart), kept whole; provenance in
`tests/fixtures/SOURCES.md`, which covers "source access ... must be verified". It also lists
the pages checked but not kept (other cities and statuses use the same markup; the city
portal's detail page isn't parsed). The tests' reproducibility comes from the committed files,
not from how they were downloaded.

- `sachsen_dresden_listing_beendet.html`: 52 markers (5 industrial, titles with `&quot;`),
  10 cards, "von 52"
- `sachsen_dresden_detail.html`: "Zeitraum 12.10.2026 bis 13.11.2026"
- `leipzig_stadt_listing_im_verfahren.html`: 39 rows (10 industrial, several with multiple
  districts / Thema values), no next-page button

Inline snippets: empty listing, cards without marker JSON, marker without `id`, swapped / empty
/ non-numeric coordinates, "von 52" against 51 markers, the next-page button (copied from
the live markup in `SOURCES.md`), and HTTP errors via `MockTransport`.

#### 7. `tests/conftest.py` ✅ (DB fixtures follow in Phase 4)

- `tests/__init__.py` makes `tests` a package, so test modules import `load_fixture` and
  `MockClient` from `tests.conftest` and mypy sees one module, not two
- `load_fixture(name) -> str`
- `mock_client(routes: dict[str, str | int])`: `make_client()` with an `httpx.MockTransport`
  that serves fixture text (or a status code) per URL and **fails the test on any unrouted
  URL**, so tests can never reach the live portals. No extra dependency (MockTransport is part
  of httpx).
- `scraping_config` fixture: no polite delay and no retries, so adapter tests run instantly.

#### 8. `tests/test_sachsen_adapter.py` ✅

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

#### 9. `tests/test_leipzig_adapter.py` ✅

```python
def test_parses_rows():
    """39 rows; ID, title without icon text, detail_url, status; no coordinates or date."""

def test_splits_districts_and_thema():
    """Nr. 485: 'West / Schönau; Alt-West / ...' -> location_text; 3 Thema values."""

def test_row_without_thema_has_empty_list(): ...

def test_unexpected_status_is_an_error():
    """Snippet: a 'rechtskräftig' row -> unexpected_status, not stored."""

def test_handles_malformed_rows():
    """Snippet: no title link -> missing_title; too few cells -> unexpected_row_layout."""

def test_parse_next_page():
    """Real button markup -> 2; the fixture (no button) -> None."""

def test_follows_next_page():
    """Snippet page 1 with data-next=2, then the fixture (no button) as page 2: both fetched, then stop."""

def test_failed_page_is_an_error():
    """503 -> retries_exhausted error, no records, no exception."""
```

#### 10. `tests/test_fetch.py` ✅

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
- **Why only "im Verfahren" on the Leipzig city portal?** "rechtskräftig" plans are already in
  force, not hearings. In the unfiltered sample, all 22 industrial matches were rechtskräftig.
- **Why are Leipzig city-portal rows `unresolved`?** The source gives only a district, and the
  detail page has no coordinates or dates. A district centroid would be an invented point;
  `geocode_reason` says why instead.
- **Why only 3 fixture files?** One real page per markup the code parses. Edge cases (empty
  listing, malformed marker, next-page button, count mismatch, HTTP errors) are small inline
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
- **Why no `BaseAdapter` class?** Two fixed sources, both set by the brief. An abstract base
  pays off when implementations are swapped or added often; here it would only enforce one
  method name. Two module functions with the same signature, picked from a dict by the
  source's `type`, do the same with less code. (An ABC is in "Nice to Have" for a third source.)
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
  what is valid (bounding box, NaN) is one rule in the pipeline for every source. Values of the
  wrong JSON type (null, list) count as missing, with the original kept in `raw_data`.
- **Why does `fetch_detail` let `FetchError` through?** A failed detail page isn't a scrape
  error: the record is still usable. The pipeline keeps it and adds `detail_page_failed`.
- **Why `source = "sachsen_portal"` as a module constant, not from the config `name`?** It is
  the identifier stored in the database's natural key; it must not change if someone renames
  the config entry.
- **Why read the Leipzig cells by column position, not by content?** The table header fixes
  the order (Titel, Status, Stadtbezirk/ Ortsteil, Thema). Guessing "contains ` / ` means
  district" would misfile any Thema value that ever contains " / ". Too few cells is an error.
- **Why check each Leipzig row's status when the URL already filters on it?** The state portal
  silently ignores a lowercase status; if the city portal ever ignored its filter, ~500 plans
  already in force would be stored as hearings. A row that isn't "im Verfahren" becomes an
  `unexpected_status` error: visible, not stored.
- **Why no Leipzig detail pages?** Checked on 2026-10-27: they show only district, Thema,
  status and plan PDFs, nothing the row lacks. Fetching them would be ~40 requests for nothing.
- **Why does the page loop only move forward?** A next-page button pointing to the same or an
  earlier page would otherwise loop forever against a public portal.
- **Why `tests/__init__.py`?** It lets test modules import helpers from `tests.conftest`
  without mypy seeing the file under two module names.
- **Why return errors instead of raising?** One bad marker or row must not stop the run; bad
  entries are collected next to the good ones ("errors are visible").
- **Why `SNIPPET_MAX_CHARS = 2048`?** Enough to see what was wrong with an entry without
  bloating `ingest_run.errors`; one constant instead of each adapter repeating 2048.
- **Why one `fetch()` function for both portals?** Both need the same retry, 429 and delay
  rules; written once, tested once (`tests/test_fetch.py`).
- **Why no retry library (tenacity) or httpx's `retries=`?** httpx's built-in retries cover
  connection failures only, not 5xx or 429. tenacity would be a dependency to replace a short
  loop that can be explained line by line.
- **Which errors are retried?** Network errors and timeouts, 5xx and 429 can go away on their
  own. Any other 4xx (404, 403) won't, so it fails at once and is recorded.
- **What does `max_retries: 3` mean?** 3 retries after the first try, so at most 4 requests per
  URL, waiting 2, 4, 8 s between them: about 14 s worst case per URL.
- **Why honour only the seconds form of `Retry-After`?** Neither portal has sent a 429 at all;
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
- [x] `pytest tests/test_sachsen_adapter.py tests/test_leipzig_adapter.py tests/test_fetch.py` -- all pass
- [x] `ruff check src/harvester/adapters/` passes
- [x] `mypy src/harvester/adapters/ src/harvester/models.py` passes

#### Manual Verification
- [x] Fixture HTML files are whole, unedited captures of the live portals (2026-10-27)
- [x] `tests/fixtures/SOURCES.md` lists every fixture and the pages checked but not kept
- [x] Status-filter mechanism, publication-date element and ID scope confirmed (Key Discoveries 5-8)
- [x] `ScrapedRecord` fields match expectations for each fixture (checked by hand against the pages, 2026-10-28)
- [x] Live smoke run of both `scrape()` functions (2026-10-28): state portal 83 records
      (Dresden 60, Leipzig 23), city portal 39, no errors; a live Leipzig state-portal
      detail page gave its Zeitraum (body-class check holds for Leipzig too)

**Checkpoint:** Pause for confirmation before proceeding to Phase 3.

---

## Phase 3: Pipeline (Filter, Detail Dates, Geocode, Normalise) ✅

### Overview

Turn `ScrapedRecord`s into `NormalisedRecord`s: filter (dropping non-matches), fetch the
state-portal detail page of each match, parse the date, check the coordinates.

### Changes Required

#### 1. `src/harvester/pipeline/filter.py` ✅

Industrial keyword filter as specified above. `IndustrialFilter(config.industrial_filter)`
takes the validated config section directly.

#### 2. `src/harvester/pipeline/geocode.py` ✅

- `check_coordinates(lon, lat, city, bbox) -> GeocodeResult` (a NamedTuple of `lon, lat,
  status, reason`) following the table in **Coordinates and Bounding Box**. Pure Python; the
  transform happens in the upsert SQL.
- It only judges coordinates. The Leipzig "district only" reason is set in `normalise`, which
  knows the source gives no coordinates at all.

#### 3. `src/harvester/pipeline/normalise.py` ✅

- `normalise(scraped, client, config) -> NormaliseResult(records, filtered_out)`. It takes the
  HTTP client because only matches get their detail page fetched.
- Order per record: filter -> (state portal, match) `fetch_detail` -> parse date -> check
  coordinates -> `NormalisedRecord`. Non-matches are dropped and counted as
  `records_filtered_out`.
- Publication date: the first `DD.MM.YYYY` in the "Zeitraum" text to `datetime.date`,
  following **Publication Dates**. Unparseable text becomes NULL plus `unparseable_date`.
- Quality flags: `missing_publication_date`, `missing_location`, `unparseable_date`,
  `detail_page_failed`. A failed detail page also gets `missing_publication_date` (the date is
  missing; the other flag says why). `missing_location` means neither a point nor a
  `location_text`.
- Leipzig city-portal rows: `unresolved` with `source gives district only: <districts>` (or
  `source gives no location` if the row has none), set here rather than in `geocode.py`.
- `raw_data` gains `filter_evidence`, and for state-portal matches `zeitraum` (raw text) and,
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
def test_matches_thema_gewerbe_among_several():  # ["Wohnen", "Gewerbe"] -> thema:Gewerbe
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
def test_leipzig_row_unresolved_without_date():  # missing_publication_date, reason lists districts
def test_leipzig_row_without_district_is_missing_location():
def test_detail_fetched_only_for_matches():      # mock client sees no request for non-matches
def test_detail_page_404_flagged():               # detail_page_failed, listing data kept
def test_out_of_bounds_marker_is_rejected():      # swapped lat/lon end to end
def test_non_industrial_records_dropped_and_counted():
```

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why does the filter run before the detail page is fetched?** Only matches cost a second
  request: on the Dresden fixture 5 of 52 records, instead of all 52. (Step 1, built)
- **Why a Thema match as well as the title?** Leipzig titles often name only a street ("Weheweg");
  the city's own "Gewerbe" tag catches those. It also lets in mixed areas (Nr. 456
  Alfred-Kunze-Sportpark); the `thema:` prefix in `matched_keywords` shows which rows came in
  that way, so they can be reviewed. (Step 1, built)
- **What does the filter miss?** Plans whose title and Thema say nothing industrial (Nr. 422
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
- **Why no date for Leipzig city-portal rows?** The source publishes none; inventing one (e.g.
  the run date) would break "verified rather than assumed". (Step 3, built)
- **Why match on the title (and Leipzig Thema) only, not descriptions?** Descriptions mention
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
- **Why no geocoding of addresses or Leipzig districts?** The brief asks for points "where the
  source provides sufficient information". A district is an area, and an external geocoder
  adds a dependency and its own errors; see "What We Are NOT Doing". (Step 2)
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
  with a script, since `--dry-run` arrives in Phase 4: Dresden 5 of 52, Leipzig 10 of 39
- [x] Live run of scrape + normalise (2026-10-28): state portal 83 scraped, 9 kept (7 Dresden,
  2 Leipzig), all with a detail-page date and `source_coordinates`, no flags; city portal 39
  scraped, 10 kept, all `unresolved` + `missing_publication_date`; no errors

**Checkpoint:** Pause for confirmation before proceeding to Phase 4.

---

## Phase 4: Upsert, CLI, and Integration ✅

### Overview

Wire adapters to pipeline to database. Implement the CLI entry point, upsert logic,
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
- One `ingest_run` per source per invocation. `city` is set when the run covers one city
  (city portal, or `--city`), NULL when the state portal covers both.
- `records_rejected` counts the record-level errors (`missing_id`, `missing_title`,
  `unexpected_row_layout`, `unexpected_status`); page-level ones are only in `errors`.

#### 4. `src/harvester/cli.py` (+ `__main__.py`) ✅

- `harvester run [--dry-run] [--source NAME] [--city SLUG]` (also `python -m harvester run`).
  An unknown source or city is a usage error, not an empty run.
- `--dry-run` prints every scraped record (KEEP with keywords, date, geocode, flags, or `-`)
  and the scrape errors; it needs no database.
- A normal run prints one summary line per source and exits 1 if any run failed.
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

# tests/test_pipeline_integration.py  (db, mocked portals)
def test_full_pipeline_fixture_to_db():         # 52 -> 5 and 39 -> 10, dates, flags, traced runs
def test_full_pipeline_twice_is_idempotent():   # (0, 5) and (0, 10); same ids
def test_errors_visible_in_ingest_run():        # unexpected_status in errors, records_rejected
def test_unreachable_source_is_a_failed_run():  # 503 -> failed, error_message
def test_crash_rolls_back_records_and_fails_run():

# tests/test_cli.py
def test_select_by_city_narrows_state_portal():
def test_unknown_source_or_city_is_an_error():
def test_dry_run_prints_decisions_without_database():
```

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **What does CI check, and when?** `.github/workflows/ci.yml` runs on every push to main:
  ruff, mypy, the migrations (up, down, up) and the whole pytest suite, including the db tests
  against a `postgis/postgis:16-3.4` service container. The README badge shows the latest
  result on main (green "passing" / red "failing"). (Built before Phase 4)
- **Why trust auth in CI rather than a password secret?** The database is a throwaway container
  on the runner, so there is no credential to store or leak; `POSTGRES_PASSWORD` is set only
  because `DatabaseSettings` requires a value. (Built before Phase 4)
- **What does one `ingest_run` row cover?** One source in one invocation. The state portal's
  scrape reads both cities in one go, so `city` is NULL there; the report breaks rows down by
  city from `signal_ingest`. (Step 3, built)
- **Why is the run row committed before the scrape?** So a run that crashes or hangs is still
  on record (`running` / `failed`), not lost with a rolled-back transaction. (Step 3, built)
- **Why do the records and the run's `completed` status commit together?** A run is either
  fully written with its counts, or not at all and marked `failed`; never half-written. A test
  crashes after the upsert and checks no rows remain. (Step 3, built)
- **What happens when one portal is down?** Its run is `failed` with the error, the other
  source still runs, and the CLI exits 1 so a scheduler notices. (Step 3, built)
- **Why a separate `runner.py` instead of logic in the CLI?** The integration tests call the
  same `run_source` the CLI calls, without click; the CLI only parses options and prints.
  (Step 3, built)
- **Why do the db tests truncate instead of rolling back?** `run_source` commits twice on
  purpose (see above), which an outer test transaction can't contain. (Step 1, built)
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
- [x] `python -m harvester run --dry-run` executes without error (mocked portals, `test_cli.py`)
- [x] `ruff check .` passes
- [x] `mypy .` passes

#### Manual Verification (live, 2026-10-28)
- [x] `python -m harvester run` against live portals populates the DB: state portal 83 scraped,
  9 inserted (7 Dresden, 2 Leipzig, all with point and date); city portal 39 scraped, 10
  inserted (all `unresolved`, no date)
- [x] `SELECT count(*), source FROM iris_staging.signal_ingest GROUP BY source` shows 9 + 10
- [x] Re-run produces 0 inserts (9 + 10 updated) and the same row count
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
  source               city     status     started (UTC)     scraped  industrial  rejected  inserted  updated  errors
  leipzig_stadtportal  leipzig  completed  2026-10-28 07:47       39          10         0        10        0       0
  sachsen_portal       all      completed  2026-10-28 07:46       83           9         0         9        0       0

Run errors
  none

Stored records
  source               city     records  with point  unresolved  rejected bbox
  leipzig_stadtportal  leipzig       10           0          10              0
  sachsen_portal       dresden        7           7           0              0
  sachsen_portal       leipzig        2           2           0              0

Quality flags
  source               city     flag                      records
  leipzig_stadtportal  leipzig  missing_publication_date       10
```

Built differently from the first draft of this plan:
- One block per **source**, not per run and city ("sachsen_portal (dresden)"): since
  Phase 4 a run covers a whole source, so the per-city view comes from `signal_ingest`.
- No "8 AKTUELLE + 52 BEENDETE" split of the scraped count: it isn't stored, and adding a
  column only for the report isn't worth it.
- `report.py` next to `runner.py` instead of a `reporting/` package holding one module.
- No per-record list and no keyword ranking: the brief asks for a concise report, and
  neither serves an acceptance criterion.
- Tables instead of labelled blocks per run; no run ID, finish time, filtered-out count or
  match percentage (scraped and industrial sit side by side; the rest is in `ingest_run`).

#### 2. `sql/validation_queries.sql` ✅

10 checks, each headed by an `\echo` so the output of
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
| 10 | The same plan on both portals (city + plan number) | documents the source overlap |

Live results on 2026-10-28: the "expect 0" checks return 0 rows; check 10 finds Leipzig
Nr. 911 on both portals. Check 6 was also tested by moving one point to Berlin inside a
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

#### 3. `sample_output/signal_ingest_sample.csv` and `sample_output/signal_ingest_sample.geojson`

Generated from a real run against the portals. CSV with all columns. GeoJSON with the rows
that have a geometry, for visual inspection.

Written by `sql/export_samples.sql` with `psql`: `\copy (SELECT …) TO … CSV HEADER` for
the CSV, and one query building the FeatureCollection with `json_build_object` /
`json_agg` and `ST_AsGeoJSON(ST_Transform(geometry, 4326))` (GeoJSON is WGS84 by spec).
No Python export command: nothing more to maintain, and anyone can repeat it with `psql`.
The README's database checks move into `validation_queries.sql`, and the README points
to the file.

#### 4. `README.md`

Full README covering:
- Project description and scope
- Prerequisites (uv, Docker; uv provides Python 3.12 via `.python-version`)
- Setup (`uv sync`, `.env`, `docker compose up -d`, `uv run alembic upgrade head`), started
  in Phase 1 and extended as steps are added.
- Quick start (`uv run harvester run`)
- Configuration (`settings.yml`, environment variables)
- Architecture overview (adapters, pipeline, DB)
- Testing (`pytest`)
- Validation (`sql/validation_queries.sql` via `psql`)
- Data source and access: both portals, public GET pages only, verified 2026-10-27
- Mapping assumptions: publication date = "Zeitraum" start; marker coordinates as the
  procedure location; Leipzig city portal limited to "im Verfahren"
- Known limitations (Leipzig city-portal rows unresolved and undated, title-only filter,
  source overlap, EPSG zone note, dependence on the `drawMarkers` script call)
- Adding new cities/districts

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

- **Why are the two portals' copies of the same plan separate rows?** Each is a signal with its
  own URL and source ID; merging them is future work. Validation check 10 lists the pairs by
  city and plan number (e.g. Nr. 911).
- **Why EPSG:25832 although both cities lie east of 12° E?** The brief requires it; it's usable
  there with growing scale distortion, documented in the README.
- **Why does the report show the latest run per source, but all stored records?** The run
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
- **Why export samples with SQL rather than a Python command?** It's two queries, needs no
  new code to test or defend, and is repeatable by anyone with `psql`.
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

### Success Criteria

#### Automated Verification
- [ ] `pytest` -- full suite passes
- [ ] `ruff check .` passes
- [ ] `mypy .` passes
- [x] `python -m harvester report` generates report without error (live, 2026-10-28)

#### Manual Verification
- [ ] README instructions produce a working setup from scratch
- [x] `sql/validation_queries.sql` runs against populated DB and returns sensible results
  (live, 2026-10-28)
- [ ] Sample CSV and GeoJSON open correctly in QGIS / geojson.io
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
  the live portals.
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

- **Request volume (2026-10-27):** 4 state-portal listings + 1 city-portal listing + one
  detail page per state-portal match (~9) = ~14 requests, under a minute at a 2 s delay. The
  live run is quick enough to demo; re-run and idempotency demos also work against fixtures.
- **Database:** Small table (<1000 rows, only industrial matches). No indexes beyond the
  primary key and `uq_source_record`; see **Database Schema**.
- **Memory:** HTML pages are small (<700 KB each). No memory concerns.
- **HTTP:** one `httpx.Client` reused across all adapters (connection reuse).

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
  non-empty `sources` / `cities` / `status_filters`, and a discriminated union on the source
  `type` for clearer error messages.
- **Adapter base class (`BaseAdapter` ABC).** Worth it once a third source is added; with two
  fixed sources, module functions with the same `scrape()` signature are enough.

---

## References

- Saxon state portal: `https://buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen`
- Leipzig city portal: `https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene`
- BauGB (Federal Building Code): procedure phases SS 2-10
- BauNVO (Building Use Ordinance): zone type definitions (GE, GI, SO, etc.)
- EPSG:25832: ETRS89 / UTM zone 32N
- EPSG:25833: ETRS89 / UTM zone 33N (Dresden's native zone)
