from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.auth.security import PASSWORD_MAX_LENGTH, USERNAME_MAX_LENGTH


class SetupStatus(BaseModel):
    setup_required: bool


class SetupAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup_token: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH * 2)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH * 2)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    is_admin: bool


class SessionInfo(BaseModel):
    user: UserPublic
    csrf_token: str
    expires_at: datetime
    idle_expires_at: datetime
