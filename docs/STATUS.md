# Implementation status

- **Requested scope (2026-09-15):** complete Phase 3 (evidence-grounded AI MVP) and implement the
  missing Phase 2 (public-source collection and source reliability) that Phase 3 depends on.
- **Phase 2 status:** verified with fixture-based contract tests and a controlled local fixture
  source (all six acceptance criteria below). **No connector has been verified against its live
  source**: no approved live targets were available, so every connector is labelled
  `fixture_tested`.
- **Phase 3 status:** **not complete.** Seven of eight acceptance criteria are verified, now also on
  collected evidence. Criterion 5 (at least 90% human-reviewed claim support) is **pending human
  review**: the worksheets are published, but no person has reviewed them.
- **Earlier phases:** Phase 0 and Phase 1 stack verifications were re-run on the final code (see
  commands below).
- **Branches:** `feat/phase-2-public-source-collection` (Phase 2, stacked on
  `feat/phase-3-evidence-grounded-ai`, which is stacked on `feat/phase-1-cases-evidence-queries` and
  `feat/phase-0-foundation`). Phase 3 was built before Phase 2; the stacked order records that.
  Nothing has been pushed; GitHub Actions has not run.

Status vocabulary: `not started`, `in progress`, `verified`, `blocked`, `pending human review`.

## Verification environment

| Item | Value |
| --- | --- |
| Date | 2026-09-15 |
| Host | macOS 26.5 (Darwin 25.5.0), Apple M3 Pro, 36 GB |
| Docker | Docker Desktop, Engine 29.8.0, Compose v5.5.1 |
| Host tools | uv 0.11.12, Python 3.13 (backend venv), Python 3.14.6 (verification scripts), Node.js 26.5.0, pnpm 12.4.1 via `npx` |
| Containers | Python 3.13.15, uv 0.12.13, Node.js 24.21.0, PostgreSQL 18 with pgvector 0.8.6, Redis 8.10.1 |
| Collection engines | Subfinder v2.16.0 (linux/arm64 release, SHA-256 `c81d4955…d63d` verified at build), sherlock-project 0.16.2 |
| Local models | Ollama 0.34.0 on the host; `qwen3:8b` (digest `500a1f067a9f`, Q4_K_M), `qwen3-embedding:0.6b` (digest `ac6da0dfba84`, Q8_0, 1024 dimensions) |
| Browser tests | `@playwright/test` 1.63.0 with the local Chromium headless shell build 1234 via `TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE` (1.63 expects build 1243) |

## Phase 2 acceptance checklist (PRD §12)

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| AC1 | A case collects controlled public-source examples and records source/provenance for each observation | verified (controlled local source) | `verify-phase2.sh`: a `fixture-site` container on an allowlisted subnet of the collector's egress network serves a Turkish web page, RSS feeds, a GitHub-shaped API and profile pages. 28 collection runs executed by the `collector` (none by the internal worker). Every collected evidence record has collection mode, access category and source reference; web evidence records requested and final URL, redirect chain, HTTP status, connected address (`172.31.250.10`), charset and publication time separately from collection time; every observation references its evidence. pytest `test_web_page_collection_stores_provenance_derived_text_and_routes_to_the_collector`. These are controlled examples, not live public sources. |
| AC2 | Tests cover success, verified no findings, 401/403, 429, timeout, malformed content and partial pagination | verified | `tests/test_connector_contracts.py` (48 tests) per connector: web (success with redirect, legacy charset, 404, 401, 403, 429 with `Retry-After`, 503, timeout, unsupported type, truncation), RSS/Atom (pagination, repeated entries, empty feed, malformed XML, entity expansion, external entity, HTML instead of feed, 404, 429, cross-origin pagination), GitHub (pagination with quota, 404, bad token, primary and secondary rate limits, 403, malformed, outage, pagination origin), Sherlock (reclassification table including 403/429/5xx that Sherlock reports as "available", candidates, verified absence, engine failure), Subfinder (recorded real stderr, all sources failing, verified empty, missing key, rate limit, partial with key redaction, result limit, fatal error, missing engine, cancellation). `tests/test_collection.py`: a failing third feed page after retries is `partial` with two pages kept; a rate limit longer than the allowed wait stops as `rate_limited`. Stack: 404 → `no_findings`, 403 → `access_denied`, truncation → `partial`, broken pagination → `partial`, malformed feed → `parse_error`, GitHub rate limit → `rate_limited` with retry time and quota, rejected token → `authentication_required`, cancellation during a request → `canceled`. |
| AC3 | Domain collection stays within the passive scope; username hits remain candidate accounts | verified | Subfinder runs without `-active`/`-nW` and with `-duc` (asserted from the arguments in tests); results outside the domain are discarded and counted (`in_scope` tests and the stored discard count). Sherlock hits are `candidate_account` observations and `platform_account` entities marked candidate, with no relationships between them (stack: 2 candidates, `relationship_count` 0); 429 and blocked checks are never read as absence. |
| AC4 | SSRF and redirect protections reject prohibited destinations | verified | `tests/test_netguard.py` (54 tests: 29 non-public address forms including IPv4-mapped, 6to4, NAT64, Teredo and the AWS IPv6 metadata address; schemes, embedded credentials, local host names, ports, numeric hosts; mixed DNS answers; redirects to metadata and to private names; redirect limit and origin scope; DNS rebinding between check and connect). Stack, inside the running collector with real Docker DNS: metadata IP, IPv4-mapped metadata, 127.0.0.1, localhost, `postgres:5432`, `redis`, `api`, `2130706433` and a redirect to the metadata address all refused with specific codes and no evidence stored; the username engine's request to a metadata address refused. The one test that needs a non-loopback local server was skipped here because this machine cannot connect to its own tunnel address. |
| AC5 | Live smoke checks run only against approved/controlled targets; fixture tests alone do not earn a live-verified badge | verified (no live check performed) | All live-facing connectors report `verification_status` `fixture_tested` and `last_live_verification` null (API test and stack check); the live smoke procedure requiring written approval, credentials and budget is documented in `docs/connectors/README.md`. No live source was contacted during development or verification. |
| AC6 | Missing credentials yield an actionable state, not fabricated or empty success | verified | Stack: Subfinder with only a key-based source and no key → `authentication_required` naming the skipped source (real binary, no requests sent); rejected GitHub token → `authentication_required` (`github_bad_credentials`) and the credential shown as rejected to the administrator; credentials encrypted under another key → `authentication_required` (`credential_unreadable`) without sending a request (pytest). The query form names required credentials and links to the Sources screen. |

## Phase 2 deliverables and locations

| Deliverable | Status | Location |
| --- | --- | --- |
| Connector SDK: page drafts, descriptors with parameter specs, credentials, cache policy, quota, concurrency, pacing, verification status | verified | `services/api/app/connectors/base.py`, `registry.py`, `app/queries/execution.py` |
| Source capability UI (Sources screen) and connector-driven query forms | verified | `apps/web/src/components/sources/`, `components/cases/QueriesView.tsx`, `RunDetailView.tsx`, `EvidenceDetailView.tsx` |
| Public web, RSS/Atom, GitHub, username (Sherlock) and domain (Subfinder) connectors | verified (fixture-tested) | `app/connectors/web.py`, `rss.py`, `feeds.py`, `htmltext.py`, `github.py`, `sherlock.py`, `engines/`, `subfinder.py`, `data/sherlock_sites.json` |
| SSRF-guarded fetching | verified | `app/connectors/netguard.py`, `http.py` |
| Per-source limits, concurrency slots, pacing, retry/backoff, cache behaviour, scope validation | verified | `app/connectors/limits.py`, descriptors, `execution.py` |
| Encrypted integration credentials | verified | `app/integrations/`, `app/connectors/router.py` |
| Collector service and image with pinned engines; collection queue | verified | `services/api/Dockerfile` (`collector` target), `compose.yaml`, `app/dispatch/service.py` |
| Migration 0004 (collection provenance, credentials, limits) | verified | `migrations/versions/20260915_0004_public_source_collection.py`, `tests/test_migrations.py` |
| Connector documentation | verified against tests and stack | `docs/connectors/`, `docs/adr/0006-public-source-collection.md` |
| Stack acceptance and browser workflow | verified | `scripts/verify-phase2.sh`, `scripts/phase2_acceptance.py`, `scripts/fixtures/public-sources/`, `compose.verify-sources.yaml`, `apps/web/e2e/phase2-sources.spec.ts` |

## Phase 3 acceptance checklist (PRD §12)

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| AC1 | A source-grounded answer opens the exact supporting evidence/chunk | verified | `verify-phase3.sh`: the citation passage equals the original bytes at the stored offsets after SHA-256 verification; browser test opens the passage and the evidence preview. `verify-phase2.sh`: an answer about a collected web page cites the derived page text and the passage opens. Model runs: 0 invalid citations. |
| AC2 | Numeric answers agree with database queries in the evaluation dataset | verified (one model, one run) | `qwen3:8b` with prompt templates v2: numeric agreement 7/7 against independent SQL counts (v1 templates: 3/7, fixed by general prompt changes). Deterministic suite 7/7; server validation drops count claims whose numbers are not in the cited tool result. |
| AC3 | Missing evidence produces an explicit insufficient-evidence answer | verified | Deterministic and model runs: q20-q22 and structural abstentions pass; stack: a question with no supporting evidence returns `insufficient_evidence`. Model run v2 also abstained unnecessarily on q03 and q07 (recorded as quality issues, not as passes). |
| AC4 | Cross-case leakage and invalid/inaccessible citations are zero in the regression suite | verified | Deterministic evaluation, pytest AI suites and both model runs: 0 invalid citations, 0 leakage; stack: non-members and other case IDs get 404 for AI records. |
| AC5 | Human-reviewed claim support ≥ 90% on a versioned set of ≥ 30 questions; method, model and results published | **pending human review** | Method, dataset (33 questions), models, automated results and claim-level worksheets are published in `docs/testing/ai-evaluation/`. No human has reviewed the worksheets, so no support rate exists. |
| AC6 | A local-only case cannot be sent to a cloud provider | verified | Policy grants checked before every model call (tests); API refuses cloud requests for local-only cases (stack); evaluation cloud transport recorded 0 requests in both model runs. |
| AC7 | Malicious instructions in evidence cannot trigger external collection, writes or secret disclosure | verified | Deterministic and model runs q29/q30; stack: non-AI row and outbox counts unchanged, no secrets in answers, no query execution started; the model has no write, network or collection tools. |
| AC8 | Disabling AI does not prevent core collection and evidence browsing | verified | Stack: with `TRACEHOLLOW_AI_ENABLED=false`, evidence browsing, import, export and entity editing work and nothing is indexed until AI is re-enabled; pytest `test_collection_works_with_ai_disabled_and_is_queued_for_later_indexing` (a public web page collected with AI disabled, its text recorded as pending for later indexing, no AI work queued). |

The Phase 2 dependency is now satisfied: collected evidence is indexed and cited (stack AI stage in
`verify-phase2.sh`). The model-backed evaluation still uses imported synthetic evidence.

Architecture decisions: [ADR 0005](adr/0005-evidence-grounded-ai.md) (Phase 3) and
[ADR 0006](adr/0006-public-source-collection.md) (Phase 2).

## Commands and results (final code)

All commands were run from the repository root unless noted, on the final code of the branch.

| Command | Result |
| --- | --- |
| `cd services/api && uv sync --locked` | OK; default groups `dev` and `collectors` (sherlock-project 0.16.2 for contract tests) |
| `cd services/api && uv run ruff check .` / `uv run ruff format --check .` | All checks passed / 147 files already formatted |
| `cd services/api && uv run mypy` | Success: no issues found in 146 source files (strict) |
| `scripts/test-backend.sh` | **345 passed, 1 skipped** in 54.1 s (real PostgreSQL and Redis). Skipped: `test_guarded_transport_connects_to_the_checked_address_on_an_allowed_private_network`, because this machine cannot connect to its own tunnel interface address |
| `cd apps/web && pnpm lint` / `pnpm typecheck` / `pnpm test` / `pnpm build` | No findings / no errors / **53 passed** (10 files) / compiled, 21 routes including `/sources` |
| `docker compose config --quiet` (with and without `compose.verify-sources.yaml`) | Valid |
| `docker compose build collector`, then in the image (read-only, UID 10001) | `subfinder -version` → v2.16.0 (release zip SHA-256 verified during build); `sherlock-project` 0.16.2 importable; the api image does not contain the engines |
| Subfinder compatibility run (release binary in a container with `--network none`) | Exit 0 and no output when every source fails; per-source errors and "no API key" skips appear only in verbose stderr; key-based sources are skipped without requests |
| Sherlock compatibility run (library against a local HTTP server) | Status-code sites report "Available" for 501/403/429; message sites report "Claimed" for any page without the error text; the CLI calls the GitHub API for update checks at start |
| `scripts/verify-phase2.sh --e2e` | **All Phase 2 stack checks passed** (115 s; isolated project with the controlled `fixture-site` container): migration 0004; collector UID 10001 with pinned engines; only the collector on `collect-egress` and no host ports; web page with redirect, provenance, derived Turkish text without script content, 404 → `no_findings`, 403 → `access_denied`, truncation → `partial`; redirect to the metadata address and eight direct destinations refused with specific codes and no evidence; RSS with two pages, three unique entries and one repeated entry stored once, broken pagination → `partial` after retries, malformed feed → `parse_error`; GitHub-shaped API with account, two repository pages and quota, 404 → `no_findings`, rate limit → `rate_limited` with 3600 s retry and quota; Sherlock candidates 2, rate-limited 1, blocked 1 → `partial`, no relationships, absence on every platform → `no_findings`; Subfinder key-only source without key → `authentication_required`; stored GitHub token never returned, rejected token → `authentication_required` and marked rejected, removed; cancel during a slow request → `canceled` after 19 s with no evidence; second username run waited for the single slot; collected page text indexed and cited (synthetic AI provider); non-member 404s, non-admin credential write 403, health limited to the user's cases; Playwright sources workflow 1 passed; 28 runs executed by the collector and none by the worker; 9 refused destinations with no evidence; exactly 4 `no_findings`; all collected evidence has provenance; no secrets, passwords, tokens or collected content in 835 log lines |
| `scripts/verify-phase3.sh --e2e` | **All Phase 3 stack checks passed** (116 s, synthetic AI provider): pgvector job, migrations fresh and from 0002 with data, network isolation, grounded answer with passage equal to the original bytes, database count 3, abstention, cloud refusal, hostile evidence without writes, AI authorization, AI disabled, rebuild and cancel, summary and a reviewed suggestion, Playwright AI workflow 1 passed, evidence and case deletion, backup drill, full restore of a revision 0002 backup upgraded to 0004, no secrets or evidence text in 1274 log lines |
| `scripts/verify-phase3.sh --model` | **All Phase 3 stack checks passed with `qwen3:8b` and `qwen3-embedding:0.6b` through the ai-worker container** (279 s): grounded answer `answered` with a passage equal to the original bytes, count claim 3 from the database tool, abstention on the missing question, hostile evidence answered without writes or secrets, local model answer with two cited facts whose passages open verified evidence, Ollama-reported usage 1949 input / 308 output tokens. The model produced no relationship suggestion (allowed; recorded) |
| `scripts/verify-phase1.sh --e2e` | **All Phase 1 stack checks passed** on the final code (322 s; Playwright Phase 1 workflow 1 passed in 20.2 s; no secrets in 1473 log lines) |
| `scripts/verify-phase0.sh` | **All Phase 0 stack checks passed** on the final application code (75 s) |
| `scripts/ai-eval.sh --providers configured` (templates v1) | 27/33 questions passing all automated checks; invalid citations 0; leakage 0; cloud requests 0; numeric agreement 3/7; median 19.0 s per question ([run](testing/ai-evaluation/runs/2026-09-15-qwen3-8b-prompts-v1/summary.md)) |
| `scripts/ai-eval.sh --providers configured` (templates v2) | 30/33; invalid citations 0; leakage 0; cloud requests 0; numeric agreement 7/7; remaining failures q03 and q07 (unnecessary abstention), q24 (conflict not labelled); median 41.8 s, overlapping with Docker builds ([run](testing/ai-evaluation/runs/2026-09-15-qwen3-8b-prompts-v2/summary.md)) |
| Embedding request check against Ollama | `num_ctx` 8192 with `truncate: false` gives vectors identical to the default request (cosine 1.0); over-long input returns HTTP 400; model memory 2.9 GB instead of 5.8 GB |
| `graphify update .` (graphify 0.9.61) | Rebuilt after the final change: 2930 nodes, 10023 edges, 138 communities; no `secrets/` paths or secret values in the graph; `graphify-out/` stays git-ignored; community labels not refreshed (needs an LLM provider) |

## Defects found during verification and fixed

| Defect | How it was found | Fix |
| --- | --- | --- |
| `httpx2` was a development-only dependency; production worker images crashed on import | `verify-phase3.sh` on freshly built images | Runtime dependency; CI imports the application without development dependencies |
| Restoring an older backup over a newer schema failed (`pg_restore --clean` keeps newer tables) | Review while updating backup docs; now tested | Restore into a new database, check row counts, swap by rename; `verify-phase3.sh` restores a revision 0002 backup into the current schema |
| Synthetic AI fixture split sentences inside domain names and abbreviations, so relationship suggestions never appeared | `verify-phase3.sh` suggestion stage | Sentence splitter fixed with a regression test; the stack check now requires a suggestion |
| Sherlock reports blocks, rate limits and server errors as "username available" and login walls as "found" | Phase 2 engine compatibility check | Adapter reclassifies from HTTP status; tests pin the cases |
| Subfinder exits successfully with no output when every source fails | Compatibility check with the real binary without network | Adapter reads per-source messages; empty results with failed sources are never `no_findings` |
| A logging `extra` key (`name`) collided with a LogRecord attribute, failing credential storage with HTTP 500 | `verify-phase2.sh` (tests run with INFO logging disabled) | Renamed; AST-based test forbids reserved keys in all logging calls |
| Pages from single-label hosts (allowlisted intranet names) failed with an internal error during entity normalization | `verify-phase2.sh` | Identifiers are validated before writing; unnormalizable entities are skipped with a coverage note; regression test |
| The Phase 1 verifier ran every browser spec and accepted any "1 passed" line | Adding Phase 2 and 3 specs | Each verifier runs only its own spec and fails on failed or skipped tests |

## Unverified checks, blockers and known limitations

- **Phase 3 human review (AC5):** pending. A reviewer must complete
  `docs/testing/ai-evaluation/runs/2026-09-15-qwen3-8b-prompts-v2/worksheet.csv` following the README
  there; until then Phase 3 is not complete. Automated checks are not human review.
- **Live sources:** no connector has been run against a live source (no approved targets, tokens or
  budget). GitHub API behaviour, Sherlock's platform responses and Subfinder's passive sources are
  covered by documentation, recorded engine output and controlled fixtures only. Fixture behaviour
  can differ from live sources, especially for username detection.
- **Cloud AI provider:** implemented against Anthropic's documentation and mocked responses; never
  called live (no key).
- **Model evaluation:** one small synthetic corpus, one local model (`qwen3:8b`), one machine, one run
  per prompt version; the v2 run overlapped with Docker builds, so its timings are not clean.
- **CI not executed:** `.github/workflows/ci.yml` now runs the Phase 0-3 stack verifiers with browser
  tests, but nothing has been pushed; runner-specific issues (Linux `host-gateway`, Docker Engine,
  arm64 vs amd64 Subfinder checksum paths, time limits) remain possible.
- **Platforms:** verified on macOS with Docker Desktop only. The collector image's Subfinder download
  is checksum-pinned for amd64 and arm64, but only arm64 was built.
- **SSRF residuals:** Subfinder's own HTTP client does not use the Tracehollow network policy (fixed
  provider endpoints only). The one real-socket guarded-transport unit test was skipped on this
  machine; the stack verification exercised guarded connections for real.
- **Collection privacy:** direct requests and platform probes reveal the collector's IP address and
  the searched input; there is no anonymizing proxy option.
- **Credentials:** administrator-only, installation-wide; no re-encryption tool for key rotation;
  no connectivity check button (problems surface as run outcomes).
- **Caching and monitoring:** no response caching or conditional requests; scheduled reruns and
  change detection are Phase 5.
- **Graphify:** see the commands table.
- Earlier limitations that still apply: team management, audit events, MFA, TLS termination,
  `'unsafe-inline'` scripts in the Next.js CSP, exports without redaction, deletion not reaching
  earlier backups (see the Phase 1 and Phase 0 records below).
- **Owner decisions still open:** MIT `LICENSE` versus PRD §13's Apache-2.0 proposal; enabling
  GitHub private vulnerability reporting; approval of live smoke-check targets.

## Next bounded task

**Human review of the Phase 3 evaluation (the remaining Phase 3 criterion).** A reviewer who did not
write the dataset scores every claim in the v2 worksheet using the published rubric; record the claim
support rate, abstention accuracy and disagreements in `docs/testing/ai-evaluation/`. If support is
below 90%, change prompts or retrieval, re-run `scripts/ai-eval.sh` and review again.

In parallel, if the owner approves specific live targets: run one live smoke check per connector as
documented in `docs/connectors/README.md` (for example an owned web page and feed, an owned GitHub
account with a token, an owned username on three platforms and an owned domain with crt.sh) and
record the results before any connector is labelled live-verified. Phase 4 should not start before
both are done.

---

## Phase 1 record

Phase 1 (cases, evidence and query lifecycle) was verified on 2026-09-15 on branch
`feat/phase-1-cases-evidence-queries`. The evidence below is kept as recorded then; current commands
and results are in the table above. The Phase 1 "next bounded task" (Phase 2) has since been
implemented.

### Phase 0 prerequisite check

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

### Phase 1 acceptance checklist (PRD §12)

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

### Phase 1 deliverables and locations

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

### Commands and results (Phase 1, final code)

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

### Unverified checks, blockers and known limitations

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
