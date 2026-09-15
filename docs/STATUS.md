# Implementation status

- **Requested phase:** Phase 0 — Repository and secure local foundation
- **Overall Phase 0 status:** verified. All eight acceptance criteria were verified locally on
  2026-09-15; see the evidence below. The GitHub Actions workflow has not run yet because nothing has
  been pushed.
- **Next phase:** Phase 1, not started. See [Next bounded task](#next-bounded-task).
- **Branch:** `feat/phase-0-foundation`, based on `origin/main` (which contains the owner's MIT
  `LICENSE`). Local `master`, which holds only an unrelated empty initial commit, was left untouched.
  Nothing has been pushed.

Status vocabulary: `not started`, `in progress`, `verified`, `blocked`.

## Verification environment

| Item | Value |
| --- | --- |
| Date | 2026-09-15 |
| Host | macOS (Darwin 25.5.0), Apple Silicon (arm64) |
| Docker | Docker Desktop, Engine 29.8.0, Compose v5.5.1 |
| Host tools | uv 0.11.12, Python 3.13.3 (backend venv), Node.js 26.5.0, pnpm 12.4.1 via `npx` |
| Container toolchain | Python 3.13.15, uv 0.12.13, Node.js 24.21.0 (corepack 0.36.0, pnpm 12.4.1) |

## Acceptance checklist (PRD §12, Phase 0)

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| AC1 | Documented setup starts core services without paid APIs or an LLM | verified | `scripts/setup.sh` then `docker compose up --build --detach --wait`: web, api, worker, postgres and redis healthy and migrate exited 0, about 24 s on a warm build cache. No external API keys or models configured. |
| AC2 | Fresh-database migrations run; repeated startup is safe and non-destructive | verified | Migrate log shows `Running upgrade  -> 0001` on an empty volume. A second `docker compose up --detach --wait` re-ran migrate as a no-op, kept container IDs and row counts. pytest `test_migrations.py`: upgrade → downgrade → upgrade on a fresh DB; repeated upgrade keeps data. `scripts/setup.sh` second run kept every file. |
| AC3 | Valid login succeeds; invalid fails; protected routes reject unauthenticated access; logout invalidates the session | verified | `scripts/smoke_test.py` via the web proxy: wrong password and unknown user → 401 with no cookie; valid login → 200 with HttpOnly SameSite=Strict cookie; 5 protected routes → 401 (web proxy) and direct API → 401; logout → 204; replayed old cookie → 401. pytest `test_auth.py` (23 tests) also covers CSRF, cross-origin/Host rejection, lockout, expiry and rotation. Browser check: invalid-login message, redirect to status, sign-out, `/status` protected again. |
| AC4 | Readiness changes when a required dependency is unavailable | verified | `docker compose stop redis` → `/api/health/ready` HTTP 503 `redis: unavailable`, liveness 200; restart → 200. `docker compose stop postgres` → 503 `database: unavailable, migrations: unavailable`; restart → 200. pytest `test_health.py` also covers pending migrations, unwritable storage and `503 database_unavailable` for unauthenticated routes during a database outage. |
| AC5 | Minimal background task verifies broker-to-worker connectivity without pretending to be an OSINT run | verified | `POST /api/v1/system/worker-checks` → 202; the worker marked it `completed` in PostgreSQL (for example `worker@e5cb21637271`, 12–66 ms in the UI). Worker ping reports `online`. The worker reconnected automatically after the Redis restart. pytest `test_worker.py`: real Celery worker round trip, offline status, dispatch failure → `dispatch_failed` + 503, idempotent duplicate delivery. |
| AC6 | A database record persists across normal Compose down/up without volume deletion | verified | Before `docker compose down`: users=1, worker_checks=2. Volumes remained. After `up`: identical counts; `setup_required=false`; the same credentials signed in; previous worker checks listed. |
| AC7 | Service bindings, secret handling and health information match documentation | verified | `docker inspect`: postgres and redis have no host ports; api `127.0.0.1:8000` (8100 in verification), web `127.0.0.1:3000` (3100). `lsof` shows listeners only on 127.0.0.1. App connects as non-superuser `tracehollow_app` (DB owner). Secrets mounted as files; `.env` 0600, `secrets/` 0700, both git-ignored. Service logs scanned: no generated secret, admin password or session cookie value present (193 lines); broker URL logged as `redis://[REDACTED]@redis`. Readiness returns check names and states only (pytest asserts no host, port or password). |
| AC8 | Backend/frontend checks, production build and Compose validation pass; unrun checks recorded | verified (local) | See [Commands and results](#commands-and-results). GitHub Actions CI: **not run** (no push authorized). |

## Deliverables and locations

| Deliverable | Status | Location |
| --- | --- | --- |
| Next.js/TypeScript frontend: setup, login, status | verified | `apps/web/src/app/{setup,login,status}`, `apps/web/src/components` |
| Same-origin API proxy with Host and header allowlists | verified | `apps/web/src/app/api/[...path]/route.ts`, `apps/web/src/lib/proxy.ts` |
| FastAPI app, config validation, redacted JSON logging | verified | `services/api/app/{main,config,logging_config,security_middleware}.py` |
| PostgreSQL models and Alembic baseline | verified | `services/api/app/{auth,system}/models.py`, `services/api/migrations/versions/20260915_0001_*.py` |
| Admin bootstrap, sessions, CSRF, lockout, CLI | verified | `services/api/app/auth/`, `services/api/app/deps.py`, `services/api/app/cli.py` |
| Liveness, readiness, status, worker ping and worker check | verified | `services/api/app/health/`, `services/api/app/system/router.py` |
| Celery worker and task | verified | `services/api/app/tasks/` |
| Docker Compose stack with volumes, internal network and hardening | verified | `compose.yaml`, `services/api/Dockerfile`, `apps/web/Dockerfile`, `docker/postgres/initdb/` |
| Secure, idempotent secret generation | verified | `scripts/setup.sh`, `.env.example` |
| Pinned dependencies and lockfiles | verified | `services/api/pyproject.toml` + `uv.lock`, `apps/web/package.json` + `pnpm-lock.yaml`, image digests |
| Initial CI | in progress (written, not yet run on GitHub) | `.github/workflows/ci.yml` |
| Backup, restore drill, full restore | verified | `scripts/backup.sh`, `scripts/restore.sh`, `docs/operations/backup-restore.md` |
| Documentation | verified against the commands above | `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `docs/operations/`, `docs/adr/0001-0003` |
| Reproducible acceptance run | verified | `scripts/verify-phase0.sh`, `scripts/smoke_test.py` |

## Commands and results

All commands were run from the repository root unless noted.

| Command | Result |
| --- | --- |
| `cd services/api && uv sync --locked` | OK (55 packages) |
| `cd services/api && uv run ruff check .` | All checks passed |
| `cd services/api && uv run ruff format --check .` | 40 files already formatted |
| `cd services/api && uv run mypy` | Success: no issues found in 40 source files (strict) |
| `scripts/test-backend.sh` | **80 passed** in 8.83 s (real PostgreSQL 18.6 and Redis 8.10.1 in an ephemeral project) |
| `cd apps/web && pnpm install --frozen-lockfile` | Lockfile up to date |
| `cd apps/web && pnpm lint` | No findings |
| `cd apps/web && pnpm typecheck` | `next typegen` + `tsc --noEmit`: no errors |
| `cd apps/web && pnpm test` | **31 passed** (6 files) |
| `cd apps/web && pnpm build` | Compiled successfully; routes `/`, `/setup`, `/login`, `/status`, `/healthz`, `/api/[...path]` |
| `docker build services/api`, `docker build apps/web` | Both images built from pinned digests; run as UID 10001 |
| `docker compose config --quiet` | Valid |
| `scripts/verify-phase0.sh` | **All Phase 0 stack checks passed** on the final code (fresh isolated project `tracehollow-verify`, 66 s, project and volumes removed afterwards): configuration; startup; port bindings; fresh migrations; setup/auth/CSRF/worker/logout; repeated startup; Redis and PostgreSQL outages; down/up persistence; operator CLI (`check-config`, `create-admin` refusal, `reset-password` with old password rejected); backup and restore drill; secrets-in-logs scan |
| `scripts/restore.sh <backup> --yes-overwrite-current-data` (manual, verification project) | Restored worker_checks 6 → 5 (backup state), table owner `tracehollow_app`, evidence marker file restored, login worked afterwards |
| Browser check (headless Chromium via `playwright-core`, not committed) | Redirects for `/`, `/status`, `/setup`; invalid-login message; status dashboard with dependency table, worker online, check completed; cookie HttpOnly/SameSite=Strict and absent from `document.cookie`; sign-out; no `X-Powered-By`; CSP present. Only console error: the expected 401 from the deliberate wrong password. |
| `next dev` against the containerised API | Proxy readiness 200, `/` → 307 `/login`, rebinding Host → 421, no files modified |

## Selected versions

Recorded with rationale in [ADR 0001](adr/0001-phase-0-technology-baseline.md). Summary: Node.js
24.21.0 LTS, Next.js 16.3.5, React 19.3.0, TypeScript 6.0.3, ESLint 9.39.5, Tailwind CSS 4.3.3,
Vitest 5.0.0, pnpm 12.4.1; Python 3.13.15, FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.5,
SQLAlchemy 2.0.53, Alembic 1.20.0, psycopg 3.3.5, Celery 5.6.3, redis-py 6.4.0, argon2-cffi 25.1.0,
uvicorn 0.53.0, uv 0.12.13; PostgreSQL 18.6 (`pgvector/pgvector:0.8.6-pg18-trixie`), Redis 8.10.1.

Architecture decisions: [ADR 0002](adr/0002-local-authentication-and-sessions.md) (authentication,
sessions, CSRF) and [ADR 0003](adr/0003-deployment-topology-and-secrets.md) (topology, secrets,
least privilege).

## Unverified checks, blockers and known limitations

- **CI not executed:** `.github/workflows/ci.yml` has not run on GitHub; pushing was not authorized.
  Its jobs use the same commands verified locally, but runner-specific issues remain possible.
- **Platforms:** verified only on macOS with Docker Desktop. Linux Docker Engine (secret file
  permissions with non-root containers, rootless Docker) and Windows/WSL are untested.
- **Secret rotation** procedures in `docs/operations/secrets.md` are documented but not exercised.
- **Restore onto a new machine** with regenerated secrets is documented but not exercised; restore
  into the same installation and the restore drill were verified.
- **End-to-end browser tests** are not committed. The UI flow was verified with an uncommitted
  headless script; a Playwright suite should be added when Phase 1 UI flows exist.
- **Accessibility:** semantic labels, focus styles, skip link and text-plus-glyph status badges are
  implemented, but no audit (axe or screen reader) was run.
- **Security tooling:** no automated dependency or container vulnerability scanning yet; no MFA; no
  audit-event table; Next.js CSP allows `'unsafe-inline'` scripts; no TLS termination (loopback only).
- **Account lockout** can be triggered by anyone who can reach the login page; recovery is
  `reset-password`.
- **Owner decisions needed:**
  - License: the repository `LICENSE` is MIT, created by the owner on GitHub, while PRD §13 proposes
    Apache-2.0 subject to review. Docs follow the existing MIT license; PRD §13 should be updated or
    the license changed deliberately.
  - `SECURITY.md` relies on GitHub private vulnerability reporting, which must be enabled in the
    repository settings.
  - `PRD.md`, `AGENTS.md` and `CLAUDE.md` were committed verbatim. They contain paste artifacts
    (for example `` `docs/[STATUS.md](http://STATUS.md)` `` links, and table rows separated by blank
    lines that do not render as tables). They were not edited, to preserve the originals.
- **Graphify:** `graphify update .` (graphify 0.9.61) rebuilt the code graph after the final change: 813 nodes, 1691 edges, 47
  communities from 94 files. It indexed no `secrets/`, `.env`, dependency or build paths and no secret
  values. `graphify-out/` is git-ignored because generated graphs contain absolute local paths.

## Next bounded task

**Phase 1, first slice — case lifecycle and ownership.** Add `cases` and `case_members` through an
Alembic migration. Build authenticated API endpoints to create, list (paginated), rename, archive and
restore cases, with server-side ownership checks on every route and deliberate deletion deferred to
the deletion-job task. Build the case list and create UI with loading, empty and error states.
Include pytest coverage for permission boundaries and lifecycle transitions, plus Vitest coverage for
the UI. Evidence import, saved queries, the fixture connector and exports follow as separate Phase 1
tasks.
