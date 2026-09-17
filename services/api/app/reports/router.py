"""Report preview and download. Reading a case is enough; nothing is stored or published."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.ai.models import AiMessage, AiRun, MessageKind, MessageRole
from app.audit.service import record
from app.cases.access import AnalystCase, ReadableCase
from app.cases.models import Note
from app.deps import ActorDep, DbDep
from app.entities.models import Entity, Relationship
from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.reports import builder
from app.reports.schemas import ReportPreview, ReportSelectable, ReportSelection, SelectableItem

router = APIRouter(prefix="/api/v1/cases/{case_id}/reports", tags=["reports"])
LIMIT = 200


def _storage(request: Request) -> EvidenceStorage:
    storage: EvidenceStorage = request.app.state.evidence_storage
    return storage


@router.post("/html/preview")
def preview_report(
    request: Request, case: AnalystCase, db: DbDep, selection: ReportSelection
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
    request: Request, case: AnalystCase, db: DbDep, actor: ActorDep, selection: ReportSelection
) -> Response:
    markup, counts, redactor, _warnings = builder.build_report(
        db, _storage(request), case, selection
    )
    record(
        db,
        actor,
        "export.report_downloaded",
        case_id=case.id,
        target_type="case",
        target_id=case.id,
        details={
            "format": "html",
            "counts": counts.model_dump() if hasattr(counts, "model_dump") else None,
            "redactions_applied": redactor.redactions,
        },
    )
    db.commit()
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


@router.get("/selectable")
def selectable(case: ReadableCase, db: DbDep) -> ReportSelectable:
    source = aliased(Entity)
    target = aliased(Entity)
    return ReportSelectable(
        entities=[
            SelectableItem(
                id=row.id, label=row.display_name, detail=f"{row.entity_type} · {row.origin}"
            )
            for row in db.scalars(
                select(Entity)
                .where(Entity.case_id == case.id)
                .order_by(Entity.updated_at.desc())
                .limit(LIMIT)
            )
        ],
        relationships=[
            SelectableItem(
                id=rel.id,
                label=f"{source_name} {rel.predicate} {target_name}",
                detail=f"{rel.origin} · {rel.review_status}",
            )
            for rel, source_name, target_name in db.execute(
                select(Relationship, source.display_name, target.display_name)
                .join(source, source.id == Relationship.source_entity_id)
                .join(target, target.id == Relationship.target_entity_id)
                .where(Relationship.case_id == case.id)
                .order_by(Relationship.updated_at.desc())
                .limit(LIMIT)
            )
        ],
        evidence=[
            SelectableItem(
                id=row.id, label=row.title, detail=f"{row.kind} · {row.acquisition_method}"
            )
            for row in db.scalars(
                select(EvidenceObject)
                .where(EvidenceObject.case_id == case.id)
                .order_by(EvidenceObject.collected_at.desc())
                .limit(LIMIT)
            )
        ],
        ai_answers=[
            SelectableItem(
                id=message.id,
                label=question or "Question not recorded",
                detail=f"AI-generated · {(message.answer or {}).get('status', 'unknown')}",
            )
            for message, question in db.execute(
                select(AiMessage, AiRun.question)
                .join(AiRun, AiRun.id == AiMessage.ai_run_id)
                .where(
                    AiMessage.case_id == case.id,
                    AiMessage.role == MessageRole.ASSISTANT,
                    AiMessage.kind == MessageKind.ANSWER,
                )
                .order_by(AiMessage.created_at.desc())
                .limit(LIMIT)
            )
        ],
        notes=[
            SelectableItem(id=row.id, label=" ".join(row.body.split())[:120], detail="analyst note")
            for row in db.scalars(
                select(Note)
                .where(Note.case_id == case.id)
                .order_by(Note.updated_at.desc())
                .limit(LIMIT)
            )
        ],
    )
