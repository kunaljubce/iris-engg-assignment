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
    - `test_upsert.py`: a re-run inserts nothing and keeps each row's `id` and `created_at`; a key repeated in one batch is written once and reported.
    - `test_pipeline_integration.py`: the whole run from fixtures to the database; running it twice leaves the same rows; an unreachable source gives a `failed` run; a crash after the upsert leaves no records.
    - `test_bounding_box.py`: points outside the box are stored without geometry; the EPSG:25832 transform matches pyproj within 1 m.
    - `test_quality_report.py`: the report's tables. 3 db tests:
      - the latest run per source is shown (not an older one) with its UTC start and counts; stored records by city with point and geocode counts, and flag counts
      - a failed run shows its failure as `run_failed` and its errors counted by kind, with an example message
      - an empty database prints every section with "no runs yet" / "none" instead of failing
- `uv run pytest` runs all tests. The scrapers are tested against real pages through httpx's mock transport, so no test touches the network. Edge cases (empty listing, malformed markers, HTTP errors, count mismatch) are small HTML snippets inside the tests. `test_fetch.py` covers the retries: 5xx, 429 with `Retry-After` and timeouts are retried, 404 is not.
- CI (`.github/workflows/ci.yml`) runs ruff, mypy, the migrations up / down / up and all tests against a PostGIS 16-3.4 service on every pull request to `main` and every push to `main`. The badge at the top of the README shows the result on `main`; it only renders while the repository is public.


#### `tests/conftest.py`

- `tests/__init__.py` makes `tests` a package, so test modules import `load_fixture` and `MockClient` from `tests.conftest` and mypy sees one module, not two
- `load_fixture(name) -> str`
- `mock_client(routes: dict[str, str | int])`: `make_client()` with an `httpx.MockTransport` that serves fixture text (or a status code) per URL and **fails the test on any unrouted URL**, so tests can never reach the live portal. No extra dependency (MockTransport is part of httpx).
- `scraping_config` fixture: no polite delay and no retries, so adapter tests run instantly.

#### Fixtures

**One real page per markup the code parses**; every edge case is a minimal HTML snippet inside its test, so the test shows exactly what is wrong with the input. Captured 2026-10-27, **once, by hand** with `curl` (harvester user agent, 2 s apart), kept whole; provenance in `tests/fixtures/SOURCES.md`, which covers "source access ... must be verified". It also lists the pages checked but not kept (other cities and statuses use the same markup). The tests' reproducibility comes from the committed files, not from how they were downloaded.

- `sachsen_dresden_listing_beendet.html`: 52 markers (5 industrial, titles with `&quot;`), 10 cards, "von 52"
- `sachsen_dresden_detail.html`: "Zeitraum 12.10.2026 bis 13.11.2026"

Inline snippets: empty listing, cards without marker JSON, marker without `id`, swapped / empty / non-numeric coordinates, "von 52" against 51 markers, and HTTP errors via `MockTransport`.

#### `tests/test_sachsen_adapter.py`

```python
def test_parses_markers():
    """id, title, city-correct detail_url, lon/lat, address, raw_data from the fixture."""

def test_listing_has_all_results():
    """52 markers although only 10 cards are rendered; matches 'von 52'."""

def test_unescapes_title():
    """Snippet: '&quot;Am Werk&quot;' -> '"Am Werk"'."""

def test_count_mismatch_logged():
    """Snippet: 'von 3' but 2 markers -> count_mismatch error, markers still returned."""

def test_parses_zeitraum_from_detail():
    """'Zeitraum 12.10.2026 bis 13.11.2026' from the detail fixture."""

def test_redirect_to_listing_gives_no_zeitraum():
    """The listing fixture has Zeitraum items in its cards; they must not be taken."""

def test_other_records_detail_page_gives_no_zeitraum():
    """Detail page of 1069133 read for record 1066719 -> None."""

def test_handles_empty_listing():
    """Snippet: no markers, no cards -> no records, no errors, no exception."""

def test_cards_without_markers_is_an_error():
    """Snippet: cards present but no marker JSON -> markers_not_found error."""

def test_handles_malformed_markers():
    """Snippet: marker without id, marker with blank title -> ScrapeErrors with snippet;
    the others still parsed."""

def test_scrape_reads_every_city_and_status():
    """mock_client: one listing served, one 404 -> its records plus one http_4xx error."""

def test_fetch_detail_adds_zeitraum(): ...
def test_fetch_detail_failure_raises(): ...
```
