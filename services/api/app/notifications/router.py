"""The signed-in account's notifications, filtered by current case membership."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select, update

from app.cases.models import Case, CaseMember, CaseStatus
from app.db.base import utcnow
from app.deps import DbDep, PrincipalDep
from app.notifications.models import Notification
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    case_title: str | None
    monitor_id: uuid.UUID | None
    event_id: uuid.UUID
    event_type: str
    severity: str
    title: str
    body: str
    link: str | None
    created_at: datetime
    read_at: datetime | None


class UnreadCount(BaseModel):
    unread: int


def _visible(principal: PrincipalDep) -> Any:
    """Notifications without a case, or about a case the account can still open."""
    member = exists().where(
        CaseMember.case_id == Notification.case_id,
        CaseMember.user_id == principal.user.id,
        Case.id == Notification.case_id,
        Case.status.in_([CaseStatus.ACTIVE, CaseStatus.ARCHIVED]),
    )
    return (Notification.user_id == principal.user.id) & or_(Notification.case_id.is_(None), member)


def _out(db: DbDep, rows: list[Notification]) -> list[NotificationOut]:
    case_ids = {row.case_id for row in rows if row.case_id}
    titles = (
        dict(db.execute(select(Case.id, Case.title).where(Case.id.in_(case_ids))).tuples().all())
        if case_ids
        else {}
    )
    return [
        NotificationOut(
            id=row.id,
            case_id=row.case_id,
            case_title=titles.get(row.case_id) if row.case_id else None,
            monitor_id=row.monitor_id,
            event_id=row.event_id,
            event_type=row.event_type,
            severity=row.severity,
            title=row.title,
            body=row.body,
            link=row.link,
            created_at=row.created_at,
            read_at=row.read_at,
        )
        for row in rows
    ]


@router.get("")
def list_notifications(
    db: DbDep,
    principal: PrincipalDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    unread: Annotated[bool, Query()] = False,
) -> Page[NotificationOut]:
    conditions = [_visible(principal)]
    if unread:
        conditions.append(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(Notification).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(items=_out(db, rows), total=total, limit=limit, offset=offset)


@router.get("/unread-count")
def unread_count(db: DbDep, principal: PrincipalDep) -> UnreadCount:
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(_visible(principal), Notification.read_at.is_(None))
    )
    return UnreadCount(unread=int(count or 0))


def _get(db: DbDep, principal: PrincipalDep, notification_id: uuid.UUID) -> Notification:
    row = db.scalar(
        select(Notification).where(Notification.id == notification_id, _visible(principal))
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="notification_not_found")
    return row


@router.get("/{notification_id}")
def get_notification(
    db: DbDep, principal: PrincipalDep, notification_id: uuid.UUID
) -> NotificationOut:
    return _out(db, [_get(db, principal, notification_id)])[0]


@router.post("/{notification_id}/read")
def mark_read(db: DbDep, principal: PrincipalDep, notification_id: uuid.UUID) -> NotificationOut:
    row = _get(db, principal, notification_id)
    if row.read_at is None:
        row.read_at = utcnow()
        db.commit()
    return _out(db, [row])[0]


@router.post("/{notification_id}/unread")
def mark_unread(db: DbDep, principal: PrincipalDep, notification_id: uuid.UUID) -> NotificationOut:
    row = _get(db, principal, notification_id)
    row.read_at = None
    db.commit()
    return _out(db, [row])[0]


@router.post("/read-all")
def mark_all_read(db: DbDep, principal: PrincipalDep) -> UnreadCount:
    db.execute(
        update(Notification)
        .where(_visible(principal), Notification.read_at.is_(None))
        .values(read_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return UnreadCount(unread=0)
