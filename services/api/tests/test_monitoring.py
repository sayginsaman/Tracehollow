"""Scheduled monitoring: lifecycle, durable scheduling, restart, overlap and re-checks."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditEvent
from app.config import Settings
from app.db.base import utcnow
from app.dispatch.models import AggregateType, DispatchOutbox
from app.monitoring.models import Monitor, MonitorOccurrence
from app.notifications.models import Notification
from app.queries.models import QueryRun
from tests.conftest import browser_headers, create_case
from tests.monitoring_helpers import (
    create_monitor,
    execute,
    later,
    monitor_runs,
    run_scheduler,
    saved_query,
    set_next_run,
)
from tests.test_team_access import ANALYST, SECOND_ANALYST, VIEWER, _team, add_member, signed_in

pytestmark = pytest.mark.integration


def _occurrences(factory: sessionmaker[Session], monitor_id: object) -> list[MonitorOccurrence]:
    with factory() as db:
        return list(
            db.scalars(
                select(MonitorOccurrence)
                .where(MonitorOccurrence.monitor_id == uuid.UUID(str(monitor_id)))
                .order_by(MonitorOccurrence.created_at)
            )
        )


def _enable(client: TestClient, csrf: str, case_id: object, monitor_id: object, **body: Any) -> Any:
    return client.post(
        f"/api/v1/cases/{case_id}/monitors/{monitor_id}/resume",
        json=body,
        headers=browser_headers(csrf),
    )


def test_monitors_are_created_paused_and_recurring_live_collection_needs_confirmation(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Monitor lifecycle")
    fixture = saved_query(client, authed, case["id"])
    monitor = create_monitor(client, authed, case["id"], fixture["id"])
    assert monitor["status"] == "paused"
    assert monitor["status_reason"] == "created"
    assert monitor["next_run_at"] is None
    assert monitor["collects_live"] is False
    assert monitor["actions"][0]["code"] == "created"
    # Nothing is scheduled for a paused monitor, however much time passes.
    assert run_scheduler(settings, db_session_factory, now=later(600)).dispatched == 0

    enabled = _enable(client, authed, case["id"], monitor["id"])
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "enabled"
    assert enabled.json()["authorized_by"] == "analyst.admin"
    assert enabled.json()["next_run_at"] is not None

    web = saved_query(
        client,
        authed,
        case["id"],
        connector="public_web.page",
        input_type="url",
        value="https://ornek.example/",
        limits={"max_pages": 1, "max_items_per_page": 5},
    )
    refused = client.post(
        f"/api/v1/cases/{case['id']}/monitors",
        json={
            "name": "Live page",
            "saved_query_id": web["id"],
            "schedule": {"kind": "daily", "time": "09:00"},
            "scope": {"max_pages": 1, "max_items_per_page": 5},
            "enable": True,
        },
        headers=browser_headers(authed),
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "recurring_collection_not_acknowledged"
    too_wide = client.post(
        f"/api/v1/cases/{case['id']}/monitors",
        json={
            "name": "Too wide",
            "saved_query_id": web["id"],
            "schedule": {"kind": "daily", "time": "09:00"},
            "scope": {"max_pages": 5, "max_items_per_page": 5},
        },
        headers=browser_headers(authed),
    )
    assert too_wide.status_code == 422
    assert too_wide.json()["detail"]["code"] == "scope_exceeds_query"
    too_often = client.post(
        f"/api/v1/cases/{case['id']}/monitors",
        json={
            "name": "Too often",
            "saved_query_id": web["id"],
            "schedule": {"kind": "interval", "every_minutes": 5},
            "scope": {"max_pages": 1, "max_items_per_page": 5},
        },
        headers=browser_headers(authed),
    )
    assert too_often.status_code == 422
    live = create_monitor(
        client,
        authed,
        case["id"],
        web["id"],
        name="Live page",
        scope={"max_pages": 1, "max_items_per_page": 5},
        enable=True,
        acknowledge_recurring_collection=True,
    )
    assert live["status"] == "enabled"
    assert live["collects_live"] is True
    with db_session_factory() as db:
        created = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "monitor.created", AuditEvent.target_id == live["id"]
            )
        )
        assert created is not None
        assert created.details["collects_live"] is True


def test_concurrent_schedulers_dispatch_each_slot_exactly_once(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Concurrent schedulers")
    query = saved_query(client, authed, case["id"])
    monitor = create_monitor(client, authed, case["id"], query["id"], enable=True)
    now = later(61)
    results: list[Any] = []
    barrier = threading.Barrier(6)

    def scheduler(index: int) -> None:
        barrier.wait()
        results.append(run_scheduler(settings, db_session_factory, now=now, instance=f"s{index}"))

    threads = [threading.Thread(target=scheduler, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(stats.dispatched for stats in results) == 1
    assert sum(stats.errors for stats in results) == 0
    occurrences = _occurrences(db_session_factory, monitor["id"])
    assert len(occurrences) == 1
    assert occurrences[0].status == "dispatched"
    runs = monitor_runs(db_session_factory, monitor["id"])
    assert len(runs) == 1
    with db_session_factory() as db:
        outbox = db.scalar(
            select(func.count())
            .select_from(DispatchOutbox)
            .where(
                DispatchOutbox.aggregate_type == AggregateType.QUERY_RUN,
                DispatchOutbox.aggregate_id == runs[0].id,
            )
        )
        assert outbox == 1
        stored = db.get(Monitor, uuid.UUID(monitor["id"]))
        assert stored is not None
        assert stored.next_run_at is not None
        assert stored.next_run_at > now
    # A scheduler that runs again for the same instant finds nothing due.
    assert run_scheduler(settings, db_session_factory, now=now).dispatched == 0
    # Redelivering the run's message twice produces one execution.
    assert execute(settings, db_session_factory, runs[0].id).status == "completed"
    assert execute(settings, db_session_factory, runs[0].id).status == "skipped"


def test_after_downtime_one_slot_runs_and_missed_slots_are_counted(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Scheduler restart")
    query = saved_query(client, authed, case["id"])
    catch_up = create_monitor(
        client, authed, case["id"], query["id"], name="Run latest", enable=True
    )
    skipping = create_monitor(
        client,
        authed,
        case["id"],
        query["id"],
        name="Skip missed",
        enable=True,
        missed_run_policy="skip",
    )
    now = utcnow()
    # The scheduler was down for five hours: five hourly slots passed.
    for monitor in (catch_up, skipping):
        set_next_run(db_session_factory, monitor["id"], now - timedelta(hours=5))
    with db_session_factory() as db:
        db.execute(
            text("UPDATE monitors SET schedule_anchor = :anchor"),
            {"anchor": now - timedelta(hours=5)},
        )
        db.commit()

    # The scheduler comes back 30 minutes after the latest slot: that slot is late too.
    restart = now + timedelta(minutes=30)
    stats = run_scheduler(settings, db_session_factory, now=restart)
    assert stats.dispatched == 1
    assert stats.skipped == 1

    ran = _occurrences(db_session_factory, catch_up["id"])
    assert [(o.status, o.missed_slots) for o in ran] == [("dispatched", 5)]
    assert len(monitor_runs(db_session_factory, catch_up["id"])) == 1
    skipped = _occurrences(db_session_factory, skipping["id"])
    assert [(o.status, o.skip_reason, o.missed_slots) for o in skipped] == [
        ("skipped", "missed", 5)
    ]
    assert monitor_runs(db_session_factory, skipping["id"]) == []
    with db_session_factory() as db:
        for monitor in (catch_up, skipping):
            stored = db.get(Monitor, uuid.UUID(monitor["id"]))
            assert stored is not None
            assert stored.next_run_at is not None
            assert stored.next_run_at > restart
    # A second pass does not burst through the missed slots.
    assert (
        run_scheduler(settings, db_session_factory, now=now + timedelta(seconds=40)).dispatched == 0
    )


def test_pause_keeps_the_active_run_disable_cancels_it_and_overlap_is_skipped(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Overlap")
    query = saved_query(client, authed, case["id"])
    monitor = create_monitor(client, authed, case["id"], query["id"], enable=True)
    base = f"/api/v1/cases/{case['id']}/monitors/{monitor['id']}"
    first = later(61)
    assert run_scheduler(settings, db_session_factory, now=first).dispatched == 1
    # The first run is still queued when the next slot arrives.
    assert run_scheduler(settings, db_session_factory, now=first + timedelta(hours=1)).skipped == 1
    reasons = [(o.status, o.skip_reason) for o in _occurrences(db_session_factory, monitor["id"])]
    assert reasons == [("dispatched", None), ("skipped", "overlap")]
    manual = client.post(f"{base}/runs", headers=browser_headers(authed))
    assert manual.status_code == 409
    assert manual.json()["detail"]["code"] == "overlap"

    paused = client.post(f"{base}/pause", headers=browser_headers(authed))
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["active_run_id"] is not None
    run_id = paused.json()["active_run_id"]
    with db_session_factory() as db:
        assert db.get(QueryRun, uuid.UUID(run_id)).status == "queued"  # type: ignore[union-attr]

    disabled = client.post(f"{base}/disable", headers=browser_headers(authed))
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["active_run_id"] is None
    with db_session_factory() as db:
        assert db.get(QueryRun, uuid.UUID(run_id)).status == "canceled"  # type: ignore[union-attr]
    assert client.post(f"{base}/runs", headers=browser_headers(authed)).status_code == 409
    deleted = client.delete(base, headers=browser_headers(authed))
    assert deleted.status_code == 204
    with db_session_factory() as db:
        # The execution history stays; it no longer points at the deleted monitor.
        assert db.get(QueryRun, uuid.UUID(run_id)) is not None


def test_query_edits_pause_monitors_and_never_change_queued_snapshots(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Query edits")
    query = saved_query(client, authed, case["id"], value="ornek")
    monitor = create_monitor(client, authed, case["id"], query["id"], enable=True)
    assert run_scheduler(settings, db_session_factory, now=later(61)).dispatched == 1
    queued = monitor_runs(db_session_factory, monitor["id"])[0]
    assert queued.parameters_snapshot["input_value"] == "ornek"
    assert queued.parameters_snapshot["monitor"]["config_version"] == 1

    edited = client.patch(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}",
        json={"input_value": "baska-hedef"},
        headers=browser_headers(authed),
    )
    assert edited.status_code == 200
    assert edited.json()["monitors_paused"] == 1
    detail = client.get(f"/api/v1/cases/{case['id']}/monitors/{monitor['id']}").json()
    assert detail["status"] == "paused"
    assert detail["status_reason"] == "query_changed"
    assert detail["query_changed"] is True
    with db_session_factory() as db:
        assert db.get(QueryRun, queued.id).parameters_snapshot["input_value"] == "ornek"  # type: ignore[union-attr]
    # The queued run still executes with its own snapshot.
    assert execute(settings, db_session_factory, queued.id).status == "completed"

    refused = _enable(client, authed, case["id"], monitor["id"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "query_changed"
    adopted = _enable(client, authed, case["id"], monitor["id"], adopt_query_changes=True)
    assert adopted.status_code == 200
    assert adopted.json()["config_version"] == 2
    assert adopted.json()["query_changed"] is False
    assert run_scheduler(settings, db_session_factory, now=later(125)).dispatched == 1
    newest = monitor_runs(db_session_factory, monitor["id"])[-1]
    assert newest.parameters_snapshot["input_value"] == "baska-hedef"
    assert newest.parameters_snapshot["monitor"]["config_version"] == 2
    delete_query = client.delete(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}", headers=browser_headers(authed)
    )
    assert delete_query.status_code == 409


def test_dispatch_rechecks_authorization_case_state_and_budget(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Dispatch checks")
        add_member(analyst, csrf, case["id"], SECOND_ANALYST[0], "analyst")
        add_member(analyst, csrf, case["id"], VIEWER[0], "viewer")
        query = saved_query(analyst, csrf, case["id"])
        monitor = create_monitor(analyst, csrf, case["id"], query["id"], enable=True)
        budgeted = create_monitor(
            analyst,
            csrf,
            case["id"],
            query["id"],
            name="Budgeted",
            enable=True,
            budget={"period": "day", "max_requests": 1},
        )
    with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
        removed = second.delete(
            f"/api/v1/cases/{case['id']}/members/{team['analyst']['id']}",
            headers=browser_headers(csrf),
        )
        assert removed.status_code == 204

    # The ledger of the budgeted monitor is already full for today.
    with db_session_factory() as db:
        db.execute(
            text(
                "INSERT INTO budget_ledgers (id, case_id, scope_type, scope_id, metric, period, "
                "period_start, period_end, limit_units, consumed_units) VALUES (gen_random_uuid(), "
                ":case, 'monitor', :monitor, 'requests', 'day', date_trunc('day', now() AT TIME "
                "ZONE 'UTC') AT TIME ZONE 'UTC', date_trunc('day', now() AT TIME ZONE 'UTC') AT "
                "TIME ZONE 'UTC' + interval '1 day', 1, 1)"
            ),
            {"case": case["id"], "monitor": budgeted["id"]},
        )
        db.commit()

    stats = run_scheduler(settings, db_session_factory, now=later(61))
    assert stats.dispatched == 0
    assert stats.skipped == 2
    lost = _occurrences(db_session_factory, monitor["id"])
    assert [(o.status, o.skip_reason) for o in lost] == [("skipped", "authorization_lost")]
    exhausted = _occurrences(db_session_factory, budgeted["id"])
    assert [(o.status, o.skip_reason) for o in exhausted] == [("skipped", "authorization_lost")]

    with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
        detail = second.get(f"/api/v1/cases/{case['id']}/monitors/{monitor['id']}").json()
        assert detail["status"] == "paused"
        assert detail["status_reason"] == "authorization_lost"
        resumed = _enable(second, csrf, case["id"], budgeted["id"])
        assert resumed.status_code == 200
        assert resumed.json()["authorized_by"] == SECOND_ANALYST[0]
        budget = next(u for u in resumed.json()["budget_usage"] if u["scope_type"] == "monitor")
        assert budget["exhausted"] is True
        assert any(a["code"] == "budget_exhausted" for a in resumed.json()["actions"])
    assert run_scheduler(settings, db_session_factory, now=later(125)).skipped == 1
    reasons = [o.skip_reason for o in _occurrences(db_session_factory, budgeted["id"])]
    assert reasons == ["authorization_lost", "budget_exhausted"]
    assert monitor_runs(db_session_factory, budgeted["id"]) == []

    with db_session_factory() as db:
        titles = {
            (row.user_id, row.title)
            for row in db.scalars(
                select(Notification).where(Notification.case_id == uuid.UUID(case["id"]))
            )
        }
    second_id = uuid.UUID(team["second"]["id"])
    viewer_id = uuid.UUID(team["viewer"]["id"])
    assert (second_id, "Monitor 'Synthetic monitor' paused") in titles
    assert (second_id, "Monitor 'Budgeted' skipped: budget used up") in titles
    # Recipients default to the case's analysts; the removed analyst gets nothing.
    assert all(user != viewer_id for user, _ in titles)
    assert all(user != uuid.UUID(team["analyst"]["id"]) for user, _ in titles)

    with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
        archived = second.post(f"/api/v1/cases/{case['id']}/archive", headers=browser_headers(csrf))
        assert archived.status_code == 200
        detail = second.get(f"/api/v1/cases/{case['id']}/monitors/{budgeted['id']}").json()
        assert detail["status"] == "paused"
        assert detail["status_reason"] == "case_archived"


def test_viewers_read_monitors_but_cannot_manage_or_run_them(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Viewer monitors")
        add_member(analyst, csrf, case["id"], VIEWER[0], "viewer")
        query = saved_query(analyst, csrf, case["id"])
        monitor = create_monitor(analyst, csrf, case["id"], query["id"], enable=True)
    base = f"/api/v1/cases/{case['id']}/monitors"
    with signed_in(settings, *VIEWER) as (viewer, csrf):
        assert viewer.get(base).json()["total"] == 1
        assert viewer.get(f"{base}/{monitor['id']}").status_code == 200
        assert viewer.get(f"{base}/{monitor['id']}/occurrences").status_code == 200
        assert viewer.get(f"/api/v1/cases/{case['id']}/budgets").status_code == 200
        assert viewer.get(f"/api/v1/cases/{case['id']}/change-sets").status_code == 200
        attempts: list[tuple[str, str, Any]] = [
            (
                "POST",
                base,
                {
                    "name": "x",
                    "saved_query_id": query["id"],
                    "schedule": {"kind": "daily", "time": "09:00"},
                },
            ),
            ("PATCH", f"{base}/{monitor['id']}", {"name": "renamed"}),
            ("POST", f"{base}/{monitor['id']}/pause", None),
            ("POST", f"{base}/{monitor['id']}/resume", {}),
            ("POST", f"{base}/{monitor['id']}/disable", None),
            ("POST", f"{base}/{monitor['id']}/runs", None),
            ("DELETE", f"{base}/{monitor['id']}", None),
            ("PUT", f"/api/v1/cases/{case['id']}/budgets", {"budgets": []}),
        ]
        for method, path, body in attempts:
            response = viewer.request(method, path, json=body, headers=browser_headers(csrf))
            assert response.status_code == 403, (method, path)
    with db_session_factory() as db:
        assert db.get(Monitor, uuid.UUID(monitor["id"])).status == "enabled"  # type: ignore[union-attr]


def test_operator_pause_stops_every_enabled_monitor_before_scheduling(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    """``python -m app.cli pause-monitors`` (run by scripts/restore.sh before the stack starts)."""
    from app.audit.service import service_actor
    from app.monitoring.service import pause_all_monitors

    first = create_case(client, authed, title="Restored monitors A")
    second = create_case(client, authed, title="Restored monitors B")
    enabled = [
        create_monitor(
            client,
            authed,
            case["id"],
            saved_query(client, authed, case["id"])["id"],
            enable=True,
        )
        for case in (first, second)
    ]
    paused = create_monitor(
        client, authed, first["id"], saved_query(client, authed, first["id"])["id"]
    )
    with db_session_factory() as db:
        assert pause_all_monitors(db, service_actor("operator-cli"), "restore") == 2
        db.commit()
    assert run_scheduler(settings, db_session_factory, now=later(600)).dispatched == 0
    for monitor in enabled:
        case_id = first["id"] if monitor["case_id"] == first["id"] else second["id"]
        body = client.get(f"/api/v1/cases/{case_id}/monitors/{monitor['id']}").json()
        assert (body["status"], body["status_reason"], body["next_run_at"]) == (
            "paused",
            "restore",
            None,
        )
    assert (
        client.get(f"/api/v1/cases/{first['id']}/monitors/{paused['id']}").json()["status_reason"]
        == "created"
    )
    with db_session_factory() as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "monitor.paused", AuditEvent.actor_label == "operator-cli"
            )
        ).all()
        assert {str(event.case_id) for event in events} == {first["id"], second["id"]}
