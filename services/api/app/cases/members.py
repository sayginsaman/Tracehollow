"""Case membership: who can open a case and with which role.

Changes lock the case row, so two analysts removing each other at the same time cannot leave a case
without an analyst. A case must always keep at least one *active* analyst (an active account whose
account role allows the analyst role); an administrator can restore access to a case that lost its
last analyst through account deactivation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import Actor, record
from app.auth.models import AccountRole, User
from app.auth.permissions import ALLOWED_MEMBERSHIP_ROLES, account_role, effective_case_role
from app.auth.security import normalize_username
from app.cases.access import AnalystCase, ReadableCase
from app.cases.models import Case, CaseMember, CaseRole
from app.deps import ActorDep, DbDep

router = APIRouter(prefix="/api/v1/cases/{case_id}/members", tags=["members"])


class MemberOut(BaseModel):
    user_id: uuid.UUID
    username: str
    account_role: str
    account_active: bool
    membership_role: str
    # Membership role capped by the account role; what the account can actually do here.
    effective_role: str
    added_at: datetime
    updated_at: datetime


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    role: CaseRole


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: CaseRole


def members_out(db: Session, case_id: uuid.UUID) -> list[MemberOut]:
    rows = db.execute(
        select(CaseMember, User)
        .join(User, User.id == CaseMember.user_id)
        .where(CaseMember.case_id == case_id)
        .order_by(func.lower(User.username))
    ).all()
    return [
        MemberOut(
            user_id=user.id,
            username=user.username,
            account_role=user.role,
            account_active=user.is_active,
            membership_role=member.role,
            effective_role=effective_case_role(user.role, member.role),
            added_at=member.created_at,
            updated_at=member.updated_at,
        )
        for member, user in rows
    ]


def active_analyst_count(db: Session, case_id: uuid.UUID, *, excluding: uuid.UUID | None) -> int:
    conditions = [
        CaseMember.case_id == case_id,
        CaseMember.role == CaseRole.ANALYST,
        User.is_active.is_(True),
        User.role != AccountRole.VIEWER,
    ]
    if excluding is not None:
        conditions.append(CaseMember.user_id != excluding)
    return int(
        db.scalar(
            select(func.count())
            .select_from(CaseMember)
            .join(User, User.id == CaseMember.user_id)
            .where(*conditions)
        )
        or 0
    )


def _lock_case(db: Session, case_id: uuid.UUID) -> Case:
    case = db.scalar(select(Case).where(Case.id == case_id).with_for_update())
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="case_not_found")
    return case


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def _check_role_allowed(user: User, role: CaseRole) -> None:
    if role not in ALLOWED_MEMBERSHIP_ROLES[account_role(user.role)]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "role_exceeds_account_role",
                "message": "A viewer account can only be a viewer in a case.",
            },
        )


def add_member(
    db: Session, actor: Actor, case_id: uuid.UUID, username: str, role: CaseRole
) -> MemberOut:
    _lock_case(db, case_id)
    user = db.scalar(select(User).where(User.username_normalized == normalize_username(username)))
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "account_not_found", "message": "No active account has that username."},
        )
    _check_role_allowed(user, role)
    existing = db.get(CaseMember, (case_id, user.id))
    if existing is not None:
        raise _conflict("already_member", "That account is already a member of this case.")
    db.add(CaseMember(case_id=case_id, user_id=user.id, role=role, added_by_user_id=actor.user_id))
    record(
        db,
        actor,
        "membership.added",
        case_id=case_id,
        target_type="user",
        target_id=user.id,
        details={"role": str(role), "username": user.username},
    )
    db.flush()
    return next(m for m in members_out(db, case_id) if m.user_id == user.id)


def change_member_role(
    db: Session, actor: Actor, case_id: uuid.UUID, user_id: uuid.UUID, role: CaseRole
) -> MemberOut:
    _lock_case(db, case_id)
    member = db.get(CaseMember, (case_id, user_id), with_for_update=True)
    user = db.get(User, user_id)
    if member is None or user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member_not_found")
    _check_role_allowed(user, role)
    previous = member.role
    if previous == role:
        return next(m for m in members_out(db, case_id) if m.user_id == user_id)
    if role != CaseRole.ANALYST and active_analyst_count(db, case_id, excluding=user_id) == 0:
        raise _conflict(
            "last_analyst",
            "Add another analyst before changing this role; a case needs at least one active "
            "analyst.",
        )
    member.role = role
    record(
        db,
        actor,
        "membership.role_changed",
        case_id=case_id,
        target_type="user",
        target_id=user_id,
        details={"previous_role": previous, "role": str(role), "username": user.username},
    )
    db.flush()
    return next(m for m in members_out(db, case_id) if m.user_id == user_id)


def remove_member(db: Session, actor: Actor, case_id: uuid.UUID, user_id: uuid.UUID) -> None:
    _lock_case(db, case_id)
    member = db.get(CaseMember, (case_id, user_id), with_for_update=True)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member_not_found")
    if active_analyst_count(db, case_id, excluding=user_id) == 0:
        raise _conflict(
            "last_analyst",
            "Add another analyst before removing this member; a case needs at least one active "
            "analyst.",
        )
    user = db.get(User, user_id)
    db.delete(member)
    record(
        db,
        actor,
        "membership.removed",
        case_id=case_id,
        target_type="user",
        target_id=user_id,
        details={"previous_role": member.role, "username": user.username if user else None},
    )
    db.flush()


@router.get("")
def list_members(case: ReadableCase, db: DbDep) -> list[MemberOut]:
    return members_out(db, case.id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_member(case: AnalystCase, db: DbDep, actor: ActorDep, body: MemberAdd) -> MemberOut:
    member = add_member(db, actor, case.id, body.username, body.role)
    db.commit()
    return member


@router.patch("/{user_id}")
def update_member(
    case: AnalystCase, db: DbDep, actor: ActorDep, user_id: uuid.UUID, body: MemberUpdate
) -> MemberOut:
    member = change_member_role(db, actor, case.id, user_id, body.role)
    db.commit()
    return member


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(case: AnalystCase, db: DbDep, actor: ActorDep, user_id: uuid.UUID) -> None:
    remove_member(db, actor, case.id, user_id)
    db.commit()
