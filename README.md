# iris-engg-assignment

[![tests](https://github.com/kunaljubce/iris-engg-assignment/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kunaljubce/iris-engg-assignment/actions/workflows/ci.yml)

Filter industrial land-use zoning hearings around Dresden and Leipzig.

Sources, both public pages fetched with plain HTTP GET requests (no browser needed):

- Saxon state participation portal, `buergerbeteiligung.sachsen.de` (Dresden and Leipzig)
- Leipzig city plan register, `www.leipzig.de` (plans "im Verfahren" only)

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
