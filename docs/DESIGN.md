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

### `iris_staging.ingest_run`

Tracks each harvester execution for auditability and quality reporting.

```sql
CREATE TABLE iris_staging.ingest_run (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at          TIMESTAMPTZ,
    source               TEXT NOT NULL,         -- 'sachsen_portal'
    city                 TEXT,                  -- 'dresden' | 'leipzig' | NULL (run covers all cities)
    status               TEXT NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'completed', 'failed')),
    -- NOT NULL so the quality report's sums never meet a NULL
    records_scraped      INTEGER NOT NULL DEFAULT 0,
    records_filtered_out INTEGER NOT NULL DEFAULT 0,  -- parsed fine, not industrial
    records_rejected     INTEGER NOT NULL DEFAULT 0,  -- unusable (no ID or title), see errors
    records_inserted     INTEGER NOT NULL DEFAULT 0,
    records_updated      INTEGER NOT NULL DEFAULT 0,  -- already present, rewritten
    errors               JSONB NOT NULL DEFAULT '[]'::jsonb,
    config_snapshot      JSONB,                 -- frozen copy of settings used
    error_message        TEXT
);
```

### `iris_staging.signal_ingest`

```sql
CREATE TABLE iris_staging.signal_ingest (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source           TEXT NOT NULL,             -- 'sachsen_portal'
    source_record_id TEXT NOT NULL,             -- marker id
    ingest_run_id    UUID NOT NULL REFERENCES iris_staging.ingest_run(id),  -- run that last wrote the row

    city             TEXT NOT NULL,             -- 'dresden' | 'leipzig'
    title            TEXT NOT NULL,
    detail_url       TEXT NOT NULL,             -- traceability: every row links back to its source
    location_text    TEXT,                      -- street address as the source gives it

    publication_date DATE,                      -- start of the detail page's "Zeitraum"; NULL if none

    -- Only industrial matches are stored, so there is always at least one keyword
    matched_keywords TEXT[] NOT NULL CHECK (cardinality(matched_keywords) > 0),

    geometry         GEOMETRY(Point, 25832),
    geocode_status   TEXT NOT NULL
                     CHECK (geocode_status IN
                            ('source_coordinates', 'unresolved', 'rejected_out_of_bounds')),
    geocode_reason   TEXT,                      -- why unresolved/rejected, e.g. 'marker has no coordinates'

    quality_flags    TEXT[] NOT NULL DEFAULT '{}',  -- e.g. 'missing_publication_date', 'missing_location',
                                                    -- 'unparseable_date', 'detail_page_failed'
    raw_data         JSONB NOT NULL,            -- all scraped fields as-is, plus filter evidence

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- first seen
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- last written, i.e. last seen

    -- Named: ON CONFLICT and the tests refer to it
    CONSTRAINT uq_source_record UNIQUE (source, source_record_id),
    -- A point exists exactly when the source gave usable coordinates: the database itself
    -- refuses a point without that status, or that status without a point
    CONSTRAINT ck_geometry_matches_status
        CHECK ((geocode_status = 'source_coordinates') = (geometry IS NOT NULL))
);
```