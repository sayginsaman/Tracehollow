# ADR 0006: Public-source collection with a guarded collector, truthful outcomes and pinned engines

- Status: accepted
- Date: 2026-09-15
- Phase: 2 (implemented after Phase 3, which depends on it)

## Context

PRD Phase 2 requires a connector SDK and registry with a source capability UI; public web, RSS/Atom,
GitHub, one username engine and one domain engine; per-source limits, concurrency, retry/backoff,
cache behaviour and scope validation; incremental progress with explicit partial and failure
states; connector documentation and fixture-based contract tests. Acceptance requires provenance
for every observation, tests for success, verified no findings, 401/403, 429, timeout, malformed
content and partial pagination, passive domain scope and candidate-only username hits, SSRF and
redirect protection, live smoke checks only against approved targets, and actionable states for
missing credentials.

Phase 1 delivered durable executions (outbox, leases, idempotent pages) and a synthetic fixture
connector whose page format was specific to candidate accounts. Phase 3 (AI) was built first on
imported evidence and needed collected evidence to complete its milestone.

## Decisions

### Connector contract

Connectors return pages made of drafts: evidence (original bytes plus provenance), entities (with
the identifier used to match existing records), observations and relationships. The execution
engine persists a page in one transaction, keeps the Phase 1 guarantees (claims, leases,
idempotent pages, cancellation between units of work) and never lets a connector touch the
database or storage. Descriptors declare mode, inputs, parameter specs, credentials, coverage,
limits, retry policy, cache policy, quota notes, concurrency, pacing, provider terms,
documentation and verification status; the UI renders parameters from these specs.

Failures are `ConnectorError`s with PRD outcomes; pages may carry an incomplete reason (becomes
`partial`) or an outcome hint (for example `authentication_required` when every source was
skipped). A run is `no_findings` only when the lookup verifiably completed. Entities whose
identifiers cannot be normalized are skipped with a coverage note instead of failing the page.

### Collection modes

`direct_request` (the target's server sees the request: web page, feed), `third_party_api` (a
third party is asked about the input: GitHub API, passive DNS datasets) and `platform_probe`
(each selected platform receives a profile request: username discovery). A saved query may only
combine connectors of one mode. Evidence records carry the mode and an access category (`public`
or `credentialed`).

### Evidence

Collected HTML and XML are stored byte-exact with new kinds `html` and `xml` and are never
rendered; readable text or normalized JSON is stored as separate derived evidence linked by
`derived_from_evidence_id`. Only text and JSON records are indexed for AI retrieval, so citations
open exact passages of the derived text, which links to its snapshot. `collection_metadata`
records requested and final URL, redirects, HTTP status, selected response headers, the connected
address, charset and engine versions; never cookies or authorization headers.

### SSRF protection (`app/connectors/netguard.py`)

Every URL fetched for a case is resolved and every resolved address must be public; loopback,
private, CGNAT, link-local (including metadata), multicast, reserved, unspecified, documentation
and benchmarking ranges are refused, as are IPv4 addresses embedded in IPv6 (mapped, 6to4, NAT64)
and Teredo. A custom httpcore network backend resolves again at connect time, checks the answers
and connects to the checked address, so DNS rebinding between check and connect cannot reach a
blocked address; TLS still verifies the host name. Redirects are followed manually with the same
checks (feeds and the GitHub API are restricted to their origin). Ports are allowlisted (80, 443
by default), proxies from the environment are ignored, and response size, redirects and time are
bounded. Operators may allow specific private networks for lab or authorized internal targets;
loopback, link-local and reserved space stay blocked regardless.

The username engine runs in a separate process whose urllib3 connection factory is replaced by
the same policy, so its requests and redirects are guarded too. Subfinder talks only to its fixed
provider endpoints; the input is a validated domain.

### Collector service

A `collector` service consumes a `tracehollow-collect` queue and is the only service on a
`collect-egress` network; collection runs are routed there by task name, while synthetic fixture
runs and deletion jobs stay on the internal worker. Its image (`collector` build target) adds the
engines; the API and other images do not contain them.

### Engines

- **Username: Sherlock (sherlock-project 0.16.2, MIT).** Compatibility check (2026-09-15): the
  command line checks GitHub for updates and downloads its manifest and exclusions at start, and
  its verdicts treat any status of 300 or above as "not found" on status-code sites and any page
  without the site's error text (login walls, rate-limit pages) as "found". Tracehollow therefore
  calls the library API from an isolated runner with a vendored, curated manifest (58 platforms;
  social networks with login walls excluded) and reclassifies each result from its HTTP status:
  401/403/429/5xx, WAF pages, blocked destinations and errors are inconclusive per platform, never
  absence. Hits are candidate accounts with no relationships between them.
  - Maigret 0.6.5 (MIT) was not chosen: it brings a Flask web UI, ReportLab, XMind and curl-cffi
    (browser TLS impersonation, at odds with not bypassing source controls) and performs recursive
    searches and profile extraction beyond candidate discovery.
- **Domain: Subfinder (v2.16.0, MIT), passive sources only.** Compatibility check (2026-09-15,
  linux/arm64 binary, SHA-256 verified, run without network): it exits 0 with no output when every
  source fails. The adapter runs with `-v`, parses the per-source error and "no key" messages,
  reports `authentication_required`, `rate_limited` or `unavailable` when no source answered and
  `partial` when some failed, disables the update check (`-duc`), writes configuration and keys to
  a private temporary directory, never uses active resolution and discards results outside the
  domain. Query strings and configured keys are removed from stored error messages.
  - BBOT 3.0.2 was not chosen: it is AGPL-3.0, which conflicts with this project's MIT licence even
    when run as a separate process, and it needs Ansible and many other dependencies.
- **GitHub REST API**, version `2026-03-10`, optional token; 404 is verified no findings with a
  note about accounts hidden from unauthorized requests; primary and secondary rate limits become
  `rate_limited` with the documented wait; quota headers are recorded; cost is `none`.
- **Web page and RSS/Atom** are implemented directly (standard-library HTML tokenizer,
  defusedxml for feeds, which rejects entity declarations and external references).

### Limits

Per-connector concurrency slots and per-key request pacing live in PostgreSQL (`source_slots`,
`source_pacing`) so all collector processes share them; a slot is a lease reclaimable after a
crash and by the same connector run on takeover. Retry backoff honours `Retry-After`; a requested
wait above `TRACEHOLLOW_COLLECTION_MAX_RETRY_WAIT_SECONDS` ends the run as `rate_limited` with the
retry time recorded instead of sleeping for an hour.

### Caching

No connector caches responses: every execution retrieves fresh data and stores new evidence, as
the PRD's "new collection creates a new evidence version" requires. Within one execution, feed
entries and repositories repeated across pages are stored once. This is stated per connector.

### Credentials

Connector credentials are stored in `integration_credentials`, encrypted with AES-256-GCM
(`cryptography`), a fresh nonce per value and the connector and credential name as associated
data. The 256-bit key is a secret file (`secrets/credential_encryption_key`) mounted into `api` (to
encrypt) and `collector` (to decrypt); a key identifier detects values sealed with another key.
Values are write-only through the API and only administrators can change them.

## Alternatives considered

- **A proxy container enforcing egress rules** instead of in-process checks: stronger isolation
  for all engines, but DNS rebinding and per-hop checks still need application logic, and it adds
  a service to operate. Deferred; the collector network is already separate.
- **Resolving once and pinning the address in the URL**: breaks TLS name verification and virtual
  hosting. The custom network backend keeps the host name while connecting to the checked address.
- **Response caching with conditional requests** (ETag): saves quota but blurs "collected at"
  semantics; revisit with Phase 5 monitoring.
- **feedparser** for feeds: lenient parsing would turn malformed feeds into partial data instead
  of `parse_error`; its sanitizer is not needed because feed text is never rendered as HTML.

## Consequences

- No connector has been verified against its live source; all are labelled `fixture_tested`.
  Live smoke checks need approved targets (see `docs/connectors/README.md`).
- Sherlock's results remain heuristic: a login wall that lacks the site's error text can look like
  a profile. Candidate accounts are leads to review.
- Subfinder's own HTTP requests are not routed through netguard; its destinations are fixed
  provider APIs. If a provider redirected to an internal address, only the passive result parsing
  would be affected, not stored content.
- Username probes disclose the searched name to every selected platform; the mode label says so.
- Losing the credential key makes stored credentials unusable; they must be entered again.

## Verification

`services/api/tests/test_netguard.py`, `test_connector_contracts.py`, `test_collection.py`,
`test_migrations.py` and `scripts/verify-phase2.sh --e2e` (controlled fixture-site container, SSRF
checks inside the running collector, browser workflow). Results are in `docs/STATUS.md`.
