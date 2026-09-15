from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.cases.access import ReadableCase, WritableCase
from app.deps import DbDep, PrincipalDep
from app.dispatch import service as dispatch
from app.queries import service
from app.queries.models import QueryRun, RunStatus, SavedQuery
from app.queries.schemas import (
    QueryRunDetail,
    QueryRunOut,
    SavedQueryCreate,
    SavedQueryOut,
    SavedQueryUpdate,
)
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["queries"])


@router.get("/saved-queries")
def list_saved_queries(
    case: ReadableCase, db: DbDep, limit: LimitParam = 50, offset: OffsetParam = 0
) -> Page[SavedQueryOut]:
    condition = SavedQuery.case_id == case.id
    total = db.scalar(select(func.count()).select_from(SavedQuery).where(condition)) or 0
    rows = list(
        db.scalars(
            select(SavedQuery)
            .where(condition)
            .order_by(SavedQuery.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(items=service.saved_queries_out(db, rows), total=total, limit=limit, offset=offset)


@router.post("/saved-queries", status_code=status.HTTP_201_CREATED)
def create_saved_query(
    case: WritableCase, db: DbDep, principal: PrincipalDep, body: SavedQueryCreate
) -> SavedQueryOut:
    query = service.create_saved_query(db, case.id, principal.user, body)
    db.commit()
    return service.saved_queries_out(db, [query])[0]


@router.get("/saved-queries/{query_id}")
def get_saved_query(case: ReadableCase, db: DbDep, query_id: uuid.UUID) -> SavedQueryOut:
    return service.saved_queries_out(db, [service.get_saved_query(db, case.id, query_id)])[0]


@router.patch("/saved-queries/{query_id}")
def update_saved_query(
    case: WritableCase, db: DbDep, query_id: uuid.UUID, body: SavedQueryUpdate
) -> SavedQueryOut:
    query = service.get_saved_query(db, case.id, query_id)
    service.update_saved_query(db, query, body)
    db.commit()
    return service.saved_queries_out(db, [query])[0]


@router.delete("/saved-queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_query(case: WritableCase, db: DbDep, query_id: uuid.UUID) -> None:
    """Removes the definition only. Previous runs keep their snapshots and results."""
    query = service.get_saved_query(db, case.id, query_id)
    active = db.scalar(
        select(func.count())
        .select_from(QueryRun)
        .where(
            QueryRun.saved_query_id == query.id,
            QueryRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
        )
    )
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="saved_query_has_active_runs")
    db.delete(query)
    db.commit()


@router.post("/saved-queries/{query_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def run_saved_query(
    request: Request, case: WritableCase, db: DbDep, principal: PrincipalDep, query_id: uuid.UUID
) -> QueryRunDetail:
    query = service.get_saved_query(db, case.id, query_id)
    run, outbox = service.create_run(db, query, principal.user)
    db.commit()
    state = request.app.state
    dispatch.publish_after_commit(state.session_factory, state.celery, state.settings, outbox.id)
    db.refresh(run)
    return service.run_detail(db, run)


@router.get("/runs")
def list_runs(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    saved_query_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> Page[QueryRunOut]:
    conditions = [QueryRun.case_id == case.id]
    if saved_query_id is not None:
        conditions.append(QueryRun.saved_query_id == saved_query_id)
    if status_filter is not None:
        conditions.append(QueryRun.status == status_filter)
    total = db.scalar(select(func.count()).select_from(QueryRun).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(QueryRun)
            .where(*conditions)
            .order_by(QueryRun.queued_at.desc(), QueryRun.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(items=service.runs_out(db, rows), total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}")
def get_run(case: ReadableCase, db: DbDep, run_id: uuid.UUID) -> QueryRunDetail:
    return service.run_detail(db, service.get_run(db, case.id, run_id))


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    case: ReadableCase, db: DbDep, principal: PrincipalDep, run_id: uuid.UUID
) -> QueryRunDetail:
    run = service.get_run(db, case.id, run_id)
    service.request_cancel(db, run, principal.user)
    db.commit()
    db.refresh(run)
    return service.run_detail(db, run)
