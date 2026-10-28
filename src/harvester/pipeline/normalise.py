"""ScrapedRecords to NormalisedRecords: filter, detail page, date, coordinates, flags.

Per record: filter on the listing data -> (state portal, match only) fetch the detail page
-> publication date from its "Zeitraum" -> coordinate check. Non-matches are dropped and
counted. Problems never drop a match; they become quality flags or a geocode reason.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date

import httpx

from harvester.adapters import leipzig_stadtportal, sachsen_portal
from harvester.adapters.fetch import FetchError
from harvester.config import HarvesterConfig
from harvester.models import NormalisedRecord, ScrapedRecord
from harvester.pipeline.filter import IndustrialFilter
from harvester.pipeline.geocode import GeocodeResult, check_coordinates

logger = logging.getLogger(__name__)

# "Zeitraum 12.10.2026 bis 13.11.2026": the first date is the start of the public display
DATE_PATTERN = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


@dataclass
class NormaliseResult:
    records: list[NormalisedRecord] = field(default_factory=list)
    filtered_out: int = 0


def normalise(
    scraped: list[ScrapedRecord], client: httpx.Client, config: HarvesterConfig
) -> NormaliseResult:
    """The industrial matches, normalised. Takes the client because only matches get their
    detail page fetched."""
    industrial_filter = IndustrialFilter(config.industrial_filter)
    result = NormaliseResult()

    for record in scraped:
        match = industrial_filter.check(record.title, record.thema)
        if not match.is_industrial:
            result.filtered_out += 1
            continue

        flags: list[str] = []
        raw_data = {**record.raw_data, "filter_evidence": match.evidence}

        if record.source == sachsen_portal.SOURCE:
            try:
                record = sachsen_portal.fetch_detail(client, record, config.scraping)
            except FetchError as exc:
                logger.warning("detail page failed for %s: %s", record.detail_url, exc.message)
                flags.append("detail_page_failed")
                raw_data["detail_error"] = exc.message
            raw_data["zeitraum"] = record.zeitraum

        publication_date, date_flag = parse_publication_date(record.zeitraum)
        if date_flag:
            flags.append(date_flag)

        geocode = _geocode(record, config)
        if geocode.status != "source_coordinates" and not record.location_text:
            flags.append("missing_location")

        result.records.append(
            NormalisedRecord(
                source=record.source,
                source_record_id=record.source_record_id,
                city=record.city,
                title=record.title,
                detail_url=record.detail_url,
                location_text=record.location_text,
                publication_date=publication_date,
                matched_keywords=match.matched_keywords,
                lon=geocode.lon,
                lat=geocode.lat,
                geocode_status=geocode.status,
                geocode_reason=geocode.reason,
                quality_flags=flags,
                raw_data=raw_data,
            )
        )
    return result


def parse_publication_date(zeitraum: str | None) -> tuple[date | None, str | None]:
    """(date, None), or (None, flag). No Zeitraum is missing; one without a valid date is
    unparseable. The raw text stays in raw_data either way."""
    if not zeitraum:
        return None, "missing_publication_date"
    found = DATE_PATTERN.search(zeitraum)
    if found is None:
        return None, "unparseable_date"
    day, month, year = (int(part) for part in found.groups())
    try:
        return date(year, month, day), None
    except ValueError:  # e.g. 31.02.2026
        return None, "unparseable_date"


def _geocode(record: ScrapedRecord, config: HarvesterConfig) -> GeocodeResult:
    if record.source == leipzig_stadtportal.SOURCE:
        # The city portal never gives coordinates; a district is an area, not a point
        reason = (
            f"source gives district only: {record.location_text}"
            if record.location_text
            else "source gives no location"
        )
        return GeocodeResult(None, None, "unresolved", reason)
    return check_coordinates(
        record.lon, record.lat, record.city, config.bounding_boxes[record.city]
    )
