from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth import service
from app.auth.models import User, UserSession
from app.config import Settings
from app.db.base import utcnow
from app.deps import SESSION_COOKIE_NAME
from app.main import create_app
from tests.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_ADMIN_USERNAME,
    ServiceEndpoints,
    TemporaryDatabase,
    browser_headers,
    complete_setup,
    login,
    make_settings,
)

pytestmark = pytest.mark.integration


def _setup_payload(settings: Settings, **overrides: str) -> dict[str, str]:
    assert settings.bootstrap_token is not None
    payload = {
        "setup_token": settings.bootstrap_token.get_secret_value(),
        "username": TEST_ADMIN_USERNAME,
        "password": TEST_ADMIN_PASSWORD,
    }
    payload.update(overrides)
    return payload


# --- First-run setup -------------------------------------------------------------------


def test_setup_requires_valid_token_and_runs_only_once(
    client: TestClient, settings: Settings
) -> None:
    assert client.get("/api/v1/setup/status").json() == {"setup_required": True}

    wrong = client.post(
        "/api/v1/setup/admin",
        json=_setup_payload(settings, setup_token="0" * 48),
        headers=browser_headers(),
    )
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "setup_token_invalid"
    assert client.get("/api/v1/setup/status").json() == {"setup_required": True}

    created = client.post(
        "/api/v1/setup/admin", json=_setup_payload(settings), headers=browser_headers()
    )
    assert created.status_code == 201
    assert created.json()["username"] == TEST_ADMIN_USERNAME
    assert "password" not in created.text

    again = client.post(
        "/api/v1/setup/admin",
        json=_setup_payload(settings, username="second.admin"),
        headers=browser_headers(),
    )
    assert again.status_code == 409
    assert client.get("/api/v1/setup/status").json() == {"setup_required": False}


def test_setup_enforces_password_policy(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/setup/admin",
        json=_setup_payload(settings, password="short"),
        headers=browser_headers(),
    )
    assert response.status_code == 422
    assert client.get("/api/v1/setup/status").json() == {"setup_required": True}


def test_web_setup_is_disabled_without_bootstrap_token(
    services: ServiceEndpoints, migrated_database: TemporaryDatabase, tmp_path: Path
) -> None:
    settings = make_settings(services, migrated_database.name, tmp_path, bootstrap_token=None)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/setup/admin",
            json={"setup_token": "x", "username": TEST_ADMIN_USERNAME, "password": "p" * 20},
            headers=browser_headers(),
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "web_setup_disabled"


def test_concurrent_bootstrap_creates_exactly_one_admin(
    settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    errors: list[BaseException] = []
    created: list[str] = []
    barrier = threading.Barrier(4)

    def attempt(index: int) -> None:
        barrier.wait()
        try:
            with db_session_factory() as db:
                user = service.create_initial_admin(
                    db, username=f"admin{index}", password=TEST_ADMIN_PASSWORD
                )
                db.commit()
                created.append(user.username)
        except service.SetupAlreadyCompletedError:
            pass
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(created) == 1
    with db_session_factory() as db:
        assert len(db.scalars(select(User)).all()) == 1


# --- Login, protected routes and logout -----------------------------------------------


def test_login_with_valid_credentials_sets_hardened_cookie(
    client: TestClient, settings: Settings
) -> None:
    complete_setup(client, settings)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ANALYST.ADMIN", "password": TEST_ADMIN_PASSWORD},
        headers=browser_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == TEST_ADMIN_USERNAME
    assert body["csrf_token"]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "samesite=strict" in lowered
    assert "path=/" in lowered
    assert client.get("/api/v1/auth/session").status_code == 200


@pytest.mark.parametrize(
    ("username", "password"),
    [(TEST_ADMIN_USERNAME, "wrong password value"), ("nobody.here", TEST_ADMIN_PASSWORD)],
)
def test_invalid_credentials_fail_identically(
    client: TestClient, settings: Settings, username: str, password: str
) -> None:
    complete_setup(client, settings)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=browser_headers(),
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_credentials"}
    assert "set-cookie" not in response.headers
    assert client.get("/api/v1/auth/session").status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/auth/session"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/system/status"),
        ("GET", "/api/v1/system/worker"),
        ("GET", "/api/v1/system/worker-checks"),
        ("POST", "/api/v1/system/worker-checks"),
    ],
)
def test_protected_routes_reject_unauthenticated_requests(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, headers=browser_headers())
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication_required"}


def test_forged_session_cookie_is_rejected(client: TestClient, settings: Settings) -> None:
    complete_setup(client, settings)
    client.cookies.set(SESSION_COOKIE_NAME, "forged-token-value")
    assert client.get("/api/v1/auth/session").status_code == 401


def test_logout_invalidates_session_server_side(client: TestClient, settings: Settings) -> None:
    complete_setup(client, settings)
    csrf = login(client)
    stolen_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    assert stolen_cookie

    response = client.post("/api/v1/auth/logout", headers=browser_headers(csrf))
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()

    # Replaying the old cookie must not work even though the client kept a copy.
    client.cookies.set(SESSION_COOKIE_NAME, stolen_cookie)
    assert client.get("/api/v1/auth/session").status_code == 401
    assert client.get("/api/v1/system/worker-checks").status_code == 401


def test_state_changing_requests_require_csrf_token(client: TestClient, settings: Settings) -> None:
    complete_setup(client, settings)
    csrf = login(client)

    missing = client.post("/api/v1/auth/logout", headers=browser_headers())
    assert missing.status_code == 403
    assert missing.json() == {"detail": "csrf_token_invalid"}

    wrong = client.post("/api/v1/auth/logout", headers=browser_headers("x" * 43))
    assert wrong.status_code == 403
    assert client.get("/api/v1/auth/session").status_code == 200

    assert client.post("/api/v1/auth/logout", headers=browser_headers(csrf)).status_code == 204


def test_cross_origin_login_is_rejected(client: TestClient, settings: Settings) -> None:
    complete_setup(client, settings)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_untrusted_host_header_is_rejected(client: TestClient) -> None:
    response = client.get("/api/health/live", headers={"Host": "rebind.attacker.example"})
    assert response.status_code == 400


def test_relogin_rotates_and_revokes_previous_session(
    client: TestClient, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    complete_setup(client, settings)
    login(client)
    first_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    login(client)
    second_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    assert first_cookie != second_cookie

    client.cookies.set(SESSION_COOKIE_NAME, str(first_cookie))
    assert client.get("/api/v1/auth/session").status_code == 401
    with db_session_factory() as db:
        sessions = db.scalars(select(UserSession)).all()
        assert len(sessions) == 2
        assert sum(session.revoked_at is None for session in sessions) == 1


def test_idle_and_absolute_session_expiry(
    client: TestClient, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    complete_setup(client, settings)
    login(client)
    assert client.get("/api/v1/auth/session").status_code == 200

    idle = timedelta(minutes=settings.session_idle_timeout_minutes + 1)
    with db_session_factory() as db:
        db.execute(update(UserSession).values(last_seen_at=utcnow() - idle))
        db.commit()
    assert client.get("/api/v1/auth/session").status_code == 401

    login(client)
    with db_session_factory() as db:
        db.execute(
            update(UserSession)
            .where(UserSession.revoked_at.is_(None))
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    assert client.get("/api/v1/auth/session").status_code == 401


def test_repeated_failures_lock_the_account_temporarily(
    client: TestClient, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    complete_setup(client, settings)
    for _ in range(settings.login_max_failed_attempts):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": TEST_ADMIN_USERNAME, "password": "not the password"},
            headers=browser_headers(),
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        headers=browser_headers(),
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0

    with db_session_factory() as db:
        db.execute(update(User).values(locked_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    login(client)
    with db_session_factory() as db:
        user = db.scalars(select(User)).one()
        assert user.failed_login_count == 0
        assert user.locked_until is None


def test_password_reset_revokes_all_sessions(
    client: TestClient, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    complete_setup(client, settings)
    login(client)
    new_password = "an entirely new passphrase"
    with db_session_factory() as db:
        assert service.reset_password(db, username=TEST_ADMIN_USERNAME, new_password=new_password)
        db.commit()
    assert client.get("/api/v1/auth/session").status_code == 401
    login(client, password=new_password)


def test_turkish_username_round_trip(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/setup/admin",
        json=_setup_payload(settings, username="Çağrı.Işık"),
        headers=browser_headers(),
    )
    assert response.status_code == 201
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "çağrı.ışık", "password": TEST_ADMIN_PASSWORD},
        headers=browser_headers(),
    )
    assert login_response.status_code == 200
    assert login_response.json()["user"]["username"] == "Çağrı.Işık"
