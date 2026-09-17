from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CaseStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETING = "deleting"
    DELETION_FAILED = "deletion_failed"


class CaseRole(enum.StrEnum):
    """Role inside one case. Analysts work on the case; viewers read it."""

    ANALYST = "analyst"
    VIEWER = "viewer"


class Case(TimestampMixin, Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text, default="", server_default="")
    scope: Mapped[str] = mapped_column(Text, default="", server_default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.ACTIVE)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None]
    # AI processing policy (disabled | local_only | cloud_allowed). Bumping the version makes
    # queued and running AI work re-check the policy before any further model request.
    ai_mode: Mapped[str] = mapped_column(
        String(16), default="local_only", server_default="local_only"
    )
    ai_policy_version: Mapped[int] = mapped_column(default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived', 'deleting', 'deletion_failed')", name="status_valid"
        ),
        CheckConstraint(
            "ai_mode IN ('disabled', 'local_only', 'cloud_allowed')", name="ai_mode_valid"
        ),
        Index("ix_cases_status", "status"),
    )


class CaseMember(Base):
    """Case-level access: which accounts can open a case, and with which role.

    The effective role is capped by the account role (a viewer account is a viewer everywhere).
    """

    __tablename__ = "case_members"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), default=CaseRole.ANALYST)
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("role IN ('analyst', 'viewer')", name="role_valid"),
        Index("ix_case_members_user_id", "user_id"),
    )


class Note(TimestampMixin, Base):
    """Analyst interpretation attached to a case or to one record inside it."""

    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("relationships.id", ondelete="CASCADE")
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE")
    )
    body: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(entity_id, relationship_id, evidence_id) <= 1", name="single_subject"
        ),
        Index("ix_notes_case_id", "case_id"),
    )


class DeletionStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CaseDeletion(Base):
    """Observable, retryable deletion job. Deliberately keeps no case content (not even the
    title) and has no foreign key to the case, so it outlives the deleted records."""

    __tablename__ = "case_deletions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID]
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default=DeletionStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    progress_note: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(64))
    removed_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    requested_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    lease_expires_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="status_valid"
        ),
        Index("ix_case_deletions_case_id", "case_id"),
        Index("ix_case_deletions_requested_by_user_id", "requested_by_user_id"),
    )
