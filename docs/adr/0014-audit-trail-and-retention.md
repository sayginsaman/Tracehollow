# ADR 0014: Transactional audit trail and retention through the deletion lifecycle

- Status: accepted
- Date: 2026-09-17
- Phase: 5

## Context

Phase 5 requires structured audit events (actor, case, action, object, time, outcome, correlation)
without credentials or raw payloads, readable only with permission and without tamper-proof
claims; and retention controls with scope, expiry, preview, safe defaults, authorized activation,
durable retryable cleanup and protection from active work, reusing the deletion lifecycle and
keeping citations honest. Deletion must not imply removal of copies outside the installation.

## Decision

1. **Audit events in PostgreSQL, in the same transaction.** `audit_events` rows are written by
   `app.audit.service.record` inside the transaction of the change; denials are written in their
   own transaction because the request fails. Details are bounded and passed through the log
   redaction rules. `case_id` has no foreign key, so events outlive a deleted case. The correlation
   ID is the request ID (also in access logs) or the job or delivery ID.
2. **No tamper-evidence claim.** Append-only is an application convention; database administrators
   can change rows. This is documented. Retention prunes events after
   `audit_retention_days` and records the pruning.
3. **Retention is a case policy, off by default.** Rules by age for collected results and imports,
   plus per-monitor keep-last and age rules. Preview first; activation requires the typed case
   title and records the preview counts.
4. **Removal reuses the evidence deletion order** (files, then rows, cascades for derived records,
   chunks, embeddings and references) in durable jobs with leases, outbox dispatch, batches and
   retries, scheduled once per UTC day per active policy through a unique occurrence key.
5. **Protection.** The latest data-producing and the latest complete execution per saved query and
   connector, unfinished executions, imports being processed, and any case with AI requests,
   processing jobs or indexing holding leases (the job waits and retries).
6. **Honest references.** Executions keep their records with `results_expired_at`; tombstones keep
   evidence ID, hash, rule and time and make evidence routes answer 410; AI citations are marked
   `source_expired`; change events show missing sides; pending webhook deliveries about removed
   executions are blocked.
7. **Restores pause monitors.** `scripts/restore.sh` runs `app.cli pause-monitors` before starting
   the stack so restored monitors never resume collection without an analyst.

## Consequences

- Retention and deletion affect only the live installation: backups, downloads and delivered
  notifications are out of reach, as documented in `docs/operations/retention.md`.
- A restored backup brings back removed data, older access and pruned audit events; the procedure
  says what to repeat.
- The release restore drill remains Phase 6 work.
