# ADR 0003: Compose topology, secrets delivery and least privilege

- Status: accepted
- Date: 2026-09-15
- Phase: 0

## Context

PRD §8 and §11 require core services web, api, worker, postgres and redis; loopback bindings for
user-facing ports; no published PostgreSQL or Redis ports; generated secrets with no shared defaults;
data that persists independently of disposable containers; readiness that reflects dependencies;
worker health reported separately; and least privilege where practical.

## Decision

- **Services:** `web`, `api`, `worker`, `postgres`, `redis`, plus a one-shot `migrate` service that
  runs `alembic upgrade head` from the API image. `api` and `worker` wait until it completes
  successfully. An Alembic advisory lock serializes concurrent migration runs.
- **Networks:** `edge` (web and api) and `data`, marked `internal: true` (api, worker, migrate,
  postgres, redis). Only web and api publish ports, on `127.0.0.1` by default. The worker has no
  external network access in Phase 0; collection phases will add a deliberate egress network.
- **Volumes:** named volumes `postgres-data` (mounted at `/var/lib/postgresql`, as PostgreSQL 18
  images expect), `redis-data` (AOF enabled) and `evidence-data`.
- **Secrets:** Compose file secrets under `/run/secrets`, consumed through `TRACEHOLLOW_*_FILE`
  settings and `POSTGRES_PASSWORD_FILE`. The `secrets/` directory is `0700` on the host. Files are
  `0644` so non-root container users can read the bind mount on Linux hosts; the private directory
  keeps them from other host users. Redis loads an ACL file containing only a SHA-256 password
  digest. The healthcheck passes the password through `REDISCLI_AUTH`, not command-line arguments.
- **Database roles:** a first-start init script creates `tracehollow_app` (no superuser, no
  CREATEDB, no CREATEROLE, no replication) as owner of the `tracehollow` database. The password is
  read inside psql from the secret file. The superuser is used only for initialisation, backups and
  operator tasks.
- **Container hardening:** app containers run as UID 10001 with `read_only: true`, tmpfs for
  `/tmp` (and the Next.js cache), `cap_drop: [ALL]` and `no-new-privileges`. PostgreSQL and Redis
  keep their entrypoint capabilities (needed to drop to their service users) but set
  `no-new-privileges`.
- **Images:** built locally with `docker compose up --build`. No `pull_policy: build`, so a plain
  `docker compose up` reuses existing images and does not recreate healthy containers.
- **Health:** container healthchecks use liveness for api and web, `pg_isready` and authenticated
  `PING` for the data services, and a Celery `inspect ping` addressed to the container's own worker.
  API readiness (`/api/health/ready`) checks database connectivity, the migration revision, Redis
  and a write probe on evidence storage. It returns `503` with check states only; details require
  authentication. Worker status comes from a broker ping and a persisted round-trip task, never from
  API readiness.

## Alternatives considered

- **Secrets in `.env` or environment variables:** visible through `docker inspect` and inherited by
  child processes, which matters once connectors run subprocesses. Rejected.
- **Migrations on API startup:** races when several API processes start and hides migration
  failures inside API restarts. Rejected in favour of the one-shot job.
- **Superuser application connection:** simpler, but turns SQL injection into server-level
  compromise (for example `COPY ... PROGRAM`). Rejected.
- **Publishing PostgreSQL and Redis for development convenience:** contradicts the PRD. Backend
  tests use a separate, tmpfs-backed `compose.test.yaml` project instead.

## Consequences

- The init script runs only when the data volume is empty. Changing `postgres_app_password` later
  requires `ALTER ROLE` (documented in `docs/operations/secrets.md`).
- Running the API outside Docker needs a reachable database, and PostgreSQL is intentionally not
  published. Contributors use the containerised API or the ephemeral test services.
- Collection phases must add explicit egress for the worker and revisit SSRF controls.

## Verification

`scripts/verify-phase0.sh` checks that postgres and redis have no host port bindings, that api and
web bind only to 127.0.0.1, fresh-database migrations, safe repeated startup, readiness under Redis
and PostgreSQL outages, persistence across `down`/`up`, the absence of generated secrets and the
administrator password in service logs, and a backup restore drill.
