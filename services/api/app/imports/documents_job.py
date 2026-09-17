"""Processing job for an imported PDF (``document_text``).

Steps, each bounded and run in a resource-limited child process:

1. Inspect the document: page count and encryption. A document that needs a password, cannot be
   parsed, or uses unsupported features is recorded as ``failed`` with that state; nothing is
   derived and the original stays available.
2. Extract the embedded text layer in batches of pages, renewing the lease and honouring
   cancellation between batches.
3. When OCR is requested (``always``) or needed (``if_needed``: pages without a usable text layer
   that contain images), render those pages and run Tesseract on them. Missing OCR support is
   recorded as an actionable state, not an error.

Results are two separate derived text records, so extracted text and OCR output are never mixed:
``text_layer`` and ``ocr_text``. Each carries a page map (page number to character range) in its
collection metadata, the parser or engine versions, and its limitations. Both are indexed for
case-scoped AI retrieval under the case's AI policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai import indexing
from app.db.session import session_scope
from app.evidence.models import EvidenceKind, EvidenceObject
from app.evidence.storage import IntegrityError
from app.imports import documents, jobs
from app.imports.derived import (
    DerivedPart,
    StagedParts,
    evidence_row,
    stage_part,
    supersede_previous,
)
from app.imports.models import ProcessingJobType, ProcessingStatus
from app.imports.pdf_worker import MIN_TEXT_CHARS

EXTRACT_BATCH_PAGES = 10
# Per-page cap inside the child; the document total is capped by document_max_text_chars.
MAX_PAGE_CHARS = 200_000
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"

_FAILED_STATES = {
    "encrypted": (
        "pdf_encrypted",
        "The PDF needs a password to open. Tracehollow does not break or guess passwords: import "
        "an unprotected copy you are authorized to use.",
    ),
    "malformed": (
        "pdf_malformed",
        "The PDF structure could not be read. The original is kept; try a repaired or re-exported "
        "copy.",
    ),
    "unsupported": (
        "pdf_unsupported",
        "The PDF uses a feature this parser does not support. The original is kept.",
    ),
}


@dataclass
class PageRecord:
    page: int
    status: str  # text | no_text | image_only | error | not_processed
    text: str = ""
    has_images: bool = False
    truncated: bool = False
    error: str | None = None
    ocr_status: str | None = None  # ocr_text | ocr_empty | ocr_failed | not_requested | ...
    ocr_text: str = ""
    ocr_error: str | None = None


@dataclass
class Outcome:
    pages_total: int = 0
    pages: list[PageRecord] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    canceled: bool = False
    ocr: dict[str, Any] = field(default_factory=dict)


def _build_text(
    records: list[PageRecord], attribute: str, max_chars: int
) -> tuple[str, list[dict[str, Any]], bool]:
    """Join page texts with page headers; return the text, the page map and truncation."""
    parts: list[str] = []
    page_map: list[dict[str, Any]] = []
    length = 0
    truncated = False
    for record in records:
        text = getattr(record, attribute).strip()
        if not text:
            continue
        header = f"[Page {record.page}]\n"
        remaining = max_chars - length - len(header) - 2
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        start = length + len(header)
        parts.append(header + text + "\n\n")
        length += len(header) + len(text) + 2
        page_map.append({"page": record.page, "char_start": start, "char_end": start + len(text)})
        if truncated:
            break
    return "".join(parts), page_map, truncated


def _load_original(ctx: jobs.ProcessingContext, job: jobs.JobSnapshot) -> EvidenceObject:
    with session_scope(ctx.session_factory) as db:
        original = db.get(EvidenceObject, job.evidence_id)
        if original is None:
            raise jobs.LeaseLostError
        db.expunge(original)
    return original


def _child(ctx: jobs.ProcessingContext, arguments: list[str], document: bytes) -> dict[str, Any]:
    try:
        return documents.pdf_command(ctx.settings, arguments, document)
    except documents.ChildFailedError as exc:
        if exc.code == "spawn_failed":
            raise jobs.ProcessingError(
                "processing_unavailable",
                "The worker could not start the document parser; it is retried.",
                retryable=True,
            ) from None
        return {"ok": False, "code": exc.code, "detail": exc.detail}


def _extract_pages(
    ctx: jobs.ProcessingContext,
    job: jobs.JobSnapshot,
    token: Any,
    document: bytes,
    outcome: Outcome,
    limit: int,
) -> None:
    per_page_chars = min(MAX_PAGE_CHARS, ctx.settings.document_max_text_chars)
    for first in range(1, limit + 1, EXTRACT_BATCH_PAGES):
        last = min(first + EXTRACT_BATCH_PAGES - 1, limit)
        try:
            jobs.renew(ctx, job.id, token)
        except jobs.JobCanceledError:
            outcome.canceled = True
            return
        payload = _child(
            ctx,
            [
                "extract",
                "--first",
                str(first),
                "--last",
                str(last),
                "--max-chars",
                str(per_page_chars),
            ],
            document,
        )
        if not payload.get("ok"):
            # A batch that times out or exceeds memory is retried page by page, so one hostile
            # page does not hide its neighbours.
            if last > first:
                for page in range(first, last + 1):
                    single = _child(
                        ctx,
                        [
                            "extract",
                            "--first",
                            str(page),
                            "--last",
                            str(page),
                            "--max-chars",
                            str(per_page_chars),
                        ],
                        document,
                    )
                    _record_batch(outcome, single, page, page)
            else:
                _record_batch(outcome, payload, first, last)
            continue
        _record_batch(outcome, payload, first, last)


def _record_batch(outcome: Outcome, payload: dict[str, Any], first: int, last: int) -> None:
    if not payload.get("ok"):
        code = str(payload.get("code") or "extraction_error")
        for page in range(first, last + 1):
            outcome.pages.append(PageRecord(page=page, status="error", error=code))
        return
    for entry in payload.get("pages", []):
        page = int(entry["page"])
        if entry.get("error"):
            outcome.pages.append(PageRecord(page=page, status="error", error=str(entry["error"])))
            continue
        text = str(entry.get("text") or "")
        visible = int(entry.get("visible_chars") or 0)
        has_images = bool(entry.get("has_images"))
        if visible >= MIN_TEXT_CHARS:
            status = "text"
        elif has_images:
            status = "image_only"
        else:
            status = "no_text"
        outcome.pages.append(
            PageRecord(
                page=page,
                status=status,
                text=text if visible else "",
                has_images=has_images,
                truncated=bool(entry.get("truncated")),
            )
        )


def _run_ocr(
    ctx: jobs.ProcessingContext,
    job: jobs.JobSnapshot,
    token: Any,
    document: bytes,
    outcome: Outcome,
    mode: str,
) -> None:
    if mode == "off":
        for record in outcome.pages:
            if record.status == "image_only":
                record.ocr_status = "not_requested"
        outcome.ocr = {"mode": mode, "status": "not_requested"}
        return
    if mode == "always":
        candidates = [r for r in outcome.pages if r.status in ("text", "image_only", "no_text")]
    else:
        candidates = [r for r in outcome.pages if r.status == "image_only"]
    availability = documents.ocr_availability(ctx.settings)
    outcome.ocr = {"mode": mode, **availability.as_dict(), "pages_requested": len(candidates)}
    if not candidates:
        outcome.ocr["status"] = "not_needed"
        return
    if not availability.available:
        outcome.ocr["status"] = "unavailable"
        for record in candidates:
            record.ocr_status = "ocr_unavailable"
        return
    outcome.ocr["dpi"] = ctx.settings.document_ocr_dpi
    budget = ctx.settings.document_ocr_max_pages
    processed = 0
    for record in candidates:
        if processed >= budget:
            record.ocr_status = "ocr_page_limit"
            continue
        try:
            jobs.renew(ctx, job.id, token)
        except jobs.JobCanceledError:
            outcome.canceled = True
            break
        processed += 1
        try:
            image = documents.render_page(ctx.settings, document, record.page)
            text = documents.ocr_image(ctx.settings, image)
        except documents.ChildFailedError as exc:
            record.ocr_status = "ocr_failed"
            record.ocr_error = exc.code
            continue
        record.ocr_text = text[: ctx.settings.document_max_text_chars]
        record.ocr_status = "ocr_text" if text.strip() else "ocr_empty"
    for record in candidates:
        if record.ocr_status is None:
            record.ocr_status = "not_processed_canceled"
    outcome.ocr["status"] = "canceled" if outcome.canceled else "completed"
    outcome.ocr["pages_processed"] = processed


def _summarise(
    outcome: Outcome, settings: Any, limit: int, text_truncated: bool, ocr_truncated: bool
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for record in outcome.pages:
        counts[record.status] = counts.get(record.status, 0) + 1
    ocr_counts: dict[str, int] = {}
    for record in outcome.pages:
        if record.ocr_status:
            ocr_counts[record.ocr_status] = ocr_counts.get(record.ocr_status, 0) + 1
    if outcome.pages_total > limit:
        outcome.gaps.append(
            f"only the first {limit} of {outcome.pages_total} pages were processed (page limit)"
        )
    if counts.get("error"):
        outcome.gaps.append(f"{counts['error']} page(s) could not be read")
    image_only_without_text = sum(
        1
        for r in outcome.pages
        if r.status == "image_only" and r.ocr_status not in ("ocr_text", "ocr_empty")
    )
    if image_only_without_text:
        outcome.gaps.append(
            f"{image_only_without_text} page(s) contain images but no text layer and have no OCR "
            "text"
        )
    if ocr_counts.get("ocr_failed"):
        outcome.gaps.append(f"OCR failed on {ocr_counts['ocr_failed']} page(s)")
    if text_truncated or any(r.truncated for r in outcome.pages):
        outcome.gaps.append("extracted text was cut at the text limit")
    if ocr_truncated:
        outcome.gaps.append("OCR text was cut at the text limit")
    if outcome.canceled:
        outcome.gaps.append("processing was canceled; pages finished before that are kept")
    outcome.limitations.extend(
        [
            "Text-layer extraction returns the text embedded in the PDF, which can differ from "
            "what is visible (hidden, overlaid or reordered text).",
            "Reading order and layout of columns and tables are approximate.",
        ]
    )
    if any(r.ocr_status in ("ocr_text", "ocr_empty") for r in outcome.pages):
        outcome.limitations.append(
            "OCR text is machine recognition of a rendered page and may contain errors; check "
            "quotes against the original page."
        )
    document_state = "text_extracted"
    if counts.get("text", 0) == 0 and not any(r.ocr_status == "ocr_text" for r in outcome.pages):
        document_state = "image_only" if counts.get("image_only") else "no_text"
    elif outcome.gaps:
        document_state = "partial"
    return {
        "document_state": document_state,
        "pages_total": outcome.pages_total,
        "pages_processed": len(outcome.pages),
        "page_status_counts": counts,
        "ocr_status_counts": ocr_counts,
        "pages": [
            {
                "page": r.page,
                "status": r.status,
                "has_images": r.has_images,
                "error": r.error,
                "ocr_status": r.ocr_status,
                "ocr_error": r.ocr_error,
            }
            for r in outcome.pages[:2000]
        ],
        "ocr": outcome.ocr,
        "parser_versions": documents.PARSER_VERSIONS,
        "limits": {
            "max_pages": settings.document_max_pages,
            "max_text_chars": settings.document_max_text_chars,
            "parse_timeout_seconds": settings.document_parse_timeout_seconds,
            "parse_memory_mb": settings.document_parse_memory_mb,
            "ocr_max_pages": settings.document_ocr_max_pages,
        },
        "gaps": outcome.gaps,
        "limitations": outcome.limitations,
    }


def process(ctx: jobs.ProcessingContext, job: jobs.JobSnapshot, token: Any) -> str:
    settings = ctx.settings
    original = _load_original(ctx, job)
    if original.kind != EvidenceKind.PDF:
        raise jobs.ProcessingError(
            "unsupported_original", "Document processing accepts PDF originals only."
        )
    try:
        document = ctx.storage.read_verified(
            original.storage_key, original.sha256, original.size_bytes
        )
    except IntegrityError as exc:
        raise jobs.ProcessingError(
            exc.code, "The stored original failed its integrity check."
        ) from None

    inspected = _child(ctx, ["inspect"], document)
    if not inspected.get("ok"):
        state = str(inspected.get("state") or "malformed")
        if inspected.get("code") in ("timeout", "resource_limit"):
            state = "unsupported"
        code, detail = _FAILED_STATES.get(state, _FAILED_STATES["malformed"])
        with jobs.guarded_write(ctx, job.id, token) as (db, row, _case):
            jobs.finish(
                db,
                row,
                ProcessingStatus.FAILED,
                result={
                    "document_state": state,
                    "reason_code": inspected.get("code"),
                    "parser_versions": documents.PARSER_VERSIONS,
                    "gaps": [],
                    "limitations": [detail],
                },
                error_code=code,
                error_detail=detail,
            )
        return str(ProcessingStatus.FAILED)

    outcome = Outcome(pages_total=int(inspected["pages"]))
    if inspected.get("opened_with_empty_password"):
        outcome.limitations.append(
            "The PDF is encrypted with an empty user password (permission restrictions only); it "
            "was opened without a password as a PDF viewer would."
        )
    if inspected.get("has_xfa"):
        outcome.limitations.append(
            "The PDF contains an XFA form; dynamic form content is not extracted."
        )
    limit = min(outcome.pages_total, settings.document_max_pages)
    _extract_pages(ctx, job, token, document, outcome, limit)
    if not outcome.canceled:
        _run_ocr(ctx, job, token, document, outcome, str(job.options.get("ocr", "if_needed")))

    text, text_map, text_truncated = _build_text(
        outcome.pages, "text", settings.document_max_text_chars
    )
    ocr_text, ocr_map, ocr_truncated = _build_text(
        outcome.pages, "ocr_text", settings.document_max_text_chars
    )
    result = _summarise(outcome, settings, limit, text_truncated, ocr_truncated)

    staged = StagedParts(ctx.storage)
    superseded: list[str] = []
    try:
        parts: list[DerivedPart] = []
        common = {
            "source_evidence_id": str(original.id),
            "source_sha256": original.sha256,
            "processing_job_id": str(job.id),
        }
        if text:
            parts.append(
                stage_part(
                    staged,
                    job.case_id,
                    text.encode("utf-8"),
                    kind=EvidenceKind.TEXT,
                    content_type=TEXT_CONTENT_TYPE,
                    title=f"{original.title} — extracted text",
                    part="text_layer",
                    original_filename=None,
                    metadata={
                        **common,
                        "derivation": "pdf_text_layer",
                        "text_origin": "embedded_text_layer",
                        "parser": {"name": "pypdf", "version": documents.PARSER_VERSIONS["pypdf"]},
                        "page_map": text_map,
                        "truncated": text_truncated,
                    },
                )
            )
        if ocr_text:
            parts.append(
                stage_part(
                    staged,
                    job.case_id,
                    ocr_text.encode("utf-8"),
                    kind=EvidenceKind.TEXT,
                    content_type=TEXT_CONTENT_TYPE,
                    title=f"{original.title} — OCR text",
                    part="ocr_text",
                    original_filename=None,
                    metadata={
                        **common,
                        "derivation": "pdf_ocr",
                        "text_origin": "ocr",
                        "engine": {
                            "name": "tesseract",
                            "version": outcome.ocr.get("engine_version"),
                            "languages": outcome.ocr.get("languages"),
                            "dpi": outcome.ocr.get("dpi"),
                        },
                        "renderer": {
                            "name": "pdfium (pypdfium2)",
                            "version": documents.PARSER_VERSIONS["pdfium"],
                        },
                        "page_map": ocr_map,
                        "truncated": ocr_truncated,
                        "limitations": [
                            "Machine recognition of rendered pages; may contain errors."
                        ],
                    },
                )
            )
        if not parts and outcome.canceled:
            raise jobs.JobCanceledError
        with jobs.guarded_write(ctx, job.id, token, allow_canceled=True) as (db, row, case):
            fresh = db.get(EvidenceObject, job.evidence_id)
            if fresh is None:
                raise jobs.LeaseLostError
            superseded = supersede_previous(
                db,
                original_id=job.evidence_id,
                job_type=ProcessingJobType.DOCUMENT_TEXT,
                current_job_id=job.id,
            )
            for part in parts:
                db.add(evidence_row(fresh, part, job.id))
            db.flush()
            for part in parts:
                indexing.mark_evidence_for_indexing(
                    db,
                    settings,
                    case_id=job.case_id,
                    evidence_id=part.evidence_id,
                    ai_mode=case.ai_mode,
                )
            result["derived_parts"] = [part.part for part in parts]
            canceled = outcome.canceled or row.cancel_requested_at is not None
            if canceled:
                status = ProcessingStatus.CANCELED
                error_code: str | None = "canceled"
                error_detail: str | None = "Canceled; text from pages finished before is kept."
            elif outcome.ocr.get("status") == "unavailable" and outcome.ocr.get("pages_requested"):
                status = ProcessingStatus.PARTIAL
                error_code = "ocr_unavailable"
                error_detail = (
                    str(outcome.ocr.get("reason") or "OCR is unavailable.")
                    + " "
                    + str(outcome.ocr.get("action") or "")
                )
            else:
                status = ProcessingStatus.PARTIAL if result["gaps"] else ProcessingStatus.COMPLETED
                error_code = None
                error_detail = None
            jobs.finish(
                db,
                row,
                status,
                result=result,
                error_code=error_code,
                error_detail=error_detail.strip() if error_detail else None,
            )
        staged.keys.clear()
    except BaseException:
        staged.discard()
        raise
    for key in superseded:
        ctx.storage.remove_key(key)
    return str(status)
