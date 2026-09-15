from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.auth.security import normalize_username


def _clean_tag(value: str) -> str:
    tag = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(tag) <= 64:
        raise ValueError("tags must be 1-64 characters")
    if any(unicodedata.category(ch)[0] == "C" for ch in tag):
        raise ValueError("tags must not contain control characters")
    return tag


def _clean_tags(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        tag = _clean_tag(value)
        seen.setdefault(normalize_username(tag), tag)
    return list(seen.values())


def _clean_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


Title = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_clean_text)]
LongText = Annotated[str, Field(max_length=10_000), AfterValidator(_clean_text)]
Tags = Annotated[list[str], Field(max_length=50), AfterValidator(_clean_tags)]


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    purpose: LongText = ""
    scope: LongText = ""
    tags: Tags = Field(default_factory=list)


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    purpose: LongText | None = None
    scope: LongText | None = None
    tags: Tags | None = None


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    purpose: str
    scope: str
    tags: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class CaseCounts(BaseModel):
    entities: int
    relationships: int
    evidence: int
    notes: int
    saved_queries: int
    query_runs: int
    active_runs: int


class CaseDetail(CaseOut):
    counts: CaseCounts


class NoteSubject(BaseModel):
    entity_id: uuid.UUID | None = None
    relationship_id: uuid.UUID | None = None
    evidence_id: uuid.UUID | None = None


class NoteCreate(NoteSubject):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, Field(min_length=1, max_length=20_000), AfterValidator(_clean_text)]


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, Field(min_length=1, max_length=20_000), AfterValidator(_clean_text)]


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    entity_id: uuid.UUID | None
    relationship_id: uuid.UUID | None
    evidence_id: uuid.UUID | None
    body: str
    created_at: datetime
    updated_at: datetime


class DeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_title: Annotated[str, Field(min_length=1, max_length=200)]


class CaseDeletionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    status: str
    attempts: int
    progress_note: str | None
    error_code: str | None
    removed_counts: dict[str, Any]
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
