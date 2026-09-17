"""Emitting notifications: in-app rows per recipient and optional webhook deliveries.

One logical event has a stable ``event_id`` derived from its case and deduplication key, so a
retried job, a redelivered task or a second scheduler produces the same notification rows (unique
per account) and the same external deliveries (unique per destination). Summaries carry counts,
names chosen by analysts and application links; never evidence values, AI text or secrets.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.auth.models import AccountRole, User
from app.cases.models import CaseMember, CaseRole
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.monitoring.models import Monitor
from app.notifications.models import (
    DeliveryStatus,
    EventType,
    MonitorSubscription,
    Notification,
    NotificationDelivery,
    NotificationDestination,
    Severity,
)

logger = logging.getLogger(__name__)

EVENT_NAMESPACE = uuid.UUID("6f1c3d2e-58a4-4e0b-9d3a-7a1f0c2b5e91")
PAYLOAD_VERSION = "tracehollow.notification/v1"
# Only these summary keys can leave the installation.
SUMMARY_KEYS = frozenset(
    {
        "new",
        "changed",
        "not_observed",
        "conflicting",
        "unknown",
        "change_sets",
        "status",
        "reason",
        "run_status",
        "scope",
        "metric",
        "period",
        "limit",
        "used",
        "connectors_failed",
    }
)


@dataclass
class Event:
    event_type: EventType
    severity: Severity
    case_id: uuid.UUID
    title: str
    body: str
    link: str
    dedupe_key: str
    monitor: Monitor | None = None
    recipients: Literal["case_analysts", "all_members"] = "case_analysts"
    summary: dict[str, Any] = field(default_factory=dict)
    occurrence_id: uuid.UUID | None = None
    query_run_id: uuid.UUID | None = None

    @property
    def event_id(self) -> uuid.UUID:
        return uuid.uuid5(EVENT_NAMESPACE, f"{self.case_id}:{self.dedupe_key}")


def _recipients(db: Session, case_id: uuid.UUID, recipients: str) -> list[uuid.UUID]:
    conditions = [CaseMember.case_id == case_id, User.is_active.is_(True)]
    if recipients == "case_analysts":
        conditions += [CaseMember.role == CaseRole.ANALYST, User.role != AccountRole.VIEWER]
    return list(
        db.scalars(
            select(User.id).join(CaseMember, CaseMember.user_id == User.id).where(*conditions)
        )
    )


def build_payload(settings: Settings, event: Event) -> dict[str, Any]:
    """The exact body a webhook receives. Allowlisted fields only."""
    summary = {
        key: value
        for key, value in event.summary.items()
        if key in SUMMARY_KEYS and (value is None or isinstance(value, bool | int | float | str))
    }
    link = f"{settings.public_origin}{event.link}" if event.link.startswith("/") else None
    return {
        "schema": PAYLOAD_VERSION,
        "event_id": str(event.event_id),
        "event_type": str(event.event_type),
        "severity": str(event.severity),
        "occurred_at": utcnow().isoformat(),
        "generator": f"tracehollow/{__version__}",
        "case_id": str(event.case_id),
        "monitor_id": str(event.monitor.id) if event.monitor is not None else None,
        "occurrence_id": str(event.occurrence_id) if event.occurrence_id else None,
        "query_run_id": str(event.query_run_id) if event.query_run_id else None,
        "summary": summary,
        "link": link,
    }


def emit(db: Session, settings: Settings, event: Event) -> uuid.UUID:
    """Create in-app notifications and queue external deliveries in the caller's transaction."""
    event_id = event.event_id
    now = utcnow()
    for user_id in _recipients(db, event.case_id, event.recipients):
        db.execute(
            insert(Notification)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                case_id=event.case_id,
                monitor_id=event.monitor.id if event.monitor is not None else None,
                event_id=event_id,
                event_type=event.event_type,
                severity=event.severity,
                title=event.title[:200],
                body=event.body[:1000],
                link=event.link[:500],
                dedupe_key=event.dedupe_key[:200],
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
        )
    if event.monitor is not None and settings.notifications_external_enabled:
        _queue_deliveries(db, settings, event)
    return event_id


def _queue_deliveries(db: Session, settings: Settings, event: Event) -> None:
    assert event.monitor is not None
    rows = db.execute(
        select(MonitorSubscription, NotificationDestination)
        .join(
            NotificationDestination,
            NotificationDestination.id == MonitorSubscription.destination_id,
        )
        .where(
            MonitorSubscription.monitor_id == event.monitor.id,
            MonitorSubscription.case_id == event.case_id,
            NotificationDestination.enabled.is_(True),
        )
    ).all()
    for subscription, destination in rows:
        event_type = str(event.event_type)
        if event_type not in subscription.event_types or event_type not in destination.event_types:
            continue
        delivery_id = uuid.uuid4()
        inserted = db.scalar(
            insert(NotificationDelivery)
            .values(
                id=delivery_id,
                destination_id=destination.id,
                subscription_id=subscription.id,
                case_id=event.case_id,
                event_id=event.event_id,
                event_type=event_type,
                payload=build_payload(settings, event),
                status=DeliveryStatus.PENDING,
                next_attempt_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["destination_id", "event_id"])
            .returning(NotificationDelivery.id)
        )
        if inserted is not None:
            dispatch.enqueue(
                db,
                task_name=dispatch.DELIVER_NOTIFICATION_TASK,
                aggregate_type=AggregateType.NOTIFICATION_DELIVERY,
                aggregate_id=inserted,
                case_id=event.case_id,
            )


def prune(session_factory: sessionmaker[Session], *, retention_days: int) -> int:
    cutoff = utcnow() - timedelta(days=retention_days)
    with session_scope(session_factory) as db:
        removed = db.execute(delete(Notification).where(Notification.created_at < cutoff))
        deliveries = db.execute(
            delete(NotificationDelivery).where(
                NotificationDelivery.created_at < cutoff,
                NotificationDelivery.status != DeliveryStatus.PENDING,
            )
        )
    return int(getattr(removed, "rowcount", 0) or 0) + int(getattr(deliveries, "rowcount", 0) or 0)
