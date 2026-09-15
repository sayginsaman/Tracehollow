"""Asynchronous, idempotent evidence indexing.

Lifecycle per evidence record (``evidence_index_states``)::

    pending -> indexing -> indexed
                        -> failed      (non-retryable error, or retries exhausted)
                        -> pending     (retryable error, with backoff)
    pending/indexing -> canceled       (analyst cancel)

``stale`` is derived at read time: an indexed record whose chunks were produced by another
chunking version, or whose vectors belong to an embedding profile that is no longer active.

Writes for one evidence record happen in a single transaction guarded by the state's lease
token: old chunks are replaced, never mixed. Duplicate task delivery claims nothing twice.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.ai.chunking import CHUNKING_VERSION, ChunkDraft, ChunkingError, chunk_evidence
from app.ai.models import (
    AiMode,
    ChunkEmbedding,
    DocumentChunk,
    EmbeddingProfile,
    EvidenceIndexState,
    IndexStatus,
)
from app.ai.policy import ProviderSet
from app.ai.providers.base import ProviderError
from app.ai.text import extract_identifiers, fold_for_search
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage, IntegrityError

logger = logging.getLogger(__name__)

# Bump when embedding inputs change (for example the query/document instruction format).
INDEXING_VERSION = 1
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 900
READABLE_CASE_STATUSES = (CaseStatus.ACTIVE, CaseStatus.ARCHIVED)


def profile_key(provider: str, model: str, digest: str | None) -> str:
    material = "\x1f".join(
        [provider, model, digest or "", str(CHUNKING_VERSION), str(INDEXING_VERSION)]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def active_profile(db: Session) -> EmbeddingProfile | None:
    return db.scalar(select(EmbeddingProfile).where(EmbeddingProfile.active.is_(True)))


# -- scheduling (API and execution side) ------------------------------------------------------


def enqueue_case_index(db: Session, case_id: uuid.UUID) -> uuid.UUID:
    row = dispatch.enqueue(
        db,
        task_name=dispatch.INDEX_CASE_TASK,
        aggregate_type=AggregateType.CASE_INDEX,
        aggregate_id=case_id,
        case_id=case_id,
    )
    return row.id


def mark_evidence_for_indexing(
    db: Session, settings: Settings, *, case_id: uuid.UUID, evidence_id: uuid.UUID, ai_mode: str
) -> uuid.UUID | None:
    """Record a pending index state in the caller's transaction and schedule work if allowed.

    The state is always recorded, so enabling AI later indexes existing evidence. Returns the
    outbox row id when indexing was scheduled.
    """
    db.execute(
        insert(EvidenceIndexState)
        .values(evidence_id=evidence_id, case_id=case_id, status=IndexStatus.PENDING)
        .on_conflict_do_nothing(index_elements=["evidence_id"])
    )
    if not settings.ai_enabled or ai_mode == AiMode.DISABLED:
        return None
    return enqueue_case_index(db, case_id)


def request_reindex(db: Session, case_id: uuid.UUID, *, scope: str) -> int:
    """Return records to ``pending``. ``scope`` is ``failed``, ``stale`` or ``all``."""
    now = utcnow()
    profile = active_profile(db)
    conditions: list[Any] = [EvidenceIndexState.case_id == case_id]
    if scope == "failed":
        conditions.append(EvidenceIndexState.status.in_([IndexStatus.FAILED, IndexStatus.CANCELED]))
    elif scope == "stale":
        conditions.append(stale_condition(profile.id if profile else None))
    else:
        conditions.append(EvidenceIndexState.status != IndexStatus.INDEXING)
    result = db.execute(
        update(EvidenceIndexState)
        .where(*conditions)
        .values(
            status=IndexStatus.PENDING,
            attempts=0,
            error_code=None,
            error_detail=None,
            cancel_requested_at=None,
            available_at=now,
            queued_at=now,
        )
    )
    # Records being indexed right now finish their current attempt with the current settings.
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def cancel_indexing(db: Session, case_id: uuid.UUID) -> int:
    now = utcnow()
    pending = db.execute(
        update(EvidenceIndexState)
        .where(
            EvidenceIndexState.case_id == case_id,
            EvidenceIndexState.status == IndexStatus.PENDING,
        )
        .values(status=IndexStatus.CANCELED, cancel_requested_at=now, error_code=None)
    )
    running = db.execute(
        update(EvidenceIndexState)
        .where(
            EvidenceIndexState.case_id == case_id,
            EvidenceIndexState.status == IndexStatus.INDEXING,
        )
        .values(cancel_requested_at=now)
    )
    return int((pending.rowcount or 0) + (running.rowcount or 0))  # type: ignore[attr-defined]


def stale_condition(profile_id: uuid.UUID | None) -> Any:
    return and_(
        EvidenceIndexState.status == IndexStatus.INDEXED,
        or_(
            EvidenceIndexState.chunking_version.is_distinct_from(CHUNKING_VERSION),
            EvidenceIndexState.profile_id.is_distinct_from(profile_id),
        ),
    )


def derived_status_expression(profile_id: uuid.UUID | None) -> Any:
    return case((stale_condition(profile_id), "stale"), else_=EvidenceIndexState.status)


def index_counts(db: Session, case_id: uuid.UUID) -> dict[str, int]:
    profile = active_profile(db)
    expression = derived_status_expression(profile.id if profile else None)
    rows = db.execute(
        select(expression, func.count())
        .select_from(EvidenceIndexState)
        .where(EvidenceIndexState.case_id == case_id)
        .group_by(expression)
    ).all()
    counts = {
        status: 0 for status in ("pending", "indexing", "indexed", "stale", "failed", "canceled")
    }
    for status, count in rows:
        counts[str(status)] = int(count)
    counts["total"] = sum(counts.values())
    return counts


# -- worker ------------------------------------------------------------------------------------


@dataclass
class IndexContext:
    session_factory: sessionmaker[Session]
    storage: EvidenceStorage
    settings: Settings
    providers: ProviderSet
    worker_name: str = "worker"
    # Test hook, called after chunks and vectors are computed and before they are written.
    before_write: Callable[[uuid.UUID], None] | None = field(default=None)


@dataclass(frozen=True)
class IndexBatchResult:
    status: str  # skipped | done | more
    indexed: int = 0
    failed: int = 0
    retried: int = 0
    canceled: int = 0


class _LeaseLost(Exception):
    pass


def _case_allows_indexing(db: Session, settings: Settings, case_id: uuid.UUID) -> bool:
    if not settings.ai_enabled:
        return False
    row = db.execute(select(Case.status, Case.ai_mode).where(Case.id == case_id)).one_or_none()
    return row is not None and row[0] in READABLE_CASE_STATUSES and row[1] != AiMode.DISABLED


def _claim(ctx: IndexContext, case_id: uuid.UUID, token: uuid.UUID) -> list[uuid.UUID]:
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        candidates = (
            select(EvidenceIndexState.evidence_id)
            .where(
                EvidenceIndexState.case_id == case_id,
                or_(
                    and_(
                        EvidenceIndexState.status == IndexStatus.PENDING,
                        EvidenceIndexState.available_at <= now,
                    ),
                    and_(
                        EvidenceIndexState.status == IndexStatus.INDEXING,
                        EvidenceIndexState.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(EvidenceIndexState.queued_at, EvidenceIndexState.evidence_id)
            .limit(ctx.settings.ai_index_batch_size)
            .with_for_update(skip_locked=True)
        )
        ids = list(db.scalars(candidates))
        if not ids:
            return []
        db.execute(
            update(EvidenceIndexState)
            .where(EvidenceIndexState.evidence_id.in_(ids))
            .values(
                status=IndexStatus.INDEXING,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=ctx.settings.ai_run_lease_seconds),
                attempts=EvidenceIndexState.attempts + 1,
                started_at=now,
            )
        )
        return ids


def _finish(
    ctx: IndexContext,
    evidence_id: uuid.UUID,
    token: uuid.UUID,
    **values: Any,
) -> None:
    with session_scope(ctx.session_factory) as db:
        db.execute(
            update(EvidenceIndexState)
            .where(
                EvidenceIndexState.evidence_id == evidence_id,
                EvidenceIndexState.lease_token == token,
                EvidenceIndexState.status == IndexStatus.INDEXING,
            )
            .values(lease_token=None, lease_expires_at=None, **values)
        )


def _ensure_profile(ctx: IndexContext, *, digest: str | None, dimensions: int) -> EmbeddingProfile:
    provider = ctx.providers.embeddings
    key = profile_key(provider.name, provider.model, digest)
    with session_scope(ctx.session_factory) as db:
        profile = db.scalar(
            select(EmbeddingProfile).where(EmbeddingProfile.profile_key == key).with_for_update()
        )
        if profile is None:
            db.execute(
                insert(EmbeddingProfile)
                .values(
                    id=uuid.uuid4(),
                    profile_key=key,
                    provider=provider.name,
                    model=provider.model,
                    model_digest=digest,
                    dimensions=dimensions,
                    chunking_version=CHUNKING_VERSION,
                    indexing_version=INDEXING_VERSION,
                )
                .on_conflict_do_nothing(index_elements=["profile_key"])
            )
            profile = db.scalar(
                select(EmbeddingProfile)
                .where(EmbeddingProfile.profile_key == key)
                .with_for_update()
            )
            assert profile is not None
        if profile.dimensions != dimensions:
            raise ProviderError(
                "embedding_dimension_mismatch",
                "The embedding model returned vectors of a different size than this profile; "
                "rebuild the index after changing models.",
            )
        if not profile.active:
            db.execute(
                update(EmbeddingProfile)
                .where(EmbeddingProfile.active.is_(True))
                .values(active=False)
            )
            profile.active = True
            profile.activated_at = utcnow()
            logger.info(
                "embedding_profile_activated",
                extra={
                    "provider": profile.provider,
                    "model": profile.model,
                    "dimensions": dimensions,
                },
            )
        db.flush()
        db.expunge(profile)
        return profile


def _embed_chunks(ctx: IndexContext, chunks: list[ChunkDraft]) -> list[list[float]]:
    vectors: list[list[float]] = []
    size = ctx.settings.ai_embedding_batch_size
    for start in range(0, len(chunks), size):
        batch = chunks[start : start + size]
        result = ctx.providers.embeddings.embed([chunk.text for chunk in batch], purpose="document")
        vectors.extend(result.vectors)
    return vectors


def _write(
    ctx: IndexContext,
    evidence: EvidenceObject,
    token: uuid.UUID,
    chunks: list[ChunkDraft],
    vectors: list[list[float]],
    profile: EmbeddingProfile | None,
) -> str:
    with session_scope(ctx.session_factory) as db:
        state = db.scalar(
            select(EvidenceIndexState)
            .where(EvidenceIndexState.evidence_id == evidence.id)
            .with_for_update()
        )
        if state is None or state.lease_token != token or state.status != IndexStatus.INDEXING:
            raise _LeaseLost
        if state.cancel_requested_at is not None:
            state.status = IndexStatus.CANCELED
            state.lease_token = None
            state.lease_expires_at = None
            return "canceled"
        case_row = db.scalar(
            select(Case).where(Case.id == evidence.case_id).with_for_update(read=True)
        )
        still_there = db.scalar(select(EvidenceObject.id).where(EvidenceObject.id == evidence.id))
        if (
            case_row is None
            or still_there is None
            or case_row.status not in READABLE_CASE_STATUSES
            or case_row.ai_mode == AiMode.DISABLED
            or not ctx.settings.ai_enabled
        ):
            state.status = IndexStatus.CANCELED
            state.lease_token = None
            state.lease_expires_at = None
            return "canceled"
        db.execute(delete(DocumentChunk).where(DocumentChunk.evidence_id == evidence.id))
        rows = []
        embeddings = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk_id = uuid.uuid4()
            rows.append(
                {
                    "id": chunk_id,
                    "case_id": evidence.case_id,
                    "evidence_id": evidence.id,
                    "evidence_sha256": evidence.sha256,
                    "chunking_version": CHUNKING_VERSION,
                    "chunk_index": chunk.index,
                    "kind": chunk.kind,
                    "text": chunk.text,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "json_locations": chunk.json_locations,
                    "search_text": fold_for_search(chunk.text),
                    "identifiers": extract_identifiers(chunk.text),
                }
            )
            embeddings.append(
                {
                    "chunk_id": chunk_id,
                    "profile_id": profile.id if profile else None,
                    "case_id": evidence.case_id,
                    "dimensions": len(vector),
                    "embedding": vector,
                }
            )
        if rows:
            assert profile is not None
            db.execute(insert(DocumentChunk), rows)
            db.execute(insert(ChunkEmbedding), embeddings)
        else:
            # Nothing to embed: record the currently active profile so the record is not stale.
            current = active_profile(db)
            profile_id = current.id if current else None
        state.status = IndexStatus.INDEXED
        state.chunking_version = CHUNKING_VERSION
        state.profile_id = profile.id if profile is not None else profile_id
        state.chunk_count = len(rows)
        state.error_code = None
        state.error_detail = None
        state.indexed_at = utcnow()
        state.lease_token = None
        state.lease_expires_at = None
        return "indexed"


def _retry_or_fail(
    ctx: IndexContext, evidence_id: uuid.UUID, token: uuid.UUID, error: ProviderError, attempts: int
) -> str:
    if error.retryable and attempts < ctx.settings.ai_index_max_attempts:
        delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
        if error.retry_after_seconds:
            delay = max(delay, int(error.retry_after_seconds))
        _finish(
            ctx,
            evidence_id,
            token,
            status=IndexStatus.PENDING,
            error_code=error.code,
            error_detail=error.message[:300],
            available_at=utcnow() + timedelta(seconds=delay),
        )
        return "retried"
    _finish(
        ctx,
        evidence_id,
        token,
        status=IndexStatus.FAILED,
        error_code=error.code,
        error_detail=error.message[:300],
    )
    return "failed"


def _index_one(
    ctx: IndexContext, evidence_id: uuid.UUID, token: uuid.UUID, digest: str | None
) -> str:
    with session_scope(ctx.session_factory) as db:
        evidence = db.get(EvidenceObject, evidence_id)
        attempts = db.scalar(
            select(EvidenceIndexState.attempts).where(EvidenceIndexState.evidence_id == evidence_id)
        )
        cancel = db.scalar(
            select(EvidenceIndexState.cancel_requested_at).where(
                EvidenceIndexState.evidence_id == evidence_id
            )
        )
        if evidence is not None:
            db.expunge(evidence)
    if evidence is None:
        return "canceled"
    if cancel is not None:
        _finish(ctx, evidence_id, token, status=IndexStatus.CANCELED)
        return "canceled"
    try:
        content = ctx.storage.read_verified(
            evidence.storage_key, evidence.sha256, evidence.size_bytes
        )
        chunks = chunk_evidence(
            evidence.kind,
            content,
            target=ctx.settings.ai_chunk_target_chars,
            overlap=ctx.settings.ai_chunk_overlap_chars,
            max_chunks=ctx.settings.ai_max_chunks_per_evidence,
        )
        profile: EmbeddingProfile | None = None
        vectors: list[list[float]] = []
        if chunks:
            vectors = _embed_chunks(ctx, chunks)
            dimensions = len(vectors[0])
            if len(vectors) != len(chunks) or any(len(vector) != dimensions for vector in vectors):
                raise ProviderError(
                    "embedding_dimension_mismatch",
                    "The embedding model returned inconsistent vectors.",
                )
            profile = _ensure_profile(ctx, digest=digest, dimensions=dimensions)
        if ctx.before_write is not None:
            ctx.before_write(evidence_id)
        return _write(ctx, evidence, token, chunks, vectors, profile)
    except IntegrityError as exc:
        _finish(
            ctx,
            evidence_id,
            token,
            status=IndexStatus.FAILED,
            error_code=exc.code,
            error_detail="The stored evidence file failed its integrity check.",
        )
        return "failed"
    except ChunkingError as exc:
        _finish(
            ctx,
            evidence_id,
            token,
            status=IndexStatus.FAILED,
            error_code=exc.code,
            error_detail=str(exc)[:300],
        )
        return "failed"
    except ProviderError as exc:
        return _retry_or_fail(ctx, evidence_id, token, exc, int(attempts or 1))


def index_case(ctx: IndexContext, case_id: uuid.UUID) -> IndexBatchResult:
    with session_scope(ctx.session_factory) as db:
        allowed = _case_allows_indexing(db, ctx.settings, case_id)
    if not allowed:
        with session_scope(ctx.session_factory) as db:
            dispatch.mark_done(db, AggregateType.CASE_INDEX, case_id)
        return IndexBatchResult("skipped")

    provider = ctx.providers.embeddings
    inventory = provider.inventory()
    digest = inventory.models.get(provider.model) if inventory.reachable else None
    token = uuid.uuid4()
    claimed = _claim(ctx, case_id, token)
    counters = {"indexed": 0, "failed": 0, "retried": 0, "canceled": 0}
    if claimed and inventory.reachable and provider.model not in inventory.models:
        error = ProviderError(
            "model_not_found",
            f"The embedding model {provider.model} is not installed in Ollama "
            f"(run: ollama pull {provider.model}).",
        )
        for evidence_id in claimed:
            _finish(
                ctx,
                evidence_id,
                token,
                status=IndexStatus.FAILED,
                error_code=error.code,
                error_detail=error.message[:300],
            )
            counters["failed"] += 1
        claimed = []
    elif claimed and not inventory.reachable:
        error = ProviderError(
            inventory.error_code or "model_unavailable",
            "The local model service could not be reached.",
            retryable=True,
        )
        with session_scope(ctx.session_factory) as db:
            attempts: dict[uuid.UUID, int] = {
                row[0]: row[1]
                for row in db.execute(
                    select(EvidenceIndexState.evidence_id, EvidenceIndexState.attempts).where(
                        EvidenceIndexState.evidence_id.in_(claimed)
                    )
                )
            }
        for evidence_id in claimed:
            counters[
                _retry_or_fail(ctx, evidence_id, token, error, int(attempts.get(evidence_id, 1)))
            ] += 1
        claimed = []

    for evidence_id in claimed:
        try:
            outcome = _index_one(ctx, evidence_id, token, digest)
        except _LeaseLost:
            continue
        counters[outcome] = counters.get(outcome, 0) + 1

    with session_scope(ctx.session_factory) as db:
        now = utcnow()
        remaining = db.scalar(
            select(func.min(EvidenceIndexState.available_at)).where(
                EvidenceIndexState.case_id == case_id,
                EvidenceIndexState.status == IndexStatus.PENDING,
            )
        )
        if remaining is not None and _case_allows_indexing(db, ctx.settings, case_id):
            row = dispatch.enqueue(
                db,
                task_name=dispatch.INDEX_CASE_TASK,
                aggregate_type=AggregateType.CASE_INDEX,
                aggregate_id=case_id,
                case_id=case_id,
            )
            row.available_at = max(now, remaining)
            status = "more"
        else:
            dispatch.mark_done(db, AggregateType.CASE_INDEX, case_id)
            status = "done"
    logger.info(
        "case_index_batch", extra={"case_ref": str(case_id)[:8], "status": status, **counters}
    )
    return IndexBatchResult(status, **counters)
