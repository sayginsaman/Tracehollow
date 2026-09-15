"""Celery application factory shared by the API (as producer) and the worker process.

Redis is only the broker. Task outcomes are written to PostgreSQL, which remains the
authoritative record; no Celery result backend is configured.
"""

from __future__ import annotations

from functools import cached_property

from celery import Celery
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import create_db_engine, create_session_factory

WORKER_CHECK_TASK = "tracehollow.system.worker_check"
DEFAULT_QUEUE = "tracehollow"
AI_QUEUE = "tracehollow-ai"


class TracehollowCelery(Celery):
    """Celery app carrying validated settings and a lazily created database session factory."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            "tracehollow",
            broker=settings.redis_url,
            include=["app.tasks.system", "app.tasks.queries", "app.tasks.ai"],
        )
        self.settings = settings

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        return create_session_factory(create_db_engine(self.settings))


def create_celery_app(settings: Settings) -> TracehollowCelery:
    app = TracehollowCelery(settings)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_default_queue=DEFAULT_QUEUE,
        worker_prefetch_multiplier=1,
        # Tasks are idempotent; on broker loss, cancel in-flight late-ack tasks so they are
        # redelivered (the Celery 6 default).
        worker_cancel_long_running_tasks_on_connection_loss=True,
        worker_hijack_root_logger=False,
        worker_send_task_events=False,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=3,
        broker_transport_options={
            "visibility_timeout": 3600,
            "socket_connect_timeout": 3,
            "socket_timeout": 5,
        },
        timezone="UTC",
        enable_utc=True,
    )
    return app
