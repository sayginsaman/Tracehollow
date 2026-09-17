"""Shared pieces for social platform connectors (Instagram, Telegram, YouTube).

Every request these connectors make goes through :func:`app.connectors.http.fetch`, so the
network policy (address checks, redirect limits, size and time bounds) applies to all of their
traffic. They use no platform SDKs and start no subprocesses.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from app.connectors import netguard
from app.connectors.base import (
    CapabilitySpec,
    CapabilityStatus,
    ConnectorDescriptor,
    ConnectorError,
    FetchRequest,
    ParameterSpec,
    selected_capability,
)
from app.queries.models import ConnectorOutcome

REDACTED = "[redacted]"


def capability_parameter(capabilities: tuple[CapabilitySpec, ...], default: str) -> ParameterSpec:
    """A choice parameter offering only capabilities backed by an adapter."""
    implemented = {
        spec.name: spec.label
        for spec in capabilities
        if spec.status == CapabilityStatus.IMPLEMENTED
    }
    assert default in implemented, default
    return ParameterSpec(
        name="capability",
        kind="choice",
        label="Capability",
        description=(
            "How this source is collected. Only implemented capabilities are offered; the Sources "
            "page lists the ones that are not implemented and why."
        ),
        default=default,
        choices=implemented,
    )


def capability_for(descriptor: ConnectorDescriptor, request: FetchRequest) -> CapabilitySpec:
    try:
        spec = selected_capability(descriptor, request.parameters)
    except ValueError as exc:
        raise ConnectorError(
            ConnectorOutcome.UNSUPPORTED, str(exc), code="capability_not_implemented"
        ) from None
    assert spec is not None
    return spec


def provenance(spec: CapabilitySpec) -> dict[str, Any]:
    """Recorded with every evidence record a capability produces."""
    return {
        "capability": spec.name,
        "access_method": str(spec.access_method),
        "provider": spec.provider,
        "capability_verification": str(spec.verification_status),
    }


def require_credentials(request: FetchRequest, spec: CapabilitySpec) -> dict[str, str]:
    """Credential values the capability needs, or an ``authentication_required`` outcome.

    Missing access blocks collection (and live verification) explicitly; it never turns into an
    empty or successful result.
    """
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in spec.credential_names:
        value = request.context.credential(name)
        if value:
            values[name] = value.strip()
        else:
            missing.append(name)
    if missing:
        raise ConnectorError(
            ConnectorOutcome.AUTHENTICATION_REQUIRED,
            f"The {spec.label} capability needs credentials that are not configured: "
            f"{', '.join(missing)}. An administrator can add them on the Sources page.",
            code="credential_not_configured",
        )
    return values


def redact(result: netguard.FetchResult, *secrets: str) -> netguard.FetchResult:
    """Remove secrets that had to travel in a URL (for example a bot token in the path)."""

    def clean(value: str) -> str:
        for secret in secrets:
            if secret:
                value = value.replace(secret, REDACTED)
        return value

    return dataclasses.replace(
        result,
        requested_url=clean(result.requested_url),
        final_url=clean(result.final_url),
        redirects=[{**item, "from": clean(str(item.get("from", "")))} for item in result.redirects],
    )


def json_body(result: netguard.FetchResult, what: str) -> Any:
    if result.truncated:
        raise ConnectorError(
            ConnectorOutcome.PARSE_ERROR, f"The {what} response was truncated.", code="too_large"
        )
    try:
        return json.loads(result.content)
    except ValueError:
        raise ConnectorError(
            ConnectorOutcome.PARSE_ERROR, f"The {what} response is not JSON.", code="not_json"
        ) from None


def text(value: object, limit: int = 5000) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped[:limit] if stripped else None


def integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
