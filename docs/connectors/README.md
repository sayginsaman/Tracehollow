# Connectors

Connectors collect data for saved queries. Each run stores what it retrieved as evidence, records
observations and reports an explicit outcome. Design: [ADR 0006](../adr/0006-public-source-collection.md).
The **Sources** screen in the application shows the same information for the installed version.

> **Verification status (2026-09-15):** every public-source connector is covered by contract tests
> and by `scripts/verify-phase2.sh` against controlled fixtures. After authorized live smoke checks,
> `public_web.page`, `rss.feed`, `github.account` and `username.sherlock` are `live_verified` for
> the scope recorded on their pages. `domain.subfinder` remains `fixture_tested`: its live check
> was partial (Digitorus failed). See [live smoke checks](#live-smoke-checks).

| Connector | Mode | Input | Credentials | Page |
| --- | --- | --- | --- | --- |
| `public_web.page` | Direct request | URL | none | [public-web-page.md](public-web-page.md) |
| `rss.feed` | Direct request | URL | none | [rss-atom-feed.md](rss-atom-feed.md) |
| `github.account` | Third-party lookup | Username | optional token | [github.md](github.md) |
| `username.sherlock` | Platform probe | Username | none | [username-sherlock.md](username-sherlock.md) |
| `domain.subfinder` | Third-party lookup | Domain | optional per-source keys | [domain-subfinder.md](domain-subfinder.md) |
| `synthetic.fixture` | Synthetic | Username, domain, email | none | [synthetic-fixture.md](synthetic-fixture.md) |
| `instagram.account` | Per capability | Username | per capability | [instagram.md](instagram.md) |
| `telegram.public_channel` | Per capability | Username | bot token for the Bot API capability | [telegram.md](telegram.md) |
| `youtube.data_api` | Third-party lookup | Handle, channel ID, video ID | API key | [youtube.md](youtube.md) |

> **Phase 4 social connectors (2026-09-16):** `instagram.account`, `telegram.public_channel` and
> `youtube.data_api` are **fixture-tested only** (contract tests and `scripts/verify-phase4.sh`).
> None has been live-verified: no platform credentials or authorization for a live check were
> available. Missing credentials block their runs with `authentication_required` rather than
> producing a result.

## Capability matrix (social platforms)

Platform connectors declare every access method considered, implemented or not
([ADR 0009](../adr/0009-social-connector-capabilities.md)). Only implemented capabilities can be
selected; the Sources screen shows why any capability is unavailable.

| Platform | Capability | Status | Access | Mode | Needs |
| --- | --- | --- | --- | --- | --- |
| Instagram | Professional account discovery (Graph API Business Discovery) | implemented | Official API | Third-party lookup | access token + professional IG user ID |
| Instagram | Public profile page metadata | implemented, off by default | Unofficial public web | Platform probe | `TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=true` |
| Instagram | Logged-in session client (Instaloader and similar) | not implemented | Unofficial client | — | — |
| Instagram | Third-party data provider | not implemented | Third-party provider | — | — |
| Instagram | Private profiles, unrestricted personal accounts | excluded | — | — | — |
| Telegram | Public channel web preview | implemented | Unofficial public web | Platform probe | none |
| Telegram | Public chat metadata (Bot API `getChat`) | implemented | Official API | Third-party lookup | bot token |
| Telegram | User-account MTProto session (Telethon and similar) | not implemented | Unofficial client | — | — |
| Telegram | Private groups, joining, messaging | excluded | — | — | — |
| YouTube | Channel and uploaded videos | implemented | Official API | Third-party lookup | API key |
| YouTube | Video and public comments | implemented | Official API | Third-party lookup | API key |
| YouTube | Caption downloads (transcripts) | not implemented | Official API (OAuth by the video's editor) | — | — |
| YouTube | Unofficial transcript endpoints | not implemented | Unofficial public web | — | — |

All social traffic uses the same guarded HTTP client as the other connectors (SSRF checks, redirect
and size limits, collector egress only); no platform SDK or subprocess is used.

## Collection modes

- **Direct request:** Tracehollow requests the address you enter. That server sees the request and
  the collector's IP address.
- **Third-party lookup:** a third-party service is asked about the input; the target itself is not
  contacted, but the third party learns what was looked up.
- **Platform probe:** each selected platform receives a request for a profile address, so each
  learns the searched name.

A saved query can only combine connectors of one mode. For platform connectors the mode is that of
the selected capability.

## Outcomes

| Outcome | Meaning | Examples |
| --- | --- | --- |
| `findings` | Usable data returned and the lookup completed | Page retrieved; feed entries; account found |
| `no_findings` | A supported lookup completed and found nothing within its coverage | HTTP 404 for a page; GitHub API 404; the name was absent on every checked platform; every selected passive source answered with no names |
| `partial` | Some usable data, but incomplete | Truncated page; a later feed page failed; some platforms or sources failed |
| `authentication_required` | Credentials missing, rejected or unreadable | GitHub token rejected; only key-based sources selected without keys |
| `access_denied` | The source refused access | HTTP 403 |
| `rate_limited` | A limit was reached; retry information kept | HTTP 429; GitHub remaining quota 0 |
| `unsupported` | Input, content or destination not supported | PDF instead of HTML; destination refused by the network policy |
| `unavailable` | Temporary source or network problem | Timeout, DNS failure, HTTP 5xx, engine not installed |
| `parse_error` | Data could not be interpreted | Malformed feed; unexpected API response |
| `canceled` | Stopped by the analyst | — |

Blocks, rate limits, login walls, source errors and incomplete pagination are never reported as
`no_findings`.

## Network safety

Every address fetched for a case (and every redirect) is checked: only `http`/`https` on allowed
ports (80, 443), no credentials in URLs, and every resolved address must be public. Loopback,
private networks, link-local and cloud metadata addresses, multicast and reserved ranges, and IPv6
forms of these are refused with outcome `unsupported` and a code such as `blocked_address`,
`blocked_host` or `blocked_port`; nothing is stored. The connection goes to the address that was
checked.

To collect from a lab or authorized internal target, an operator can allow specific private
networks and ports:

```bash
# .env — only networks you control; loopback and link-local stay blocked regardless
TRACEHOLLOW_COLLECTION_ALLOWED_PRIVATE_NETWORKS=10.20.0.0/16
TRACEHOLLOW_COLLECTION_ALLOWED_PORTS=80,443,8080
```

## Limits, retries and caching

- Each connector has a timeout, page limit and per-page item limit; saved-query limits can only
  lower them.
- Concurrency: at most `max_concurrent_runs` runs of one connector execute at once across all
  collector processes; others wait (shown as progress) until a slot is free or the timeout ends.
- Pacing: requests to one host (web, feeds) or API are spaced by `min_request_interval_seconds`.
- Retries: transient outcomes are retried with exponential backoff, honouring `Retry-After`. If a
  source asks to wait longer than `TRACEHOLLOW_COLLECTION_MAX_RETRY_WAIT_SECONDS` (default 60),
  the run ends as `rate_limited` and records when to try again.
- Caching: none. Each execution retrieves fresh data and creates new evidence records; items
  repeated on later pages of the same execution are stored once.

## Credentials

Administrators set credentials on the **Sources** screen. They are encrypted with AES-256-GCM using
`secrets/credential_encryption_key`, never shown again, never logged and never written to evidence.
Runs mark a credential as `rejected` when the source refuses it. See
[secrets.md](../operations/secrets.md#credential_encryption_key).

## Where collection runs

Runs that contact sources are executed by the `collector` service; synthetic fixture runs use the
internal `worker`. The collector image contains sherlock-project 0.16.2. Subfinder v2.16.0 runs only
in the `discovery-runner` sandbox. Its traffic leaves through `discovery-gateway`, which admits
only the selected providers after the address policy and verifies their certificates
([ADR 0007](../adr/0007-subfinder-network-sandbox.md)). Only `collector` and `discovery-gateway`
have the `collect-egress` network.

## Live smoke checks

Fixture tests do not prove that a live source still behaves as documented. A connector may be
marked `live_verified` only after a live check that meets all of these conditions:

1. The exact inputs are approved for this purpose by whoever is authorized to approve them, and
   the check stays within the source's terms. Approval for one input does not extend to others.
2. Required credentials and any budget are available; no paid call is made without that approval.
3. The check runs through the normal application (a saved query in a dedicated case), so
   outcomes, evidence and provenance are recorded exactly as for real use.
4. The result is recorded in `docs/connectors/live-smoke/` and in the connector's page under
   "Live verification log" (date, version, target category without sensitive details, outcome,
   reviewer). Only then are `last_live_verification` and `verification_status` updated in the
   connector descriptor.

The plan, bounds and harness (`scripts/live-smoke.sh`) are in [live-smoke.md](live-smoke.md). The
2026-09-15 record covers six checks: five met their expectations, and the Subfinder check was
partial.

## Writing a connector

1. Implement `validate()` (reject invalid inputs and parameters with a clear `ValueError`) and
   `fetch_page()` returning a `ConnectorPage` (see `app/connectors/base.py`).
2. Fetch URLs only through `app.connectors.http.fetch` (network policy, pacing, cancellation).
   Subprocess engines use `app.connectors.engines.process.run` with an argument list and a minimal
   environment. A third-party binary whose own network traffic the network policy cannot control
   must run in a network sandbox like the Subfinder runner (ADR 0007), not in the collector.
3. Store the original bytes as evidence with provenance; store derived text or JSON separately with
   `derived_from`; describe entities with a stable match identifier (platform IDs where available);
   never create relationships that assert identity.
4. Map every failure to an outcome; use `incomplete_reason` for usable but incomplete pages and
   `outcome_hint` when a completed run must not be `findings`/`no_findings`.
5. Declare the descriptor fully (mode, coverage, credentials, limits, retry, cache policy, quota,
   terms, documentation) and register the connector in `app/connectors/registry.py`.
6. Add contract tests covering success, verified no findings, 401/403, 429, timeout, malformed
   content and partial pagination where the source has these states, and a page in this directory.
