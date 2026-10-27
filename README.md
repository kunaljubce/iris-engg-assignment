# iris-engg-assignment

Filter industrial land-use zoning hearings around Dresden and Leipzig.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python version (3.12, from
  `.python-version`) and all dependencies.
- Docker with Docker Compose, for the local PostgreSQL 16 / PostGIS 3.4 database.

### Steps

```bash
# 1. Install Python dependencies (locked in uv.lock), including the dev tools
#    (pytest, ruff, mypy)
uv sync --extra dev

# 2. Download the Chromium build that Playwright drives
uv run playwright install chromium

# 3. Create your local environment file and set POSTGRES_PASSWORD in it
cp .env.example .env

# 4. Start PostgreSQL/PostGIS and wait until it reports "healthy"
docker compose up -d
docker compose ps

# 5. Create the database schema
uv run alembic upgrade head
```

To inspect the schema SQL without a database or `.env`: `uv run alembic upgrade head --sql`

Notes:

- **Run all commands from the repository root.** The harvester and Alembic read `.env` from the current directory.
- **Step 2 is a separate download.** The `playwright` Python package doesn't include a browser, and Python packaging can't fetch one at install time. The Chromium version is tied to the `playwright` version pinned in `uv.lock`, so re-run step 2 after upgrading Playwright.
- **`.env` is gitignored and must never be committed.** `docker compose up` refuses to start if `POSTGRES_PASSWORD` is not set.
- **Set the password once.** Docker Compose and the harvester read the same `POSTGRES_*` variables. The harvester builds the connection URL from them, so the password can contain any characters and doesn't need URL-encoding.
- **"healthy" means ready.** The healthcheck connects over TCP, which only succeeds after the first-start init scripts have finished.
- **Step 1 needs `--extra dev`.** Without it, `uv sync` installs only the runtime dependencies and removes pytest, ruff and mypy from `.venv`.
- **Apple Silicon:** the official `postgis/postgis` image is amd64-only, so Docker runs it under emulation (`platform: linux/amd64` in `docker-compose.yml`). It's slower to start but fine for this dataset.
- **Step 4 creates two databases:** `iris` for the harvester and `iris_test` for the database tests, both with PostGIS. The image's own `10_postgis.sh` sets up `iris`, and `docker/initdb/20_create_test_db.sql` sets up `iris_test`.
- **Database init scripts only run on an empty data volume.** To recreate the database from scratch, run `docker compose down -v` and then `docker compose up -d`.
- **Everything the harvester creates is in the `iris_staging` schema,** including Alembic's version table (`iris_staging.alembic_version`). Migrations never drop the schema or the PostGIS extension.
