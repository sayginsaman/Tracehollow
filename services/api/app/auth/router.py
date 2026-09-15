from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth import security, service
from app.auth.models import User, UserSession
from app.auth.schemas import (
    LoginRequest,
    SessionInfo,
    SetupAdminRequest,
    SetupStatus,
    UserPublic,
)
from app.config import Settings
from app.deps import SESSION_COOKIE_NAME, DbDep, PrincipalDep, SettingsDep

setup_router = APIRouter(prefix="/api/v1/setup", tags=["setup"])
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _session_info(settings: Settings, user: User, session: UserSession) -> SessionInfo:
    return SessionInfo(
        user=UserPublic.model_validate(user),
        csrf_token=security.csrf_token_for(session.id, settings.require_secret("secret_key")),
        expires_at=session.expires_at,
        idle_expires_at=service.idle_expires_at(settings, session),
    )


@setup_router.get("/status")
def get_setup_status(db: DbDep) -> SetupStatus:
    return SetupStatus(setup_required=service.setup_required(db))


@setup_router.post("/admin", status_code=status.HTTP_201_CREATED)
def create_admin(body: SetupAdminRequest, db: DbDep, settings: SettingsDep) -> UserPublic:
    if settings.bootstrap_token is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="web_setup_disabled")
    if not service.setup_required(db):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="setup_already_completed")
    if not security.constant_time_equals(
        settings.require_secret("bootstrap_token"), body.setup_token.strip()
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="setup_token_invalid")
    try:
        user = service.create_initial_admin(db, username=body.username, password=body.password)
    except service.SetupAlreadyCompletedError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="setup_already_completed") from None
    except service.UsernameTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="username_taken") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return UserPublic.model_validate(user)


@auth_router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> SessionInfo:
    try:
        user = service.authenticate(db, settings, username=body.username, password=body.password)
    except service.AccountLockedError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too_many_failed_attempts",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
    if user is None:
        db.commit()  # persist the failed-attempt counter
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    # Replace any session presented with this request (prevents session fixation).
    previous = service.resolve_session(db, settings, request.cookies.get(SESSION_COOKIE_NAME))
    if previous is not None:
        service.revoke_session(db, previous.id)
    issued = service.issue_session(db, settings, user)
    db.commit()
    max_age = settings.session_absolute_timeout_hours * 3600
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return _session_info(settings, user, issued.session)


@auth_router.get("/session")
def get_session(principal: PrincipalDep, settings: SettingsDep) -> SessionInfo:
    return _session_info(settings, principal.user, principal.session)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(principal: PrincipalDep, db: DbDep, settings: SettingsDep, response: Response) -> None:
    service.revoke_session(db, principal.session.id)
    db.commit()
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
