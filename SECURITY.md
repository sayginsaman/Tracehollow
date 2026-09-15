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

## Security model (Phases 0, 1 and 3)

This section describes what the current code actually enforces. It is updated as phases add
functionality.

### Network exposure

- Only `web` (3000) and `api` (8000) are published, bound to `127.0.0.1` by default.
- PostgreSQL and Redis have no host ports and run on an internal Docker network with no external
  connectivity. The worker and the dispatcher are attached only to that internal network, so the
  Phase 1 fixture connector cannot reach the internet even if it tried.
- `ai-worker` (Phase 3) is the only service with outbound connectivity, through a dedicated
  `ai-egress` network, and it publishes no ports. It connects only to the operator-configured
  Ollama address and, if configured, the cloud provider. It does not follow redirects or use proxy
  settings from the environment, and model responses are size-limited.
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
  the proxy, except evidence imports (5 MiB at the API, 5 MiB + 64 KiB at the proxy, rejected from
  `Content-Length` before the body is read).

### Case authorization (Phase 1)

- Every case route, including evidence previews and downloads, exports, execution progress,
  cancellation, the graph and notes, loads the case through a membership check on the server. Case
  ids that do not exist and cases the user is not a member of both return `404`.
- Child records are always selected by case id and record id together, so an evidence, run or
  relationship id from another case returns `404` even for a member of both cases.
- Archived cases are read-only (`409 case_archived`); cases being deleted refuse writes
  (`409 case_deletion_in_progress`). Deletion requires typing the exact case title, and deletion
  jobs are visible only to the user who requested them.
- There is no team management yet. The model supports several members per case, but members can
  only be added directly in the database.

### Evidence handling (Phase 1)

- Imports accept only UTF-8 text and JSON up to 5 MiB; binary data, invalid encodings, malformed
  or overly deep JSON and empty files are rejected before anything is stored. Each import requires
  an import origin and is labelled `authorized_import`; synthetic fixture output is labelled
  `synthetic_fixture` everywhere.
- Client filenames are display metadata only (normalized, path components, control and
  bidirectional characters removed); storage paths are server-generated UUIDs and a stored file is
  never overwritten.
- Stored bytes are re-hashed on every read; a missing or altered file is reported and never served.
- Previews are returned as JSON strings and rendered as inert text; imported HTML or scripts are
  never interpreted. Downloads are `application/octet-stream` attachments with
  `Content-Security-Policy: sandbox; default-src 'none'`.
- Application log events record case and evidence id prefixes, sizes and outcomes, not evidence
  content, filenames or query input values. Tracebacks of unexpected errors are logged and could
  contain fragments of the data being processed; treat logs as sensitive.

### Exports (Phase 1)

- Exports are built from explicit column allowlists. They exclude password hashes, sessions, CSRF
  tokens, execution lease tokens, storage paths and configuration, and they do not embed evidence
  bytes (records carry the evidence id and SHA-256).
- Exports contain case content and are **not redacted**; the manifest says so. Treat exported files
  like the case itself.
- CSV cells that a spreadsheet could evaluate as a formula (leading `=`, `+`, `-`, `@`, tab,
  carriage return, or their full-width forms) are prefixed with `'`.

### Background work (Phase 1)

- PostgreSQL holds execution state, leases, cancellation and outcomes; Redis messages contain only
  a run or deletion job id. A forged or replayed broker message cannot change parameters, and
  duplicate delivery is a no-op.
- Execution parameters are validated against the connector descriptor when the query is saved and
  snapshotted when a run is created. The only connector in Phase 1 is the synthetic fixture, which
  performs no network access.

### Evidence-grounded AI (Phase 3)

- **Authorization:** AI routes use the same case membership checks as the rest of the case. The
  worker re-checks the case status, the requester's active membership, the installation AI switch and
  the case's AI policy version before retrieval, before every model request and in the transaction
  that stores the result; a failed check stops the run without storing output. Retrieval, read tools
  and citation lookups always filter by case id in SQL.
- **Local-only processing:** new cases are local-only. A cloud request for such a case is refused
  by the API, and each model call needs a processing grant issued immediately before it for the
  requested location. There is no fallback between local and cloud. Embeddings are always local.
- **Untrusted content:** evidence text, earlier answers and imported material are placed in
  per-request, randomly named data blocks, and text that imitates those delimiters is neutralized.
  The instructions tell the model to treat them as data, but the protection does not depend on the
  model complying: the model has no write, network, collection or shell capability. It can only
  select registered read tools whose arguments are validated against strict schemas and executed as
  fixed, parameterized queries in a read-only transaction scoped to the case. The model never writes
  SQL.
- **Output validation:** every evidence citation must reference a passage that was actually given
  to the model and contain a quote found in it; the stored citation records the exact offsets or
  JSON pointer and the evidence hash. Unsupported factual claims and count claims whose numbers are
  not in the cited database result are removed. Configured secret values are redacted from answers.
  Relationship suggestions may only link existing entities with a verified quote and are stored as
  `unreviewed`.
- **Display:** answers are rendered as plain text (no HTML or Markdown). Opening a citation
  re-reads and re-hashes the original evidence; changed or deleted sources are reported instead of
  shown. Internal instructions, stack traces and credentials are not returned to the browser;
  provider error messages are shortened to 200 characters.
- **Credentials:** the cloud API key is a secret file mounted into `api` and `ai-worker` only; it is
  never stored in the database, returned by the API or logged. Model endpoint URLs must not contain
  credentials or query strings, and the cloud endpoint must use HTTPS.
- **Logs and records:** routine logs contain run and case id prefixes, stages and error codes, not
  prompts, questions, evidence text or answers. Questions, answers, retrieval summaries and read-tool
  results are stored in PostgreSQL as case records and are removed with the case.
- **Limits:** context size, output tokens, retrieved passages, tool calls, retries, active runs per
  case, request timeouts and run leases are bounded by configuration.

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

No telemetry (Next.js telemetry is disabled). The only runtime outbound requests are model
requests from `ai-worker` to the configured Ollama address and, for cases an analyst has explicitly
allowed, the configured cloud provider. The optional API docs (`TRACEHOLLOW_API_DOCS_ENABLED=true`)
load Swagger UI assets from a public CDN.

## Known limitations

- No TLS termination is included. Serve over HTTPS before exposing Tracehollow beyond loopback,
  and update the origin and host settings.
- The Next.js CSP allows `'unsafe-inline'` scripts because nonce-based CSP is not configured yet.
- Single administrator only; no multi-factor authentication and no audit-event table yet. Review
  decisions on relationships are recorded, but other changes are not audited.
- Deleting a case does not remove it from earlier backups, exports or host-level volume snapshots.
- Evidence files are stored unencrypted on the Docker volume, and exports are not redacted.
- Evidence import validation rejects binary content but does not scan text for malware; open
  downloaded evidence with the same care as any untrusted file.
- Docker volumes and backups are not encrypted by Tracehollow. Use full-disk encryption and store
  backups and `secrets/` in protected locations.
- Secret files are mounted readable inside the service containers that need them.
- Dependency and container vulnerability scanning is not yet automated in CI.
- AI (Phase 3): delimiting untrusted text and validating citations limit, but do not eliminate,
  the effect of hostile evidence on answer wording; a model can still be steered into misleading
  but citation-backed claims. Analysts must review answers against the cited passages.
- AI: the Ollama API has no authentication and traffic to it is unencrypted HTTP; run it on the
  same host or a trusted network. The cloud integration has not been tested against the live API.
- AI: a cancel request cannot interrupt a model request already in progress; the output is
  discarded when it returns. Answers that cited deleted evidence keep their generated claim text.

Operational guidance: [docs/operations/secrets.md](docs/operations/secrets.md) and
[docs/operations/backup-restore.md](docs/operations/backup-restore.md).
