"""Case retention routes: policy, preview, activation and jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.audit.service import record
from app.cases.access import AnalystCase, ReadableCase
from app.deps import ActorDep, DbDep, PrincipalDep
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.retention import service
from app.retention.models import CaseRetentionPolicy, RetentionJob
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}/retention", tags=["retention"])


class RulesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collected_results_max_age_days: Annotated[int | None, Field(ge=1, le=3650)] = None
    imported_evidence_max_age_days: Annotated[int | None, Field(ge=1, le=3650)] = None


class ActivationIn(RulesIn):
    # The exact case title, typed to confirm that records will be removed.
    confirm_title: Annotated[str, Field(min_length=1, max_length=200)]


class PolicyOut(BaseModel):
    active: bool
    collected_results_max_age_days: int | None
    imported_evidence_max_age_days: int | None
    version: int
    activated_at: datetime | None
    last_applied_at: datetime | None
    monitor_rules: list[dict[str, Any]]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trigger: str
    status: str
    policy_version: int
    attempts: int
    removed: dict[str, Any]
    deferred: dict[str, Any]
    progress_note: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _policy_out(db: DbDep, case_id: uuid.UUID) -> PolicyOut:
    from app.monitoring.models import Monitor

    policy = db.get(CaseRetentionPolicy, case_id)
    monitors = [
        {"monitor_id": str(m.id), "name": m.name, **(m.retention or {})}
        for m in db.scalars(select(Monitor).where(Monitor.case_id == case_id))
        if any(v is not None for v in (m.retention or {}).values())
    ]
    return PolicyOut(
        active=bool(policy and policy.active),
        collected_results_max_age_days=policy.collected_results_max_age_days if policy else None,
        imported_evidence_max_age_days=policy.imported_evidence_max_age_days if policy else None,
        version=policy.version if policy else 0,
        activated_at=policy.activated_at if policy else None,
        last_applied_at=policy.last_applied_at if policy else None,
        monitor_rules=monitors,
    )


@router.get("")
def get_policy(case: ReadableCase, db: DbDep) -> PolicyOut:
    return _policy_out(db, case.id)


@router.post("/preview")
def preview_policy(case: AnalystCase, db: DbDep, body: RulesIn) -> dict[str, Any]:
    """What applying these rules now would remove (monitor retention settings included)."""
    return service.preview(db, case.id, service.Rules(**body.model_dump()))


def _publish(request: Request, db: DbDep, job: RetentionJob | None) -> None:
    if job is None:
        return
    state = request.app.state
    dispatch.publish_aggregate_if_pending(
        state.session_factory,
        state.celery,
        state.settings,
        AggregateType.RETENTION_JOB,
        job.id,
    )


@router.put("")
def activate_policy(
    request: Request,
    case: AnalystCase,
    db: DbDep,
    principal: PrincipalDep,
    actor: ActorDep,
    body: ActivationIn,
) -> PolicyOut:
    if body.confirm_title.strip() != case.title:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "confirmation_mismatch",
                "message": "Type the exact case title to confirm.",
            },
        )
    rules = service.Rules(
        collected_results_max_age_days=body.collected_results_max_age_days,
        imported_evidence_max_age_days=body.imported_evidence_max_age_days,
    )
    if rules.empty:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "empty_policy",
                "message": "Set at least one rule, or turn retention off.",
            },
        )
    counts = service.preview(db, case.id, rules)
    policy = db.scalar(
        select(CaseRetentionPolicy).where(CaseRetentionPolicy.case_id == case.id).with_for_update()
    )
    if policy is None:
        policy = CaseRetentionPolicy(case_id=case.id, version=0)
        db.add(policy)
    policy.active = True
    policy.collected_results_max_age_days = rules.collected_results_max_age_days
    policy.imported_evidence_max_age_days = rules.imported_evidence_max_age_days
    policy.version = (policy.version or 0) + 1
    policy.updated_by_user_id = principal.user.id
    policy.activated_at = datetime.now().astimezone()
    db.flush()
    record(
        db,
        actor,
        "retention.policy_activated",
        case_id=case.id,
        target_type="case",
        target_id=case.id,
        details={
            "version": policy.version,
            "collected_results_max_age_days": rules.collected_results_max_age_days,
            "imported_evidence_max_age_days": rules.imported_evidence_max_age_days,
            "preview": {k: v for k, v in counts.items() if k != "not_removed"},
        },
    )
    job = service.enqueue_job(
        db,
        case.id,
        trigger="activation",
        policy_version=policy.version,
        requested_by=principal.user.id,
    )
    db.commit()
    _publish(request, db, job)
    return _policy_out(db, case.id)


@router.delete("")
def deactivate_policy(case: AnalystCase, db: DbDep, actor: ActorDep) -> PolicyOut:
    policy = db.get(CaseRetentionPolicy, case.id)
    if policy is not None and policy.active:
        policy.active = False
        policy.version += 1
        record(
            db,
            actor,
            "retention.policy_deactivated",
            case_id=case.id,
            target_type="case",
            target_id=case.id,
            details={"version": policy.version},
        )
        db.commit()
    return _policy_out(db, case.id)


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def run_now(
    request: Request, case: AnalystCase, db: DbDep, principal: PrincipalDep, actor: ActorDep
) -> JobOut:
    policy = db.get(CaseRetentionPolicy, case.id)
    job = service.enqueue_job(
        db,
        case.id,
        trigger="manual",
        policy_version=policy.version if policy else 0,
        requested_by=principal.user.id,
    )
    assert job is not None
    record(
        db,
        actor,
        "retention.run_requested",
        case_id=case.id,
        target_type="retention_job",
        target_id=job.id,
    )
    db.commit()
    _publish(request, db, job)
    db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/jobs")
def list_jobs(
    case: ReadableCase, db: DbDep, limit: LimitParam = 20, offset: OffsetParam = 0
) -> Page[JobOut]:
    from sqlalchemy import func

    condition = RetentionJob.case_id == case.id
    total = db.scalar(select(func.count()).select_from(RetentionJob).where(condition)) or 0
    rows = db.scalars(
        select(RetentionJob)
        .where(condition)
        .order_by(RetentionJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[JobOut.model_validate(row) for row in rows], total=total, limit=limit, offset=offset
    )
