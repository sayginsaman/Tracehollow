from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.cases.access import ReadableCase
from app.deps import DbDep
from app.exports import builder

router = APIRouter(prefix="/api/v1/cases/{case_id}/exports", tags=["exports"])


def _filename(case_id: str, extension: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"tracehollow-case-{case_id}-{stamp}.{extension}"


def _schema_revision(request: Request) -> str:
    return ",".join(sorted(request.app.state.expected_migration_heads))


@router.get("/json")
def export_json(request: Request, case: ReadableCase, db: DbDep) -> Response:
    data = builder.collect(db, case)
    manifest = builder.build_manifest(db, case, data, _schema_revision(request))
    return Response(
        content=builder.build_json_export(manifest, data),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(str(case.id), "json")}"'
        },
    )


@router.get("/csv")
def export_csv(request: Request, case: ReadableCase, db: DbDep) -> Response:
    data = builder.collect(db, case)
    manifest = builder.build_manifest(db, case, data, _schema_revision(request))
    return Response(
        content=builder.build_csv_export(manifest, data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_filename(str(case.id), "zip")}"'},
    )
