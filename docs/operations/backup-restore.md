# Backup and restore

PostgreSQL is Tracehollow's system of record; the `evidence-data` volume holds stored evidence
files (empty in Phase 0, used from Phase 1). Redis holds only transient broker messages and is not
part of backups.

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

Backups are not encrypted. Store them on encrypted media and apply your own retention policy;
deleting a case in a later phase does not remove it from backups taken earlier.

## Create a backup

The stack must be running.

```bash
scripts/backup.sh
```

Phase 0 writes no evidence files. From Phase 1 on, stop activity that writes evidence (or the whole
`web`, `api` and `worker` services) for a fully consistent evidence archive; the database dump itself
is always transactionally consistent.

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

The script stops `web`, `api` and `worker`, runs `pg_restore --clean --if-exists --single-transaction`
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

## Crash consistency between files and database

Phase 0 stores no evidence files. When evidence storage is implemented (Phase 1), the recovery
procedure for a crash between writing a file and committing its metadata will be documented here
together with the storage implementation.
