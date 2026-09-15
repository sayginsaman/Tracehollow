from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


class QueryLimits(BaseModel):
    """Upper bounds for a run. Values above a connector's own limits are capped to them."""

    model_config = ConfigDict(extra="forbid")

    max_pages: Annotated[int, Field(ge=1, le=10)] = 3
    max_items_per_page: Annotated[int, Field(ge=1, le=5000)] = 5


class SavedQueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_nfc)]
    input_type: Annotated[str, Field(pattern=r"^[a-z_]{2,32}$")]
    input_value: Annotated[str, Field(min_length=1, max_length=1000), AfterValidator(_nfc)]
    connector_ids: Annotated[list[str], Field(min_length=1, max_length=10)]
    parameters: Annotated[dict[str, Any], Field(max_length=20)] = Field(default_factory=dict)
    limits: QueryLimits = Field(default_factory=QueryLimits)


class SavedQueryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    input_value: Annotated[str | None, Field(min_length=1, max_length=1000)] = None
    parameters: Annotated[dict[str, Any] | None, Field(max_length=20)] = None
    limits: QueryLimits | None = None


class SavedQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    name: str
    input_type: str
    input_value: str
    connector_ids: list[str]
    collection_mode: str
    parameters: dict[str, Any]
    limits: dict[str, Any]
    run_counter: int
    created_at: datetime
    updated_at: datetime
    synthetic: bool = False
    last_run_id: uuid.UUID | None = None
    last_run_status: str | None = None


class ConnectorRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    connector_id: str
    connector_version: str
    status: str
    outcome: str | None
    pages_completed: int
    items_collected: int
    fetch_attempts: int
    retries: int
    last_error_code: str | None
    last_error_detail: str | None
    retry_after_seconds: float | None
    coverage: dict[str, Any]
    coverage_note: str | None
    quota_usage: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None


class QueryRunOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    saved_query_id: uuid.UUID | None
    saved_query_name: str | None
    run_number: int
    status: str
    parameters_snapshot: dict[str, Any]
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    error_code: str | None
    synthetic: bool
    evidence_count: int
    observation_count: int
    dispatch_status: str | None


class QueryRunDetail(QueryRunOut):
    connector_runs: list[ConnectorRunOut]
    entity_count: int
    relationship_count: int
