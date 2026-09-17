# Instagram account (`instagram.account` 1.0.0)

Capability-dependent Instagram lookups. Design: [ADR 0009](../adr/0009-social-connector-capabilities.md).
Tracehollow does **not** offer private-profile access, unrestricted personal-account collection,
stories, follower lists or direct messages.

| | |
| --- | --- |
| Input | Instagram username (1-30 letters, digits, periods, underscores; a leading `@` is ignored) |
| Parameters | `capability` (`official_business_discovery` default, or `public_profile_page`), `include_media` (official API, default on) |
| Credentials | `access_token` (Facebook User access token) and `ig_user_id` (your professional account's Instagram user ID) for the official API; none for the profile page |
| Endpoints | `TRACEHOLLOW_INSTAGRAM_GRAPH_API_BASE_URL` (default `https://graph.facebook.com`), `TRACEHOLLOW_INSTAGRAM_WEB_BASE_URL` (default `https://www.instagram.com`) |
| Limits | up to 10 pages, 50 media per page, 120 s per run, 1 concurrent run, 2 s between requests |
| Retries | 3 attempts for `unavailable` and `rate_limited` |
| Verification | **fixture-tested**; not live-verified (no credentials or authorization for a live check) |

## Capabilities

| Capability | Status | Access | Collection mode |
| --- | --- | --- | --- |
| Official API: professional account discovery | implemented | Official API (Graph API `v25.0` Business Discovery) | Third-party lookup |
| Unofficial: public profile page metadata | implemented, **off by default** | Unofficial public web page | Platform probe |
| Unofficial client with a logged-in session (Instaloader and similar) | not implemented | Unofficial client | — |
| Third-party data provider | not implemented | Third-party provider | — |
| Private profiles and unrestricted personal accounts | **excluded** | — | — |

### Official API: Business Discovery

`GET /v25.0/{ig_user_id}?fields=business_discovery.username({username}){id,username,biography,website,followers_count,media_count,media.limit(N){id,caption,media_type,media_product_type,permalink,shortcode,timestamp,like_count,comments_count}}`
with `Authorization: Bearer <token>`; later media pages use `media.limit(N).after(cursor)`.
Checked against developers.facebook.com (IG User Business Discovery, IG User, IG Media, Graph API
error handling) on 2026-09-16.

- **Covers:** Business and Creator accounts that are not age-gated. Personal accounts are not
  returned by this API.
- **Requires:** a Facebook User access token with `instagram_basic`, `instagram_manage_insights`
  and `pages_read_engagement` for a professional account you administer, and that account's
  Instagram user ID. Meta Platform Terms and App Review apply.
- **Returns:** account `id`, `username`, `biography`, `website`, `followers_count`, `media_count`;
  per media item the fields above. `like_count` is absent when the owner hid likes
  (`like_count_hidden: true`).
- **Never returns:** personal or age-gated accounts, private content, stories, follower and
  following lists, messages, profile name and picture (not public fields), comment text.
- **Stored:** the JSON response as evidence (`credentialed`), a `platform_account` entity matched by
  the Instagram user ID, `instagram_account` and `instagram_media` observations, and Graph API usage
  headers (`X-App-Usage`, `X-Business-Use-Case-Usage`) as quota. The token is sent only in the
  header and never stored with evidence.

| Situation | Outcome and code |
| --- | --- |
| Account returned | `findings` |
| Credentials not configured | `authentication_required` / `credential_not_configured` (nothing requested) |
| Error 190 or 102 (expired, revoked, changed password) | `authentication_required` / `instagram_token_invalid`; credential marked rejected |
| Error 4, 17, 32, 613, 80002 or HTTP 429 | `rate_limited` / `instagram_rate_limited`, waiting for `estimated_time_to_regain_access` when reported |
| Error 110 or subcode 2207013 | `unsupported` / `instagram_account_not_discoverable` — the account may not exist or may be personal, restricted or age-gated; **not** evidence of absence (subcode taken from developer reports, not Meta's error reference) |
| Error 10 or 200-299 | `access_denied` / `instagram_permission_missing` |
| Error 368 or 25 | `access_denied` / `instagram_restricted` |
| Error 1, 2 or HTTP 5xx | `unavailable` / `instagram_temporary_error` |
| Non-JSON or missing account object | `parse_error` |
| A later media page fails | `partial` |

### Unofficial: public profile page

One unauthenticated `GET {web}/{username}/` without cookies, reading only `og:*` and `description`
metadata tags. Instagram's Terms of Use restrict automated collection, so the capability runs only
when an administrator sets `TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=true`; otherwise runs end as
`unsupported` / `capability_disabled` and the Sources page shows it as disabled.

| Situation | Outcome and code |
| --- | --- |
| Metadata present | `findings`; observation `instagram_public_profile_page` with displayed counts as text; no entity (no stable ID) |
| Redirect to `/accounts/login` or a login form without profile metadata | `authentication_required` / `instagram_login_wall` — nothing can be concluded |
| HTTP 404 | `no_findings` with a note that the account may not exist, be renamed, removed, restricted or hidden |
| HTTP 429 | `rate_limited` / `instagram_throttled` |
| No expected metadata | `parse_error` / `unexpected_page_layout` |
| Page says the account is private | `findings` with an incomplete reason: only the summary is visible |

The HTML snapshot is stored but not indexed for AI retrieval.

## Tests

`tests/test_social_connectors.py` (capability model, token handling, every error class above,
login walls, disabled capability, profile page states), `tests/test_social_collection.py` (Sources
capability blockers, unimplemented capabilities rejected, missing credentials block runs),
`scripts/verify-phase4.sh` (fixture Graph API and profile pages in the running stack).
