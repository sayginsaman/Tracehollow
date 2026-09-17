"""Team roles at API level: administrator, analyst and viewer (PRD Phase 5 acceptance 4).

Covers the permission matrix in docs/security/permissions.md, including AI requests, exports,
evidence downloads and background work that outlives a membership change.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import AiRun
from app.audit.models import AuditEvent
from app.config import Settings
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.imports.jobs import ProcessingContext
from app.imports.models import ProcessingJob
from app.imports.runner import execute_job
from app.main import create_app
from app.queries.execution import ExecutionContext, execute_run
from app.queries.models import QueryRun
from tests.ai_helpers import run_ai, run_indexing
from tests.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_ADMIN_USERNAME,
    browser_headers,
    create_case,
    import_file,
    login_as,
)

pytestmark = pytest.mark.integration

ANALYST = ("case.analyst", "analyst passphrase for tests")
SECOND_ANALYST = ("second.analyst", "another analyst passphrase")
VIEWER = ("case.viewer", "viewer passphrase for tests")
REGISTRY = "Kayıt özeti: ornek.example alan adı Örnek A.Ş. tarafından tescil edildi."


@contextmanager
def signed_in(settings: Settings, username: str, password: str) -> Iterator[tuple[TestClient, str]]:
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        yield client, login_as(client, username, password)


def create_account(
    client: TestClient, csrf: str, credentials: tuple[str, str], role: str
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/accounts",
        json={"username": credentials[0], "password": credentials[1], "role": role},
        headers=browser_headers(csrf),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def add_member(
    client: TestClient, csrf: str, case_id: object, username: str, role: str
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/members",
        json={"username": username, "role": role},
        headers=browser_headers(csrf),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def audit_actions(factory: sessionmaker[Session], **conditions: Any) -> list[tuple[str, str]]:
    with factory() as db:
        query = select(AuditEvent.action, AuditEvent.outcome).order_by(AuditEvent.occurred_at)
        for key, value in conditions.items():
            query = query.where(getattr(AuditEvent, key) == value)
        return [(row.action, row.outcome) for row in db.execute(query)]


def _team(client: TestClient, admin_csrf: str) -> dict[str, dict[str, Any]]:
    return {
        "analyst": create_account(client, admin_csrf, ANALYST, "analyst"),
        "second": create_account(client, admin_csrf, SECOND_ANALYST, "analyst"),
        "viewer": create_account(client, admin_csrf, VIEWER, "viewer"),
    }


def _query_run(client: TestClient, csrf: str, case_id: object) -> dict[str, Any]:
    query = client.post(
        f"/api/v1/cases/{case_id}/saved-queries",
        json={
            "name": "Fixture",
            "input_type": "username",
            "input_value": "ornek",
            "connector_ids": ["synthetic.fixture"],
        },
        headers=browser_headers(csrf),
    )
    assert query.status_code == 201, query.text
    run = client.post(
        f"/api/v1/cases/{case_id}/saved-queries/{query.json()['id']}/runs",
        headers=browser_headers(csrf),
    )
    assert run.status_code == 202, run.text
    return {"query": query.json(), "run": run.json()}


def _execution(
    settings: Settings, factory: sessionmaker[Session], **hooks: Any
) -> ExecutionContext:
    return ExecutionContext(
        session_factory=factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="test-worker",
        sleep=lambda _seconds: None,
        **hooks,
    )


# -- administration ----------------------------------------------------------------------------


def test_administrator_manages_accounts_and_keeps_one_active_administrator(
    client: TestClient, authed: str, settings: Settings
) -> None:
    session = client.get("/api/v1/auth/session").json()
    assert session["user"]["role"] == "administrator"
    assert session["user"]["is_admin"] is True
    assert "accounts.manage" in session["permissions"]

    team = _team(client, authed)
    assert team["viewer"]["role"] == "viewer"
    listing = client.get("/api/v1/admin/accounts").json()
    assert {item["username"] for item in listing["items"]} == {
        TEST_ADMIN_USERNAME,
        ANALYST[0],
        SECOND_ANALYST[0],
        VIEWER[0],
    }
    duplicate = client.post(
        "/api/v1/admin/accounts",
        json={
            "username": ANALYST[0].upper(),
            "password": "long enough passphrase",
            "role": "viewer",
        },
        headers=browser_headers(authed),
    )
    assert duplicate.status_code == 409
    weak = client.post(
        "/api/v1/admin/accounts",
        json={"username": "weak.password", "password": "short", "role": "viewer"},
        headers=browser_headers(authed),
    )
    assert weak.status_code == 422

    me = next(item for item in listing["items"] if item["username"] == TEST_ADMIN_USERNAME)
    demote = client.patch(
        f"/api/v1/admin/accounts/{me['id']}",
        json={"role": "analyst"},
        headers=browser_headers(authed),
    )
    assert demote.status_code == 409
    assert demote.json()["detail"]["code"] == "last_administrator"
    deactivate = client.patch(
        f"/api/v1/admin/accounts/{me['id']}",
        json={"is_active": False},
        headers=browser_headers(authed),
    )
    assert deactivate.status_code == 409

    promoted = client.patch(
        f"/api/v1/admin/accounts/{team['second']['id']}",
        json={"role": "administrator"},
        headers=browser_headers(authed),
    )
    assert promoted.status_code == 200
    assert promoted.json()["account"]["role"] == "administrator"
    demoted = client.patch(
        f"/api/v1/admin/accounts/{me['id']}",
        json={"role": "analyst"},
        headers=browser_headers(authed),
    )
    assert demoted.status_code == 200
    # The role change applies to the open session at once.
    assert client.get("/api/v1/admin/accounts").status_code == 403


def test_non_administrators_are_refused_and_the_refusal_is_audited(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    for credentials in (ANALYST, VIEWER):
        with signed_in(settings, *credentials) as (other, csrf):
            assert other.get("/api/v1/admin/accounts").status_code == 403
            assert other.get("/api/v1/admin/cases").status_code == 403
            assert other.get("/api/v1/admin/audit-events").status_code == 403
            refused = other.post(
                "/api/v1/admin/accounts",
                json={
                    "username": "sneaky",
                    "password": "sneaky passphrase",
                    "role": "administrator",
                },
                headers=browser_headers(csrf),
            )
            assert refused.status_code == 403
            assert refused.json()["detail"]["code"] == "insufficient_account_role"
            credential = other.post(
                "/api/v1/connectors/github.account/credentials/token",
                json={"value": "ghp_not_a_real_token_000000000000000000"},
                headers=browser_headers(csrf),
            )
            assert credential.status_code == 403
    with signed_in(settings, *VIEWER) as (viewer, _csrf):
        assert viewer.get("/api/v1/accounts", params={"q": "case"}).status_code == 403
    with signed_in(settings, *ANALYST) as (analyst, _csrf):
        found = analyst.get("/api/v1/accounts", params={"q": "VIEWER"}).json()
        assert [item["username"] for item in found] == [VIEWER[0]]
        assert set(found[0]) == {"id", "username", "role"}

    denied = audit_actions(db_session_factory, actor_user_id=uuid.UUID(team["viewer"]["id"]))
    assert ("access.denied", "denied") in denied
    events = client.get("/api/v1/admin/audit-events", params={"outcome": "denied"}).json()
    assert events["total"] >= 8
    assert all("ghp_not_a_real_token" not in json.dumps(item) for item in events["items"])


def test_deactivation_and_password_changes_revoke_sessions(
    client: TestClient, authed: str, settings: Settings
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        with signed_in(settings, *ANALYST) as (other_device, _):
            wrong = analyst.post(
                "/api/v1/auth/password",
                json={"current_password": "not it", "new_password": "a brand new passphrase"},
                headers=browser_headers(csrf),
            )
            assert wrong.status_code == 403
            changed = analyst.post(
                "/api/v1/auth/password",
                json={"current_password": ANALYST[1], "new_password": "a brand new passphrase"},
                headers=browser_headers(csrf),
            )
            assert changed.status_code == 204
            assert analyst.get("/api/v1/auth/session").status_code == 200
            assert other_device.get("/api/v1/auth/session").status_code == 401

        reset = client.post(
            f"/api/v1/admin/accounts/{team['analyst']['id']}/password",
            json={"password": "administrator chosen passphrase"},
            headers=browser_headers(authed),
        )
        assert reset.status_code == 204
        assert analyst.get("/api/v1/auth/session").status_code == 401

    with signed_in(settings, ANALYST[0], "administrator chosen passphrase") as (analyst, _):
        deactivated = client.patch(
            f"/api/v1/admin/accounts/{team['analyst']['id']}",
            json={"is_active": False},
            headers=browser_headers(authed),
        )
        assert deactivated.status_code == 200
        assert analyst.get("/api/v1/cases").status_code == 401
    login = client.post(
        "/api/v1/auth/login",
        json={"username": ANALYST[0], "password": "administrator chosen passphrase"},
        headers=browser_headers(),
    )
    assert login.status_code == 401


# -- case roles --------------------------------------------------------------------------------


def _analyst_case(
    settings: Settings, factory: sessionmaker[Session], analyst: TestClient, csrf: str
) -> dict[str, Any]:
    case = create_case(analyst, csrf, title="Team case (synthetic)")
    status, imported = import_file(analyst, csrf, case["id"], REGISTRY.encode(), title="Registry")
    assert status == 201
    entity = analyst.post(
        f"/api/v1/cases/{case['id']}/entities",
        json={"entity_type": "domain", "display_name": "ornek.example"},
        headers=browser_headers(csrf),
    ).json()
    run = _query_run(analyst, csrf, case["id"])
    execute_run(_execution(settings, factory), uuid.UUID(run["run"]["id"]))
    run_indexing(settings, factory, case["id"])
    conversation = analyst.post(
        f"/api/v1/cases/{case['id']}/ai/conversations", json={}, headers=browser_headers(csrf)
    ).json()
    asked = analyst.post(
        f"/api/v1/cases/{case['id']}/ai/conversations/{conversation['id']}/questions",
        json={"question": "ornek.example alan adı kim tarafından tescil edildi?"},
        headers=browser_headers(csrf),
    ).json()
    assert run_ai(settings, factory, asked["id"]) == "completed"
    return {
        "case": case,
        "evidence": imported["evidence"],
        "entity": entity,
        "run": run["run"],
        "query": run["query"],
        "conversation": conversation,
        "ai_run": asked,
    }


def test_viewer_reads_everything_but_cannot_change_collect_request_ai_or_export(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, analyst_csrf):
        data = _analyst_case(settings, db_session_factory, analyst, analyst_csrf)
        case_id = data["case"]["id"]
        add_member(analyst, analyst_csrf, case_id, VIEWER[0], "viewer")
        detail = analyst.get(f"/api/v1/cases/{case_id}").json()
        assert detail["my_role"] == "analyst"
        assert "exports.create" in detail["permissions"]

    with signed_in(settings, *VIEWER) as (viewer, csrf):
        detail = viewer.get(f"/api/v1/cases/{case_id}").json()
        assert detail["my_role"] == "viewer"
        assert detail["permissions"] == ["case.read"]
        assert viewer.get("/api/v1/cases").json()["items"][0]["my_role"] == "viewer"

        conversation = viewer.get(
            f"/api/v1/cases/{case_id}/ai/conversations/{data['conversation']['id']}"
        ).json()
        answer = next(m for m in conversation["messages"] if m["role"] == "assistant")
        citation_id = answer["answer"]["claims"][0]["citations"][0]["citation_id"]
        evidence_id = data["evidence"]["id"]
        readable = [
            f"/api/v1/cases/{case_id}",
            f"/api/v1/cases/{case_id}/members",
            f"/api/v1/cases/{case_id}/entities",
            f"/api/v1/cases/{case_id}/entities/{data['entity']['id']}",
            f"/api/v1/cases/{case_id}/relationships",
            f"/api/v1/cases/{case_id}/observations",
            f"/api/v1/cases/{case_id}/graph",
            f"/api/v1/cases/{case_id}/timeline",
            f"/api/v1/cases/{case_id}/notes",
            f"/api/v1/cases/{case_id}/evidence",
            f"/api/v1/cases/{case_id}/evidence/{evidence_id}",
            f"/api/v1/cases/{case_id}/evidence/{evidence_id}/preview",
            f"/api/v1/cases/{case_id}/evidence/{evidence_id}/content",
            f"/api/v1/cases/{case_id}/saved-queries",
            f"/api/v1/cases/{case_id}/runs",
            f"/api/v1/cases/{case_id}/runs/{data['run']['id']}",
            f"/api/v1/cases/{case_id}/processing-jobs",
            f"/api/v1/cases/{case_id}/ai",
            f"/api/v1/cases/{case_id}/ai/conversations",
            f"/api/v1/cases/{case_id}/ai/runs",
            f"/api/v1/cases/{case_id}/ai/runs/{data['ai_run']['id']}",
            f"/api/v1/cases/{case_id}/ai/citations/{citation_id}",
            f"/api/v1/cases/{case_id}/ai/outputs?kind=summary",
            # Keyword search of indexed text; no model is called.
            f"/api/v1/cases/{case_id}/ai/search?q=ornek",
            f"/api/v1/cases/{case_id}/reports/selectable",
        ]
        for path in readable:
            assert viewer.get(path).status_code == 200, path
        download = viewer.get(f"/api/v1/cases/{case_id}/evidence/{evidence_id}/content")
        assert download.content == REGISTRY.encode()

        forbidden_get = [
            f"/api/v1/cases/{case_id}/exports/json",
            f"/api/v1/cases/{case_id}/exports/csv",
            f"/api/v1/cases/{case_id}/audit-events",
        ]
        for path in forbidden_get:
            response = viewer.get(path)
            assert response.status_code == 403, path
            assert response.json()["detail"]["code"] == "insufficient_case_role", path
            assert "ornek" not in response.text

        writes: list[tuple[str, str, Any]] = [
            ("PATCH", f"/api/v1/cases/{case_id}", {"title": "Renamed"}),
            ("POST", f"/api/v1/cases/{case_id}/archive", None),
            ("POST", f"/api/v1/cases/{case_id}/notes", {"body": "viewer note"}),
            (
                "POST",
                f"/api/v1/cases/{case_id}/entities",
                {"entity_type": "domain", "display_name": "x.example"},
            ),
            (
                "POST",
                f"/api/v1/cases/{case_id}/members",
                {"username": VIEWER[0], "role": "analyst"},
            ),
            (
                "POST",
                f"/api/v1/cases/{case_id}/saved-queries",
                {
                    "name": "q",
                    "input_type": "username",
                    "input_value": "x",
                    "connector_ids": ["synthetic.fixture"],
                },
            ),
            ("POST", f"/api/v1/cases/{case_id}/saved-queries/{data['query']['id']}/runs", None),
            ("POST", f"/api/v1/cases/{case_id}/runs/{data['run']['id']}/cancel", None),
            ("POST", f"/api/v1/cases/{case_id}/ai/conversations", {}),
            (
                "POST",
                f"/api/v1/cases/{case_id}/ai/conversations/{data['conversation']['id']}/questions",
                {"question": "Who registered it?"},
            ),
            ("POST", f"/api/v1/cases/{case_id}/ai/summaries", {}),
            ("POST", f"/api/v1/cases/{case_id}/ai/relationship-suggestions", {}),
            ("POST", f"/api/v1/cases/{case_id}/ai/index/rebuild", {"scope": "all"}),
            ("POST", f"/api/v1/cases/{case_id}/ai/index/cancel", None),
            ("POST", f"/api/v1/cases/{case_id}/ai/runs/{data['ai_run']['id']}/cancel", None),
            ("PATCH", f"/api/v1/cases/{case_id}/ai/settings", {"mode": "disabled"}),
            ("POST", f"/api/v1/cases/{case_id}/reports/html/preview", {}),
            ("POST", f"/api/v1/cases/{case_id}/reports/html", {}),
            (
                "POST",
                f"/api/v1/cases/{case_id}/evidence/{evidence_id}/deletion",
                {"confirm_title": "Registry"},
            ),
            ("POST", f"/api/v1/cases/{case_id}/deletion", {"confirm_title": data["case"]["title"]}),
        ]
        for method, path, body in writes:
            response = viewer.request(method, path, json=body, headers=browser_headers(csrf))
            assert response.status_code == 403, (method, path, response.text)
        upload = viewer.post(
            f"/api/v1/cases/{case_id}/evidence/imports",
            files={"file": ("n.txt", b"viewer text", "application/octet-stream")},
            data={"kind": "text", "import_origin": "Synthetic test"},
            headers=browser_headers(csrf),
        )
        assert upload.status_code == 403

    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AiRun)) == 1
        assert db.scalar(select(func.count()).select_from(QueryRun)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(EvidenceObject)
                .where(EvidenceObject.title == "n.txt")
            )
            == 0
        )
    denials = audit_actions(db_session_factory, case_id=uuid.UUID(case_id), outcome="denied")
    assert len(denials) >= len(writes) + len(forbidden_get)


def test_viewer_accounts_are_capped_and_cannot_create_cases(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Capped roles")
        refused = analyst.post(
            f"/api/v1/cases/{case['id']}/members",
            json={"username": VIEWER[0], "role": "analyst"},
            headers=browser_headers(csrf),
        )
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "role_exceeds_account_role"
    with db_session_factory() as db:
        # Even a membership written directly as analyst stays read-only for a viewer account.
        db.execute(
            text(
                "INSERT INTO case_members (case_id, user_id, role) VALUES (:case, :user, 'analyst')"
            ),
            {"case": case["id"], "user": team["viewer"]["id"]},
        )
        db.commit()
    with signed_in(settings, *VIEWER) as (viewer, csrf):
        assert viewer.get(f"/api/v1/cases/{case['id']}").json()["my_role"] == "viewer"
        note = viewer.post(
            f"/api/v1/cases/{case['id']}/notes", json={"body": "x"}, headers=browser_headers(csrf)
        )
        assert note.status_code == 403
        created = viewer.post(
            "/api/v1/cases", json={"title": "Mine"}, headers=browser_headers(csrf)
        )
        assert created.status_code == 403


def test_administrator_restores_case_access_without_reading_the_case(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Only analyst leaves")
        import_file(analyst, csrf, case["id"], b"confidential synthetic content")

    # The administrator is not a member: no case content.
    assert client.get(f"/api/v1/cases/{case['id']}").status_code == 404
    assert client.get(f"/api/v1/cases/{case['id']}/evidence").status_code == 404
    assert client.get("/api/v1/cases").json()["total"] == 0

    changed = client.patch(
        f"/api/v1/admin/accounts/{team['analyst']['id']}",
        json={"is_active": False},
        headers=browser_headers(authed),
    ).json()
    assert changed["cases_without_active_analyst"] == [case["id"]]
    orphaned = client.get("/api/v1/admin/cases", params={"without_active_analyst": True}).json()
    assert [item["id"] for item in orphaned["items"]] == [case["id"]]
    assert set(orphaned["items"][0]) == {
        "id",
        "title",
        "status",
        "created_at",
        "member_count",
        "active_analyst_count",
    }
    added = client.post(
        f"/api/v1/admin/cases/{case['id']}/members",
        json={"username": SECOND_ANALYST[0], "role": "analyst"},
        headers=browser_headers(authed),
    )
    assert added.status_code == 201
    assert "confidential" not in json.dumps(
        client.get(f"/api/v1/admin/cases/{case['id']}/members").json()
    )
    with signed_in(settings, *SECOND_ANALYST) as (second, _):
        evidence = second.get(f"/api/v1/cases/{case['id']}/evidence").json()
        assert evidence["total"] == 1
    actions = audit_actions(db_session_factory, case_id=uuid.UUID(case["id"]))
    assert ("membership.added", "succeeded") in actions


def test_a_case_always_keeps_an_active_analyst(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Guarded membership")
        case_id = case["id"]
        me = team["analyst"]["id"]
        alone = analyst.delete(
            f"/api/v1/cases/{case_id}/members/{me}", headers=browser_headers(csrf)
        )
        assert alone.status_code == 409
        assert alone.json()["detail"]["code"] == "last_analyst"
        downgrade = analyst.patch(
            f"/api/v1/cases/{case_id}/members/{me}",
            json={"role": "viewer"},
            headers=browser_headers(csrf),
        )
        assert downgrade.status_code == 409

        add_member(analyst, csrf, case_id, SECOND_ANALYST[0], "analyst")
        add_member(analyst, csrf, case_id, VIEWER[0], "viewer")
        duplicate = analyst.post(
            f"/api/v1/cases/{case_id}/members",
            json={"username": VIEWER[0], "role": "viewer"},
            headers=browser_headers(csrf),
        )
        assert duplicate.status_code == 409
        members = analyst.get(f"/api/v1/cases/{case_id}/members").json()
        assert {(m["username"], m["effective_role"]) for m in members} == {
            (ANALYST[0], "analyst"),
            (SECOND_ANALYST[0], "analyst"),
            (VIEWER[0], "viewer"),
        }
        left = analyst.delete(
            f"/api/v1/cases/{case_id}/members/{me}", headers=browser_headers(csrf)
        )
        assert left.status_code == 204
        # Removing yourself takes effect at once.
        assert analyst.get(f"/api/v1/cases/{case_id}").status_code == 404

    with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
        events = second.get(f"/api/v1/cases/{case_id}/audit-events").json()
        actions = [item["action"] for item in events["items"]]
        assert actions.count("membership.added") == 2
        assert "membership.removed" in actions
        assert "case.created" in actions
        promoted = second.patch(
            f"/api/v1/cases/{case_id}/members/{team['viewer']['id']}",
            json={"role": "analyst"},
            headers=browser_headers(csrf),
        )
        assert promoted.status_code == 422


def test_role_changes_apply_to_open_sessions_immediately(
    client: TestClient, authed: str, settings: Settings
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, analyst_csrf):
        case = create_case(analyst, analyst_csrf, title="Live role change")
        add_member(analyst, analyst_csrf, case["id"], SECOND_ANALYST[0], "viewer")
        with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
            note = {"body": "Second analyst note"}
            path = f"/api/v1/cases/{case['id']}/notes"
            assert second.post(path, json=note, headers=browser_headers(csrf)).status_code == 403
            promoted = analyst.patch(
                f"/api/v1/cases/{case['id']}/members/{team['second']['id']}",
                json={"role": "analyst"},
                headers=browser_headers(analyst_csrf),
            )
            assert promoted.status_code == 200
            assert second.post(path, json=note, headers=browser_headers(csrf)).status_code == 201
            client.patch(
                f"/api/v1/admin/accounts/{team['second']['id']}",
                json={"role": "viewer"},
                headers=browser_headers(authed),
            )
            assert second.post(path, json=note, headers=browser_headers(csrf)).status_code == 403


# -- background work after a membership change -------------------------------------------------


def test_revoked_membership_stops_queued_and_running_query_runs(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Revocation")
        add_member(analyst, csrf, case["id"], SECOND_ANALYST[0], "analyst")
    with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
        queued = _query_run(second, csrf, case["id"])["run"]
        running = _query_run(second, csrf, case["id"])["run"]

    # Revoked while the run waits in the queue: nothing is collected.
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        response = analyst.patch(
            f"/api/v1/cases/{case['id']}/members/{team['second']['id']}",
            json={"role": "viewer"},
            headers=browser_headers(csrf),
        )
        assert response.status_code == 200
    result = execute_run(_execution(settings, db_session_factory), uuid.UUID(queued["id"]))
    assert result.status == "canceled"

    # Restored, then revoked after the first page of a running execution.
    with db_session_factory() as db:
        db.execute(
            text("UPDATE case_members SET role = 'analyst' WHERE user_id = :user"),
            {"user": team["second"]["id"]},
        )
        db.commit()

    def revoke_after_first_page(_connector_run: uuid.UUID, page_index: int) -> None:
        if page_index == 0:
            with db_session_factory() as db:
                db.execute(
                    text("DELETE FROM case_members WHERE user_id = :user"),
                    {"user": team["second"]["id"]},
                )
                db.commit()

    context = _execution(settings, db_session_factory, after_page=revoke_after_first_page)
    assert execute_run(context, uuid.UUID(running["id"])).status == "canceled"

    with db_session_factory() as db:
        first = db.get(QueryRun, uuid.UUID(queued["id"]))
        second_run = db.get(QueryRun, uuid.UUID(running["id"]))
        assert first is not None
        assert second_run is not None
        assert first.error_code == "authorization_revoked"
        assert second_run.error_code == "authorization_revoked"
        counts: dict[uuid.UUID | None, int] = {
            run_id: int(count)
            for run_id, count in db.execute(
                select(EvidenceObject.query_run_id, func.count()).group_by(
                    EvidenceObject.query_run_id
                )
            )
        }
    assert counts.get(first.id, 0) == 0
    assert counts[second_run.id] == 1  # the page collected before the revocation is kept
    with signed_in(settings, *ANALYST) as (analyst, _):
        detail = analyst.get(f"/api/v1/cases/{case['id']}/runs/{running['id']}").json()
        assert detail["status"] == "canceled"
        assert "no longer has analyst access" in detail["connector_runs"][0]["coverage_note"]


def test_processing_job_and_ai_answer_stop_when_the_requester_loses_access(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    team = _team(client, authed)
    chat = "03/04/2024, 09:16 - Ayşe: Merhaba\n14/04/2024, 09:17 - Can: Selam\n"
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Revoked imports")
        add_member(analyst, csrf, case["id"], SECOND_ANALYST[0], "analyst")
        import_file(analyst, csrf, case["id"], REGISTRY.encode(), title="Registry")
        run_indexing(settings, db_session_factory, case["id"])
    with signed_in(settings, *SECOND_ANALYST) as (second, csrf):
        uploaded = second.post(
            f"/api/v1/cases/{case['id']}/imports/whatsapp",
            files={"file": ("WhatsApp Chat.txt", chat.encode(), "application/octet-stream")},
            data={"import_origin": "Synthetic export", "timezone": "Europe/Istanbul"},
            headers=browser_headers(csrf),
        )
        assert uploaded.status_code == 202, uploaded.text
        conversation = second.post(
            f"/api/v1/cases/{case['id']}/ai/conversations", json={}, headers=browser_headers(csrf)
        ).json()
        asked = second.post(
            f"/api/v1/cases/{case['id']}/ai/conversations/{conversation['id']}/questions",
            json={"question": "Kim tescil etti?"},
            headers=browser_headers(csrf),
        ).json()

    with db_session_factory() as db:
        db.execute(
            text("UPDATE users SET role = 'viewer' WHERE id = :user"),
            {"user": team["second"]["id"]},
        )
        db.commit()

    job_id = uuid.UUID(uploaded.json()["job"]["id"])
    context = ProcessingContext(
        session_factory=db_session_factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="test-worker",
    )
    assert execute_job(context, job_id) == "canceled"
    assert run_ai(settings, db_session_factory, asked["id"]) == "canceled"
    with db_session_factory() as db:
        job = db.get(ProcessingJob, job_id)
        ai_run = db.get(AiRun, uuid.UUID(asked["id"]))
        assert job is not None
        assert ai_run is not None
        assert job.error_code == "authorization_revoked"
        assert ai_run.error_code == "authorization_revoked"
        assert (
            db.execute(
                text("SELECT count(*) FROM observations WHERE case_id = :case"),
                {"case": case["id"]},
            ).scalar()
            == 0
        )
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM ai_messages WHERE role = 'assistant' AND case_id = :case"
                ),
                {"case": case["id"]},
            ).scalar()
            == 0
        )


def test_setup_admin_session_reports_system_permissions(client: TestClient, authed: str) -> None:
    # A fresh administrator from setup can manage accounts but has no case until creating one.
    session = client.get("/api/v1/auth/session").json()
    assert session["user"]["username"] == TEST_ADMIN_USERNAME
    assert set(session["permissions"]) >= {"accounts.manage", "cases.create", "audit.system.read"}
    assert TEST_ADMIN_PASSWORD not in json.dumps(session)
