"""Report preview and download. Reading a case is enough; nothing is stored or published."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.cases.access import ReadableCase
from app.deps import DbDep
from app.evidence.storage import EvidenceStorage
from app.reports import builder
from app.reports.schemas import ReportPreview, ReportSelection

router = APIRouter(prefix="/api/v1/cases/{case_id}/reports", tags=["reports"])


def _storage(request: Request) -> EvidenceStorage:
    storage: EvidenceStorage = request.app.state.evidence_storage
    return storage


@router.post("/html/preview")
def preview_report(
    request: Request, case: ReadableCase, db: DbDep, selection: ReportSelection
) -> ReportPreview:
    """The exact report that would be downloaded, with counts, redactions and warnings."""
    markup, counts, redactor, warnings = builder.build_report(
        db, _storage(request), case, selection
    )
    return ReportPreview(
        counts=counts,
        redactions_applied=redactor.redactions,
        credential_like_values_removed=redactor.credentials,
        warnings=warnings,
        size_bytes=len(markup.encode()),
        html=markup,
    )


@router.post("/html")
def download_report(
    request: Request, case: ReadableCase, db: DbDep, selection: ReportSelection
) -> Response:
    markup, _counts, _redactor, _warnings = builder.build_report(
        db, _storage(request), case, selection
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"tracehollow-report-{case.id}-{stamp}.html"
    return Response(
        content=markup.encode(),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # If a browser renders it in the application origin anyway, it cannot run or load.
            "Content-Security-Policy": f"sandbox; {builder.CSP}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
