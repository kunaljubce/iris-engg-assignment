## Implementation Approach

One adapter, `sachsen_portal.py`, runs once per city and status and produces `ScrapedRecord`s. It fetches pages with one shared `httpx.Client` and parses them with BeautifulSoup. Parsing functions take an HTML string and return data, so tests feed them fixture files directly. A shared pipeline applies the industrial keyword filter, fetches the detail page of each match for its publication date, checks coordinates against the city bounding box, and upserts into PostGIS, where `ST_Transform` produces the EPSG:25832 point.

## Database Schema

Every column traces back to a line in the brief; anything else scraped stays in `raw_data`.

| Brief | Columns |
|---|---|
| "preserve source identifiers" | `source`, `source_record_id` |
| "titles" | `title` |
| "publication dates" + "verified rather than assumed" | `publication_date` (raw "Zeitraum" text kept in `raw_data`) |
| "source URLs" / "traceable staging records" | `detail_url`, `ingest_run_id` |
| "geographic context when available" | `city`, `location_text` |
| "EPSG:25832 point geometries" | `geometry` |
| "document ambiguous or unresolved locations" / "invalid coordinates are not silently accepted" | `geocode_status`, `geocode_reason` |
| "errors and missing fields are visible" | `quality_flags` |
| "filter industrial land-use zoning hearings" (why this row matched) | `matched_keywords` |
| "malformed data" / "mapping assumptions" (the values as scraped) | `raw_data` |

Only industrial matches are stored: the objective says *filter*. Non-matching records are counted in `ingest_run.records_filtered_out`, not stored. Fields that are scraped but not required (status, raw "Zeitraum" text, source coordinates as given) live in `raw_data` only.

No secondary indexes: at <1000 rows a sequential scan beats any index, and nothing queries the geometry spatially except validation, which reads every row anyway. The only index besides the primary key is the one behind `uq_source_record`, which `ON CONFLICT` requires.

1. `iris_staging.ingest_run` - Tracks each harvester execution for auditability and quality reporting.
2. `iris_staging.signal_ingest` - Stores the actual details of the filtered industrial land-use zoning hearings near Dresden and Leipzig.

### Upsert Logic

Idempotency comes from the natural key: `uq_source_record UNIQUE (source, source_record_id)` plus `INSERT ... ON CONFLICT DO UPDATE`. Re-running over the same data cannot add rows. There is **no change detection**: an existing row is always rewritten with the latest values. Nothing in the brief consumes "what changed", so telling a real change apart from a re-scrape isn't worth a hash column and its tests (see "What We Are NOT Doing").

**Step 0: de-duplicate the batch in Python.** Records are written one statement each, so a repeated key would not fail, but it would be written twice and counted as 1 inserted + 1 updated for a single row, and the repeat (a source problem) would go unseen. Records with a repeated `(source, source_record_id)` in one batch (e.g. a procedure that moves from `AKTUELLE` to `BEENDETE` between the two listing requests) are collapsed to the last occurrence. Each one dropped is logged to `ingest_run.errors` as `duplicate_in_batch`.

**Step 1: insert or overwrite.**

```sql
INSERT INTO iris_staging.signal_ingest (
    source, source_record_id, ingest_run_id,
    city, title, detail_url, location_text,
    publication_date, matched_keywords,
    geometry, geocode_status, geocode_reason, quality_flags, raw_data
)
VALUES (
    ...,
    -- WGS84 -> EPSG:25832 in PostGIS (no pyproj). NULL lon/lat (unresolved or rejected)
    -- gives a NULL geometry; the bounding box was checked in Python before this
    ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 25832),
    ...
)
ON CONFLICT (source, source_record_id) DO UPDATE SET
    ingest_run_id    = EXCLUDED.ingest_run_id,
    city             = EXCLUDED.city,
    title            = EXCLUDED.title,
    detail_url       = EXCLUDED.detail_url,
    location_text    = EXCLUDED.location_text,
    publication_date = EXCLUDED.publication_date,
    matched_keywords = EXCLUDED.matched_keywords,
    geometry         = EXCLUDED.geometry,
    geocode_status   = EXCLUDED.geocode_status,
    geocode_reason   = EXCLUDED.geocode_reason,
    quality_flags    = EXCLUDED.quality_flags,
    raw_data         = EXCLUDED.raw_data,
    updated_at       = NOW()
RETURNING (xmax = 0) AS inserted;
```

**Counting:** `inserted` = rows returned with `inserted = true`; `updated` = the rest. A re-run over unchanged data therefore reports 0 inserted and N updated, with the row count and every `id` and `created_at` unchanged.

`xmax = 0` is the standard PostgreSQL test for whether a row from an `ON CONFLICT` upsert was newly inserted. It's covered by a test.

**How it's executed:** the statement is a SQLAlchemy `text()` with named parameters, run with one parameter dict per record (no ORM models; see Phase 1). psycopg 3 adapts Python lists to `TEXT[]`. Dicts for `JSONB` must be wrapped in `psycopg.types.json.Jsonb(...)`, or binding fails. Records are executed one statement each, in a loop inside the batch's single transaction, collecting each `RETURNING` row. That's simple, keeps the per-record counting obvious, and is fast enough for <1000 rows; collecting `RETURNING` rows from one `executemany` call isn't something to rely on with `text()`.

**Rows are never deleted.** A plan that disappears from the portal, or stops matching after the keyword list changes, keeps its row; its `ingest_run_id` then points at an older run than the latest one covering its city (validation check 9 lists these).

## Industrial Keyword Filter

Plan titles are usually German compounds ("Gewerbestandort Nord", "Logistikzentrum Radefeld", "Kleingewerbe am Bahnhof"), so whole-word matching misses most real hits. The filter uses two kinds of match:

- **Long keywords** (Gewerbe, Industrie, Logistik, ...) match case-insensitively **anywhere inside a word**. The whole word containing the hit is kept as evidence.
- **Short codes** (GE, GI, GVZ) match **case-sensitively as whole tokens only**. As substrings they would hit ordinary words ("gegen", "Gießen").

```python
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
```

`evidence` goes into `raw_data` and the quality report, so false positives (e.g. "Wohn- und Gewerbepark", a mixed area) can be spotted and the keyword list tuned.

Only the title is read; see "What We Are NOT Doing" for why not descriptions. The filter runs on listing data, **before** any detail page is fetched, so only matches cost an extra request. Only records with `is_industrial = True` are stored; `matched_keywords` is the stored evidence for why each row is there. Non-matches are counted in `ingest_run.records_filtered_out`, and `--dry-run` prints every record with its filter decision, so false negatives can be checked while tuning the keyword list.

---
