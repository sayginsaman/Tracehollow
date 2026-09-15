"""Passive subdomain discovery with Subfinder (ProjectDiscovery, MIT), run as a subprocess.

Compatibility check (2026-09-15, subfinder v2.16.0 linux/arm64, recorded in ADR 0006):

* Subfinder exits 0 with no output when every source fails, which looks exactly like "no
  subdomains". The adapter therefore runs with ``-v`` and reads the per-source messages
  ("Selected source(s)", "Encountered an error with source", "Cannot use the ... source")
  to tell verified empty results from failures and from sources skipped for missing keys.
* The update check is disabled (``-duc``); configuration and provider keys are written to a
  private temporary directory for the run and removed afterwards.
* Passive only: no ``-active``/``-nW`` resolution and no requests to the domain itself.
  Results outside the requested domain are discarded and counted.
"""

from __future__ import annotations

import ipaddress
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from app.connectors.base import (
    CollectionMode,
    ConnectorDescriptor,
    ConnectorError,
    ConnectorPage,
    CredentialSpec,
    EntityDraft,
    EvidenceDraft,
    FetchRequest,
    IdentifierDraft,
    ObservationDraft,
    ParameterSpec,
    RelationshipDraft,
    RetryPolicy,
    VerificationStatus,
    parameter_value,
    validate_parameters,
)
from app.connectors.engines import process
from app.entities import normalize
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "domain.subfinder"
ENGINE_VERSION = "v2.16.0"

# Reviewed passive sources. Key-based sources run only when an administrator stored a key.
SOURCES: dict[str, dict[str, Any]] = {
    "crtsh": {"label": "crt.sh certificate transparency search", "key": None},
    "digitorus": {"label": "Digitorus certificate transparency", "key": None},
    "anubis": {"label": "Anubis subdomain database (jldc.me)", "key": None},
    "hackertarget": {"label": "HackerTarget host search (free tier is limited)", "key": None},
    "rapiddns": {"label": "RapidDNS", "key": None},
    "certspotter": {"label": "Cert Spotter (SSLMate) - API key", "key": "certspotter"},
    "virustotal": {"label": "VirusTotal - API key", "key": "virustotal"},
    "alienvault": {"label": "AlienVault OTX passive DNS - API key", "key": "alienvault"},
    "securitytrails": {"label": "SecurityTrails - API key", "key": "securitytrails"},
}
DEFAULT_SOURCES = ["crtsh", "digitorus"]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SELECTED = re.compile(r"Selected source\(s\) for this search: (.+)$")
_ERROR = re.compile(r"Encountered an error with source (\w+): (.*)$")
_SKIPPED = re.compile(r"Cannot use the (\w+) source because there was no (?:API )?key")
_URL = re.compile(r"https?://[^\s\"']+")
_RATE = re.compile(r"\b429\b|rate limit|quota exhausted|too many requests", re.I)


def _redact(message: str, secrets: list[str]) -> str:
    """Strip query strings (API keys travel in some source URLs) and known key values."""

    def strip_query(match: re.Match[str]) -> str:
        return match.group(0).split("?", 1)[0]

    cleaned = _URL.sub(strip_query, message)
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned[:300]


def parse_stderr(lines: list[str]) -> tuple[list[str], dict[str, str], list[str], str | None]:
    """Return (selected sources, errors by source, sources skipped for missing keys, fatal)."""
    selected: list[str] = []
    errors: dict[str, str] = {}
    skipped: list[str] = []
    fatal: str | None = None
    for raw in lines:
        line = _ANSI.sub("", raw).strip()
        if (match := _SELECTED.search(line)) is not None:
            selected = [item.strip() for item in match.group(1).split(",") if item.strip()]
        elif (match := _ERROR.search(line)) is not None:
            errors.setdefault(match.group(1), match.group(2))
        elif (match := _SKIPPED.search(line)) is not None:
            if match.group(1) not in skipped:
                skipped.append(match.group(1))
        elif line.startswith("[FTL]") and fatal is None:
            fatal = line[5:].strip()
    return selected, errors, skipped, fatal


def in_scope(host: str, domain: str) -> str | None:
    """Normalize a result host; return None when it is not the domain or below it."""
    candidate = host.strip().lower().rstrip(".")
    if candidate.startswith("*."):
        candidate = candidate[2:]
    try:
        normalized = normalize.normalize_domain(candidate)
    except normalize.IdentifierError:
        return None
    if normalized == domain or normalized.endswith("." + domain):
        return normalized
    return None


class SubfinderDomainConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Passive subdomain discovery (Subfinder)",
        synthetic=False,
        description=(
            "Lists subdomains of a domain from passive third-party datasets such as "
            "certificate transparency logs, using ProjectDiscovery Subfinder. The domain's own "
            "servers are not contacted and names are not resolved."
        ),
        supported_input_types=("domain",),
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_requirements=(
            "None for the default certificate transparency sources; optional API keys enable "
            "Cert Spotter, VirusTotal, AlienVault OTX and SecurityTrails."
        ),
        coverage=(
            "Only names that the selected passive sources have recorded (for example in issued "
            "certificates). Absence from these datasets does not mean a subdomain does not "
            "exist; results may include names that no longer resolve."
        ),
        max_pages=1,
        max_items_per_page=5000,
        timeout_seconds=300,
        retry_policy=RetryPolicy(
            max_attempts=2,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE,),
            base_backoff_seconds=10.0,
            max_backoff_seconds=30.0,
        ),
        output_schema="tracehollow.domain.subdomains/v1",
        cost_model="Free for keyless sources; key-based sources follow each provider's plan.",
        quota_notes="Each selected source applies its own limits; Subfinder paces requests.",
        last_live_verification=None,
        verification_status=VerificationStatus.FIXTURE_TESTED,
        parameters=(
            ParameterSpec(
                name="sources",
                kind="multi_choice",
                label="Passive sources",
                description="Datasets to query about the domain.",
                default=DEFAULT_SOURCES,
                choices={name: str(info["label"]) for name, info in SOURCES.items()},
                maximum=len(SOURCES),
            ),
            ParameterSpec(
                name="max_results",
                kind="integer",
                label="Maximum subdomains",
                description="Stop after this many in-scope names.",
                default=500,
                minimum=1,
                maximum=5000,
            ),
        ),
        credentials=tuple(
            CredentialSpec(
                name=str(info["key"]),
                label=f"{info['label'].split(' - ')[0]} API key",
                description=f"Used only by Subfinder's {name} source.",
                required=False,
            )
            for name, info in SOURCES.items()
            if info["key"]
        ),
        cache_policy="No caching: every execution queries the selected sources again.",
        max_concurrent_runs=1,
        provider_terms=(
            "Each source has its own terms (crt.sh, Digitorus, Anubis, HackerTarget, RapidDNS "
            "and key-based providers). Passive lookups only."
        ),
        documentation="docs/connectors/domain-subfinder.md",
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        try:
            normalize.normalize_domain(input_value)
        except normalize.IdentifierError as exc:
            raise ValueError("the input must be a domain name such as example.org") from exc
        if input_value.strip().startswith("*"):
            raise ValueError("wildcards are not accepted; enter the domain itself")
        try:
            ipaddress.ip_address(input_value.strip())
        except ValueError:
            pass
        else:
            raise ValueError("enter a domain name, not an IP address")
        validate_parameters(self.descriptor, parameters)

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "domain":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only domains are supported.")
        domain = normalize.normalize_domain(request.input_value)
        context = request.context
        settings = context.settings
        binary = Path(settings.subfinder_path) if settings is not None else Path("subfinder")
        if not binary.is_file():
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The Subfinder engine is not installed in this service; run collection in the "
                "collector service.",
                code="engine_not_installed",
            )
        sources = list(parameter_value(self.descriptor, request.parameters, "sources"))
        max_results = int(parameter_value(self.descriptor, request.parameters, "max_results"))
        keys: dict[str, str] = {}
        # Every configured key is redacted from messages, not only those of selected sources.
        secrets: list[str] = []
        for source, info in SOURCES.items():
            if not info["key"]:
                continue
            value = context.credential(str(info["key"]))
            if value:
                secrets.append(value)
                if source in sources:
                    keys[source] = value

        hosts: dict[str, set[str]] = {}
        discarded = 0

        def on_line(line: str) -> None:
            nonlocal discarded
            try:
                item = json.loads(line)
            except ValueError:
                return
            if not isinstance(item, dict) or not isinstance(item.get("host"), str):
                return
            name = in_scope(item["host"], domain)
            if name is None:
                discarded += 1
                return
            if name not in hosts and len(hosts) >= max_results:
                return
            found = item.get("sources") or ([item["source"]] if item.get("source") else [])
            hosts.setdefault(name, set()).update(str(s) for s in found if isinstance(s, str))
            context.progress({"subdomains_found": len(hosts)})

        started = time.monotonic()
        max_minutes = max(1, int(context.remaining_seconds() // 60))
        with tempfile.TemporaryDirectory(prefix="tracehollow-subfinder-") as home:
            base = Path(home)
            config = base / "config.yaml"
            provider_config = base / "provider-config.yaml"
            config.write_text("", encoding="utf-8")
            provider_config.write_text(
                "".join(f"{source}:\n  - {json.dumps(value)}\n" for source, value in keys.items())
                or "{}\n",
                encoding="utf-8",
            )
            provider_config.chmod(0o600)
            result = process.run(
                [
                    str(binary),
                    "-d",
                    domain,
                    "-s",
                    ",".join(sources),
                    "-oJ",
                    "-cs",
                    "-duc",
                    "-nc",
                    "-v",
                    "-timeout",
                    str(int(min(30, context.request_timeout_seconds))),
                    "-max-time",
                    str(max_minutes),
                    "-rsr",
                    str(context.max_response_bytes),
                    "-config",
                    str(config),
                    "-pc",
                    str(provider_config),
                ],
                env=process.minimal_environment(base),
                cwd=base,
                deadline=context.deadline,
                cancelled=context.cancelled,
                on_stdout=on_line,
                stop_when=lambda: len(hosts) >= max_results,
            )
        if result.stopped == "canceled":
            raise ConnectorError(ConnectorOutcome.CANCELED, "Canceled.", code="canceled")

        selected, errors, skipped, fatal = parse_stderr(result.stderr)
        selected = selected or sources
        errors = {source: _redact(message, secrets) for source, message in errors.items()}
        if fatal is not None or (result.returncode not in (0, None) and not result.stopped):
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "Subfinder failed: " + _redact(fatal or f"exit code {result.returncode}", secrets),
                code="engine_failed",
            )
        succeeded = [s for s in selected if s not in errors and s not in skipped]
        truncated = result.stopped == "output_limit" or len(hosts) >= max_results
        timed_out = result.stopped == "timeout"

        body = {
            "schema": self.descriptor.output_schema,
            "domain": domain,
            "engine": {"name": "subfinder", "version": ENGINE_VERSION},
            "mode": "passive",
            "sources": {
                "selected": selected,
                "answered": succeeded,
                "skipped_missing_key": skipped,
                "errors": errors,
            },
            "limits": {"max_results": max_results, "reached": truncated, "timed_out": timed_out},
            "out_of_scope_discarded": discarded,
            "results": [
                {"host": host, "sources": sorted(found)} for host, found in sorted(hosts.items())
            ],
        }
        evidence = EvidenceDraft(
            key="results",
            kind="json",
            content=json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
            title=f"Passive subdomains of {domain} ({len(hosts)} found)"[:300],
            source_reference=f"passive-dns:{domain}",
            collection_metadata={
                "engine": "subfinder",
                "engine_version": ENGINE_VERSION,
                "sources_selected": selected,
                "sources_with_errors": sorted(errors),
                "sources_skipped_missing_key": skipped,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "engine_stopped": result.stopped,
            },
            access_category="credentialed" if keys else "public",
        )

        entities = [
            EntityDraft(
                key="input",
                entity_type="domain",
                display_name=domain,
                match=IdentifierDraft("domain", domain),
                description="Domain queried for passive subdomain discovery.",
            )
        ]
        observations: list[ObservationDraft] = []
        relationships: list[RelationshipDraft] = []
        for host, found in sorted(hosts.items()):
            if host == domain:
                continue
            key = f"host:{host}"
            entities.append(
                EntityDraft(
                    key=key,
                    entity_type="domain",
                    display_name=host,
                    match=IdentifierDraft("domain", host),
                    description="Subdomain recorded by a passive source.",
                )
            )
            observations.append(
                ObservationDraft(
                    observation_type="subdomain",
                    evidence_key="results",
                    entity_key=key,
                    source_object_id=host,
                    payload={"host": host, "domain": domain, "sources": sorted(found)},
                    idempotency_suffix=f"host:{host}",
                )
            )
            relationships.append(
                RelationshipDraft(
                    source_key=key,
                    target_key="input",
                    predicate="subdomain_of",
                    origin="deterministic_derivation",
                    description="The name is below the queried domain.",
                    observation_index=len(observations) - 1,
                )
            )

        problems: list[str] = []
        if errors:
            problems.append(f"source(s) with errors: {', '.join(sorted(errors))}")
        if skipped:
            problems.append(f"source(s) skipped for a missing API key: {', '.join(skipped)}")
        if truncated:
            problems.append(f"stopped at the limit of {max_results} subdomains")
        if timed_out:
            problems.append("the time limit was reached")

        outcome_hint: ConnectorOutcome | None = None
        incomplete: str | None = None
        if not succeeded and not hosts:
            if skipped and not errors:
                outcome_hint = ConnectorOutcome.AUTHENTICATION_REQUIRED
            elif errors and all(_RATE.search(message) for message in errors.values()):
                outcome_hint = ConnectorOutcome.RATE_LIMITED
            else:
                outcome_hint = ConnectorOutcome.UNAVAILABLE
        elif problems:
            incomplete = "Passive discovery was incomplete: " + "; ".join(problems) + "."
        notes = []
        if discarded:
            notes.append(f"{discarded} result(s) outside {domain} were discarded.")
        return ConnectorPage(
            page_index=0,
            has_more=False,
            evidence=[evidence],
            entities=entities,
            observations=observations,
            relationships=relationships,
            items=len(observations),
            coverage={
                "sources_selected": selected,
                "sources_answered": succeeded,
                "sources_with_errors": sorted(errors),
                "sources_skipped_missing_key": skipped,
                "out_of_scope_discarded": discarded,
            },
            incomplete_reason=incomplete,
            outcome_hint=outcome_hint,
            notes=notes,
        )
