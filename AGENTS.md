# [AGENTS.md](http://AGENTS.md) — Repository-wide engineering instructions

## Purpose

Build the product specified in [`PRD.md`](http://PRD.md): a local-first, evidence-centered OSINT investigation workspace with Docker Compose, PostgreSQL, modular collection, and source-grounded AI.

This file governs engineering work across the repository. [`CLAUDE.md`](http://CLAUDE.md) is a short Claude-specific entrypoint. Product scope and acceptance criteria live in [`PRD.md`](http://PRD.md); do not maintain conflicting copies here.

These files are a specification, not proof that implementation exists. Inspect actual files and verification results before reporting progress.

## Working agreement

1. Read [`PRD.md`](http://PRD.md), this file, and `docs/[STATUS.md](http://STATUS.md)` if present before changing code.

2. Determine the requested phase and implement its concrete acceptance criteria. The initial implementation task is Phase 0 only.

3. Respect the user's latest explicit instructions and the host tool/permission policies.

4. Continue routine, reversible work autonomously. Do not repeatedly ask for approval of choices already covered by the PRD.

5. Ask only when a missing decision materially changes scope, a necessary secret/access cannot be supplied through existing configuration, or an action crosses an authorization boundary.

6. Do not create remote repositories, publish artifacts, push branches, send external messages, enable paid services, or run real-target collection without authorization covering that action.

7. Do not generate large speculative implementations for later phases.

8. Keep existing user work intact. Never revert unrelated changes or use destructive repository cleanup to simplify a task.

9. If a requirement cannot be verified, finish independent work and record the exact remaining blocker. Never substitute fake success.

10. Use subagents only if the user or an applicable instruction explicitly requests delegation. When used, assign disjoint ownership and preserve other agents' edits.

## Planned repository layout

Use this as a target structure, adapting minimally to an existing repository. Create directories when they contain real phase-relevant content.

```text

[PRD.md](http://PRD.md)

[AGENTS.md](http://AGENTS.md)

[CLAUDE.md](http://CLAUDE.md)

[README.md](http://README.md)

[SECURITY.md](http://SECURITY.md)

[CONTRIBUTING.md](http://CONTRIBUTING.md)

.env.example

.gitignore

compose.yaml

apps/

  web/                       # Next.js / TypeScript application

services/

  api/

    pyproject.toml

    uv.lock

    app/

      [main.py](http://main.py)

      [config.py](http://config.py)

      auth/

      db/

      cases/

      queries/

      evidence/

      connectors/

      ai/

      tasks/                 # Celery worker entrypoint and task orchestration

    migrations/

    tests/

packages/

  contracts/                 # Shared/generated API contracts, when needed

tests/

  e2e/

  fixtures/                  # Synthetic or redistributable fixtures only

docs/

  [STATUS.md](http://STATUS.md)

  architecture/

  adr/

  connectors/

  testing/

  operations/

.github/

  workflows/

```

Do not create a folder full of empty modules to claim architectural progress. The worker may reuse the API image with a different entrypoint; do not create a redundant microservice for every connector.

## Phase control and progress records

Create `docs/[STATUS.md](http://STATUS.md)` in Phase 0 with:

- Current requested phase and overall status.

- A checklist mapped to the phase acceptance criteria.

- Implemented behaviors and their file locations.

- Verification commands/scenarios and actual outcomes.

- Unverified checks, missing credentials, environment blockers and known limitations.

- Selected versions and links to architecture decisions when relevant.

- The next bounded task.

Use `not started`, `in progress`, `verified`, or `blocked` for individual requirements. Do not mark an entire phase verified while a required acceptance check is blocked. A blocked connector does not prevent unrelated work.

After a requested phase is finished, report its outcome and next phase. Do not silently continue into all remaining phases unless the user authorized that scope.

## Architecture rules

- PostgreSQL is the system of record for cases, observations, relationships, query runs, durable dispatch and AI metadata.

- Redis is a broker/cache, not the only persistent copy of investigation results or run status.

- Use SQLAlchemy and Alembic for schema changes. Do not rely on runtime `create_all` as the migration strategy.

- Keep source-specific fields in structured JSONB when appropriate, with schema validation and versioning.

- Store binary/large original evidence through a storage abstraction on a persistent volume. Store references, hashes and provenance in PostgreSQL.

- A saved query and an execution are separate objects. Executions snapshot parameters and never overwrite previous results.

- Implement at-least-once task processing with idempotent effects. Use durable dispatch/outbox behavior when query execution is introduced in Phase 1.

- Database state is authoritative for cancellation and progress. Bound work so cancellation can take effect.

- Keep collection, normalization, relationship inference, retrieval and presentation separated by explicit interfaces.

- Avoid early Kafka, Kubernetes, a graph database, multiple vector stores or a distributed microservice fleet.

- Explain significant architecture changes in an ADR: context, decision, alternatives, consequences and verification.

## Dependencies and configuration

- Verify current supported dependency versions from primary documentation when implementing, then pin compatible versions.

- Use one JavaScript package manager, preferably pnpm, and commit its lockfile. Use uv and a Python lockfile for backend dependencies unless the existing repository establishes another supported standard.

- Keep optional AI, OCR and third-party integrations outside the default dependency/startup path where practical.

- Release container images must use explicit versions or digests, not `latest`.

- `.env.example` contains documented placeholders, never actual credentials.

- Validate required configuration at startup and fail with useful, redacted errors.

- No shared default administrator password, application secret or encryption key.

- Bootstrap operations are idempotent and never reset an existing password/database implicitly.

## Security baseline

- Bind web/API to loopback by default. PostgreSQL and Redis remain on the internal Compose network.

- Authentication is required from Phase 0; localhost binding is not a replacement for authentication.

- Protect sessions using a reviewed implementation, appropriate CSRF protection, restricted CORS and secure password hashing.

- Apply case authorization to every relevant read/write path, including evidence download, exports, AI citations and streaming endpoints.

- Encrypt stored integration credentials using a reviewed library; keep encryption keys outside the database and never expose them in API responses.

- Never log passwords, tokens, cookies, raw provider authorization headers, or unnecessary case content.

- Public web fetching and browser automation must enforce SSRF protections across DNS resolution and each redirect. Block loopback, private, link-local and metadata addresses, including IPv6 variants.

- Explicitly configured local infrastructure/model endpoints use a separate trusted configuration path; they must not weaken arbitrary evidence-URL checks.

- Limit downloads, document parsing, decompression, concurrency, retries and wall-clock execution time.

- Treat filenames, MIME types, HTML, Markdown and uploaded documents as untrusted.

- No shell interpolation of user input; use argument arrays and validation. Never mount the Docker socket in collection services.

- Do not turn passive collection into active scanning or third-party submission without a visible, separately authorized operation.

- No CAPTCHA bypass, credential theft, automatic cookie harvesting or private-account access features.

## Connector contract

Every production connector must provide:

1. Stable ID and version.

2. Supported input types, collection mode and output schema.

3. Credential/session requirements and actual source coverage.

4. Limits, timeout, retry/backoff and cancellation boundaries.

5. Cost/quota metadata when available, without invented prices.

6. Structured observations with raw evidence references and provenance.

7. Explicit outcome from the PRD vocabulary.

8. Fixture-based contract tests and a documented optional live smoke procedure.

9. License/provider requirements and last live-verification date when applicable.

Never map authentication failures, rate limits, source errors or incomplete pagination to `no_findings`. A username match is a candidate account, not an identity assertion. Do not assign arbitrary high confidence to all results.

Cache results only under documented freshness and authorization rules. Cached observations retain their original collection time; a cache hit is not a fresh source observation.

Do not copy third-party code solely because a repository is public. Separate code, dataset, model and provider licenses. Isolation behind an API/process does not automatically waive license obligations.

## Evidence and entity rules

- Preserve original identifiers alongside normalized values.

- Prefer platform IDs over mutable usernames when available.

- Never merge people/accounts automatically on a shared name, username, email or phone alone.

- Keep observed facts, deterministic derivations, AI suggestions and analyst assertions distinct.

- Store temporal precision and separate event/publication/collection timestamps.

- New evidence versions are new records; deliberate deletion is an audited lifecycle action.

- Every relationship must expose its origin and supporting/contradicting evidence.

- A content hash is integrity metadata, not proof of source truth.

- Ensure case deletion removes originals, chunks, vectors and generated artifacts; document independent backup retention.

## AI implementation rules

- Implement AI only in its authorized phase; it is optional infrastructure for the core workspace.

- Use an explicit provider interface with local-model support and optional cloud configuration.

- Enforce case permissions and local-only policy before retrieval and before external model calls.

- Use case-filtered retrieval before ranking, not a global query followed by superficial UI filtering.

- Use parameterized, bounded read tools for exact counts/dates. No arbitrary model-generated SQL execution.

- Source documents and imported chats are data, not instructions.

- Allow only registered, typed tools with enforced budgets and scope.

- Persist citations to actual evidence/chunks and verify their accessibility.

- Test claim support as well as citation existence.

- Abstain when evidence is insufficient; label inference and preserve contradictions.

- Never silently promote AI output into accepted evidence or an identity merge.

- Record model/provider identifiers, prompt versions, evidence references and usage without logging secrets.

- No generated claims of calibrated confidence without a documented evaluation method.

## Frontend rules

- Build a readable analyst workspace with accessible keyboard navigation, focus states and status labels.

- Every implemented feature has loading, empty, error, partial and success states when applicable.

- Render only real backend state. Synthetic demo mode must be clearly labeled and opt-in.

- Unimplemented roadmap features must not appear functional.

- Keep source provenance and evidence navigation near findings and graph edges.

- Show access restrictions and quota/coverage limitations in plain language.

- Lazy-load graph/document-heavy components, paginate lists and bound graph expansion.

- Keep secrets server-side. Avoid sensitive evidence in analytics, browser console logs or public URLs.

- Repository and initial interface language are English; preserve Unicode and test Turkish content.

## Testing and verification

- Add meaningful tests for actual behavior and boundaries; avoid tests that only mirror implementation details or check that decorative text exists.

- Backend: configuration/authentication, migrations, permission boundaries, execution lifecycle, connector outcomes and evidence behavior as phases introduce them.

- Frontend: important interactions, error/partial states and accessibility behavior.

- End-to-end: setup/login, case creation, import, query execution, source failure, evidence inspection and AI citation navigation as available.

- Integration: real PostgreSQL/Redis for persistence and queue semantics where needed.

- AI: versioned synthetic evaluation set, absent answers, conflicting evidence, numeric accuracy, cross-case isolation and prompt injection.

- CI is deterministic and requires no private accounts, paid APIs or real investigation data.

- External smoke tests are separate, bounded and opt-in. Never claim a mocked test proves live support.

- Run checks appropriate to changed behavior. Once they pass, do not repeatedly rerun unrelated suites without a reason.

- If a required tool is unavailable, record the exact unrun check and continue useful independent work; do not weaken tests or auth to obtain green output.

## Command documentation

Do not invent successful commands. During Phase 0 establish and document actual commands for:

- Dependency install and development startup.

- Docker Compose configuration validation, build and startup.

- Migrations against a fresh and existing database.

- Backend lint/test and frontend lint/typecheck/test/build.

- End-to-end verification.

- Backup and restore without destructive volume cleanup.

Keep [`README.md`](http://README.md), CI and `docs/[STATUS.md](http://STATUS.md)` synchronized with commands that actually work in this repository.

## Graphify project convention

- If `graphify-out/graph.json` exists, first use `graphify query "<question>"` for codebase questions.

- Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts.

- Prefer `graphify-out/wiki/[index.md](http://index.md)` for broad navigation if present.

- Read the full graph report only for broad architecture review or when scoped queries are insufficient.

- After code changes, run `graphify update .` when the CLI is available and inspect its outcome.

- If the CLI or graph is unavailable, document this and use ordinary code search; do not block all engineering work or claim the graph is current.

- Do not scan local evidence volumes, credentials or real case data into the code knowledge graph.

- If the user invokes `/graphify`, load the available graphify skill before proceeding, subject to the host's tool interface.

## Completion report

At the end of an implementation task, report:

1. Which requested phase/requirements were addressed.

2. What now works and how the user starts it.

3. What was actually tested and the outcome.

4. Material unverified behavior, limitations and blockers.

5. The next bounded phase/task.

Never claim deployment, publication, live collection, performance numbers, or completed phases without supporting evidence.

