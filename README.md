# iris-engg-assignment

Filter industrial land-use zoning hearings around Dresden and Leipzig.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python version (3.12, from
  `.python-version`) and all dependencies.
- Docker with Docker Compose, for the local PostgreSQL 16 / PostGIS 3.4 database.

### Steps

```bash
# 1. Install Python dependencies (locked in uv.lock)
uv sync

# 2. Download the Chromium build that Playwright drives
uv run playwright install chromium

# 3. Create your local environment file and set a password in it
cp .env.example .env

# 4. Start PostgreSQL/PostGIS
docker compose up -d

# 5. Create the database schema
uv run alembic upgrade head
```

Notes:

- **Step 2 is a separate download.** The `playwright` Python package doesn't include a
  browser, and Python packaging can't fetch one at install time. The Chromium version is
  tied to the `playwright` version pinned in `uv.lock`, so re-run step 2 after upgrading
  Playwright.
- **`.env` is gitignored and must never be committed.** `docker compose up` refuses to start
  if `POSTGRES_PASSWORD` is not set.
- **Database init scripts only run on an empty data volume.** To recreate the database from
  scratch, run `docker compose down -v` and then `docker compose up -d`.
