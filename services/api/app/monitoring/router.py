"""Monitor routes: reading needs case access; changes, runs and cancellation need an analyst."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.budgets import service as budgets
from app.cases.access import AnalystCase, ReadableCase, WritableCase
from app.changes.models import ChangeSet
from app.deps import ActorDep, DbDep, PrincipalDep, SettingsDep
from app.dispatch import service as dispatch
from app.monitoring import service
from app.monitoring.models import Monitor, MonitorOccurrence, MonitorStatus
from app.monitoring.schemas import (
    ActionItem,
    ChangeSummary,
    MonitorCreate,
    MonitorOut,
    MonitorResume,
    MonitorUpdate,
    OccurrenceOut,
    UsageOut,
)
from app.queries.models import ConnectorRun, QueryRun, RunStatus, SavedQuery
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}/monitors", tags=["monitors"])

REASON_MESSAGES = {
    "created": "Created paused. Enable it to start scheduled collection.",
    "query_changed": (
        "Paused because the saved query changed. Review the query, then resume to adopt it "
        "(change detection starts a new baseline)."
    ),
    "authorization_lost": (
        "Paused because the analyst who enabled it lost analyst access. Resume to authorize "
        "future runs yourself."
    ),
    "repeated_failures": "Paused after repeated failed runs. Check the last runs, then resume.",
    "case_archived": "Paused because the case was archived. Restore the case to resume.",
    "query_invalid": "Paused because the saved query is no longer valid.",
    "invalid_schedule": "Paused because its schedule is no longer valid on this installation.",
}


def usage_out(usage: budgets.Usage) -> UsageOut:
    return UsageOut(
        scope_type=usage.scope_type,
        metric=usage.metric,
        period=usage.period,
        period_start=usage.period_start,
        period_end=usage.period_end,
        limit_units=usage.limit_units,
        reserved_units=usage.reserved_units,
        consumed_units=usage.consumed_units,
        estimated_units=usage.estimated_units,
        remaining_units=usage.remaining_units,
        exhausted=usage.exhausted,
        denied_requests=usage.denied_requests,
    )


def occurrences_out(db: Session, rows: list[MonitorOccurrence]) -> list[OccurrenceOut]:
    run_ids = [row.query_run_id for row in rows if row.query_run_id]
    runs = (
        {run.id: run for run in db.scalars(select(QueryRun).where(QueryRun.id.in_(run_ids)))}
        if run_ids
        else {}
    )
    outcomes: dict[uuid.UUID, list[str | None]] = {}
    changes: dict[uuid.UUID, list[ChangeSummary]] = {}
    if run_ids:
        for run_id, outcome in db.execute(
            select(ConnectorRun.query_run_id, ConnectorRun.outcome)
            .where(ConnectorRun.query_run_id.in_(run_ids))
            .order_by(ConnectorRun.query_run_id, ConnectorRun.position)
        ):
            outcomes.setdefault(run_id, []).append(outcome)
        for change_set in db.scalars(
            select(ChangeSet)
            .where(ChangeSet.query_run_id.in_(run_ids))
            .order_by(ChangeSet.created_at)
        ):
            changes.setdefault(change_set.query_run_id, []).append(
                ChangeSummary(
                    change_set_id=change_set.id,
                    connector_id=change_set.connector_id,
                    status=change_set.status,
                    counts={k: int(v) for k, v in (change_set.counts or {}).items()},
                )
            )
    result = []
    for row in rows:
        run = runs.get(row.query_run_id) if row.query_run_id else None
        result.append(
            OccurrenceOut(
                id=row.id,
                kind=row.kind,
                scheduled_for=row.scheduled_for,
                status=row.status,
                skip_reason=row.skip_reason,
                missed_slots=row.missed_slots,
                config_version=row.config_version,
                query_run_id=row.query_run_id,
                run_status=run.status if run else None,
                run_error_code=run.error_code if run else None,
                connector_outcomes=outcomes.get(row.query_run_id, []) if row.query_run_id else [],
                changes=changes.get(row.query_run_id, []) if row.query_run_id else [],
                dispatched_by=row.dispatched_by,
                created_at=row.created_at,
            )
        )
    return result


def monitor_out(db: Session, monitor: Monitor) -> MonitorOut:
    query = db.get(SavedQuery, monitor.saved_query_id)
    assert query is not None
    authorizer = (
        db.get(User, monitor.authorized_by_user_id) if monitor.authorized_by_user_id else None
    )
    active = service.active_run(db, monitor.id)
    last = db.scalar(
        select(MonitorOccurrence)
        .where(MonitorOccurrence.monitor_id == monitor.id)
        .order_by(MonitorOccurrence.created_at.desc())
        .limit(1)
    )
    requirements = budgets.monitor_requirements(
        monitor.id, monitor.budget
    ) + budgets.case_requirements(db, monitor.case_id)
    usage = [budgets.current_usage(db, requirement) for requirement in requirements]
    actions: list[ActionItem] = []
    query_changed = service.query_fingerprint(query) != monitor.query_fingerprint
    if monitor.status == MonitorStatus.PAUSED and monitor.status_reason in REASON_MESSAGES:
        actions.append(
            ActionItem(code=monitor.status_reason, message=REASON_MESSAGES[monitor.status_reason])
        )
    elif query_changed:
        actions.append(ActionItem(code="query_changed", message=REASON_MESSAGES["query_changed"]))
    for item in usage:
        if item.exhausted:
            reset = item.period_end.isoformat() if item.period_end else "the next execution"
            actions.append(
                ActionItem(
                    code="budget_exhausted",
                    message=(
                        f"The {item.scope_type} {item.metric.replace('_', ' ')} budget for this "
                        f"{item.period} is used up; scheduled runs are skipped until {reset} "
                        "(UTC) unless the limit is raised."
                    ),
                )
            )
    last_out = occurrences_out(db, [last])[0] if last is not None else None
    if last_out is not None and last_out.run_status == RunStatus.FAILED:
        actions.append(
            ActionItem(
                code="last_run_failed",
                message="The last execution failed; open it to see each source's outcome.",
            )
        )
    return MonitorOut(
        id=monitor.id,
        case_id=monitor.case_id,
        saved_query_id=monitor.saved_query_id,
        saved_query_name=query.name,
        name=monitor.name,
        description=monitor.description,
        status=monitor.status,
        status_reason=monitor.status_reason,
        status_changed_at=monitor.status_changed_at,
        schedule=monitor.schedule,
        timezone=monitor.timezone,
        missed_run_policy=monitor.missed_run_policy,
        connector_ids=list(monitor.connector_ids),
        scope=monitor.scope,
        limits=monitor.limits,
        budget=monitor.budget,
        retention=monitor.retention,
        notify=monitor.notify,
        config_version=monitor.config_version,
        collects_live=service.collects_live(query, list(monitor.connector_ids)),
        query_changed=query_changed,
        authorized_by=authorizer.username if authorizer else None,
        next_run_at=monitor.next_run_at,
        last_scheduled_for=monitor.last_scheduled_for,
        consecutive_failures=monitor.consecutive_failures,
        active_run_id=active.id if active else None,
        last_occurrence=last_out,
        budget_usage=[usage_out(item) for item in usage],
        actions=actions,
        created_at=monitor.created_at,
        updated_at=monitor.updated_at,
    )


def _user(db: Session, principal: PrincipalDep) -> User:
    user = db.get(User, principal.user.id)
    assert user is not None
    return user


@router.get("")
def list_monitors(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    saved_query_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[MonitorOut]:
    conditions = [Monitor.case_id == case.id]
    if saved_query_id is not None:
        conditions.append(Monitor.saved_query_id == saved_query_id)
    total = db.scalar(select(func.count()).select_from(Monitor).where(*conditions)) or 0
    rows = db.scalars(
        select(Monitor)
        .where(*conditions)
        .order_by(Monitor.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[monitor_out(db, row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_monitor(
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    body: MonitorCreate,
) -> MonitorOut:
    monitor = service.create_monitor(db, settings, actor, _user(db, principal), case, body)
    db.commit()
    return monitor_out(db, monitor)


@router.get("/{monitor_id}")
def get_monitor(case: ReadableCase, db: DbDep, monitor_id: uuid.UUID) -> MonitorOut:
    return monitor_out(db, service.get_monitor(db, case.id, monitor_id))


@router.patch("/{monitor_id}")
def update_monitor(
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    monitor_id: uuid.UUID,
    body: MonitorUpdate,
) -> MonitorOut:
    monitor = service.get_monitor(db, case.id, monitor_id, lock=True)
    service.update_monitor(db, settings, actor, _user(db, principal), monitor, body)
    db.commit()
    return monitor_out(db, monitor)


@router.post("/{monitor_id}/pause")
def pause_monitor(
    case: AnalystCase, db: DbDep, actor: ActorDep, monitor_id: uuid.UUID
) -> MonitorOut:
    monitor = service.get_monitor(db, case.id, monitor_id, lock=True)
    service.pause_monitor(db, actor, monitor)
    db.commit()
    return monitor_out(db, monitor)


@router.post("/{monitor_id}/resume")
def resume_monitor(
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    monitor_id: uuid.UUID,
    body: MonitorResume,
) -> MonitorOut:
    monitor = service.get_monitor(db, case.id, monitor_id, lock=True)
    service.resume_monitor(db, settings, actor, _user(db, principal), monitor, body)
    db.commit()
    return monitor_out(db, monitor)


@router.post("/{monitor_id}/disable")
def disable_monitor(
    case: AnalystCase, db: DbDep, principal: PrincipalDep, actor: ActorDep, monitor_id: uuid.UUID
) -> MonitorOut:
    monitor = service.get_monitor(db, case.id, monitor_id, lock=True)
    service.disable_monitor(db, actor, _user(db, principal), monitor)
    db.commit()
    return monitor_out(db, monitor)


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_monitor(case: AnalystCase, db: DbDep, actor: ActorDep, monitor_id: uuid.UUID) -> None:
    monitor = service.get_monitor(db, case.id, monitor_id, lock=True)
    service.delete_monitor(db, actor, monitor)
    db.commit()


@router.post("/{monitor_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def run_monitor_now(
    request: Request,
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    monitor_id: uuid.UUID,
) -> OccurrenceOut:
    monitor = service.get_monitor(db, case.id, monitor_id, lock=True)
    result = service.run_now(db, settings, actor, _user(db, principal), monitor)
    db.commit()
    if result.outbox_id is not None:
        state = request.app.state
        dispatch.publish_after_commit(
            state.session_factory, state.celery, state.settings, result.outbox_id
        )
    db.refresh(result.occurrence)
    return occurrences_out(db, [result.occurrence])[0]


@router.get("/{monitor_id}/occurrences")
def list_occurrences(
    case: ReadableCase,
    db: DbDep,
    monitor_id: uuid.UUID,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    status_filter: Annotated[
        str | None, Query(alias="status", pattern="^(dispatched|skipped)$")
    ] = None,
) -> Page[OccurrenceOut]:
    monitor = service.get_monitor(db, case.id, monitor_id)
    conditions = [MonitorOccurrence.monitor_id == monitor.id]
    if status_filter:
        conditions.append(MonitorOccurrence.status == status_filter)
    total = db.scalar(select(func.count()).select_from(MonitorOccurrence).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(MonitorOccurrence)
            .where(*conditions)
            .order_by(MonitorOccurrence.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(items=occurrences_out(db, rows), total=total, limit=limit, offset=offset)
