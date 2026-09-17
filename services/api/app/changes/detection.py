"""Deterministic change detection between an execution and its compatible baseline.

For every connector run of a finished execution of a saved query:

1. **Baseline:** among earlier connector runs of the same saved query and connector that
   collected at least one page, the most recent one with complete coverage and the same
   fingerprint (connector version, input, parameters and scope limits). When the most recent
   usable run has a different fingerprint, the change set says ``baseline_incompatible``, names
   what differs and compares nothing. Without any complete comparable run, the most recent usable
   one is the baseline and items it lacks are ``unknown`` rather than new.
2. **Items** are keyed by observation type and source object ID (stable platform identifiers
   where connectors have them). The latest observation of each item in each run is compared.
3. **Classification:** new items; changed values (volatile metadata, counters and bookkeeping
   excluded, see ``app.entities.observation_diff``); items the baseline had that a *complete*
   later collection did not return (``not_observed``: absence within that scope, not deletion);
   the same items after an *incomplete* collection (``unknown``); differing values for one item
   within the run (``conflicting``).
4. Every event keeps the runs, observations and evidence it is based on. Nothing is merged,
   linked or edited, and no model is involved.

Idempotent: a connector run has at most one change set, and notifications are deduplicated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.changes.models import ChangeEvent, ChangeKind, ChangeSet, ChangeSetStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.entities.models import Observation
from app.entities.observation_diff import (
    MONITORING_EXCLUDED_FIELDS,
    collection_complete,
    field_differences,
    value_text,
)
from app.monitoring.models import Monitor, MonitorOccurrence
from app.notifications import service as notifications
from app.notifications.models import EventType, Severity
from app.queries.models import TERMINAL_RUN_STATUSES, ConnectorOutcome, ConnectorRun, QueryRun

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_SET = 500
FINGERPRINT_KEYS = (
    "connector_id",
    "connector_version",
    "input_type",
    "input_value",
    "parameters",
    "max_pages",
    "max_items_per_page",
)
ACTION_OUTCOMES = frozenset(
    {
        ConnectorOutcome.AUTHENTICATION_REQUIRED,
        ConnectorOutcome.ACCESS_DENIED,
        ConnectorOutcome.PARSE_ERROR,
        ConnectorOutcome.UNSUPPORTED,
    }
)
ACTION_ERROR_CODES = frozenset({"authorization_revoked", "worker_lost", "internal_error"})


def fingerprint_basis(run: QueryRun, connector_run: ConnectorRun) -> dict[str, Any]:
    snapshot = run.parameters_snapshot
    limits = snapshot.get("limits") or {}
    return {
        "connector_id": connector_run.connector_id,
        "connector_version": connector_run.connector_version,
        "input_type": snapshot.get("input_type"),
        "input_value": snapshot.get("input_value"),
        "parameters": snapshot.get("parameters") or {},
        "max_pages": limits.get("max_pages"),
        "max_items_per_page": limits.get("max_items_per_page"),
    }


def fingerprint(basis: dict[str, Any]) -> str:
    canonical = json.dumps(basis, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class _Item:
    observation: Observation
    values: dict[str, set[str | None]] = field(default_factory=lambda: defaultdict(set))


def _items(db: Session, connector_run_id: uuid.UUID) -> dict[tuple[str, str], _Item]:
    items: dict[tuple[str, str], _Item] = {}
    for observation in db.scalars(
        select(Observation)
        .where(Observation.connector_run_id == connector_run_id)
        .order_by(Observation.collected_at, Observation.idempotency_key)
    ):
        key = (
            observation.observation_type,
            observation.source_object_id or f"payload:{_payload_hash(observation.payload)}",
        )
        item = items.get(key)
        if item is None:
            item = items[key] = _Item(observation=observation)
        else:
            item.observation = observation
        for name, value in observation.payload.items():
            if name not in MONITORING_EXCLUDED_FIELDS:
                item.values[name].add(value_text(value))
    return items


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# How many earlier collections are searched for a complete comparable baseline.
BASELINE_SEARCH_LIMIT = 50


def _baseline(
    db: Session, run: QueryRun, connector_run: ConnectorRun
) -> tuple[QueryRun, ConnectorRun] | None:
    """The collection this one is compared with.

    Earlier usable collections (at least one page) of the same saved query and connector are
    searched newest first. The newest one with complete coverage and the same fingerprint
    (connector version, input, parameters and scope) is preferred, so a partial or rate-limited
    run in between does not become the reference for the next complete one. The search stops at
    the first collection with a different fingerprint. Without a complete comparable collection,
    the newest usable one is returned: the caller then reports it as incompatible or as an
    incomplete baseline.
    """
    rows = db.execute(
        select(QueryRun, ConnectorRun)
        .join(ConnectorRun, ConnectorRun.query_run_id == QueryRun.id)
        .where(
            QueryRun.case_id == run.case_id,
            QueryRun.saved_query_id == run.saved_query_id,
            QueryRun.id != run.id,
            QueryRun.queued_at < run.queued_at,
            QueryRun.status.in_(TERMINAL_RUN_STATUSES),
            ConnectorRun.connector_id == connector_run.connector_id,
            ConnectorRun.pages_completed > 0,
        )
        .order_by(QueryRun.queued_at.desc())
        .limit(BASELINE_SEARCH_LIMIT)
    ).all()
    if not rows:
        return None
    target = fingerprint(fingerprint_basis(run, connector_run))
    for candidate_run, candidate in rows:
        if fingerprint(fingerprint_basis(candidate_run, candidate)) != target:
            break
        if collection_complete(candidate.outcome, candidate.coverage):
            return candidate_run, candidate
    return rows[0][0], rows[0][1]


def _event(
    change_set: ChangeSet,
    kind: ChangeKind,
    key: tuple[str, str],
    *,
    previous: Observation | None,
    current: Observation | None,
    field_name: str | None = None,
    before: str | None = None,
    after: str | None = None,
    note: str,
) -> dict[str, Any]:
    reference = current or previous
    assert reference is not None
    return {
        "id": uuid.uuid4(),
        "change_set_id": change_set.id,
        "case_id": change_set.case_id,
        "kind": kind,
        "observation_type": key[0][:64],
        "source_object_id": (reference.source_object_id or None),
        "field": field_name[:100] if field_name else None,
        "previous_value": before,
        "current_value": after,
        "entity_id": reference.entity_id,
        "previous_observation_id": previous.id if previous else None,
        "current_observation_id": current.id if current else None,
        "previous_evidence_id": previous.evidence_id if previous else None,
        "current_evidence_id": current.evidence_id if current else None,
        "note": note,
        "created_at": utcnow(),
    }


def compare_connector_run(
    db: Session,
    run: QueryRun,
    connector_run: ConnectorRun,
    monitor_id: uuid.UUID | None,
    occurrence_id: uuid.UUID | None,
) -> ChangeSet:
    basis = fingerprint_basis(run, connector_run)
    complete = collection_complete(connector_run.outcome, connector_run.coverage)
    change_set = ChangeSet(
        id=uuid.uuid4(),
        case_id=run.case_id,
        monitor_id=monitor_id,
        occurrence_id=occurrence_id,
        query_run_id=run.id,
        connector_run_id=connector_run.id,
        connector_id=connector_run.connector_id,
        connector_version=connector_run.connector_version,
        status=ChangeSetStatus.UNKNOWN,
        fingerprint=fingerprint(basis),
        coverage_complete=complete,
        counts={},
        limitations=[],
    )
    limitations: list[str] = []
    counts = {kind.value: 0 for kind in ChangeKind}
    events: list[dict[str, Any]] = []
    stopped = (connector_run.coverage or {}).get("stopped_reason")
    if not complete:
        limitations.append(
            f"This collection is incomplete (status {connector_run.status}, outcome "
            f"{connector_run.outcome or 'none'}, stopped: {stopped or 'unknown'}); items it did "
            "not return are unknown, not removed."
        )

    baseline = _baseline(db, run, connector_run)
    if baseline is None:
        change_set.status = (
            ChangeSetStatus.BASELINE_ESTABLISHED
            if connector_run.pages_completed > 0
            else ChangeSetStatus.UNKNOWN
        )
        if connector_run.pages_completed == 0:
            limitations.append("No earlier collection and nothing collected: there is no baseline.")
        change_set.counts = counts
        change_set.limitations = limitations
        db.add(change_set)
        return change_set

    baseline_run, baseline_connector = baseline
    baseline_basis = fingerprint_basis(baseline_run, baseline_connector)
    change_set.baseline_query_run_id = baseline_run.id
    change_set.baseline_connector_run_id = baseline_connector.id
    baseline_complete = collection_complete(baseline_connector.outcome, baseline_connector.coverage)
    change_set.baseline_coverage_complete = baseline_complete
    if fingerprint(baseline_basis) != change_set.fingerprint:
        differing = sorted(
            key for key in FINGERPRINT_KEYS if baseline_basis.get(key) != basis.get(key)
        )
        change_set.status = ChangeSetStatus.BASELINE_INCOMPATIBLE
        limitations.append(
            "The previous collection is not comparable (different "
            + ", ".join(differing)
            + "); this collection starts a new baseline and nothing is reported as changed."
        )
        change_set.counts = counts
        change_set.limitations = limitations
        db.add(change_set)
        return change_set
    if not baseline_complete:
        limitations.append(
            "No complete comparable collection exists before this one; compared with an "
            "incomplete one, items it lacks are unknown rather than new."
        )

    current_items = _items(db, connector_run.id)
    previous_items = _items(db, baseline_connector.id)
    # Against an incomplete baseline an item it lacks may have existed already: unknown, not new.
    absent_kind = ChangeKind.NEW if baseline_complete else ChangeKind.UNKNOWN
    absent_note = (
        "Returned by this collection and not by the baseline."
        if baseline_complete
        else "Not in the incomplete baseline; whether it is new is unknown."
    )
    for key, item in current_items.items():
        previous = previous_items.get(key)
        if previous is None:
            counts[absent_kind] += 1
            events.append(
                _event(
                    change_set,
                    absent_kind,
                    key,
                    previous=None,
                    current=item.observation,
                    note=absent_note,
                )
            )
            continue
        for field_name, before, after in field_differences(
            previous.observation.payload,
            item.observation.payload,
            excluded=MONITORING_EXCLUDED_FIELDS,
        ):
            counts[ChangeKind.CHANGED] += 1
            events.append(
                _event(
                    change_set,
                    ChangeKind.CHANGED,
                    key,
                    previous=previous.observation,
                    current=item.observation,
                    field_name=field_name,
                    before=before,
                    after=after,
                    note=(
                        "The source reported a different value than in the baseline. The change "
                        "happened at an unknown time between the two collections."
                    ),
                )
            )
    for key, item in current_items.items():
        for field_name, values in item.values.items():
            distinct = sorted(value for value in values if value is not None)
            if len(distinct) > 1:
                counts[ChangeKind.CONFLICTING] += 1
                events.append(
                    _event(
                        change_set,
                        ChangeKind.CONFLICTING,
                        key,
                        previous=None,
                        current=item.observation,
                        field_name=field_name,
                        after="; ".join(distinct)[:500],
                        note=(
                            "This collection returned the same item more than once with different "
                            "values. Neither value is preferred."
                        ),
                    )
                )
    for key, item in previous_items.items():
        if key in current_items:
            continue
        kind = ChangeKind.NOT_OBSERVED if complete else ChangeKind.UNKNOWN
        counts[kind] += 1
        events.append(
            _event(
                change_set,
                kind,
                key,
                previous=item.observation,
                current=None,
                note=(
                    "In the baseline but not returned by this complete collection. This is absence "
                    "within the recorded scope, not proof of deletion: items can be hidden, made "
                    "private, moved or excluded by the source."
                    if complete
                    else "In the baseline but not returned by this incomplete collection; whether "
                    "it still exists is unknown."
                ),
            )
        )

    meaningful = (
        counts[ChangeKind.NEW]
        + counts[ChangeKind.CHANGED]
        + counts[ChangeKind.NOT_OBSERVED]
        + counts[ChangeKind.CONFLICTING]
    )
    if meaningful:
        change_set.status = ChangeSetStatus.CHANGES_DETECTED
    elif not complete or counts[ChangeKind.UNKNOWN]:
        change_set.status = ChangeSetStatus.UNKNOWN
    else:
        change_set.status = ChangeSetStatus.NO_MEANINGFUL_CHANGE
    if len(events) > MAX_EVENTS_PER_SET:
        change_set.truncated = True
        limitations.append(
            f"Only the first {MAX_EVENTS_PER_SET} of {len(events)} item events are listed."
        )
        events = events[:MAX_EVENTS_PER_SET]
    change_set.counts = counts
    change_set.limitations = limitations
    db.add(change_set)
    db.flush()
    if events:
        db.execute(insert(ChangeEvent), events)
    return change_set


def detect(session_factory: sessionmaker[Session], settings: Settings, run_id: uuid.UUID) -> str:
    with session_scope(session_factory) as db:
        run = db.scalar(select(QueryRun).where(QueryRun.id == run_id).with_for_update())
        if run is None:
            dispatch.mark_done(db, AggregateType.CHANGE_DETECTION, run_id)
            return "missing"
        if run.status not in TERMINAL_RUN_STATUSES:
            return "not_ready"
        occurrence_id = db.scalar(
            select(MonitorOccurrence.id).where(MonitorOccurrence.query_run_id == run.id)
        )
        created: list[ChangeSet] = []
        for connector_run in db.scalars(
            select(ConnectorRun)
            .where(ConnectorRun.query_run_id == run.id)
            .order_by(ConnectorRun.position)
        ):
            existing = db.scalar(
                select(ChangeSet).where(ChangeSet.connector_run_id == connector_run.id)
            )
            if existing is not None:
                created.append(existing)
                continue
            if (
                connector_run.started_at is None
                and connector_run.pages_completed == 0
                and run.started_at is None
            ):
                continue
            created.append(
                compare_connector_run(db, run, connector_run, run.monitor_id, occurrence_id)
            )
        db.flush()
        if run.monitor_id is not None:
            monitor = db.get(Monitor, run.monitor_id)
            if monitor is not None:
                _notify(db, settings, monitor, run, occurrence_id, created)
        dispatch.mark_done(db, AggregateType.CHANGE_DETECTION, run.id)
    logger.info("changes_detected", extra={"run_ref": str(run_id)[:8], "change_sets": len(created)})
    return "completed"


def _failure_signature(db: Session, run: QueryRun) -> list[str]:
    codes = set()
    if run.error_code in ACTION_ERROR_CODES:
        codes.add(str(run.error_code))
    for outcome, error_code in db.execute(
        select(ConnectorRun.outcome, ConnectorRun.last_error_code).where(
            ConnectorRun.query_run_id == run.id
        )
    ):
        if outcome in ACTION_OUTCOMES:
            codes.add(f"{outcome}:{error_code or ''}")
    if run.status == "failed" and not codes:
        codes.add("failed")
    return sorted(codes)


def _notify(
    db: Session,
    settings: Settings,
    monitor: Monitor,
    run: QueryRun,
    occurrence_id: uuid.UUID | None,
    change_sets: list[ChangeSet],
) -> None:
    link = f"/cases/{monitor.case_id}/monitors/{monitor.id}?run={run.id}"
    recipients = monitor.notify.get("recipients", "case_analysts")
    totals = {kind.value: 0 for kind in ChangeKind}
    for change_set in change_sets:
        for kind, count in (change_set.counts or {}).items():
            totals[kind] = totals.get(kind, 0) + int(count)
    meaningful = totals["new"] + totals["changed"] + totals["not_observed"] + totals["conflicting"]
    if meaningful and monitor.notify.get("on_change", True):
        parts = [
            f"{totals[k]} {label}"
            for k, label in (
                ("new", "new"),
                ("changed", "changed"),
                ("not_observed", "no longer observed"),
                ("conflicting", "conflicting"),
            )
            if totals[k]
        ]
        notifications.emit(
            db,
            settings,
            notifications.Event(
                event_type=EventType.CHANGE_DETECTED,
                severity=Severity.INFO,
                case_id=monitor.case_id,
                monitor=monitor,
                title=f"Monitor '{monitor.name}': changes detected",
                body=", ".join(parts)
                + (f"; {totals['unknown']} unknown" if totals["unknown"] else "")
                + ".",
                link=link,
                dedupe_key=f"changes:{run.id}",
                recipients=recipients,
                summary={**totals, "change_sets": len(change_sets), "run_status": str(run.status)},
                occurrence_id=occurrence_id,
                query_run_id=run.id,
            ),
        )
    if run.error_code == "budget_exhausted" and monitor.notify.get("on_budget_exhausted", True):
        from app.budgets.models import BudgetPeriod
        from app.budgets.service import period_bounds

        period = str(monitor.budget.get("period", "day"))
        start, _ = period_bounds(BudgetPeriod(period), run.finished_at or utcnow())
        notifications.emit(
            db,
            settings,
            notifications.Event(
                event_type=EventType.BUDGET_EXHAUSTED,
                severity=Severity.WARNING,
                case_id=monitor.case_id,
                monitor=monitor,
                title=f"Monitor '{monitor.name}': budget used up during a run",
                body="The run stopped before asking the source again; missing items are unknown.",
                link=link,
                dedupe_key=f"budget-run:{monitor.id}:{start.date().isoformat()}",
                recipients=recipients,
                summary={
                    "reason": "budget_exhausted",
                    "run_status": str(run.status),
                    "period": period,
                },
                occurrence_id=occurrence_id,
                query_run_id=run.id,
            ),
        )
    signature = _failure_signature(db, run)
    if signature and monitor.notify.get("on_failure", True):
        previous = db.scalar(
            select(QueryRun)
            .where(
                QueryRun.monitor_id == monitor.id,
                QueryRun.id != run.id,
                QueryRun.queued_at < run.queued_at,
                QueryRun.status.in_(TERMINAL_RUN_STATUSES),
            )
            .order_by(QueryRun.queued_at.desc())
            .limit(1)
        )
        if previous is None or _failure_signature(db, previous) != signature:
            notifications.emit(
                db,
                settings,
                notifications.Event(
                    event_type=EventType.ACTION_REQUIRED,
                    severity=Severity.ACTION_REQUIRED,
                    case_id=monitor.case_id,
                    monitor=monitor,
                    title=f"Monitor '{monitor.name}': run needs attention",
                    body="The latest run ended with: "
                    + ", ".join(signature)[:300]
                    + ". Later runs with the same problem are not notified again.",
                    link=link,
                    dedupe_key=f"failure:{run.id}",
                    recipients="case_analysts",
                    summary={
                        "reason": ",".join(signature)[:200],
                        "run_status": str(run.status),
                        "connectors_failed": len(signature),
                    },
                    occurrence_id=occurrence_id,
                    query_run_id=run.id,
                ),
            )
    if monitor.notify.get("on_completion", False):
        notifications.emit(
            db,
            settings,
            notifications.Event(
                event_type=EventType.RUN_COMPLETED,
                severity=Severity.INFO,
                case_id=monitor.case_id,
                monitor=monitor,
                title=f"Monitor '{monitor.name}': run {run.status}",
                body="Completion update requested in the monitor's notification settings.",
                link=link,
                dedupe_key=f"completed:{run.id}",
                recipients=recipients,
                summary={"run_status": str(run.status), **totals},
                occurrence_id=occurrence_id,
                query_run_id=run.id,
            ),
        )
