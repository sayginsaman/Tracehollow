from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ConfigurationError, load_settings

SECRET_KEY = "k" * 64
DB_PASSWORD = "database-password-value-1234"
REDIS_PASSWORD = "redis-password-value-5678"


def _valid(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "database_password": DB_PASSWORD,
        "redis_password": REDIS_PASSWORD,
        "secret_key": SECRET_KEY,
    }
    values.update(overrides)
    return values


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith("TRACEHOLLOW_") and not name.startswith("TRACEHOLLOW_TEST_"):
            monkeypatch.delenv(name)


def test_missing_required_secrets_are_reported_by_name() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    message = str(caught.value)
    assert "TRACEHOLLOW_DATABASE_PASSWORD or TRACEHOLLOW_DATABASE_PASSWORD_FILE" in message
    assert "TRACEHOLLOW_REDIS_PASSWORD" in message
    assert "TRACEHOLLOW_SECRET_KEY" in message


def test_short_secret_is_rejected_without_echoing_any_value() -> None:
    weak_key = "tooshort-but-secret"
    with pytest.raises(ConfigurationError) as caught:
        load_settings(**_valid(secret_key=weak_key))
    message = str(caught.value)
    assert "TRACEHOLLOW_SECRET_KEY must be at least 32 characters" in message
    for value in (weak_key, DB_PASSWORD, REDIS_PASSWORD):
        assert value not in message
    assert caught.value.__cause__ is None


def test_invalid_field_error_does_not_echo_other_inputs() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings(**_valid(database_port="not-a-port"))
    message = str(caught.value)
    assert "database_port" in message
    assert DB_PASSWORD not in message
    assert SECRET_KEY not in message


def test_secrets_are_read_from_files(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret_key"
    secret_file.write_text(SECRET_KEY + "\n", encoding="utf-8")
    settings = load_settings(**_valid(secret_key=None, secret_key_file=secret_file))
    assert settings.secret_key is not None
    assert settings.secret_key.get_secret_value() == SECRET_KEY
    assert SECRET_KEY not in repr(settings)


def test_unreadable_secret_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="SECRET_KEY_FILE points to a file"):
        load_settings(**_valid(secret_key=None, secret_key_file=tmp_path / "missing"))


def test_database_url_masks_password_when_rendered() -> None:
    settings = load_settings(**_valid())
    assert DB_PASSWORD not in str(settings.database_url)
    assert DB_PASSWORD not in repr(settings)


def test_trusted_origins_include_public_origin_and_are_normalized() -> None:
    settings = load_settings(
        **_valid(
            public_origin="http://LOCALHOST:3000/",
            trusted_origins="http://127.0.0.1:3000",
        )
    )
    assert settings.public_origin == "http://localhost:3000"
    assert settings.trusted_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert settings.cookie_secure is False


@pytest.mark.parametrize(
    "origin", ["localhost:3000", "http://localhost:3000/path", "ftp://example.org"]
)
def test_invalid_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(**_valid(trusted_origins=origin))


def test_wildcard_allowed_hosts_are_rejected() -> None:
    with pytest.raises(ConfigurationError, match="wildcard"):
        load_settings(**_valid(allowed_hosts="*"))


def test_https_public_origin_enables_secure_cookies() -> None:
    assert load_settings(**_valid(public_origin="https://tracehollow.test")).cookie_secure
