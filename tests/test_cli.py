import httpx
import pytest
from click.testing import CliRunner

from harvester.adapters import sachsen_portal
from harvester.cli import cli, select_sources
from harvester.config import HarvesterConfig
from tests.conftest import MockClient, load_fixture


def test_select_by_city_narrows_state_portal(harvester_config: HarvesterConfig) -> None:
    [leipzig] = select_sources(harvester_config, None, "leipzig")
    [dresden] = select_sources(harvester_config, None, "dresden")

    assert leipzig.city_slugs == ["leipzig"]
    assert dresden.city_slugs == ["dresden"]


def test_unknown_source_or_city_is_an_error() -> None:
    runner = CliRunner()

    unknown_source = runner.invoke(cli, ["run", "--dry-run", "--source", "nope"])
    unknown_city = runner.invoke(cli, ["run", "--dry-run", "--city", "chemnitz"])

    assert unknown_source.exit_code == 2 and "one of: sachsen_portal" in unknown_source.output
    assert unknown_city.exit_code == 2 and "chemnitz" in unknown_city.output


def test_dry_run_prints_decisions_without_database(
    mock_client: MockClient,
    harvester_config: HarvesterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One listing (Dresden, finished plans) and every match's detail page as a 404
    [sachsen] = harvester_config.sources
    sachsen = sachsen.model_copy(
        update={"cities": sachsen.cities[:1], "status_filters": ["BEENDETE"]}
    )
    harvester_config = harvester_config.model_copy(update={"sources": [sachsen]})
    detail = "https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/{}"
    matches = ["1066719", "1033966", "1034723", "1054740", "1065790"]
    client = mock_client(
        {
            sachsen_portal.listing_url(sachsen, "dresden", "BEENDETE"): load_fixture(
                "sachsen_dresden_listing_beendet.html"
            ),
            **{detail.format(record_id): 404 for record_id in matches},
        }
    )

    def fake_client(*_: object) -> httpx.Client:
        return client

    monkeypatch.setattr("harvester.cli.make_client", fake_client)
    monkeypatch.setattr("harvester.cli.load_config", lambda: harvester_config)

    def no_database() -> None:
        pytest.fail("--dry-run must not connect to the database")

    monkeypatch.setattr("harvester.cli.make_engine", no_database)

    result = CliRunner().invoke(cli, ["run", "--dry-run", "--source", "sachsen_portal"])

    assert result.exit_code == 0, result.output
    assert "sachsen_portal: 52 scraped, 5 industrial, 47 filtered out, 0 errors" in result.output
    assert "KEEP Bebauungsplan Nr. 3017, Dresden-Hellerau Nr. 13, Gewerbegebiet Flughafen" in (
        result.output
    )
