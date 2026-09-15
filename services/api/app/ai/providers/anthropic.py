"""Optional cloud generation through the Anthropic Messages API.

Checked against the primary documentation on 2026-09-16:
- https://platform.claude.com/docs/en/api/messages (headers, body, usage, stop reasons)
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
  (``output_config.format`` with ``type: json_schema``; no beta header required)
- https://platform.claude.com/docs/en/api/errors (error shapes, ``request-id`` header)

Anthropic offers no embeddings API, so embeddings always stay local. This provider is only
constructed with a configured API key and only sends a request carrying a cloud processing
grant issued by the case policy. It has not been verified against the live API in this
repository (no credentials were available); tests use a mocked transport.
"""

from __future__ import annotations

import time
from typing import Any

import httpx2

from app.ai.models import ProcessingLocation
from app.ai.providers.base import (
    MAX_GENERATION_RESPONSE_BYTES,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    Usage,
    as_dict,
    parse_model_json,
    read_json_response,
    require_grant,
)

API_VERSION = "2023-06-01"

_ERRORS: dict[int, tuple[str, str, bool]] = {
    400: ("provider_bad_request", "The cloud provider rejected the request.", False),
    401: ("provider_auth_failed", "The cloud provider API key was rejected.", False),
    402: ("provider_billing_error", "The cloud provider reported a billing problem.", False),
    403: ("provider_permission_denied", "The API key may not use this model.", False),
    404: ("model_not_found", "The configured cloud model was not found.", False),
    413: ("provider_request_too_large", "The request was too large for the provider.", False),
    429: ("provider_rate_limited", "The cloud provider rate limit was reached.", True),
    500: ("provider_server_error", "The cloud provider had an internal error.", True),
    504: ("provider_timeout", "The cloud provider timed out.", True),
    529: ("provider_overloaded", "The cloud provider is temporarily overloaded.", True),
}


class AnthropicGenerationProvider:
    name = "anthropic"
    location = ProcessingLocation.CLOUD
    synthetic = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = httpx2.Timeout(timeout_seconds, connect=10.0)
        self.transport = transport

    def generate_json(self, request: GenerationRequest) -> GenerationResult:
        require_grant(request, ProcessingLocation.CLOUD)
        started = time.monotonic()
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {"format": {"type": "json_schema", "schema": request.schema}},
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            with (
                httpx2.Client(
                    base_url=self.base_url,
                    timeout=self.timeout,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream("POST", "/v1/messages", json=body, headers=headers) as response,
            ):
                payload, _ = read_json_response(
                    response, limit=MAX_GENERATION_RESPONSE_BYTES, provider="cloud"
                )
                request_id = response.headers.get("request-id")
                if response.status_code != 200:
                    code, message, retryable = _ERRORS.get(
                        response.status_code,
                        (
                            "provider_server_error"
                            if response.status_code >= 500
                            else "provider_bad_request",
                            f"The cloud provider returned HTTP {response.status_code}.",
                            response.status_code >= 500,
                        ),
                    )
                    retry_after = response.headers.get("retry-after")
                    raise ProviderError(
                        code,
                        message,
                        retryable=retryable,
                        retry_after_seconds=float(retry_after)
                        if retry_after and retry_after.replace(".", "", 1).isdigit()
                        else None,
                        request_id=request_id,
                    )
        except httpx2.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", "The cloud provider did not respond in time.", retryable=True
            ) from exc
        except httpx2.TransportError as exc:
            raise ProviderError(
                "model_unavailable", "The cloud provider could not be reached.", retryable=True
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError(
                "invalid_model_output", "The cloud provider response was unreadable."
            )
        stop_reason = payload.get("stop_reason")
        if stop_reason == "max_tokens":
            raise ProviderError(
                "output_truncated", "The cloud model reached the output limit before finishing."
            )
        if stop_reason == "refusal":
            raise ProviderError("provider_refused", "The cloud model declined to answer.")
        texts = [
            block.get("text")
            for block in payload.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "".join(item for item in texts if isinstance(item, str))
        usage = as_dict(payload.get("usage"))
        input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        reported = isinstance(input_tokens, int) and isinstance(output_tokens, int)
        return GenerationResult(
            data=parse_model_json(text, "cloud"),
            usage=Usage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                source="provider_reported" if reported else "unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
            ),
            model=str(payload.get("model") or self.model),
            stop_reason=str(stop_reason) if stop_reason else None,
            request_id=request_id,
        )
