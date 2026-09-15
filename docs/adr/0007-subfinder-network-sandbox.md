# ADR 0007: Network sandbox and egress gateway for Subfinder

- Status: accepted
- Date: 2026-09-15
- Phase: 2–3 verification and hardening follow-up (amends the Subfinder part of ADR 0006)

## Context

ADR 0006 ran ProjectDiscovery Subfinder as a subprocess of the `collector`. That container is on
the `data` network (PostgreSQL, Redis) and on `collect-egress` (unrestricted outbound access).
Tracehollow's SSRF protections (`app/connectors/netguard.py`) apply only to requests Tracehollow's
own HTTP client makes. Validating Subfinder's arguments does not constrain the traffic the binary
sends itself. STATUS recorded this as a residual risk.

A trace of the Subfinder v2.16.0 source (2026-09-15) showed what the binary actually does with
the flags Tracehollow uses (`-duc`, no `-active`/`-nW`):

| Behaviour | Where | Consequence |
| --- | --- | --- |
| HTTP requests to fixed provider URLs (`https://crt.sh/?q=...`, `https://certificatedetails.com/<domain>`, `https://anubisdb.com/...`, `https://api.hackertarget.com/...`, `https://rapiddns.io/...`, `https://api.certspotter.com/...`, `https://www.virustotal.com/...`, `https://otx.alienvault.com/...`, `https://api.securitytrails.com/...`) | `pkg/subscraping/sources/*` through one `http.Client` | Go follows redirects without any address checks; `-proxy` is honoured by this client only |
| `TLSClientConfig{InsecureSkipVerify: true}` | `pkg/subscraping/agent.go` | Provider certificates are never verified, so a network attacker could read API keys or alter results |
| Direct PostgreSQL connection to `crt.sh:5432` before the HTTPS API | `pkg/subscraping/sources/crtsh` | Not HTTP, so it ignores `-proxy`; it resolves `crt.sh` with the local resolver; a failure is logged as a source error even when the HTTPS fallback succeeds |
| DNS resolution | Go resolver for the PostgreSQL path (and for HTTP without a proxy) | Resolver answers decide the destination; a hostile or misconfigured resolver can point providers at internal addresses |
| Update check | `-duc` disables it | None when the flag is present |
| Exit status 0 with no output when every source fails | runner | Already handled by parsing verbose messages (ADR 0006); the trace added `-stats` as positive evidence of completed requests |

The domain being investigated appears only in request paths and query strings sent to providers.
The binary does not contact the domain itself in passive mode.

## Decision

Subfinder runs only in a network sandbox. Its traffic can leave only through an egress gateway
that enforces the collection policy. The protection is the container network, not in-process code.

1. **`discovery-runner` service** (image target `discovery-runner`). It contains Subfinder v2.16.0,
   its licence and a standard-library job runner (`app/connectors/engines/subfinder_runner.py`).
   It has no secrets, no database or broker client and no other application code. Its only
   network is `discovery`.
2. **`discovery` network**: `internal: true` with
   `com.docker.network.bridge.gateway_mode_ipv4: isolated`. It has no default route and no
   external DNS forwarding. With isolated mode the host has no address on the bridge. Only
   `collector`, `discovery-runner` and `discovery-gateway` are attached.
3. **`discovery-gateway` service** (`app/egress/gateway.py`, API image, no secrets) on `discovery`
   and `collect-egress`. It accepts only `CONNECT <provider-host>:443`, where the host is on the
   allowlist traced above (`app/egress/providers.py`). IP literals, other names and other ports
   are refused. Every resolved address must pass `NetworkPolicy`: loopback, link-local,
   metadata, private and reserved ranges are refused unless an operator allows a lab network in
   `TRACEHOLLOW_DISCOVERY_ALLOWED_PRIVATE_NETWORKS` (empty by default; loopback and link-local can
   never be allowed). The gateway connects to a checked address, **verifies the provider
   certificate for the host name** and only then answers `200`. It then terminates Subfinder's
   TLS with a throwaway certificate, which Subfinder accepts because it does not verify, and
   relays plaintext between the two sessions. Refusals use the reason phrase
   `Tracehollow egress refused <code>`, which appears in Subfinder's error messages. Tunnels are
   limited in count (16), duration (300 s), idle time (60 s) and bytes (64 MiB per direction).
   Logs record the allowlisted provider name, decision, code, byte counts and duration, never
   refused names, paths or query strings.
4. **The runner refuses to run when the sandbox is not in place.** Before every job it requires
   no IPv4 or IPv6 default route, exactly one interface, no answer from the network's first
   address unless that address is a sandbox container, and a healthy gateway. Its health check
   fails otherwise, so `docker compose up --wait` reports it.
5. **The collector never runs Subfinder.** The binary was removed from the collector image. The
   connector sends a validated job to `TRACEHOLLOW_DISCOVERY_RUNNER_URL` (a trusted internal
   endpoint, not case data) and reads newline-delimited results with a one-second heartbeat.
   Cancelling closes the stream and the runner stops the process group. Unavailable, unhealthy
   or misconfigured sandboxes fail visibly with `unavailable` and a specific code:
   `discovery_runner_unavailable`, `egress_sandbox_unavailable`, `engine_not_installed` or
   `egress_sandbox_not_configured`. There is no fallback to local execution.
6. **Outcomes stay truthful.**
   - A source counts as answered only with positive evidence: results, or a completed request in
     Subfinder's `-stats` table without an error.
   - crt.sh's unroutable database path is recorded as a note, not as a source failure.
   - When every failing source was refused by the gateway, the run is `unsupported` with
     `provider_destination_refused`.
   - An untrusted provider certificate is `unavailable` with `upstream_certificate_invalid`.
   - Pages can now carry an `outcome_code`, which becomes the run's error code.

Passive semantics are preserved: Subfinder still runs without resolution flags, results outside
the domain are discarded, and the gateway cannot reach the investigated domain unless it is
itself a provider host (for example `crt.sh`).

## Alternatives considered

- **Pass `-proxy` to Subfinder inside the collector.** Rejected. It is voluntary: crt.sh's
  PostgreSQL path already ignores it, and the collector's networks would still allow direct
  connections to the internet, metadata addresses, PostgreSQL and Redis.
- **Per-UID `iptables` rules or a new network namespace (`unshare -n`) inside the collector.**
  Rejected. Both need `CAP_NET_ADMIN`/`CAP_SYS_ADMIN` or user namespaces in a hardened container
  that drops all capabilities, and they are hard to verify on Docker Desktop.
- **Plain CONNECT tunnel without TLS re-origination.** Rejected. It would enforce destinations
  but leave provider API keys and results exposed to interception, because Subfinder never
  verifies certificates.
- **Disable the connector until upstream adds controls.** This is the fallback if Docker cannot
  provide isolated internal networks, for example on Engine versions older than 28. It was not
  needed on the verified engine.
- **Run Subfinder through the Docker socket.** Rejected by AGENTS.md: never mount the Docker socket
  in collection services.

## Consequences

- There are two new long-running services. Both are hardened (read-only, all capabilities
  dropped, `no-new-privileges`, UID 10001) and publish no ports.
- **Docker Engine 28 or later** is required for isolated gateway mode. Verified on Engine 29.8.0
  (Docker Desktop, arm64). On an engine without it, the runner reports that the host answers
  and refuses jobs.
- Provider endpoints are pinned to the traced v2.16.0 source. Upgrading Subfinder requires
  re-tracing and updating `app/egress/providers.py`. A provider that redirects to another host is
  refused and shows as a source error; only a live check reveals this.
- The gateway sees provider requests in plaintext, including API keys, but never logs them.
  Keys travel from the collector to the runner as JSON on the internal `discovery` network,
  unencrypted but inside the Docker bridge, and are written only to the runner's private
  temporary provider configuration.
- crt.sh answers only through its HTTPS API. The PostgreSQL interface, sometimes more complete,
  is not used.
- **Sherlock is not in this sandbox.** It still runs in the collector, and its network policy is
  enforced in-process: the runner patches `urllib3`'s connection function for every connection
  of that process, including redirects. That covers the `requests`-based Sherlock library but is
  not a network boundary. A dependency opening its own sockets would bypass it. This remains a
  documented limitation.
- Verification needs a controlled certificate authority. `scripts/verify-phase2.sh` creates a
  throwaway CA and certificate for the crt.sh stand-in; only the verification gateway trusts it,
  through `SSL_CERT_FILE`.

## Verification

- `tests/test_egress_gateway.py` runs the real gateway server on loopback:
  - refusal before any connection for non-provider names, IP literals (IPv4 and IPv6), other
    ports, providers resolving to loopback, metadata, private or mixed addresses, non-CONNECT
    methods and oversized heads;
  - a verified TLS relay to a local provider with a test CA;
  - `502 upstream_certificate_invalid` for an untrusted certificate;
  - no refused host names in logs.
- `tests/test_connector_contracts.py` drives the connector through a real runner on loopback with a
  fake Subfinder binary:
  - the `-proxy` argument and passive flags;
  - no job when the runner is unreachable, the engine is missing, or a routing table with a
    default route is presented;
  - the crt.sh database path, silent sources without `-stats` evidence, gateway refusals and
    certificate failures;
  - cancellation stopping the process;
  - job and sandbox-check validation.
- `scripts/verify-phase2.sh` checks the real boundary with the real binary in Docker:
  - the `discovery` network is internal and isolated, with only the three sandbox services;
  - from inside the runner, the fixture, metadata and public addresses are unreachable (`ENETUNREACH`);
  - PostgreSQL, Redis, the API, the fixture and `crt.sh` do not resolve;
  - the network's first address belongs to a sandbox container, not the host;
  - the gateway refuses each prohibited destination with its code and admits `crt.sh:443` only
    with a verification-CA certificate;
  - Subfinder returns crt.sh results only through the gateway, and the fixture recorded the
    gateway's address as the only client;
  - a verified empty result, a refused run and an untrusted-certificate run;
  - the runner image refuses to work on the egress network or on an internal network without
    isolation;
  - a stopped runner yields `discovery_runner_unavailable`;
  - investigated domains never appear in gateway or runner logs.
