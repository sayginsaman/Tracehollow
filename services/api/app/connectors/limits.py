"""Per-source concurrency and request pacing, enforced in PostgreSQL across all collectors.

* **Slots** bound how many connector runs of one connector execute at once. A slot is a lease
  row (connector id, slot number) claimed with a conditional upsert; a crashed worker's slot is
  reclaimable once its lease expires, and leases are renewed while the run makes progress.
* **Pacing** spaces requests to one key (a host or an API) by a minimum interval. Reserving the
  next start time is a single atomic statement, so concurrent workers queue up instead of
  bursting.

Pacing keys are operational metadata (host names), are not exposed through the API and are
pruned by the dispatcher.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.db.session import session_scope
from app.integrations.models import SourcePacing, SourceSlot


def try_claim_slot(
    session_factory: sessionmaker[Session],
    connector_id: str,
    max_slots: int,
    holder: uuid.UUID,
    lease_seconds: int,
) -> int | None:
    with session_scope(session_factory) as db:
        for slot in range(max_slots):
            claimed = db.execute(
                text(
                    "INSERT INTO source_slots (connector_id, slot, holder, expires_at) "
                    "VALUES (:connector, :slot, :holder, now() + make_interval(secs => :lease)) "
                    "ON CONFLICT (connector_id, slot) DO UPDATE "
                    "SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at "
                    "WHERE source_slots.expires_at < now() OR source_slots.holder = :holder "
                    "RETURNING slot"
                ),
                {"connector": connector_id, "slot": slot, "holder": holder, "lease": lease_seconds},
            ).first()
            if claimed is not None:
                return slot
    return None


def renew_slot(
    session_factory: sessionmaker[Session], connector_id: str, holder: uuid.UUID, lease_seconds: int
) -> None:
    with session_scope(session_factory) as db:
        db.execute(
            text(
                "UPDATE source_slots SET expires_at = now() + make_interval(secs => :lease) "
                "WHERE connector_id = :connector AND holder = :holder"
            ),
            {"connector": connector_id, "holder": holder, "lease": lease_seconds},
        )


def release_slot(
    session_factory: sessionmaker[Session], connector_id: str, holder: uuid.UUID
) -> None:
    with session_scope(session_factory) as db:
        db.execute(
            delete(SourceSlot).where(
                SourceSlot.connector_id == connector_id, SourceSlot.holder == holder
            )
        )


def reserve_request(
    session_factory: sessionmaker[Session], key: str, interval_seconds: float
) -> float:
    """Reserve the next request start for ``key``; return how long to wait before sending."""
    if interval_seconds <= 0:
        return 0.0
    with session_scope(session_factory) as db:
        start: datetime = db.execute(
            text(
                "INSERT INTO source_pacing (key, next_allowed_at) "
                "VALUES (:key, now() + make_interval(secs => :interval)) "
                "ON CONFLICT (key) DO UPDATE SET next_allowed_at = "
                "GREATEST(source_pacing.next_allowed_at, now()) "
                "+ make_interval(secs => :interval) "
                "RETURNING next_allowed_at - make_interval(secs => :interval)"
            ),
            {"key": key[:300], "interval": interval_seconds},
        ).scalar_one()
        now: datetime = db.execute(text("SELECT now()")).scalar_one()
    return max(0.0, (start - now).total_seconds())


def pace(
    session_factory: sessionmaker[Session],
    key: str,
    interval_seconds: float,
    *,
    sleep: Callable[[float], None],
    cancelled: Callable[[], bool],
) -> None:
    wait = reserve_request(session_factory, key, interval_seconds)
    while wait > 0 and not cancelled():
        step = min(wait, 1.0)
        sleep(step)
        wait -= step


def prune(session_factory: sessionmaker[Session], older_than: timedelta) -> None:
    cutoff = utcnow() - older_than
    with session_scope(session_factory) as db:
        db.execute(delete(SourcePacing).where(SourcePacing.next_allowed_at < cutoff))
        db.execute(delete(SourceSlot).where(SourceSlot.expires_at < cutoff))
