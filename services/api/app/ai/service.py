"""API-side AI operations. Model calls never happen here; they run in the AI worker."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.ai import indexing
from app.ai.chunking import decode_evidence_text, resolve_pointer
from app.ai.coverage import case_coverage
from app.ai.models import (
    TERMINAL_AI_RUN_STATUSES,
    AiCitation,
    AiConversation,
    AiMessage,
    AiMode,
    AiProviderStatus,
    AiRun,
    AiRunStatus,
    AiRunType,
    DocumentChunk,
    EvidenceIndexState,
    MessageKind,
    MessageRole,
    ProcessingLocation,
)
from app.ai.policy import local_location
from app.ai.retrieval import retrieve
from app.ai.schemas import (
    AiRunOut,
    AiStatusOut,
    CaseAiOut,
    CaseAiSettingsIn,
    CitationDetail,
    CitationOut,
    ConversationDetail,
    ConversationOut,
    EmbeddingProfileOut,
    IndexCounts,
    IndexItemOut,
    MessageOut,
    PassageOut,
    ProviderStatusOut,
    SearchHit,
    SearchOut,
)
from app.auth.models import User
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.db.base import utcnow
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType, DispatchOutbox
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.evidence.storage import EvidenceStorage, IntegrityError

CONTEXT_CHARS = 300
MAX_JSON_VALUE_CHARS = 4000


def _conflict(code: str, message: str | None = None) -> HTTPException:
    detail: Any = {"code": code, "message": message} if message else code
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


def require_ai_enabled(settings: Settings) -> None:
    if not settings.ai_enabled:
        raise _conflict("ai_disabled", "AI features are disabled for this installation.")


# -- status and settings -----------------------------------------------------------------------


def ai_status(db: Session, settings: Settings) -> AiStatusOut:
    checks = list(db.scalars(select(AiProviderStatus).order_by(AiProviderStatus.provider)))
    return AiStatusOut(
        enabled=settings.ai_enabled,
        local_provider=settings.ai_local_provider,
        local_location=local_location(settings).value,
        local_generation_model=(
            "synthetic-extractive-v1"
            if settings.ai_local_provider == "synthetic_fixture"
            else settings.ai_generation_model
        ),
        local_embedding_model=(
            "synthetic-hash-embedding-v1"
            if settings.ai_local_provider == "synthetic_fixture"
            else settings.ai_embedding_model
        ),
        synthetic=settings.ai_local_provider == "synthetic_fixture",
        cloud_provider=settings.ai_cloud_provider,
        cloud_model=settings.ai_cloud_model if settings.ai_cloud_provider != "none" else None,
        cloud_configured=settings.ai_cloud_configured,
        checks=[ProviderStatusOut.model_validate(check, from_attributes=True) for check in checks],
    )


def _active_runs(db: Session, case_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AiRun)
            .where(
                AiRun.case_id == case_id,
                AiRun.status.in_([AiRunStatus.QUEUED, AiRunStatus.RUNNING]),
            )
        )
        or 0
    )


def case_ai(db: Session, settings: Settings, case: Case) -> CaseAiOut:
    profile = indexing.active_profile(db)
    return CaseAiOut(
        enabled=settings.ai_enabled,
        mode=case.ai_mode,
        policy_version=case.ai_policy_version,
        local_location=local_location(settings).value,
        cloud_available=settings.ai_cloud_configured,
        index=IndexCounts(**indexing.index_counts(db, case.id)),
        embedding_profile=None
        if profile is None
        else EmbeddingProfileOut(
            provider=profile.provider,
            model=profile.model,
            dimensions=profile.dimensions,
            chunking_version=profile.chunking_version,
            indexing_version=profile.indexing_version,
            activated_at=profile.activated_at,
            synthetic=profile.provider == "synthetic_fixture",
        ),
        active_runs=_active_runs(db, case.id),
    )


def update_case_settings(
    db: Session, settings: Settings, case: Case, body: CaseAiSettingsIn
) -> tuple[Case, uuid.UUID | None]:
    require_ai_enabled(settings)
    if body.mode == AiMode.CLOUD_ALLOWED and not body.acknowledge_cloud_processing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "cloud_acknowledgement_required",
                "message": (
                    "Confirm that retrieved case excerpts may be sent to the cloud provider."
                ),
            },
        )
    locked = db.scalar(select(Case).where(Case.id == case.id).with_for_update())
    assert locked is not None
    outbox_id = None
    if locked.ai_mode != body.mode:
        previous = locked.ai_mode
        locked.ai_mode = body.mode
        locked.ai_policy_version += 1
        now = utcnow()
        # Queued work is stopped now; running work stops at its next policy check.
        db.execute(
            update(AiRun)
            .where(AiRun.case_id == case.id, AiRun.status == AiRunStatus.QUEUED)
            .values(
                status=AiRunStatus.CANCELED,
                finished_at=now,
                error_code="processing_policy_changed",
                error_detail="The case's AI processing setting changed before this request ran.",
            )
        )
        db.execute(
            update(AiRun)
            .where(AiRun.case_id == case.id, AiRun.status == AiRunStatus.RUNNING)
            .values(cancel_requested_at=func.coalesce(AiRun.cancel_requested_at, now))
        )
        if previous == AiMode.DISABLED and body.mode != AiMode.DISABLED:
            outbox_id = indexing.enqueue_case_index(db, case.id)
    db.flush()
    return locked, outbox_id


def index_items(
    db: Session, case_id: uuid.UUID, *, status_filter: str | None, limit: int, offset: int
) -> tuple[list[IndexItemOut], int]:
    profile = indexing.active_profile(db)
    derived = indexing.derived_status_expression(profile.id if profile else None).label("derived")
    base = (
        select(EvidenceIndexState, EvidenceObject.title, EvidenceObject.acquisition_method, derived)
        .join(EvidenceObject, EvidenceObject.id == EvidenceIndexState.evidence_id)
        .where(EvidenceIndexState.case_id == case_id, EvidenceObject.case_id == case_id)
    )
    if status_filter:
        base = base.where(derived == status_filter)
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = db.execute(
        base.order_by(EvidenceIndexState.queued_at.desc(), EvidenceIndexState.evidence_id)
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        IndexItemOut(
            evidence_id=state.evidence_id,
            title=title,
            acquisition_method=method,
            status=str(derived_status),
            attempts=state.attempts,
            chunk_count=state.chunk_count,
            error_code=state.error_code,
            error_detail=state.error_detail,
            queued_at=state.queued_at,
            indexed_at=state.indexed_at,
            available_at=state.available_at,
        )
        for state, title, method, derived_status in rows
    ]
    return items, total


# -- conversations and runs --------------------------------------------------------------------


def get_conversation(db: Session, case_id: uuid.UUID, conversation_id: uuid.UUID) -> AiConversation:
    conversation = db.scalar(
        select(AiConversation).where(
            AiConversation.id == conversation_id, AiConversation.case_id == case_id
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="conversation_not_found")
    return conversation


def conversation_out(db: Session, conversation: AiConversation) -> ConversationOut:
    count = db.scalar(
        select(func.count())
        .select_from(AiMessage)
        .where(AiMessage.conversation_id == conversation.id)
    )
    return ConversationOut(
        id=conversation.id,
        case_id=conversation.case_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=int(count or 0),
    )


def run_out(run: AiRun) -> AiRunOut:
    return AiRunOut(
        id=run.id,
        case_id=run.case_id,
        conversation_id=run.conversation_id,
        run_type=run.run_type,
        status=run.status,
        stage=run.stage,
        question=run.question,
        requested_location=run.requested_location,
        provider=run.provider,
        model=run.model,
        processing_location=run.processing_location,
        prompt_template_version=run.prompt_template_version,
        usage=run.usage or {},
        coverage=run.coverage or {},
        validation=run.validation or {},
        tool_calls=run.tool_calls or [],
        retrieval=run.retrieval or {},
        error_code=run.error_code,
        error_detail=run.error_detail,
        queued_at=run.queued_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        cancel_requested_at=run.cancel_requested_at,
        synthetic=run.processing_location == ProcessingLocation.FIXTURE,
    )


def messages_out(db: Session, case_id: uuid.UUID, messages: list[AiMessage]) -> list[MessageOut]:
    ids = [message.id for message in messages]
    citations: dict[uuid.UUID, list[CitationOut]] = {}
    if ids:
        rows = db.execute(
            select(AiCitation, EvidenceObject.title)
            .outerjoin(EvidenceObject, EvidenceObject.id == AiCitation.evidence_id)
            .where(AiCitation.case_id == case_id, AiCitation.message_id.in_(ids))
            .order_by(AiCitation.claim_index, AiCitation.label)
        ).all()
        for citation, title in rows:
            assert citation.message_id is not None
            citations.setdefault(citation.message_id, []).append(
                CitationOut(
                    id=citation.id,
                    label=citation.label,
                    ref_type=citation.ref_type,
                    evidence_id=citation.evidence_id,
                    evidence_title=title,
                    tool_name=citation.tool_name,
                    source_available=citation.ref_type == "tool"
                    or citation.evidence_id is not None,
                )
            )
    return [
        MessageOut(
            id=message.id,
            role=message.role,
            kind=message.kind,
            content=message.content,
            answer=message.answer,
            ai_run_id=message.ai_run_id,
            created_at=message.created_at,
            citations=citations.get(message.id, []),
        )
        for message in messages
    ]


def conversation_detail(db: Session, conversation: AiConversation) -> ConversationDetail:
    messages = list(
        db.scalars(
            select(AiMessage)
            .where(
                AiMessage.conversation_id == conversation.id,
                AiMessage.case_id == conversation.case_id,
            )
            .order_by(AiMessage.created_at, AiMessage.role.desc())
        )
    )
    runs = list(
        db.scalars(
            select(AiRun)
            .where(AiRun.conversation_id == conversation.id, AiRun.case_id == conversation.case_id)
            .order_by(AiRun.queued_at)
        )
    )
    return ConversationDetail(
        conversation=conversation_out(db, conversation),
        messages=messages_out(db, conversation.case_id, messages),
        runs=[run_out(run) for run in runs],
    )


def create_run(
    db: Session,
    settings: Settings,
    case: Case,
    user: User,
    *,
    run_type: AiRunType,
    location: str,
    question: str | None = None,
    conversation: AiConversation | None = None,
) -> tuple[AiRun, DispatchOutbox]:
    require_ai_enabled(settings)
    locked = db.scalar(select(Case).where(Case.id == case.id).with_for_update())
    assert locked is not None
    if locked.status != CaseStatus.ACTIVE:
        raise _conflict(
            "case_archived" if locked.status == CaseStatus.ARCHIVED else "case_deletion_in_progress"
        )
    if locked.ai_mode == AiMode.DISABLED:
        raise _conflict("case_ai_disabled", "AI processing is turned off for this case.")
    if location == ProcessingLocation.CLOUD:
        if locked.ai_mode != AiMode.CLOUD_ALLOWED:
            raise _conflict(
                "cloud_processing_not_allowed",
                "This case only allows local processing; nothing was sent to a cloud provider.",
            )
        if not settings.ai_cloud_configured:
            raise _conflict("cloud_not_configured", "No cloud provider is configured.")
    if _active_runs(db, case.id) >= settings.ai_max_active_runs_per_case:
        raise _conflict(
            "ai_run_limit_reached", "Wait for the current AI requests in this case to finish."
        )
    run = AiRun(
        id=uuid.uuid4(),
        case_id=case.id,
        conversation_id=conversation.id if conversation else None,
        run_type=run_type,
        status=AiRunStatus.QUEUED,
        stage="queued",
        requested_by_user_id=user.id,
        question=question,
        policy_version=locked.ai_policy_version,
        requested_location=location,
        queued_at=utcnow(),
    )
    db.add(run)
    db.flush()
    if question is not None and conversation is not None:
        db.add(
            AiMessage(
                case_id=case.id,
                conversation_id=conversation.id,
                ai_run_id=run.id,
                role=MessageRole.USER,
                kind=MessageKind.QUESTION,
                content=question,
            )
        )
        conversation.updated_at = utcnow()
    outbox = dispatch.enqueue(
        db,
        task_name=dispatch.EXECUTE_AI_RUN_TASK,
        aggregate_type=AggregateType.AI_RUN,
        aggregate_id=run.id,
        case_id=case.id,
    )
    db.flush()
    return run, outbox


def get_run(db: Session, case_id: uuid.UUID, run_id: uuid.UUID) -> AiRun:
    run = db.scalar(select(AiRun).where(AiRun.id == run_id, AiRun.case_id == case_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="ai_run_not_found")
    return run


def request_cancel(db: Session, run: AiRun) -> None:
    locked = db.scalar(select(AiRun).where(AiRun.id == run.id).with_for_update())
    assert locked is not None
    if locked.status in TERMINAL_AI_RUN_STATUSES:
        raise _conflict("ai_run_already_finished")
    now = utcnow()
    locked.cancel_requested_at = locked.cancel_requested_at or now
    if locked.status == AiRunStatus.QUEUED:
        locked.status = AiRunStatus.CANCELED
        locked.finished_at = now
        locked.error_code = "canceled"
        locked.error_detail = "Canceled by the analyst before it started."
        dispatch.mark_done(db, AggregateType.AI_RUN, locked.id)


# -- passages ----------------------------------------------------------------------------------


def _passage(
    storage: EvidenceStorage,
    evidence: EvidenceObject | None,
    *,
    expected_sha256: str | None,
    kind: str | None,
    char_start: int | None,
    char_end: int | None,
    json_pointer: str | None,
    chunk_text: str | None,
    quote: str | None,
) -> PassageOut:
    if evidence is None:
        return PassageOut(
            evidence_id=None,
            evidence_title=None,
            acquisition_method=None,
            synthetic=False,
            collected_at=None,
            source_published_at=None,
            source_published_at_original=None,
            source_reference=None,
            kind=kind,
            status="source_deleted",
            integrity=None,
        )
    out = PassageOut(
        evidence_id=evidence.id,
        evidence_title=evidence.title,
        acquisition_method=evidence.acquisition_method,
        synthetic=evidence.acquisition_method == AcquisitionMethod.SYNTHETIC_FIXTURE,
        collected_at=evidence.collected_at,
        source_published_at=evidence.source_published_at,
        source_published_at_original=evidence.source_published_at_original,
        source_reference=evidence.source_reference,
        kind=evidence.kind,
        status="available",
        integrity="verified",
        chunk_text=chunk_text,
        quote=quote,
        json_pointer=json_pointer,
        text_origin=(evidence.collection_metadata or {}).get("text_origin"),
        derived_from_evidence_id=evidence.derived_from_evidence_id,
    )
    if expected_sha256 is not None and expected_sha256 != evidence.sha256:
        out.status = "evidence_changed"
        return out
    try:
        content = storage.read_verified(evidence.storage_key, evidence.sha256, evidence.size_bytes)
    except IntegrityError as exc:
        out.status = "integrity_failed"
        out.integrity = exc.code
        out.chunk_text = None
        out.quote = None
        return out
    text = decode_evidence_text(content)
    if evidence.kind == "json":
        if json_pointer is not None:
            try:
                value = resolve_pointer(json.loads(text.removeprefix("﻿")), json_pointer)
                rendered = json.dumps(value, ensure_ascii=False, indent=2)
                out.json_value = rendered[:MAX_JSON_VALUE_CHARS]
            except (KeyError, json.JSONDecodeError):
                out.status = "location_not_found"
        return out
    if char_start is not None and char_end is not None and 0 <= char_start < char_end <= len(text):
        out.char_start, out.char_end = char_start, char_end
        out.before = text[max(0, char_start - CONTEXT_CHARS) : char_start]
        out.passage = text[char_start:char_end]
        out.after = text[char_end : char_end + CONTEXT_CHARS]
        out.line = text.count("\n", 0, char_start) + 1
        out.page = page_for_offset(evidence.collection_metadata, char_start)
    else:
        out.status = "location_not_found"
    return out


def page_for_offset(metadata: dict[str, Any] | None, offset: int) -> int | None:
    """The PDF page a character offset falls on, from a derived text record's page map."""
    page_map = (metadata or {}).get("page_map")
    if not isinstance(page_map, list):
        return None
    for entry in page_map:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("page"), int)
            and isinstance(entry.get("char_start"), int)
            and isinstance(entry.get("char_end"), int)
            # The "[Page N]" header just before the text counts as part of that page.
            and entry["char_start"] - len(f"[Page {entry['page']}]\n") <= offset < entry["char_end"]
        ):
            return int(entry["page"])
    return None


def citation_detail(
    db: Session, storage: EvidenceStorage, case_id: uuid.UUID, citation_id: uuid.UUID
) -> CitationDetail:
    citation = db.scalar(
        select(AiCitation).where(AiCitation.id == citation_id, AiCitation.case_id == case_id)
    )
    if citation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="citation_not_found")
    tool_result = None
    passage = None
    if citation.ref_type == "tool":
        run = db.scalar(
            select(AiRun).where(AiRun.id == citation.ai_run_id, AiRun.case_id == case_id)
        )
        for entry in (run.tool_calls if run else []) or []:
            if entry.get("ref") == citation.label:
                tool_result = {
                    "tool": entry.get("tool"),
                    "arguments": entry.get("arguments"),
                    "result": entry.get("result"),
                }
    else:
        evidence = (
            db.scalar(
                select(EvidenceObject).where(
                    EvidenceObject.id == citation.evidence_id, EvidenceObject.case_id == case_id
                )
            )
            if citation.evidence_id
            else None
        )
        chunk = (
            db.scalar(
                select(DocumentChunk).where(
                    DocumentChunk.id == citation.chunk_id, DocumentChunk.case_id == case_id
                )
            )
            if citation.chunk_id
            else None
        )
        passage = _passage(
            storage,
            evidence,
            expected_sha256=citation.evidence_sha256,
            kind=chunk.kind if chunk else None,
            char_start=citation.source_char_start,
            char_end=citation.source_char_end,
            json_pointer=citation.json_pointer,
            chunk_text=chunk.text if chunk else None,
            quote=citation.quote,
        )
        if evidence is not None and chunk is None and passage.status == "available":
            passage.status = "reindexed"
    return CitationDetail(
        id=citation.id,
        label=citation.label,
        ref_type=citation.ref_type,
        claim_index=citation.claim_index,
        ai_run_id=citation.ai_run_id,
        tool_name=citation.tool_name,
        tool_result=tool_result,
        passage=passage,
    )


def chunk_passage(
    db: Session, storage: EvidenceStorage, case_id: uuid.UUID, chunk_id: uuid.UUID
) -> PassageOut:
    chunk = db.scalar(
        select(DocumentChunk).where(DocumentChunk.id == chunk_id, DocumentChunk.case_id == case_id)
    )
    if chunk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="chunk_not_found")
    evidence = db.scalar(
        select(EvidenceObject).where(
            EvidenceObject.id == chunk.evidence_id, EvidenceObject.case_id == case_id
        )
    )
    first_pointer = chunk.json_locations[0]["pointer"] if chunk.json_locations else None
    return _passage(
        storage,
        evidence,
        expected_sha256=chunk.evidence_sha256,
        kind=chunk.kind,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        json_pointer=first_pointer,
        chunk_text=chunk.text,
        quote=None,
    )


def search(db: Session, case: Case, query: str, *, limit: int) -> SearchOut:
    result = retrieve(
        db,
        case.id,
        query,
        top_k=limit,
        semantic_unavailable_reason="semantic_search_runs_in_ai_answers",
    )
    coverage = case_coverage(db, case.id, semantic_used=False)
    return SearchOut(
        query=query,
        hits=[
            SearchHit(
                chunk_id=chunk.chunk_id,
                evidence_id=chunk.evidence_id,
                evidence_title=chunk.evidence_title,
                acquisition_method=chunk.acquisition_method,
                synthetic=chunk.synthetic,
                chunk_index=chunk.chunk_index,
                kind=chunk.kind,
                snippet=chunk.text[:400],
                matched_by=sorted(chunk.ranks),
            )
            for chunk in result.chunks
        ],
        semantic="not_used",
        coverage_notes=[
            note for note in coverage["notes"] if not note.startswith("Semantic search")
        ],
    )
