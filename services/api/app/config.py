"""Application configuration.

Configuration is read from ``TRACEHOLLOW_*`` environment variables. Secrets may be
supplied directly or, preferably, through ``*_FILE`` variables that point to files
mounted by Docker Compose. Validation errors never include the offending values.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

ENV_PREFIX = "TRACEHOLLOW_"

# (field name, minimum length, required)
_SECRET_FIELDS: tuple[tuple[str, int, bool], ...] = (
    ("database_password", 16, True),
    ("redis_password", 16, True),
    ("secret_key", 32, True),
    ("bootstrap_token", 32, False),
)


class ConfigurationError(RuntimeError):
    """Raised when configuration is invalid. The message never contains secret values."""


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        extra="ignore",
        hide_input_in_errors=True,
    )

    env: Literal["development", "test", "production"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_docs_enabled: bool = False

    # Browser-facing origin of the web application, e.g. http://localhost:3000.
    public_origin: str = "http://localhost:3000"
    # Origins allowed to send state-changing requests. Defaults to the public origin.
    trusted_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Host header values accepted by the API (DNS-rebinding protection).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]", "api"]
    )

    database_host: str = "postgres"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "tracehollow"
    database_user: str = "tracehollow_app"
    database_password: SecretStr | None = None
    database_password_file: Path | None = None
    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=60)

    redis_host: str = "redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_password: SecretStr | None = None
    redis_password_file: Path | None = None

    secret_key: SecretStr | None = None
    secret_key_file: Path | None = None
    bootstrap_token: SecretStr | None = None
    bootstrap_token_file: Path | None = None

    evidence_storage_path: Path = Path("/data/evidence")

    session_idle_timeout_minutes: int = Field(default=480, ge=5, le=10080)
    session_absolute_timeout_hours: int = Field(default=24, ge=1, le=720)
    login_max_failed_attempts: int = Field(default=5, ge=1, le=100)

    worker_ping_timeout_seconds: float = Field(default=1.5, gt=0, le=10)

    @field_validator("trusted_origins", "allowed_hosts", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("public_origin")
    @classmethod
    def _validate_public_origin(cls, value: str) -> str:
        return _normalize_origin(value)

    @model_validator(mode="after")
    def _resolve_and_validate(self) -> Settings:
        problems: list[str] = []
        for name, min_length, required in _SECRET_FIELDS:
            env_name = f"{ENV_PREFIX}{name.upper()}"
            value: SecretStr | None = getattr(self, name)
            path: Path | None = getattr(self, f"{name}_file")
            if value is None and path is not None:
                try:
                    content = path.read_text(encoding="utf-8").strip()
                except OSError:
                    problems.append(f"{env_name}_FILE points to a file that cannot be read")
                    continue
                value = SecretStr(content) if content else None
                setattr(self, name, value)
            if value is None:
                if required:
                    problems.append(f"{env_name} or {env_name}_FILE must be set")
                continue
            if len(value.get_secret_value()) < min_length:
                problems.append(f"{env_name} must be at least {min_length} characters")

        try:
            origins = [_normalize_origin(origin) for origin in self.trusted_origins]
        except ValueError as exc:
            problems.append(f"{ENV_PREFIX}TRUSTED_ORIGINS: {exc}")
            origins = []
        if self.public_origin not in origins:
            origins.insert(0, self.public_origin)
        self.trusted_origins = origins

        if not self.allowed_hosts:
            problems.append(f"{ENV_PREFIX}ALLOWED_HOSTS must not be empty")
        if "*" in self.allowed_hosts:
            problems.append(f"{ENV_PREFIX}ALLOWED_HOSTS must not contain a wildcard")

        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def database_url(self) -> URL:
        assert self.database_password is not None
        return URL.create(
            "postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )

    @property
    def redis_url(self) -> str:
        assert self.redis_password is not None
        password = quote(self.redis_password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def require_secret(self, name: Literal["secret_key", "bootstrap_token"]) -> bytes:
        value: SecretStr | None = getattr(self, name)
        if value is None:
            raise ConfigurationError(f"{ENV_PREFIX}{name.upper()} is not configured")
        return value.get_secret_value().encode("utf-8")


def _normalize_origin(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("origins must be absolute http(s) URLs")
    if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
        raise ValueError("origins must not contain a path, query, fragment or credentials")
    return f"{parts.scheme}://{parts.netloc.lower()}"


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting validation failures into a redacted ConfigurationError."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        messages = []
        for error in exc.errors(include_url=False, include_input=False, include_context=False):
            location = ".".join(str(part) for part in error["loc"])
            message = error["msg"].removeprefix("Value error, ")
            messages.append(f"{location}: {message}" if location else message)
        raise ConfigurationError("Invalid configuration: " + " | ".join(messages)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
