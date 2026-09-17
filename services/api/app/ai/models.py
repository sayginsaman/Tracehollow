"""Derived retrieval data and AI execution records.

Everything here is derived from, or refers to, case records. It never replaces original
evidence: chunks are copies of verified evidence text with their exact source location, and
answers keep the provider, model, prompt version, retrieved context and validated citations
needed to reproduce and review them. All rows are case-owned and removed with the case.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.ai.vector import Vector
from app.db.base import Base, TimestampMixin


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class AiMode(enum.StrEnum):
    """Case-level processing policy, enforced on the server before any model request."""

    DISABLED = "disabled"
    LOCAL_ONLY = "local_only"
    CLOUD_ALLOWED = "cloud_allowed"


class IndexStatus(enum.StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"
    CANCELED = "canceled"


class AiRunType(enum.StrEnum):
    ANSWER = "answer"
    SUMMARY = "summary"
    RELATIONSHIP_SUGGESTIONS = "relationship_suggestions"


class AiRunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_AI_RUN_STATUSES = (AiRunStatus.COMPLETED, AiRunStatus.FAILED, AiRunStatus.CANCELED)


class ProcessingLocation(enum.StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"
    FIXTURE = "fixture"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageKind(enum.StrEnum):
    QUESTION = "question"
    ANSWER = "answer"
    SUMMARY = "summary"
    SUGGESTIONS = "suggestions"


class CitationStatus(enum.StrEnum):
    ACCEPTED = "accepted"
    REJECTED_UNKNOWN_REFERENCE = "rejected_unknown_reference"
    REJECTED_QUOTE_NOT_FOUND = "rejected_quote_not_found"


class EmbeddingProfile(Base):
    """One embedding configuration. Vectors from different profiles are never compared."""

    __tablename__ = "embedding_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_key: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    model_digest: Mapped[str | None] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)
    chunking_version: Mapped[int] = mapped_column(Integer)
    indexing_version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    activated_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("dimensions > 0 AND dimensions <= 16000", name="dimensions_range"),
        Index(
            "uq_embedding_profiles_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class DocumentChunk(Base):
    """A contiguous piece of verified evidence text with its exact source location.

    Text chunks are exact slices of the decoded original (``char_start``/``char_end`` are
    character offsets). JSON chunks are a flattened ``pointer: value`` rendering; each line's
    RFC 6901 pointer and its range within ``text`` are kept in ``json_locations``.
    """

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE")
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    chunking_version: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    json_locations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Case- and accent-folded copy used only for full-text matching.
    search_text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True)
    )
    # Normalized exact identifiers ("domain:ornek.example", "sha256:…") found in the text.
    identifiers: Mapped[list[str]] = mapped_column(
        ARRAY(String(512)), default=list, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind IN ('text', 'json')", name="kind_valid"),
        CheckConstraint(
            "(kind = 'text' AND char_start IS NOT NULL AND char_end > char_start)"
            " OR (kind = 'json' AND json_locations IS NOT NULL)",
            name="location_present",
        ),
        Index(
            "uq_document_chunks_evidence_version_index",
            "evidence_id",
            "chunking_version",
            "chunk_index",
            unique=True,
        ),
        Index("ix_document_chunks_case_id", "case_id"),
        Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_document_chunks_identifiers", "identifiers", postgresql_using="gin"),
    )


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("embedding_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("vector_dims(embedding) = dimensions", name="dimensions_match"),
        Index("ix_chunk_embeddings_case_profile", "case_id", "profile_id"),
    )


class EvidenceIndexState(Base):
    """Indexing lifecycle of one evidence record. ``stale`` is derived: an indexed record whose
    chunking version or embedding profile no longer matches the active configuration."""

    __tablename__ = "evidence_index_states"

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE"), primary_key=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default=IndexStatus.PENDING)
    chunking_version: Mapped[int | None] = mapped_column(Integer)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("embedding_profiles.id", ondelete="SET NULL")
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(300))
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    cancel_requested_at: Mapped[datetime | None]
    available_at: Mapped[datetime] = mapped_column(server_default=func.now())
    queued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    indexed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(_in("status", IndexStatus), name="status_valid"),
        Index("ix_evidence_index_states_case_status", "case_id", "status"),
    )


class AiConversation(TimestampMixin, Base):
    __tablename__ = "ai_conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200))

    __table_args__ = (Index("ix_ai_conversations_case_updated", "case_id", "updated_at"),)


class AiRun(Base):
    """One model-backed operation with everything needed to review how its output was made."""

    __tablename__ = "ai_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE")
    )
    run_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=AiRunStatus.QUEUED)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    question: Mapped[str | None] = mapped_column(Text)
    # Case policy version when requested; a changed policy stops the run before model calls.
    policy_version: Mapped[int] = mapped_column(Integer)
    requested_location: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(200))
    processing_location: Mapped[str | None] = mapped_column(String(16))
    prompt_template_version: Mapped[str | None] = mapped_column(String(32))
    retrieval: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(300))
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    claim_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested_at: Mapped[datetime | None]
    queued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(_in("run_type", AiRunType), name="run_type_valid"),
        CheckConstraint(_in("status", AiRunStatus), name="status_valid"),
        CheckConstraint(_in("requested_location", ProcessingLocation), name="requested_valid"),
        CheckConstraint(
            "processing_location IS NULL OR " + _in("processing_location", ProcessingLocation),
            name="processing_location_valid",
        ),
        CheckConstraint(
            "run_type <> 'answer' OR (question IS NOT NULL AND conversation_id IS NOT NULL)",
            name="answer_has_question",
        ),
        Index("ix_ai_runs_case_queued", "case_id", "queued_at"),
        Index("ix_ai_runs_conversation", "conversation_id"),
    )


class AiMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE")
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Validated structured output: status, claims with citation labels, limitations.
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("role", MessageRole), name="role_valid"),
        CheckConstraint(_in("kind", MessageKind), name="kind_valid"),
        Index("ix_ai_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_ai_messages_run", "ai_run_id"),
    )


class AiCitation(Base):
    """A reference the model made to context it was given, and the server's verdict on it."""

    __tablename__ = "ai_citations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    ai_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_messages.id", ondelete="CASCADE")
    )
    claim_index: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(16))
    ref_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="SET NULL")
    )
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    quote: Mapped[str | None] = mapped_column(Text)
    # Exact location of the verified quote in the original evidence text (text evidence).
    source_char_start: Mapped[int | None] = mapped_column(Integer)
    source_char_end: Mapped[int | None] = mapped_column(Integer)
    # RFC 6901 pointer of the JSON value containing the quote (JSON evidence).
    json_pointer: Mapped[str | None] = mapped_column(String(2048))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    # Why the cited evidence no longer exists: "deleted" (by an analyst) or "retention".
    source_removed_reason: Mapped[str | None] = mapped_column(String(16))
    source_removed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("status", CitationStatus), name="status_valid"),
        CheckConstraint("ref_type IN ('chunk', 'tool')", name="ref_type_valid"),
        Index("ix_ai_citations_run", "ai_run_id"),
        Index("ix_ai_citations_evidence", "evidence_id"),
    )


class AiProviderStatus(Base):
    """Last connectivity check of a configured provider, written by the AI worker."""

    __tablename__ = "ai_provider_status"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    location: Mapped[str] = mapped_column(String(16))
    configured: Mapped[bool] = mapped_column(Boolean)
    reachable: Mapped[bool | None] = mapped_column(Boolean)
    generation_model: Mapped[str | None] = mapped_column(String(200))
    generation_model_available: Mapped[bool | None] = mapped_column(Boolean)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_model_available: Mapped[bool | None] = mapped_column(Boolean)
    embedding_model_digest: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (CheckConstraint(_in("location", ProcessingLocation), name="location_valid"),)
