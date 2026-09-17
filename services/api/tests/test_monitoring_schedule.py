"""Monitor schedule arithmetic: intervals, local times, daylight saving and missed slots."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.monitoring import schedule as schedules

ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)


def _daily(time: str, timezone: str) -> schedules.Schedule:
    return schedules.parse({"kind": "daily", "time": time}, timezone, min_interval_minutes=60)


def test_interval_schedules_count_elapsed_time_and_ignore_daylight_saving() -> None:
    schedule = schedules.parse(
        {"kind": "interval", "every_minutes": 60}, "Europe/Berlin", min_interval_minutes=15
    )
    anchor = datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
    # Across the 2026-03-29 spring-forward transition the spacing stays 60 minutes of real time.
    slots = [anchor]
    for _ in range(4):
        slots.append(schedules.next_after(schedule, slots[-1], anchor=anchor))
    assert [b - a for a, b in pairwise(slots)] == [timedelta(hours=1)] * 4
    assert schedules.next_after(schedule, anchor - timedelta(days=1), anchor=anchor) == anchor
    assert schedules.at_or_before(schedule, anchor + timedelta(minutes=150), anchor=anchor) == (
        anchor + timedelta(hours=2)
    )
    assert schedules.at_or_before(schedule, anchor - timedelta(seconds=1), anchor=anchor) is None


def test_local_time_in_a_spring_forward_gap_runs_after_the_gap() -> None:
    schedule = _daily("02:30", "Europe/Berlin")
    day_before = schedules.next_after(schedule, datetime(2026, 3, 28, 0, tzinfo=UTC), anchor=ANCHOR)
    gap_day = schedules.next_after(schedule, day_before, anchor=ANCHOR)
    # 02:30 does not exist on 2026-03-29 in Berlin; it runs at 03:30 CEST (01:30 UTC).
    assert day_before == datetime(2026, 3, 28, 1, 30, tzinfo=UTC)
    assert gap_day == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert gap_day.astimezone(schedule.zone).strftime("%H:%M") == "03:30"
    following = schedules.next_after(schedule, gap_day, anchor=ANCHOR)
    assert following == datetime(2026, 3, 30, 0, 30, tzinfo=UTC)


def test_local_time_repeated_by_fall_back_runs_once_at_the_first_instant() -> None:
    schedule = _daily("02:30", "Europe/Berlin")
    overlap = schedules.next_after(schedule, datetime(2026, 10, 24, 12, tzinfo=UTC), anchor=ANCHOR)
    assert overlap == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST, the first 02:30
    after = schedules.next_after(schedule, overlap, anchor=ANCHOR)
    # The second 02:30 (CET, 01:30 UTC) is not scheduled again.
    assert after == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)


def test_weekly_schedules_use_local_weekdays() -> None:
    schedule = schedules.parse(
        {"kind": "weekly", "time": "09:00", "weekdays": [4, 0]},
        "Europe/Istanbul",
        min_interval_minutes=60,
    )
    thursday = datetime(2026, 9, 17, 12, tzinfo=UTC)
    assert schedules.next_after(schedule, thursday, anchor=ANCHOR) == datetime(
        2026, 9, 18, 6, tzinfo=UTC
    )
    assert schedules.at_or_before(schedule, thursday, anchor=ANCHOR) == datetime(
        2026, 9, 14, 6, tzinfo=UTC
    )
    september = schedules.count_between(
        schedule,
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
        anchor=ANCHOR,
    )
    assert september == 8
    assert schedule.describe() == {"kind": "weekly", "time": "09:00", "weekdays": [0, 4]}


def test_missed_slots_are_counted_without_enumerating_unbounded_ranges() -> None:
    schedule = schedules.parse(
        {"kind": "interval", "every_minutes": 1}, "UTC", min_interval_minutes=1
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert schedules.count_between(schedule, start, start + timedelta(minutes=5), anchor=start) == 5
    long_downtime = schedules.count_between(
        schedule, start, start + timedelta(days=365), anchor=start
    )
    assert long_downtime == schedules.MAX_SLOT_SCAN


@pytest.mark.parametrize(
    ("raw", "timezone", "message"),
    [
        ({"kind": "interval", "every_minutes": 5}, "UTC", "at most every 60 minutes"),
        ({"kind": "interval", "every_minutes": "60"}, "UTC", "whole number"),
        ({"kind": "daily", "time": "24:00"}, "UTC", "HH:MM"),
        ({"kind": "daily", "time": "09:00", "weekdays": [1]}, "UTC", "weekly schedules only"),
        ({"kind": "weekly", "time": "09:00", "weekdays": [7]}, "UTC", "0 (Monday) to 6"),
        ({"kind": "weekly", "time": "09:00", "weekdays": []}, "UTC", "0 (Monday) to 6"),
        ({"kind": "cron", "expression": "* * * * *"}, "UTC", "interval, daily or weekly"),
        ({"kind": "daily", "time": "09:00"}, "Mars/Olympus", "unknown timezone"),
    ],
)
def test_invalid_schedules_are_refused(raw: dict[str, object], timezone: str, message: str) -> None:
    with pytest.raises(schedules.ScheduleError, match=re.escape(message)):
        schedules.parse(raw, timezone, min_interval_minutes=60)
