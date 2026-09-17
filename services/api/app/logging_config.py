"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"pass(word)?|secret|token|cookie|authori[sz]ation|csrf|session_?id|api_?key|credential",
    re.IGNORECASE,
)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]*)@", re.I)
_KEY_VALUE = re.compile(
    r"(?P<key>\b[\w-]*(?:password|secret|token|cookie|authorization|api[_-]?key)[\w-]*)"
    r"(?P<sep>\s*[=:]\s*)(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
# Telegram Bot API tokens travel in the request path (/bot<id>:<secret>/method).
_BOT_TOKEN_PATH = re.compile(r"/bot\d{3,20}:[A-Za-z0-9_-]{20,}")
# HTTP client libraries log every request URL at INFO. Collection URLs are case data and may carry
# secrets in their path, so these loggers only report warnings and errors.
QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "requests")

# Attributes present on every LogRecord; anything else was passed through ``extra``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime", "taskName"}
)


def redact_text(text: str) -> str:
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group('scheme')}{REDACTED}@", text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group('key')}{m.group('sep')}{REDACTED}", text)
    text = _BOT_TOKEN_PATH.sub(f"/bot{REDACTED}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)


def redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = redact_value(key, value)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "alembic"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # Access logs are emitted by the API's own middleware without query strings.
    logging.getLogger("uvicorn.access").disabled = True
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
