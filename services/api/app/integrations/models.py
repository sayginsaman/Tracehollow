from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class IntegrationCredential(TimestampMixin, Base):
    """A connector credential, encrypted with a key held outside PostgreSQL.

    Only ciphertext is stored. The plaintext is decrypted in the collector when a run needs it
    and is never returned by the API, logged or written to evidence.
    """

    __tablename__ = "integration_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(16))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_used_at: Mapped[datetime | None]
    # Outcome of the last run that used the credential, e.g. "accepted" or "rejected".
    last_result: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("uq_integration_credentials_connector_name", "connector_id", "name", unique=True),
        CheckConstraint("octet_length(nonce) = 12", name="nonce_length"),
    )


class SourceSlot(Base):
    """Lease-based concurrency slot per connector, shared by all collector processes."""

    __tablename__ = "source_slots"

    connector_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    slot: Mapped[int] = mapped_column(primary_key=True)
    holder: Mapped[uuid.UUID]
    expires_at: Mapped[datetime]


class SourcePacing(Base):
    """Earliest time the next request to a pacing key (a host or an API) may start."""

    __tablename__ = "source_pacing"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    next_allowed_at: Mapped[datetime]
