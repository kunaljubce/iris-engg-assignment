"""Records passed between the pipeline stages.

ScrapedRecord is what an adapter produces: the source's values as given, unchecked.
NormalisedRecord is what the upsert writes: one field per signal_ingest column it fills.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

# Must match the CHECK on signal_ingest.geocode_status, so a typo fails here, not in the INSERT
GeocodeStatus = Literal["source_coordinates", "unresolved", "rejected_out_of_bounds"]


class ScrapedRecord(BaseModel):
    source: str  # 'sachsen_portal' | 'leipzig_stadtportal'
    source_record_id: str  # stable ID from the source
    city: str  # 'dresden' | 'leipzig'
    title: str
    detail_url: str
    location_text: str | None = None
    lon: float | str | None = None  # as given by the source; checked in the pipeline
    lat: float | str | None = None
    zeitraum: str | None = None  # raw "Zeitraum ..." text from the detail page
    thema: list[str] = Field(default_factory=list)  # filter input only (Leipzig)
    raw_data: dict[str, Any]  # all scraped fields as-is


class NormalisedRecord(BaseModel):
    source: str
    source_record_id: str
    city: str
    title: str
    detail_url: str
    location_text: str | None
    publication_date: date | None
    matched_keywords: list[str]
    # WGS84, inside the city's bounding box; the upsert transforms it to EPSG:25832.
    # Both None unless geocode_status is 'source_coordinates'
    lon: float | None
    lat: float | None
    geocode_status: GeocodeStatus
    geocode_reason: str | None
    quality_flags: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any]
