"""Recording audit events.

Events are written in the caller's transaction, so an action and its record commit or roll back
together. Denials are written by :func:`record_denial` in their own short transaction, because the
request that was denied does not commit anything.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import CursorResult, delete
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import ActorType, AuditEvent, AuditOutcome
from app.auth.models import User
from app.db.base import utcnow
from app.db.session import session_scope
from app.logging_config import REDACTED, redact_value

logger = logging.getLogger(__name__)

MAX_DETAIL_KEYS = 30
MAX_DETAIL_STRING = 200
MAX_DETAIL_LIST = 20


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    label: str
    user_id: uuid.UUID | None = None
    correlation_id: str | None = None

    def with_correlation(self, correlation_id: str | uuid.UUID | None) -> Actor:
        return Actor(
            type=self.type,
            label=self.label,
            user_id=self.user_id,
            correlation_id=str(correlation_id)[:64] if correlation_id is not None else None,
        )


def user_actor(user: User, correlation_id: str | None = None) -> Actor:
    return Actor(
        type=ActorType.USER,
        label=user.username[:64],
        user_id=user.id,
        correlation_id=correlation_id[:64] if correlation_id else None,
    )


def service_actor(name: str, correlation_id: str | uuid.UUID | None = None) -> Actor:
    return Actor(
        type=ActorType.SERVICE,
        label=name[:64],
        correlation_id=str(correlation_id)[:64] if correlation_id is not None else None,
    )


SYSTEM_ACTOR = Actor(type=ActorType.SYSTEM, label="system")


def _clean(key: str, value: Any, depth: int = 0) -> Any:
    value = redact_value(key, value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        return value[:MAX_DETAIL_STRING]
    if isinstance(value, list | tuple) and depth < 2:
        return [_clean(key, item, depth + 1) for item in list(value)[:MAX_DETAIL_LIST]]
    if isinstance(value, dict) and depth < 2:
        return {
            str(k)[:64]: _clean(str(k), v, depth + 1)
            for k, v in list(value.items())[:MAX_DETAIL_KEYS]
        }
    return REDACTED


def sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Bounded, redacted copy of event details (secret-looking keys and values are removed)."""
    if not details:
        return {}
    return {
        str(key)[:64]: _clean(str(key), value)
        for key, value in list(details.items())[:MAX_DETAIL_KEYS]
    }


def record(
    db: Session,
    actor: Actor,
    action: str,
    *,
    outcome: AuditOutcome = AuditOutcome.SUCCEEDED,
    case_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    correlation_id: str | uuid.UUID | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=uuid.uuid4(),
        occurred_at=utcnow(),
        actor_type=actor.type,
        actor_user_id=actor.user_id,
        actor_label=actor.label,
        case_id=case_id,
        action=action[:64],
        outcome=outcome,
        target_type=target_type[:32] if target_type else None,
        target_id=str(target_id)[:64] if target_id is not None else None,
        correlation_id=(
            str(correlation_id)[:64] if correlation_id is not None else actor.correlation_id
        ),
        details=sanitize_details(details),
    )
    db.add(event)
    return event


def record_denial(
    session_factory: sessionmaker[Session],
    actor: Actor,
    action: str,
    *,
    case_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record a refused action in its own transaction; never fails the caller."""
    try:
        with session_scope(session_factory) as db:
            record(db, actor, action, outcome=AuditOutcome.DENIED, case_id=case_id, details=details)
    except Exception:
        logger.exception("audit_denial_not_recorded")


def prune(session_factory: sessionmaker[Session], *, retention_days: int) -> int:
    """Delete events older than the retention period and record that pruning happened."""
    cutoff = utcnow() - timedelta(days=retention_days)
    with session_scope(session_factory) as db:
        result: CursorResult[Any] = db.execute(  # type: ignore[assignment]
            delete(AuditEvent).where(AuditEvent.occurred_at < cutoff)
        )
        removed = result.rowcount
        if removed:
            record(
                db,
                SYSTEM_ACTOR,
                "audit.pruned",
                details={"removed": int(removed), "retention_days": retention_days},
            )
    return int(removed or 0)
