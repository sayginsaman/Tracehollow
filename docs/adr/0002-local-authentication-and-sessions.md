# ADR 0002: Local administrator bootstrap, sessions and request-forgery protection

- Status: accepted
- Date: 2026-09-15
- Phase: 0

## Context

PRD Phase 0 requires authenticated API access, a secure local administrator bootstrap without shared
default passwords, a reviewed password-hashing and session approach, CSRF protection appropriate to
the session mechanism and restricted CORS. Localhost binding must not replace authentication. The
application is local-first and single-administrator in early phases; team roles arrive in Phase 5.

Threats considered: another local process or another localhost web app (same-site) forging requests,
DNS rebinding from a public site, someone reaching the setup page before the owner, stolen or
replayed cookies, database disclosure, password guessing, and secrets leaking through configuration
errors or logs.

## Decision

1. **Bootstrap:** `scripts/setup.sh` generates `secrets/bootstrap_token`. The web setup endpoint
   requires it (constant-time comparison) and refuses once any administrator exists. Creation runs
   under a transaction-scoped PostgreSQL advisory lock. A CLI (`python -m app.cli create-admin`) is
   available to anyone with container shell access and does not need the token.
2. **Passwords:** Argon2id through `argon2-cffi` with its RFC 9106 low-memory defaults, rehash on
   login when parameters change, 12–1024 characters with no composition rules, dummy-hash
   verification for unknown users, and temporary per-account lockout after 5 failures (30 s doubling
   to 15 min). Usernames are NFKC-normalised and case-folded, folding Turkish dotted and dotless i.
3. **Sessions:** server-side rows in PostgreSQL holding only a SHA-256 digest of a 256-bit random
   token, with idle and absolute expiry, rotation on login, revocation on logout and password reset.
   Cookie `tracehollow_session`: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` for `https` origins.
4. **Request forgery:** defence in depth:
   - a per-session CSRF token `HMAC-SHA256(app_secret_key, session_id)`, required on every
     authenticated unsafe request, enforced inside the authentication dependency so new routes are
     covered by default;
   - a global middleware rejecting unsafe browser requests whose `Origin` is not trusted, or whose
     `Sec-Fetch-Site` is `cross-site` or `same-site` (other localhost ports are same-site);
   - JSON-only request bodies.
5. **Topology:** the browser talks only to the web origin; a Next.js route handler proxies `/api/*`
   to the internal API with header allowlists. CORS stays disabled. Both the API and the proxy
   validate `Host` against allowlists to resist DNS rebinding.

## Alternatives considered

- **Stateless JWT sessions:** rejected. Server-side revocation on logout, a PRD requirement, would
  need a denylist anyway, and token contents would be exposed.
- **Only `SameSite=Strict`:** insufficient, because other applications on localhost are same-site.
- **Direct browser-to-API calls with credentialed CORS:** adds CORS configuration and a second
  browser-facing origin for no benefit.
- **Printing a setup token to container logs** (as some self-hosted tools do): rejected because
  logs must not contain secrets.
- **Default administrator credentials or first-visitor-wins setup:** rejected by the PRD.

## Consequences

- Tokens and the CSRF key live outside the database; database disclosure alone does not yield
  usable sessions.
- Rotating `app_secret_key` invalidates open pages' CSRF tokens but not sessions.
- Lockout can be triggered by anyone who can reach the login page; recovery is the CLI
  `reset-password`. Rate limiting by client address is deferred until non-loopback deployments are
  supported.
- No MFA and no audit-event table in Phase 0; auth events are written to structured logs.

## Verification

`services/api/tests/test_auth.py`, `test_security.py` and `test_middleware.py` cover setup token
rules, concurrent bootstrap, valid and invalid logins, lockout, unauthenticated rejection, forged
cookies, CSRF, cross-origin and Host rejection, session rotation, idle and absolute expiry, logout
replay and password reset. `scripts/smoke_test.py` repeats the security-relevant flows through the
web proxy on the running stack.
