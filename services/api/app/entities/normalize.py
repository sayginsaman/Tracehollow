"""Deterministic identifier normalization.

The original value is always stored next to the normalized one. Normalization only creates a
comparison key; it never decides that two entities are the same person or account.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from app.auth.security import normalize_username
from app.entities.models import IdentifierType

MAX_IDENTIFIER_LENGTH = 2048
_DOMAIN_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_PLATFORM = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


class IdentifierError(ValueError):
    pass


def _clean(value: str) -> str:
    cleaned = unicodedata.normalize("NFKC", value).strip()
    if not cleaned:
        raise IdentifierError("identifier value must not be empty")
    if len(cleaned) > MAX_IDENTIFIER_LENGTH:
        raise IdentifierError(
            f"identifier value must be at most {MAX_IDENTIFIER_LENGTH} characters"
        )
    if any(unicodedata.category(ch) == "Cc" for ch in cleaned):
        raise IdentifierError("identifier value must not contain control characters")
    return cleaned


def normalize_domain(value: str) -> str:
    host = _clean(value).rstrip(".").lower()
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise IdentifierError("invalid domain name") from exc
    labels = ascii_host.split(".")
    if len(labels) < 2 or not all(_DOMAIN_LABEL.match(label) for label in labels):
        raise IdentifierError("invalid domain name")
    return ascii_host


def normalize_email(value: str) -> str:
    cleaned = _clean(value)
    local, sep, domain = cleaned.rpartition("@")
    if not sep or not local or not domain:
        raise IdentifierError("invalid email address")
    return f"{normalize_username(local)}@{normalize_domain(domain)}"


def normalize_url(value: str) -> str:
    cleaned = _clean(value)
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise IdentifierError("URL must be an absolute http(s) URL")
    if _is_ip(parts.hostname):
        address = ipaddress.ip_address(parts.hostname)
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    else:
        host = normalize_domain(parts.hostname)
    try:
        port = parts.port
    except ValueError as exc:
        raise IdentifierError("URL port is invalid") from exc
    default_port = {"http": 80, "https": 443}[parts.scheme.lower()]
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


def normalize_ip(value: str) -> str:
    try:
        return ipaddress.ip_address(_clean(value).strip("[]")).compressed
    except ValueError as exc:
        raise IdentifierError("invalid IP address") from exc


def normalize_phone(value: str) -> str:
    cleaned = _clean(value)
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not 5 <= len(digits) <= 17:
        raise IdentifierError("phone number must contain 5-17 digits")
    # No country inference: a leading '+' is kept only when the analyst supplied it.
    return f"+{digits}" if cleaned.startswith("+") else digits


def normalize_platform(value: str | None) -> str | None:
    if value is None:
        return None
    platform = _clean(value).lower()
    if not _PLATFORM.match(platform):
        raise IdentifierError("platform must be 1-100 lowercase letters, digits, '.', '_' or '-'")
    return platform


def normalize_identifier(identifier_type: IdentifierType, value: str) -> str:
    match identifier_type:
        case IdentifierType.DOMAIN:
            return normalize_domain(value)
        case IdentifierType.EMAIL:
            return normalize_email(value)
        case IdentifierType.URL:
            return normalize_url(value)
        case IdentifierType.IP:
            return normalize_ip(value)
        case IdentifierType.PHONE:
            return normalize_phone(value)
        case IdentifierType.USERNAME | IdentifierType.NAME:
            return normalize_username(_clean(value))
        case IdentifierType.PLATFORM_ID | IdentifierType.OTHER:
            return _clean(value)
