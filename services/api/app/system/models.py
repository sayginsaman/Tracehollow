from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkerCheckStatus(enum.StrEnum):
    QUEUED = "queued"
    COMPLETED = "completed"
    DISPATCH_FAILED = "dispatch_failed"


class WorkerCheck(Base):
    """A minimal broker-to-worker round trip. This is infrastructure, not an investigation run."""

    __tablename__ = "worker_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), default=WorkerCheckStatus.QUEUED)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(server_default=func.now())
    dispatched_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    worker_hostname: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'completed', 'dispatch_failed')", name="status_valid"
        ),
        Index("ix_worker_checks_requested_at", "requested_at"),
    )
