from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EntityType(enum.StrEnum):
    ORGANIZATION = "organization"
    DOMAIN = "domain"
    IP = "ip"
    URL = "url"
    USERNAME = "username"
    PLATFORM_ACCOUNT = "platform_account"
    EMAIL = "email"
    PHONE = "phone"
    DOCUMENT = "document"
    EVENT = "event"


class IdentifierType(enum.StrEnum):
    DOMAIN = "domain"
    EMAIL = "email"
    USERNAME = "username"
    PLATFORM_ID = "platform_id"
    URL = "url"
    IP = "ip"
    PHONE = "phone"
    NAME = "name"
    OTHER = "other"


class Origin(enum.StrEnum):
    OBSERVED = "observed"
    DETERMINISTIC_DERIVATION = "deterministic_derivation"
    AI_SUGGESTION = "ai_suggestion"
    ANALYST_ASSERTION = "analyst_assertion"
    # Received from another tool through an exchange format (STIX). Not verified by Tracehollow.
    IMPORTED = "imported"


class ReviewStatus(enum.StrEnum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Stance(enum.StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Entity(TimestampMixin, Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    origin: Mapped[str] = mapped_column(String(32))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(_in("entity_type", EntityType), name="entity_type_valid"),
        CheckConstraint(_in("origin", Origin), name="origin_valid"),
        Index("ix_entities_case_id_entity_type", "case_id", "entity_type"),
    )


class EntityIdentifier(Base):
    """Original and normalized identifier values. Only stable platform IDs are unique per case;
    shared usernames, emails or names never merge entities automatically."""

    __tablename__ = "entity_identifiers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    identifier_type: Mapped[str] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(100))
    original_value: Mapped[str] = mapped_column(String(2048))
    normalized_value: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("identifier_type", IdentifierType), name="identifier_type_valid"),
        CheckConstraint(
            "identifier_type <> 'platform_id' OR platform IS NOT NULL",
            name="platform_id_requires_platform",
        ),
        Index(
            "uq_entity_identifiers_entity_value",
            "entity_id",
            "identifier_type",
            "platform",
            "normalized_value",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "uq_entity_identifiers_platform_id",
            "case_id",
            "platform",
            "normalized_value",
            unique=True,
            postgresql_where=text("identifier_type = 'platform_id'"),
        ),
        Index("ix_entity_identifiers_lookup", "case_id", "identifier_type", "normalized_value"),
        Index("ix_entity_identifiers_entity_id", "entity_id"),
    )


class Observation(Base):
    """A source-specific, timestamped fact retained separately from its raw evidence."""

    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE")
    )
    query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE")
    )
    connector_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_runs.id", ondelete="CASCADE")
    )
    observation_type: Mapped[str] = mapped_column(String(64))
    source_object_id: Mapped[str | None] = mapped_column(String(512))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    collected_at: Mapped[datetime]
    event_time: Mapped[datetime | None]
    source_published_at: Mapped[datetime | None]
    idempotency_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("uq_observations_case_idempotency", "case_id", "idempotency_key", unique=True),
        Index("ix_observations_entity_id", "entity_id"),
        Index("ix_observations_query_run_id", "query_run_id"),
    )


class Relationship(TimestampMixin, Base):
    __tablename__ = "relationships"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    predicate: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(32), default=ReviewStatus.UNREVIEWED)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    valid_from: Mapped[datetime | None]
    valid_to: Mapped[datetime | None]
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL")
    )
    created_by_ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(_in("origin", Origin), name="origin_valid"),
        CheckConstraint(_in("review_status", ReviewStatus), name="review_status_valid"),
        CheckConstraint("source_entity_id <> target_entity_id", name="distinct_endpoints"),
        CheckConstraint("predicate ~ '^[a-z][a-z0-9_]{1,63}$'", name="predicate_format"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_period",
        ),
        Index(
            "uq_relationships_observed_edge",
            "case_id",
            "source_entity_id",
            "target_entity_id",
            "predicate",
            unique=True,
            postgresql_where=text("origin = 'observed'"),
        ),
        Index("ix_relationships_source", "case_id", "source_entity_id"),
        Index("ix_relationships_target", "case_id", "target_entity_id"),
    )


class RelationshipEvidence(Base):
    """Supporting or contradicting evidence/observation references for a relationship."""

    __tablename__ = "relationship_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    relationship_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("relationships.id", ondelete="CASCADE")
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE")
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE")
    )
    stance: Mapped[str] = mapped_column(String(16), default=Stance.SUPPORTS)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("stance", Stance), name="stance_valid"),
        CheckConstraint("num_nonnulls(evidence_id, observation_id) >= 1", name="has_reference"),
        Index(
            "uq_relationship_evidence_reference",
            "relationship_id",
            "evidence_id",
            "observation_id",
            "stance",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class EntityEvidence(Base):
    __tablename__ = "entity_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("uq_entity_evidence_link", "entity_id", "evidence_id", unique=True),
        Index("ix_entity_evidence_evidence_id", "evidence_id"),
    )


class AnalystDecision(Base):
    """History of analyst review decisions; relationships only show the current state."""

    __tablename__ = "analyst_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    relationship_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("relationships.id", ondelete="CASCADE")
    )
    decision_type: Mapped[str] = mapped_column(String(32), default="review_status")
    previous_value: Mapped[str] = mapped_column(String(32))
    new_value: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index("ix_analyst_decisions_relationship_id", "relationship_id"),)
