"""System administration: local accounts, case access and the system audit trail.

Administrators manage who can sign in and which cases each account can open. None of these routes
return case content: the case directory lists identifiers, titles, statuses and members only, so
an administrator can restore access to a case without reading it (docs/security/permissions.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case as sql_case
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.schemas import AuditEventOut, audit_page
from app.audit.service import record
from app.auth import service as accounts
from app.auth.models import AccountRole, User
from app.auth.permissions import SystemPermission
from app.auth.security import PASSWORD_MAX_LENGTH, USERNAME_MAX_LENGTH, normalize_username
from app.cases import members
from app.cases.models import Case, CaseMember, CaseRole, CaseStatus
from app.deps import ActorDep, DbDep, PrincipalDep, require_system_permission
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["administration"],
    dependencies=[require_system_permission(SystemPermission.MANAGE_ACCOUNTS)],
)
case_access_router = APIRouter(
    prefix="/api/v1/admin/cases",
    tags=["administration"],
    dependencies=[require_system_permission(SystemPermission.MANAGE_CASE_ACCESS)],
)
audit_router = APIRouter(
    prefix="/api/v1/admin/audit-events",
    tags=["administration"],
    dependencies=[require_system_permission(SystemPermission.READ_SYSTEM_AUDIT)],
)
directory_router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


class AccountOut(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    locked_until: datetime | None
    case_count: int
    analyst_case_count: int


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=USERNAME_MAX_LENGTH * 2)]
    role: AccountRole
    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AccountRole | None = None
    is_active: bool | None = None


class PasswordSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class AccountChangeOut(BaseModel):
    account: AccountOut
    # Cases where this change left no active analyst; an administrator can add one.
    cases_without_active_analyst: list[uuid.UUID]


class DirectoryCase(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    created_at: datetime
    member_count: int
    active_analyst_count: int


class DirectoryAccount(BaseModel):
    id: uuid.UUID
    username: str
    role: str


def _accounts_out(db: Session, users: list[User]) -> list[AccountOut]:
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    if users:
        for user_id, total, analyst in db.execute(
            select(
                CaseMember.user_id,
                func.count(),
                func.count(sql_case((CaseMember.role == CaseRole.ANALYST, 1))),
            )
            .where(CaseMember.user_id.in_([user.id for user in users]))
            .group_by(CaseMember.user_id)
        ):
            counts[user_id] = (int(total), int(analyst))
    return [
        AccountOut(
            id=user.id,
            username=user.username,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            locked_until=user.locked_until,
            case_count=counts.get(user.id, (0, 0))[0],
            analyst_case_count=counts.get(user.id, (0, 0))[1],
        )
        for user in users
    ]


def _account(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> User:
    user = db.get(User, user_id, with_for_update=lock)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found")
    return user


def _active_admin_count(db: Session, excluding: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == AccountRole.ADMINISTRATOR,
                User.is_active.is_(True),
                User.id != excluding,
            )
        )
        or 0
    )


def _orphaned_cases(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    case_ids = list(
        db.scalars(
            select(CaseMember.case_id).where(
                CaseMember.user_id == user_id, CaseMember.role == CaseRole.ANALYST
            )
        )
    )
    return [
        case_id
        for case_id in case_ids
        if members.active_analyst_count(db, case_id, excluding=None) == 0
    ]


# -- accounts ----------------------------------------------------------------------------------


@router.get("/accounts")
def list_accounts(
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    q: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[AccountOut]:
    conditions = []
    if q:
        pattern = (
            "%"
            + normalize_username(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        conditions.append(User.username_normalized.like(pattern, escape="\\"))
    total = db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0
    users = list(
        db.scalars(
            select(User)
            .where(*conditions)
            .order_by(func.lower(User.username), User.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(items=_accounts_out(db, users), total=total, limit=limit, offset=offset)


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(db: DbDep, actor: ActorDep, body: AccountCreate) -> AccountOut:
    try:
        user = accounts.create_account(
            db, username=body.username, password=body.password, role=body.role
        )
    except accounts.UsernameTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="username_taken") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    record(
        db,
        actor,
        "account.created",
        target_type="user",
        target_id=user.id,
        details={"username": user.username, "role": str(body.role)},
    )
    db.commit()
    return _accounts_out(db, [user])[0]


@router.patch("/accounts/{user_id}")
def update_account(
    db: DbDep, actor: ActorDep, principal: PrincipalDep, user_id: uuid.UUID, body: AccountUpdate
) -> AccountChangeOut:
    # Serialize administrator changes so two administrators cannot demote each other at once.
    db.execute(select(User.id).where(User.role == AccountRole.ADMINISTRATOR).with_for_update())
    user = _account(db, user_id, lock=True)
    removes_admin = user.role == AccountRole.ADMINISTRATOR and (
        (body.role is not None and body.role != AccountRole.ADMINISTRATOR)
        or body.is_active is False
    )
    if removes_admin and _active_admin_count(db, excluding=user.id) == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "last_administrator",
                "message": "Keep at least one active administrator; promote another account first.",
            },
        )
    changes: dict[str, object] = {}
    if body.role is not None and body.role != user.role:
        changes["previous_role"] = user.role
        changes["role"] = str(body.role)
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = body.is_active
        user.is_active = body.is_active
        if not body.is_active:
            accounts.revoke_all_sessions(db, user.id)
    if changes:
        record(
            db,
            actor,
            "account.updated",
            target_type="user",
            target_id=user.id,
            details={"username": user.username, **changes, "self": user.id == principal.user.id},
        )
    db.flush()
    orphaned = _orphaned_cases(db, user.id)
    db.commit()
    return AccountChangeOut(
        account=_accounts_out(db, [user])[0], cases_without_active_analyst=orphaned
    )


@router.post("/accounts/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_account_password(
    db: DbDep, actor: ActorDep, user_id: uuid.UUID, body: PasswordSet
) -> None:
    user = _account(db, user_id, lock=True)
    try:
        accounts.set_password(db, user, body.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    record(
        db,
        actor,
        "account.password_reset",
        target_type="user",
        target_id=user.id,
        details={"username": user.username, "sessions_revoked": True},
    )
    db.commit()


# -- case access -------------------------------------------------------------------------------


@case_access_router.get("")
def case_directory(
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    without_active_analyst: bool = False,
) -> Page[DirectoryCase]:
    analysts = (
        select(CaseMember.case_id, func.count().label("analysts"))
        .join(User, User.id == CaseMember.user_id)
        .where(
            CaseMember.role == CaseRole.ANALYST,
            User.is_active.is_(True),
            User.role != AccountRole.VIEWER,
        )
        .group_by(CaseMember.case_id)
        .subquery()
    )
    member_counts = (
        select(CaseMember.case_id, func.count().label("members"))
        .group_by(CaseMember.case_id)
        .subquery()
    )
    base = (
        select(
            Case.id,
            Case.title,
            Case.status,
            Case.created_at,
            func.coalesce(member_counts.c.members, 0),
            func.coalesce(analysts.c.analysts, 0),
        )
        .outerjoin(member_counts, member_counts.c.case_id == Case.id)
        .outerjoin(analysts, analysts.c.case_id == Case.id)
        .where(Case.status.in_([CaseStatus.ACTIVE, CaseStatus.ARCHIVED]))
    )
    if without_active_analyst:
        base = base.where(or_(analysts.c.analysts.is_(None), analysts.c.analysts == 0))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(base.order_by(Case.created_at.desc(), Case.id).limit(limit).offset(offset))
    return Page(
        items=[
            DirectoryCase(
                id=row[0],
                title=row[1],
                status=row[2],
                created_at=row[3],
                member_count=int(row[4]),
                active_analyst_count=int(row[5]),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def _directory_case(db: Session, case_id: uuid.UUID) -> Case:
    case = db.get(Case, case_id)
    if case is None or case.status not in (CaseStatus.ACTIVE, CaseStatus.ARCHIVED):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="case_not_found")
    return case


@case_access_router.get("/{case_id}/members")
def directory_members(db: DbDep, case_id: uuid.UUID) -> list[members.MemberOut]:
    return members.members_out(db, _directory_case(db, case_id).id)


@case_access_router.post("/{case_id}/members", status_code=status.HTTP_201_CREATED)
def directory_add_member(
    db: DbDep, actor: ActorDep, case_id: uuid.UUID, body: members.MemberAdd
) -> members.MemberOut:
    case = _directory_case(db, case_id)
    member = members.add_member(db, actor, case.id, body.username, body.role)
    db.commit()
    return member


@case_access_router.patch("/{case_id}/members/{user_id}")
def directory_update_member(
    db: DbDep, actor: ActorDep, case_id: uuid.UUID, user_id: uuid.UUID, body: members.MemberUpdate
) -> members.MemberOut:
    case = _directory_case(db, case_id)
    member = members.change_member_role(db, actor, case.id, user_id, body.role)
    db.commit()
    return member


@case_access_router.delete("/{case_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def directory_remove_member(
    db: DbDep, actor: ActorDep, case_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    case = _directory_case(db, case_id)
    members.remove_member(db, actor, case.id, user_id)
    db.commit()


# -- audit -------------------------------------------------------------------------------------


@audit_router.get("")
def system_audit(
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    case_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
    outcome: Annotated[str | None, Query(pattern="^(succeeded|denied|failed)$")] = None,
) -> Page[AuditEventOut]:
    conditions = []
    if case_id is not None:
        conditions.append(AuditEvent.case_id == case_id)
    if actor_user_id is not None:
        conditions.append(AuditEvent.actor_user_id == actor_user_id)
    if action:
        conditions.append(AuditEvent.action.startswith(action))
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    return audit_page(db, conditions, limit=limit, offset=offset)


# -- account directory (for adding members) ----------------------------------------------------


@directory_router.get(
    "", dependencies=[require_system_permission(SystemPermission.SEARCH_ACCOUNTS)]
)
def search_accounts(
    db: DbDep,
    q: Annotated[str, Query(min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[DirectoryAccount]:
    """Active accounts whose username contains ``q``: enough to add a member, nothing more."""
    pattern = (
        "%"
        + normalize_username(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )
    users = db.scalars(
        select(User)
        .where(User.is_active.is_(True), User.username_normalized.like(pattern, escape="\\"))
        .order_by(func.lower(User.username))
        .limit(limit)
    )
    return [DirectoryAccount(id=user.id, username=user.username, role=user.role) for user in users]
