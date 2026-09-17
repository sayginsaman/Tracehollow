from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EventType(enum.StrEnum):
    CHANGE_DETECTED = "change_detected"
    ACTION_REQUIRED = "action_required"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RUN_COMPLETED = "run_completed"


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    ACTION_REQUIRED = "action_required"


class DeliveryStatus(enum.StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    # The receiver kept failing, or refused the payload permanently.
    FAILED = "failed"
    # Not sent: destination disabled, subscription removed, authorization lost, adapter off.
    BLOCKED = "blocked"


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Notification(Base):
    """An in-app notification for one account. Summaries carry counts and names, never evidence."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    monitor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monitors.id", ondelete="SET NULL")
    )
    # Shared by every recipient and external delivery of the same logical event.
    event_id: Mapped[uuid.UUID]
    event_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Application path (for example /cases/<id>/monitors/<id>); never an external URL.
    link: Mapped[str | None] = mapped_column(String(500))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    read_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(_in("event_type", EventType), name="event_type_valid"),
        CheckConstraint(_in("severity", Severity), name="severity_valid"),
        Index("uq_notifications_user_dedupe", "user_id", "dedupe_key", unique=True),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_case", "case_id"),
    )


class NotificationDestination(TimestampMixin, Base):
    """An administrator-configured webhook receiver. Created disabled.

    The signing secret is encrypted with the credential key held outside PostgreSQL and is never
    returned by the API or included in payloads.
    """

    __tablename__ = "notification_destinations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16), default="webhook", server_default="webhook")
    url: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_key_id: Mapped[str | None] = mapped_column(String(16))
    max_per_minute: Mapped[int] = mapped_column(default=30, server_default="30")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_delivery_at: Mapped[datetime | None]
    last_status: Mapped[str | None] = mapped_column(String(32))
    consecutive_failures: Mapped[int] = mapped_column(default=0, server_default="0")

    __table_args__ = (
        CheckConstraint("kind = 'webhook'", name="kind_valid"),
        CheckConstraint("max_per_minute BETWEEN 1 AND 600", name="rate_valid"),
    )


class MonitorSubscription(Base):
    """An analyst's choice to send some event types of one monitor to one destination."""

    __tablename__ = "monitor_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_destinations.id", ondelete="CASCADE")
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index(
            "uq_monitor_subscriptions_monitor_destination",
            "monitor_id",
            "destination_id",
            unique=True,
        ),
    )


class NotificationDelivery(Base):
    """One external delivery of one event to one destination (at least once, stable event ID)."""

    __tablename__ = "notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_destinations.id", ondelete="CASCADE")
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monitor_subscriptions.id", ondelete="SET NULL")
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    event_id: Mapped[uuid.UUID]
    event_type: Mapped[str] = mapped_column(String(32))
    # The exact JSON body that is (or would be) sent; minimal and redacted when created.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_response_status: Mapped[int | None]
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("status", DeliveryStatus), name="status_valid"),
        CheckConstraint(_in("event_type", EventType), name="event_type_valid"),
        Index("uq_notification_deliveries_event", "destination_id", "event_id", unique=True),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
        Index("ix_notification_deliveries_case", "case_id"),
    )
