"""ASGI middleware: request context/logging, security headers, origin checks, body limits."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Iterable, Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("tracehollow.access")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DOCS_PATH_PREFIXES = ("/api/docs", "/api/openapi.json")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


async def _send_json_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Assigns a request ID, adds security headers and writes one access log line per request.

    Query strings, cookies and bodies are never logged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if 8 <= len(incoming) <= 64 and incoming.isalnum() else ""
        request_id = request_id or secrets.token_hex(12)
        # Available to handlers as request.state.request_id (audit correlation).
        scope.setdefault("state", {})["request_id"] = request_id
        path: str = scope.get("path", "")
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if not path.startswith(DOCS_PATH_PREFIXES):
                    headers.setdefault("Content-Security-Policy", _API_CSP)
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": path,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )


class OriginCheckMiddleware:
    """Rejects state-changing browser requests that do not come from a trusted origin.

    Complements SameSite=Strict cookies and per-session CSRF tokens. Requests without both
    ``Origin`` and ``Sec-Fetch-Site`` are non-browser clients, which cannot carry a victim's
    browser cookies, and are allowed through to normal authentication.
    """

    def __init__(self, app: ASGIApp, trusted_origins: Iterable[str]) -> None:
        self.app = app
        self.trusted_origins = frozenset(trusted_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") in UNSAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            fetch_site = headers.get("sec-fetch-site")
            if origin is not None:
                if origin.lower().rstrip("/") not in self.trusted_origins:
                    await _send_json_error(send, 403, "origin_not_allowed")
                    return
            elif fetch_site is not None and fetch_site not in {"same-origin", "none"}:
                await _send_json_error(send, 403, "origin_not_allowed")
                return
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than the limit, including chunked uploads.

    ``overrides`` raise the limit for specific path patterns (evidence uploads) only.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        overrides: Sequence[tuple[str, int]] = (),
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.overrides = [(re.compile(pattern), limit) for pattern, limit in overrides]

    def _limit_for(self, path: str) -> int:
        for pattern, limit in self.overrides:
            if pattern.match(path):
                return limit
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = self._limit_for(scope.get("path", ""))
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > max_bytes):
            await _send_json_error(send, 413, "request_body_too_large")
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if not response_started:
                await _send_json_error(send, 413, "request_body_too_large")


class _BodyTooLargeError(Exception):
    pass
