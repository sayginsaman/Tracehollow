"""Server-side case authorization.

Every case-scoped route resolves its case through these dependencies. Access requires a
``case_members`` row for the authenticated user; there is no administrator bypass. A case the
user cannot access is indistinguishable from a case that does not exist (404), so identifiers
cannot be probed. Child records must additionally be filtered by ``case_id`` in each query.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select

from app.cases.models import Case, CaseMember, CaseStatus
from app.deps import DbDep, PrincipalDep


def load_member_case(db: DbDep, principal: PrincipalDep, case_id: uuid.UUID) -> Case:
    case = db.scalar(
        select(Case)
        .join(CaseMember, CaseMember.case_id == Case.id)
        .where(Case.id == case_id, CaseMember.user_id == principal.user.id)
    )
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="case_not_found")
    return case


def require_case_readable(
    db: DbDep, principal: PrincipalDep, case_id: Annotated[uuid.UUID, Path()]
) -> Case:
    case = load_member_case(db, principal, case_id)
    if case.status in (CaseStatus.DELETING, CaseStatus.DELETION_FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_deletion_in_progress")
    return case


def require_case_writable(
    case: Annotated[Case, Depends(require_case_readable)],
) -> Case:
    if case.status == CaseStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_archived")
    return case


ReadableCase = Annotated[Case, Depends(require_case_readable)]
WritableCase = Annotated[Case, Depends(require_case_writable)]
