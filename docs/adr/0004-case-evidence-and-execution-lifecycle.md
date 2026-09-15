# ADR 0004: Case authorization, evidence storage and durable query execution

- Status: accepted
- Date: 2026-09-15
- Phase: 1

## Context

PRD Phase 1 adds cases, manual entities and relationships, bounded text and JSON evidence imports,
saved queries with separate executions, one synthetic fixture connector, exports and case deletion.
The PRD requires server-side authorization for every case record (including downloads, exports and
progress), provenance on every stored item, PostgreSQL as the authority for execution state, durable
dispatch, idempotent duplicate delivery, honest per-connector outcomes, cancellation that keeps
collected evidence, and no automatic merging of identities.

Phase 1 still has a single local administrator and no team management, so authorization must be
modelled for more than one user without building membership administration.

## Decision

### Authorization

- `case_members` links users to cases; the creator is the owner. Every case route loads the case
  through one dependency that joins on membership. A case that does not exist and a case the user is
  not a member of both return `404 case_not_found`, so identifiers cannot be probed.
- Child records (entities, relationships, evidence, notes, saved queries, runs) are always looked up
  with both the case id from the path and their own id, so an id from another case returns `404`.
- Writes use a stricter dependency: archived cases return `409 case_archived`; cases being deleted
  return `409 case_deletion_in_progress`.
- Deletion jobs are visible only to the user who requested them.

### Entities, identifiers and relationships

- Entity types follow PRD §7 exactly (organization, domain, IP, URL, username, platform account,
  email, phone, document, event). There is deliberately no person type.
- Identifiers store the original and a normalized value (IDNA domains, lower-cased email, canonical
  URLs and IPs, digits-only phones, Turkish-aware username folding of `I/İ/ı`). Only
  `platform_id` identifiers are unique per case (per platform). Equal usernames, emails or domains on
  different entities are shown as hints and never merged.
- Relationships carry `origin` (`observed`, `deterministic_derivation`, `ai_suggestion`,
  `analyst_assertion`), `review_status` and references to supporting or contradicting evidence or
  observations. Observations (connector output) and analyst assertions are separate records; review
  decisions are appended to `analyst_decisions` with the previous and new value.

### Evidence storage

- Bytes on the `evidence-data` volume under server-generated UUID paths; metadata in PostgreSQL.
  Writes stage, promote without overwrite and then commit; failed commits remove the file; stale
  staged files and orphans are handled by reconciliation (details in
  [evidence-storage.md](../operations/evidence-storage.md)).
- Every read re-verifies size and SHA-256. Previews are returned as JSON text for plain-text
  rendering; downloads are sandboxed `application/octet-stream` attachments.
- Imports record `acquisition_method = authorized_import`, a required import origin, optional source
  reference and source publication date (stored in UTC with the original string). Fixture pages
  record `synthetic_fixture` and are labelled synthetic in their content, metadata and UI.

### Saved queries and executions

- A saved query is a definition. `POST …/saved-queries/{id}/runs` creates a `query_runs` row with an
  immutable parameter snapshot and one `connector_runs` row per connector, in the same transaction
  as a `dispatch_outbox` row.
- **Dispatch:** the API publishes the Celery message after commit. If Redis is unavailable the row
  stays `pending`. The `dispatcher` service publishes due rows, re-arms rows whose run is still
  queued long after dispatch (exponential backoff), and rows whose running run has an expired lease.
- **Execution:** a worker claims a run with one conditional `UPDATE` (queued, or running with an
  expired lease and fewer than `run_max_claims` claims) that sets a fresh lease token. Duplicate or
  late messages find nothing to claim and exit. Each page is persisted in one transaction guarded by
  the lease token: evidence file and row, observations, platform-account entities (serialized by
  advisory locks on the platform id), observed relationships (`ON CONFLICT` on the observed edge)
  and progress. `(connector_run_id, page_index)` is unique, so a reclaimed run resumes after the
  last committed page without duplicates.
- **Outcomes:** connectors report PRD outcomes (`findings`, `no_findings`, `partial`, `unavailable`,
  `rate_limited`, `authentication_required`, `access_denied`, `parse_error`, `unsupported`,
  `canceled`, …). Retryable outcomes follow the connector's retry policy. The run status is
  `canceled` if cancellation was requested, `completed` if every connector completed, `failed` if
  every connector failed, otherwise `partial`.
- **Cancellation:** stored as `cancel_requested_at`; workers check it before each page and before
  retries. Queued runs are canceled immediately. Pages committed before the cancel are kept.
- **Internal failures:** an unexpected exception marks the run `failed` with `internal_error`; a run
  whose worker keeps disappearing is failed with `worker_lost` after `run_max_claims` claims, so no
  run stays `running` forever.

### Fixture connector

`synthetic.fixture` 1.0.0 generates deterministic data from the query input using `.example`
domains, performs no network access and exposes scenarios for findings, no findings, partial
coverage, failure, transient failure with retry, rate limiting, authentication and access errors,
parse errors and slow pages for cancellation.

### Exports and deletion

- JSON and CSV exports are built from explicit column allowlists (no password hashes, sessions,
  lease tokens, storage keys or configuration). A manifest records format version, generator and
  schema revision, record counts, source date ranges, acquisition methods, synthetic-data presence,
  coverage gaps from non-successful connector outcomes, and a SHA-256 of the data (JSON) or of each
  file (CSV). CSV cells starting with `=`, `+`, `-`, `@`, tab or carriage return (including
  full-width variants) are prefixed with `'`.
- Case deletion requires the exact case title, marks the case `deleting` and runs as an outbox
  job: cancel executions, remove files, delete rows by cascade, verify, record counts. Failures are
  visible and retryable.

## Alternatives considered

- **Celery result backend or Redis as execution state:** loses state on broker loss and splits the
  source of truth. Rejected; Redis carries messages only.
- **Publishing directly inside the request transaction:** a message can arrive before commit, or be
  lost when the broker is down after commit. Rejected in favour of the outbox.
- **Celery `acks_late` redelivery alone for crash recovery:** Redis redelivers unacknowledged
  messages only after the visibility timeout (one hour) and gives no way to bound retries. Kept as a
  secondary mechanism; leases in PostgreSQL are authoritative.
- **Content-addressed storage (hash as filename):** would deduplicate bytes, but deleting one case
  could remove bytes another record references, and it leaks content equality through paths.
  Rejected; each record owns its file and duplicates are reported instead.
- **`403` for non-members:** reveals that a case id exists. Rejected in favour of `404`.
- **Automatic entity merge on equal identifiers:** contradicts PRD §3 and §7. Rejected.

## Consequences

- The `dispatcher` is a required service: without it, work created while Redis was down is not
  published and lost messages are not redelivered (runs stay visibly `queued`).
- Crash recovery of a running execution waits for its lease to expire (`run_lease_seconds`,
  default 60 seconds).
- The fixture connector defines the connector contract that Phase 2 connectors must implement; its
  outcomes and retry semantics become part of the connector SDK.
- Membership is ready for teams, but there is no UI or API to add members yet; authorization tests
  create a second account directly in the database.
- Deleting a case cannot reach earlier backups or exports; this is documented for operators.

## Verification

`scripts/test-backend.sh` covers authorization boundaries, import validation, storage crash windows,
claim and lease behaviour, duplicate delivery, retries, cancellation, exports and deletion failures.
`scripts/verify-phase1.sh` exercises the same guarantees against the Compose stack, including a Redis
outage at run creation, a deleted broker message, duplicate delivery, a killed worker and a stack
restart.
