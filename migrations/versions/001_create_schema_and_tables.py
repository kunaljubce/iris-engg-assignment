"""create schema and tables

Revision ID: 001
Revises:
Create Date: 2026-10-27 21:21:40.747410

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the iris_staging schema and its two tables.

    No secondary indexes: under 1000 rows a sequential scan is faster. The only index besides
    the primary keys is the one behind uq_source_record, which ON CONFLICT requires.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    # Already created by env.py; kept so this migration's SQL is complete on its own
    op.execute("CREATE SCHEMA IF NOT EXISTS iris_staging")

    op.execute(
        """
        CREATE TABLE iris_staging.ingest_run (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at          TIMESTAMPTZ,
            source               TEXT NOT NULL,
            city                 TEXT,
            status               TEXT NOT NULL DEFAULT 'running'
                                 CHECK (status IN ('running', 'completed', 'failed')),
            records_scraped      INTEGER NOT NULL DEFAULT 0,
            records_filtered_out INTEGER NOT NULL DEFAULT 0,
            records_rejected     INTEGER NOT NULL DEFAULT 0,
            records_inserted     INTEGER NOT NULL DEFAULT 0,
            records_updated      INTEGER NOT NULL DEFAULT 0,
            errors               JSONB NOT NULL DEFAULT '[]'::jsonb,
            config_snapshot      JSONB,
            error_message        TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iris_staging.signal_ingest (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source           TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            ingest_run_id    UUID NOT NULL REFERENCES iris_staging.ingest_run(id),

            city             TEXT NOT NULL,
            title            TEXT NOT NULL,
            detail_url       TEXT NOT NULL,
            -- As the source gives it: street address (state portal) or district(s) (city portal)
            location_text    TEXT,

            -- Start of the detail page's "Zeitraum"; the raw text stays in raw_data
            publication_date DATE,

            -- Only industrial matches are stored, so there is always at least one keyword
            matched_keywords TEXT[] NOT NULL CHECK (cardinality(matched_keywords) > 0),

            geometry         GEOMETRY(Point, 25832),
            geocode_status   TEXT NOT NULL
                             CHECK (geocode_status IN (
                                 'source_coordinates',
                                 'unresolved',
                                 'rejected_out_of_bounds'
                             )),
            geocode_reason   TEXT,

            quality_flags    TEXT[] NOT NULL DEFAULT '{}',
            raw_data         JSONB NOT NULL,

            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            -- Named: ON CONFLICT and the tests refer to it
            CONSTRAINT uq_source_record UNIQUE (source, source_record_id),
            -- A point exists exactly when the source gave usable coordinates
            CONSTRAINT ck_geometry_matches_status
                CHECK ((geocode_status = 'source_coordinates') = (geometry IS NOT NULL))
        )
        """
    )


def downgrade() -> None:
    """Drop only the two tables.

    The postgis extension and the iris_staging schema stay: other schemas may use the extension,
    and the schema holds Alembic's version table. A migration only removes what it alone owns.
    """
    # signal_ingest first: its foreign key references ingest_run
    op.execute("DROP TABLE iris_staging.signal_ingest")
    op.execute("DROP TABLE iris_staging.ingest_run")
