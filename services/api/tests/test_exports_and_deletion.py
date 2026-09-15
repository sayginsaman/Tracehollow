from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.cases.deletion import DeletionContext, count_case_rows, execute_deletion
from app.cases.models import CaseDeletion
from app.config import Settings
from app.evidence.storage import EvidenceStorage
from app.queries.execution import ExecutionContext, execute_run
from tests.conftest import TEST_ADMIN_PASSWORD, browser_headers, create_case, import_file

pytestmark = pytest.mark.integration

FORMULA = '=HYPERLINK("http://attacker.example","click")'


def _populate(
    client: TestClient, settings: Settings, csrf: str, factory: sessionmaker[Session]
) -> dict[str, Any]:
    case = create_case(client, csrf, title="Export case")
    entity = client.post(
        f"/api/v1/cases/{case['id']}/entities",
        json={
            "entity_type": "organization",
            "display_name": FORMULA,
            "identifiers": [{"identifier_type": "name", "value": "+Örnek A.Ş."}],
        },
        headers=browser_headers(csrf),
    ).json()
    _s, imported = import_file(
        client, csrf, case["id"], "İçerik: @SUM(1)".encode(), title="-2+3 synthetic import"
    )
    client.post(
        f"/api/v1/cases/{case['id']}/entities/{entity['id']}/evidence-links",
        json={"evidence_id": imported["evidence"]["id"]},
        headers=browser_headers(csrf),
    )
    client.post(
        f"/api/v1/cases/{case['id']}/notes",
        json={"body": "@cmd note", "entity_id": entity["id"]},
        headers=browser_headers(csrf),
    )
    query = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries",
        json={
            "name": "Partial fixture",
            "input_type": "domain",
            "input_value": "örnek.example",
            "connector_ids": ["synthetic.fixture"],
            "parameters": {"scenario": "partial"},
        },
        headers=browser_headers(csrf),
    ).json()
    run = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}/runs",
        headers=browser_headers(csrf),
    ).json()
    execute_run(
        ExecutionContext(
            session_factory=factory,
            storage=EvidenceStorage(settings.evidence_storage_path),
            settings=settings,
            worker_name="test",
            sleep=lambda _s: None,
        ),
        uuid.UUID(run["id"]),
    )
    return {"case": case, "entity": entity, "evidence": imported["evidence"], "run": run}


def _secret_values(settings: Settings) -> list[str]:
    values = [TEST_ADMIN_PASSWORD, "$argon2id$", str(settings.evidence_storage_path)]
    for secret in (
        settings.secret_key,
        settings.database_password,
        settings.redis_password,
        settings.bootstrap_token,
    ):
        assert secret is not None
        values.append(secret.get_secret_value())
    return values


def test_json_export_preserves_provenance_without_secrets(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    data = _populate(client, settings, authed, db_session_factory)
    case = data["case"]
    response = client.get(f"/api/v1/cases/{case['id']}/exports/json")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    document = response.json()
    manifest, payload = document["manifest"], document["data"]

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert manifest["data_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert manifest["format"] == "tracehollow.case-export"
    assert manifest["synthetic_data_present"] is True
    assert manifest["acquisition_methods"] == {"authorized_import": 1, "synthetic_fixture": 1}
    assert manifest["coverage_gaps"][0]["outcome"] == "partial"
    assert manifest["record_counts"]["evidence"] == 2
    assert manifest["evidence_content_included"] is False

    imported = next(
        e for e in payload["evidence"] if e["acquisition_method"] == "authorized_import"
    )
    assert imported["sha256"] == data["evidence"]["sha256"]
    assert imported["import_origin"]
    assert imported["imported_by"] == "analyst.admin"
    assert "storage_key" not in imported
    fixture = next(e for e in payload["evidence"] if e["synthetic"])
    assert fixture["connector_id"] == "synthetic.fixture"
    assert fixture["query_run_id"] == data["run"]["id"]
    assert payload["observations"][0]["evidence_id"] == fixture["id"]

    raw = response.text
    for value in _secret_values(settings):
        assert value not in raw
    assert "lease_token" not in raw
    assert "password_hash" not in raw


def test_csv_export_neutralizes_formulas_and_lists_hashes(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    data = _populate(client, settings, authed, db_session_factory)
    case = data["case"]
    response = client.get(f"/api/v1/cases/{case['id']}/exports/csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(archive.read("manifest.json"))
    for name, info in manifest["files"].items():
        assert hashlib.sha256(archive.read(name)).hexdigest() == info["sha256"]

    def rows(name: str) -> list[dict[str, str]]:
        text = archive.read(name).decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    entity = rows("entities.csv")[0]
    assert entity["display_name"] == "'" + FORMULA
    assert rows("entity_identifiers.csv")[0]["original_value"] == "'+Örnek A.Ş."
    imported = next(
        r for r in rows("evidence.csv") if r["acquisition_method"] == "authorized_import"
    )
    assert imported["title"] == "'-2+3 synthetic import"
    assert rows("notes.csv")[0]["body"] == "'@cmd note"
    assert rows("connector_runs.csv")[0]["outcome"] == "partial"

    everything = b"".join(archive.read(name) for name in archive.namelist()).decode(
        "utf-8", "ignore"
    )
    for value in _secret_values(settings):
        assert value not in everything
    for cell_row in csv.reader(io.StringIO(archive.read("entities.csv").decode("utf-8-sig"))):
        for cell in cell_row:
            assert not cell.startswith(("=", "+", "-", "@"))


def test_case_deletion_removes_records_and_files(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    data = _populate(client, settings, authed, db_session_factory)
    case = data["case"]
    case_id = uuid.UUID(case["id"])
    case_dir = settings.evidence_storage_path / "cases" / str(case_id)
    assert any(case_dir.rglob("*"))
    other = create_case(client, authed, title="Unrelated case")
    _s, kept = import_file(client, authed, other["id"], b"must survive")

    mismatch = client.post(
        f"/api/v1/cases/{case_id}/deletion",
        json={"confirm_title": "wrong"},
        headers=browser_headers(authed),
    )
    assert mismatch.status_code == 422
    requested = client.post(
        f"/api/v1/cases/{case_id}/deletion",
        json={"confirm_title": "Export case"},
        headers=browser_headers(authed),
    )
    assert requested.status_code == 202
    deletion = requested.json()
    assert deletion["status"] == "queued"
    blocked = client.get(f"/api/v1/cases/{case_id}")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "case_deletion_in_progress"
    assert client.get(f"/api/v1/cases/{case_id}/exports/json").status_code == 409

    with db_session_factory() as db:
        before = count_case_rows(db, case_id)
    assert before["evidence_objects"] == 2
    assert before["observations"] > 0

    result = execute_deletion(
        DeletionContext(
            db_session_factory, EvidenceStorage(settings.evidence_storage_path), settings, "test"
        ),
        uuid.UUID(deletion["id"]),
    )
    assert result == "completed"
    status = client.get(f"/api/v1/case-deletions/{deletion['id']}").json()
    assert status["status"] == "completed"
    assert status["removed_counts"]["evidence_objects"] == 2
    assert status["removed_counts"]["evidence_files"] == 2

    with db_session_factory() as db:
        assert all(count == 0 for count in count_case_rows(db, case_id).values())
    assert not case_dir.exists()
    assert client.get(f"/api/v1/cases/{case_id}").status_code == 404
    # Unrelated data is untouched.
    assert (
        client.get(f"/api/v1/cases/{other['id']}/evidence/{kept['evidence']['id']}/content").content
        == b"must survive"
    )


def test_failed_deletion_is_visible_and_retryable(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed, title="Fragile")
    import_file(client, authed, case["id"], b"evidence to remove")
    deletion = client.post(
        f"/api/v1/cases/{case['id']}/deletion",
        json={"confirm_title": "Fragile"},
        headers=browser_headers(authed),
    ).json()
    storage = EvidenceStorage(settings.evidence_storage_path)

    def fail(_case_id: uuid.UUID) -> None:
        raise PermissionError("simulated read-only evidence volume")

    assert (
        execute_deletion(
            DeletionContext(
                db_session_factory, storage, settings, "test", before_file_removal=fail
            ),
            uuid.UUID(deletion["id"]),
        )
        == "failed"
    )
    failed = client.get(f"/api/v1/case-deletions/{deletion['id']}").json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "remove_files_failed"
    assert "retry" in failed["progress_note"]
    assert client.get(f"/api/v1/cases/{case['id']}").json()["detail"] == "case_deletion_in_progress"
    assert client.get("/api/v1/cases", params={"status": "deletion_failed"}).json()["total"] == 1

    retry = client.post(
        f"/api/v1/case-deletions/{deletion['id']}/retry", headers=browser_headers(authed)
    )
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued"
    assert (
        execute_deletion(
            DeletionContext(db_session_factory, storage, settings, "test"),
            uuid.UUID(deletion["id"]),
        )
        == "completed"
    )
    with db_session_factory() as db:
        job = db.scalar(select(CaseDeletion).where(CaseDeletion.id == uuid.UUID(deletion["id"])))
        assert job is not None
        assert job.attempts == 2
    assert (
        client.post(
            f"/api/v1/case-deletions/{deletion['id']}/retry", headers=browser_headers(authed)
        ).status_code
        == 409
    )


def test_deletion_waits_for_running_executions(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    from app.queries.execution import claim_run

    case = create_case(client, authed, title="Busy")
    query = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries",
        json={
            "name": "slow",
            "input_type": "username",
            "input_value": "x",
            "connector_ids": ["synthetic.fixture"],
            "parameters": {"scenario": "slow"},
        },
        headers=browser_headers(authed),
    ).json()
    run = client.post(
        f"/api/v1/cases/{case['id']}/saved-queries/{query['id']}/runs",
        headers=browser_headers(authed),
    ).json()
    context = ExecutionContext(
        db_session_factory,
        EvidenceStorage(settings.evidence_storage_path),
        settings,
        "test",
        sleep=lambda _s: None,
    )
    assert claim_run(context, uuid.UUID(run["id"])) is not None  # a worker is mid-run

    deletion = client.post(
        f"/api/v1/cases/{case['id']}/deletion",
        json={"confirm_title": "Busy"},
        headers=browser_headers(authed),
    ).json()
    deletion_context = DeletionContext(
        db_session_factory, EvidenceStorage(settings.evidence_storage_path), settings, "test"
    )
    assert execute_deletion(deletion_context, uuid.UUID(deletion["id"])) == "waiting"
    waiting = client.get(f"/api/v1/case-deletions/{deletion['id']}").json()
    assert waiting["status"] == "queued"
    assert "Waiting for 1 running execution" in waiting["progress_note"]

    # The running execution observes the cancellation request and stops.
    from sqlalchemy import update

    from app.db.base import utcnow
    from app.queries.models import QueryRun

    with db_session_factory() as db:
        db.execute(
            update(QueryRun)
            .where(QueryRun.id == uuid.UUID(run["id"]))
            .values(lease_expires_at=utcnow())
        )
        db.commit()
    assert execute_run(context, uuid.UUID(run["id"])).status == "canceled"
    assert execute_deletion(deletion_context, uuid.UUID(deletion["id"])) == "completed"


def test_deletion_status_is_private_to_the_requester(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    from tests.conftest import SECOND_PASSWORD, SECOND_USERNAME, create_second_user, login_as

    case = create_case(client, authed, title="Private")
    deletion = client.post(
        f"/api/v1/cases/{case['id']}/deletion",
        json={"confirm_title": "Private"},
        headers=browser_headers(authed),
    ).json()
    create_second_user(db_session_factory)
    client.cookies.clear()
    csrf = login_as(client, SECOND_USERNAME, SECOND_PASSWORD)
    assert client.get(f"/api/v1/case-deletions/{deletion['id']}").status_code == 404
    assert (
        client.post(
            f"/api/v1/case-deletions/{deletion['id']}/retry", headers=browser_headers(csrf)
        ).status_code
        == 404
    )
    assert client.get("/api/v1/case-deletions").json()["total"] == 0
