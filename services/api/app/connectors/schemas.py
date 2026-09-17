from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class ParameterSpecOut(BaseModel):
    name: str
    kind: str
    label: str
    description: str
    default: Any
    choices: dict[str, str] | None
    minimum: int | None
    maximum: int | None


class CredentialStatusOut(BaseModel):
    name: str
    label: str
    description: str
    required: bool
    configured: bool
    usable: bool
    updated_at: datetime | None
    last_used_at: datetime | None
    last_result: str | None


class ConnectorHealthOut(BaseModel):
    """Recent outcomes in cases the requesting user can access."""

    last_run_at: datetime | None
    last_outcome: str | None
    last_error_code: str | None
    last_quota: dict[str, Any] | None
    recent_outcomes: dict[str, int]


class CapabilityOut(BaseModel):
    name: str
    label: str
    status: str  # implemented | not_implemented | excluded
    access_method: str
    provider: str
    collection_mode: str | None
    account_types: list[str]
    content_types: list[str]
    returned_fields: list[str]
    unavailable_fields: list[str]
    stable_identifiers: list[str]
    pagination: str
    session_requirements: str
    restrictions: str
    cost_quota: str
    verification_status: str | None
    last_live_verification: str | None
    credential_names: list[str]
    reason: str | None
    references: list[str]
    # Whether it can run in this installation now, and if not, why (missing credentials, turned
    # off by configuration, no adapter).
    available: bool
    blocked_reason: str | None


class ConnectorDescriptorOut(BaseModel):
    connector_id: str
    version: str
    display_name: str
    synthetic: bool
    description: str
    supported_input_types: list[str]
    collection_mode: str
    credential_requirements: str
    coverage: str
    max_pages: int
    max_items_per_page: int
    timeout_seconds: int
    retry_max_attempts: int
    retryable_outcomes: list[str]
    output_schema: str
    cost_model: str | None
    quota_notes: str | None
    cache_policy: str
    max_concurrent_runs: int
    min_request_interval_seconds: float
    provider_terms: str | None
    documentation: str | None
    last_live_verification: str | None
    verification_status: str
    parameters: list[ParameterSpecOut]
    credentials: list[CredentialStatusOut]
    health: ConnectorHealthOut
    capabilities: list[CapabilityOut] = []


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[str, Field(min_length=1, max_length=4096)]
