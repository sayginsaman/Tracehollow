from __future__ import annotations

import uuid

import pytest

from app.auth import security


def test_password_hash_uses_argon2id_and_verifies() -> None:
    hashed = security.hash_password("a sufficiently long passphrase")
    assert hashed.startswith("$argon2id$")
    assert security.verify_password(hashed, "a sufficiently long passphrase")
    assert not security.verify_password(hashed, "a different long passphrase")


def test_unknown_user_verification_fails_safely() -> None:
    assert security.verify_password(None, "anything at all") is False
    assert security.verify_password("not-a-hash", "anything at all") is False


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short", "at least 12"),
        (" " * 20, "blank"),
        ("x" * 1025, "at most 1024"),
    ],
)
def test_password_policy_rejections(password: str, message: str) -> None:
    with pytest.raises(security.PasswordPolicyError, match=message):
        security.validate_password(password)


def test_password_must_not_match_username() -> None:
    with pytest.raises(security.PasswordPolicyError, match="username"):
        security.validate_password("Administrator1", username="administrator1")


def test_turkish_usernames_are_accepted_and_normalized_consistently() -> None:
    assert security.validate_username("Şule.Yılmaz") == "Şule.Yılmaz"
    assert security.normalize_username("ŞULE.YILMAZ") == security.normalize_username("şule.yılmaz")
    assert security.normalize_username("İpek") == security.normalize_username("İPEK")


@pytest.mark.parametrize("username", ["ab", "has space", "semi;colon", "x" * 65, "<script>"])
def test_invalid_usernames_are_rejected(username: str) -> None:
    with pytest.raises(ValueError, match="username"):
        security.validate_username(username)


def test_session_tokens_are_random_and_hashed() -> None:
    first, second = security.new_session_token(), security.new_session_token()
    assert first != second
    assert len(security.hash_session_token(first)) == 32
    assert security.hash_session_token(first) != security.hash_session_token(second)


def test_csrf_token_is_bound_to_session_and_key() -> None:
    session_id = uuid.uuid4()
    key = b"k" * 32
    token = security.csrf_token_for(session_id, key)
    assert security.verify_csrf_token(session_id, key, token)
    assert not security.verify_csrf_token(uuid.uuid4(), key, token)
    assert not security.verify_csrf_token(session_id, b"x" * 32, token)
    assert not security.verify_csrf_token(session_id, key, None)
    assert not security.verify_csrf_token(session_id, key, token + "ç")
