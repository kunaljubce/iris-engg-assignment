# iris-engg-assignment

[![tests](https://github.com/kunaljubce/iris-engg-assignment/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kunaljubce/iris-engg-assignment/actions/workflows/ci.yml)

IRIS-SIG-08: harvest land-use plan hearings (Bauleitplanverfahren) for Dresden and
Leipzig, keep the industrial ones, and load them into `iris_staging.signal_ingest`
(PostgreSQL 16 / PostGIS 3.4) with EPSG:25832 points.

**How it works.** One run per source: scrape → filter → normalise → upsert, tracked in
`iris_staging.ingest_run`.

| Step | Code | What it does |
|---|---|---|
| Scrape | `src/harvester/adapters/` | one module per portal; `fetch.py` does the GET requests with retries and a delay |
| Filter | `pipeline/filter.py` | keeps records whose title (or Leipzig Thema) names an industrial use |
| Normalise | `pipeline/normalise.py`, `pipeline/geocode.py` | publication date, bounding-box check of the coordinates, quality flags |
| Upsert | `db/upsert.py` | `INSERT … ON CONFLICT (source, source_record_id) DO UPDATE`, WGS84 → EPSG:25832 in PostGIS |
| Run tracking | `runner.py` | one `ingest_run` row per source and run, with counts and errors |
| CLI | `cli.py`, `report.py` | `harvester run` and `harvester report` |

## Data sources

Both are public pages, read with plain HTTP GET requests: no login, no browser, no API
key. Checked live on 2026-10-27 and 2026-10-28; the saved pages and their URLs are in
`tests/fixtures/SOURCES.md`. Requests are 2 s apart, retried on 5xx, 429 and timeouts,
and send the user agent `IRIS-SIG-08-Harvester/1.0 (bot)`.

| | Saxon state participation portal | Leipzig city plan register |
|---|---|---|
| URL | `buergerbeteiligung.sachsen.de` (the source named in the brief) | `www.leipzig.de` → Bebauungspläne |
| Cities | Dresden and Leipzig | Leipzig |
| Selection | format "Bauleitplan", status `AKTUELLE` and `BEENDETE` | status "im Verfahren" only |
| What is read | the map's `drawMarkers([...])` JSON, one entry per procedure, plus each matching record's detail page | the table rows: title, status, districts, Thema |
| Pagination | not needed: the marker JSON lists every result; the "von N" count is checked against it | follows the "load more" button page by page |
| Records (2026-10-28) | 83 (Dresden 60, Leipzig 23), 9 industrial | 39, 10 industrial |

Why the second source: the state portal lists only the procedures Leipzig publishes there
(23). The city register is Leipzig's own list of plans. Its plans in force
("rechtskräftig", about 93 %) are not hearings, so only "im Verfahren" plans are read.

## Mapping assumptions

| Column | Saxon state portal | Leipzig city register |
|---|---|---|
| `source_record_id` | marker `id` | row id `project-{id}` |
| `title` | marker title | link text |
| `detail_url` | marker `href` | link `href` |
| `city` | the city whose listing it came from (`/portal/dresden/…`, `/portal/leipzig/…`) | `leipzig` |
| `location_text` | marker address | district(s), e.g. "Nord / Eutritzsch" |
| `publication_date` | start of the detail page's "Zeitraum" | none |
| `geometry` | marker latitude / longitude | none (`unresolved`) |
| `matched_keywords` | keywords found in the title | keywords in the title, plus `thema:Gewerbe` |
| `raw_data` | marker, raw "Zeitraum" text, filter evidence | status, districts, Thema, listing URL, filter evidence |

- **Industrial means:** the title contains one of the keywords in `config/settings.yml`
  (e.g. Gewerbe, Industrie, Logistik, Gewerbepark), case-insensitively and also inside
  compound words (Gewerbecampus). The short codes GE, GI and GVZ match only as whole,
  upper-case words. A Leipzig register row also matches when its Thema is "Gewerbe". Only
  matches are stored; the rest are counted as `filtered out` on the run.
- **Publication date = start of the "Zeitraum".** The state portal has no "published on"
  field. The detail page's "Zeitraum 12.10.2026 bis 13.11.2026" is the period the draft is
  publicly displayed (BauGB § 3 (2)), so its start date is used. The raw text is kept in
  `raw_data`.
- **The marker coordinates are the plan's location.** They are WGS84 and checked against
  the city's bounding box in `settings.yml`. Outside the box, or not a number:
  `rejected_out_of_bounds` with no point, the raw values kept in `raw_data`. Missing:
  `unresolved`. Inside: transformed to EPSG:25832 by PostGIS (`source_coordinates`).
- **A record that couldn't be read completely is still stored, flagged:** e.g.
  `missing_publication_date` or `detail_page_failed` in `quality_flags`. Records that
  can't be used at all (no ID or title) are not stored; they are counted as `rejected`
  with the error on the run.
- **Finished state-portal hearings are kept.** Each state-portal entry is a hearing with
  its own "Zeitraum", whether it is still open (`AKTUELLE`) or over (`BEENDETE`), so both
  are read. A Leipzig register plan in force ("rechtskräftig") is not a hearing record at
  all, so it is left out. Whether a hearing is still open shows in `raw_data`: the
  `listing_url` has its status, and the "Zeitraum" text has its end date.
- **Each portal's copy of a plan is its own row,** with its own ID and URL (see the
  limitations).

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python version (3.12, from
  `.python-version`) and all dependencies.
- Docker with Docker Compose, for the local PostgreSQL 16 / PostGIS 3.4 database.

### Steps

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
```

### Verify the setup

```bash
# Lint, type checks and tests
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest

# PostGIS is installed in both databases (expect 3.4 ... twice)
docker compose exec db psql -U iris -d iris -c "SELECT PostGIS_Version();"
docker compose exec db psql -U iris -d iris_test -c "SELECT PostGIS_Version();"

# The schema exists: alembic_version, ingest_run and signal_ingest in iris_staging
docker compose exec db psql -U iris -d iris -c "\dt iris_staging.*"
uv run alembic current        # expect: 001 (head)

# Migrations reverse cleanly
uv run alembic downgrade base
uv run alembic upgrade head
```

The database tests (marked `db`) run against `iris_test`. Without the database settings
in `.env` they are skipped, and if the settings are there but the database can't be
reached they fail.

## Run the harvester

```bash
# Scrape, filter and print what would be kept (KEEP) or dropped (-); writes nothing
uv run harvester run --dry-run

# Harvest all sources into iris_staging.signal_ingest
uv run harvester run

# One source or one city only (names as in config/settings.yml)
uv run harvester run --source leipzig_stadtportal
uv run harvester run --city dresden
```

Each source gets one row in `iris_staging.ingest_run`, and the run prints one summary
line per source (scraped, filtered out, rejected, inserted, updated, errors). The command
exits with code 1 if any run failed. Re-running is safe: records are upserted on
`(source, source_record_id)`, so a second run updates rows and adds none.

### Quality report

```bash
# Print the quality report from the database (also: uv run python -m harvester report)
uv run harvester report
```

The report reads only from the database, so it can be run at any time, also after a
failed run. Output after the first run (live data on 2026-10-28):

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

- **Latest runs:** the most recent run of each source, from `iris_staging.ingest_run`.
  `city` is `all` when the run covered all the source's cities.
  - `scraped`: records read from the portal
  - `industrial`: records that passed the industrial keyword filter
  - `rejected`: records that couldn't be used (e.g. no ID or title)
  - `inserted` / `updated`: new rows / existing rows rewritten
  - `errors`: errors recorded for the run
- **Run errors:** the latest runs' errors counted by kind, with one example message each.
  A failed run's message is listed as `run_failed`; the full list is in
  `ingest_run.errors`.
- **Stored records:** everything in `iris_staging.signal_ingest`, from all runs, by
  source and city.
  - `with point`: records with a geometry
  - `unresolved`: no coordinates in the source
  - `rejected bbox`: coordinates outside the city's bounding box or not valid numbers, so
    no point is stored
- **Quality flags:** how many stored records carry each flag, e.g.
  `missing_publication_date`.

### Check the results in the database

`sql/validation_queries.sql` holds 10 checks on the stored data. Run them all with:

```bash
docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql
```

| # | Check | Expect after the first run (live data on 2026-10-28) |
|---|---|---|
| 1 | Latest run per source: status and counts | `sachsen_portal`: 83 scraped, 9 inserted; `leipzig_stadtportal`: 39 scraped, 10 inserted; both `completed` |
| 2 | Run errors by kind | 0 rows |
| 3 | Records by source, city and geocode status | `sachsen_portal`: Dresden 7, Leipzig 2, all with point and date; `leipzig_stadtportal`: 10, all `unresolved` |
| 4 | Quality flags | `missing_publication_date` on the 10 `leipzig_stadtportal` rows |
| 5 | Records without a point, and why | the 10 `leipzig_stadtportal` rows: "source gives district only: …" |
| 6 | Points outside the bounding box of the run that wrote them | 0 rows |
| 7 | Points in EPSG:25832 and back in WGS84 | 9 rows, SRID 25832; Dresden about 13.8°E / 51.0°N |
| 8 | Each record with its run, source URL and filter evidence | 19 rows |
| 9 | Records not written by the latest run covering their city | 0 rows |
| 10 | The same plan on both portals | Leipzig Nr. 911 on both, kept as separate signals |

Duplicate keys and a point without `source_coordinates` aren't checked here: the
database's constraints make them impossible. To check idempotency, run
`uv run harvester run` again: check 1 should show 0 inserted and 9 / 10 updated, and the
record count should stay at 19.

To run a single query, open a SQL shell with `docker compose exec db psql -U iris -d iris`.

### Sample output

`sample_output/signal_ingest_sample.csv` holds all 19 records from the live run on
2026-10-28, with every column of `signal_ingest`. The geometry is given as `x_25832` /
`y_25832` (EPSG:25832, as stored) and `lon` / `lat` (WGS84). It's public-source data:
`raw_data` keeps only what the portals publish (marker, Zeitraum, Thema, districts,
status), with no personal details, so nothing needed anonymizing.

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
- **Set the password once.** Docker Compose and the harvester read the same `POSTGRES_*` variables. The harvester builds the connection URL from them, so the password can contain any characters and doesn't need URL-encoding.
- **Database init scripts run only once, on an empty data volume.** After editing `docker/initdb/20_create_test_db.sql`, or `POSTGRES_USER`, `POSTGRES_PASSWORD` or `POSTGRES_DB` in `.env`, reset the database (`docker compose down -v`) for the change to take effect.
- **Apple Silicon:** the official `postgis/postgis` image is amd64-only, so Docker runs it under emulation (`platform: linux/amd64` in `docker-compose.yml`). It's slower to start but fine for this dataset.
- **Everything the harvester creates is in the `iris_staging` schema,** including Alembic's version table (`iris_staging.alembic_version`). Migrations never drop the schema or the PostGIS extension.

## Configuration

- **`config/settings.yml`** (no secrets, checked in): the sources and their cities, the
  request delay, retries and timeout, the industrial keywords, and a WGS84 bounding box
  per city. It is validated on load: an unknown key, an inverted box, or a city without a
  box stops the run with a clear error.
- **`.env`** (gitignored, from `.env.example`): the `POSTGRES_*` connection settings, and
  optionally `HARVESTER_USER_AGENT` to add an operator contact to the user agent.

To add a city that is on the state portal, add its portal slug under the
`sachsen_portal` source's `cities` and its box under `bounding_boxes`; no code changes. Each run stores
the config it used in `ingest_run.config_snapshot`.

## Tests

`uv run pytest` runs all tests. The scrapers are tested against real pages saved on
2026-10-27 (`tests/fixtures/`, provenance in `SOURCES.md`) through httpx's mock
transport, so no test touches the network. Edge cases (empty listing, malformed markers,
HTTP errors, next page) are small HTML snippets inside the tests. `test_fetch.py` covers
the retries: 5xx, 429 with `Retry-After` and timeouts are retried, 404 is not.

The `db` tests run against `iris_test`:

- `test_upsert.py`: a re-run inserts nothing and keeps each row's `id` and
  `created_at`; a key repeated in one batch is written once and reported.
- `test_pipeline_integration.py`: the whole run from fixtures to the database; running it
  twice leaves the same rows; an unreachable source gives a `failed` run; a crash after
  the upsert leaves no records.
- `test_bounding_box.py`: points outside the box are stored without geometry; the
  EPSG:25832 transform matches pyproj within 1 m.
- `test_quality_report.py`: the report's tables.

CI (`.github/workflows/ci.yml`) runs ruff, mypy, the migrations up / down / up and all
tests against a PostGIS 16-3.4 service on every pull request to `main` and every push to
`main`. The badge at the top shows the result on `main`; it only renders while the
repository is public.

## Known limitations

- **Only the two cities are covered.** "Around Dresden and Leipzig" is read as the cities
  themselves: the state portal's Dresden and Leipzig listings and Leipzig's register.
  Hearings run by surrounding municipalities or districts (e.g. Meißen, Nordsachsen) are
  not read. Any that publish on the state portal can be added as a `cities` entry plus a
  bounding box in `settings.yml`, with no code changes.

- **The filter reads titles only.** A plan whose title names a place but not the use is
  missed. For example, Leipzig's "Bebauungsplan Nr. 422 Radefelder Allee West" has no
  keyword in its title and no Thema in the register.
- **Thema "Gewerbe" also tags mixed-use plans.** 7 of the 10 Leipzig register rows match
  on Thema only, among them "Alfred-Kunze-Sportpark" and "Wohngebiet und Reitsportanlage
  Sternbachstraße". They are kept, since the city tags them as commercial, with
  `matched_keywords = {thema:Gewerbe}` so they can be told apart.
- **Leipzig register rows have no date and no point.** The register and its detail pages
  give only districts, no coordinates and no dates. The rows are `unresolved` with the
  district as `location_text`, and flagged `missing_publication_date`. District centroids
  would be a guess, so no point is made up.
- **The same plan can appear on both portals,** e.g. Leipzig Nr. 911, as two rows with
  different IDs and URLs. They are not merged; validation check 10 lists the pairs.
- **EPSG:25832 is used outside its zone.** It covers 6–12° E; Leipzig (~12.4° E) and
  Dresden (~13.7° E) lie in the zone of EPSG:25833. The brief requires 25832, so points are
  correct but have growing scale distortion to the east.
- **The scrapers depend on the portals' markup:** the state portal's `drawMarkers` call
  and the register's table layout. A change shows as errors on the run
  (`markers_not_found`, `count_mismatch`, `unexpected_row_layout`) or a `failed` run, not
  as a silently empty result.
- **Rows are never deleted.** A plan that leaves a portal stays in the table; validation
  check 9 lists rows the latest run of their source and city didn't write.
