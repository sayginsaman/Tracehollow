"""Provider contract shared by local, cloud and synthetic fixture model providers.

Providers only turn a fully prepared request into structured output or vectors. They never see
case identifiers, database handles or secrets other than their own credential, and they never
decide whether a case may use them: that is the processing policy's job (``policy.py``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx2

from app.ai.models import ProcessingLocation

# Responses larger than this are rejected rather than parsed.
MAX_GENERATION_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_EMBEDDING_RESPONSE_BYTES = 64 * 1024 * 1024


class ProviderError(Exception):
    """A model provider failure with a stable code that is safe to show to users."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    # "provider_reported" when the provider returned counts, otherwise "unavailable".
    source: str = "unavailable"
    duration_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "source": self.source,
            "duration_ms": self.duration_ms,
            # Tracehollow has no verified price list; costs are never estimated silently.
            "cost": "unknown",
        }


@dataclass(frozen=True, slots=True)
class ProcessingGrant:
    """Proof that the processing policy allowed this location for this request."""

    location: ProcessingLocation
    case_policy_version: int


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    task: Literal["plan", "answer", "summary", "relationship_suggestions"]
    system: str
    user: str
    schema: dict[str, Any]
    max_output_tokens: int
    grant: ProcessingGrant
    # Structured copy of the prompt context. Only the synthetic fixture provider reads it.
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerationResult:
    data: dict[str, Any]
    usage: Usage
    model: str
    stop_reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    usage: Usage
    model: str


@dataclass(frozen=True, slots=True)
class ModelInventory:
    reachable: bool
    models: dict[str, str | None]  # model name -> digest (None when unknown)
    error_code: str | None = None


class GenerationProvider(Protocol):
    name: str
    location: ProcessingLocation
    model: str
    synthetic: bool

    def generate_json(self, request: GenerationRequest) -> GenerationResult: ...


class EmbeddingProvider(Protocol):
    name: str
    location: ProcessingLocation
    model: str
    synthetic: bool

    def embed(
        self, texts: list[str], *, purpose: Literal["document", "query"]
    ) -> EmbeddingResult: ...

    def inventory(self) -> ModelInventory: ...


def require_grant(request: GenerationRequest, location: ProcessingLocation) -> None:
    if request.grant.location != location:
        raise ProviderError(
            "processing_not_permitted",
            f"This request was not authorized for {location.value} processing.",
        )


def read_json_response(
    response: httpx2.Response, *, limit: int, provider: str
) -> tuple[Any, bytes]:
    """Read a streamed response body with a size limit and parse it as JSON."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise ProviderError(
                "provider_response_too_large", f"The {provider} response exceeded {limit} bytes."
            )
    raw = bytes(body)
    try:
        return json.loads(raw) if raw else None, raw
    except json.JSONDecodeError:
        return None, raw


def parse_model_json(text: str, provider: str) -> dict[str, Any]:
    """Parse the model's structured output. Thinking tags or code fences are tolerated."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else candidate
    if "</think>" in candidate:
        candidate = candidate.rsplit("</think>", 1)[1]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end < start:
        raise ProviderError(
            "invalid_model_output", f"The {provider} model did not return a JSON object."
        )
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "invalid_model_output", f"The {provider} model returned malformed JSON."
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError(
            "invalid_model_output", f"The {provider} model did not return a JSON object."
        )
    return data


def as_list(value: Any) -> list[Any]:
    """Untrusted JSON value as a list (anything else becomes empty)."""
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    """Untrusted JSON value as an object (anything else becomes empty)."""
    return value if isinstance(value, dict) else {}
