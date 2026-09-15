from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import security
from app.auth.models import User, UserSession
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
