"""Dispatcher service: ``python -m app.dispatch.relay``.

Publishes outbox rows that the API could not publish, re-queues work whose broker message was
lost or whose worker stopped, and periodically reconciles interrupted evidence writes.
A heartbeat file lets the container healthcheck detect a stalled loop.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType

from sqlalchemy.exc import SQLAlchemyError

from app.config import ConfigurationError, get_settings
from app.db.session import create_db_engine, create_session_factory
from app.dispatch.service import relay_once, schedule_provider_check
from app.evidence.reconcile import reconcile
from app.evidence.storage import EvidenceStorage
from app.logging_config import configure_logging
from app.tasks.celery_app import create_celery_app

logger = logging.getLogger("tracehollow.dispatcher")

HEARTBEAT_PATH = Path("/tmp/tracehollow-dispatcher.heartbeat")  # noqa: S108 - tmpfs in container
RECONCILE_INTERVAL_SECONDS = 3600
PROVIDER_CHECK_INTERVAL_SECONDS = 600


class _Stop:
    requested = False


def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
    _Stop.requested = True


def main() -> int:
    configure_logging("INFO")
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        logger.critical("configuration_invalid", extra={"error": str(exc)})
        return 2
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    session_factory = create_session_factory(create_db_engine(settings))
    celery_app = create_celery_app(settings)
    storage = EvidenceStorage(settings.evidence_storage_path)
    next_reconcile = time.monotonic() + 30
    next_provider_check = time.monotonic() + 5
    logger.info("dispatcher_started", extra={"poll_seconds": settings.dispatch_poll_seconds})

    while not _Stop.requested:
        try:
            stats = relay_once(session_factory, celery_app, settings)
            if stats.requeued or stats.published or stats.failed:
                logger.info(
                    "dispatcher_cycle",
                    extra={
                        "requeued": stats.requeued,
                        "published": stats.published,
                        "failed": stats.failed,
                    },
                )
            if time.monotonic() >= next_reconcile:
                reconcile(
                    session_factory,
                    storage,
                    grace_seconds=settings.evidence_orphan_grace_seconds,
                    apply=True,
                    verify_hashes=False,
                )
                next_reconcile = time.monotonic() + RECONCILE_INTERVAL_SECONDS
            if time.monotonic() >= next_provider_check:
                schedule_provider_check(session_factory, settings)
                next_provider_check = time.monotonic() + PROVIDER_CHECK_INTERVAL_SECONDS
            HEARTBEAT_PATH.touch()
        except SQLAlchemyError as exc:
            logger.warning(
                "dispatcher_database_unavailable", extra={"error_type": type(exc).__name__}
            )
        except Exception:
            logger.exception("dispatcher_cycle_failed")
        time.sleep(settings.dispatch_poll_seconds)
    logger.info("dispatcher_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
