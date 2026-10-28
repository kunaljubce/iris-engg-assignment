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
- **HTTP:** no browser. Parsing tests call the pure `parse_*` functions on fixture text; `scrape()` and pipeline tests use the `mock_client` from `conftest.py` (httpx `MockTransport`), which **fails on any URL not mapped to a fixture**, so tests can never reach the live portal.
- **Database:** the PostGIS `iris_test` database from `docker compose`, at `DatabaseSettings().test_url`. There is **no SQLite/SpatiaLite fallback**: the upsert depends on PostgreSQL-only features (`ON CONFLICT`, `xmax`, `ST_Transform`, `TEXT[]`), so a different engine would test different behaviour.
  - A session-scoped fixture runs `alembic upgrade head` against `iris_test`.
  - Each test starts by emptying both tables (`TRUNCATE`). Not a rolled-back transaction: `run_source` commits the run row and the upsert separately on purpose, so a test can't hold them in one outer transaction.
  - Tests marked `db` are **skipped with an explicit reason** if the database settings can't be loaded (no `POSTGRES_PASSWORD` in the environment or `.env`). If they load but the database is unreachable, the tests **fail**: configured-but-broken is an error, not a skip. The non-DB suite (parsing, filter, normalise, geocode, fetch) runs with no Docker.

#### `tests/test_quality_report.py`

3 db tests:
- the latest run per source is shown (not an older one) with its UTC start and counts; stored records by city with point and geocode counts, and flag counts
- a failed run shows its failure as `run_failed` and its errors counted by kind, with an example message
- an empty database prints every section with "no runs yet" / "none" instead of failing

Rows are compared with whitespace collapsed, so the tests don't depend on column widths.

The tests set `started_at` explicitly: `NOW()` is the transaction start, so every run made in one test transaction would otherwise have the same time.

#### `tests/conftest.py`

- `tests/__init__.py` makes `tests` a package, so test modules import `load_fixture` and `MockClient` from `tests.conftest` and mypy sees one module, not two
- `load_fixture(name) -> str`
- `mock_client(routes: dict[str, str | int])`: `make_client()` with an `httpx.MockTransport` that serves fixture text (or a status code) per URL and **fails the test on any unrouted URL**, so tests can never reach the live portal. No extra dependency (MockTransport is part of httpx).
- `scraping_config` fixture: no polite delay and no retries, so adapter tests run instantly.