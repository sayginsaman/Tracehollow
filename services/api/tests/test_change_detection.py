"""Evidence-backed change detection between repeated collections (Phase 5 acceptance 1)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.changes.models import ChangeEvent, ChangeSet
from app.config import Settings
from app.entities.models import Observation
from app.notifications.models import Notification
from tests.collection_helpers import Router, respond
from tests.conftest import browser_headers, create_case
from tests.monitoring_helpers import (
    FEED,
    create_monitor,
    detect_changes,
    execute,
    feed_query,
    later,
    monitor_runs,
    rss,
    run_scheduler,
    saved_query,
)

pytestmark = pytest.mark.integration

RSS = "application/rss+xml"


def _feed(
    router: Router,
    first: list[tuple[str, str]],
    second: list[tuple[str, str]] | None,
    *,
    second_status: int = 200,
) -> None:
    router.routes.clear()
    router.add(
        FEED,
        respond(
            200, body=rss(first, f"{FEED}?p=2" if second is not None else None), content_type=RSS
        ),
    )
    if second is not None:
        router.add(f"{FEED}?p=2", respond(second_status, body=rss(second), content_type=RSS))


def _monitor_run(
    client: TestClient,
    csrf: str,
    settings: Settings,
    factory: sessionmaker[Session],
    case_id: object,
    monitor_id: object,
    router: Router,
    minutes: int,
) -> dict[str, Any]:
    assert run_scheduler(settings, factory, now=later(minutes)).dispatched == 1
    run = monitor_runs(factory, monitor_id)[-1]
    execute(settings, factory, run.id, router)
    assert detect_changes(settings, factory, run.id) == "completed"
    change_sets = client.get(
        f"/api/v1/cases/{case_id}/change-sets", params={"query_run_id": str(run.id)}
    ).json()
    assert change_sets["total"] == 1
    detail: dict[str, Any] = client.get(
        f"/api/v1/cases/{case_id}/change-sets/{change_sets['items'][0]['id']}"
    ).json()
    return detail


def _events(
    detail: dict[str, Any],
) -> set[tuple[str, str | None, str | None, str | None, str | None]]:
    return {
        (e["kind"], e["source_object_id"], e["field"], e["previous_value"], e["current_value"])
        for e in detail["events"]["items"]
    }


def test_repeated_collection_reports_a_real_change_and_no_deletion_after_a_partial_run(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Controlled change (synthetic)")
    query = feed_query(client, authed, case["id"])
    monitor = create_monitor(
        client,
        authed,
        case["id"],
        query["id"],
        enable=True,
        acknowledge_recurring_collection=True,
        scope={"max_pages": 3, "max_items_per_page": 50},
    )
    router = Router()

    _feed(
        router,
        [("haber-1", "İzmir şubesi"), ("haber-2", "Ankara toplantısı")],
        [("haber-3", "Kuruluş")],
    )
    baseline = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 61
    )
    assert baseline["status"] == "baseline_established"
    assert baseline["coverage_complete"] is True
    assert baseline["events"]["total"] == 0

    # Controlled change: one title edited, one entry gone, one entry added.
    _feed(
        router,
        [("haber-1", "İzmir şubesi açıldı"), ("haber-4", "Yeni duyuru")],
        [("haber-3", "Kuruluş")],
    )
    changed = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 125
    )
    assert changed["status"] == "changes_detected"
    assert changed["counts"] == {
        "new": 1,
        "changed": 1,
        "not_observed": 1,
        "conflicting": 0,
        "unknown": 0,
    }
    assert _events(changed) == {
        ("new", "haber-4", None, None, None),
        ("changed", "haber-1", "title", "İzmir şubesi", "İzmir şubesi açıldı"),
        ("not_observed", "haber-2", None, None, None),
    }
    removed = next(e for e in changed["events"]["items"] if e["kind"] == "not_observed")
    assert "not proof of deletion" in removed["note"]
    assert removed["previous_evidence_available"] is True
    assert removed["previous_observation_id"] is not None
    edit = next(e for e in changed["events"]["items"] if e["kind"] == "changed")
    assert edit["previous_evidence_id"] != edit["current_evidence_id"]
    assert edit["current_evidence_available"] is True
    assert changed["baseline_query_run_id"] == str(
        monitor_runs(db_session_factory, monitor["id"])[0].id
    )

    # Partial collection: the second page fails, so haber-3 is unknown, never "not observed".
    _feed(
        router,
        [("haber-1", "İzmir şubesi açıldı"), ("haber-4", "Yeni duyuru")],
        [],
        second_status=503,
    )
    partial = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 189
    )
    assert partial["coverage_complete"] is False
    assert partial["status"] == "unknown"
    assert partial["counts"]["not_observed"] == 0
    assert _events(partial) == {("unknown", "haber-3", None, None, None)}
    assert any("incomplete" in limitation for limitation in partial["limitations"])

    # Rate limited on the first page: nothing collected, still no deletion claim.
    router.routes.clear()
    router.add(FEED, respond(429, headers={"retry-after": "7200"}))
    limited = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 253
    )
    assert limited["status"] == "unknown"
    assert limited["counts"]["not_observed"] == 0
    with db_session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ChangeEvent)
                .where(ChangeEvent.kind == "not_observed")
            )
            == 1
        )

    # The next complete collection is compared with the last complete one, not with the partial
    # or rate-limited runs in between, so nothing that already existed is reported as new.
    _feed(
        router,
        [("haber-1", "İzmir şubesi açıldı"), ("haber-4", "Yeni duyuru")],
        [("haber-3", "Kuruluş")],
    )
    recovered = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 317
    )
    assert recovered["status"] == "no_meaningful_change"
    assert recovered["baseline_query_run_id"] == changed["query_run_id"]
    assert recovered["events"]["total"] == 0

    notifications = client.get("/api/v1/notifications").json()
    titles = [item["title"] for item in notifications["items"]]
    assert titles.count("Monitor 'Synthetic monitor': changes detected") == 1
    assert "1 new, 1 changed, 1 no longer observed" in next(
        item["body"] for item in notifications["items"] if item["event_type"] == "change_detected"
    )
    # A retried detection job reuses its change sets and notifies nobody twice.
    last = monitor_runs(db_session_factory, monitor["id"])[1]
    assert detect_changes(settings, db_session_factory, last.id) == "completed"
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ChangeSet)) == 5
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.event_type == "change_detected")
            )
            == 1
        )


def test_items_missing_from_an_incomplete_baseline_are_unknown_not_new(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Incomplete baseline (synthetic)")
    query = feed_query(client, authed, case["id"])
    monitor = create_monitor(
        client,
        authed,
        case["id"],
        query["id"],
        enable=True,
        acknowledge_recurring_collection=True,
        scope={"max_pages": 3, "max_items_per_page": 50},
    )
    router = Router()
    # The first collection ever is partial: its second page fails.
    _feed(router, [("haber-1", "İzmir şubesi")], [], second_status=503)
    first = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 61
    )
    assert first["status"] == "baseline_established"
    assert first["coverage_complete"] is False

    _feed(router, [("haber-1", "İzmir şubesi")], [("haber-2", "Ankara toplantısı")])
    second = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 125
    )
    assert second["baseline_coverage_complete"] is False
    assert second["status"] == "unknown"
    assert second["counts"]["new"] == 0
    assert _events(second) == {("unknown", "haber-2", None, None, None)}
    assert any("incomplete one" in limitation for limitation in second["limitations"])
    notifications = client.get("/api/v1/notifications").json()["items"]
    assert not [item for item in notifications if item["event_type"] == "change_detected"]

    # Once a complete collection exists it becomes the baseline and real additions are new.
    _feed(
        router,
        [("haber-1", "İzmir şubesi"), ("haber-3", "Yeni duyuru")],
        [("haber-2", "Ankara toplantısı")],
    )
    third = _monitor_run(
        client, authed, settings, db_session_factory, case["id"], monitor["id"], router, 189
    )
    assert third["baseline_query_run_id"] == second["query_run_id"]
    assert _events(third) == {("new", "haber-3", None, None, None)}


def test_incompatible_baselines_volatile_fields_and_conflicting_values(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Comparison limits")
    page = "https://ornek.example/duyuru"
    web = saved_query(
        client,
        authed,
        case["id"],
        connector="public_web.page",
        input_type="url",
        value=page,
        limits={"max_pages": 1, "max_items_per_page": 5},
    )
    router = Router()
    router.add(
        page, respond(200, body="<html><head><title>Duyuru</title></head><body>kısa</body></html>")
    )

    def run_query(query_id: object) -> dict[str, Any]:
        started = client.post(
            f"/api/v1/cases/{case['id']}/saved-queries/{query_id}/runs",
            headers=browser_headers(authed),
        ).json()
        execute(settings, db_session_factory, started["id"], router)
        detect_changes(settings, db_session_factory, started["id"])
        sets = client.get(
            f"/api/v1/cases/{case['id']}/change-sets", params={"query_run_id": started["id"]}
        ).json()
        result: dict[str, Any] = sets["items"][0]
        return result

    assert run_query(web["id"])["status"] == "baseline_established"
    # Same title, longer body: byte and text counts differ but nothing meaningful changed.
    router.routes.clear()
    router.add(
        page,
        respond(
            200,
            body="<html><head><title>Duyuru</title></head><body>" + "uzun " * 50 + "</body></html>",
        ),
    )
    assert run_query(web["id"])["status"] == "no_meaningful_change"

    # A wider scope is not comparable with the earlier collection.
    edited = client.patch(
        f"/api/v1/cases/{case['id']}/saved-queries/{web['id']}",
        json={"limits": {"max_pages": 1, "max_items_per_page": 3}},
        headers=browser_headers(authed),
    )
    assert edited.status_code == 200
    incompatible = run_query(web["id"])
    assert incompatible["status"] == "baseline_incompatible"
    assert "max_items_per_page" in incompatible["limitations"][0]
    assert incompatible["counts"] == {
        "new": 0,
        "changed": 0,
        "not_observed": 0,
        "conflicting": 0,
        "unknown": 0,
    }

    # One collection returned the same item twice with different values.
    router.routes.clear()
    router.add(
        page, respond(200, body="<html><head><title>Duyuru</title></head><body>x</body></html>")
    )
    started = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries/{web['id']}/runs",
        headers=browser_headers(authed),
    ).json()
    execute(settings, db_session_factory, started["id"], router)
    with db_session_factory() as db:
        original = db.scalar(
            select(Observation).where(Observation.query_run_id == uuid.UUID(started["id"]))
        )
        assert original is not None
        db.execute(
            text(
                "INSERT INTO observations (id, case_id, entity_id, evidence_id, query_run_id, "
                "connector_run_id, observation_type, source_object_id, payload, collected_at, "
                "idempotency_key) SELECT gen_random_uuid(), case_id, entity_id, evidence_id, "
                "query_run_id, connector_run_id, observation_type, source_object_id, "
                "jsonb_set(payload, '{title}', '\"Duyuru (ikinci)\"'), "
                "collected_at + interval '1 second', idempotency_key || '-duplicate' "
                "FROM observations WHERE id = :id"
            ),
            {"id": original.id},
        )
        db.commit()
    detect_changes(settings, db_session_factory, started["id"])
    conflict_set = client.get(
        f"/api/v1/cases/{case['id']}/change-sets", params={"query_run_id": started["id"]}
    ).json()["items"][0]
    detail = client.get(f"/api/v1/cases/{case['id']}/change-sets/{conflict_set['id']}").json()
    assert detail["status"] == "changes_detected"
    conflicts = [e for e in detail["events"]["items"] if e["kind"] == "conflicting"]
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "title"
    assert conflicts[0]["current_value"] == "Duyuru; Duyuru (ikinci)"
    # Comparison never merged or edited entities.
    with db_session_factory() as db:
        assert (
            db.scalar(text("SELECT count(*) FROM relationships WHERE origin = 'ai_suggestion'"))
            == 0
        )


def test_change_sets_are_case_scoped(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Scoped A")
    other = create_case(client, authed, title="Scoped B")
    query = saved_query(client, authed, case["id"])
    started = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}/runs",
        headers=browser_headers(authed),
    ).json()
    execute(settings, db_session_factory, started["id"])
    detect_changes(settings, db_session_factory, started["id"])
    change_set = client.get(f"/api/v1/cases/{case['id']}/change-sets").json()["items"][0]
    assert (
        client.get(f"/api/v1/cases/{other['id']}/change-sets/{change_set['id']}").status_code == 404
    )
    assert client.get(f"/api/v1/cases/{other['id']}/change-sets").json()["total"] == 0
