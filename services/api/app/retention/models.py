from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RetentionJobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CaseRetentionPolicy(Base):
    """Analyst-activated expiry rules for one case. Without a row nothing expires."""

    __tablename__ = "case_retention_policies"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    active: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Results (evidence, derived text, observations, index data) of executions that finished
    # longer ago than this. The latest usable collection of each query and connector is kept.
    collected_results_max_age_days: Mapped[int | None]
    # Authorized imports (originals and everything derived from them) imported longer ago.
    imported_evidence_max_age_days: Mapped[int | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None]
    last_applied_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "collected_results_max_age_days IS NULL OR collected_results_max_age_days >= 1",
            name="collected_age_positive",
        ),
        CheckConstraint(
            "imported_evidence_max_age_days IS NULL OR imported_evidence_max_age_days >= 1",
            name="imported_age_positive",
        ),
    )


class RetentionJob(Base):
    """One durable, retryable application of a case's retention rules."""

    __tablename__ = "retention_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(16))
    # One scheduled job per case per UTC day.
    occurrence_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=RetentionJobStatus.QUEUED)
    policy_version: Mapped[int]
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None]
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    removed: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Records left for a later run because work was active on them.
    deferred: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    progress_note: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="status_valid"
        ),
        CheckConstraint("trigger IN ('scheduled', 'manual', 'activation')", name="trigger_valid"),
        Index("uq_retention_jobs_occurrence", "occurrence_key", unique=True),
        Index("ix_retention_jobs_case_created", "case_id", "created_at"),
    )


class RetentionTombstone(Base):
    """A record removed by retention, kept without its content so references can explain it."""

    __tablename__ = "retention_tombstones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    record_type: Mapped[str] = mapped_column(String(32))
    record_id: Mapped[uuid.UUID]
    # For relationship references: the relationship that lost a supporting reference.
    parent_id: Mapped[uuid.UUID | None]
    retention_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("retention_jobs.id", ondelete="SET NULL")
    )
    policy_version: Mapped[int]
    rule: Mapped[str] = mapped_column(String(32))
    # Non-content facts only (kind, acquisition method, hash, collection time).
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    expired_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "record_type IN ('evidence', 'relationship_reference', 'query_run_results')",
            name="record_type_valid",
        ),
        Index("uq_retention_tombstones_record", "case_id", "record_type", "record_id", unique=True),
        Index("ix_retention_tombstones_parent", "case_id", "parent_id"),
    )
