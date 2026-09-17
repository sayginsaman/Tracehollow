"""Optional webhook adapter: bounded, signed, at-least-once delivery of minimal payloads.

Off unless ``TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED`` is true. Runs in the collector, whose
outbound requests pass the same address policy as collection (no loopback, link-local, metadata
or unlisted private addresses; redirects are not followed). Before every attempt the delivery is
re-checked: adapter switched on, destination still enabled and still accepting the event type,
subscription still present, case still readable, the analyst who subscribed still an analyst of
the case, and no configured secret inside the payload. Receivers must deduplicate on the event ID:
a delivery whose response was lost is sent again with the same ID.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx2
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.audit.models import AuditOutcome
from app.audit.service import record, service_actor
from app.cases.access import has_analyst_access
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.connectors import limits, netguard
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.integrations import crypto
from app.notifications.models import (
    DeliveryStatus,
    MonitorSubscription,
    NotificationDelivery,
    NotificationDestination,
)
from app.notifications.service import PAYLOAD_VERSION, SUMMARY_KEYS

logger = logging.getLogger(__name__)

NOTIFIER = "notifier"
SEALING_SCOPE = "notification_destination"  # associated data for encrypted signing keys
PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "event_id",
        "event_type",
        "severity",
        "occurred_at",
        "generator",
        "case_id",
        "monitor_id",
        "occurrence_id",
        "query_run_id",
        "summary",
        "link",
    }
)
RETRYABLE_STATUS = frozenset({408, 425, 429})


@dataclass
class DeliveryContext:
    session_factory: sessionmaker[Session]
    settings: Settings
    worker_name: str
    sleep: Callable[[float], None] = time.sleep
    transport: httpx2.BaseTransport | None = None
    resolver: Callable[[str, int], list[str]] | None = field(default=None)


def seal_secret(settings: Settings, destination_id: uuid.UUID, secret: str) -> crypto.Sealed:
    return crypto.seal(settings, SEALING_SCOPE, str(destination_id), secret)


def open_secret(settings: Settings, destination: NotificationDestination) -> str | None:
    if destination.secret_ciphertext is None or destination.secret_nonce is None:
        return None
    return crypto.open_sealed(
        settings,
        SEALING_SCOPE,
        str(destination.id),
        destination.secret_ciphertext,
        destination.secret_nonce,
        destination.secret_key_id or "",
    )


def signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def validate_payload(payload: dict[str, Any], forbidden: list[str]) -> str | None:
    """Reason the payload must not leave the installation, or None."""
    if set(payload) - PAYLOAD_KEYS or payload.get("schema") != PAYLOAD_VERSION:
        return "payload_not_allowlisted"
    summary = payload.get("summary") or {}
    if not isinstance(summary, dict) or set(summary) - SUMMARY_KEYS:
        return "payload_not_allowlisted"
    serialized = json.dumps(payload, ensure_ascii=False)
    if any(value and value in serialized for value in forbidden):
        return "secret_in_payload"
    return None


def _policy(ctx: DeliveryContext) -> netguard.NetworkPolicy:
    return netguard.NetworkPolicy(
        allowed_ports=frozenset(ctx.settings.collection_allowed_ports),
        allowed_private_networks=netguard.parse_networks(
            ctx.settings.collection_allowed_private_networks
        ),
        resolver=ctx.resolver or netguard.system_resolver,
    )


def _claim(ctx: DeliveryContext, delivery_id: uuid.UUID) -> uuid.UUID | None:
    token = uuid.uuid4()
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        claimed = db.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status == DeliveryStatus.PENDING,
                NotificationDelivery.next_attempt_at <= now,
                (NotificationDelivery.lease_expires_at.is_(None))
                | (NotificationDelivery.lease_expires_at < now),
            )
            .values(
                lease_token=token,
                lease_expires_at=now
                + timedelta(seconds=ctx.settings.notification_delivery_timeout_seconds + 60),
                attempts=NotificationDelivery.attempts + 1,
            )
            .returning(NotificationDelivery.id)
        ).first()
    return token if claimed else None


def _blocker(
    ctx: DeliveryContext, db: Session, delivery: NotificationDelivery
) -> tuple[str | None, NotificationDestination | None]:
    if not ctx.settings.notifications_external_enabled:
        return "adapter_disabled", None
    destination = db.get(NotificationDestination, delivery.destination_id)
    if destination is None or not destination.enabled:
        return "destination_disabled", destination
    if delivery.event_type not in destination.event_types:
        return "event_type_not_allowed", destination
    subscription = (
        db.get(MonitorSubscription, delivery.subscription_id) if delivery.subscription_id else None
    )
    if subscription is None or delivery.event_type not in subscription.event_types:
        return "subscription_removed", destination
    case = db.get(Case, delivery.case_id)
    if case is None or case.status not in (CaseStatus.ACTIVE, CaseStatus.ARCHIVED):
        return "case_unavailable", destination
    if not has_analyst_access(db, subscription.created_by_user_id, delivery.case_id):
        return "authorization_lost", destination
    return None, destination


def _finish(
    ctx: DeliveryContext,
    delivery_id: uuid.UUID,
    token: uuid.UUID,
    *,
    status: DeliveryStatus,
    error_code: str | None,
    response_status: int | None = None,
    retry_in: float | None = None,
) -> str:
    actor = service_actor(NOTIFIER, delivery_id)
    with session_scope(ctx.session_factory) as db:
        delivery = db.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None or delivery.lease_token != token:
            return "lease_lost"
        delivery.lease_token = None
        delivery.lease_expires_at = None
        delivery.last_error_code = error_code
        delivery.last_response_status = response_status
        destination = db.get(NotificationDestination, delivery.destination_id)
        now = utcnow()
        if (
            retry_in is not None
            and delivery.attempts < ctx.settings.notification_delivery_max_attempts
        ):
            delivery.status = DeliveryStatus.PENDING
            delivery.next_attempt_at = now + timedelta(seconds=retry_in)
            outbox = dispatch.enqueue(
                db,
                task_name=dispatch.DELIVER_NOTIFICATION_TASK,
                aggregate_type=AggregateType.NOTIFICATION_DELIVERY,
                aggregate_id=delivery.id,
                case_id=delivery.case_id,
            )
            outbox.available_at = delivery.next_attempt_at
            outcome = "retrying"
        else:
            delivery.status = status if retry_in is None else DeliveryStatus.FAILED
            if delivery.status == DeliveryStatus.DELIVERED:
                delivery.delivered_at = now
            dispatch.mark_done(db, AggregateType.NOTIFICATION_DELIVERY, delivery.id)
            outcome = str(delivery.status)
        if destination is not None and error_code != "adapter_disabled":
            destination.last_delivery_at = now
            destination.last_status = (
                outcome if error_code is None else f"{outcome}:{error_code}"[:32]
            )
            destination.consecutive_failures = (
                0
                if delivery.status == DeliveryStatus.DELIVERED
                else destination.consecutive_failures + 1
            )
        record(
            db,
            actor,
            {
                "delivered": "notification.delivered",
                "retrying": "notification.delivery_retry",
                "failed": "notification.delivery_failed",
                "blocked": "notification.delivery_blocked",
            }[outcome],
            outcome=AuditOutcome.SUCCEEDED
            if outcome == "delivered"
            else (AuditOutcome.DENIED if outcome == "blocked" else AuditOutcome.FAILED),
            case_id=delivery.case_id,
            target_type="notification_delivery",
            target_id=delivery.id,
            details={
                "destination_id": delivery.destination_id,
                "event_id": delivery.event_id,
                "event_type": delivery.event_type,
                "attempt": delivery.attempts,
                "error_code": error_code,
                "response_status": response_status,
            },
        )
    logger.info(
        "notification_delivery_finished",
        extra={"delivery_ref": str(delivery_id)[:8], "outcome": outcome, "code": error_code},
    )
    return outcome


def _backoff(attempts: int, retry_after: str | None) -> float:
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after.strip()), 3600.0)
    return float(min(3600, 30 * (2 ** max(0, attempts - 1))))


def deliver(ctx: DeliveryContext, delivery_id: uuid.UUID) -> str:
    token = _claim(ctx, delivery_id)
    if token is None:
        return "skipped"
    with session_scope(ctx.session_factory) as db:
        delivery = db.get(NotificationDelivery, delivery_id)
        assert delivery is not None
        reason, destination = _blocker(ctx, db, delivery)
        payload = dict(delivery.payload)
        attempts = delivery.attempts
        destination_id = delivery.destination_id
        url = destination.url if destination is not None else ""
        rate = destination.max_per_minute if destination is not None else 1
        secret: str | None = None
        if reason is None and destination is not None:
            try:
                secret = open_secret(ctx.settings, destination)
            except (crypto.CredentialDecryptionError, crypto.CredentialStoreUnavailableError):
                reason = "secret_unreadable"
    if reason is not None:
        return _finish(ctx, delivery_id, token, status=DeliveryStatus.BLOCKED, error_code=reason)
    forbidden = [*ctx.settings.secret_values(), *([secret] if secret else [])]
    problem = validate_payload(payload, forbidden)
    if problem is not None:
        return _finish(ctx, delivery_id, token, status=DeliveryStatus.BLOCKED, error_code=problem)

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "content-type": "application/json",
        "user-agent": f"Tracehollow/{__version__} (webhook)",
        "x-tracehollow-event-id": str(payload["event_id"]),
        "x-tracehollow-timestamp": timestamp,
    }
    if secret:
        headers["x-tracehollow-signature"] = signature(secret, timestamp, body)
    limits.pace(
        ctx.session_factory,
        f"webhook:{destination_id}",
        60.0 / max(1, rate),
        sleep=ctx.sleep,
        cancelled=lambda: False,
    )
    try:
        result = netguard.post(
            url,
            policy=_policy(ctx),
            body=body,
            headers=headers,
            timeout_seconds=ctx.settings.notification_delivery_timeout_seconds,
            transport=ctx.transport,
        )
    except netguard.DestinationBlockedError as exc:
        return _finish(
            ctx, delivery_id, token, status=DeliveryStatus.BLOCKED, error_code=exc.code[:64]
        )
    except netguard.FetchError as exc:
        return _finish(
            ctx,
            delivery_id,
            token,
            status=DeliveryStatus.FAILED,
            error_code=exc.code[:64],
            retry_in=_backoff(attempts, None),
        )
    if 200 <= result.status_code < 300:
        return _finish(
            ctx,
            delivery_id,
            token,
            status=DeliveryStatus.DELIVERED,
            error_code=None,
            response_status=result.status_code,
        )
    retryable = result.status_code in RETRYABLE_STATUS or result.status_code >= 500
    return _finish(
        ctx,
        delivery_id,
        token,
        status=DeliveryStatus.FAILED,
        error_code=f"http_{result.status_code}",
        response_status=result.status_code,
        retry_in=_backoff(attempts, result.headers.get("retry-after")) if retryable else None,
    )
