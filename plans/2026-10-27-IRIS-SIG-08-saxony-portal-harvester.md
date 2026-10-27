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

1. **Two distinct portal architectures.** Dresden is served by the Saxon state portal
   (`buergerbeteiligung.sachsen.de`, JSF/PrimeFaces). Leipzig has entries on the state
   portal AND a comprehensive city portal (`www.leipzig.de`, TYPO3 CMS). Both are fully
   server-side rendered.

2. **No coordinates in HTML.** Neither portal embeds geographic data. Geometry comes from a
   checked-in district centroid lookup table.

3. **EPSG zone mismatch.** Dresden (~13.7 deg E) sits in UTM zone 33N (EPSG:25833), outside the
   nominal coverage of the target CRS EPSG:25832 (UTM zone 32N, 6--12 deg E). Points are
   reprojected via PostGIS `ST_Transform` or pyproj. This is documented and the bounding-box
   check catches any gross errors.

4. **Small result sets for state portal** (Dresden 8 active, Leipzig 2 active), **large for
   Leipzig city portal** (555 total Bebauungsplaene). All plans (active + historical) are in scope.

5. **The state portal hides finished procedures by default.** The listing has a PrimeFaces
   status filter (`filterForm:beteiligungFilter:statusFilter:selectManyCheckbox_statusFilter`)
   with two values, `BeteiligungStatusFilter#AKTUELLE` and `BeteiligungStatusFilter#BEENDETE`.
   The default view shows only current procedures; that is why the Dresden listing renders 8
   cards and no paginator. To reach historical plans, the adapter must also select `BEENDETE`
   (see the adapter flow below).

### Portal Details

| Attribute | Saxon State Portal (Dresden + Leipzig) | Leipzig City Portal |
|---|---|---|
| Base URL | `buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen` | `www.leipzig.de/rathaus/buergerbeteiligung/.../bebauungsplaene` |
| Framework | Jakarta Faces / PrimeFaces | TYPO3 + `tx_lepurpose` extension |
| Pagination | PrimeFaces AJAX POST (click paginator, ViewState managed by browser) | GET param `tx_lepurpose[filter][page]=N`, 50 rows/page |
| Listing selector | `div.nbp-teaser` cards | `tr[id^="project-"]` table rows |
| Detail URL | `/portal/{city}/beteiligung/themen/{numeric_id}` | `.../bebauungsplaene/detail/projekt/{slug}` |
| Stable ID | Numeric ID from URL path | `project-{id}` from row, slug from link |
| Status filter | PrimeFaces checkboxes `#AKTUELLE` / `#BEENDETE`; default shows current only | `tx_lepurpose[filter][89][]` (251 rechtskraeftig, 252 im Verfahren) |
| Items | Dresden: 8 current (verified 2026-10-27), ~243 finished (research estimate, to verify); Leipzig: 2 current, finished count to verify | ~555 |

---

## Desired End State

- `docker compose up` starts PostgreSQL 16 / PostGIS 3.4.
- `alembic upgrade head` creates `iris_staging` schema, `signal_ingest` + `ingest_run` tables, PostGIS extension.
- `python -m harvester run` scrapes all three sources, filters, upserts, and writes a quality report.
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
- Online geocoding (Nominatim, Google, etc.). District centroid lookup only.
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
- Incremental retrieval (skipping unchanged detail pages via a listing hash). The brief asks
  for idempotent re-runs, not fast ones; every run fetches every detail page (~30 min live,
  seconds against fixtures). A possible optimisation if live runs become frequent.
- Change detection (`content_hash`, `last_seen_*` columns, an "unchanged" counter). The
  brief asks for no duplicates on re-run, which the unique key plus `ON CONFLICT` guarantees
  on its own. Nothing consumes "what changed", so existing rows are simply rewritten.
- Storing non-industrial records. The objective is to *filter*; non-matches are only counted
  (`records_filtered_out`). `--dry-run` prints every record with its filter decision for
  tuning the keyword list.
- Columns the brief doesn't ask for (plan type, plan number, procedure step, status,
  participation period, Thema, description). They are kept as scraped in `raw_data`.
- Secondary indexes. Under 1000 rows, PostgreSQL scans the table faster than it would use an
  index; only the unique index behind `uq_source_record` exists, because `ON CONFLICT` needs it.
- Deleting rows that vanish from a portal or stop matching the filter. They stay, with an
  older `ingest_run_id` (validation query 7).

---

## Implementation Approach

Three adapters behind a common `ScrapedRecord` model. Each adapter uses Playwright
headless to navigate listing pages, paginate, and extract detail pages. A shared pipeline
normalises records, applies the industrial keyword filter, resolves geometry from the
centroid lookup, and upserts into PostGIS.

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
    settings.yaml                   # city/source list, keyword list, bounding boxes
  data/
    district_centroids.csv          # checked-in centroid lookup (WGS84, with provenance)
  migrations/
    env.py
    versions/
      001_create_schema_and_tables.py
  src/
    harvester/
      __init__.py
      __main__.py                   # `python -m harvester`: calls cli.main()
      cli.py                        # click CLI; `harvester` console script (pyproject)
      config.py                     # pydantic-settings: POSTGRES_* -> DB URL; settings.yaml
      models.py                     # ScrapedRecord, IngestRun, NormalisedRecord
      db/
        __init__.py
        engine.py                   # SQLAlchemy engine from DatabaseSettings().url
        upsert.py                   # INSERT ... ON CONFLICT (source, source_record_id)
      adapters/
        __init__.py
        base.py                     # BaseAdapter ABC, ScrapeResult, ScrapeError
        fetch.py                    # fetch_page(): retries, status checks, polite delay
        sachsen_portal.py           # PrimeFaces adapter (Dresden + Leipzig)
        leipzig_stadtportal.py      # TYPO3 adapter
      pipeline/
        __init__.py
        normalise.py                # field normalisation, date parsing
        filter.py                   # industrial keyword matcher
        geocode.py                  # district centroid lookup + bounding box
      reporting/
        __init__.py
        quality_report.py           # generates report from ingest_run data
  scripts/
    capture_fixtures.py             # one-off: save trimmed live pages as test fixtures
  sql/
    validation_queries.sql          # post-ingest verification
  tests/
    __init__.py
    conftest.py                     # Playwright browser fixture, DB fixtures
    fixtures/
      sachsen_dresden_listing.html
      sachsen_dresden_listing_beendet.html
      sachsen_dresden_detail.html
      sachsen_leipzig_listing.html
      sachsen_leipzig_detail.html
      leipzig_stadt_listing.html
      leipzig_stadt_listing_page2.html
      leipzig_stadt_detail.html
      empty_listing.html
      malformed_card.html
    test_sachsen_adapter.py
    test_leipzig_adapter.py
    test_fetch.py                   # retry on 5xx/429/timeouts, no retry on 4xx
    test_normalise.py
    test_filter.py
    test_geocode.py
    test_upsert.py                  # includes re-run idempotency + duplicate tests
    test_bounding_box.py            # out-of-bounds coordinate rejection
    test_pipeline_integration.py
    test_quality_report.py
  sample_output/
    signal_ingest_sample.csv
    signal_ingest_sample.geojson
  plans/
    2026-10-27-IRIS-SIG-08-saxony-portal-harvester.md  # this file
  README.md
```

---

## Configuration (settings.yaml)

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
    # One scrape pass per status; BEENDETE is needed for historical plans
    status_filters: ["AKTUELLE", "BEENDETE"]
    rows_per_page: 10

  - name: leipzig_stadtportal
    type: leipzig_stadt
    base_url: "https://www.leipzig.de"
    listing_path: "/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene"
    rows_per_page: 50

scraping:
  headless: true
  request_delay_seconds: 2.0
  max_retries: 3
  retry_backoff_factor: 2.0
  # Override via HARVESTER_USER_AGENT env var to add an operator contact address
  user_agent: "IRIS-SIG-08-Harvester/1.0 (bot)"
  page_timeout_ms: 30000

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
  # Leipzig Thema field direct match
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
| "publication dates" + "verified rather than assumed" | `publication_date`, `date_source` |
| "source URLs" / "traceable staging records" | `detail_url`, `ingest_run_id` |
| "geographic context when available" | `city`, `district` |
| "EPSG:25832 point geometries" | `geometry` |
| "document ambiguous or unresolved locations" / "invalid coordinates are not silently accepted" | `geocode_status`, `geocode_reason` |
| "errors and missing fields are visible" | `quality_flags` |
| "filter industrial land-use zoning hearings" (why this row matched) | `matched_keywords` |
| "malformed data" / "mapping assumptions" (the values as scraped) | `raw_data` |

Only industrial matches are stored: the objective says *filter*. Non-matching records are
counted in `ingest_run.records_filtered_out`, not stored. Fields that are scraped but not
required (plan type, procedure step, status, participation period, Thema, description) live
in `raw_data` only.

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
    district         TEXT,                      -- Stadtbezirk/Ortsteil

    publication_date DATE,                      -- only from an explicit publication field; never derived
    date_source      TEXT                       -- NULL if no publication date found
                     CHECK (date_source IN ('listing_card', 'detail_page')),

    -- Only industrial matches are stored, so there is always at least one keyword
    matched_keywords TEXT[] NOT NULL CHECK (cardinality(matched_keywords) > 0),

    geometry         GEOMETRY(Point, 25832),
    geocode_status   TEXT NOT NULL
                     CHECK (geocode_status IN
                            ('resolved_district_centroid', 'unresolved', 'rejected_out_of_bounds')),
    geocode_reason   TEXT,                      -- e.g. 'district: Nordost / Schoenefeld-Ost' or rejection reason

    quality_flags    TEXT[] NOT NULL DEFAULT '{}',  -- e.g. 'missing_publication_date', 'missing_district',
                                                    -- 'unparseable_date', 'detail_page_failed'
    raw_data         JSONB NOT NULL,            -- all scraped fields as-is, plus filter evidence

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- first seen
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- last written, i.e. last seen

    -- Named: ON CONFLICT and the tests refer to it
    CONSTRAINT uq_source_record UNIQUE (source, source_record_id),
    -- A point exists exactly when the location was resolved: the database itself refuses a
    -- point without a resolved status, or a resolved status without a point
    CONSTRAINT ck_geometry_matches_status
        CHECK ((geocode_status = 'resolved_district_centroid') = (geometry IS NOT NULL))
);
```

### Upsert Logic

Idempotency comes from the natural key: `uq_source_record UNIQUE (source, source_record_id)`
plus `INSERT ... ON CONFLICT DO UPDATE`. Re-running over the same data cannot add rows. There
is **no change detection**: an existing row is always rewritten with the latest values. Nothing
in the brief consumes "what changed", so telling a real change apart from a re-scrape isn't
worth a hash column and its tests (see "What We Are NOT Doing").

**Step 0: de-duplicate the batch in Python.** PostgreSQL rejects an `INSERT ... ON CONFLICT
DO UPDATE` that touches the same key twice ("command cannot affect row a second time").
Records with a repeated `(source, source_record_id)` in one batch (e.g. a card shown on two
listing pages while the portal re-sorts) are collapsed to the last occurrence. Each one
dropped is logged to `ingest_run.errors` as `duplicate_in_batch`.

**Step 1: insert or overwrite.**

```sql
INSERT INTO iris_staging.signal_ingest (
    source, source_record_id, ingest_run_id,
    city, title, detail_url, district,
    publication_date, date_source, matched_keywords,
    geometry, geocode_status, geocode_reason, quality_flags, raw_data
)
VALUES (
    ...,
    -- Geometry is built in SQL from the transformed coordinates; NULL x/y gives NULL
    ST_SetSRID(ST_MakePoint(:x_25832, :y_25832), 25832),
    ...
)
ON CONFLICT (source, source_record_id) DO UPDATE SET
    ingest_run_id    = EXCLUDED.ingest_run_id,
    city             = EXCLUDED.city,
    title            = EXCLUDED.title,
    detail_url       = EXCLUDED.detail_url,
    district         = EXCLUDED.district,
    publication_date = EXCLUDED.publication_date,
    date_source      = EXCLUDED.date_source,
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
latest one for its source (validation query 7 lists these).

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


@dataclass(frozen=True)
class FilterResult:
    is_industrial: bool
    matched_keywords: list[str] = field(default_factory=list)  # canonical keywords from config
    evidence: list[str] = field(default_factory=list)  # matched words, e.g. 'Gewerbestandort'


class IndustrialFilter:
    def __init__(self, keywords: list[str], case_sensitive_tokens: list[str],
                 thema_signals: list[str]) -> None:
        # \w* on both sides captures the full compound word around the keyword
        self._ci = [(kw, re.compile(rf"\w*{re.escape(kw)}\w*", re.IGNORECASE)) for kw in keywords]
        self._cs = [(tok, re.compile(rf"\b{re.escape(tok)}\b")) for tok in case_sensitive_tokens]
        self._thema = set(thema_signals)

    def _scan(self, text: str) -> tuple[list[str], list[str]]:
        keywords: list[str] = []
        evidence: list[str] = []
        for kw, pattern in [*self._ci, *self._cs]:
            hits = [m.group() for m in pattern.finditer(text)]
            if hits:
                keywords.append(kw)
                evidence.extend(hits)
        return keywords, evidence

    def check(self, title: str, description: str | None, thema: str | None) -> FilterResult:
        keywords: list[str] = []
        evidence: list[str] = []

        if thema and thema in self._thema:
            keywords.append(f"thema:{thema}")

        for text in (title, description):
            if not text:
                continue
            kws, ev = self._scan(text)
            keywords.extend(kws)
            evidence.extend(ev)

        unique = list(dict.fromkeys(keywords))
        return FilterResult(
            is_industrial=bool(unique),
            matched_keywords=unique,
            evidence=list(dict.fromkeys(evidence)),
        )
```

`evidence` goes into `raw_data` and the quality report, so false positives (e.g.
"Gewerbesteuer" in a description) can be spotted and the keyword list tuned.

Only records with `is_industrial = True` are stored; `matched_keywords` is the stored
evidence for why each row is there (a `thema:` prefix marks a Leipzig Thema match). The
filter reads `description` and `thema`, but those are kept only in `raw_data`. Non-matches
are counted in `ingest_run.records_filtered_out`, and `--dry-run` prints every record with its
filter decision, so false negatives can be checked while tuning the keyword list.

---

## District Centroid Lookup

Checked-in file: `data/district_centroids.csv`

```csv
city,district,lat_wgs84,lon_wgs84,provenance
dresden,Altstadt,51.0480,13.7370,"OpenStreetMap Nominatim, 2026-10"
dresden,Neustadt,51.0640,13.7480,"OpenStreetMap Nominatim, 2026-10"
dresden,Pieschen,51.0770,13.7130,"OpenStreetMap Nominatim, 2026-10"
dresden,Klotzsche,51.1130,13.7700,"OpenStreetMap Nominatim, 2026-10"
...
leipzig,Mitte,51.3400,12.3750,"OpenStreetMap Nominatim, 2026-10"
leipzig,Nordost / Schoenefeld-Ost,51.3600,12.4100,"OpenStreetMap Nominatim, 2026-10"
leipzig,Suedost / Reudnitz-Thonberg,51.3300,12.4000,"OpenStreetMap Nominatim, 2026-10"
...
```

Provenance column documents the source and date for each centroid. All coordinates are
WGS84 (EPSG:4326); the pipeline converts to EPSG:25832 via pyproj at load time.

**Bounding box rejection:** Centroids are checked in WGS84 against the per-city bounding
box (from `settings.yaml`) **before** being transformed to EPSG:25832. Any point outside
is rejected with `geocode_status = 'rejected_out_of_bounds'` and a `geocode_reason`
explaining the violation. These rows are stored with `geometry = NULL`, never with the
bad point. Non-finite or swapped lat/lon values (e.g. lat 13.7, lon 51.0) fail the same
check. The transformed point is also checked to be finite before it is stored. Validation
query 9 re-checks stored geometries in SQL.

**Note on EPSG:25832 and Dresden:** Dresden at ~13.7 deg E is east of UTM zone 32N's nominal
12 deg E boundary. The CRS is still usable (UTM zones have overlap), but distortion increases.
This is documented in the README and the centroid CSV provenance notes.

---

## Adapter Details

### Saxon State Portal Adapter (`sachsen_portal.py`)

**Navigation flow (Playwright):**

1. `page.goto(f"{base_url}/portal/{city}/beteiligung/themen?format=Bauleitplan")`
2. Run one pass per status in `status_filters` from config (default `[AKTUELLE, BEENDETE]`).
   For each pass, set the status checkboxes so only that status is selected, then wait for the
   PrimeFaces AJAX re-render. Checking the inputs by `value` attribute
   (`input[value$="#BEENDETE"]`) avoids depending on the generated `j_idt` IDs. Running separate
   passes keeps each one's paginator count ("1 - 10 von N") as a completeness check: after each
   pass, assert that the cards collected equal N, and log a run error if they don't.
   Phase 2 must first confirm whether a GET parameter can set the status filter. If one can,
   prefer it over clicking.
3. Wait for `div.nbp-teaser` to appear (or timeout = empty result).
4. Extract all cards on the page (only title, link and dates become columns; the rest goes
   into `raw_data` as scraped):
   - Title: `h3.nbp-teaser-title > a` (text + href)
   - Plan type: `span.nbp-teaser-taglist--beteiligungstyp`
   - Authority: `span.nbp-teaser-taglist--mandant`
   - Procedure step: `span.nbp-teaser-taglist--verfahrensschritt`
   - Status: `li.nbp-statistik--status`
   - Dates: `li.nbp-statistik--laufzeit` (parse "DD.MM.YYYY bis DD.MM.YYYY")
   - Submissions: `li.nbp-statistik--stellungnahmen`
5. If paginator exists, click the "next" button (`a` in paginator with `ui-paginator-next`),
   wait for content update, repeat. Playwright handles the PrimeFaces AJAX/ViewState
   transparently.
6. For each card, navigate to the detail page to scrape description and publication date.
7. Between page loads: `page.wait_for_timeout(config.request_delay_seconds * 1000)`.

**Source record ID:** Numeric ID extracted from the detail URL path.

**Empty result handling:** If no `div.nbp-teaser` elements found after page load, return
an empty list (not an exception). Log a warning. The `ingest_run` records 0 scraped.

### Leipzig City Portal Adapter (`leipzig_stadtportal.py`)

**Navigation flow (Playwright):**

1. `page.goto(f"{base_url}{listing_path}")` -- loads first 50 rows.
2. Wait for `tr[id^="project-"]` rows.
3. Extract all rows (status and Thema go into `raw_data`; Thema is also a filter input):
   - Title + detail link: `a.filterlist-detailpage > span`
   - Status: 2nd `td > ul > li` text
   - District: 3rd `td > ul > li` text
   - Thema: 4th `td > ul > li` text
   - Project ID: from `tr` id attribute (e.g., `project-1751` -> `1751`)
4. Paginate via `page.goto` with `?tx_lepurpose[filter][page]=N` (N = 2, 3, ...) until
   no more rows appear.
5. For each row, navigate to detail page for description and publication date.
6. Polite delay between requests.

**Source record ID:** Project ID from the table row (e.g., `1751`).

### Common: ScrapedRecord Model

```python
from datetime import date

from pydantic import BaseModel

class ScrapedRecord(BaseModel):
    source: str                    # 'sachsen_portal' | 'leipzig_stadtportal'
    source_record_id: str          # stable ID from the source
    city: str                      # 'dresden' | 'leipzig'
    title: str
    detail_url: str
    district: str | None = None
    publication_date: date | None = None
    date_source: str | None = None
    description: str | None = None  # filter input only; stored in raw_data
    thema: str | None = None        # filter input only (Leipzig); stored in raw_data
    raw_html: str | None = None     # preserved for debugging, not stored in DB
    raw_data: dict                  # all scraped fields as-is (incl. status, procedure step, period)
```

---

## Resilience

- **Retries with exponential backoff:** Playwright's `page.goto()` does **not** raise on HTTP
  error statuses; it returns a `Response`. Every navigation goes through one helper,
  `fetch_page()`, which treats these as retryable, up to `max_retries` attempts with
  `retry_backoff_factor` between them:
  - Playwright `TimeoutError` / `Error` (network failures, DNS, connection reset)
  - `response is None`
  - `response.status >= 500`
  - `429`, honouring `Retry-After` when present

  Any other `4xx` is not retried. It is recorded as a page-level error (e.g. a detail page
  that 404s gets `quality_flags = ['detail_page_failed']` and keeps its listing data).
- **Polite delay:** `request_delay_seconds` (default 2s) between all page loads.
- **User agent:** Neutral identifying string (`IRIS-SIG-08-Harvester/1.0 (bot)`), overridable
  via the `HARVESTER_USER_AGENT` env var so an operator can add a contact address without
  committing it to source.
- **Timeout:** `page_timeout_ms` (default 30s) per page load.
- **Empty response handling:** An adapter that receives zero results returns an empty list
  and logs a warning. It does not raise an exception. The `ingest_run` row records the count.
- **Malformed records:** Adapters return a `ScrapeResult(records, errors)`, not a bare list.
  The handling depends on how bad the record is:
  - **Unusable** (no stable ID or no title): becomes a `ScrapeError` with the raw HTML
    snippet, the selector that failed and the page URL. It is written to `ingest_run.errors`
    and counted in `records_rejected`.
  - **Usable but incomplete** (e.g. unparseable date, missing district): stored, with the
    problem recorded in `quality_flags`.

  Nothing is silently dropped. The quality report surfaces both kinds.
- **Graceful degradation:** If one adapter fails entirely (e.g., portal is down), the
  other adapters still run. The failed adapter's `ingest_run` records `status = 'failed'`.

---

## Publication Dates

The brief requires dates to be verified, not assumed, so:

- `publication_date` is filled **only** from an explicit publication field on the page.
  Candidate labels include "veröffentlicht am", "Bekanntmachung vom" and "Amtsblatt vom". The
  exact selector for each source is confirmed when fixtures are captured in Phase 2 and
  recorded in the README.
- It is **never** copied from the participation period (`Laufzeit`) or derived from `scraped_at`.
- `date_source` records where each date came from (`listing_card` | `detail_page`).
- If no publication field exists, `publication_date` stays NULL and
  `quality_flags` gets `missing_publication_date`.
- Dates that are present but unparseable get `unparseable_date`, and the raw string is
  kept in `raw_data`.

---

## Source Overlap Documentation

The same Leipzig Bauleitplan may appear in both the Saxon state portal and the Leipzig
city portal. These are stored as **separate rows** with different `source` values.

Potential matching strategy (not implemented, documented for future work):
- Match on the plan number in the title (e.g., "Nr. 456") after normalisation.
- Match on title fuzzy similarity (validation query 6 lists exact title matches).
- A future `iris_staging.signal_match` table could link `source_record_id` pairs.

The `source` column and `UNIQUE (source, source_record_id)` constraint ensure no
accidental merging.

---

## Phase 1: Project Skeleton and Database

### Overview

Set up the Python project, Docker environment, config and the Alembic migration (plain SQL
DDL, no ORM models). This phase produces a working `docker compose up` +
`alembic upgrade head` that creates the full schema.

### Changes Required

#### 1. `pyproject.toml`

```toml
[project]
name = "iris-sig-08-harvester"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "playwright>=1.48",
    "beautifulsoup4>=4.12",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.1",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "pyyaml>=6.0",
    "pyproj>=3.6",
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
interpreter and package versions. The `playwright` pin in `uv.lock` also fixes which
Chromium build `playwright install chromium` downloads.

**Sync, not async, throughout.** The harvester is a sequential, deliberately throttled
crawler, so async gains nothing and costs complexity. So it uses:
- Playwright's sync API (`playwright.sync_api`)
- a sync SQLAlchemy `Engine` / `Session` with psycopg 3
- plain synchronous pytest tests (no `pytest-asyncio`)

#### 2. `docker-compose.yml`

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

#### 3. `.env.example`

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

#### 4. Alembic setup

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

#### 5. `src/harvester/config.py`

- **Done:** `DatabaseSettings`, a pydantic-settings model reading `POSTGRES_HOST`, `_PORT`,
  `_USER`, `_PASSWORD` (`SecretStr`, required), `_DB` and `_TEST_DB` from the environment or
  `.env`. Its properties `url` and `test_url` return a `sqlalchemy.engine.URL`. It's covered by
  `tests/test_config.py`: a password with special characters survives the URL round trip, the
  password is masked in `str()`/`repr()`, and a missing password fails with a validation error.
- **To do:** the model that loads `config/settings.yaml` (sources, keywords, bounding boxes).

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
- [ ] The constraints bite: an insert with `geocode_status = 'resolved_district_centroid'`
      and a NULL geometry fails with `ck_geometry_matches_status`, an unknown
      `geocode_status` value fails its CHECK, an empty `matched_keywords` fails its CHECK,
      and a repeated `(source, source_record_id)` fails `uq_source_record`

**Checkpoint:** Pause for confirmation before proceeding to Phase 2.

---

## Phase 2: Adapters and HTML Parsing

### Overview

Implement the two Playwright-based adapters that scrape listing and detail pages,
producing `ScrapedRecord` instances. Test with saved HTML fixtures (no live network).

### Changes Required

#### 1. `src/harvester/models.py`

Pydantic `ScrapedRecord` model (as specified above). Also `NormalisedRecord` with all
fields that map to the DB schema.

#### 2. `src/harvester/adapters/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from playwright.sync_api import Page

from harvester.models import ScrapedRecord


@dataclass(frozen=True)
class ScrapeError:
    url: str
    kind: str              # 'missing_id' | 'missing_title' | 'http_4xx' | 'retries_exhausted' | ...
    message: str
    selector: str | None = None
    html_snippet: str | None = None  # truncated to 2 KB


@dataclass
class ScrapeResult:
    records: list[ScrapedRecord] = field(default_factory=list)
    errors: list[ScrapeError] = field(default_factory=list)
    expected_count: int | None = None  # from paginator "von N", for completeness checks


class BaseAdapter(ABC):
    @abstractmethod
    def scrape(self, page: Page) -> ScrapeResult:
        """Scrape all records from this source. Handles pagination internally."""
```

#### 3. `src/harvester/adapters/sachsen_portal.py`

- `SachsenPortalAdapter(BaseAdapter)` with `city_slug` parameter.
- `scrape()` method: navigates to listing, extracts cards, paginates by clicking the
  PrimeFaces paginator, visits detail pages.
- `_parse_listing_card(card_element) -> ScrapedRecord` -- extracts fields from a
  `div.nbp-teaser` element.
- `_parse_detail_page(page) -> dict` -- extracts description and publication date.
- Retry logic wrapping each page navigation.

#### 4. `src/harvester/adapters/leipzig_stadtportal.py`

- `LeipzigStadtAdapter(BaseAdapter)`.
- `scrape()` method: navigates to listing with `page.goto`, extracts table rows,
  paginates via GET parameters, visits detail pages.
- `_parse_listing_row(row_element) -> ScrapedRecord`.
- `_parse_detail_page(page) -> dict`.

#### 5. Test fixtures

Save representative HTML pages to `tests/fixtures/`:
- `sachsen_dresden_listing.html` -- 2-3 cards including one industrial match
- `sachsen_dresden_listing_beendet.html` -- listing with the `BEENDETE` filter applied, with paginator
- `sachsen_dresden_detail.html` -- detail page for one card
- `sachsen_leipzig_listing.html` -- 1-2 cards
- `sachsen_leipzig_detail.html`
- `leipzig_stadt_listing.html` -- 5-10 rows with mixed Thema values
- `leipzig_stadt_listing_page2.html` -- second page for pagination testing
- `leipzig_stadt_detail.html`
- `empty_listing.html` -- page with no results
- `malformed_card.html` -- card with missing fields

These are captured from the live portals with a one-off `scripts/capture_fixtures.py`
(using the same polite settings) and trimmed. Capture date and source URL go in an HTML
comment at the top of each fixture. While capturing, also confirm and record:
- whether a GET parameter can set the state portal status filter
- which detail-page element holds an explicit publication date, if any

#### 6. `src/harvester/adapters/fetch.py`

`fetch_page(page, url, settings) -> Response` with the retry and status rules from
**Resilience**. It also applies the polite delay before every navigation. It raises
`FetchError(kind, url, status)` when retries run out or on a non-retryable 4xx; adapters
turn that into a `ScrapeError`.

#### 7. `tests/test_sachsen_adapter.py`

Fixtures are served via Playwright `page.route()`, which intercepts the portal URLs and
returns fixture files. That way the adapter's real navigation code runs with no network.
`page.set_content()` is used only for single-element parsing tests.

```python
def test_parses_listing_cards(page):
    """Verify ScrapedRecord fields from the listing fixture."""

def test_includes_beendete_pass(page):
    """Both AKTUELLE and BEENDETE passes run; historical cards are collected."""

def test_count_mismatch_logged(page):
    """Paginator says 'von 12' but 10 cards found -> ScrapeError, not silent."""

def test_handles_empty_listing(page):
    """Empty fixture returns ScrapeResult with no records and no exception."""

def test_handles_malformed_card(page):
    """Card without ID -> ScrapeError in result.errors; other cards still parsed."""
```

#### 8. `tests/test_leipzig_adapter.py`

```python
def test_parses_table_rows(page):
    """Verify ScrapedRecord fields from the listing fixture."""

def test_pagination(page):
    """Page 1 and page 2 fixtures combine; stops when a page has no rows."""

def test_extracts_thema(page):
    """Thema field correctly parsed from row."""
```

#### 9. `tests/test_fetch.py`

```python
def test_retries_on_503_then_succeeds(page): ...
def test_retries_on_timeout(page): ...
def test_honours_retry_after_on_429(page): ...
def test_no_retry_on_404(page): ...
def test_raises_fetch_error_when_retries_exhausted(page): ...
```

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_sachsen_adapter.py tests/test_leipzig_adapter.py tests/test_fetch.py` -- all pass
- [ ] `ruff check src/harvester/adapters/` passes
- [ ] `mypy src/harvester/adapters/ src/harvester/models.py` passes

#### Manual Verification
- [ ] Inspect fixture HTML files to confirm they represent real portal structure
- [ ] Status-filter mechanism and publication-date selector confirmed and noted in README
- [ ] `ScrapedRecord` fields match expectations for each fixture

**Checkpoint:** Pause for confirmation before proceeding to Phase 3.

---

## Phase 3: Pipeline (Normalise, Filter, Geocode)

### Overview

Implement the normalisation pipeline that transforms `ScrapedRecord` into
`NormalisedRecord` with industrial filter and geocoding, dropping non-matches.

### Changes Required

#### 1. `src/harvester/pipeline/normalise.py`

- Publication date parsing: `DD.MM.YYYY` strings to `datetime.date`, following
  **Publication Dates**. Unparseable strings become NULL plus a `unparseable_date` quality
  flag. Other scraped values (status, procedure step, participation period) are not
  normalised; they stay as scraped in `raw_data`.
- Quality flags: `missing_publication_date`, `missing_district`, `unparseable_date`,
  `detail_page_failed`.
- Records the filter doesn't match are dropped here and counted as `records_filtered_out`.

#### 2. `src/harvester/pipeline/filter.py`

Industrial keyword filter as specified above. Loads keywords from config.

#### 3. `src/harvester/pipeline/geocode.py`

- Load `data/district_centroids.csv` at startup.
- `resolve_geometry(city, district) -> tuple[Point | None, str, str]`
  returns `(geometry_25832, geocode_status, geocode_reason)`.
- WGS84 -> EPSG:25832 transformation via pyproj.
- Bounding box check in WGS84 before transformation.
- Returns `(None, 'unresolved', 'district not in lookup: ...')` for unknown districts.
- Returns `(None, 'rejected_out_of_bounds', 'lon 14.2 outside ...')` for violations.

#### 4. `data/district_centroids.csv`

Full lookup table for all Dresden Stadtbezirke (10) and Ortschaften (9), plus Leipzig
Stadtbezirke (10) and common Ortsteile. WGS84 coordinates with provenance.

#### 5. Tests

```python
# tests/test_normalise.py
def test_date_parsing_german_format():
def test_unparseable_date_flagged_not_raised():
def test_publication_date_never_copied_from_participation_period():
def test_missing_publication_date_flagged():
def test_non_industrial_records_dropped_and_counted():

# tests/test_filter.py
def test_matches_gewerbegebiet_in_title():
def test_matches_compounds():  # Gewerbestandort, Kleingewerbe, Logistikzentrum, Industriepark
def test_evidence_contains_full_compound_word():
def test_matches_GE_case_sensitive():
def test_no_false_positive_on_lowercase_ge():  # "gegen" should not match
def test_short_codes_not_matched_inside_words():  # "GIESSEN", "GEMEINDE" -> no match
def test_matches_thema_gewerbe():
def test_keywords_collected_from_thema_title_and_description():
def test_no_match_returns_false():
def test_keywords_from_config():

# tests/test_geocode.py
def test_resolves_known_district():
def test_unknown_district_returns_unresolved():
def test_rejects_out_of_bounds():
def test_rejects_swapped_lat_lon():
def test_rejects_non_finite_coordinates():
def test_wgs84_to_25832_transformation():  # known control point, tolerance 1 m
```

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_normalise.py tests/test_filter.py tests/test_geocode.py` -- all pass
- [ ] `ruff check src/harvester/pipeline/` passes
- [ ] `mypy src/harvester/pipeline/` passes

#### Manual Verification
- [ ] Spot-check centroid coordinates on a map
- [ ] Verify bounding boxes exclude obviously wrong locations

**Checkpoint:** Pause for confirmation before proceeding to Phase 4.

---

## Phase 4: Upsert, CLI, and Integration

### Overview

Wire adapters to pipeline to database. Implement the CLI entry point, upsert logic,
and integration tests including idempotent re-run and duplicate detection.

### Changes Required

#### 1. `src/harvester/db/upsert.py`

- `upsert_records(conn: Connection, records: list[NormalisedRecord], run: IngestRun) -> UpsertStats`,
  using `text()` statements on a SQLAlchemy Core connection (no ORM session)
- Implements **Upsert Logic** exactly, in one transaction per batch:
  - de-duplicate the batch, logging dropped duplicates as `duplicate_in_batch`
  - `INSERT ... ON CONFLICT (source, source_record_id) DO UPDATE ... RETURNING (xmax = 0)`
- Returns `UpsertStats(inserted, updated, duplicates_in_batch)`, derived from `RETURNING`
  as described there.

#### 2. `src/harvester/cli.py` (+ `__main__.py`)

```python
@click.group()
def cli(): ...

@cli.command()
@click.option("--dry-run", is_flag=True, help="Print records without writing to DB")
@click.option("--source", type=str, help="Run only this source")
@click.option("--city", type=str, help="Run only this city")
def run(dry_run: bool, source: str | None, city: str | None) -> None: ...

@cli.command()
def report(): ...
```

#### 3. `src/harvester/db/engine.py`

Sync SQLAlchemy `Engine` (psycopg 3) from `DatabaseSettings().url`, created with
`hide_parameters=True`. A missing `POSTGRES_PASSWORD` fails with a clear message.
`--dry-run` needs no database.

#### 4. Integration tests

All tests here are marked `@pytest.mark.db`.

```python
# tests/test_upsert.py
def test_insert_new_records(db_session):
    """First run inserts all records."""

def test_rerun_creates_no_duplicates(db_session):
    """Second run with same data: 0 inserted, N updated; row count, ids and created_at unchanged."""

def test_rerun_overwrites_changed_content(db_session):
    """Modified record is rewritten in place: new title, new ingest_run_id, same id."""

def test_duplicate_keys_in_one_batch(db_session):
    """Same key twice in a batch -> one row, no DB error, duplicate logged."""

def test_filter_config_change_updates_keywords(db_session):
    """Re-run with a new keyword list rewrites matched_keywords on existing rows."""

# tests/test_bounding_box.py
def test_out_of_bounds_stored_with_null_geometry(db_session):
    """Record with bad coords stored but geometry is NULL, status is rejected."""

def test_in_bounds_stored_with_geometry(db_session):
    """Valid coords produce non-NULL geometry with ST_SRID = 25832."""

# tests/test_pipeline_integration.py
def test_full_pipeline_fixture_to_db(db_session, page):
    """Routed fixtures -> adapter -> normalise -> filter -> geocode -> upsert; verify DB state."""

def test_full_pipeline_twice_is_idempotent(db_session, page):
    """End-to-end re-run over the same fixtures adds no rows."""

def test_errors_visible_in_ingest_run(db_session, page):
    """Malformed card and 404 detail page appear in ingest_run.errors / quality_flags."""
```

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_upsert.py tests/test_bounding_box.py tests/test_pipeline_integration.py` -- all pass
- [ ] `python -m harvester run --dry-run` executes without error (with fixtures or mocked adapters)
- [ ] `ruff check .` passes
- [ ] `mypy .` passes

#### Manual Verification
- [ ] `python -m harvester run` against live portals populates the DB
- [ ] `SELECT count(*), source FROM iris_staging.signal_ingest GROUP BY source` shows expected counts
- [ ] Re-run produces 0 inserts and the same row count
- [ ] Dresden row count is about current + finished, matching the paginator totals from both passes

**Checkpoint:** Pause for confirmation before proceeding to Phase 5.

---

## Phase 5: Reporting, Validation, and Deliverables

### Overview

Build the quality report, validation SQL, sample output, and README. This phase
completes all deliverables.

### Changes Required

#### 1. `src/harvester/reporting/quality_report.py`

Generates a report from `ingest_run` and `signal_ingest` tables:

```
=== IRIS-SIG-08 Ingest Quality Report ===
Run ID:        <uuid>
Started:       2026-10-27 14:30:00 UTC
Finished:      2026-10-27 14:32:15 UTC
Source:        sachsen_portal (dresden)

Records scraped:    251
Filtered out:       243   (not industrial)
Rejected:             0   (unusable, see errors)
Industrial matches:   8 / 251 (3.2%)
  inserted:           2
  updated:            6   (already present, rewritten)
Errors:               0

Geocode resolved:     7 / 8
Geocode unresolved:   1 / 8
Geocode rejected:     0 / 8

Top matched keywords:
  Gewerbe          - 5 records
  Gewerbegebiet    - 2 records
  Industrie        - 1 record
```

#### 2. `sql/validation_queries.sql`

```sql
-- 1. Total records by source and city
SELECT source, city, count(*) FROM iris_staging.signal_ingest GROUP BY 1, 2;

-- 2. Quality flag distribution
SELECT unnest(quality_flags) AS flag, count(*)
FROM iris_staging.signal_ingest GROUP BY 1 ORDER BY 2 DESC;

-- 3. Geocode status distribution
SELECT geocode_status, count(*) FROM iris_staging.signal_ingest GROUP BY 1;

-- 4. Records with NULL geometry
SELECT source, source_record_id, title, geocode_status, geocode_reason
FROM iris_staging.signal_ingest WHERE geometry IS NULL;

-- 5. Most recent ingest runs (filtered_out vs inserted + updated is the match rate)
SELECT id, source, city, status, records_scraped, records_filtered_out,
       records_rejected, records_inserted, records_updated,
       jsonb_array_length(errors) AS errors, started_at, finished_at
FROM iris_staging.ingest_run ORDER BY started_at DESC LIMIT 10;

-- 6. Possible cross-source duplicates: same title on both portals (documented, not merged)
SELECT lower(title) AS title, array_agg(source || ':' || source_record_id) AS records
FROM iris_staging.signal_ingest
GROUP BY 1
HAVING count(DISTINCT source) > 1;

-- 7. Stale rows: not written by the latest completed run of their source
SELECT s.source, s.source_record_id, s.title, s.updated_at
FROM iris_staging.signal_ingest s
WHERE s.ingest_run_id <> (
    SELECT r.id FROM iris_staging.ingest_run r
    WHERE r.source = s.source AND r.status = 'completed'
    ORDER BY r.started_at DESC LIMIT 1
);

-- 8. Keyword match distribution
SELECT unnest(matched_keywords) AS keyword, count(*)
FROM iris_staging.signal_ingest
GROUP BY 1 ORDER BY 2 DESC;

-- 9. Geometry bounding box check (should return 0 rows)
SELECT source_record_id, title, ST_AsText(geometry),
       ST_X(ST_Transform(geometry, 4326)) AS lon,
       ST_Y(ST_Transform(geometry, 4326)) AS lat
FROM iris_staging.signal_ingest
WHERE geometry IS NOT NULL
  AND NOT ST_Within(
    ST_Transform(geometry, 4326),
    ST_MakeEnvelope(12.0, 50.8, 14.2, 51.5, 4326)
  );
```

#### 3. `sample_output/signal_ingest_sample.csv` and `sample_output/signal_ingest_sample.geojson`

Generated from a real run against the portals. CSV with all columns. GeoJSON with the rows
that have a geometry, for visual inspection.

#### 4. `README.md`

Full README covering:
- Project description and scope
- Prerequisites (uv, Docker; uv provides Python 3.12 via `.python-version`)
- Setup (`uv sync`, `uv run playwright install chromium`, `.env`, `docker compose up -d`,
  `uv run alembic upgrade head`), started in Phase 1 and extended as steps are added.
  Note that the Chromium download is version-locked to the `playwright` pin in `uv.lock`
  and must be re-run after upgrading Playwright.
- Quick start (`uv run harvester run`)
- Configuration (`settings.yaml`, environment variables)
- Architecture overview (adapters, pipeline, DB)
- Testing (`pytest`)
- Validation (`psql -f sql/validation_queries.sql`)
- Known limitations (no online geocoding, source overlap, EPSG zone note)
- Adding new cities/districts

#### 5. Tests

```python
# tests/test_quality_report.py
@pytest.mark.db
def test_report_generation(db_session_with_data):
    """Report generates without error and includes expected sections."""

@pytest.mark.db
def test_report_lists_errors_and_quality_flags(db_session_with_data):
    """Rejected records, quality flag counts and missing-date counts are shown."""
```

The report also prints quality-flag counts (e.g. `missing_publication_date: 214`) and
filter evidence for spotting false positives.

### Success Criteria

#### Automated Verification
- [ ] `pytest` -- full suite passes
- [ ] `ruff check .` passes
- [ ] `mypy .` passes
- [ ] `python -m harvester report` generates report without error

#### Manual Verification
- [ ] README instructions produce a working setup from scratch
- [ ] `sql/validation_queries.sql` runs against populated DB and returns sensible results
- [ ] Sample CSV and GeoJSON open correctly in QGIS / geojson.io
- [ ] Quality report identifies any data issues

**Checkpoint:** Final review.

---

## Testing Strategy

### Unit Tests
- Adapter HTML parsing (from fixtures, no network)
- Normalisation functions (publication date parsing, quality flags, dropping non-matches)
- Industrial keyword filter (positive matches, negative matches, case sensitivity)
- Geocode lookup (known districts, unknown districts, out-of-bounds)

### Integration Tests
- Full pipeline: fixture HTML -> adapter -> normalise -> filter -> geocode -> upsert -> DB query
- Idempotent re-run: same fixtures upserted twice, verify 0 new rows and stable ids
- Content change: modify fixture, verify the existing row is rewritten in place
- Out-of-bounds coordinates: verify NULL geometry + rejection status stored

### Test Infrastructure
- **Browser:** a session-scoped Playwright browser and a function-scoped `page` in
  `conftest.py`. Fixtures are served via `page.route()` (or `page.set_content()` for
  element-level tests). A catch-all route **aborts any request not matched to a fixture**,
  so tests can never reach the live portals.
- **Database:** the PostGIS `iris_test` database from `docker compose`, at
  `DatabaseSettings().test_url`. There is **no SQLite/SpatiaLite fallback**: the upsert depends on
  PostgreSQL-only features (`ON CONFLICT`, `xmax`, `ST_Transform`, `TEXT[]`), so a
  different engine would test different behaviour.
  - A session-scoped fixture runs `alembic upgrade head` against `iris_test`.
  - Each test runs in a transaction that is rolled back afterwards.
  - Tests marked `db` are **skipped with an explicit reason** if the database settings can't
    be loaded (no `POSTGRES_PASSWORD` in the environment or `.env`). If they load but the
    database is unreachable, the tests **fail**: configured-but-broken is an error, not a skip. The non-DB suite (parsing, filter, normalise, geocode, fetch) runs with no Docker.

---

## Performance Considerations

- **Request volume:** about 800 records across all sources:
  - Leipzig city portal: ~555 records on 12 listing pages
  - Dresden and Leipzig state portal: ~250 records on ~25 listing pages

  A live run is ~840 page loads (~37 listing + ~800 detail), about 30 minutes at a 2s
  delay. Every run fetches every detail page (no incremental mode; see "What We Are NOT
  Doing"). Re-run and idempotency demos use the saved fixtures and take seconds.
- **Database:** Small table (<1000 rows, only industrial matches). No indexes beyond the
  primary key and `uq_source_record`; see **Database Schema**.
- **Memory:** HTML pages are small (<100KB each). No memory concerns.
- **Playwright startup:** Single browser instance, reused across all adapters.

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
  - `setup`: `uv sync` + `uv run playwright install chromium`
  - `db-up` / `db-reset`: `docker compose up -d` / `docker compose down -v && docker compose up -d`
  - `migrate`: `uv run alembic upgrade head`
  - `test`, `lint` (`ruff check .` + `mypy`), `run` (`uv run harvester run`)

  `make` ships with macOS and most Linux systems; `just` reads better but is an extra
  install. Either way, don't hide steps the README doesn't also list.

---

## References

- Saxon state portal: `https://buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen`
- Leipzig city portal: `https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene`
- BauGB (Federal Building Code): procedure phases SS 2-10
- BauNVO (Building Use Ordinance): zone type definitions (GE, GI, SO, etc.)
- EPSG:25832: ETRS89 / UTM zone 32N
- EPSG:25833: ETRS89 / UTM zone 33N (Dresden's native zone)
