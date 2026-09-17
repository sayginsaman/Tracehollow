"""Monitor schedules: validation and occurrence times.

Two kinds of schedule, with explicit time semantics (docs/monitoring/README.md):

* ``interval`` — every N minutes of elapsed time, counted from the monitor's anchor in UTC.
  Daylight-saving changes never shift an interval schedule; the timezone is used for display.
* ``daily`` / ``weekly`` — a wall-clock time in an IANA timezone, optionally on selected weekdays.
  When the local time does not exist (the clocks jump forward over it), the occurrence runs the
  same length of time later (02:30 in a 02:00-03:00 gap runs at 03:30). When the local time occurs
  twice (the clocks go back), it runs once, at the first of the two instants.

All returned instants are timezone-aware UTC datetimes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_SLOT_SCAN = 10_000
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Schedule:
    kind: Literal["interval", "daily", "weekly"]
    timezone: str
    every_minutes: int | None = None
    at: time | None = None
    weekdays: tuple[int, ...] | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def describe(self) -> dict[str, Any]:
        if self.kind == "interval":
            return {"kind": "interval", "every_minutes": self.every_minutes}
        assert self.at is not None
        value: dict[str, Any] = {"kind": self.kind, "time": self.at.strftime("%H:%M")}
        if self.kind == "weekly":
            value["weekdays"] = list(self.weekdays or ())
        return value

    def nominal_interval(self) -> timedelta:
        """Typical spacing between occurrences (used for lateness tolerance)."""
        if self.kind == "interval":
            assert self.every_minutes is not None
            return timedelta(minutes=self.every_minutes)
        if self.kind == "daily":
            return timedelta(days=1)
        return timedelta(days=7) / max(1, len(self.weekdays or (0,)))


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown timezone '{name}'") from None
    return name


def parse(raw: dict[str, Any], timezone: str, *, min_interval_minutes: int) -> Schedule:
    """Validate a stored or submitted schedule."""
    validate_timezone(timezone)
    kind = raw.get("kind")
    if kind == "interval":
        every = raw.get("every_minutes")
        if isinstance(every, bool) or not isinstance(every, int):
            raise ScheduleError("every_minutes must be a whole number of minutes")
        if every < min_interval_minutes:
            raise ScheduleError(
                f"monitors may run at most every {min_interval_minutes} minutes on this "
                "installation"
            )
        if every > 60 * 24 * 90:
            raise ScheduleError("every_minutes must be at most 90 days")
        return Schedule(kind="interval", timezone=timezone, every_minutes=every)
    if kind in ("daily", "weekly"):
        value = raw.get("time")
        match = _TIME.match(value) if isinstance(value, str) else None
        if match is None:
            raise ScheduleError("time must be HH:MM (24-hour clock)")
        at = time(int(match.group(1)), int(match.group(2)))
        weekdays: tuple[int, ...] | None = None
        if kind == "weekly":
            days = raw.get("weekdays")
            if (
                not isinstance(days, list)
                or not days
                or not all(isinstance(d, int) and not isinstance(d, bool) for d in days)
                or not all(0 <= d <= 6 for d in days)
            ):
                raise ScheduleError("weekdays must list days 0 (Monday) to 6 (Sunday)")
            weekdays = tuple(sorted(set(days)))
        elif "weekdays" in raw:
            raise ScheduleError("weekdays apply to weekly schedules only")
        return Schedule(kind=kind, timezone=timezone, at=at, weekdays=weekdays)
    raise ScheduleError("schedule kind must be interval, daily or weekly")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC)


def local_occurrence(schedule: Schedule, day: date) -> datetime:
    """The UTC instant of ``schedule.at`` on a local calendar day (gap and overlap rules above)."""
    assert schedule.at is not None
    # fold=0: in a gap this resolves with the offset before the transition (later wall time); in an
    # overlap it is the first occurrence.
    wall = datetime.combine(day, schedule.at, tzinfo=schedule.zone)
    return wall.astimezone(UTC)


def next_after(schedule: Schedule, after: datetime, *, anchor: datetime) -> datetime:
    """The first occurrence strictly after ``after``."""
    after = _utc(after)
    if schedule.kind == "interval":
        assert schedule.every_minutes is not None
        step = timedelta(minutes=schedule.every_minutes)
        anchor = _utc(anchor)
        if after < anchor:
            return anchor
        elapsed = (after - anchor) // step
        return anchor + step * (elapsed + 1)
    local_day = after.astimezone(schedule.zone).date() - timedelta(days=1)
    for offset in range(0, 370):
        day = local_day + timedelta(days=offset)
        if schedule.weekdays is not None and day.weekday() not in schedule.weekdays:
            continue
        candidate = local_occurrence(schedule, day)
        if candidate > after:
            return candidate
    raise ScheduleError("no occurrence within a year")  # unreachable for valid schedules


def at_or_before(schedule: Schedule, moment: datetime, *, anchor: datetime) -> datetime | None:
    """The latest occurrence at or before ``moment`` (None before the first one)."""
    moment = _utc(moment)
    if schedule.kind == "interval":
        assert schedule.every_minutes is not None
        step = timedelta(minutes=schedule.every_minutes)
        anchor = _utc(anchor)
        if moment < anchor:
            return None
        return anchor + step * ((moment - anchor) // step)
    local_day = moment.astimezone(schedule.zone).date() + timedelta(days=1)
    for offset in range(0, 370):
        day = local_day - timedelta(days=offset)
        if schedule.weekdays is not None and day.weekday() not in schedule.weekdays:
            continue
        candidate = local_occurrence(schedule, day)
        if candidate <= moment:
            return candidate
    return None


def count_between(schedule: Schedule, start: datetime, end: datetime, *, anchor: datetime) -> int:
    """Occurrences with ``start <= t < end`` (bounded by MAX_SLOT_SCAN)."""
    start, end = _utc(start), _utc(end)
    if end <= start:
        return 0
    if schedule.kind == "interval":
        assert schedule.every_minutes is not None
        step = timedelta(minutes=schedule.every_minutes)
        first = (
            start
            if (start - _utc(anchor)) % step == timedelta(0)
            else next_after(schedule, start, anchor=anchor)
        )
        if first >= end:
            return 0
        return min(MAX_SLOT_SCAN, (end - first - timedelta(microseconds=1)) // step + 1)
    count = 0
    cursor = start - timedelta(microseconds=1)
    while count < MAX_SLOT_SCAN:
        cursor = next_after(schedule, cursor, anchor=anchor)
        if cursor >= end:
            break
        count += 1
    return count
