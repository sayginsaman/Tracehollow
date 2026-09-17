"""In-app notifications and the optional webhook adapter (Phase 5 acceptance 6)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditEvent
from app.config import Settings
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.notifications.delivery import DeliveryContext, deliver
from app.notifications.models import NotificationDelivery
from tests.collection_helpers import Router, public_resolver, resolver_map, respond
from tests.conftest import (
    ServiceEndpoints,
    TemporaryDatabase,
    browser_headers,
    complete_setup,
    create_case,
    login,
    make_settings,
)
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
)
from tests.test_team_access import ANALYST, VIEWER, _team, add_member, signed_in

pytestmark = pytest.mark.integration

RSS = "application/rss+xml"
HOOK = "https://hooks.ornek.example/tracehollow"
MONITOR_NAME = "Gizli hedef izlemesi"


@pytest.fixture
def external_settings(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> Settings:
    return make_settings(
        services, migrated_database.name, tmp_path, notifications_external_enabled=True
    )


@pytest.fixture
def external(
    external_settings: Settings,
) -> Iterator[tuple[TestClient, str, sessionmaker[Session]]]:
    engine = create_db_engine(external_settings)
    factory = create_session_factory(engine)
    with TestClient(create_app(external_settings), base_url="http://localhost") as client:
        complete_setup(client, external_settings)
        yield client, login(client), factory
    engine.dispose()


def _changing_monitor(
    client: TestClient,
    csrf: str,
    settings: Settings,
    factory: sessionmaker[Session],
    router: Router,
    **monitor: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = create_case(client, csrf, title="Özel vaka başlığı (synthetic)")
    query = feed_query(client, csrf, case["id"])
    created = create_monitor(
        client,
        csrf,
        case["id"],
        query["id"],
        name=MONITOR_NAME,
        enable=True,
        acknowledge_recurring_collection=True,
        scope={"max_pages": 3, "max_items_per_page": 50},
        **monitor,
    )
    router.add(FEED, respond(200, body=rss([("gizli-1", "Gizli kanıt değeri")]), content_type=RSS))
    _collect(settings, factory, created["id"], router, 61)
    return case, created


def _collect(
    settings: Settings,
    factory: sessionmaker[Session],
    monitor_id: object,
    router: Router,
    minutes: int,
) -> uuid.UUID:
    assert run_scheduler(settings, factory, now=later(minutes)).dispatched == 1
    run = monitor_runs(factory, monitor_id)[-1]
    execute(settings, factory, run.id, router)
    assert detect_changes(settings, factory, run.id) == "completed"
    return run.id


def test_in_app_notifications_follow_membership_dedupe_and_read_state(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    router = Router()
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case, monitor = _changing_monitor(analyst, csrf, settings, db_session_factory, router)
        add_member(analyst, csrf, case["id"], VIEWER[0], "viewer")
        # Unchanged successful collections notify nobody.
        _collect(settings, db_session_factory, monitor["id"], router, 125)
        assert analyst.get("/api/v1/notifications/unread-count").json() == {"unread": 0}

        router.routes.clear()
        router.add(FEED, respond(200, body=rss([("gizli-2", "Başka değer")]), content_type=RSS))
        changed_run = _collect(settings, db_session_factory, monitor["id"], router, 189)
        listing = analyst.get("/api/v1/notifications").json()
        assert listing["total"] == 1
        note = listing["items"][0]
        assert note["event_type"] == "change_detected"
        assert note["case_title"] == case["title"]
        assert note["link"] == f"/cases/{case['id']}/monitors/{monitor['id']}?run={changed_run}"
        assert note["body"] == "1 new, 1 no longer observed."
        # Values collected from the source are not copied into notifications.
        assert "Gizli kanıt" not in json.dumps(listing)
        assert "Başka değer" not in json.dumps(listing)
        # A redelivered detection job does not notify again.
        detect_changes(settings, db_session_factory, changed_run)
        assert analyst.get("/api/v1/notifications/unread-count").json() == {"unread": 1}

        read = analyst.post(
            f"/api/v1/notifications/{note['id']}/read", headers=browser_headers(csrf)
        )
        assert read.json()["read_at"] is not None
        assert analyst.get("/api/v1/notifications", params={"unread": True}).json()["total"] == 0
        unread = analyst.post(
            f"/api/v1/notifications/{note['id']}/unread", headers=browser_headers(csrf)
        )
        assert unread.json()["read_at"] is None
        assert analyst.post(
            "/api/v1/notifications/read-all", headers=browser_headers(csrf)
        ).json() == {"unread": 0}

    with signed_in(settings, *VIEWER) as (viewer, _):
        # Recipients default to analysts.
        assert viewer.get("/api/v1/notifications").json()["total"] == 0
        assert viewer.get(f"/api/v1/notifications/{note['id']}").status_code == 404

    with signed_in(settings, *ANALYST) as (analyst, csrf):
        analyst.post(
            f"/api/v1/cases/{case['id']}/members",
            json={"username": "second.analyst", "role": "analyst"},
            headers=browser_headers(csrf),
        )
        left = analyst.delete(
            f"/api/v1/cases/{case['id']}/members/{team['analyst']['id']}",
            headers=browser_headers(csrf),
        )
        assert left.status_code == 204
        # Notifications about a case the account can no longer open are hidden.
        assert analyst.get("/api/v1/notifications").json()["total"] == 0
        assert analyst.get(f"/api/v1/notifications/{note['id']}").status_code == 404


def test_webhooks_are_off_by_default_and_nothing_is_queued(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    assert settings.notifications_external_enabled is False
    created = client.post(
        "/api/v1/admin/notification-destinations",
        json={"name": "Receiver", "url": HOOK, "event_types": ["change_detected"]},
        headers=browser_headers(authed),
    )
    assert created.status_code == 409
    assert created.json()["detail"]["code"] == "external_notifications_disabled"
    router = Router()
    case, monitor = _changing_monitor(client, authed, settings, db_session_factory, router)
    router.routes.clear()
    router.add(FEED, respond(200, body=rss([("yeni", "Yeni")]), content_type=RSS))
    _collect(settings, db_session_factory, monitor["id"], router, 125)
    assert client.get(f"/api/v1/cases/{case['id']}/notification-destinations").json() == []
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(NotificationDelivery)) == 0


def _delivery_context(
    settings: Settings, factory: sessionmaker[Session], receiver: Router, **kwargs: Any
) -> DeliveryContext:
    return DeliveryContext(
        session_factory=factory,
        settings=settings,
        worker_name="collector-test",
        sleep=lambda _seconds: None,
        transport=receiver.transport,
        resolver=kwargs.pop("resolver", public_resolver),
    )


def _deliveries(factory: sessionmaker[Session]) -> list[NotificationDelivery]:
    with factory() as db:
        return list(
            db.scalars(select(NotificationDelivery).order_by(NotificationDelivery.created_at))
        )


def test_webhook_sends_only_configured_redacted_events_to_the_selected_destination(
    external: tuple[TestClient, str, sessionmaker[Session]], external_settings: Settings
) -> None:
    client, csrf, factory = external
    settings = external_settings
    blocked = client.post(
        "/api/v1/admin/notification-destinations",
        json={
            "name": "Metadata",
            "url": "http://169.254.169.254/hook",
            "event_types": ["change_detected"],
        },
        headers=browser_headers(csrf),
    )
    assert blocked.status_code == 422
    with_token = client.post(
        "/api/v1/admin/notification-destinations",
        json={"name": "Token", "url": f"{HOOK}?token=abc", "event_types": ["change_detected"]},
        headers=browser_headers(csrf),
    )
    assert with_token.status_code == 422

    created = client.post(
        "/api/v1/admin/notification-destinations",
        json={
            "name": "SOC receiver",
            "url": HOOK,
            "event_types": ["change_detected", "action_required"],
        },
        headers=browser_headers(csrf),
    )
    assert created.status_code == 201, created.text
    destination = created.json()
    assert destination["enabled"] is False
    secret = destination["signing_secret"]
    assert secret
    assert "signing_secret" not in client.get("/api/v1/admin/notification-destinations").json()[
        0
    ] or (
        client.get("/api/v1/admin/notification-destinations").json()[0].get("signing_secret")
        is None
    )

    router = Router()
    case, monitor = _changing_monitor(client, csrf, settings, factory, router)
    base = f"/api/v1/cases/{case['id']}/monitors/{monitor['id']}/subscriptions"
    # Disabled destinations cannot be selected.
    assert client.get(f"/api/v1/cases/{case['id']}/notification-destinations").json() == []
    refused = client.post(
        base,
        json={"destination_id": destination["id"], "event_types": ["change_detected"]},
        headers=browser_headers(csrf),
    )
    assert refused.status_code == 404

    preview = client.post(
        f"/api/v1/admin/notification-destinations/{destination['id']}/preview",
        headers=browser_headers(csrf),
    ).json()
    assert set(preview["body"]) == {
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
    unconfirmed = client.post(
        f"/api/v1/admin/notification-destinations/{destination['id']}/enable",
        json={"confirm_host": "other.example"},
        headers=browser_headers(csrf),
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["detail"]["code"] == "confirmation_mismatch"
    enabled = client.post(
        f"/api/v1/admin/notification-destinations/{destination['id']}/enable",
        json={"confirm_host": destination["host"]},
        headers=browser_headers(csrf),
    )
    assert enabled.json()["enabled"] is True
    available = client.get(f"/api/v1/cases/{case['id']}/notification-destinations").json()
    assert available == [
        {
            "id": destination["id"],
            "name": "SOC receiver",
            "host": "hooks.ornek.example",
            "event_types": ["action_required", "change_detected"],
        }
    ]
    too_many = client.post(
        base,
        json={"destination_id": destination["id"], "event_types": ["run_completed"]},
        headers=browser_headers(csrf),
    )
    assert too_many.status_code == 422
    subscribed = client.post(
        base,
        json={"destination_id": destination["id"], "event_types": ["change_detected"]},
        headers=browser_headers(csrf),
    )
    assert subscribed.status_code == 201
    subscription = subscribed.json()[0]
    sample = client.post(
        f"{base}/{subscription['id']}/preview", headers=browser_headers(csrf)
    ).json()
    assert MONITOR_NAME not in json.dumps(sample)
    assert sample["body"]["monitor_id"] == monitor["id"]

    router.routes.clear()
    router.add(FEED, respond(200, body=rss([("gizli-2", "Başka gizli değer")]), content_type=RSS))
    run_id = _collect(settings, factory, monitor["id"], router, 125)
    deliveries = _deliveries(factory)
    assert len(deliveries) == 1
    receiver = Router()
    received: list[httpx2.Request] = []

    def accept(request: httpx2.Request) -> httpx2.Response:
        received.append(request)
        return httpx2.Response(204)

    receiver.add(HOOK, accept)
    context = _delivery_context(settings, factory, receiver)
    assert deliver(context, deliveries[0].id) == "delivered"
    assert deliver(context, deliveries[0].id) == "skipped"  # redelivered task message
    assert len(received) == 1
    request = received[0]
    body = request.content
    payload = json.loads(body)
    assert payload["event_type"] == "change_detected"
    assert payload["case_id"] == case["id"]
    assert payload["query_run_id"] == str(run_id)
    assert payload["summary"] == {
        "new": 1,
        "changed": 0,
        "not_observed": 1,
        "conflicting": 0,
        "unknown": 0,
        "change_sets": 1,
        "run_status": "completed",
    }
    assert payload["link"].startswith("http://localhost:3000/cases/")
    text_body = body.decode()
    for private in (MONITOR_NAME, case["title"], "Gizli kanıt", "Başka gizli", FEED, secret):
        assert private not in text_body
    for value in settings.secret_values():
        assert value not in text_body
    assert request.headers["x-tracehollow-event-id"] == payload["event_id"]
    expected = (
        "sha256="
        + hmac.new(
            secret.encode(),
            request.headers["x-tracehollow-timestamp"].encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
    )
    assert request.headers["x-tracehollow-signature"] == expected
    # A retried detection job queues no second delivery for the same event.
    detect_changes(settings, factory, run_id)
    assert len(_deliveries(factory)) == 1

    # The destination is disabled after the event was queued: nothing is sent.
    router.routes.clear()
    router.add(FEED, respond(200, body=rss([("gizli-3", "Üçüncü")]), content_type=RSS))
    _collect(settings, factory, monitor["id"], router, 189)
    pending = _deliveries(factory)[-1]
    assert pending.status == "pending"
    client.post(
        f"/api/v1/admin/notification-destinations/{destination['id']}/disable",
        headers=browser_headers(csrf),
    )
    assert deliver(context, pending.id) == "blocked"
    assert len(received) == 1
    with factory() as db:
        stored = db.get(NotificationDelivery, pending.id)
        assert stored is not None
        assert (stored.status, stored.last_error_code) == ("blocked", "destination_disabled")
        actions = set(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.target_type == "notification_delivery")
            )
        )
        assert actions == {"notification.delivered", "notification.delivery_blocked"}
    history = client.get(
        f"/api/v1/admin/notification-destinations/{destination['id']}/deliveries"
    ).json()
    assert [item["status"] for item in history["items"]] == ["blocked", "delivered"]


def test_webhook_retries_failures_and_rechecks_the_address_before_sending(
    external: tuple[TestClient, str, sessionmaker[Session]], external_settings: Settings
) -> None:
    client, csrf, factory = external
    settings = external_settings
    destination = client.post(
        "/api/v1/admin/notification-destinations",
        json={"name": "Flaky", "url": HOOK, "event_types": ["change_detected"]},
        headers=browser_headers(csrf),
    ).json()
    client.post(
        f"/api/v1/admin/notification-destinations/{destination['id']}/enable",
        json={"confirm_host": destination["host"]},
        headers=browser_headers(csrf),
    )
    router = Router()
    case, monitor = _changing_monitor(client, csrf, settings, factory, router)
    client.post(
        f"/api/v1/cases/{case['id']}/monitors/{monitor['id']}/subscriptions",
        json={"destination_id": destination["id"], "event_types": ["change_detected"]},
        headers=browser_headers(csrf),
    )
    router.routes.clear()
    router.add(FEED, respond(200, body=rss([("farkli", "Farklı")]), content_type=RSS))
    _collect(settings, factory, monitor["id"], router, 125)
    delivery = _deliveries(factory)[0]

    receiver = Router()
    receiver.add(HOOK, respond(503, headers={"retry-after": "5"}))
    assert deliver(_delivery_context(settings, factory, receiver), delivery.id) == "retrying"
    with factory() as db:
        stored = db.get(NotificationDelivery, delivery.id)
        assert stored is not None
        assert (stored.status, stored.attempts, stored.last_response_status) == ("pending", 1, 503)
        stored.next_attempt_at = stored.created_at
        db.commit()

    # The receiver's host name now resolves to a private address: refused before any request.
    rebinding = _delivery_context(
        settings, factory, receiver, resolver=resolver_map({"hooks.ornek.example": ["10.1.2.3"]})
    )
    assert deliver(rebinding, delivery.id) == "blocked"
    assert len(receiver.requests) == 1
    with factory() as db:
        stored = db.get(NotificationDelivery, delivery.id)
        assert stored is not None
        assert stored.last_error_code == "blocked_address"


def test_only_administrators_manage_destinations_and_viewers_cannot_subscribe(
    external: tuple[TestClient, str, sessionmaker[Session]], external_settings: Settings
) -> None:
    client, csrf, _factory = external
    _team(client, csrf)
    destination = client.post(
        "/api/v1/admin/notification-destinations",
        json={"name": "Receiver", "url": HOOK, "event_types": ["change_detected"]},
        headers=browser_headers(csrf),
    ).json()
    client.post(
        f"/api/v1/admin/notification-destinations/{destination['id']}/enable",
        json={"confirm_host": destination["host"]},
        headers=browser_headers(csrf),
    )
    with signed_in(external_settings, *ANALYST) as (analyst, analyst_csrf):
        assert analyst.get("/api/v1/admin/notification-destinations").status_code == 403
        denied = analyst.post(
            "/api/v1/admin/notification-destinations",
            json={"name": "Mine", "url": HOOK, "event_types": ["change_detected"]},
            headers=browser_headers(analyst_csrf),
        )
        assert denied.status_code == 403
        case = create_case(analyst, analyst_csrf, title="Viewer subscriptions")
        add_member(analyst, analyst_csrf, case["id"], VIEWER[0], "viewer")
        query = feed_query(analyst, analyst_csrf, case["id"])
        monitor = create_monitor(
            analyst,
            analyst_csrf,
            case["id"],
            query["id"],
            scope={"max_pages": 3, "max_items_per_page": 50},
        )
    with signed_in(external_settings, *VIEWER) as (viewer, viewer_csrf):
        assert (
            viewer.get(f"/api/v1/cases/{case['id']}/notification-destinations").status_code == 403
        )
        response = viewer.post(
            f"/api/v1/cases/{case['id']}/monitors/{monitor['id']}/subscriptions",
            json={"destination_id": destination["id"], "event_types": ["change_detected"]},
            headers=browser_headers(viewer_csrf),
        )
        assert response.status_code == 403
