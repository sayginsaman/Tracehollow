"""Username discovery with Sherlock: candidate accounts on a curated set of platforms.

Compatibility check (2026-09-15, sherlock-project 0.16.2, recorded in ADR 0006):

* The command line checks GitHub for updates and downloads its site list and exclusions at
  start. Tracehollow therefore calls the library API from an isolated runner with a vendored,
  curated manifest (``data/sherlock_sites.json``) and no remote downloads.
* Sherlock reports "Available" (username not found) for *any* response of 300 or above on
  status-code sites, including 403, 429 and 5xx, and "Claimed" for any page that lacks the
  site's error text on message sites, including login walls. The adapter reinterprets each
  result from the HTTP status so that blocks, rate limits and outages are never recorded as a
  missing account and error pages are never recorded as candidates.

A hit is a *candidate account*: an account with that name appears to exist on the platform. It
may belong to anyone; nothing links it to other hits or to a person.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import time
from collections import Counter
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from app.connectors.base import (
    CollectionMode,
    ConnectorDescriptor,
    ConnectorError,
    ConnectorPage,
    EntityDraft,
    EvidenceDraft,
    FetchRequest,
    IdentifierDraft,
    ObservationDraft,
    ParameterSpec,
    RetryPolicy,
    VerificationStatus,
    parameter_value,
    validate_parameters,
)
from app.connectors.engines import process
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "username.sherlock"
_USERNAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MANIFEST_FILE = "sherlock_sites.json"


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    raw = resources.files("app.connectors.data").joinpath(_MANIFEST_FILE).read_text("utf-8")
    return dict(json.loads(raw))


_MANIFEST = load_manifest()
_SITE_CHOICES = {
    name: str(info.get("urlMain", "")) for name, info in sorted(_MANIFEST["sites"].items())
}

# Per-site classifications (not the connector outcome).
CANDIDATE = "candidate"
NOT_FOUND = "not_found"
DEFINITIVE = (CANDIDATE, NOT_FOUND)


def platform_slug(site: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", site.lower()).strip("-.")
    return slug[:100] or "site"


def classify(
    site_info: dict[str, Any],
    raw_status: str,
    http_status: int | None,
    context: str | None,
    *,
    blocked: bool = False,
) -> tuple[str, str]:
    """Reinterpret one Sherlock result truthfully. Returns (classification, explanation)."""
    if blocked:
        return "blocked", "The destination is not permitted by the network policy."
    if raw_status == "Illegal":
        return "unsupported", "The username does not match this platform's rules."
    if raw_status == "WAF":
        return "access_denied", "A bot-protection page blocked the check."
    if http_status is None:
        detail = context or "no response"
        if "timeout" in detail.lower():
            return "unavailable", "The platform did not respond in time."
        return "unavailable", f"The check failed before a response arrived ({detail})."
    if http_status == 401:
        return "authentication_required", "The platform requires signing in (HTTP 401)."
    if http_status == 403:
        return "access_denied", "The platform refused the request (HTTP 403)."
    if http_status == 429:
        return "rate_limited", "The platform rate-limited the request (HTTP 429)."
    if http_status >= 500:
        return "unavailable", f"The platform had a server error (HTTP {http_status})."

    error_types = site_info.get("errorType")
    types = [error_types] if isinstance(error_types, str) else list(error_types or [])
    error_codes = site_info.get("errorCode")
    codes = [error_codes] if isinstance(error_codes, int) else list(error_codes or [])
    success = 200 <= http_status < 300
    if raw_status == "Claimed" and success:
        return CANDIDATE, f"The profile address answered HTTP {http_status}."
    if raw_status == "Available":
        if "message" in types and success:
            return NOT_FOUND, "The platform's page says no such account exists."
        if "status_code" in types and (http_status in codes or http_status in (404, 410)):
            return NOT_FOUND, f"The platform answered HTTP {http_status} for this name."
        if "response_url" in types and 300 <= http_status < 400:
            return NOT_FOUND, "The platform redirected away from the profile address."
    return "inconclusive", f"HTTP {http_status} does not show whether the account exists."


class SherlockUsernameConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Username discovery (Sherlock)",
        synthetic=False,
        description=(
            "Checks whether a username appears to exist on selected platforms by requesting "
            "each platform's profile address with the Sherlock engine. Every selected platform "
            "sees a request. Hits are candidate accounts, not identity matches."
        ),
        supported_input_types=("username",),
        collection_mode=CollectionMode.PLATFORM_PROBE,
        credential_requirements="none",
        coverage=(
            f"{len(_SITE_CHOICES)} curated platforms from Sherlock's manifest (social networks "
            "with login walls are excluded). Detection relies on each platform's public "
            "response and can be wrong: a login wall can look like a profile, and platforms "
            "change. Blocked, rate-limited and failed checks are reported per platform."
        ),
        max_pages=1,
        max_items_per_page=len(_SITE_CHOICES),
        timeout_seconds=240,
        retry_policy=RetryPolicy(max_attempts=1, retryable_outcomes=()),
        output_schema="tracehollow.username.candidates/v1",
        cost_model="Free (requests to public profile addresses).",
        last_live_verification=None,
        verification_status=VerificationStatus.FIXTURE_TESTED,
        parameters=(
            ParameterSpec(
                name="sites",
                kind="multi_choice",
                label="Platforms",
                description="Platforms to check.",
                default=list(_MANIFEST["default_sites"]),
                choices=_SITE_CHOICES,
                maximum=len(_SITE_CHOICES),
            ),
            ParameterSpec(
                name="timeout_seconds",
                kind="integer",
                label="Per-platform timeout",
                description="Seconds to wait for each platform.",
                default=15,
                minimum=5,
                maximum=60,
            ),
        ),
        cache_policy="No caching: every execution checks the platforms again.",
        max_concurrent_runs=1,
        provider_terms=(
            "Requests individual public profile addresses. Respect each platform's terms; "
            "do not use results to harass or identify people."
        ),
        documentation="docs/connectors/username-sherlock.md",
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        if not _USERNAME.match(input_value.strip()):
            raise ValueError("a username for discovery has 1-64 letters, digits, '.', '_' or '-'")
        validate_parameters(self.descriptor, parameters)

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "username":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only usernames are supported.")
        username = request.input_value.strip()
        context = request.context
        settings = context.settings
        manifest = (
            load_manifest(settings.sherlock_manifest_path)
            if settings is not None and settings.sherlock_manifest_path
            else _MANIFEST
        )
        selected = list(parameter_value(self.descriptor, request.parameters, "sites"))
        sites = {name: manifest["sites"][name] for name in selected if name in manifest["sites"]}
        missing = sorted(set(selected) - set(sites))
        if not sites:
            raise ConnectorError(
                ConnectorOutcome.UNSUPPORTED,
                "None of the selected platforms is in the installed manifest.",
                code="unknown_sites",
            )
        timeout = int(parameter_value(self.descriptor, request.parameters, "timeout_seconds"))
        policy = context.network_policy
        config = {
            "username": username,
            "sites": sites,
            "timeout": timeout,
            "allowed_ports": sorted(policy.allowed_ports),
            "allowed_private_networks": [str(n) for n in policy.allowed_private_networks],
        }
        checked = 0

        def on_line(line: str) -> None:
            nonlocal checked
            if '"progress"' in line:
                checked += 1
                context.progress({"sites_checked": checked, "sites_selected": len(sites)})

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="tracehollow-sherlock-") as home:
            result = process.run(
                [sys.executable, "-m", "app.connectors.engines.sherlock_runner"],
                env=process.minimal_environment(Path(home), {"PYTHONPATH": str(_app_root())}),
                cwd=_app_root(),
                deadline=context.deadline,
                cancelled=context.cancelled,
                stdin=json.dumps(config).encode("utf-8"),
                on_stdout=on_line,
                max_lines=len(sites) * 4 + 100,
            )
        if result.stopped == "canceled":
            raise ConnectorError(ConnectorOutcome.CANCELED, "Canceled.", code="canceled")

        records: dict[str, dict[str, Any]] = {}
        blocked_hosts: set[str] = set()
        done = False
        for line in result.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") == "result" and item.get("site") in sites:
                records[str(item["site"])] = item
            elif item.get("type") == "blocked":
                blocked_hosts.add(str(item.get("host", "")).lower())
            elif item.get("type") == "done":
                done = True
        if not records and any("sherlock_project" in line for line in result.stderr):
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The username engine is not installed in this service; run collection in the "
                "collector service.",
                code="engine_not_installed",
            )
        if not records and (result.returncode not in (0, None) or result.stopped):
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The username engine stopped before reporting results"
                + (f" ({result.stopped})." if result.stopped else f" (exit {result.returncode})."),
                code="engine_failed" if not result.stopped else f"engine_{result.stopped}",
            )

        entries = []
        for name, info in sites.items():
            record = records.get(name)
            if record is None:
                entries.append(
                    {
                        "site": name,
                        "url_main": info.get("urlMain"),
                        "classification": "not_checked",
                        "explanation": "The engine stopped before this platform was checked.",
                    }
                )
                continue
            url = record.get("url") or ""
            host = re.sub(r"^https?://([^/:]+).*$", r"\1", url).lower() if url else ""
            classification, explanation = classify(
                info,
                str(record.get("status")),
                record.get("http_status"),
                record.get("context"),
                blocked=bool(host) and host in blocked_hosts,
            )
            entries.append(
                {
                    "site": name,
                    "url_main": info.get("urlMain"),
                    "url_user": url or None,
                    "engine_status": record.get("status"),
                    "http_status": record.get("http_status"),
                    "engine_context": record.get("context"),
                    "classification": classification,
                    "explanation": explanation,
                    "query_seconds": record.get("query_time"),
                }
            )

        counts = Counter(entry["classification"] for entry in entries)
        candidates = [entry for entry in entries if entry["classification"] == CANDIDATE]
        definitive = sum(counts[c] for c in DEFINITIVE)
        inconclusive = len(entries) - definitive
        engine_version = _engine_version()
        body = {
            "schema": self.descriptor.output_schema,
            "username": username,
            "engine": {"name": "sherlock-project", "version": engine_version},
            "manifest": {
                "upstream_version": manifest.get("upstream", {}).get("version"),
                "sha256": hashlib.sha256(
                    json.dumps(sites, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "sites_selected": len(sites),
                "unknown_sites": missing,
            },
            "note": (
                "Candidates only: an account with this name appears to exist on the platform. "
                "It may belong to anyone."
            ),
            "results": entries,
        }
        evidence = EvidenceDraft(
            key="results",
            kind="json",
            content=json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
            title=f"Username checks for {username} on {len(sites)} platform(s)"[:300],
            source_reference=f"username-probe:{username}",
            collection_metadata={
                "engine": "sherlock-project",
                "engine_version": engine_version,
                "sites_selected": len(sites),
                "sites_reported": len(records),
                "engine_finished": done,
                "engine_stopped": result.stopped,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            },
        )
        entities: list[EntityDraft] = []
        observations: list[ObservationDraft] = []
        for entry in candidates:
            slug = platform_slug(str(entry["site"]))
            key = f"account:{slug}"
            identifiers = [IdentifierDraft("username", username, slug)]
            if entry.get("url_user"):
                identifiers.insert(0, IdentifierDraft("url", str(entry["url_user"]), slug))
            entities.append(
                EntityDraft(
                    key=key,
                    entity_type="platform_account",
                    display_name=f"{username} on {entry['site']}"[:300],
                    match=identifiers[0],
                    identifiers=tuple(identifiers),
                    description=(
                        "Candidate account from username discovery. It may belong to anyone; "
                        "not an identity assertion."
                    ),
                    attributes={"candidate": True, "platform": entry["site"]},
                )
            )
            observations.append(
                ObservationDraft(
                    observation_type="candidate_account",
                    evidence_key="results",
                    entity_key=key,
                    source_object_id=str(entry.get("url_user") or slug),
                    payload={
                        "platform": entry["site"],
                        "username": username,
                        "profile_reference": entry.get("url_user"),
                        "http_status": entry.get("http_status"),
                        "explanation": entry["explanation"],
                        "candidate": True,
                    },
                    idempotency_suffix=f"candidate:{slug}",
                )
            )

        outcome_hint: ConnectorOutcome | None = None
        incomplete: str | None = None
        if definitive == 0:
            failures = Counter(
                entry["classification"]
                for entry in entries
                if entry["classification"]
                in ("rate_limited", "access_denied", "authentication_required", "unavailable")
            )
            outcome_hint = (
                ConnectorOutcome(failures.most_common(1)[0][0])
                if failures
                else ConnectorOutcome.UNAVAILABLE
            )
        elif inconclusive:
            incomplete = (
                f"{inconclusive} of {len(entries)} platform check(s) were inconclusive "
                "(blocked, rate-limited, failed or not checked); see the stored results."
            )
        return ConnectorPage(
            page_index=0,
            has_more=False,
            evidence=[evidence],
            entities=entities,
            observations=observations,
            items=len(candidates),
            coverage={
                "sites_selected": len(sites),
                "classifications": dict(counts),
            },
            incomplete_reason=incomplete,
            outcome_hint=outcome_hint,
        )


def _app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _engine_version() -> str | None:
    try:
        return metadata.version("sherlock-project")
    except metadata.PackageNotFoundError:
        return None
