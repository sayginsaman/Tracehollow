from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    kind: str
    title: str
    original_filename: str | None
    content_type: str
    size_bytes: int
    sha256: str
    acquisition_method: str
    import_origin: str | None
    source_reference: str | None
    source_published_at: datetime | None
    source_published_at_original: str | None
    collected_at: datetime
    created_at: datetime
    connector_id: str | None
    connector_version: str | None
    query_run_id: uuid.UUID | None
    connector_run_id: uuid.UUID | None
    page_index: int | None
    description: str
    synthetic: bool
    collection_mode: str | None = None
    access_category: str | None = None
    derived_from_evidence_id: uuid.UUID | None = None
    collection_metadata: dict[str, Any] = Field(default_factory=dict)


class ImportResult(BaseModel):
    evidence: EvidenceOut
    filename_sanitized: bool
    duplicate_of: list[uuid.UUID]


class LinkedEntity(BaseModel):
    link_id: uuid.UUID
    entity_id: uuid.UUID
    display_name: str
    entity_type: str
    note: str | None


class LinkedRelationship(BaseModel):
    reference_id: uuid.UUID
    relationship_id: uuid.UUID
    predicate: str
    stance: str
    source_entity_id: uuid.UUID
    target_entity_id: uuid.UUID


class Integrity(BaseModel):
    status: (
        str  # verified | evidence_file_missing | evidence_size_mismatch | evidence_hash_mismatch
    )
    checked_at: datetime


class EvidenceDetail(BaseModel):
    evidence: EvidenceOut
    integrity: Integrity
    linked_entities: list[LinkedEntity]
    linked_relationships: list[LinkedRelationship]
    observation_count: int
    duplicate_of: list[uuid.UUID]
    index: EvidenceIndexOut | None = None
    # Evidence derived from this record (extracted text, parsed entries).
    derived_evidence: list[uuid.UUID] = Field(default_factory=list)


class EvidencePreview(BaseModel):
    """Escaped-text preview. Clients must render ``text`` as plain text, never as markup."""

    evidence_id: uuid.UUID
    kind: str
    encoding: str
    text: str
    pretty_json: str | None
    truncated: bool
    preview_bytes: int
    size_bytes: int


class EvidenceDeletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_title: Annotated[str, Field(min_length=1, max_length=300)]


class EvidenceDeletionOut(BaseModel):
    evidence_id: uuid.UUID
    removed_chunks: int
    removed_relationship_references: int
    removed_entity_links: int
    removed_notes: int
    affected_citations: int


class EvidenceIndexOut(BaseModel):
    status: str
    chunk_count: int
    attempts: int
    error_code: str | None
    error_detail: str | None
    indexed_at: datetime | None
