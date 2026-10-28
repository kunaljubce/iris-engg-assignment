## Alembic setup

- `alembic.ini` -- points to `migrations/` directory. It holds **no** `sqlalchemy.url`.
- `migrations/env.py` builds its engine from `DatabaseSettings().url` and passes the connection to Alembic. Don't copy the URL into the ini with `set_main_option()`: it needs the plain-text password, and configparser treats `%` in it as interpolation.
- **Offline mode** (`alembic upgrade head --sql`) configures only `dialect_name="postgresql"`, so the SQL script can be produced without a `.env` or a running database.
- **Version table in our schema:** `version_table_schema="iris_staging"`. A default `public.alembic_version` would collide with any other Alembic-managed project in a shared database. Alembic creates its table before migration 001 runs, so `env.py` runs `CREATE SCHEMA IF NOT EXISTS iris_staging` first (committed separately online; emitted at the top of the script offline).
- `alembic.ini` post-write hooks run `ruff check --fix` and `ruff format` on every generated revision. Generate with `--rev-id 001` etc. so file names follow `001_<slug>.py`.
- `target_metadata = None`: there are no ORM models, so no autogenerate (and no GeoAlchemy2 helpers or schema filters that autogenerate would need).
- `migrations/versions/001_create_schema_and_tables.py`, created with `alembic revision --rev-id 001 -m "create schema and tables"` and written **by hand as plain SQL**. This DDL is the single source of truth for the schema.
  - **One `op.execute()` per statement**, not one string with several `;`-separated statements, so an error points at the right statement. `'{}'::text[]`-style casts are fine: `text()` treats `:name` as a parameter but recognises `::`.
  - `CREATE EXTENSION IF NOT EXISTS postgis`
  - `CREATE SCHEMA IF NOT EXISTS iris_staging` (already created by `env.py`; kept so the migration's own SQL is complete when read on its own)
  - Create `iris_staging.ingest_run`, then `iris_staging.signal_ingest` (it references `ingest_run`), with all columns and constraints. No `CREATE INDEX`: at <1000 rows a sequential scan is faster, and the only index needed is the one behind `uq_source_record`, which `ON CONFLICT` requires
  - `downgrade()` drops **only the two tables**. It keeps the `postgis` extension, which other schemas in a shared database may depend on, and the `iris_staging` schema, which holds Alembic's version table and may hold other pipelines' tables. Same rule for both: a migration only removes what it alone owns.