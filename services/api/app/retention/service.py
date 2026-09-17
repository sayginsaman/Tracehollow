"""Retention: preview, activation and the durable cleanup job (docs/operations/retention.md).

Rules come from the case policy (collected results older than N days, authorized imports older
than N days) and from each monitor's retention settings (keep the last K executions, drop
executions older than N days). Removal reuses the evidence deletion order: stored files first,
then rows, with cascades removing observations, derived text, OCR records, chunks, embeddings,
entity links and relationship references. Execution records stay, marked with the time their
results expired; AI citations keep their verdict and say that their source expired; tombstones
keep a content-free trace for references such as change events.

Protected from removal:

* the latest collection that produced data for each saved query and connector (the change
  detection baseline);
* executions that are not finished, and evidence an AI index or processing job is working on;
* everything while the case is being deleted (the deletion job removes it anyway).

A job that meets active AI runs or processing jobs in the case waits and retries instead of racing
them. Deletion does not reach backups, exports or anything already delivered outside.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import (
    AiCitation,
    AiRun,
    AiRunStatus,
    DocumentChunk,
    EvidenceIndexState,
    IndexStatus,
)
from app.audit.service import record, service_actor
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.entities.models import Observation, RelationshipEvidence
from app.entities.observation_diff import COMPLETE_OUTCOMES, COMPLETE_STOP
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.imports.models import ProcessingJob, ProcessingStatus
from app.monitoring.models import Monitor
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.queries.models import TERMINAL_RUN_STATUSES, ConnectorRun, QueryRun
from app.retention.models import (
    CaseRetentionPolicy,
    RetentionJob,
    RetentionJobStatus,
    RetentionTombstone,
)

logger = logging.getLogger(__name__)

RUN_BATCH = 20
WAIT_FOR_ACTIVE_WORK_SECONDS = 60
LEASE_SECONDS = 600
RETAINER = "retention"


@dataclass
class Rules:
    collected_results_max_age_days: int | None = None
    imported_evidence_max_age_days: int | None = None

    @property
    def empty(self) -> bool:
        return (
            self.collected_results_max_age_days is None
            and self.imported_evidence_max_age_days is None
        )


@dataclass
class Plan:
    runs: dict[uuid.UUID, str] = field(default_factory=dict)
    imports: dict[uuid.UUID, str] = field(default_factory=dict)
    protected_baselines: int = 0
    deferred: Counter[str] = field(default_factory=Counter)
    oldest: datetime | None = None
    newest: datetime | None = None

    def note(self, moment: datetime | None) -> None:
        if moment is None:
            return
        self.oldest = moment if self.oldest is None else min(self.oldest, moment)
        self.newest = moment if self.newest is None else max(self.newest, moment)


def _baseline_runs(db: Session, case_id: uuid.UUID) -> set[uuid.UUID]:
    """Runs kept as comparison baselines, per saved query and connector.

    The latest run with data and the latest run with complete coverage: change detection prefers
    the latter (app/changes/detection.py), so neither is removed while newer partial runs exist.
    """
    complete = and_(
        ConnectorRun.outcome.in_([str(outcome) for outcome in COMPLETE_OUTCOMES]),
        ConnectorRun.coverage["stopped_reason"].astext == COMPLETE_STOP,
    )
    kept: set[uuid.UUID] = set()
    for extra in (None, complete):
        conditions = [
            ConnectorRun.case_id == case_id,
            ConnectorRun.pages_completed > 0,
            QueryRun.status.in_(TERMINAL_RUN_STATUSES),
            QueryRun.results_expired_at.is_(None),
        ]
        if extra is not None:
            conditions.append(extra)
        ranked = (
            select(
                ConnectorRun.query_run_id,
                func.row_number()
                .over(
                    partition_by=(QueryRun.saved_query_id, ConnectorRun.connector_id),
                    order_by=QueryRun.queued_at.desc(),
                )
                .label("rank"),
            )
            .join(QueryRun, QueryRun.id == ConnectorRun.query_run_id)
            .where(*conditions)
            .subquery()
        )
        kept.update(db.scalars(select(ranked.c.query_run_id).where(ranked.c.rank == 1)))
    return kept


def plan(db: Session, case_id: uuid.UUID, rules: Rules, *, now: datetime | None = None) -> Plan:
    now = now or utcnow()
    result = Plan()
    baselines = _baseline_runs(db, case_id)
    candidates: dict[uuid.UUID, str] = {}
    if rules.collected_results_max_age_days is not None:
        cutoff = now - timedelta(days=rules.collected_results_max_age_days)
        for run_id in db.scalars(
            select(QueryRun.id).where(
                QueryRun.case_id == case_id,
                QueryRun.status.in_(TERMINAL_RUN_STATUSES),
                QueryRun.results_expired_at.is_(None),
                QueryRun.finished_at < cutoff,
            )
        ):
            candidates.setdefault(run_id, "collected_results_max_age")
    for monitor in db.scalars(select(Monitor).where(Monitor.case_id == case_id)):
        keep_last = (monitor.retention or {}).get("keep_last_runs")
        max_age = (monitor.retention or {}).get("max_age_days")
        runs = list(
            db.execute(
                select(QueryRun.id, QueryRun.finished_at)
                .where(
                    QueryRun.monitor_id == monitor.id,
                    QueryRun.status.in_(TERMINAL_RUN_STATUSES),
                    QueryRun.results_expired_at.is_(None),
                )
                .order_by(QueryRun.queued_at.desc())
            )
        )
        for index, (run_id, finished_at) in enumerate(runs):
            if keep_last is not None and index >= int(keep_last):
                candidates.setdefault(run_id, "monitor_keep_last_runs")
            elif (
                max_age is not None
                and finished_at is not None
                and finished_at < now - timedelta(days=int(max_age))
            ):
                candidates.setdefault(run_id, "monitor_max_age")
    for run_id, rule in candidates.items():
        if run_id in baselines:
            result.protected_baselines += 1
            continue
        result.runs[run_id] = rule
    if result.runs:
        for finished_at in db.scalars(
            select(QueryRun.finished_at).where(QueryRun.id.in_(result.runs))
        ):
            result.note(finished_at)
    if rules.imported_evidence_max_age_days is not None:
        cutoff = now - timedelta(days=rules.imported_evidence_max_age_days)
        busy = select(ProcessingJob.evidence_id).where(
            ProcessingJob.case_id == case_id,
            ProcessingJob.status.in_(
                [ProcessingStatus.QUEUED, ProcessingStatus.RUNNING, ProcessingStatus.NEEDS_INPUT]
            ),
        )
        for evidence_id, collected_at, is_busy in db.execute(
            select(
                EvidenceObject.id,
                EvidenceObject.collected_at,
                EvidenceObject.id.in_(busy),
            ).where(
                EvidenceObject.case_id == case_id,
                EvidenceObject.acquisition_method == AcquisitionMethod.AUTHORIZED_IMPORT,
                EvidenceObject.derived_from_evidence_id.is_(None),
                EvidenceObject.processing_job_id.is_(None),
                EvidenceObject.collected_at < cutoff,
            )
        ):
            if is_busy:
                result.deferred["import_being_processed"] += 1
                continue
            result.imports[evidence_id] = "imported_evidence_max_age"
            result.note(collected_at)
    return result


def _evidence_ids(db: Session, case_id: uuid.UUID, plan_: Plan) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    if plan_.runs:
        ids += list(
            db.scalars(
                select(EvidenceObject.id).where(
                    EvidenceObject.case_id == case_id, EvidenceObject.query_run_id.in_(plan_.runs)
                )
            )
        )
    frontier = list(plan_.imports)
    seen = set(frontier)
    ids += frontier
    while frontier:
        children = list(
            db.scalars(
                select(EvidenceObject.id).where(
                    EvidenceObject.case_id == case_id,
                    EvidenceObject.derived_from_evidence_id.in_(frontier),
                    EvidenceObject.processing_job_id.is_not(None),
                )
            )
        )
        frontier = [child for child in children if child not in seen]
        seen.update(frontier)
        ids += frontier
    return ids


def preview(
    db: Session, case_id: uuid.UUID, rules: Rules, *, now: datetime | None = None
) -> dict[str, Any]:
    """Counts of what applying ``rules`` now would remove, without removing anything."""
    result = plan(db, case_id, rules, now=now)
    evidence_ids = _evidence_ids(db, case_id, result)

    def count(model: Any, *conditions: Any) -> int:
        if not evidence_ids:
            return 0
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)

    size = (
        int(
            db.scalar(
                select(func.coalesce(func.sum(EvidenceObject.size_bytes), 0)).where(
                    EvidenceObject.id.in_(evidence_ids)
                )
            )
            or 0
        )
        if evidence_ids
        else 0
    )
    return {
        "executions": len(result.runs),
        "imported_originals": len(result.imports),
        "evidence_records": len(evidence_ids),
        "stored_bytes": size,
        "observations": count(Observation, Observation.evidence_id.in_(evidence_ids)),
        "relationship_references": count(
            RelationshipEvidence, RelationshipEvidence.evidence_id.in_(evidence_ids)
        ),
        "index_chunks": count(DocumentChunk, DocumentChunk.evidence_id.in_(evidence_ids)),
        "ai_citations_affected": count(AiCitation, AiCitation.evidence_id.in_(evidence_ids)),
        "protected_baselines": result.protected_baselines,
        "deferred": dict(result.deferred),
        "oldest": result.oldest.isoformat() if result.oldest else None,
        "newest": result.newest.isoformat() if result.newest else None,
        "not_removed": [
            "Execution records, saved queries, monitors and change sets (their evidence links "
            "show the expiry).",
            "Entities and relationships themselves (only their references to expired evidence).",
            "Backups, downloaded exports and reports, and notifications already delivered.",
        ],
    }


# -- jobs --------------------------------------------------------------------------------------


def enqueue_job(
    db: Session,
    case_id: uuid.UUID,
    *,
    trigger: str,
    policy_version: int,
    requested_by: uuid.UUID | None,
    occurrence_key: str | None = None,
) -> RetentionJob | None:
    from sqlalchemy.dialects.postgresql import insert

    job_id = uuid.uuid4()
    inserted = db.scalar(
        insert(RetentionJob)
        .values(
            id=job_id,
            case_id=case_id,
            trigger=trigger,
            occurrence_key=occurrence_key,
            status=RetentionJobStatus.QUEUED,
            policy_version=policy_version,
            requested_by_user_id=requested_by,
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["occurrence_key"])
        .returning(RetentionJob.id)
    )
    if inserted is None:
        return None
    dispatch.enqueue(
        db,
        task_name=dispatch.APPLY_RETENTION_TASK,
        aggregate_type=AggregateType.RETENTION_JOB,
        aggregate_id=job_id,
        case_id=case_id,
    )
    return db.get(RetentionJob, job_id)


def schedule_daily(session_factory: sessionmaker[Session], *, now: datetime | None = None) -> int:
    """One scheduled job per active policy per UTC day (dispatcher)."""
    now = now or utcnow()
    scheduled = 0
    with session_scope(session_factory) as db:
        for policy in db.scalars(
            select(CaseRetentionPolicy)
            .join(Case, Case.id == CaseRetentionPolicy.case_id)
            .where(
                CaseRetentionPolicy.active.is_(True),
                Case.status.in_([CaseStatus.ACTIVE, CaseStatus.ARCHIVED]),
            )
        ):
            job = enqueue_job(
                db,
                policy.case_id,
                trigger="scheduled",
                policy_version=policy.version,
                requested_by=None,
                occurrence_key=f"{policy.case_id}:{now.date().isoformat()}",
            )
            scheduled += int(job is not None)
    return scheduled


@dataclass
class RetentionContext:
    session_factory: sessionmaker[Session]
    storage: EvidenceStorage
    settings: Settings
    worker_name: str


def _claim(ctx: RetentionContext, job_id: uuid.UUID) -> bool:
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        claimed = db.execute(
            update(RetentionJob)
            .where(
                RetentionJob.id == job_id,
                (RetentionJob.status == RetentionJobStatus.QUEUED)
                | and_(
                    RetentionJob.status == RetentionJobStatus.RUNNING,
                    RetentionJob.lease_expires_at < now,
                ),
            )
            .values(
                status=RetentionJobStatus.RUNNING,
                attempts=RetentionJob.attempts + 1,
                started_at=func.coalesce(RetentionJob.started_at, now),
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                progress_note="Started",
            )
            .returning(RetentionJob.id)
        ).first()
    return claimed is not None


def _active_work(db: Session, case_id: uuid.UUID) -> Counter[str]:
    now = utcnow()
    active: Counter[str] = Counter()
    active["ai_runs"] = int(
        db.scalar(
            select(func.count())
            .select_from(AiRun)
            .where(
                AiRun.case_id == case_id,
                AiRun.status == AiRunStatus.RUNNING,
                AiRun.lease_expires_at > now,
            )
        )
        or 0
    )
    active["processing_jobs"] = int(
        db.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(
                ProcessingJob.case_id == case_id,
                ProcessingJob.status == ProcessingStatus.RUNNING,
                ProcessingJob.lease_expires_at > now,
            )
        )
        or 0
    )
    active["indexing"] = int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceIndexState)
            .where(
                EvidenceIndexState.case_id == case_id,
                EvidenceIndexState.status == IndexStatus.INDEXING,
                EvidenceIndexState.lease_expires_at > now,
            )
        )
        or 0
    )
    return +active


def _remove_evidence(
    db: Session,
    storage: EvidenceStorage,
    case_id: uuid.UUID,
    evidence: list[EvidenceObject],
    *,
    job_id: uuid.UUID,
    policy_version: int,
    rule: str,
    removed: Counter[str],
) -> None:
    now = utcnow()
    ids = [row.id for row in evidence]
    if not ids:
        return
    for relationship_id, reference_id in db.execute(
        select(RelationshipEvidence.relationship_id, RelationshipEvidence.id).where(
            RelationshipEvidence.case_id == case_id, RelationshipEvidence.evidence_id.in_(ids)
        )
    ):
        db.add(
            RetentionTombstone(
                case_id=case_id,
                record_type="relationship_reference",
                record_id=reference_id,
                parent_id=relationship_id,
                retention_job_id=job_id,
                policy_version=policy_version,
                rule=rule,
                expired_at=now,
            )
        )
        removed["relationship_references"] += 1
    removed["observations"] += int(
        db.scalar(
            select(func.count()).select_from(Observation).where(Observation.evidence_id.in_(ids))
        )
        or 0
    )
    removed["index_chunks"] += int(
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.evidence_id.in_(ids))
        )
        or 0
    )
    citations = db.execute(
        update(AiCitation)
        .where(AiCitation.case_id == case_id, AiCitation.evidence_id.in_(ids))
        .values(
            quote=None,
            source_char_start=None,
            source_char_end=None,
            json_pointer=None,
            source_removed_reason="retention",
            source_removed_at=now,
        )
    )
    removed["ai_citations_marked"] += int(getattr(citations, "rowcount", 0) or 0)
    for row in evidence:
        db.add(
            RetentionTombstone(
                case_id=case_id,
                record_type="evidence",
                record_id=row.id,
                parent_id=row.query_run_id,
                retention_job_id=job_id,
                policy_version=policy_version,
                rule=rule,
                details={
                    "kind": row.kind,
                    "acquisition_method": row.acquisition_method,
                    "sha256": row.sha256,
                    "size_bytes": row.size_bytes,
                    "collected_at": row.collected_at.isoformat(),
                    "connector_id": row.connector_id,
                },
                expired_at=now,
            )
        )
    # Files first: an interruption leaves rows whose files are missing (reported by integrity
    # checks, removed by the retried job), never unreferenced copies of the content.
    for row in evidence:
        storage.remove_key(row.storage_key)
    for row in evidence:
        db.delete(row)
    removed["evidence_records"] += len(evidence)
    removed["stored_bytes"] += sum(row.size_bytes for row in evidence)


def apply_job(ctx: RetentionContext, job_id: uuid.UUID) -> str:
    if not _claim(ctx, job_id):
        return "skipped"
    actor = service_actor(RETAINER, job_id)
    with session_scope(ctx.session_factory) as db:
        job = db.get(RetentionJob, job_id)
        assert job is not None
        case_id = job.case_id
        case = db.get(Case, case_id)
        policy = db.get(CaseRetentionPolicy, case_id)
        if case is None or case.status not in (CaseStatus.ACTIVE, CaseStatus.ARCHIVED):
            return _finish(
                ctx, job_id, RetentionJobStatus.COMPLETED, Counter(), Counter(), "case_unavailable"
            )
        active = _active_work(db, case_id)
        if active:
            job.status = RetentionJobStatus.QUEUED
            job.lease_expires_at = None
            job.deferred = dict(active)
            job.progress_note = "Waiting for active work in the case to finish"
            outbox = dispatch.enqueue(
                db,
                task_name=dispatch.APPLY_RETENTION_TASK,
                aggregate_type=AggregateType.RETENTION_JOB,
                aggregate_id=job_id,
                case_id=case_id,
            )
            outbox.available_at = utcnow() + timedelta(seconds=WAIT_FOR_ACTIVE_WORK_SECONDS)
            return "waiting"
        rules = Rules(
            collected_results_max_age_days=policy.collected_results_max_age_days
            if policy and policy.active
            else None,
            imported_evidence_max_age_days=policy.imported_evidence_max_age_days
            if policy and policy.active
            else None,
        )
        policy_version = policy.version if policy else job.policy_version
        work = plan(db, case_id, rules)
    removed: Counter[str] = Counter()
    deferred: Counter[str] = Counter(work.deferred)
    run_ids = list(work.runs.items())
    for start in range(0, len(run_ids), RUN_BATCH):
        with session_scope(ctx.session_factory) as db:
            for run_id, rule in run_ids[start : start + RUN_BATCH]:
                run = db.scalar(
                    select(QueryRun).where(QueryRun.id == run_id).with_for_update(skip_locked=True)
                )
                if (
                    run is None
                    or run.status not in TERMINAL_RUN_STATUSES
                    or run.results_expired_at is not None
                ):
                    deferred["execution_locked_or_changed"] += 1
                    continue
                evidence = list(
                    db.scalars(
                        select(EvidenceObject)
                        .where(
                            EvidenceObject.case_id == case_id, EvidenceObject.query_run_id == run_id
                        )
                        .with_for_update()
                    )
                )
                _remove_evidence(
                    db,
                    ctx.storage,
                    case_id,
                    evidence,
                    job_id=job_id,
                    policy_version=policy_version,
                    rule=rule,
                    removed=removed,
                )
                run.results_expired_at = utcnow()
                db.add(
                    RetentionTombstone(
                        case_id=case_id,
                        record_type="query_run_results",
                        record_id=run_id,
                        retention_job_id=job_id,
                        policy_version=policy_version,
                        rule=rule,
                        details={"evidence_records": len(evidence)},
                    )
                )
                blocked = db.execute(
                    update(NotificationDelivery)
                    .where(
                        NotificationDelivery.case_id == case_id,
                        NotificationDelivery.status == DeliveryStatus.PENDING,
                        NotificationDelivery.payload["query_run_id"].astext == str(run_id),
                    )
                    .values(status=DeliveryStatus.BLOCKED, last_error_code="retention_expired")
                )
                removed["pending_deliveries_blocked"] += int(getattr(blocked, "rowcount", 0) or 0)
                removed["executions"] += 1
    for evidence_id, rule in work.imports.items():
        with session_scope(ctx.session_factory) as db:
            original = db.scalar(
                select(EvidenceObject)
                .where(EvidenceObject.id == evidence_id)
                .with_for_update(skip_locked=True)
            )
            if original is None:
                continue
            busy = db.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(
                    ProcessingJob.evidence_id == evidence_id,
                    ProcessingJob.status.in_(
                        [
                            ProcessingStatus.QUEUED,
                            ProcessingStatus.RUNNING,
                            ProcessingStatus.NEEDS_INPUT,
                        ]
                    ),
                )
            )
            if busy:
                deferred["import_being_processed"] += 1
                continue
            from app.evidence.service import _processing_descendants

            derived = _processing_descendants(db, case_id, evidence_id)
            _remove_evidence(
                db,
                ctx.storage,
                case_id,
                [*derived, original],
                job_id=job_id,
                policy_version=policy_version,
                rule=rule,
                removed=removed,
            )
            removed["imported_originals"] += 1
    return _finish(ctx, job_id, RetentionJobStatus.COMPLETED, removed, deferred, None, actor=actor)


def _finish(
    ctx: RetentionContext,
    job_id: uuid.UUID,
    status: RetentionJobStatus,
    removed: Counter[str],
    deferred: Counter[str],
    error_code: str | None,
    *,
    actor: Any = None,
) -> str:
    with session_scope(ctx.session_factory) as db:
        job = db.get(RetentionJob, job_id)
        if job is None:
            return "missing"
        job.status = status
        job.removed = dict(removed)
        job.deferred = dict(deferred)
        job.error_code = error_code
        job.lease_expires_at = None
        job.finished_at = utcnow()
        job.progress_note = "Completed" if error_code is None else f"Completed: {error_code}"
        policy = db.get(CaseRetentionPolicy, job.case_id)
        if policy is not None:
            policy.last_applied_at = utcnow()
        dispatch.mark_done(db, AggregateType.RETENTION_JOB, job_id)
        if actor is not None:
            record(
                db,
                actor,
                "retention.applied",
                case_id=job.case_id,
                target_type="retention_job",
                target_id=job_id,
                details={
                    "removed": dict(removed),
                    "deferred": dict(deferred),
                    "trigger": job.trigger,
                },
            )
    logger.info(
        "retention_job_finished", extra={"job_ref": str(job_id)[:8], "removed": dict(removed)}
    )
    return str(status)
