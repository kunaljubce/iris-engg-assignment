from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from harvester.adapters.fetch import make_client
from harvester.config import ScrapingConfig

FIXTURES = Path(__file__).parent / "fixtures"

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
