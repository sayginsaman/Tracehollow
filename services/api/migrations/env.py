"""Alembic environment.

The URL comes from validated application settings (or an explicit ``database_url``
attribute supplied by tests). A PostgreSQL advisory lock serializes concurrent runs, so
starting several containers at once cannot apply the same migration twice.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import URL

from app.config import get_settings
from app.db.models import Base
from app.logging_config import configure_logging

MIGRATION_LOCK_KEY = 7_311_402_001

config = context.config
target_metadata = Base.metadata


def _database_url() -> URL | str:
    explicit: URL | str | None = config.attributes.get("database_url")
    if explicit is not None:
        return explicit
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if not config.attributes.get("skip_logging_setup"):
        configure_logging("INFO")
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            connection.commit()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
