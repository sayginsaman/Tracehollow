"""Budgets across concurrent work: reservations, settlement, crash recovery and execution."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.budgets import service as budgets
from app.budgets.models import (
    BudgetLedger,
    BudgetMetric,
    BudgetPeriod,
    BudgetReservation,
    BudgetScope,
    ReservationStatus,
)
from app.config import Settings
from app.db.base import utcnow
from app.evidence.models import EvidenceObject
from app.queries.models import QueryRun
from tests.collection_helpers import Router, respond
from tests.conftest import browser_headers, create_case
from tests.monitoring_helpers import FEED, execute, feed_query, rss

pytestmark = pytest.mark.integration

RSS = "application/rss+xml"


def _requirement(case_id: uuid.UUID, limit: int) -> budgets.Requirement:
    return budgets.Requirement(
        scope_type=BudgetScope.CASE,
        scope_id=case_id,
        metric=BudgetMetric.REQUESTS,
        period=BudgetPeriod.DAY,
        limit_units=limit,
    )


def _ledger(factory: sessionmaker[Session], scope_id: uuid.UUID) -> BudgetLedger:
    with factory() as db:
        ledger = db.scalar(select(BudgetLedger).where(BudgetLedger.scope_id == scope_id))
        assert ledger is not None
        return ledger


def _set_case_budget(client: TestClient, csrf: str, case_id: object, limit: int) -> Any:
    response = client.put(
        f"/api/v1/cases/{case_id}/budgets",
        json={"budgets": [{"metric": "requests", "period": "day", "limit_units": limit}]},
        headers=browser_headers(csrf),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_concurrent_reservations_never_spend_the_same_remaining_units(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Concurrent budget")
    case_id = uuid.UUID(case["id"])
    requirement = _requirement(case_id, 7)
    granted: list[budgets.Ticket] = []
    denied: list[budgets.BudgetExhaustedError] = []
    barrier = threading.Barrier(20)
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            ticket = budgets.acquire(
                db_session_factory,
                [requirement],
                case_id=case_id,
                units={BudgetMetric.REQUESTS: 1},
                hold_seconds=60,
            )
        except budgets.BudgetExhaustedError as exc:
            with lock:
                denied.append(exc)
        else:
            with lock:
                granted.append(ticket)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 7
    assert len(denied) == 13
    ledger = _ledger(db_session_factory, case_id)
    assert (ledger.reserved_units, ledger.consumed_units, ledger.denied_requests) == (7, 0, 13)
    assert ledger.exhausted_at is not None

    for ticket in granted[:5]:
        budgets.settle(db_session_factory, ticket, actual={BudgetMetric.REQUESTS: 1})
    # A second settlement of the same request (a retried worker step) changes nothing.
    budgets.settle(db_session_factory, granted[0], actual={BudgetMetric.REQUESTS: 1})
    budgets.settle(db_session_factory, granted[5], actual=None)
    ledger = _ledger(db_session_factory, case_id)
    assert (ledger.reserved_units, ledger.consumed_units, ledger.estimated_units) == (1, 5, 0)
    # The released unit is available again; the held one is not.
    usage = budgets.current_usage_for(db_session_factory, requirement)
    assert usage.remaining_units == 1


def test_reservations_of_a_crashed_worker_are_counted_as_estimated_use(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Crashed worker")
    case_id = uuid.UUID(case["id"])
    requirement = _requirement(case_id, 3)
    ticket = budgets.acquire(
        db_session_factory,
        [requirement],
        case_id=case_id,
        units={BudgetMetric.REQUESTS: 2},
        hold_seconds=30,
    )
    assert budgets.expire_stale(db_session_factory, now=utcnow()) == 0
    assert budgets.expire_stale(db_session_factory, now=utcnow() + timedelta(seconds=31)) == 1
    ledger = _ledger(db_session_factory, case_id)
    assert (ledger.reserved_units, ledger.consumed_units, ledger.estimated_units) == (0, 0, 2)
    # The worker comes back later and settles: nothing is counted twice.
    budgets.settle(db_session_factory, ticket, actual={BudgetMetric.REQUESTS: 2})
    ledger = _ledger(db_session_factory, case_id)
    assert (ledger.consumed_units, ledger.estimated_units) == (0, 2)
    with db_session_factory() as db:
        reservation = db.scalar(
            select(BudgetReservation).where(BudgetReservation.ticket_id == ticket.id)
        )
        assert reservation is not None
        assert reservation.status == ReservationStatus.EXPIRED
        assert reservation.estimated is True
    with pytest.raises(budgets.BudgetExhaustedError):
        budgets.acquire(
            db_session_factory,
            [requirement],
            case_id=case_id,
            units={BudgetMetric.REQUESTS: 2},
            hold_seconds=30,
        )


def _paged_feed(router: Router, pages: int) -> None:
    for index in range(1, pages + 1):
        url = FEED if index == 1 else f"{FEED}?p={index}"
        next_url = f"{FEED}?p={index + 1}" if index < pages else None
        router.add(
            url,
            respond(
                200, body=rss([(f"entry-{index}", f"Başlık {index}")], next_url), content_type=RSS
            ),
        )


def _start(client: TestClient, csrf: str, case_id: object, query_id: object) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/saved-queries/{query_id}/runs", headers=browser_headers(csrf)
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


def test_concurrent_runs_share_a_case_budget_and_stop_with_partial_results(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Shared case budget")
    usage = _set_case_budget(client, authed, case["id"], 4)
    assert usage["budgets"] == [{"metric": "requests", "period": "day", "limit_units": 4}]
    assert "estimates" in usage["measurement"]
    query = feed_query(client, authed, case["id"])
    runs = [_start(client, authed, case["id"], query["id"]) for _ in range(3)]
    router = Router()
    _paged_feed(router, 3)
    results: dict[str, str] = {}
    barrier = threading.Barrier(3)

    def worker(run: dict[str, Any]) -> None:
        barrier.wait()
        results[run["id"]] = execute(settings, db_session_factory, run["id"], router).status

    threads = [threading.Thread(target=worker, args=(run,)) for run in runs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Three runs of three pages would need nine requests; exactly four were sent.
    assert len(router.requests) == 4
    ledger = _ledger(db_session_factory, uuid.UUID(case["id"]))
    assert (ledger.consumed_units, ledger.reserved_units, ledger.limit_units) == (4, 0, 4)
    details = [client.get(f"/api/v1/cases/{case['id']}/runs/{run['id']}").json() for run in runs]
    connectors = [detail["connector_runs"][0] for detail in details]
    assert sum(c["pages_completed"] for c in connectors) == 4
    assert all(d["error_code"] == "budget_exhausted" for d in details if d["status"] != "completed")
    stopped = [c for c in connectors if c["coverage"].get("stopped_reason") == "budget_exhausted"]
    assert stopped
    for connector in stopped:
        assert connector["last_error_code"] == "budget_exhausted"
        assert "budget of 4 requests" in connector["coverage_note"]
        if connector["pages_completed"]:
            assert connector["status"] == "partial"
            assert connector["outcome"] == "partial"
        else:
            assert connector["status"] == "failed"
            assert connector["outcome"] is None
    with db_session_factory() as db:
        assert (
            db.scalar(select(func.count()).select_from(EvidenceObject)) == 8
        )  # 4 pages x 2 records
        held = db.scalar(
            select(func.count())
            .select_from(BudgetReservation)
            .where(BudgetReservation.status == ReservationStatus.HELD)
        )
        assert held == 0
    budget_view = client.get(f"/api/v1/cases/{case['id']}/budgets").json()
    assert budget_view["usage"][0]["exhausted"] is True
    assert budget_view["usage"][0]["consumed_units"] == 4


def test_retries_pagination_and_refused_destinations_are_counted_correctly(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Retries count")
    _set_case_budget(client, authed, case["id"], 100)
    query = feed_query(
        client,
        authed,
        case["id"],
    )
    router = Router()
    router.add(
        FEED, respond(503), respond(200, body=rss([("a", "A")], f"{FEED}?p=2"), content_type=RSS)
    )
    router.add(f"{FEED}?p=2", respond(200, body=rss([("b", "B")]), content_type=RSS))
    run = _start(client, authed, case["id"], query["id"])
    assert execute(settings, db_session_factory, run["id"], router).status == "completed"
    # One failed attempt, its retry and the second page.
    assert len(router.requests) == 3
    assert _ledger(db_session_factory, uuid.UUID(case["id"])).consumed_units == 3

    blocked = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries",
        json={
            "name": "Metadata page",
            "input_type": "url",
            "input_value": "http://169.254.169.254/latest/",
            "connector_ids": ["public_web.page"],
        },
        headers=browser_headers(authed),
    )
    if blocked.status_code == 201:
        blocked_run = _start(client, authed, case["id"], blocked.json()["id"])
        execute(settings, db_session_factory, blocked_run["id"], router)
    # A destination the network policy refuses never leaves the installation and is not counted.
    assert _ledger(db_session_factory, uuid.UUID(case["id"])).consumed_units == 3


def test_per_run_request_limit_and_cancellation_keep_collected_pages(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Run limit")
    query = feed_query(
        client,
        authed,
        case["id"],
    )
    limited = client.patch(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}",
        json={"limits": {"max_pages": 3, "max_items_per_page": 50, "max_requests": 2}},
        headers=browser_headers(authed),
    )
    assert limited.status_code == 200
    router = Router()
    _paged_feed(router, 3)
    run = _start(client, authed, case["id"], query["id"])
    assert execute(settings, db_session_factory, run["id"], router).status == "partial"
    detail = client.get(f"/api/v1/cases/{case['id']}/runs/{run['id']}").json()
    assert detail["error_code"] == "budget_exhausted"
    assert detail["connector_runs"][0]["pages_completed"] == 2
    assert "execution budget of 2 requests" in detail["connector_runs"][0]["coverage_note"]
    with db_session_factory() as db:
        ledger = db.scalar(
            select(BudgetLedger).where(BudgetLedger.scope_id == uuid.UUID(run["id"]))
        )
        assert ledger is not None
        assert (ledger.scope_type, ledger.period, ledger.consumed_units) == ("query_run", "run", 2)

    # Cancel after the first page: no new request is issued and the page is kept.
    unlimited = feed_query(client, authed, case["id"])
    second = _start(client, authed, case["id"], unlimited["id"])
    before = len(router.requests)

    def cancel_after_first_page(_connector_run: uuid.UUID, page_index: int) -> None:
        if page_index == 0:
            response = client.post(
                f"/api/v1/cases/{case['id']}/runs/{second['id']}/cancel",
                headers=browser_headers(authed),
            )
            assert response.status_code == 200

    result = execute(
        settings, db_session_factory, second["id"], router, after_page=cancel_after_first_page
    )
    assert result.status == "canceled"
    assert len(router.requests) - before == 1
    with db_session_factory() as db:
        assert db.get(QueryRun, uuid.UUID(second["id"])).status == "canceled"  # type: ignore[union-attr]
        assert (
            db.scalar(
                select(func.count())
                .select_from(EvidenceObject)
                .where(EvidenceObject.query_run_id == uuid.UUID(second["id"]))
            )
            == 2
        )
