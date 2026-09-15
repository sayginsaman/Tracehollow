"""Connector contract (AGENTS.md "Connector contract", PRD FR-03).

A connector turns one saved-query input into bounded *pages*. Each page is a self-contained unit
that the execution engine persists in one transaction: evidence (original bytes with provenance),
entities it refers to, observations (source-specific facts) and relationships between entities.
Connectors never touch the database or the evidence store themselves, and they report every
failure as a :class:`ConnectorError` with an outcome from the PRD vocabulary, never as an empty
page.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import httpx2

from app.connectors.netguard import NetworkPolicy
from app.queries.models import ConnectorOutcome


class CollectionMode(enum.StrEnum):
    """How a connector reaches its data. Shown to analysts before they run anything."""

    SYNTHETIC_FIXTURE = "synthetic_fixture"
    # Contacts the server named by the input directly (the target sees the request).
    DIRECT_REQUEST = "direct_request"
    # Asks a third-party service about the input; the target itself is not contacted.
    THIRD_PARTY_API = "third_party_api"
    # Requests candidate profile addresses on third-party platforms; each platform sees a request.
    PLATFORM_PROBE = "platform_probe"


class VerificationStatus(enum.StrEnum):
    SYNTHETIC = "synthetic"
    FIXTURE_TESTED = "fixture_tested"
    LIVE_VERIFIED = "live_verified"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    retryable_outcomes: tuple[ConnectorOutcome, ...]
    base_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One saved-query parameter, described so the UI can render and the API can validate it."""

    name: str
    kind: Literal["choice", "multi_choice", "integer", "boolean"]
    label: str
    description: str
    default: Any
    choices: Mapping[str, str] | None = None
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    name: str
    label: str
    description: str
    required: bool


@dataclass(frozen=True, slots=True)
class ConnectorDescriptor:
    connector_id: str
    version: str
    display_name: str
    synthetic: bool
    description: str
    supported_input_types: tuple[str, ...]
    collection_mode: CollectionMode
    credential_requirements: str
    coverage: str
    max_pages: int
    max_items_per_page: int
    timeout_seconds: int
    retry_policy: RetryPolicy
    output_schema: str
    cost_model: str | None
    last_live_verification: str | None
    verification_status: VerificationStatus
    parameters: tuple[ParameterSpec, ...] = ()
    credentials: tuple[CredentialSpec, ...] = ()
    cache_policy: str = (
        "No caching: every execution retrieves fresh data and stores it as new evidence."
    )
    quota_notes: str | None = None
    max_concurrent_runs: int = 2
    # Minimum spacing between requests to one pacing key (host or API), across all workers.
    min_request_interval_seconds: float = 0.0
    provider_terms: str | None = None
    documentation: str | None = None

    def parameter(self, name: str) -> ParameterSpec | None:
        return next((spec for spec in self.parameters if spec.name == name), None)


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    """Original bytes to store. ``key`` is local to the page and links drafts together."""

    key: str
    kind: Literal["text", "json", "html", "xml"]
    content: bytes
    content_type: str
    title: str
    source_reference: str
    collection_metadata: dict[str, Any] = field(default_factory=dict)
    access_category: Literal["public", "credentialed", "synthetic"] = "public"
    source_published_at: datetime | None = None
    source_published_at_original: str | None = None
    derived_from: str | None = None
    description: str = ""
    # Text/JSON evidence is indexed for AI retrieval unless a derived record replaces it or
    # this is False (e.g. an error page body).
    indexable: bool = True


@dataclass(frozen=True, slots=True)
class IdentifierDraft:
    identifier_type: str
    value: str
    platform: str | None = None


@dataclass(frozen=True, slots=True)
class EntityDraft:
    """An entity the page refers to. Matched only against observed entities of the same type
    that share the ``match`` identifier; analyst-created entities are never modified."""

    key: str
    entity_type: str
    display_name: str
    match: IdentifierDraft
    identifiers: tuple[IdentifierDraft, ...] = ()
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    observation_type: str
    evidence_key: str
    payload: dict[str, Any]
    source_object_id: str | None = None
    entity_key: str | None = None
    event_time: datetime | None = None
    source_published_at: datetime | None = None
    # Stable within the connector run; defaults to the observation's position on the page.
    idempotency_suffix: str | None = None
    # The same source object on a later page of the run is the same observation (counted once).
    dedupe_across_pages: bool = False


@dataclass(frozen=True, slots=True)
class RelationshipDraft:
    source_key: str
    target_key: str
    predicate: str
    origin: Literal["observed", "deterministic_derivation"]
    description: str
    # Index into the page's observations that supports the relationship.
    observation_index: int


@dataclass
class ConnectorPage:
    page_index: int
    has_more: bool
    evidence: list[EvidenceDraft] = field(default_factory=list)
    entities: list[EntityDraft] = field(default_factory=list)
    observations: list[ObservationDraft] = field(default_factory=list)
    relationships: list[RelationshipDraft] = field(default_factory=list)
    # Usable items on this page (counted into items_collected).
    items: int = 0
    # Connector-specific counters merged into the connector run's coverage.
    coverage: dict[str, Any] = field(default_factory=dict)
    # Set when the page is usable but incomplete (truncated body, sources that failed, ...).
    incomplete_reason: str | None = None
    # Outcome to report if the whole run ends without a more specific one (e.g. no_findings
    # only after every source answered). ``None`` lets the engine decide from ``items``.
    outcome_hint: ConnectorOutcome | None = None
    # Machine-readable reason recorded as the run's error code when the hint is a failure.
    outcome_code: str | None = None
    # State needed to fetch the next page (e.g. a pagination URL). Persisted with the page.
    next_cursor: dict[str, Any] | None = None
    quota: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


class ConnectorError(Exception):
    """A source failure mapped to the PRD outcome vocabulary."""

    def __init__(
        self,
        outcome: ConnectorOutcome,
        detail: str,
        *,
        retry_after_seconds: float | None = None,
        code: str | None = None,
        quota: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds
        self.code = code
        self.quota = quota


def _never_cancelled() -> bool:
    return False


def _ignore_progress(_: dict[str, Any]) -> None:
    return None


def _no_credential(_: str) -> str | None:
    return None


def _no_pacing(_: str, __: float) -> None:
    return None


def _ignore_credential_result(_: str, __: str) -> None:
    return None


@dataclass
class FetchContext:
    """What the engine offers a connector while it fetches one page."""

    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    cancelled: Callable[[], bool] = _never_cancelled
    progress: Callable[[dict[str, Any]], None] = _ignore_progress
    credential: Callable[[str], str | None] = _no_credential
    # Report whether a credential was accepted or rejected by the source ("accepted"/"rejected").
    credential_result: Callable[[str, str], None] = _ignore_credential_result
    pace: Callable[[str, float], None] = _no_pacing
    deadline: float = field(default_factory=lambda: time.monotonic() + 300)
    max_response_bytes: int = 5 * 1024 * 1024
    request_timeout_seconds: float = 20.0
    max_redirects: int = 5
    user_agent: str = "Tracehollow/0.1 (+self-hosted OSINT workspace)"
    # Tests replace network access here; production uses the guarded transport.
    http_transport: httpx2.BaseTransport | None = None
    settings: Any = None

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


@dataclass(frozen=True, slots=True)
class FetchRequest:
    input_type: str
    input_value: str
    parameters: dict[str, Any]
    page_index: int
    attempt: int
    max_items_per_page: int
    cursor: dict[str, Any] | None = None
    context: FetchContext = field(default_factory=FetchContext)


class Connector(Protocol):
    descriptor: ConnectorDescriptor

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        """Raise ValueError for invalid saved-query parameters."""

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        """Return one bounded page or raise ConnectorError."""


def validate_parameters(descriptor: ConnectorDescriptor, parameters: dict[str, Any]) -> None:
    """Check parameters against the descriptor's specs (types, choices, bounds, unknown keys)."""
    known = {spec.name for spec in descriptor.parameters}
    unknown = sorted(set(parameters) - known)
    if unknown:
        raise ValueError(f"unsupported parameters: {', '.join(unknown)}")
    for spec in descriptor.parameters:
        if spec.name not in parameters:
            continue
        value = parameters[spec.name]
        if spec.kind == "choice":
            if not isinstance(value, str) or spec.choices is None or value not in spec.choices:
                raise ValueError(f"unknown {spec.label.lower()} '{value}'")
        elif spec.kind == "multi_choice":
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) for item in value)
            ):
                raise ValueError(f"{spec.name} must be a non-empty list of names")
            invalid = sorted({item for item in value if spec.choices and item not in spec.choices})
            if invalid:
                raise ValueError(f"unknown {spec.label.lower()}: {', '.join(invalid)}")
            if spec.maximum is not None and len(value) > spec.maximum:
                raise ValueError(f"at most {spec.maximum} {spec.label.lower()} can be selected")
        elif spec.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{spec.name} must be an integer")
            if spec.minimum is not None and value < spec.minimum:
                raise ValueError(f"{spec.name} must be at least {spec.minimum}")
            if spec.maximum is not None and value > spec.maximum:
                raise ValueError(f"{spec.name} must be at most {spec.maximum}")
        elif spec.kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{spec.name} must be true or false")


def parameter_value(descriptor: ConnectorDescriptor, parameters: dict[str, Any], name: str) -> Any:
    spec = descriptor.parameter(name)
    assert spec is not None, name
    return parameters.get(name, spec.default)
