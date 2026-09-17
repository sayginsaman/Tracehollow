"""Pruning of monitoring records that are not case content."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.db.session import session_scope
from app.monitoring.models import MonitorOccurrence, OccurrenceStatus


def prune_occurrences(session_factory: sessionmaker[Session], *, retention_days: int) -> int:
    """Remove old *skipped* occurrences; dispatched ones stay with their executions."""
    cutoff = utcnow() - timedelta(days=retention_days)
    with session_scope(session_factory) as db:
        result = db.execute(
            delete(MonitorOccurrence).where(
                MonitorOccurrence.status == OccurrenceStatus.SKIPPED,
                MonitorOccurrence.created_at < cutoff,
            )
        )
    return int(getattr(result, "rowcount", 0) or 0)
