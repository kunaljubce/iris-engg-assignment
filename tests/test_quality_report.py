from typing import Any
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb
from sqlalchemy import Connection, Engine, text

from harvester.db.upsert import upsert_records
from harvester.report import quality_report
from tests.db_helpers import make_record, make_run

pytestmark = pytest.mark.db


def finish_run(conn: Connection, run_id: UUID, started: str, **values: Any) -> None:
    """Fill in a run made by make_run. started is set explicitly: NOW() is the same for
    every run created in one transaction."""
    columns = {
        "status": "completed",
        "records_scraped": 0,
        "records_filtered_out": 0,
        "records_rejected": 0,
        "records_inserted": 0,
        "records_updated": 0,
        "errors": [],
        "error_message": None,
        **values,
    }
    conn.execute(
        text(
            "UPDATE iris_staging.ingest_run SET started_at = :started, finished_at = :started, "
            + ", ".join(f"{c} = :{c}" for c in columns)
            + " WHERE id = :run_id"
        ),
        {**columns, "errors": Jsonb(columns["errors"]), "started": started, "run_id": run_id},
    )


def table_lines(report: str) -> list[str]:
    """Report lines with single spaces, so the tests don't depend on column widths."""
    return [" ".join(line.split()) for line in report.splitlines()]


def test_report_shows_latest_run_and_stored_records(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        older = make_run(conn)
        finish_run(conn, older, "2026-10-27 10:00+00", records_scraped=1)
        latest = make_run(conn)
        finish_run(
            conn,
            latest,
            "2026-10-28 10:00+00",
            records_scraped=40,
            records_filtered_out=38,
            records_inserted=1,
            records_updated=1,
        )
        upsert_records(
            conn,
            [
                make_record("1"),
                make_record(
                    "2",
                    city="leipzig",
                    lon=None,
                    lat=None,
                    geocode_status="unresolved",
                    quality_flags=["missing_location"],
                ),
            ],
            latest,
        )

        report = quality_report(conn)

    lines = table_lines(report)
    # The latest run only; the older one (scraped=1) is not shown
    assert "sachsen_portal all completed 2026-10-28 10:00 40 2 0 1 1 0" in lines
    assert not any(line.startswith("sachsen_portal all completed 2026-10-27") for line in lines)
    assert "sachsen_portal dresden 1 1 0 0" in lines
    assert "sachsen_portal leipzig 1 0 1 0" in lines
    assert "sachsen_portal leipzig missing_location 1" in lines


def test_report_shows_failed_run_and_its_errors(db_engine: Engine) -> None:
    errors = [
        {"url": f"https://example.org/{i}", "kind": "retries_exhausted", "message": "HTTP 503"}
        for i in range(7)
    ]
    with db_engine.begin() as conn:
        run_id = make_run(conn, source="leipzig_stadtportal")
        finish_run(
            conn,
            run_id,
            "2026-10-28 10:00+00",
            status="failed",
            errors=errors,
            error_message="no records; first error: HTTP 503",
        )

        report = quality_report(conn)

    lines = table_lines(report)
    assert "leipzig_stadtportal all failed 2026-10-28 10:00 0 0 0 0 0 7" in lines
    assert "leipzig_stadtportal run_failed 1 no records; first error: HTTP 503" in lines
    assert "leipzig_stadtportal retries_exhausted 7 HTTP 503" in lines


def test_report_on_empty_database(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        report = quality_report(conn)

    assert report.splitlines()[1:] == [
        "",
        "Latest runs",
        "  no runs yet",
        "",
        "Run errors",
        "  none",
        "",
        "Stored records",
        "  none",
        "",
        "Quality flags",
        "  none",
    ]
