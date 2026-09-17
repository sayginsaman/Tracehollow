"""Accepting authorized imports that need processing, and managing their processing jobs.

The uploaded file is stored byte-exact as the original evidence record before anything is parsed;
parsing happens later in the worker. Upload checks here are cheap and bounded (size, archive
directory screening, a layout check on the first lines) so obviously unusable files are refused
without creating records.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import indexing
from app.audit import service as audit
from app.audit.service import Actor
from app.auth.models import User
from app.config import Settings
from app.db.base import utcnow
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.entities.models import Observation
from app.evidence import importing
from app.evidence.importing import ImportRejectedError
from app.evidence.models import AcquisitionMethod, EvidenceKind, EvidenceObject
from app.evidence.service import find_duplicates, lock_case_for_write, to_out
from app.evidence.storage import EvidenceStorage
from app.imports import jobs
from app.imports.archives import ArchiveRejectedError, ArchiveReport, is_zip, list_regular_names
from app.imports.models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    ProcessingJob,
    ProcessingJobType,
    ProcessingStatus,
)
from app.imports.schemas import (
    DerivedEvidenceOut,
    ImportAccepted,
    ProcessingJobDetail,
    ProcessingJobOut,
)
from app.imports.whatsapp import (
    DATE_ORDER_CHOICES,
    WhatsAppParseError,
    looks_like_export,
    validate_timezone,
)
from app.imports.whatsapp_job import archive_limits, choose_chat_text

logger = logging.getLogger(__name__)


def _reject(code: str, message: str, http_status: int = 422) -> HTTPException:
    return HTTPException(http_status, detail={"code": code, "message": message})


@dataclass(frozen=True, slots=True)
class Original:
    kind: EvidenceKind
    content_type: str
    import_format: str


def whatsapp_options(date_order: str, timezone: str) -> dict[str, str]:
    if date_order not in DATE_ORDER_CHOICES:
        raise _reject("invalid_date_order", f"date_order must be one of {DATE_ORDER_CHOICES}")
    try:
        timezone = validate_timezone(timezone.strip())
    except WhatsAppParseError as exc:
        raise _reject(exc.code, exc.message) from None
    return {"date_order": date_order, "timezone": timezone}


def classify_whatsapp_upload(settings: Settings, content: bytes) -> Original:
    if not content:
        raise _reject("empty_content", "The uploaded file is empty.")
    if is_zip(content):
        try:
            names = list_regular_names(content, archive_limits(settings), ArchiveReport())
            choose_chat_text(names)
        except ArchiveRejectedError as exc:
            raise _reject(exc.code, exc.message) from None
        except jobs.ProcessingError as exc:
            raise _reject(exc.code, exc.detail) from None
        return Original(EvidenceKind.ARCHIVE, "application/zip", "whatsapp_export_zip")
    if len(content) > settings.import_max_chat_text_bytes:
        raise _reject(
            "chat_text_too_large",
            f"A chat text file may be at most {settings.import_max_chat_text_bytes} bytes.",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )
    try:
        importing.validate_content(EvidenceKind.TEXT, content, max_json_depth=1)
    except ImportRejectedError as exc:
        raise _reject(
            exc.code,
            f"{exc.message}. Import the chat .txt file or the .zip WhatsApp created.",
        ) from None
    if not looks_like_export(content.decode("utf-8")):
        raise _reject(
            "not_a_whatsapp_export",
            "No line near the start matches a WhatsApp export layout (Android "
            "'date, time - label: text' or iOS '[date, time] label: text').",
        )
    return Original(
        EvidenceKind.TEXT, importing.CONTENT_TYPES[EvidenceKind.TEXT], "whatsapp_export_txt"
    )


def classify_document_upload(settings: Settings, content: bytes) -> Original:
    if not content:
        raise _reject("empty_content", "The uploaded file is empty.")
    if len(content) > settings.import_max_document_bytes:
        raise _reject(
            "document_too_large",
            f"A document may be at most {settings.import_max_document_bytes} bytes.",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )
    # The PDF header may follow a short preamble; browser-declared types are not trusted.
    if b"%PDF-" not in content[:1024]:
        raise _reject(
            "unsupported_document_type",
            "Only PDF documents can be processed. Import plain text or JSON with the evidence "
            "import form.",
        )
    return Original(EvidenceKind.PDF, "application/pdf", "pdf_document")


def store_original_and_queue(
    db: Session,
    storage: EvidenceStorage,
    settings: Settings,
    *,
    case_id: uuid.UUID,
    user: User,
    original: Original,
    content: bytes,
    filename: str | None,
    title: str | None,
    import_origin: str,
    source_reference: str | None,
    description: str,
    job_type: ProcessingJobType,
    options: dict[str, Any],
    actor: Actor | None = None,
) -> tuple[ImportAccepted, uuid.UUID]:
    """Store the original, create its processing job and outbox row in one transaction."""
    sanitized = importing.sanitize_filename(filename)
    evidence_id = uuid.uuid4()
    key = EvidenceStorage.key_for(case_id, evidence_id)
    case = lock_case_for_write(db, case_id)
    staged = storage.store(key, content)
    try:
        evidence = EvidenceObject(
            id=evidence_id,
            case_id=case_id,
            kind=original.kind,
            title=(title or sanitized.value or f"Imported {original.import_format}")[:300],
            original_filename=sanitized.value,
            content_type=original.content_type,
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
            storage_key=key,
            acquisition_method=AcquisitionMethod.AUTHORIZED_IMPORT,
            import_origin=import_origin,
            source_reference=source_reference,
            collected_at=utcnow(),
            imported_by_user_id=user.id,
            description=description,
            collection_metadata={"import_format": original.import_format},
        )
        db.add(evidence)
        db.flush()
        duplicates = find_duplicates(db, case_id, staged.sha256, evidence_id)
        if original.kind in (EvidenceKind.TEXT, EvidenceKind.JSON):
            indexing.mark_evidence_for_indexing(
                db, settings, case_id=case_id, evidence_id=evidence_id, ai_mode=case.ai_mode
            )
        job = _new_job(db, evidence, user, job_type, options)
        outbox = _enqueue(db, job)
        if actor is not None:
            audit.record(
                db,
                actor,
                "evidence.imported",
                case_id=case_id,
                target_type="evidence",
                target_id=evidence_id,
                details={
                    "import_format": original.import_format,
                    "size_bytes": staged.size_bytes,
                    "processing_job_id": job.id,
                },
            )
        db.commit()
    except BaseException:
        db.rollback()
        storage.remove_key(key)
        raise
    logger.info(
        "processing_import_accepted",
        extra={
            "case_ref": str(case_id)[:8],
            "kind": str(original.kind),
            "size_bytes": staged.size_bytes,
            "job_type": str(job_type),
        },
    )
    db.refresh(job)
    return (
        ImportAccepted(
            evidence=to_out(evidence),
            job=job_out(job),
            filename_sanitized=sanitized.changed,
            duplicate_of=duplicates,
        ),
        outbox.id,
    )


def _new_job(
    db: Session,
    evidence: EvidenceObject,
    user: User,
    job_type: ProcessingJobType,
    options: dict[str, Any],
) -> ProcessingJob:
    job = ProcessingJob(
        case_id=evidence.case_id,
        evidence_id=evidence.id,
        job_type=job_type,
        status=ProcessingStatus.QUEUED,
        options=options,
        result={},
        created_by_user_id=user.id,
    )
    db.add(job)
    db.flush()
    return job


def _enqueue(db: Session, job: ProcessingJob) -> Any:
    return dispatch.enqueue(
        db,
        task_name=dispatch.PROCESS_IMPORT_TASK,
        aggregate_type=AggregateType.PROCESSING_JOB,
        aggregate_id=job.id,
        case_id=job.case_id,
    )


def job_out(job: ProcessingJob) -> ProcessingJobOut:
    return ProcessingJobOut(
        id=job.id,
        case_id=job.case_id,
        evidence_id=job.evidence_id,
        job_type=job.job_type,
        status=job.status,
        options=job.options or {},
        result=job.result or {},
        needs_input=job.needs_input,
        attempts=job.attempts,
        error_code=job.error_code,
        error_detail=job.error_detail,
        cancel_requested_at=job.cancel_requested_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def job_detail(db: Session, job: ProcessingJob) -> ProcessingJobDetail:
    conditions = (EvidenceObject.processing_job_id == job.id, EvidenceObject.case_id == job.case_id)
    derived = db.scalars(
        select(EvidenceObject).where(*conditions).order_by(EvidenceObject.page_part).limit(500)
    )
    total = db.scalar(select(func.count()).select_from(EvidenceObject).where(*conditions)) or 0
    observations = db.scalar(
        select(func.count())
        .select_from(Observation)
        .where(
            Observation.case_id == job.case_id,
            Observation.payload["processing_job_id"].astext == str(job.id),
        )
    )
    return ProcessingJobDetail(
        **job_out(job).model_dump(),
        derived_evidence=[
            DerivedEvidenceOut(
                id=row.id,
                title=row.title,
                kind=row.kind,
                page_part=row.page_part,
                size_bytes=row.size_bytes,
                content_type=row.content_type,
            )
            for row in derived
        ],
        derived_evidence_total=total,
        observation_count=observations or 0,
    )


def get_job(
    db: Session, case_id: uuid.UUID, job_id: uuid.UUID, *, lock: bool = False
) -> ProcessingJob:
    statement = select(ProcessingJob).where(
        ProcessingJob.id == job_id, ProcessingJob.case_id == case_id
    )
    if lock:
        statement = statement.with_for_update()
    job = db.scalar(statement)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="processing_job_not_found")
    return job


def _require_original(evidence: EvidenceObject) -> None:
    if (
        evidence.acquisition_method != AcquisitionMethod.AUTHORIZED_IMPORT
        or evidence.processing_job_id is not None
    ):
        raise _reject(
            "not_an_imported_original",
            "Only an imported original can be processed; derived records are recreated by "
            "processing their original again.",
            status.HTTP_409_CONFLICT,
        )


def start_processing(
    db: Session,
    settings: Settings,
    *,
    case_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: User,
    job_type: ProcessingJobType,
    options: dict[str, Any],
) -> tuple[ProcessingJob, uuid.UUID]:
    """Process an imported original again (new options, or after a failure or cancellation)."""
    lock_case_for_write(db, case_id)
    evidence = db.scalar(
        select(EvidenceObject)
        .where(EvidenceObject.id == evidence_id, EvidenceObject.case_id == case_id)
        .with_for_update()
    )
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evidence_not_found")
    if not (
        job_type == ProcessingJobType.DOCUMENT_TEXT
        and evidence.acquisition_method == AcquisitionMethod.AUTHORIZED_IMPORT
        and evidence.kind == EvidenceKind.PDF
    ):
        # PDF attachments extracted from an export can be processed like imported PDFs.
        _require_original(evidence)
    allowed = {
        ProcessingJobType.WHATSAPP_EXPORT: (EvidenceKind.ARCHIVE, EvidenceKind.TEXT),
        ProcessingJobType.DOCUMENT_TEXT: (EvidenceKind.PDF,),
    }[job_type]
    if evidence.kind not in allowed:
        raise _reject(
            "unsupported_original",
            f"{job_type} processing accepts evidence of kind {', '.join(allowed)}.",
            status.HTTP_409_CONFLICT,
        )
    active = db.scalar(
        select(ProcessingJob.id).where(
            ProcessingJob.evidence_id == evidence.id,
            ProcessingJob.job_type == job_type,
            ProcessingJob.status.in_(ACTIVE_STATUSES),
        )
    )
    if active is not None:
        raise _reject(
            "processing_already_active",
            "This original already has a queued, running or waiting job of this type.",
            status.HTTP_409_CONFLICT,
        )
    job = _new_job(db, evidence, user, job_type, options)
    outbox = _enqueue(db, job)
    db.commit()
    db.refresh(job)
    return job, outbox.id


def provide_input(
    db: Session,
    *,
    case_id: uuid.UUID,
    job_id: uuid.UUID,
    date_order: str,
    timezone: str | None,
) -> tuple[ProcessingJob, uuid.UUID]:
    lock_case_for_write(db, case_id)
    job = get_job(db, case_id, job_id, lock=True)
    if job.status != ProcessingStatus.NEEDS_INPUT:
        raise _reject(
            "processing_job_not_waiting",
            "This job is not waiting for input.",
            status.HTTP_409_CONFLICT,
        )
    options = dict(job.options or {})
    chosen = whatsapp_options(
        date_order, timezone if timezone is not None else options.get("timezone", "unknown")
    )
    options.update(chosen)
    job.options = options
    job.status = ProcessingStatus.QUEUED
    job.needs_input = None
    # A new analyst decision starts a fresh set of attempts.
    job.attempts = 0
    job.error_code = None
    job.error_detail = None
    outbox = _enqueue(db, job)
    db.commit()
    db.refresh(job)
    return job, outbox.id


def request_cancel(db: Session, *, case_id: uuid.UUID, job_id: uuid.UUID) -> ProcessingJob:
    """Queued and waiting jobs stop now; a running job stops at its next bounded unit of work
    (records it already committed are kept)."""
    job = get_job(db, case_id, job_id, lock=True)
    if job.status in TERMINAL_STATUSES:
        raise _reject(
            "processing_job_finished", "This job has already finished.", status.HTTP_409_CONFLICT
        )
    now = utcnow()
    if job.cancel_requested_at is None:
        job.cancel_requested_at = now
    if job.status in (ProcessingStatus.QUEUED, ProcessingStatus.NEEDS_INPUT):
        jobs.finish(
            db,
            job,
            ProcessingStatus.CANCELED,
            error_code="canceled",
            error_detail="Canceled before processing recorded results.",
        )
    db.commit()
    db.refresh(job)
    return job
