"""AI routes. Every case route resolves the case through the membership dependency first, and
child records (conversations, runs, citations, chunks) are always selected by case id as well."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.ai import indexing, service
from app.ai.models import AiConversation, AiMessage, AiMode, AiRun, AiRunType, MessageKind
from app.ai.schemas import (
    AiRunOut,
    AiStatusOut,
    CaseAiOut,
    CaseAiSettingsIn,
    CitationDetail,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    GenerateIn,
    IndexItemOut,
    MessageOut,
    PassageOut,
    QuestionIn,
    ReindexIn,
    SearchOut,
)
from app.cases.access import ReadableCase, WritableCase
from app.deps import DbDep, PrincipalDep, SettingsDep
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType, DispatchOutbox
from app.evidence.storage import EvidenceStorage
from app.schemas import LimitParam, OffsetParam, Page

status_router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
router = APIRouter(prefix="/api/v1/cases/{case_id}/ai", tags=["ai"])


def _publish(request: Request, outbox_id: uuid.UUID | None) -> None:
    if outbox_id is None:
        return
    state = request.app.state
    dispatch.publish_after_commit(state.session_factory, state.celery, state.settings, outbox_id)


def _storage(request: Request) -> EvidenceStorage:
    storage: EvidenceStorage = request.app.state.evidence_storage
    return storage


@status_router.get("/status")
def get_ai_status(db: DbDep, settings: SettingsDep, _principal: PrincipalDep) -> AiStatusOut:
    return service.ai_status(db, settings)


@status_router.post("/provider-checks", status_code=status.HTTP_202_ACCEPTED)
def request_provider_check(
    request: Request, settings: SettingsDep, _principal: PrincipalDep
) -> dict[str, str]:
    service.require_ai_enabled(settings)
    state = request.app.state
    dispatch.schedule_provider_check(state.session_factory, settings)
    with state.session_factory() as db:
        outbox_id = db.scalar(
            select(DispatchOutbox.id).where(
                DispatchOutbox.aggregate_type == AggregateType.AI_PROVIDER_CHECK,
                DispatchOutbox.aggregate_id == dispatch.PROVIDER_CHECK_ID,
            )
        )
    _publish(request, outbox_id)
    return {"status": "queued"}


@router.get("")
def get_case_ai(case: ReadableCase, db: DbDep, settings: SettingsDep) -> CaseAiOut:
    return service.case_ai(db, settings, case)


@router.patch("/settings")
def patch_case_ai_settings(
    request: Request, case: WritableCase, db: DbDep, settings: SettingsDep, body: CaseAiSettingsIn
) -> CaseAiOut:
    updated, outbox_id = service.update_case_settings(db, settings, case, body)
    db.commit()
    _publish(request, outbox_id)
    db.refresh(updated)
    return service.case_ai(db, settings, updated)


# -- index -------------------------------------------------------------------------------------


@router.get("/index")
def list_index(
    case: ReadableCase,
    db: DbDep,
    settings: SettingsDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    status_filter: Annotated[
        str | None,
        Query(alias="status", pattern="^(pending|indexing|indexed|stale|failed|canceled)$"),
    ] = None,
) -> Page[IndexItemOut]:
    service.require_ai_enabled(settings)
    items, total = service.index_items(
        db, case.id, status_filter=status_filter, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/index/rebuild")
def rebuild_index(
    request: Request, case: WritableCase, db: DbDep, settings: SettingsDep, body: ReindexIn
) -> CaseAiOut:
    service.require_ai_enabled(settings)
    if case.ai_mode == AiMode.DISABLED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_ai_disabled")
    indexing.request_reindex(db, case.id, scope=body.scope)
    outbox_id = indexing.enqueue_case_index(db, case.id)
    db.commit()
    _publish(request, outbox_id)
    return service.case_ai(db, settings, case)


@router.post("/index/cancel")
def cancel_index(case: ReadableCase, db: DbDep, settings: SettingsDep) -> CaseAiOut:
    service.require_ai_enabled(settings)
    indexing.cancel_indexing(db, case.id)
    db.commit()
    return service.case_ai(db, settings, case)


@router.get("/search")
def search_index(
    case: ReadableCase,
    db: DbDep,
    settings: SettingsDep,
    q: Annotated[str, Query(min_length=2, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchOut:
    service.require_ai_enabled(settings)
    return service.search(db, case, q, limit=limit)


@router.get("/chunks/{chunk_id}")
def get_chunk_passage(
    request: Request, case: ReadableCase, db: DbDep, settings: SettingsDep, chunk_id: uuid.UUID
) -> PassageOut:
    service.require_ai_enabled(settings)
    return service.chunk_passage(db, _storage(request), case.id, chunk_id)


# -- conversations -----------------------------------------------------------------------------


@router.get("/conversations")
def list_conversations(
    case: ReadableCase, db: DbDep, limit: LimitParam = 25, offset: OffsetParam = 0
) -> Page[ConversationOut]:
    condition = AiConversation.case_id == case.id
    total = db.scalar(select(func.count()).select_from(AiConversation).where(condition)) or 0
    rows = db.scalars(
        select(AiConversation)
        .where(condition)
        .order_by(AiConversation.updated_at.desc(), AiConversation.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[service.conversation_out(db, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    body: ConversationCreate,
) -> ConversationOut:
    service.require_ai_enabled(settings)
    conversation = AiConversation(
        case_id=case.id,
        created_by_user_id=principal.user.id,
        title=(body.title or "").strip() or "New conversation",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return service.conversation_out(db, conversation)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    case: ReadableCase, db: DbDep, conversation_id: uuid.UUID
) -> ConversationDetail:
    return service.conversation_detail(db, service.get_conversation(db, case.id, conversation_id))


@router.post("/conversations/{conversation_id}/questions", status_code=status.HTTP_202_ACCEPTED)
def ask_question(
    request: Request,
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    conversation_id: uuid.UUID,
    body: QuestionIn,
) -> AiRunOut:
    conversation = service.get_conversation(db, case.id, conversation_id)
    run, outbox = service.create_run(
        db,
        settings,
        case,
        principal.user,
        run_type=AiRunType.ANSWER,
        location=body.location,
        question=body.question,
        conversation=conversation,
    )
    if conversation.title == "New conversation":
        conversation.title = body.question[:80]
    db.commit()
    _publish(request, outbox.id)
    db.refresh(run)
    return service.run_out(run)


# -- summaries and suggestions -----------------------------------------------------------------


@router.post("/summaries", status_code=status.HTTP_202_ACCEPTED)
def create_summary(
    request: Request,
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    body: GenerateIn,
) -> AiRunOut:
    run, outbox = service.create_run(
        db, settings, case, principal.user, run_type=AiRunType.SUMMARY, location=body.location
    )
    db.commit()
    _publish(request, outbox.id)
    db.refresh(run)
    return service.run_out(run)


@router.post("/relationship-suggestions", status_code=status.HTTP_202_ACCEPTED)
def create_suggestions(
    request: Request,
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    body: GenerateIn,
) -> AiRunOut:
    run, outbox = service.create_run(
        db,
        settings,
        case,
        principal.user,
        run_type=AiRunType.RELATIONSHIP_SUGGESTIONS,
        location=body.location,
    )
    db.commit()
    _publish(request, outbox.id)
    db.refresh(run)
    return service.run_out(run)


@router.get("/outputs")
def list_outputs(
    case: ReadableCase,
    db: DbDep,
    kind: Annotated[str, Query(pattern="^(summary|suggestions)$")],
    limit: LimitParam = 10,
    offset: OffsetParam = 0,
) -> Page[MessageOut]:
    condition = (AiMessage.case_id == case.id) & (AiMessage.kind == MessageKind(kind))
    total = db.scalar(select(func.count()).select_from(AiMessage).where(condition)) or 0
    rows = list(
        db.scalars(
            select(AiMessage)
            .where(condition)
            .order_by(AiMessage.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=service.messages_out(db, case.id, rows), total=total, limit=limit, offset=offset
    )


# -- runs and citations ------------------------------------------------------------------------


@router.get("/runs")
def list_runs(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    run_type: Annotated[
        str | None, Query(pattern="^(answer|summary|relationship_suggestions)$")
    ] = None,
) -> Page[AiRunOut]:
    conditions = [AiRun.case_id == case.id]
    if run_type:
        conditions.append(AiRun.run_type == run_type)
    total = db.scalar(select(func.count()).select_from(AiRun).where(*conditions)) or 0
    rows = db.scalars(
        select(AiRun)
        .where(*conditions)
        .order_by(AiRun.queued_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[service.run_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/runs/{run_id}")
def get_run(case: ReadableCase, db: DbDep, run_id: uuid.UUID) -> AiRunOut:
    return service.run_out(service.get_run(db, case.id, run_id))


@router.post("/runs/{run_id}/cancel")
def cancel_run(case: ReadableCase, db: DbDep, run_id: uuid.UUID) -> AiRunOut:
    run = service.get_run(db, case.id, run_id)
    service.request_cancel(db, run)
    db.commit()
    db.refresh(run)
    return service.run_out(run)


@router.get("/citations/{citation_id}")
def get_citation(
    request: Request, case: ReadableCase, db: DbDep, citation_id: uuid.UUID
) -> CitationDetail:
    return service.citation_detail(db, _storage(request), case.id, citation_id)
