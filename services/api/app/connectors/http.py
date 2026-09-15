"""Shared HTTP handling for HTTP-based connectors: guarded fetch plus outcome mapping."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.connectors import netguard
from app.connectors.base import ConnectorError, FetchRequest
from app.queries.models import ConnectorOutcome


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float | None:
    """Parse ``Retry-After`` (delta seconds or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC)
    return max(0.0, (when - current).total_seconds())


def fetch(
    request: FetchRequest,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    pacing_key: str | None = None,
    interval_seconds: float = 0.0,
    same_origin_redirects_only: bool = False,
    max_bytes: int | None = None,
) -> netguard.FetchResult:
    """Fetch within the network policy; map network-level failures to connector outcomes."""
    context = request.context
    if pacing_key is not None:
        context.pace(pacing_key, interval_seconds)
    if context.cancelled():
        raise ConnectorError(ConnectorOutcome.CANCELED, "Canceled.", code="canceled")
    timeout = min(context.request_timeout_seconds, max(1.0, context.remaining_seconds()))
    try:
        return netguard.fetch(
            url,
            policy=context.network_policy,
            headers={"user-agent": context.user_agent, **(headers or {})},
            max_bytes=max_bytes or context.max_response_bytes,
            timeout_seconds=timeout,
            max_redirects=context.max_redirects,
            cancelled=context.cancelled,
            transport=context.http_transport,
            same_origin_redirects_only=same_origin_redirects_only,
        )
    except netguard.DestinationBlockedError as exc:
        raise ConnectorError(
            ConnectorOutcome.UNSUPPORTED,
            f"Destination not permitted: {exc.detail}",
            code=exc.code,
        ) from None
    except netguard.FetchError as exc:
        if exc.code == "canceled":
            raise ConnectorError(ConnectorOutcome.CANCELED, exc.detail, code="canceled") from None
        outcome = ConnectorOutcome.UNAVAILABLE if exc.retryable else ConnectorOutcome.PARSE_ERROR
        if exc.code == "too_many_redirects":
            outcome = ConnectorOutcome.UNAVAILABLE
        raise ConnectorError(outcome, exc.detail, code=exc.code) from None


def raise_for_status(result: netguard.FetchResult, what: str) -> None:
    """Map non-success HTTP statuses (other than those the caller handles) to outcomes."""
    code = result.status_code
    if 200 <= code < 300:
        return
    retry_after = retry_after_seconds(result.headers.get("retry-after"))
    if code == 401:
        raise ConnectorError(
            ConnectorOutcome.AUTHENTICATION_REQUIRED,
            f"{what} requires authentication (HTTP 401).",
            code="http_401",
        )
    if code == 403:
        raise ConnectorError(
            ConnectorOutcome.ACCESS_DENIED, f"{what} refused access (HTTP 403).", code="http_403"
        )
    if code == 429:
        raise ConnectorError(
            ConnectorOutcome.RATE_LIMITED,
            f"{what} rate limit reached (HTTP 429).",
            retry_after_seconds=retry_after,
            code="http_429",
        )
    if code in (408, 425) or code >= 500:
        raise ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            f"{what} is temporarily unavailable (HTTP {code}).",
            retry_after_seconds=retry_after,
            code=f"http_{code}",
        )
    if 300 <= code < 400:
        raise ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            f"{what} answered with a redirect that could not be followed (HTTP {code}).",
            code=f"http_{code}",
        )
    raise ConnectorError(
        ConnectorOutcome.UNSUPPORTED,
        f"{what} rejected the request (HTTP {code}).",
        code=f"http_{code}",
    )


def decode_text(result: netguard.FetchResult, fallback: str | None = None) -> tuple[str, str]:
    """Decode a response body; return (text, encoding used). Undecodable bytes are replaced."""
    for candidate in (result.charset, fallback, "utf-8"):
        if not candidate:
            continue
        try:
            return result.content.decode(candidate, errors="replace"), candidate
        except LookupError:
            continue
    return result.content.decode("utf-8", errors="replace"), "utf-8"
