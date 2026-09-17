from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BudgetMetric(enum.StrEnum):
    # Outbound source requests issued by collection (measured per HTTP request; estimated for
    # engines that make requests in their own process).
    REQUESTS = "requests"
    # Provider quota units from documented per-request costs (for example YouTube Data API
    # units). Estimates, never currency.
    PROVIDER_UNITS = "provider_units"


class BudgetPeriod(enum.StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    # One execution (per-run limits).
    RUN = "run"


class BudgetScope(enum.StrEnum):
    CASE = "case"
    MONITOR = "monitor"
    QUERY_RUN = "query_run"


class ReservationStatus(enum.StrEnum):
    HELD = "held"
    SETTLED = "settled"
    RELEASED = "released"
    # The holder disappeared (worker crash); the units were counted as consumed estimates.
    EXPIRED = "expired"


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class CaseBudget(Base):
    """An analyst-configured limit shared by every execution in a case."""

    __tablename__ = "case_budgets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(8))
    limit_units: Mapped[int]
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(_in("metric", BudgetMetric), name="metric_valid"),
        CheckConstraint("period IN ('day', 'week', 'month')", name="period_valid"),
        CheckConstraint("limit_units >= 0", name="limit_non_negative"),
        Index("uq_case_budgets_case_metric_period", "case_id", "metric", "period", unique=True),
    )


class BudgetLedger(Base):
    """Counters for one budget in one period, changed only by atomic conditional updates.

    ``reserved`` units are held by requests in flight; ``consumed`` units were measured;
    ``estimated`` units were counted without a measurement (engine pages, documented provider
    costs, and reservations whose holder crashed). A reservation succeeds only while
    ``reserved + consumed + estimated + requested <= limit``, so concurrent workers can never both
    spend the last remaining allowance. ``overage`` records measured use above an estimate.
    """

    __tablename__ = "budget_ledgers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[uuid.UUID]
    metric: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(8))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime | None]
    limit_units: Mapped[int]
    reserved_units: Mapped[int] = mapped_column(default=0, server_default="0")
    consumed_units: Mapped[int] = mapped_column(default=0, server_default="0")
    estimated_units: Mapped[int] = mapped_column(default=0, server_default="0")
    overage_units: Mapped[int] = mapped_column(default=0, server_default="0")
    denied_requests: Mapped[int] = mapped_column(default=0, server_default="0")
    exhausted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(_in("scope_type", BudgetScope), name="scope_type_valid"),
        CheckConstraint(_in("metric", BudgetMetric), name="metric_valid"),
        CheckConstraint(_in("period", BudgetPeriod), name="period_valid"),
        CheckConstraint(
            "reserved_units >= 0 AND consumed_units >= 0 AND estimated_units >= 0 "
            "AND overage_units >= 0 AND limit_units >= 0",
            name="units_non_negative",
        ),
        Index(
            "uq_budget_ledgers_scope_period",
            "scope_type",
            "scope_id",
            "metric",
            "period_start",
            unique=True,
        ),
        Index("ix_budget_ledgers_case", "case_id", "period_start"),
    )


class BudgetReservation(Base):
    """Units held on one ledger for one request (all ledgers of a request share ``ticket_id``)."""

    __tablename__ = "budget_reservations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID]
    ledger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_ledgers.id", ondelete="CASCADE")
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE")
    )
    connector_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_runs.id", ondelete="CASCADE")
    )
    units: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default=ReservationStatus.HELD)
    # True when the settled units are an estimate rather than a measurement.
    estimated: Mapped[bool] = mapped_column(default=False, server_default="false")
    settled_units: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    settled_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(_in("status", ReservationStatus), name="status_valid"),
        CheckConstraint("units >= 0", name="units_non_negative"),
        Index("ix_budget_reservations_ticket", "ticket_id"),
        Index("ix_budget_reservations_held", "status", "expires_at"),
        Index("ix_budget_reservations_run", "query_run_id"),
    )
