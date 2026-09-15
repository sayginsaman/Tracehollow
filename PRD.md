# OSINT Workspace — Product Requirements Document (Name: Tracehollow)

Status: implementation-ready specification; no application functionality has been implemented or verified by this document.

Version: 1.0

Research baseline: 2026-09-15

Working product name: OSINT Workspace. Final branding remains open.

## 1. Product summary

Build an open-source, self-hosted OSINT investigation workspace that runs locally through Docker Compose. Analysts create cases, collect information from public or explicitly authorized sources, retain query history and evidence in local storage, explore relationships, compare observations over time, and ask an AI assistant questions grounded in case evidence.

The product must explain what it found, where it came from, when it was collected, which collection attempts failed, and which conclusions remain uncertain.

The application is local-first, not necessarily offline: live collection contacts external sources. Local AI must be available without a cloud model subscription. Cloud model connections are optional and must disclose which case material leaves the machine.

## 2. Users and jobs to be done

### Primary users

- Security researchers investigating public digital infrastructure and online assets.
- Threat intelligence analysts correlating public reports with domains, URLs, IPs, organizations, and accounts.
- Investigative researchers organizing public documents and traceable findings.
- Individuals reviewing their own public digital footprint.

### Core jobs

1. Start a case around an organization, domain, account, username, or document collection.
2. Select relevant sources and understand their access and cost requirements.
3. Run a collection job and see progress, partial results, and actionable failures.
4. Preserve evidence and reopen it after restarting the application.
5. Follow a relationship from one entity to another and inspect supporting evidence.
6. Repeat a query and compare observations without overwriting history.
7. Ask questions whose answers cite the supporting case evidence.
8. Export a reproducible investigation package with appropriate redaction.

## 3. Scope and boundaries

### In scope

- Public web pages, RSS, public APIs, and user-supplied material the user is authorized to process.
- Public account discovery and platform-specific collection within actual source capabilities.
- Domain, IP, URL, company, document, and cybersecurity indicator research.
- Local case management, evidence storage, relationship exploration, and query history.
- AI summaries, structured extraction, contradictions, case Q&amp;A, and report drafts.
- Optional provider credentials, local models, and paid data integrations.

### Outside the product scope

- Access to private accounts, private messages, or restricted groups without authorization.
- Authentication bypass, CAPTCHA evasion, session theft, credential harvesting, or account takeover.
- WhatsApp private-message discovery or decryption from a phone number.
- Exploitation, credential attacks, and indiscriminate active network scanning.
- Automatic attribution of multiple accounts to one person based only on username similarity.
- Automatic determination of criminality, identity, intent, or sensitive personal traits.
- An autonomous agent with arbitrary shell, SQL, network, or database-write access.
- Automatic external publication or sharing of case data.
- A promise that all supported platforms are free, keyless, complete, or always accessible.

Authorized imports must be labeled as imports rather than public-source collection. Passive third-party lookups and direct requests to a target are different collection modes and must be distinguishable.

## 4. Product principles

1. Evidence precedes interpretation: source observations, model suggestions, and analyst decisions are separate records.
2. Unknown is a valid result: access failure must never become a negative finding.
3. Local data control: no mandatory SaaS database, authentication, analytics, or model service.
4. Modular collection: a broken connector must not break unrelated jobs or the case workspace.
5. Reproducibility: retain inputs, connector versions, timestamps, model identifiers, and evidence references.
6. Honest coverage: incomplete pagination, blocked sources, stale data, and missing fields remain visible.
7. Small operational footprint: PostgreSQL is the initial system of record, including vectors and relationships.
8. Progressive delivery: finish and verify each phase before expanding scope.

## 5. Release definitions

| Milestone | Included phases | Claim permitted after acceptance |

| --- | --- | --- |

| Foundation | Phase 0 | Local development and deployment foundation |

| Core alpha | Phases 0–2 | Persistent investigation workspace with bounded public-source collection |

| AI MVP | Phases 0–3 | Source-grounded AI over collected case evidence |

| Social beta | Phases 0–4 | Documented social connectors and authorized imports |

| Full v1.0 | Phases 0–6 | Verified release with monitoring, interoperability, and release documentation |

Instagram collection and WhatsApp imports are required for the full product vision, but are not prerequisites for proving the core AI MVP. Never describe the MVP as supporting platforms that only appear on the roadmap.

## 6. Functional requirements

### FR-01 — Cases and access

- Create, rename, archive, restore, and deliberately delete cases.
- Store a case purpose, target scope, tags, and lifecycle status.
- Every case-owned record has a case identifier and is access-controlled on the server.
- Initial release supports one local administrator; later phases extend to team roles.
- Case deletion removes associated originals, derived text, vectors, reports, and case-specific AI caches through an observable deletion job. Backup retention is documented separately.

### FR-02 — Query definitions and executions

- A saved query contains input type/value, connector selection, filters, collection mode, and limits.
- An execution references the saved query and records a snapshot of those parameters.
- Rerunning creates a new execution; previous results remain addressable.
- Record actor, start/end time, connector/configuration versions, quota usage, estimated/actual provider cost when available, and result coverage.
- Cancellation is persisted and checked between bounded requests or units of work. Already completed evidence remains available.
- Terminal job states: completed, partial, failed, canceled. Nonterminal states: queued and running.

### FR-03 — Connector results and health

Connector outcome vocabulary:

| Outcome | Meaning |

| --- | --- |

| findings | Usable observations were returned |

| no_findings | A successful, supported lookup returned no matches within its stated coverage |

| partial | Some usable data exists but collection was incomplete |

| authentication_required | Credentials or a user-controlled session are missing/expired |

| access_denied | The source refused access |

| rate_limited | A provider limit was reached; retry information is retained |

| unsupported | The requested input or operation is not supported |

| unavailable | A temporary source/network problem prevented completion |

| parse_error | Source data could not be interpreted using the expected schema |

| canceled | The user stopped this operation |

Never collapse these outcomes into an empty array. A missing profile is not inferred from HTTP 200 alone, a login wall, or an unvalidated 404 response.

Each connector declares its ID/version, supported inputs, collection mode, credential requirements, known coverage, limits, output schema, timeout, retry policy, cost model if available, and last verification date.

### FR-04 — Entities, observations, and assertions

Initial entity types: organization, domain, IP, URL, username, platform account, email, phone, document, and event. Not all types require an automatic collector in the MVP.

- Use stable platform IDs when available. Usernames and display names may change.
- Account identity is platform-scoped. Preserve original values alongside normalized values.
- Model shared email, phone, domain, and name values as evidence of relationships; do not automatically merge identities.
- Preserve independent conflicting observations.
- Relationships carry predicate, source/target, supporting observation IDs, temporal information, origin, and review status.
- Origins: observed, deterministic derivation, AI suggestion, analyst assertion.
- Review states: unreviewed, accepted, rejected, superseded.
- A model-generated numeric confidence is not a calibrated probability. Show the basis for any score and keep source reliability separate from assertion confidence.

### FR-05 — Evidence and provenance

- Store collection URL or import origin, retrieval timestamp, source publication timestamp when known, content type, hash, connector version, and access category.
- Distinguish event time, publication time, collection time, and processing time.
- Preserve UTC internally, plus original timezone/precision when available.
- Retain raw JSON/text or permitted snapshots separately from normalized observations.
- Evidence content is not silently overwritten. New collection creates a new evidence version.
- A SHA-256 hash detects changes to stored bytes; it does not independently prove authorship or authenticity.
- Parsed text and document chunks reference original evidence and page/section offsets where available.
- Serve untrusted HTML/documents through a safe preview boundary, never executable in the application origin.
- Source retention constraints apply to originals and derived content.

### FR-06 — Search and graph

- Exact filtering by entity type, source, date range, tags, and outcome.
- Full-text search for names, identifiers, captions, notes, and document text.
- Semantic search is added with AI indexing in Phase 3.
- Relationship graph supports selection, expansion, filtering, and opening the evidence behind an edge.
- Use pagination and bounded graph expansion; never load the complete database into a browser graph by default.
- Show suggested and accepted relationships distinctly.

### FR-07 — AI

- Provider-independent interface supporting a local Ollama connection and an optional cloud provider.
- Provider secrets stay server-side and are excluded from evidence, browser responses, model prompts, and logs.
- A case can forbid cloud processing; enforce the setting server-side before any provider request.
- Retrieval is restricted to the current case and the authenticated user's permissions before ranking or generation.
- Use SQL-backed read tools for counts, dates, and exact filtering. Use hybrid text/vector retrieval for contextual answers.
- Every substantive factual claim in a generated report or answer must cite evidence, be explicitly labeled inference, or state that evidence is insufficient.
- Validate citation existence and access. Evaluate whether cited evidence actually supports the claim; existence alone is not sufficient.
- Record model/provider ID, prompt template version, referenced evidence/chunks, timestamp, usage, and review status.
- External source text is untrusted data, not instructions. Retrieval must not grant tool permissions.
- The model may propose bounded collection plans from a registered allowlist. It may not invent connectors or execute arbitrary code.
- Editing findings, accepting identity merges, transmitting files, or launching new paid collection requires explicit user action.
- Imported conversations are never used as instructions to the assistant.

### FR-08 — Comparison and monitoring

- Compare executions using source object IDs and normalized fields.
- Separate newly observed data from confirmed changes to previously observed data.
- A missing item in a partial or failed run is unknown, not deleted.
- Scheduled monitoring has a scope, interval, budget, retention setting, and cancellation control.
- Notify only on a meaningful change, failure needing action, or explicit requested status.
- Outbound notification destinations are opt-in and redaction-aware.

### FR-09 — Exports

- Core alpha: JSON and CSV with source/evidence references and manifest.
- Social beta: safe standalone HTML report; PDF export is optional until verified.
- Phase 5: a documented subset of STIX 2.1 and optional MISP/OpenCTI adapters.
- Escape spreadsheet formulas in CSV exports and sanitize HTML reports.
- Offer redaction before generating shareable exports. Exclude secrets unconditionally.
- Include generation date, source dates, coverage gaps, and AI inference labels.
- Never claim full STIX compatibility unless supported types and round trips are tested.

## 7. Platform and source strategy

### Initial connectors

| Source | Delivery | Access model | Acceptance boundary |

| --- | --- | --- | --- |

| Public web page | Phase 2 | Bounded direct retrieval | Text and provenance; redirects and SSRF defenses tested |

| RSS/Atom | Phase 2 | Public feed | Entry IDs, dates, URLs, deduplication and pagination/coverage |

| GitHub | Phase 2 | Official API, optional token | Public repository/account data; explicit quota handling |

| Username discovery | Phase 2 | Maigret or Sherlock adapter after compatibility check | Candidate accounts, never automatic person attribution |

| Domain discovery | Phase 2 | Subfinder or passive BBOT profile | Scope-limited results with source provenance |

| Instagram | Phase 4 | Separate official API and optional unofficial adapter paths | Document actual account types, fields, session needs, cost and limits |

| Telegram | Phase 4 | Current supported client/API | Explicit accessible sources; no automatic private-group joining |

| YouTube | Phase 4 | Official Data API | Public metadata/comments within quota; no unrestricted transcript promise |

| WhatsApp import | Phase 4 | User-provided authorized export | Robust parsing, timestamps, attachments and local provenance |

| Document import | Phase 1 basic; Phase 4 advanced | User-provided files | Basic text/JSON first, bounded PDF/OCR later |

Maigret versus Sherlock and Subfinder versus BBOT are adapter choices to validate before Phase 2, not requirements to install both. Prefer structured APIs/output and isolated execution over copying upstream internals.

### Later optional integrations

Bluesky, Mastodon, approved Reddit access, paid X API, eligible TikTok research access, permitted LinkedIn APIs, authorized Discord bot access, VirusTotal, [urlscan.io](http://urlscan.io), Censys, Shodan, GreyNoise, AbuseIPDB, HIBP, OpenCorporates, GLEIF, OpenSanctions, GDELT, ArchiveBox, and Browsertrix.

Each requires a connector-specific capability, licensing, cost, storage, and access review. Their presence in this PRD does not authorize live queries or imply a working implementation.

### Instagram requirements

- Represent official API support separately from unofficial scraping or third-party data providers.
- Never advertise access to all personal accounts through the official API.
- Instaloader and Osintgram 2.0 are candidates, not tested dependencies.
- Paid provider requests require a visible budget; local AI does not make collection local or free.
- No automatic browser-cookie extraction. Sessions are deliberately supplied/configured by the user.
- Authentication walls and incomplete follower/comment access produce explicit coverage outcomes.

### WhatsApp requirements

- Publicly published contact links may be collected as ordinary web observations.
- Authorized exported chats can be imported, searched, summarized, and placed on a timeline.
- Parse locale-dependent timestamps, multiline messages, system events, attachments, and malformed input.
- Do not equate a saved contact label with a verified person or phone identity.
- Cloud API is business messaging infrastructure, not a general private-conversation research API.
- Public channel collection remains an experimental future capability until supported access is verified.

## 8. Technical architecture

### Default stack

- Frontend: Next.js App Router, React, TypeScript, Tailwind CSS, accessible components, Cytoscape.js for bounded graphs.
- API: Python, FastAPI, Pydantic, SQLAlchemy, Alembic, psycopg.
- Database: PostgreSQL with pgvector. Use JSONB for source-specific attributes, not as a substitute for the core relational model.
- Background work: Celery with Redis as broker. PostgreSQL remains authoritative for execution state and results.
- Evidence: a dedicated local filesystem volume behind an application storage interface. S3-compatible storage is a later adapter.
- AI: small provider interface; select one of Haystack, LlamaIndex, or a minimal explicit pipeline after Phase 3 requirements are tested. Do not install all three by default.
- Packaging: Docker Compose, Python dependency lock, one JavaScript package manager and lockfile.
- Testing: pytest for backend, an appropriate TypeScript test runner for frontend, Playwright for meaningful end-to-end flows.

Choose currently supported compatible versions during implementation and record them in lockfiles and image tags/digests. Do not use unpinned `latest` images in release configuration. Record major deviations in an architecture decision record.

### Deployment services

Core services: web, api, worker, postgres, redis. Optional AI and advanced-document profiles must not be required to start the core workspace.

- Bind user-facing ports to 127.0.0.1 by default.
- Do not publish PostgreSQL or Redis host ports by default.
- Require a local administrator setup and authenticated API access from Phase 0.
- Use generated/configured secrets with no shared default password.
- Persist database and evidence independently of disposable application containers.
- Configure Ollama base URL so the model may run on the host or a supported optional container profile. Do not require Linux-only GPU passthrough for macOS users.
- Disable optional telemetry by default where supported; document unavoidable external requests.

### Data flow

1. Validate case access and query scope.
2. Persist execution and a dispatch/outbox record in one database transaction.
3. Dispatch a bounded task. Recover dispatch after a process or broker restart.
4. Apply connector validation, budget reservation, timeout and rate limits.
5. Retrieve data; write evidence and structured outcomes.
6. Normalize observations and create candidate relationships idempotently.
7. Update authoritative execution state and expose progress via SSE or bounded polling.
8. Index eligible text for case-scoped retrieval.
9. Generate source-grounded answers and reports without mutating evidence.

Assume at-least-once task delivery. Duplicate delivery must not duplicate observations, consume reserved budget twice, or create conflicting terminal states. Document filesystem/database recovery for a crash between file creation and metadata persistence.

## 9. Minimum data model

| Record | Responsibility |

| --- | --- |

| users / sessions | Local authentication and session lifecycle |

| cases / case_members | Scope, lifecycle, ownership and later team access |

| entities | Case-scoped normalized investigation objects |

| entity_identifiers | Platform IDs, usernames, original/normalized identifiers |

| saved_queries | Reusable source selections, inputs and limits |

| query_runs | Immutable execution parameter snapshots and lifecycle |

| connector_runs | Per-source outcomes, coverage, retry and cost records |

| dispatch_outbox | Durable handoff from database transaction to queue |

| observations | Source-specific timestamped facts |

| evidence_objects | Original bytes metadata, storage key, hash and provenance |

| relationships | Typed edges, origin and review state |

| relationship_evidence | Supporting or contradicting observation references |

| notes / analyst_decisions | Human interpretation and correction history |

| document_chunks | Derived text, original offsets and embedding version |

| ai_conversations / ai_messages | Case-scoped Q&amp;A with evidence references |

| ai_runs / citations | Model configuration, cited context and usage |

| integration_credentials | Encrypted secrets with keys held outside the database |

| monitors / change_events | Later schedules, comparisons and meaningful events |

| audit_events | Access and mutation history without secrets or unnecessary content |

Build tables when their phase requires them. Phase 0 must not create a speculative implementation of the complete schema.

## 10. Screens and interaction requirements

1. Setup/login: secure first-run initialization and clear dependency errors.
2. Cases: list, filters, create/archive, useful empty state.
3. Case overview: scope, source coverage, recent runs and reviewed findings.
4. New query: input type, sources, access needs, limits and cost notice.
5. Run detail: per-source progress, usable partial results, cancel/retry.
6. Entity detail: identifiers, observations, conflicts and evidence.
7. Graph: bounded expansion and inspectable edges.
8. Timeline: distinguish event and collection times.
9. Evidence: safe previews, origin, hash and source version.
10. AI workspace: cited answers, insufficient-evidence state and provider indicator.
11. History/comparison: repeat queries and review meaningful differences.
12. Integrations/settings: credentials, connectivity checks, quotas and source health.

Use English as the initial repository and interface language, with text organized for later localization. Make identifiers, dates, filenames, and parsing Unicode-safe. Turkish content and characters are included in test fixtures.

Design direction: a readable, restrained analyst workspace with dense tables and progressive disclosure. Prioritize typography, keyboard access, clear statuses and evidence navigation. Avoid decorative threat maps, fabricated metrics, and terminal effects that imply capabilities the backend does not have.

## 11. Security and operational requirements

- Authentication, case access checks, CSRF protections appropriate to the session mechanism, restricted CORS, and secret redaction from the foundation.
- Use a reviewed password-hashing/session approach. Do not invent cryptography.
- Store connector credentials encrypted; hold the encryption key outside PostgreSQL and document secure backup/rotation.
- Scope cache keys by case/authorization context as needed. No case data leakage through search, vectors, reports, caches, or logs.
- Protect URL collection against SSRF: reject local/private/link-local and metadata endpoints; validate resolved addresses and every redirect, including IPv6; apply equivalent controls to browser collectors.
- Bound response size, redirects, download time, concurrency, retries, document pages, extracted text and archive expansion.
- Normalize uploaded storage paths; prevent traversal, archive extraction escapes, zip bombs and executable previews.
- Never interpolate user input into shell commands. Use argument arrays and connector-specific validation.
- Run services with least privilege where practical. Do not mount the Docker socket into collectors.
- Backoff on 429/temporary errors; avoid automatic bypass of source controls.
- Keep passive lookups distinct from target-contacting operations and content uploads to third parties.
- Provide backup/restore instructions for PostgreSQL, evidence and separately protected secret material. Test restore, not just backup creation.
- On deletion, remove derivatives and retrieval indexes. Explain what remains in independently retained backups.
- Collect no telemetry or external notifications by default.

## 12. Delivery phases

### Phase 0 — Repository and secure local foundation

Goal: a new contributor can start the application, authenticate, and verify its dependencies.

Deliverables:

- Repository layout, root documentation, development commands, dependency locks.
- Next.js shell with real setup/login and environment status, without invented case metrics.
- FastAPI application, configuration validation, structured redacted logging.
- PostgreSQL migration foundation, Redis broker, Celery worker, persistent volumes.
- Liveness endpoint that tests process health; readiness endpoint that checks required API dependencies and returns non-success when unavailable.
- Separate visible worker status; API readiness alone must not claim workers are healthy.
- Secure local administrator bootstrap, session lifecycle, secret generation/configuration.
- `.env.example`, `.gitignore`, [`SECURITY.md`](http://SECURITY.md), [`CONTRIBUTING.md`](http://CONTRIBUTING.md), setup/backup instructions and initial CI.
- `docs/[STATUS.md](http://STATUS.md)` tracking actual progress, evidence and blockers.

Acceptance:

1. Documented setup starts core services without paid APIs or an LLM.
2. Fresh-database migrations run; repeated startup is safe and non-destructive.
3. Login succeeds with configured credentials; invalid credentials fail; protected routes reject unauthenticated access; logout invalidates the session.
4. Readiness changes appropriately when a required dependency is unavailable.
5. A minimal background integration task verifies broker-to-worker connectivity without pretending to be an OSINT run.
6. A database record persists across normal Compose down/up without volume deletion.
7. Service bindings, secret handling and health information match documentation.
8. Backend/frontend checks, production build and Compose validation pass in the available environment; unrun checks are explicitly recorded.

Exclude collectors, business dashboards, evidence graphs, embeddings, AI orchestration and social platform integrations from this phase.

### Phase 1 — Cases, evidence and query lifecycle

Depends on: Phase 0.

Deliverables:

- Cases, ownership, manual entities, relationships, notes, evidence records.
- Text/JSON imports with safe storage, hash/provenance and explicit size limits.
- Saved queries, execution snapshots, connector outcomes, outbox/dispatch and cancellation state.
- Case table/detail, evidence view, simple bounded graph and history UI.
- Internal deterministic fixture connector for tests/demo only, visibly labeled synthetic.
- JSON/CSV export and deletion workflow.

Acceptance:

1. Create a case, import sample evidence, link entities, restart, and reopen the same records.
2. Two runs of one query remain independently addressable.
3. Duplicate task delivery creates no duplicate logical result; queued work can recover after interruption.
4. Cancellation preserves collected evidence and results in accurate terminal status.
5. Evidence cannot be fetched through another unauthorized case/user route.
6. Exports preserve provenance, contain no secrets and neutralize CSV formulas.
7. Case deletion removes case-owned evidence and derived records; failure is visible and retryable.

### Phase 2 — Public-source collection and source reliability

Depends on: Phase 1.

Deliverables:

- Connector SDK/registry and source capability UI.
- Public web, RSS/Atom, GitHub, one username engine and one domain engine.
- Per-source limits, concurrency, retry/backoff, cache behavior and scope validation.
- Incremental run progress and explicit partial/failure states.
- Connector documentation and fixture-based contract tests.

Acceptance:

1. A case collects controlled public-source examples and records source/provenance for each observation.
2. Tests cover success, verified no findings, 401/403, 429, timeout, malformed content and partial pagination.
3. Domain collection remains within the chosen passive scope; username hits remain candidate accounts.
4. SSRF and redirect protections reject prohibited destinations.
5. Live smoke checks run only against approved/controlled targets with available credentials and budgets. Fixture tests alone do not earn a live-verified badge.
6. Missing credentials yield an actionable state, not fabricated or empty success.

### Phase 3 — Evidence-grounded AI MVP

Depends on: Phase 2.

Deliverables:

- Chunking, embedding versioning, pgvector indexing and hybrid retrieval.
- Local Ollama provider and optional configurable cloud provider.
- Case Q&amp;A, source citations, deterministic read tools, summaries and relationship suggestions.
- Evidence/model usage records and an analyst review flow.
- Synthetic evaluation dataset including Turkish text, absent answers, conflicting sources and hostile source instructions.

Acceptance:

1. A source-grounded answer opens the exact supporting evidence/chunk.
2. Numeric answers agree with database queries in the evaluation dataset.
3. Missing evidence produces an explicit insufficient-evidence answer.
4. Cross-case retrieval leakage and invalid/inaccessible citation references are zero in the regression suite.
5. Human-reviewed claim support is at least 90% on a versioned evaluation set of at least 30 questions; publish method, model and results. This is a release target, not a current measured claim.
6. A local-only case cannot be sent to a cloud provider.
7. Malicious instructions embedded in evidence cannot trigger external collection, writes or secret disclosure.
8. Disabling AI does not prevent core collection and evidence browsing.

### Phase 4 — Social research and advanced imports

Depends on: Phase 3.

Deliverables:

- Instagram adapter with explicit official/unofficial/provider capabilities.
- Telegram and YouTube adapters with current access constraints.
- WhatsApp authorized export importer, attachments and timeline.
- PDF/document processing with bounded optional OCR.
- Entity comparison, temporal observations and safe standalone HTML reports.

Acceptance:

1. Instagram access errors, login walls and incomplete results are distinguishable; no unsupported full-account claims.
2. Missing social API access can block a particular connector's verification without falsifying its status.
3. WhatsApp tests cover multiple date formats, multiline content, system messages, Turkish characters, timezone ambiguity and missing attachments.
4. Import provenance remains distinct from public-source provenance.
5. Safe preview/export tests cover hostile HTML and filenames.
6. Reports preserve uncertainty and evidence citations after export.

### Phase 5 — Monitoring, teams and interoperability

Depends on: Phase 4.

Deliverables:

- Scheduled reruns, bounded budgets, change detection and in-app notifications.
- Optional explicitly configured external notification adapters.
- Team roles: administrator, analyst, viewer; case-level membership.
- Documented STIX subset and optional MISP/OpenCTI exchange.
- Audit trail, retention controls and deletion/restore operating procedures.

Acceptance:

1. A repeated run identifies a real controlled change and avoids a false deletion during partial collection.
2. Schedules survive restart and avoid duplicate dispatch.
3. Budgets and cancellation work across concurrent jobs.
4. Viewer/analyst/admin permissions are tested at API level, including AI and export routes.
5. Supported exchange types pass round-trip tests; unsupported mappings remain explicit.
6. External notifications send only configured, redacted content to user-selected destinations.

### Phase 6 — Open-source v1.0 release readiness

Depends on: Phase 5 and all required supported-source checks.

Deliverables:

- Fresh-install and upgrade verification, backup/restore drill, release images and changelog.
- User quickstart, operator guide, connector author guide, architecture decisions and capability matrix.
- Synthetic demo dataset and screenshots from the real application.
- Dependency/license inventory, third-party notices, chosen project license and security disclosure policy.
- Accessibility review, representative performance measurements and documented hardware profiles.
- Release checklist and known limitations.

Acceptance:

1. A clean checkout can be installed following only the documentation.
2. Database/evidence restore reconstructs the verified sample case and hashes.
3. No real investigation data, credentials or unredacted logs appear in the repository or release artifacts.
4. Versioned integration coverage distinguishes implemented, fixture-tested, live-verified, experimental and unavailable sources.
5. Core flows work by keyboard; major states are readable without relying on color alone.
6. Performance is measured on a stated machine/dataset; publish results and bottlenecks instead of unsupported speed claims.
7. Required checks pass on the release candidate. Any material failed requirement prevents claiming v1.0 completion.

## 13. Licensing and reuse decisions

Default proposal for original project code: Apache-2.0, subject to compatibility review before release. This is a proposal, not authorization to relicense third-party code.

- Prefer documented interfaces to copying existing framework source.
- Inventory code licenses, dataset licenses, provider terms and model licenses separately.
- A separate process or API adapter does not automatically remove redistribution or other license obligations.
- Preserve upstream notices and assess bundled distributions.
- Do not copy code from repositories with contradictory license declarations until resolved.
- OpenCTI Community and Enterprise code have different licenses.
- OSIF's README and [LICENSE.md](http://LICENSE.md) disagreed during research; treat reuse as unresolved.
- Legacy Aleph maintenance ended in December 2025; use it as a design reference, not an assumed supported dependency.

## 14. Verification and definition of done

For every completed requirement, record implementation location, verification command/scenario, result and remaining limitation in `docs/[STATUS.md](http://STATUS.md)` or linked test documentation.

Required test categories, introduced with the relevant feature:

- Configuration, authentication and permission boundaries.
- Database migrations and persistence.
- Queue interruption, duplicate delivery, cancellation and partial jobs.
- Connector contracts and truthful source outcomes.
- URL/file safety and safe exports.
- Evidence provenance and entity matching behavior.
- Case-scoped AI retrieval, support quality and injection resistance.
- Backup/restore and clean installation.

Use deterministic fixtures for CI. Network tests are separate, opt-in and bounded. Passing a mocked integration does not prove live provider compatibility.

A phase is complete only when its acceptance criteria are satisfied and evidence is recorded. Missing Docker, credentials, hardware, or platform permission must be reported accurately; continue independent work, but do not mark blocked checks as passed.

## 15. Research references

These references informed the design and must be rechecked when selecting versions and implementing providers. Stars and README claims are not production validation.

- [IntelOwl]([https://github.com/intelowlproject/IntelOwl](https://github.com/intelowlproject/IntelOwl)): modular enrichment and workflow reference.
- [Taranis AI]([https://github.com/taranis-ai/taranis-ai](https://github.com/taranis-ai/taranis-ai)): AI-assisted collection-to-report workflow.
- [OpenCTI architecture]([https://docs.opencti.io/latest/deployment/overview/](https://docs.opencti.io/latest/deployment/overview/)) and [license]([https://github.com/OpenCTI-Platform/opencti/blob/master/LICENSE](https://github.com/OpenCTI-Platform/opencti/blob/master/LICENSE)): CTI relationships and edition boundaries.
- [SpiderFoot]([https://github.com/smicallef/spiderfoot](https://github.com/smicallef/spiderfoot)): event-driven OSINT collection reference; older mainline/release activity.
- [BBOT]([https://github.com/blacklanternsecurity/bbot](https://github.com/blacklanternsecurity/bbot)), [Subfinder]([https://github.com/projectdiscovery/subfinder](https://github.com/projectdiscovery/subfinder)): domain collection candidates.
- [Maigret]([https://github.com/soxoj/maigret](https://github.com/soxoj/maigret)), [Sherlock]([https://github.com/sherlock-project/sherlock](https://github.com/sherlock-project/sherlock)), [WhatsMyName]([https://github.com/WebBreacher/WhatsMyName](https://github.com/WebBreacher/WhatsMyName)): account discovery and dataset candidates.
- [Osintgram 2.0]([https://github.com/Datalux/Osintgram/releases/tag/2.0](https://github.com/Datalux/Osintgram/releases/tag/2.0)), [Instaloader]([https://github.com/instaloader/instaloader](https://github.com/instaloader/instaloader)): Instagram references, not verified integrations.
- [Meta Instagram API]([https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login](https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login)): account/access limitations.
- [Meta WhatsApp Cloud API]([https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)): business messaging boundary.
- [Telethon migration notice]([https://github.com/LonamiWebs/Telethon](https://github.com/LonamiWebs/Telethon)): repository moved to Codeberg; locate maintained source before pinning.
- [YouTube Data API]([https://developers.google.com/youtube/v3/docs](https://developers.google.com/youtube/v3/docs)): supported resources and quota behavior.
- [Scope Intelligence]([https://github.com/Samarth-23-eng/scope-intelligence](https://github.com/Samarth-23-eng/scope-intelligence)): comparable self-hosted architecture.
- [OSIF]([https://github.com/fr4nc1stein/osint-framework](https://github.com/fr4nc1stein/osint-framework)): case/graph reference with observed HIBP and licensing issues.
- [HIBP API]([https://haveibeenpwned.com/API/v3](https://haveibeenpwned.com/API/v3)): authenticated email lookup requirements.
- [Aleph]([https://github.com/alephdata/aleph](https://github.com/alephdata/aleph)), [FollowTheMoney]([https://github.com/alephdata/followthemoney](https://github.com/alephdata/followthemoney)): investigative data modeling references.
- [MISP]([https://github.com/MISP/MISP](https://github.com/MISP/MISP)), [DFIR-IRIS]([https://github.com/dfir-iris/iris-web](https://github.com/dfir-iris/iris-web)): exchange and case workflow references.
- [pgvector]([https://github.com/pgvector/pgvector](https://github.com/pgvector/pgvector)), [Cytoscape.js]([https://github.com/cytoscape/cytoscape.js](https://github.com/cytoscape/cytoscape.js)), [Ollama]([https://github.com/ollama/ollama](https://github.com/ollama/ollama)): implementation candidates.
- [Haystack]([https://github.com/deepset-ai/haystack](https://github.com/deepset-ai/haystack)), [LlamaIndex]([https://github.com/run-llama/llama_index](https://github.com/run-llama/llama_index)), [Docling]([https://github.com/docling-project/docling](https://github.com/docling-project/docling)): AI pipeline/document candidates.
- [ArchiveBox]([https://github.com/ArchiveBox/ArchiveBox](https://github.com/ArchiveBox/ArchiveBox)), [Browsertrix]([https://github.com/webrecorder/browsertrix](https://github.com/webrecorder/browsertrix)): optional archival integrations.
- [OSINT Framework]([https://github.com/lockfale/OSINT-Framework](https://github.com/lockfale/OSINT-Framework)), [Awesome OSINT]([https://github.com/jivoi/awesome-osint](https://github.com/jivoi/awesome-osint)), [Bellingcat Toolkit]([https://bellingcat.gitbook.io/toolkit](https://bellingcat.gitbook.io/toolkit)): source discovery catalogs.

