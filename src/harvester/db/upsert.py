"""Write NormalisedRecords to signal_ingest, idempotently.

The natural key (source, source_record_id) plus ON CONFLICT DO UPDATE means a re-run can't
add rows: an existing row is rewritten with the latest values. Rows are never deleted.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from psycopg.types.json import Jsonb
from sqlalchemy import Connection, text

from harvester.adapters.base import ScrapeError
from harvester.models import NormalisedRecord

logger = logging.getLogger(__name__)

UPSERT = text(
    """
    INSERT INTO iris_staging.signal_ingest (
        source, source_record_id, ingest_run_id,
        city, title, detail_url, location_text,
        publication_date, matched_keywords,
        geometry, geocode_status, geocode_reason, quality_flags, raw_data
    )
    VALUES (
        :source, :source_record_id, :ingest_run_id,
        :city, :title, :detail_url, :location_text,
        :publication_date, :matched_keywords,
        -- WGS84 -> EPSG:25832. NULL lon/lat (unresolved or rejected) gives NULL geometry;
        -- the bounding box was already checked in Python
        ST_Transform(
            ST_SetSRID(ST_MakePoint(CAST(:lon AS float8), CAST(:lat AS float8)), 4326), 25832
        ),
        :geocode_status, :geocode_reason, :quality_flags, :raw_data
    )
    ON CONFLICT ON CONSTRAINT uq_source_record DO UPDATE SET
        ingest_run_id    = EXCLUDED.ingest_run_id,
        city             = EXCLUDED.city,
        title            = EXCLUDED.title,
        detail_url       = EXCLUDED.detail_url,
        location_text    = EXCLUDED.location_text,
        publication_date = EXCLUDED.publication_date,
        matched_keywords = EXCLUDED.matched_keywords,
        geometry         = EXCLUDED.geometry,
        geocode_status   = EXCLUDED.geocode_status,
        geocode_reason   = EXCLUDED.geocode_reason,
        quality_flags    = EXCLUDED.quality_flags,
        raw_data         = EXCLUDED.raw_data,
        updated_at       = NOW()
    -- xmax is 0 only for a row this statement inserted, not one it updated
    RETURNING (xmax = 0) AS inserted
    """
)


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    # One per record dropped because its key came again later in the batch
    duplicates: list[ScrapeError] = field(default_factory=list)


def upsert_records(conn: Connection, records: list[NormalisedRecord], run_id: UUID) -> UpsertStats:
    """Insert or rewrite each record, on the caller's connection and transaction."""
    stats = UpsertStats()
    for record in _drop_repeated_keys(records, stats):
        params = record.model_dump(exclude={"raw_data"})
        params.update(ingest_run_id=run_id, raw_data=Jsonb(record.raw_data))
        if conn.execute(UPSERT, params).scalar_one():
            stats.inserted += 1
        else:
            stats.updated += 1
    return stats


def _drop_repeated_keys(
    records: list[NormalisedRecord], stats: UpsertStats
) -> list[NormalisedRecord]:
    """The batch with each key once, keeping the last occurrence (it was scraped latest).

    Without this, a repeated key would be written twice and counted as 1 inserted + 1
    updated for a single row, and the repeat itself (a source problem) would go unseen.
    """
    latest: dict[tuple[str, str], NormalisedRecord] = {}
    for record in records:
        key = (record.source, record.source_record_id)
        if key in latest:
            logger.warning("duplicate record %s/%s in batch; last kept", *key)
            stats.duplicates.append(
                ScrapeError(
                    record.detail_url,
                    "duplicate_in_batch",
                    f"{key[0]}/{key[1]} appears more than once in the run; last one kept",
                )
            )
        latest[key] = record
    return list(latest.values())
