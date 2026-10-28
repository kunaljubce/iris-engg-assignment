## Implementation Approach

One adapter, `sachsen_portal.py`, runs once per city and status and produces `ScrapedRecord`s. It fetches pages with one shared `httpx.Client` and parses them with BeautifulSoup. Parsing functions take an HTML string and return data, so tests can call them directly on the text of a fixture file. A shared pipeline applies the industrial keyword filter, fetches the detail page of each match for its publication date, checks coordinates against the city bounding box, and upserts into PostGIS, where `ST_Transform` produces the EPSG:25832 point.

**Sync throughout.** The harvester is a sequential, deliberately throttled crawler, so it uses:
- a sync `httpx.Client`
- a sync SQLAlchemy `Engine` / `Session` with psycopg 3
- plain synchronous pytest tests (no `pytest-asyncio`)

## Key Discoveries

1. **One portal, server-rendered, and everything needed is a plain GET.** Dresden and Leipzig are both on the Saxon state portal (`buergerbeteiligung.sachsen.de`). No browser automation is needed: `httpx` + BeautifulSoup.

2. **The state portal embeds per-procedure coordinates.** Each listing contains an OpenLayers call `map.drawMarkers([...])` whose JSON array has one entry per result, **across all pages**: `id`, `kategorie` (the title, HTML-escaped), `href`, `latitude`, `longitude` (WGS84), `adresse` (street address). All 83 markers captured have coordinates inside the city bounding boxes. Geometry comes from the source, not from a lookup table.

3. **EPSG zone mismatch.** Both cities are east of 12 deg E (Leipzig ~12.4, Dresden ~13.7), i.e. outside the nominal coverage of EPSG:25832 (UTM 32N, 6--12 deg E); their native zone is EPSG:25833. The brief mandates 25832, so points are transformed with PostGIS `ST_Transform` and the zone note goes in the README. The bounding-box check catches gross errors.

4. **The status filter is a GET parameter**: `&status=AKTUELLE` (the default view) or `&status=BEENDETE` (finished). Uppercase only; lowercase is silently ignored. Cards render 10 per page behind a PrimeFaces AJAX paginator ("1 - 10 von 52"), but the marker JSON already lists all 52, so the adapter reads the markers and never pages. The "von N" count, when present, is a completeness check against the marker count.

5. **IDs come from one shared sequence across cities**, so `source_record_id` is unique on the portal. The detail URL must use the marker's `href`: a Dresden ID under `/portal/leipzig/` silently redirects to the Leipzig listing (HTTP 200).

6. **Publication date = start of the "Zeitraum".** There is no separate "published on" field. The detail page's `li.nbp-statistik--laufzeit` ("Zeitraum 12.10.2026 bis 13.11.2026") is the period in which the draft is publicly "veröffentlicht" (the detail text says so: "vom ... bis ... veröffentlicht"; BauGB § 3 (2)). Its start date is the publication date.

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

Only industrial matches are stored: the objective says *filter*. Non-matching records are counted in `ingest_run.records_filtered_out`, not stored. Fields that are scraped but not required (status, raw "Zeitraum" text, source coordinates as given) live in `raw_data` only.

No secondary indexes: at <1000 rows a sequential scan beats any index, and nothing queries the geometry spatially except validation, which reads every row anyway. The only index besides the primary key is the one behind `uq_source_record`, which `ON CONFLICT` requires.

1. `iris_staging.ingest_run` - Tracks each harvester execution for auditability and quality reporting.
2. `iris_staging.signal_ingest` - Stores the actual details of the filtered industrial land-use zoning hearings near Dresden and Leipzig.

### Upsert Logic

Idempotency comes from the natural key: `uq_source_record UNIQUE (source, source_record_id)` plus `INSERT ... ON CONFLICT DO UPDATE`. Re-running over the same data cannot add rows. There is **no change detection**: an existing row is always rewritten with the latest values. Nothing in the brief consumes "what changed", so telling a real change apart from a re-scrape isn't worth a hash column and its tests (see [What We Are NOT Doing](#what-we-are-not-doing)).

**Step 0: de-duplicate the batch in Python.** Records are written one statement each, so a repeated key would not fail, but it would be written twice and counted as 1 inserted + 1 updated for a single row, and the repeat (a source problem) would go unseen. Records with a repeated `(source, source_record_id)` in one batch (e.g. a procedure that moves from `AKTUELLE` to `BEENDETE` between the two listing requests) are collapsed to the last occurrence. Each one dropped is logged to `ingest_run.errors` as `duplicate_in_batch`.

**Step 1: insert or overwrite.**

The statement is `UPSERT` in [`src/harvester/db/upsert.py`](../src/harvester/db/upsert.py): `INSERT ... ON CONFLICT ON CONSTRAINT uq_source_record DO UPDATE` rewrites every column except the key, `id` and `created_at`, and sets `updated_at = NOW()`. The point is built in the statement with `ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 25832)`, so NULL lon/lat (unresolved or rejected) gives a NULL geometry. It ends with `RETURNING (xmax = 0) AS inserted`.

**Counting:** `inserted` = rows returned with `inserted = true`; `updated` = the rest. A re-run over unchanged data therefore reports 0 inserted and N updated, with the row count and every `id` and `created_at` unchanged.

`xmax = 0` is the standard PostgreSQL test for whether a row from an `ON CONFLICT` upsert was newly inserted. It's covered by a test.

**How it's executed:** the statement is a SQLAlchemy `text()` with named parameters, run with one parameter dict per record (no ORM models; see [What We Are NOT Doing](#what-we-are-not-doing)). psycopg 3 adapts Python lists to `TEXT[]`. Dicts for `JSONB` must be wrapped in `psycopg.types.json.Jsonb(...)`, or binding fails. Records are executed one statement each, in a loop inside the batch's single transaction, collecting each `RETURNING` row. That's simple, keeps the per-record counting obvious, and is fast enough for <1000 rows; collecting `RETURNING` rows from one `executemany` call isn't something to rely on with `text()`.

**Rows are never deleted.** A plan that disappears from the portal, or stops matching after the keyword list changes, keeps its row; its `ingest_run_id` then points at an older run than the latest one covering its city (validation check 9 lists these).

## Industrial Keyword Filter

Plan titles are usually German compounds ("Gewerbestandort Nord", "Logistikzentrum Radefeld", "Kleingewerbe am Bahnhof"), so whole-word matching misses most real hits. The filter uses two kinds of match:
- **Long keywords** (Gewerbe, Industrie, Logistik, ...) match case-insensitively **anywhere inside a word**. The whole word containing the hit is kept as evidence.
- **Short codes** (GE, GI, GVZ) match **case-sensitively as whole tokens only**. As substrings they would hit ordinary words ("gegen", "Gießen").

Implemented as `IndustrialFilter` in [`src/harvester/pipeline/filter.py`](../src/harvester/pipeline/filter.py). Its `check(title)` returns a `FilterResult` with `is_industrial`, `matched_keywords` (as written in the config) and `evidence` (the matched words, e.g. "Gewerbestandort").

`evidence` goes into `raw_data` and the quality report, so false positives (e.g. "Wohn- und Gewerbepark", a mixed area) can be spotted and the keyword list tuned.

The filter runs on listing data, **before** any detail page is fetched, so only matches cost an extra request. Only records with `is_industrial = True` are stored; `matched_keywords` is the stored evidence for why each row is there. Non-matches are counted in `ingest_run.records_filtered_out`, and `--dry-run` prints every record with its filter decision, so false negatives can be checked while tuning the keyword list.

## Performance Considerations

- **Request volume (2026-10-27):** 4 listings (2 cities x 2 statuses) + one detail page per match (~9) = ~13 requests, under a minute at a 2 s delay. The live run is quick enough to demo; re-run and idempotency demos also work against fixtures.
- **Database:** Small table (<1000 rows, only industrial matches). No indexes beyond the primary key and `uq_source_record`; see [Database Schema](#database-schema).
- **Memory:** HTML pages are small (<700 KB each). No memory concerns.
- **HTTP:** one `httpx.Client` reused for every request (connection reuse).

## What We Are NOT Doing

- Browser automation (Playwright). Every page needed is a plain GET (Key Discoveries 1, 4).
- Geocoding of any kind (Nominatim, Google, address lookup). The portal's markers supply coordinates; a marker without them is stored `unresolved` with the reason.
- Paging through the state portal's AJAX card paginator: the marker JSON lists every result.
- Matching keywords in descriptions. The filter reads titles: plan titles name the area type, and descriptions mention "Gewerbe" in passing (neighbouring uses, "Gewerbesteuer"), which would add false positives. Detail pages are then fetched only for matches, for the publication date.
- Scraping surrounding districts (Meissen, Nordsachsen, Leipzig-Land, etc.).
- Production hardening (see the scope note in the [plan's Overview](2026-10-27-IRIS-SIG-08-saxony-portal-harvester.md#overview)). For example, no SQLAlchemy `naming_convention`: it only pays off when later migrations alter or drop constraints by name, and this repo is expected to have the single migration `001`. Constraints and indexes the code refers to (e.g. `uq_source_record` for `ON CONFLICT`) are named explicitly.
- ORM models (`schema.py`, GeoAlchemy2). Two tables and one migration don't justify a second copy of the schema: the SQL DDL in migration `001` is the single source of truth, and the `db`-marked tests run every query against it.
- Incremental retrieval (skipping unchanged detail pages). The brief asks for idempotent re-runs, not fast ones, and a full live run is ~13 requests (under a minute).
- Change detection (`content_hash`, `last_seen_*` columns, an "unchanged" counter). The brief asks for no duplicates on re-run, which the unique key plus `ON CONFLICT` guarantees on its own. Nothing consumes "what changed", so existing rows are simply rewritten.
- Columns the brief doesn't ask for (plan type, plan number, procedure step, status, participation end date). They are kept as scraped in `raw_data`.
- Deleting rows that vanish from the portal or stop matching the filter. They stay, with an older `ingest_run_id` (validation check 9).
