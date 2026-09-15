"""Password hashing, session tokens and CSRF tokens.

Uses reviewed primitives only: Argon2id via argon2-cffi (RFC 9106 low-memory profile),
``secrets`` for token generation, SHA-256 for token digests and HMAC-SHA256 for CSRF
tokens bound to a server-side session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import unicodedata
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
USERNAME_MAX_LENGTH = 64
SESSION_TOKEN_BYTES = 32

_hasher = PasswordHasher()
# Verified against unknown usernames so response timing does not reveal account existence.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


class PasswordPolicyError(ValueError):
    pass


_DOTTED_DOTLESS_I = str.maketrans({"I": "i", "İ": "i", "ı": "i"})


def normalize_username(username: str) -> str:
    """Comparison key for usernames.

    Locale-independent ``casefold`` maps Turkish ``I`` to ``i`` but leaves ``ı`` untouched,
    so ``Işık`` and ``ışık`` would differ. Dotted and dotless i are folded together, which
    only makes uniqueness stricter.
    """
    value = unicodedata.normalize("NFKC", username).strip().translate(_DOTTED_DOTLESS_I)
    return value.casefold()


def validate_username(username: str) -> str:
    value = unicodedata.normalize("NFKC", username).strip()
    if not 3 <= len(value) <= USERNAME_MAX_LENGTH:
        raise ValueError(f"username must be 3-{USERNAME_MAX_LENGTH} characters")
    for char in value:
        category = unicodedata.category(char)
        if not (category[0] in {"L", "N"} or char in "._-"):
            raise ValueError("username may contain letters, digits, '.', '_' and '-' only")
    return value


def validate_password(password: str, *, username: str | None = None) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(f"password must be at most {PASSWORD_MAX_LENGTH} characters")
    if not password.strip():
        raise PasswordPolicyError("password must not be blank")
    if username is not None and normalize_username(password) == normalize_username(username):
        raise PasswordPolicyError("password must not match the username")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification; unknown users are checked against a dummy hash."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def csrf_token_for(session_id: uuid.UUID, secret_key: bytes) -> str:
    digest = hmac.new(secret_key, b"tracehollow-csrf-v1:" + session_id.bytes, hashlib.sha256)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")


def verify_csrf_token(session_id: uuid.UUID, secret_key: bytes, candidate: str | None) -> bool:
    if not candidate:
        return False
    expected = csrf_token_for(session_id, secret_key)
    return hmac.compare_digest(expected.encode("ascii"), candidate.encode("utf-8", "replace"))


def constant_time_equals(expected: bytes, candidate: str) -> bool:
    return hmac.compare_digest(expected, candidate.encode("utf-8", "replace"))
