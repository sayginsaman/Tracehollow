"""Change sets and their events (readable by every case member)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.cases.access import ReadableCase
from app.changes.models import ChangeEvent, ChangeSet
from app.deps import DbDep
from app.entities.models import Observation
from app.evidence.models import EvidenceObject
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}/change-sets", tags=["changes"])


class ChangeSetOut(BaseModel):
    id: uuid.UUID
    monitor_id: uuid.UUID | None
    occurrence_id: uuid.UUID | None
    query_run_id: uuid.UUID
    connector_run_id: uuid.UUID
    connector_id: str
    connector_version: str
    baseline_query_run_id: uuid.UUID | None
    baseline_connector_run_id: uuid.UUID | None
    status: str
    coverage_complete: bool
    baseline_coverage_complete: bool | None
    counts: dict[str, int]
    limitations: list[str]
    truncated: bool
    created_at: datetime


class ChangeEventOut(BaseModel):
    id: uuid.UUID
    kind: str
    observation_type: str
    source_object_id: str | None
    field: str | None
    previous_value: str | None
    current_value: str | None
    entity_id: uuid.UUID | None
    previous_observation_id: uuid.UUID | None
    current_observation_id: uuid.UUID | None
    previous_evidence_id: uuid.UUID | None
    current_evidence_id: uuid.UUID | None
    # False when the referenced record no longer exists (for example removed by retention).
    previous_evidence_available: bool | None
    current_evidence_available: bool | None
    note: str


class ChangeSetDetail(ChangeSetOut):
    events: Page[ChangeEventOut]


def change_set_out(row: ChangeSet) -> ChangeSetOut:
    return ChangeSetOut(
        id=row.id,
        monitor_id=row.monitor_id,
        occurrence_id=row.occurrence_id,
        query_run_id=row.query_run_id,
        connector_run_id=row.connector_run_id,
        connector_id=row.connector_id,
        connector_version=row.connector_version,
        baseline_query_run_id=row.baseline_query_run_id,
        baseline_connector_run_id=row.baseline_connector_run_id,
        status=row.status,
        coverage_complete=row.coverage_complete,
        baseline_coverage_complete=row.baseline_coverage_complete,
        counts={key: int(value) for key, value in (row.counts or {}).items()},
        limitations=list(row.limitations or []),
        truncated=row.truncated,
        created_at=row.created_at,
    )


@router.get("")
def list_change_sets(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    monitor_id: Annotated[uuid.UUID | None, Query()] = None,
    query_run_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=32)] = None,
) -> Page[ChangeSetOut]:
    conditions: list[Any] = [ChangeSet.case_id == case.id]
    if monitor_id is not None:
        conditions.append(ChangeSet.monitor_id == monitor_id)
    if query_run_id is not None:
        conditions.append(ChangeSet.query_run_id == query_run_id)
    if status_filter:
        conditions.append(ChangeSet.status == status_filter)
    total = db.scalar(select(func.count()).select_from(ChangeSet).where(*conditions)) or 0
    rows = db.scalars(
        select(ChangeSet)
        .where(*conditions)
        .order_by(ChangeSet.created_at.desc(), ChangeSet.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[change_set_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{change_set_id}")
def get_change_set(
    case: ReadableCase,
    db: DbDep,
    change_set_id: uuid.UUID,
    limit: LimitParam = 100,
    offset: OffsetParam = 0,
    kind: Annotated[
        str | None, Query(pattern="^(new|changed|not_observed|conflicting|unknown)$")
    ] = None,
) -> ChangeSetDetail:
    row = db.scalar(
        select(ChangeSet).where(ChangeSet.id == change_set_id, ChangeSet.case_id == case.id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="change_set_not_found")
    conditions: list[Any] = [ChangeEvent.change_set_id == row.id]
    if kind:
        conditions.append(ChangeEvent.kind == kind)
    total = db.scalar(select(func.count()).select_from(ChangeEvent).where(*conditions)) or 0
    events = list(
        db.scalars(
            select(ChangeEvent)
            .where(*conditions)
            .order_by(
                ChangeEvent.kind,
                ChangeEvent.observation_type,
                ChangeEvent.source_object_id,
                ChangeEvent.field,
            )
            .limit(limit)
            .offset(offset)
        )
    )
    evidence_ids = {
        value
        for event in events
        for value in (event.previous_evidence_id, event.current_evidence_id)
        if value is not None
    }
    existing = (
        set(
            db.scalars(
                select(EvidenceObject.id).where(
                    EvidenceObject.case_id == case.id, EvidenceObject.id.in_(evidence_ids)
                )
            )
        )
        if evidence_ids
        else set()
    )
    observation_ids = {
        value
        for event in events
        for value in (event.previous_observation_id, event.current_observation_id)
        if value is not None
    }
    observations = (
        set(
            db.scalars(
                select(Observation.id).where(
                    Observation.case_id == case.id, Observation.id.in_(observation_ids)
                )
            )
        )
        if observation_ids
        else set()
    )

    def available(value: uuid.UUID | None) -> bool | None:
        return None if value is None else value in existing

    return ChangeSetDetail(
        **change_set_out(row).model_dump(),
        events=Page(
            items=[
                ChangeEventOut(
                    id=event.id,
                    kind=event.kind,
                    observation_type=event.observation_type,
                    source_object_id=event.source_object_id,
                    field=event.field,
                    previous_value=event.previous_value,
                    current_value=event.current_value,
                    entity_id=event.entity_id,
                    previous_observation_id=event.previous_observation_id
                    if event.previous_observation_id in observations
                    else None,
                    current_observation_id=event.current_observation_id
                    if event.current_observation_id in observations
                    else None,
                    previous_evidence_id=event.previous_evidence_id,
                    current_evidence_id=event.current_evidence_id,
                    previous_evidence_available=available(event.previous_evidence_id),
                    current_evidence_available=available(event.current_evidence_id),
                    note=event.note,
                )
                for event in events
            ],
            total=total,
            limit=limit,
            offset=offset,
        ),
    )
