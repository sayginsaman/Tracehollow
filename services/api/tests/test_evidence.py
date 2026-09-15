from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.evidence.models import EvidenceObject
from app.evidence.reconcile import reconcile
from app.evidence.storage import EvidenceStorage
from app.main import create_app
from tests.conftest import (
    ServiceEndpoints,
    TemporaryDatabase,
    browser_headers,
    complete_setup,
    create_case,
    import_file,
    login,
    make_settings,
)

pytestmark = pytest.mark.integration

HOSTILE_HTML = (
    b"<script>alert('x')</script><img src=x onerror=alert(1)> \xc3\x87al\xc4\xb1\xc5\x9fma"
)


def test_text_import_stores_exact_bytes_with_provenance(
    client: TestClient, settings: Settings, authed: str
) -> None:
    case = create_case(client, authed)
    content = "Satır 1: İstanbul\r\nLine 2 with CRLF\r\n".encode()
    status, body = import_file(
        client,
        authed,
        case["id"],
        content,
        filename="saha-notları.txt",
        title="Field notes",
        source_reference="https://example.org/original",
        source_published_at="2026-09-01T10:00:00+03:00",
    )
    assert status == 201, body
    evidence = body["evidence"]
    assert evidence["sha256"] == hashlib.sha256(content).hexdigest()
    assert evidence["size_bytes"] == len(content)
    assert evidence["acquisition_method"] == "authorized_import"
    assert evidence["synthetic"] is False
    assert evidence["import_origin"].startswith("Synthetic test fixture")
    assert evidence["source_published_at"] in ("2026-09-01T07:00:00Z", "2026-09-01T07:00:00+00:00")
    assert evidence["source_published_at_original"] == "2026-09-01T10:00:00+03:00"
    assert evidence["content_type"] == "text/plain; charset=utf-8"
    assert evidence["original_filename"] == "saha-notları.txt"

    stored = (
        settings.evidence_storage_path / "cases" / str(case["id"]) / "evidence" / evidence["id"]
    )
    assert stored.read_bytes() == content

    detail = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence['id']}").json()
    assert detail["integrity"]["status"] == "verified"


def test_reimport_creates_new_record_and_reports_duplicates(
    client: TestClient, authed: str
) -> None:
    case = create_case(client, authed)
    _s, first = import_file(client, authed, case["id"], b"same bytes")
    status, second = import_file(client, authed, case["id"], b"same bytes")
    assert status == 201
    assert second["evidence"]["id"] != first["evidence"]["id"]
    assert second["duplicate_of"] == [first["evidence"]["id"]]
    assert client.get(f"/api/v1/cases/{case['id']}/evidence").json()["total"] == 2


def test_json_import_and_safe_preview(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    document = {"şehir": "İzmir", "html": "<script>alert(1)</script>"}
    status, body = import_file(
        client,
        authed,
        case["id"],
        json.dumps(document, ensure_ascii=False).encode(),
        kind="json",
        filename="data.json",
    )
    assert status == 201
    evidence_id = body["evidence"]["id"]
    preview = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}/preview")
    assert preview.headers["content-type"].startswith("application/json")
    payload = preview.json()
    assert json.loads(payload["pretty_json"]) == document
    assert payload["truncated"] is False


def test_hostile_content_is_served_as_inert_attachment(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    _s, body = import_file(client, authed, case["id"], HOSTILE_HTML, filename="page.html")
    evidence_id = body["evidence"]["id"]

    preview = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}/preview")
    assert preview.headers["content-type"].startswith("application/json")
    assert "<script>" in preview.json()["text"]  # returned as data for text rendering only

    download = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}/content")
    assert download.status_code == 200
    assert download.content == HOSTILE_HTML
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in download.headers["content-security-policy"]
    assert download.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("kind", "content", "code"),
    [
        ("json", b"{broken", "invalid_json"),
        ("text", b"\xff\xfe not utf8", "invalid_encoding"),
        ("text", b"", "empty_content"),
    ],
)
def test_malformed_imports_are_rejected_without_storing_anything(
    client: TestClient, settings: Settings, authed: str, kind: str, content: bytes, code: str
) -> None:
    case = create_case(client, authed)
    status, body = import_file(client, authed, case["id"], content, kind=kind)
    assert status == 422
    assert body["detail"]["code"] == code
    assert client.get(f"/api/v1/cases/{case['id']}/evidence").json()["total"] == 0
    case_dir = settings.evidence_storage_path / "cases" / str(case["id"])
    assert not case_dir.exists() or not any(case_dir.rglob("*"))


def test_unsafe_filename_never_reaches_the_filesystem(
    client: TestClient, settings: Settings, authed: str
) -> None:
    case = create_case(client, authed)
    status, body = import_file(
        client, authed, case["id"], b"payload", filename="../../../../tmp/evil‮txt.sh"
    )
    assert status == 201
    assert body["filename_sanitized"] is True
    evidence = body["evidence"]
    assert evidence["original_filename"] == "eviltxt.sh"
    root = settings.evidence_storage_path.resolve()
    files = [path for path in root.rglob("*") if path.is_file()]
    assert files == [root / "cases" / str(case["id"]) / "evidence" / evidence["id"]]
    assert not (Path("/tmp") / "evil").exists()  # noqa: S108


def test_oversized_import_is_rejected(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(
        services, migrated_database.name, tmp_path, evidence_max_import_bytes=2048
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        complete_setup(client, settings)
        csrf = login(client)
        case = create_case(client, csrf)
        status, _body = import_file(client, csrf, case["id"], b"x" * 4096)
        assert status == 413
        huge = client.post(
            f"/api/v1/cases/{case['id']}/evidence/imports",
            files={"file": ("big.txt", b"x" * (2048 + 70_000), "text/plain")},
            data={"kind": "text", "import_origin": "test"},
            headers=browser_headers(csrf),
        )
        assert huge.status_code == 413


def test_import_requires_origin_and_writable_case(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    missing_origin = client.post(
        f"/api/v1/cases/{case['id']}/evidence/imports",
        files={"file": ("a.txt", b"abc", "text/plain")},
        data={"kind": "text"},
        headers=browser_headers(authed),
    )
    assert missing_origin.status_code == 422
    client.post(f"/api/v1/cases/{case['id']}/archive", headers=browser_headers(authed))
    status, body = import_file(client, authed, case["id"], b"abc")
    assert status == 409
    assert body["detail"] == "case_archived"


def test_integrity_failures_are_reported_not_hidden(
    client: TestClient, settings: Settings, authed: str
) -> None:
    case = create_case(client, authed)
    _s, body = import_file(client, authed, case["id"], b"original bytes")
    evidence_id = body["evidence"]["id"]
    stored = settings.evidence_storage_path / "cases" / str(case["id"]) / "evidence" / evidence_id
    stored.write_bytes(b"tampered bytes")

    detail = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}").json()
    assert detail["integrity"]["status"] == "evidence_hash_mismatch"
    content = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}/content")
    assert content.status_code == 409
    assert content.json()["detail"] == "evidence_hash_mismatch"

    stored.unlink()
    assert (
        client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}").json()["integrity"][
            "status"
        ]
        == "evidence_file_missing"
    )


def test_failed_metadata_commit_removes_promoted_file(
    client: TestClient, settings: Settings, authed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = create_case(client, authed)
    from app.evidence import service

    def boom(*_args: object, **_kwargs: object) -> list[uuid.UUID]:
        raise RuntimeError("simulated database failure after file promotion")

    monkeypatch.setattr(service, "find_duplicates", boom)
    with pytest.raises(RuntimeError):
        import_file(client, authed, case["id"], b"will not be committed")
    root = settings.evidence_storage_path
    assert [p for p in root.rglob("*") if p.is_file()] == []


def test_reconcile_recovers_from_interrupted_writes(
    client: TestClient, settings: Settings, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    _s, body = import_file(client, authed, case["id"], b"committed evidence")
    committed_id = body["evidence"]["id"]
    storage = EvidenceStorage(settings.evidence_storage_path)

    # Crash before promotion: a staged temp file remains.
    staged = storage.stage(b"staged but never promoted")
    # Crash after promotion but before commit: a file without a metadata row.
    orphan_key = EvidenceStorage.key_for(uuid.UUID(str(case["id"])), uuid.uuid4())
    storage.store(orphan_key, b"orphaned bytes")
    old = time.time() - 3600
    for path in (staged.path, storage.path_for_key(orphan_key)):
        os.utime(path, (old, old))
    # Storage loss: a metadata row whose file disappeared.
    with db_session_factory() as db:
        row = db.scalar(select(EvidenceObject).where(EvidenceObject.id == uuid.UUID(committed_id)))
        assert row is not None
        storage.path_for_key(row.storage_key).unlink()

    dry = reconcile(db_session_factory, storage, grace_seconds=60, apply=False)
    assert dry.staged_removed == 1
    assert dry.orphans_quarantined == [orphan_key]
    assert staged.path.exists()

    report = reconcile(db_session_factory, storage, grace_seconds=60, apply=True)
    assert not staged.path.exists()
    assert not storage.path_for_key(orphan_key).exists()
    assert len(list((settings.evidence_storage_path / "quarantine").iterdir())) == 1
    assert report.missing_files == [uuid.UUID(committed_id)]
    with db_session_factory() as db:
        assert db.get(EvidenceObject, uuid.UUID(committed_id)) is not None  # never auto-deleted
