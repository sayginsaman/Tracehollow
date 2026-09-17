from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELED}
)


class ConnectorOutcome(enum.StrEnum):
    """PRD FR-03 outcome vocabulary. Never collapsed into an empty result."""

    FINDINGS = "findings"
    NO_FINDINGS = "no_findings"
    PARTIAL = "partial"
    AUTHENTICATION_REQUIRED = "authentication_required"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    PARSE_ERROR = "parse_error"
    CANCELED = "canceled"


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class SavedQuery(TimestampMixin, Base):
    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    input_type: Mapped[str] = mapped_column(String(32))
    input_value: Mapped[str] = mapped_column(String(1000))
    connector_ids: Mapped[list[str]] = mapped_column(ARRAY(String(100)))
    collection_mode: Mapped[str] = mapped_column(String(32))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    run_counter: Mapped[int] = mapped_column(default=0, server_default="0")

    __table_args__ = (Index("ix_saved_queries_case_id", "case_id"),)


class QueryRun(Base):
    """One execution of a saved query. The parameter snapshot is immutable."""

    __tablename__ = "query_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    saved_query_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("saved_queries.id", ondelete="SET NULL")
    )
    run_number: Mapped[int]
    parameters_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.QUEUED)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    queued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    cancel_requested_at: Mapped[datetime | None]
    cancel_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    claim_count: Mapped[int] = mapped_column(default=0, server_default="0")
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    # Set for executions started by a monitor (scheduled or "run now").
    monitor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monitors.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(_in("status", RunStatus), name="status_valid"),
        Index("ix_query_runs_case_queued", "case_id", "queued_at"),
        Index("ix_query_runs_monitor", "monitor_id", "queued_at"),
        Index("ix_query_runs_saved_query", "saved_query_id", "run_number"),
        Index("ix_query_runs_status", "status"),
    )


class ConnectorRun(Base):
    """Per-connector outcome, coverage, retry and (when available) quota/cost record."""

    __tablename__ = "connector_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    query_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_runs.id", ondelete="CASCADE"))
    position: Mapped[int]
    connector_id: Mapped[str] = mapped_column(String(100))
    connector_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.QUEUED)
    outcome: Mapped[str | None] = mapped_column(String(32))
    pages_completed: Mapped[int] = mapped_column(default=0, server_default="0")
    items_collected: Mapped[int] = mapped_column(default=0, server_default="0")
    fetch_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    retries: Mapped[int] = mapped_column(default=0, server_default="0")
    page_attempts: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_detail: Mapped[str | None] = mapped_column(String(500))
    retry_after_seconds: Mapped[float | None]
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    coverage_note: Mapped[str | None] = mapped_column(Text)
    quota_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(_in("status", RunStatus), name="status_valid"),
        CheckConstraint(
            f"outcome IS NULL OR {_in('outcome', ConnectorOutcome)}", name="outcome_valid"
        ),
        Index("uq_connector_runs_run_connector", "query_run_id", "connector_id", unique=True),
    )
