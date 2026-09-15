"""Helpers for connector contract and collection tests. No real network access."""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2

from app.connectors.base import FetchContext, FetchRequest
from app.connectors.netguard import NetworkPolicy

# A public (global) address that tests pretend public host names resolve to. Nothing is sent
# to it: HTTP-level tests use mock transports.
PUBLIC_ADDRESS = "93.184.216.34"

Handler = Callable[[httpx2.Request], httpx2.Response]


def public_resolver(_host: str, _port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


def resolver_map(mapping: dict[str, list[str]]) -> Callable[[str, int], list[str]]:
    def resolve(host: str, _port: int) -> list[str]:
        return mapping.get(host, [PUBLIC_ADDRESS])

    return resolve


@dataclass
class Router:
    """A mock transport that answers by URL and records every request it receives."""

    routes: dict[str, Handler | list[Handler]] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def add(self, url: str, *handlers: Handler) -> Router:
        self.routes[url] = list(handlers) if len(handlers) > 1 else handlers[0]
        return self

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self.routes.get(str(request.url))
        if handler is None:
            return httpx2.Response(599, text=f"no route for {request.url}")
        if isinstance(handler, list):
            current = handler.pop(0) if len(handler) > 1 else handler[0]
            return current(request)
        return handler(request)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def respond(
    status: int = 200,
    *,
    body: bytes | str | None = None,
    json_body: Any = None,
    content_type: str = "text/html; charset=utf-8",
    headers: dict[str, str] | None = None,
) -> Handler:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        merged = {"content-type": content_type, **(headers or {})}
        if json_body is not None:
            merged["content-type"] = "application/json; charset=utf-8"
            return httpx2.Response(status, content=json.dumps(json_body).encode(), headers=merged)
        content = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        return httpx2.Response(status, content=content, headers=merged)

    return handler


def raise_error(error: Exception) -> Handler:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        raise error

    return handler


def context(
    router: Router | None = None,
    *,
    resolver: Callable[[str, int], list[str]] = public_resolver,
    credentials: dict[str, str] | None = None,
    settings: Any = None,
    cancelled: Callable[[], bool] | None = None,
    policy: NetworkPolicy | None = None,
    **overrides: Any,
) -> tuple[FetchContext, dict[str, Any]]:
    """A FetchContext wired to the router; the dict collects progress and credential results."""
    record: dict[str, Any] = {"progress": [], "credential_results": {}, "paced": []}
    creds = credentials or {}
    ctx = FetchContext(
        network_policy=policy or NetworkPolicy(resolver=resolver),
        cancelled=cancelled or (lambda: False),
        progress=lambda values: record["progress"].append(values),
        credential=creds.get,
        credential_result=lambda name, result: record["credential_results"].__setitem__(
            name, result
        ),
        pace=lambda key, interval: record["paced"].append((key, interval)),
        deadline=time.monotonic() + overrides.pop("seconds", 60),
        http_transport=router.transport if router is not None else None,
        settings=settings,
        **overrides,
    )
    return ctx, record


def request(
    input_type: str,
    input_value: str,
    ctx: FetchContext,
    *,
    parameters: dict[str, Any] | None = None,
    page_index: int = 0,
    cursor: dict[str, Any] | None = None,
    max_items_per_page: int = 100,
) -> FetchRequest:
    return FetchRequest(
        input_type=input_type,
        input_value=input_value,
        parameters=parameters or {},
        page_index=page_index,
        attempt=1,
        max_items_per_page=max_items_per_page,
        cursor=cursor,
        context=ctx,
    )


def non_loopback_address() -> str | None:
    """This machine's outbound interface address, for servers that must not be on loopback.

    Connecting a UDP socket sends nothing; it only selects the local address.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 80))
        except OSError:
            return None
        address = str(probe.getsockname()[0])
    return None if address.startswith("127.") or address == "0.0.0.0" else address  # noqa: S104


@contextmanager
def http_server(
    address: str, handler: type[http.server.BaseHTTPRequestHandler]
) -> Iterator[tuple[str, int]]:
    server = http.server.ThreadingHTTPServer((address, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield address, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
