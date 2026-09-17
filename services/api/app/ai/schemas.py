from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.ai.models import AiMode


def _clean(value: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", value).strip()


Question = Annotated[str, Field(min_length=3, max_length=2000), AfterValidator(_clean)]


class ProviderStatusOut(BaseModel):
    provider: str
    location: str
    configured: bool
    reachable: bool | None
    generation_model: str | None
    generation_model_available: bool | None
    embedding_model: str | None
    embedding_model_available: bool | None
    error_code: str | None
    checked_at: datetime


class AiStatusOut(BaseModel):
    enabled: bool
    local_provider: str
    local_location: str
    local_generation_model: str
    local_embedding_model: str
    synthetic: bool
    cloud_provider: str
    cloud_model: str | None
    cloud_configured: bool
    checks: list[ProviderStatusOut]


class IndexCounts(BaseModel):
    pending: int
    indexing: int
    indexed: int
    stale: int
    failed: int
    canceled: int
    total: int


class EmbeddingProfileOut(BaseModel):
    provider: str
    model: str
    dimensions: int
    chunking_version: int
    indexing_version: int
    activated_at: datetime | None
    synthetic: bool


class CaseAiOut(BaseModel):
    enabled: bool
    mode: str
    policy_version: int
    local_location: str
    cloud_available: bool
    index: IndexCounts
    embedding_profile: EmbeddingProfileOut | None
    active_runs: int


class CaseAiSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AiMode
    # Required when allowing cloud processing, so the choice is always explicit.
    acknowledge_cloud_processing: bool = False


class IndexItemOut(BaseModel):
    evidence_id: uuid.UUID
    title: str
    acquisition_method: str
    status: str
    attempts: int
    chunk_count: int
    error_code: str | None
    error_detail: str | None
    queued_at: datetime
    indexed_at: datetime | None
    available_at: datetime


class ReindexIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["failed", "stale", "all"] = "failed"


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Field(max_length=200)] = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question
    location: Literal["local", "cloud"] = "local"


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: Literal["local", "cloud"] = "local"


class AiRunOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    conversation_id: uuid.UUID | None
    run_type: str
    status: str
    stage: str
    question: str | None
    requested_location: str
    provider: str | None
    model: str | None
    processing_location: str | None
    prompt_template_version: str | None
    usage: dict[str, Any]
    coverage: dict[str, Any]
    validation: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    retrieval: dict[str, Any]
    error_code: str | None
    error_detail: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    synthetic: bool


class CitationOut(BaseModel):
    id: uuid.UUID
    label: str
    ref_type: str
    evidence_id: uuid.UUID | None
    evidence_title: str | None
    tool_name: str | None
    source_available: bool


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    kind: str
    content: str
    answer: dict[str, Any] | None
    ai_run_id: uuid.UUID | None
    created_at: datetime
    citations: list[CitationOut] = []


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    runs: list[AiRunOut]


class PassageOut(BaseModel):
    """The exact cited or retrieved location, re-read from the verified original evidence."""

    evidence_id: uuid.UUID | None
    evidence_title: str | None
    acquisition_method: str | None
    synthetic: bool
    collected_at: datetime | None
    source_published_at: datetime | None
    source_published_at_original: str | None
    source_reference: str | None
    kind: str | None
    # available | source_deleted | source_expired | evidence_changed | integrity_failed | reindexed
    status: str
    removed_at: datetime | None = None
    integrity: str | None
    before: str | None = None
    passage: str | None = None
    after: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    json_pointer: str | None = None
    json_value: str | None = None
    chunk_text: str | None = None
    quote: str | None = None
    # Where the passage is in its source: the line in the text record and, for text a processing
    # job derived from a PDF, the page and whether it is embedded text or OCR output.
    line: int | None = None
    page: int | None = None
    text_origin: str | None = None
    derived_from_evidence_id: uuid.UUID | None = None


class CitationDetail(BaseModel):
    id: uuid.UUID
    label: str
    ref_type: str
    claim_index: int
    ai_run_id: uuid.UUID
    tool_name: str | None
    tool_result: dict[str, Any] | None
    passage: PassageOut | None


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    evidence_id: uuid.UUID
    evidence_title: str
    acquisition_method: str
    synthetic: bool
    chunk_index: int
    kind: str
    snippet: str
    matched_by: list[str]


class SearchOut(BaseModel):
    query: str
    hits: list[SearchHit]
    semantic: str
    coverage_notes: list[str]
