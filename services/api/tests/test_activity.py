"""Workspace activity: recent runs and processing jobs across the cases a user can open.

Also covers the per-connector outcomes that run lists carry for plain-language summaries.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.queries.execution import execute_run
from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    browser_headers,
    create_case,
    create_second_user,
    login_as,
)
from tests.test_queries import _context, _saved_query, _start_run
from tests.test_whatsapp_import import ANDROID_AMBIGUOUS, _run_queued, _upload

pytestmark = pytest.mark.integration


def test_run_lists_carry_connector_outcomes(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    empty = _saved_query(client, authed, case["id"], "no_findings")
    finished = _start_run(client, authed, case["id"], empty["id"])
    execute_run(_context(settings, db_session_factory), uuid.UUID(str(finished["id"])))
    blocked = _saved_query(client, authed, case["id"], "authentication_required")
    queued = _start_run(client, authed, case["id"], blocked["id"])

    runs = {
        item["id"]: item for item in client.get(f"/api/v1/cases/{case['id']}/runs").json()["items"]
    }
    assert runs[finished["id"]]["status"] == "completed"
    assert runs[finished["id"]]["connector_outcomes"] == ["no_findings"]
    # A connector that has not finished has no outcome yet; it is not reported as empty.
    assert runs[queued["id"]]["connector_outcomes"] == [None]


def test_activity_lists_recent_work_in_member_cases_only(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    first = create_case(client, authed, title="Örnek birinci vaka")
    second = create_case(client, authed, title="İkinci vaka")
    query = _saved_query(client, authed, first["id"], "findings")
    run = _start_run(client, authed, first["id"], query["id"])
    execute_run(_context(settings, db_session_factory), uuid.UUID(str(run["id"])))
    active = _start_run(client, authed, first["id"], query["id"])
    upload = _upload(client, authed, second["id"], ANDROID_AMBIGUOUS.encode())
    assert upload.status_code == 202, upload.text
    assert _run_queued(db_session_factory, settings) == ["needs_input"]

    response = client.get("/api/v1/activity?limit=5")
    assert response.status_code == 200, response.text
    activity = response.json()
    assert [item["id"] for item in activity["runs"]] == [active["id"], run["id"]]
    assert activity["runs"][1]["connector_outcomes"] == ["findings"]
    assert activity["active_runs"] == 1
    assert [job["id"] for job in activity["jobs_needing_input"]] == [upload.json()["job"]["id"]]
    assert activity["processing_jobs"][0]["status"] == "needs_input"
    assert activity["active_processing_jobs"] == 0
    titles = {item["id"]: item["title"] for item in activity["cases"]}
    assert titles == {first["id"]: "Örnek birinci vaka", second["id"]: "İkinci vaka"}

    # A case being deleted can no longer be opened, so its work disappears from the overview.
    with db_session_factory() as db:
        db.execute(
            update(Case)
            .where(Case.id == uuid.UUID(second["id"]))
            .values(status=CaseStatus.DELETING)
        )
        db.commit()
    activity = client.get("/api/v1/activity").json()
    assert activity["jobs_needing_input"] == []
    assert activity["processing_jobs"] == []
    assert {item["id"] for item in activity["cases"]} == {first["id"]}

    # Another user sees none of it.
    create_second_user(db_session_factory)
    login_as(client, SECOND_USERNAME, SECOND_PASSWORD)
    outsider = client.get("/api/v1/activity", headers=browser_headers()).json()
    assert outsider == {
        "runs": [],
        "active_runs": 0,
        "processing_jobs": [],
        "active_processing_jobs": 0,
        "jobs_needing_input": [],
        "cases": [],
    }


def test_activity_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/activity").status_code == 401
