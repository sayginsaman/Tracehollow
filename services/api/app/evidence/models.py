from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EvidenceKind(enum.StrEnum):
    TEXT = "text"
    JSON = "json"


class AcquisitionMethod(enum.StrEnum):
    """How the bytes entered the system. Imports are never presented as collection."""

    AUTHORIZED_IMPORT = "authorized_import"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class EvidenceObject(Base):
    """Immutable metadata for original bytes kept on the evidence volume.

    Content is never overwritten: every import or collected page is a new record with its own
    server-generated storage key. The SHA-256 digest detects changes to stored bytes; it does
    not prove authorship or authenticity.
    """

    __tablename__ = "evidence_objects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    acquisition_method: Mapped[str] = mapped_column(String(32))
    # Analyst-supplied description of where imported material came from.
    import_origin: Mapped[str | None] = mapped_column(Text)
    # Reference to the original location. Recorded only; never fetched by Tracehollow.
    source_reference: Mapped[str | None] = mapped_column(String(2048))
    source_published_at: Mapped[datetime | None]
    # Exactly as supplied (keeps the original offset and precision); the column above is UTC.
    source_published_at_original: Mapped[str | None] = mapped_column(String(64))
    # Retrieval time for collected material, import time for imports.
    collected_at: Mapped[datetime]
    imported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    connector_id: Mapped[str | None] = mapped_column(String(100))
    connector_version: Mapped[str | None] = mapped_column(String(32))
    query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL")
    )
    connector_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_runs.id", ondelete="SET NULL")
    )
    page_index: Mapped[int | None]
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Processing time (when the record was written).
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind IN ('text', 'json')", name="kind_valid"),
        CheckConstraint(
            "acquisition_method IN ('authorized_import', 'synthetic_fixture')",
            name="acquisition_method_valid",
        ),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint(
            "acquisition_method <> 'authorized_import' OR import_origin IS NOT NULL",
            name="import_requires_origin",
        ),
        Index(
            "uq_evidence_objects_connector_page",
            "connector_run_id",
            "page_index",
            unique=True,
            postgresql_where=text("connector_run_id IS NOT NULL"),
        ),
        Index("ix_evidence_objects_case_collected", "case_id", "collected_at"),
        Index("ix_evidence_objects_case_sha256", "case_id", "sha256"),
        Index("ix_evidence_objects_query_run_id", "query_run_id"),
    )
