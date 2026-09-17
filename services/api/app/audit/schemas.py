from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.schemas import Page


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_label: str
    case_id: uuid.UUID | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    correlation_id: str | None
    details: dict[str, Any]


def audit_page(
    db: Session, conditions: list[Any], *, limit: int, offset: int
) -> Page[AuditEventOut]:
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
    rows = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[AuditEventOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
