# ADR 0001: Phase 0 technology baseline and version selection

- Status: accepted
- Date: 2026-09-15
- Phase: 0

## Context

PRD §8 fixes the default stack (Next.js/TypeScript/Tailwind, FastAPI/Pydantic/SQLAlchemy/Alembic/
psycopg, PostgreSQL with pgvector, Celery with Redis, Docker Compose, uv and one JavaScript package
manager) and requires currently supported, compatible, pinned versions. Several ecosystems shipped
new major versions shortly before this baseline, so "latest" was not automatically compatible.

Versions were checked on 2026-09-15 against npm and PyPI metadata (versions, peer dependencies,
classifiers, wheel availability), release notes (Next.js 16 upgrade guide, pnpm 11/12, uv 0.12,
Starlette 1.0, Celery changelog), the Node.js release index and Docker Hub/GHCR tags.

## Decision

| Component | Selected | Reason for deviating from the newest release, if any |
| --- | --- | --- |
| Node.js runtime | 24.21.0 (LTS "Krypton") | Node 26 is "Current", not LTS, until October 2026 |
| Next.js / React | 16.3.5 / 19.3.0 | — |
| TypeScript | 6.0.3 | TypeScript 7.0 is incompatible with `typescript-eslint` (peer `<6.1.0`), which `eslint-config-next` requires |
| ESLint | 9.39.5 | ESLint 10 is outside the peer ranges of `eslint-plugin-react`, `eslint-plugin-import` and `eslint-plugin-jsx-a11y` used by `eslint-config-next` 16.3.5 |
| Tailwind CSS | 4.3.3 | — |
| Vitest / Vite / jsdom | 5.0.0 / 8.3.0 / 30.0.1 | — |
| pnpm | 12.4.1 | Requires corepack ≥ 0.36 (bundled with Node 24.21); older corepack cannot launch pnpm's ESM build, so `npx pnpm@12.4.1` is documented as a fallback |
| Python | 3.13.15 | Celery 5.6 declares only "initial" Python 3.14 support and its classifiers stop at 3.13 |
| FastAPI / Starlette | 0.141.1 / 1.6.0 | — |
| Pydantic / pydantic-settings | 2.13.5 / 2.15.0 | — |
| SQLAlchemy / Alembic / psycopg | 2.0.53 / 1.20.0 / 3.3.5 | — |
| Celery / kombu | 5.6.3 / 5.6.2 | — |
| redis-py | 6.4.0 | `kombu[redis]` 5.6.2 requires `redis<6.5`, so redis-py 8.x cannot be installed alongside Celery |
| argon2-cffi | 25.1.0 | — |
| uvicorn | 0.53.0 | — |
| uv | 0.12.13 in images and CI | 0.12.14 was published hours before the baseline; the previous patch was chosen |
| PostgreSQL image | `pgvector/pgvector:0.8.6-pg18-trixie` (PostgreSQL 18.6) | pgvector image chosen now so Phase 3 does not require a data-volume image migration; the extension is not created in Phase 0 |
| Redis image | `redis:8.10.1-trixie` | — |
| Test tooling | pytest 9.1.1, httpx2 2.13.0, ruff 0.16.7, mypy 2.3.1 | Starlette 1.x deprecates `httpx` in its test client in favour of `httpx2` |

Every image reference includes a multi-architecture index digest. GitHub Actions are pinned to
commit SHAs.

Further structural choices:

- **Separate projects instead of a JavaScript monorepo workspace.** `apps/web` is a standalone pnpm
  project with its own lockfile; `services/api` is an unpackaged uv project. This keeps Docker build
  contexts small. A root workspace can be introduced when `packages/contracts` has real content.
- **Synchronous SQLAlchemy in FastAPI and Celery.** FastAPI runs sync endpoints in a thread pool,
  which is adequate for a single-user local tool and lets the API and worker share one session
  model. Streaming endpoints in later phases can be async individually.
- **One image for API, worker and migrations**, with different commands (AGENTS.md).
- **Runtime-configured same-origin proxy (route handler) instead of `next.config` rewrites.**
  `next.config` is serialized into the standalone server at build time, so rewrites would freeze
  the API URL into the image.
- **Checked-in `apps/web/AGENTS.md`** containing the managed block that `next dev` writes when an
  AI coding agent is detected, so development does not leave uncommitted changes. It points back to
  the root `AGENTS.md`.

## Alternatives considered

- TypeScript 7, ESLint 10, Python 3.14, redis-py 8: rejected for the compatibility reasons above.
- Valkey instead of Redis: the PRD names Redis; Redis 8 is used unmodified.
- Async SQLAlchemy: more plumbing in Phase 0 for no measurable benefit.

## Consequences

- Upgrading TypeScript to 7 or ESLint to 10 waits for `typescript-eslint` and the React/import/a11y
  plugins to support them.
- Moving to Python 3.14 waits for Celery to declare full support.
- Pinned digests must be refreshed deliberately; no automated update tooling is configured yet.

## Verification

Lockfiles (`services/api/uv.lock`, `apps/web/pnpm-lock.yaml`) resolve and install with
`uv sync --locked` and `pnpm install --frozen-lockfile`; images build from the pinned digests; the
full check suite and `scripts/verify-phase0.sh` pass (see `docs/STATUS.md`).
