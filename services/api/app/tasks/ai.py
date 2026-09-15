"""Celery entrypoints for AI work, consumed only by the ai-worker service.

Tasks use the ``tracehollow-ai`` queue.

Thin wrappers: all logic lives in ``app.ai`` so it can be tested without a broker.
"""

from __future__ import annotations

import uuid

from celery import Task, shared_task

from app.ai.indexing import IndexContext, index_case
from app.ai.policy import build_providers
from app.ai.provider_status import check_providers
from app.ai.runs import AiRunContext, execute_ai_run
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.evidence.storage import EvidenceStorage
from app.tasks.celery_app import TracehollowCelery


def _app(task: Task[[str], None]) -> TracehollowCelery:
    app = task.app
    assert isinstance(app, TracehollowCelery)
    return app


@shared_task(bind=True, name=dispatch.EXECUTE_AI_RUN_TASK, acks_late=True, ignore_result=True)
def execute_ai_run_task(self: Task[[str], None], run_id: str) -> None:
    app = _app(self)
    context = AiRunContext(
        session_factory=app.session_factory,
        storage=EvidenceStorage(app.settings.evidence_storage_path),
        settings=app.settings,
        providers=build_providers(app.settings),
        worker_name=(self.request.hostname or "ai-worker")[:255],
    )
    execute_ai_run(context, uuid.UUID(run_id))


@shared_task(bind=True, name=dispatch.INDEX_CASE_TASK, acks_late=True, ignore_result=True)
def index_case_task(self: Task[[str], None], case_id: str) -> None:
    app = _app(self)
    context = IndexContext(
        session_factory=app.session_factory,
        storage=EvidenceStorage(app.settings.evidence_storage_path),
        settings=app.settings,
        providers=build_providers(app.settings),
        worker_name=(self.request.hostname or "ai-worker")[:255],
    )
    index_case(context, uuid.UUID(case_id))


@shared_task(bind=True, name=dispatch.CHECK_AI_PROVIDERS_TASK, acks_late=True, ignore_result=True)
def check_providers_task(self: Task[[str], None], _check_id: str) -> None:
    app = _app(self)
    check_providers(app.session_factory, app.settings, build_providers(app.settings))
    with session_scope(app.session_factory) as db:
        dispatch.mark_done(db, AggregateType.AI_PROVIDER_CHECK, dispatch.PROVIDER_CHECK_ID)
