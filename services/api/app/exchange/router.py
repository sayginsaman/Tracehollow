"""STIX 2.1 export and import routes (analysts; viewers can neither export nor import)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from app.audit.service import record
from app.cases.access import AnalystCase, WritableCase
from app.deps import ActorDep, DbDep, PrincipalDep, SettingsDep
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.evidence.storage import EvidenceStorage
from app.exchange import stix
from app.exchange.stix_export import ExportOptions, build_bundle
from app.exchange.stix_import import StixRejectedError, import_bundle, import_result

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["exchange"])

STIX_IMPORT_PATH_PATTERN = r"^/api/v1/cases/[^/]+/imports/stix$"


def _options(
    include_source_urls: bool, include_unreviewed: bool, include_ai_suggestions: bool
) -> ExportOptions:
    return ExportOptions(
        include_source_urls=include_source_urls,
        include_unreviewed=include_unreviewed,
        include_ai_suggestions=include_ai_suggestions,
    )


@router.get("/exports/stix/report")
def stix_export_report(
    case: AnalystCase,
    db: DbDep,
    settings: SettingsDep,
    include_source_urls: Annotated[bool, Query()] = False,
    include_unreviewed: Annotated[bool, Query()] = True,
    include_ai_suggestions: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """What the bundle would contain and leave out, without downloading it."""
    bundle, report = build_bundle(
        db,
        case,
        _options(include_source_urls, include_unreviewed, include_ai_suggestions),
        secret_values=settings.secret_values(),
    )
    return {**report.as_dict(), "objects": len(bundle["objects"]), "media_type": stix.MEDIA_TYPE}


@router.get("/exports/stix")
def stix_export(
    case: AnalystCase,
    db: DbDep,
    settings: SettingsDep,
    actor: ActorDep,
    include_source_urls: Annotated[bool, Query()] = False,
    include_unreviewed: Annotated[bool, Query()] = True,
    include_ai_suggestions: Annotated[bool, Query()] = False,
) -> Response:
    options = _options(include_source_urls, include_unreviewed, include_ai_suggestions)
    bundle, report = build_bundle(db, case, options, secret_values=settings.secret_values())
    body = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    for value in settings.secret_values():
        if value.encode() in body:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, detail="export_secret_check_failed"
            )
    record(
        db,
        actor,
        "export.downloaded",
        case_id=case.id,
        target_type="case",
        target_id=case.id,
        details={
            "format": "stix-2.1",
            "objects": len(bundle["objects"]),
            "excluded": dict(report.excluded),
            "include_source_urls": include_source_urls,
            "include_ai_suggestions": include_ai_suggestions,
        },
    )
    db.commit()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=body,
        media_type=stix.MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="tracehollow-case-{case.id}-{stamp}.stix.json"'
            ),
            "X-Tracehollow-Excluded": str(sum(report.excluded.values())),
        },
    )


@router.post("/imports/stix", status_code=status.HTTP_201_CREATED)
def stix_import(
    request: Request,
    case: WritableCase,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File(description="STIX 2.1 bundle (JSON)")],
    import_origin: Annotated[str, Form(min_length=3, max_length=2000)],
    on_unsupported: Annotated[Literal["skip", "reject"], Form()] = "skip",
) -> dict[str, Any]:
    limit = settings.stix_import_max_bytes
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "import_too_large",
                "message": f"A STIX bundle may be at most {limit} bytes.",
            },
        )
    storage: EvidenceStorage = request.app.state.evidence_storage
    try:
        summary = import_bundle(
            db,
            storage,
            settings,
            case_id=case.id,
            user=principal.user,
            actor=actor,
            content=content,
            filename=file.filename,
            import_origin=import_origin.strip(),
            on_unsupported=on_unsupported,
        )
    except StixRejectedError as exc:
        db.rollback()
        record(
            db,
            actor,
            "exchange.stix_rejected",
            case_id=case.id,
            target_type="case",
            target_id=case.id,
            details={"code": exc.code, "size_bytes": len(content)},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message, "objects": exc.details[:100]},
        ) from None
    state = request.app.state
    dispatch.publish_aggregate_if_pending(
        state.session_factory, state.celery, state.settings, AggregateType.CASE_INDEX, case.id
    )
    return import_result(summary, db)
