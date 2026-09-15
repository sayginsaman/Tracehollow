"""Infrastructure tasks. These verify plumbing only; they never collect investigation data."""

from __future__ import annotations

import logging
import uuid

from celery import Task, shared_task
from sqlalchemy import update

from app.db.base import utcnow
from app.db.session import session_scope
from app.system.models import WorkerCheck, WorkerCheckStatus
from app.tasks.celery_app import WORKER_CHECK_TASK, TracehollowCelery

logger = logging.getLogger(__name__)


@shared_task(bind=True, name=WORKER_CHECK_TASK, acks_late=True, ignore_result=True)
def worker_check(self: Task[[str], None], check_id: str) -> None:
    """Record that a worker received the check. Idempotent under redelivery."""
    app = self.app
    assert isinstance(app, TracehollowCelery)
    with session_scope(app.session_factory) as db:
        result = db.execute(
            update(WorkerCheck)
            .where(
                WorkerCheck.id == uuid.UUID(check_id),
                WorkerCheck.status != WorkerCheckStatus.COMPLETED,
            )
            .values(
                status=WorkerCheckStatus.COMPLETED,
                completed_at=utcnow(),
                worker_hostname=(self.request.hostname or "unknown")[:255],
                error_code=None,
            )
        )
    updated = getattr(result, "rowcount", 0)
    logger.info("worker_check_processed", extra={"check_ref": check_id[:8], "updated": updated})
