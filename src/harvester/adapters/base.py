"""What every source adapter returns.

There is no adapter base class: each adapter module has a `scrape()` function with the same
signature, and the CLI picks one by the source's `type`.

Plain dataclasses, not pydantic: these are built by our own code, so there is nothing to
validate. A bad record never stops the run; it becomes a ScrapeError next to the good ones.
"""

from dataclasses import dataclass, field

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
