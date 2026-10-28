# IRIS-SIG-08 Saxony Citizen Participation Portal Harvester -- Implementation Plan

## Overview

Build a Python harvester that scrapes Bauleitplan (land-use zoning) procedures from the
Saxon state citizen participation portal, filters for industrial/commercial hearings, normalises
the data, and loads it into a PostGIS-backed `iris_staging.signal_ingest` table with
idempotent upserts and full auditability.

**Scope:** Dresden and Leipzig only; surrounding districts deferred.

**This is a sample solution for an exercise, not a production system.** The repo matters for
a few weeks and is demonstrated from a fresh database. Add something only if it serves the
brief's acceptance criteria or makes the demo work. Leave out production hardening for a
future the repo won't have (schema evolution over many migrations, multi-environment
deployment, scale), and name it under [What We Are NOT Doing](DESIGN.md#what-we-are-not-doing) so the omission is deliberate.

**Branch:** `main`

---

## Current State Analysis

Greenfield repository. What the live pages showed on 2026-10-27 is in
[`docs/DESIGN.md`](DESIGN.md#key-discoveries) (Key Discoveries, Portal Details).

---

## Desired End State

- `docker compose up` starts PostgreSQL 16 / PostGIS 3.4.
- `alembic upgrade head` creates `iris_staging` schema, `signal_ingest` + `ingest_run` tables, PostGIS extension.
- `harvester run` scrapes the state portal (per city and status), filters, upserts, and records each run in `ingest_run`.
- `pytest` passes, including idempotent re-run and duplicate detection, with no network access.
- `harvester report` prints the quality report from the database: the latest run with counts and errors, and the stored records by geocode status and quality flag.

### Verification

```bash
# Automated
uv run pytest --tb=short -q
uv run ruff check .
uv run mypy .

# Manual
docker compose up -d
uv run alembic upgrade head
uv run harvester run --dry-run   # prints every record with its filter decision, no DB write
uv run harvester run             # full ingest
uv run harvester report          # quality report
docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql
```

---

## What We Are NOT Doing

See [`docs/DESIGN.md`](DESIGN.md#what-we-are-not-doing).

## Phase 1: Project Skeleton and Database ✅

### Overview

Set up the Python project, Docker environment, config and the Alembic migration (plain SQL
DDL, no ORM models). This phase produces a working `docker compose up` +
`alembic upgrade head` that creates the full schema.

### Changes Required

#### 1. `pyproject.toml` ✅

See [`docs/DESIGN.md`](DESIGN.md#implementation-approach) (Implementation Approach).

#### 2. `docker-compose.yml` ✅

See `docker-compose.yml`; the reasons for its settings are in the choices below.

#### 3. `.env.example` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#configuration-and-secrets) (Configuration and Secrets).

#### 4. Alembic setup ✅

See [`migrations/README.md`](../migrations/README.md).

#### 5. `src/harvester/config.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#configuration-and-secrets) (Configuration and Secrets).

- **Changed after the Phase 2 captures:** `scraping.headless` and `page_timeout_ms` removed (Playwright-only), `timeout_seconds: float` added.
- `rows_per_page` is not in the config: the page size is a property of the portal, and the marker JSON lists every result anyway.

### Choices You May Be Asked About

- **Why PostGIS in Docker instead of a local install?** One `docker compose up` gives the exact versions the brief names (PostgreSQL 16 / PostGIS 3.4) on any machine.
- **Why `platform: linux/amd64`?** The official `postgis/postgis` image is amd64-only; on Apple Silicon it runs under emulation. Fine for <1000 rows, and stated so it isn't a surprise.
- **Why mount the single init SQL file, not a folder?** A folder over `/docker-entrypoint-initdb.d` hides the image's own `10_postgis.sh`, so `iris` would get no PostGIS.
- **Why a separate `iris_test` database?** The db tests truncate and rewrite tables; they must never touch the data a demo run produced.
- **Why no `DATABASE_URL` in `.env`?** It repeated the password and broke on `@ : / %` unless encoded by hand. `URL.create()` builds it from the `POSTGRES_*` parts compose already uses.
- **How does the password stay out of logs?** `SecretStr` masks `repr()`, `str(URL)` shows `***`, and the engine uses `hide_parameters=True`.
- **Why hand-written SQL migrations, no ORM / autogenerate?** There are no ORM models, so autogenerate has nothing to compare. The DDL in migration 001 is the single source of truth.
- **Why one `op.execute()` per statement?** An error points at the failing statement.
- **Why the Alembic version table in `iris_staging`?** A default `public.alembic_version` would collide with other Alembic projects in a shared database.
- **Why does `downgrade()` keep the `postgis` extension and the schema?** A migration only removes what it alone created; other schemas may use PostGIS, and the schema holds Alembic's own table.
- **Why was migration 001 edited instead of adding 002?** It was never deployed anywhere; the only database is the local demo one, rebuilt with `downgrade base && upgrade head`.
- **Why no secondary indexes?** At <1000 rows a sequential scan is faster; the only index is the one `ON CONFLICT` needs (`uq_source_record`).
- **Why `ck_geometry_matches_status`?** It makes the database itself refuse a point without `source_coordinates`, or that status without a point ("invalid coordinates are not silently accepted").
- **Why `extra="forbid"` on the config models?** A misspelt key (e.g. `keyowrds`) would otherwise be ignored, leaving the filter empty and the run "succeeding" with zero matches.
- **Why these config validators and no others?** Each one stops a mistake that would silently produce wrong data (blank keyword matches everything, swapped bounding box rejects every point, missing box fails only after the whole scrape).
- **Why sync, not async?** It's a sequential, deliberately throttled crawler; async adds complexity and gains nothing.

---

## Phase 2: Adapters and HTML Parsing ✅

### Overview

Implement `fetch()` and the state portal adapter (httpx + BeautifulSoup), producing `ScrapedRecord`
instances. Test with saved HTML fixtures (no live network).

### Changes Required

#### 1. `src/harvester/models.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#scrapedrecord-model) (ScrapedRecord Model).

#### 2. `src/harvester/adapters/base.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#adapter-result-adaptersbasepy) (Adapter Result).

#### 3. `src/harvester/adapters/fetch.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#fetch-adaptersfetchpy) (`fetch()`).

#### 4. `src/harvester/adapters/sachsen_portal.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#saxon-state-portal-adapter-sachsen_portalpy) (Saxon State Portal Adapter).

#### 5. Test fixtures ✅

See [`docs/TESTS.md`](TESTS.md#fixtures) (Fixtures).

#### 6. `tests/conftest.py` ✅

See [`docs/TESTS.md`](TESTS.md#testsconftestpy).

#### 7. `tests/test_sachsen_adapter.py` ✅

See [`docs/TESTS.md`](TESTS.md#teststest_sachsen_adapterpy) (`tests/test_sachsen_adapter.py`).

#### 8. `tests/test_fetch.py` ✅

See [`docs/TESTS.md`](TESTS.md).

### Choices You May Be Asked About

- **Why httpx and no browser (Playwright)?** The live checks on 2026-10-27 showed every page needed is a plain GET with the data in the HTML, including the state portal's map markers. A browser would add a heavy dependency and a slower, flakier test setup for nothing.
- **Why use the marker JSON instead of the listing cards?** `map.drawMarkers([...])` holds *all* results with coordinates in one response; cards show only 10 per page. No paging is needed, and the "von N" count / card count is checked against it (`count_mismatch`).
- **Why use the marker `href` for the detail URL?** A wrong city in the path redirects to the listing page instead of failing; the marker's own link is always right.
- **Why fetch detail pages only for filter matches?** Only the detail page has the "Zeitraum"; fetching it for non-matches would be wasted requests on a public portal.
- **Why only 2 fixture files?** One real page per markup the code parses. Edge cases (empty listing, malformed marker, count mismatch, HTTP errors) are small inline snippets in the tests. `tests/fixtures/SOURCES.md` records where each page came from and what the dropped pages showed.
- **Why `Field(default_factory=list)` and not `= []`?** Pydantic copies `[]` defaults, so it isn't a bug there, but the org rule forbids mutable defaults and `default_factory` is correct everywhere (dataclasses too).
- **Why is `geocode_status` a `Literal`?** It mirrors the migration's CHECK, so a typo fails in Python with a clear error before the INSERT.
- **Why are `lon`/`lat` `float | str | None` on `ScrapedRecord` but `float | None` on `NormalisedRecord`?** The scraped record keeps values as the source gives them; turning them into checked floats is the pipeline's job (Phase 3).
- **Why no validators for "point only with `source_coordinates`" or "at least one keyword"?** The database CHECKs already enforce both; repeating them in Python adds code to defend and nothing else.
- **Why are `ScrapeError` / `ScrapeResult` dataclasses, not pydantic?** They're built only by our own code, so there's nothing to validate. `dataclasses.asdict()` turns an error into JSON for `ingest_run.errors`.
- **Why is `ScrapeError` frozen?** It records something that happened; nothing should change it afterwards.
- **Why is `ScrapeError.kind` a plain `str`?** No database CHECK constrains it, unlike `geocode_status`; the allowed values are listed in a comment.
- **Why no `BaseAdapter` class?** There is one source, the one the brief names. An abstract base pays off when implementations are swapped or added; here it would only wrap one function. The runner calls `sachsen_portal.scrape()` directly.
- **Why does `scrape()` take the `httpx.Client` as a parameter?** So tests can pass a client backed by `MockTransport`. The adapter never creates its own client.
- **Why read the marker JSON with `raw_decode` instead of a regex?** A regex like `\[.*?\]\);` would stop early if a title ever contained `]);`. `raw_decode` parses exactly one JSON value and stops where it ends.
- **Why does `parse_detail` check the body class `nbp-beteiligung-{id}`?** Found in testing: listing cards carry the same Zeitraum element, so a detail URL redirected to a listing (HTTP 200) would have given the row another procedure's dates. Now it gets no date and is flagged `missing_publication_date`: wrong data is worse than missing data.
- **Why does the count check compare with the cards or "von N"?** The marker JSON is the data source; the number the page itself shows is an independent check that no results were lost. A mismatch is recorded, not fatal: the markers are still the most complete list.
- **Why is "cards but no markers" an error, but "no cards, no markers" not?** No cards means the portal has no results, which is normal. Cards without markers means the page layout changed and the adapter would silently return nothing.
- **Why keep coordinates as given and check them later?** The adapter only reads; deciding what is valid (bounding box, NaN) is one rule in the pipeline. Values of the wrong JSON type (null, list) count as missing, with the original kept in `raw_data`.
- **Why does `fetch_detail` let `FetchError` through?** A failed detail page isn't a scrape error: the record is still usable. The pipeline keeps it and adds `detail_page_failed`.
- **Why `source = "sachsen_portal"` as a module constant, not from the config `name`?** It is the identifier stored in the database's natural key; it must not change if someone renames the config entry.
- **Why `tests/__init__.py`?** It lets test modules import helpers from `tests.conftest` without mypy seeing the file under two module names.
- **Why return errors instead of raising?** One bad marker must not stop the run; bad entries are collected next to the good ones ("errors are visible").
- **Why `SNIPPET_MAX_CHARS = 2048`?** Enough to see what was wrong with an entry without bloating `ingest_run.errors`; one named constant instead of a bare 2048.
- **Why one `fetch()` function for every request?** Listings and detail pages need the same retry, 429 and delay rules; written once, tested once (`tests/test_fetch.py`).
- **Why no retry library (tenacity) or httpx's `retries=`?** httpx's built-in retries cover connection failures only, not 5xx or 429. tenacity would be a dependency to replace a short loop that can be explained line by line.
- **Which errors are retried?** Network errors and timeouts, 5xx and 429 can go away on their own. Any other 4xx (404, 403) won't, so it fails at once and is recorded.
- **What does `max_retries: 3` mean?** 3 retries after the first try, so at most 4 requests per URL, waiting 2, 4, 8 s between them: about 14 s worst case per URL.
- **Why honour only the seconds form of `Retry-After`?** The portal has never sent a 429; parsing the date form would be code for a case never seen. It falls back to the backoff.
- **Why does a retry wait only the backoff, not backoff plus polite delay?** The backoff is already at least as long; adding both makes the waits harder to reason about and test.
- **Why `follow_redirects=True`?** The state portal redirects a wrong-city detail URL to the listing page; following it lets `parse_detail` see the page isn't the record's own and flag the row, instead of failing on a 302.
- **Why `make_client()`?** One place sets the user agent, timeout and redirects. Tests build their client with it too (passing a `MockTransport`), so they run with production settings.
- **How do the tests avoid real sleeps?** They replace `time.sleep` in `fetch.py` with a list recorder: tests run instantly and can assert the exact waits (e.g. `Retry-After: 5` -> 5 s).

---

## Phase 3: Pipeline (Filter, Detail Dates, Geocode, Normalise) ✅

### Overview

Turn `ScrapedRecord`s into `NormalisedRecord`s: filter (dropping non-matches), fetch the
detail page of each match, parse the date, check the coordinates.

### Changes Required

#### 1. `src/harvester/pipeline/filter.py` ✅

See [`docs/DESIGN.md`](DESIGN.md#industrial-keyword-filter) (Industrial Keyword Filter).

#### 2. `src/harvester/pipeline/geocode.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#coordinates-and-bounding-box) (Coordinates and Bounding Box).

#### 3. `src/harvester/pipeline/normalise.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#pipeline) (Pipeline).

#### 4. Tests ✅

See [`docs/TESTS.md`](TESTS.md).

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why does the filter run before the detail page is fetched?** Only matches cost a second
  request: on the Dresden fixture 5 of 52 records, instead of all 52. (Step 1, built)
- **What does the filter miss?** Plans whose title says nothing industrial (Nr. 422
  Radefelder Allee West). Reading descriptions would catch them but adds false positives; see
  [What We Are NOT Doing](DESIGN.md#what-we-are-not-doing). (Step 1, checked on the fixtures)
- **Why do some rows list both "Gewerbegebiet" and "Gewerbe"?** Long keywords match inside words,
  so the general one hits too. Which rows are stored only depends on whether anything matched.
  (Step 1, built)
- **Why does `normalise` take the HTTP client?** The detail page is the only source of the
  date, and it's fetched for matches only (9 requests live instead of 83). (Step 3, built)
- **Why is a failed detail page a flag, not an error that drops the record?** The listing data
  (title, URL, point) is still valid; dropping the record would hide a real hearing. The flag
  and `raw_data.detail_error` make the gap visible. (Step 3, built)
- **Why match on the title only, not descriptions?** Descriptions mention
  neighbouring commercial areas and produce false positives.
- **Why substring matching for long keywords but whole tokens for GE/GI/GVZ?** German compounds
  ("Gewerbestandort") need substring hits; short codes as substrings would hit ordinary words
  ("gegen").
- **Why is a missing coordinate `unresolved` but a bad one `rejected`?** Missing means the
  source gave no location, which is normal. A value that is there but not a number or outside
  the box means the source data is wrong, and that should stand out in the report. (Step 2,
  built)
- **Why not fix swapped lat/lon automatically?** Guessing would store a point nobody can vouch
  for; the brief says invalid coordinates must not be silently accepted. The raw values stay
  in `raw_data`. (Step 2, built)
- **Why no geocoding of addresses?** The brief asks for points "where the source provides
  sufficient information", and the markers carry coordinates. An external geocoder adds a
  dependency and its own errors; see [What We Are NOT Doing](DESIGN.md#what-we-are-not-doing). (Step 2)
- **Why check the bounding box in Python, before the database?** A bad point is never written;
  the row is stored with `geometry = NULL`, `rejected_out_of_bounds` and the reason.
- **Why is the check written as "inside" (`min <= v <= max`)?** NaN fails every comparison, so
  it's rejected too.
- **Why is `publication_date` the start of "Zeitraum"?** That's the first day the plan is
  publicly available (BauGB § 3 (2)); the raw text stays in `raw_data`.

---

## Phase 4: Upsert, CLI, and Integration ✅

### Overview

Wire the adapter to the pipeline and the database. Implement the CLI entry point, upsert logic,
and integration tests including idempotent re-run and duplicate detection.

### Changes Required

#### 1. `src/harvester/db/engine.py` + db test fixtures ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#database-runner-and-cli) (Database, Runner and CLI). The db test fixtures: [`docs/TESTS.md`](TESTS.md#test-infrastructure).

#### 2. `src/harvester/db/upsert.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#database-runner-and-cli) (Database, Runner and CLI). The upsert itself: [Upsert Logic](DESIGN.md#upsert-logic).

#### 3. `src/harvester/runner.py` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#database-runner-and-cli) (Database, Runner and CLI).

#### 4. `src/harvester/cli.py` (+ `__main__.py`) ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#database-runner-and-cli) (Database, Runner and CLI).

#### 5. Tests ✅

See [`docs/TESTS.md`](TESTS.md).

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **What does CI check, and when?** `.github/workflows/ci.yml` runs on every pull request to
  main and every push to main:
  ruff, mypy, the migrations (up, down, up) and the whole pytest suite, including the db tests
  against a `postgis/postgis:16-3.4` service container. The README badge shows the latest
  result on main (green "passing" / red "failing"). (Built before Phase 4)
- **Why trust auth in CI rather than a password secret?** The database is a throwaway container
  on the runner, so there is no credential to store or leak; `POSTGRES_PASSWORD` is set only
  because `DatabaseSettings` requires a value. (Built before Phase 4)
- **What does one `ingest_run` row cover?** One invocation. The scrape reads both cities in
  one go, so `city` is NULL unless `--city` narrows it; the report breaks rows down by
  city from `signal_ingest`. (Step 3, built)
- **Why is the run row committed before the scrape?** So a run that crashes or hangs is still
  on record (`running` / `failed`), not lost with a rolled-back transaction. (Step 3, built)
- **Why do the records and the run's `completed` status commit together?** A run is either
  fully written with its counts, or not at all and marked `failed`; never half-written. A test
  crashes after the upsert and checks no rows remain. (Step 3, built)
- **What happens when the portal is down?** The run is `failed` with the error, and the CLI
  exits 1 so a scheduler notices. (Step 3, built)
- **Why a separate `runner.py` instead of logic in the CLI?** The integration tests call the
  same `run_source` the CLI calls, without click; the CLI only parses options and prints.
  (Step 3, built)
- **Why do the db tests truncate instead of rolling back?** `run_source` commits twice on
  purpose (see above), which an outer test transaction can't contain. (Step 1, built)
- **Why a `source` column with a single source?** It is half of the natural key the brief
  asks for ("preserve source identifiers"), and it makes every row say where it came from.
  `test_same_id_from_two_sources_is_two_rows` checks the key is the pair, not the ID alone.
- **How is the run idempotent?** `UNIQUE (source, source_record_id)` plus `ON CONFLICT DO
  UPDATE`; a re-run can't add rows.
- **Why no change detection (hash column)?** Nothing in the brief consumes "what changed"; an
  existing row is simply rewritten with the latest values.
- **How are inserted vs updated counted?** `RETURNING (xmax = 0)`: true only for a freshly
  inserted row. Covered by the re-run tests. (Step 2, built)
- **Why de-duplicate the batch in Python first?** Otherwise one row would count as 1 inserted
  + 1 updated, and the repeat itself would go unseen; each dropped duplicate is logged as a
  `duplicate_in_batch` error. (Step 2, built)
- **Why the EPSG:25832 transform in SQL?** `ST_Transform` in the upsert keeps pyproj out of the
  dependencies; a test checks a control point computed independently with pyproj. (Step 2,
  built)
- **Why are rows never deleted?** A plan missing from one run may just be a portal hiccup;
  staging keeps what was seen, with `updated_at` as "last seen".

---

## Phase 5: Reporting, Validation, and Deliverables

### Overview

Build the quality report, validation SQL, sample output, and README. This phase
completes all deliverables.

### Changes Required

#### 1. `src/harvester/report.py` + `harvester report` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#quality-report) (Quality Report). The live output is in the README.

Built differently from the first draft of this plan:
- One block per **source**, not per run and city ("sachsen_portal (dresden)"): since
  Phase 4 a run covers all cities, so the per-city view comes from `signal_ingest`.
- No "8 AKTUELLE + 52 BEENDETE" split of the scraped count: it isn't stored, and adding a
  column only for the report isn't worth it.
- `report.py` next to `runner.py` instead of a `reporting/` package holding one module.
- No per-record list and no keyword ranking: the brief asks for a concise report, and
  neither serves an acceptance criterion.
- Tables instead of labelled blocks per run; no run ID, finish time, filtered-out count or
  match percentage (scraped and industrial sit side by side; the rest is in `ingest_run`).

#### 2. `sql/validation_queries.sql` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#validation-queries) (Validation Queries).

Built differently from the first draft of this plan:
- Check 6 reads each city's box from the `config_snapshot` of the run that wrote the row,
  instead of one hardcoded Saxony envelope: it checks against the config actually used,
  with no second copy of the boxes to keep in sync.
- Check 9 compares with the latest run covering the row's city (`r.city IS NULL OR
  r.city = s.city`); see the choice below.
- Added check 8 (traceability, which also shows the filter evidence). Dropped the keyword
  distribution, which serves no acceptance criterion, and the duplicate-key and
  point-vs-status checks: `uq_source_record` and `ck_geometry_matches_status` make both
  impossible, so the checks could only ever return 0 rows. Idempotency shows in the
  re-run counts (check 1: 0 inserted).
- The README's inline queries moved here; the README lists the checks with the expected
  live results.

#### 3. `sample_output/signal_ingest_sample.csv` ✅

See [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md#sample-output) (Sample Output).

Built differently from the first draft of this plan:
- No GeoJSON: the brief asks for sample output, and a second format of the same rows
  isn't needed; the points are in the CSV as coordinates.
- No `sql/export_samples.sql`: one `COPY` command in the README does the same.

#### 4. `README.md` ✅

Sections, each tied to the brief's "README explaining data source, mapping assumptions,
how to run, and known limitations" or to an acceptance criterion:

| Section | Serves |
|---|---|
| Intro + "How it works" (one table: step, code, what it does) | maintainable |
| Data source: the state portal, access, selection, pagination, counts | data source; "access verified rather than assumed" |
| Mapping assumptions: column table, filter, Zeitraum date, coordinates, flags, finished hearings | mapping assumptions |
| Setup, Run the harvester, Quality report, validation checks, Sample output | how to run; demonstrable |
| Configuration: `settings.yml`, `.env`, adding a city | configurable |
| Tests: fixtures, retries, the db tests incl. re-run / duplicates, CI | automated tests incl. re-run/duplicate behaviour |
| Known limitations | known limitations |

Known limitations listed: only the two cities, the portal lists only what a city publishes
there (no open industrial Leipzig hearing on 2026-10-28), title-only filter (Nr. 422
Radefelder Allee West), EPSG:25832 outside its zone, dependence on the portal's markup
(`markers_not_found`, `count_mismatch`), rows never deleted.

Built differently from the first draft of this plan:
- No separate "Quick start", "Architecture overview" or "Adding new cities" sections:
  "Run the harvester", the "How it works" table and one paragraph in Configuration cover
  them.
- No test count in the README; it would go stale with every test added.

#### 5. Tests ✅

See [`docs/TESTS.md`](TESTS.md).

### Choices You May Be Asked About

Decided in this plan; confirm or update each one as the step is built.

- **Why EPSG:25832 although both cities lie east of 12° E?** The brief requires it; it's usable
  there with growing scale distortion, documented in the README.
- **Why does the report show the latest run, but all stored records?** The run
  block answers "did the last harvest work?"; the records block answers "what does
  `signal_ingest` hold?", which is what a consumer reads. Rows from older runs (e.g. a plan
  that has since left the portal) are still in the table, so they belong in that count.
- **Why only aggregates, no per-record list or keyword ranking?** The brief asks for a
  concise error/quality report; each section maps to "errors and missing fields are
  visible; invalid coordinates are not silently accepted". Inspecting rows (e.g. the
  filter evidence, to check false positives) is a validation query, and the evidence is
  stored per record in `raw_data->'filter_evidence'`.
- **Why errors by kind with one example, not the error list?** An outage gives one error
  per page; the count by kind says what happened, and the full list stays in
  `ingest_run.errors`.
- **Why read from the database instead of the run's in-memory summary?** The report then
  works at any time, after any run or a crash, and checks what was actually committed.
- **Why only a CSV sample, made by one `COPY` command?** The brief asks for sample output;
  one file with every column (points as coordinates) covers it. A GeoJSON copy or an
  export command would be extra code and files with nothing new to show.
- **Why does the bounding-box check read the boxes from `config_snapshot`?** It checks
  each point against the config the run actually used. A hardcoded box in SQL would be a
  second copy of `settings.yml` that can drift, and a changed box would wrongly flag
  older rows.
- **Why no duplicate-key check?** `uq_source_record` makes duplicates impossible, so the
  check could never fail; the re-run counts (0 inserted) and the idempotency tests show it.
- **Why are the validation checks SQL and not tests?** They check the real data after a
  live run, which the fixture tests can't see; anyone can run them with `psql`.
- **Why does the stale-rows query match the run's city?** A `--city dresden` run doesn't
  touch Leipzig rows; without the city condition they would all look stale.
- **Why keep finished (`BEENDETE`) hearings?** Each portal entry is a hearing record with
  its own Zeitraum, open or over. Live 2026-10-28, 7 of the 9 rows are finished hearings. Open vs finished shows in `raw_data` (listing URL status,
  Zeitraum end).
- **Why read "around Dresden and Leipzig" as the two cities?** The brief names the state
  portal, whose Dresden and Leipzig listings are the cities' own. Surrounding
  municipalities are a `cities` entry each (README limitation), not a code change.
- **Why does the live data have no flags or unresolved rows?** On 2026-10-28 all 9 records
  had coordinates in the box and a Zeitraum. The flagged, unresolved, rejected and failed
  cases are shown by the tests (marker without coordinates, out-of-bounds marker, detail
  page 404, unreachable portal), including the quality report's.

---

## Migration Notes

- First run: `alembic upgrade head` creates everything from scratch.
- Subsequent schema changes: add new Alembic migration files.
- No existing data to migrate (greenfield).

---

## Nice to Have

Not required for acceptance. Pick up only once Phases 1-5 are done.

- **One-command setup via `Makefile` or `justfile`.** The README setup steps stay the
  source of truth; the task runner only wraps them. Suggested targets:
  - `setup`: `uv sync`
  - `db-up` / `db-reset`: `docker compose up -d` / `docker compose down -v && docker compose up -d`
  - `migrate`: `uv run alembic upgrade head`
  - `test`, `lint` (`ruff check .` + `mypy`), `run` (`uv run harvester run`)

  `make` ships with macOS and most Linux systems; `just` reads better but is an extra
  install. Either way, don't hide steps the README doesn't also list.
- **Scripted fixture capture** (`scripts/capture_fixtures.py`). Only worth it if fixtures
  have to be refreshed regularly; they are captured once, by hand (see [Fixtures](TESTS.md#fixtures)).
- **Stricter config validation.** Parked because each check guards against an unlikely
  mistake rather than an acceptance criterion: `https://` pattern on `base_url`, value ranges
  (delay >= 0, `retry_backoff_factor` >= 1, timeout > 0, lat/lon within -90..90 / -180..180),
  and non-empty `sources` / `cities` / `status_filters`.

---

## References

- Saxon state portal: `https://buergerbeteiligung.sachsen.de/portal/{city}/beteiligung/themen`
- BauGB (Federal Building Code): procedure phases SS 2-10
- BauNVO (Building Use Ordinance): zone type definitions (GE, GI, SO, etc.)
- EPSG:25832: ETRS89 / UTM zone 32N
- EPSG:25833: ETRS89 / UTM zone 33N (Dresden's native zone)
