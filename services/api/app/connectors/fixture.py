"""Deterministic synthetic fixture connector.

For development, demonstrations and tests only. It contacts nothing: every value is derived
from a SHA-256 of the query input, all hostnames use the reserved ``.example`` TLD, and every
payload and item is labelled synthetic. Scenarios exercise each connector outcome.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.auth.security import normalize_username
from app.connectors.base import (
    ConnectorDescriptor,
    ConnectorError,
    ConnectorPage,
    FetchRequest,
    ObservedAccount,
    RetryPolicy,
)
from app.entities.normalize import IdentifierError, normalize_domain, normalize_email
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "synthetic.fixture"
SYNTHETIC_LABEL = "SYNTHETIC FIXTURE DATA - not collected from any real source"

SCENARIOS: dict[str, str] = {
    "findings": "Three pages of synthetic candidate accounts.",
    "no_findings": "A successful lookup with no matches within the fixture's coverage.",
    "partial": "Page 1 succeeds, page 2 fails permanently: usable but incomplete data.",
    "failure": "The fixture source is unavailable on every attempt (retries exhausted).",
    "flaky": "Page 2 is unavailable on its first attempt and succeeds on retry.",
    "rate_limited": "The first attempt is rate limited with retry information, then succeeds.",
    "authentication_required": "The fixture source demands credentials that are not configured.",
    "access_denied": "The fixture source refuses access.",
    "parse_error": "Page 1 returns data that does not match the expected schema.",
    "slow": "Ten slow pages, useful for demonstrating progress and cancellation.",
}

_SUPPORTED_INPUTS = ("username", "domain", "email")
_PLATFORMS = ("synthetic-social.example", "synthetic-forum.example", "synthetic-code.example")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class FixtureConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Synthetic fixture (not a real source)",
        synthetic=True,
        description=(
            "Deterministic synthetic results for development, demonstrations and tests. "
            "Contacts no network service and implies no live integration."
        ),
        supported_input_types=_SUPPORTED_INPUTS,
        collection_mode="synthetic_fixture",
        credential_requirements="none",
        coverage="Synthetic data only. Values are generated from the query input.",
        max_pages=10,
        max_items_per_page=5,
        timeout_seconds=300,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE, ConnectorOutcome.RATE_LIMITED),
        ),
        output_schema="tracehollow.fixture.accounts/v1",
        cost_model=None,
        last_live_verification=None,
        parameters={"scenario": SCENARIOS},
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        scenario = parameters.get("scenario", "findings")
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown fixture scenario '{scenario}'")
        unknown = set(parameters) - {"scenario"}
        if unknown:
            raise ValueError(f"unsupported parameters: {', '.join(sorted(unknown))}")

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type not in _SUPPORTED_INPUTS:
            raise ConnectorError(
                ConnectorOutcome.UNSUPPORTED,
                f"The fixture connector does not support input type '{request.input_type}'.",
            )
        scenario = str(request.parameters.get("scenario", "findings"))
        page = request.page_index

        match scenario:
            case "authentication_required":
                raise ConnectorError(
                    ConnectorOutcome.AUTHENTICATION_REQUIRED,
                    "Synthetic source requires credentials that are not configured.",
                )
            case "access_denied":
                raise ConnectorError(
                    ConnectorOutcome.ACCESS_DENIED, "Synthetic source refused access."
                )
            case "failure":
                raise ConnectorError(
                    ConnectorOutcome.UNAVAILABLE, "Synthetic source is unavailable."
                )
            case "parse_error":
                raise ConnectorError(
                    ConnectorOutcome.PARSE_ERROR,
                    "Synthetic response did not match schema tracehollow.fixture.accounts/v1.",
                )
            case "rate_limited" if page == 0 and request.attempt == 1:
                raise ConnectorError(
                    ConnectorOutcome.RATE_LIMITED,
                    "Synthetic rate limit reached.",
                    retry_after_seconds=1.0,
                )
            case "flaky" if page == 1 and request.attempt == 1:
                raise ConnectorError(
                    ConnectorOutcome.UNAVAILABLE, "Synthetic transient failure on page 2."
                )
            case "partial" if page == 1:
                raise ConnectorError(
                    ConnectorOutcome.UNAVAILABLE,
                    "Synthetic source stopped responding after the first page.",
                )

        if scenario == "no_findings":
            total_pages, per_page = 1, 0
        elif scenario == "slow":
            total_pages, per_page = 10, 1
        elif scenario == "partial":
            total_pages, per_page = 3, 2
        else:
            total_pages, per_page = 3, 2
        per_page = min(per_page, request.max_items_per_page)

        items = [self._item(request, page, index) for index in range(per_page)]
        return ConnectorPage(
            page_index=page,
            items=items,
            has_more=page + 1 < total_pages,
            raw_payload={
                "synthetic": True,
                "label": SYNTHETIC_LABEL,
                "connector": {"id": CONNECTOR_ID, "version": self.descriptor.version},
                "schema": self.descriptor.output_schema,
                "query": {"input_type": request.input_type, "input_value": request.input_value},
                "scenario": scenario,
                "page": {
                    "index": page,
                    "total_pages": total_pages,
                    "has_more": page + 1 < total_pages,
                },
                "items": [
                    {
                        "id": item.source_object_id,
                        "platform": item.platform,
                        "username": item.username,
                        "display_name": item.display_name,
                        "profile": item.profile_reference,
                        "linked_domain": item.linked_domain,
                        "event_time": item.event_time,
                    }
                    for item in items
                ],
            },
        )

    def _item(self, request: FetchRequest, page: int, index: int) -> ObservedAccount:
        key = _normalized_input(request.input_type, request.input_value)
        digest = _digest(CONNECTOR_ID, request.input_type, key, str(page), str(index))
        platform = _PLATFORMS[int(digest[:2], 16) % len(_PLATFORMS)]
        base = key.split("@")[0].split(".")[0] or "subject"
        username = f"{base}_{digest[:4]}"
        return ObservedAccount(
            source_object_id=f"fixture-{digest[:16]}",
            platform=platform,
            username=username,
            display_name=f"Synthetic account {digest[:6]}",
            profile_reference=f"https://{platform}/u/{username}",
            linked_domain=f"{digest[6:14]}.example",
            event_time=(
                f"2026-0{1 + int(digest[14], 16) % 9}-1{int(digest[15], 16) % 9}T12:00:00+03:00"
            ),
        )


def _normalized_input(input_type: str, value: str) -> str:
    try:
        if input_type == "domain":
            return normalize_domain(value)
        if input_type == "email":
            return normalize_email(value)
    except IdentifierError:
        return normalize_username(value)
    return normalize_username(value)
