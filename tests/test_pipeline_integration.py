"""Fixtures -> adapters -> normalise -> upsert -> database, through run_source."""

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from harvester.adapters import sachsen_portal
from harvester.config import HarvesterConfig, SachsenSource
from harvester.db.upsert import upsert_records
from harvester.runner import run_source
from tests.conftest import MockClient, load_fixture
from tests.test_sachsen_adapter import marker, markers_page

pytestmark = pytest.mark.db

DETAIL = "https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/{}"
# The 5 industrial matches in the Dresden fixture
GEWERBEGEBIET_FLUGHAFEN, GEWERBEPARK_WEISSIG = "1066719", "1033966"
OTHER_MATCHES = ["1034723", "1054740", "1065790"]


def dresden_finished(config: HarvesterConfig) -> SachsenSource:
    """The source narrowed to one listing (Dresden, finished plans): the one captured as a
    fixture."""
    [sachsen] = config.sources
    return sachsen.model_copy(update={"cities": sachsen.cities[:1], "status_filters": ["BEENDETE"]})


def portal_routes(config: HarvesterConfig) -> dict[str, str | int]:
    sachsen = dresden_finished(config)
    detail_page = load_fixture("sachsen_dresden_detail.html")
    routes: dict[str, str | int] = {
        sachsen_portal.listing_url(sachsen, "dresden", "BEENDETE"): load_fixture(
            "sachsen_dresden_listing_beendet.html"
        ),
        # This record's own detail page: the fixture with its id in the body class
        DETAIL.format(GEWERBEGEBIET_FLUGHAFEN): detail_page.replace(
            "nbp-beteiligung-1069133", f"nbp-beteiligung-{GEWERBEGEBIET_FLUGHAFEN}"
        ),
        DETAIL.format(GEWERBEPARK_WEISSIG): 404,
    }
    # Another record's detail page: no Zeitraum is read from it
    routes.update({DETAIL.format(record_id): detail_page for record_id in OTHER_MATCHES})
    return routes


def query(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql)))


def test_full_pipeline_fixture_to_db(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(portal_routes(harvester_config))

    summary = run_source(db_engine, client, dresden_finished(harvester_config), harvester_config)

    assert (summary.status, summary.scraped, summary.filtered_out, summary.inserted) == (
        "completed",
        52,
        47,
        5,
    )
    runs = query(
        db_engine,
        "SELECT source, city, status, records_inserted, finished_at FROM iris_staging.ingest_run"
        " ORDER BY source",
    )
    assert [(r.source, r.city, r.status, r.records_inserted) for r in runs] == [
        # Limited to Dresden here; a run over both cities stores NULL
        ("sachsen_portal", "dresden", "completed", 5),
    ]
    assert all(r.finished_at is not None for r in runs)

    stored = {
        r.source_record_id: r
        for r in query(
            db_engine,
            "SELECT s.source_record_id, s.publication_date, s.quality_flags, s.geocode_status,"
            " ST_SRID(s.geometry) AS srid, s.ingest_run_id = r.id AS traced"
            " FROM iris_staging.signal_ingest s JOIN iris_staging.ingest_run r"
            " ON r.id = s.ingest_run_id",
        )
    }
    assert len(stored) == 5 and all(r.traced for r in stored.values())

    flughafen = stored[GEWERBEGEBIET_FLUGHAFEN]
    assert flughafen.publication_date == date(2026, 10, 12)
    assert (flughafen.srid, flughafen.quality_flags) == (25832, [])
    assert stored[GEWERBEPARK_WEISSIG].quality_flags == [
        "detail_page_failed",
        "missing_publication_date",
    ]


def test_full_pipeline_twice_is_idempotent(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(portal_routes(harvester_config))
    source = dresden_finished(harvester_config)
    run_source(db_engine, client, source, harvester_config)
    ids_before = query(db_engine, "SELECT id FROM iris_staging.signal_ingest ORDER BY id")

    second = run_source(db_engine, client, source, harvester_config)

    assert (second.inserted, second.updated) == (0, 5)
    assert query(db_engine, "SELECT id FROM iris_staging.signal_ingest ORDER BY id") == ids_before
    # Every row now points at the second run
    assert {
        r.ingest_run_id
        for r in query(db_engine, "SELECT ingest_run_id FROM iris_staging.signal_ingest")
    } == {second.run_id}


def test_errors_visible_in_ingest_run(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    sachsen = dresden_finished(harvester_config)
    # One good marker, one without a title
    page = markers_page(
        [marker(1), marker(2, title="  ")], '<span class="ui-paginator-current">1 - 2 von 2</span>'
    )
    client = mock_client(
        {sachsen_portal.listing_url(sachsen, "dresden", "BEENDETE"): page, DETAIL.format(1): 404}
    )

    summary = run_source(db_engine, client, sachsen, harvester_config)

    [run] = query(db_engine, "SELECT status, records_rejected, errors FROM iris_staging.ingest_run")
    assert (summary.status, run.status, run.records_rejected) == ("completed", "completed", 1)
    assert summary.inserted == 1
    [error] = run.errors
    assert error["kind"] == "missing_title"
    assert '"id": 2' in error["snippet"]


def test_unreachable_source_is_a_failed_run(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    sachsen = dresden_finished(harvester_config)
    client = mock_client({sachsen_portal.listing_url(sachsen, "dresden", "BEENDETE"): 503})

    summary = run_source(db_engine, client, sachsen, harvester_config)

    [run] = query(db_engine, "SELECT status, error_message, errors FROM iris_staging.ingest_run")
    assert (summary.status, run.status) == ("failed", "failed")
    assert run.error_message is not None and "HTTP 503" in run.error_message
    assert [e["kind"] for e in run.errors] == ["retries_exhausted"]


def test_crash_rolls_back_records_and_fails_run(
    db_engine: Engine,
    mock_client: MockClient,
    harvester_config: HarvesterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def upsert_then_crash(conn: Connection, records: list[Any], run_id: UUID) -> None:
        # The rows are written, then the run dies inside the same transaction
        upsert_records(conn, records, run_id)
        raise RuntimeError("database went away")

    monkeypatch.setattr("harvester.runner.upsert_records", upsert_then_crash)
    sachsen = dresden_finished(harvester_config)
    client = mock_client(portal_routes(harvester_config))

    summary = run_source(db_engine, client, sachsen, harvester_config)

    assert summary.status == "failed"
    assert query(db_engine, "SELECT count(*) AS n FROM iris_staging.signal_ingest")[0].n == 0
    [run] = query(db_engine, "SELECT status, error_message FROM iris_staging.ingest_run")
    assert (run.status, run.error_message) == ("failed", "RuntimeError: database went away")
