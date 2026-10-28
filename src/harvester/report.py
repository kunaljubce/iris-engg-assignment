"""Quality report: the latest run per source, then what is stored in signal_ingest.

Runs are per source, so the per-city view comes from the stored records. Those cover all
runs, which is what a consumer of the table sees.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, text

LATEST_RUNS = text(
    """
    SELECT DISTINCT ON (source) *
    FROM iris_staging.ingest_run
    ORDER BY source, started_at DESC
    """
)

STORED = text(
    """
    SELECT source, city, count(*) AS records, count(geometry) AS with_point,
           count(*) FILTER (WHERE geocode_status = 'unresolved') AS unresolved,
           count(*) FILTER (WHERE geocode_status = 'rejected_out_of_bounds') AS rejected
    FROM iris_staging.signal_ingest
    GROUP BY 1, 2
    ORDER BY 1, 2
    """
)

FLAGS = text(
    """
    SELECT source, city, flag, count(*) AS records
    FROM iris_staging.signal_ingest, unnest(quality_flags) AS flag
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 4 DESC
    """
)


def quality_report(conn: Connection) -> str:
    runs = list(conn.execute(LATEST_RUNS))
    stored = list(conn.execute(STORED))
    flags = list(conn.execute(FLAGS))
    return "\n".join(
        [
            "IRIS-SIG-08 quality report",
            *_section("Latest runs", _runs_table(runs), "no runs yet"),
            *_section("Run errors", _errors_table(runs), "none"),
            *_section("Stored records", _stored_table(stored), "none"),
            *_section(
                "Quality flags", _table(["source", "city", "flag", "records"], flags), "none"
            ),
        ]
    )


def _runs_table(runs: list[Any]) -> list[str]:
    headers = ["source", "city", "status", "started (UTC)", "scraped", "industrial"]
    headers += ["rejected", "inserted", "updated", "errors"]
    return _table(
        headers,
        [
            [
                r.source,
                r.city or "all",
                r.status,
                _time(r.started_at),
                r.records_scraped,
                r.records_scraped - r.records_filtered_out,
                r.records_rejected,
                r.records_inserted,
                r.records_updated,
                len(r.errors),
            ]
            for r in runs
        ],
    )


def _errors_table(runs: list[Any]) -> list[str]:
    """Count per error kind, with the first message of each as an example. A failed run's
    message is listed as kind 'run_failed'."""
    rows: list[list[Any]] = []
    for run in runs:
        if run.error_message:
            rows.append([run.source, "run_failed", 1, run.error_message])
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for error in run.errors:
            by_kind.setdefault(error["kind"], []).append(error)
        rows += [[run.source, k, len(e), e[0]["message"]] for k, e in by_kind.items()]
    return _table(["source", "kind", "count", "example"], rows)


def _stored_table(stored: list[Any]) -> list[str]:
    headers = ["source", "city", "records", "with point", "unresolved", "rejected bbox"]
    return _table(headers, stored)


def _section(title: str, table: list[str], empty: str) -> list[str]:
    return ["", title, *(table or [f"  {empty}"])]


def _table(headers: list[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    """Aligned columns: text left, numbers right. Empty list when there are no rows."""
    if not rows:
        return []
    cells = [headers, *([str(v) for v in row] for row in rows)]
    numeric = [isinstance(v, int) for v in rows[0]]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]

    def line(row: list[str]) -> str:
        parts = (
            v.rjust(w) if num else v.ljust(w)
            for v, w, num in zip(row, widths, numeric, strict=True)
        )
        return "  " + "  ".join(parts).rstrip()

    return [line(row) for row in cells]


def _time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if value else "-"
