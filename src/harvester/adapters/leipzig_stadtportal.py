"""Leipzig city plan register (www.leipzig.de), plans "im Verfahren" only.

One table row per plan: title link, status, district(s), Thema. The source gives no
coordinates and no dates, and the detail pages add nothing, so they are not fetched.
"""

import logging
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from harvester.adapters.base import SNIPPET_MAX_CHARS, ScrapeError, ScrapeResult
from harvester.adapters.fetch import FetchError, fetch
from harvester.config import LeipzigStadtSource, ScrapingConfig
from harvester.models import ScrapedRecord

logger = logging.getLogger(__name__)

SOURCE = "leipzig_stadtportal"
CITY = "leipzig"

# The status label that status_filter_id 252 selects. Checked per row: if the portal ever
# ignored the filter, plans already in force would otherwise be stored as hearings
STATUS_IM_VERFAHREN = "im Verfahren"

# Columns as in the table header: image, Titel, Status, Stadtbezirk/ Ortsteil, Thema
STATUS_COL, DISTRICT_COL, THEMA_COL = 2, 3, 4


def listing_url(source: LeipzigStadtSource, page: int = 1) -> str:
    params: dict[str, int] = {"tx_lepurpose[filter][89][]": source.status_filter_id}
    if page > 1:
        params["tx_lepurpose[filter][page]"] = page
    return f"{source.base_url}{source.listing_path}?{urlencode(params)}"


def scrape(
    client: httpx.Client, source: LeipzigStadtSource, settings: ScrapingConfig
) -> ScrapeResult:
    """Read the filtered register, following the next-page button until there is none."""
    result = ScrapeResult()
    page = 1
    while True:
        url = listing_url(source, page)
        try:
            html = fetch(client, url, settings)
        except FetchError as exc:
            result.errors.append(ScrapeError(url, exc.kind, exc.message))
            break
        records, errors = parse_rows(html, url)
        result.records.extend(records)
        result.errors.extend(errors)

        next_page = parse_next_page(html)
        # Only move forward, so a button pointing back can't loop forever
        if next_page is None or next_page <= page:
            break
        page = next_page

    if not result.records and not result.errors:
        logger.warning("no plans found on %s", listing_url(source))
    return result


def parse_rows(html: str, page_url: str) -> tuple[list[ScrapedRecord], list[ScrapeError]]:
    records: list[ScrapedRecord] = []
    errors: list[ScrapeError] = []
    for row in BeautifulSoup(html, "html.parser").select('tr[id^="project-"]'):
        record_or_error = _parse_row(row, page_url)
        if isinstance(record_or_error, ScrapeError):
            errors.append(record_or_error)
        else:
            records.append(record_or_error)
    return records, errors


def _parse_row(row: Tag, page_url: str) -> ScrapedRecord | ScrapeError:
    snippet = str(row)[:SNIPPET_MAX_CHARS]
    record_id = str(row.get("id", "")).removeprefix("project-").strip()
    if not record_id:
        return ScrapeError(page_url, "missing_id", "row has no project id", snippet)

    link = row.select_one("a.filterlist-detailpage")
    # The link's second span is the "arrow_forward" icon text
    title_span = link.select_one("span:not(.icon)") if link else None
    title = title_span.get_text(" ", strip=True) if title_span else ""
    href = str(link.get("href", "")) if link else ""
    if not title or not href:
        return ScrapeError(page_url, "missing_title", f"row {record_id} has no title link", snippet)

    cells = row.find_all("td", recursive=False)
    if len(cells) <= THEMA_COL:
        return ScrapeError(
            page_url, "unexpected_row_layout", f"row {record_id}: {len(cells)} cells", snippet
        )
    status, districts, thema = (_items(cells[i]) for i in (STATUS_COL, DISTRICT_COL, THEMA_COL))

    if status != [STATUS_IM_VERFAHREN]:
        return ScrapeError(
            page_url, "unexpected_status", f"row {record_id} has status {status}", snippet
        )

    return ScrapedRecord(
        source=SOURCE,
        source_record_id=record_id,
        city=CITY,
        title=title,
        detail_url=urljoin(page_url, href),
        location_text="; ".join(districts) or None,
        thema=thema,
        raw_data={
            "status": status,
            "districts": districts,
            "thema": thema,
            "listing_url": page_url,
        },
    )


def _items(cell: Tag) -> list[str]:
    return [text for li in cell.select("ul > li") if (text := li.get_text(" ", strip=True))]


def parse_next_page(html: str) -> int | None:
    """Page number on the "load more" button, or None on the last page."""
    button = BeautifulSoup(html, "html.parser").select_one(".start-ajax-pagination[data-next]")
    value = str(button["data-next"]).strip() if button else ""
    return int(value) if value.isdigit() else None
