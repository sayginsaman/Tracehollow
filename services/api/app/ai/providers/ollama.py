"""Local Ollama provider for structured generation and embeddings.

Endpoints follow https://docs.ollama.com/api (``/api/chat``, ``/api/embed``, ``/api/tags``).
The base URL comes only from operator configuration; it is never taken from case data.
"""

from __future__ import annotations

import time
from typing import Literal

import httpx2

from app.ai.models import ProcessingLocation
from app.ai.providers.base import (
    MAX_EMBEDDING_RESPONSE_BYTES,
    MAX_GENERATION_RESPONSE_BYTES,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    ModelInventory,
    ProviderError,
    Usage,
    parse_model_json,
    read_json_response,
    require_grant,
)

# Qwen3-Embedding recommends an instruction for queries and none for documents.
QUERY_INSTRUCTION = (
    "Instruct: Given an investigator's question, retrieve case evidence passages that answer it"
    "\nQuery:"
)


def _error_from_status(status: int, payload: object, what: str) -> ProviderError:
    detail = payload.get("error") if isinstance(payload, dict) else None
    text = str(detail)[:200] if detail else f"HTTP {status}"
    if status == 404 or "not found" in text.lower():
        return ProviderError("model_not_found", f"The local {what} model is not installed.")
    if status == 429:
        return ProviderError("provider_rate_limited", "Ollama is busy.", retryable=True)
    if status >= 500:
        return ProviderError("provider_server_error", f"Ollama failed: {text}", retryable=True)
    return ProviderError("provider_bad_request", f"Ollama rejected the request: {text}")


class _OllamaBase:
    location = ProcessingLocation.LOCAL
    synthetic = False

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout = httpx2.Timeout(timeout_seconds, connect=5.0)
        self.transport = transport

    def _client(self) -> httpx2.Client:
        return httpx2.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        )

    def _post(
        self, path: str, body: dict[str, object], *, limit: int, what: str
    ) -> dict[str, object]:
        try:
            with self._client() as client, client.stream("POST", path, json=body) as response:
                payload, _ = read_json_response(response, limit=limit, provider="Ollama")
                if response.status_code != 200:
                    raise _error_from_status(response.status_code, payload, what)
        except httpx2.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", "The local model did not respond in time.", retryable=True
            ) from exc
        except httpx2.TransportError as exc:
            raise ProviderError(
                "model_unavailable",
                "Ollama could not be reached at the configured address.",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError("invalid_model_output", "Ollama returned an unreadable response.")
        return payload

    def inventory(self) -> ModelInventory:
        try:
            with self._client() as client, client.stream("GET", "/api/tags") as response:
                payload, _ = read_json_response(response, limit=1024 * 1024, provider="Ollama")
        except httpx2.TransportError:
            return ModelInventory(reachable=False, models={}, error_code="model_unavailable")
        if response.status_code != 200 or not isinstance(payload, dict):
            return ModelInventory(reachable=True, models={}, error_code="provider_server_error")
        models: dict[str, str | None] = {}
        for item in payload.get("models") or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                digest = item.get("digest")
                models[item["name"]] = digest if isinstance(digest, str) else None
        return ModelInventory(reachable=True, models=models)


class OllamaGenerationProvider(_OllamaBase):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        num_ctx: int,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url, model=model, timeout_seconds=timeout_seconds, transport=transport
        )
        self.num_ctx = num_ctx

    def generate_json(self, request: GenerationRequest) -> GenerationResult:
        require_grant(request, ProcessingLocation.LOCAL)
        started = time.monotonic()
        payload = self._post(
            "/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                "stream": False,
                "format": request.schema,
                "think": False,
                "keep_alive": "10m",
                "options": {
                    "temperature": 0,
                    "seed": 7,
                    "num_predict": request.max_output_tokens,
                    "num_ctx": self.num_ctx,
                },
            },
            limit=MAX_GENERATION_RESPONSE_BYTES,
            what="generation",
        )
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError("invalid_model_output", "Ollama returned no message content.")
        if payload.get("done_reason") == "length":
            raise ProviderError(
                "output_truncated",
                "The local model reached the output limit before finishing its answer.",
            )
        prompt_tokens, output_tokens = payload.get("prompt_eval_count"), payload.get("eval_count")
        reported = isinstance(prompt_tokens, int) and isinstance(output_tokens, int)
        return GenerationResult(
            data=parse_model_json(content, "local"),
            usage=Usage(
                input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                source="provider_reported" if reported else "unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
            ),
            model=str(payload.get("model") or self.model),
            stop_reason=str(payload.get("done_reason")) if payload.get("done_reason") else None,
        )


class OllamaEmbeddingProvider(_OllamaBase):
    name = "ollama"

    def embed(self, texts: list[str], *, purpose: Literal["document", "query"]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], usage=Usage(), model=self.model)
        inputs = [f"{QUERY_INSTRUCTION}{text}" for text in texts] if purpose == "query" else texts
        started = time.monotonic()
        payload = self._post(
            "/api/embed",
            {"model": self.model, "input": inputs, "truncate": True, "keep_alive": "10m"},
            limit=MAX_EMBEDDING_RESPONSE_BYTES,
            what="embedding",
        )
        vectors = payload.get("embeddings")
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(texts)
            or not all(isinstance(vector, list) and vector for vector in vectors)
        ):
            raise ProviderError(
                "invalid_model_output", "Ollama returned an unexpected number of embeddings."
            )
        tokens = payload.get("prompt_eval_count")
        return EmbeddingResult(
            vectors=[[float(value) for value in vector] for vector in vectors],
            usage=Usage(
                input_tokens=tokens if isinstance(tokens, int) else None,
                source="provider_reported" if isinstance(tokens, int) else "unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
            ),
            model=str(payload.get("model") or self.model),
        )
