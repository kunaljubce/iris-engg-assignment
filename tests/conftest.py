from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, text

from harvester.adapters.fetch import make_client
from harvester.config import HarvesterConfig, ScrapingConfig, load_config
from harvester.db.engine import DatabaseConfigError, load_database_settings, make_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yml"

MockClient = Callable[[dict[str, str | int]], httpx.Client]


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def scraping_config() -> ScrapingConfig:
    # No polite delay and no retries, so adapter tests run instantly
    return ScrapingConfig(
        request_delay_seconds=0,
        max_retries=0,
        retry_backoff_factor=0,
        user_agent="test-agent",
        timeout_seconds=5,
    )


@pytest.fixture
def mock_client(scraping_config: ScrapingConfig) -> MockClient:
    """Client serving page text (or a status code) per full URL. Any other URL fails the
    test, so tests can never reach the live portals."""

    def build(routes: dict[str, str | int]) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url not in routes:
                pytest.fail(f"unrouted URL: {url}")
            reply = routes[url]
            if isinstance(reply, int):
                return httpx.Response(reply)
            return httpx.Response(200, text=reply)

        return make_client(scraping_config, httpx.MockTransport(handler))

    return build


@pytest.fixture
def harvester_config(scraping_config: ScrapingConfig) -> HarvesterConfig:
    """The repo's settings.yml, with the instant scraping settings."""
    return load_config(SETTINGS_PATH).model_copy(update={"scraping": scraping_config})


@pytest.fixture(scope="session")
def _migrated_test_engine() -> Iterator[Engine]:
    """Engine on iris_test with the schema migrated to head, once per test session.

    Skips when the database settings are missing (no POSTGRES_PASSWORD), so the unit tests
    run without Docker. Settings present but database unreachable is a failure, not a skip.
    """
    try:
        settings = load_database_settings()
    except DatabaseConfigError as exc:
        pytest.skip(f"db tests need the PostGIS test database: {exc}")

    engine = make_engine(settings.test_url)
    # No alembic.ini: that would also reconfigure logging for the whole test session
    alembic_config = AlembicConfig()
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    with engine.connect() as connection:
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_engine(_migrated_test_engine: Engine) -> Engine:
    """The migrated iris_test engine, emptied before each test.

    Emptied rather than wrapped in a rolled-back transaction: run_source commits the
    ingest_run row and the upsert separately on purpose, so a test can't hold them in one."""
    with _migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE iris_staging.signal_ingest, iris_staging.ingest_run"))
    return _migrated_test_engine
