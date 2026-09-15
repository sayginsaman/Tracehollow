from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.security_middleware import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    RequestContextMiddleware,
)

TRUSTED = "http://localhost:3000"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @app.get("/read")
    def read() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=16)
    app.add_middleware(OriginCheckMiddleware, trusted_origins=[TRUSTED])
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app)


def test_trusted_origin_is_allowed(client: TestClient) -> None:
    response = client.post("/echo", content=b"{}", headers={"Origin": TRUSTED})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://localhost:5173"},
        {"Origin": "https://attacker.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_untrusted_browser_requests_are_rejected(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.post("/echo", content=b"{}", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_not_allowed"}


def test_non_browser_clients_without_origin_reach_authentication(client: TestClient) -> None:
    assert client.post("/echo", content=b"{}").status_code == 200


def test_safe_methods_are_not_origin_checked(client: TestClient) -> None:
    assert client.get("/read", headers={"Origin": "https://attacker.example"}).status_code == 200


def test_oversized_bodies_are_rejected(client: TestClient) -> None:
    declared = client.post("/echo", content=b"x" * 17)
    assert declared.status_code == 413

    def chunks() -> object:
        yield b"x" * 10
        yield b"x" * 10

    streamed = client.post("/echo", content=chunks())  # type: ignore[arg-type]
    assert streamed.status_code == 413


def test_security_headers_and_request_id(client: TestClient) -> None:
    response = client.get("/read")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert len(response.headers["X-Request-ID"]) >= 8
