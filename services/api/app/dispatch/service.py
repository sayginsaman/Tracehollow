"""Transactional outbox: enqueue with the work, publish after commit, recover when lost."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from celery import Celery
from kombu.exceptions import OperationalError as KombuOperationalError
from redis import RedisError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.cases.models import CaseDeletion, DeletionStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch.models import AggregateType, DispatchOutbox, OutboxStatus
from app.queries.models import QueryRun, RunStatus
from app.tasks.celery_app import DEFAULT_QUEUE

logger = logging.getLogger(__name__)

EXECUTE_QUERY_RUN_TASK = "tracehollow.queries.execute_run"
EXECUTE_CASE_DELETION_TASK = "tracehollow.cases.execute_deletion"


def enqueue(
    db: Session,
    *,
    task_name: str,
    aggregate_type: AggregateType,
    aggregate_id: uuid.UUID,
    case_id: uuid.UUID | None,
) -> DispatchOutbox:
    """Add (or re-arm) the outbox row inside the caller's transaction."""
    row = db.scalar(
        select(DispatchOutbox)
        .where(
            DispatchOutbox.aggregate_type == aggregate_type,
            DispatchOutbox.aggregate_id == aggregate_id,
        )
        .with_for_update()
    )
    now = utcnow()
    if row is None:
        row = DispatchOutbox(
            task_name=task_name,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            case_id=case_id,
            payload={"aggregate_id": str(aggregate_id)},
            status=OutboxStatus.PENDING,
            available_at=now,
        )
        db.add(row)
    else:
        row.status = OutboxStatus.PENDING
        row.available_at = now
        row.done_at = None
    db.flush()
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
                queue=DEFAULT_QUEUE,
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
    if requeued:
        logger.info("dispatch_requeued", extra={"count": requeued})
    return requeued


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
