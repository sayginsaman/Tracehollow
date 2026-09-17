# Tracehollow

Tracehollow is an open-source, self-hosted OSINT investigation workspace that runs locally with
Docker Compose. The product goal — cases, evidence with provenance, modular public-source
collection and evidence-grounded AI — is specified in [PRD.md](PRD.md).

> **Project status: Phases 0-4 implemented.** Phase 3 is complete (human review by one reviewer).
> Phase 4 adds authorized WhatsApp export and PDF imports with optional OCR, capability-based
> Instagram, Telegram and YouTube connectors, a timeline, entity comparison and self-contained HTML
> reports. The Phase 4 social connectors are **fixture-tested only**: none has been checked against
> its live platform (no credentials or authorization for that). Four Phase 2 connectors are
> live-verified for a narrow scope. See [docs/STATUS.md](docs/STATUS.md) for verified progress,
> acceptance status per phase and limitations.

## What works today

- **Foundation (Phase 0):** `docker compose` stack with `web` (Next.js), `api` (FastAPI), `worker`
  (Celery), `dispatcher` (outbox relay), `postgres` (PostgreSQL 18, pgvector image), `redis`
  (broker only) and a one-shot `migrate` job; token-protected first-run setup; server-side sessions
  with CSRF protection; readiness and worker health; backup and restore scripts.
- **Cases:** create, edit, tag, archive, restore and delete (typed-title confirmation, observable
  and retryable deletion job that removes records and evidence files). Case access is checked on the
  server for every record, download, export and progress request.
- **Entities and relationships:** the PRD's initial entity types, identifiers stored with original
  and normalized values (Turkish-aware for usernames), matching identifiers shown as hints and never
  merged, typed relationships with origin, review status, decision history and supporting or
  contradicting evidence. Notes on cases, entities, relationships and evidence.
- **Evidence:** bounded UTF-8 text and JSON imports (5 MiB) with required import origin, SHA-256,
  safe display filenames, duplicate detection, inert previews, hash-verified downloads and
  crash-safe storage on the evidence volume.
- **Saved queries and executions:** definitions separate from runs, immutable parameter snapshots,
  statuses `queued`/`running`/`completed`/`partial`/`failed`/`canceled`, explicit per-connector
  outcomes, retries, cancellation that keeps collected evidence, and recovery from broker outages,
  lost messages, duplicate delivery and worker crashes (transactional outbox and leases in
  PostgreSQL).
- **Public-source collection (Phase 2):** five connectors run in a separate `collector` service
  ([docs/connectors](docs/connectors/README.md)):
  - *Public web page* and *RSS/Atom feed* (direct requests): byte-exact snapshots plus extracted
    text or parsed entries, redirects and HTTP provenance, feed pagination with deduplication.
  - *GitHub account* (official REST API, optional token): profile and repositories with rate-limit
    quota recorded.
  - *Username discovery* (Sherlock engine, 58 curated platforms): candidate accounts only, with
    blocked, rate-limited and failed platform checks reported instead of read as absence.
  - *Passive subdomain discovery* (Subfinder, certificate transparency and other passive datasets):
    scope-limited, never resolving or contacting the domain. Subfinder runs in a network sandbox
    (`discovery-runner`) whose only way out is an egress gateway admitting the selected providers
    with verified certificates ([ADR 0007](docs/adr/0007-subfinder-network-sandbox.md)).
  - Every fetched address and redirect is checked against SSRF rules; per-source concurrency and
    pacing, retries honouring `Retry-After`, explicit outcomes (`no_findings` only for verified empty
    results) and incremental progress. A **Sources** screen shows each connector's mode, coverage,
    limits, cost, quota, verification status, write-only encrypted credentials and recent health.
- **Authorized imports with processing (Phase 4)** — processed by the internal `worker`, which has
  no internet route ([ADR 0008](docs/adr/0008-authorized-imports-and-document-processing.md)):
  - *WhatsApp chat exports* (`.txt` or `.zip`, Android and iOS layouts): the original is kept; each
    message becomes an observation citing its line in the chat text, with the timestamp as written.
    Unprovable date orders are asked, not guessed; unknown timezones keep local times off the UTC
    timeline; sender labels never become identities or phone numbers; attachments are stored inert
    and marked present, missing or omitted. Archives are screened for traversal, symlinks, bombs
    and oversized entries ([guide](docs/imports/whatsapp.md)).
  - *PDF documents:* text-layer extraction with page references in a resource-limited child
    process, explicit encrypted/malformed/unsupported/partial/image-only states, and optional
    Tesseract OCR as a separate labelled record ([operations](docs/operations/document-processing.md)).
  - Jobs support cancellation (finished pages kept), bounded retries, reprocessing that replaces
    earlier derived records, and deletion of derived records and files; derived text is indexed for
    case-scoped AI retrieval, and citations show the page and line.
- **Social platform connectors (Phase 4, fixture-tested):** a capability model lists every access
  method per platform with provider, fields returned and never returned, identifiers, pagination,
  session needs, restrictions and quota; only implemented capabilities can be run
  ([ADR 0009](docs/adr/0009-social-connector-capabilities.md), [matrix](docs/connectors/README.md#capability-matrix-social-platforms)):
  - *Instagram:* official Graph API Business Discovery for professional accounts; an unofficial
    public profile-page lookup that is off unless an administrator enables it. Login walls, expired
    tokens, throttling and undiscoverable accounts are distinct outcomes. No private-profile or
    unrestricted personal-account access.
  - *Telegram:* public channel web preview (posts, edits, links, gaps reported as not visible) and
    Bot API chat metadata with the token redacted. No user sessions, joining or messaging.
  - *YouTube:* Data API channel uploads and video comments with quota, disabled comments and
    unavailable videos. No transcripts.
- **Timeline, comparison and reports (Phase 4)** ([guide](docs/analysis/README.md)): a timeline that
  keeps UTC, local-only and collection-only times apart; a read-only comparison of 2-4 entities
  (shared and conflicting identifiers, relationships, source coverage, changes between collections,
  absences that stay unknown after incomplete collections, conflicts, unresolved questions); and
  self-contained HTML reports from an explicit selection with redaction, escaped content, no scripts
  or external requests, and citations that resolve to bundled excerpts offline.
- **Synthetic fixture connector** (`synthetic.fixture`): deterministic, clearly labelled test data
  with scenarios for findings, no findings, partial coverage, failures, retries, rate limits and slow
  runs. It makes no network requests.
- **Graph:** a bounded relationship graph (at most 150 entities, depth 2) with a keyboard-accessible
  edge table; selecting an edge shows its origin, review history and evidence.
- **Exports:** JSON and CSV (ZIP) with a manifest of record counts, source dates, acquisition
  methods, coverage gaps and SHA-256 hashes; spreadsheet formulas neutralized; no secrets.
- **Evidence-grounded AI (Phase 3, optional):**
  - *Indexing:* imported text and JSON evidence is chunked (exact character offsets, JSON pointers),
    embedded with a local model and stored in PostgreSQL with pgvector; per-record status
    (pending, indexing, indexed, stale, failed, canceled), retries, cancel and rebuild.
  - *Retrieval:* case-scoped hybrid search combining Turkish- and accent-aware full-text search,
    exact identifier matches (domains, emails, URLs, IPs, hashes, usernames) and vector similarity.
  - *Questions:* persistent case conversations. Exact counts and date or status filters come from
    registered read-only database tools; answers are split into labelled claims (sourced, database
    count, inference, conflict, insufficient evidence) with citations that open the exact passage or
    JSON location in the hash-verified original. Unsupported claims are removed; answers without
    support say so, and coverage gaps are shown.
  - *Summaries and relationship suggestions:* suggestions link existing entities only, cite a
    verified quote and stay unreviewed until an analyst decides.
  - *Data controls:* local Ollama models by default; optional Anthropic cloud generation only for cases
    an analyst explicitly allows; no fallback between them; AI can be turned off per case or for the
    installation. Every run records provider, model, prompt version, retrieved passages, tool calls
    and reported token usage.
  - *Evaluation:* a versioned synthetic set of 41 questions (8 of them a frozen holdout) with
    automated checks, separate answering/abstention and citation measures, and a human-review
    package ([docs/testing/ai-evaluation](docs/testing/ai-evaluation/README.md)).
- **Interface (UI/UX redesign, between Phase 4 and Phase 5):** one design system (tokens, bundled
  IBM Plex fonts, light theme with a maintained dark theme) and a grouped sidebar with breadcrumbs
  and case context; an Overview of work that needs attention; a dedicated Imports page; plain-language
  run outcomes; provenance labels for collected, imported, extracted, OCR, synthetic and AI-generated
  material; responsive layouts from 390px, keyboard access and axe-checked pages
  ([docs/design](docs/design/README.md)).

## Requirements

| Purpose | Requirement |
| --- | --- |
| Run the stack | Docker Engine or Docker Desktop with Compose v2 (verified with Docker 29.8.0, Compose v5.5.1) |
| Setup script | `bash`, and `openssl` or `/dev/urandom` |
| Verification scripts | `python3` (3.9 or newer, standard library only) |
| Backend development | [uv](https://docs.astral.sh/uv/) 0.11.12 or newer (installs Python 3.13 if needed) |
| Frontend development | Node.js 24.15 or newer and pnpm 12.4.1 (`corepack` 0.36+, or `npx pnpm@12.4.1`) |

No paid API, cloud account or language model is required. AI features need
[Ollama](https://ollama.com) with the models described in
[docs/operations/ai-models.md](docs/operations/ai-models.md); everything else works without it.

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
token, choose a username and a password of at least 12 characters, then sign in. You land on the
case list; **Environment status** shows dependency checks and worker health.

## Main workflow

All example data below is synthetic; use only material you are authorized to process.

The sidebar groups navigation by task: **Workspace** (Overview, Cases), the **current case** when
you are inside one (Case overview; Collect: Queries & runs, Imports; Examine: Evidence, Entities,
Relationships, Graph; Analyze: Timeline, Compare, AI; Report: Reports, Case settings) and
**Configuration** (Sources, Environment status, Preferences). Breadcrumbs in the header always name
the case you are in. Below 1024px the sidebar opens from the menu button.

1. **Overview:** after signing in you land on the Overview: items that need a decision (for example
   a chat export waiting for its date order, or a run that needs access), recent cases, recent runs
   and imports, and whether required services respond. Press **New case**.
2. **Cases:** fill in title, purpose, scope and tags and press **Create case**. The case list has
   search, status filters, tag filters, sorting and pagination.
3. **Entities:** add, for example, an organization and a domain with identifiers. Entities with
   matching identifiers are listed as leads on the entity page and can be compared; nothing is
   merged automatically.
4. **Imports:** choose *Text or JSON*, *WhatsApp export* or *PDF document*; each shows accepted
   formats, default limits and what happens before you upload. Describe where the material came
   from in **Import origin**. WhatsApp exports and PDFs are processed in the worker; follow them
   under **Processing jobs**, answer the date-order question if asked, cancel or process again.
5. **Evidence:** every record carries a provenance label (collected, authorized import, extracted
   text, OCR text, synthetic). Use **Quick look** from the list, or open a record for its
   line-numbered content, provenance, SHA-256, integrity status and download. Link it to an entity
   from the entity page.
6. **Relationships:** connect two entities with a predicate such as `owns`, tick supporting
   evidence, then open the relationship to record a review decision with a rationale.
7. **Queries & runs:** choose a source (for example *Public web page* with a URL you are allowed to
   collect, or the *Synthetic fixture* with a scenario such as `partial`), note who will see the
   request, save and press **Run**. Run lists state results in plain language (completed with or
   without findings, partial, access or setup required, rate limited, failed, canceled). The run
   page shows per-connector outcomes, retries, quota, coverage notes and the evidence collected.
   **Run again** creates a new, independent execution; **Cancel execution** stops a running one and
   keeps pages already collected. Compare sources and add optional credentials on **Sources**.
8. **Graph:** inspect the bounded graph; select a node or edge for details, or use the edge table.
9. **Timeline and Compare:** the timeline keeps UTC times, local times with an unknown timezone and
   collection-only times in separate tabs; Compare puts 2-4 entities side by side and separates
   shared identifiers, differences, conflicts, changes over time and unknowns.
10. **AI:** the processing indicator shows whether the case is local-only. Once the **Evidence
    index** shows your records as indexed, start from a suggested question or a new conversation,
    for example *"ornek.example alan adı hangi tarihte kim tarafından tescil edildi?"*. Answers are
    labelled AI-generated; select a citation such as **E1** to see the exact supporting passage and
    follow its link to the evidence record. **Generate summary** and **Suggest relationships** add
    reviewable outputs (see [docs/operations/ai-models.md](docs/operations/ai-models.md)).
11. **Reports:** select records, redact, preview the exact file in a sandboxed frame, then download a
    self-contained HTML report whose citations work offline.
12. **Case settings:** archive or restore the case, download the JSON or CSV export, or delete the
    case by typing its title. Deletion progress is shown on the case list. An imported evidence
    record can also be deleted on its own page, which removes its index data.

**Preferences** stores a light, dark or system theme in the browser and shows your account and
session. The interface design is documented in [docs/design/README.md](docs/design/README.md),
[DESIGN.md](DESIGN.md) and [PRODUCT.md](PRODUCT.md).

Stored data survives `docker compose down` and `up`; reopen the case to continue.

Stop the stack with `docker compose down`. Data stays in Docker volumes; **do not** add `--volumes`
(`-v`) unless you intend to delete all local data.

## Services and ports

| Service | Purpose | Published on host |
| --- | --- | --- |
| `web` | Next.js UI and same-origin API proxy | `127.0.0.1:3000` |
| `api` | FastAPI application | `127.0.0.1:8000` |
| `worker` | Celery worker that runs executions and deletion jobs (reuses the API image) | none |
| `collector` | Celery worker for public-source collection with the Sherlock engine; outbound access on its own network; sends passive domain lookups to the sandbox | none |
| `discovery-runner` | Runs Subfinder in a network sandbox (internal `discovery` network only, no secrets, no route out) | none |
| `discovery-gateway` | Only way out of the sandbox: CONNECT to allowlisted provider hosts on 443 after the address policy, provider certificates verified | none |
| `ai-worker` | Celery worker for indexing and AI requests; outbound access to model endpoints on its own network | none |
| `dispatcher` | Publishes the transactional outbox, redelivers lost work, reconciles evidence storage | none |
| `db-extensions` | Creates the pgvector extension as the database superuser, then exits | none |
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
| `GET /api/v1/ai/status` | session | AI switch, configured providers and models, synthetic flag and the latest model availability checks. |

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
| `TRACEHOLLOW_COLLECTION_ALLOWED_PRIVATE_NETWORKS` | empty | Private networks the collector may reach (lab targets only; loopback and link-local stay blocked) |
| `TRACEHOLLOW_COLLECTION_ALLOWED_PORTS` | `80,443` | Ports collection may connect to |
| `TRACEHOLLOW_GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub Enterprise Server: `https://HOST/api/v3` |
| `TRACEHOLLOW_AI_ENABLED` | `true` | Turns every AI feature on or off |
| `TRACEHOLLOW_AI_LOCAL_PROVIDER` | `ollama` | `synthetic_fixture` for tests and demos (labelled, not a model) |
| `TRACEHOLLOW_AI_OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama address as seen from `ai-worker` |
| `TRACEHOLLOW_AI_GENERATION_MODEL` / `TRACEHOLLOW_AI_EMBEDDING_MODEL` | `qwen3:8b` / `qwen3-embedding:0.6b` | See [ai-models.md](docs/operations/ai-models.md) |
| `TRACEHOLLOW_AI_CLOUD_PROVIDER` / `TRACEHOLLOW_AI_CLOUD_MODEL` | `none` / `claude-sonnet-5` | Optional cloud generation |

Secrets are generated by `scripts/setup.sh` into `secrets/` (directory mode `0700`, git-ignored)
and mounted into containers as files, never as environment variables:

| File | Used by |
| --- | --- |
| `postgres_superuser_password` | PostgreSQL superuser (initialisation, backups, operators) |
| `postgres_app_password` | Non-superuser `tracehollow_app` role used by api, worker and migrate |
| `redis_password`, `redis_users.acl` | Redis authentication (the ACL file stores only a SHA-256 digest) |
| `app_secret_key` | HMAC key for CSRF tokens |
| `bootstrap_token` | One-time web setup; ignored once an administrator exists |
| `cloud_ai_api_key` | Optional cloud AI key, empty by default; mounted into api and ai-worker only |
| `credential_encryption_key` | Encrypts connector credentials stored in PostgreSQL; mounted into api and collector only |

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
scripts/verify-phase0.sh         # Phase 0 acceptance run in an isolated project (ports 3100/8100), cleans up
scripts/verify-phase1.sh         # Phase 1 acceptance run: persistence, reruns, recovery, cancel, authz, exports, deletion
scripts/verify-phase2.sh         # Phase 2 acceptance run against a controlled fixture source (--e2e: browser)
scripts/verify-phase3.sh         # Phase 3 acceptance run with the synthetic AI provider (--model: local Ollama, --e2e: browser)
scripts/verify-phase4.sh         # Phase 4 acceptance run: imports, PDFs, social fixture platforms, reports (--ocr, --e2e)
scripts/ai-eval.sh               # model-backed AI evaluation in a disposable database (needs Ollama)
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

Secrets are not part of backups and must be protected separately. Deleting a case does not remove
it from earlier backups or exports. Full procedures, including restoring onto a new machine:
[docs/operations/backup-restore.md](docs/operations/backup-restore.md). Evidence storage, crash
recovery and `reconcile-evidence`: [docs/operations/evidence-storage.md](docs/operations/evidence-storage.md).

## Architecture

```text
Browser ──HTTP──▶ web (Next.js, 127.0.0.1:3000)
                   │  same-origin /api/* proxy (header allowlist, Host check)
                   ▼
                 api (FastAPI, 127.0.0.1:8000) ──▶ postgres (records, execution state, outbox)
                   │                         └──▶ evidence-data volume
                   ▼ publish after commit
                 redis (broker: run and job ids only) ──▶ worker (Celery) ──▶ postgres, evidence-data
                   │                                  ├──▶ collector (Celery) ──▶ public sources (collect-egress, SSRF-checked)
                   │                                  │       └──▶ discovery-runner (Subfinder; internal network only)
                   │                                  │               └──▶ discovery-gateway ──▶ allowlisted providers (TLS verified)
                   ▲                                  └──▶ ai-worker (Celery) ──▶ postgres (pgvector), evidence-data
                   │                                           │ ai-egress network
                   │                                           ▼
                   │                               Ollama on the host; optional cloud provider
                 dispatcher (reads the outbox in postgres; publishes pending rows, re-queues lost work)
```

The browser only talks to the web origin, so the session cookie is first-party and no CORS is
enabled. PostgreSQL is authoritative for execution state, cancellation and outcomes; Redis carries
only run and job ids, and the dispatcher re-publishes anything lost. Design decisions are recorded in
[docs/adr](docs/adr) (Phase 1: [ADR 0004](docs/adr/0004-case-evidence-and-execution-lifecycle.md),
Phase 2: [ADR 0006](docs/adr/0006-public-source-collection.md) and
[ADR 0007](docs/adr/0007-subfinder-network-sandbox.md), Phase 3:
[ADR 0005](docs/adr/0005-evidence-grounded-ai.md)).

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
- **Run stays "Queued":** check `docker compose ps` for `worker` and `dispatcher`. A run created
  while Redis was down is published by the dispatcher once Redis is back (after up to a minute).
- **Evidence shows an integrity problem:** run
  `docker compose exec api python -m app.cli reconcile-evidence` and follow
  [docs/operations/evidence-storage.md](docs/operations/evidence-storage.md).
- **Import rejected:** the message names the reason (`invalid_encoding`, `binary_content`,
  `invalid_json`, `evidence_too_large`, …). Only UTF-8 text and JSON up to 5 MiB are accepted.
- **`secrets/...` not found when starting Compose:** run `scripts/setup.sh` first (it also creates
  `secrets/credential_encryption_key` and the empty `secrets/cloud_ai_api_key` added by later phases;
  existing secrets are never changed).
- **A collection run is `unsupported` with `blocked_address`, `blocked_host` or `blocked_port`:** the
  address is not a permitted public destination. See the network safety section in
  [docs/connectors/README.md](docs/connectors/README.md).
- **A connector reports `engine_not_installed`:** collection runs must be executed by the `collector`
  service; check `docker compose ps collector`.
- **Passive domain discovery is `unavailable` with `discovery_runner_unavailable` or
  `egress_sandbox_unavailable`:** check `docker compose ps discovery-runner discovery-gateway` and
  `docker compose logs discovery-runner`. The runner refuses to work when its network has a route
  out or the Docker host is reachable on it; the `discovery` network needs Docker Engine 28 or
  later for `gateway_mode_ipv4: isolated`. See [ADR 0007](docs/adr/0007-subfinder-network-sandbox.md).
- **AI tab shows the model as unavailable, or indexing stays pending:** start Ollama and pull the
  models; on Linux, Ollama must listen on an address containers can reach. See the troubleshooting
  table in [docs/operations/ai-models.md](docs/operations/ai-models.md).
- **`migrate` fails with a message about the pgvector extension:** the `db-extensions` job did not
  run or failed; check `docker compose logs db-extensions`.
- **Changed a secret file and services fail to authenticate:** follow
  [docs/operations/secrets.md](docs/operations/secrets.md); PostgreSQL passwords are stored in the
  database and must be changed there as well.

## Privacy and network behaviour

The running application sends no telemetry. Its outbound requests are the collection requests you
start (from `collector`, and for passive domain discovery from `discovery-gateway` to the selected
providers, to the sources shown for each connector), model requests from `ai-worker`
to the configured Ollama address and, for cases an analyst has explicitly allowed, to the
configured cloud provider. Social platform connectors reach their platforms only from `collector`;
imported chat exports and PDFs are parsed by `worker`, which has no outbound route. Next.js telemetry is disabled in the images, fonts are system fonts, and no third-party assets are loaded (except Swagger
UI assets when `TRACEHOLLOW_API_DOCS_ENABLED=true`). Building images downloads base images and
packages from Docker Hub, GitHub Container Registry, PyPI and npm (and Debian packages when
`TRACEHOLLOW_INSTALL_OCR=true`).

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md) — workflow, commands, tests and conventions.
- [SECURITY.md](SECURITY.md) — reporting vulnerabilities and the security model.
- [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) — instructions for AI coding agents.

## License

Released under the [MIT License](LICENSE). Third-party components keep their own licenses; a full
dependency license inventory is planned for the release-readiness phase.
