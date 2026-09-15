from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.entities.models import EntityType, IdentifierType, ReviewStatus, Stance

SUGGESTED_PREDICATES = (
    "associated_with",
    "controls",
    "hosts",
    "links_to",
    "member_of",
    "mentions",
    "owns",
    "registered_by",
    "resolves_to",
    "uses",
)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


Name = Annotated[str, Field(min_length=1, max_length=300), AfterValidator(_nfc)]
LongText = Annotated[str, Field(max_length=10_000), AfterValidator(_nfc)]
Predicate = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


class IdentifierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier_type: IdentifierType
    value: Annotated[str, Field(min_length=1, max_length=2048)]
    platform: Annotated[str | None, Field(max_length=100)] = None

    @model_validator(mode="after")
    def _platform_rules(self) -> IdentifierIn:
        if self.identifier_type == IdentifierType.PLATFORM_ID and not self.platform:
            raise ValueError("platform is required for platform_id identifiers")
        return self


class IdentifierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    identifier_type: str
    platform: str | None
    original_value: str
    normalized_value: str
    created_at: datetime


class EntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    display_name: Name
    description: LongText = ""
    attributes: Annotated[dict[str, Any], Field(max_length=100)] = Field(default_factory=dict)
    identifiers: Annotated[list[IdentifierIn], Field(max_length=50)] = Field(default_factory=list)


class EntityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Name | None = None
    description: LongText | None = None
    attributes: Annotated[dict[str, Any] | None, Field(max_length=100)] = None


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    entity_type: str
    display_name: str
    description: str
    attributes: dict[str, Any]
    origin: str
    created_by_query_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class EntitySummary(EntityOut):
    identifiers: list[IdentifierOut]


class SharedIdentifier(BaseModel):
    """Another entity with the same normalized identifier. Informational only: never merged."""

    identifier_type: str
    normalized_value: str
    entity_id: uuid.UUID
    display_name: str
    entity_type: str


class EvidenceLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: uuid.UUID
    note: Annotated[str | None, Field(max_length=2000)] = None


class EntityEvidenceOut(BaseModel):
    link_id: uuid.UUID
    evidence_id: uuid.UUID
    title: str
    acquisition_method: str
    sha256: str
    note: str | None
    created_at: datetime


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    entity_id: uuid.UUID | None
    evidence_id: uuid.UUID | None
    query_run_id: uuid.UUID | None
    connector_run_id: uuid.UUID | None
    observation_type: str
    source_object_id: str | None
    payload: dict[str, Any]
    collected_at: datetime
    event_time: datetime | None
    source_published_at: datetime | None
    created_at: datetime


class RelationshipEntityRef(BaseModel):
    id: uuid.UUID
    display_name: str
    entity_type: str


class RelationshipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_entity_id: uuid.UUID
    target_entity_id: uuid.UUID
    predicate: Predicate
    description: LongText = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supporting_evidence_ids: Annotated[list[uuid.UUID], Field(max_length=50)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _check(self) -> RelationshipCreate:
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("source and target entities must differ")
        for value in (self.valid_from, self.valid_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("timestamps must include a timezone offset")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        return self


class RelationshipUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: LongText | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class ReviewDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus
    rationale: Annotated[str | None, Field(max_length=5000)] = None


class RelationshipReferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: uuid.UUID | None = None
    observation_id: uuid.UUID | None = None
    stance: Stance = Stance.SUPPORTS
    note: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def _one_reference(self) -> RelationshipReferenceIn:
        if self.evidence_id is None and self.observation_id is None:
            raise ValueError("evidence_id or observation_id is required")
        return self


class RelationshipReferenceOut(BaseModel):
    id: uuid.UUID
    stance: str
    note: str | None
    evidence_id: uuid.UUID | None
    evidence_title: str | None
    evidence_acquisition_method: str | None
    evidence_sha256: str | None
    observation_id: uuid.UUID | None
    observation_type: str | None
    observation_collected_at: datetime | None
    query_run_id: uuid.UUID | None
    created_at: datetime


class AnalystDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision_type: str
    previous_value: str
    new_value: str
    rationale: str | None
    decided_at: datetime


class RelationshipOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    source: RelationshipEntityRef
    target: RelationshipEntityRef
    predicate: str
    origin: str
    review_status: str
    description: str
    valid_from: datetime | None
    valid_to: datetime | None
    created_by_query_run_id: uuid.UUID | None
    reference_count: int
    created_at: datetime
    updated_at: datetime


class RelationshipDetail(RelationshipOut):
    references: list[RelationshipReferenceOut]
    decisions: list[AnalystDecisionOut]


class EntityDetail(BaseModel):
    entity: EntitySummary
    linked_evidence: list[EntityEvidenceOut]
    relationships: list[RelationshipOut]
    shared_identifiers: list[SharedIdentifier]
    observation_count: int


class GraphNode(BaseModel):
    id: uuid.UUID
    label: str
    entity_type: str
    origin: str


class GraphEdge(BaseModel):
    id: uuid.UUID
    source: uuid.UUID
    target: uuid.UUID
    predicate: str
    origin: str
    review_status: str


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    focus_entity_id: uuid.UUID | None
    depth: int
    max_nodes: int
    truncated: bool
    total_entities: int
    total_relationships: int
