# Live smoke checks

Fixture tests show how a connector handles recorded or simulated responses. A live smoke check
shows that the connector still works against its real source on a given day. Checks are small,
bounded and run only with explicit authorization.

- Plan: `scripts/live-smoke.sh --plan`
- Run: `scripts/live-smoke.sh --authorization FILE`

## Rules

1. **Authorization names exact checks.** An authorization file lists check IDs from the table
   below, who approved them, the date, an expiry date, `"credentials": "none"` and
   `"paid_requests": 0`. The harness refuses unknown checks, expired files and any configured
   connector credential. Permission for one input does not cover other inputs, usernames,
   accounts, providers, paid requests or file submissions. Add a new check definition and get
   it approved first.
2. **Checks run through the application.**
   - `scripts/live-smoke.sh` starts an isolated Compose project (`tracehollow-live`, ports 3200
     and 8200) without the fixture overlay and with AI disabled.
   - It creates an administrator and a dedicated case, then runs one saved query per check.
   - It deletes the case and removes the project and its volumes afterwards.
   - It never touches the default `tracehollow` project.
3. **Results keep what verification needs, not the collected content:**
   - outcome, error code and coverage note;
   - pages and items;
   - evidence kinds, provenance fields and hashes;
   - a few public identifiers;
   - gateway decisions.
4. **A result that does not match the expectation is a failed live check.** The connector keeps
   its `fixture_tested` label.
5. **A connector becomes `live_verified` only after a passing check is recorded.** The record goes
   in `docs/connectors/live-smoke/` and in the connector page's live verification log. The label
   covers what the check exercised, as described there.

## Checks

| Check | Connector and exact input | Sources contacted | Investigated target contacted? | Credentials | Request, time and cost bounds | Expected observable behaviour | Cleanup |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `web.example-com` | `public_web.page`, `https://example.com/` | example.com (IANA documentation domain) and any redirect target, each checked by the network policy | yes (it is the target) | none | 1 GET plus at most 5 redirects; 60 s run limit; no cost | `findings`; HTML snapshot with HTTP 200, final URL, connected address, derived text record | case and project deleted |
| `rss.subfinder-releases` | `rss.feed`, `https://github.com/projectdiscovery/subfinder/releases.atom` | github.com | yes (the feed host) | none | at most 2 feed pages; 120 s; no cost | `findings`; feed entries stored once each with feed evidence | same |
| `github.account` → `github.octocat` | `github.account`, username `octocat` (GitHub's demo account) | api.github.com | no (third-party API) | none (anonymous quota) | at most 2 API requests (account and one repository page) of 60 per hour; 120 s; no cost | `findings`; account `octocat` with its platform ID, repository page, quota recorded | same |
| `sherlock.octocat-three-sites` | `username.sherlock`, `octocat` on GitHub, GitLab, Codeberg | github.com, gitlab.com and codeberg.org profile addresses; each platform learns the name | no (platform probes) | none | 3 profile requests; 15 s per platform; 240 s run; no cost | GitHub classified `candidate`; other platforms classified from their responses | same |
| `sherlock.unregistered-three-sites` | `username.sherlock`, `th-smoke-<12 random hex>` on the same three platforms | same three platforms; each learns the random name | no | none | 3 profile requests; as above | no candidate; `no_findings` only if every platform answered `not_found`, otherwise an explicit failure or `partial` | same |
| `subfinder.example-com` | `domain.subfinder`, `example.com`, sources `crtsh`, `digitorus`, at most 500 names | crt.sh and certificatedetails.com, only through the discovery egress gateway (ADR 0007) | no | none (keyless sources) | one crt.sh API request (its database path cannot route) and one Digitorus request; 300 s run; no cost | `findings` or verified `no_findings` with both sources answering; any provider failure stays explicit | same |

Checks that would need credentials, such as the GitHub token path or Subfinder key-based
sources, are not defined. Each needs its own authorization naming the credential and budget.

## Record: 2026-09-15

- **Authorization:**
  [`live-smoke/2026-09-15-authorization.json`](live-smoke/2026-09-15-authorization.json).
  Given in the working session by the repository owner, covering the six checks above, one run
  each.
- **Results:** [`live-smoke/2026-09-15-results.json`](live-smoke/2026-09-15-results.json).
- **Command:** `scripts/live-smoke.sh --authorization docs/connectors/live-smoke/2026-09-15-authorization.json`
- **Machine:** macOS, Docker Desktop 29.8.0, arm64, from this machine's network.

| Check | Outcome | Verdict | What was observed |
| --- | --- | --- | --- |
| `web.example-com` | `findings` | live verified | HTTP 200 from `https://example.com/`, no redirects, not truncated, connected address recorded, HTML snapshot (559 bytes) and derived text record (142 bytes) |
| `rss.subfinder-releases` | `findings` | live verified | 10 feed entries on 1 page (the feed has no next link) |
| `github.octocat` | `findings` | live verified | account `octocat`, platform ID 583231, 8 repositories on the first page, quota fields recorded, 2 pages |
| `sherlock.octocat-three-sites` | `findings` | live verified | GitHub `candidate`; GitLab and Codeberg `not_found` |
| `sherlock.unregistered-three-sites` | `no_findings` | live verified | `not_found` on all three platforms |
| `subfinder.example-com` | `partial` | **failed** (see note) | crt.sh answered through the gateway (verified TLS, 26 KB) with 5 in-scope names; Digitorus failed. Its tunnel was allowed and returned 5.5 KB over verified TLS, but Subfinder reported a source error. The run was correctly `partial`, not `findings` |

**Note on `subfinder.example-com`:**
- The harness evaluator used for this run accepted a `partial` result. Against the documented
  expectation (both sources answer), the check failed. The published results record the
  original verdict next to the corrected one.
- The evaluator now requires every selected source to answer and keeps the redacted source error
  messages. This run did not keep Digitorus's message, so the cause is unknown.
- It was not re-run, because the authorization covered one run. `domain.subfinder` therefore
  remains `fixture_tested`.

Durations in the results file are poll-granular (2 seconds). The live-check logs contained no
secret values.
