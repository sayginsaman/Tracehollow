# Backup and restore

PostgreSQL is Tracehollow's system of record, including query execution state, the dispatch
outbox and derived AI data (chunks, vectors, conversations); the `evidence-data` volume holds
original evidence files. Redis holds only transient broker
messages and is not part of backups: after a restore the `dispatcher` service re-publishes any
queued work whose message is missing.

All commands run from the repository root against the Compose project in use (`tracehollow` by
default; set `COMPOSE_PROJECT_NAME` for another project). None of them delete Docker volumes.

## What a backup contains

`scripts/backup.sh [destination]` writes to `backups/<UTC timestamp>/` by default (git-ignored,
created with mode `0700`/`0600`):

| File | Content |
| --- | --- |
| `database.dump` | `pg_dump --format=custom --no-owner --exclude-extension=vector` of the `tracehollow` database. The pgvector extension itself is superuser-owned and recreated before every restore; the vector data is included. |
| `evidence.tar` | Contents of the evidence volume |
| `row-counts.txt` | Exact row count per table at backup time |
| `manifest.txt` | Creation time, Compose project, Alembic revision and SHA-256 checksums |

**Not included:** `secrets/`, `.env`, Redis data. Connector credentials are in the dump only as
ciphertext; they can be decrypted only with `secrets/credential_encryption_key`. Back up `secrets/` separately to protected storage
(for example an encrypted password manager or encrypted volume). Without it a restored installation
needs new secrets (see "Restoring onto a new machine").

Backups are not encrypted. Store them on encrypted media and apply your own retention policy.
Deleting a case does not remove it from backups taken earlier (see
[Case deletion and backups](#case-deletion-and-backups)).

## Create a backup

The stack must be running.

```bash
scripts/backup.sh
```

The database dump is always transactionally consistent. `backup.sh` archives evidence files after
the dump, so while imports, executions or deletions run the archive can contain files whose rows
were committed after the dump (harmless: reconciliation quarantines them after a restore) and, if a
case is deleted during the backup, can lack files for rows that are still in the dump (reported by
`reconcile-evidence`). For a fully matching pair, stop writers first; `backup.sh` reads the evidence
volume through a one-off container when `api` is stopped:

```bash
docker compose stop web api worker ai-worker collector dispatcher
scripts/backup.sh
docker compose up --detach --wait
```

## Verify a backup (restore drill)

Test restores, not just backup creation:

```bash
scripts/restore.sh backups/<timestamp> --verify-only
```

The drill verifies checksums, checks the evidence archive, restores the dump into a temporary
database (after creating the `vector` extension there), compares every table's row count with
`row-counts.txt`, then drops the temporary database.
The live database and volumes are untouched.

## Full restore into the current installation

This replaces the current database and evidence contents. Take a fresh backup of the current state
first.

```bash
scripts/backup.sh                                   # safety copy of the current state
scripts/restore.sh backups/<timestamp> --yes-overwrite-current-data
```

The script:

1. stops `web`, `api`, `worker`, `ai-worker`, `collector` and `dispatcher`;
2. creates a new database, creates the `vector` extension in it and runs
   `pg_restore --single-transaction` as the application role;
3. compares every table's row count with the backup; on any failure the new database is dropped
   and the live database is unchanged;
4. renames the live database to `tracehollow_before_restore_<timestamp>` and the restored one to
   `tracehollow`;
5. replaces the evidence volume contents, pauses every restored monitor
   (`python -m app.cli pause-monitors --reason restore`) so scheduled collection does not resume on
   its own, and starts the stack; the `migrate` service upgrades a
   backup from an older schema revision (for example a Phase 1 backup gains the AI tables and its
   evidence is queued for indexing);
6. drops the previous database once the stack is healthy. If starting the stack fails, the previous
   database is kept under its printed name for investigation; drop it manually afterwards.

Restoring into a fresh database instead of over the existing one matters across versions:
`pg_restore --clean` only drops objects that are in the archive, so newer tables would survive or
block the restore. The swap needs free disk space for a second copy of the database.

## Restoring onto a new machine

1. Clone the repository at a commit compatible with the backup's `alembic_revision`
   (same or newer).
2. Copy your protected `secrets/` directory into the repository root (mode `0700`). If the original
   secrets are lost, skip this step; `scripts/setup.sh` generates new ones and the restore still
   works because the database role is created from the new secrets.
3. `scripts/setup.sh` (keeps any secrets you copied).
4. `docker compose up --build --detach --wait`
5. `scripts/restore.sh <backup-dir> --verify-only`
6. `scripts/restore.sh <backup-dir> --yes-overwrite-current-data`
7. Sign in with the restored administrator account. If the password is unknown, use
   `docker compose exec api python -m app.cli reset-password --username <name>`.

After a restore, check that every evidence record has its file and the expected hash:

```bash
docker compose exec api python -m app.cli reconcile-evidence
```

Executions and AI requests that were `running` when the backup was taken resume or fail through the
normal lease recovery once the dispatcher and workers start. Indexing resumes the same way. If the
restored installation uses a different embedding model, earlier vectors are reported as stale until
the index is rebuilt (see [ai-models.md](ai-models.md)).

## Monitoring, notifications, accounts and audit after a restore

A restore returns monitors, budgets, notification destinations, accounts, memberships, retention
policies and the audit trail to their state at backup time. Monitors are paused by the restore
script; review and resume them in each case. Cases deleted, evidence removed by retention, accounts
deactivated and members removed after the backup was taken come back and must be removed again.
Details: [retention.md](retention.md#restoring-a-backup).

## Crash consistency between files and database

Evidence writes stage, promote and then commit metadata; an interruption can leave staged files or
orphaned files but never a record pointing at a partially written file. The procedure, the
reconciliation command and how to recover quarantined files are documented in
[evidence-storage.md](evidence-storage.md#what-an-interruption-can-leave-behind).

## Case deletion and backups

Deleting a case removes its database rows and the `cases/<case-uuid>/` directory from the live
installation only. It does not and cannot reach:

- backups created earlier with `scripts/backup.sh` (both `database.dump` and `evidence.tar` still
  contain the case);
- JSON or CSV exports downloaded earlier;
- copies made by host-level backup, snapshot or sync tools of the Docker volumes.

The same holds for evidence removed by a retention policy and for webhook notifications already
delivered to a receiver ([retention.md](retention.md#what-deletion-and-retention-cannot-reach)).

If a case must be removed everywhere, delete it in the application, then create a new backup and
destroy older backups and exports that contain it according to your retention policy. Restoring an
older backup brings a deleted case back; delete it again after such a restore. The same applies to
imported evidence deleted individually. The deletion job
record kept after deletion contains the case id and removed row counts, not case content.
