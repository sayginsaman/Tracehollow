"""Server-side case authorization.

Every case-scoped route resolves its case through these dependencies. Access requires a
``case_members`` row for the authenticated user; there is no administrator bypass. A case the
user cannot access is indistinguishable from a case that does not exist (404), so identifiers
cannot be probed. Child records must additionally be filtered by ``case_id`` in each query.

Members get the *effective* case role (membership capped by account role, see
``app.auth.permissions``). Reading needs any role; changes, collection, imports, AI requests and
bulk exports need the analyst role (403 for viewers, recorded in the audit trail).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.audit.service import record_denial
from app.auth.models import AccountRole, User
from app.auth.permissions import CasePermission, case_permissions, effective_case_role
from app.cases.models import Case, CaseMember, CaseRole, CaseStatus
from app.deps import ActorDep, DbDep, PrincipalDep


@dataclass(frozen=True, slots=True)
class CaseAccess:
    case: Case
    role: CaseRole
    membership_role: CaseRole

    @property
    def permissions(self) -> frozenset[CasePermission]:
        return case_permissions(self.role)


def load_member_access(db: DbDep, principal: PrincipalDep, case_id: uuid.UUID) -> CaseAccess:
    row = db.execute(
        select(Case, CaseMember.role)
        .join(CaseMember, CaseMember.case_id == Case.id)
        .where(Case.id == case_id, CaseMember.user_id == principal.user.id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="case_not_found")
    case, membership = row
    return CaseAccess(
        case=case,
        role=effective_case_role(principal.user.role, membership),
        membership_role=effective_case_role(AccountRole.ANALYST, membership),
    )


def load_member_case(db: DbDep, principal: PrincipalDep, case_id: uuid.UUID) -> Case:
    return load_member_access(db, principal, case_id).case


def require_case_access(
    db: DbDep, principal: PrincipalDep, case_id: Annotated[uuid.UUID, Path()]
) -> CaseAccess:
    access = load_member_access(db, principal, case_id)
    if access.case.status in (CaseStatus.DELETING, CaseStatus.DELETION_FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_deletion_in_progress")
    return access


CaseAccessDep = Annotated[CaseAccess, Depends(require_case_access)]


def require_case_readable(access: CaseAccessDep) -> Case:
    return access.case


def deny_case_role(request: Request, actor: ActorDep, access: CaseAccess) -> HTTPException:
    record_denial(
        request.app.state.session_factory,
        actor,
        "access.denied",
        case_id=access.case.id,
        details={
            "case_role": str(access.role),
            "method": request.method,
            "route": getattr(request.scope.get("route"), "path", None),
        },
    )
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail={
            "code": "insufficient_case_role",
            "message": "Your role in this case only allows reading it.",
        },
    )


def require_case_analyst(request: Request, actor: ActorDep, access: CaseAccessDep) -> Case:
    if access.role != CaseRole.ANALYST:
        raise deny_case_role(request, actor, access)
    return access.case


def require_case_writable(case: Annotated[Case, Depends(require_case_analyst)]) -> Case:
    if case.status == CaseStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_archived")
    return case


ReadableCase = Annotated[Case, Depends(require_case_readable)]
# Analyst role, any readable status (archive, restore, cancellations, exports, member management).
AnalystCase = Annotated[Case, Depends(require_case_analyst)]
# Analyst role on an active case.
WritableCase = Annotated[Case, Depends(require_case_writable)]


def has_analyst_access(db: Session, user_id: uuid.UUID | None, case_id: uuid.UUID) -> bool:
    """Whether ``user_id`` may still start or continue work in the case (background re-checks)."""
    if user_id is None:
        return False
    return bool(
        db.scalar(
            select(
                exists().where(
                    and_(
                        CaseMember.case_id == case_id,
                        CaseMember.user_id == user_id,
                        CaseMember.role == CaseRole.ANALYST,
                        User.id == CaseMember.user_id,
                        User.is_active.is_(True),
                        User.role != AccountRole.VIEWER,
                    )
                )
            )
        )
    )
