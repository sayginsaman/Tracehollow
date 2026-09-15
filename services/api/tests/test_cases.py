from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    browser_headers,
    create_case,
    create_second_user,
    import_file,
    login,
    login_as,
)

pytestmark = pytest.mark.integration


def test_case_lifecycle_create_edit_list_archive_restore(client: TestClient, authed: str) -> None:
    case = create_case(client, authed, title="Şirket altyapısı", tags=["infra", "İnfra", "tr"])
    assert case["status"] == "active"
    assert case["tags"] == ["infra", "tr"]  # case-insensitive de-duplication keeps first spelling
    assert case["counts"] == {
        "entities": 0,
        "relationships": 0,
        "evidence": 0,
        "notes": 0,
        "saved_queries": 0,
        "query_runs": 0,
        "active_runs": 0,
    }

    updated = client.patch(
        f"/api/v1/cases/{case['id']}",
        json={"title": "Şirket altyapısı (revised)", "scope": "Public DNS only"},
        headers=browser_headers(authed),
    )
    assert updated.status_code == 200
    assert updated.json()["scope"] == "Public DNS only"

    listing = client.get("/api/v1/cases", params={"q": "altyapı"}).json()
    assert listing["total"] == 1

    archived = client.post(f"/api/v1/cases/{case['id']}/archive", headers=browser_headers(authed))
    assert archived.json()["status"] == "archived"
    blocked = client.patch(
        f"/api/v1/cases/{case['id']}", json={"title": "x"}, headers=browser_headers(authed)
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "case_archived"
    assert client.get(f"/api/v1/cases/{case['id']}").status_code == 200

    restored = client.post(f"/api/v1/cases/{case['id']}/restore", headers=browser_headers(authed))
    assert restored.json()["status"] == "active"
    assert client.get("/api/v1/cases", params={"status": "active"}).json()["total"] == 1


def test_case_routes_require_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/cases").status_code == 401
    assert (
        client.post("/api/v1/cases", json={"title": "x"}, headers=browser_headers()).status_code
        == 401
    )


def test_non_member_cannot_reach_any_case_record(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    case_id = case["id"]
    status, imported = import_file(client, authed, case_id, b"secret synthetic note")
    assert status == 201
    evidence_id = imported["evidence"]["id"]
    entity = client.post(
        f"/api/v1/cases/{case_id}/entities",
        json={"entity_type": "domain", "display_name": "example.org"},
        headers=browser_headers(authed),
    ).json()
    query = client.post(
        f"/api/v1/cases/{case_id}/saved-queries",
        json={
            "name": "q",
            "input_type": "username",
            "input_value": "analyst",
            "connector_ids": ["synthetic.fixture"],
        },
        headers=browser_headers(authed),
    ).json()
    run = client.post(
        f"/api/v1/cases/{case_id}/saved-queries/{query['id']}/runs", headers=browser_headers(authed)
    ).json()

    create_second_user(db_session_factory)
    client.cookies.clear()
    outsider_csrf = login_as(client, SECOND_USERNAME, SECOND_PASSWORD)

    assert client.get("/api/v1/cases").json()["total"] == 0
    for path in (
        f"/api/v1/cases/{case_id}",
        f"/api/v1/cases/{case_id}/entities",
        f"/api/v1/cases/{case_id}/entities/{entity['id']}",
        f"/api/v1/cases/{case_id}/evidence",
        f"/api/v1/cases/{case_id}/evidence/{evidence_id}",
        f"/api/v1/cases/{case_id}/evidence/{evidence_id}/preview",
        f"/api/v1/cases/{case_id}/evidence/{evidence_id}/content",
        f"/api/v1/cases/{case_id}/exports/json",
        f"/api/v1/cases/{case_id}/exports/csv",
        f"/api/v1/cases/{case_id}/runs",
        f"/api/v1/cases/{case_id}/runs/{run['id']}",
        f"/api/v1/cases/{case_id}/graph",
        f"/api/v1/cases/{case_id}/notes",
        f"/api/v1/cases/{case_id}/observations",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"detail": "case_not_found"}, path
        assert b"secret synthetic note" not in response.content

    for path, body in (
        (f"/api/v1/cases/{case_id}/runs/{run['id']}/cancel", None),
        (f"/api/v1/cases/{case_id}/archive", None),
        (f"/api/v1/cases/{case_id}/deletion", {"confirm_title": "Synthetic case"}),
        (f"/api/v1/cases/{case_id}/notes", {"body": "intrusion"}),
    ):
        response = client.post(path, json=body, headers=browser_headers(outsider_csrf))
        assert response.status_code == 404, path

    # The owner still sees everything unchanged.
    client.cookies.clear()
    login(client)
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "active"


def test_evidence_is_not_reachable_through_another_case(client: TestClient, authed: str) -> None:
    first = create_case(client, authed, title="First")
    second = create_case(client, authed, title="Second")
    _status, imported = import_file(client, authed, first["id"], b"belongs to first case")
    evidence_id = imported["evidence"]["id"]

    for suffix in ("", "/preview", "/content"):
        response = client.get(f"/api/v1/cases/{second['id']}/evidence/{evidence_id}{suffix}")
        assert response.status_code == 404
        assert response.json() == {"detail": "evidence_not_found"}

    entity = client.post(
        f"/api/v1/cases/{second['id']}/entities",
        json={"entity_type": "document", "display_name": "doc"},
        headers=browser_headers(authed),
    ).json()
    link = client.post(
        f"/api/v1/cases/{second['id']}/entities/{entity['id']}/evidence-links",
        json={"evidence_id": evidence_id},
        headers=browser_headers(authed),
    )
    assert link.status_code == 404


def test_notes_attach_to_one_subject_in_the_same_case(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    entity = client.post(
        f"/api/v1/cases/{case['id']}/entities",
        json={"entity_type": "organization", "display_name": "Örnek A.Ş."},
        headers=browser_headers(authed),
    ).json()
    note = client.post(
        f"/api/v1/cases/{case['id']}/notes",
        json={"body": "Analyst interpretation: needs review.", "entity_id": entity["id"]},
        headers=browser_headers(authed),
    )
    assert note.status_code == 201
    case_note = client.post(
        f"/api/v1/cases/{case['id']}/notes",
        json={"body": "Case-level note"},
        headers=browser_headers(authed),
    )
    assert case_note.status_code == 201
    assert (
        client.get(f"/api/v1/cases/{case['id']}/notes", params={"entity_id": entity["id"]}).json()[
            "total"
        ]
        == 1
    )
    assert (
        client.get(f"/api/v1/cases/{case['id']}/notes", params={"case_level": True}).json()["total"]
        == 1
    )

    both = client.post(
        f"/api/v1/cases/{case['id']}/notes",
        json={"body": "x", "entity_id": entity["id"], "evidence_id": entity["id"]},
        headers=browser_headers(authed),
    )
    assert both.status_code == 422
