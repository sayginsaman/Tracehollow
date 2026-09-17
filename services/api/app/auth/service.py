from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, or_, select, text, update
from sqlalchemy.orm import Session

from app.auth import security
from app.auth.models import AccountRole, User, UserSession
from app.config import Settings
from app.db.base import utcnow

logger = logging.getLogger(__name__)

# Serializes first-run administrator creation across concurrent requests and processes.
_BOOTSTRAP_LOCK_KEY = 7_311_402_115
_LOCKOUT_BASE_SECONDS = 30
_LOCKOUT_MAX_SECONDS = 15 * 60
_LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=60)


class SetupAlreadyCompletedError(Exception):
    pass


class UsernameTakenError(Exception):
    pass


class AccountLockedError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("account temporarily locked")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    session: UserSession
    user: User


def setup_required(db: Session) -> bool:
    return not db.scalar(select(exists().where(User.role == AccountRole.ADMINISTRATOR)))


def create_initial_admin(db: Session, *, username: str, password: str) -> User:
    """Create the first administrator. Refuses if any administrator already exists."""
    clean_username = security.validate_username(username)
    security.validate_password(password, username=clean_username)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _BOOTSTRAP_LOCK_KEY})
    if not setup_required(db):
        raise SetupAlreadyCompletedError
    normalized = security.normalize_username(clean_username)
    if db.scalar(select(exists().where(User.username_normalized == normalized))):
        raise UsernameTakenError
    user = User(
        username=clean_username,
        username_normalized=normalized,
        password_hash=security.hash_password(password),
        role=AccountRole.ADMINISTRATOR,
        password_changed_at=utcnow(),
    )
    db.add(user)
    db.flush()
    logger.info("administrator_created", extra={"user_id": str(user.id)})
    return user


def create_account(db: Session, *, username: str, password: str, role: AccountRole) -> User:
    """Create a local account (administrator action). Raises UsernameTakenError or ValueError."""
    clean_username = security.validate_username(username)
    security.validate_password(password, username=clean_username)
    normalized = security.normalize_username(clean_username)
    if db.scalar(select(exists().where(User.username_normalized == normalized))):
        raise UsernameTakenError
    user = User(
        username=clean_username,
        username_normalized=normalized,
        password_hash=security.hash_password(password),
        role=role,
        password_changed_at=utcnow(),
    )
    db.add(user)
    db.flush()
    logger.info("account_created", extra={"user_id": str(user.id), "role": str(role)})
    return user


def revoke_all_sessions(db: Session, user_id: uuid.UUID, *, keep: uuid.UUID | None = None) -> None:
    conditions = [UserSession.user_id == user_id, UserSession.revoked_at.is_(None)]
    if keep is not None:
        conditions.append(UserSession.id != keep)
    db.execute(update(UserSession).where(*conditions).values(revoked_at=utcnow()))


def set_password(
    db: Session, user: User, new_password: str, *, keep_session: uuid.UUID | None = None
) -> None:
    """Set a new password, clear lockout and revoke sessions (except ``keep_session``)."""
    security.validate_password(new_password, username=user.username)
    user.password_hash = security.hash_password(new_password)
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    revoke_all_sessions(db, user.id, keep=keep_session)


def reset_password(db: Session, *, username: str, new_password: str) -> User | None:
    """Explicit operator action: set a new password, clear lockout, revoke all sessions."""
    user = db.scalar(
        select(User).where(User.username_normalized == security.normalize_username(username))
    )
    if user is None:
        return None
    security.validate_password(new_password, username=user.username)
    now = utcnow()
    user.password_hash = security.hash_password(new_password)
    user.password_changed_at = now
    user.failed_login_count = 0
    user.locked_until = None
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    logger.info("password_reset", extra={"user_id": str(user.id)})
    return user


def authenticate(db: Session, settings: Settings, *, username: str, password: str) -> User | None:
    """Return the user for valid credentials, None otherwise.

    Raises AccountLockedError while an account is locked after repeated failures.
    """
    now = utcnow()
    user = db.scalar(
        select(User)
        .where(User.username_normalized == security.normalize_username(username))
        .with_for_update()
    )
    if user is not None and user.locked_until is not None and user.locked_until > now:
        security.verify_password(None, password)  # keep timing comparable
        retry_after = max(1, int((user.locked_until - now).total_seconds()))
        raise AccountLockedError(retry_after)

    valid = security.verify_password(user.password_hash if user else None, password)
    if user is None or not user.is_active:
        logger.info("login_failed", extra={"reason": "invalid_credentials"})
        return None
    if not valid:
        user.failed_login_count += 1
        excess = user.failed_login_count - settings.login_max_failed_attempts
        if excess >= 0:
            seconds = min(_LOCKOUT_MAX_SECONDS, _LOCKOUT_BASE_SECONDS * (2**excess))
            user.locked_until = now + timedelta(seconds=seconds)
        logger.info(
            "login_failed",
            extra={"reason": "invalid_credentials", "failed_login_count": user.failed_login_count},
        )
        return None

    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    return user


def issue_session(db: Session, settings: Settings, user: User) -> IssuedSession:
    now = utcnow()
    # Opportunistic cleanup of sessions that can never be used again.
    db.execute(
        delete(UserSession).where(
            or_(
                UserSession.expires_at < now - timedelta(days=1),
                UserSession.revoked_at < now - timedelta(days=1),
            )
        )
    )
    token = security.new_session_token()
    session = UserSession(
        user_id=user.id,
        token_hash=security.hash_session_token(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_timeout_hours),
    )
    db.add(session)
    db.flush()
    logger.info("session_created", extra={"user_id": str(user.id)})
    return IssuedSession(token=token, session=session, user=user)


def idle_expires_at(settings: Settings, session: UserSession) -> datetime:
    idle_deadline = session.last_seen_at + timedelta(minutes=settings.session_idle_timeout_minutes)
    return min(idle_deadline, session.expires_at)


def resolve_session(db: Session, settings: Settings, token: str | None) -> UserSession | None:
    """Return the active session for a bearer token, refreshing its idle timer."""
    if not token or len(token) > 256:
        return None
    now = utcnow()
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == security.hash_session_token(token))
    )
    if session is None or session.revoked_at is not None:
        return None
    if session.expires_at <= now or idle_expires_at(settings, session) <= now:
        return None
    user = session.user
    if not user.is_active:
        return None
    if now - session.last_seen_at >= _LAST_SEEN_WRITE_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return session


def revoke_session(db: Session, session_id: uuid.UUID) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    logger.info("session_revoked", extra={"session_ref": str(session_id)[:8]})
