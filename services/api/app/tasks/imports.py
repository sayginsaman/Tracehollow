"""Celery entrypoint for processing imported originals.

Uses the default ``tracehollow`` queue, consumed by the worker service, which has no route to the
internet: parsers and OCR never fetch external resources.
"""

from __future__ import annotations

import uuid

from celery import Task, shared_task

from app.dispatch import service as dispatch
from app.evidence.storage import EvidenceStorage
from app.imports.jobs import ProcessingContext
from app.imports.runner import execute_job
from app.tasks.celery_app import TracehollowCelery


@shared_task(bind=True, name=dispatch.PROCESS_IMPORT_TASK, acks_late=True, ignore_result=True)
def process_import_job(self: Task[[str], None], job_id: str) -> None:
    app = self.app
    assert isinstance(app, TracehollowCelery)
    context = ProcessingContext(
        session_factory=app.session_factory,
        storage=EvidenceStorage(app.settings.evidence_storage_path),
        settings=app.settings,
        worker_name=(self.request.hostname or "worker")[:255],
    )
    execute_job(context, uuid.UUID(job_id))
