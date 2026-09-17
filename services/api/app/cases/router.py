from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select

from app.audit.service import record
from app.auth.permissions import (
    SystemPermission,
    case_permissions,
    effective_case_role,
)
from app.cases.access import (
    AnalystCase,
    CaseAccess,
    CaseAccessDep,
    ReadableCase,
    WritableCase,
    deny_case_role,
    load_member_access,
)
from app.cases.models import (
    Case,
    CaseDeletion,
    CaseMember,
    CaseRole,
    CaseStatus,
    DeletionStatus,
    Note,
)
from app.cases.schemas import (
    CaseCounts,
    CaseCreate,
    CaseDeletionOut,
    CaseDetail,
    CaseOut,
    CaseUpdate,
    DeletionRequest,
    NoteCreate,
    NoteOut,
    NoteUpdate,
)
from app.db.base import utcnow
from app.deps import ActorDep, DbDep, PrincipalDep, require_system_permission
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.entities.models import Entity, Relationship
from app.entities.service import get_case_evidence_or_404, get_entity, get_relationship
from app.evidence.models import EvidenceObject
from app.monitoring.service import mark_case_monitors_paused
from app.queries.models import QueryRun, RunStatus, SavedQuery
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])

CaseSort = Literal[
    "updated_desc", "updated_asc", "created_desc", "created_asc", "title_asc", "title_desc"
]

_CASE_ORDER: dict[str, tuple[Any, ...]] = {
    "updated_desc": (Case.updated_at.desc(),),
    "updated_asc": (Case.updated_at.asc(),),
    "created_desc": (Case.created_at.desc(),),
    "created_asc": (Case.created_at.asc(),),
    "title_asc": (func.lower(Case.title).asc(),),
    "title_desc": (func.lower(Case.title).desc(),),
}
deletions_router = APIRouter(prefix="/api/v1/case-deletions", tags=["cases"])


def _count(db: DbDep, model: Any, case_id: uuid.UUID, *extra: Any) -> int:
    return int(
        db.scalar(select(func.count()).select_from(model).where(model.case_id == case_id, *extra))
        or 0
    )


def _detail(db: DbDep, case: Case, role: CaseRole) -> CaseDetail:
    return CaseDetail(
        **CaseOut.model_validate(case).model_dump(exclude={"my_role"}),
        my_role=str(role),
        permissions=sorted(str(permission) for permission in case_permissions(role)),
        counts=CaseCounts(
            entities=_count(db, Entity, case.id),
            relationships=_count(db, Relationship, case.id),
            evidence=_count(db, EvidenceObject, case.id),
            notes=_count(db, Note, case.id),
            saved_queries=_count(db, SavedQuery, case.id),
            query_runs=_count(db, QueryRun, case.id),
            active_runs=_count(
                db, QueryRun, case.id, QueryRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING])
            ),
        ),
    )


@router.get("")
def list_cases(
    db: DbDep,
    principal: PrincipalDep,
    limit: LimitParam = 25,
    offset: OffsetParam = 0,
    status_filter: Annotated[CaseStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=64)] = None,
    sort: CaseSort = "updated_desc",
) -> Page[CaseOut]:
    conditions = [CaseMember.user_id == principal.user.id]
    if status_filter is not None:
        conditions.append(Case.status == status_filter)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(
            or_(Case.title.ilike(pattern, escape="\\"), Case.purpose.ilike(pattern, escape="\\"))
        )
    if tag:
        conditions.append(Case.tags.contains([tag]))
    base = (
        select(Case, CaseMember.role)
        .join(CaseMember, CaseMember.case_id == Case.id)
        .where(*conditions)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(base.order_by(*_CASE_ORDER[sort], Case.id).limit(limit).offset(offset))
    return Page(
        items=[
            CaseOut.model_validate(row).model_copy(
                update={"my_role": str(effective_case_role(principal.user.role, membership))}
            )
            for row, membership in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_system_permission(SystemPermission.CREATE_CASE)],
)
def create_case(
    db: DbDep, principal: PrincipalDep, actor: ActorDep, body: CaseCreate
) -> CaseDetail:
    case = Case(
        title=body.title,
        purpose=body.purpose,
        scope=body.scope,
        tags=body.tags,
        status=CaseStatus.ACTIVE,
        created_by_user_id=principal.user.id,
    )
    db.add(case)
    db.flush()
    db.add(
        CaseMember(
            case_id=case.id,
            user_id=principal.user.id,
            role=CaseRole.ANALYST,
            added_by_user_id=principal.user.id,
        )
    )
    record(db, actor, "case.created", case_id=case.id, target_type="case", target_id=case.id)
    db.commit()
    return _detail(db, case, CaseRole.ANALYST)


@router.get("/{case_id}")
def get_case(access: CaseAccessDep, db: DbDep) -> CaseDetail:
    return _detail(db, access.case, access.role)


@router.patch("/{case_id}")
def update_case(
    case: WritableCase, access: CaseAccessDep, db: DbDep, actor: ActorDep, body: CaseUpdate
) -> CaseDetail:
    changed = []
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(case, field, value)
            changed.append(field)
    if changed:
        record(
            db,
            actor,
            "case.updated",
            case_id=case.id,
            target_type="case",
            target_id=case.id,
            details={"fields": changed},
        )
    db.commit()
    return _detail(db, case, access.role)


@router.post("/{case_id}/archive")
def archive_case(
    case: AnalystCase, access: CaseAccessDep, db: DbDep, actor: ActorDep
) -> CaseDetail:
    if case.status != CaseStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_not_active")
    active_runs = _count(
        db, QueryRun, case.id, QueryRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING])
    )
    if active_runs:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_has_active_runs")
    case.status = CaseStatus.ARCHIVED
    case.archived_at = utcnow()
    record(db, actor, "case.archived", case_id=case.id, target_type="case", target_id=case.id)
    # An archived case is read-only: its monitors stop scheduling until it is restored.
    mark_case_monitors_paused(db, actor, case.id, "case_archived")
    db.commit()
    return _detail(db, case, access.role)


@router.post("/{case_id}/restore")
def restore_case(
    case: AnalystCase, access: CaseAccessDep, db: DbDep, actor: ActorDep
) -> CaseDetail:
    if case.status != CaseStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_not_archived")
    case.status = CaseStatus.ACTIVE
    case.archived_at = None
    record(db, actor, "case.restored", case_id=case.id, target_type="case", target_id=case.id)
    db.commit()
    return _detail(db, case, access.role)


# -- notes ------------------------------------------------------------------------------------


@router.get("/{case_id}/notes")
def list_notes(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    relationship_id: Annotated[uuid.UUID | None, Query()] = None,
    evidence_id: Annotated[uuid.UUID | None, Query()] = None,
    case_level: Annotated[bool, Query()] = False,
) -> Page[NoteOut]:
    conditions = [Note.case_id == case.id]
    if entity_id is not None:
        conditions.append(Note.entity_id == entity_id)
    if relationship_id is not None:
        conditions.append(Note.relationship_id == relationship_id)
    if evidence_id is not None:
        conditions.append(Note.evidence_id == evidence_id)
    if case_level:
        conditions.extend(
            [Note.entity_id.is_(None), Note.relationship_id.is_(None), Note.evidence_id.is_(None)]
        )
    total = db.scalar(select(func.count()).select_from(Note).where(*conditions)) or 0
    rows = db.scalars(
        select(Note).where(*conditions).order_by(Note.created_at.desc()).limit(limit).offset(offset)
    )
    return Page(
        items=[NoteOut.model_validate(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("/{case_id}/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    case: WritableCase, db: DbDep, principal: PrincipalDep, body: NoteCreate
) -> NoteOut:
    subjects = [body.entity_id, body.relationship_id, body.evidence_id]
    if sum(subject is not None for subject in subjects) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="a note can have at most one subject"
        )
    if body.entity_id:
        get_entity(db, case.id, body.entity_id)
    if body.relationship_id:
        get_relationship(db, case.id, body.relationship_id)
    if body.evidence_id:
        get_case_evidence_or_404(db, case.id, body.evidence_id)
    note = Note(
        case_id=case.id,
        entity_id=body.entity_id,
        relationship_id=body.relationship_id,
        evidence_id=body.evidence_id,
        body=body.body,
        created_by_user_id=principal.user.id,
    )
    db.add(note)
    db.commit()
    return NoteOut.model_validate(note)


def _get_note(db: DbDep, case_id: uuid.UUID, note_id: uuid.UUID) -> Note:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.case_id == case_id))
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="note_not_found")
    return note


@router.patch("/{case_id}/notes/{note_id}")
def update_note(case: WritableCase, db: DbDep, note_id: uuid.UUID, body: NoteUpdate) -> NoteOut:
    note = _get_note(db, case.id, note_id)
    note.body = body.body
    db.commit()
    return NoteOut.model_validate(note)


@router.delete("/{case_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(case: WritableCase, db: DbDep, note_id: uuid.UUID) -> None:
    db.delete(_get_note(db, case.id, note_id))
    db.commit()


# -- deletion ---------------------------------------------------------------------------------


@router.post("/{case_id}/deletion", status_code=status.HTTP_202_ACCEPTED)
def request_deletion(
    request: Request,
    case_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
    actor: ActorDep,
    body: DeletionRequest,
) -> CaseDeletionOut:
    access: CaseAccess = load_member_access(db, principal, case_id)
    case = access.case
    if access.role != CaseRole.ANALYST:
        raise deny_case_role(request, actor, access)
    if case.status not in (CaseStatus.ACTIVE, CaseStatus.ARCHIVED):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_deletion_in_progress")
    if body.confirm_title.strip() != case.title:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "confirmation_mismatch",
                "message": "Type the exact case title to confirm.",
            },
        )
    locked = db.scalar(select(Case).where(Case.id == case.id).with_for_update())
    assert locked is not None
    locked.status = CaseStatus.DELETING
    job = CaseDeletion(
        case_id=case.id, requested_by_user_id=principal.user.id, status=DeletionStatus.QUEUED
    )
    db.add(job)
    db.flush()
    outbox = dispatch.enqueue(
        db,
        task_name=dispatch.EXECUTE_CASE_DELETION_TASK,
        aggregate_type=AggregateType.CASE_DELETION,
        aggregate_id=job.id,
        case_id=None,
    )
    record(
        db,
        actor,
        "case.deletion_requested",
        case_id=case.id,
        target_type="case_deletion",
        target_id=job.id,
    )
    db.commit()
    state = request.app.state
    dispatch.publish_after_commit(state.session_factory, state.celery, state.settings, outbox.id)
    db.refresh(job)
    return CaseDeletionOut.model_validate(job)


def _get_deletion(db: DbDep, principal: PrincipalDep, deletion_id: uuid.UUID) -> CaseDeletion:
    job = db.scalar(
        select(CaseDeletion).where(
            CaseDeletion.id == deletion_id, CaseDeletion.requested_by_user_id == principal.user.id
        )
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="deletion_not_found")
    return job


@deletions_router.get("")
def list_deletions(
    db: DbDep, principal: PrincipalDep, limit: LimitParam = 20, offset: OffsetParam = 0
) -> Page[CaseDeletionOut]:
    condition = CaseDeletion.requested_by_user_id == principal.user.id
    total = db.scalar(select(func.count()).select_from(CaseDeletion).where(condition)) or 0
    rows = db.scalars(
        select(CaseDeletion)
        .where(condition)
        .order_by(CaseDeletion.requested_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[CaseDeletionOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@deletions_router.get("/{deletion_id}")
def get_deletion(db: DbDep, principal: PrincipalDep, deletion_id: uuid.UUID) -> CaseDeletionOut:
    return CaseDeletionOut.model_validate(_get_deletion(db, principal, deletion_id))


@deletions_router.post("/{deletion_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_deletion(
    request: Request, db: DbDep, principal: PrincipalDep, actor: ActorDep, deletion_id: uuid.UUID
) -> CaseDeletionOut:
    job = _get_deletion(db, principal, deletion_id)
    locked = db.scalar(select(CaseDeletion).where(CaseDeletion.id == job.id).with_for_update())
    assert locked is not None
    if locked.status != DeletionStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="deletion_not_failed")
    locked.status = DeletionStatus.QUEUED
    locked.error_code = None
    locked.progress_note = "Retry requested"
    db.execute(select(Case).where(Case.id == locked.case_id).with_for_update())
    case = db.get(Case, locked.case_id)
    if case is not None:
        case.status = CaseStatus.DELETING
    outbox = dispatch.enqueue(
        db,
        task_name=dispatch.EXECUTE_CASE_DELETION_TASK,
        aggregate_type=AggregateType.CASE_DELETION,
        aggregate_id=locked.id,
        case_id=None,
    )
    record(
        db,
        actor,
        "case.deletion_retried",
        case_id=locked.case_id,
        target_type="case_deletion",
        target_id=locked.id,
    )
    db.commit()
    state = request.app.state
    dispatch.publish_after_commit(state.session_factory, state.celery, state.settings, outbox.id)
    db.refresh(locked)
    return CaseDeletionOut.model_validate(locked)
