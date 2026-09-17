# Audit trail, retention, deletion and restore

What Tracehollow records about actions, how case data is removed on a schedule or on request, what
removal cannot reach, and how restores interact with both. Decision record:
[ADR 0014](../adr/0014-audit-trail-and-retention.md). Backups: [backup-restore.md](backup-restore.md).

## Audit trail

### What is recorded

Every change that matters for accountability writes one row to `audit_events`, in the same
database transaction as the change itself (so a change is never committed without its event):

| Field | Content |
| --- | --- |
| `occurred_at` | server time |
| `actor_type`, `actor_user_id`, `actor_label` | `user` (account ID and username), `service` (for example `scheduler`, `notifier`, `retention`, `operator-cli`) or `system` |
| `case_id` | the case, when the action concerns one (kept after the case is deleted) |
| `action` | for example `membership.added`, `monitor.enabled`, `export.downloaded`, `retention.applied`, `notification.delivery_blocked`, `account.updated`, `credential.set` |
| `outcome` | `succeeded`, `denied` (a permission refusal) or `failed` |
| `target_type`, `target_id` | the object acted on |
| `correlation_id` | the HTTP request ID (also in the API access log), or the job or delivery ID for background work |
| `details` | bounded, redacted settings and counts: roles, usernames, schedule and limits, formats, counts, reason codes |

Covered areas: accounts and passwords (never the password), case lifecycle and membership, evidence
imports and deletions, query cancellation, monitors and their dispatches and skips, budgets,
exports and reports (downloads), STIX imports and refusals, retention policies and jobs, connector
credentials (never the value), notification destinations and deliveries, and **denied requests**
(403 responses for insufficient case or account roles).

**Never recorded:** passwords, tokens, API keys, signing secrets, session identifiers, request or
response bodies, evidence contents, collected values, AI questions or answers. Keys and values that
look like secrets are replaced before storage; strings are truncated to 200 characters.

### Who can read it

- **Case audit log** (Case → Manage → Audit log): analysts of the case.
- **System audit log** (Administration → Audit log): administrators; includes case events by ID
  without case content.
- Viewers cannot read either.

### Limits

- **Not tamper-evident.** The trail is append-only by application convention: no API edits or
  deletes events. Anyone with direct database access (the PostgreSQL superuser, the application
  role, a host administrator, someone restoring a modified backup) can change or delete rows, and
  nothing would detect it. Export events to an external write-once system if you need that
  guarantee; Tracehollow does not provide one.
- **Retention.** Events older than `TRACEHOLLOW_AUDIT_RETENTION_DAYS` (default 400, minimum 30) are
  removed hourly by the dispatcher, which records an `audit.pruned` event with the count.
- Events are in backups, and a restored backup brings back events pruned since.

## Retention

Retention removes case evidence after a set age. It is **off** for every case until an analyst
activates it.

### Rules

| Rule | Where | Removes |
| --- | --- | --- |
| Collected results older than N days | Case settings → Retention | the evidence and observations of executions that finished more than N days ago |
| Imported evidence older than N days | Case settings → Retention | authorized imports (and their derived text, OCR text and attachments) imported more than N days ago |
| Keep the last K runs / results older than N days | each monitor | results of that monitor's older executions, applied by the case retention job even when the case rules are off |

### Activation

1. **Preview** (analysts): counts of executions, imported originals, evidence records and bytes,
   observations, relationship references, indexed passages and AI citations that would be affected,
   how many baselines are protected, work that would make the job wait, and what is not removed.
2. **Activate** by typing the exact case title. The policy gets a new version, the activation is
   audited with the preview counts, and a job runs at once.
3. While active, the dispatcher queues one job per case per UTC day. **Apply now** queues another.
   **Turn retention off** stops future jobs.

### What a job removes

For each eligible execution or import, in the order the evidence deletion lifecycle uses: stored
files first, then rows. Removed with the evidence:

- observations and derived records (extracted text, OCR text, attachments);
- indexed passages and embeddings;
- entity links and relationship evidence references (relationships themselves stay);
- pending webhook deliveries about the removed executions (blocked, not sent).

Kept, with an explicit trace:

- **Execution records** stay and show when their results expired (`results_expired_at`).
- **Tombstones** keep the removed evidence ID, SHA-256, rule and time, so a link to removed evidence
  answers **410** `evidence_expired_by_retention` with that information instead of 404.
- **AI citations** keep the answer and its verdict; opening the citation says the source was removed
  by retention (`source_expired`) and when.
- **Change events** show that the evidence on one side was removed.
- Saved queries, monitors, change sets, entities, relationships, notes and audit events are not
  removed by retention. An entity whose only supporting evidence expired stays, without that
  support.

### Protected from removal

- For every saved query and connector, the **latest execution that collected data and the latest
  complete execution** (the change-detection baselines).
- Executions that have not finished, and imports that a processing job is working on.
- **Active work:** while an AI request, a processing job or indexing holds a lease in the case, the
  job waits (status *queued*, note "Waiting for active work", counts of what it waits for) and
  retries every 60 seconds. `scripts/verify-phase5.sh` holds an indexing lease during activation and
  checks that nothing is removed until it ends.
- Cases being deleted (the deletion job removes everything anyway).

### Durability

Jobs are rows in PostgreSQL dispatched through the outbox, claimed with a lease and retried when a
worker dies; each batch is its own transaction, so an interrupted job resumes without removing
anything twice. Progress, removed counts and waits are visible in **Recent retention jobs** and
audited (`retention.applied`).

## Deleting a case

**Case settings → Delete case** (analysts), confirmed by typing the case title. The durable deletion
job (see [evidence-storage.md](evidence-storage.md)) removes the case's database rows and its
`cases/<case-uuid>/` directory. For Phase 5 records it also:

- disables the case's monitors as soon as deletion is requested, so no new execution starts;
- removes occurrences, change sets, budgets and ledgers, subscriptions, pending and past webhook
  deliveries, in-app notifications, retention policies, jobs and tombstones, and STIX links.

Audit events of the case remain (identifiers and settings, no case content) until audit retention
removes them.

## What deletion and retention cannot reach

Removal in Tracehollow affects the live installation only. It does **not** remove:

- **backups** made earlier with `scripts/backup.sh` or host-level snapshots of the volumes;
- **downloads**: JSON, CSV and STIX exports, HTML reports and single evidence files already saved;
- **webhook notifications already delivered** to a receiver (they contain identifiers and counts,
  not content, but the receiver keeps them);
- copies people made by other means.

To remove data everywhere: remove it in Tracehollow, create a new backup, destroy older backups and
downloaded files that contain it under your own procedures, and ask receivers of webhook events to
delete them if needed.

## Restoring a backup

`scripts/restore.sh <backup> --yes-overwrite-current-data` replaces the database and evidence
volume (see [backup-restore.md](backup-restore.md)). Consequences for Phase 5 data:

- **Monitors are paused** before the stack starts (`python -m app.cli pause-monitors --reason
  restore`), so restored monitors never resume scheduled collection on their own. Review each
  monitor and resume it explicitly; resuming never catches up the time the installation was down.
- **Deleted or expired data comes back.** Cases deleted and evidence removed by retention after the
  backup was taken reappear. Delete them again, and let active retention policies run (they are
  restored with the database).
- **Accounts and memberships** return to their state at backup time: an account deactivated or a
  member removed since then regains access until you repeat the change.
- **Notification destinations** are restored with their encrypted signing secrets; they can be
  decrypted only with the same `secrets/credential_encryption_key`. Pending deliveries resume only
  if the destination is enabled and the adapter is on.
- **Audit events** return to their state at backup time, including events pruned since.
- **Budgets**: ledgers return to their backup-time counts; periods that ended in the meantime simply
  start fresh.

A full restore drill for the release is Phase 6 work. Until then, run
`scripts/restore.sh <backup> --verify-only` regularly; it restores into a temporary database and
checks row counts without touching the live data.
