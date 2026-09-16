"""Execution of AI runs in the AI worker.

A run is claimed with a lease (like query executions). Before every stage, before every model
request and in the transaction that stores the result, the worker re-checks that:

- the case still exists and is not being deleted;
- the requesting user is still an active case member;
- AI is enabled for the installation and the case, and the case policy version is unchanged
  (so a switch to local-only or disabled stops queued and running cloud work).

Nothing is stored if any check fails. Model output is validated before it is persisted; the
only writes a run can make are its own run, message and citation records, and, for relationship
suggestions, unreviewed ``ai_suggestion`` relationships between existing entities.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.ai import prompts
from app.ai.coverage import case_coverage
from app.ai.indexing import active_profile, profile_key
from app.ai.models import (
    AiCitation,
    AiConversation,
    AiMessage,
    AiRun,
    AiRunStatus,
    AiRunType,
    CitationStatus,
    MessageKind,
    MessageRole,
    ProcessingLocation,
)
from app.ai.policy import PolicyError, ProviderSet, authorize_generation, check_case_ai_available
from app.ai.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    as_list,
)
from app.ai.retrieval import RetrievalResult, RetrievedChunk, retrieve
from app.ai.text import extract_identifiers, fold_for_search, locate_quote
from app.ai.tools import ToolResult, execute_tool_calls
from app.ai.validation import ValidatedAnswer, render_plain_text, validate_answer
from app.auth.models import User
from app.cases.models import Case, CaseMember, CaseStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.entities.models import (
    Entity,
    EntityIdentifier,
    Origin,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
)
from app.entities.schemas import SUGGESTED_PREDICATES
from app.evidence.storage import EvidenceStorage

logger = logging.getLogger(__name__)

PREDICATE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
READABLE = (CaseStatus.ACTIVE, CaseStatus.ARCHIVED)
STOP_CODES_CANCELED = frozenset(
    {
        "canceled",
        "authorization_revoked",
        "case_unavailable",
        "ai_disabled",
        "case_ai_disabled",
        "processing_policy_changed",
    }
)
MAX_HISTORY_TURNS = 3
MAX_SUGGESTION_ENTITIES = 60
RETRY_SLEEP_SECONDS = 2.0


class RunStopped(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _LeaseLost(Exception):
    pass


@dataclass
class AiRunContext:
    session_factory: sessionmaker[Session]
    storage: EvidenceStorage
    settings: Settings
    providers: ProviderSet
    worker_name: str = "ai-worker"
    # Test hooks: called right before a model request, and instead of sleeping between retries.
    before_generation: Callable[[uuid.UUID, str], None] | None = None
    sleep: Callable[[float], None] = time.sleep


@dataclass
class _Usage:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add(self, task: str, result: GenerationResult, location: str) -> None:
        self.calls.append(
            {"task": task, "location": location, "model": result.model, **result.usage.as_dict()}
        )

    def summary(self) -> dict[str, Any]:
        def total(key: str) -> int | None:
            values = [call[key] for call in self.calls]
            return (
                sum(values) if values and all(isinstance(value, int) for value in values) else None
            )

        return {
            "calls": self.calls,
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "source": "provider_reported"
            if self.calls and all(call["source"] == "provider_reported" for call in self.calls)
            else "unavailable",
            "cost": "unknown",
        }


# -- lease -------------------------------------------------------------------------------------


def claim_ai_run(ctx: AiRunContext, run_id: uuid.UUID) -> uuid.UUID | None:
    token = uuid.uuid4()
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        claimed = db.execute(
            update(AiRun)
            .where(
                AiRun.id == run_id,
                or_(
                    AiRun.status == AiRunStatus.QUEUED,
                    and_(
                        AiRun.status == AiRunStatus.RUNNING,
                        or_(AiRun.lease_expires_at.is_(None), AiRun.lease_expires_at < now),
                        AiRun.claim_count < ctx.settings.ai_run_max_claims,
                    ),
                ),
            )
            .values(
                status=AiRunStatus.RUNNING,
                started_at=func.coalesce(AiRun.started_at, now),
                claim_count=AiRun.claim_count + 1,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=ctx.settings.ai_run_lease_seconds),
                error_code=None,
                error_detail=None,
            )
            .returning(AiRun.id)
        ).first()
    return token if claimed else None


def _abandon_exhausted(ctx: AiRunContext, run_id: uuid.UUID) -> None:
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        updated = db.execute(
            update(AiRun)
            .where(
                AiRun.id == run_id,
                AiRun.status == AiRunStatus.RUNNING,
                AiRun.lease_expires_at < now,
                AiRun.claim_count >= ctx.settings.ai_run_max_claims,
            )
            .values(
                status=AiRunStatus.FAILED,
                finished_at=now,
                lease_token=None,
                lease_expires_at=None,
                error_code="worker_lost",
                error_detail="The AI worker stopped repeatedly while processing this request.",
            )
            .returning(AiRun.id)
        ).first()
        if updated:
            dispatch.mark_done(db, AggregateType.AI_RUN, run_id)


def _locked_run(db: Session, run_id: uuid.UUID, token: uuid.UUID) -> AiRun:
    run = db.scalar(select(AiRun).where(AiRun.id == run_id).with_for_update())
    if run is None or run.lease_token != token or run.status != AiRunStatus.RUNNING:
        raise _LeaseLost
    return run


def _authorize(db: Session, settings: Settings, run: AiRun) -> Case:
    case = db.scalar(select(Case).where(Case.id == run.case_id).with_for_update(read=True))
    if case is None or case.status not in READABLE:
        raise RunStopped("case_unavailable", "The case is being deleted or no longer exists.")
    member = db.scalar(
        select(User.is_active)
        .join(CaseMember, CaseMember.user_id == User.id)
        .where(CaseMember.case_id == case.id, User.id == run.requested_by_user_id)
    )
    if not member:
        raise RunStopped(
            "authorization_revoked", "The requesting user no longer has access to this case."
        )
    try:
        check_case_ai_available(settings, case.ai_mode)
    except PolicyError as exc:
        raise RunStopped(exc.code, exc.message) from exc
    if case.ai_policy_version != run.policy_version:
        raise RunStopped(
            "processing_policy_changed",
            "The case's AI processing setting changed after this request was made.",
        )
    return case


def _checkpoint(ctx: AiRunContext, run_id: uuid.UUID, token: uuid.UUID, stage: str) -> None:
    """Renew the lease, record the stage and re-check cancellation and authorization."""
    with session_scope(ctx.session_factory) as db:
        run = _locked_run(db, run_id, token)
        if run.cancel_requested_at is not None:
            raise RunStopped("canceled", "Canceled by the analyst.")
        _authorize(db, ctx.settings, run)
        run.stage = stage
        run.lease_expires_at = utcnow() + timedelta(seconds=ctx.settings.ai_run_lease_seconds)


def _finish(ctx: AiRunContext, run_id: uuid.UUID, token: uuid.UUID, **values: Any) -> None:
    with session_scope(ctx.session_factory) as db:
        updated = db.execute(
            update(AiRun)
            .where(
                AiRun.id == run_id, AiRun.lease_token == token, AiRun.status == AiRunStatus.RUNNING
            )
            .values(finished_at=utcnow(), lease_token=None, lease_expires_at=None, **values)
            .returning(AiRun.id)
        ).first()
        if updated:
            dispatch.mark_done(db, AggregateType.AI_RUN, run_id)


# -- model calls -------------------------------------------------------------------------------


def _generate(
    ctx: AiRunContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    usage: _Usage,
    *,
    task: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    context: dict[str, Any],
) -> GenerationResult:
    attempt = 0
    while True:
        with session_scope(ctx.session_factory) as db:
            run = _locked_run(db, run_id, token)
            if run.cancel_requested_at is not None:
                raise RunStopped("canceled", "Canceled by the analyst.")
            case = _authorize(db, ctx.settings, run)
            grant = authorize_generation(
                ctx.settings,
                case_mode=case.ai_mode,
                case_policy_version=case.ai_policy_version,
                run_policy_version=run.policy_version,
                requested_location=run.requested_location,
            )
            provider = ctx.providers.generation_for(grant)
            run.provider = provider.name
            run.model = provider.model
            run.processing_location = grant.location.value
            run.lease_expires_at = utcnow() + timedelta(seconds=ctx.settings.ai_run_lease_seconds)
        if ctx.before_generation is not None:
            ctx.before_generation(run_id, task)
        request = GenerationRequest(
            task=task,  # type: ignore[arg-type]
            system=system,
            user=user,
            schema=schema,
            max_output_tokens=max_tokens,
            grant=grant,
            context=context,
        )
        try:
            result = provider.generate_json(request)
        except ProviderError as exc:
            if exc.retryable and attempt < ctx.settings.ai_max_retries:
                attempt += 1
                ctx.sleep(
                    min(30.0, max(RETRY_SLEEP_SECONDS * attempt, exc.retry_after_seconds or 0))
                )
                continue
            raise
        usage.add(task, result, grant.location.value)
        with session_scope(ctx.session_factory) as db:
            run = _locked_run(db, run_id, token)
            if run.cancel_requested_at is not None:
                raise RunStopped(
                    "canceled", "Canceled by the analyst; the model output was discarded."
                )
            run.usage = usage.summary()
        return result


def _query_embedding(
    ctx: AiRunContext, text: str
) -> tuple[list[float] | None, uuid.UUID | None, str | None]:
    provider = ctx.providers.embeddings
    if provider.location == ProcessingLocation.CLOUD:
        return None, None, "cloud_embeddings_not_supported"
    with session_scope(ctx.session_factory) as db:
        profile = active_profile(db)
        if profile is not None:
            db.expunge(profile)
    if profile is None:
        return None, None, "no_active_embedding_profile"
    inventory = provider.inventory()
    digest = inventory.models.get(provider.model) if inventory.reachable else None
    if not inventory.reachable:
        return None, None, inventory.error_code or "model_unavailable"
    if profile_key(provider.name, provider.model, digest) != profile.profile_key:
        return None, None, "embedding_configuration_changed"
    try:
        vectors = provider.embed([text], purpose="query").vectors
    except ProviderError as exc:
        return None, None, exc.code
    if not vectors or len(vectors[0]) != profile.dimensions:
        return None, None, "embedding_dimension_mismatch"
    return vectors[0], profile.id, None


def _retrieve(
    ctx: AiRunContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    case_id: uuid.UUID,
    query: str,
    top_k: int,
) -> tuple[RetrievalResult, dict[str, Any]]:
    vector, profile_id, reason = _query_embedding(ctx, query)
    with session_scope(ctx.session_factory) as db:
        run = _locked_run(db, run_id, token)
        _authorize(db, ctx.settings, run)
        result = retrieve(
            db,
            case_id,
            query,
            top_k=top_k,
            query_vector=vector,
            profile_id=profile_id,
            semantic_unavailable_reason=reason,
        )
        coverage = case_coverage(db, case_id, semantic_used=vector is not None)
    return result, coverage


def _question_subjects(question: str, limit: int = 2) -> list[str]:
    """Identifiers the question names, in normalized form (domains, accounts, addresses, ...)."""
    subjects = []
    for key in extract_identifiers(question):
        _, _, value = key.partition(":")
        if value and value not in subjects:
            subjects.append(value)
    return subjects[:limit]


def _run_tools(
    ctx: AiRunContext, run_id: uuid.UUID, token: uuid.UUID, case_id: uuid.UUID, calls: list[Any]
) -> tuple[list[ToolResult], list[dict[str, str]]]:
    _checkpoint(ctx, run_id, token, "running_tools")
    db = ctx.session_factory()
    try:
        results, rejected = execute_tool_calls(
            db, case_id, calls, max_calls=ctx.settings.ai_max_tool_calls
        )
    finally:
        db.rollback()
        db.close()
    return results, [{"tool": item.name, "reason": item.reason} for item in rejected]


# -- persistence -------------------------------------------------------------------------------


def _citation_rows(
    run: AiRun, message: AiMessage, answer: ValidatedAnswer
) -> tuple[list[AiCitation], list[dict[str, Any]]]:
    rows: list[AiCitation] = []
    claims_json = []
    for index, claim in enumerate(answer.claims):
        refs = []
        for citation in claim.citations:
            row = AiCitation(
                id=uuid.uuid4(),
                case_id=run.case_id,
                ai_run_id=run.id,
                message_id=message.id,
                claim_index=index,
                label=citation.label,
                ref_type=citation.ref_type,
                status=CitationStatus.ACCEPTED,
                chunk_id=citation.chunk.chunk_id if citation.chunk else None,
                evidence_id=citation.chunk.evidence_id if citation.chunk else None,
                evidence_sha256=citation.chunk.evidence_sha256 if citation.chunk else None,
                quote=citation.quote if citation.chunk else None,
                source_char_start=citation.source_char_start,
                source_char_end=citation.source_char_end,
                json_pointer=citation.json_pointer,
                tool_name=citation.tool.name if citation.tool else None,
            )
            rows.append(row)
            refs.append(
                {"citation_id": str(row.id), "label": citation.label, "ref_type": citation.ref_type}
            )
        claims_json.append(
            {
                "text": claim.text,
                "kind": claim.kind,
                "citations": refs,
                "answers_question": claim.answers_question,
                "applicability": claim.applicability,
                "difference_type": claim.difference_type,
                "about": claim.about.as_dict(),
            }
        )
    return rows, claims_json


def _tool_record(results: list[ToolResult], rejected: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {"ref": item.ref, "tool": item.name, "arguments": item.arguments, "result": item.result}
        for item in results
    ] + [{"tool": item["tool"], "rejected": item["reason"]} for item in rejected]


def _persist_answer(
    ctx: AiRunContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    *,
    kind: MessageKind,
    answer: ValidatedAnswer,
    retrieval: dict[str, Any],
    tools: list[dict[str, Any]],
    coverage: dict[str, Any],
    usage: _Usage,
    prompt_version: str,
) -> uuid.UUID:
    with session_scope(ctx.session_factory) as db:
        run = _locked_run(db, run_id, token)
        if run.cancel_requested_at is not None:
            raise RunStopped("canceled", "Canceled by the analyst; the model output was discarded.")
        _authorize(db, ctx.settings, run)
        message = AiMessage(
            id=uuid.uuid4(),
            case_id=run.case_id,
            conversation_id=run.conversation_id,
            ai_run_id=run.id,
            role=MessageRole.ASSISTANT,
            kind=kind,
            content=render_plain_text(answer),
        )
        db.add(message)
        db.flush()
        citations, claims_json = _citation_rows(run, message, answer)
        db.add_all(citations)
        message.answer = {
            "status": answer.status,
            "claims": claims_json,
            "limitations": answer.limitations,
            "coverage_notes": coverage.get("notes", []),
            "server_notes": answer.server_notes,
            "synthetic_model": run.processing_location == ProcessingLocation.FIXTURE,
        }
        run.status = AiRunStatus.COMPLETED
        run.stage = "done"
        run.finished_at = utcnow()
        run.lease_token = None
        run.lease_expires_at = None
        run.prompt_template_version = prompt_version
        run.retrieval = retrieval
        run.tool_calls = tools
        run.coverage = coverage
        run.validation = answer.report()
        run.usage = usage.summary()
        if run.conversation_id is not None:
            db.execute(
                update(AiConversation)
                .where(AiConversation.id == run.conversation_id)
                .values(updated_at=func.now())
            )
        dispatch.mark_done(db, AggregateType.AI_RUN, run.id)
        return message.id


def _history(db: Session, run: AiRun) -> list[tuple[str, list[str]]]:
    if run.conversation_id is None:
        return []
    answers = list(
        db.execute(
            select(AiRun.question, AiMessage.answer)
            .join(AiMessage, AiMessage.ai_run_id == AiRun.id)
            .where(
                AiRun.conversation_id == run.conversation_id,
                AiRun.case_id == run.case_id,
                AiRun.id != run.id,
                AiRun.status == AiRunStatus.COMPLETED,
                AiRun.queued_at < run.queued_at,
                AiMessage.kind == MessageKind.ANSWER,
            )
            .order_by(AiRun.queued_at.desc())
            .limit(MAX_HISTORY_TURNS)
        ).all()
    )
    turns = []
    for question, answer in reversed(answers):
        claims = [
            str(claim.get("text", ""))[:300]
            for claim in (answer or {}).get("claims", [])
            if claim.get("kind") in ("fact", "count", "conflict", "inference")
        ]
        turns.append((str(question or "")[:500], claims[:5]))
    return turns


def _context_payload(
    evidence: list[tuple[str, RetrievedChunk, str]],
    tools: list[ToolResult],
    question: str,
    notes: list[str],
) -> dict[str, Any]:
    return {
        "question": question,
        "evidence": [
            {
                "ref": ref,
                "text": chunk.text,
                "title": chunk.evidence_title,
                "synthetic": chunk.synthetic,
            }
            for ref, chunk, _ in evidence
        ],
        "tools": [
            {
                "ref": item.ref,
                "name": item.name,
                "result": item.result,
                "description": item.result.get("description"),
            }
            for item in tools
        ],
        "coverage": notes,
    }


# -- run types ---------------------------------------------------------------------------------


def _answer(
    ctx: AiRunContext, run_id: uuid.UUID, token: uuid.UUID, snapshot: dict[str, Any]
) -> None:
    settings = ctx.settings
    usage = _Usage()
    question: str = snapshot["question"]
    case_id: uuid.UUID = snapshot["case_id"]

    tools: list[ToolResult] = []
    rejected: list[dict[str, str]] = []
    search_query = question
    if settings.ai_max_tool_calls > 0:
        _checkpoint(ctx, run_id, token, "planning")
        system, user = prompts.plan_messages(
            question,
            max_calls=settings.ai_max_tool_calls,
            today=datetime.now(UTC).date().isoformat(),
        )
        plan = _generate(
            ctx,
            run_id,
            token,
            usage,
            task="plan",
            system=system,
            user=user,
            schema=prompts.PLAN_SCHEMA,
            max_tokens=min(600, settings.ai_max_output_tokens),
            context={"question": question},
        )
        calls = as_list(plan.data.get("tool_calls"))
        planned_query = str(plan.data.get("search_query") or "").strip()[:300]
        if planned_query and fold_for_search(planned_query) != fold_for_search(question):
            search_query = f"{question}\n{planned_query}"
        # Whatever the plan asked for, check the case's own records for differing values about
        # the identifiers the question names. This is a fixed, deterministic read.
        for identifier in _question_subjects(question):
            calls.append(
                {"tool": "find_conflicting_records", "arguments": {"identifier": identifier}}
            )
        tools, rejected = _run_tools(ctx, run_id, token, case_id, calls)
        tools = [
            result
            for result in tools
            if result.name != "find_conflicting_records"
            or result.result.get("different_value_groups")
        ]

    _checkpoint(ctx, run_id, token, "retrieving")
    retrieval, coverage = _retrieve(
        ctx, run_id, token, case_id, search_query, settings.ai_retrieval_top_k
    )
    with session_scope(ctx.session_factory) as db:
        history = _history(db, _locked_run(db, run_id, token))

    _checkpoint(ctx, run_id, token, "generating")
    blocks = prompts.new_blocks()
    evidence = prompts.fit_evidence(blocks, retrieval.chunks, settings.ai_max_context_chars)
    system, user = prompts.answer_messages(
        blocks,
        question=question,
        evidence=evidence,
        tools=tools,
        coverage_notes=coverage["notes"],
        history=history,
    )
    context_payload = _context_payload(evidence, tools, question, coverage["notes"])
    try:
        result = _generate(
            ctx,
            run_id,
            token,
            usage,
            task="answer",
            system=system,
            user=user,
            schema=prompts.ANSWER_SCHEMA,
            max_tokens=settings.ai_max_output_tokens,
            context=context_payload,
        )
        shortened = False
    except ProviderError as exc:
        if exc.code != "output_truncated":
            raise
        # The answer did not fit. Partial JSON is never used; the model is asked once more for a
        # shorter answer, and the reader is told that this happened.
        result = _generate(
            ctx,
            run_id,
            token,
            usage,
            task="answer",
            system=system,
            user=f"{user}\n\n{prompts.ANSWER_TOO_LONG_NOTICE}",
            schema=prompts.compact_answer_schema(),
            max_tokens=settings.ai_max_output_tokens,
            context=context_payload,
        )
        shortened = True
    _checkpoint(ctx, run_id, token, "validating")
    answer = validate_answer(
        result.data,
        question=question,
        evidence={ref: chunk for ref, chunk, _ in evidence},
        tools={item.ref: item for item in tools},
        secrets=settings.secret_values(),
    )
    if shortened:
        answer.server_notes.append(
            "The first answer was longer than the output limit and was discarded; this is a "
            "second, shorter answer to the same question from the same evidence."
        )
    _persist_answer(
        ctx,
        run_id,
        token,
        kind=MessageKind.ANSWER,
        answer=answer,
        retrieval={
            **retrieval.summary(),
            "query": search_query,
            "evidence_labels": {ref: str(chunk.chunk_id) for ref, chunk, _ in evidence},
        },
        tools=_tool_record(tools, rejected),
        coverage=coverage,
        usage=usage,
        prompt_version=f"{prompts.ANSWER_VERSION}+{prompts.PLAN_VERSION}",
    )


def _summary(
    ctx: AiRunContext, run_id: uuid.UUID, token: uuid.UUID, snapshot: dict[str, Any]
) -> None:
    settings = ctx.settings
    usage = _Usage()
    case_id: uuid.UUID = snapshot["case_id"]
    fixed_calls = [
        {"tool": "count_evidence", "arguments": {}},
        {"tool": "count_entities", "arguments": {}},
        {"tool": "count_relationships", "arguments": {}},
        {"tool": "connector_coverage", "arguments": {}},
    ]
    tools, rejected = _run_tools(ctx, run_id, token, case_id, fixed_calls)
    _checkpoint(ctx, run_id, token, "retrieving")
    topic = snapshot["topic"]
    retrieval, coverage = _retrieve(ctx, run_id, token, case_id, topic, settings.ai_retrieval_top_k)
    _checkpoint(ctx, run_id, token, "generating")
    blocks = prompts.new_blocks()
    evidence = prompts.fit_evidence(blocks, retrieval.chunks, settings.ai_max_context_chars)
    system, user = prompts.answer_messages(
        blocks,
        question=topic,
        evidence=evidence,
        tools=tools,
        coverage_notes=coverage["notes"],
        history=[],
        summary=True,
    )
    result = _generate(
        ctx,
        run_id,
        token,
        usage,
        task="summary",
        system=system,
        user=user,
        schema=prompts.ANSWER_SCHEMA,
        max_tokens=settings.ai_max_output_tokens,
        context=_context_payload(evidence, tools, topic, coverage["notes"]),
    )
    _checkpoint(ctx, run_id, token, "validating")
    answer = validate_answer(
        result.data,
        question="",
        evidence={ref: chunk for ref, chunk, _ in evidence},
        tools={item.ref: item for item in tools},
        secrets=settings.secret_values(),
    )
    _persist_answer(
        ctx,
        run_id,
        token,
        kind=MessageKind.SUMMARY,
        answer=answer,
        retrieval={
            **retrieval.summary(),
            "query": topic,
            "evidence_labels": {ref: str(chunk.chunk_id) for ref, chunk, _ in evidence},
        },
        tools=_tool_record(tools, rejected),
        coverage=coverage,
        usage=usage,
        prompt_version=prompts.SUMMARY_VERSION,
    )


def _mentions(chunk_text: str, entity: dict[str, Any]) -> bool:
    folded = fold_for_search(chunk_text)
    terms = [entity["name"], *entity["identifiers"]]
    return any(
        len(fold_for_search(term)) >= 3 and fold_for_search(term) in folded for term in terms
    )


def _suggestions(
    ctx: AiRunContext, run_id: uuid.UUID, token: uuid.UUID, snapshot: dict[str, Any]
) -> None:
    settings = ctx.settings
    usage = _Usage()
    case_id: uuid.UUID = snapshot["case_id"]
    _checkpoint(ctx, run_id, token, "retrieving")
    with session_scope(ctx.session_factory) as db:
        entity_rows = list(
            db.scalars(
                select(Entity)
                .where(Entity.case_id == case_id)
                .order_by(Entity.created_at, Entity.id)
                .limit(MAX_SUGGESTION_ENTITIES)
            )
        )
        identifiers: dict[uuid.UUID, list[str]] = {}
        for entity_id, value in db.execute(
            select(EntityIdentifier.entity_id, EntityIdentifier.original_value).where(
                EntityIdentifier.case_id == case_id,
                EntityIdentifier.entity_id.in_([row.id for row in entity_rows] or [uuid.uuid4()]),
            )
        ):
            identifiers.setdefault(entity_id, []).append(value)
        entities: list[dict[str, Any]] = [
            {
                "ref": f"N{position}",
                "entity_id": row.id,
                "name": row.display_name,
                "type": row.entity_type,
                "identifiers": identifiers.get(row.id, [])[:5],
            }
            for position, row in enumerate(entity_rows, start=1)
        ]
    by_ref = {entity["ref"]: entity for entity in entities}
    notes: list[str] = []
    created: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    retrieval_summary: dict[str, Any] = {}
    coverage: dict[str, Any] = {"notes": []}
    evidence: list[tuple[str, RetrievedChunk, str]] = []
    raw_suggestions: list[Any] = []
    if len(entities) < 2:
        notes.append("At least two entities are needed before relationships can be suggested.")
    else:
        query = " ".join(
            term for entity in entities for term in [entity["name"], *entity["identifiers"]]
        )[:1000]
        retrieval, coverage = _retrieve(
            ctx, run_id, token, case_id, query, min(30, settings.ai_retrieval_top_k * 2)
        )
        retrieval_summary = {**retrieval.summary(), "query": query}
        _checkpoint(ctx, run_id, token, "generating")
        blocks = prompts.new_blocks()
        evidence = prompts.fit_evidence(blocks, retrieval.chunks, settings.ai_max_context_chars)
        if not evidence:
            notes.append("No indexed evidence mentions the case entities.")
        else:
            system, user = prompts.suggestion_messages(
                blocks, entities=entities, evidence=evidence, predicates=list(SUGGESTED_PREDICATES)
            )
            result = _generate(
                ctx,
                run_id,
                token,
                usage,
                task="relationship_suggestions",
                system=system,
                user=user,
                schema=prompts.SUGGESTIONS_SCHEMA,
                max_tokens=settings.ai_max_output_tokens,
                context={
                    "entities": [
                        {key: value for key, value in entity.items() if key != "entity_id"}
                        for entity in entities
                    ],
                    "evidence": [{"ref": ref, "text": chunk.text} for ref, chunk, _ in evidence],
                },
            )
            raw_suggestions = as_list(result.data.get("suggestions"))

    _checkpoint(ctx, run_id, token, "validating")
    chunks = {ref: chunk for ref, chunk, _ in evidence}
    secrets = settings.secret_values()
    with session_scope(ctx.session_factory) as db:
        run = _locked_run(db, run_id, token)
        if run.cancel_requested_at is not None:
            raise RunStopped("canceled", "Canceled by the analyst; the model output was discarded.")
        case = _authorize(db, settings, run)
        if case.status != CaseStatus.ACTIVE:
            raise RunStopped("case_unavailable", "Archived cases do not accept new relationships.")
        message = AiMessage(
            id=uuid.uuid4(),
            case_id=case_id,
            conversation_id=None,
            ai_run_id=run.id,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.SUGGESTIONS,
            content="",
        )
        db.add(message)
        db.flush()
        seen: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
        citation_rows: list[AiCitation] = []
        for item in raw_suggestions[:10]:
            if not isinstance(item, dict):
                continue
            source = by_ref.get(str(item.get("source", "")).strip().upper())
            target = by_ref.get(str(item.get("target", "")).strip().upper())
            predicate = str(item.get("predicate", "")).strip().lower()
            rationale = str(item.get("rationale", "")).strip()[:1000]
            if source is None or target is None:
                rejected.append({"reason": "unknown_entity_reference"})
                continue
            if source["entity_id"] == target["entity_id"]:
                rejected.append({"reason": "same_entity"})
                continue
            if not PREDICATE_PATTERN.match(predicate):
                rejected.append({"reason": "invalid_predicate"})
                continue
            if any(secret in rationale for secret in secrets):
                rejected.append({"reason": "secret_value_detected"})
                continue
            verified = []
            for raw in as_list(item.get("citations"))[:5]:
                ref = str(raw.get("ref", "") if isinstance(raw, dict) else "").strip().upper()
                chunk = chunks.get(ref)
                quote = str(raw.get("quote", "") if isinstance(raw, dict) else "")[:600]
                match = locate_quote(chunk.text, quote) if chunk else None
                if chunk is None or match is None:
                    continue
                if not (_mentions(chunk.text, source) and _mentions(chunk.text, target)):
                    continue
                verified.append((ref, chunk, chunk.text[match.start : match.end], match))
            if not verified:
                rejected.append({"reason": "no_verified_citation_mentioning_both_entities"})
                continue
            source_id: uuid.UUID = source["entity_id"]
            target_id: uuid.UUID = target["entity_id"]
            key = (source_id, target_id, predicate)
            exists_already = db.scalar(
                select(Relationship.id).where(
                    Relationship.case_id == case_id,
                    Relationship.source_entity_id == source["entity_id"],
                    Relationship.target_entity_id == target["entity_id"],
                    Relationship.predicate == predicate,
                )
            )
            if key in seen or exists_already is not None:
                rejected.append({"reason": "relationship_already_exists"})
                continue
            seen.add(key)
            relationship = Relationship(
                id=uuid.uuid4(),
                case_id=case_id,
                source_entity_id=source["entity_id"],
                target_entity_id=target["entity_id"],
                predicate=predicate,
                origin=Origin.AI_SUGGESTION,
                review_status=ReviewStatus.UNREVIEWED,
                description=f"AI suggestion (unreviewed): {rationale}"[:2000],
                created_by_ai_run_id=run.id,
            )
            db.add(relationship)
            db.flush()
            refs = []
            linked: set[uuid.UUID] = set()
            for ref, chunk, quote, match in verified:
                citation = AiCitation(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    ai_run_id=run.id,
                    message_id=message.id,
                    claim_index=len(created),
                    label=ref,
                    ref_type="chunk",
                    status=CitationStatus.ACCEPTED,
                    chunk_id=chunk.chunk_id,
                    evidence_id=chunk.evidence_id,
                    evidence_sha256=chunk.evidence_sha256,
                    quote=quote,
                    source_char_start=chunk.char_start + match.start
                    if chunk.kind == "text" and chunk.char_start is not None
                    else None,
                    source_char_end=chunk.char_start + match.end
                    if chunk.kind == "text" and chunk.char_start is not None
                    else None,
                    json_pointer=None,
                )
                if chunk.kind == "json" and chunk.json_locations:
                    from app.ai.chunking import pointer_at

                    citation.json_pointer = pointer_at(chunk.json_locations, match.start)
                citation_rows.append(citation)
                refs.append({"citation_id": str(citation.id), "label": ref, "ref_type": "chunk"})
                if chunk.evidence_id not in linked:
                    linked.add(chunk.evidence_id)
                    db.add(
                        RelationshipEvidence(
                            case_id=case_id,
                            relationship_id=relationship.id,
                            evidence_id=chunk.evidence_id,
                            stance="supports",
                            note=f"Cited by an AI suggestion: {quote}"[:2000],
                        )
                    )
            created.append(
                {
                    "relationship_id": str(relationship.id),
                    "source": {
                        "entity_id": str(source["entity_id"]),
                        "display_name": source["name"],
                    },
                    "target": {
                        "entity_id": str(target["entity_id"]),
                        "display_name": target["name"],
                    },
                    "predicate": predicate,
                    "rationale": rationale,
                    "citations": refs,
                }
            )
        db.add_all(citation_rows)
        if not created and not notes:
            notes.append("No relationship could be suggested with verifiable supporting evidence.")
        message.content = f"{len(created)} relationship suggestion(s) awaiting analyst review."
        message.answer = {
            "status": "answered" if created else "insufficient_evidence",
            "suggestions": created,
            "rejected": rejected,
            "server_notes": notes,
            "coverage_notes": coverage.get("notes", []),
            "synthetic_model": run.processing_location == ProcessingLocation.FIXTURE,
        }
        run.status = AiRunStatus.COMPLETED
        run.stage = "done"
        run.finished_at = utcnow()
        run.lease_token = None
        run.lease_expires_at = None
        run.prompt_template_version = prompts.SUGGESTIONS_VERSION
        run.retrieval = retrieval_summary
        run.coverage = coverage
        run.validation = {"created": len(created), "rejected": rejected, "server_notes": notes}
        run.usage = usage.summary()
        dispatch.mark_done(db, AggregateType.AI_RUN, run.id)


def execute_ai_run(ctx: AiRunContext, run_id: uuid.UUID) -> str:
    token = claim_ai_run(ctx, run_id)
    if token is None:
        _abandon_exhausted(ctx, run_id)
        return "skipped"
    with session_scope(ctx.session_factory) as db:
        run = db.get(AiRun, run_id)
        assert run is not None
        snapshot: dict[str, Any] = {
            "case_id": run.case_id,
            "question": run.question,
            "run_type": run.run_type,
        }
        if run.run_type == AiRunType.SUMMARY:
            case = db.get(Case, run.case_id)
            snapshot["topic"] = (
                f"{case.title}. Purpose: {case.purpose}. Scope: {case.scope}"[:1000]
                if case
                else "case summary"
            )
    try:
        if snapshot["run_type"] == AiRunType.ANSWER:
            _answer(ctx, run_id, token, snapshot)
        elif snapshot["run_type"] == AiRunType.SUMMARY:
            _summary(ctx, run_id, token, snapshot)
        else:
            _suggestions(ctx, run_id, token, snapshot)
        logger.info(
            "ai_run_completed", extra={"run_ref": str(run_id)[:8], "run_type": snapshot["run_type"]}
        )
        return "completed"
    except RunStopped as stop:
        status = AiRunStatus.CANCELED if stop.code in STOP_CODES_CANCELED else AiRunStatus.FAILED
        _finish(
            ctx, run_id, token, status=status, error_code=stop.code, error_detail=stop.message[:300]
        )
        logger.info("ai_run_stopped", extra={"run_ref": str(run_id)[:8], "code": stop.code})
        return str(status)
    except PolicyError as exc:
        status = AiRunStatus.CANCELED if exc.code in STOP_CODES_CANCELED else AiRunStatus.FAILED
        _finish(
            ctx, run_id, token, status=status, error_code=exc.code, error_detail=exc.message[:300]
        )
        logger.info("ai_run_policy_stop", extra={"run_ref": str(run_id)[:8], "code": exc.code})
        return str(status)
    except ProviderError as exc:
        _finish(
            ctx,
            run_id,
            token,
            status=AiRunStatus.FAILED,
            error_code=exc.code,
            error_detail=exc.message[:300],
        )
        logger.warning(
            "ai_run_provider_error", extra={"run_ref": str(run_id)[:8], "code": exc.code}
        )
        return "failed"
    except _LeaseLost:
        logger.warning("ai_run_lease_lost", extra={"run_ref": str(run_id)[:8]})
        return "lease_lost"
    except (OperationalError, InterfaceError):
        raise
    except Exception as exc:
        logger.exception("ai_run_internal_error", extra={"run_ref": str(run_id)[:8]})
        _finish(
            ctx,
            run_id,
            token,
            status=AiRunStatus.FAILED,
            error_code="internal_error",
            error_detail=type(exc).__name__[:300],
        )
        return "failed"
