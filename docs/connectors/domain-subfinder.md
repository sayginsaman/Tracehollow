# Passive subdomain discovery with Subfinder (`domain.subfinder` 1.0.0)

Lists subdomains of a domain from passive third-party datasets using ProjectDiscovery Subfinder.
The domain's own servers are not contacted and names are not resolved. Subfinder runs only in a
network sandbox whose single way out is an egress gateway admitting the selected providers
([ADR 0007](../adr/0007-subfinder-network-sandbox.md)).

| | |
| --- | --- |
| Mode | Third-party lookup: the selected sources learn which domain was looked up |
| Input | Domain name (IDNA accepted; no wildcards, no IP addresses) |
| Parameters | `sources` (default `crtsh`, `digitorus`); `max_results` (1-5000, default 500) |
| Sources and provider hosts | keyless: `crtsh` (crt.sh), `digitorus` (certificatedetails.com), `anubis` (anubisdb.com), `hackertarget` (api.hackertarget.com, limited free tier), `rapiddns` (rapiddns.io); key-based: `certspotter` (api.certspotter.com), `virustotal` (www.virustotal.com), `alienvault` (otx.alienvault.com), `securitytrails` (api.securitytrails.com) |
| Credentials | optional API keys per key-based source (Sources screen) |
| Engine | Subfinder v2.16.0 (MIT) binary in the `discovery-runner` image only (not in the collector), SHA-256 verified against the release checksums at build time |
| Network | `discovery-runner` on the internal, gateway-isolated `discovery` network; provider traffic only through `discovery-gateway` (allowlisted host names, port 443, collection address policy, provider certificates verified) |
| Limits | 1 page, 300 s run timeout, 1 concurrent run, `max_results` in-scope names |
| Retries | 2 attempts for `unavailable` |
| Cache | none |
| Output schema | `tracehollow.domain.subdomains/v1` |
| Live verification | live check failed 2026-09-15 (partial: crt.sh answered, Digitorus failed); remains fixture-tested — see [live-smoke.md](live-smoke.md) |

## How it runs

1. The collector validates the query and sends a job (domain, sources, limits and the keys of
   selected key-based sources) to `discovery-runner` over the internal network. The collector
   has no Subfinder binary and never runs it itself.
2. Before every job the runner checks the sandbox: no default route, one interface, the Docker
   host not reachable on the network, gateway healthy. If a check fails it refuses the job; the
   run is `unavailable` (`egress_sandbox_unavailable`).
3. The runner starts `subfinder -d <domain> -s <sources> -oJ -cs -duc -nc -v -stats -timeout <s>
   -max-time <min> -rsr <bytes> -config <tmp> -pc <tmp> -proxy http://discovery-gateway:3128` as an
   argument list in a private temporary directory with a minimal environment. `-duc` disables
   the update check. There is no `-active`/`-nW`, so names are never resolved. Keys are written
   to a temporary provider configuration only for selected sources and deleted after the run.
4. Results stream back line by line with a heartbeat every second. Cancelling the run closes
   the stream and the runner stops the process.
5. The gateway admits `CONNECT` only to the provider host names above on port 443, after
   checking every resolved address against the collection policy. It verifies the provider's
   certificate: Subfinder itself does not.

Compatibility checks (Subfinder v2.16.0):

- Subfinder exits successfully with no output when every source fails. The adapter reads the
  verbose messages ("Selected source(s)", "Encountered an error with source", "Cannot use the …
  source because there was no API key") and the `-stats` table. A source counts as answered only
  when it returned names, or completed a request without an error.
- crt.sh is queried first through a direct PostgreSQL connection, which ignores the proxy and
  cannot route or resolve in the sandbox, then through its HTTPS API. The expected database-path
  error is recorded as a note, not a source failure.

## Outcomes

| Situation | Outcome |
| --- | --- |
| Names found, every selected source answered | `findings` |
| No names, every selected source answered | `no_findings` |
| Names found, some sources failed or were skipped, the result or time limit was reached | `partial` |
| No names and no source answered: all skipped for missing keys | `authentication_required` |
| No names and no source answered: all errors were rate limits or quota | `rate_limited` |
| No names and no source answered otherwise | `unavailable` |
| No names; every failing source was refused by the egress gateway (provider resolving to a blocked address, non-provider host, other port) | `unsupported` (`provider_destination_refused`) |
| No names; a provider's certificate could not be verified by the gateway | `unavailable` (`upstream_certificate_invalid`) |
| A selected source neither returned names nor completed a request | not counted as answered; `unavailable` (`sources_not_completed`) when nothing else answered |
| Sandbox runner unreachable, sandbox checks failing, not configured | `unavailable` (`discovery_runner_unavailable`, `egress_sandbox_unavailable`, `egress_sandbox_not_configured`) |
| Engine error or not installed in the runner | `unavailable` (`engine_failed`, `engine_not_installed`) |

## What is stored

- Evidence (`json`): selected, answering, failing (with messages), skipped, gateway-refused and
  unconfirmed sources, the sandbox and gateway used, whether crt.sh's database path was
  unavailable, limits reached, the number of discarded out-of-scope results and every in-scope
  name with the sources that reported it. Query strings and configured keys are removed from
  messages. Access category is `credentialed` when keys were used.
- Entities: the queried domain and one `domain` per subdomain, linked by derived `subdomain_of`
  relationships; observation `subdomain` per name with its sources.

## Scope

Only the domain itself and names ending in `.<domain>` are kept (wildcard prefixes removed);
anything else is discarded and counted. Absence from these datasets does not mean a subdomain does
not exist, and listed names may no longer resolve.

## Tests

- `tests/test_connector_contracts.py`: the connector talks to a real runner on loopback that starts
  a fake binary. Covered:
  - arguments, including `-proxy` and passive flags;
  - no job without a reachable runner, with a missing engine, or with a default route in the
    runner's routing table;
  - scope filtering and the result limit;
  - stderr and `-stats` parsing against recorded output: all sources failing, a verified empty
    result, the crt.sh database path, a silent source without statistics, a missing key, a rate
    limit, a partial result with key redaction;
  - gateway refusals and certificate failures, a fatal error, cancellation stopping the process,
    and job validation.
- `tests/test_egress_gateway.py`: the gateway's refusals, verified TLS relay, certificate failure
  and log hygiene.
- `scripts/verify-phase2.sh` runs the real binary and the real network boundary against a
  controlled crt.sh stand-in. See ADR 0007 for the list of checks.

## Live verification log

| Date | Version | Target category | Outcome | Reviewer |
| --- | --- | --- | --- | --- |
| 2026-09-15 | 1.0.0 | IANA documentation domain `example.com`, sources crt.sh and Digitorus, through the network sandbox | `partial`: crt.sh answered through the egress gateway with verified TLS (5 in-scope names); Digitorus returned a response through the gateway that Subfinder reported as a source error (message not kept by that harness version) | implementing assistant; authorized by the repository owner ([record](live-smoke/2026-09-15-results.json)) |

The sandbox, the gateway and the crt.sh HTTPS path worked against the live provider. The connector stays `fixture_tested` until a check in which every selected source answers.
