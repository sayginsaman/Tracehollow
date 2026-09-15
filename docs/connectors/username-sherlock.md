# Username discovery with Sherlock (`username.sherlock` 1.0.0)

Checks whether a username appears to exist on selected platforms. **Every hit is a candidate
account: an account with that name appears to exist on the platform. It may belong to anyone.**
Tracehollow never links candidates to each other or to a person.

| | |
| --- | --- |
| Mode | Platform probe: every selected platform receives a request for the profile address and learns the searched name |
| Input | Username (1-64 letters, digits, `.`, `_`, `-`) |
| Parameters | `sites`: platforms to check (default 15, at most 58); `timeout_seconds` per platform (5-60, default 15) |
| Credentials | none |
| Engine | sherlock-project 0.16.2 (MIT) library, run in an isolated process in the collector |
| Manifest | `app/connectors/data/sherlock_sites.json`: 58 platforms copied from Sherlock's `data.json` (upstream SHA-256 recorded, MIT notice in `THIRD_PARTY_NOTICES.md`); social networks with login walls excluded |
| Limits | 1 page, 240 s run timeout, 1 concurrent run |
| Retries | none for the whole run; each platform is checked once |
| Cache | none |
| Output schema | `tracehollow.username.candidates/v1` |
| Live verification | live verified 2026-09-15 on 3 of 58 platforms (GitHub, GitLab, Codeberg; see [live-smoke.md](live-smoke.md)) |

## How results are interpreted

Sherlock's own verdicts are not used directly (compatibility check, 2026-09-15): it reports "not
found" for any status of 300 or above on status-code platforms (including 403, 429 and 5xx) and
"found" for any page lacking the platform's error text (including login walls and rate-limit pages).
Tracehollow reclassifies every platform from the HTTP status:

| Platform result | Classification |
| --- | --- |
| Profile address answered 2xx and the engine found the profile | `candidate` |
| 404/410 (or the platform's declared error status), the platform's "no such user" text on a 2xx page, or a redirect away from the profile on redirect-checked platforms | `not_found` |
| 401 / 403 / 429 / 5xx / bot-protection page / timeout or connection error | `authentication_required` / `access_denied` / `rate_limited` / `unavailable` |
| Name not allowed by the platform's rules | `unsupported` |
| Destination refused by the network policy | `blocked` |
| Anything else (for example an unexpected redirect) | `inconclusive` |

The Sherlock command line is not used: it checks GitHub for updates and downloads its manifest and
exclusion list at start. The runner uses the library API with the vendored manifest, so the only
requests are the profile checks, each guarded by the network policy (including redirects).

## Outcomes

| Situation | Outcome |
| --- | --- |
| Candidates found and every platform gave a definitive answer | `findings` |
| Every platform answered `not_found` | `no_findings` |
| Some definitive answers and some inconclusive platforms | `partial`, listing how many were inconclusive |
| No platform gave a definitive answer | the most common failure (`rate_limited`, `access_denied`, `authentication_required`) or `unavailable` |
| Engine not installed (for example outside the collector) | `unavailable` (`engine_not_installed`) |

## What is stored

- Evidence (`json`): per platform the profile address, engine status, HTTP status, classification,
  explanation and timing, plus engine version and a digest of the site definitions used.
- Observation `candidate_account` and a `platform_account` entity per candidate, identified by the
  profile URL and platform-scoped username, marked `candidate`.

## Limitations

Detection depends on each platform's current public responses and can be wrong: a login wall that
lacks the platform's error text can look like a profile; platforms change page layouts and status
codes. Results are leads for manual review.

## Tests

`tests/test_connector_contracts.py` (classification table including the cases Sherlock itself gets
wrong, candidates with partial coverage, verified absence, rate-limited and blocked checks, engine
failure, input and platform validation, the real runner refusing a loopback profile address),
`scripts/verify-phase2.sh` (fixture platforms: candidates, 429, a manifest entry pointing at the
metadata address, verified absence, single-slot concurrency).

## Live verification log

| Date | Version | Target category | Outcome | Reviewer |
| --- | --- | --- | --- | --- |
| 2026-09-15 | 1.0.0 | Demo account name `octocat` on GitHub, GitLab, Codeberg | `findings`: GitHub `candidate`, GitLab and Codeberg `not_found` | implementing assistant; authorized by the repository owner ([record](live-smoke/2026-09-15-results.json)) |
| 2026-09-15 | 1.0.0 | Random never-registered name on the same platforms | `no_findings`: `not_found` on all three | same |

Scope: three platforms. The other 55 manifest entries have not been checked live, and platform responses change over time.
