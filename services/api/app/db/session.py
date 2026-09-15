from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_db_engine(settings: Settings, url: URL | None = None) -> Engine:
    timeout = settings.database_connect_timeout_seconds
    return create_engine(
        url or settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=timeout,
        connect_args={
            "connect_timeout": timeout,
            "application_name": "tracehollow",
            # Bound individual statements so a stuck query cannot hold a request forever.
            "options": "-c statement_timeout=30000",
        },
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
