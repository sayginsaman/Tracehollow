"""Recent collection and processing activity across the cases a user can open.

Read-only. It feeds the workspace overview so the interface does not have to query every case.
Only cases the user is a member of and that can still be opened (active or archived) are included,
the same rule as the case-scoped routes; nothing here reveals other users' cases.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.cases.models import Case, CaseMember, CaseStatus
from app.deps import DbDep, PrincipalDep
from app.imports import service as imports
from app.imports.models import ProcessingJob, ProcessingStatus
from app.imports.schemas import ProcessingJobOut
from app.queries import service as queries
from app.queries.models import QueryRun, RunStatus
from app.queries.schemas import QueryRunOut

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])

MAX_WAITING_JOBS = 20


class ActivityCase(BaseModel):
    id: uuid.UUID
    title: str
    status: str


class Activity(BaseModel):
    runs: list[QueryRunOut]
    active_runs: int
    processing_jobs: list[ProcessingJobOut]
    active_processing_jobs: int
    jobs_needing_input: list[ProcessingJobOut]
    # Titles and states of the cases referenced by the items above.
    cases: list[ActivityCase]


@router.get("")
def get_activity(
    db: DbDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> Activity:
    readable = (
        select(CaseMember.case_id)
        .join(Case, Case.id == CaseMember.case_id)
        .where(
            CaseMember.user_id == principal.user.id,
            Case.status.in_([CaseStatus.ACTIVE, CaseStatus.ARCHIVED]),
        )
    )
    runs = list(
        db.scalars(
            select(QueryRun)
            .where(QueryRun.case_id.in_(readable))
            .order_by(QueryRun.queued_at.desc(), QueryRun.id)
            .limit(limit)
        )
    )
    active_runs = db.scalar(
        select(func.count())
        .select_from(QueryRun)
        .where(
            QueryRun.case_id.in_(readable),
            QueryRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
        )
    )
    jobs = list(
        db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.case_id.in_(readable))
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
            .limit(limit)
        )
    )
    active_jobs = db.scalar(
        select(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.case_id.in_(readable),
            ProcessingJob.status.in_([ProcessingStatus.QUEUED, ProcessingStatus.RUNNING]),
        )
    )
    waiting = list(
        db.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.case_id.in_(readable),
                ProcessingJob.status == ProcessingStatus.NEEDS_INPUT,
            )
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
            .limit(MAX_WAITING_JOBS)
        )
    )
    case_ids = {run.case_id for run in runs} | {job.case_id for job in [*jobs, *waiting]}
    cases = (
        db.execute(select(Case.id, Case.title, Case.status).where(Case.id.in_(case_ids))).all()
        if case_ids
        else []
    )
    return Activity(
        runs=queries.runs_out(db, runs),
        active_runs=int(active_runs or 0),
        processing_jobs=[imports.job_out(job) for job in jobs],
        active_processing_jobs=int(active_jobs or 0),
        jobs_needing_input=[imports.job_out(job) for job in waiting],
        cases=[ActivityCase(id=row.id, title=row.title, status=row.status) for row in cases],
    )
