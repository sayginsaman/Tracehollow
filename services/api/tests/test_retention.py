"""Retention: preview, confirmed activation, durable cleanup, tombstones and races with work."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import AiCitation
from app.audit.models import AuditEvent
from app.cases.deletion import DeletionContext, count_case_rows, execute_deletion
from app.config import Settings
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.queries.models import QueryRun
from app.retention.models import RetentionJob, RetentionTombstone
from app.retention.service import RetentionContext, apply_job
from tests.ai_helpers import run_ai, run_indexing
from tests.collection_helpers import Router, respond
from tests.conftest import browser_headers, create_case, import_file
from tests.monitoring_helpers import (
    create_monitor,
    detect_changes,
    execute,
    later,
    monitor_runs,
    run_scheduler,
    saved_query,
)
from tests.test_team_access import ANALYST, VIEWER, _team, add_member, signed_in

pytestmark = pytest.mark.integration

PAGE = "https://ornek.example/duyurular"


def _html(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><p>{body}</p></body></html>"


def _context(settings: Settings, factory: sessionmaker[Session]) -> RetentionContext:
    return RetentionContext(
        session_factory=factory,
        storage=EvidenceStorage(settings.evidence_storage_path),
        settings=settings,
        worker_name="test-worker",
    )


def _age_runs(factory: sessionmaker[Session], run_ids: list[Any], days: int) -> None:
    with factory() as db:
        db.execute(
            text(
                "UPDATE query_runs SET finished_at = now() - make_interval(days => :days), "
                "queued_at = queued_at - make_interval(days => :days) WHERE id = ANY(:ids)"
            ),
            {"days": days, "ids": [uuid.UUID(str(run_id)) for run_id in run_ids]},
        )
        db.commit()


def _collect_three_times(
    client: TestClient,
    csrf: str,
    settings: Settings,
    factory: sessionmaker[Session],
    case_id: object,
) -> list[str]:
    query = saved_query(
        client,
        csrf,
        case_id,
        connector="public_web.page",
        input_type="url",
        value=PAGE,
        limits={"max_pages": 1, "max_items_per_page": 5},
    )
    runs = []
    for title, body in (
        ("Eski duyuru", "Eski duyuru: İzmir deposu 2025 yılında kapandı."),
        ("Duyuru", "Ankara ofisi açıldı."),
        ("Duyuru", "Ankara ofisi taşındı."),
    ):
        router = Router()
        router.add(PAGE, respond(200, body=_html(title, body)))
        started = client.post(
            f"/api/v1/cases/{case_id}/saved-queries/{query['id']}/runs",
            headers=browser_headers(csrf),
        ).json()
        execute(settings, factory, started["id"], router)
        detect_changes(settings, factory, started["id"])
        runs.append(started["id"])
    return runs


def test_retention_removes_expired_results_keeps_baselines_history_and_explains_references(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="Saklama vakası (synthetic)")
        add_member(analyst, csrf, case["id"], VIEWER[0], "viewer")
        runs = _collect_three_times(analyst, csrf, settings, db_session_factory, case["id"])
        _age_runs(db_session_factory, runs, 40)
        _, recent_import = import_file(analyst, csrf, case["id"], b"Yeni not", title="Recent note")
        run_indexing(settings, db_session_factory, case["id"])
        conversation = analyst.post(
            f"/api/v1/cases/{case['id']}/ai/conversations", json={}, headers=browser_headers(csrf)
        ).json()
        asked = analyst.post(
            f"/api/v1/cases/{case['id']}/ai/conversations/{conversation['id']}/questions",
            json={"question": "İzmir deposu ne zaman kapandı?"},
            headers=browser_headers(csrf),
        ).json()
        assert run_ai(settings, db_session_factory, asked["id"]) == "completed"
        with db_session_factory() as db:
            first_run_evidence = list(
                db.scalars(
                    select(EvidenceObject.id).where(
                        EvidenceObject.query_run_id == uuid.UUID(runs[0])
                    )
                )
            )
            cited = db.scalar(
                select(func.count())
                .select_from(AiCitation)
                .where(AiCitation.evidence_id.in_(first_run_evidence))
            )
        assert first_run_evidence
        # The question can only be answered from the first (to be expired) collection.
        assert cited
        base = f"/api/v1/cases/{case['id']}/retention"
        assert analyst.get(base).json()["active"] is False

        preview = analyst.post(
            f"{base}/preview",
            json={"collected_results_max_age_days": 30},
            headers=browser_headers(csrf),
        ).json()
        assert preview["executions"] == 2
        assert preview["protected_baselines"] == 1
        assert preview["evidence_records"] == 4  # snapshot and derived text of two executions
        assert preview["observations"] == 2
        assert preview["stored_bytes"] > 0
        assert preview["ai_citations_affected"] == cited
        with db_session_factory() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(EvidenceObject)
                    .where(EvidenceObject.case_id == uuid.UUID(case["id"]))
                )
                == 7
            )

        wrong = analyst.put(
            base,
            json={"collected_results_max_age_days": 30, "confirm_title": "yanlış"},
            headers=browser_headers(csrf),
        )
        assert wrong.status_code == 422
    with signed_in(settings, *VIEWER) as (viewer, viewer_csrf):
        assert viewer.get(base).status_code == 200
        refused = viewer.put(
            base,
            json={"collected_results_max_age_days": 30, "confirm_title": case["title"]},
            headers=browser_headers(viewer_csrf),
        )
        assert refused.status_code == 403
        assert (
            viewer.post(
                f"{base}/preview",
                json={"collected_results_max_age_days": 30},
                headers=browser_headers(viewer_csrf),
            ).status_code
            == 403
        )
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        activated = analyst.put(
            base,
            json={"collected_results_max_age_days": 30, "confirm_title": case["title"]},
            headers=browser_headers(csrf),
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["active"] is True
        assert activated.json()["version"] == 1
        with db_session_factory() as db:
            job = db.scalar(
                select(RetentionJob).where(RetentionJob.case_id == uuid.UUID(case["id"]))
            )
            assert job is not None
            assert job.trigger == "activation"
        assert apply_job(_context(settings, db_session_factory), job.id) == "completed"
        assert apply_job(_context(settings, db_session_factory), job.id) == "skipped"
        jobs = analyst.get(f"{base}/jobs").json()["items"]
        assert jobs[0]["removed"]["executions"] == 2
        assert jobs[0]["removed"]["evidence_records"] == 4

        with db_session_factory() as db:
            remaining = set(
                db.scalars(
                    select(EvidenceObject.query_run_id).where(
                        EvidenceObject.case_id == uuid.UUID(case["id"])
                    )
                )
            )
            expired = {
                row.id: row.results_expired_at
                for row in db.scalars(
                    select(QueryRun).where(QueryRun.case_id == uuid.UUID(case["id"]))
                )
            }
            tombstones = db.scalar(
                select(func.count())
                .select_from(RetentionTombstone)
                .where(RetentionTombstone.record_type == "evidence")
            )
        assert remaining == {uuid.UUID(runs[2]), None}  # the baseline and the recent import stay
        assert expired[uuid.UUID(runs[0])] is not None
        assert expired[uuid.UUID(runs[1])] is not None
        assert expired[uuid.UUID(runs[2])] is None
        assert tombstones == 4
        files = [
            p
            for p in settings.evidence_storage_path.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        ]
        assert len(files) == 3

        # History stays readable and says what happened.
        run_detail = analyst.get(f"/api/v1/cases/{case['id']}/runs/{runs[0]}")
        assert run_detail.status_code == 200
        gone = analyst.get(f"/api/v1/cases/{case['id']}/evidence/{first_run_evidence[0]}")
        assert gone.status_code == 410
        assert gone.json()["detail"]["code"] == "evidence_expired_by_retention"
        assert (
            analyst.get(
                f"/api/v1/cases/{case['id']}/evidence/{recent_import['evidence']['id']}"
            ).status_code
            == 200
        )
        change_sets = analyst.get(
            f"/api/v1/cases/{case['id']}/change-sets", params={"query_run_id": runs[1]}
        ).json()["items"]
        changed = analyst.get(
            f"/api/v1/cases/{case['id']}/change-sets/{change_sets[0]['id']}"
        ).json()
        event = changed["events"]["items"][0]
        assert event["previous_evidence_available"] is False
        assert event["current_evidence_available"] is False
        conversation_detail = analyst.get(
            f"/api/v1/cases/{case['id']}/ai/conversations/{conversation['id']}"
        ).json()
        answer = next(m for m in conversation_detail["messages"] if m["role"] == "assistant")
        statuses = set()
        for claim in answer["answer"]["claims"]:
            for citation in claim.get("citations", []):
                if citation.get("citation_id"):
                    detail = analyst.get(
                        f"/api/v1/cases/{case['id']}/ai/citations/{citation['citation_id']}"
                    ).json()
                    statuses.add(detail["passage"]["status"] if detail.get("passage") else None)
        assert "source_expired" in statuses

    with db_session_factory() as db:
        actions = set(
            db.scalars(select(AuditEvent.action).where(AuditEvent.case_id == uuid.UUID(case["id"])))
        )
    assert {"retention.policy_activated", "retention.applied"} <= actions


def test_retention_waits_for_active_work_and_never_touches_unfinished_runs(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Active work")
    runs = _collect_three_times(client, authed, settings, db_session_factory, case["id"])
    _age_runs(db_session_factory, runs, 90)
    with db_session_factory() as db:
        # An old execution another worker is still running.
        db.execute(
            text(
                "UPDATE query_runs SET status = 'running', "
                "lease_expires_at = now() + interval '5 minutes' WHERE id = :id"
            ),
            {"id": runs[0]},
        )
        db.execute(
            text(
                "INSERT INTO ai_runs (id, case_id, run_type, status, stage, policy_version, "
                "requested_location, lease_expires_at) VALUES (gen_random_uuid(), :case, "
                "'summary', 'running', 'retrieval', 1, 'local', now() + interval '5 minutes')"
            ),
            {"case": case["id"]},
        )
        db.commit()
    base = f"/api/v1/cases/{case['id']}/retention"
    activated = client.put(
        base,
        json={"collected_results_max_age_days": 30, "confirm_title": case["title"]},
        headers=browser_headers(authed),
    )
    assert activated.status_code == 200
    with db_session_factory() as db:
        job = db.scalar(select(RetentionJob).where(RetentionJob.case_id == uuid.UUID(case["id"])))
        assert job is not None
    assert apply_job(_context(settings, db_session_factory), job.id) == "waiting"
    with db_session_factory() as db:
        waiting = db.get(RetentionJob, job.id)
        assert waiting is not None
        assert waiting.status == "queued"
        assert waiting.deferred == {"ai_runs": 1}
        assert (
            db.scalar(
                select(func.count())
                .select_from(EvidenceObject)
                .where(EvidenceObject.case_id == uuid.UUID(case["id"]))
            )
            == 6
        )
        db.execute(
            text(
                "UPDATE ai_runs SET status = 'completed', lease_expires_at = NULL "
                "WHERE case_id = :case"
            ),
            {"case": case["id"]},
        )
        db.commit()
    assert apply_job(_context(settings, db_session_factory), job.id) == "completed"
    with db_session_factory() as db:
        kept = set(
            db.scalars(
                select(EvidenceObject.query_run_id).where(
                    EvidenceObject.case_id == uuid.UUID(case["id"])
                )
            )
        )
        running = db.get(QueryRun, uuid.UUID(runs[0]))
        assert running is not None
        assert running.results_expired_at is None
    # The running execution and the baseline keep their evidence; only the second one expired.
    assert kept == {uuid.UUID(runs[0]), uuid.UUID(runs[2])}


def test_monitor_and_import_rules_and_case_deletion_cover_every_new_record(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Monitor retention")
    query = saved_query(client, authed, case["id"])
    monitor = create_monitor(
        client, authed, case["id"], query["id"], enable=True, retention={"keep_last_runs": 2}
    )
    for step in range(4):
        assert (
            run_scheduler(settings, db_session_factory, now=later(61 + 60 * step)).dispatched == 1
        )
        run = monitor_runs(db_session_factory, monitor["id"])[-1]
        assert execute(settings, db_session_factory, run.id).status == "completed"
        detect_changes(settings, db_session_factory, run.id)
    chat = "03/04/2024, 09:16 - Ayşe: Merhaba\n14/04/2024, 09:17 - Can: Selam\n"
    uploaded = client.post(
        f"/api/v1/cases/{case['id']}/imports/whatsapp",
        files={"file": ("WhatsApp Chat.txt", chat.encode(), "application/octet-stream")},
        data={"import_origin": "Synthetic export", "timezone": "Europe/Istanbul"},
        headers=browser_headers(authed),
    )
    assert uploaded.status_code == 202
    with db_session_factory() as db:
        db.execute(
            text(
                "UPDATE evidence_objects SET collected_at = now() - interval '400 days' "
                "WHERE id = :id"
            ),
            {"id": uploaded.json()["evidence"]["id"]},
        )
        db.execute(text("UPDATE processing_jobs SET status = 'completed'"))
        db.commit()
    preview = client.post(
        f"/api/v1/cases/{case['id']}/retention/preview",
        json={"imported_evidence_max_age_days": 365},
        headers=browser_headers(authed),
    ).json()
    # Monitor rules apply with or without a case policy: 4 runs, keep 2 (newest is the baseline).
    assert preview["executions"] == 2
    assert preview["imported_originals"] == 1
    client.put(
        f"/api/v1/cases/{case['id']}/retention",
        json={"imported_evidence_max_age_days": 365, "confirm_title": case["title"]},
        headers=browser_headers(authed),
    )
    with db_session_factory() as db:
        job_id = db.scalar(
            select(RetentionJob.id).where(RetentionJob.case_id == uuid.UUID(case["id"]))
        )
    assert job_id is not None
    assert apply_job(_context(settings, db_session_factory), job_id) == "completed"
    with db_session_factory() as db:
        expired = [
            r.results_expired_at is not None
            for r in db.scalars(
                select(QueryRun)
                .where(QueryRun.monitor_id == uuid.UUID(monitor["id"]))
                .order_by(QueryRun.queued_at)
            )
        ]
        imports = db.scalar(
            select(func.count())
            .select_from(EvidenceObject)
            .where(EvidenceObject.acquisition_method == "authorized_import")
        )
    assert expired == [True, True, False, False]
    assert imports == 0

    stix_bundle = client.get(f"/api/v1/cases/{case['id']}/exports/stix").content
    assert (
        client.post(
            f"/api/v1/cases/{case['id']}/imports/stix",
            files={"file": ("b.json", stix_bundle, "application/json")},
            data={"import_origin": "Synthetic"},
            headers=browser_headers(authed),
        ).status_code
        == 201
    )
    deletion = client.post(
        f"/api/v1/cases/{case['id']}/deletion",
        json={"confirm_title": case["title"]},
        headers=browser_headers(authed),
    ).json()
    context = DeletionContext(
        db_session_factory, EvidenceStorage(settings.evidence_storage_path), settings, "test"
    )
    assert execute_deletion(context, uuid.UUID(deletion["id"])) == "completed"
    with db_session_factory() as db:
        remaining = {
            name: count
            for name, count in count_case_rows(db, uuid.UUID(case["id"])).items()
            if count
        }
        assert remaining == {}
        audit_count = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.case_id == uuid.UUID(case["id"]))
        )
        # The audit trail of a deleted case stays (it holds no case content).
        assert audit_count
