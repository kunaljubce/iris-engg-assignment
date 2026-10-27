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
  older `ingest_run_id` (validation query 7).

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
      config.py                     # pydantic-settings: POSTGRES_* -> DB URL; settings.yml
      models.py                     # ScrapedRecord, NormalisedRecord
      db/
        __init__.py
        engine.py                   # SQLAlchemy engine from DatabaseSettings().url
        upsert.py                   # INSERT ... ON CONFLICT (source, source_record_id)
      adapters/
        __init__.py
        base.py                     # BaseAdapter ABC, ScrapeResult, ScrapeError
        fetch.py                    # fetch(): httpx GET with retries, status checks, polite delay
        sachsen_portal.py           # state portal: marker JSON + detail page (Dresden + Leipzig)
        leipzig_stadtportal.py      # city portal: table rows, "im Verfahren" only
      pipeline/
        __init__.py
        normalise.py                # ScrapedRecord -> NormalisedRecord, date parsing, quality flags
        filter.py                   # industrial keyword matcher
        geocode.py                  # bounding-box check of source coordinates
      reporting/
        __init__.py
        quality_report.py           # generates report from ingest_run data
  sql/
    validation_queries.sql          # post-ingest verification
  tests/
    __init__.py
    conftest.py                     # fixture loader, httpx MockTransport, DB fixtures
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

**Step 0: de-duplicate the batch in Python.** PostgreSQL rejects an `INSERT ... ON CONFLICT
DO UPDATE` that touches the same key twice ("command cannot affect row a second time").
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

    def check(self, title: str, thema: list[str]) -> FilterResult:
        # A Leipzig row can carry several Thema values ("Wohnen", "Gewerbe", ...)
        keywords = [f"thema:{t}" for t in thema if t in self._thema]
        kws, evidence = self._scan(title)
        keywords.extend(kws)

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
| Marker coordinates missing or empty | `unresolved` | NULL | `marker has no coordinates` |
| Leipzig city-portal row | `unresolved` | NULL | `source gives district only: <districts>` |
| Coordinates non-numeric, non-finite or outside the box (e.g. lat/lon swapped) | `rejected_out_of_bounds` | NULL | e.g. `lon 51.05 outside dresden [13.55, 13.95]` |

**Bounding box rejection:** coordinates are checked in WGS84 against the per-city box from
`settings.yml` **before** they reach the database. Rejected rows are stored with
`geometry = NULL` and the raw values in `raw_data`, never with the bad point. The check is
written as "inside" (`min <= v <= max`), so NaN fails it too. `ck_geometry_matches_status`
makes the database refuse a point without `source_coordinates`. Validation query 9 re-checks
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
2. `parse_markers(html)`: find `map.drawMarkers(` followed by a JSON array, and `json.loads` it.
   For each entry:
   - `id` -> `source_record_id` (as a string)
   - `kategorie` -> `title` (`html.unescape`, since the portal escapes quotes as `&quot;`)
   - `href` -> `detail_url` (joined to `base_url`; the city in the path matters, Key Discovery 6)
   - `latitude` / `longitude` -> kept as given; checked in the pipeline
   - `adresse` -> `location_text`
   - the whole entry -> `raw_data`
3. `parse_expected_count(html)`: "von N" from `span.ui-paginator-current` when present (more
   than 10 results), else the number of `div.nbp-teaser` cards. If it differs from the marker
   count, a `count_mismatch` error goes to `ingest_run.errors`; the markers are still used.
4. The pipeline filters on title. For each **match only**, GET the detail page and
   `parse_detail(html)` reads `li.nbp-statistik--laufzeit` ("Zeitraum DD.MM.YYYY bis
   DD.MM.YYYY"). The raw text goes into `raw_data`; the start date becomes
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
2. `parse_rows(html)` for each `tr[id^="project-"]`:
   - `source_record_id`: from the `tr` id (`project-1751` -> `1751`)
   - title and `detail_url`: `a.filterlist-detailpage` (text without the trailing
     `arrow_forward` icon text, and href)
   - cells: `td > ul > li` texts. The first is the status; cells containing `" / "` are
     districts ("Nord / Eutritzsch"; a row can have several); the rest are Thema values
   - `location_text`: the districts joined with `"; "`
3. `parse_next_page(html)`: `data-next` of `.start-ajax-pagination`, or None. While there is
   one, GET the same URL plus `tx_lepurpose[filter][page]=N`. Today the filtered list fits on
   one page; the loop is tested with a snippet page carrying the real button markup.
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
  caught because `parse_detail` finds no "Zeitraum" there, flagged `missing_publication_date`.
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
- Match on city + plan number from the title ("Nr. 911", "Nummer 320"); validation query 6
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

## Phase 2: Adapters and HTML Parsing

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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from harvester.models import ScrapedRecord


@dataclass(frozen=True)
class ScrapeError:
    url: str
    kind: str              # 'missing_id' | 'missing_title' | 'markers_not_found' | 'count_mismatch' | 'http_4xx' | 'retries_exhausted'
    message: str
    snippet: str | None = None  # marker JSON entry or row HTML, truncated to 2 KB


@dataclass
class ScrapeResult:
    records: list[ScrapedRecord] = field(default_factory=list)
    errors: list[ScrapeError] = field(default_factory=list)


class BaseAdapter(ABC):
    @abstractmethod
    def scrape(self, client: httpx.Client) -> ScrapeResult:
        """Scrape all listing records from this source. Handles pagination internally."""
```

#### 3. `src/harvester/adapters/fetch.py`

`fetch(client, url, settings) -> str` with the retry and status rules from **Resilience**. It
applies the polite delay before every request and raises `FetchError(kind, url, status)` when
retries run out or on a non-retryable 4xx; adapters turn that into a `ScrapeError`.

#### 4. `src/harvester/adapters/sachsen_portal.py`

- `SachsenPortalAdapter(BaseAdapter)` with `city` and `status` parameters.
- `parse_markers(html) -> tuple[list[ScrapedRecord], list[ScrapeError]]`
- `parse_expected_count(html) -> int | None`
- `parse_detail(html) -> str | None`: the raw "Zeitraum ..." text.
- `fetch_detail(client, record) -> ScrapedRecord`: used by the pipeline for matches only.

#### 5. `src/harvester/adapters/leipzig_stadtportal.py`

- `LeipzigStadtAdapter(BaseAdapter)`.
- `parse_rows(html) -> tuple[list[ScrapedRecord], list[ScrapeError]]`
- `parse_next_page(html) -> int | None`

#### 6. Test fixtures

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

#### 7. `tests/conftest.py`

- `load_fixture(name) -> str`
- `mock_client(routes: dict[str, str | int])`: an `httpx.Client` on `httpx.MockTransport`
  that serves fixture text (or a status code) per URL and **fails the test on any unrouted
  URL**, so tests can never reach the live portals. No extra dependency (MockTransport is part
  of httpx).
- The polite delay is patched to 0.

#### 8. `tests/test_sachsen_adapter.py`

```python
def test_parses_markers():
    """id, unescaped title, city-correct detail_url, lon/lat, address from the fixture."""

def test_listing_has_all_results():
    """52 markers although only 10 cards are rendered; matches 'von 52'."""

def test_count_mismatch_logged():
    """Snippet: 'von 52' but 51 markers -> count_mismatch error, markers still returned."""

def test_parses_zeitraum_from_detail():
    """'Zeitraum 12.10.2026 bis 13.11.2026' from the detail fixture."""

def test_handles_empty_listing():
    """Snippet: no markers, no cards -> no records, no errors, no exception."""

def test_cards_without_markers_is_an_error():
    """Snippet: cards present but no marker JSON -> markers_not_found error."""

def test_handles_malformed_markers():
    """Snippet: marker without id -> ScrapeError; the others still parsed."""
```

#### 9. `tests/test_leipzig_adapter.py`

```python
def test_parses_rows():
    """ID, title without icon text, detail_url, status from the im-Verfahren fixture."""

def test_splits_districts_and_thema():
    """'West / Schönau', 'Alt-West / ...' -> location_text; 'Gewerbe', 'Sondergebiet' -> thema."""

def test_follows_next_page():
    """Snippet page 1 with data-next=2, then the fixture (no button) as page 2: both fetched, then stop."""
```

#### 10. `tests/test_fetch.py`

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
- **Why does `scrape()` take the `httpx.Client` as a parameter?** So tests can pass a client
  backed by `MockTransport`. The adapter never creates its own client.
- **Why return errors instead of raising?** One bad marker or row must not stop the run; bad
  entries are collected next to the good ones ("errors are visible").
- **Why `SNIPPET_MAX_CHARS = 2048`?** Enough to see what was wrong with an entry without
  bloating `ingest_run.errors`; one constant instead of each adapter repeating 2048.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_sachsen_adapter.py tests/test_leipzig_adapter.py tests/test_fetch.py` -- all pass
- [ ] `ruff check src/harvester/adapters/` passes
- [ ] `mypy src/harvester/adapters/ src/harvester/models.py` passes

#### Manual Verification
- [x] Fixture HTML files are whole, unedited captures of the live portals (2026-10-27)
- [x] `tests/fixtures/SOURCES.md` lists every fixture and the pages checked but not kept
- [x] Status-filter mechanism, publication-date element and ID scope confirmed (Key Discoveries 5-8)
- [ ] `ScrapedRecord` fields match expectations for each fixture

**Checkpoint:** Pause for confirmation before proceeding to Phase 3.

---

## Phase 3: Pipeline (Filter, Detail Dates, Geocode, Normalise)

### Overview

Turn `ScrapedRecord`s into `NormalisedRecord`s: filter (dropping non-matches), fetch the
state-portal detail page of each match, parse the date, check the coordinates.

### Changes Required

#### 1. `src/harvester/pipeline/filter.py`

Industrial keyword filter as specified above. Loads keywords from config.

#### 2. `src/harvester/pipeline/geocode.py`

- `check_coordinates(city, lon, lat, bbox) -> tuple[float | None, float | None, str, str | None]`
  returns `(lon, lat, geocode_status, geocode_reason)` following the table in **Coordinates
  and Bounding Box**. Pure Python; the transform happens in the upsert SQL.

#### 3. `src/harvester/pipeline/normalise.py`

- Order per record: filter -> (state portal, match) `fetch_detail` -> parse date -> check
  coordinates -> `NormalisedRecord`. Non-matches are dropped and counted as
  `records_filtered_out`.
- Publication date: the first `DD.MM.YYYY` in the "Zeitraum" text to `datetime.date`,
  following **Publication Dates**. Unparseable text becomes NULL plus `unparseable_date`.
- Quality flags: `missing_publication_date`, `missing_location`, `unparseable_date`,
  `detail_page_failed`.

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
def test_rejects_non_numeric_and_non_finite():  # "", "abc", NaN

# tests/test_normalise.py
def test_publication_date_is_zeitraum_start():
def test_unparseable_date_flagged_not_raised():
def test_leipzig_row_unresolved_without_date():  # missing_publication_date, reason lists districts
def test_detail_fetched_only_for_matches():      # mock client sees no request for non-matches
def test_detail_page_404_flagged():               # detail_page_failed, listing data kept
def test_non_industrial_records_dropped_and_counted():
```

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why match on the title (and Leipzig Thema) only, not descriptions?** Descriptions mention
  neighbouring commercial areas and produce false positives.
- **Why substring matching for long keywords but whole tokens for GE/GI/GVZ?** German compounds
  ("Gewerbestandort") need substring hits; short codes as substrings would hit ordinary words
  ("gegen").
- **Why check the bounding box in Python, before the database?** A bad point is never written;
  the row is stored with `geometry = NULL`, `rejected_out_of_bounds` and the reason.
- **Why is the check written as "inside" (`min <= v <= max`)?** NaN fails every comparison, so
  it's rejected too.
- **Why is `publication_date` the start of "Zeitraum"?** That's the first day the plan is
  publicly available (BauGB § 3 (2)); the raw text stays in `raw_data`.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_normalise.py tests/test_filter.py tests/test_geocode.py` -- all pass
- [ ] `ruff check src/harvester/pipeline/` passes
- [ ] `mypy src/harvester/pipeline/` passes

#### Manual Verification
- [ ] `--dry-run` filter decisions on the fixtures look right (no obvious misses or false hits)

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

def test_transform_control_point(db_session):
    """A known WGS84 point lands on its EPSG:25832 coordinates, tolerance 1 m."""

# tests/test_pipeline_integration.py
def test_full_pipeline_fixture_to_db(db_session, mock_client):
    """Mocked fixtures -> adapters -> filter -> detail -> geocode -> upsert; verify DB state."""

def test_full_pipeline_twice_is_idempotent(db_session, mock_client):
    """End-to-end re-run over the same fixtures adds no rows."""

def test_errors_visible_in_ingest_run(db_session, mock_client):
    """Malformed marker and 404 detail page appear in ingest_run.errors / quality_flags."""
```

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **How is the run idempotent?** `UNIQUE (source, source_record_id)` plus `ON CONFLICT DO
  UPDATE`; a re-run can't add rows.
- **Why no change detection (hash column)?** Nothing in the brief consumes "what changed"; an
  existing row is simply rewritten with the latest values.
- **How are inserted vs updated counted?** `RETURNING (xmax = 0)`: true only for a freshly
  inserted row.
- **Why de-duplicate the batch in Python first?** PostgreSQL rejects an `ON CONFLICT DO UPDATE`
  that hits the same key twice in one statement; each dropped duplicate is logged.
- **Why the EPSG:25832 transform in SQL?** `ST_Transform` in the upsert keeps pyproj out of the
  dependencies; a test checks a control point.
- **Why are rows never deleted?** A plan missing from one run may just be a portal hiccup;
  staging keeps what was seen, with `updated_at` as "last seen".

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
- [ ] No `count_mismatch` errors: marker counts match the "von N" / card counts

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

Records scraped:     60   (8 AKTUELLE + 52 BEENDETE markers)
Filtered out:        53   (not industrial)
Rejected:             0   (unusable, see errors)
Industrial matches:   7 / 60 (11.7%)
  inserted:           2
  updated:            5   (already present, rewritten)
Errors:               0

Geocode source_coordinates: 7 / 7
Geocode unresolved:         0 / 7
Geocode rejected:           0 / 7

Missing publication date:   0 / 7

Top matched keywords:
  Gewerbe          - 7 records
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

-- 6. Possible cross-source duplicates: same city + plan number on both portals
--    (titles differ in punctuation, e.g. Nr. 911; documented, not merged)
SELECT city, substring(title FROM '(?:Nr\.|Nummer)\s*(\d+(?:\.\d+)?)') AS plan_number,
       array_agg(source || ':' || source_record_id) AS records
FROM iris_staging.signal_ingest
GROUP BY 1, 2
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
- Setup (`uv sync`, `.env`, `docker compose up -d`, `uv run alembic upgrade head`), started
  in Phase 1 and extended as steps are added.
- Quick start (`uv run harvester run`)
- Configuration (`settings.yml`, environment variables)
- Architecture overview (adapters, pipeline, DB)
- Testing (`pytest`)
- Validation (`psql -f sql/validation_queries.sql`)
- Data source and access: both portals, public GET pages only, verified 2026-10-27
- Mapping assumptions: publication date = "Zeitraum" start; marker coordinates as the
  procedure location; Leipzig city portal limited to "im Verfahren"
- Known limitations (Leipzig city-portal rows unresolved and undated, title-only filter,
  source overlap, EPSG zone note, dependence on the `drawMarkers` script call)
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

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why are the two portals' copies of the same plan separate rows?** Each is a signal with its
  own URL and source ID; merging them is future work. Validation query 6 lists the pairs by
  city and plan number (e.g. Nr. 911).
- **Why EPSG:25832 although both cities lie east of 12° E?** The brief requires it; it's usable
  there with growing scale distortion, documented in the README.

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
  - Each test runs in a transaction that is rolled back afterwards.
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

---

## References

- Saxon state portal: `https://buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen`
- Leipzig city portal: `https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene`
- BauGB (Federal Building Code): procedure phases SS 2-10
- BauNVO (Building Use Ordinance): zone type definitions (GE, GI, SO, etc.)
- EPSG:25832: ETRS89 / UTM zone 32N
- EPSG:25833: ETRS89 / UTM zone 33N (Dresden's native zone)
