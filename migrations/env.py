from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from harvester.config import DatabaseSettings

# Alembic Config object: access to the values in alembic.ini
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models: migrations are hand-written SQL, so there is nothing to autogenerate from
target_metadata = None

# Everything this project owns lives in one schema, including Alembic's own version table.
# In a shared database, a default public.alembic_version would collide with any other
# Alembic-managed project.
STAGING_SCHEMA = "iris_staging"
VERSION_TABLE_SCHEMA = STAGING_SCHEMA


def run_migrations_offline() -> None:
    """Emit the migration SQL to stdout (`alembic upgrade --sql`) without connecting.

    Only the dialect is needed, so this works without a .env or a running database.
    """
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=VERSION_TABLE_SCHEMA,
    )

    with context.begin_transaction():
        # The version table is created before the first migration runs, so its schema
        # has to exist first
        context.execute(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}")
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database configured by the POSTGRES_* settings."""
    engine = create_engine(
        DatabaseSettings().url,
        poolclass=pool.NullPool,
        hide_parameters=True,
    )

    with engine.connect() as connection:
        # Committed separately so Alembic's own transaction starts clean and can create
        # the version table inside the schema
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
