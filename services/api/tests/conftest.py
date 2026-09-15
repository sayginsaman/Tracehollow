"""Shared fixtures.

Integration tests use real PostgreSQL and Redis (see scripts/test-backend.sh). Each test
session creates its own uniquely named database, migrates it with Alembic and drops it
afterwards. Tests never touch an application database.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, load_settings
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app

API_ROOT = Path(__file__).resolve().parents[1]
TEST_ORIGIN = "http://localhost:3000"
TEST_ADMIN_USERNAME = "analyst.admin"
TEST_ADMIN_PASSWORD = "correct horse battery staple"


@dataclass(frozen=True)
class ServiceEndpoints:
    pg_host: str
    pg_port: int
    pg_user: str
    pg_password: str
    redis_host: str
    redis_port: int
    redis_password: str

    def pg_url(self, database: str) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.pg_user,
            password=self.pg_password,
            host=self.pg_host,
            port=self.pg_port,
            database=database,
        )


def _services_from_env() -> ServiceEndpoints | None:
    env = os.environ
    if "TRACEHOLLOW_TEST_POSTGRES_PORT" not in env:
        return None
    return ServiceEndpoints(
        pg_host=env.get("TRACEHOLLOW_TEST_POSTGRES_HOST", "127.0.0.1"),
        pg_port=int(env["TRACEHOLLOW_TEST_POSTGRES_PORT"]),
        pg_user=env["TRACEHOLLOW_TEST_POSTGRES_USER"],
        pg_password=env["TRACEHOLLOW_TEST_POSTGRES_PASSWORD"],
        redis_host=env.get("TRACEHOLLOW_TEST_REDIS_HOST", "127.0.0.1"),
        redis_port=int(env["TRACEHOLLOW_TEST_REDIS_PORT"]),
        redis_password=env["TRACEHOLLOW_TEST_REDIS_PASSWORD"],
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _services_from_env() is not None:
        return
    if os.environ.get("TRACEHOLLOW_REQUIRE_INTEGRATION") == "1":
        raise pytest.UsageError("integration services are required but not configured")
    skip = pytest.mark.skip(reason="integration services not configured; use test-backend.sh")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def services() -> ServiceEndpoints:
    found = _services_from_env()
    if found is None:
        pytest.skip("integration services not configured")
    return found


def alembic_config(url: URL) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["database_url"] = url
    config.attributes["skip_logging_setup"] = True
    return config


@dataclass
class TemporaryDatabase:
    services: ServiceEndpoints
    name: str

    @property
    def url(self) -> URL:
        return self.services.pg_url(self.name)


def _admin_engine(services: ServiceEndpoints) -> Engine:
    return create_engine(
        services.pg_url("tracehollow_test"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )


@pytest.fixture(scope="session")
def database_factory(services: ServiceEndpoints) -> Iterator[list[TemporaryDatabase]]:
    """Creates uniquely named databases on demand and drops them at session end."""
    created: list[TemporaryDatabase] = []
    yield created
    engine = _admin_engine(services)
    with engine.connect() as connection:
        for database in created:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database.name}" WITH (FORCE)'))
    engine.dispose()


def create_temporary_database(
    services: ServiceEndpoints, registry: list[TemporaryDatabase]
) -> TemporaryDatabase:
    database = TemporaryDatabase(services, f"tracehollow_t_{secrets.token_hex(6)}")
    engine = _admin_engine(services)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database.name}"'))
    engine.dispose()
    registry.append(database)
    return database


@pytest.fixture(scope="session")
def migrated_database(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> TemporaryDatabase:
    database = create_temporary_database(services, database_factory)
    command.upgrade(alembic_config(database.url), "head")
    return database


def make_settings(
    services: ServiceEndpoints, database_name: str, tmp_path: Path, **overrides: object
) -> Settings:
    storage = tmp_path / "evidence"
    storage.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "env": "test",
        "public_origin": TEST_ORIGIN,
        "database_host": services.pg_host,
        "database_port": services.pg_port,
        "database_name": database_name,
        "database_user": services.pg_user,
        "database_password": services.pg_password,
        "redis_host": services.redis_host,
        "redis_port": services.redis_port,
        "redis_db": 3,
        "redis_password": services.redis_password,
        "secret_key": secrets.token_hex(32),
        "bootstrap_token": secrets.token_hex(24),
        "evidence_storage_path": storage,
        "worker_ping_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return load_settings(**values)


@pytest.fixture
def settings(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> Settings:
    return make_settings(services, migrated_database.name, tmp_path)


@pytest.fixture
def db_session_factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(settings)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_state(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    if "integration" not in request.keywords or _services_from_env() is None:
        return
    if "migrated_database" not in request.fixturenames:
        return
    database: TemporaryDatabase = request.getfixturevalue("migrated_database")
    engine = create_engine(database.url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE worker_checks, sessions, users RESTART IDENTITY CASCADE"))
    engine.dispose()
    services: ServiceEndpoints = request.getfixturevalue("services")
    client = redis.Redis(
        host=services.redis_host, port=services.redis_port, password=services.redis_password, db=3
    )
    client.flushdb()
    client.close()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def browser_headers(csrf_token: str | None = None) -> dict[str, str]:
    headers = {"Origin": TEST_ORIGIN, "Sec-Fetch-Site": "same-origin"}
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
    return headers


def complete_setup(client: TestClient, settings: Settings) -> None:
    assert settings.bootstrap_token is not None
    response = client.post(
        "/api/v1/setup/admin",
        json={
            "setup_token": settings.bootstrap_token.get_secret_value(),
            "username": TEST_ADMIN_USERNAME,
            "password": TEST_ADMIN_PASSWORD,
        },
        headers=browser_headers(),
    )
    assert response.status_code == 201, response.text


def login(client: TestClient, password: str = TEST_ADMIN_PASSWORD) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": password},
        headers=browser_headers(),
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["csrf_token"]
    return token
