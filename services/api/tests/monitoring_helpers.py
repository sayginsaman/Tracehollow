"""Helpers for monitor, budget, change detection and notification tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.changes.detection import detect
from app.config import Settings
from app.db.base import utcnow
from app.evidence.storage import EvidenceStorage
from app.monitoring.models import Monitor
from app.monitoring.scheduler import SchedulerStats, schedule_due
from app.queries.execution import ExecutionContext, ExecutionResult, execute_run
from app.queries.models import QueryRun
from tests.collection_helpers import Router, public_resolver
from tests.conftest import browser_headers

FEED = "https://ornek.example/monitor-feed"


def saved_query(
    client: TestClient,
    csrf: str,
    case_id: object,
    *,
    connector: str = "synthetic.fixture",
    input_type: str = "username",
    value: str = "ornek",
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/saved-queries",
        json={
            "name": f"{connector} query",
            "input_type": input_type,
            "input_value": value,
            "connector_ids": [connector],
            **extra,
        },
        headers=browser_headers(csrf),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def feed_query(client: TestClient, csrf: str, case_id: object, **extra: Any) -> dict[str, Any]:
    return saved_query(
        client,
        csrf,
        case_id,
        connector="rss.feed",
        input_type="url",
        value=FEED,
        limits={"max_pages": 3, "max_items_per_page": 50},
        **extra,
    )


def create_monitor(
    client: TestClient, csrf: str, case_id: object, query_id: object, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Synthetic monitor",
        "saved_query_id": str(query_id),
        "schedule": {"kind": "interval", "every_minutes": 60},
        "timezone": "Europe/Istanbul",
        "scope": {"max_pages": 3, "max_items_per_page": 5},
        "limits": {"max_requests_per_run": 50, "max_items_per_run": 500, "max_run_seconds": 600},
        "budget": {"period": "day", "max_requests": 500},
        **overrides,
    }
    response = client.post(
        f"/api/v1/cases/{case_id}/monitors", json=body, headers=browser_headers(csrf)
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def later(minutes: int = 61) -> datetime:
    return utcnow() + timedelta(minutes=minutes)


def run_scheduler(
    settings: Settings, factory: sessionmaker[Session], *, now: datetime, instance: str = "test"
) -> SchedulerStats:
    return schedule_due(factory, settings, instance=instance, now=now)


def execution_context(
    settings: Settings, factory: sessionmaker[Session], router: Router | None = None, **hooks: Any
) -> ExecutionContext:
    return ExecutionContext(
        session_factory=factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="test-worker",
        sleep=lambda _seconds: None,
        http_transport=router.transport if router is not None else None,
        resolver=public_resolver,
        **hooks,
    )


def execute(
    settings: Settings,
    factory: sessionmaker[Session],
    run_id: object,
    router: Router | None = None,
    **hooks: Any,
) -> ExecutionResult:
    return execute_run(
        execution_context(settings, factory, router, **hooks), uuid.UUID(str(run_id))
    )


def detect_changes(settings: Settings, factory: sessionmaker[Session], run_id: object) -> str:
    return detect(factory, settings, uuid.UUID(str(run_id)))


def monitor_runs(factory: sessionmaker[Session], monitor_id: object) -> list[QueryRun]:
    with factory() as db:
        return list(
            db.scalars(
                select(QueryRun)
                .where(QueryRun.monitor_id == uuid.UUID(str(monitor_id)))
                .order_by(QueryRun.queued_at)
            )
        )


def set_next_run(factory: sessionmaker[Session], monitor_id: object, when: datetime) -> None:
    with factory() as db:
        monitor = db.get(Monitor, uuid.UUID(str(monitor_id)))
        assert monitor is not None
        monitor.next_run_at = when
        db.commit()


def rss(entries: list[tuple[str, str]], next_url: str | None = None) -> str:
    link = f'<atom:link rel="next" href="{next_url}"/>' if next_url else ""
    items = "".join(
        f"<item><guid>{guid}</guid><title>{title}</title></item>" for guid, title in entries
    )
    return (
        '<?xml version="1.0"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        f"<channel><title>Örnek izleme</title>{link}{items}</channel></rss>"
    )
