import httpx
import pytest

from harvester.adapters.fetch import FetchError, fetch, make_client
from harvester.config import ScrapingConfig

URL = "https://portal.example/page"

SETTINGS = ScrapingConfig(
    request_delay_seconds=2.0,
    max_retries=3,
    retry_backoff_factor=2.0,
    user_agent="test-agent",
    timeout_seconds=5.0,
)

Reply = httpx.Response | Exception


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record waits instead of sleeping, so tests are instant and can check the waits."""
    waits: list[float] = []
    monkeypatch.setattr("harvester.adapters.fetch.time.sleep", waits.append)
    return waits


def client_replying(*replies: Reply) -> tuple[httpx.Client, list[httpx.Request]]:
    """Client whose transport answers the requests with `replies`, in order."""
    requests: list[httpx.Request] = []
    pending = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = pending.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return make_client(SETTINGS, httpx.MockTransport(handler)), requests


def test_retries_on_503_then_succeeds(sleeps: list[float]) -> None:
    client, requests = client_replying(
        httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")
    )

    assert fetch(client, URL, SETTINGS) == "ok"
    assert len(requests) == 3
    assert sleeps == [2.0, 2.0, 4.0]  # polite delay, then backoff 2 ** 1, 2 ** 2
    assert requests[0].headers["User-Agent"] == "test-agent"


def test_retries_on_timeout(sleeps: list[float]) -> None:
    client, requests = client_replying(
        httpx.ConnectTimeout("timed out"), httpx.Response(200, text="ok")
    )

    assert fetch(client, URL, SETTINGS) == "ok"
    assert len(requests) == 2


def test_honours_retry_after_on_429(sleeps: list[float]) -> None:
    client, _ = client_replying(
        httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, text="ok")
    )

    assert fetch(client, URL, SETTINGS) == "ok"
    assert sleeps == [2.0, 5.0]


def test_no_retry_on_404(sleeps: list[float]) -> None:
    client, requests = client_replying(httpx.Response(404))

    with pytest.raises(FetchError) as caught:
        fetch(client, URL, SETTINGS)

    assert (caught.value.kind, caught.value.status) == ("http_4xx", 404)
    assert len(requests) == 1


def test_raises_fetch_error_when_retries_exhausted(sleeps: list[float]) -> None:
    client, requests = client_replying(*[httpx.Response(503)] * 4)

    with pytest.raises(FetchError) as caught:
        fetch(client, URL, SETTINGS)

    assert (caught.value.kind, caught.value.status) == ("retries_exhausted", 503)
    assert len(requests) == 4  # first attempt + max_retries
    assert sleeps == [2.0, 2.0, 4.0, 8.0]
