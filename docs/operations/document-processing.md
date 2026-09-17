# Document processing (PDF text and OCR)

Imported PDFs are stored unchanged and processed by the **worker** service, which has no internet
access. Design: [ADR 0008](../adr/0008-authorized-imports-and-document-processing.md).

## Importing

**Evidence → Import a PDF document** or `POST /api/v1/cases/{case}/imports/documents`
(multipart: `file`, `import_origin`, `ocr` = `if_needed` | `always` | `off`). Only files with a PDF
header are accepted (64 MiB, `TRACEHOLLOW_IMPORT_MAX_DOCUMENT_BYTES`). PDF attachments from a
WhatsApp export can be processed from their evidence page (**Extract text**).

## States

| Result | Job status | Meaning |
| --- | --- | --- |
| `text_extracted` | completed | every processed page had a text layer or OCR text |
| `partial` | partial | some pages failed, were beyond a limit, or were cut at the text limit |
| `image_only` | partial | no page had a text layer; OCR produced nothing or was unavailable |
| `no_text` | completed | pages without text or images |
| `encrypted` | failed (`pdf_encrypted`) | a password is required; Tracehollow never guesses or breaks passwords |
| `malformed` | failed (`pdf_malformed`) | the structure could not be read |
| `unsupported` | failed (`pdf_unsupported`) | unsupported encryption, no pages, or a resource limit on inspection |
| — | partial (`ocr_unavailable`) | pages needed OCR but the engine or language data is not installed; the detail says how to enable it |
| — | canceled | stopped; text from pages finished before is kept |

PDFs protected only by an owner password (permission flags) open as in a viewer; the result
records that limitation. XFA form content is not extracted.

## Derived records

- `… — extracted text` (`text_layer`): the embedded text layer, pages separated by `[Page N]`
  headers, with `page_map` (page → character range), parser version and limitations.
- `… — OCR text` (`ocr_text`): Tesseract output for rendered pages, with engine version, languages,
  DPI, renderer version and a machine-recognition warning. Never merged with the text layer.

Both are ordinary evidence records derived from the original, indexed for AI retrieval under the
case's AI policy (a local-only case stays local). AI citations show the page, line and text origin.

## Limits and isolation

| Setting | Default | Purpose |
| --- | --- | --- |
| `TRACEHOLLOW_DOCUMENT_MAX_PAGES` | 500 | pages processed per document |
| `TRACEHOLLOW_DOCUMENT_MAX_TEXT_CHARS` | 2,000,000 | characters kept per derived record |
| `TRACEHOLLOW_DOCUMENT_PARSE_TIMEOUT_SECONDS` | 120 | wall-clock limit per parsing step |
| `TRACEHOLLOW_DOCUMENT_PARSE_MEMORY_MB` | 1024 | address-space limit of each child process |
| `TRACEHOLLOW_DOCUMENT_OCR_ENABLED` | true | allow OCR when the engine is installed |
| `TRACEHOLLOW_DOCUMENT_OCR_LANGUAGES` | `eng+tur` | Tesseract languages |
| `TRACEHOLLOW_DOCUMENT_OCR_MAX_PAGES` | 50 | pages OCR'd per document |
| `TRACEHOLLOW_DOCUMENT_OCR_DPI` | 200 | rendering resolution (scaled down above 25 million pixels) |
| `TRACEHOLLOW_DOCUMENT_OCR_PAGE_TIMEOUT_SECONDS` | 60 | limit per rendered page and per OCR call |
| `TRACEHOLLOW_PROCESSING_LEASE_SECONDS` / `_MAX_ATTEMPTS` | 300 / 3 | lease and retries of a processing job |

Each parsing, rendering and OCR step runs in a fresh child process with a minimal environment, the
document on standard input, a wall-clock timeout and address-space, CPU-time, open-file and core
limits. Linux enforces the memory limit (checked in the worker container by
`scripts/verify-phase4.sh`); macOS development hosts only enforce the timeout. A batch of pages that
fails is retried page by page, so one hostile page does not hide the others. Worker concurrency is
the Celery concurrency of the `worker` service (2); each job runs one child at a time with
`OMP_THREAD_LIMIT=1`.

Neither pypdf nor PDFium is given a way to fetch external resources, links and JavaScript in a PDF
are ignored, and the worker has no network route beyond PostgreSQL, Redis and the evidence volume.

## Enabling OCR

The default image has no OCR engine. To add Tesseract with English and Turkish language data:

```bash
# .env
TRACEHOLLOW_INSTALL_OCR=true
docker compose build api      # the worker uses the same image
docker compose up -d worker
```

Then use **Process again** on documents that ended with `ocr_unavailable`. The Debian packages are
not version-pinned; every OCR record stores the Tesseract version that produced it. Other languages
need their `tesseract-ocr-<lang>` package and `TRACEHOLLOW_DOCUMENT_OCR_LANGUAGES`.

To turn OCR off without rebuilding, set `TRACEHOLLOW_DOCUMENT_OCR_ENABLED=false` on the worker.

## Troubleshooting

- *Job stays queued:* check that the `worker` and `dispatcher` services are healthy.
- *`worker_lost`:* the job was interrupted three times; check worker memory and logs, then process
  again.
- *`ocr_unavailable` after enabling OCR:* the worker still runs the old image; rebuild and restart.

Tests: `tests/test_document_processing.py` (states, page map, OCR bookkeeping, cancellation,
retries, reprocessing, deletion, AI citation with page), `scripts/verify-phase4.sh [--ocr]`.
