"""Saxon state participation portal (buergerbeteiligung.sachsen.de), Dresden and Leipzig.

Each listing page embeds `map.drawMarkers([...])`: a JSON array with one entry per result,
across all pages, with WGS84 coordinates. So one GET per city and status is enough; the
10-per-page cards are only used to check the marker count.
"""

import html
import json
import logging
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from harvester.adapters.base import SNIPPET_MAX_CHARS, ScrapeError, ScrapeResult
from harvester.adapters.fetch import FetchError, fetch
from harvester.config import SachsenSource, ScrapingConfig
from harvester.models import ScrapedRecord

logger = logging.getLogger(__name__)

SOURCE = "sachsen_portal"

# The page also calls drawMarkers(markerData); only the call with the literal array is read
MARKERS_CALL = "map.drawMarkers("


def listing_url(source: SachsenSource, city: str, status: str) -> str:
    query = urlencode({"format": source.format_filter, "status": status})
    return f"{source.base_url}/portal/{city}/beteiligung/themen?{query}"


def scrape(client: httpx.Client, source: SachsenSource, settings: ScrapingConfig) -> ScrapeResult:
    """Read every configured city and status. A failed listing is an error, not a crash."""
    result = ScrapeResult()
    for city in source.city_slugs:
        for status in source.status_filters:
            url = listing_url(source, city, status)
            try:
                page = fetch(client, url, settings)
            except FetchError as exc:
                result.errors.append(ScrapeError(url, exc.kind, exc.message))
                continue
            records, errors = parse_listing(page, url, city)
            result.records.extend(records)
            result.errors.extend(errors)
    return result


def parse_listing(
    page: str, page_url: str, city: str
) -> tuple[list[ScrapedRecord], list[ScrapeError]]:
    """Records from the marker JSON, checked against the count the page itself shows."""
    expected = parse_expected_count(page)
    markers = _find_markers(page)

    if markers is None:
        if expected == 0:
            logger.warning("no results on %s", page_url)
            return [], []
        # Cards but no readable markers: the page layout has changed
        return [], [
            ScrapeError(page_url, "markers_not_found", f"{expected} results shown, no marker JSON")
        ]

    records: list[ScrapedRecord] = []
    errors: list[ScrapeError] = []
    for entry in markers:
        record_or_error = _parse_marker(entry, page_url, city)
        if isinstance(record_or_error, ScrapeError):
            errors.append(record_or_error)
        else:
            records.append(record_or_error)

    if expected != len(markers):
        errors.append(
            ScrapeError(
                page_url,
                "count_mismatch",
                f"page shows {expected} results, marker JSON has {len(markers)}; markers used",
            )
        )
    return records, errors


def parse_expected_count(page: str) -> int:
    """'von N' from the paginator (more than 10 results), else the number of cards."""
    soup = BeautifulSoup(page, "html.parser")
    paginator = soup.select_one("span.ui-paginator-current")  # e.g. "1 - 10 von 52"
    if paginator is not None:
        _, _, total = paginator.get_text().partition("von")
        if total.strip().isdigit():
            return int(total)
    return len(soup.select("div.nbp-teaser"))


def _find_markers(page: str) -> list[Any] | None:
    start = page.find(MARKERS_CALL + "[")
    if start == -1:
        return None
    try:
        # raw_decode stops at the end of the array, whatever follows it
        markers, _ = json.JSONDecoder().raw_decode(page, start + len(MARKERS_CALL))
    except json.JSONDecodeError:
        return None
    return markers if isinstance(markers, list) else None


def _parse_marker(entry: Any, page_url: str, city: str) -> ScrapedRecord | ScrapeError:
    snippet = json.dumps(entry, ensure_ascii=False)[:SNIPPET_MAX_CHARS]
    if not isinstance(entry, dict):
        return ScrapeError(page_url, "missing_id", "marker is not an object", snippet)

    record_id = _text(entry.get("id"))
    if record_id is None:
        return ScrapeError(page_url, "missing_id", "marker has no id", snippet)
    title = _text(entry.get("kategorie"))
    if title is None:
        return ScrapeError(page_url, "missing_title", f"marker {record_id} has no title", snippet)

    # The marker's own href: the city in the path matters (a wrong one redirects to a listing)
    href = _text(entry.get("href")) or f"/portal/{city}/beteiligung/themen/{record_id}"
    return ScrapedRecord(
        source=SOURCE,
        source_record_id=record_id,
        city=city,
        title=html.unescape(title),  # the portal escapes quotes as &quot;
        detail_url=urljoin(page_url, href),
        location_text=_text(entry.get("adresse")),
        # As given; the pipeline checks them against the city's bounding box
        lon=_coordinate(entry.get("longitude")),
        lat=_coordinate(entry.get("latitude")),
        raw_data={"marker": entry, "listing_url": page_url},
    )


def _text(value: Any) -> str | None:
    text = str(value).strip() if isinstance(value, str | int) else ""
    return text or None


def _coordinate(value: Any) -> float | str | None:
    # Anything else (null, list, object) counts as missing; the raw value stays in raw_data
    return value if isinstance(value, float | int | str) and not isinstance(value, bool) else None


def fetch_detail(
    client: httpx.Client, record: ScrapedRecord, settings: ScrapingConfig
) -> ScrapedRecord:
    """The record with the detail page's "Zeitraum" added. Raises FetchError; the pipeline
    turns that into the detail_page_failed flag and keeps the listing data."""
    page = fetch(client, record.detail_url, settings)
    return record.model_copy(update={"zeitraum": parse_detail(page, record.source_record_id)})


def parse_detail(page: str, record_id: str) -> str | None:
    """The raw "Zeitraum DD.MM.YYYY bis DD.MM.YYYY" text, or None.

    Listing cards carry the same `li.nbp-statistik--laufzeit`, and a detail URL with the wrong
    city redirects to a listing (HTTP 200). So the Zeitraum is only read when the page is this
    record's own detail page, which its body class `nbp-beteiligung-{id}` shows.
    """
    soup = BeautifulSoup(page, "html.parser")
    body = soup.body
    if body is None or f"nbp-beteiligung-{record_id}" not in body.get_attribute_list("class"):
        return None
    item = soup.select_one("li.nbp-statistik--laufzeit")
    if item is None:
        return None
    return " ".join(item.get_text(" ").split()) or None
