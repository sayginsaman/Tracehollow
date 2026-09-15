# Backup and restore

PostgreSQL is Tracehollow's system of record, including query execution state and the dispatch
outbox; the `evidence-data` volume holds original evidence files. Redis holds only transient broker
messages and is not part of backups: after a restore the `dispatcher` service re-publishes any
queued work whose message is missing.

All commands run from the repository root against the Compose project in use (`tracehollow` by
default; set `COMPOSE_PROJECT_NAME` for another project). None of them delete Docker volumes.

## What a backup contains

`scripts/backup.sh [destination]` writes to `backups/<UTC timestamp>/` by default (git-ignored,
created with mode `0700`/`0600`):

| File | Content |
| --- | --- |
| `database.dump` | `pg_dump --format=custom --no-owner` of the `tracehollow` database |
| `evidence.tar` | Contents of the evidence volume |
| `row-counts.txt` | Exact row count per table at backup time |
| `manifest.txt` | Creation time, Compose project, Alembic revision and SHA-256 checksums |

**Not included:** `secrets/`, `.env`, Redis data. Back up `secrets/` separately to protected storage
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
docker compose stop web api worker dispatcher
scripts/backup.sh
docker compose up --detach --wait
```

## Verify a backup (restore drill)

Test restores, not just backup creation:

```bash
scripts/restore.sh backups/<timestamp> --verify-only
```

The drill verifies checksums, checks the evidence archive, restores the dump into a temporary
database, compares every table's row count with `row-counts.txt`, then drops the temporary database.
The live database and volumes are untouched.

## Full restore into the current installation

This replaces the current database and evidence contents. Take a fresh backup of the current state
first.

```bash
scripts/backup.sh                                   # safety copy of the current state
scripts/restore.sh backups/<timestamp> --yes-overwrite-current-data
```

The script stops `web`, `api`, `worker` and `dispatcher`, runs `pg_restore --clean --if-exists --single-transaction`
as the application role owner, replaces the evidence volume contents, compares row counts with the
backup and starts the stack again. The `migrate` service then upgrades the restored schema if the
backup came from an older revision.

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

Executions that were `running` when the backup was taken resume or fail through the normal lease
recovery once the dispatcher and worker start.

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

If a case must be removed everywhere, delete it in the application, then create a new backup and
destroy older backups and exports that contain it according to your retention policy. Restoring an
older backup brings a deleted case back; delete it again after such a restore. The deletion job
record kept after deletion contains the case id and removed row counts, not case content.
