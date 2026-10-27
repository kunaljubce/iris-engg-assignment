"""The one place HTTP requests are made, so the retry and politeness rules live in one spot.

Rules (see "Resilience" in the plan):
- network errors, timeouts, 5xx and 429 are retried up to `max_retries` times after the
  first attempt, waiting `retry_backoff_factor ** n` seconds before retry n (2, 4, 8 s);
  a 429's `Retry-After` (in seconds) replaces that wait
- any other non-2xx status is not retried: retrying a 404 can't fix it
- the first attempt waits `request_delay_seconds`, so consecutive pages are spaced out
"""

import logging
import time

import httpx

from harvester.config import ScrapingConfig

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """A page could not be fetched; adapters turn this into a ScrapeError or quality flag."""

    def __init__(self, kind: str, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{kind} for {url}: {message}")
        self.kind = kind  # 'http_4xx' | 'retries_exhausted', same values as ScrapeError.kind
        self.url = url
        self.status = status
        self.message = message


def make_client(
    settings: ScrapingConfig, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Client used for every request; tests pass a MockTransport so nothing goes online."""
    return httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.timeout_seconds,
        # The state portal redirects a wrong-city detail URL to the listing page
        follow_redirects=True,
        transport=transport,
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    # Only the seconds form; the HTTP-date form falls back to the normal backoff
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdigit() else None


def fetch(client: httpx.Client, url: str, settings: ScrapingConfig) -> str:
    """GET `url` and return its text, or raise FetchError."""
    wait = settings.request_delay_seconds
    status: int | None = None
    problem = ""

    for attempt in range(settings.max_retries + 1):
        time.sleep(wait)
        wait = settings.retry_backoff_factor ** (attempt + 1)
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            status, problem = None, f"{type(exc).__name__}: {exc}"
        else:
            if response.is_success:
                return response.text
            status, problem = response.status_code, f"HTTP {response.status_code}"
            if status != 429 and status < 500:
                raise FetchError("http_4xx", url, status, problem)
            retry_after = _retry_after_seconds(response) if status == 429 else None
            if retry_after is not None:
                wait = retry_after

        if attempt < settings.max_retries:
            logger.warning("%s on %s, retrying in %.0f s", problem, url, wait)

    raise FetchError(
        "retries_exhausted", url, status, f"{problem} after {settings.max_retries + 1} attempts"
    )
