from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    DONE = "done"


class AggregateType(enum.StrEnum):
    QUERY_RUN = "query_run"
    CASE_DELETION = "case_deletion"
    AI_RUN = "ai_run"
    CASE_INDEX = "case_index"
    AI_PROVIDER_CHECK = "ai_provider_check"
    PROCESSING_JOB = "processing_job"


class DispatchOutbox(Base):
    """Durable handoff from a committed transaction to the Celery broker.

    Rows are written in the same transaction as the work they describe. The API publishes
    immediately after commit when it can; the dispatcher service republishes pending rows and
    re-queues work whose message was lost or whose worker died. Workers are idempotent, so
    redelivery never duplicates logical results.
    """

    __tablename__ = "dispatch_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_name: Mapped[str] = mapped_column(String(100))
    aggregate_type: Mapped[str] = mapped_column(String(32))
    aggregate_id: Mapped[uuid.UUID]
    # No foreign key: deletion rows must outlive the case they delete.
    case_id: Mapped[uuid.UUID | None]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(server_default=func.now())
    dispatched_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'dispatched', 'done')", name="status_valid"),
        CheckConstraint(
            "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
            " 'ai_provider_check', 'processing_job')",
            name="aggregate_type_valid",
        ),
        Index("uq_dispatch_outbox_aggregate", "aggregate_type", "aggregate_id", unique=True),
        Index("ix_dispatch_outbox_status_available", "status", "available_at"),
        Index("ix_dispatch_outbox_case_id", "case_id"),
    )
