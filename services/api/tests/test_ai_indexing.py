"""Indexing lifecycle, retrieval scoping and derived-data deletion (PostgreSQL with pgvector)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai.indexing import IndexContext, cancel_indexing, index_case, index_counts, request_reindex
from app.ai.models import (
    AiMode,
    ChunkEmbedding,
    DocumentChunk,
    EmbeddingProfile,
    EvidenceIndexState,
    IndexStatus,
)
from app.ai.providers.base import ProviderError
from app.ai.retrieval import retrieve
from app.cases.deletion import count_case_rows
from app.config import Settings
from app.db.base import utcnow
from app.dispatch.models import AggregateType, DispatchOutbox, OutboxStatus
from app.dispatch.service import schedule_index_work
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from tests.ai_helpers import FailingEmbeddings, fixture_providers, run_indexing
from tests.conftest import browser_headers, create_case, import_file, make_settings

pytestmark = pytest.mark.integration

REGISTRY = (
    "Kayıt özeti: ornek.example alan adı Örnek A.Ş. tarafından 2026-09-01 tarihinde tescil "
    "edildi.\n\n"
    "Teknik iletişim adresi bilgi@ornek.example olarak görünüyor. Sunucu 203.0.113.7 adresinde."
)
FORUM = (
    "Forum post by @sule_yilmaz mentions a mirror at mirror.ornek.example and hash "
    + "b" * 64
    + "."
)


def _states(factory: sessionmaker[Session], case_id: str) -> dict[str, str]:
    with factory() as db:
        return {
            str(evidence_id): status
            for evidence_id, status in db.execute(
                select(EvidenceIndexState.evidence_id, EvidenceIndexState.status).where(
                    EvidenceIndexState.case_id == uuid.UUID(case_id)
                )
            )
        }


def test_import_records_pending_state_and_schedules_indexing(
    client: TestClient, authed: str, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    status, body = import_file(client, authed, case["id"], REGISTRY.encode())
    assert status == 201
    evidence_id = body["evidence"]["id"]
    assert _states(db_session_factory, case["id"]) == {evidence_id: "pending"}
    with db_session_factory() as db:
        outbox = db.scalar(
            select(DispatchOutbox).where(
                DispatchOutbox.aggregate_type == AggregateType.CASE_INDEX,
                DispatchOutbox.aggregate_id == uuid.UUID(case["id"]),
            )
        )
    assert outbox is not None
    assert outbox.task_name == "tracehollow.ai.index_case"
    detail = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}").json()
    assert detail["index"]["status"] == "pending"


def test_indexing_stores_exact_chunks_and_vectors_idempotently(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    _, text_body = import_file(client, authed, case["id"], REGISTRY.encode())
    document = {
        "registrant": {"name": "Örnek A.Ş.", "email": "bilgi@ornek.example"},
        "created": "2026-09-01",
    }
    _, json_body = import_file(
        client,
        authed,
        case["id"],
        json.dumps(document, ensure_ascii=False).encode(),
        kind="json",
        filename="whois.json",
    )

    assert run_indexing(settings, db_session_factory, case["id"]) == ["done"]
    assert set(_states(db_session_factory, case["id"]).values()) == {"indexed"}

    with db_session_factory() as db:
        chunks = list(
            db.scalars(select(DocumentChunk).where(DocumentChunk.case_id == uuid.UUID(case["id"])))
        )
        text_chunks = [
            chunk for chunk in chunks if str(chunk.evidence_id) == text_body["evidence"]["id"]
        ]
        json_chunks = [
            chunk for chunk in chunks if str(chunk.evidence_id) == json_body["evidence"]["id"]
        ]
        assert text_chunks
        assert json_chunks
        for chunk in text_chunks:
            assert REGISTRY[chunk.char_start : chunk.char_end] == chunk.text
            assert chunk.evidence_sha256 == text_body["evidence"]["sha256"]
        assert "domain:ornek.example" in text_chunks[0].identifiers
        assert any(
            location["pointer"] == "/registrant/name"
            for location in json_chunks[0].json_locations or []
        )
        profile = db.scalar(select(EmbeddingProfile).where(EmbeddingProfile.active.is_(True)))
        assert profile is not None
        assert profile.provider == "synthetic_fixture"
        assert profile.dimensions == 256
        embeddings = db.scalar(
            select(func.count())
            .select_from(ChunkEmbedding)
            .where(ChunkEmbedding.case_id == uuid.UUID(case["id"]))
        )
        assert embeddings == len(chunks)
        chunk_ids = {chunk.id for chunk in chunks}

    # Duplicate delivery and an explicit rebuild never duplicate chunks.
    assert run_indexing(settings, db_session_factory, case["id"]) == ["done"]
    with db_session_factory() as db:
        request_reindex(db, uuid.UUID(case["id"]), scope="all")
        db.commit()
    run_indexing(settings, db_session_factory, case["id"])
    with db_session_factory() as db:
        after = list(
            db.scalars(
                select(DocumentChunk.id).where(DocumentChunk.case_id == uuid.UUID(case["id"]))
            )
        )
        assert len(after) == len(chunk_ids)
        assert set(after).isdisjoint(chunk_ids)  # replaced, not appended
        assert db.scalar(select(func.count()).select_from(ChunkEmbedding)) == len(after)


def test_cancel_retry_and_failure_states_are_honest(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    case_id = uuid.UUID(case["id"])
    import_file(client, authed, case["id"], REGISTRY.encode())

    with db_session_factory() as db:
        assert cancel_indexing(db, case_id) == 1
        db.commit()
    assert run_indexing(settings, db_session_factory, case["id"]) == ["done"]
    assert set(_states(db_session_factory, case["id"]).values()) == {"canceled"}

    with db_session_factory() as db:
        request_reindex(db, case_id, scope="failed")
        db.commit()
    unavailable = FailingEmbeddings(ProviderError("model_unavailable", "down", retryable=True))
    run_indexing(
        settings, db_session_factory, case["id"], fixture_providers(embeddings=unavailable)
    )
    with db_session_factory() as db:
        state = db.scalar(select(EvidenceIndexState).where(EvidenceIndexState.case_id == case_id))
        assert state is not None
        assert (state.status, state.error_code, state.attempts) == (
            "pending",
            "model_unavailable",
            1,
        )
        assert state.available_at > utcnow() + timedelta(seconds=20)
        # Exhaust the retry budget.
        db.execute(
            update(EvidenceIndexState).values(
                available_at=utcnow(), attempts=settings.ai_index_max_attempts - 1
            )
        )
        db.commit()
    run_indexing(
        settings, db_session_factory, case["id"], fixture_providers(embeddings=unavailable)
    )
    with db_session_factory() as db:
        state = db.scalar(select(EvidenceIndexState).where(EvidenceIndexState.case_id == case_id))
        assert state is not None
        assert (state.status, state.error_code) == (
            "failed",
            "model_unavailable",
        )

    listing = client.get(f"/api/v1/cases/{case['id']}/ai/index?status=failed").json()
    assert listing["total"] == 1
    assert listing["items"][0]["error_code"] == "model_unavailable"
    rebuilt = client.post(
        f"/api/v1/cases/{case['id']}/ai/index/rebuild",
        json={"scope": "failed"},
        headers=browser_headers(authed),
    )
    assert rebuilt.status_code == 200
    assert rebuilt.json()["index"]["pending"] == 1
    run_indexing(settings, db_session_factory, case["id"])
    assert set(_states(db_session_factory, case["id"]).values()) == {"indexed"}


def test_missing_embedding_model_and_integrity_failures_fail_with_actionable_codes(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    _, body = import_file(client, authed, case["id"], REGISTRY.encode())
    missing = FailingEmbeddings(ProviderError("unused", "unused"))
    missing.model = "qwen3-embedding:0.6b"  # inventory lists a different model

    def inventory_without_model() -> Any:
        from app.ai.providers.base import ModelInventory

        return ModelInventory(reachable=True, models={"qwen3:8b": "digest"})

    missing.inventory = inventory_without_model  # type: ignore[method-assign]
    run_indexing(settings, db_session_factory, case["id"], fixture_providers(embeddings=missing))
    item = client.get(f"/api/v1/cases/{case['id']}/ai/index").json()["items"][0]
    assert item["status"] == "failed"
    assert item["error_code"] == "model_not_found"
    assert "ollama pull qwen3-embedding:0.6b" in item["error_detail"]
    assert missing.calls == 0

    # Tampered original: the index never trusts altered bytes.
    storage = Path(settings.evidence_storage_path)
    with db_session_factory() as db:
        evidence = db.get(EvidenceObject, uuid.UUID(body["evidence"]["id"]))
        assert evidence is not None
        (storage / evidence.storage_key).write_bytes(b"tampered")
        request_reindex(db, uuid.UUID(case["id"]), scope="failed")
        db.commit()
    run_indexing(settings, db_session_factory, case["id"])
    item = client.get(f"/api/v1/cases/{case['id']}/ai/index").json()["items"][0]
    assert (item["status"], item["error_code"]) == ("failed", "evidence_size_mismatch")


def test_changed_embedding_model_marks_index_stale_and_keeps_vectors_apart(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    case_id = uuid.UUID(case["id"])
    import_file(client, authed, case["id"], REGISTRY.encode())
    run_indexing(settings, db_session_factory, case["id"])

    class OtherModel:
        name = "synthetic_fixture"
        location = fixture_providers().embeddings.location
        model = "synthetic-hash-embedding-v2"
        synthetic = True

        def embed(self, texts: list[str], *, purpose: str) -> Any:
            from app.ai.providers.base import EmbeddingResult, Usage

            return EmbeddingResult(
                vectors=[[1.0, 0.0, 0.0] for _ in texts], usage=Usage(), model=self.model
            )

        def inventory(self) -> Any:
            from app.ai.providers.base import ModelInventory

            return ModelInventory(reachable=True, models={self.model: "v2"})

    _, second = import_file(client, authed, case["id"], FORUM.encode())
    run_indexing(
        settings, db_session_factory, case["id"], fixture_providers(embeddings=OtherModel())
    )
    with db_session_factory() as db:
        counts = index_counts(db, case_id)
        assert (counts["indexed"], counts["stale"]) == (1, 1)
        profiles = list(db.scalars(select(EmbeddingProfile).order_by(EmbeddingProfile.created_at)))
        assert [(profile.model, profile.dimensions, profile.active) for profile in profiles] == [
            ("synthetic-hash-embedding-v1", 256, False),
            ("synthetic-hash-embedding-v2", 3, True),
        ]
        active = profiles[1]
        # Semantic retrieval only compares vectors of the active profile.
        result = retrieve(
            db, case_id, "Örnek A.Ş.", top_k=5, query_vector=[1.0, 0.0, 0.0], profile_id=active.id
        )
        semantic_evidence = {
            chunk.evidence_id for chunk in result.chunks if "semantic" in chunk.ranks
        }
        assert semantic_evidence == {uuid.UUID(second["evidence"]["id"])}
        # Lexical retrieval still finds the stale record (same chunking version).
        assert any(
            "lexical" in chunk.ranks
            for chunk in result.chunks
            if str(chunk.evidence_id) != second["evidence"]["id"]
        )
        request_reindex(db, case_id, scope="stale")
        db.commit()
    run_indexing(
        settings, db_session_factory, case["id"], fixture_providers(embeddings=OtherModel())
    )
    with db_session_factory() as db:
        assert index_counts(db, case_id)["stale"] == 0


def test_retrieval_is_case_scoped_turkish_aware_and_identifier_exact(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    first = create_case(client, authed, title="Case A")
    second = create_case(client, authed, title="Case B")
    for case in (first, second):
        import_file(client, authed, case["id"], REGISTRY.encode())
    import_file(client, authed, first["id"], FORUM.encode())
    _, pending = import_file(client, authed, first["id"], b"Unindexed note about ornek.example")
    for case in (first, second):
        run_indexing(settings, db_session_factory, case["id"])
    with db_session_factory() as db:
        db.execute(
            update(EvidenceIndexState)
            .where(EvidenceIndexState.evidence_id == uuid.UUID(pending["evidence"]["id"]))
            .values(status=IndexStatus.FAILED)
        )
        db.commit()
        first_id, second_id = uuid.UUID(first["id"]), uuid.UUID(second["id"])
        own_evidence = set(
            db.scalars(select(EvidenceObject.id).where(EvidenceObject.case_id == first_id))
        )

        folded = retrieve(db, first_id, "ornek a.s. tescil tarihi", top_k=10)
        assert folded.chunks
        assert {chunk.evidence_id for chunk in folded.chunks} <= own_evidence
        assert uuid.UUID(pending["evidence"]["id"]) not in {
            chunk.evidence_id for chunk in folded.chunks
        }

        by_hash = retrieve(db, first_id, "hash " + "B" * 64, top_k=3)
        assert by_hash.query_identifiers == ["hash:" + "b" * 64]
        assert by_hash.chunks[0].ranks.get("identifier") == 1
        assert "@sule_yilmaz" in by_hash.chunks[0].text

        by_handle = retrieve(db, first_id, "who is @Sule_Yilmaz?", top_k=3)
        assert "username:sule_yilmaz" in by_handle.query_identifiers
        assert by_handle.chunks[0].ranks.get("identifier") == 1

        other = retrieve(db, second_id, "@sule_yilmaz mirror", top_k=10)
        assert all(chunk.evidence_id not in own_evidence for chunk in other.chunks)
        assert not any("sule_yilmaz" in chunk.text for chunk in other.chunks)

    # The keyword search endpoint applies the same scoping and hides other cases entirely.
    hits = client.get(
        f"/api/v1/cases/{first['id']}/ai/search", params={"q": "Sule Yilmaz mirror"}
    ).json()["hits"]
    assert hits
    assert all(hit["evidence_id"] in {str(e) for e in own_evidence} for hit in hits)
    assert any("identifier" in hit["matched_by"] or "lexical" in hit["matched_by"] for hit in hits)


def test_ai_disabled_or_case_disabled_schedules_nothing_and_workspace_still_works(
    client: TestClient,
    authed: str,
    services: Any,
    migrated_database: Any,
    tmp_path: Path,
    db_session_factory: sessionmaker[Session],
) -> None:
    disabled = make_settings(services, migrated_database.name, tmp_path, ai_enabled=False)
    from app.main import create_app

    with TestClient(create_app(disabled), base_url="http://localhost") as off:
        csrf = off.post(
            "/api/v1/auth/login",
            json={"username": "analyst.admin", "password": "correct horse battery staple"},
            headers=browser_headers(),
        ).json()["csrf_token"]
        case = create_case(off, csrf)
        status, body = import_file(off, csrf, case["id"], REGISTRY.encode())
        assert status == 201
        with db_session_factory() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(DispatchOutbox)
                    .where(DispatchOutbox.aggregate_type == AggregateType.CASE_INDEX)
                )
                == 0
            )
        assert _states(db_session_factory, case["id"]) == {body["evidence"]["id"]: "pending"}
        assert off.get(f"/api/v1/cases/{case['id']}/ai").json()["enabled"] is False
        assert (
            off.get(f"/api/v1/cases/{case['id']}/ai/search", params={"q": "ornek"}).status_code
            == 409
        )
        assert (
            off.post(
                f"/api/v1/cases/{case['id']}/ai/conversations",
                json={},
                headers=browser_headers(csrf),
            ).status_code
            == 409
        )
        # Core workspace keeps working.
        assert (
            off.get(
                f"/api/v1/cases/{case['id']}/evidence/{body['evidence']['id']}/preview"
            ).status_code
            == 200
        )
        assert off.get(f"/api/v1/cases/{case['id']}/exports/json").status_code == 200
        assert (
            off.post(
                f"/api/v1/cases/{case['id']}/entities",
                json={"entity_type": "domain", "display_name": "ornek.example"},
                headers=browser_headers(csrf),
            ).status_code
            == 201
        )
    assert run_indexing(disabled, db_session_factory, case["id"]) == ["skipped"]
    assert schedule_index_work(db_session_factory, disabled) == 0

    # Re-enabled installation: the dispatcher finds the pending evidence without re-import.
    with db_session_factory() as db:
        db.execute(update(EvidenceIndexState).values(available_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    enabled = make_settings(services, migrated_database.name, tmp_path)
    assert schedule_index_work(db_session_factory, enabled) == 1
    assert run_indexing(enabled, db_session_factory, case["id"]) == ["done"]

    # A case switched to "disabled" is skipped by workers even with queued index work.
    response = client.patch(
        f"/api/v1/cases/{case['id']}/ai/settings",
        json={"mode": "disabled"},
        headers=browser_headers(authed),
    )
    assert response.status_code == 200
    assert response.json()["mode"] == AiMode.DISABLED
    with db_session_factory() as db:
        request_reindex(db, uuid.UUID(case["id"]), scope="all")
        db.commit()
    assert run_indexing(enabled, db_session_factory, case["id"]) == ["skipped"]
    assert set(_states(db_session_factory, case["id"]).values()) == {"pending"}


def test_evidence_and_case_deletion_remove_derived_retrieval_data(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="Deletion case")
    case_id = uuid.UUID(case["id"])
    _, kept = import_file(client, authed, case["id"], FORUM.encode())
    _, doomed = import_file(client, authed, case["id"], REGISTRY.encode(), title="Registry extract")
    run_indexing(settings, db_session_factory, case["id"])
    doomed_id = uuid.UUID(doomed["evidence"]["id"])

    wrong = client.post(
        f"/api/v1/cases/{case['id']}/evidence/{doomed_id}/deletion",
        json={"confirm_title": "nope"},
        headers=browser_headers(authed),
    )
    assert wrong.status_code == 422
    response = client.post(
        f"/api/v1/cases/{case['id']}/evidence/{doomed_id}/deletion",
        json={"confirm_title": "Registry extract"},
        headers=browser_headers(authed),
    )
    assert response.status_code == 200, response.text
    assert response.json()["removed_chunks"] >= 1
    with db_session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.evidence_id == doomed_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(EvidenceIndexState)
                .where(EvidenceIndexState.evidence_id == doomed_id)
            )
            == 0
        )
        leftover = db.execute(
            text(
                "SELECT count(*) FROM chunk_embeddings e "
                "LEFT JOIN document_chunks c ON c.id = e.chunk_id WHERE c.id IS NULL"
            )
        ).scalar()
        assert leftover == 0
        assert retrieve(db, case_id, "Örnek A.Ş. tescil", top_k=10).chunks == [] or all(
            chunk.evidence_id != doomed_id
            for chunk in retrieve(db, case_id, "Örnek A.Ş. tescil", top_k=10).chunks
        )
    assert not (
        Path(settings.evidence_storage_path) / f"cases/{case_id}/evidence/{doomed_id}"
    ).exists()
    assert client.get(f"/api/v1/cases/{case['id']}/evidence/{doomed_id}").status_code == 404
    assert (
        client.get(f"/api/v1/cases/{case['id']}/evidence/{kept['evidence']['id']}").status_code
        == 200
    )

    # Case deletion removes every remaining derived row (verified by the deletion job too).
    from app.cases.deletion import DeletionContext, execute_deletion

    job = client.post(
        f"/api/v1/cases/{case['id']}/deletion",
        json={"confirm_title": "Deletion case"},
        headers=browser_headers(authed),
    ).json()
    context = DeletionContext(
        session_factory=db_session_factory,
        storage=EvidenceStorage(Path(settings.evidence_storage_path)),
        settings=settings,
        worker_name="test",
    )
    assert execute_deletion(context, uuid.UUID(job["id"])) == "completed"
    with db_session_factory() as db:
        remaining = {name: count for name, count in count_case_rows(db, case_id).items() if count}
        assert remaining == {}
        assert (
            db.scalar(
                select(func.count())
                .select_from(DispatchOutbox)
                .where(
                    DispatchOutbox.case_id == case_id, DispatchOutbox.status != OutboxStatus.DONE
                )
            )
            == 0
        )


def test_worker_that_loses_the_case_mid_index_writes_nothing(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    import_file(client, authed, case["id"], REGISTRY.encode())

    def archive_then_delete(_: uuid.UUID) -> None:
        with db_session_factory() as db:
            db.execute(
                text("UPDATE cases SET status = 'deleting' WHERE id = :id"), {"id": case["id"]}
            )
            db.commit()

    context = IndexContext(
        session_factory=db_session_factory,
        storage=EvidenceStorage(Path(settings.evidence_storage_path)),
        settings=settings,
        providers=fixture_providers(),
        before_write=archive_then_delete,
    )
    index_case(context, uuid.UUID(case["id"]))
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    assert set(_states(db_session_factory, case["id"]).values()) == {"canceled"}
