from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class AccountRole(enum.StrEnum):
    """What an account may do anywhere (docs/security/permissions.md).

    The account role is a ceiling: case membership decides *which* cases an account can open, and
    a viewer account is a viewer in every case whatever its membership says. Administration is a
    system role and grants no access to case content by itself.
    """

    ADMINISTRATOR = "administrator"
    ANALYST = "analyst"
    VIEWER = "viewer"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64))
    # NFKC + casefold form used for uniqueness and login lookups.
    username_normalized: Mapped[str] = mapped_column(String(256), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(
        String(16), default=AccountRole.ANALYST, server_default=AccountRole.ANALYST.value
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    failed_login_count: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    password_changed_at: Mapped[datetime] = mapped_column(server_default=func.now())

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        CheckConstraint("role IN ('administrator', 'analyst', 'viewer')", name="role_valid"),
    )

    @property
    def is_admin(self) -> bool:
        return self.role == AccountRole.ADMINISTRATOR

    @is_admin.setter
    def is_admin(self, value: bool) -> None:
        # Kept for callers written before account roles existed (tests and verification scripts).
        if value:
            self.role = AccountRole.ADMINISTRATOR
        elif self.role in (None, AccountRole.ADMINISTRATOR):
            self.role = AccountRole.ANALYST


class UserSession(Base):
    """Server-side session. Only a SHA-256 digest of the bearer token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_sha256_length"),
    )
