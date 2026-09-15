# Implementation status

- **Requested phase:** Phase 1 — Cases, evidence and query lifecycle
- **Overall Phase 1 status:** verified. All seven PRD Phase 1 acceptance criteria and the eight
  requested verification scenarios were verified locally on 2026-09-15; see the evidence below.
  GitHub Actions has not run because nothing has been pushed.
- **Phase 0 status:** verified; its stack verification was re-run successfully on the final Phase 1
  code.
- **Next phase:** Phase 2, not started. See [Next bounded task](#next-bounded-task).
- **Branch:** `feat/phase-1-cases-evidence-queries`, on top of `feat/phase-0-foundation` (which is
  based on `origin/main`). Nothing has been pushed.

Status vocabulary: `not started`, `in progress`, `verified`, `blocked`.

## Verification environment

| Item | Value |
| --- | --- |
| Date | 2026-09-15 |
| Host | macOS (Darwin 25.5.0), Apple Silicon (arm64) |
| Docker | Docker Desktop, Engine 29.8.0, Compose v5.5.1 |
| Host tools | uv 0.11.12, Python 3.13.3 (backend venv), Python 3.14 (verification scripts), Node.js 26.5.0, pnpm 12.4.1 via `npx` |
| Container toolchain | Python 3.13.15, uv 0.12.13, Node.js 24.21.0 (corepack 0.36.0, pnpm 12.4.1) |
| Browser tests | `@playwright/test` 1.63.0 driving the locally installed Chromium headless shell build 1234 through `TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE` (Playwright 1.63 expects build 1243; see limitations) |

## Phase 0 prerequisite check

Before Phase 1 changes, `scripts/verify-phase0.sh` passed on the Phase 0 branch (authentication,
migrations, persistent volumes, web-to-API connectivity, Redis and worker round trip). Defects in
the foundation that blocked or endangered Phase 1, and their fixes:

| Defect | Effect | Fix |
| --- | --- | --- |
| API request bodies were capped at 64 KiB for every route | Evidence imports above 64 KiB were impossible | Per-path limit for the import route only, derived from `TRACEHOLLOW_EVIDENCE_MAX_IMPORT_BYTES` (`services/api/app/security_middleware.py`, commit 1c156f0) |
| Web proxy capped bodies at 1 MiB and dropped `Content-Disposition` | Uploads failed and downloads lost their attachment disposition | Upload limit only for the import route, header allowlist extended (commit 77b9d66) |
| Worker, dispatcher and CLI processes did not import every ORM model | Found on the real stack: the worker raised `NoReferencedTableError` and runs stayed `running` forever | Session factory registers all models; unexpected errors fail the run as `internal_error`; runs whose workers keep dying fail as `worker_lost` after bounded claims; fresh-interpreter regression test (commit a042063) |
| `verify-phase0.sh` expected exactly one migration in the log | Would fail once revision 0002 exists | Compares upgrade counts before and after the repeated startup (commit 4ddda09) |
| `restore.sh` did not stop the new dispatcher | Dispatcher could reconcile or republish during a restore | Stops `dispatcher` with web, api and worker (commit 4ddda09) |

## Phase 1 acceptance checklist (PRD §12)

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| AC1 | Create a case, import sample evidence, link entities, restart, reopen the same records | verified | `verify-phase1.sh` seed stage (case, 4 entities, 3 imports, evidence link, relationship with supporting evidence, review decision, note), then `docker compose down` and `up` without removing volumes: row counts and the 3 evidence files unchanged; reopen stage re-hashed the files (`verified`), downloaded original bytes through the web proxy with matching SHA-256, found links, review history and note intact. Browser: the same flow in `apps/web/e2e/phase1-workflow.spec.ts`. |
| AC2 | Two runs of one query remain independently addressable | verified | Lifecycle stage: run #1, edit the saved query, run #2. Both readable by id; run #1 keeps its snapshot (`sule.yilmaz`), finish time and 3 evidence records; run #2 captured `ilkay`; evidence sets are disjoint; history lists both. pytest `test_two_runs_are_independent_and_keep_snapshots`. Browser: "Run again" opens run #2 and run #1 still shows its own evidence. |
| AC3 | Duplicate delivery creates no duplicate logical result; queued work recovers after interruption | verified | Stack: (a) Redis stopped at run creation → outbox `pending,broker_unavailable`, run completed after Redis returned; (b) queued Redis message deleted → dispatcher re-published (outbox attempts 2), run completed once; (c) two broker messages for one run → `claim_count=1`, 3 evidence records; (d) worker killed after 2 of 10 slow pages → lease expired, run reclaimed (`claim_count=2`) and completed with 10 unique pages. Every recovered run: no page or observation stored twice. pytest `test_duplicate_delivery_is_a_no_op`, `test_takeover_after_worker_crash_resumes_without_duplicates`, `test_outbox_recovers_when_the_broker_was_down_at_request_time`, `test_relay_requeues_lost_messages_and_expired_leases`, `test_runs_whose_workers_keep_dying_are_failed_after_bounded_claims`. |
| AC4 | Cancellation preserves collected evidence with an accurate terminal status | verified | Stack: slow run canceled after ≥2 pages → run and connector `canceled`, kept pages equal evidence records, kept evidence downloadable with matching hash and labelled synthetic; cancelling again is refused harmlessly. Canceled queued runs are final immediately (`test_canceling_a_queued_run_is_immediate_and_final`). Browser: cancel on the run page keeps page 1 listed. |
| AC5 | Evidence cannot be fetched through another unauthorized case or user route | verified | Stack: a second, non-member account received `404` without case data for the case, entities, relationship, evidence list/detail/preview/content, runs, run detail, graph, notes, JSON and CSV exports, cancel, entity creation, import and deletion; anonymous download and export → `401`; the owner could not read evidence or a run through a different case id (`404`). pytest `test_non_member_cannot_reach_any_case_record`, `test_evidence_is_not_reachable_through_another_case`, `test_deletion_status_is_private_to_the_requester`. |
| AC6 | Exports preserve provenance, contain no secrets and neutralize CSV formulas | verified | Stack: JSON manifest `data_sha256` recomputed and matched; record counts, acquisition methods (3 imports, 36 synthetic), coverage gaps (partial, unavailable, canceled, authentication_required, …), source dates, evidence hashes, import origins, relationship origin/review and references present. CSV ZIP: every file hash matches the manifest, UTF-8 BOM, `=HYPERLINK(…)` exported as `'=HYPERLINK(…)`, Turkish text intact. No `password_hash`, `$argon2`, CSRF, session or lease tokens, storage paths, generated secret values or account passwords in any export file. |
| AC7 | Case deletion removes case-owned evidence and derived records; failure visible and retryable | verified | Stack: wrong confirmation refused; writes refused while deleting (`409`); job completed with removed counts; neither `cases` nor any of the 14 case-owned tables (including outbox rows) has a row for the case id; the case directory is gone from the volume; `reconcile-evidence` clean before (39 records re-hashed) and after; another case unaffected. Failure and retry: pytest `test_failed_deletion_is_visible_and_retryable`, `test_deletion_waits_for_running_executions`. Browser: deletion from the UI, job shown as completed by polling, case URL returns 404. |
| Scenario 8 | Malformed imports, unsafe filenames, source failures and partial outcomes handled honestly | verified | Stack: malformed JSON, NUL bytes, invalid UTF-8, empty file, 200-level nesting, missing import origin → `422` with specific codes and no records; 5 MiB + 1 byte → `413 evidence_too_large`; a request declaring 8 MiB → `413` from the proxy before the body is sent; `../../etc/passwd<U+202E>txt.json` stored as display name `passwdtxt.json`; fixture scenarios produced `no_findings`, `partial` (2 retries), `unavailable` failure, retries for flaky and rate-limited sources, `authentication_required` and `parse_error`, each with coverage notes and error codes. |

## Phase 1 deliverables and locations

| Deliverable | Status | Location |
| --- | --- | --- |
| Migration 0002 and models (cases, members, notes, entities, identifiers, observations, relationships, references, decisions, evidence, saved queries, runs, connector runs, outbox, deletion jobs) | verified | `services/api/migrations/versions/20260915_0002_*.py`, `services/api/app/*/models.py` |
| Case API with membership authorization, notes, archive/restore, deletion jobs | verified | `services/api/app/cases/` |
| Entities, identifier normalization, relationships, review decisions, bounded graph | verified | `services/api/app/entities/` |
| Evidence imports, storage, previews, downloads, reconciliation CLI | verified | `services/api/app/evidence/`, `services/api/app/cli.py` |
| Saved queries, executions, leases, retries, cancellation | verified | `services/api/app/queries/` |
| Transactional outbox and dispatcher service | verified | `services/api/app/dispatch/`, `compose.yaml` (`dispatcher`) |
| Synthetic fixture connector and registry | verified | `services/api/app/connectors/` |
| JSON/CSV exports with manifest and formula neutralization | verified | `services/api/app/exports/` |
| Web workspace: case list/detail, entities, relationships, evidence, queries and runs, graph, export and delete | verified | `apps/web/src/app/(workspace)/`, `apps/web/src/components/cases/` |
| Tests | verified | `services/api/tests/` (172 tests), `apps/web/src/**/*.test.*` (42 tests), `apps/web/e2e/` (browser workflow) |
| Stack acceptance | verified | `scripts/verify-phase1.sh`, `scripts/phase1_acceptance.py` |
| CI | in progress (written, not run on GitHub) | `.github/workflows/ci.yml` (stack job runs both verifiers and the browser test) |
| Documentation | verified against the commands above | `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `docs/adr/0004-*.md`, `docs/operations/evidence-storage.md`, `docs/operations/backup-restore.md`, `apps/web/e2e/README.md` |

## Commands and results (Phase 1, final code)

All commands were run from the repository root unless noted.

| Command | Result |
| --- | --- |
| `cd services/api && uv sync --locked` | OK (56 packages) |
| `cd services/api && uv run ruff check .` / `uv run ruff format --check .` | All checks passed / 89 files already formatted |
| `cd services/api && uv run mypy` | Success: no issues found in 89 source files (strict) |
| `scripts/test-backend.sh` | **172 passed** in 23.8 s (real PostgreSQL 18.6, Redis 8.10.1 and a Celery worker round trip) |
| `ruff check --isolated --select F,E9,B scripts/phase1_acceptance.py scripts/smoke_test.py` | All checks passed |
| `cd apps/web && pnpm install --frozen-lockfile` | Lockfile up to date |
| `cd apps/web && pnpm lint` | No findings |
| `cd apps/web && pnpm typecheck` | No errors |
| `cd apps/web && pnpm test` | **42 passed** (8 files) |
| `cd apps/web && pnpm build` | Compiled successfully; 18 routes including all case workspace pages |
| `docker compose config --quiet` | Valid (run inside both verifiers) |
| `scripts/verify-phase0.sh` | **All Phase 0 stack checks passed** on the Phase 1 code (73 s, isolated project removed afterwards) |
| `scripts/verify-phase1.sh --e2e` | **All Phase 1 stack checks passed** on the final code (fresh isolated project `tracehollow-verify`, 319 s, project and volumes removed afterwards): Compose validation; no host ports for dispatcher, worker, postgres, redis; migration 0002; seed and hostile imports; down/up persistence (1 case, 4 entities, 3 evidence rows and files); reopen with re-hashing; reruns, 7 fixture scenarios and cancellation; recovery 3a–3d (outbox attempts 2, claim_count 1 for duplicate delivery, claim_count 2 after the killed worker, no duplicate pages); Playwright browser workflow (1 passed, 20.0 s); 16 non-member routes plus import and deletion → 404; exports; deletion of 39 evidence files and all case rows with `reconcile-evidence` clean before and after; no secrets, passwords or session cookies in 1155 log lines. Chromium: local headless shell build 1234 via `TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE` |
| `pnpm e2e` against a separate throwaway stack | 1 passed (19.8 s, fresh setup path) and 1 passed (16.8 s, existing administrator); without credentials reported as 1 skipped |
| Mutation checks | The new deletion-polling Vitest test fails with polling disabled; `test_entrypoint_registers_every_table` fails without the model registration fix |
| Exploratory browser session (uncommitted `playwright-core` script with screenshots) | Full workflow on the Compose stack; found and fixed duplicate form labels (db87cc5), the worker model registration bug (a042063) and overlapping graph labels with a Cytoscape console warning (bf5347d). Final run: no console errors or warnings |

## Unverified checks, blockers and known limitations

- **CI not executed:** `.github/workflows/ci.yml` has not run on GitHub (pushing was not authorized).
  The stack job now installs a Playwright Chromium build and runs both verifiers; runner-specific
  problems (time limits, browser dependencies, Docker Engine on Linux) remain possible.
- **Platforms:** verified only on macOS with Docker Desktop. Linux Docker Engine and Windows/WSL are
  untested; evidence-volume ownership for UID 10001 on Linux bind mounts is untested.
- **Browser build mismatch:** locally the browser test used Chromium headless shell build 1234
  instead of the build 1243 that `@playwright/test` 1.63.0 expects, to avoid downloading browsers.
  CI installs the matching build.
- **Team access:** membership exists in the data model but there is no API or UI to add members.
  Non-member checks use a second account created directly in the database.
- **Search and filtering (FR-06):** case title/purpose search, tag and status filters, entity
  name/identifier search with type and origin filters, evidence acquisition and kind filters, and
  run status filters exist. Full-text search across notes and evidence text, and date-range
  filters, are not implemented.
- **Exports (FR-09):** no redaction step; exports reference evidence bytes by id and hash instead of
  including them.
- **Imports:** UTF-8 text and JSON up to 5 MiB only. PDF, OCR, images and messaging exports are
  deferred. Text is not scanned for malware.
- **Connectors:** only `synthetic.fixture`. No live source was contacted and no connector has a live
  verification date. The connector contract has not yet been exercised by a network connector.
- **Evidence limits** are not passed through `compose.yaml`; changing them requires editing the
  service environment (documented).
- **Crash recovery timing:** a run whose worker dies resumes only after its lease expires (60 s by
  default); a lost broker message is re-published after about 60 s (exponential backoff).
- **Audit:** review decisions on relationships are recorded; other edits and exports are not
  audited.
- **Accessibility:** labelled controls, keyboard-usable edge table and text-plus-glyph badges; no
  axe or screen-reader audit was run. The Cytoscape canvas itself is not keyboard accessible (the
  edge table is the accessible alternative).
- **Deletion and backups:** deleting a case does not remove it from earlier backups or exports
  (documented in `docs/operations/backup-restore.md`).
- **Owner decisions still open from Phase 0:** MIT `LICENSE` versus PRD §13's Apache-2.0 proposal;
  enabling GitHub private vulnerability reporting; paste artifacts in `PRD.md`, `AGENTS.md` and
  `CLAUDE.md` left untouched.
- **Graphify:** `graphify update .` (graphify 0.9.61) rebuilt the code graph after the final code
  change: 1623 nodes, 4942 edges, 85 communities from 134 files. No `secrets/` or `.env` paths and no
  secret values appear in the output. Community labels were not refreshed (`graphify label` needs an
  LLM provider and was not run). `graphify-out/` stays git-ignored.

## Next bounded task

**Phase 2, first slice — connector SDK hardening and one public web page connector with SSRF
protection.** Promote the fixture connector's contract into a documented connector SDK and registry
(descriptor validation, capability listing in the UI). Add a single `public_web.page` connector that
fetches one user-supplied URL through a dedicated worker egress network, with scheme and port
allowlists, DNS resolution checks that reject loopback, private, link-local, multicast and
cloud-metadata addresses, re-validation of every redirect, response size, time and content-type
limits, and evidence capture with collection URL and retrieval time. Contract tests against a local
test HTTP server must cover success, verified no findings, 401/403, 429 with retry information,
timeouts, malformed content and partial pagination, plus SSRF and redirect rejections. RSS, GitHub,
username and domain engines follow as separate Phase 2 tasks.

---

## Phase 0 record

Phase 0 — Repository and secure local foundation — was verified on 2026-09-15 on branch
`feat/phase-0-foundation`. The evidence below is kept as recorded then; commands whose output
changed in Phase 1 (test counts, routes) are listed above with their current results.

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| AC1 | Documented setup starts core services without paid APIs or an LLM | verified | `scripts/setup.sh` then `docker compose up --build --detach --wait`: web, api, worker, postgres and redis healthy and migrate exited 0. No external API keys or models configured. |
| AC2 | Fresh-database migrations run; repeated startup is safe and non-destructive | verified | Migrate applied revision 0001 on an empty volume; a second `up` re-ran migrate as a no-op with unchanged rows. pytest `test_migrations.py` (upgrade → downgrade → upgrade). |
| AC3 | Valid login succeeds; invalid fails; protected routes reject unauthenticated access; logout invalidates the session | verified | `scripts/smoke_test.py` through the web proxy and against the API directly; pytest `test_auth.py` covers CSRF, origin and Host checks, lockout, expiry and rotation. |
| AC4 | Readiness changes when a required dependency is unavailable | verified | Redis and PostgreSQL stopped in turn: readiness `503` naming the failed check, liveness `200`; recovery to `200`. |
| AC5 | Broker-to-worker connectivity task without pretending to be an OSINT run | verified | Worker check persisted as `completed`; worker ping `online`; reconnect after a Redis restart. |
| AC6 | A database record persists across Compose down/up without volume deletion | verified | Row counts identical before and after `down`/`up`; same credentials signed in. |
| AC7 | Service bindings, secret handling and health information match documentation | verified | No host ports for postgres and redis; api and web on 127.0.0.1 only; secrets mounted as files; service logs free of generated secrets, passwords and session cookies. |
| AC8 | Checks, production build and Compose validation pass; unrun checks recorded | verified (local) | Phase 0 run: 80 backend tests, 31 frontend tests, build, `verify-phase0.sh` (66 s). CI not run. |

Phase 0 architecture decisions: [ADR 0001](adr/0001-phase-0-technology-baseline.md) (versions),
[ADR 0002](adr/0002-local-authentication-and-sessions.md) (authentication, sessions, CSRF) and
[ADR 0003](adr/0003-deployment-topology-and-secrets.md) (topology, secrets, least privilege). Phase 1:
[ADR 0004](adr/0004-case-evidence-and-execution-lifecycle.md).

Selected versions (unchanged in Phase 1 except the additions noted): Node.js 24.21.0 LTS, Next.js
16.3.5, React 19.3.0, TypeScript 6.0.3, ESLint 9.39.5, Tailwind CSS 4.3.3, Vitest 5.0.0, pnpm 12.4.1;
Python 3.13.15, FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.5, SQLAlchemy 2.0.53, Alembic 1.20.0,
psycopg 3.3.5, Celery 5.6.3, redis-py 6.4.0, argon2-cffi 25.1.0, uvicorn 0.53.0, uv 0.12.13;
PostgreSQL 18.6 (`pgvector/pgvector:0.8.6-pg18-trixie`), Redis 8.10.1. Added in Phase 1:
python-multipart 0.0.32, cytoscape 3.34.3, `@playwright/test` 1.63.0 (development only).

Phase 0 limitations that still apply: secret rotation and restore onto a new machine are documented
but not exercised; no automated dependency or container vulnerability scanning; no MFA; the Next.js
CSP allows `'unsafe-inline'` scripts; no TLS termination (loopback only); account lockout can be
triggered by anyone who can reach the login page (recovery: `reset-password`).
