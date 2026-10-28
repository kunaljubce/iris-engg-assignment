"""Industrial keyword filter, run on listing data before any detail page is fetched.

Titles are mostly German compounds ("Gewerbestandort Nord", "Kleingewerbe am Bahnhof"), so
long keywords match case-insensitively anywhere inside a word. Short codes (GE, GI, GVZ)
match case-sensitively as whole tokens only; as substrings they would hit "gegen", "Gießen".
"""

import re
from dataclasses import dataclass, field

from harvester.config import IndustrialFilterConfig


@dataclass(frozen=True)
class FilterResult:
    is_industrial: bool
    matched_keywords: list[str] = field(default_factory=list)  # keywords as written in config
    evidence: list[str] = field(default_factory=list)  # matched words, e.g. "Gewerbestandort"


class IndustrialFilter:
    def __init__(self, config: IndustrialFilterConfig) -> None:
        # \w* on both sides captures the whole compound word around the keyword as evidence
        self._patterns = [
            *((kw, re.compile(rf"\w*{re.escape(kw)}\w*", re.IGNORECASE)) for kw in config.keywords),
            *((tok, re.compile(rf"\b{re.escape(tok)}\b")) for tok in config.case_sensitive_tokens),
        ]

    def check(self, title: str) -> FilterResult:
        keywords: list[str] = []
        evidence: list[str] = []
        for keyword, pattern in self._patterns:
            hits = pattern.findall(title)
            if hits:
                keywords.append(keyword)
                evidence.extend(hits)

        unique = list(dict.fromkeys(keywords))
        return FilterResult(
            is_industrial=bool(unique),
            matched_keywords=unique,
            evidence=list(dict.fromkeys(evidence)),
        )
