from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.models import User
from app.connectors.base import CollectionMode, effective_collection_mode
from app.connectors.registry import get_connector
from app.db.base import utcnow
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType, DispatchOutbox
from app.entities.models import Entity, Observation, Relationship
from app.evidence.models import EvidenceObject
from app.queries.models import (
    TERMINAL_RUN_STATUSES,
    ConnectorOutcome,
    ConnectorRun,
    QueryRun,
    RunStatus,
    SavedQuery,
)
from app.queries.schemas import (
    ConnectorRunOut,
    QueryRunDetail,
    QueryRunOut,
    SavedQueryCreate,
    SavedQueryOut,
    SavedQueryUpdate,
)

SNAPSHOT_VERSION = 1


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "invalid_query", "message": message}
    )


def validate_definition(
    input_type: str, input_value: str, connector_ids: Sequence[str], parameters: dict[str, Any]
) -> str:
    """Validate against the registry and return the shared collection mode."""
    modes = set()
    for connector_id in connector_ids:
        connector = get_connector(connector_id)
        if connector is None:
            raise _unprocessable(f"unknown connector '{connector_id}'")
        if input_type not in connector.descriptor.supported_input_types:
            raise _unprocessable(
                f"connector '{connector_id}' does not support input type '{input_type}'"
            )
        try:
            connector.validate(input_type, input_value, parameters)
        except ValueError as exc:
            raise _unprocessable(str(exc)) from None
        modes.add(str(effective_collection_mode(connector.descriptor, parameters)))
    if len(connector_ids) != len(set(connector_ids)):
        raise _unprocessable("connectors must not be repeated")
    if len(modes) != 1:
        raise _unprocessable("all selected connectors must share one collection mode")
    return modes.pop()


def get_saved_query(db: Session, case_id: uuid.UUID, query_id: uuid.UUID) -> SavedQuery:
    query = db.scalar(
        select(SavedQuery).where(SavedQuery.id == query_id, SavedQuery.case_id == case_id)
    )
    if query is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="saved_query_not_found")
    return query


def create_saved_query(
    db: Session, case_id: uuid.UUID, user: User, body: SavedQueryCreate
) -> SavedQuery:
    mode = validate_definition(
        body.input_type, body.input_value, body.connector_ids, body.parameters
    )
    query = SavedQuery(
        case_id=case_id,
        name=body.name,
        input_type=body.input_type,
        input_value=body.input_value,
        connector_ids=list(body.connector_ids),
        collection_mode=mode,
        parameters=body.parameters,
        limits=body.limits.model_dump(),
        created_by_user_id=user.id,
    )
    db.add(query)
    db.flush()
    return query


def update_saved_query(db: Session, query: SavedQuery, body: SavedQueryUpdate) -> None:
    """Edits affect future runs only; existing runs keep their parameter snapshots."""
    parameters: dict[str, Any] = (
        body.parameters if body.parameters is not None else dict(query.parameters)
    )
    input_value = body.input_value.strip() if body.input_value else query.input_value
    validate_definition(query.input_type, input_value, query.connector_ids, parameters)
    if body.name:
        query.name = body.name.strip()
    query.input_value = input_value
    query.parameters = parameters
    if body.limits is not None:
        query.limits = body.limits.model_dump()


def saved_queries_out(db: Session, queries: Sequence[SavedQuery]) -> list[SavedQueryOut]:
    last_runs: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if queries:
        ranked = (
            select(
                QueryRun.saved_query_id,
                QueryRun.id,
                QueryRun.status,
                func.row_number()
                .over(partition_by=QueryRun.saved_query_id, order_by=QueryRun.run_number.desc())
                .label("rank"),
            )
            .where(QueryRun.saved_query_id.in_([q.id for q in queries]))
            .subquery()
        )
        for saved_query_id, run_id, run_status, _rank in db.execute(
            select(ranked).where(ranked.c.rank == 1)
        ):
            last_runs[saved_query_id] = (run_id, run_status)
    result = []
    for query in queries:
        out = SavedQueryOut.model_validate(query)
        last = last_runs.get(query.id)
        result.append(
            out.model_copy(
                update={
                    "synthetic": query.collection_mode == "synthetic_fixture",
                    "last_run_id": last[0] if last else None,
                    "last_run_status": last[1] if last else None,
                }
            )
        )
    return result


def build_snapshot(query: SavedQuery) -> dict[str, Any]:
    connectors = []
    for connector_id in query.connector_ids:
        connector = get_connector(connector_id)
        assert connector is not None
        descriptor = connector.descriptor
        connectors.append(
            {
                "id": descriptor.connector_id,
                "version": descriptor.version,
                "synthetic": descriptor.synthetic,
                "collection_mode": str(effective_collection_mode(descriptor, query.parameters)),
                "retry_max_attempts": descriptor.retry_policy.max_attempts,
                "timeout_seconds": descriptor.timeout_seconds,
            }
        )
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "saved_query_name": query.name,
        "input_type": query.input_type,
        "input_value": query.input_value,
        "collection_mode": query.collection_mode,
        "parameters": dict(query.parameters),
        "limits": dict(query.limits),
        "connectors": connectors,
        "captured_at": utcnow().isoformat(),
    }


def create_run(db: Session, query: SavedQuery, user: User) -> tuple[QueryRun, DispatchOutbox]:
    """Persist the run, its connector runs and the outbox row in one transaction."""
    validate_definition(query.input_type, query.input_value, query.connector_ids, query.parameters)
    locked = db.scalar(select(SavedQuery).where(SavedQuery.id == query.id).with_for_update())
    assert locked is not None
    locked.run_counter += 1
    snapshot = build_snapshot(locked)
    run = QueryRun(
        id=uuid.uuid4(),
        case_id=locked.case_id,
        saved_query_id=locked.id,
        run_number=locked.run_counter,
        parameters_snapshot=snapshot,
        status=RunStatus.QUEUED,
        requested_by_user_id=user.id,
        queued_at=utcnow(),
    )
    db.add(run)
    db.flush()
    for position, connector in enumerate(snapshot["connectors"]):
        db.add(
            ConnectorRun(
                case_id=run.case_id,
                query_run_id=run.id,
                position=position,
                connector_id=connector["id"],
                connector_version=connector["version"],
                status=RunStatus.QUEUED,
            )
        )
    network = any(
        connector["collection_mode"] != CollectionMode.SYNTHETIC_FIXTURE
        for connector in snapshot["connectors"]
    )
    outbox = dispatch.enqueue(
        db,
        task_name=(
            dispatch.EXECUTE_COLLECTION_RUN_TASK if network else dispatch.EXECUTE_QUERY_RUN_TASK
        ),
        aggregate_type=AggregateType.QUERY_RUN,
        aggregate_id=run.id,
        case_id=run.case_id,
    )
    db.flush()
    return run, outbox


def get_run(db: Session, case_id: uuid.UUID, run_id: uuid.UUID) -> QueryRun:
    run = db.scalar(select(QueryRun).where(QueryRun.id == run_id, QueryRun.case_id == case_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run_not_found")
    return run


def request_cancel(db: Session, run: QueryRun, user: User) -> None:
    """Persist cancellation. Queued runs stop immediately; running runs stop at the next
    bounded unit of work. Already collected evidence is kept."""
    locked = db.scalar(select(QueryRun).where(QueryRun.id == run.id).with_for_update())
    assert locked is not None
    if locked.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="run_already_finished")
    now = utcnow()
    if locked.cancel_requested_at is None:
        locked.cancel_requested_at = now
        locked.cancel_requested_by_user_id = user.id
    if locked.status == RunStatus.QUEUED:
        locked.status = RunStatus.CANCELED
        locked.finished_at = now
        db.execute(
            update(ConnectorRun)
            .where(
                ConnectorRun.query_run_id == locked.id,
                ConnectorRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
            )
            .values(status=RunStatus.CANCELED, outcome=ConnectorOutcome.CANCELED, finished_at=now)
        )
        dispatch.mark_done(db, AggregateType.QUERY_RUN, locked.id)


def _counts(
    db: Session, runs: Sequence[QueryRun]
) -> tuple[dict[uuid.UUID | None, int], dict[uuid.UUID | None, int]]:
    ids = [run.id for run in runs]
    if not ids:
        return {}, {}
    evidence: dict[uuid.UUID | None, int] = {
        run_id: count
        for run_id, count in db.execute(
            select(EvidenceObject.query_run_id, func.count())
            .where(EvidenceObject.query_run_id.in_(ids))
            .group_by(EvidenceObject.query_run_id)
        )
    }
    observations: dict[uuid.UUID | None, int] = {
        run_id: count
        for run_id, count in db.execute(
            select(Observation.query_run_id, func.count())
            .where(Observation.query_run_id.in_(ids))
            .group_by(Observation.query_run_id)
        )
    }
    return evidence, observations


def runs_out(db: Session, runs: Sequence[QueryRun]) -> list[QueryRunOut]:
    evidence, observations = _counts(db, runs)
    names: dict[uuid.UUID, str] = {}
    query_ids = {run.saved_query_id for run in runs if run.saved_query_id}
    if query_ids:
        names = {
            query_id: name
            for query_id, name in db.execute(
                select(SavedQuery.id, SavedQuery.name).where(SavedQuery.id.in_(query_ids))
            )
        }
    dispatch_status: dict[uuid.UUID, str] = {}
    if runs:
        dispatch_status = {
            aggregate_id: outbox_status
            for aggregate_id, outbox_status in db.execute(
                select(DispatchOutbox.aggregate_id, DispatchOutbox.status).where(
                    DispatchOutbox.aggregate_type == AggregateType.QUERY_RUN,
                    DispatchOutbox.aggregate_id.in_([run.id for run in runs]),
                )
            )
        }
    return [
        QueryRunOut(
            id=run.id,
            case_id=run.case_id,
            saved_query_id=run.saved_query_id,
            saved_query_name=names.get(run.saved_query_id) if run.saved_query_id else None,
            run_number=run.run_number,
            status=run.status,
            parameters_snapshot=run.parameters_snapshot,
            queued_at=run.queued_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            cancel_requested_at=run.cancel_requested_at,
            error_code=run.error_code,
            synthetic=run.parameters_snapshot.get("collection_mode") == "synthetic_fixture",
            evidence_count=int(evidence.get(run.id, 0)),
            observation_count=int(observations.get(run.id, 0)),
            dispatch_status=dispatch_status.get(run.id),
        )
        for run in runs
    ]


def run_detail(db: Session, run: QueryRun) -> QueryRunDetail:
    base = runs_out(db, [run])[0]
    connector_runs = db.scalars(
        select(ConnectorRun)
        .where(ConnectorRun.query_run_id == run.id)
        .order_by(ConnectorRun.position)
    )
    entity_count = db.scalar(
        select(func.count()).select_from(Entity).where(Entity.created_by_query_run_id == run.id)
    )
    relationship_count = db.scalar(
        select(func.count())
        .select_from(Relationship)
        .where(Relationship.created_by_query_run_id == run.id)
    )
    return QueryRunDetail(
        **base.model_dump(),
        connector_runs=[ConnectorRunOut.model_validate(cr) for cr in connector_runs],
        entity_count=entity_count or 0,
        relationship_count=relationship_count or 0,
    )
