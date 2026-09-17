# ADR 0008: Authorized imports with durable processing (WhatsApp exports, PDFs)

- Status: accepted
- Date: 2026-09-16
- Phase: 4

## Context

Phase 4 adds imports that need parsing after upload: WhatsApp chat exports (`.txt` or `.zip`)
and PDF documents, with optional OCR. These files are untrusted, can be large, can be hostile
(archive bombs, path traversal, malformed or encrypted PDFs, active content) and their
interpretation can be ambiguous (date order, missing timezone). The PRD requires originals to stay
unchanged, derived data to cite its source location, local-only processing for local-only cases,
cancellation, retries, idempotency and cleanup.

## Decision

1. **Store first, parse later.** The upload endpoint stores the original byte-exact as an
   `authorized_import` evidence record (new kinds `archive`, `pdf`, `binary`) and creates a
   `processing_jobs` row plus an outbox row in the same transaction. Upload checks are cheap and
   bounded: size, archive directory screening, a layout check on the first lines, the PDF header.
2. **Processing runs in the `worker` service**, which is attached only to the internal `data`
   network. Parsers and OCR therefore cannot reach the internet or case targets, regardless of what
   a file references.
3. **Jobs follow the existing execution pattern:** conditional claim with a lease token, lease
   renewal between bounded units of work, cancellation checked at those points, a guarded write
   transaction that re-checks the lease, job status and case state, bounded attempts
   (`worker_lost` after repeated disappearance), outbox redelivery through the dispatcher, and a
   partial unique index allowing one active job per original and type.
4. **Derived records are ordinary evidence** (`derived_from_evidence_id`, `processing_job_id`,
   `page_part`). Their files are staged before the recording transaction and removed if it does
   not commit. Reprocessing replaces what earlier jobs of the same type derived, including records
   derived from those (text from a PDF attachment). Deleting an original deletes its derived
   records and files; case deletion waits for running jobs and removes everything.
5. **WhatsApp:** messages and system events become observations on the chat text record with line
   range, character offsets, the timestamp as written, the local time and, only when the analyst
   gave an IANA timezone, a UTC instant. Date order is inferred only from proof (a day or month
   above 12, a year-first layout); otherwise the job stops in `needs_input` and derives nothing.
   Daylight-saving gaps and overlaps are recorded, not resolved. Sender labels never become
   entities, identifiers or phone numbers. Attachments are stored as inert files with a media type
   from magic bytes and resolved to `present`, `missing` or `omitted_by_export`.
6. **Archives** are read with `zipfile` after screening the central directory: member count,
   declared total size, per-member size, expansion ratio, unsafe names (absolute, drive letters,
   `..`, control characters), symbolic links, encrypted members and case-insensitive duplicates.
   Reading enforces the declared size and a running total. Nothing is extracted to the filesystem.
7. **PDFs** are parsed in a short-lived child process (`python -m app.imports.pdf_worker`) with a
   minimal environment, wall-clock timeout, and address-space, CPU, open-file and core limits (the
   latter enforced on Linux). The document is passed on standard input. pypdf (BSD-3-Clause)
   extracts the text layer in batches of pages; a failing batch is retried page by page. Encrypted
   documents that need a password, malformed documents and unsupported features end as `failed`
   with an explicit state; nothing is guessed or cracked. Owner-password-only documents are opened
   as a viewer would, with a limitation recorded.
8. **OCR is optional.** pypdfium2 (Apache-2.0/BSD-3-Clause) renders pages in greyscale in the same
   limited child; the Tesseract binary runs with a fixed argument vector, `stdin`/`stdout` and
   `OMP_THREAD_LIMIT=1`. Images built without `INSTALL_OCR=true` have no Tesseract; jobs then end
   `partial` with `ocr_unavailable` and the action to take, and ordinary work continues.
9. **Extracted text and OCR text are separate records** (`text_layer`, `ocr_text`) with a page map,
   parser or engine versions, languages, DPI and limitations. Both are indexed under the case's AI
   policy, so a local-only case stays local; AI citations report page, line and text origin.

## Consequences

- The default worker image stays small; OCR needs an explicit build and is documented in
  [document-processing.md](../operations/document-processing.md).
- Memory limits are not enforced by the macOS development host; the stack verification checks
  them inside the Linux worker container.
- Media inside WhatsApp exports is stored but not analysed (no image OCR, no audio transcription).
- Downgrading migration 0005 deletes binary evidence rows because revision 0004 cannot represent
  them; their files remain and are reported by `reconcile-evidence`.

## Alternatives considered

- *Parsing in the API request:* rejected; unbounded work in the web tier and no retries.
- *Parsing in the collector:* rejected; it has internet egress.
- *PyMuPDF:* AGPL licence; rejected for the default dependency set.
- *Guessing the date order from the phone locale:* rejected; exports do not state it and a wrong
  guess silently corrupts every date.
