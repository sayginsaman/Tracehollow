from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select

from app.cases.access import ReadableCase, WritableCase
from app.config import Settings
from app.deps import ActorDep, DbDep, PrincipalDep, SettingsDep
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.evidence import service
from app.evidence.importing import ImportRejectedError
from app.evidence.models import EvidenceKind, EvidenceObject
from app.evidence.schemas import (
    EvidenceDeletionIn,
    EvidenceDeletionOut,
    EvidenceDetail,
    EvidenceOut,
    EvidencePreview,
    ImportResult,
)
from app.evidence.storage import EvidenceStorage
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}/evidence", tags=["evidence"])

IMPORT_PATH_PATTERN = r"^/api/v1/cases/[^/]+/evidence/imports$"
_DOWNLOAD_EXTENSIONS: dict[str, str] = {
    EvidenceKind.TEXT: "txt",
    EvidenceKind.JSON: "json",
    EvidenceKind.HTML: "txt",
    EvidenceKind.XML: "txt",
    EvidenceKind.PDF: "pdf",
    EvidenceKind.ARCHIVE: "zip",
}


def _storage(request: Request) -> EvidenceStorage:
    storage: EvidenceStorage = request.app.state.evidence_storage
    return storage


def _parse_published_at(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_timestamp", "message": "source_published_at must be ISO 8601"},
        ) from None
    if parsed.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "timestamp_without_offset",
                "message": "source_published_at must include a timezone offset",
            },
        )
    return parsed.astimezone(UTC)


def _import_limit(settings: Settings) -> int:
    return settings.evidence_max_import_bytes


@router.get("")
def list_evidence(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    kind: Annotated[EvidenceKind | None, Query()] = None,
    acquisition_method: Annotated[str | None, Query(max_length=32)] = None,
    query_run_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[EvidenceOut]:
    conditions = [EvidenceObject.case_id == case.id]
    if kind is not None:
        conditions.append(EvidenceObject.kind == kind)
    if acquisition_method is not None:
        conditions.append(EvidenceObject.acquisition_method == acquisition_method)
    if query_run_id is not None:
        conditions.append(EvidenceObject.query_run_id == query_run_id)
    if q:
        pattern = f"%{q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"
        conditions.append(EvidenceObject.title.ilike(pattern, escape="\\"))
    total = db.scalar(select(func.count()).select_from(EvidenceObject).where(*conditions)) or 0
    rows = db.scalars(
        select(EvidenceObject)
        .where(*conditions)
        .order_by(EvidenceObject.collected_at.desc(), EvidenceObject.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[service.to_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("/imports", status_code=status.HTTP_201_CREATED)
def import_evidence(
    request: Request,
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    actor: ActorDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Text or JSON file")],
    kind: Annotated[EvidenceKind, Form()],
    import_origin: Annotated[str, Form(min_length=3, max_length=2000)],
    title: Annotated[str | None, Form(max_length=300)] = None,
    source_reference: Annotated[str | None, Form(max_length=2048)] = None,
    source_published_at: Annotated[str | None, Form(max_length=64)] = None,
    description: Annotated[str, Form(max_length=10_000)] = "",
) -> ImportResult:
    limit = _import_limit(settings)
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="evidence_too_large")
    published_at = _parse_published_at(source_published_at)
    try:
        result = service.import_evidence(
            db,
            _storage(request),
            settings,
            case_id=case.id,
            user=principal.user,
            kind=kind,
            content=content,
            filename=file.filename,
            title=title.strip() if title and title.strip() else None,
            import_origin=import_origin.strip(),
            source_reference=source_reference.strip() if source_reference else None,
            source_published_at=published_at,
            source_published_at_original=(
                source_published_at.strip()
                if published_at is not None and source_published_at
                else None
            ),
            description=description.strip(),
            actor=actor,
        )
    except ImportRejectedError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": exc.code, "message": exc.message}
        ) from None
    state = request.app.state
    dispatch.publish_aggregate_if_pending(
        state.session_factory, state.celery, state.settings, AggregateType.CASE_INDEX, case.id
    )
    return result


@router.get("/{evidence_id}")
def get_evidence(
    request: Request, case: ReadableCase, db: DbDep, evidence_id: uuid.UUID
) -> EvidenceDetail:
    evidence = service.get_case_evidence(db, case.id, evidence_id)
    return service.evidence_detail(db, _storage(request), evidence)


@router.get("/{evidence_id}/preview")
def preview_evidence(
    request: Request, case: ReadableCase, db: DbDep, settings: SettingsDep, evidence_id: uuid.UUID
) -> EvidencePreview:
    evidence = service.get_case_evidence(db, case.id, evidence_id)
    return service.build_preview(_storage(request), settings, evidence)


@router.get("/{evidence_id}/content")
def download_evidence(
    request: Request, case: ReadableCase, db: DbDep, evidence_id: uuid.UUID
) -> Response:
    """Original bytes as an inert attachment; never rendered in the application origin."""
    evidence = service.get_case_evidence(db, case.id, evidence_id)
    content = service.read_content(_storage(request), evidence)
    extension = _DOWNLOAD_EXTENSIONS.get(evidence.kind, "bin")
    fallback = f"evidence-{evidence.id}.{extension}"
    disposition = f'attachment; filename="{fallback}"'
    if evidence.original_filename:
        disposition += f"; filename*=UTF-8''{quote(evidence.original_filename, safe='')}"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Evidence-SHA256": evidence.sha256,
        },
    )


@router.post("/{evidence_id}/deletion")
def delete_evidence(
    request: Request,
    case: WritableCase,
    db: DbDep,
    actor: ActorDep,
    evidence_id: uuid.UUID,
    body: EvidenceDeletionIn,
) -> EvidenceDeletionOut:
    """Deliberately delete an imported evidence record, its stored file and derived index data."""
    return service.delete_evidence(
        db,
        _storage(request),
        case_id=case.id,
        evidence_id=evidence_id,
        confirm_title=body.confirm_title,
        actor=actor,
    )
