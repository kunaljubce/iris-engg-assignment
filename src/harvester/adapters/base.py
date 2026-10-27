"""What every source adapter returns.

Plain dataclasses, not pydantic: these are built by our own code, so there is nothing to
validate. A bad record never stops the run; it becomes a ScrapeError next to the good ones.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from harvester.models import ScrapedRecord

# Enough to see what was wrong with an entry without bloating ingest_run.errors
SNIPPET_MAX_CHARS = 2048


@dataclass(frozen=True)
class ScrapeError:
    url: str
    # 'missing_id' | 'missing_title' | 'markers_not_found' | 'count_mismatch' | 'http_4xx'
    # | 'retries_exhausted'
    kind: str
    message: str
    snippet: str | None = None  # marker JSON entry or row HTML, cut to SNIPPET_MAX_CHARS


@dataclass
class ScrapeResult:
    records: list[ScrapedRecord] = field(default_factory=list)
    errors: list[ScrapeError] = field(default_factory=list)


class BaseAdapter(ABC):
    @abstractmethod
    def scrape(self, client: httpx.Client) -> ScrapeResult:
        """Scrape all listing records from this source. Handles pagination internally."""
