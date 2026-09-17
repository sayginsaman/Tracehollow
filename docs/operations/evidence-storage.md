# Evidence storage

Original evidence bytes live on the `evidence-data` Docker volume (mounted at `/data/evidence` in
the `api`, `worker` and `dispatcher` containers). Their metadata — case, kind, size, SHA-256,
acquisition method, import origin, source reference and dates — lives in the `evidence_objects`
table in PostgreSQL. PostgreSQL is authoritative: a file without a metadata row is not evidence.

## Layout

```text
/data/evidence/
  tmp/<random>.part                    staged writes, fsynced before promotion
  cases/<case-uuid>/evidence/<uuid>    stored originals, named only by server-generated UUIDs
  quarantine/<UTC stamp>-<case>-<uuid> orphaned files moved aside by reconciliation
```

User-supplied filenames never influence paths. The sanitized filename is kept only as display
metadata (`original_filename`): it is NFKC-normalized, reduced to its last path component, and
control and bidirectional formatting characters and leading dots are removed. The API reports
`filename_sanitized: true` when anything changed.
Downloads are served as `application/octet-stream` attachments with
`Content-Security-Policy: sandbox; default-src 'none'`, so stored content is never rendered in
the application origin.

## Limits and accepted content (Phase 1)

| Setting | Default | Meaning |
| --- | --- | --- |
| `TRACEHOLLOW_EVIDENCE_MAX_IMPORT_BYTES` | 5 MiB | Largest accepted file. Larger files return `413 evidence_too_large`. |
| `TRACEHOLLOW_EVIDENCE_PREVIEW_MAX_BYTES` | 256 KiB | Bytes decoded for the in-app preview; longer content is marked truncated. |
| `TRACEHOLLOW_EVIDENCE_JSON_MAX_DEPTH` | 64 | Deeper JSON is rejected as `json_too_deep`. |
| `TRACEHOLLOW_EVIDENCE_ORPHAN_GRACE_SECONDS` | 900 | Age before reconciliation touches staged or orphaned files. |
| `TRACEHOLLOW_WEB_MAX_UPLOAD_BYTES` (web) | 5 MiB + 64 KiB | Request body limit of the web proxy for evidence imports only. |

Only UTF-8 text and JSON are accepted. Empty files (`empty_content`), bytes that are not valid
UTF-8 (`invalid_encoding`), UTF-8 containing NUL bytes (`binary_content`), malformed JSON
(`invalid_json`) and overly deep JSON (`json_too_deep`) are rejected before anything is written.
PDF, image, OCR and messaging-export imports are deferred to later phases.

`compose.yaml` does not pass these variables through, so the defaults apply. To change a limit,
add the variable to the `api` service environment (and `TRACEHOLLOW_WEB_MAX_UPLOAD_BYTES` to the
`web` service), keeping the web limit at least 64 KiB above the API limit for multipart overhead.

## Write procedure

Both analyst imports and fixture-connector pages use the same order:

1. Take a share lock on the case row (`SELECT … FOR SHARE`) so a deletion cannot run concurrently.
2. **Stage:** write the bytes to `tmp/<random>.part` with `O_EXCL`, `fsync` the file.
3. **Promote:** `os.replace` the staged file to `cases/<case>/evidence/<uuid>` after checking that
   the target does not exist (stored evidence is never overwritten), then `fsync` the directory.
4. **Commit:** insert the metadata row (and, for connector pages, the observations, entities,
   relationships and progress) in one database transaction.
5. If the transaction fails, the promoted file is removed before the error is returned.

Importing identical bytes again creates a separate record; the response lists the earlier records
in `duplicate_of`. Nothing is merged or replaced.

## What an interruption can leave behind

| Interrupted between | Leftover | Handling |
| --- | --- | --- |
| steps 2 and 3 | `tmp/*.part` file, no row | Deleted by reconciliation after the grace period. |
| steps 3 and 4 (process killed, so step 5 never ran) | promoted file, no row | **Moved to `quarantine/`** after the grace period, never deleted automatically. |
| after step 4 | nothing | The row and file are consistent. |
| storage loss or tampering | row whose file is missing or altered | Reported; rows are never deleted automatically. |

The evidence detail view and `GET …/evidence/{id}` re-hash the stored file on every read and report
`verified`, `evidence_file_missing`, `evidence_size_mismatch` or `evidence_hash_mismatch`. Content
and preview requests for a file that fails verification return `409` with that code instead of
serving altered bytes.

A killed worker never produces a half-written page: page persistence is one transaction, pages are
unique per connector run (`connector_run_id`, `page_index`), and a reclaimed run resumes from the
last committed page.

## Reconciliation

The `dispatcher` service runs reconciliation 30 seconds after start and then hourly, applying the
table above (stale staged files removed, orphans quarantined) without re-hashing every file. Run a
full check, including hashes, manually:

```bash
# Dry run: report only (exit code 1 when a record's file is missing or altered)
docker compose exec api python -m app.cli reconcile-evidence

# Apply: delete stale staged files and quarantine orphans older than the grace period
docker compose exec api python -m app.cli reconcile-evidence --apply

# Ignore the grace period, for example after stopping api, worker and dispatcher
docker compose exec api python -m app.cli reconcile-evidence --apply --no-grace
```

Do not use `--no-grace` while imports or executions are running: a write between steps 3 and 4 is
indistinguishable from an orphan.

To recover a quarantined file, inspect it (`docker compose exec api ls /data/evidence/quarantine`),
copy it out with `docker compose cp api:/data/evidence/quarantine/<name> .`, check its content and
hash, and import it again as authorized evidence with an import origin describing the recovery.

When reconciliation reports a missing or mismatched file, restore that file from a backup
(`evidence.tar` preserves the `cases/<case>/evidence/<uuid>` layout) and run the dry run again.
If no backup has it, keep the record: its hash and provenance still document what was collected,
and the integrity status tells readers that the bytes are no longer available.

## Derived AI data (Phase 3)

Indexing never modifies stored originals. For each record the `ai-worker` decodes the verified
bytes and stores, in PostgreSQL:

- `document_chunks`: exact character slices of the text (`char_start`/`char_end`), or for JSON a
  flattened `pointer: value` rendering with the RFC 6901 pointer of every line, together with the
  evidence SHA-256 and the chunking version;
- `chunk_embeddings`: one vector per chunk and embedding profile;
- `evidence_index_states`: status, attempts, errors and the profile used.

When a citation is opened, the API re-reads and re-hashes the original and extracts the passage from
the original bytes, not from the chunk copy. A changed or missing file is reported instead of a
passage.

## Evidence deletion (imported evidence)

An imported record can be deleted from its evidence page by typing its title. Records collected by
query executions cannot be deleted individually; they stay with their execution history until the
case is deleted. The API (`POST /api/v1/cases/{case}/evidence/{evidence}/deletion`):

1. locks the case and the record and checks the confirmation;
2. clears the quoted text and source location stored in earlier AI citations of the record;
3. removes the stored original from the volume;
4. deletes the row, which cascades to chunks, vectors, index state, entity and relationship
   evidence links and evidence notes, and commits.

If step 4 fails after the file was removed, the record remains with a missing file (reported by
integrity checks and `reconcile-evidence`) and the deletion can be repeated; an unreferenced copy of
the content is never left behind. Earlier AI answers stay in their conversations and their citations
report `source_deleted`, but the generated claim text of those answers is kept and may restate what
the evidence said. Deleting the case removes those answers as well. As with case deletion, earlier
backups and exports are unaffected.

## Case deletion

Deleting a case (typed-title confirmation, **Case settings** page) runs as a job in the worker:
cancel queued and running executions and AI work, remove `cases/<case-uuid>/`, delete the case row
(all case-owned rows cascade, including chunks, vectors, conversations, AI runs and citations), remove the directory again, verify no row or file remains and record the
removed counts. A failed attempt leaves the case inaccessible in `deletion_failed` and can be
retried by the requester. Only the job record, without case content, is kept.

Deletion does **not** remove data from backups or exports made earlier; see
[backup-restore.md](backup-restore.md#case-deletion-and-backups).

## Backups

`scripts/backup.sh` archives the whole evidence volume next to the database dump. For an archive
that matches the dump exactly, stop writers first (`docker compose stop web api worker dispatcher`)
or take the backup while no import or execution runs. Restoring replaces both the database and the
volume contents; run `reconcile-evidence` afterwards.
