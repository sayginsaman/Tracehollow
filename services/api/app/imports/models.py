"""Durable processing of imported originals (chat exports, documents).

A processing job turns one stored original into derived records: normalized chat text and
messages, attachments, extracted or OCR text. Jobs run in the ``worker`` service, which has no
route to the internet, so a parser cannot fetch external resources even if a document asks it
to. Like query executions, jobs are claimed with a lease, retried after a lost worker, and
cancelled through the database; every derived record written by a job carries the job id and a
part name that is unique per job, so a repeated delivery never duplicates results.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessingJobType(enum.StrEnum):
    WHATSAPP_EXPORT = "whatsapp_export"
    DOCUMENT_TEXT = "document_text"


class ProcessingStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    # The original is kept, but nothing is derived until the analyst answers a question
    # (for example which date order an ambiguous export uses).
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    # Usable results with a stated gap (pages over the limit, OCR unavailable, missing files).
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"


ACTIVE_STATUSES = (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING, ProcessingStatus.NEEDS_INPUT)
TERMINAL_STATUSES = (
    ProcessingStatus.COMPLETED,
    ProcessingStatus.PARTIAL,
    ProcessingStatus.FAILED,
    ProcessingStatus.CANCELED,
)


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    # The stored original this job reads. Deleting it deletes the job.
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="CASCADE")
    )
    job_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=ProcessingStatus.QUEUED)
    # Analyst choices (date order, timezone, OCR); validated per job type.
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Counts, versions of the parsers used, limitations and per-part states.
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # What the analyst must decide before processing can continue (status needs_input).
    needs_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    worker_name: Mapped[str | None] = mapped_column(String(255))
    cancel_requested_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(_in("job_type", ProcessingJobType), name="job_type_valid"),
        CheckConstraint(_in("status", ProcessingStatus), name="status_valid"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # One live job per original and type: resubmitting returns the existing job.
        Index(
            "uq_processing_jobs_active",
            "evidence_id",
            "job_type",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'needs_input')"),
        ),
        Index("ix_processing_jobs_case_created", "case_id", "created_at"),
        Index("ix_processing_jobs_status_lease", "status", "lease_expires_at"),
    )
