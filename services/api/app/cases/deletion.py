"""Deliberate case deletion as an observable, retryable job.

Order of operations (each step is idempotent, so a failed attempt can simply be retried):

1. Claim the job with a lease. The case is already in ``deleting`` (set when requested).
2. Cancel queued runs and request cancellation of running runs; while a run still holds a valid
   lease the job waits and re-queues itself instead of racing the worker.
3. Remove every stored file under ``cases/<case-id>/`` on the evidence volume.
4. Delete the case row; foreign keys cascade to all case-owned records. Outbox rows for the
   case's runs are removed in the same transaction.
5. Remove the case directory again (covers files written by a racing writer) and verify that
   no file and no case-owned row remains.
6. Record the removed counts. Only the job record (without case content) is kept.

On any failure the job becomes ``failed`` with an error code, the case (if it still exists) is
marked ``deletion_failed`` and remains inaccessible, and the requester can retry.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import (
    AiCitation,
    AiConversation,
    AiMessage,
    AiRun,
    AiRunStatus,
    ChunkEmbedding,
    DocumentChunk,
    EvidenceIndexState,
    IndexStatus,
)
from app.budgets.models import BudgetLedger, BudgetReservation, CaseBudget
from app.cases.models import Case, CaseDeletion, CaseMember, CaseStatus, DeletionStatus, Note
from app.changes.models import ChangeEvent, ChangeSet
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType, DispatchOutbox
from app.entities.models import (
    AnalystDecision,
    Entity,
    EntityEvidence,
    EntityIdentifier,
    Observation,
    Relationship,
    RelationshipEvidence,
)
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.exchange.models import StixObjectLink
from app.imports.models import ProcessingJob, ProcessingStatus
from app.monitoring.models import Monitor, MonitorOccurrence, MonitorStatus
from app.notifications.models import (
    MonitorSubscription,
    Notification,
    NotificationDelivery,
)
from app.queries.models import ConnectorOutcome, ConnectorRun, QueryRun, RunStatus, SavedQuery
from app.retention.models import CaseRetentionPolicy, RetentionJob, RetentionTombstone

logger = logging.getLogger(__name__)

WAIT_FOR_RUNS_SECONDS = 5

CASE_OWNED_TABLES: tuple[tuple[str, Any], ...] = (
    ("case_members", CaseMember),
    ("notes", Note),
    ("entities", Entity),
    ("entity_identifiers", EntityIdentifier),
    ("entity_evidence", EntityEvidence),
    ("observations", Observation),
    ("relationships", Relationship),
    ("relationship_evidence", RelationshipEvidence),
    ("analyst_decisions", AnalystDecision),
    ("evidence_objects", EvidenceObject),
    ("saved_queries", SavedQuery),
    ("query_runs", QueryRun),
    ("connector_runs", ConnectorRun),
    ("document_chunks", DocumentChunk),
    ("chunk_embeddings", ChunkEmbedding),
    ("evidence_index_states", EvidenceIndexState),
    ("ai_conversations", AiConversation),
    ("ai_runs", AiRun),
    ("ai_messages", AiMessage),
    ("ai_citations", AiCitation),
    ("processing_jobs", ProcessingJob),
    ("monitors", Monitor),
    ("monitor_occurrences", MonitorOccurrence),
    ("case_budgets", CaseBudget),
    ("budget_ledgers", BudgetLedger),
    ("budget_reservations", BudgetReservation),
    ("change_sets", ChangeSet),
    ("change_events", ChangeEvent),
    ("notifications", Notification),
    ("monitor_subscriptions", MonitorSubscription),
    ("notification_deliveries", NotificationDelivery),
    ("stix_object_links", StixObjectLink),
    ("case_retention_policies", CaseRetentionPolicy),
    ("retention_jobs", RetentionJob),
    ("retention_tombstones", RetentionTombstone),
)


@dataclass
class DeletionContext:
    session_factory: sessionmaker[Session]
    storage: EvidenceStorage
    settings: Settings
    worker_name: str
    # Test hook to inject failures before files are removed.
    before_file_removal: Callable[[uuid.UUID], None] | None = field(default=None)


def count_case_rows(db: Session, case_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model in CASE_OWNED_TABLES:
        counts[name] = int(
            db.scalar(select(func.count()).select_from(model).where(model.case_id == case_id)) or 0
        )
    counts["cases"] = int(
        db.scalar(select(func.count()).select_from(Case).where(Case.id == case_id)) or 0
    )
    return counts


def _claim(ctx: DeletionContext, deletion_id: uuid.UUID) -> CaseDeletion | None:
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        claimed = db.execute(
            update(CaseDeletion)
            .where(
                CaseDeletion.id == deletion_id,
                or_(
                    CaseDeletion.status == DeletionStatus.QUEUED,
                    and_(
                        CaseDeletion.status == DeletionStatus.RUNNING,
                        CaseDeletion.lease_expires_at < now,
                    ),
                ),
            )
            .values(
                status=DeletionStatus.RUNNING,
                attempts=CaseDeletion.attempts + 1,
                started_at=func.coalesce(CaseDeletion.started_at, now),
                lease_expires_at=now + timedelta(seconds=ctx.settings.run_lease_seconds),
                error_code=None,
                progress_note="Started",
            )
            .returning(CaseDeletion)
        ).scalar_one_or_none()
        if claimed is not None:
            db.expunge(claimed)
        return claimed


def _set(ctx: DeletionContext, deletion_id: uuid.UUID, **values: object) -> None:
    with session_scope(ctx.session_factory) as db:
        db.execute(update(CaseDeletion).where(CaseDeletion.id == deletion_id).values(**values))


def _stop_runs(db: Session, case_id: uuid.UUID) -> int:
    """Cancel queued runs and request cancellation of running ones. Returns active leases."""
    now = utcnow()
    db.execute(
        update(QueryRun)
        .where(
            QueryRun.case_id == case_id,
            QueryRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
            QueryRun.cancel_requested_at.is_(None),
        )
        .values(cancel_requested_at=now)
    )
    queued = list(
        db.scalars(
            select(QueryRun.id).where(
                QueryRun.case_id == case_id, QueryRun.status == RunStatus.QUEUED
            )
        )
    )
    if queued:
        db.execute(
            update(QueryRun)
            .where(QueryRun.id.in_(queued))
            .values(status=RunStatus.CANCELED, finished_at=now)
        )
        db.execute(
            update(ConnectorRun)
            .where(ConnectorRun.query_run_id.in_(queued))
            .values(status=RunStatus.CANCELED, outcome=ConnectorOutcome.CANCELED, finished_at=now)
        )
    running_queries = int(
        db.scalar(
            select(func.count())
            .select_from(QueryRun)
            .where(
                QueryRun.case_id == case_id,
                QueryRun.status == RunStatus.RUNNING,
                QueryRun.lease_expires_at > now,
            )
        )
        or 0
    )
    return running_queries + _stop_ai_work(db, case_id, now) + _stop_processing(db, case_id, now)


def _stop_processing(db: Session, case_id: uuid.UUID, now: datetime) -> int:
    """Cancel waiting processing jobs; count jobs still holding a lease."""
    db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.case_id == case_id,
            ProcessingJob.status.in_([ProcessingStatus.QUEUED, ProcessingStatus.NEEDS_INPUT]),
        )
        .values(
            status=ProcessingStatus.CANCELED,
            finished_at=now,
            error_code="case_unavailable",
            needs_input=None,
        )
    )
    db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.case_id == case_id, ProcessingJob.status == ProcessingStatus.RUNNING)
        .values(cancel_requested_at=func.coalesce(ProcessingJob.cancel_requested_at, now))
    )
    running = db.scalar(
        select(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.case_id == case_id,
            ProcessingJob.status == ProcessingStatus.RUNNING,
            ProcessingJob.lease_expires_at > now,
        )
    )
    return int(running or 0)


def _stop_ai_work(db: Session, case_id: uuid.UUID, now: datetime) -> int:
    """Cancel queued AI runs and pending indexing; count AI work still holding a lease."""
    db.execute(
        update(AiRun)
        .where(AiRun.case_id == case_id, AiRun.status == AiRunStatus.QUEUED)
        .values(status=AiRunStatus.CANCELED, finished_at=now, error_code="case_unavailable")
    )
    db.execute(
        update(AiRun)
        .where(AiRun.case_id == case_id, AiRun.status == AiRunStatus.RUNNING)
        .values(cancel_requested_at=func.coalesce(AiRun.cancel_requested_at, now))
    )
    db.execute(
        update(EvidenceIndexState)
        .where(
            EvidenceIndexState.case_id == case_id,
            EvidenceIndexState.status.in_([IndexStatus.PENDING, IndexStatus.INDEXING]),
        )
        .values(cancel_requested_at=func.coalesce(EvidenceIndexState.cancel_requested_at, now))
    )
    running_ai = db.scalar(
        select(func.count())
        .select_from(AiRun)
        .where(
            AiRun.case_id == case_id,
            AiRun.status == AiRunStatus.RUNNING,
            AiRun.lease_expires_at > now,
        )
    )
    indexing = db.scalar(
        select(func.count())
        .select_from(EvidenceIndexState)
        .where(
            EvidenceIndexState.case_id == case_id,
            EvidenceIndexState.status == IndexStatus.INDEXING,
            EvidenceIndexState.lease_expires_at > now,
        )
    )
    return int(running_ai or 0) + int(indexing or 0)


def execute_deletion(ctx: DeletionContext, deletion_id: uuid.UUID) -> str:
    job = _claim(ctx, deletion_id)
    if job is None:
        return "skipped"
    case_id = job.case_id
    step = "cancel_runs"
    try:
        with session_scope(ctx.session_factory) as db:
            case = db.scalar(select(Case).where(Case.id == case_id).with_for_update())
            if case is not None:
                case.status = CaseStatus.DELETING
                # No new occurrence may be scheduled for a case being deleted.
                db.execute(
                    update(Monitor)
                    .where(Monitor.case_id == case_id, Monitor.status != MonitorStatus.DISABLED)
                    .values(
                        status=MonitorStatus.DISABLED,
                        status_reason="case_deleting",
                        next_run_at=None,
                    )
                )
                active = _stop_runs(db, case_id)
                counts = count_case_rows(db, case_id) if active == 0 else {}
            else:
                active = 0
                counts = dict(job.removed_counts)
        if active:
            with session_scope(ctx.session_factory) as db:
                db.execute(
                    update(CaseDeletion)
                    .where(CaseDeletion.id == deletion_id)
                    .values(
                        status=DeletionStatus.QUEUED,
                        lease_expires_at=None,
                        progress_note=f"Waiting for {active} running execution(s) to stop",
                    )
                )
                outbox = dispatch.enqueue(
                    db,
                    task_name=dispatch.EXECUTE_CASE_DELETION_TASK,
                    aggregate_type=AggregateType.CASE_DELETION,
                    aggregate_id=deletion_id,
                    case_id=None,
                )
                outbox.available_at = utcnow() + timedelta(seconds=WAIT_FOR_RUNS_SECONDS)
            return "waiting"

        step = "remove_files"
        _set(
            ctx,
            deletion_id,
            progress_note="Removing evidence files",
            lease_expires_at=utcnow() + timedelta(seconds=ctx.settings.run_lease_seconds),
        )
        if ctx.before_file_removal is not None:
            ctx.before_file_removal(case_id)
        files_removed = ctx.storage.delete_case_files(case_id)

        step = "delete_records"
        _set(ctx, deletion_id, progress_note="Deleting database records")
        with session_scope(ctx.session_factory) as db:
            run_ids = select(QueryRun.id).where(QueryRun.case_id == case_id).scalar_subquery()
            db.execute(
                delete(DispatchOutbox).where(
                    DispatchOutbox.aggregate_type == AggregateType.QUERY_RUN,
                    DispatchOutbox.aggregate_id.in_(run_ids),
                )
            )
            db.execute(
                delete(DispatchOutbox).where(
                    DispatchOutbox.case_id == case_id,
                    DispatchOutbox.aggregate_type.in_(
                        [
                            AggregateType.AI_RUN,
                            AggregateType.CASE_INDEX,
                            AggregateType.PROCESSING_JOB,
                            AggregateType.CHANGE_DETECTION,
                            AggregateType.NOTIFICATION_DELIVERY,
                            AggregateType.RETENTION_JOB,
                        ]
                    ),
                )
            )
            db.execute(delete(Case).where(Case.id == case_id))

        step = "verify"
        files_removed += ctx.storage.delete_case_files(case_id)
        with session_scope(ctx.session_factory) as db:
            remaining = {name: n for name, n in count_case_rows(db, case_id).items() if n}
        if remaining or ctx.storage.case_files_exist(case_id):
            raise RuntimeError("verification_failed")

        counts = {**counts, "evidence_files": files_removed}
        _set(
            ctx,
            deletion_id,
            status=DeletionStatus.COMPLETED,
            finished_at=utcnow(),
            lease_expires_at=None,
            progress_note="Case records and evidence files removed",
            removed_counts=counts,
        )
        with session_scope(ctx.session_factory) as db:
            dispatch.mark_done(db, AggregateType.CASE_DELETION, deletion_id)
        logger.info("case_deletion_completed", extra={"deletion_ref": str(deletion_id)[:8]})
        return "completed"
    except Exception as exc:
        code = f"{step}_failed"
        logger.error(
            "case_deletion_failed",
            extra={
                "deletion_ref": str(deletion_id)[:8],
                "step": step,
                "error_type": type(exc).__name__,
            },
        )
        with session_scope(ctx.session_factory) as db:
            db.execute(
                update(CaseDeletion)
                .where(CaseDeletion.id == deletion_id)
                .values(
                    status=DeletionStatus.FAILED,
                    error_code=code,
                    lease_expires_at=None,
                    progress_note=f"Failed during {step.replace('_', ' ')}; retry is possible",
                )
            )
            db.execute(
                update(Case).where(Case.id == case_id).values(status=CaseStatus.DELETION_FAILED)
            )
            dispatch.mark_done(db, AggregateType.CASE_DELETION, deletion_id)
        return "failed"
