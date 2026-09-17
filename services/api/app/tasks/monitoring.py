"""Celery entrypoints for change detection (worker) and webhook delivery (collector)."""

from __future__ import annotations

import uuid

from celery import Task, shared_task

from app.changes.detection import detect
from app.dispatch.service import (
    APPLY_RETENTION_TASK,
    DELIVER_NOTIFICATION_TASK,
    DETECT_CHANGES_TASK,
)
from app.notifications.delivery import DeliveryContext, deliver
from app.tasks.celery_app import TracehollowCelery


def _app(task: Task[[str], None]) -> TracehollowCelery:
    app = task.app
    assert isinstance(app, TracehollowCelery)
    return app


@shared_task(bind=True, name=DETECT_CHANGES_TASK, acks_late=True, ignore_result=True)
def detect_changes(self: Task[[str], None], run_id: str) -> None:
    app = _app(self)
    detect(app.session_factory, app.settings, uuid.UUID(run_id))


@shared_task(bind=True, name=DELIVER_NOTIFICATION_TASK, acks_late=True, ignore_result=True)
def deliver_notification(self: Task[[str], None], delivery_id: str) -> None:
    app = _app(self)
    context = DeliveryContext(
        session_factory=app.session_factory,
        settings=app.settings,
        worker_name=(self.request.hostname or "collector")[:255],
    )
    deliver(context, uuid.UUID(delivery_id))


@shared_task(bind=True, name=APPLY_RETENTION_TASK, acks_late=True, ignore_result=True)
def apply_retention(self: Task[[str], None], job_id: str) -> None:
    from app.evidence.storage import EvidenceStorage
    from app.retention.service import RetentionContext, apply_job

    app = _app(self)
    context = RetentionContext(
        session_factory=app.session_factory,
        storage=EvidenceStorage(app.settings.evidence_storage_path),
        settings=app.settings,
        worker_name=(self.request.hostname or "worker")[:255],
    )
    apply_job(context, uuid.UUID(job_id))
