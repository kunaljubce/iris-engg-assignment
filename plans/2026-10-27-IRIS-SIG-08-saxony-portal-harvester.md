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

### Portal Details

| Attribute | Saxon State Portal (Dresden + Leipzig) | Leipzig City Portal |
|---|---|---|
| Base URL | `buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen` | `www.leipzig.de/rathaus/buergerbeteiligung/.../bebauungsplaene` |
| Framework | Jakarta Faces / PrimeFaces | TYPO3 + `tx_lepurpose` extension |
| Pagination | PrimeFaces AJAX POST (click paginator, ViewState managed by browser) | GET param `tx_lepurpose[filter][page]=N`, 50 rows/page |
| Listing selector | `div.nbp-teaser` cards | `tr[id^="project-"]` table rows |
| Detail URL | `/portal/{city}/beteiligung/themen/{numeric_id}` | `.../bebauungsplaene/detail/projekt/{slug}` |
| Stable ID | Numeric ID from URL path | `project-{id}` from row, slug from link |
| Items | Dresden ~251, Leipzig ~101 | ~555 |

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
  docker-compose.yml                # postgis/postgis:16-3.4
  .env.example                      # DATABASE_URL template (no secrets committed)
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
        base.py                     # BaseAdapter ABC
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
  sql/
    validation_queries.sql          # post-ingest verification
  tests/
    __init__.py
    conftest.py                     # Playwright browser fixture, DB fixtures
    fixtures/
      sachsen_dresden_listing.html
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
    test_normalise.py
    test_filter.py
    test_geocode.py
    test_upsert.py                  # includes re-run idempotency + duplicate tests
    test_bounding_box.py            # out-of-bounds coordinate rejection
    test_pipeline_integration.py
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
  # Case-insensitive whole-word match unless noted
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
    - "GVZ"
    - "Produktion"
  # Case-SENSITIVE whole-token match (avoid false positives on German words)
  case_sensitive_tokens:
    - "GE"
    - "GI"
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
    content_hash        TEXT NOT NULL,          -- SHA-256 of normalised content fields
    ingest_run_id       UUID REFERENCES iris_staging.ingest_run(id),

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
    publication_date    DATE,
    date_source         TEXT,                   -- 'listing_card' | 'detail_page' | 'inferred'

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

```sql
INSERT INTO iris_staging.signal_ingest (
    source, source_record_id, content_hash, ingest_run_id,
    city, plan_type, plan_number, title, description,
    status, procedure_step, district, thema,
    participation_start, participation_end, publication_date, date_source,
    detail_url, is_industrial, matched_keywords, match_reason,
    geometry, geocode_status, geocode_reason,
    raw_data, first_seen_at, last_seen_at
)
VALUES (...)
ON CONFLICT (source, source_record_id) DO UPDATE SET
    content_hash        = EXCLUDED.content_hash,
    ingest_run_id       = EXCLUDED.ingest_run_id,
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
    raw_data            = EXCLUDED.raw_data,
    last_seen_at        = EXCLUDED.last_seen_at,
    updated_at          = NOW()
WHERE iris_staging.signal_ingest.content_hash != EXCLUDED.content_hash;
```

The `WHERE content_hash != EXCLUDED.content_hash` clause ensures `updated_at` and
`last_seen_at` only change when content actually changes. The `records_unchanged` counter
in `ingest_run` tracks rows where the hash matched.

---

## Industrial Keyword Filter

```python
import re
from dataclasses import dataclass

@dataclass
class FilterResult:
    is_industrial: bool
    matched_keywords: list[str]
    match_reason: str  # 'keyword_title' | 'keyword_description' | 'thema_field' | 'no_match'

# Case-insensitive whole-word keywords
INDUSTRIAL_KEYWORDS = [
    "Gewerbegebiet", "Industriegebiet", "Gewerbe", "Industrie",
    "Logistik", "Gewerbepark", "Industriepark", "Technologiepark",
    "Gueterverkehrszentrum", "Güterverkehrszentrum", "GVZ", "Produktion",
]

_CI_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in INDUSTRIAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Case-SENSITIVE whole-token match for GE / GI
_CS_PATTERN = re.compile(r"\b(GE|GI)\b")

THEMA_SIGNALS = {"Gewerbe"}


def check_industrial(title: str, description: str | None, thema: str | None) -> FilterResult:
    matches: list[str] = []
    reasons: list[str] = []

    # Check thema field first (Leipzig direct signal)
    if thema and thema in THEMA_SIGNALS:
        matches.append(f"thema:{thema}")
        reasons.append("thema_field")

    # Check title
    for m in _CI_PATTERN.finditer(title):
        matches.append(m.group())
    for m in _CS_PATTERN.finditer(title):
        matches.append(m.group())
    if matches and "thema_field" not in reasons:
        reasons.append("keyword_title")

    # Check description
    if description:
        desc_matches_before = len(matches)
        for m in _CI_PATTERN.finditer(description):
            matches.append(m.group())
        for m in _CS_PATTERN.finditer(description):
            matches.append(m.group())
        if len(matches) > desc_matches_before:
            reasons.append("keyword_description")

    # Deduplicate
    unique_matches = list(dict.fromkeys(matches))
    return FilterResult(
        is_industrial=len(unique_matches) > 0,
        matched_keywords=unique_matches,
        match_reason=", ".join(reasons) if reasons else "no_match",
    )
```

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

**Bounding box rejection:** After transformation to WGS84, any point outside the per-city
bounding box (from `settings.yaml`) is rejected with `geocode_status = 'rejected_out_of_bounds'`
and `geocode_reason` explaining the violation. These rows are stored but with `geometry = NULL`.

**Note on EPSG:25832 and Dresden:** Dresden at ~13.7 deg E is east of UTM zone 32N's nominal
12 deg E boundary. The CRS is still usable (UTM zones have overlap), but distortion increases.
This is documented in the README and the centroid CSV provenance notes.

---

## Adapter Details

### Saxon State Portal Adapter (`sachsen_portal.py`)

**Navigation flow (Playwright):**

1. `page.goto(f"{base_url}/portal/{city}/beteiligung/themen?format=Bauleitplan")`
2. Wait for `div.nbp-teaser` to appear (or timeout = empty result).
3. Extract all cards on the page:
   - Title: `h3.nbp-teaser-title > a` (text + href)
   - Plan type: `span.nbp-teaser-taglist--beteiligungstyp`
   - Authority: `span.nbp-teaser-taglist--mandant`
   - Procedure step: `span.nbp-teaser-taglist--verfahrensschritt`
   - Status: `li.nbp-statistik--status`
   - Dates: `li.nbp-statistik--laufzeit` (parse "DD.MM.YYYY bis DD.MM.YYYY")
   - Submissions: `li.nbp-statistik--stellungnahmen`
4. If paginator exists, click the "next" button (`a` in paginator with `ui-paginator-next`),
   wait for content update, repeat. Playwright handles the PrimeFaces AJAX/ViewState
   transparently.
5. For each card, navigate to the detail page to scrape description and documents.
6. Between page loads: `await asyncio.sleep(config.request_delay_seconds)`.

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
5. For each row, navigate to detail page for description and document links.
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

- **Retries with exponential backoff:** Network errors and HTTP 5xx trigger up to
  `max_retries` attempts with `retry_backoff_factor` between them.
- **Polite delay:** `request_delay_seconds` (default 2s) between all page loads.
- **User agent:** Neutral identifying string (`IRIS-SIG-08-Harvester/1.0 (bot)`), overridable
  via the `HARVESTER_USER_AGENT` env var so an operator can add a contact address without
  committing it to source.
- **Timeout:** `page_timeout_ms` (default 30s) per page load.
- **Empty response handling:** An adapter that receives zero results returns an empty list
  and logs a warning. It does not raise an exception. The `ingest_run` row records the count.
- **Malformed records:** Records that fail validation (missing title, unparseable dates,
  etc.) are logged to the `ingest_run.errors` JSONB array with the raw data and error
  message. They are never silently dropped. The quality report surfaces them.
- **Graceful degradation:** If one adapter fails entirely (e.g., portal is down), the
  other adapters still run. The failed adapter's `ingest_run` records `status = 'failed'`.

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
    "pytest-asyncio>=0.23",
    "ruff>=0.7",
    "mypy>=1.13",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy", "sqlalchemy.ext.mypy.plugin"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

#### 2. `docker-compose.yml`

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    environment:
      POSTGRES_DB: iris
      POSTGRES_USER: iris
      POSTGRES_PASSWORD: iris_dev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U iris"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

#### 3. `.env.example`

```
DATABASE_URL=postgresql+psycopg://iris:iris_dev@localhost:5432/iris
```

#### 4. Alembic setup

- `alembic.ini` -- points to `migrations/` directory, reads `DATABASE_URL` from env.
- `migrations/env.py` -- standard Alembic env with SQLAlchemy metadata from `schema.py`.
- `migrations/versions/001_create_schema_and_tables.py`:
  - `CREATE EXTENSION IF NOT EXISTS postgis`
  - `CREATE SCHEMA IF NOT EXISTS iris_staging`
  - Create `iris_staging.ingest_run` table
  - Create `iris_staging.signal_ingest` table with all columns, indexes, and constraints

#### 5. `src/harvester/db/schema.py`

GeoAlchemy2 ORM models for `signal_ingest` and `ingest_run`, matching the SQL above.

#### 6. `src/harvester/config.py`

Pydantic-settings model loading `settings.yaml` and `DATABASE_URL` from environment.

### Success Criteria

#### Automated Verification
- [ ] `docker compose up -d` starts PostgreSQL with PostGIS
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
from harvester.models import ScrapedRecord

class BaseAdapter(ABC):
    @abstractmethod
    async def scrape(self, page: Page) -> list[ScrapedRecord]:
        """Scrape all records from this source. Handles pagination internally."""
        ...
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
- `sachsen_dresden_detail.html` -- detail page for one card
- `sachsen_leipzig_listing.html` -- 1-2 cards
- `sachsen_leipzig_detail.html`
- `leipzig_stadt_listing.html` -- 5-10 rows with mixed Thema values
- `leipzig_stadt_listing_page2.html` -- second page for pagination testing
- `leipzig_stadt_detail.html`
- `empty_listing.html` -- page with no results
- `malformed_card.html` -- card with missing fields

These are hand-crafted from real portal HTML with content anonymised as needed.

#### 6. `tests/test_sachsen_adapter.py`

```python
async def test_parses_listing_cards(browser):
    """Load fixture HTML via page.set_content, verify ScrapedRecord fields."""

async def test_handles_empty_listing(browser):
    """Empty fixture returns empty list, no exception."""

async def test_extracts_plan_number():
    """Regex extracts 'Nr. 3083' from various title formats."""

async def test_handles_malformed_card(browser):
    """Missing fields logged as error, not raised."""
```

#### 7. `tests/test_leipzig_adapter.py`

```python
async def test_parses_table_rows(browser):
    """Load fixture HTML, verify ScrapedRecord fields."""

async def test_pagination(browser):
    """Load page1 then page2 fixtures, verify combined results."""

async def test_extracts_thema():
    """Thema field correctly parsed from row."""
```

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_sachsen_adapter.py tests/test_leipzig_adapter.py` -- all pass
- [ ] `ruff check src/harvester/adapters/` passes
- [ ] `mypy src/harvester/adapters/ src/harvester/models.py` passes

#### Manual Verification
- [ ] Inspect fixture HTML files to confirm they represent real portal structure
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
- Date parsing: `DD.MM.YYYY` strings to `datetime.date`.
- Plan number extraction: regex for "Nr. XXXX" patterns.
- Content hash: SHA-256 of `(title, description, status, procedure_step, district,
  participation_start, participation_end)`.

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
def test_content_hash_deterministic():
def test_content_hash_changes_on_update():

# tests/test_filter.py
def test_matches_gewerbegebiet_in_title():
def test_matches_GE_case_sensitive():
def test_no_false_positive_on_lowercase_ge():  # "gegen" should not match
def test_matches_thema_gewerbe():
def test_no_match_returns_false():
def test_keywords_from_config():

# tests/test_geocode.py
def test_resolves_known_district():
def test_unknown_district_returns_unresolved():
def test_rejects_out_of_bounds():
def test_wgs84_to_25832_transformation():
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

- `upsert_records(session, records: list[NormalisedRecord], run_id: UUID) -> UpsertStats`
- Uses `INSERT ... ON CONFLICT (source, source_record_id) DO UPDATE SET ... WHERE content_hash != EXCLUDED.content_hash`.
- Returns counts: inserted, updated, unchanged.
- Updates `last_seen_at` on every conflict (even if content unchanged) via a separate
  lightweight UPDATE.

#### 2. `src/harvester/__main__.py`

```python
@click.group()
def cli(): ...

@cli.command()
@click.option("--dry-run", is_flag=True, help="Print records without writing to DB")
@click.option("--source", type=str, help="Run only this source")
@click.option("--city", type=str, help="Run only this city")
def run(dry_run, source, city): ...

@cli.command()
def report(): ...
```

#### 3. `src/harvester/db/engine.py`

SQLAlchemy async engine from `DATABASE_URL` environment variable.

#### 4. Integration tests

```python
# tests/test_upsert.py
async def test_insert_new_records(db_session):
    """First run inserts all records."""

async def test_rerun_creates_no_duplicates(db_session):
    """Second run with same data: 0 inserted, 0 updated, N unchanged."""

async def test_update_on_content_change(db_session):
    """Modified record triggers update, content_hash changes."""

async def test_last_seen_at_always_updates(db_session):
    """Even unchanged records get last_seen_at bumped."""

# tests/test_bounding_box.py
async def test_out_of_bounds_stored_with_null_geometry(db_session):
    """Record with bad coords stored but geometry is NULL, status is rejected."""

async def test_in_bounds_stored_with_geometry(db_session):
    """Valid coords produce non-NULL geometry in EPSG:25832."""

# tests/test_pipeline_integration.py
async def test_full_pipeline_fixture_to_db(db_session, browser):
    """Adapter -> normalise -> filter -> geocode -> upsert, verify DB state."""
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
def test_report_generation(db_session_with_data):
    """Report generates without error and includes expected sections."""
```

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
- Playwright fixtures using `page.set_content()` with saved HTML (no live network)
- PostgreSQL test database via Docker (or SQLite with spatialite for fast unit tests)
- pytest fixtures for DB sessions with automatic rollback

---

## Performance Considerations

- **Request volume:** ~560 listing items + ~560 detail pages = ~1120 page loads. At 2s
  delay between requests, a full run takes ~37 minutes. Acceptable for a batch harvester.
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
- Design discussion: `plans/2026-10-27-IRIS-SIG-08-saxony-portal-harvester-design.md` (Phase A artifact, to be written)
