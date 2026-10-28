# iris-engg-assignment

[![tests](https://github.com/kunaljubce/iris-engg-assignment/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kunaljubce/iris-engg-assignment/actions/workflows/ci.yml)

IRIS-SIG-08: harvest land-use plan hearings (Bauleitplanverfahren) for Dresden and Leipzig, keep the industrial ones, and load them into `iris_staging.signal_ingest` (PostgreSQL 16 / PostGIS 3.4) with EPSG:25832 points.

**How it works.** Each run: scrape → filter → normalise → upsert, tracked in `iris_staging.ingest_run`.

| Step | Code | What it does |
|---|---|---|
| Scrape | `src/harvester/adapters/` | `sachsen_portal.py` reads the portal; `fetch.py` does the GET requests with retries and a delay |
| Filter | `pipeline/filter.py` | keeps records whose title names an industrial use |
| Normalise | `pipeline/normalise.py`, `pipeline/geocode.py` | publication date, bounding-box check of the coordinates, quality flags |
| Upsert | `db/upsert.py` | `INSERT … ON CONFLICT (source, source_record_id) DO UPDATE`, WGS84 → EPSG:25832 in PostGIS |
| Run tracking | `runner.py` | one `ingest_run` row per run, with counts and errors |
| CLI | `cli.py`, `report.py` | `harvester run` and `harvester report` |

## Data source

The Saxon state participation portal, `buergerbeteiligung.sachsen.de` (the source named in the brief). It is a public site, read with plain HTTP GET requests: no login, no browser, no API key. Checked live on 2026-10-27 and 2026-10-28; the saved pages and their URLs are in `tests/fixtures/SOURCES.md`. Requests are 2 s apart, retried on 5xx, 429 and timeouts, and send the user agent `IRIS-SIG-08-Harvester/1.0 (bot)`.

| | |
|---|---|
| Cities | Dresden and Leipzig (`/portal/dresden/…`, `/portal/leipzig/…`) |
| Selection | format "Bauleitplan", status `AKTUELLE` (open) and `BEENDETE` (finished): one listing per city and status |
| What is read | the map's `drawMarkers([...])` JSON, one entry per procedure, plus each matching record's detail page |
| Pagination | not needed: the marker JSON lists every result, while the cards below it are paged 10 at a time. The "von N" count is checked against the markers, so a missing result shows as a `count_mismatch` error |
| Records (2026-10-28) | 83 (Dresden 60, Leipzig 23), 9 industrial (Dresden 7, Leipzig 2) |

## Mapping assumptions

| Column | From |
|---|---|
| `source_record_id` | marker `id` |
| `title` | marker title |
| `detail_url` | marker `href` |
| `city` | the city whose listing it came from |
| `location_text` | marker address |
| `publication_date` | start of the detail page's "Zeitraum" |
| `geometry` | marker latitude / longitude |
| `matched_keywords` | keywords found in the title |
| `raw_data` | marker, listing URL, raw "Zeitraum" text, filter evidence |

- **Industrial means:** the title contains one of the keywords in `config/settings.yml` (e.g. Gewerbe, Industrie, Logistik, Gewerbepark), case-insensitively and also inside compound words (Gewerbecampus). The short codes GE, GI and GVZ match only as whole, upper-case words. Only matches are stored; the rest are counted as `filtered out` on the run.
- **Publication date = start of the "Zeitraum".** The portal has no "published on" field. The detail page's "Zeitraum 12.10.2026 bis 13.11.2026" is the period the draft is publicly displayed (BauGB § 3 (2)), so its start date is used. The raw text is kept in `raw_data`.
- **The marker coordinates are the plan's location.** They are WGS84 and checked against the city's bounding box in `settings.yml`. 
  - Outside the box, or not a number: `rejected_out_of_bounds` with no point, the raw values kept in `raw_data`. 
  - Missing: `unresolved`. Inside: transformed to EPSG:25832 by PostGIS (`source_coordinates`).
- **A record that couldn't be read completely is still stored, flagged:** e.g. `missing_publication_date` or `detail_page_failed` in `quality_flags`. Records that can't be used at all (no ID or title) are not stored; they are counted as `rejected` with the error on the run.
- **Finished hearings are kept.** Each portal entry is a hearing with its own "Zeitraum", whether it is still open (`AKTUELLE`) or over (`BEENDETE`), so both are read. Whether a hearing is still open shows in `raw_data`: the `listing_url` has its status, and the "Zeitraum" text has its end date.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) installs all dependencies, on top of the pinned Python version.
- Docker with Docker Compose, for the local PostgreSQL 16 / PostGIS 3.4 database.

### Steps (with verifications to test everything works)

```bash
# 1. Install Python dependencies
uv sync --extra dev

# 2. Create your local .env and set POSTGRES_PASSWORD in it; without this
# variable, `docker compose up` refuses to start.
cp .env.example .env

# 3. Start PostgreSQL/PostGIS and wait until it reports "healthy"
# This step creates 2 databases - `iris` for the harvester and `iris_test` for
# the database tests, both with PostGIS
docker compose up -d
docker compose ps

# 4. Create the database schema
uv run alembic upgrade head

# 5. Verify - Tests pass
uv run mypy . && \
uv run pytest

# 6. Verify - PostGIS is installed in both databases (expect 3.4 ... twice)
docker compose exec db psql -U iris -d iris -c "SELECT PostGIS_Version();"
docker compose exec db psql -U iris -d iris_test -c "SELECT PostGIS_Version();"

# 7. Verify - alembic_version, ingest_run and signal_ingest in iris_staging
docker compose exec db psql -U iris -d iris -c "\dt iris_staging.*"
uv run alembic current        # expect: 001 (head)

# 8. Verify - Migrations reverse cleanly
uv run alembic downgrade base
uv run alembic upgrade head
```

The database tests (marked `db`) run against `iris_test`. Without the database settings in `.env` they are skipped, and if the settings are there but the database can't be reached they fail.

## Run the harvester

```bash
# Scrape, filter and print what would be kept (KEEP) or dropped (-); writes nothing
uv run harvester run --dry-run

# Harvest into iris_staging.signal_ingest
uv run harvester run

# One city only (slug as in config/settings.yml)
uv run harvester run --city dresden
```

Each run gets one row in `iris_staging.ingest_run` and prints a summary line (scraped, filtered out, rejected, inserted, updated, errors). The command exits with code 1 if the run failed. Re-running is safe: records are upserted on `(source, source_record_id)`, so a second run updates rows and adds none.

### Quality report

```bash
# Print the quality report from the database (also: uv run python -m harvester report)
uv run harvester report
```

The report reads only from the database, so it can be run at any time, also after a failed run. Output after the first run (live data on 2026-10-28):

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

- **Latest runs:** the most recent run, from `iris_staging.ingest_run`. `city` is `all` when the run covered all configured cities.
  - `scraped`: records read from the portal
  - `industrial`: records that passed the industrial keyword filter
  - `rejected`: records that couldn't be used (e.g. no ID or title)
  - `inserted` / `updated`: new rows / existing rows rewritten
  - `errors`: errors recorded for the run
- **Run errors:** the latest runs' errors counted by kind, with one example message each. A failed run's message is listed as `run_failed`; the full list is in `ingest_run.errors`.
- **Stored records:** everything in `iris_staging.signal_ingest`, from all runs, by city.
  - `with point`: records with a geometry
  - `unresolved`: no coordinates in the source
  - `rejected bbox`: coordinates outside the city's bounding box or not valid numbers, so no point is stored
- **Quality flags:** how many stored records carry each flag, e.g. `missing_publication_date` or `detail_page_failed`. The live run on 2026-10-28 had none; `tests/test_quality_report.py` shows the report with a failed run, errors and flags.

### Check the results in the database

`sql/validation_queries.sql` holds 9 checks on the stored data. Run them all with:

```bash
docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql
```

| # | Check | Expect after the first run (live data on 2026-10-28) |
|---|---|---|
| 1 | Latest run: status and counts | 83 scraped, 9 inserted, `completed` |
| 2 | Run errors by kind | 0 rows |
| 3 | Records by city and geocode status | Dresden 7, Leipzig 2, all `source_coordinates` with point and date |
| 4 | Quality flags | 0 rows |
| 5 | Records without a point, and why | 0 rows |
| 6 | Points outside the bounding box of the run that wrote them | 0 rows |
| 7 | Points in EPSG:25832 and back in WGS84 | 9 rows, SRID 25832; Dresden about 13.8°E / 51.0°N, Leipzig about 12.3°E / 51.4°N |
| 8 | Each record with its run, source URL and filter evidence | 9 rows |
| 9 | Records not written by the latest run covering their city | 0 rows |

Duplicate keys and a point without `source_coordinates` aren't checked here: the database's constraints make them impossible. To check idempotency, run `uv run harvester run` again: check 1 should show 0 inserted and 9 updated, and the record count should stay at 9.

To run a single query, open a SQL shell with `docker compose exec db psql -U iris -d iris`.

### Sample output

`sample_output/signal_ingest_sample.csv` holds all 9 records from the live run on 2026-10-28, with every column of `signal_ingest`. The geometry is given as `x_25832`/`y_25832` (EPSG:25832, as stored) and `lon` / `lat` (WGS84). It's public-source data: `raw_data` keeps only what the portal publishes (marker, listing URL, Zeitraum, filter evidence), with no personal details, so nothing needed anonymizing.

To regenerate it from your database:

```bash
docker compose exec -T db psql -U iris -d iris -c "COPY (
  SELECT id, source, source_record_id, ingest_run_id, city, title, detail_url, location_text,
         publication_date, matched_keywords, geocode_status, geocode_reason,
         round(ST_X(geometry)::numeric, 2) AS x_25832, round(ST_Y(geometry)::numeric, 2) AS y_25832,
         round(ST_X(ST_Transform(geometry, 4326))::numeric, 6) AS lon,
         round(ST_Y(ST_Transform(geometry, 4326))::numeric, 6) AS lat,
         quality_flags, raw_data, created_at, updated_at
  FROM iris_staging.signal_ingest ORDER BY source, city, source_record_id
) TO STDOUT WITH (FORMAT csv, HEADER)" > sample_output/signal_ingest_sample.csv
```

### Stop or reset the database

```bash
# Stop the container; data is kept for the next `docker compose up -d`
docker compose down

# Reset: also delete the data volume (all harvested rows), then start from step 3
docker compose down -v
```

To inspect the schema SQL without a database or `.env`: `uv run alembic upgrade head --sql`

Notes:

- **Run all commands from the repository root.** The harvester and Alembic read `.env` from the current directory.
- **`.env` is gitignored and must never be committed.** `docker compose up` refuses to start if `POSTGRES_PASSWORD` is not set.
- **Everything the harvester creates is in the `iris_staging` schema,** including Alembic's version table (`iris_staging.alembic_version`). Migrations never drop the schema or the PostGIS extension.

## Configuration

- **`config/settings.yml`** (no secrets, checked in): the sources and their cities, the request delay, retries and timeout, the industrial keywords, and a WGS84 bounding box per city. It is validated on load: an unknown key, an inverted box, or a city without a box stops the run with a clear error.
- **`.env`** (gitignored, from `.env.example`): the `POSTGRES_*` connection settings, and optionally `HARVESTER_USER_AGENT` to add an operator contact to the user agent.

To add a city that is on the state portal, add its portal slug under `cities` and its box under `bounding_boxes`; no code changes. Each run stores the config it used in `ingest_run.config_snapshot`.


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

## Known limitations

- **Only the two cities are covered.** "Around Dresden and Leipzig" is read as the cities themselves: the state portal's Dresden and Leipzig listings. Hearings run by surrounding municipalities or districts (e.g. Meißen, Nordsachsen) are not read. Any that publish on the state portal can be added as a `cities` entry plus a bounding box in `settings.yml`, with no code changes.
- **The portal lists only what a city publishes there.** Leipzig has 23 procedures on it; on 2026-10-28 the two industrial ones are both finished, so there is no open Leipzig hearing in the data.
- **The filter reads titles only.** A plan whose title names a place but not the use (e.g. "Bebauungsplan Nr. 422 Radefelder Allee West") is missed. Descriptions are not read, because they mention "Gewerbe" in passing and would add false positives.
- **EPSG:25832 is used outside its zone.** It covers 6–12° E; Leipzig (~12.4° E) and Dresden (~13.7° E) lie in the zone of EPSG:25833. The brief requires 25832, so points are correct but have growing scale distortion to the east.
- **The scraper depends on the portal's markup,** above all the `drawMarkers` call. A change shows as errors on the run (`markers_not_found`, `count_mismatch`) or a `failed` run, not as a silently empty result.
- **Rows are never deleted.** A plan that leaves the portal stays in the table; validation check 9 lists rows the latest run covering their city didn't write.
