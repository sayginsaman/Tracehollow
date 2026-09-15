# Contributing to Tracehollow

Thank you for helping. Tracehollow handles investigation material, so correctness, honesty about
capabilities and security matter more than speed.

## Before you start

1. Read [PRD.md](PRD.md) (scope, phases, acceptance criteria), [AGENTS.md](AGENTS.md)
   (engineering and security rules) and [docs/STATUS.md](docs/STATUS.md) (what is actually done).
2. Work within the current phase. Do not add speculative modules, connectors or UI for later phases.
3. Never commit secrets, `.env`, `secrets/`, backups, real investigation data or real personal data.
   Test fixtures must be synthetic or redistributable; include Turkish text where text handling
   matters.

## Development environment

Requirements are listed in the [README](README.md#requirements). Initial setup:

```bash
scripts/setup.sh
docker compose up --build --detach --wait
(cd services/api && uv sync --locked)
(cd apps/web && npx --yes pnpm@12.4.1 install --frozen-lockfile)
```

`corepack pnpm` works as well with corepack 0.36 or newer (bundled with Node.js 24.21).

## Checks to run

Run the checks that cover your change; run everything before opening a pull request. `make check`
runs all static checks, tests and builds.

| Area | Command |
| --- | --- |
| Backend lint and format | `cd services/api && uv run ruff check . && uv run ruff format --check .` |
| Backend types | `cd services/api && uv run mypy` |
| Backend tests | `scripts/test-backend.sh` (starts ephemeral PostgreSQL and Redis containers) |
| Frontend lint | `cd apps/web && pnpm lint` |
| Frontend types | `cd apps/web && pnpm typecheck` |
| Frontend tests | `cd apps/web && pnpm test` |
| Frontend build | `cd apps/web && pnpm build` |
| Compose configuration | `docker compose config --quiet` |
| Stack acceptance | `scripts/verify-phase0.sh`, `scripts/verify-phase1.sh`, `scripts/verify-phase2.sh` and `scripts/verify-phase3.sh` (isolated projects on ports 3100/8100, run one at a time) |
| Browser workflow | `--e2e` on the Phase 1, 2 and 3 verifiers, or `pnpm e2e` against a running stack (see `apps/web/e2e/README.md`) |
| Connector contract tests | part of `scripts/test-backend.sh` (`tests/test_connector_contracts.py`, `test_netguard.py`, `test_collection.py`) |
| AI evaluation (deterministic) | part of `scripts/test-backend.sh` (`tests/test_ai_evaluation.py`, synthetic fixture provider) |
| AI evaluation (local model, opt-in) | `scripts/ai-eval.sh --providers configured` (needs Ollama and the models; see [docs/testing/ai-evaluation](docs/testing/ai-evaluation/README.md)) |
| Local model stack check (opt-in) | `scripts/verify-phase3.sh --model` |

Pass extra pytest arguments through the script, e.g. `scripts/test-backend.sh -k auth -x`.

## Testing expectations

- Test behaviour and boundaries, not implementation details or decorative text.
- Persistence, migrations and queue behaviour are tested against real PostgreSQL and Redis.
  Integration tests are marked `integration`; the test harness creates and drops uniquely named
  databases and never touches an application database.
- CI must stay deterministic: no network calls to real services, no paid APIs, no private accounts.
  Opt-in live smoke tests will be separate and clearly labelled once connectors exist.
- Connectors are tested with mock transports, recorded engine output and local fixture servers; CI
  never contacts a real source. Follow [docs/connectors/README.md](docs/connectors/README.md) for the
  contract, required outcome tests and the live smoke procedure; never mark a connector live-verified
  from fixture results.
- Use the synthetic fixture connector (`synthetic.fixture`) for execution tests. Its scenarios cover
  findings, no findings, partial coverage, failures, retries, rate limits, authentication and
  access errors, parse errors and slow runs; never point tests at real accounts or domains.
- Background work is tested for duplicate delivery, lost messages and interrupted workers, not only
  the happy path. Use the `ExecutionContext` hooks in `app/queries/execution.py` instead of sleeping.
- AI tests use the synthetic fixture providers or scripted providers from `tests/ai_helpers.py`;
  never call a model or a cloud API from the default test suite. Keep three kinds of AI checks
  separate and labelled: deterministic tests (CI), model-backed evaluation runs (opt-in, recorded
  with model names and digests) and live cloud checks (not run yet). Automated or model-based scoring
  is never reported as human review.
- Changing a prompt template, read tool or validation rule: bump the template version in
  `app/ai/prompts.py`, rerun the deterministic evaluation and, when possible, a model-backed run, and
  record the results.
- Never weaken authentication, CSRF, origin checks or tests to make a check pass.
- A mocked integration does not prove live compatibility; say so in docs and status.

## Database migrations

Schema changes go through Alembic only (no runtime `create_all`).

Autogenerate against a throwaway database at the current head (the ephemeral test services):

```bash
export TEST_POSTGRES_PASSWORD="$(openssl rand -hex 24)" TEST_REDIS_PASSWORD="$(openssl rand -hex 24)"
docker compose -f compose.test.yaml up --detach --wait
export TRACEHOLLOW_DATABASE_HOST=127.0.0.1 \
  TRACEHOLLOW_DATABASE_PORT="$(docker compose -f compose.test.yaml port postgres 5432 | awk -F: '{print $NF}')" \
  TRACEHOLLOW_DATABASE_USER=tracehollow_test TRACEHOLLOW_DATABASE_NAME=tracehollow_test \
  TRACEHOLLOW_DATABASE_PASSWORD="$TEST_POSTGRES_PASSWORD" TRACEHOLLOW_REDIS_PASSWORD="$TEST_REDIS_PASSWORD" \
  TRACEHOLLOW_SECRET_KEY="$(openssl rand -hex 32)"
cd services/api
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe the change"
cd ../.. && docker compose -f compose.test.yaml down --volumes
```

Always review and edit generated migrations, keep them reversible where practical, and extend
`services/api/tests/test_migrations.py`. The `migrate` service applies migrations on the next
`docker compose up`.

## Dependencies

- Pin exact versions. Commit `services/api/uv.lock` and `apps/web/pnpm-lock.yaml`.
- Check compatibility against primary sources (release notes, peer dependencies) before upgrading,
  and record notable choices in an ADR under `docs/adr/` and in `docs/STATUS.md`.
- Container images must use explicit versions with digests, never `latest`.
- New dependency build scripts must be explicitly approved in `apps/web/pnpm-workspace.yaml`.

## Code conventions

- Python: ruff (lint and format), mypy strict, `Annotated` FastAPI dependencies, sync SQLAlchemy 2
  sessions, settings from `app.config`. Never log secrets or case content.
- TypeScript: strict mode, ESLint (`eslint-config-next`), no secrets in client code, render only
  real backend state, accessible labels and keyboard support, status never conveyed by colour alone.
- Shell: `set -euo pipefail`, quote variables, never interpolate user input into commands.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat(api): ...`,
  `fix(web): ...`, `docs: ...`, `ci: ...`). Explain *why* in the body when it is not obvious.
- Keep pull requests focused. Describe what changed, how it was verified (commands and results) and
  what remains unverified.
- Update `docs/STATUS.md` when a requirement changes state, and the README when commands change.

## Reporting security issues

Do not open public issues for vulnerabilities. Follow [SECURITY.md](SECURITY.md).
