"""SQLAlchemy engine (psycopg 3) for the harvester database. `--dry-run` never calls this."""

from pydantic import ValidationError
from sqlalchemy import URL, Engine, create_engine

from harvester.config import DatabaseSettings


class DatabaseConfigError(Exception):
    pass


def load_database_settings() -> DatabaseSettings:
    """POSTGRES_* settings from the environment or .env, with a clear error if incomplete."""
    try:
        return DatabaseSettings()
    except ValidationError as exc:
        missing = ", ".join(
            f"POSTGRES_{'_'.join(map(str, e['loc'])).upper()}" for e in exc.errors()
        )
        raise DatabaseConfigError(
            f"database settings incomplete ({missing}); copy .env.example to .env and set them"
        ) from exc


def make_engine(url: URL | None = None) -> Engine:
    """Engine for `url`, or the harvester database by default.

    hide_parameters keeps bound values (record data) out of error messages and logs.
    """
    return create_engine(url or load_database_settings().url, hide_parameters=True)
