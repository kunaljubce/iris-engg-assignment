"""Fixtures -> adapters -> normalise -> upsert -> database, through run_source."""

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from harvester.adapters import leipzig_stadtportal, sachsen_portal
from harvester.config import HarvesterConfig, LeipzigStadtSource, SachsenSource
from harvester.db.upsert import upsert_records
from harvester.runner import run_source
from tests.conftest import MockClient, load_fixture
from tests.test_leipzig_adapter import row, table

pytestmark = pytest.mark.db

DETAIL = "https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/{}"
# The 5 industrial matches in the Dresden fixture
GEWERBEGEBIET_FLUGHAFEN, GEWERBEPARK_WEISSIG = "1066719", "1033966"
OTHER_MATCHES = ["1034723", "1054740", "1065790"]


def sources(config: HarvesterConfig) -> tuple[SachsenSource, LeipzigStadtSource]:
    sachsen, leipzig = config.sources
    assert isinstance(sachsen, SachsenSource) and isinstance(leipzig, LeipzigStadtSource)
    # One listing (Dresden, finished plans): the one captured as a fixture
    sachsen = sachsen.model_copy(
        update={"cities": sachsen.cities[:1], "status_filters": ["BEENDETE"]}
    )
    return sachsen, leipzig


def portal_routes(config: HarvesterConfig) -> dict[str, str | int]:
    sachsen, leipzig = sources(config)
    detail_page = load_fixture("sachsen_dresden_detail.html")
    routes: dict[str, str | int] = {
        sachsen_portal.listing_url(sachsen, "dresden", "BEENDETE"): load_fixture(
            "sachsen_dresden_listing_beendet.html"
        ),
        leipzig_stadtportal.listing_url(leipzig): load_fixture(
            "leipzig_stadt_listing_im_verfahren.html"
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


def run_all(engine: Engine, client: Any, config: HarvesterConfig) -> list[Any]:
    return [run_source(engine, client, source, config) for source in sources(config)]


def query(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql)))


def test_full_pipeline_fixture_to_db(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(portal_routes(harvester_config))

    sachsen, leipzig = run_all(db_engine, client, harvester_config)

    assert (sachsen.status, sachsen.scraped, sachsen.filtered_out, sachsen.inserted) == (
        "completed",
        52,
        47,
        5,
    )
    assert (leipzig.status, leipzig.scraped, leipzig.filtered_out, leipzig.inserted) == (
        "completed",
        39,
        29,
        10,
    )
    runs = query(
        db_engine,
        "SELECT source, city, status, records_inserted, finished_at FROM iris_staging.ingest_run"
        " ORDER BY source",
    )
    assert [(r.source, r.city, r.status, r.records_inserted) for r in runs] == [
        ("leipzig_stadtportal", "leipzig", "completed", 10),
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
    assert len(stored) == 15 and all(r.traced for r in stored.values())

    flughafen = stored[GEWERBEGEBIET_FLUGHAFEN]
    assert flughafen.publication_date == date(2026, 10, 12)
    assert (flughafen.srid, flughafen.quality_flags) == (25832, [])
    assert stored[GEWERBEPARK_WEISSIG].quality_flags == [
        "detail_page_failed",
        "missing_publication_date",
    ]
    nr_911 = stored["11196578"]
    assert (nr_911.geocode_status, nr_911.srid) == ("unresolved", None)


def test_full_pipeline_twice_is_idempotent(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(portal_routes(harvester_config))
    run_all(db_engine, client, harvester_config)
    ids_before = query(db_engine, "SELECT id FROM iris_staging.signal_ingest ORDER BY id")

    second = run_all(db_engine, client, harvester_config)

    assert [(s.inserted, s.updated) for s in second] == [(0, 5), (0, 10)]
    assert query(db_engine, "SELECT id FROM iris_staging.signal_ingest ORDER BY id") == ids_before
    # Every row now points at the second run of its source
    latest = {s.run_id for s in second}
    assert {
        r.ingest_run_id
        for r in query(db_engine, "SELECT ingest_run_id FROM iris_staging.signal_ingest")
    } == latest


def test_errors_visible_in_ingest_run(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    _, leipzig = sources(harvester_config)
    # One good row, one plan already in force (the portal ignoring its own filter)
    page = table(row("1"), row("2", status="rechtskräftig"))
    client = mock_client({leipzig_stadtportal.listing_url(leipzig): page})

    summary = run_source(db_engine, client, leipzig, harvester_config)

    [run] = query(db_engine, "SELECT status, records_rejected, errors FROM iris_staging.ingest_run")
    assert (summary.status, run.status, run.records_rejected) == ("completed", "completed", 1)
    assert summary.inserted == 1
    [error] = run.errors
    assert error["kind"] == "unexpected_status"
    assert "rechtskräftig" in error["snippet"]


def test_unreachable_source_is_a_failed_run(
    db_engine: Engine, mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    _, leipzig = sources(harvester_config)
    client = mock_client({leipzig_stadtportal.listing_url(leipzig): 503})

    summary = run_source(db_engine, client, leipzig, harvester_config)

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
    _, leipzig = sources(harvester_config)
    listing = load_fixture("leipzig_stadt_listing_im_verfahren.html")
    client = mock_client({leipzig_stadtportal.listing_url(leipzig): listing})

    summary = run_source(db_engine, client, leipzig, harvester_config)

    assert summary.status == "failed"
    assert query(db_engine, "SELECT count(*) AS n FROM iris_staging.signal_ingest")[0].n == 0
    [run] = query(db_engine, "SELECT status, error_message FROM iris_staging.ingest_run")
    assert (run.status, run.error_message) == ("failed", "RuntimeError: database went away")
