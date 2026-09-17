"""Monitor lifecycle and occurrence dispatch.

Semantics (docs/monitoring/README.md):

* **Created paused.** A monitor collects only after an analyst enables it; enabling one whose
  connectors contact external sources needs an explicit acknowledgement.
* **Pause** stops future occurrences; an execution already queued or running continues.
  **Cancel** (on the execution) stops only that execution. **Disable** stops future occurrences
  and cancels the active execution.
* **Query edits** pause enabled monitors of that query (``query_changed``). Resuming adopts the
  new definition and starts a new change-detection baseline. Queued and running executions keep
  the snapshot they were created with.
* **Dispatch** re-checks the case, the authorizing analyst's access, the pinned query definition,
  the connectors, overlapping executions and budgets. A slot that fails a check is recorded as a
  skipped occurrence with its reason; nothing is retried in a burst.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.service import Actor, record
from app.auth.models import User
from app.budgets import service as budgets
from app.budgets.models import BudgetPeriod
from app.cases.access import has_analyst_access
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.connectors.base import CollectionMode
from app.connectors.registry import get_connector
from app.db.base import utcnow
from app.monitoring import schedule as schedules
from app.monitoring.models import (
    Monitor,
    MonitorOccurrence,
    MonitorStatus,
    OccurrenceKind,
    OccurrenceStatus,
)
from app.monitoring.schemas import MonitorCreate, MonitorResume, MonitorUpdate
from app.notifications import service as notifications
from app.notifications.models import EventType, Severity
from app.queries import service as queries
from app.queries.models import TERMINAL_RUN_STATUSES, QueryRun, RunStatus, SavedQuery

logger = logging.getLogger(__name__)

SCHEDULER = "scheduler"
_ACTIVE = (RunStatus.QUEUED, RunStatus.RUNNING)


def _unprocessable(code: str, message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": code, "message": message}
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def query_fingerprint(query: SavedQuery) -> str:
    """Digest of everything that decides what a query collects."""
    definition = {
        "input_type": query.input_type,
        "input_value": query.input_value,
        "connector_ids": sorted(query.connector_ids),
        "collection_mode": query.collection_mode,
        "parameters": query.parameters,
        "limits": query.limits,
    }
    canonical = json.dumps(definition, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def collects_live(query: SavedQuery, connector_ids: list[str]) -> bool:
    for connector_id in connector_ids:
        connector = get_connector(connector_id)
        if connector is None or not connector.descriptor.synthetic:
            return True
    return query.collection_mode != CollectionMode.SYNTHETIC_FIXTURE


def parse_schedule(settings: Settings, monitor: Monitor) -> schedules.Schedule:
    return schedules.parse(
        monitor.schedule,
        monitor.timezone,
        min_interval_minutes=settings.monitor_min_interval_minutes,
    )


def _validate(
    settings: Settings,
    query: SavedQuery,
    *,
    connector_ids: list[str],
    schedule: dict[str, Any],
    timezone: str,
    scope: dict[str, Any],
) -> None:
    if not connector_ids or len(set(connector_ids)) != len(connector_ids):
        raise _unprocessable("invalid_monitor", "Select at least one connector, each once.")
    unknown = sorted(set(connector_ids) - set(query.connector_ids))
    if unknown:
        raise _unprocessable(
            "invalid_monitor",
            f"Connectors not in the saved query: {', '.join(unknown)}.",
        )
    try:
        schedules.parse(
            schedule, timezone, min_interval_minutes=settings.monitor_min_interval_minutes
        )
    except schedules.ScheduleError as exc:
        raise _unprocessable("invalid_schedule", str(exc)) from None
    for key in ("max_pages", "max_items_per_page"):
        allowed = int(query.limits.get(key, 0) or 0)
        if allowed and int(scope[key]) > allowed:
            raise _unprocessable(
                "scope_exceeds_query",
                f"{key} ({scope[key]}) is above the saved query's own limit ({allowed}).",
            )


def config_snapshot(monitor: Monitor) -> dict[str, Any]:
    return {
        "id": str(monitor.id),
        "name": monitor.name,
        "config_version": monitor.config_version,
        "schedule": monitor.schedule,
        "timezone": monitor.timezone,
        "missed_run_policy": monitor.missed_run_policy,
        "connector_ids": list(monitor.connector_ids),
        "scope": monitor.scope,
        "limits": monitor.limits,
        "budget": monitor.budget,
        "retention": monitor.retention,
        "query_fingerprint": monitor.query_fingerprint,
    }


def _change_status(monitor: Monitor, new_status: MonitorStatus, reason: str | None) -> None:
    monitor.status = new_status
    monitor.status_reason = reason
    monitor.status_changed_at = utcnow()
    if new_status != MonitorStatus.ENABLED:
        monitor.next_run_at = None


def _schedule_next(settings: Settings, monitor: Monitor, after: datetime) -> None:
    schedule = parse_schedule(settings, monitor)
    monitor.next_run_at = schedules.next_after(schedule, after, anchor=monitor.schedule_anchor)


def get_saved_query(db: Session, case_id: uuid.UUID, query_id: uuid.UUID) -> SavedQuery:
    return queries.get_saved_query(db, case_id, query_id)


def get_monitor(
    db: Session, case_id: uuid.UUID, monitor_id: uuid.UUID, *, lock: bool = False
) -> Monitor:
    statement = select(Monitor).where(Monitor.id == monitor_id, Monitor.case_id == case_id)
    if lock:
        statement = statement.with_for_update()
    monitor = db.scalar(statement)
    if monitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="monitor_not_found")
    return monitor


def active_run(db: Session, monitor_id: uuid.UUID) -> QueryRun | None:
    return db.scalar(
        select(QueryRun)
        .where(QueryRun.monitor_id == monitor_id, QueryRun.status.in_(_ACTIVE))
        .order_by(QueryRun.queued_at.desc())
        .limit(1)
    )


def _require_acknowledgement(
    query: SavedQuery, connector_ids: list[str], acknowledged: bool
) -> None:
    if collects_live(query, connector_ids) and not acknowledged:
        raise _unprocessable(
            "recurring_collection_not_acknowledged",
            "This monitor contacts external sources on a schedule. Confirm recurring collection "
            "to enable it.",
        )


def create_monitor(
    db: Session, settings: Settings, actor: Actor, user: User, case: Case, body: MonitorCreate
) -> Monitor:
    query = get_saved_query(db, case.id, body.saved_query_id)
    connector_ids = list(body.connector_ids or query.connector_ids)
    scope = body.scope.model_dump()
    _validate(
        settings,
        query,
        connector_ids=connector_ids,
        schedule=body.schedule.as_dict(),
        timezone=body.timezone,
        scope=scope,
    )
    if body.enable:
        _require_acknowledgement(query, connector_ids, body.acknowledge_recurring_collection)
    now = utcnow()
    monitor = Monitor(
        id=uuid.uuid4(),
        case_id=case.id,
        saved_query_id=query.id,
        name=body.name,
        description=body.description,
        status=MonitorStatus.PAUSED,
        status_reason="created",
        status_changed_at=now,
        schedule=body.schedule.as_dict(),
        timezone=body.timezone,
        schedule_anchor=now,
        missed_run_policy=body.missed_run_policy,
        connector_ids=connector_ids,
        scope=scope,
        limits=body.limits.model_dump(),
        budget=body.budget.model_dump(),
        retention=body.retention.model_dump(),
        notify=body.notify.model_dump(),
        query_fingerprint=query_fingerprint(query),
        config_version=1,
        authorized_by_user_id=user.id,
        created_by_user_id=user.id,
    )
    db.add(monitor)
    db.flush()
    if body.enable:
        _change_status(monitor, MonitorStatus.ENABLED, None)
        _schedule_next(settings, monitor, now)
    record(
        db,
        actor,
        "monitor.created",
        case_id=case.id,
        target_type="monitor",
        target_id=monitor.id,
        details={
            "saved_query_id": query.id,
            "enabled": body.enable,
            "schedule": monitor.schedule,
            "timezone": monitor.timezone,
            "limits": monitor.limits,
            "budget": monitor.budget,
            "collects_live": collects_live(query, connector_ids),
        },
    )
    return monitor


def update_monitor(
    db: Session, settings: Settings, actor: Actor, user: User, monitor: Monitor, body: MonitorUpdate
) -> Monitor:
    query = db.get(SavedQuery, monitor.saved_query_id)
    assert query is not None
    values = body.model_dump(exclude_unset=True)
    connector_ids = list(values.get("connector_ids") or monitor.connector_ids)
    schedule = body.schedule.as_dict() if body.schedule is not None else monitor.schedule
    timezone = body.timezone or monitor.timezone
    scope = body.scope.model_dump() if body.scope is not None else monitor.scope
    _validate(
        settings,
        query,
        connector_ids=connector_ids,
        schedule=schedule,
        timezone=timezone,
        scope=scope,
    )
    if (
        monitor.status == MonitorStatus.ENABLED
        and collects_live(query, connector_ids)
        and not collects_live(query, list(monitor.connector_ids))
    ):
        raise _conflict(
            "recurring_collection_not_acknowledged",
            "Pause the monitor before adding connectors that contact external sources, then "
            "enable it again with the acknowledgement.",
        )
    changed: list[str] = []
    for key in ("name", "description", "missed_run_policy"):
        if key in values and values[key] is not None and getattr(monitor, key) != values[key]:
            setattr(monitor, key, values[key])
            changed.append(key)
    replacements: dict[str, Any] = {
        "connector_ids": connector_ids,
        "schedule": schedule,
        "timezone": timezone,
        "scope": scope,
        "limits": body.limits.model_dump() if body.limits is not None else monitor.limits,
        "budget": body.budget.model_dump() if body.budget is not None else monitor.budget,
        "retention": body.retention.model_dump()
        if body.retention is not None
        else monitor.retention,
        "notify": body.notify.model_dump() if body.notify is not None else monitor.notify,
    }
    for key, value in replacements.items():
        current = list(getattr(monitor, key)) if key == "connector_ids" else getattr(monitor, key)
        if current != value:
            setattr(monitor, key, value)
            changed.append(key)
    if not changed:
        return monitor
    monitor.config_version += 1
    if {"schedule", "timezone"} & set(changed):
        monitor.schedule_anchor = utcnow()
        if monitor.status == MonitorStatus.ENABLED:
            _schedule_next(settings, monitor, utcnow())
    if "budget" in changed:
        for requirement in budgets.monitor_requirements(monitor.id, monitor.budget):
            budgets.apply_limit_change(db, requirement)
    record(
        db,
        actor,
        "monitor.updated",
        case_id=monitor.case_id,
        target_type="monitor",
        target_id=monitor.id,
        details={"fields": changed, "config_version": monitor.config_version},
    )
    return monitor


def pause_monitor(db: Session, actor: Actor, monitor: Monitor, *, reason: str = "manual") -> None:
    if monitor.status != MonitorStatus.ENABLED:
        raise _conflict("monitor_not_enabled", "Only an enabled monitor can be paused.")
    _change_status(monitor, MonitorStatus.PAUSED, reason)
    record(
        db,
        actor,
        "monitor.paused",
        case_id=monitor.case_id,
        target_type="monitor",
        target_id=monitor.id,
        details={"reason": reason},
    )


def resume_monitor(
    db: Session, settings: Settings, actor: Actor, user: User, monitor: Monitor, body: MonitorResume
) -> None:
    if monitor.status == MonitorStatus.ENABLED:
        raise _conflict("monitor_already_enabled", "The monitor is already enabled.")
    case = db.get(Case, monitor.case_id)
    if case is None or case.status != CaseStatus.ACTIVE:
        raise _conflict("case_archived", "Restore the case before enabling its monitors.")
    query = db.get(SavedQuery, monitor.saved_query_id)
    assert query is not None
    fingerprint = query_fingerprint(query)
    details: dict[str, Any] = {"previous_status": monitor.status}
    if fingerprint != monitor.query_fingerprint:
        if not body.adopt_query_changes:
            raise _conflict(
                "query_changed",
                "The saved query changed since this monitor was configured. Review the query and "
                "confirm to adopt the new definition.",
            )
        unknown = sorted(set(monitor.connector_ids) - set(query.connector_ids))
        if unknown:
            raise _conflict(
                "query_changed",
                f"The saved query no longer uses: {', '.join(unknown)}. Edit the monitor first.",
            )
        monitor.query_fingerprint = fingerprint
        monitor.config_version += 1
        details["adopted_query_changes"] = True
    try:
        queries.validate_definition(
            query.input_type, query.input_value, query.connector_ids, query.parameters
        )
    except HTTPException as exc:
        raise _conflict("query_invalid", "The saved query is no longer valid.") from exc
    _require_acknowledgement(
        query, list(monitor.connector_ids), body.acknowledge_recurring_collection
    )
    monitor.authorized_by_user_id = user.id
    monitor.consecutive_failures = 0
    _change_status(monitor, MonitorStatus.ENABLED, None)
    # Resuming never catches up the paused period.
    _schedule_next(settings, monitor, utcnow())
    record(
        db,
        actor,
        "monitor.enabled",
        case_id=monitor.case_id,
        target_type="monitor",
        target_id=monitor.id,
        details={**details, "collects_live": collects_live(query, list(monitor.connector_ids))},
    )


def disable_monitor(db: Session, actor: Actor, user: User, monitor: Monitor) -> uuid.UUID | None:
    if monitor.status == MonitorStatus.DISABLED:
        raise _conflict("monitor_already_disabled", "The monitor is already disabled.")
    previous = monitor.status
    _change_status(monitor, MonitorStatus.DISABLED, "manual")
    run = active_run(db, monitor.id)
    canceled = None
    if run is not None:
        queries.request_cancel(db, run, user)
        canceled = run.id
    record(
        db,
        actor,
        "monitor.disabled",
        case_id=monitor.case_id,
        target_type="monitor",
        target_id=monitor.id,
        details={"previous_status": previous, "canceled_run_id": canceled},
    )
    return canceled


def delete_monitor(db: Session, actor: Actor, monitor: Monitor) -> None:
    if monitor.status != MonitorStatus.DISABLED:
        raise _conflict("monitor_not_disabled", "Disable the monitor before deleting it.")
    if active_run(db, monitor.id) is not None:
        raise _conflict("monitor_run_active", "Wait for the canceled execution to stop.")
    record(
        db,
        actor,
        "monitor.deleted",
        case_id=monitor.case_id,
        target_type="monitor",
        target_id=monitor.id,
        details={"name": monitor.name},
    )
    db.delete(monitor)


def pause_for_query_change(db: Session, actor: Actor, query: SavedQuery) -> int:
    """Pause enabled monitors whose pinned definition no longer matches the edited query."""
    fingerprint = query_fingerprint(query)
    paused = 0
    for monitor in db.scalars(
        select(Monitor)
        .where(Monitor.saved_query_id == query.id, Monitor.status == MonitorStatus.ENABLED)
        .with_for_update()
    ):
        if monitor.query_fingerprint == fingerprint:
            continue
        pause_monitor(db, actor, monitor, reason="query_changed")
        paused += 1
    return paused


# -- dispatch ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchResult:
    occurrence: MonitorOccurrence
    outbox_id: uuid.UUID | None


def _skip_notification(
    db: Session, settings: Settings, monitor: Monitor, occurrence: MonitorOccurrence, reason: str
) -> None:
    if reason == "budget_exhausted":
        if not monitor.notify.get("on_budget_exhausted", True):
            return
        usage_period = str(monitor.budget.get("period", "day"))
        start, _end = budgets.period_bounds(BudgetPeriod(usage_period), utcnow())
        notifications.emit(
            db,
            settings,
            notifications.Event(
                event_type=EventType.BUDGET_EXHAUSTED,
                severity=Severity.WARNING,
                case_id=monitor.case_id,
                monitor=monitor,
                title=f"Monitor '{monitor.name}' skipped: budget used up",
                body=(
                    "Scheduled runs are skipped until the budget period ends "
                    f"({usage_period}, UTC) or an analyst raises the limit."
                ),
                link=f"/cases/{monitor.case_id}/monitors/{monitor.id}",
                dedupe_key=f"budget:{monitor.id}:{start.date().isoformat()}",
                recipients=monitor.notify.get("recipients", "case_analysts"),
                summary={"reason": reason, "scope": "monitor", "period": usage_period},
                occurrence_id=occurrence.id,
            ),
        )
        return
    if reason in ("overlap", "missed"):
        return
    if not monitor.notify.get("on_failure", True):
        return
    messages = {
        "authorization_lost": (
            "The analyst who enabled this monitor no longer has analyst access to the case. "
            "An analyst must resume it to authorize further runs."
        ),
        "query_changed": (
            "The saved query changed. Review the new definition and resume the monitor to adopt "
            "it; change detection starts a new baseline."
        ),
        "query_invalid": "The saved query is no longer valid; edit it or the monitor.",
        "case_inactive": "The case was archived; restore it to resume monitoring.",
    }
    notifications.emit(
        db,
        settings,
        notifications.Event(
            event_type=EventType.ACTION_REQUIRED,
            severity=Severity.ACTION_REQUIRED,
            case_id=monitor.case_id,
            monitor=monitor,
            title=f"Monitor '{monitor.name}' paused",
            body=messages.get(reason, "The monitor needs attention."),
            link=f"/cases/{monitor.case_id}/monitors/{monitor.id}",
            dedupe_key=f"paused:{monitor.id}:{reason}:{monitor.status_changed_at.isoformat()}",
            recipients="case_analysts",
            summary={"reason": reason, "status": str(monitor.status)},
            occurrence_id=occurrence.id,
        ),
    )


def _budget_blocker(db: Session, monitor: Monitor) -> bool:
    requirements = budgets.case_requirements(db, monitor.case_id) + budgets.monitor_requirements(
        monitor.id, monitor.budget
    )
    return any(budgets.current_usage(db, requirement).exhausted for requirement in requirements)


def dispatch_occurrence(
    db: Session,
    settings: Settings,
    monitor: Monitor,
    *,
    scheduled_for: datetime,
    kind: OccurrenceKind,
    missed_slots: int,
    dispatched_by: str,
    actor: Actor,
    requester: User | None = None,
) -> DispatchResult | None:
    """Create the occurrence for one slot: dispatched with its execution, or skipped with a reason.

    Returns None when the slot already has an occurrence (another scheduler recorded it).
    """
    snapshot = config_snapshot(monitor)
    occurrence_id = uuid.uuid4()
    inserted = db.scalar(
        insert(MonitorOccurrence)
        .values(
            id=occurrence_id,
            monitor_id=monitor.id,
            case_id=monitor.case_id,
            kind=kind,
            scheduled_for=scheduled_for,
            status=OccurrenceStatus.SKIPPED,
            skip_reason="pending",
            missed_slots=missed_slots,
            config_version=monitor.config_version,
            config_snapshot=snapshot,
            dispatched_by=dispatched_by[:100],
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["monitor_id", "scheduled_for"])
        .returning(MonitorOccurrence.id)
    )
    if inserted is None:
        return None
    occurrence = db.get(MonitorOccurrence, occurrence_id)
    assert occurrence is not None

    reason, pause_reason = _dispatch_blocker(db, monitor, kind, requester)
    if reason is not None:
        occurrence.skip_reason = reason
        if pause_reason is not None and monitor.status == MonitorStatus.ENABLED:
            _change_status(monitor, MonitorStatus.PAUSED, pause_reason)
        record(
            db,
            actor,
            "monitor.occurrence_skipped",
            case_id=monitor.case_id,
            target_type="monitor_occurrence",
            target_id=occurrence.id,
            correlation_id=occurrence.id,
            details={
                "monitor_id": monitor.id,
                "reason": reason,
                "scheduled_for": scheduled_for.isoformat(),
                "missed_slots": missed_slots,
                "paused": pause_reason is not None,
            },
        )
        db.flush()
        _skip_notification(db, settings, monitor, occurrence, reason)
        return DispatchResult(occurrence=occurrence, outbox_id=None)

    query = db.get(SavedQuery, monitor.saved_query_id)
    authorizer = requester or db.get(User, monitor.authorized_by_user_id)
    assert query is not None
    assert authorizer is not None
    scope = monitor.scope
    limits = {
        "max_pages": int(scope["max_pages"]),
        "max_items_per_page": int(scope["max_items_per_page"]),
        "max_requests": int(monitor.limits["max_requests_per_run"]),
        "max_items_per_run": int(monitor.limits["max_items_per_run"]),
        "max_run_seconds": int(monitor.limits["max_run_seconds"]),
    }
    run_snapshot = {**snapshot, "occurrence_id": str(occurrence.id), "kind": str(kind)}
    run, outbox = queries.create_run(
        db,
        query,
        authorizer,
        connector_ids=list(monitor.connector_ids),
        limits=limits,
        monitor=run_snapshot,
        monitor_id=monitor.id,
    )
    occurrence.status = OccurrenceStatus.DISPATCHED
    occurrence.skip_reason = None
    occurrence.query_run_id = run.id
    record(
        db,
        actor,
        "monitor.dispatched",
        case_id=monitor.case_id,
        target_type="query_run",
        target_id=run.id,
        correlation_id=occurrence.id,
        details={
            "monitor_id": monitor.id,
            "occurrence_id": occurrence.id,
            "kind": str(kind),
            "scheduled_for": scheduled_for.isoformat(),
            "missed_slots": missed_slots,
            "config_version": monitor.config_version,
            "limits": limits,
        },
    )
    db.flush()
    return DispatchResult(occurrence=occurrence, outbox_id=outbox.id)


def record_missed_slot(
    db: Session,
    monitor: Monitor,
    *,
    scheduled_for: datetime,
    missed_slots: int,
    dispatched_by: str,
    actor: Actor,
) -> DispatchResult | None:
    """Record a slot that passed while no scheduler ran (``missed_run_policy = skip``)."""
    occurrence_id = uuid.uuid4()
    inserted = db.scalar(
        insert(MonitorOccurrence)
        .values(
            id=occurrence_id,
            monitor_id=monitor.id,
            case_id=monitor.case_id,
            kind=OccurrenceKind.SCHEDULED,
            scheduled_for=scheduled_for,
            status=OccurrenceStatus.SKIPPED,
            skip_reason="missed",
            missed_slots=missed_slots,
            config_version=monitor.config_version,
            config_snapshot=config_snapshot(monitor),
            dispatched_by=dispatched_by[:100],
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["monitor_id", "scheduled_for"])
        .returning(MonitorOccurrence.id)
    )
    if inserted is None:
        return None
    record(
        db,
        actor,
        "monitor.occurrence_skipped",
        case_id=monitor.case_id,
        target_type="monitor_occurrence",
        target_id=occurrence_id,
        correlation_id=occurrence_id,
        details={
            "monitor_id": monitor.id,
            "reason": "missed",
            "scheduled_for": scheduled_for.isoformat(),
            "missed_slots": missed_slots,
        },
    )
    occurrence = db.get(MonitorOccurrence, occurrence_id)
    assert occurrence is not None
    return DispatchResult(occurrence=occurrence, outbox_id=None)


def _dispatch_blocker(
    db: Session, monitor: Monitor, kind: OccurrenceKind, requester: User | None
) -> tuple[str | None, str | None]:
    """(skip reason, pause reason) for the first failing check, or (None, None)."""
    case = db.scalar(select(Case).where(Case.id == monitor.case_id).with_for_update(read=True))
    if case is None or case.status != CaseStatus.ACTIVE:
        return "case_inactive", "case_archived"
    authorizer_id = requester.id if requester is not None else monitor.authorized_by_user_id
    if not has_analyst_access(db, authorizer_id, monitor.case_id):
        return "authorization_lost", "authorization_lost"
    query = db.get(SavedQuery, monitor.saved_query_id)
    if query is None or query_fingerprint(query) != monitor.query_fingerprint:
        return "query_changed", "query_changed"
    try:
        queries.validate_definition(
            query.input_type, query.input_value, query.connector_ids, query.parameters
        )
    except HTTPException:
        return "query_invalid", "query_invalid"
    if active_run(db, monitor.id) is not None:
        return "overlap", None
    if _budget_blocker(db, monitor):
        return "budget_exhausted", None
    return None, None


def run_now(
    db: Session, settings: Settings, actor: Actor, user: User, monitor: Monitor
) -> DispatchResult:
    if monitor.status == MonitorStatus.DISABLED:
        raise _conflict("monitor_disabled", "Enable the monitor before running it.")
    query = db.get(SavedQuery, monitor.saved_query_id)
    assert query is not None
    result = dispatch_occurrence(
        db,
        settings,
        monitor,
        scheduled_for=utcnow(),
        kind=OccurrenceKind.MANUAL,
        missed_slots=0,
        dispatched_by=f"user:{user.username}"[:100],
        actor=actor,
        requester=user,
    )
    assert result is not None
    if result.occurrence.status == OccurrenceStatus.SKIPPED:
        messages = {
            "overlap": "An execution of this monitor is still queued or running.",
            "budget_exhausted": "A budget for this monitor or case is used up for this period.",
            "query_changed": "The saved query changed; resume the monitor to adopt it first.",
            "query_invalid": "The saved query is no longer valid.",
            "case_inactive": "The case is not active.",
            "authorization_lost": "You no longer have analyst access to this case.",
        }
        reason = result.occurrence.skip_reason or "skipped"
        # Keep the skipped occurrence as a record of the attempt.
        db.commit()
        raise _conflict(reason, messages.get(reason, "The run was not started."))
    return result


# -- execution results -------------------------------------------------------------------------


def record_run_finished(db: Session, settings: Settings, run: QueryRun) -> None:
    """Called in the transaction that finalizes a monitor's execution."""
    if run.monitor_id is None or run.status not in TERMINAL_RUN_STATUSES:
        return
    monitor = db.scalar(select(Monitor).where(Monitor.id == run.monitor_id).with_for_update())
    if monitor is None:
        return
    if run.status == RunStatus.FAILED:
        monitor.consecutive_failures += 1
    elif run.status in (RunStatus.COMPLETED, RunStatus.PARTIAL):
        monitor.consecutive_failures = 0
    if (
        monitor.status == MonitorStatus.ENABLED
        and monitor.consecutive_failures >= settings.monitor_max_consecutive_failures
    ):
        _change_status(monitor, MonitorStatus.PAUSED, "repeated_failures")
        notifications.emit(
            db,
            settings,
            notifications.Event(
                event_type=EventType.ACTION_REQUIRED,
                severity=Severity.ACTION_REQUIRED,
                case_id=monitor.case_id,
                monitor=monitor,
                title=f"Monitor '{monitor.name}' paused after repeated failures",
                body=(
                    f"The last {monitor.consecutive_failures} executions failed. Check the run "
                    "outcomes, fix the cause and resume the monitor."
                ),
                link=f"/cases/{monitor.case_id}/monitors/{monitor.id}",
                dedupe_key=f"paused:{monitor.id}:repeated_failures:{run.id}",
                recipients="case_analysts",
                summary={"reason": "repeated_failures", "run_status": str(run.status)},
                query_run_id=run.id,
            ),
        )


def occurrence_counts(db: Session, monitor_id: uuid.UUID) -> dict[str, int]:
    return {
        f"{row[0]}:{row[1] or ''}": int(row[2])
        for row in db.execute(
            select(
                MonitorOccurrence.status,
                MonitorOccurrence.skip_reason,
                func.count().label("count"),
            )
            .where(MonitorOccurrence.monitor_id == monitor_id)
            .group_by(MonitorOccurrence.status, MonitorOccurrence.skip_reason)
        )
    }


def pause_all_monitors(db: Session, actor: Actor, reason: str) -> int:
    """Pause every enabled monitor of the installation (operator command, for example after a
    restore, so restored monitors never resume scheduled collection on their own)."""
    return _pause_where(db, actor, reason, Monitor.status == MonitorStatus.ENABLED)


def mark_case_monitors_paused(db: Session, actor: Actor, case_id: uuid.UUID, reason: str) -> int:
    """Pause every enabled monitor of a case (archiving)."""
    return _pause_where(
        db, actor, reason, Monitor.case_id == case_id, Monitor.status == MonitorStatus.ENABLED
    )


def _pause_where(db: Session, actor: Actor, reason: str, *conditions: Any) -> int:
    result = db.execute(
        update(Monitor)
        .where(*conditions)
        .values(
            status=MonitorStatus.PAUSED,
            status_reason=reason,
            status_changed_at=utcnow(),
            next_run_at=None,
        )
        .returning(Monitor.id, Monitor.case_id)
    )
    rows = list(result.all())
    for monitor_id, case_id in rows:
        record(
            db,
            actor,
            "monitor.paused",
            case_id=case_id,
            target_type="monitor",
            target_id=monitor_id,
            details={"reason": reason},
        )
    return len(rows)
