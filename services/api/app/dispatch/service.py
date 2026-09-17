"""Transactional outbox: enqueue with the work, publish after commit, recover when lost."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from celery import Celery
from kombu.exceptions import OperationalError as KombuOperationalError
from redis import RedisError
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import AiMode, AiRun, AiRunStatus, EvidenceIndexState, IndexStatus
from app.cases.models import Case, CaseDeletion, CaseStatus, DeletionStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch.models import AggregateType, DispatchOutbox, OutboxStatus
from app.imports.models import ProcessingJob, ProcessingStatus
from app.queries.models import QueryRun, RunStatus
from app.tasks.celery_app import AI_QUEUE, COLLECT_QUEUE, DEFAULT_QUEUE

logger = logging.getLogger(__name__)

EXECUTE_QUERY_RUN_TASK = "tracehollow.queries.execute_run"
# Runs that contact external sources execute in the collector service, which has egress.
EXECUTE_COLLECTION_RUN_TASK = "tracehollow.queries.execute_collection_run"
EXECUTE_CASE_DELETION_TASK = "tracehollow.cases.execute_deletion"
EXECUTE_AI_RUN_TASK = "tracehollow.ai.execute_run"
INDEX_CASE_TASK = "tracehollow.ai.index_case"
CHECK_AI_PROVIDERS_TASK = "tracehollow.ai.check_providers"
# Processing of imported originals (chat exports, documents) runs in the worker service, which has
# no route to the internet.
PROCESS_IMPORT_TASK = "tracehollow.imports.process_job"
# Change detection after an execution finishes (worker, no egress).
DETECT_CHANGES_TASK = "tracehollow.changes.detect"
# Webhook deliveries leave the installation, so they run in the collector (egress + SSRF checks).
DELIVER_NOTIFICATION_TASK = "tracehollow.notifications.deliver"
# Model-backed work runs on its own queue, consumed by the ai-worker service.
AI_TASKS = frozenset({EXECUTE_AI_RUN_TASK, INDEX_CASE_TASK, CHECK_AI_PROVIDERS_TASK})
# Fixed aggregate id for the installation-wide provider check.
PROVIDER_CHECK_ID = uuid.UUID("00000000-0000-4000-8000-00000000a1c0")


def queue_for(task_name: str) -> str:
    if task_name in AI_TASKS:
        return AI_QUEUE
    if task_name in (EXECUTE_COLLECTION_RUN_TASK, DELIVER_NOTIFICATION_TASK):
        return COLLECT_QUEUE
    return DEFAULT_QUEUE


def enqueue(
    db: Session,
    *,
    task_name: str,
    aggregate_type: AggregateType,
    aggregate_id: uuid.UUID,
    case_id: uuid.UUID | None,
) -> DispatchOutbox:
    """Add (or re-arm) the outbox row inside the caller's transaction.

    A single upsert, so concurrent transactions enqueueing the same aggregate (for example two
    executions of one case marking evidence for indexing) cannot collide on the unique key.
    """
    now = utcnow()
    outbox_id = db.scalar(
        insert(DispatchOutbox)
        .values(
            id=uuid.uuid4(),
            task_name=task_name,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            case_id=case_id,
            payload={"aggregate_id": str(aggregate_id)},
            status=OutboxStatus.PENDING,
            attempts=0,
            available_at=now,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=["aggregate_type", "aggregate_id"],
            set_={"status": OutboxStatus.PENDING, "available_at": now, "done_at": None},
        )
        .returning(DispatchOutbox.id)
    )
    row = db.scalar(
        select(DispatchOutbox)
        .where(DispatchOutbox.id == outbox_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def mark_done(db: Session, aggregate_type: AggregateType, aggregate_id: uuid.UUID) -> None:
    db.execute(
        update(DispatchOutbox)
        .where(
            DispatchOutbox.aggregate_type == aggregate_type,
            DispatchOutbox.aggregate_id == aggregate_id,
        )
        .values(status=OutboxStatus.DONE, done_at=utcnow())
    )


def _redelivery_delay(settings: Settings, attempts: int) -> timedelta:
    exponent = max(0, min(attempts - 1, 16))
    seconds = min(
        settings.dispatch_redelivery_max_seconds,
        settings.dispatch_redelivery_seconds * (2**exponent),
    )
    return timedelta(seconds=seconds)


def publish(
    session_factory: sessionmaker[Session],
    celery_app: Celery,
    settings: Settings,
    outbox_id: uuid.UUID,
) -> bool:
    """Publish one pending row. Returns True when the broker accepted the message."""
    with session_scope(session_factory) as db:
        row = db.scalar(
            select(DispatchOutbox)
            .where(DispatchOutbox.id == outbox_id, DispatchOutbox.status == OutboxStatus.PENDING)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        row.attempts += 1
        try:
            celery_app.send_task(
                row.task_name,
                args=[str(row.aggregate_id)],
                queue=queue_for(row.task_name),
                retry=True,
                retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.5},
            )
        except (KombuOperationalError, RedisError, OSError) as exc:
            row.last_error_code = "broker_unavailable"
            row.available_at = utcnow() + _redelivery_delay(settings, row.attempts)
            logger.warning(
                "dispatch_publish_failed",
                extra={"aggregate_type": row.aggregate_type, "error_type": type(exc).__name__},
            )
            return False
        row.status = OutboxStatus.DISPATCHED
        row.dispatched_at = utcnow()
        row.last_error_code = None
        logger.info(
            "dispatch_published",
            extra={"aggregate_type": row.aggregate_type, "attempt": row.attempts},
        )
        return True


@dataclass(frozen=True, slots=True)
class RelayStats:
    requeued: int
    published: int
    failed: int


def requeue_stale(session_factory: sessionmaker[Session], settings: Settings) -> int:
    """Re-arm outbox rows whose message was likely lost or whose worker stopped.

    A queued aggregate that was dispatched long ago (with exponential backoff per attempt), or a
    running aggregate whose lease expired, is published again. Workers claim atomically, so a
    duplicate of a message that is merely delayed is harmless.
    """
    now = utcnow()
    requeued = 0
    with session_scope(session_factory) as db:
        rows = db.execute(
            select(DispatchOutbox, QueryRun)
            .join(QueryRun, QueryRun.id == DispatchOutbox.aggregate_id)
            .where(
                DispatchOutbox.aggregate_type == AggregateType.QUERY_RUN,
                DispatchOutbox.status.in_([OutboxStatus.DISPATCHED, OutboxStatus.DONE]),
                or_(
                    QueryRun.status == RunStatus.QUEUED,
                    and_(QueryRun.status == RunStatus.RUNNING, QueryRun.lease_expires_at < now),
                ),
            )
            .with_for_update(of=DispatchOutbox, skip_locked=True)
        ).all()
        for outbox, run in rows:
            stale = run.status == RunStatus.RUNNING or (
                outbox.dispatched_at is not None
                and outbox.dispatched_at + _redelivery_delay(settings, outbox.attempts) < now
            )
            if stale:
                outbox.status = OutboxStatus.PENDING
                outbox.available_at = now
                outbox.last_error_code = "redelivery"
                requeued += 1

        deletions = db.execute(
            select(DispatchOutbox, CaseDeletion)
            .join(CaseDeletion, CaseDeletion.id == DispatchOutbox.aggregate_id)
            .where(
                DispatchOutbox.aggregate_type == AggregateType.CASE_DELETION,
                DispatchOutbox.status.in_([OutboxStatus.DISPATCHED, OutboxStatus.DONE]),
                or_(
                    CaseDeletion.status == DeletionStatus.QUEUED,
                    and_(
                        CaseDeletion.status == DeletionStatus.RUNNING,
                        CaseDeletion.lease_expires_at < now,
                    ),
                ),
            )
            .with_for_update(of=DispatchOutbox, skip_locked=True)
        ).all()
        for outbox, deletion in deletions:
            stale = deletion.status == DeletionStatus.RUNNING or (
                outbox.dispatched_at is not None
                and outbox.dispatched_at + _redelivery_delay(settings, outbox.attempts) < now
            )
            if stale:
                outbox.status = OutboxStatus.PENDING
                outbox.available_at = now
                outbox.last_error_code = "redelivery"
                requeued += 1
        ai_runs = db.execute(
            select(DispatchOutbox, AiRun)
            .join(AiRun, AiRun.id == DispatchOutbox.aggregate_id)
            .where(
                DispatchOutbox.aggregate_type == AggregateType.AI_RUN,
                DispatchOutbox.status.in_([OutboxStatus.DISPATCHED, OutboxStatus.DONE]),
                or_(
                    AiRun.status == AiRunStatus.QUEUED,
                    and_(AiRun.status == AiRunStatus.RUNNING, AiRun.lease_expires_at < now),
                ),
            )
            .with_for_update(of=DispatchOutbox, skip_locked=True)
        ).all()
        for outbox, ai_run in ai_runs:
            stale = ai_run.status == AiRunStatus.RUNNING or (
                outbox.dispatched_at is not None
                and outbox.dispatched_at + _redelivery_delay(settings, outbox.attempts) < now
            )
            if stale:
                outbox.status = OutboxStatus.PENDING
                outbox.available_at = now
                outbox.last_error_code = "redelivery"
                requeued += 1
        jobs = db.execute(
            select(DispatchOutbox, ProcessingJob)
            .join(ProcessingJob, ProcessingJob.id == DispatchOutbox.aggregate_id)
            .where(
                DispatchOutbox.aggregate_type == AggregateType.PROCESSING_JOB,
                DispatchOutbox.status.in_([OutboxStatus.DISPATCHED, OutboxStatus.DONE]),
                or_(
                    ProcessingJob.status == ProcessingStatus.QUEUED,
                    and_(
                        ProcessingJob.status == ProcessingStatus.RUNNING,
                        ProcessingJob.lease_expires_at < now,
                    ),
                ),
            )
            .with_for_update(of=DispatchOutbox, skip_locked=True)
        ).all()
        for outbox, job in jobs:
            stale = job.status == ProcessingStatus.RUNNING or (
                outbox.dispatched_at is not None
                and outbox.dispatched_at + _redelivery_delay(settings, outbox.attempts) < now
            )
            if stale:
                outbox.status = OutboxStatus.PENDING
                outbox.available_at = now
                outbox.last_error_code = "redelivery"
                requeued += 1
    requeued += _requeue_lease_free(session_factory, settings)
    requeued += schedule_index_work(session_factory, settings)
    if requeued:
        logger.info("dispatch_requeued", extra={"count": requeued})
    return requeued


def _requeue_lease_free(session_factory: sessionmaker[Session], settings: Settings) -> int:
    """Re-arm idempotent work without its own lease (change detection, webhook deliveries).

    A row dispatched long enough ago (exponential backoff per attempt) and still not done had its
    message lost or its worker stopped. Change detection is idempotent and deliveries are claimed
    with a lease, so a delayed duplicate is harmless.
    """
    now = utcnow()
    requeued = 0
    with session_scope(session_factory) as db:
        rows = list(
            db.scalars(
                select(DispatchOutbox)
                .where(
                    DispatchOutbox.aggregate_type.in_(
                        [AggregateType.CHANGE_DETECTION, AggregateType.NOTIFICATION_DELIVERY]
                    ),
                    DispatchOutbox.status == OutboxStatus.DISPATCHED,
                )
                .limit(200)
                .with_for_update(skip_locked=True)
            )
        )
        for outbox in rows:
            if (
                outbox.dispatched_at is not None
                and outbox.dispatched_at + _redelivery_delay(settings, outbox.attempts) < now
            ):
                outbox.status = OutboxStatus.PENDING
                outbox.available_at = now
                outbox.last_error_code = "redelivery"
                requeued += 1
    return requeued


def schedule_index_work(session_factory: sessionmaker[Session], settings: Settings) -> int:
    """Make sure every case with due index work has a live outbox row.

    Covers evidence recorded while AI was disabled, migrated Phase 1 evidence, retries whose
    backoff has elapsed and index attempts whose worker disappeared.
    """
    if not settings.ai_enabled:
        return 0
    now = utcnow()
    scheduled = 0
    with session_scope(session_factory) as db:
        due = exists().where(
            EvidenceIndexState.case_id == Case.id,
            or_(
                and_(
                    EvidenceIndexState.status == IndexStatus.PENDING,
                    EvidenceIndexState.available_at <= now,
                ),
                and_(
                    EvidenceIndexState.status == IndexStatus.INDEXING,
                    EvidenceIndexState.lease_expires_at < now,
                ),
            ),
        )
        case_ids = list(
            db.scalars(
                select(Case.id)
                .where(
                    Case.status.in_([CaseStatus.ACTIVE, CaseStatus.ARCHIVED]),
                    Case.ai_mode != AiMode.DISABLED,
                    due,
                )
                .limit(200)
            )
        )
        for case_id in case_ids:
            row = db.scalar(
                select(DispatchOutbox)
                .where(
                    DispatchOutbox.aggregate_type == AggregateType.CASE_INDEX,
                    DispatchOutbox.aggregate_id == case_id,
                )
                .with_for_update(skip_locked=True)
            )
            if row is not None and row.status == OutboxStatus.PENDING:
                continue
            if (
                row is not None
                and row.status == OutboxStatus.DISPATCHED
                and row.dispatched_at is not None
                and row.dispatched_at + _redelivery_delay(settings, row.attempts) > now
            ):
                continue
            enqueue(
                db,
                task_name=INDEX_CASE_TASK,
                aggregate_type=AggregateType.CASE_INDEX,
                aggregate_id=case_id,
                case_id=case_id,
            )
            scheduled += 1
    return scheduled


def schedule_provider_check(session_factory: sessionmaker[Session], settings: Settings) -> bool:
    if not settings.ai_enabled:
        return False
    with session_scope(session_factory) as db:
        enqueue(
            db,
            task_name=CHECK_AI_PROVIDERS_TASK,
            aggregate_type=AggregateType.AI_PROVIDER_CHECK,
            aggregate_id=PROVIDER_CHECK_ID,
            case_id=None,
        )
    return True


def publish_due(
    session_factory: sessionmaker[Session], celery_app: Celery, settings: Settings, limit: int = 50
) -> tuple[int, int]:
    with session_scope(session_factory) as db:
        ids = list(
            db.scalars(
                select(DispatchOutbox.id)
                .where(
                    DispatchOutbox.status == OutboxStatus.PENDING,
                    DispatchOutbox.available_at <= utcnow(),
                )
                .order_by(DispatchOutbox.created_at)
                .limit(limit)
            )
        )
    published = failed = 0
    for outbox_id in ids:
        if publish(session_factory, celery_app, settings, outbox_id):
            published += 1
        else:
            failed += 1
    return published, failed


def relay_once(
    session_factory: sessionmaker[Session], celery_app: Celery, settings: Settings
) -> RelayStats:
    requeued = requeue_stale(session_factory, settings)
    published, failed = publish_due(session_factory, celery_app, settings)
    return RelayStats(requeued=requeued, published=published, failed=failed)


def publish_aggregate_if_pending(
    session_factory: sessionmaker[Session],
    celery_app: Celery,
    settings: Settings,
    aggregate_type: AggregateType,
    aggregate_id: uuid.UUID,
) -> bool:
    """Publish an aggregate's outbox row now if it is pending; the dispatcher covers failures."""
    with session_scope(session_factory) as db:
        outbox_id = db.scalar(
            select(DispatchOutbox.id).where(
                DispatchOutbox.aggregate_type == aggregate_type,
                DispatchOutbox.aggregate_id == aggregate_id,
                DispatchOutbox.status == OutboxStatus.PENDING,
            )
        )
    if outbox_id is None:
        return False
    return publish_after_commit(session_factory, celery_app, settings, outbox_id)


def publish_after_commit(
    session_factory: sessionmaker[Session],
    celery_app: Celery,
    settings: Settings,
    outbox_id: uuid.UUID,
) -> bool:
    """Best-effort immediate publish; the dispatcher retries anything left pending."""
    try:
        return publish(session_factory, celery_app, settings, outbox_id)
    except Exception:  # never fail the user's request after its transaction committed
        logger.exception("dispatch_publish_unexpected_error")
        return False
