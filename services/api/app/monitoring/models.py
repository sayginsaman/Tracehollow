from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MonitorStatus(enum.StrEnum):
    # Occurrences are scheduled.
    ENABLED = "enabled"
    # No new occurrences; a run already queued or running continues. Resuming continues from the
    # next future slot without catching up the paused period.
    PAUSED = "paused"
    # Retired: no occurrences and the active run (if any) was canceled when it was disabled.
    DISABLED = "disabled"


class MissedRunPolicy(enum.StrEnum):
    # After downtime, run once for the most recent missed slot (never one run per missed slot).
    RUN_LATEST = "run_latest"
    # After downtime, record the missed slots and wait for the next future slot.
    SKIP = "skip"


class OccurrenceStatus(enum.StrEnum):
    DISPATCHED = "dispatched"
    SKIPPED = "skipped"


class OccurrenceKind(enum.StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Monitor(TimestampMixin, Base):
    """A schedule, bounded scope, limits and notification settings for one saved query.

    The monitor pins the saved query's definition (``query_fingerprint``). Editing the query pauses
    enabled monitors until an analyst reviews and resumes them, so a monitor never starts collecting
    a different target silently. Every occurrence keeps an immutable snapshot of the configuration
    it used.
    """

    __tablename__ = "monitors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    saved_query_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("saved_queries.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=MonitorStatus.PAUSED)
    status_reason: Mapped[str | None] = mapped_column(String(64))
    status_changed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # {"kind": "interval", "every_minutes": n} | {"kind": "daily"|"weekly", "time": "HH:MM",
    #  "weekdays": [0-6]} (weekly only; 0 is Monday).
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB)
    timezone: Mapped[str] = mapped_column(String(64))
    # Interval schedules count from this instant.
    schedule_anchor: Mapped[datetime] = mapped_column(server_default=func.now())
    missed_run_policy: Mapped[str] = mapped_column(String(16), default=MissedRunPolicy.RUN_LATEST)
    # Subset of the saved query's connectors this monitor runs.
    connector_ids: Mapped[list[str]] = mapped_column(ARRAY(String(100)))
    # {"max_pages": n, "max_items_per_page": n}
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # {"max_requests_per_run": n, "max_items_per_run": n, "max_run_seconds": n}
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # {"period": "day"|"week"|"month", "max_requests": n, "max_provider_units": n|null}
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # {"keep_last_runs": n|null, "max_age_days": n|null}
    retention: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # {"on_change", "on_failure", "on_budget_exhausted", "on_completion": bool,
    #  "recipients": "case_analysts"|"all_members"}
    notify: Mapped[dict[str, Any]] = mapped_column(JSONB)
    query_fingerprint: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[int] = mapped_column(default=1, server_default="1")
    # Scheduled runs work for this analyst; losing analyst access pauses the monitor.
    authorized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    next_run_at: Mapped[datetime | None]
    last_scheduled_for: Mapped[datetime | None]
    consecutive_failures: Mapped[int] = mapped_column(default=0, server_default="0")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")

    __table_args__ = (
        CheckConstraint(_in("status", MonitorStatus), name="status_valid"),
        CheckConstraint(_in("missed_run_policy", MissedRunPolicy), name="missed_run_policy_valid"),
        CheckConstraint(
            "status = 'enabled' OR next_run_at IS NULL", name="only_enabled_monitors_are_due"
        ),
        Index("ix_monitors_due", "status", "next_run_at"),
        Index("ix_monitors_case_id", "case_id"),
        Index("ix_monitors_saved_query_id", "saved_query_id"),
    )


class MonitorOccurrence(Base):
    """One slot of a monitor's schedule (or a manual run), dispatched or skipped with a reason.

    ``(monitor_id, scheduled_for)`` is unique: however many schedulers run, a slot produces at most
    one logical execution.
    """

    __tablename__ = "monitor_occurrences"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default=OccurrenceKind.SCHEDULED)
    scheduled_for: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16))
    # Why a slot was skipped: overlap, budget_exhausted, authorization_lost, query_changed,
    # query_invalid, case_inactive, missed.
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL")
    )
    # Earlier slots that were not run because no scheduler was running at the time.
    missed_slots: Mapped[int] = mapped_column(default=0, server_default="0")
    config_version: Mapped[int]
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dispatched_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("status", OccurrenceStatus), name="status_valid"),
        CheckConstraint(_in("kind", OccurrenceKind), name="kind_valid"),
        CheckConstraint("status = 'dispatched' OR query_run_id IS NULL", name="skipped_has_no_run"),
        Index("uq_monitor_occurrences_slot", "monitor_id", "scheduled_for", unique=True),
        Index("ix_monitor_occurrences_case_created", "case_id", "created_at"),
        Index(
            "uq_monitor_occurrences_query_run",
            "query_run_id",
            unique=True,
            postgresql_where=text("query_run_id IS NOT NULL"),
        ),
    )
