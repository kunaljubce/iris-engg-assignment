# iris-engg-assignment

Filter industrial land-use zoning hearings around Dresden and Leipzig.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python version (3.12, from
  `.python-version`) and all dependencies.
- Docker with Docker Compose, for the local PostgreSQL 16 / PostGIS 3.4 database.

### Steps

```bash
# 1. Install Python dependencies
uv sync --extra dev

# 2. Download Chromium build that Playwright drives
uv run playwright install chromium

# 3. Create your local .env and set POSTGRES_PASSWORD in it; without this
# variable, `docker compose up` refuses to start.
cp .env.example .env

# 4. Start PostgreSQL/PostGIS and wait until it reports "healthy"
# This step creates 2 databases - `iris` for the harvester and `iris_test` for 
# the database tests, both with PostGIS
docker compose up -d
docker compose ps

# 5. Create the database schema
uv run alembic upgrade head

# 20. Kill running Docker containers
docker compose down -v
```

To inspect the schema SQL without a database or `.env`: `uv run alembic upgrade head --sql`

Notes:

- **Run all commands from the repository root.** The harvester and Alembic read `.env` from the current directory.
- **`.env` is gitignored and must never be committed.** `docker compose up` refuses to start if `POSTGRES_PASSWORD` is not set.
- **Set the password once.** Docker Compose and the harvester read the same `POSTGRES_*` variables. The harvester builds the connection URL from them, so the password can contain any characters and doesn't need URL-encoding.
- **Apple Silicon:** the official `postgis/postgis` image is amd64-only, so Docker runs it under emulation (`platform: linux/amd64` in `docker-compose.yml`). It's slower to start but fine for this dataset.
- **Everything the harvester creates is in the `iris_staging` schema,** including Alembic's version table (`iris_staging.alembic_version`). Migrations never drop the schema or the PostGIS extension.
