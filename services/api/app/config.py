"""Application configuration.

Configuration is read from ``TRACEHOLLOW_*`` environment variables. Secrets may be
supplied directly or, preferably, through ``*_FILE`` variables that point to files
mounted by Docker Compose. Validation errors never include the offending values.
"""

from __future__ import annotations

import re
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
    ("ai_cloud_api_key", 20, False),
    ("credential_encryption_key", 64, False),
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

    # Evidence imports and previews (bounded; see docs/operations/evidence-storage.md).
    evidence_max_import_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    evidence_preview_max_bytes: int = Field(default=256 * 1024, ge=1024, le=5 * 1024 * 1024)
    evidence_json_max_depth: int = Field(default=64, ge=2, le=512)
    evidence_orphan_grace_seconds: int = Field(default=900, ge=0, le=86400)

    # Query execution and durable dispatch.
    run_lease_seconds: int = Field(default=60, ge=5, le=3600)
    # A run whose worker keeps disappearing is failed instead of being retried forever.
    run_max_claims: int = Field(default=5, ge=1, le=100)
    dispatch_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    dispatch_redelivery_seconds: int = Field(default=60, ge=1, le=3600)
    dispatch_redelivery_max_seconds: int = Field(default=900, ge=1, le=86400)

    # Synthetic fixture connector pacing (demo realism; tests set these to 0).
    fixture_page_delay_seconds: float = Field(default=0.3, ge=0, le=30)
    fixture_slow_page_delay_seconds: float = Field(default=2.0, ge=0, le=60)
    fixture_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=60)

    # Public-source collection (see docs/connectors/README.md). Network destinations for
    # collection are checked by app.connectors.netguard; these settings only narrow or, for
    # explicitly listed private networks, widen what it permits.
    credential_encryption_key: SecretStr | None = None
    credential_encryption_key_file: Path | None = None
    collection_allowed_private_networks: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    collection_allowed_ports: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [80, 443]
    )
    collection_max_response_bytes: int = Field(
        default=5 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024
    )
    collection_request_timeout_seconds: float = Field(default=20, gt=0, le=120)
    collection_max_redirects: int = Field(default=5, ge=0, le=10)
    # A source asking to wait longer than this ends the connector run as rate_limited instead.
    collection_max_retry_wait_seconds: float = Field(default=60, ge=0, le=900)
    collection_slot_lease_seconds: int = Field(default=300, ge=30, le=3600)
    collection_user_agent: str = Field(
        default="Tracehollow/0.1 (self-hosted OSINT workspace)", min_length=1, max_length=200
    )
    # Operator-configured API endpoint (GitHub.com by default; GitHub Enterprise Server or a
    # controlled fixture server otherwise). Never taken from case data.
    github_api_base_url: str = "https://api.github.com"
    # Sherlock site manifest; empty uses the manifest bundled with the pinned sherlock-project.
    sherlock_manifest_path: Path | None = None
    # Trusted internal endpoint of the Subfinder network sandbox (ADR 0007). Subfinder never runs
    # in the collector itself.
    discovery_runner_url: str = "http://discovery-runner:8090"

    # Evidence-grounded AI (see docs/operations/ai-models.md). The core workspace never needs a
    # model: with AI disabled or no model reachable, every other feature keeps working.
    ai_enabled: bool = True
    # "synthetic_fixture" is a deterministic, visibly labelled stand-in for tests and demos.
    ai_local_provider: Literal["ollama", "synthetic_fixture"] = "ollama"
    # Operator-configured model endpoint. Never derived from case data or user input.
    ai_ollama_base_url: str = "http://host.docker.internal:11434"
    ai_generation_model: str = Field(default="qwen3:8b", min_length=1, max_length=200)
    ai_embedding_model: str = Field(default="qwen3-embedding:0.6b", min_length=1, max_length=200)
    ai_cloud_provider: Literal["none", "anthropic"] = "none"
    ai_cloud_model: str = Field(default="claude-sonnet-5", min_length=1, max_length=200)
    ai_cloud_base_url: str = "https://api.anthropic.com"
    ai_cloud_api_key: SecretStr | None = None
    ai_cloud_api_key_file: Path | None = None
    ai_request_timeout_seconds: float = Field(default=300, gt=0, le=1800)
    ai_cloud_timeout_seconds: float = Field(default=120, gt=0, le=600)
    ai_max_retries: int = Field(default=1, ge=0, le=3)
    ai_max_output_tokens: int = Field(default=1200, ge=128, le=8192)
    ai_num_ctx: int = Field(default=16384, ge=2048, le=131072)
    # Chunks are short; a bounded embedding context keeps the embedding model's memory small.
    ai_embedding_num_ctx: int = Field(default=8192, ge=1024, le=131072)
    ai_max_context_chars: int = Field(default=14000, ge=2000, le=200000)
    ai_retrieval_top_k: int = Field(default=8, ge=1, le=30)
    ai_max_tool_calls: int = Field(default=4, ge=0, le=8)
    ai_max_active_runs_per_case: int = Field(default=3, ge=1, le=20)
    # A run lease must outlive the longest single model request.
    ai_run_lease_seconds: int = Field(default=420, ge=30, le=7200)
    ai_run_max_claims: int = Field(default=3, ge=1, le=20)
    ai_index_batch_size: int = Field(default=8, ge=1, le=100)
    ai_embedding_batch_size: int = Field(default=16, ge=1, le=256)
    ai_max_chunks_per_evidence: int = Field(default=500, ge=1, le=10000)
    ai_chunk_target_chars: int = Field(default=1200, ge=200, le=8000)
    ai_chunk_overlap_chars: int = Field(default=150, ge=0, le=2000)
    ai_index_max_attempts: int = Field(default=4, ge=1, le=20)

    @field_validator(
        "trusted_origins",
        "allowed_hosts",
        "collection_allowed_private_networks",
        "collection_allowed_ports",
        mode="before",
    )
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

        try:
            self.ai_ollama_base_url = _normalize_endpoint(self.ai_ollama_base_url, https_only=False)
        except ValueError as exc:
            problems.append(f"{ENV_PREFIX}AI_OLLAMA_BASE_URL: {exc}")
        try:
            self.ai_cloud_base_url = _normalize_endpoint(
                self.ai_cloud_base_url, https_only=self.env != "test"
            )
        except ValueError as exc:
            problems.append(f"{ENV_PREFIX}AI_CLOUD_BASE_URL: {exc}")
        try:
            self.github_api_base_url = _normalize_endpoint(
                self.github_api_base_url, https_only=False
            )
        except ValueError as exc:
            problems.append(f"{ENV_PREFIX}GITHUB_API_BASE_URL: {exc}")
        try:
            self.discovery_runner_url = _normalize_endpoint(
                self.discovery_runner_url, https_only=False
            )
        except ValueError as exc:
            problems.append(f"{ENV_PREFIX}DISCOVERY_RUNNER_URL: {exc}")
        key = self.credential_encryption_key
        if key is not None and not re.fullmatch(r"[0-9a-f]{64}", key.get_secret_value()):
            problems.append(
                f"{ENV_PREFIX}CREDENTIAL_ENCRYPTION_KEY must be 64 lowercase hexadecimal characters"
            )
        try:
            from app.connectors.netguard import parse_networks

            parse_networks(self.collection_allowed_private_networks)
        except ValueError as exc:
            problems.append(f"{ENV_PREFIX}COLLECTION_ALLOWED_PRIVATE_NETWORKS: {exc}")
        if any(not 1 <= port <= 65535 for port in self.collection_allowed_ports):
            problems.append(
                f"{ENV_PREFIX}COLLECTION_ALLOWED_PORTS must be ports between 1 and 65535"
            )
        if self.ai_chunk_overlap_chars >= self.ai_chunk_target_chars:
            problems.append(f"{ENV_PREFIX}AI_CHUNK_OVERLAP_CHARS must be below the chunk size")
        if self.ai_run_lease_seconds <= max(
            self.ai_request_timeout_seconds, self.ai_cloud_timeout_seconds
        ):
            problems.append(
                f"{ENV_PREFIX}AI_RUN_LEASE_SECONDS must exceed the longest model request timeout"
            )

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

    @property
    def ai_cloud_configured(self) -> bool:
        return self.ai_cloud_provider != "none" and self.ai_cloud_api_key is not None

    def secret_values(self) -> list[str]:
        """Every configured secret, for defence-in-depth scans of generated text."""
        values = []
        for name, _, _ in _SECRET_FIELDS:
            secret: SecretStr | None = getattr(self, name)
            if secret is not None:
                values.append(secret.get_secret_value())
        return [value for value in dict.fromkeys(values) if len(value) >= 8]

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


def _normalize_endpoint(value: str, *, https_only: bool) -> str:
    """Validate an operator-configured service endpoint (scheme, host, optional port and path)."""
    parts = urlsplit(value.strip())
    allowed = {"https"} if https_only else {"http", "https"}
    if parts.scheme not in allowed or not parts.hostname:
        raise ValueError(f"must be an absolute {' or '.join(sorted(allowed))} URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("must not contain credentials, a query or a fragment")
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


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
