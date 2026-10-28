"""Shared builders for the db-marked tests."""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from harvester.models import NormalisedRecord


def make_run(conn: Connection, source: str = "sachsen_portal") -> UUID:
    run_id: UUID = conn.execute(
        text("INSERT INTO iris_staging.ingest_run (source) VALUES (:source) RETURNING id"),
        {"source": source},
    ).scalar_one()
    return run_id


def make_record(record_id: str = "1066719", **overrides: Any) -> NormalisedRecord:
    values: dict[str, Any] = {
        "source": "sachsen_portal",
        "source_record_id": record_id,
        "city": "dresden",
        "title": "Bebauungsplan Nr. 3017, Gewerbegebiet Flughafen",
        "detail_url": f"https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/{record_id}",
        "location_text": "Feldstraße",
        "publication_date": None,
        "matched_keywords": ["Gewerbegebiet", "Gewerbe"],
        "lon": 13.7416,
        "lat": 51.0519,
        "geocode_status": "source_coordinates",
        "geocode_reason": None,
        "quality_flags": [],
        "raw_data": {"marker": {"id": record_id}},
    }
    return NormalisedRecord(**{**values, **overrides})


def rows(conn: Connection) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT id, source_record_id, ingest_run_id, title, matched_keywords, created_at,"
                " ST_SRID(geometry) AS srid, ST_X(geometry) AS x, ST_Y(geometry) AS y,"
                " geocode_status, geocode_reason"
                " FROM iris_staging.signal_ingest ORDER BY source_record_id"
            )
        )
    )
