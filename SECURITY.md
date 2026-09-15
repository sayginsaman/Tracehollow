# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
for this repository (**Security → Report a vulnerability**). Do not open public issues, pull
requests or discussions for security problems.

Include the affected commit or version, reproduction steps, impact, and whether real data or
credentials were exposed. Never include real investigation data or live credentials in a report.
Disclosure timelines are agreed with the reporter for each report.

## Supported versions

Tracehollow is pre-release software. Only the latest commit on `main` receives security fixes.

## Intended use

Tracehollow is intended for investigating public sources and material you are authorized to
process. Features that bypass authentication, access private accounts, harvest credentials or
evade source controls are out of scope and will not be accepted (see [PRD.md](PRD.md) §3).

## Security model (Phase 0)

This section describes what the current code actually enforces. It is updated as phases add
functionality.

### Network exposure

- Only `web` (3000) and `api` (8000) are published, bound to `127.0.0.1` by default.
- PostgreSQL and Redis have no host ports and run on an internal Docker network with no external
  connectivity. The worker is attached only to that internal network.
- Localhost binding is not a substitute for authentication: every non-health API route requires a
  session.

### Authentication and sessions

- One local administrator, created once. Web setup requires a random token generated on the host
  (`secrets/bootstrap_token`); setup is refused permanently once an administrator exists, and
  concurrent setup attempts are serialized with a database advisory lock.
- Passwords are hashed with Argon2id (argon2-cffi, RFC 9106 low-memory parameters). Minimum length
  12, maximum 1024; no composition rules. Unknown usernames are verified against a dummy hash.
- After 5 consecutive failures an account is locked for 30 seconds, doubling up to 15 minutes.
  The lockout can be used by someone with network access to delay the administrator; recovery is
  `python -m app.cli reset-password`.
- Sessions are opaque 256-bit random tokens. Only their SHA-256 digest is stored in PostgreSQL.
  Sessions have an idle timeout (default 8 hours) and an absolute lifetime (default 24 hours), are
  rotated on login and revoked server-side on logout or password reset.
- Session cookie: `HttpOnly`, `SameSite=Strict`, `Path=/`; `Secure` when the public origin is
  `https://`.

### Request forgery and origin protection

- State-changing requests must carry a per-session HMAC-SHA256 CSRF token (`X-CSRF-Token`).
- Unsafe methods from browsers are rejected unless `Origin` is a trusted origin; requests whose
  `Sec-Fetch-Site` is `cross-site` or `same-site` are rejected. This also covers other services on
  other localhost ports, which count as same-site.
- The API rejects unexpected `Host` headers, and the web proxy does the same, to resist DNS
  rebinding.
- CORS is not enabled: the browser reaches the API only through the same-origin web proxy, which
  forwards a fixed header allowlist.
- API responses send `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and a restrictive CSP. The web app sends
  a CSP without third-party sources. Request bodies are limited to 64 KiB at the API and 1 MiB at
  the proxy.

### Secrets

- No default passwords or keys exist in the repository. `scripts/setup.sh` generates secrets with
  the operating system's CSPRNG, never overwrites existing ones, and keeps `secrets/` at mode `0700`.
- Secrets reach containers as mounted files (`*_FILE` settings), not environment variables, so
  they are not visible in `docker inspect` and are not inherited by child processes.
- Redis authenticates with an ACL file that contains only a SHA-256 digest of its password.
- Configuration errors name the offending setting but never echo values.
- Logs are structured JSON. Credentials in URLs, key/value pairs, bearer tokens and sensitive field
  names are redacted, and access logs omit query strings, cookies and bodies.

### Least privilege

- The API, worker and migration job connect as `tracehollow_app`, a non-superuser role that owns
  only the application database.
- Application containers run as UID 10001 with a read-only root filesystem, all Linux
  capabilities dropped and `no-new-privileges`. No container mounts the Docker socket.
- Base images and GitHub Actions are pinned by version and digest or commit SHA.

### Privacy

No telemetry and no outbound network requests at runtime (Next.js telemetry is disabled). The
optional API docs (`TRACEHOLLOW_API_DOCS_ENABLED=true`) load Swagger UI assets from a public CDN.

## Known limitations

- No TLS termination is included. Serve over HTTPS before exposing Tracehollow beyond loopback,
  and update the origin and host settings.
- The Next.js CSP allows `'unsafe-inline'` scripts because nonce-based CSP is not configured yet.
- Single administrator only; no multi-factor authentication and no audit-event table yet.
- Docker volumes and backups are not encrypted by Tracehollow. Use full-disk encryption and store
  backups and `secrets/` in protected locations.
- Secret files are mounted readable inside the service containers that need them.
- Dependency and container vulnerability scanning is not yet automated in CI.

Operational guidance: [docs/operations/secrets.md](docs/operations/secrets.md) and
[docs/operations/backup-restore.md](docs/operations/backup-restore.md).
