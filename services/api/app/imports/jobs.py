"""Claiming, leasing, cancelling and finishing processing jobs (worker side).

The pattern matches query executions: a worker claims a job with a conditional update that also
sets a lease token; every write that records results happens in one transaction that re-checks the
token, the job status, the cancellation flag and the case. A second delivery of the same job finds
it claimed or finished and does nothing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.cases.access import has_analyst_access
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.evidence.storage import EvidenceStorage
from app.imports.models import ProcessingJob, ProcessingStatus

logger = logging.getLogger(__name__)


class LeaseLostError(Exception):
    """Another worker owns the job now, or it was finished or deleted meanwhile."""


class JobCanceledError(Exception):
    """The analyst asked to stop, the case is being deleted, or the requester lost access."""

    def __init__(self, code: str = "canceled") -> None:
        super().__init__(code)
        self.code = code


class ProcessingError(Exception):
    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable


@dataclass
class ProcessingContext:
    session_factory: sessionmaker[Session]
    storage: EvidenceStorage
    settings: Settings
    worker_name: str
    # Test hook called before the transaction that records results.
    before_write: Callable[[uuid.UUID], None] | None = field(default=None)


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    id: uuid.UUID
    case_id: uuid.UUID
    evidence_id: uuid.UUID
    job_type: str
    options: dict[str, Any]
    attempts: int
    created_by_user_id: uuid.UUID | None


def claim(ctx: ProcessingContext, job_id: uuid.UUID) -> tuple[uuid.UUID, JobSnapshot] | None:
    token = uuid.uuid4()
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        row = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.attempts < ctx.settings.processing_max_attempts,
                or_(
                    ProcessingJob.status == ProcessingStatus.QUEUED,
                    and_(
                        ProcessingJob.status == ProcessingStatus.RUNNING,
                        or_(
                            ProcessingJob.lease_expires_at.is_(None),
                            ProcessingJob.lease_expires_at < now,
                        ),
                    ),
                ),
            )
            .values(
                status=ProcessingStatus.RUNNING,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=ctx.settings.processing_lease_seconds),
                attempts=ProcessingJob.attempts + 1,
                worker_name=ctx.worker_name[:255],
                started_at=now,
                error_code=None,
                error_detail=None,
            )
            .returning(
                ProcessingJob.id,
                ProcessingJob.case_id,
                ProcessingJob.evidence_id,
                ProcessingJob.job_type,
                ProcessingJob.options,
                ProcessingJob.attempts,
                ProcessingJob.created_by_user_id,
            )
        ).first()
        if row is None:
            _fail_if_exhausted(db, ctx.settings, job_id)
            return None
        snapshot = JobSnapshot(
            id=row.id,
            case_id=row.case_id,
            evidence_id=row.evidence_id,
            job_type=row.job_type,
            options=dict(row.options or {}),
            attempts=row.attempts,
            created_by_user_id=row.created_by_user_id,
        )
    return token, snapshot


def _fail_if_exhausted(db: Session, settings: Settings, job_id: uuid.UUID) -> None:
    """A job whose workers keep disappearing is failed so the failure is visible."""
    job = db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == ProcessingStatus.RUNNING,
            ProcessingJob.lease_expires_at < utcnow(),
            ProcessingJob.attempts >= settings.processing_max_attempts,
        )
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return
    job.status = ProcessingStatus.FAILED
    job.error_code = "worker_lost"
    job.error_detail = (
        f"Processing was interrupted {job.attempts} times without finishing and was stopped. "
        "Start it again from the evidence page."
    )
    job.lease_token = None
    job.lease_expires_at = None
    job.finished_at = utcnow()
    dispatch.mark_done(db, AggregateType.PROCESSING_JOB, job.id)
    logger.error("processing_job_abandoned", extra={"job_ref": str(job_id)[:8]})


def _revoked(db: Session, job: ProcessingJob) -> bool:
    """Whether the account that started the job lost analyst access; records the stop once."""
    if has_analyst_access(db, job.created_by_user_id, job.case_id):
        return False
    if job.cancel_requested_at is None:
        job.cancel_requested_at = utcnow()
    return True


def authorization_revoked(ctx: ProcessingContext, job_id: uuid.UUID) -> bool:
    with session_scope(ctx.session_factory) as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        return job is not None and _revoked(db, job)


def renew(ctx: ProcessingContext, job_id: uuid.UUID, token: uuid.UUID) -> None:
    """Extend the lease between bounded units of work; stop when cancellation was requested."""
    with session_scope(ctx.session_factory) as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None or job.lease_token != token or job.status != ProcessingStatus.RUNNING:
            raise LeaseLostError
        if _revoked(db, job):
            db.commit()
            raise JobCanceledError("authorization_revoked")
        if job.cancel_requested_at is not None or not _case_active(db, job.case_id):
            raise JobCanceledError
        job.lease_expires_at = utcnow() + timedelta(seconds=ctx.settings.processing_lease_seconds)


def cancel_requested(ctx: ProcessingContext, job_id: uuid.UUID) -> bool:
    with session_scope(ctx.session_factory) as db:
        job = db.get(ProcessingJob, job_id)
        return (
            job is None
            or job.cancel_requested_at is not None
            or not _case_active(db, job.case_id)
            or not has_analyst_access(db, job.created_by_user_id, job.case_id)
        )


def _case_active(db: Session, case_id: uuid.UUID) -> bool:
    return db.scalar(select(Case.status).where(Case.id == case_id)) == CaseStatus.ACTIVE


@contextmanager
def guarded_write(
    ctx: ProcessingContext, job_id: uuid.UUID, token: uuid.UUID, *, allow_canceled: bool = False
) -> Iterator[tuple[Session, ProcessingJob, Case]]:
    """One transaction that records results, only while this worker still owns the job.

    ``allow_canceled`` lets a handler record work finished before a cancellation (completed pages
    are kept); otherwise a pending cancellation raises :class:`JobCanceledError`.
    """
    if ctx.before_write is not None:
        ctx.before_write(job_id)
    with session_scope(ctx.session_factory) as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None or job.lease_token != token or job.status != ProcessingStatus.RUNNING:
            raise LeaseLostError
        case = db.scalar(select(Case).where(Case.id == job.case_id).with_for_update(read=True))
        if case is None or case.status != CaseStatus.ACTIVE:
            raise JobCanceledError
        if not has_analyst_access(db, job.created_by_user_id, job.case_id):
            # Nothing is recorded for a requester who lost access, not even finished pages.
            raise JobCanceledError("authorization_revoked")
        if job.cancel_requested_at is not None and not allow_canceled:
            raise JobCanceledError
        yield db, job, case


def finish(
    db: Session,
    job: ProcessingJob,
    status: ProcessingStatus,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    needs_input: dict[str, Any] | None = None,
) -> None:
    job.status = status
    if result is not None:
        job.result = result
    job.error_code = error_code
    job.error_detail = error_detail[:2000] if error_detail else None
    job.needs_input = needs_input
    job.lease_token = None
    job.lease_expires_at = None
    if status != ProcessingStatus.NEEDS_INPUT:
        job.finished_at = utcnow()
    dispatch.mark_done(db, AggregateType.PROCESSING_JOB, job.id)


def finish_without_results(
    ctx: ProcessingContext,
    job_id: uuid.UUID,
    token: uuid.UUID,
    status: ProcessingStatus,
    *,
    error_code: str | None = None,
    error_detail: str | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    """Record a terminal state that derives nothing (failure or cancellation before results)."""
    with session_scope(ctx.session_factory) as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None or job.lease_token != token or job.status != ProcessingStatus.RUNNING:
            return "lease_lost"
        finish(db, job, status, result=result, error_code=error_code, error_detail=error_detail)
    return str(status)


def requeue_after_retryable_error(
    ctx: ProcessingContext, job_id: uuid.UUID, token: uuid.UUID, error: ProcessingError
) -> str:
    """Put a job back in the queue after a transient failure while attempts remain."""
    with session_scope(ctx.session_factory) as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None or job.lease_token != token or job.status != ProcessingStatus.RUNNING:
            return "lease_lost"
        if job.attempts >= ctx.settings.processing_max_attempts:
            finish(
                db, job, ProcessingStatus.FAILED, error_code=error.code, error_detail=error.detail
            )
            return "failed"
        job.status = ProcessingStatus.QUEUED
        job.error_code = error.code
        job.error_detail = error.detail[:2000]
        job.lease_token = None
        job.lease_expires_at = None
        dispatch.enqueue(
            db,
            task_name=dispatch.PROCESS_IMPORT_TASK,
            aggregate_type=AggregateType.PROCESSING_JOB,
            aggregate_id=job.id,
            case_id=job.case_id,
        )
    return "retried"
