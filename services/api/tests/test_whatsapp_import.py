"""WhatsApp export imports end to end: upload, worker processing, provenance, safety, deletion.

All chats, names and numbers are synthetic. Sender labels include a fictional NANP 555-01xx
number to show that labels are never turned into phone-number identifiers.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.entities.models import Entity, EntityIdentifier, Observation
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.imports.jobs import ProcessingContext
from app.imports.models import ProcessingJob, ProcessingStatus
from app.imports.runner import execute_job
from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    browser_headers,
    create_case,
    create_second_user,
    login_as,
)

pytestmark = pytest.mark.integration

ANDROID_AMBIGUOUS = (
    "03/04/2024, 09:15 - Messages and calls are end-to-end encrypted. No one outside of this "
    "chat can read them.\n"
    "03/04/2024, 09:16 - Ayşe Yılmaz: Merhaba, toplantı saat kaçta?\n"
    "İkinci satır: çğıöşü ÇĞİÖŞÜ\n"
    "03/04/2024, 09:17 - +1 202-555-0143: IMG-20240403-WA0001.jpg (file attached)\n"
    "05/04/2024, 21:05 - Can: <Media omitted>\n"
)

ANDROID_ZIP_CHAT = (
    '13/04/2024, 08:00 - Ayşe Yılmaz created group "Synthetic group"\n'
    "13/04/2024, 08:01 - Ayşe Yılmaz: IMG-20240413-WA0001.jpg (file attached)\n"
    "Fotoğraf ektedir\n"
    "13/04/2024, 08:02 - Can: rapor.pdf (file attached)\n"
    "13/04/2024, 08:03 - Can: <Media omitted>\n"
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _context(factory: sessionmaker[Session], settings: Settings) -> ProcessingContext:
    return ProcessingContext(
        session_factory=factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="test-worker",
    )


def _run_queued(factory: sessionmaker[Session], settings: Settings) -> list[str]:
    with factory() as db:
        ids = list(
            db.scalars(
                select(ProcessingJob.id)
                .where(ProcessingJob.status == ProcessingStatus.QUEUED)
                .order_by(ProcessingJob.created_at)
            )
        )
    return [execute_job(_context(factory, settings), job_id) for job_id in ids]


def _upload(
    client: TestClient,
    csrf: str,
    case_id: object,
    content: bytes,
    *,
    filename: str = "WhatsApp Chat with Synthetic.txt",
    timezone: str = "Europe/Istanbul",
    date_order: str = "auto",
) -> Any:
    return client.post(
        f"/api/v1/cases/{case_id}/imports/whatsapp",
        files={"file": (filename, content, "application/octet-stream")},
        data={
            "import_origin": "Synthetic export written by the test suite",
            "timezone": timezone,
            "date_order": date_order,
        },
        headers=browser_headers(csrf),
    )


def _zip(entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue()


def test_ambiguous_export_asks_for_date_order_then_records_messages_with_locations(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    content = ANDROID_AMBIGUOUS.encode()
    response = _upload(client, authed, case["id"], content)
    assert response.status_code == 202, response.text
    body = response.json()
    evidence = body["evidence"]
    job = body["job"]
    assert evidence["acquisition_method"] == "authorized_import"
    assert evidence["import_origin"] == "Synthetic export written by the test suite"
    assert evidence["collection_metadata"] == {"import_format": "whatsapp_export_txt"}
    assert evidence["connector_id"] is None
    assert evidence["collection_mode"] is None
    assert job["status"] == "queued"
    assert job["options"] == {"date_order": "auto", "timezone": "Europe/Istanbul"}

    assert _run_queued(db_session_factory, settings) == ["needs_input"]
    waiting = client.get(f"/api/v1/cases/{case['id']}/processing-jobs/{job['id']}").json()
    assert waiting["status"] == "needs_input"
    assert waiting["needs_input"]["field"] == "date_order"
    assert waiting["needs_input"]["basis"] == "ambiguous"
    assert "03/04/2024" in waiting["needs_input"]["samples"]
    assert waiting["observation_count"] == 0
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Observation)) == 0

    answered = client.post(
        f"/api/v1/cases/{case['id']}/processing-jobs/{job['id']}/input",
        json={"date_order": "day_first"},
        headers=browser_headers(authed),
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "queued"
    assert _run_queued(db_session_factory, settings) == ["partial"]

    finished = client.get(f"/api/v1/cases/{case['id']}/processing-jobs/{job['id']}").json()
    result = finished["result"]
    assert finished["status"] == "partial"
    assert result["messages"] == 3
    assert result["system_events"] == 1
    assert result["date_order"]["basis"] == "explicit"
    assert result["attachments"]["missing"] == 1
    assert result["attachments"]["omitted_by_export"] == 1
    assert any("not in the import" in gap for gap in result["gaps"])
    assert finished["observation_count"] == 4
    assert finished["derived_evidence"] == []  # a .txt original is its own chat text

    with db_session_factory() as db:
        rows = list(
            db.scalars(
                select(Observation)
                .where(Observation.case_id == uuid.UUID(case["id"]))
                .order_by(Observation.idempotency_key)
            )
        )
        assert db.scalar(select(func.count()).select_from(Entity)) == 0
        assert db.scalar(select(func.count()).select_from(EntityIdentifier)) == 0
    text = content.decode()
    by_index = {row.payload["index"]: row for row in rows}
    system, turkish, phone_label, omitted = (by_index[i] for i in range(4))
    assert system.observation_type == "whatsapp_system_event"
    assert turkish.observation_type == "whatsapp_message"
    assert turkish.payload["sender_label"] == "Ayşe Yılmaz"
    assert "not a verified identity" in turkish.payload["sender_label_note"]
    assert turkish.payload["text"] == "Merhaba, toplantı saat kaçta?\nİkinci satır: çğıöşü ÇĞİÖŞÜ"
    assert (turkish.payload["line_start"], turkish.payload["line_end"]) == (2, 3)
    quoted = text[turkish.payload["char_start"] : turkish.payload["char_end"]]
    assert quoted.startswith("03/04/2024, 09:16 - Ayşe")
    assert quoted.endswith("ÇĞİÖŞÜ")
    assert turkish.payload["timestamp_text"] == "03/04/2024, 09:16"
    assert turkish.payload["local_time"] == "2024-04-03T09:16:00"
    assert turkish.event_time is not None
    assert turkish.event_time.isoformat() == "2024-04-03T06:16:00+00:00"
    assert turkish.evidence_id == uuid.UUID(evidence["id"])
    assert turkish.idempotency_key == f"whatsapp:{evidence['id']}:0000001"
    assert phone_label.payload["sender_label"] == "+1 202-555-0143"
    assert phone_label.payload["attachments"][0]["status"] == "missing"
    assert omitted.payload["attachments"][0]["status"] == "omitted_by_export"


def test_unknown_timezone_keeps_local_time_off_the_utc_timeline(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    response = _upload(client, authed, case["id"], ANDROID_ZIP_CHAT.encode(), timezone="unknown")
    assert response.status_code == 202, response.text
    assert _run_queued(db_session_factory, settings) == ["partial"]
    with db_session_factory() as db:
        rows = list(db.scalars(select(Observation)))
    assert rows
    assert all(row.event_time is None for row in rows)
    assert {row.payload["time_basis"] for row in rows} == {"local_time_timezone_unknown"}
    assert rows[0].payload["date_order_basis"] == "inferred"
    job = client.get(f"/api/v1/cases/{case['id']}/processing-jobs").json()["items"][0]
    assert any("not placed on the UTC timeline" in item for item in job["result"]["limitations"])


def test_zip_export_keeps_attachments_inert_and_reports_hostile_entries(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    link = zipfile.ZipInfo("link-to-secrets")
    link.external_attr = (0o120777 << 16) | 0x20
    archive = _zip(
        [
            ("WhatsApp Chat with Synthetic group.txt", ANDROID_ZIP_CHAT.encode()),
            ("IMG-20240413-WA0001.jpg", JPEG),
            ("../escape.txt", b"outside"),
            (link, b"/etc/passwd"),
            ("note<svg onload=alert(1)>.html", b"<script>alert(document.cookie)</script>"),
        ]
    )
    response = _upload(client, authed, case["id"], archive, filename="../../export.zip")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["evidence"]["kind"] == "archive"
    assert body["evidence"]["original_filename"] == "export.zip"
    assert body["filename_sanitized"] is True
    assert _run_queued(db_session_factory, settings) == ["partial"]

    job = client.get(f"/api/v1/cases/{case['id']}/processing-jobs/{body['job']['id']}").json()
    result = job["result"]
    skipped = {item["name"]: item["reason"] for item in result["skipped_archive_entries"]}
    assert skipped["../escape.txt"] == "unsafe_path"
    assert skipped["link-to-secrets"] == "symbolic_link"
    assert result["attachments"]["present"] == 1
    assert result["attachments"]["missing"] == 1  # rapor.pdf
    assert result["attachments"]["omitted_by_export"] == 1
    parts = {item["page_part"]: item for item in job["derived_evidence"]}
    assert set(parts) == {"chat_text", "attachment:0", "attachment:1"}

    # Nothing was written outside the case's evidence directory.
    assert not (settings.evidence_storage_path / "escape.txt").exists()
    assert not any(settings.evidence_storage_path.parent.glob("escape.txt"))

    hostile = next(
        item for item in job["derived_evidence"] if item["title"].startswith("Attachment: note")
    )
    assert hostile["kind"] == "binary"
    assert hostile["content_type"] == "application/octet-stream"
    preview = client.get(f"/api/v1/cases/{case['id']}/evidence/{hostile['id']}/preview").json()
    assert preview["previewable"] is False
    assert preview["text"] == ""
    download = client.get(f"/api/v1/cases/{case['id']}/evidence/{hostile['id']}/content")
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert download.headers["content-disposition"].startswith("attachment;")

    chat = parts["chat_text"]
    with db_session_factory() as db:
        chat_row = db.get(EvidenceObject, uuid.UUID(chat["id"]))
        assert chat_row is not None
        assert chat_row.derived_from_evidence_id == uuid.UUID(body["evidence"]["id"])
        assert chat_row.acquisition_method == "authorized_import"
        observations = list(
            db.scalars(select(Observation).where(Observation.evidence_id == chat_row.id))
        )
        photo = next(o for o in observations if o.payload["line_start"] == 2)
    assert photo.payload["attachments"][0]["status"] == "present"
    assert photo.payload["attachments"][0]["evidence_id"] == parts["attachment:0"]["id"]
    assert photo.payload["text"].endswith("Fotoğraf ektedir")


def test_repeated_delivery_and_reprocessing_do_not_duplicate_records(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    archive = _zip(
        [
            ("_chat.txt", ANDROID_ZIP_CHAT.encode()),
            ("IMG-20240413-WA0001.jpg", JPEG),
        ]
    )
    body = _upload(client, authed, case["id"], archive).json()
    job_id = uuid.UUID(body["job"]["id"])
    ctx = _context(db_session_factory, settings)
    assert execute_job(ctx, job_id) == "partial"
    assert execute_job(ctx, job_id) == "skipped"  # a second delivery of the same message

    def counts() -> tuple[int, int, int]:
        with db_session_factory() as db:
            return (
                db.scalar(select(func.count()).select_from(EvidenceObject)) or 0,
                db.scalar(select(func.count()).select_from(Observation)) or 0,
                len([path for path in settings.evidence_storage_path.rglob("*") if path.is_file()]),
            )

    first = counts()
    assert first[:2] == (3, 4)

    again = client.post(
        f"/api/v1/cases/{case['id']}/evidence/{body['evidence']['id']}/processing",
        json={"job_type": "whatsapp_export", "date_order": "day_first", "timezone": "UTC"},
        headers=browser_headers(authed),
    )
    assert again.status_code == 202, again.text
    duplicate = client.post(
        f"/api/v1/cases/{case['id']}/evidence/{body['evidence']['id']}/processing",
        json={"job_type": "whatsapp_export", "date_order": "day_first", "timezone": "UTC"},
        headers=browser_headers(authed),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "processing_already_active"
    assert _run_queued(db_session_factory, settings) == ["partial"]
    assert counts() == first  # earlier derived records and files were replaced, not added
    with db_session_factory() as db:
        times = {o.payload["timezone"] for o in db.scalars(select(Observation))}
    assert times == {"UTC"}


def test_upload_checks_refuse_unusable_files_without_creating_records(
    client: TestClient,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    cases: list[tuple[bytes, dict[str, str], int, str]] = [
        (b"just some notes\n", {}, 422, "not_a_whatsapp_export"),
        (b"\xff\xfe binary", {}, 422, "invalid_encoding"),
        (ANDROID_AMBIGUOUS.encode(), {"timezone": "Mars/Olympus"}, 422, "invalid_timezone"),
        (_zip([("readme.md", b"x")]), {}, 422, "chat_text_not_found"),
        (b"PK\x03\x04 truncated", {}, 422, "invalid_archive"),
    ]
    for content, overrides, status, code in cases:
        response = _upload(client, authed, case["id"], content, **overrides)
        assert response.status_code == status, (code, response.text)
        assert response.json()["detail"]["code"] == code
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(EvidenceObject)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0

    generic = client.post(
        f"/api/v1/cases/{case['id']}/evidence/imports",
        files={"file": ("chat.zip", b"PK\x03\x04", "application/zip")},
        data={"kind": "archive", "import_origin": "Synthetic test fixture"},
        headers=browser_headers(authed),
    )
    assert generic.status_code == 422
    assert generic.json()["detail"]["code"] == "unsupported_kind"


def test_cancel_waiting_job_and_non_members_cannot_see_imports(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    body = _upload(client, authed, case["id"], ANDROID_AMBIGUOUS.encode()).json()
    assert _run_queued(db_session_factory, settings) == ["needs_input"]
    job_url = f"/api/v1/cases/{case['id']}/processing-jobs/{body['job']['id']}"

    create_second_user(db_session_factory)
    client.post("/api/v1/auth/logout", headers=browser_headers(authed))
    outsider = login_as(client, SECOND_USERNAME, SECOND_PASSWORD)
    evidence_url = f"/api/v1/cases/{case['id']}/evidence/{body['evidence']['id']}"
    for method, url, kwargs in (
        ("get", f"/api/v1/cases/{case['id']}/processing-jobs", {}),
        ("get", job_url, {}),
        ("post", f"{job_url}/cancel", {}),
        ("post", f"{job_url}/input", {"json": {"date_order": "day_first"}}),
        ("get", f"{evidence_url}/content", {}),
        (
            "post",
            f"{evidence_url}/processing",
            {"json": {"job_type": "whatsapp_export", "timezone": "UTC"}},
        ),
    ):
        response = getattr(client, method)(url, headers=browser_headers(outsider), **kwargs)
        assert response.status_code == 404, (url, response.text)
    upload = _upload(client, outsider, case["id"], ANDROID_AMBIGUOUS.encode())
    assert upload.status_code == 404

    client.post("/api/v1/auth/logout", headers=browser_headers(outsider))
    from tests.conftest import login

    owner = login(client)
    canceled = client.post(f"{job_url}/cancel", headers=browser_headers(owner))
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"
    again = client.post(f"{job_url}/cancel", headers=browser_headers(owner))
    assert again.status_code == 409
    assert _run_queued(db_session_factory, settings) == []


def test_deleting_the_original_removes_derived_records_and_files(
    client: TestClient,
    settings: Settings,
    authed: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    case = create_case(client, authed)
    archive = _zip([("_chat.txt", ANDROID_ZIP_CHAT.encode()), ("IMG-20240413-WA0001.jpg", JPEG)])
    body = _upload(client, authed, case["id"], archive, filename="export.zip").json()
    assert _run_queued(db_session_factory, settings) == ["partial"]
    case_dir = settings.evidence_storage_path / "cases" / case["id"] / "evidence"
    assert len(list(case_dir.iterdir())) == 3

    deleted = client.post(
        f"/api/v1/cases/{case['id']}/evidence/{body['evidence']['id']}/deletion",
        json={"confirm_title": body["evidence"]["title"]},
        headers=browser_headers(authed),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["removed_derived_records"] == 2
    assert list(case_dir.iterdir()) == []
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(EvidenceObject)) == 0
        assert db.scalar(select(func.count()).select_from(Observation)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0
