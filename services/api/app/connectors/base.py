"""Connector contract (AGENTS.md "Connector contract", PRD FR-03)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.queries.models import ConnectorOutcome


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    retryable_outcomes: tuple[ConnectorOutcome, ...]


@dataclass(frozen=True, slots=True)
class ConnectorDescriptor:
    connector_id: str
    version: str
    display_name: str
    synthetic: bool
    description: str
    supported_input_types: tuple[str, ...]
    collection_mode: str
    credential_requirements: str
    coverage: str
    max_pages: int
    max_items_per_page: int
    timeout_seconds: int
    retry_policy: RetryPolicy
    output_schema: str
    cost_model: str | None
    last_live_verification: str | None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservedAccount:
    """One candidate account returned by a connector page."""

    source_object_id: str
    platform: str
    username: str
    display_name: str
    profile_reference: str
    linked_domain: str | None
    event_time: str | None


@dataclass(frozen=True, slots=True)
class ConnectorPage:
    page_index: int
    items: list[ObservedAccount]
    has_more: bool
    raw_payload: dict[str, Any]


class ConnectorError(Exception):
    """A source failure mapped to the PRD outcome vocabulary."""

    def __init__(
        self,
        outcome: ConnectorOutcome,
        detail: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class FetchRequest:
    input_type: str
    input_value: str
    parameters: dict[str, Any]
    page_index: int
    attempt: int
    max_items_per_page: int


class Connector(Protocol):
    descriptor: ConnectorDescriptor

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        """Raise ValueError for invalid saved-query parameters."""

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        """Return one bounded page or raise ConnectorError."""
