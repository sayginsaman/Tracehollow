from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _clean(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


Name = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_clean)]


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["interval", "daily", "weekly"]
    every_minutes: int | None = None
    time: str | None = None
    weekdays: list[int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class MonitorScope(BaseModel):
    """Bounded target scope: at most the saved query's own limits."""

    model_config = ConfigDict(extra="forbid")

    max_pages: Annotated[int, Field(ge=1, le=10)] = 1
    max_items_per_page: Annotated[int, Field(ge=1, le=5000)] = 5


class MonitorLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Outbound requests one execution may issue, retries and pagination included.
    max_requests_per_run: Annotated[int, Field(ge=1, le=10_000)] = 25
    # Items one connector may collect in one execution.
    max_items_per_run: Annotated[int, Field(ge=1, le=100_000)] = 200
    # Wall-clock limit of one execution.
    max_run_seconds: Annotated[int, Field(ge=30, le=3600)] = 600


class MonitorBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["day", "week", "month"] = "day"
    max_requests: Annotated[int, Field(ge=1, le=1_000_000)] = 100
    # Documented provider quota units (estimates); never money.
    max_provider_units: Annotated[int | None, Field(ge=1, le=10_000_000)] = None


class MonitorRetention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Results of older executions of this monitor become eligible for removal by the case
    # retention job; the latest complete baseline is always kept.
    keep_last_runs: Annotated[int | None, Field(ge=2, le=1000)] = None
    max_age_days: Annotated[int | None, Field(ge=1, le=3650)] = None


class MonitorNotify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_change: bool = True
    on_failure: bool = True
    on_budget_exhausted: bool = True
    # Completion updates for unchanged successful runs are off by default to avoid noise.
    on_completion: bool = False
    recipients: Literal["case_analysts", "all_members"] = "case_analysts"


class MonitorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Annotated[str, Field(max_length=2000), AfterValidator(_clean)] = ""
    saved_query_id: uuid.UUID
    # Defaults to every connector of the saved query.
    connector_ids: Annotated[list[str] | None, Field(min_length=1, max_length=10)] = None
    schedule: ScheduleIn
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "UTC"
    missed_run_policy: Literal["run_latest", "skip"] = "run_latest"
    scope: MonitorScope = Field(default_factory=MonitorScope)
    limits: MonitorLimits = Field(default_factory=MonitorLimits)
    budget: MonitorBudget = Field(default_factory=MonitorBudget)
    retention: MonitorRetention = Field(default_factory=MonitorRetention)
    notify: MonitorNotify = Field(default_factory=MonitorNotify)
    # Monitors are created paused unless enabled explicitly.
    enable: bool = False
    # Required to enable a monitor whose connectors contact external sources.
    acknowledge_recurring_collection: bool = False


class MonitorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    connector_ids: Annotated[list[str] | None, Field(min_length=1, max_length=10)] = None
    schedule: ScheduleIn | None = None
    timezone: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    missed_run_policy: Literal["run_latest", "skip"] | None = None
    scope: MonitorScope | None = None
    limits: MonitorLimits | None = None
    budget: MonitorBudget | None = None
    retention: MonitorRetention | None = None
    notify: MonitorNotify | None = None


class MonitorResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_recurring_collection: bool = False
    # Resuming a monitor paused because its saved query changed adopts the new definition (and
    # starts a new baseline for change detection).
    adopt_query_changes: bool = False


class UsageOut(BaseModel):
    scope_type: str
    metric: str
    period: str
    period_start: datetime
    period_end: datetime | None
    limit_units: int
    reserved_units: int
    consumed_units: int
    estimated_units: int
    remaining_units: int
    exhausted: bool
    denied_requests: int


class ChangeSummary(BaseModel):
    change_set_id: uuid.UUID
    connector_id: str
    status: str
    counts: dict[str, int]


class OccurrenceOut(BaseModel):
    id: uuid.UUID
    kind: str
    scheduled_for: datetime
    status: str
    skip_reason: str | None
    missed_slots: int
    config_version: int
    query_run_id: uuid.UUID | None
    run_status: str | None
    run_error_code: str | None
    connector_outcomes: list[str | None]
    changes: list[ChangeSummary]
    dispatched_by: str
    created_at: datetime


class ActionItem(BaseModel):
    code: str
    message: str


class MonitorOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    saved_query_id: uuid.UUID
    saved_query_name: str
    name: str
    description: str
    status: str
    status_reason: str | None
    status_changed_at: datetime
    schedule: dict[str, Any]
    timezone: str
    missed_run_policy: str
    connector_ids: list[str]
    scope: dict[str, Any]
    limits: dict[str, Any]
    budget: dict[str, Any]
    retention: dict[str, Any]
    notify: dict[str, Any]
    config_version: int
    collects_live: bool
    query_changed: bool
    authorized_by: str | None
    next_run_at: datetime | None
    last_scheduled_for: datetime | None
    consecutive_failures: int
    active_run_id: uuid.UUID | None
    last_occurrence: OccurrenceOut | None
    budget_usage: list[UsageOut]
    actions: list[ActionItem]
    created_at: datetime
    updated_at: datetime
