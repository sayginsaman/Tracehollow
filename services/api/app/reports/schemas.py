from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.entities.models import IdentifierType

RedactTerm = Annotated[str, Field(min_length=2, max_length=200)]


class ReportSelection(BaseModel):
    """What goes into a report. Nothing is included unless it is selected here."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Field(max_length=200)] = None
    include_case_purpose: bool = True
    entity_ids: Annotated[list[uuid.UUID], Field(max_length=50)] = Field(default_factory=list)
    relationship_ids: Annotated[list[uuid.UUID], Field(max_length=100)] = Field(
        default_factory=list
    )
    evidence_ids: Annotated[list[uuid.UUID], Field(max_length=100)] = Field(default_factory=list)
    comparison_entity_ids: Annotated[list[uuid.UUID], Field(max_length=4)] = Field(
        default_factory=list
    )
    ai_message_ids: Annotated[list[uuid.UUID], Field(max_length=20)] = Field(default_factory=list)
    note_ids: Annotated[list[uuid.UUID], Field(max_length=100)] = Field(default_factory=list)
    include_timeline: bool = False
    timeline_entity_id: uuid.UUID | None = None
    timeline_limit: Annotated[int, Field(ge=1, le=100)] = 50
    include_coverage: bool = True
    excerpt_chars: Annotated[int, Field(ge=200, le=4000)] = 1200
    # Literal strings replaced everywhere in the report (case-insensitive).
    redact_terms: Annotated[list[RedactTerm], Field(max_length=50)] = Field(default_factory=list)
    # Every value of these identifier types known in the case is replaced.
    redact_identifier_types: Annotated[list[IdentifierType], Field(max_length=10)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _comparison_size(self) -> ReportSelection:
        if self.comparison_entity_ids and not 2 <= len(set(self.comparison_entity_ids)) <= 4:
            raise ValueError("a comparison needs 2 to 4 different entities")
        return self


class ReportPreview(BaseModel):
    counts: dict[str, int]
    redactions_applied: int
    credential_like_values_removed: int
    warnings: list[str]
    size_bytes: int
    # Complete report markup, for display in a sandboxed frame with scripts disabled.
    html: str


class SelectableItem(BaseModel):
    id: uuid.UUID
    label: str
    detail: str


class ReportSelectable(BaseModel):
    """Candidates for a report selection, newest first, at most 200 of each."""

    entities: list[SelectableItem]
    relationships: list[SelectableItem]
    evidence: list[SelectableItem]
    ai_answers: list[SelectableItem]
    notes: list[SelectableItem]
