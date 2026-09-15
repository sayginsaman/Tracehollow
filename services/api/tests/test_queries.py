from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import utcnow
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType, DispatchOutbox, OutboxStatus
from app.entities.models import Entity, Observation, Relationship, RelationshipEvidence
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.main import create_app
from app.queries.execution import ExecutionContext, claim_run, execute_run
from app.queries.models import QueryRun
from app.tasks.celery_app import create_celery_app
from tests.conftest import (
    ServiceEndpoints,
    TemporaryDatabase,
    browser_headers,
    complete_setup,
    create_case,
    login,
    make_settings,
)
from tests.test_health import _closed_port

pytestmark = pytest.mark.integration


def _saved_query(
    client: TestClient, csrf: str, case_id: object, scenario: str = "findings", **extra: object
) -> dict[str, Any]:
    body = {
        "name": f"Fixture {scenario}",
        "input_type": "username",
        "input_value": "şule.yılmaz",
        "connector_ids": ["synthetic.fixture"],
        "parameters": {"scenario": scenario},
        **extra,
    }
    response = client.post(
        f"/api/v1/cases/{case_id}/saved-queries", json=body, headers=browser_headers(csrf)
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def _start_run(client: TestClient, csrf: str, case_id: object, query_id: object) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/saved-queries/{query_id}/runs", headers=browser_headers(csrf)
    )
    assert response.status_code == 202, response.text
    result: dict[str, Any] = response.json()
    return result


def _context(
    settings: Settings, factory: sessionmaker[Session], **kwargs: object
) -> ExecutionContext:
    return ExecutionContext(
        session_factory=factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="test-worker",
        sleep=lambda _seconds: None,
        **kwargs,  # type: ignore[arg-type]
    )


def _run_detail(client: TestClient, case_id: object, run_id: object) -> dict[str, Any]:
    detail: dict[str, Any] = client.get(f"/api/v1/cases/{case_id}/runs/{run_id}").json()
    return detail


def test_saved_query_validation(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    for body, message in (
        ({"connector_ids": ["osint.real"]}, "unknown connector"),
        ({"input_type": "phone"}, "does not support input type"),
        ({"parameters": {"scenario": "live"}}, "unknown fixture scenario"),
    ):
        payload = {
            "name": "q",
            "input_type": "username",
            "input_value": "x",
            "connector_ids": ["synthetic.fixture"],
            **body,
        }
        response = client.post(
            f"/api/v1/cases/{case['id']}/saved-queries",
            json=payload,
            headers=browser_headers(authed),
        )
        assert response.status_code == 422
        assert message in str(response.json()["detail"])

    connectors = client.get("/api/v1/connectors").json()
    assert [c["connector_id"] for c in connectors] == ["synthetic.fixture"]
    assert connectors[0]["synthetic"] is True
    assert connectors[0]["last_live_verification"] is None


def test_two_runs_are_independent_and_keep_snapshots(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    first = _start_run(client, authed, case["id"], query["id"])
    assert first["status"] == "queued"
    assert first["dispatch_status"] == "dispatched"
    assert (
        execute_run(_context(settings, db_session_factory), uuid.UUID(str(first["id"]))).status
        == "completed"
    )

    # Editing the saved query affects future runs only.
    client.patch(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}",
        json={"name": "Renamed", "limits": {"max_pages": 2, "max_items_per_page": 5}},
        headers=browser_headers(authed),
    )
    second = _start_run(client, authed, case["id"], query["id"])
    execute_run(_context(settings, db_session_factory), uuid.UUID(str(second["id"])))

    one = _run_detail(client, case["id"], first["id"])
    two = _run_detail(client, case["id"], second["id"])
    assert (one["run_number"], two["run_number"]) == (1, 2)
    assert one["status"] == "completed"
    assert one["connector_runs"][0]["outcome"] == "findings"
    assert two["status"] == "partial"  # stopped at the new 2-page limit while more pages exist
    assert two["connector_runs"][0]["coverage"]["stopped_reason"] == "page_limit"
    assert one["parameters_snapshot"]["saved_query_name"] == "Fixture findings"
    assert one["parameters_snapshot"]["limits"]["max_pages"] == 3
    assert two["parameters_snapshot"]["limits"]["max_pages"] == 2
    assert (one["evidence_count"], one["observation_count"]) == (3, 6)
    assert (two["evidence_count"], two["observation_count"]) == (2, 4)

    first_evidence = client.get(
        f"/api/v1/cases/{case['id']}/evidence", params={"query_run_id": first["id"]}
    ).json()
    second_evidence = client.get(
        f"/api/v1/cases/{case['id']}/evidence", params={"query_run_id": second["id"]}
    ).json()
    assert {e["id"] for e in first_evidence["items"]}.isdisjoint(
        {e["id"] for e in second_evidence["items"]}
    )
    assert all(
        e["synthetic"] and e["acquisition_method"] == "synthetic_fixture"
        for e in first_evidence["items"]
    )

    with db_session_factory() as db:
        # Candidate accounts are matched by stable platform ID, so reruns reuse them.
        accounts = db.scalar(
            select(func.count()).select_from(Entity).where(Entity.entity_type == "platform_account")
        )
        assert accounts == 6
        relationships = db.scalar(select(func.count()).select_from(Relationship))
        assert relationships == 6
        references = db.scalar(select(func.count()).select_from(RelationshipEvidence))
        assert references == 10  # six from run 1, four more from run 2

    history = client.get(
        f"/api/v1/cases/{case['id']}/runs", params={"saved_query_id": query["id"]}
    ).json()
    assert [item["run_number"] for item in history["items"]] == [2, 1]


@pytest.mark.parametrize(
    ("scenario", "status", "outcome", "retries", "evidence"),
    [
        ("findings", "completed", "findings", 0, 3),
        ("no_findings", "completed", "no_findings", 0, 1),
        ("partial", "partial", "partial", 2, 1),
        ("failure", "failed", "unavailable", 2, 0),
        ("flaky", "completed", "findings", 1, 3),
        ("rate_limited", "completed", "findings", 1, 3),
        ("authentication_required", "failed", "authentication_required", 0, 0),
        ("access_denied", "failed", "access_denied", 0, 0),
        ("parse_error", "failed", "parse_error", 0, 0),
    ],
)
def test_fixture_scenarios_produce_truthful_outcomes(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
    scenario: str,
    status: str,
    outcome: str,
    retries: int,
    evidence: int,
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"], scenario)
    run = _start_run(client, authed, case["id"], query["id"])
    execute_run(_context(settings, db_session_factory), uuid.UUID(str(run["id"])))
    detail = _run_detail(client, case["id"], run["id"])
    connector_run = detail["connector_runs"][0]
    assert detail["status"] == status
    assert connector_run["outcome"] == outcome
    assert connector_run["retries"] == retries
    assert detail["evidence_count"] == evidence
    if status != "completed":
        assert connector_run["coverage_note"]
        assert connector_run["last_error_code"]
    if scenario == "rate_limited":
        assert connector_run["retry_after_seconds"] == 1.0


def test_duplicate_delivery_is_a_no_op(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    run = _start_run(client, authed, case["id"], query["id"])
    run_id = uuid.UUID(str(run["id"]))

    results: list[str] = []
    barrier = threading.Barrier(3)

    def deliver() -> None:
        barrier.wait()
        results.append(execute_run(_context(settings, db_session_factory), run_id).status)

    threads = [threading.Thread(target=deliver) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["completed", "skipped", "skipped"]
    # A late redelivery after completion changes nothing either.
    assert execute_run(_context(settings, db_session_factory), run_id).status == "skipped"

    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(EvidenceObject)) == 3
        assert db.scalar(select(func.count()).select_from(Observation)) == 6
    files = [p for p in settings.evidence_storage_path.rglob("*") if p.is_file()]
    assert len(files) == 3


def test_takeover_after_worker_crash_resumes_without_duplicates(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    run = _start_run(client, authed, case["id"], query["id"])
    run_id = uuid.UUID(str(run["id"]))

    class Crash(BaseException):  # simulates the worker process dying, not a handled error
        pass

    def crash_after_first_page(_connector_run_id: uuid.UUID, page_index: int) -> None:
        if page_index == 0:
            raise Crash

    with pytest.raises(Crash):
        execute_run(
            _context(settings, db_session_factory, after_page=crash_after_first_page), run_id
        )

    # The crashed worker still holds a valid lease: a redelivered message must not take over.
    assert execute_run(_context(settings, db_session_factory), run_id).status == "skipped"
    with db_session_factory() as db:
        db.execute(
            update(QueryRun)
            .where(QueryRun.id == run_id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    assert execute_run(_context(settings, db_session_factory), run_id).status == "completed"

    detail = _run_detail(client, case["id"], run_id)
    assert detail["evidence_count"] == 3
    assert detail["observation_count"] == 6
    with db_session_factory() as db:
        assert db.scalar(select(QueryRun.claim_count).where(QueryRun.id == run_id)) == 2


def test_cancellation_preserves_collected_evidence(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"], "slow")
    run = _start_run(client, authed, case["id"], query["id"])

    def cancel_after_second_page(_connector_run_id: uuid.UUID, page_index: int) -> None:
        if page_index == 1:
            response = client.post(
                f"/api/v1/cases/{case['id']}/runs/{run['id']}/cancel",
                headers=browser_headers(authed),
            )
            assert response.status_code == 200

    result = execute_run(
        _context(settings, db_session_factory, after_page=cancel_after_second_page),
        uuid.UUID(str(run["id"])),
    )
    assert result.status == "canceled"
    detail = _run_detail(client, case["id"], run["id"])
    connector_run = detail["connector_runs"][0]
    assert detail["status"] == "canceled"
    assert detail["cancel_requested_at"] is not None
    assert connector_run["status"] == "canceled"
    assert connector_run["outcome"] == "canceled"
    assert connector_run["pages_completed"] == 2
    assert detail["evidence_count"] == 2
    evidence = client.get(
        f"/api/v1/cases/{case['id']}/evidence", params={"query_run_id": run["id"]}
    ).json()
    for item in evidence["items"]:
        content = client.get(f"/api/v1/cases/{case['id']}/evidence/{item['id']}/content")
        assert content.status_code == 200

    again = client.post(
        f"/api/v1/cases/{case['id']}/runs/{run['id']}/cancel", headers=browser_headers(authed)
    )
    assert again.status_code == 409


def test_canceling_a_queued_run_is_immediate_and_final(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    run = _start_run(client, authed, case["id"], query["id"])
    canceled = client.post(
        f"/api/v1/cases/{case['id']}/runs/{run['id']}/cancel", headers=browser_headers(authed)
    ).json()
    assert canceled["status"] == "canceled"
    assert canceled["connector_runs"][0]["outcome"] == "canceled"
    assert (
        execute_run(_context(settings, db_session_factory), uuid.UUID(str(run["id"]))).status
        == "skipped"
    )
    assert _run_detail(client, case["id"], run["id"])["evidence_count"] == 0


def test_outbox_recovers_when_the_broker_was_down_at_request_time(
    services: ServiceEndpoints,
    migrated_database: TemporaryDatabase,
    tmp_path: Path,
    db_session_factory: sessionmaker[Session],
) -> None:
    down = make_settings(services, migrated_database.name, tmp_path, redis_port=_closed_port())
    with TestClient(create_app(down), base_url="http://localhost") as client:
        complete_setup(client, down)
        csrf = login(client)
        case = create_case(client, csrf)
        query = _saved_query(client, csrf, case["id"])
        run = _start_run(client, csrf, case["id"], query["id"])
        assert run["status"] == "queued"
        assert run["dispatch_status"] == "pending"

    with db_session_factory() as db:
        row = db.scalar(
            select(DispatchOutbox).where(DispatchOutbox.aggregate_id == uuid.UUID(str(run["id"])))
        )
        assert row is not None
        assert row.last_error_code == "broker_unavailable"
        db.execute(update(DispatchOutbox).values(available_at=utcnow()))
        db.commit()

    up = make_settings(services, migrated_database.name, tmp_path)
    stats = dispatch.relay_once(db_session_factory, create_celery_app(up), up)
    assert stats.published == 1
    with db_session_factory() as db:
        row = db.scalar(
            select(DispatchOutbox).where(DispatchOutbox.aggregate_id == uuid.UUID(str(run["id"])))
        )
        assert row is not None
        assert row.status == OutboxStatus.DISPATCHED


def test_relay_requeues_lost_messages_and_expired_leases(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    lost = _start_run(client, authed, case["id"], query["id"])
    crashed = _start_run(client, authed, case["id"], query["id"])
    crashed_id = uuid.UUID(str(crashed["id"]))

    celery_app = create_celery_app(settings)
    # Nothing is stale yet.
    assert dispatch.requeue_stale(db_session_factory, settings) == 0

    token = claim_run(_context(settings, db_session_factory), crashed_id)
    assert token is not None
    time.sleep(1.2)  # older than dispatch_redelivery_seconds=1 in tests
    with db_session_factory() as db:
        db.execute(
            update(QueryRun)
            .where(QueryRun.id == crashed_id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()

    assert dispatch.requeue_stale(db_session_factory, settings) == 2
    published, failed = dispatch.publish_due(db_session_factory, celery_app, settings)
    assert (published, failed) == (2, 0)
    with db_session_factory() as db:
        attempts: dict[uuid.UUID, int] = {
            aggregate_id: count
            for aggregate_id, count in db.execute(
                select(DispatchOutbox.aggregate_id, DispatchOutbox.attempts).where(
                    DispatchOutbox.aggregate_type == AggregateType.QUERY_RUN
                )
            )
        }
    assert attempts[uuid.UUID(str(lost["id"]))] == 2
    assert attempts[crashed_id] == 2
    # The expired lease can be taken over and the run completes exactly once.
    assert execute_run(_context(settings, db_session_factory), crashed_id).status == "completed"


@pytest.fixture
def worker(client: TestClient) -> Iterator[None]:
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


def test_run_executes_through_the_real_broker_and_worker(
    client: TestClient, authed: str, worker: None
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    run = _start_run(client, authed, case["id"], query["id"])
    deadline = time.monotonic() + 30
    detail = _run_detail(client, case["id"], run["id"])
    while detail["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.2)
        detail = _run_detail(client, case["id"], run["id"])
    assert detail["status"] == "completed"
    assert detail["dispatch_status"] == "done"
    graph = client.get(f"/api/v1/cases/{case['id']}/graph").json()
    assert {edge["origin"] for edge in graph["edges"]} == {"observed"}
    edge = graph["edges"][0]
    relationship = client.get(f"/api/v1/cases/{case['id']}/relationships/{edge['id']}").json()
    assert relationship["references"][0]["observation_type"] == "candidate_account"
    assert relationship["references"][0]["evidence_acquisition_method"] == "synthetic_fixture"
    assert relationship["review_status"] == "unreviewed"


def test_internal_errors_become_visible_failures_not_stuck_runs(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _saved_query(client, authed, case["id"])
    run = _start_run(client, authed, case["id"], query["id"])

    def bug_after_first_page(_connector_run_id: uuid.UUID, page_index: int) -> None:
        if page_index == 0:
            raise RuntimeError("simulated programming error")

    result = execute_run(
        _context(settings, db_session_factory, after_page=bug_after_first_page),
        uuid.UUID(str(run["id"])),
    )
    assert result.status == "partial"
    detail = _run_detail(client, case["id"], run["id"])
    connector_run = detail["connector_runs"][0]
    assert detail["status"] == "partial"
    assert detail["error_code"] == "internal_error"
    assert connector_run["outcome"] == "partial"
    assert connector_run["last_error_code"] == "internal_error"
    assert connector_run["last_error_detail"] == "RuntimeError"
    assert "simulated" not in str(detail)  # exception messages are not exposed
    assert detail["evidence_count"] == 1
    assert detail["dispatch_status"] == "done"


def test_runs_whose_workers_keep_dying_are_failed_after_bounded_claims(
    services: ServiceEndpoints,
    migrated_database: TemporaryDatabase,
    tmp_path: Path,
    db_session_factory: sessionmaker[Session],
) -> None:
    settings = make_settings(services, migrated_database.name, tmp_path, run_max_claims=2)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        complete_setup(client, settings)
        csrf = login(client)
        case = create_case(client, csrf)
        query = _saved_query(client, csrf, case["id"])
        run = _start_run(client, csrf, case["id"], query["id"])
        run_id = uuid.UUID(str(run["id"]))

        for _ in range(2):
            assert claim_run(_context(settings, db_session_factory), run_id) is not None
            with db_session_factory() as db:
                db.execute(
                    update(QueryRun)
                    .where(QueryRun.id == run_id)
                    .values(lease_expires_at=utcnow() - timedelta(seconds=1))
                )
                db.commit()

        assert execute_run(_context(settings, db_session_factory), run_id).status == "failed"
        detail = _run_detail(client, case["id"], run_id)
        assert detail["status"] == "failed"
        assert detail["error_code"] == "worker_lost"
        assert detail["connector_runs"][0]["last_error_code"] == "worker_lost"
        assert detail["dispatch_status"] == "done"
