"""Worker entrypoint: ``celery -A app.tasks.worker worker``."""

from __future__ import annotations

from typing import Any

from celery.signals import setup_logging

from app.config import get_settings
from app.logging_config import configure_logging
from app.tasks.celery_app import create_celery_app

settings = get_settings()
celery_app = create_celery_app(settings)


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    configure_logging(settings.log_level)
