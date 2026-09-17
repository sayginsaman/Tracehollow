"""Authorized imports that need processing (WhatsApp exports, documents) and their jobs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select

from app.cases.access import ReadableCase, WritableCase
from app.deps import DbDep, PrincipalDep, SettingsDep
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.evidence.storage import EvidenceStorage
from app.imports import service
from app.imports.models import ProcessingJob, ProcessingJobType, ProcessingStatus
from app.imports.schemas import (
    DateOrderChoice,
    ImportAccepted,
    OcrMode,
    ProcessingInputIn,
    ProcessingJobDetail,
    ProcessingJobOut,
    ProcessingStartIn,
)
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["imports"])

WHATSAPP_IMPORT_PATH_PATTERN = r"^/api/v1/cases/[^/]+/imports/whatsapp$"
DOCUMENT_IMPORT_PATH_PATTERN = r"^/api/v1/cases/[^/]+/imports/documents$"


def _publish(request: Request, outbox_id: uuid.UUID, case_id: uuid.UUID) -> None:
    state = request.app.state
    dispatch.publish_after_commit(state.session_factory, state.celery, state.settings, outbox_id)
    dispatch.publish_aggregate_if_pending(
        state.session_factory, state.celery, state.settings, AggregateType.CASE_INDEX, case_id
    )


def _read_upload(file: UploadFile, limit: int) -> bytes:
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "import_too_large", "message": f"The file exceeds {limit} bytes."},
        )
    return content


def _clean(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


@router.post("/imports/whatsapp", status_code=status.HTTP_202_ACCEPTED)
def import_whatsapp_export(
    request: Request,
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Exported chat .txt or the export .zip")],
    import_origin: Annotated[str, Form(min_length=3, max_length=2000)],
    timezone: Annotated[str, Form(min_length=1, max_length=64)],
    date_order: Annotated[DateOrderChoice, Form()] = "auto",
    title: Annotated[str | None, Form(max_length=300)] = None,
    source_reference: Annotated[str | None, Form(max_length=2048)] = None,
    description: Annotated[str, Form(max_length=10_000)] = "",
) -> ImportAccepted:
    """Store an authorized WhatsApp export as evidence and queue parsing in the worker.

    ``timezone`` is required: an IANA name, or ``unknown`` to keep local times off the UTC
    timeline. ``date_order=auto`` asks for a decision when the export does not prove it.
    """
    options = service.whatsapp_options(date_order, timezone)
    content = _read_upload(file, settings.import_max_archive_bytes)
    original = service.classify_whatsapp_upload(settings, content)
    storage: EvidenceStorage = request.app.state.evidence_storage
    accepted, outbox_id = service.store_original_and_queue(
        db,
        storage,
        settings,
        case_id=case.id,
        user=principal.user,
        original=original,
        content=content,
        filename=file.filename,
        title=_clean(title),
        import_origin=import_origin.strip(),
        source_reference=_clean(source_reference),
        description=description.strip(),
        job_type=ProcessingJobType.WHATSAPP_EXPORT,
        options=options,
    )
    _publish(request, outbox_id, case.id)
    return accepted


@router.post("/imports/documents", status_code=status.HTTP_202_ACCEPTED)
def import_document(
    request: Request,
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="PDF document")],
    import_origin: Annotated[str, Form(min_length=3, max_length=2000)],
    ocr: Annotated[OcrMode, Form()] = "if_needed",
    title: Annotated[str | None, Form(max_length=300)] = None,
    source_reference: Annotated[str | None, Form(max_length=2048)] = None,
    description: Annotated[str, Form(max_length=10_000)] = "",
) -> ImportAccepted:
    """Store an authorized PDF as evidence and queue text extraction (and optional OCR)."""
    content = _read_upload(file, settings.import_max_document_bytes)
    original = service.classify_document_upload(settings, content)
    storage: EvidenceStorage = request.app.state.evidence_storage
    accepted, outbox_id = service.store_original_and_queue(
        db,
        storage,
        settings,
        case_id=case.id,
        user=principal.user,
        original=original,
        content=content,
        filename=file.filename,
        title=_clean(title),
        import_origin=import_origin.strip(),
        source_reference=_clean(source_reference),
        description=description.strip(),
        job_type=ProcessingJobType.DOCUMENT_TEXT,
        options={"ocr": ocr},
    )
    _publish(request, outbox_id, case.id)
    return accepted


@router.get("/processing-jobs")
def list_processing_jobs(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    evidence_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[ProcessingStatus | None, Query(alias="status")] = None,
) -> Page[ProcessingJobOut]:
    conditions = [ProcessingJob.case_id == case.id]
    if evidence_id is not None:
        conditions.append(ProcessingJob.evidence_id == evidence_id)
    if status_filter is not None:
        conditions.append(ProcessingJob.status == status_filter)
    total = db.scalar(select(func.count()).select_from(ProcessingJob).where(*conditions)) or 0
    rows = db.scalars(
        select(ProcessingJob)
        .where(*conditions)
        .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[service.job_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/processing-jobs/{job_id}")
def get_processing_job(case: ReadableCase, db: DbDep, job_id: uuid.UUID) -> ProcessingJobDetail:
    return service.job_detail(db, service.get_job(db, case.id, job_id))


@router.post("/processing-jobs/{job_id}/input")
def provide_processing_input(
    request: Request, case: WritableCase, db: DbDep, job_id: uuid.UUID, body: ProcessingInputIn
) -> ProcessingJobDetail:
    job, outbox_id = service.provide_input(
        db, case_id=case.id, job_id=job_id, date_order=body.date_order, timezone=body.timezone
    )
    _publish(request, outbox_id, case.id)
    return service.job_detail(db, job)


@router.post("/processing-jobs/{job_id}/cancel")
def cancel_processing_job(case: ReadableCase, db: DbDep, job_id: uuid.UUID) -> ProcessingJobDetail:
    job = service.request_cancel(db, case_id=case.id, job_id=job_id)
    return service.job_detail(db, job)


@router.post("/evidence/{evidence_id}/processing", status_code=status.HTTP_202_ACCEPTED)
def start_processing(
    request: Request,
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    evidence_id: uuid.UUID,
    body: ProcessingStartIn,
) -> ProcessingJobDetail:
    job_type = ProcessingJobType(body.job_type)
    if job_type == ProcessingJobType.WHATSAPP_EXPORT:
        options: dict[str, str] = service.whatsapp_options(body.date_order, body.timezone)
    else:
        options = {"ocr": body.ocr}
    job, outbox_id = service.start_processing(
        db,
        settings,
        case_id=case.id,
        evidence_id=evidence_id,
        user=principal.user,
        job_type=job_type,
        options=options,
    )
    _publish(request, outbox_id, case.id)
    return service.job_detail(db, job)
