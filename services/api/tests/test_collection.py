"""Public-source collection through the execution engine, API and database (Phase 2)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import EvidenceIndexState
from app.config import Settings
from app.connectors import limits
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType, DispatchOutbox
from app.entities.models import Entity, Observation, Relationship
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.integrations.models import IntegrationCredential
from app.queries.execution import ExecutionContext, execute_run
from app.queries.models import ConnectorRun
from tests.collection_helpers import Router, public_resolver, respond
from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    browser_headers,
    create_case,
    create_second_user,
    login_as,
)

pytestmark = pytest.mark.integration

API = "https://api.github.com"


def _query(
    client: TestClient,
    csrf: str,
    case_id: object,
    connector: str,
    input_type: str,
    value: str,
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


def _run(
    client: TestClient,
    csrf: str,
    settings: Settings,
    factory: sessionmaker[Session],
    case_id: object,
    query_id: object,
    router: Router,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/saved-queries/{query_id}/runs", headers=browser_headers(csrf)
    )
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["id"])
    context = ExecutionContext(
        session_factory=factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="collector-test",
        sleep=lambda _seconds: None,
        http_transport=router.transport,
        resolver=public_resolver,
    )
    execute_run(context, run_id)
    detail: dict[str, Any] = client.get(f"/api/v1/cases/{case_id}/runs/{run_id}").json()
    return detail


PAGE = (
    "<html><head><title>Örnek A.Ş. duyurular</title></head><body><p>İzmir şubesi açıldı.</p>"
    "<script>alert(1)</script></body></html>"
)


def test_web_page_collection_stores_provenance_derived_text_and_routes_to_the_collector(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "public_web.page", "url", "https://ornek.example/")
    router = Router()
    router.add("https://ornek.example/", respond(302, headers={"location": "/duyuru"}))
    router.add("https://ornek.example/duyuru", respond(200, body=PAGE))
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)

    assert run["status"] == "completed"
    assert run["synthetic"] is False
    connector = run["connector_runs"][0]
    assert (connector["outcome"], connector["items_collected"]) == ("findings", 1)
    with db_session_factory() as db:
        outbox = db.scalar(
            select(DispatchOutbox).where(
                DispatchOutbox.aggregate_type == AggregateType.QUERY_RUN,
                DispatchOutbox.aggregate_id == uuid.UUID(run["id"]),
            )
        )
        assert outbox is not None
        assert outbox.task_name == dispatch.EXECUTE_COLLECTION_RUN_TASK
        assert dispatch.queue_for(outbox.task_name) == "tracehollow-collect"
        evidence = {
            row.page_part: row
            for row in db.scalars(
                select(EvidenceObject).where(EvidenceObject.query_run_id == uuid.UUID(run["id"]))
            )
        }
        snapshot, text = evidence["snapshot"], evidence["text"]
        assert snapshot.acquisition_method == "connector_collection"
        assert snapshot.collection_mode == "direct_request"
        assert snapshot.access_category == "public"
        assert snapshot.kind == "html"
        assert snapshot.source_reference == "https://ornek.example/duyuru"
        assert snapshot.collection_metadata["redirects"][0]["from"] == "https://ornek.example/"
        assert text.derived_from_evidence_id == snapshot.id
        indexed = set(db.scalars(select(EvidenceIndexState.evidence_id)))
        assert text.id in indexed
        assert snapshot.id not in indexed
        observation = db.scalar(select(Observation).where(Observation.evidence_id == text.id))
        assert observation is not None
        assert observation.observation_type == "web_page"
        assert db.scalar(select(func.count()).select_from(Relationship)) == 1

    detail = client.get(f"/api/v1/cases/{case['id']}/evidence/{snapshot.id}").json()
    assert detail["evidence"]["collection_mode"] == "direct_request"
    assert detail["derived_evidence"] == [str(text.id)]
    preview = client.get(f"/api/v1/cases/{case['id']}/evidence/{snapshot.id}/preview").json()
    assert "<script>alert(1)</script>" in preview["text"]
    text_preview = client.get(f"/api/v1/cases/{case['id']}/evidence/{text.id}/preview").json()
    assert "alert(1)" not in text_preview["text"]
    assert "İzmir şubesi açıldı." in text_preview["text"]


RSS_1 = """<?xml version="1.0"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>Haberler</title><atom:link rel="next" href="https://ornek.example/feed?p=2"/>
<item><guid>a</guid><title>A</title></item><item><guid>b</guid><title>B</title></item>
</channel></rss>"""
RSS_2 = """<?xml version="1.0"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>Haberler</title><atom:link rel="next" href="https://ornek.example/feed?p=3"/>
<item><guid>b</guid><title>B</title></item><item><guid>c</guid><title>C</title></item>
</channel></rss>"""


def test_feed_pagination_deduplicates_and_a_failing_later_page_is_partial(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _query(
        client,
        authed,
        case["id"],
        "rss.feed",
        "url",
        "https://ornek.example/feed",
        limits={"max_pages": 5, "max_items_per_page": 50},
    )
    router = Router()
    router.add(
        "https://ornek.example/feed", respond(200, body=RSS_1, content_type="application/rss+xml")
    )
    router.add(
        "https://ornek.example/feed?p=2",
        respond(200, body=RSS_2, content_type="application/rss+xml"),
    )
    router.add("https://ornek.example/feed?p=3", respond(503))
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)

    connector = run["connector_runs"][0]
    assert run["status"] == "partial"
    assert connector["outcome"] == "partial"
    assert connector["pages_completed"] == 2
    assert connector["items_collected"] == 3
    assert connector["retries"] == 2
    assert connector["coverage"]["duplicate_observations_skipped"] == 1
    assert "collected before the failure" in connector["coverage_note"]
    assert router.urls().count("https://ornek.example/feed?p=3") == 3
    with db_session_factory() as db:
        entries = db.scalars(
            select(Observation.source_object_id).where(Observation.observation_type == "feed_entry")
        ).all()
    assert sorted(str(entry) for entry in entries) == ["a", "b", "c"]


def test_rate_limit_longer_than_allowed_wait_stops_with_quota_and_retry_information(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "github.account", "username", "ornek-dev")
    router = Router().add(
        f"{API}/users/ornek-dev",
        respond(
            403,
            json_body={"message": "API rate limit exceeded"},
            headers={
                "x-ratelimit-limit": "60",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "9999999999",
                "retry-after": "3600",
            },
        ),
    )
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
    connector = run["connector_runs"][0]
    assert (run["status"], connector["outcome"]) == ("failed", "rate_limited")
    assert connector["retry_after_seconds"] == 3600
    assert connector["last_error_code"] == "github_rate_limited"
    assert connector["quota_usage"]["remaining"] == 0
    assert "longer than allowed" in connector["coverage_note"]
    assert len(router.requests) == 1


def test_credentials_are_write_only_encrypted_admin_managed_and_used_by_runs(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    token = "ghp_" + "t" * 36
    url = "/api/v1/connectors/github.account/credentials/token"
    response = client.post(url, json={"value": token}, headers=browser_headers(authed))
    assert response.status_code == 200, response.text
    assert token not in response.text
    status = response.json()["credentials"][0]
    assert (status["configured"], status["usable"]) == (True, True)
    with db_session_factory() as db:
        stored = db.scalar(select(IntegrationCredential))
        assert stored is not None
        assert token.encode() not in stored.ciphertext
    listing = client.get("/api/v1/connectors").text
    assert token not in listing
    assert (
        client.post(
            "/api/v1/connectors/github.account/credentials/unknown",
            json={"value": "x"},
            headers=browser_headers(authed),
        ).status_code
        == 404
    )

    case = create_case(client, authed)
    query = _query(
        client,
        authed,
        case["id"],
        "github.account",
        "username",
        "ornek-dev",
        parameters={"include_repositories": False},
    )
    router = Router().add(
        f"{API}/users/ornek-dev",
        respond(
            200,
            json_body={"login": "ornek-dev", "id": 7, "html_url": "https://github.com/ornek-dev"},
        ),
    )
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
    assert run["connector_runs"][0]["outcome"] == "findings"
    assert router.requests[0].headers["authorization"] == f"Bearer {token}"
    with db_session_factory() as db:
        evidence = db.scalar(
            select(EvidenceObject).where(EvidenceObject.case_id == uuid.UUID(case["id"]))
        )
        assert evidence is not None
        assert evidence.access_category == "credentialed"
        assert token not in json.dumps(evidence.collection_metadata)
        credential = db.scalar(select(IntegrationCredential))
        assert credential is not None
        assert credential.last_result == "accepted"
        entity = db.scalar(select(Entity).where(Entity.case_id == uuid.UUID(case["id"])))
        assert entity is not None
        assert entity.entity_type == "platform_account"

    create_second_user(db_session_factory)
    other = TestClient(client.app, base_url=str(client.base_url))
    other_csrf = login_as(other, SECOND_USERNAME, SECOND_PASSWORD)
    assert (
        other.post(url, json={"value": "x" * 10}, headers=browser_headers(other_csrf)).status_code
        == 403
    )
    assert other.delete(url, headers=browser_headers(other_csrf)).status_code == 403
    assert client.delete(url, headers=browser_headers(authed)).status_code == 200
    assert client.delete(url, headers=browser_headers(authed)).status_code == 404


def test_credentials_under_another_key_are_reported_and_not_used(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    from pydantic import SecretStr

    from app.integrations import crypto

    previous_key = settings.model_copy(update={"credential_encryption_key": SecretStr("ab" * 32)})
    sealed = crypto.seal(previous_key, "github.account", "token", "ghp_old")
    with db_session_factory() as db:
        db.add(
            IntegrationCredential(
                connector_id="github.account",
                name="token",
                ciphertext=sealed.ciphertext,
                nonce=sealed.nonce,
                key_id=sealed.key_id,
            )
        )
        db.commit()
    status = client.get("/api/v1/connectors").json()
    github = next(c for c in status if c["connector_id"] == "github.account")
    assert github["credentials"][0]["configured"] is True
    assert github["credentials"][0]["usable"] is False

    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "github.account", "username", "ornek-dev")
    router = Router()
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
    connector = run["connector_runs"][0]
    assert (connector["outcome"], connector["last_error_code"]) == (
        "authentication_required",
        "credential_unreadable",
    )
    assert router.requests == []


def test_concurrency_slots_and_request_pacing_are_shared_and_recoverable(
    settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert limits.try_claim_slot(db_session_factory, "c", 2, first, 60) == 0
    assert limits.try_claim_slot(db_session_factory, "c", 2, second, 60) == 1
    assert limits.try_claim_slot(db_session_factory, "c", 2, third, 60) is None
    # The same holder (a takeover of the same connector run) gets its slot back.
    assert limits.try_claim_slot(db_session_factory, "c", 2, first, 60) == 0
    limits.release_slot(db_session_factory, "c", second)
    assert limits.try_claim_slot(db_session_factory, "c", 2, third, 60) == 1
    # An expired lease is reclaimable.
    assert limits.try_claim_slot(db_session_factory, "d", 1, first, 0) == 0
    assert limits.try_claim_slot(db_session_factory, "d", 1, second, 60) == 0

    waits = [
        limits.reserve_request(db_session_factory, "host:ornek.example", 2.0) for _ in range(3)
    ]
    assert waits[0] == 0
    assert 1.5 < waits[1] <= 2.0
    assert 3.5 < waits[2] <= 4.0
    assert limits.reserve_request(db_session_factory, "host:other.example", 2.0) == 0


def test_missing_engine_and_blocked_destination_are_actionable_outcomes(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "domain.subfinder", "domain", "ornek.example")
    run = _run(
        client,
        authed,
        settings.model_copy(update={"subfinder_path": settings.evidence_storage_path / "missing"}),
        db_session_factory,
        case["id"],
        query["id"],
        Router(),
    )
    connector = run["connector_runs"][0]
    assert (connector["outcome"], connector["last_error_code"]) == (
        "unavailable",
        "engine_not_installed",
    )

    blocked = _query(
        client,
        authed,
        case["id"],
        "public_web.page",
        "url",
        "http://169.254.169.254/latest/meta-data/",
    )
    router = Router()
    run = _run(client, authed, settings, db_session_factory, case["id"], blocked["id"], router)
    connector = run["connector_runs"][0]
    assert (connector["outcome"], connector["last_error_code"]) == (
        "unsupported",
        "blocked_address",
    )
    assert router.requests == []
    with db_session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConnectorRun)
                .where(
                    ConnectorRun.case_id == uuid.UUID(case["id"]),
                    ConnectorRun.outcome == "no_findings",
                )
            )
            == 0
        )


def test_collection_modes_cannot_be_mixed_and_sources_screen_shows_health(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    response = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries",
        json={
            "name": "mixed",
            "input_type": "username",
            "input_value": "ornekdev",
            "connector_ids": ["github.account", "username.sherlock"],
        },
        headers=browser_headers(authed),
    )
    assert response.status_code == 422
    assert "collection mode" in response.text

    query = _query(client, authed, case["id"], "public_web.page", "url", "https://ornek.example/")
    _run(
        client,
        authed,
        settings,
        db_session_factory,
        case["id"],
        query["id"],
        Router().add("https://ornek.example/", respond(404)),
    )
    sources = {c["connector_id"]: c for c in client.get("/api/v1/connectors").json()}
    web = sources["public_web.page"]
    assert web["collection_mode"] == "direct_request"
    assert web["health"]["last_outcome"] == "no_findings"
    assert web["health"]["recent_outcomes"] == {"no_findings": 1}

    create_second_user(db_session_factory)
    other = TestClient(client.app, base_url=str(client.base_url))
    login_as(other, SECOND_USERNAME, SECOND_PASSWORD)
    other_view = {c["connector_id"]: c for c in other.get("/api/v1/connectors").json()}
    assert other_view["public_web.page"]["health"]["last_outcome"] is None


def test_identifiers_that_cannot_be_normalized_do_not_fail_the_page(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    """A single-label host (for example an allowlisted intranet name) is not a valid domain."""
    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "public_web.page", "url", "http://intranet/")
    router = Router().add("http://intranet/", respond(200, body=PAGE))
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
    connector = run["connector_runs"][0]
    assert (run["status"], connector["outcome"]) == ("completed", "findings")
    assert "could not be normalized" in connector["coverage_note"]
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Entity)) == 0
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
