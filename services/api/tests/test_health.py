from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import (
    ServiceEndpoints,
    TemporaryDatabase,
    complete_setup,
    create_temporary_database,
    login,
    make_settings,
)

pytestmark = pytest.mark.integration


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def _readiness(settings: Settings) -> tuple[int, dict[str, object]]:
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        response = client.get("/api/health/ready")
    return response.status_code, response.json()


def test_liveness_does_not_depend_on_dependencies(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(
        services,
        migrated_database.name,
        tmp_path,
        database_port=_closed_port(),
        redis_port=_closed_port(),
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/health/live").json() == {"status": "ok"}


def test_ready_when_all_required_dependencies_are_available(settings: Settings) -> None:
    status_code, body = _readiness(settings)
    assert status_code == 200
    assert body == {
        "status": "ready",
        "checks": {"database": "ok", "migrations": "ok", "redis": "ok", "storage": "ok"},
    }


def test_not_ready_when_redis_is_unavailable(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(services, migrated_database.name, tmp_path, redis_port=_closed_port())
    status_code, body = _readiness(settings)
    assert status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"] == {
        "database": "ok",
        "migrations": "ok",
        "redis": "unavailable",
        "storage": "ok",
    }


def test_not_ready_when_database_is_unavailable(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(
        services, migrated_database.name, tmp_path, database_port=_closed_port()
    )
    status_code, body = _readiness(settings)
    assert status_code == 503
    assert body["checks"] == {
        "database": "unavailable",
        "migrations": "unavailable",
        "redis": "ok",
        "storage": "ok",
    }


def test_not_ready_when_migrations_have_not_run(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase], tmp_path: Path
) -> None:
    empty = create_temporary_database(services, database_factory)
    settings = make_settings(services, empty.name, tmp_path)
    status_code, body = _readiness(settings)
    assert status_code == 503
    assert body["checks"]["migrations"] == "migrations_pending"  # type: ignore[index]


def test_not_ready_when_storage_is_not_writable(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(
        services,
        migrated_database.name,
        tmp_path,
        evidence_storage_path=tmp_path / "does-not-exist",
    )
    status_code, body = _readiness(settings)
    assert status_code == 503
    assert body["checks"]["storage"] == "not_writable"  # type: ignore[index]


def test_readiness_response_contains_no_connection_details(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(
        services, migrated_database.name, tmp_path, database_port=_closed_port()
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        text = client.get("/api/health/ready").text
    assert services.pg_password not in text
    assert str(settings.database_port) not in text
    assert "127.0.0.1" not in text


def test_authenticated_status_reports_details(client: TestClient, settings: Settings) -> None:
    complete_setup(client, settings)
    login(client)
    body = client.get("/api/v1/system/status").json()
    assert body["ready"] is True
    assert body["checks"]["migrations"]["status"] == "ok"
    assert body["checks"]["migrations"]["detail"].startswith("at revision")
    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() not in str(body)
