"""Budget enforcement shared by every worker (docs/monitoring/budgets.md).

A request is allowed only after an atomic conditional update has reserved its units on every
budget that applies to it (case, monitor and per-run limits) in one transaction. The update checks
``reserved + consumed + estimated + units <= limit`` on the row it changes, so concurrent workers
serialize on the ledger row and can never both spend the last remaining units. When the request
finishes the reservation is settled with what was measured (or released when nothing was sent).
A reservation whose holder disappeared is expired by the dispatcher and counted as an estimated
use, because the request may have reached the provider before the crash.
"""

from __future__ import annotations

import logging
import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.budgets.models import (
    BudgetLedger,
    BudgetMetric,
    BudgetPeriod,
    BudgetReservation,
    BudgetScope,
    CaseBudget,
    ReservationStatus,
)
from app.db.base import utcnow
from app.db.session import session_scope

logger = logging.getLogger(__name__)

RUN_PERIOD_START = datetime(1970, 1, 1, tzinfo=UTC)


class BudgetExhaustedError(Exception):
    """A request was refused because a budget has no remaining units."""

    def __init__(self, requirement: Requirement, used: int) -> None:
        super().__init__(f"{requirement.scope_type} {requirement.metric} budget exhausted")
        self.requirement = requirement
        self.used = used

    @property
    def code(self) -> str:
        return "budget_exhausted"

    def describe(self) -> str:
        requirement = self.requirement
        scope = {"case": "case", "monitor": "monitor", "query_run": "execution"}[
            requirement.scope_type
        ]
        unit = "requests" if requirement.metric == BudgetMetric.REQUESTS else "provider units"
        period = (
            "this execution"
            if requirement.period == BudgetPeriod.RUN
            else (f"this {requirement.period} (UTC)")
        )
        return (
            f"The {scope} budget of {requirement.limit_units} {unit} for {period} is used up "
            f"({self.used} used); the source was not asked again."
        )


@dataclass(frozen=True, slots=True)
class Requirement:
    scope_type: BudgetScope
    scope_id: uuid.UUID
    metric: BudgetMetric
    period: BudgetPeriod
    limit_units: int


def period_bounds(period: BudgetPeriod, now: datetime) -> tuple[datetime, datetime | None]:
    """UTC calendar period containing ``now`` (ISO weeks start on Monday)."""
    now = now.astimezone(UTC)
    if period == BudgetPeriod.RUN:
        return RUN_PERIOD_START, None
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == BudgetPeriod.DAY:
        return day, day + timedelta(days=1)
    if period == BudgetPeriod.WEEK:
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    start = day.replace(day=1)
    days = monthrange(start.year, start.month)[1]
    return start, start + timedelta(days=days)


def case_requirements(db: Session, case_id: uuid.UUID) -> list[Requirement]:
    return [
        Requirement(
            scope_type=BudgetScope.CASE,
            scope_id=case_id,
            metric=BudgetMetric(row.metric),
            period=BudgetPeriod(row.period),
            limit_units=row.limit_units,
        )
        for row in db.scalars(select(CaseBudget).where(CaseBudget.case_id == case_id))
    ]


def monitor_requirements(monitor_id: uuid.UUID, budget: dict[str, Any]) -> list[Requirement]:
    period = BudgetPeriod(str(budget.get("period", "day")))
    result = [
        Requirement(
            scope_type=BudgetScope.MONITOR,
            scope_id=monitor_id,
            metric=BudgetMetric.REQUESTS,
            period=period,
            limit_units=int(budget["max_requests"]),
        )
    ]
    if budget.get("max_provider_units") is not None:
        result.append(
            Requirement(
                scope_type=BudgetScope.MONITOR,
                scope_id=monitor_id,
                metric=BudgetMetric.PROVIDER_UNITS,
                period=period,
                limit_units=int(budget["max_provider_units"]),
            )
        )
    return result


def run_requirements(run_id: uuid.UUID, snapshot: dict[str, Any]) -> list[Requirement]:
    max_requests = (snapshot.get("limits") or {}).get("max_requests")
    if max_requests is None:
        return []
    return [
        Requirement(
            scope_type=BudgetScope.QUERY_RUN,
            scope_id=run_id,
            metric=BudgetMetric.REQUESTS,
            period=BudgetPeriod.RUN,
            limit_units=int(max_requests),
        )
    ]


def requirements_for_run(
    db: Session, *, case_id: uuid.UUID, run_id: uuid.UUID, snapshot: dict[str, Any]
) -> list[Requirement]:
    """Every budget an execution's requests count against."""
    from app.monitoring.models import Monitor

    requirements = case_requirements(db, case_id)
    monitor_ref = snapshot.get("monitor") or {}
    if monitor_ref.get("id"):
        monitor = db.get(Monitor, uuid.UUID(str(monitor_ref["id"])))
        budget = monitor.budget if monitor is not None else monitor_ref.get("budget")
        if budget:
            requirements += monitor_requirements(uuid.UUID(str(monitor_ref["id"])), budget)
    return requirements + run_requirements(run_id, snapshot)


def _ensure_ledger(
    db: Session, case_id: uuid.UUID, requirement: Requirement, now: datetime
) -> datetime:
    start, end = period_bounds(requirement.period, now)
    db.execute(
        insert(BudgetLedger)
        .values(
            id=uuid.uuid4(),
            case_id=case_id,
            scope_type=requirement.scope_type,
            scope_id=requirement.scope_id,
            metric=requirement.metric,
            period=requirement.period,
            period_start=start,
            period_end=end,
            limit_units=requirement.limit_units,
        )
        # The limit configured now applies to the rest of the period.
        .on_conflict_do_update(
            index_elements=["scope_type", "scope_id", "metric", "period_start"],
            set_={"limit_units": requirement.limit_units},
            where=BudgetLedger.limit_units != requirement.limit_units,
        )
    )
    return start


@dataclass
class Ticket:
    id: uuid.UUID
    ledgers: int
    units: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.ledgers == 0


def acquire(
    session_factory: sessionmaker[Session],
    requirements: list[Requirement],
    *,
    case_id: uuid.UUID,
    units: dict[BudgetMetric, int],
    hold_seconds: int,
    query_run_id: uuid.UUID | None = None,
    connector_run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> Ticket:
    """Reserve ``units`` on every applicable ledger, all or nothing.

    Raises :class:`BudgetExhaustedError` (and records the refusal) when any ledger lacks room.
    """
    applicable = [r for r in requirements if units.get(r.metric, 0) > 0]
    ticket = Ticket(id=uuid.uuid4(), ledgers=0, units={str(k): v for k, v in units.items()})
    if not applicable:
        return ticket
    now = now or utcnow()
    denied: tuple[Requirement, uuid.UUID, int] | None = None
    with session_scope(session_factory) as db:
        starts = {r: _ensure_ledger(db, case_id, r, now) for r in applicable}
        # A consistent lock order prevents deadlocks between workers holding several budgets.
        ordered = sorted(
            applicable, key=lambda r: (r.scope_type, str(r.scope_id), r.metric, starts[r])
        )
        for requirement in ordered:
            amount = units[requirement.metric]
            row = db.execute(
                text(
                    "UPDATE budget_ledgers SET reserved_units = reserved_units + :amount, "
                    "updated_at = now() "
                    "WHERE scope_type = :scope_type AND scope_id = :scope_id AND metric = :metric "
                    "AND period_start = :start AND "
                    "reserved_units + consumed_units + estimated_units + :amount <= limit_units "
                    "RETURNING id"
                ),
                {
                    "amount": amount,
                    "scope_type": str(requirement.scope_type),
                    "scope_id": requirement.scope_id,
                    "metric": str(requirement.metric),
                    "start": starts[requirement],
                },
            ).first()
            if row is None:
                ledger = db.scalar(
                    select(BudgetLedger).where(
                        BudgetLedger.scope_type == requirement.scope_type,
                        BudgetLedger.scope_id == requirement.scope_id,
                        BudgetLedger.metric == requirement.metric,
                        BudgetLedger.period_start == starts[requirement],
                    )
                )
                assert ledger is not None
                used = ledger.reserved_units + ledger.consumed_units + ledger.estimated_units
                denied = (requirement, ledger.id, used)
                db.rollback()
                break
            db.add(
                BudgetReservation(
                    ticket_id=ticket.id,
                    ledger_id=row.id,
                    case_id=case_id,
                    query_run_id=query_run_id,
                    connector_run_id=connector_run_id,
                    units=amount,
                    status=ReservationStatus.HELD,
                    expires_at=now + timedelta(seconds=hold_seconds),
                )
            )
            ticket.ledgers += 1
    if denied is not None:
        requirement, ledger_id, used = denied
        with session_scope(session_factory) as db:
            db.execute(
                update(BudgetLedger)
                .where(BudgetLedger.id == ledger_id)
                .values(
                    denied_requests=BudgetLedger.denied_requests + 1,
                    exhausted_at=text("coalesce(exhausted_at, now())"),
                )
            )
        logger.info(
            "budget_request_denied",
            extra={
                "scope_type": str(requirement.scope_type),
                "metric": str(requirement.metric),
                "period": str(requirement.period),
            },
        )
        raise BudgetExhaustedError(requirement, used)
    return ticket


def settle(
    session_factory: sessionmaker[Session],
    ticket: Ticket,
    *,
    actual: dict[BudgetMetric, int] | None,
    estimated: bool = False,
    estimated_metrics: frozenset[BudgetMetric] = frozenset(),
) -> None:
    """Record what the request used (``actual=None`` releases it: nothing was sent).

    ``estimated`` marks every metric as an estimate; ``estimated_metrics`` only some (documented
    provider units next to measured requests). Idempotent: only held reservations change.
    """
    if ticket.empty:
        return
    with session_scope(session_factory) as db:
        rows = db.execute(
            select(BudgetReservation, BudgetLedger.metric)
            .join(BudgetLedger, BudgetLedger.id == BudgetReservation.ledger_id)
            .where(
                BudgetReservation.ticket_id == ticket.id,
                BudgetReservation.status == ReservationStatus.HELD,
            )
            .order_by(BudgetReservation.ledger_id)
            .with_for_update(of=BudgetReservation)
        ).all()
        now = utcnow()
        for reservation, metric in rows:
            used = 0 if actual is None else int(actual.get(BudgetMetric(metric), 0))
            is_estimate = estimated or BudgetMetric(metric) in estimated_metrics
            reservation.status = (
                ReservationStatus.RELEASED if actual is None else ReservationStatus.SETTLED
            )
            reservation.settled_units = used
            reservation.estimated = is_estimate and actual is not None
            reservation.settled_at = now
            measured = 0 if is_estimate else used
            counted_estimate = used if is_estimate else 0
            db.execute(
                text(
                    "UPDATE budget_ledgers SET "
                    "reserved_units = reserved_units - :reserved, "
                    "consumed_units = consumed_units + :measured, "
                    "estimated_units = estimated_units + :estimated, "
                    "overage_units = overage_units + greatest(:used - :reserved, 0), "
                    "exhausted_at = CASE WHEN consumed_units + estimated_units + :measured "
                    "+ :estimated >= limit_units THEN coalesce(exhausted_at, now()) "
                    "ELSE exhausted_at END, "
                    "updated_at = now() WHERE id = :ledger"
                ),
                {
                    "reserved": reservation.units,
                    "measured": measured,
                    "estimated": counted_estimate,
                    "used": used,
                    "ledger": reservation.ledger_id,
                },
            )


def expire_stale(session_factory: sessionmaker[Session], *, now: datetime | None = None) -> int:
    """Count reservations whose holder disappeared as estimated use (dispatcher housekeeping)."""
    now = now or utcnow()
    with session_scope(session_factory) as db:
        rows = list(
            db.scalars(
                select(BudgetReservation)
                .where(
                    BudgetReservation.status == ReservationStatus.HELD,
                    BudgetReservation.expires_at < now,
                )
                .order_by(BudgetReservation.ledger_id)
                .limit(500)
                .with_for_update(skip_locked=True)
            )
        )
        for reservation in rows:
            reservation.status = ReservationStatus.EXPIRED
            reservation.estimated = True
            reservation.settled_units = reservation.units
            reservation.settled_at = now
            db.execute(
                text(
                    "UPDATE budget_ledgers SET reserved_units = reserved_units - :units, "
                    "estimated_units = estimated_units + :units, updated_at = now() "
                    "WHERE id = :ledger"
                ),
                {"units": reservation.units, "ledger": reservation.ledger_id},
            )
    if rows:
        logger.warning("budget_reservations_expired", extra={"count": len(rows)})
    return len(rows)


@dataclass(frozen=True, slots=True)
class Usage:
    scope_type: str
    scope_id: uuid.UUID
    metric: str
    period: str
    period_start: datetime
    period_end: datetime | None
    limit_units: int
    reserved_units: int
    consumed_units: int
    estimated_units: int
    overage_units: int
    denied_requests: int
    exhausted_at: datetime | None

    @property
    def remaining_units(self) -> int:
        return max(
            0, self.limit_units - self.reserved_units - self.consumed_units - self.estimated_units
        )

    @property
    def exhausted(self) -> bool:
        return self.remaining_units == 0


def current_usage(db: Session, requirement: Requirement, now: datetime | None = None) -> Usage:
    """Current-period usage for a requirement (zero use when no ledger exists yet)."""
    start, end = period_bounds(requirement.period, now or utcnow())
    ledger = db.scalar(
        select(BudgetLedger).where(
            BudgetLedger.scope_type == requirement.scope_type,
            BudgetLedger.scope_id == requirement.scope_id,
            BudgetLedger.metric == requirement.metric,
            BudgetLedger.period_start == start,
        )
    )
    if ledger is None:
        return Usage(
            scope_type=str(requirement.scope_type),
            scope_id=requirement.scope_id,
            metric=str(requirement.metric),
            period=str(requirement.period),
            period_start=start,
            period_end=end,
            limit_units=requirement.limit_units,
            reserved_units=0,
            consumed_units=0,
            estimated_units=0,
            overage_units=0,
            denied_requests=0,
            exhausted_at=None,
        )
    return Usage(
        scope_type=ledger.scope_type,
        scope_id=ledger.scope_id,
        metric=ledger.metric,
        period=ledger.period,
        period_start=ledger.period_start,
        period_end=ledger.period_end,
        # The configured limit wins when it changed during the period.
        limit_units=requirement.limit_units,
        reserved_units=ledger.reserved_units,
        consumed_units=ledger.consumed_units,
        estimated_units=ledger.estimated_units,
        overage_units=ledger.overage_units,
        denied_requests=ledger.denied_requests,
        exhausted_at=ledger.exhausted_at,
    )


def apply_limit_change(db: Session, requirement: Requirement, now: datetime | None = None) -> None:
    """Update the current period's ledger when a limit is edited."""
    start, _end = period_bounds(requirement.period, now or utcnow())
    db.execute(
        update(BudgetLedger)
        .where(
            BudgetLedger.scope_type == requirement.scope_type,
            BudgetLedger.scope_id == requirement.scope_id,
            BudgetLedger.metric == requirement.metric,
            BudgetLedger.period_start == start,
        )
        .values(limit_units=requirement.limit_units, updated_at=utcnow())
    )


def current_usage_for(
    session_factory: sessionmaker[Session], requirement: Requirement, now: datetime | None = None
) -> Usage:
    with session_scope(session_factory) as db:
        return current_usage(db, requirement, now)
