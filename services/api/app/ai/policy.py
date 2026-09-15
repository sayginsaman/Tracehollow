"""Processing policy and provider construction.

This module is the only place that turns configuration and a case's AI mode into a processing
grant. There is no fallback between locations: a run requested for local processing never
reaches a cloud provider, and a cloud request that is not allowed fails instead of silently
running elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx2

from app.ai.models import AiMode, ProcessingLocation
from app.ai.providers.anthropic import AnthropicGenerationProvider
from app.ai.providers.base import EmbeddingProvider, GenerationProvider, ProcessingGrant
from app.ai.providers.fixture import FixtureEmbeddingProvider, FixtureGenerationProvider
from app.ai.providers.ollama import OllamaEmbeddingProvider, OllamaGenerationProvider
from app.config import Settings


class PolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def local_location(settings: Settings) -> ProcessingLocation:
    return (
        ProcessingLocation.FIXTURE
        if settings.ai_local_provider == "synthetic_fixture"
        else ProcessingLocation.LOCAL
    )


def check_case_ai_available(settings: Settings, case_mode: str) -> None:
    if not settings.ai_enabled:
        raise PolicyError("ai_disabled", "AI features are disabled for this installation.")
    if case_mode == AiMode.DISABLED:
        raise PolicyError("case_ai_disabled", "AI processing is turned off for this case.")


def authorize_generation(
    settings: Settings,
    *,
    case_mode: str,
    case_policy_version: int,
    run_policy_version: int,
    requested_location: str,
) -> ProcessingGrant:
    """Issue a grant for one model request, or raise ``PolicyError``.

    Called immediately before every generation request, not only when the run is created, so a
    policy change or an installation-wide switch-off takes effect for queued and running work.
    """
    check_case_ai_available(settings, case_mode)
    if run_policy_version != case_policy_version:
        raise PolicyError(
            "processing_policy_changed",
            "The case's AI processing setting changed after this request was made.",
        )
    if requested_location == ProcessingLocation.CLOUD:
        if case_mode != AiMode.CLOUD_ALLOWED:
            raise PolicyError(
                "cloud_processing_not_allowed",
                "This case only allows local processing; nothing was sent to a cloud provider.",
            )
        if not settings.ai_cloud_configured:
            raise PolicyError("cloud_not_configured", "No cloud provider is configured.")
        return ProcessingGrant(ProcessingLocation.CLOUD, case_policy_version)
    return ProcessingGrant(local_location(settings), case_policy_version)


@dataclass
class ProviderSet:
    local_generation: GenerationProvider
    embeddings: EmbeddingProvider
    cloud_generation: GenerationProvider | None = None

    def generation_for(self, grant: ProcessingGrant) -> GenerationProvider:
        if grant.location == ProcessingLocation.CLOUD:
            if self.cloud_generation is None:
                raise PolicyError("cloud_not_configured", "No cloud provider is configured.")
            return self.cloud_generation
        if self.local_generation.location != grant.location:
            raise PolicyError(
                "provider_mismatch", "The configured local provider does not match the grant."
            )
        return self.local_generation


def build_providers(
    settings: Settings,
    *,
    local_transport: httpx2.BaseTransport | None = None,
    cloud_transport: httpx2.BaseTransport | None = None,
) -> ProviderSet:
    local_generation: GenerationProvider
    embeddings: EmbeddingProvider
    if settings.ai_local_provider == "synthetic_fixture":
        local_generation = FixtureGenerationProvider()
        embeddings = FixtureEmbeddingProvider()
    else:
        local_generation = OllamaGenerationProvider(
            base_url=settings.ai_ollama_base_url,
            model=settings.ai_generation_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
            num_ctx=settings.ai_num_ctx,
            transport=local_transport,
        )
        embeddings = OllamaEmbeddingProvider(
            base_url=settings.ai_ollama_base_url,
            model=settings.ai_embedding_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
            transport=local_transport,
        )
    cloud: GenerationProvider | None = None
    if settings.ai_cloud_provider == "anthropic" and settings.ai_cloud_api_key is not None:
        cloud = AnthropicGenerationProvider(
            api_key=settings.ai_cloud_api_key.get_secret_value(),
            model=settings.ai_cloud_model,
            base_url=settings.ai_cloud_base_url,
            timeout_seconds=settings.ai_cloud_timeout_seconds,
            transport=cloud_transport,
        )
    return ProviderSet(
        local_generation=local_generation, embeddings=embeddings, cloud_generation=cloud
    )
