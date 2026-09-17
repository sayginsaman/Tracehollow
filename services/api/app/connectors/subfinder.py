"""Passive subdomain discovery with Subfinder (ProjectDiscovery, MIT) in the network sandbox.

Subfinder is not run by the collector. The collector sends a validated job to the
``discovery-runner`` service (``app.connectors.engines.subfinder_runner``), which runs the binary
in a container attached only to the internal ``discovery`` network; the egress gateway
(``app.egress.gateway``) is its only way out and admits only the provider host names of the
selected sources, after the collection address policy. See ADR 0007.

Compatibility checks (subfinder v2.16.0, recorded in ADR 0006 and ADR 0007):

* Subfinder exits 0 with no output when every source fails, which looks exactly like "no
  subdomains". The adapter therefore runs with ``-v -stats`` and reads the per-source messages
  ("Selected source(s)", "Encountered an error with source", "Cannot use the ... source") and
  the final statistics table (results, requests, errors per source): a source counts as
  answered only when it returned results or completed a request without an error.
* crt.sh is first queried through a direct PostgreSQL connection that the sandbox cannot route;
  that expected failure is recorded as a note, and the HTTPS API result decides the source.
* Gateway refusals appear as ``Tracehollow egress refused <code>`` in source errors.
* The update check is disabled (``-duc``); configuration and provider keys are written to a
  private temporary directory in the runner and removed afterwards.
* Passive only: no ``-active``/``-nW`` resolution and no requests to the domain itself.
  Results outside the requested domain are discarded and counted.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2

from app.connectors.base import (
    CollectionMode,
    ConnectorDescriptor,
    ConnectorError,
    ConnectorPage,
    CredentialSpec,
    EntityDraft,
    EvidenceDraft,
    FetchContext,
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
from app.egress.providers import SUBFINDER_SOURCES
from app.entities import normalize
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "domain.subfinder"
ENGINE_VERSION = "v2.16.0"

# Reviewed passive sources. Key-based sources run only when an administrator stored a key.
SOURCES = SUBFINDER_SOURCES
DEFAULT_SOURCES = ["crtsh", "digitorus"]
MAX_STDERR_LINES = 20_000

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SELECTED = re.compile(r"Selected source\(s\) for this search: (.+)$")
_ERROR = re.compile(r"Encountered an error with source (\w+): (.*)$")
_SKIPPED = re.compile(r"Cannot use the (\w+) source because there was no (?:API )?key")
_URL = re.compile(r"https?://[^\s\"']+")
_RATE = re.compile(r"\b429\b|rate limit|quota exhausted|too many requests", re.I)
_EGRESS = re.compile(r"Tracehollow egress (?:refused|failed) ([a-z_]+)")
# crt.sh's PostgreSQL path (never an https:// URL); the sandbox has no route or DNS for it.
_CRTSH_DATABASE = re.compile(r"lookup crt\.sh\b|crt\.sh:5432|:5432\b|^pq: ", re.I)
# ``-stats`` table row: source, duration, results, requests, errors.
_STATS_ROW = re.compile(r"^\s*([a-z0-9]+)\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)\s*$")
_POLICY_REFUSALS = {"host_not_allowed", "blocked_address", "blocked_port", "ip_literal_not_allowed"}


def _redact(message: str, secrets: list[str]) -> str:
    """Strip query strings (API keys travel in some source URLs) and known key values."""

    def strip_query(match: re.Match[str]) -> str:
        return match.group(0).split("?", 1)[0]

    cleaned = _URL.sub(strip_query, message)
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned[:300]


def parse_stderr(
    lines: list[str],
) -> tuple[list[str], dict[str, list[str]], list[str], str | None]:
    """Return (selected sources, error messages by source, sources skipped for keys, fatal)."""
    selected: list[str] = []
    errors: dict[str, list[str]] = {}
    skipped: list[str] = []
    fatal: str | None = None
    for raw in lines:
        line = _ANSI.sub("", raw).strip()
        if (match := _SELECTED.search(line)) is not None:
            selected = [item.strip() for item in match.group(1).split(",") if item.strip()]
        elif (match := _ERROR.search(line)) is not None:
            errors.setdefault(match.group(1), []).append(match.group(2))
        elif (match := _SKIPPED.search(line)) is not None:
            if match.group(1) not in skipped:
                skipped.append(match.group(1))
        elif line.startswith("[FTL]") and fatal is None:
            fatal = line[5:].strip()
    return selected, errors, skipped, fatal


def parse_statistics(lines: list[str]) -> dict[str, tuple[int, int, int]]:
    """Per-source (results, requests, errors) from the ``-stats`` table printed at the end."""
    stats: dict[str, tuple[int, int, int]] = {}
    in_table = False
    for raw in lines:
        line = _ANSI.sub("", raw)
        if "Source" in line and "Requests" in line and "Errors" in line:
            in_table = True
            continue
        if in_table and (match := _STATS_ROW.match(line)) is not None:
            stats[match.group(1)] = (
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
            )
    return stats


def is_crtsh_database_path(source: str, message: str) -> bool:
    return source == "crtsh" and "https://" not in message and bool(_CRTSH_DATABASE.search(message))


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


@dataclass
class RunnerOutput:
    returncode: int | None = None
    stopped: str | None = None
    stderr: list[str] = field(default_factory=list)


SOURCE_REQUEST_ESTIMATE = 5


class SubfinderDomainConnector:
    def __init__(self, runner_transport: httpx2.BaseTransport | None = None) -> None:
        # Tests inject a transport; production uses the configured discovery runner URL.
        self.runner_transport = runner_transport

    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Passive subdomain discovery (Subfinder)",
        synthetic=False,
        description=(
            "Lists subdomains of a domain from passive third-party datasets such as "
            "certificate transparency logs, using ProjectDiscovery Subfinder in a network "
            "sandbox that can reach only the selected providers. The domain's own servers are "
            "not contacted and names are not resolved."
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

    def request_estimate(self, parameters: dict[str, Any]) -> int:
        """Requests one run issues, for budgets. Subfinder queries its providers from the network
        sandbox, where Tracehollow cannot count individual requests; each selected source is
        estimated at SOURCE_REQUEST_ESTIMATE requests (providers that paginate may use more)."""
        return SOURCE_REQUEST_ESTIMATE * len(
            list(parameter_value(self.descriptor, parameters, "sources"))
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
        runner_url = settings.discovery_runner_url if settings is not None else ""
        if not runner_url:
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "Passive domain discovery runs only in the network sandbox (discovery-runner), "
                "which is not configured.",
                code="egress_sandbox_not_configured",
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
        remaining = context.remaining_seconds()
        job = {
            "domain": domain,
            "sources": sources,
            "provider_keys": keys,
            "request_timeout_seconds": int(max(1, min(30, context.request_timeout_seconds))),
            "max_time_minutes": max(1, min(10, int(remaining // 60))),
            "max_response_bytes": int(context.max_response_bytes),
            "deadline_seconds": int(max(1, min(900, remaining))),
            "max_results": max_results,
        }
        result = self._run_in_sandbox(runner_url, job, context, on_line, secrets)
        if result.stopped == "canceled":
            raise ConnectorError(ConnectorOutcome.CANCELED, "Canceled.", code="canceled")

        selected, raw_errors, skipped, fatal = parse_stderr(result.stderr)
        selected = selected or sources
        if fatal is not None or (result.returncode not in (0, None) and not result.stopped):
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "Subfinder failed: " + _redact(fatal or f"exit code {result.returncode}", secrets),
                code="engine_failed",
            )
        errors: dict[str, str] = {}
        egress: dict[str, str] = {}
        database_path_failed = False
        for source, messages in raw_errors.items():
            relevant = [m for m in messages if not is_crtsh_database_path(source, m)]
            database_path_failed = database_path_failed or len(relevant) < len(messages)
            if not relevant:
                continue
            errors[source] = _redact(relevant[0], secrets)
            for message in relevant:
                if (match := _EGRESS.search(message)) is not None:
                    egress[source] = match.group(1)
                    break
        # A source counts as answered only with positive evidence: results, or a completed
        # request in Subfinder's statistics. An empty, error-free silence is not "no findings".
        statistics = parse_statistics(result.stderr)
        produced = {source for found in hosts.values() for source in found}
        succeeded: list[str] = []
        unconfirmed: list[str] = []
        for source in selected:
            if source in errors or source in skipped:
                continue
            requests = statistics.get(source, (0, 0, 0))[1]
            (succeeded if source in produced or requests > 0 else unconfirmed).append(source)
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
                "refused_by_egress_gateway": egress,
                "no_completed_request": unconfirmed,
            },
            "egress": {
                "sandbox": "discovery-runner (internal network, no direct route)",
                "gateway": "discovery-gateway (provider allowlist, address policy, TLS verified)",
                "crtsh_database_path_unavailable": database_path_failed,
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
                "network_sandbox": "discovery-runner",
                "egress_refusals": egress,
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
        if unconfirmed:
            problems.append(f"source(s) without a completed request: {', '.join(unconfirmed)}")
        if truncated:
            problems.append(f"stopped at the limit of {max_results} subdomains")
        if timed_out:
            problems.append("the time limit was reached")

        outcome_hint: ConnectorOutcome | None = None
        outcome_code: str | None = None
        incomplete: str | None = None
        if not succeeded and not hosts:
            if skipped and not errors and not unconfirmed:
                outcome_hint, outcome_code = (
                    ConnectorOutcome.AUTHENTICATION_REQUIRED,
                    "api_key_missing",
                )
            elif (
                errors
                and not unconfirmed
                and set(egress) == set(errors)
                and set(egress.values()) <= _POLICY_REFUSALS
            ):
                outcome_hint, outcome_code = (
                    ConnectorOutcome.UNSUPPORTED,
                    "provider_destination_refused",
                )
            elif (
                errors
                and not unconfirmed
                and all(_RATE.search(message) for message in errors.values())
            ):
                outcome_hint, outcome_code = ConnectorOutcome.RATE_LIMITED, "provider_rate_limited"
            else:
                outcome_hint = ConnectorOutcome.UNAVAILABLE
                codes = sorted(set(egress.values()))
                if len(codes) == 1 and set(egress) == set(errors):
                    outcome_code = codes[0]
                elif unconfirmed and not errors:
                    outcome_code = "sources_not_completed"
                else:
                    outcome_code = "all_sources_failed"
        elif problems:
            incomplete = "Passive discovery was incomplete: " + "; ".join(problems) + "."
        notes = []
        if discarded:
            notes.append(f"{discarded} result(s) outside {domain} were discarded.")
        if egress:
            notes.append(
                "The egress gateway refused or could not complete provider connections for: "
                + ", ".join(f"{source} ({code})" for source, code in sorted(egress.items()))
                + "."
            )
        if database_path_failed:
            notes.append(
                "crt.sh's direct database connection is not available in the network sandbox; "
                "its HTTPS API was used."
            )
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
            outcome_code=outcome_code,
            notes=notes,
        )

    def _run_in_sandbox(
        self,
        runner_url: str,
        job: dict[str, Any],
        context: FetchContext,
        on_line: Any,
        secrets: list[str],
    ) -> RunnerOutput:
        output = RunnerOutput()
        finished = False
        timeout = httpx2.Timeout(10.0, read=15.0)  # the runner sends a heartbeat every second
        try:
            with (
                httpx2.Client(
                    base_url=runner_url,
                    timeout=timeout,
                    trust_env=False,
                    transport=self.runner_transport,
                ) as client,
                client.stream("POST", "/v1/subfinder", json=job) as response,
            ):
                if response.status_code != 200:
                    raise _runner_refusal(response, secrets)
                for raw in response.iter_lines():
                    if context.cancelled():
                        output.stopped = "canceled"
                        return output
                    if time.monotonic() > context.deadline:
                        output.stopped = "timeout"
                        return output
                    try:
                        item = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if isinstance(item.get("stdout"), str):
                        on_line(item["stdout"])
                    elif isinstance(item.get("stderr"), str):
                        if len(output.stderr) < MAX_STDERR_LINES:
                            output.stderr.append(item["stderr"])
                    elif "exit" in item:
                        code = item.get("exit")
                        output.returncode = code if isinstance(code, int) else None
                        stopped = item.get("stopped")
                        output.stopped = stopped if isinstance(stopped, str) else None
                        finished = True
        except httpx2.TransportError as exc:
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The passive discovery sandbox (discovery-runner) could not be reached.",
                code="discovery_runner_unavailable",
            ) from exc
        if not finished:
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The passive discovery runner stopped without reporting a result.",
                code="discovery_runner_failed",
            )
        return output


def _runner_refusal(response: httpx2.Response, secrets: list[str]) -> ConnectorError:
    try:
        payload = json.loads(response.read()[:65536] or b"{}")
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if error == "sandbox_unavailable":
        problems = "; ".join(str(p) for p in payload.get("problems", []))[:400]
        return ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            "The network sandbox for passive discovery is not in place, so Subfinder was not "
            f"started: {problems}",
            code="egress_sandbox_unavailable",
        )
    if error == "engine_not_installed":
        return ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            "The Subfinder engine is not installed in the discovery runner.",
            code="engine_not_installed",
        )
    if error == "runner_busy":
        return ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            "The discovery runner is busy; try again later.",
            code="discovery_runner_busy",
        )
    if error == "invalid_job":
        detail = _redact(str(payload.get("detail", "")), secrets)
        return ConnectorError(
            ConnectorOutcome.UNSUPPORTED,
            f"The discovery runner rejected the job: {detail}",
            code="invalid_input",
        )
    return ConnectorError(
        ConnectorOutcome.UNAVAILABLE,
        f"The discovery runner answered HTTP {response.status_code}.",
        code="discovery_runner_failed",
    )
