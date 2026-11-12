"""Alembic environment.

Which database, in order:
1. `sqlalchemy.url` set by the caller (tests always pass one);
2. MIGRATION_DATABASE_URL, or MIGRATION_DATABASE_URL_FILE, or `migration_database_url` in the
   secrets directory: the schema owner. Nothing else from the app's settings is needed;
3. development only: the app's own DATABASE_URL.

In production, step 3 is refused: the app's runtime role must never run migrations, and the
migration job shouldn't need the app's Google secret.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.models  # noqa: F401  # Registers every table on Base.metadata.
from app.core.config import Environment, get_settings, read_secret
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # Tests pass an explicit URL, so they can never fall through to the development database.
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    migration = read_secret("MIGRATION_DATABASE_URL")
    if migration:
        if not migration.startswith("postgresql+psycopg://"):
            raise SystemExit("MIGRATION_DATABASE_URL must use the postgresql+psycopg:// scheme")
        return migration
    if os.environ.get("APP_ENV", "").strip().lower() in (Environment.PRODUCTION, Environment.DEMO):
        raise SystemExit(
            "Migrations outside development need MIGRATION_DATABASE_URL: the schema owner, never "
            "the app's runtime role"
        )
    return str(get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
