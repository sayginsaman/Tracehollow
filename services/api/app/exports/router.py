from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.audit.service import record
from app.cases.access import AnalystCase
from app.deps import ActorDep, DbDep
from app.exports import builder

router = APIRouter(prefix="/api/v1/cases/{case_id}/exports", tags=["exports"])


def _filename(case_id: str, extension: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"tracehollow-case-{case_id}-{stamp}.{extension}"


def _schema_revision(request: Request) -> str:
    return ",".join(sorted(request.app.state.expected_migration_heads))


def _audit_export(db: DbDep, actor: ActorDep, case_id: object, export_format: str) -> None:
    record(
        db,
        actor,
        "export.downloaded",
        case_id=case_id,  # type: ignore[arg-type]
        target_type="case",
        target_id=str(case_id),
        details={"format": export_format},
    )
    db.commit()


@router.get("/json")
def export_json(request: Request, case: AnalystCase, db: DbDep, actor: ActorDep) -> Response:
    data = builder.collect(db, case)
    manifest = builder.build_manifest(db, case, data, _schema_revision(request))
    _audit_export(db, actor, case.id, "json")
    return Response(
        content=builder.build_json_export(manifest, data),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(str(case.id), "json")}"'
        },
    )


@router.get("/csv")
def export_csv(request: Request, case: AnalystCase, db: DbDep, actor: ActorDep) -> Response:
    data = builder.collect(db, case)
    manifest = builder.build_manifest(db, case, data, _schema_revision(request))
    _audit_export(db, actor, case.id, "csv")
    return Response(
        content=builder.build_csv_export(manifest, data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_filename(str(case.id), "zip")}"'},
    )
