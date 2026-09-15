from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.main import create_app
from app.system.models import WorkerCheck, WorkerCheckStatus
from app.tasks.celery_app import WORKER_CHECK_TASK
from tests.conftest import (
    ServiceEndpoints,
    TemporaryDatabase,
    browser_headers,
    complete_setup,
    login,
    make_settings,
)
from tests.test_health import _closed_port

pytestmark = pytest.mark.integration


@pytest.fixture
def running_worker(client: TestClient) -> Iterator[None]:
    """A real Celery worker consuming from the test Redis broker in a background thread."""
    celery_app = client.app.state.celery  # type: ignore[attr-defined]
    celery_app.loader.import_default_modules()
    with start_worker(
        celery_app,
        pool="solo",
        concurrency=1,
        perform_ping_check=False,
        queues=["tracehollow"],
        loglevel="WARNING",
        shutdown_timeout=10,
    ):
        yield


def _authenticated(client: TestClient, settings: Settings) -> str:
    complete_setup(client, settings)
    return login(client)


def _wait_for_completion(client: TestClient, check_id: str, timeout: float = 20) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body: dict[str, str] = client.get(f"/api/v1/system/worker-checks/{check_id}").json()
        if body["status"] == WorkerCheckStatus.COMPLETED:
            return body
        time.sleep(0.2)
    raise AssertionError(f"worker check {check_id} did not complete within {timeout}s")


def test_worker_check_round_trip_through_broker(
    client: TestClient, settings: Settings, running_worker: None
) -> None:
    csrf = _authenticated(client, settings)
    created = client.post("/api/v1/system/worker-checks", headers=browser_headers(csrf))
    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    assert created.json()["dispatched_at"] is not None

    completed = _wait_for_completion(client, created.json()["id"])
    assert completed["worker_hostname"]
    assert completed["completed_at"] is not None

    listed = client.get("/api/v1/system/worker-checks").json()
    assert [item["id"] for item in listed] == [created.json()["id"]]


def test_worker_status_reports_online_worker(
    client: TestClient, settings: Settings, running_worker: None
) -> None:
    _authenticated(client, settings)
    body = client.get("/api/v1/system/worker").json()
    assert body["status"] == "online"
    assert len(body["workers"]) == 1


def test_worker_status_reports_offline_without_worker(
    client: TestClient, settings: Settings
) -> None:
    _authenticated(client, settings)
    body = client.get("/api/v1/system/worker").json()
    assert body["status"] == "offline"
    assert body["workers"] == []


def test_queued_check_stays_queued_without_a_worker(client: TestClient, settings: Settings) -> None:
    csrf = _authenticated(client, settings)
    created = client.post("/api/v1/system/worker-checks", headers=browser_headers(csrf)).json()
    time.sleep(1)
    body = client.get(f"/api/v1/system/worker-checks/{created['id']}").json()
    assert body["status"] == "queued"
    assert body["completed_at"] is None


def test_dispatch_failure_is_recorded_when_broker_is_down(
    services: ServiceEndpoints,
    migrated_database: TemporaryDatabase,
    tmp_path: Path,
) -> None:
    settings = make_settings(services, migrated_database.name, tmp_path, redis_port=_closed_port())
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        csrf = _authenticated(client, settings)
        response = client.post("/api/v1/system/worker-checks", headers=browser_headers(csrf))
        assert response.status_code == 503
        assert response.json()["status"] == "dispatch_failed"
        assert response.json()["error_code"] == "broker_unavailable"

        worker = client.get("/api/v1/system/worker").json()
        assert worker["status"] == "broker_unavailable"


def test_duplicate_delivery_is_idempotent(
    client: TestClient, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    with db_session_factory() as db:
        check = WorkerCheck(status=WorkerCheckStatus.QUEUED)
        db.add(check)
        db.commit()
        check_id = str(check.id)

    celery_app = client.app.state.celery  # type: ignore[attr-defined]
    celery_app.loader.import_default_modules()  # registers the app's included task modules
    task = celery_app.tasks[WORKER_CHECK_TASK]
    task.apply(args=[check_id]).get()
    with db_session_factory() as db:
        first = db.get(WorkerCheck, uuid.UUID(check_id))
        assert first is not None
        first_completed_at = first.completed_at

    task.apply(args=[check_id]).get()
    with db_session_factory() as db:
        second = db.get(WorkerCheck, uuid.UUID(check_id))
        assert second is not None
        assert second.status == WorkerCheckStatus.COMPLETED
        assert second.completed_at == first_completed_at
