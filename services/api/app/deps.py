from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit.service import Actor, user_actor
from app.auth import security
from app.auth.models import User, UserSession
from app.auth.permissions import SystemPermission, has_system_permission
from app.auth.service import resolve_session
from app.config import Settings

SESSION_COOKIE_NAME = "tracehollow_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


DbDep = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True, slots=True)
class Principal:
    user: User
    session: UserSession


def require_session(request: Request, db: DbDep, settings: SettingsDep) -> Principal:
    """Authenticate the request; state-changing methods also require a valid CSRF token."""
    session = resolve_session(db, settings, request.cookies.get(SESSION_COOKIE_NAME))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    if request.method not in SAFE_METHODS and not security.verify_csrf_token(
        session.id, settings.require_secret("secret_key"), request.headers.get(CSRF_HEADER_NAME)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_token_invalid")
    return Principal(user=session.user, session=session)


PrincipalDep = Annotated[Principal, Depends(require_session)]


def request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def current_actor(request: Request, principal: PrincipalDep) -> Actor:
    """The signed-in user as an audit actor, correlated with this request."""
    return user_actor(principal.user, request_id(request))


ActorDep = Annotated[Actor, Depends(current_actor)]


def require_system_permission(permission: SystemPermission) -> Any:
    """Dependency factory: 403 unless the account role grants ``permission`` (denial audited)."""

    def dependency(request: Request, principal: PrincipalDep, actor: ActorDep) -> Principal:
        if not has_system_permission(principal.user.role, permission):
            from app.audit.service import record_denial

            record_denial(
                request.app.state.session_factory,
                actor,
                "access.denied",
                details={"permission": str(permission), "account_role": principal.user.role},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "insufficient_account_role",
                    "message": "Your account role does not allow this action.",
                },
            )
        return principal

    return Depends(dependency)
