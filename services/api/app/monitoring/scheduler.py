"""Durable monitor scheduling, run by every dispatcher process.

* The due set is read from PostgreSQL (``status = enabled AND next_run_at <= now``).
* Each monitor is handled in its own transaction under ``SELECT ... FOR UPDATE SKIP LOCKED``; a
  second scheduler skips a monitor another one is handling, and re-checks ``next_run_at`` after
  the lock, so a slot it has already advanced past is not dispatched again.
* The occurrence row is unique per ``(monitor, scheduled_for)`` and is written in the same
  transaction as the execution and its outbox row, so a crash before commit leaves nothing and a
  crash after commit leaves a dispatched occurrence the outbox publishes.
* After downtime at most one slot is dispatched (``run_latest``) or recorded as missed (``skip``),
  together with the number of slots that were missed; there is never a catch-up burst.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import service_actor
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.monitoring import schedule as schedules
from app.monitoring import service
from app.monitoring.models import MissedRunPolicy, Monitor, MonitorStatus, OccurrenceKind

logger = logging.getLogger(__name__)


@dataclass
class SchedulerStats:
    dispatched: int = 0
    skipped: int = 0
    already_handled: int = 0
    errors: int = 0


def due_monitor_ids(
    session_factory: sessionmaker[Session], now: datetime, limit: int
) -> list[uuid.UUID]:
    with session_scope(session_factory) as db:
        return list(
            db.scalars(
                select(Monitor.id)
                .where(Monitor.status == MonitorStatus.ENABLED, Monitor.next_run_at <= now)
                .order_by(Monitor.next_run_at)
                .limit(limit)
            )
        )


def _slot_to_run(settings: Settings, monitor: Monitor, now: datetime) -> tuple[datetime, int, bool]:
    """(slot, missed slots before it, whether the slot itself was missed)."""
    assert monitor.next_run_at is not None
    schedule = service.parse_schedule(settings, monitor)
    due = monitor.next_run_at
    grace = min(
        timedelta(seconds=settings.monitor_misfire_grace_seconds), schedule.nominal_interval() / 2
    )
    if now - due <= grace:
        return due, 0, False
    latest = schedules.at_or_before(schedule, now, anchor=monitor.schedule_anchor) or due
    latest = max(latest, due)
    missed = schedules.count_between(schedule, due, latest, anchor=monitor.schedule_anchor)
    if now - latest > grace:
        # Even the most recent slot is too far in the past to count as on time.
        return latest, missed, True
    return latest, missed, False


def schedule_one(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monitor_id: uuid.UUID,
    *,
    now: datetime,
    instance: str,
) -> str:
    actor = service_actor(service.SCHEDULER)
    with session_scope(session_factory) as db:
        monitor = db.scalar(
            select(Monitor)
            .where(
                Monitor.id == monitor_id,
                Monitor.status == MonitorStatus.ENABLED,
                Monitor.next_run_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
        if monitor is None:
            return "already_handled"
        try:
            slot, missed, slot_missed = _slot_to_run(settings, monitor, now)
            schedule = service.parse_schedule(settings, monitor)
        except schedules.ScheduleError:
            service._change_status(monitor, MonitorStatus.PAUSED, "invalid_schedule")
            return "skipped"
        skip_missed = slot_missed and monitor.missed_run_policy == MissedRunPolicy.SKIP
        result = None
        if skip_missed:
            result = service.record_missed_slot(
                db,
                monitor,
                scheduled_for=slot,
                missed_slots=missed,
                dispatched_by=instance,
                actor=actor,
            )
        else:
            result = service.dispatch_occurrence(
                db,
                settings,
                monitor,
                scheduled_for=slot,
                kind=OccurrenceKind.SCHEDULED,
                missed_slots=missed,
                dispatched_by=instance,
                actor=actor,
            )
        monitor.last_scheduled_for = slot
        if monitor.status == MonitorStatus.ENABLED:
            monitor.next_run_at = schedules.next_after(
                schedule, max(slot, now), anchor=monitor.schedule_anchor
            )
        if result is None:
            return "already_handled"
        return "dispatched" if result.outbox_id is not None else "skipped"


def schedule_due(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    instance: str,
    now: datetime | None = None,
    limit: int = 50,
) -> SchedulerStats:
    now = now or utcnow()
    stats = SchedulerStats()
    for monitor_id in due_monitor_ids(session_factory, now, limit):
        try:
            outcome = schedule_one(
                session_factory, settings, monitor_id, now=now, instance=instance
            )
        except Exception:
            stats.errors += 1
            logger.exception("monitor_schedule_failed", extra={"monitor_ref": str(monitor_id)[:8]})
            continue
        if outcome == "dispatched":
            stats.dispatched += 1
        elif outcome == "skipped":
            stats.skipped += 1
        else:
            stats.already_handled += 1
    if stats.dispatched or stats.skipped or stats.errors:
        logger.info(
            "monitors_scheduled",
            extra={
                "dispatched": stats.dispatched,
                "skipped": stats.skipped,
                "errors": stats.errors,
                "instance": instance,
            },
        )
    return stats
