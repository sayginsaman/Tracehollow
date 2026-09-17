from __future__ import annotations

import json
import logging

import pytest

from app.logging_config import REDACTED, JsonFormatter, configure_logging, redact_text


def _format(message: str, **extra: object) -> dict[str, object]:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def test_url_credentials_are_redacted() -> None:
    text = redact_text("connecting to redis://:hunter2hunter2@redis:6379/0 now")
    assert "hunter2" not in text
    assert f"redis://{REDACTED}@redis:6379/0" in text


def test_key_value_and_bearer_secrets_are_redacted() -> None:
    text = redact_text("password=abc123 api_key: 'zzz' Authorization Bearer eyJhbGciOi.x.y")
    assert "abc123" not in text
    assert "zzz" not in text
    assert "eyJhbGciOi" not in text


def test_sensitive_extra_fields_are_redacted_recursively() -> None:
    payload = _format(
        "login attempt",
        password="s3cret-value",
        headers={"Cookie": "tracehollow_session=abc", "Accept": "application/json"},
        user_id="1234",
    )
    assert payload["password"] == REDACTED
    assert payload["headers"] == {"Cookie": REDACTED, "Accept": "application/json"}
    assert payload["user_id"] == "1234"
    assert "s3cret-value" not in json.dumps(payload)


def test_unicode_messages_are_preserved() -> None:
    payload = _format("İnceleme başlatıldı: çalışma alanı")
    assert payload["message"] == "İnceleme başlatıldı: çalışma alanı"


def test_bot_tokens_in_request_paths_are_redacted() -> None:
    text = redact_text(
        "GET https://api.telegram.org/bot123456789:AAsyntheticBotToken_000000000000000/getChat"
    )
    assert "AAsynthetic" not in text
    assert f"/bot{REDACTED}/getChat" in text


def test_http_client_request_logs_are_not_emitted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    try:
        logging.getLogger("httpx2").info(
            'HTTP Request: GET https://example.org/private-target/path "HTTP/1.1 200 OK"'
        )
        logging.getLogger("app.test").info("kept")
        logging.getLogger("httpx2").warning("client warning")
    finally:
        logging.getLogger().handlers.clear()
    output = capsys.readouterr().out
    assert "private-target" not in output
    assert "kept" in output
    assert "client warning" in output
