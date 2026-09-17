"""Timeline sections and entity comparison over existing records (Phase 4 temporal analysis)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.entities.models import Entity, EntityIdentifier, Observation
from tests.collection_helpers import Router, respond
from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    browser_headers,
    create_case,
    create_second_user,
    import_file,
    login_as,
)
from tests.test_collection import _query, _run
from tests.test_whatsapp_import import ANDROID_ZIP_CHAT, _run_queued, _upload

pytestmark = pytest.mark.integration

API = "https://api.github.com"
REPOS = f"{API}/users/ornek-dev/repos?type=owner&sort=updated&per_page=5&page=1"


def _account(name: str, repos: int) -> dict[str, Any]:
    return {
        "login": "ornek-dev",
        "id": 424242,
        "html_url": "https://github.com/ornek-dev",
        "name": name,
        "public_repos": repos,
        "created_at": "2019-05-01T08:00:00Z",
    }


def _github_runs(
    client: TestClient, authed: str, settings: Settings, factory: sessionmaker[Session], case: Any
) -> list[dict[str, Any]]:
    query = _query(
        client,
        authed,
        case["id"],
        "github.account",
        "username",
        "ornek-dev",
        limits={"max_pages": 3, "max_items_per_page": 5},
    )
    scenarios = [
        (_account("Örnek Dev", 2), [respond(200, json_body=[{"id": 1}, {"id": 2}])]),
        (_account("Örnek Developer", 1), [respond(200, json_body=[{"id": 1}])]),
        (_account("Örnek Developer", 1), [respond(503, body="down")]),
    ]
    runs = []
    for account, repos in scenarios:
        router = Router()
        router.add(f"{API}/users/ornek-dev", respond(200, json_body=account))
        router.add(REPOS, *repos)
        runs.append(_run(client, authed, settings, factory, case["id"], query["id"], router))
    return runs


def test_comparison_reports_identifiers_changes_absences_and_conflicts_without_merging(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    runs = _github_runs(client, authed, settings, db_session_factory, case)
    assert [r["connector_runs"][0]["outcome"] for r in runs] == ["findings", "findings", "partial"]
    with db_session_factory() as db:
        github = db.scalar(select(Entity).where(Entity.case_id == uuid.UUID(case["id"])))
        assert github is not None

    def entity(name: str, identifiers: list[dict[str, str]]) -> str:
        response = client.post(
            f"/api/v1/cases/{case['id']}/entities",
            json={
                "entity_type": "platform_account",
                "display_name": name,
                "identifiers": identifiers,
            },
            headers=browser_headers(authed),
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    same_username = entity(
        "ornek-dev elsewhere",
        [{"identifier_type": "username", "value": "ornek-dev", "platform": "github.com"}],
    )
    other_account = entity(
        "Another GitHub account",
        [{"identifier_type": "platform_id", "value": "999", "platform": "github.com"}],
    )
    # An imported note that gives the same account a different name.
    _, note = import_file(client, authed, case["id"], b"Profile says: Name Ornek Takim")
    with db_session_factory() as db:
        db.add(
            Observation(
                case_id=uuid.UUID(case["id"]),
                entity_id=github.id,
                evidence_id=uuid.UUID(note["evidence"]["id"]),
                observation_type="analyst_note_fact",
                payload={"name": "Ornek Takim"},
                collected_at=datetime(2026, 9, 16, tzinfo=UTC),
                idempotency_key="test:note-name",
            )
        )
        db.commit()
        entity_count = db.query(Entity).count()
        identifier_count = db.query(EntityIdentifier).count()

    url = f"/api/v1/cases/{case['id']}/entity-comparison"
    params = {"entity_id": [str(github.id), same_username, other_account]}
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    body = response.json()

    shared = [i for i in body["identifiers"] if i["kind"] == "shared"]
    assert [(i["identifier_type"], i["values"]) for i in shared] == [("username", ["ornek-dev"])]
    conflicting = [i for i in body["identifiers"] if i["kind"] == "conflicting_platform_id"]
    assert conflicting[0]["values"] == ["424242", "999"]
    assert any(c["kind"] == "different_stable_ids" for c in body["conflicts"])
    assert any("not proof that they are the same" in u for u in body["unresolved"])
    assert "never merges" in body["merge_policy"]

    name_changes = [c for c in body["changes"] if c["field"] == "name"]
    assert (name_changes[0]["previous"], name_changes[0]["current"]) == (
        "Örnek Dev",
        "Örnek Developer",
    )
    assert "do not date it" in name_changes[0]["note"]
    assert not any(c["field"] == "name" and c["current"] == "Ornek Takim" for c in body["changes"])

    absences = {a["interpretation"]: a for a in body["absences"]}
    complete = absences["not_observed_in_later_complete_collection"]
    assert complete["items"] == ["github_repository:2"]
    assert "does not prove deletion" in complete["note"]
    unknown = absences["unknown_later_collection_incomplete"]
    assert unknown["items"] == ["github_repository:1"]
    assert unknown["later_run_outcome"] == "partial"

    name_conflict = next(c for c in body["conflicts"] if c["field"] == "name")
    assert name_conflict["kind"] == "different_values_across_sources"
    assert sorted(name_conflict["values"]) == [
        "authorized_import: Ornek Takim",
        "github.account: Örnek Developer",
    ]

    github_summary = next(e for e in body["entities"] if e["id"] == str(github.id))
    sources = {c["source"]: c for c in github_summary["coverage"]}
    assert len(sources["github.account"]["connector_runs"]) == 3
    assert sources["authorized_import"]["acquisition_method"] == "authorized_import"
    assert github_summary["event_time_span"][0].startswith("2019-05-01")

    with db_session_factory() as db:
        assert db.query(Entity).count() == entity_count
        assert db.query(EntityIdentifier).count() == identifier_count

    assert client.get(url, params={"entity_id": [str(github.id)]}).status_code == 422
    missing = {"entity_id": [str(github.id), str(uuid.uuid4())]}
    assert client.get(url, params=missing).status_code == 404


def test_timeline_keeps_utc_local_only_and_undated_items_apart(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    assert _upload(client, authed, case["id"], ANDROID_ZIP_CHAT.encode()).status_code == 202
    assert (
        _upload(
            client, authed, case["id"], ANDROID_ZIP_CHAT.encode(), timezone="unknown"
        ).status_code
        == 202
    )
    assert _run_queued(db_session_factory, settings) == ["partial", "partial"]
    _github_runs(client, authed, settings, db_session_factory, case)

    base = f"/api/v1/cases/{case['id']}/timeline"
    dated = client.get(base, params={"limit": 100}).json()
    sections = dated["sections"]
    assert sections["local_time_only"] == 4
    assert sections["dated"] >= 4 + 3
    assert sections["undated"] >= 1
    times = [item["time"] for item in dated["items"]]
    assert times == sorted(times)
    whatsapp = [i for i in dated["items"] if i["observation_type"] == "whatsapp_message"]
    assert whatsapp[0]["time_basis"] == "event_time"
    assert whatsapp[0]["time"].startswith("2024-04-13T05:01:00")  # 08:01 in Istanbul
    assert whatsapp[0]["timestamp_text"] == "13/04/2024, 08:01"
    assert whatsapp[0]["acquisition_method"] == "authorized_import"
    assert whatsapp[0]["location"]["line_start"] == 2

    local = client.get(base, params={"section": "local_time_only"}).json()
    assert {i["time"] for i in local["items"]} == {None}
    assert [i["local_time"] for i in local["items"]] == sorted(
        i["local_time"] for i in local["items"]
    )
    assert "cannot be placed on the UTC timeline" in local["items"][0]["notes"][0]

    undated = client.get(base, params={"section": "undated"}).json()
    assert all(i["time_basis"] == "collected_at_only" for i in undated["items"])

    window = client.get(
        base, params={"from": "2024-04-13T05:01:30+00:00", "to": "2024-04-13T05:03:00+00:00"}
    ).json()
    assert {i["timestamp_text"] for i in window["items"]} == {
        "13/04/2024, 08:02",
        "13/04/2024, 08:03",
    }
    assert client.get(base, params={"from": "2024-04-13T05:00:00"}).status_code == 422

    create_second_user(db_session_factory)
    other = TestClient(client.app, base_url=str(client.base_url))
    login_as(other, SECOND_USERNAME, SECOND_PASSWORD)
    assert other.get(base).status_code == 404
    with db_session_factory() as db:
        ids = [
            str(e.id)
            for e in db.scalars(select(Entity).where(Entity.case_id == uuid.UUID(case["id"])))
        ]
    ids.append(ids[0])
    comparison = other.get(
        f"/api/v1/cases/{case['id']}/entity-comparison",
        params=[("entity_id", value) for value in ids[:2]],
    )
    assert comparison.status_code == 404
