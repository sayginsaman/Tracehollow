"""Shared helpers for AI tests: scripted providers, recording transports and direct executors."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy.orm import Session, sessionmaker

from app.ai.indexing import IndexContext, index_case
from app.ai.models import ProcessingLocation
from app.ai.policy import ProviderSet, build_providers
from app.ai.providers.base import (
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    ModelInventory,
    ProviderError,
    Usage,
    require_grant,
)
from app.ai.providers.fixture import FixtureEmbeddingProvider, FixtureGenerationProvider
from app.ai.runs import AiRunContext, execute_ai_run
from app.config import Settings
from app.evidence.storage import EvidenceStorage


@dataclass
class ScriptedGeneration:
    """A generation provider whose outputs are scripted per task (a hostile or broken model)."""

    # Each item is a response, an error to raise, or a callable building one from the request.
    responses: dict[str, list[Any]]
    name: str = "scripted"
    location: ProcessingLocation = ProcessingLocation.FIXTURE
    model: str = "scripted-test-model"
    synthetic: bool = True
    requests: list[GenerationRequest] = field(default_factory=list)
    fallback: FixtureGenerationProvider = field(default_factory=FixtureGenerationProvider)

    def generate_json(self, request: GenerationRequest) -> GenerationResult:
        require_grant(request, self.location)
        self.requests.append(request)
        queue = self.responses.get(request.task)
        if not queue:
            return self.fallback.generate_json(request)
        item = queue.pop(0)
        if isinstance(item, ProviderError):
            raise item
        if callable(item):
            item = item(request)
        return GenerationResult(
            data=item,
            usage=Usage(input_tokens=11, output_tokens=7, source="provider_reported"),
            model=self.model,
        )


@dataclass
class FailingEmbeddings:
    error: ProviderError
    name: str = "synthetic_fixture"
    location: ProcessingLocation = ProcessingLocation.FIXTURE
    model: str = "synthetic-hash-embedding-v1"
    synthetic: bool = True
    reachable: bool = True
    calls: int = 0

    def embed(self, texts: list[str], *, purpose: str) -> EmbeddingResult:
        self.calls += 1
        raise self.error

    def inventory(self) -> ModelInventory:
        return ModelInventory(
            reachable=self.reachable,
            models={self.model: "fixture-v1"} if self.reachable else {},
            error_code=None if self.reachable else "model_unavailable",
        )


class RecordingTransport(httpx2.MockTransport):
    """Counts and records every HTTP request a provider tries to make."""

    def __init__(self, handler: Callable[[httpx2.Request], httpx2.Response] | None = None) -> None:
        self.requests: list[httpx2.Request] = []

        def record(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            if handler is not None:
                return handler(request)
            return httpx2.Response(
                500, json={"type": "error", "error": {"type": "api_error", "message": "unexpected"}}
            )

        super().__init__(record)


def anthropic_answer(data: dict[str, Any]) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"request-id": "req_test"},
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": json.dumps(data)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 120, "output_tokens": 40},
            },
        )

    return handler


def fixture_providers(**overrides: Any) -> ProviderSet:
    providers = ProviderSet(
        local_generation=FixtureGenerationProvider(), embeddings=FixtureEmbeddingProvider()
    )
    for key, value in overrides.items():
        setattr(providers, key, value)
    return providers


def run_indexing(
    settings: Settings,
    session_factory: sessionmaker[Session],
    case_id: object,
    providers: ProviderSet | None = None,
    *,
    max_batches: int = 20,
    **hooks: Any,
) -> list[str]:
    context = IndexContext(
        session_factory=session_factory,
        storage=EvidenceStorage(Path(settings.evidence_storage_path)),
        settings=settings,
        providers=providers or fixture_providers(),
        **hooks,
    )
    outcomes = []
    for _ in range(max_batches):
        result = index_case(context, uuid.UUID(str(case_id)))
        outcomes.append(result.status)
        if result.status != "more":
            break
    return outcomes


def run_ai(
    settings: Settings,
    session_factory: sessionmaker[Session],
    run_id: object,
    providers: ProviderSet | None = None,
    **hooks: Any,
) -> str:
    context = AiRunContext(
        session_factory=session_factory,
        storage=EvidenceStorage(Path(settings.evidence_storage_path)),
        settings=settings,
        providers=providers or build_providers(settings),
        sleep=lambda _: None,
        **hooks,
    )
    return execute_ai_run(context, uuid.UUID(str(run_id)))


def ref_containing(request: GenerationRequest, needle: str) -> str:
    """The evidence label (E#) whose text contains ``needle`` in this request's context."""
    for block in request.context.get("evidence", []):
        if needle in block["text"]:
            return str(block["ref"])
    raise AssertionError(f"no evidence block contains {needle!r}")
