# IRIS-SIG-08 Saxony Citizen Participation Portal Harvester -- Implementation Plan

## Overview

Build a Python harvester that scrapes Bauleitplan (land-use zoning) procedures from two
Saxony citizen participation portals, filters for industrial/commercial hearings, normalises
the data, and loads it into a PostGIS-backed `iris_staging.signal_ingest` table with
idempotent upserts and full auditability.

**Scope:** Dresden and Leipzig city portals only; surrounding districts deferred.

**Branch:** `feature/IRIS-SIG-08-portal-harvester`

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
psql "$DATABASE_URL" -f sql/validation_queries.sql
```

---

## What We Are NOT Doing

- Merging duplicate rows across sources (overlap is documented, not resolved).
- Online geocoding (Nominatim, Google, etc.). District centroid lookup only.
- Scraping surrounding districts (Meissen, Nordsachsen, Leipzig-Land, etc.).
- Downloading or parsing PDF plan documents.
- Building a web UI or API layer.
- Handling BundID authentication (public pages only).

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
  alembic.ini                       # Alembic config (reads DATABASE_URL from env)
  docker-compose.yml                # postgis/postgis:16-3.4, credentials from .env
  docker/
    initdb/
      01_create_test_db.sql         # creates iris_test database + postgis extension
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
      __main__.py                   # CLI entry point
      config.py                     # pydantic-settings, loads settings.yaml
      models.py                     # ScrapedRecord, IngestRun, NormalisedRecord
      db/
        __init__.py
        engine.py                   # SQLAlchemy engine from DATABASE_URL
        schema.py                   # GeoAlchemy2 ORM: signal_ingest, ingest_run
        upsert.py                   # INSERT ... ON CONFLICT with content_hash
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
  # Skip detail pages for listing rows whose listing_hash is unchanged (override: --full)
  incremental: true

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

### `iris_staging.ingest_run`

Tracks each harvester execution for auditability and quality reporting.

```sql
CREATE TABLE iris_staging.ingest_run (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    source              TEXT NOT NULL,          -- 'sachsen_portal' | 'leipzig_stadtportal'
    city                TEXT,                   -- 'dresden' | 'leipzig' | NULL (for multi-city runs)
    status              TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'completed' | 'failed'
    records_scraped     INTEGER DEFAULT 0,
    records_inserted    INTEGER DEFAULT 0,
    records_updated     INTEGER DEFAULT 0,
    records_unchanged   INTEGER DEFAULT 0,
    records_rejected    INTEGER DEFAULT 0,
    errors              JSONB DEFAULT '[]'::jsonb,
    config_snapshot     JSONB,                 -- frozen copy of settings used
    error_message       TEXT
);
```

### `iris_staging.signal_ingest`

```sql
CREATE TABLE iris_staging.signal_ingest (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source              TEXT NOT NULL,          -- 'sachsen_portal' | 'leipzig_stadtportal'
    source_record_id    TEXT NOT NULL,          -- portal-specific ID (numeric or slug)
    content_hash        TEXT NOT NULL,          -- SHA-256 of all persisted output fields (see below)
    listing_hash        TEXT NOT NULL,          -- SHA-256 of listing-row fields only (incremental mode)
    ingest_run_id       UUID REFERENCES iris_staging.ingest_run(id),  -- run that last inserted/changed the row
    last_seen_run_id    UUID REFERENCES iris_staging.ingest_run(id),  -- run that last saw the row

    -- Plan identity
    city                TEXT NOT NULL,          -- 'dresden' | 'leipzig'
    plan_type           TEXT,                   -- 'Bebauungsplan', 'Flaechennutzungsplan', etc.
    plan_number         TEXT,                   -- extracted: 'Nr. 3083', 'Nr. 10', etc.
    title               TEXT NOT NULL,
    description         TEXT,

    -- Status and procedure
    status              TEXT,                   -- normalised: 'aktiv', 'beendet', 'im_verfahren', 'rechtskraeftig'
    procedure_step      TEXT,                   -- 'fruehzeitige_beteiligung', 'oeffentliche_auslegung', etc.

    -- Location
    district            TEXT,                   -- Stadtbezirk/Ortsteil
    thema               TEXT,                   -- topic category (Leipzig sources)

    -- Dates (with provenance)
    participation_start DATE,
    participation_end   DATE,
    publication_date    DATE,                   -- only from an explicit publication field; never derived
    date_source         TEXT,                   -- 'listing_card' | 'detail_page' | NULL if no date found

    -- URLs
    detail_url          TEXT,

    -- Industrial filter
    is_industrial       BOOLEAN NOT NULL DEFAULT FALSE,
    matched_keywords    TEXT[],                 -- e.g. ARRAY['Gewerbe', 'GE']
    match_reason        TEXT,                   -- 'keyword_title', 'keyword_description', 'thema_field', etc.

    -- Geometry
    geometry            GEOMETRY(Point, 25832),
    geocode_status      TEXT,                   -- 'resolved_district_centroid' | 'unresolved' | 'rejected_out_of_bounds'
    geocode_reason      TEXT,                   -- e.g. 'district: Nordost / Schoenefeld-Ost' or rejection reason

    -- Data quality
    quality_flags       TEXT[] NOT NULL DEFAULT '{}',  -- e.g. 'missing_publication_date', 'missing_district',
                                                       -- 'unparseable_date', 'detail_page_failed'

    -- Audit
    raw_data            JSONB,                 -- full scraped payload
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_source_record UNIQUE (source, source_record_id)
);

CREATE INDEX idx_signal_ingest_city ON iris_staging.signal_ingest (city);
CREATE INDEX idx_signal_ingest_is_industrial ON iris_staging.signal_ingest (is_industrial);
CREATE INDEX idx_signal_ingest_status ON iris_staging.signal_ingest (status);
CREATE INDEX idx_signal_ingest_geometry ON iris_staging.signal_ingest USING GIST (geometry);
```

### Upsert Logic

Each batch is written in **one transaction with two statements**, so both rules hold:
content columns and `updated_at` change only when content changed, and `last_seen_at`
changes on every run.

**Step 0: de-duplicate the batch in Python.** PostgreSQL rejects an `INSERT ... ON CONFLICT
DO UPDATE` that touches the same key twice ("command cannot affect row a second time").
Records with a repeated `(source, source_record_id)` in one batch (e.g. a card shown on two
listing pages while the portal re-sorts) are collapsed to the last occurrence. Each one
dropped is logged to `ingest_run.errors` as `duplicate_in_batch`.

**Step 1: insert or change content.**

```sql
INSERT INTO iris_staging.signal_ingest (
    source, source_record_id, content_hash, listing_hash, ingest_run_id, last_seen_run_id,
    city, plan_type, plan_number, title, description,
    status, procedure_step, district, thema,
    participation_start, participation_end, publication_date, date_source,
    detail_url, is_industrial, matched_keywords, match_reason,
    geometry, geocode_status, geocode_reason, quality_flags,
    raw_data, first_seen_at, last_seen_at
)
VALUES (...)
ON CONFLICT (source, source_record_id) DO UPDATE SET
    content_hash        = EXCLUDED.content_hash,
    listing_hash        = EXCLUDED.listing_hash,
    ingest_run_id       = EXCLUDED.ingest_run_id,
    city                = EXCLUDED.city,
    plan_type           = EXCLUDED.plan_type,
    plan_number         = EXCLUDED.plan_number,
    title               = EXCLUDED.title,
    description         = EXCLUDED.description,
    status              = EXCLUDED.status,
    procedure_step      = EXCLUDED.procedure_step,
    district            = EXCLUDED.district,
    thema               = EXCLUDED.thema,
    participation_start = EXCLUDED.participation_start,
    participation_end   = EXCLUDED.participation_end,
    publication_date    = EXCLUDED.publication_date,
    date_source         = EXCLUDED.date_source,
    detail_url          = EXCLUDED.detail_url,
    is_industrial       = EXCLUDED.is_industrial,
    matched_keywords    = EXCLUDED.matched_keywords,
    match_reason        = EXCLUDED.match_reason,
    geometry            = EXCLUDED.geometry,
    geocode_status      = EXCLUDED.geocode_status,
    geocode_reason      = EXCLUDED.geocode_reason,
    quality_flags       = EXCLUDED.quality_flags,
    raw_data            = EXCLUDED.raw_data,
    updated_at          = NOW()
WHERE iris_staging.signal_ingest.content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING source_record_id, (xmax = 0) AS inserted;
```

**Step 2: mark every record in the batch as seen.**

```sql
UPDATE iris_staging.signal_ingest
SET last_seen_at = :run_started_at, last_seen_run_id = :run_id
WHERE source = :source AND source_record_id = ANY(:batch_ids);
```

**Counting:**
- `inserted` = rows returned by step 1 with `inserted = true`
- `updated` = rows returned with `inserted = false`
- `unchanged` = batch size minus rows returned (the `WHERE` skipped them, so `RETURNING`
  omits them)

`xmax = 0` is the standard PostgreSQL test for whether a row from an `ON CONFLICT` upsert
was newly inserted. It's covered by a test.

**What `content_hash` covers:** a SHA-256 over a canonical JSON of **every persisted output
column** except the audit and run columns (`id`, the hashes, the run IDs, `first_seen_at`,
`last_seen_at`, `created_at`, `updated_at`, `raw_data`). That includes the derived fields:
`is_industrial`, `matched_keywords`, `match_reason`, `geometry` (as rounded WKT),
`geocode_status`, `geocode_reason` and `quality_flags`. So a change to the keyword config or
the centroid CSV changes the hash and propagates to existing rows on the next run, even if
the portal content didn't change. `raw_data` is excluded so cosmetic HTML changes (e.g.
tracking parameters) don't trigger updates.

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
    match_reason: str = "no_match"  # comma-joined: 'thema_field', 'keyword_title', 'keyword_description'
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
        reasons: list[str] = []

        if thema and thema in self._thema:
            keywords.append(f"thema:{thema}")
            reasons.append("thema_field")

        for reason, text in (("keyword_title", title), ("keyword_description", description)):
            if not text:
                continue
            kws, ev = self._scan(text)
            if kws:
                reasons.append(reason)
                keywords.extend(kws)
                evidence.extend(ev)

        unique = list(dict.fromkeys(keywords))
        return FilterResult(
            is_industrial=bool(unique),
            matched_keywords=unique,
            match_reason=", ".join(reasons) if reasons else "no_match",
            evidence=list(dict.fromkeys(evidence)),
        )
```

`evidence` goes into `raw_data` and the quality report, so false positives (e.g.
"Gewerbesteuer" in a description) can be spotted and the keyword list tuned.

All Bauleitplan rows from both cities are stored with the `is_industrial` flag, making
the filter auditable. Downstream consumers query `WHERE is_industrial = TRUE`.

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
4. Extract all cards on the page:
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
6. For each card, navigate to the detail page to scrape description and documents. In
   incremental mode this is skipped when the card is unchanged (see Incremental Retrieval).
7. Between page loads: `page.wait_for_timeout(config.request_delay_seconds * 1000)`.

**Source record ID:** Numeric ID extracted from the detail URL path.

**Empty result handling:** If no `div.nbp-teaser` elements found after page load, return
an empty list (not an exception). Log a warning. The `ingest_run` records 0 scraped.

### Leipzig City Portal Adapter (`leipzig_stadtportal.py`)

**Navigation flow (Playwright):**

1. `page.goto(f"{base_url}{listing_path}")` -- loads first 50 rows.
2. Wait for `tr[id^="project-"]` rows.
3. Extract all rows:
   - Title + detail link: `a.filterlist-detailpage > span`
   - Status: 2nd `td > ul > li` text
   - District: 3rd `td > ul > li` text
   - Thema: 4th `td > ul > li` text
   - Project ID: from `tr` id attribute (e.g., `project-1751` -> `1751`)
4. Paginate via `page.goto` with `?tx_lepurpose[filter][page]=N` (N = 2, 3, ...) until
   no more rows appear.
5. For each row, navigate to detail page for description and document links. In
   incremental mode this is skipped when the row is unchanged (see Incremental Retrieval).
6. Polite delay between requests.

**Source record ID:** Project ID from the table row (e.g., `1751`).

### Common: ScrapedRecord Model

```python
from pydantic import BaseModel
from datetime import date, datetime

class ScrapedRecord(BaseModel):
    source: str                    # 'sachsen_portal' | 'leipzig_stadtportal'
    source_record_id: str          # stable ID from the source
    city: str                      # 'dresden' | 'leipzig'
    title: str
    description: str | None = None
    plan_type: str | None = None
    plan_number: str | None = None  # extracted via regex from title
    status: str | None = None
    procedure_step: str | None = None
    district: str | None = None
    thema: str | None = None
    participation_start: date | None = None
    participation_end: date | None = None
    publication_date: date | None = None
    date_source: str | None = None
    detail_url: str
    raw_html: str | None = None     # preserved for debugging, not stored in DB
    raw_data: dict                  # all scraped fields as-is
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
- It is **never** copied from `participation_start` or derived from `scraped_at`.
- `date_source` records where each date came from (`listing_card` | `detail_page`).
- If no publication field exists, `publication_date` stays NULL and
  `quality_flags` gets `missing_publication_date`.
- Dates that are present but unparseable get `unparseable_date`, and the raw string is
  kept in `raw_data`.

---

## Incremental Retrieval

A full run opens every detail page, about 37 minutes at a 2s delay. Incremental mode (the
default, `scraping.incremental: true`) cuts this down:

1. Listing pages are always fetched in full; they are cheap and they reveal new, removed and
   changed rows.
2. For each listing row, `listing_hash` is computed from the listing-level fields only.
3. If a stored row has the same `(source, source_record_id)` and `listing_hash`, the detail
   page is **skipped**. Its detail fields are reused from the stored row, and the record goes
   through the normal upsert (usually counted `unchanged`). `last_seen_at` is still bumped.
4. New rows and rows with a changed `listing_hash` get their detail page fetched as normal.

**Limitation:** changes that show only on a detail page (e.g. a new document) are missed until
the next full run. `--full` forces every detail page; the README recommends a weekly full run.
Config changes (keywords, centroids) still propagate in incremental mode, because the derived
fields and `content_hash` are recomputed from the reused detail fields.

---

## Source Overlap Documentation

The same Leipzig Bauleitplan may appear in both the Saxon state portal and the Leipzig
city portal. These are stored as **separate rows** with different `source` values.

Potential matching strategy (not implemented, documented for future work):
- Match on `plan_number` (e.g., "Nr. 456") after normalisation.
- Match on title fuzzy similarity.
- A future `iris_staging.signal_match` table could link `source_record_id` pairs.

The `source` column and `UNIQUE (source, source_record_id)` constraint ensure no
accidental merging.

---

## Phase 1: Project Skeleton and Database

### Overview

Set up the Python project, Docker environment, Alembic migrations, and core ORM models.
This phase produces a working `docker compose up` + `alembic upgrade head` that creates
the full schema.

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
    "geoalchemy2>=0.15",
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
# SQLAlchemy 2.0 typing works natively via Mapped[]; the old sqlalchemy.ext.mypy plugin is deprecated
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["db: needs the PostGIS test database (TEST_DATABASE_URL)"]
```

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
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-iris}
      POSTGRES_USER: ${POSTGRES_USER:-iris}
      # No default: compose fails fast if .env doesn't set it
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      # Creates the separate iris_test database used by the db-marked tests
      - ./docker/initdb:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

#### 3. `.env.example`

```
# Copy to .env (gitignored) and replace the placeholder. Never commit .env.
POSTGRES_USER=iris
POSTGRES_DB=iris
POSTGRES_PASSWORD=<choose-a-local-password>
DATABASE_URL=postgresql+psycopg://iris:<same-password>@localhost:5432/iris
TEST_DATABASE_URL=postgresql+psycopg://iris:<same-password>@localhost:5432/iris_test
# Optional: append an operator contact to the scraper's user agent
# HARVESTER_USER_AGENT="IRIS-SIG-08-Harvester/1.0 (bot; contact: you@example.org)"
```

The repo commits no credential, not even a dev default. `.env` is already in `.gitignore`.
The harvester never logs `DATABASE_URL`: the engine is built with `hide_parameters=True`,
and connection errors are logged with the password masked.

#### 4. Alembic setup

- `alembic.ini` -- points to `migrations/` directory, reads `DATABASE_URL` from env.
- `migrations/env.py` -- standard Alembic env with SQLAlchemy metadata from `schema.py`.
- `migrations/versions/001_create_schema_and_tables.py`:
  - `CREATE EXTENSION IF NOT EXISTS postgis`
  - `CREATE SCHEMA IF NOT EXISTS iris_staging`
  - Create `iris_staging.ingest_run` table
  - Create `iris_staging.signal_ingest` table with all columns, indexes, and constraints
  - `downgrade()` drops the two tables and the `iris_staging` schema but **not** the `postgis`
    extension, which other schemas in a shared database may depend on

#### 5. `src/harvester/db/schema.py`

GeoAlchemy2 ORM models for `signal_ingest` and `ingest_run`, matching the SQL above.

#### 6. `src/harvester/config.py`

Pydantic-settings model loading `settings.yaml` and `DATABASE_URL` from environment.

### Success Criteria

#### Automated Verification
- [ ] `docker compose up -d` starts PostgreSQL with PostGIS (and fails clearly if `.env` is missing)
- [ ] `alembic upgrade head` creates schema and tables without errors
- [ ] `alembic downgrade base` + `alembic upgrade head` is idempotent
- [ ] `ruff check src/` passes
- [ ] `mypy src/harvester/db/ src/harvester/config.py` passes

#### Manual Verification
- [ ] `psql` shows `iris_staging.signal_ingest` and `iris_staging.ingest_run` tables
- [ ] `SELECT PostGIS_Version();` returns `3.4`
- [ ] Geometry column has SRID 25832

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
    def scrape(self, page: Page, known_listing_hashes: dict[str, str]) -> ScrapeResult:
        """Scrape all records from this source. Handles pagination internally.

        known_listing_hashes maps source_record_id -> stored listing_hash, so
        incremental mode can skip unchanged detail pages.
        """
```

#### 3. `src/harvester/adapters/sachsen_portal.py`

- `SachsenPortalAdapter(BaseAdapter)` with `city_slug` parameter.
- `scrape()` method: navigates to listing, extracts cards, paginates by clicking the
  PrimeFaces paginator, visits detail pages.
- `_parse_listing_card(card_element) -> ScrapedRecord` -- extracts fields from a
  `div.nbp-teaser` element.
- `_parse_detail_page(page) -> dict` -- extracts description, documents from detail page.
- `_extract_plan_number(title: str) -> str | None` -- regex to pull "Nr. XXXX" from title.
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

def test_extracts_plan_number():
    """Regex extracts 'Nr. 3083' from various title formats."""

def test_handles_malformed_card(page):
    """Card without ID -> ScrapeError in result.errors; other cards still parsed."""

def test_incremental_skips_unchanged_detail(page):
    """Known listing_hash -> detail route never requested."""
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
`NormalisedRecord` with industrial filter, geocoding, and content hashing.

### Changes Required

#### 1. `src/harvester/pipeline/normalise.py`

- Status normalisation: map German status strings to enum values
  (`aktiv`, `beendet`, `im_verfahren`, `rechtskraeftig`).
- Procedure step normalisation: `Frühzeitige Beteiligung` -> `fruehzeitige_beteiligung`.
- Date parsing: `DD.MM.YYYY` strings to `datetime.date`. Unparseable strings become NULL
  plus a `unparseable_date` quality flag. Publication date follows **Publication Dates**.
- Plan number extraction: regex for "Nr. XXXX" patterns.
- Quality flags: `missing_publication_date`, `missing_district`, `unparseable_date`,
  `detail_page_failed`.
- `listing_hash`: SHA-256 of the listing-level fields only.
- `content_hash`: SHA-256 of the canonical JSON of every persisted output field, including
  derived ones (filter result, geometry WKT, geocode status, quality flags). See
  **Upsert Logic**. It is computed **after** filter and geocode, as the last pipeline step.

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
def test_status_normalisation():
def test_date_parsing_german_format():
def test_unparseable_date_flagged_not_raised():
def test_publication_date_never_copied_from_participation_start():
def test_missing_publication_date_flagged():
def test_content_hash_deterministic():
def test_content_hash_changes_on_update():
def test_content_hash_changes_when_filter_config_changes():
def test_content_hash_changes_when_centroid_changes():
def test_content_hash_ignores_raw_data():

# tests/test_filter.py
def test_matches_gewerbegebiet_in_title():
def test_matches_compounds():  # Gewerbestandort, Kleingewerbe, Logistikzentrum, Industriepark
def test_evidence_contains_full_compound_word():
def test_matches_GE_case_sensitive():
def test_no_false_positive_on_lowercase_ge():  # "gegen" should not match
def test_short_codes_not_matched_inside_words():  # "GIESSEN", "GEMEINDE" -> no match
def test_matches_thema_gewerbe():
def test_reasons_include_all_sources():  # thema + title + description all listed
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

- `upsert_records(session, records: list[NormalisedRecord], run: IngestRun) -> UpsertStats`
- Implements **Upsert Logic** exactly, in one transaction per batch:
  - de-duplicate the batch, logging dropped duplicates as `duplicate_in_batch`
  - step 1: `INSERT ... ON CONFLICT ... WHERE content_hash IS DISTINCT FROM ... RETURNING (xmax = 0)`
  - step 2: `UPDATE last_seen_at, last_seen_run_id` for every key in the batch
- Returns `UpsertStats(inserted, updated, unchanged, duplicates_in_batch)`, derived from
  `RETURNING` as described there.
- `load_listing_hashes(session, source) -> dict[str, str]` feeds incremental mode.

#### 2. `src/harvester/__main__.py`

```python
@click.group()
def cli(): ...

@cli.command()
@click.option("--dry-run", is_flag=True, help="Print records without writing to DB")
@click.option("--source", type=str, help="Run only this source")
@click.option("--city", type=str, help="Run only this city")
@click.option("--full", is_flag=True, help="Ignore listing_hash; fetch every detail page")
def run(dry_run: bool, source: str | None, city: str | None, full: bool) -> None: ...

@cli.command()
def report(): ...
```

#### 3. `src/harvester/db/engine.py`

Sync SQLAlchemy `Engine` (psycopg 3) from the `DATABASE_URL` environment variable,
created with `hide_parameters=True`. A missing `DATABASE_URL` fails with a clear message.
`--dry-run` needs no database.

#### 4. Integration tests

All tests here are marked `@pytest.mark.db`.

```python
# tests/test_upsert.py
def test_insert_new_records(db_session):
    """First run inserts all records."""

def test_rerun_creates_no_duplicates(db_session):
    """Second run with same data: 0 inserted, 0 updated, N unchanged; row count unchanged."""

def test_update_on_content_change(db_session):
    """Modified record triggers update, content_hash and updated_at change."""

def test_unchanged_rerun_keeps_updated_at(db_session):
    """Unchanged records: updated_at and ingest_run_id stay the same."""

def test_last_seen_at_always_updates(db_session):
    """Even unchanged records get last_seen_at and last_seen_run_id bumped."""

def test_duplicate_keys_in_one_batch(db_session):
    """Same key twice in a batch -> one row, no DB error, duplicate logged."""

def test_filter_config_change_propagates(db_session):
    """Re-run with a new keyword list updates is_industrial on existing rows."""

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
- [ ] Re-run produces 0 inserts, 0 updates (unless portal data changed)
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
Records inserted:    12
Records updated:      3
Records unchanged:  236
Records rejected:     0
Errors:               0

Industrial matches:   8 / 251 (3.2%)
Geocode resolved:   245 / 251 (97.6%)
Geocode unresolved:   6 / 251 (2.4%)
Geocode rejected:     0 / 251 (0.0%)

Top matched keywords:
  Gewerbe          - 5 records
  Gewerbegebiet    - 2 records
  Industrie        - 1 record
```

#### 2. `sql/validation_queries.sql`

```sql
-- 1. Total records by source and city
SELECT source, city, count(*) FROM iris_staging.signal_ingest GROUP BY 1, 2;

-- 2. Industrial matches by city
SELECT city, count(*) FILTER (WHERE is_industrial) AS industrial,
       count(*) AS total
FROM iris_staging.signal_ingest GROUP BY city;

-- 3. Geocode status distribution
SELECT geocode_status, count(*) FROM iris_staging.signal_ingest GROUP BY 1;

-- 4. Records with NULL geometry
SELECT source, source_record_id, title, geocode_status, geocode_reason
FROM iris_staging.signal_ingest WHERE geometry IS NULL;

-- 5. Most recent ingest runs
SELECT id, source, city, status, records_scraped, records_inserted,
       records_updated, records_unchanged, records_rejected,
       started_at, finished_at
FROM iris_staging.ingest_run ORDER BY started_at DESC LIMIT 10;

-- 6. Duplicate detection: same plan_number across sources
SELECT plan_number, array_agg(source || ':' || source_record_id) AS sources
FROM iris_staging.signal_ingest
WHERE plan_number IS NOT NULL
GROUP BY plan_number
HAVING count(DISTINCT source) > 1;

-- 7. Keyword match distribution
SELECT unnest(matched_keywords) AS keyword, count(*)
FROM iris_staging.signal_ingest
WHERE is_industrial = TRUE
GROUP BY 1 ORDER BY 2 DESC;

-- 8. Status distribution
SELECT status, count(*) FROM iris_staging.signal_ingest GROUP BY 1;

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

Generated from a real run against the portals. CSV with all columns. GeoJSON with
`is_industrial = TRUE` records only, for visual inspection.

#### 4. `README.md`

Full README covering:
- Project description and scope
- Prerequisites (Python 3.12+, Docker, Playwright)
- Quick start (`docker compose up`, `alembic upgrade head`, `playwright install chromium`,
  `python -m harvester run`)
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
- Normalisation functions (status mapping, date parsing, plan number extraction)
- Industrial keyword filter (positive matches, negative matches, case sensitivity)
- Geocode lookup (known districts, unknown districts, out-of-bounds)
- Content hash determinism

### Integration Tests
- Full pipeline: fixture HTML -> adapter -> normalise -> filter -> geocode -> upsert -> DB query
- Idempotent re-run: same fixtures upserted twice, verify 0 new rows
- Content change detection: modify fixture, verify update counted
- Out-of-bounds coordinates: verify NULL geometry + rejection status stored

### Test Infrastructure
- **Browser:** a session-scoped Playwright browser and a function-scoped `page` in
  `conftest.py`. Fixtures are served via `page.route()` (or `page.set_content()` for
  element-level tests). A catch-all route **aborts any request not matched to a fixture**,
  so tests can never reach the live portals.
- **Database:** the PostGIS `iris_test` database from `docker compose`, at
  `TEST_DATABASE_URL`. There is **no SQLite/SpatiaLite fallback**: the upsert depends on
  PostgreSQL-only features (`ON CONFLICT`, `xmax`, `ST_Transform`, `TEXT[]`), so a
  different engine would test different behaviour.
  - A session-scoped fixture runs `alembic upgrade head` against `iris_test`.
  - Each test runs in a transaction that is rolled back afterwards.
  - Tests marked `db` are **skipped with an explicit reason** if `TEST_DATABASE_URL` is
    unset. The non-DB suite (parsing, filter, normalise, geocode, fetch) runs with no Docker.

---

## Performance Considerations

- **Request volume:** about 800 records across all sources:
  - Leipzig city portal: ~555 records on 12 listing pages
  - Dresden and Leipzig state portal: ~250 records on ~25 listing pages

  A `--full` run is ~840 page loads (~37 listing + ~800 detail), about 30 minutes at a 2s
  delay. An incremental run fetches listing pages plus only new or changed detail pages,
  usually a few minutes.
- **Database:** Small table (<1000 rows). No partitioning or special indexing needed beyond
  the defined indexes.
- **Memory:** HTML pages are small (<100KB each). No memory concerns.
- **Playwright startup:** Single browser instance, reused across all adapters.

---

## Migration Notes

- First run: `alembic upgrade head` creates everything from scratch.
- Subsequent schema changes: add new Alembic migration files.
- No existing data to migrate (greenfield).

---

## References

- Saxon state portal: `https://buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen`
- Leipzig city portal: `https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene`
- BauGB (Federal Building Code): procedure phases SS 2-10
- BauNVO (Building Use Ordinance): zone type definitions (GE, GI, SO, etc.)
- EPSG:25832: ETRS89 / UTM zone 32N
- EPSG:25833: ETRS89 / UTM zone 33N (Dresden's native zone)
