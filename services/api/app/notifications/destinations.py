"""Webhook destinations (administrators) and monitor subscriptions (case analysts).

The external adapter is optional and off by default. Administrators decide which receivers exist
(created disabled, previewed, then enabled); analysts decide which monitors send which event types
to an enabled receiver. Payloads are minimal: identifiers, counts, reason codes and an application
link. Evidence, AI output, query inputs, names typed by analysts and secrets never leave.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.audit.service import record
from app.auth.permissions import SystemPermission
from app.cases.access import AnalystCase, WritableCase
from app.config import Settings
from app.connectors import netguard
from app.deps import ActorDep, DbDep, PrincipalDep, SettingsDep, require_system_permission
from app.integrations import crypto
from app.monitoring import service as monitoring
from app.notifications import delivery
from app.notifications import service as notifications
from app.notifications.models import (
    EventType,
    MonitorSubscription,
    NotificationDelivery,
    NotificationDestination,
    Severity,
)
from app.schemas import LimitParam, OffsetParam, Page

admin_router = APIRouter(
    prefix="/api/v1/admin/notification-destinations",
    tags=["notifications"],
    dependencies=[require_system_permission(SystemPermission.MANAGE_NOTIFICATION_DESTINATIONS)],
)
case_router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["notifications"])

EventTypeName = Annotated[
    str, Field(pattern="^(change_detected|action_required|budget_exhausted|run_completed)$")
]


class DestinationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=100)]
    url: Annotated[str, Field(min_length=8, max_length=2048)]
    event_types: Annotated[list[EventTypeName], Field(min_length=1, max_length=4)]
    max_per_minute: Annotated[int, Field(ge=1, le=600)] = 30


class DestinationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    url: Annotated[str | None, Field(min_length=8, max_length=2048)] = None
    event_types: Annotated[list[EventTypeName] | None, Field(min_length=1, max_length=4)] = None
    max_per_minute: Annotated[int | None, Field(ge=1, le=600)] = None
    rotate_secret: bool = False


class DestinationOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    host: str
    enabled: bool
    event_types: list[str]
    max_per_minute: int
    signing: bool
    subscriptions: int
    last_delivery_at: datetime | None
    last_status: str | None
    consecutive_failures: int
    adapter_enabled: bool
    created_at: datetime
    updated_at: datetime


class DestinationCreated(DestinationOut):
    # Shown once, when created or rotated; store it in the receiver to verify signatures.
    signing_secret: str | None = None


class PayloadPreview(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    body: dict[str, object]
    notes: list[str]


class DeliveryOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_error_code: str | None
    last_response_status: int | None
    delivered_at: datetime | None
    created_at: datetime


class AvailableDestination(BaseModel):
    id: uuid.UUID
    name: str
    host: str
    event_types: list[str]


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_id: uuid.UUID
    event_types: Annotated[list[EventTypeName], Field(min_length=1, max_length=4)]


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    monitor_id: uuid.UUID
    destination_id: uuid.UUID
    destination_name: str
    destination_host: str
    destination_enabled: bool
    event_types: list[str]
    created_at: datetime


def _host(url: str) -> str:
    return urlsplit(url).netloc


def _check_url(settings: Settings, url: str) -> str:
    """Refuse destinations the collection address policy refuses.

    The API checks the URL's shape and literal addresses; host names are resolved and checked
    by the collector at every delivery attempt (the API has no route to receivers).
    """
    policy = netguard.NetworkPolicy(
        allowed_ports=frozenset(settings.collection_allowed_ports),
        allowed_private_networks=netguard.parse_networks(
            settings.collection_allowed_private_networks
        ),
    )
    try:
        checked = netguard.check_url(url.strip(), policy, resolve=False)
    except netguard.DestinationBlockedError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": f"Destination not permitted: {exc.detail}"},
        ) from None
    if checked.url.query or checked.url.fragment:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "url_query_not_allowed",
                "message": (
                    "Destination URLs may not carry a query or fragment; use the signing secret "
                    "instead of tokens in the URL."
                ),
            },
        )
    return str(checked.url)


def _require_adapter(settings: Settings) -> None:
    if not settings.notifications_external_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "external_notifications_disabled",
                "message": (
                    "External notifications are turned off on this installation "
                    "(TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED=false)."
                ),
            },
        )


def _out(db: DbDep, settings: Settings, row: NotificationDestination) -> DestinationOut:
    count = db.scalar(
        select(func.count())
        .select_from(MonitorSubscription)
        .where(MonitorSubscription.destination_id == row.id)
    )
    return DestinationOut(
        id=row.id,
        name=row.name,
        url=row.url,
        host=_host(row.url),
        enabled=row.enabled,
        event_types=list(row.event_types),
        max_per_minute=row.max_per_minute,
        signing=row.secret_ciphertext is not None,
        subscriptions=int(count or 0),
        last_delivery_at=row.last_delivery_at,
        last_status=row.last_status,
        consecutive_failures=row.consecutive_failures,
        adapter_enabled=settings.notifications_external_enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _destination(db: DbDep, destination_id: uuid.UUID) -> NotificationDestination:
    row = db.get(NotificationDestination, destination_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="destination_not_found")
    return row


def _seal(settings: Settings, row: NotificationDestination) -> str:
    secret = secrets.token_urlsafe(32)
    try:
        sealed = delivery.seal_secret(settings, row.id, secret)
    except crypto.CredentialStoreUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="credential_store_unavailable"
        ) from None
    row.secret_ciphertext = sealed.ciphertext
    row.secret_nonce = sealed.nonce
    row.secret_key_id = sealed.key_id
    return secret


@admin_router.get("")
def list_destinations(db: DbDep, settings: SettingsDep) -> list[DestinationOut]:
    rows = db.scalars(select(NotificationDestination).order_by(NotificationDestination.created_at))
    return [_out(db, settings, row) for row in rows]


@admin_router.post("", status_code=status.HTTP_201_CREATED)
def create_destination(
    db: DbDep, settings: SettingsDep, principal: PrincipalDep, actor: ActorDep, body: DestinationIn
) -> DestinationCreated:
    _require_adapter(settings)
    row = NotificationDestination(
        id=uuid.uuid4(),
        name=body.name.strip(),
        url=_check_url(settings, body.url),
        enabled=False,
        event_types=sorted(set(body.event_types)),
        max_per_minute=body.max_per_minute,
        created_by_user_id=principal.user.id,
        updated_by_user_id=principal.user.id,
    )
    secret = _seal(settings, row)
    db.add(row)
    db.flush()
    record(
        db,
        actor,
        "notification_destination.created",
        target_type="notification_destination",
        target_id=row.id,
        details={"host": _host(row.url), "event_types": row.event_types, "enabled": False},
    )
    db.commit()
    return DestinationCreated(**_out(db, settings, row).model_dump(), signing_secret=secret)


@admin_router.patch("/{destination_id}")
def update_destination(
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    destination_id: uuid.UUID,
    body: DestinationUpdate,
) -> DestinationCreated:
    row = _destination(db, destination_id)
    changed = []
    if body.name is not None:
        row.name = body.name.strip()
        changed.append("name")
    if body.url is not None and body.url.strip() != row.url:
        row.url = _check_url(settings, body.url)
        # A new receiver must be previewed and enabled again.
        row.enabled = False
        changed += ["url", "enabled"]
    if body.event_types is not None:
        row.event_types = sorted(set(body.event_types))
        changed.append("event_types")
    if body.max_per_minute is not None:
        row.max_per_minute = body.max_per_minute
        changed.append("max_per_minute")
    secret = None
    if body.rotate_secret:
        secret = _seal(settings, row)
        changed.append("signing_secret")
    row.updated_by_user_id = principal.user.id
    record(
        db,
        actor,
        "notification_destination.updated",
        target_type="notification_destination",
        target_id=row.id,
        details={"fields": changed, "host": _host(row.url)},
    )
    db.commit()
    return DestinationCreated(**_out(db, settings, row).model_dump(), signing_secret=secret)


def _toggle(
    db: DbDep, settings: Settings, actor: ActorDep, destination_id: uuid.UUID, enabled: bool
) -> DestinationOut:
    row = _destination(db, destination_id)
    if enabled:
        _require_adapter(settings)
        _check_url(settings, row.url)
    row.enabled = enabled
    record(
        db,
        actor,
        "notification_destination.enabled" if enabled else "notification_destination.disabled",
        target_type="notification_destination",
        target_id=row.id,
        details={"host": _host(row.url)},
    )
    db.commit()
    return _out(db, settings, row)


@admin_router.post("/{destination_id}/enable")
def enable_destination(
    db: DbDep, settings: SettingsDep, actor: ActorDep, destination_id: uuid.UUID
) -> DestinationOut:
    return _toggle(db, settings, actor, destination_id, True)


@admin_router.post("/{destination_id}/disable")
def disable_destination(
    db: DbDep, settings: SettingsDep, actor: ActorDep, destination_id: uuid.UUID
) -> DestinationOut:
    return _toggle(db, settings, actor, destination_id, False)


@admin_router.delete("/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_destination(db: DbDep, actor: ActorDep, destination_id: uuid.UUID) -> None:
    row = _destination(db, destination_id)
    record(
        db,
        actor,
        "notification_destination.deleted",
        target_type="notification_destination",
        target_id=row.id,
        details={"host": _host(row.url)},
    )
    db.delete(row)
    db.commit()


def preview_for(
    settings: Settings, row: NotificationDestination, event: notifications.Event
) -> PayloadPreview:
    body = notifications.build_payload(settings, event)
    return PayloadPreview(
        method="POST",
        url=row.url,
        headers={
            "content-type": "application/json",
            "x-tracehollow-event-id": str(body["event_id"]),
            "x-tracehollow-timestamp": "<unix time when sent>",
            "x-tracehollow-signature": (
                "sha256=<HMAC-SHA256 of timestamp + '.' + body with the signing secret>"
                if row.secret_ciphertext is not None
                else "(not signed)"
            ),
        },
        body=body,
        notes=[
            "This is the exact body format sent; values here come from a sample event.",
            "Receivers must deduplicate on event_id: a delivery can arrive more than once.",
            "Redirects are not followed; 2xx is success; 408, 425, 429, 5xx and network errors "
            "are retried with backoff.",
            "Never included: evidence, AI answers, query inputs, monitor or case names, "
            "credentials.",
        ],
    )


@admin_router.post("/{destination_id}/preview")
def preview_destination(
    db: DbDep, settings: SettingsDep, destination_id: uuid.UUID
) -> PayloadPreview:
    row = _destination(db, destination_id)
    sample_case = uuid.UUID("00000000-0000-4000-8000-000000000000")
    event = notifications.Event(
        event_type=EventType.CHANGE_DETECTED,
        severity=Severity.INFO,
        case_id=sample_case,
        title="sample",
        body="sample",
        link=f"/cases/{sample_case}/monitors",
        dedupe_key="preview",
        summary={
            "new": 2,
            "changed": 1,
            "not_observed": 0,
            "conflicting": 0,
            "unknown": 0,
            "change_sets": 1,
        },
    )
    return preview_for(settings, row, event)


@admin_router.get("/{destination_id}/deliveries")
def list_deliveries(
    db: DbDep, destination_id: uuid.UUID, limit: LimitParam = 25, offset: OffsetParam = 0
) -> Page[DeliveryOut]:
    row = _destination(db, destination_id)
    condition = NotificationDelivery.destination_id == row.id
    total = db.scalar(select(func.count()).select_from(NotificationDelivery).where(condition)) or 0
    rows = db.scalars(
        select(NotificationDelivery)
        .where(condition)
        .order_by(NotificationDelivery.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[
            DeliveryOut(
                id=item.id,
                case_id=item.case_id,
                event_id=item.event_id,
                event_type=item.event_type,
                status=item.status,
                attempts=item.attempts,
                next_attempt_at=item.next_attempt_at,
                last_error_code=item.last_error_code,
                last_response_status=item.last_response_status,
                delivered_at=item.delivered_at,
                created_at=item.created_at,
            )
            for item in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# -- case analysts -----------------------------------------------------------------------------


@case_router.get("/notification-destinations")
def available_destinations(
    case: AnalystCase, db: DbDep, settings: SettingsDep
) -> list[AvailableDestination]:
    if not settings.notifications_external_enabled:
        return []
    rows = db.scalars(
        select(NotificationDestination)
        .where(NotificationDestination.enabled.is_(True))
        .order_by(NotificationDestination.name)
    )
    return [
        AvailableDestination(
            id=row.id, name=row.name, host=_host(row.url), event_types=list(row.event_types)
        )
        for row in rows
    ]


def _subscriptions_out(db: DbDep, monitor_id: uuid.UUID) -> list[SubscriptionOut]:
    rows = db.execute(
        select(MonitorSubscription, NotificationDestination)
        .join(
            NotificationDestination,
            NotificationDestination.id == MonitorSubscription.destination_id,
        )
        .where(MonitorSubscription.monitor_id == monitor_id)
        .order_by(MonitorSubscription.created_at)
    ).all()
    return [
        SubscriptionOut(
            id=subscription.id,
            monitor_id=subscription.monitor_id,
            destination_id=destination.id,
            destination_name=destination.name,
            destination_host=_host(destination.url),
            destination_enabled=destination.enabled,
            event_types=list(subscription.event_types),
            created_at=subscription.created_at,
        )
        for subscription, destination in rows
    ]


@case_router.get("/monitors/{monitor_id}/subscriptions")
def list_subscriptions(
    case: AnalystCase, db: DbDep, monitor_id: uuid.UUID
) -> list[SubscriptionOut]:
    monitor = monitoring.get_monitor(db, case.id, monitor_id)
    return _subscriptions_out(db, monitor.id)


@case_router.post("/monitors/{monitor_id}/subscriptions", status_code=status.HTTP_201_CREATED)
def create_subscription(
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    monitor_id: uuid.UUID,
    body: SubscriptionIn,
) -> list[SubscriptionOut]:
    _require_adapter(settings)
    monitor = monitoring.get_monitor(db, case.id, monitor_id)
    destination = db.get(NotificationDestination, body.destination_id)
    if destination is None or not destination.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="destination_not_found")
    requested = sorted(set(body.event_types))
    refused = sorted(set(requested) - set(destination.event_types))
    if refused:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "event_type_not_allowed",
                "message": f"This destination does not accept: {', '.join(refused)}.",
            },
        )
    existing = db.scalar(
        select(MonitorSubscription).where(
            MonitorSubscription.monitor_id == monitor.id,
            MonitorSubscription.destination_id == destination.id,
        )
    )
    if existing is not None:
        existing.event_types = requested
        existing.created_by_user_id = principal.user.id
    else:
        db.add(
            MonitorSubscription(
                monitor_id=monitor.id,
                destination_id=destination.id,
                case_id=case.id,
                event_types=requested,
                created_by_user_id=principal.user.id,
            )
        )
    record(
        db,
        actor,
        "notification_subscription.set",
        case_id=case.id,
        target_type="monitor",
        target_id=monitor.id,
        details={"destination_id": destination.id, "event_types": requested},
    )
    db.commit()
    return _subscriptions_out(db, monitor.id)


@case_router.delete(
    "/monitors/{monitor_id}/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_subscription(
    case: AnalystCase, db: DbDep, actor: ActorDep, monitor_id: uuid.UUID, subscription_id: uuid.UUID
) -> None:
    monitor = monitoring.get_monitor(db, case.id, monitor_id)
    row = db.scalar(
        select(MonitorSubscription).where(
            MonitorSubscription.id == subscription_id, MonitorSubscription.monitor_id == monitor.id
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="subscription_not_found")
    record(
        db,
        actor,
        "notification_subscription.removed",
        case_id=case.id,
        target_type="monitor",
        target_id=monitor.id,
        details={"destination_id": row.destination_id},
    )
    db.delete(row)
    db.commit()


@case_router.post("/monitors/{monitor_id}/subscriptions/{subscription_id}/preview")
def preview_subscription(
    case: AnalystCase,
    db: DbDep,
    settings: SettingsDep,
    monitor_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> PayloadPreview:
    monitor = monitoring.get_monitor(db, case.id, monitor_id)
    row = db.scalar(
        select(MonitorSubscription).where(
            MonitorSubscription.id == subscription_id, MonitorSubscription.monitor_id == monitor.id
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="subscription_not_found")
    destination = db.get(NotificationDestination, row.destination_id)
    assert destination is not None
    event = notifications.Event(
        event_type=EventType(row.event_types[0]),
        severity=Severity.INFO,
        case_id=case.id,
        monitor=monitor,
        title="preview",
        body="preview",
        link=f"/cases/{case.id}/monitors/{monitor.id}",
        dedupe_key=f"preview:{row.id}",
        summary={
            "new": 1,
            "changed": 0,
            "not_observed": 0,
            "conflicting": 0,
            "unknown": 0,
            "change_sets": 1,
        },
    )
    return preview_for(settings, destination, event)
