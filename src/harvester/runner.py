"""One harvester run for one source: scrape -> normalise -> upsert, tracked in ingest_run.

Transactions, so every outcome stays visible in the database:
1. The ingest_run row is committed as 'running' before any request.
2. The upsert and the run's final counts commit together: records and 'completed' land
   at once, or neither does.
3. On any failure that transaction rolls back and the run row is marked 'failed' with the
   error, so a crash leaves no half-written records and is still on record.
"""

import logging
from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb
from sqlalchemy import Connection, Engine, text

from harvester.adapters import leipzig_stadtportal, sachsen_portal
from harvester.adapters.base import ScrapeError, ScrapeResult
from harvester.config import HarvesterConfig, LeipzigStadtSource, SachsenSource
from harvester.db.upsert import upsert_records
from harvester.pipeline.normalise import NormaliseResult, normalise

logger = logging.getLogger(__name__)

Source = SachsenSource | LeipzigStadtSource

# Errors for one unusable record (counted as rejected), as opposed to page-level errors
RECORD_ERROR_KINDS = {"missing_id", "missing_title", "unexpected_row_layout", "unexpected_status"}


@dataclass
class RunSummary:
    run_id: UUID
    source: str
    status: Literal["completed", "failed"] = "completed"
    scraped: int = 0
    filtered_out: int = 0
    rejected: int = 0
    inserted: int = 0
    updated: int = 0
    errors: list[ScrapeError] = field(default_factory=list)
    error_message: str | None = None


def scrape_and_normalise(
    client: httpx.Client, source: Source, config: HarvesterConfig
) -> tuple[ScrapeResult, NormaliseResult]:
    """The network half of a run, with no database; `--dry-run` uses it on its own."""
    if isinstance(source, SachsenSource):
        scraped = sachsen_portal.scrape(client, source, config.scraping)
    else:
        scraped = leipzig_stadtportal.scrape(client, source, config.scraping)
    return scraped, normalise(scraped.records, client, config)


def run_source(
    engine: Engine, client: httpx.Client, source: Source, config: HarvesterConfig
) -> RunSummary:
    """Run one source end to end. Never raises: a failure is returned (and stored) as a
    'failed' run, so the next source still runs."""
    with engine.begin() as conn:
        run_id = _start_run(conn, source, config)
    summary = RunSummary(run_id=run_id, source=source.name)

    try:
        scraped, normalised = scrape_and_normalise(client, source, config)
        summary.scraped = len(scraped.records)
        summary.filtered_out = normalised.filtered_out
        summary.errors = list(scraped.errors)
        summary.rejected = sum(e.kind in RECORD_ERROR_KINDS for e in scraped.errors)

        if not scraped.records and scraped.errors:
            # Nothing usable and something went wrong: the source is down or its layout changed
            summary.status = "failed"
            summary.error_message = f"no records; first error: {scraped.errors[0].message}"

        with engine.begin() as conn:
            stats = upsert_records(conn, normalised.records, run_id)
            summary.inserted, summary.updated = stats.inserted, stats.updated
            summary.errors.extend(stats.duplicates)
            _finish_run(conn, summary)
    # The run boundary: whatever went wrong is recorded on the run, then the next source runs
    except Exception as exc:
        logger.exception("run %s for %s failed", run_id, source.name)
        # The upsert rolled back, so nothing was written
        summary.inserted = summary.updated = 0
        summary.status = "failed"
        summary.error_message = f"{type(exc).__name__}: {exc}"
        with engine.begin() as conn:
            _finish_run(conn, summary)
    return summary


def _start_run(conn: Connection, source: Source, config: HarvesterConfig) -> UUID:
    # city stays NULL for the state portal: one run covers all its cities
    cities = source.city_slugs
    run_id: UUID = conn.execute(
        text(
            "INSERT INTO iris_staging.ingest_run (source, city, config_snapshot)"
            " VALUES (:source, :city, :config) RETURNING id"
        ),
        {
            "source": source.name,
            "city": cities[0] if len(cities) == 1 else None,
            "config": Jsonb(config.model_dump(mode="json")),
        },
    ).scalar_one()
    return run_id


def _finish_run(conn: Connection, summary: RunSummary) -> None:
    conn.execute(
        text(
            """
            UPDATE iris_staging.ingest_run SET
                finished_at = NOW(),
                status = :status,
                records_scraped = :scraped,
                records_filtered_out = :filtered_out,
                records_rejected = :rejected,
                records_inserted = :inserted,
                records_updated = :updated,
                errors = :errors,
                error_message = :error_message
            WHERE id = :run_id
            """
        ),
        {
            "run_id": summary.run_id,
            "status": summary.status,
            "scraped": summary.scraped,
            "filtered_out": summary.filtered_out,
            "rejected": summary.rejected,
            "inserted": summary.inserted,
            "updated": summary.updated,
            "errors": Jsonb([asdict(e) for e in summary.errors]),
            "error_message": summary.error_message,
        },
    )
