# Tracehollow

Tracehollow is an open-source, self-hosted OSINT investigation workspace that runs locally with
Docker Compose. The product goal — cases, evidence with provenance, modular public-source
collection and evidence-grounded AI — is specified in [PRD.md](PRD.md).

> **Project status: Phase 0 (foundation).** This build provides a secure, runnable base:
> first-run administrator setup, sign-in/sign-out, and live dependency and worker health checks.
> **Cases, collection connectors, evidence handling, graphs and AI are not implemented yet.**
> See [docs/STATUS.md](docs/STATUS.md) for verified progress and known limitations.

## What works today

- `docker compose` stack: `web` (Next.js), `api` (FastAPI), `worker` (Celery), `postgres`
  (PostgreSQL 18 with the pgvector image), `redis` (Redis 8 broker) and a one-shot `migrate` job.
- Secure first-run setup protected by a locally generated setup token; no default password.
- Local administrator sign-in with server-side sessions, CSRF protection and logout that
  invalidates the session on the server.
- Liveness and readiness endpoints; readiness fails when PostgreSQL, migrations, Redis or the
  evidence volume are unavailable. Worker health is reported separately.
- A broker-to-worker connectivity check whose result is stored in PostgreSQL. It collects no data.
- Backup, restore drill and full restore scripts for PostgreSQL and the evidence volume.

## Requirements

| Purpose | Requirement |
| --- | --- |
| Run the stack | Docker Engine or Docker Desktop with Compose v2 (verified with Docker 29.8.0, Compose v5.5.1) |
| Setup script | `bash`, and `openssl` or `/dev/urandom` |
| Verification scripts | `python3` (3.9 or newer, standard library only) |
| Backend development | [uv](https://docs.astral.sh/uv/) 0.11.12 or newer (installs Python 3.13 if needed) |
| Frontend development | Node.js 24.15 or newer and pnpm 12.4.1 (`corepack` 0.36+, or `npx pnpm@12.4.1`) |

No paid API, cloud account or language model is required.

## Quick start

```bash
git clone https://github.com/sayginsaman/Tracehollow.git
cd Tracehollow

# 1. Create .env and generate local secrets in secrets/ (safe to re-run; never overwrites).
scripts/setup.sh

# 2. Build and start the core services and wait until they are healthy.
docker compose up --build --detach --wait

# 3. Show the one-time setup token.
cat secrets/bootstrap_token
```

Open <http://localhost:3000>. You are redirected to **Create the administrator**; paste the setup
token, choose a username and a password of at least 12 characters, then sign in. The status page
shows API dependency checks, worker health and the connectivity check.

Stop the stack with `docker compose down`. Data stays in Docker volumes; **do not** add `--volumes`
(`-v`) unless you intend to delete all local data.

## Services and ports

| Service | Purpose | Published on host |
| --- | --- | --- |
| `web` | Next.js UI and same-origin API proxy | `127.0.0.1:3000` |
| `api` | FastAPI application | `127.0.0.1:8000` |
| `worker` | Celery worker (reuses the API image) | none |
| `migrate` | Runs `alembic upgrade head`, then exits | none |
| `postgres` | System of record | none (internal `data` network only) |
| `redis` | Celery broker | none (internal `data` network only) |

Ports and bind addresses are configured in `.env`. Binding to anything other than loopback exposes
the sign-in page to your network; see [SECURITY.md](SECURITY.md) first.

Persistent volumes: `postgres-data`, `redis-data` and `evidence-data` (prefixed with the Compose
project name, `tracehollow_` by default).

## Health and status endpoints

| Endpoint | Auth | Meaning |
| --- | --- | --- |
| `GET /api/health/live` | no | API process is running. Does not contact dependencies. |
| `GET /api/health/ready` | no | `200` when database, migrations, Redis and evidence storage are OK; otherwise `503`. Reports only check names and states. |
| `GET /healthz` (web) | no | Web server process is running. |
| `GET /api/v1/system/status` | session | Readiness checks with safe detail text and API version. |
| `GET /api/v1/system/worker` | session | `online`, `offline` or `broker_unavailable` from a broker ping. |
| `POST /api/v1/system/worker-checks` | session + CSRF | Queues the connectivity task; poll `GET /api/v1/system/worker-checks/{id}`. |

API readiness never implies worker health. Example:

```bash
curl -s http://127.0.0.1:8000/api/health/ready
```

## Configuration

Non-secret settings live in `.env` (copied from [.env.example](.env.example)):

| Variable | Default | Notes |
| --- | --- | --- |
| `TRACEHOLLOW_WEB_BIND_ADDRESS` / `TRACEHOLLOW_WEB_PORT` | `127.0.0.1` / `3000` | Published web address |
| `TRACEHOLLOW_API_BIND_ADDRESS` / `TRACEHOLLOW_API_PORT` | `127.0.0.1` / `8000` | Published API address |
| `TRACEHOLLOW_PUBLIC_ORIGIN` | `http://localhost:<web port>` | `https://` origins enable `Secure` cookies |
| `TRACEHOLLOW_TRUSTED_ORIGINS` | localhost and 127.0.0.1 on the web port | Origins allowed to send state-changing requests |
| `TRACEHOLLOW_API_ALLOWED_HOSTS` / `TRACEHOLLOW_WEB_ALLOWED_HOSTS` | loopback names | Host header allowlists (DNS-rebinding protection) |
| `TRACEHOLLOW_SESSION_IDLE_TIMEOUT_MINUTES` | `480` | Idle session timeout |
| `TRACEHOLLOW_SESSION_ABSOLUTE_TIMEOUT_HOURS` | `24` | Maximum session lifetime |
| `TRACEHOLLOW_LOG_LEVEL` | `INFO` | JSON logs with secret redaction |
| `TRACEHOLLOW_API_DOCS_ENABLED` | `false` | Swagger UI at `/api/docs`; loads assets from a public CDN |

Secrets are generated by `scripts/setup.sh` into `secrets/` (directory mode `0700`, git-ignored)
and mounted into containers as files, never as environment variables:

| File | Used by |
| --- | --- |
| `postgres_superuser_password` | PostgreSQL superuser (initialisation, backups, operators) |
| `postgres_app_password` | Non-superuser `tracehollow_app` role used by api, worker and migrate |
| `redis_password`, `redis_users.acl` | Redis authentication (the ACL file stores only a SHA-256 digest) |
| `app_secret_key` | HMAC key for CSRF tokens |
| `bootstrap_token` | One-time web setup; ignored once an administrator exists |

Invalid configuration stops the API with a message naming the problem but never the value.
Rotation procedures: [docs/operations/secrets.md](docs/operations/secrets.md).

## Administration

```bash
# Validate configuration inside the API container
docker compose exec api python -m app.cli check-config

# Create the first administrator without the web form (prompts for the password)
docker compose exec api python -m app.cli create-admin --username <name>

# Forgotten password or lockout: set a new password and revoke that user's sessions
docker compose exec api python -m app.cli reset-password --username <name>
```

After five consecutive failed sign-ins an account is locked temporarily (30 seconds, doubling up
to 15 minutes). `reset-password` clears the lock.

## Development

All commands below are also available as `make` targets (`make help`).

```bash
# Backend (services/api)
cd services/api
uv sync --locked                 # install locked dependencies
uv run ruff check . && uv run ruff format --check .
uv run mypy
cd ../..
scripts/test-backend.sh          # pytest against ephemeral PostgreSQL and Redis containers

# Frontend (apps/web) — use `corepack pnpm` (corepack 0.36+) or `npx --yes pnpm@12.4.1`
cd apps/web
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build

# Whole stack (repository root)
docker compose config --quiet    # validate Compose configuration
docker compose up --build --detach --wait
scripts/verify-phase0.sh         # isolated end-to-end acceptance run (ports 3100/8100), cleans up after itself
```

Code changes to `services/api` or `apps/web` are picked up by `docker compose up --build`.
Migrations run automatically through the `migrate` service; to create a new one, see
[CONTRIBUTING.md](CONTRIBUTING.md#database-migrations).

For frontend iteration with hot reload, stop the `web` container (`docker compose stop web`) and
run `pnpm dev` in `apps/web`; it listens on `127.0.0.1:3000` and proxies to the API published on
`127.0.0.1:8000`.

## Backups

```bash
scripts/backup.sh                                   # writes backups/<UTC timestamp>/
scripts/restore.sh backups/<timestamp> --verify-only # non-destructive restore drill
```

Secrets are not part of backups and must be protected separately. Full procedures, including
restoring onto a new machine: [docs/operations/backup-restore.md](docs/operations/backup-restore.md).

## Architecture

```text
Browser ──HTTP──▶ web (Next.js, 127.0.0.1:3000)
                   │  same-origin /api/* proxy (header allowlist, Host check)
                   ▼
                 api (FastAPI, 127.0.0.1:8000) ──▶ postgres (system of record)
                   │                         └──▶ evidence-data volume
                   ▼ Celery messages
                 redis (broker only) ──▶ worker (Celery) ──▶ postgres
```

The browser only talks to the web origin, so the session cookie is first-party and no CORS is
enabled. PostgreSQL is authoritative; Redis carries messages only. Design decisions are recorded in
[docs/adr](docs/adr).

## Repository layout

```text
apps/web/             Next.js application (pnpm project with its own lockfile)
services/api/         FastAPI app, Celery tasks, Alembic migrations, pytest suite (uv project)
docker/postgres/      PostgreSQL first-start initialisation (least-privilege application role)
scripts/              setup, tests, smoke/verification, backup and restore
docs/                 STATUS, ADRs and operations guides
compose.yaml          Core services
compose.test.yaml     Ephemeral PostgreSQL/Redis used by backend tests
```

## Troubleshooting

- **Port already in use:** change `TRACEHOLLOW_WEB_PORT` or `TRACEHOLLOW_API_PORT` in `.env`. The
  trusted origin defaults follow the web port automatically.
- **"The setup token is not valid":** copy it again with `cat secrets/bootstrap_token` on the
  machine running the stack. The token is only accepted until the first administrator exists.
- **Status page shows "Not ready":** run `docker compose ps` and `docker compose logs <service>`.
  `migrations_pending` means the `migrate` service did not complete.
- **Worker offline:** `docker compose logs worker`. The API can be ready while no worker runs.
- **`secrets/...` not found when starting Compose:** run `scripts/setup.sh` first.
- **Changed a secret file and services fail to authenticate:** follow
  [docs/operations/secrets.md](docs/operations/secrets.md); PostgreSQL passwords are stored in the
  database and must be changed there as well.

## Privacy and network behaviour

The running application sends no telemetry and makes no outbound requests. Next.js telemetry is
disabled in the images, fonts are system fonts, and no third-party assets are loaded (except Swagger
UI assets when `TRACEHOLLOW_API_DOCS_ENABLED=true`). Building images downloads base images and
packages from Docker Hub, GitHub Container Registry, PyPI and npm.

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md) — workflow, commands, tests and conventions.
- [SECURITY.md](SECURITY.md) — reporting vulnerabilities and the security model.
- [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) — instructions for AI coding agents.

## License

Released under the [MIT License](LICENSE). Third-party components keep their own licenses; a full
dependency license inventory is planned for the release-readiness phase.
