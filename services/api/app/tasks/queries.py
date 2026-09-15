"""Celery entrypoints for query execution and case deletion (thin wrappers; logic lives in the
domain modules so it can be tested without a broker)."""

from __future__ import annotations

import uuid

from celery import Task, shared_task

from app.cases.deletion import DeletionContext, execute_deletion
from app.dispatch.service import EXECUTE_CASE_DELETION_TASK, EXECUTE_QUERY_RUN_TASK
from app.evidence.storage import EvidenceStorage
from app.queries.execution import ExecutionContext, execute_run
from app.tasks.celery_app import TracehollowCelery


def _app(task: Task[[str], None]) -> TracehollowCelery:
    app = task.app
    assert isinstance(app, TracehollowCelery)
    return app


@shared_task(bind=True, name=EXECUTE_QUERY_RUN_TASK, acks_late=True, ignore_result=True)
def execute_query_run(self: Task[[str], None], run_id: str) -> None:
    app = _app(self)
    context = ExecutionContext(
        session_factory=app.session_factory,
        storage=EvidenceStorage(app.settings.evidence_storage_path),
        settings=app.settings,
        worker_name=(self.request.hostname or "worker")[:255],
    )
    execute_run(context, uuid.UUID(run_id))


@shared_task(bind=True, name=EXECUTE_CASE_DELETION_TASK, acks_late=True, ignore_result=True)
def execute_case_deletion(self: Task[[str], None], deletion_id: str) -> None:
    app = _app(self)
    context = DeletionContext(
        session_factory=app.session_factory,
        storage=EvidenceStorage(app.settings.evidence_storage_path),
        settings=app.settings,
        worker_name=(self.request.hostname or "worker")[:255],
    )
    execute_deletion(context, uuid.UUID(deletion_id))
