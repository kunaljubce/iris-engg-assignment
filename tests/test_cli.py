import httpx
import pytest
from click.testing import CliRunner

from harvester.adapters import leipzig_stadtportal
from harvester.cli import cli, select_sources
from harvester.config import HarvesterConfig, LeipzigStadtSource, SachsenSource
from tests.conftest import MockClient, load_fixture


def test_select_by_city_narrows_state_portal(harvester_config: HarvesterConfig) -> None:
    sachsen, leipzig = select_sources(harvester_config, None, "leipzig")

    assert isinstance(sachsen, SachsenSource) and sachsen.city_slugs == ["leipzig"]
    assert isinstance(leipzig, LeipzigStadtSource)
    [only] = select_sources(harvester_config, None, "dresden")
    assert only.city_slugs == ["dresden"]


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
    leipzig = harvester_config.sources[1]
    assert isinstance(leipzig, LeipzigStadtSource)
    client = mock_client(
        {
            leipzig_stadtportal.listing_url(leipzig): load_fixture(
                "leipzig_stadt_listing_im_verfahren.html"
            )
        }
    )

    def fake_client(*_: object) -> httpx.Client:
        return client

    monkeypatch.setattr("harvester.cli.make_client", fake_client)
    monkeypatch.setattr("harvester.cli.load_config", lambda: harvester_config)

    def no_database() -> None:
        pytest.fail("--dry-run must not connect to the database")

    monkeypatch.setattr("harvester.cli.make_engine", no_database)

    result = CliRunner().invoke(cli, ["run", "--dry-run", "--source", "leipzig_stadtportal"])

    assert result.exit_code == 0, result.output
    assert (
        "leipzig_stadtportal: 39 scraped, 10 industrial, 29 filtered out, 0 errors" in result.output
    )
    assert "KEEP Bebauungsplan Nr. 911 Industriegebiet" in result.output
