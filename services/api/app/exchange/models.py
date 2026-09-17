from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StixObjectLink(Base):
    """Which case record an imported STIX object became, so repeated imports reuse it.

    Also used on export: a record that came from STIX keeps its original identifier.
    """

    __tablename__ = "stix_object_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    stix_id: Mapped[str] = mapped_column(String(128))
    record_type: Mapped[str] = mapped_column(String(16))
    record_id: Mapped[uuid.UUID]
    first_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("record_type IN ('entity', 'relationship')", name="record_type_valid"),
        Index("uq_stix_object_links_case_stix", "case_id", "stix_id", unique=True),
        Index("ix_stix_object_links_record", "case_id", "record_type", "record_id"),
    )
