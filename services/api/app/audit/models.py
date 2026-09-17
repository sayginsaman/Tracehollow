from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActorType(enum.StrEnum):
    USER = "user"
    # A background service acting on stored authorization (scheduler, collector, worker).
    SERVICE = "service"
    # Housekeeping with no user or authorization behind it (pruning, migrations).
    SYSTEM = "system"


class AuditOutcome(enum.StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


class AuditEvent(Base):
    """One recorded decision or change.

    Events hold identifiers, action names, outcomes and small allowlisted details, never
    credentials, evidence content, query inputs or AI text. They are append-only by application
    convention; nothing in the database prevents an operator with database access from changing
    them, so the trail is not tamper-evident. ``case_id`` has no foreign key: the record of a case
    deletion outlives the case.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Username at the time of the event, or the service name.
    actor_label: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    # Request ID for API actions; occurrence, run or job ID for background work.
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'service', 'system')", name="actor_type_valid"),
        CheckConstraint("outcome IN ('succeeded', 'denied', 'failed')", name="outcome_valid"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_case_occurred", "case_id", "occurred_at"),
        Index("ix_audit_events_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_audit_events_action", "action"),
    )
