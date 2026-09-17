"""Case audit trail: readable by the case's analysts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.audit.models import AuditEvent
from app.audit.schemas import AuditEventOut, audit_page
from app.cases.access import AnalystCase
from app.deps import DbDep
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}/audit-events", tags=["audit"])


@router.get("")
def case_audit(
    case: AnalystCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    action: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
    outcome: Annotated[str | None, Query(pattern="^(succeeded|denied|failed)$")] = None,
) -> Page[AuditEventOut]:
    conditions = [AuditEvent.case_id == case.id]
    if action:
        conditions.append(AuditEvent.action.startswith(action))
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    return audit_page(db, conditions, limit=limit, offset=offset)
