## Adapter Details

The adapter uses one shared `httpx.Client` (user agent and timeout from config) and go through `fetch()` for every request (see **Resilience**). Parsing is split into pure functions (`str` in, data out) so the tests can call them on fixture text directly.

#### Saxon State Portal Adapter (`sachsen_portal.py`)

Runs once per city in `cities` and status in `status_filters`:

1. GET `{base_url}/portal/{city}/beteiligung/themen?format={format_filter}&status={status}`.
2. `parse_listing(html, page_url, city)`: find `map.drawMarkers([` (the page also calls `drawMarkers(markerData)`) and decode the array with `json.JSONDecoder().raw_decode`, which stops at the end of the array. For each entry:
   - `id` -> `source_record_id` (as a string)
   - `kategorie` -> `title` (`html.unescape`, since the portal escapes quotes as `&quot;`)
   - `href` -> `detail_url` (joined to the listing URL; the city in the path matters, Key Discovery 5). Missing `href`: built from the listing's city and the `id`
   - `latitude` / `longitude` -> kept as given; checked in the pipeline
   - `adresse` -> `location_text`
   - `raw_data` = `{"marker": <entry as given>, "listing_url": <page URL>}`
3. `parse_expected_count(html) -> int`: "von N" from `span.ui-paginator-current` when present (more than 10 results), else the number of `div.nbp-teaser` cards. `parse_listing` compares it with the marker count; a difference is a `count_mismatch` error in `ingest_run.errors`, and the markers are still used.
4. The pipeline filters on title. For each **match only**, GET the detail page and `parse_detail(html, record_id)` reads `li.nbp-statistik--laufzeit` ("Zeitraum DD.MM.YYYY bis DD.MM.YYYY"), **only if** the `<body>` has class `nbp-beteiligung-{id}`: listing cards carry the same `li`, so a redirect to a listing would otherwise yield another procedure's dates (found while testing on 2026-10-28). The raw text goes into `raw_data`; the start date becomes `publication_date` (see **Publication Dates**). A detail page that fails keeps the listing data and gets `detail_page_failed`.

**Empty result:** no `drawMarkers` call **and** no cards -> empty result plus a warning, no exception; the `ingest_run` records 0 scraped. A listing that has cards but no parseable marker JSON is an error (`markers_not_found`), not an empty result: it means the page changed.

**Malformed markers:** an entry without `id` or `kategorie` becomes a `ScrapeError` (counted in `records_rejected`); the others are still parsed.

#### ScrapedRecord Model

```python
from typing import Any

from pydantic import BaseModel


class ScrapedRecord(BaseModel):
    source: str  # 'sachsen_portal'
    source_record_id: str  # stable ID from the source
    city: str  # 'dresden' | 'leipzig'
    title: str
    detail_url: str
    location_text: str | None = None
    lon: float | str | None = None  # as given by the source; checked in the pipeline
    lat: float | str | None = None
    zeitraum: str | None = None  # raw "Zeitraum ..." text from the detail page
    raw_data: dict[str, Any]  # all scraped fields as-is
```

## Resilience

- **Retries with exponential backoff:** every request goes through one helper, `fetch()`, which retries up to `max_retries` attempts with `retry_backoff_factor` between them on:
  - `httpx.TransportError` (network failures, DNS, connection reset, timeouts)
  - `status >= 500`
  - `429`, honouring `Retry-After` when present

  Any other `4xx` is not retried. It is recorded as a page-level error (e.g. a detail page that 404s gets `quality_flags = ['detail_page_failed']` and keeps its listing data).
- **Redirects:** followed. A detail URL that ends up on a listing page (Key Discovery 5) is caught because that page's `<body>` lacks the record's `nbp-beteiligung-{id}` class, so `parse_detail` returns None and the row is flagged `missing_publication_date`.
- **Polite delay:** `request_delay_seconds` (default 2s) before every request.
- **User agent:** Neutral identifying string (`IRIS-SIG-08-Harvester/1.0 (bot)`), overridable via the `HARVESTER_USER_AGENT` env var so an operator can add a contact address without committing it to source.
- **Timeout:** `timeout_seconds` (default 30s) per request.
- **Empty response handling:** A listing with zero results gives an empty list and a logged warning. It does not raise an exception. The `ingest_run` row records the count.
- **Malformed records:** The adapter returns a `ScrapeResult(records, errors)`, not a bare list. The handling depends on how bad the record is:
  - **Unusable** (no stable ID or no title): becomes a `ScrapeError` with the raw snippet (the marker JSON entry) and the page URL. It is written to `ingest_run.errors` and counted in `records_rejected`.
  - **Usable but incomplete** (e.g. unparseable date, missing location, bad coordinates): stored, with the problem recorded in `quality_flags` / `geocode_status`.

  Nothing is silently dropped. The quality report surfaces both kinds.
- **Failed runs:** If the run fails entirely (e.g. the portal is down), its `ingest_run` records `status = 'failed'` and the CLI exits 1. "Fails entirely" means an exception anywhere in the run, or no records at all plus at least one error. Some pages failing while others work is a `completed` run with those errors.

---

## Publication Dates

The brief requires dates to be verified, not assumed. What was verified (Key Discovery 6):

- The state portal has no separate "published on" field. The detail page's "Zeitraum A bis B" is the period in which the plan is publicly "veröffentlicht" (BauGB § 3 (2)), stated in the detail text itself. `publication_date` = **A**, the first day it is publicly available.
- It is taken from the detail page, never from the listing card (cards exist for only 10 results per page) and never derived from the run time.
- The raw "Zeitraum ..." text is kept in `raw_data`, so the derivation is traceable per row.
- A detail page without a "Zeitraum" (or one that fails to load) leaves `publication_date` NULL, and `quality_flags` gets `missing_publication_date`.
- Dates that are present but unparseable get `unparseable_date`, with the raw text in `raw_data`.
