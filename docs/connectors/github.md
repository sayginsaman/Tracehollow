# GitHub account (`github.account` 1.0.0)

Looks up a GitHub user or organization and its public repositories through the official REST API.

| | |
| --- | --- |
| Mode | Third-party lookup: GitHub sees the request; the account holder is not contacted |
| Input | GitHub account name (1-39 letters, digits or single hyphens) |
| Parameters | `include_repositories` (default on) |
| Credentials | optional personal access token (no permissions needed for public data) |
| API | `GET /users/{name}`, `GET /users/{name}/repos?type=owner&sort=updated`; headers `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2026-03-10` |
| Endpoint | `TRACEHOLLOW_GITHUB_API_BASE_URL` (default `https://api.github.com`; GitHub Enterprise Server uses `https://HOST/api/v3`) |
| Limits | 1 account page plus up to 9 repository pages, up to 100 repositories per page, 120 s run timeout, 2 concurrent runs, 1 s between requests |
| Retries | 3 attempts for `unavailable` and `rate_limited`, honouring `Retry-After` and the rate-limit reset time |
| Quota | GitHub allows 60 requests/hour per IP address without a token and 5,000/hour with one; remaining, limit, used, resource and reset time are recorded on every run; cost is `none` |
| Cache | none |
| Output schema | `tracehollow.github.account/v1` |
| Live verification | not performed |

API details were taken from docs.github.com ("API Versions", "Rate limits for the REST API",
"Users", "Repositories", "Using pagination") on 2026-09-15.

## What is stored

- Evidence (`json`): each API response body as returned, with HTTP provenance, rate-limit headers
  and the API version. Access category is `credentialed` when a token was used. The token is sent
  only in the `Authorization` header to the configured API address and is never stored.
- Account: a `platform_account` entity matched by GitHub's numeric account ID (so renamed accounts
  stay one entity), with the login and profile URL as identifiers; observation `github_account`
  with public profile fields (name, company, blog, location, public email, bio, counts, created
  and updated times; account creation is the event time).
- The profile website becomes a `url` entity with an observed `links_to` relationship, and a public
  email an `email` entity with an observed `lists_email` relationship. Neither establishes who
  controls the other.
- Repositories: observation `github_repository` per public repository owned by the account.

## Outcomes

| Situation | Outcome |
| --- | --- |
| Account found | `findings` |
| API 404 for the account | `no_findings`, noting that accounts can be renamed, deleted, suspended or hidden (Enterprise Managed Users) |
| 401 with a stored token | `authentication_required` (`github_bad_credentials`); the credential is marked `rejected` |
| 403 or 429 with remaining quota 0 or a rate-limit message | `rate_limited` with the wait from `Retry-After` or the reset time; a wait above the configured maximum ends the run instead of sleeping |
| Other 403 | `access_denied` |
| 5xx, timeouts | `unavailable` (retried) |
| Non-JSON or missing required fields | `parse_error` |
| A repository page fails after the account page | `partial` |
| Pagination link outside the configured API address | `parse_error` (stops) |

## Tests

`tests/test_connector_contracts.py` (account and two repository pages with token and quota, 404
without token, bad token, primary and secondary rate limits, 403, malformed response, outage,
pagination origin, login validation), `tests/test_collection.py` (stored token used and marked
accepted, unreadable credential, rate limit longer than the allowed wait), `scripts/verify-phase2.sh`
(GitHub-shaped fixture API: pagination, quota, 404, rate limit, rejected token).

## Live verification log

None.
