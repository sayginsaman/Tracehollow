from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.ai import indexing
from app.ai.models import AiCitation, DocumentChunk, EvidenceIndexState
from app.auth.models import User
from app.cases.models import Case, CaseStatus, Note
from app.config import Settings
from app.db.base import utcnow
from app.entities.models import (
    Entity,
    EntityEvidence,
    Observation,
    Relationship,
    RelationshipEvidence,
)
from app.entities.models import RelationshipEvidence as RelEvidence
from app.evidence import importing
from app.evidence.models import AcquisitionMethod, EvidenceKind, EvidenceObject
from app.evidence.schemas import (
    EvidenceDeletionOut,
    EvidenceDetail,
    EvidenceIndexOut,
    EvidenceOut,
    EvidencePreview,
    ImportResult,
    Integrity,
    LinkedEntity,
    LinkedRelationship,
)
from app.evidence.storage import EvidenceStorage, IntegrityError

logger = logging.getLogger(__name__)


def to_out(evidence: EvidenceObject) -> EvidenceOut:
    return EvidenceOut(
        id=evidence.id,
        case_id=evidence.case_id,
        kind=evidence.kind,
        title=evidence.title,
        original_filename=evidence.original_filename,
        content_type=evidence.content_type,
        size_bytes=evidence.size_bytes,
        sha256=evidence.sha256,
        acquisition_method=evidence.acquisition_method,
        import_origin=evidence.import_origin,
        source_reference=evidence.source_reference,
        source_published_at=evidence.source_published_at,
        source_published_at_original=evidence.source_published_at_original,
        collected_at=evidence.collected_at,
        created_at=evidence.created_at,
        connector_id=evidence.connector_id,
        connector_version=evidence.connector_version,
        query_run_id=evidence.query_run_id,
        connector_run_id=evidence.connector_run_id,
        page_index=evidence.page_index,
        description=evidence.description,
        synthetic=evidence.acquisition_method == AcquisitionMethod.SYNTHETIC_FIXTURE,
    )


def lock_case_for_write(db: Session, case_id: uuid.UUID) -> Case:
    """Hold a share lock on the case for the rest of the transaction.

    Deletion changes the case status under a row lock, so it waits for in-flight writers and
    later writers see the new status.
    """
    case = db.scalar(select(Case).where(Case.id == case_id).with_for_update(read=True))
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="case_not_found")
    if case.status == CaseStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_archived")
    if case.status != CaseStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="case_deletion_in_progress")
    return case


def find_duplicates(
    db: Session, case_id: uuid.UUID, sha256: str, exclude: uuid.UUID
) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(EvidenceObject.id)
            .where(
                EvidenceObject.case_id == case_id,
                EvidenceObject.sha256 == sha256,
                EvidenceObject.id != exclude,
            )
            .order_by(EvidenceObject.created_at)
            .limit(20)
        )
    )


def import_evidence(
    db: Session,
    storage: EvidenceStorage,
    settings: Settings,
    *,
    case_id: uuid.UUID,
    user: User,
    kind: EvidenceKind,
    content: bytes,
    filename: str | None,
    title: str | None,
    import_origin: str,
    source_reference: str | None,
    source_published_at: datetime | None,
    source_published_at_original: str | None,
    description: str,
) -> ImportResult:
    importing.validate_content(kind, content, max_json_depth=settings.evidence_json_max_depth)
    sanitized = importing.sanitize_filename(filename)
    evidence_id = uuid.uuid4()
    key = EvidenceStorage.key_for(case_id, evidence_id)

    case = lock_case_for_write(db, case_id)
    staged = storage.store(key, content)
    try:
        evidence = EvidenceObject(
            id=evidence_id,
            case_id=case_id,
            kind=kind,
            title=(title or sanitized.value or f"Imported {kind}")[:300],
            original_filename=sanitized.value,
            content_type=importing.CONTENT_TYPES[kind],
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
            storage_key=key,
            acquisition_method=AcquisitionMethod.AUTHORIZED_IMPORT,
            import_origin=import_origin,
            source_reference=source_reference,
            source_published_at=source_published_at,
            source_published_at_original=source_published_at_original,
            collected_at=utcnow(),
            imported_by_user_id=user.id,
            description=description,
        )
        db.add(evidence)
        db.flush()
        duplicates = find_duplicates(db, case_id, staged.sha256, evidence_id)
        indexing.mark_evidence_for_indexing(
            db, settings, case_id=case_id, evidence_id=evidence_id, ai_mode=case.ai_mode
        )
        db.commit()
    except BaseException:
        db.rollback()
        storage.remove_key(key)
        raise
    logger.info(
        "evidence_imported",
        extra={"case_ref": str(case_id)[:8], "kind": str(kind), "size_bytes": staged.size_bytes},
    )
    return ImportResult(
        evidence=to_out(evidence), filename_sanitized=sanitized.changed, duplicate_of=duplicates
    )


def get_case_evidence(db: Session, case_id: uuid.UUID, evidence_id: uuid.UUID) -> EvidenceObject:
    evidence = db.scalar(
        select(EvidenceObject).where(
            EvidenceObject.id == evidence_id, EvidenceObject.case_id == case_id
        )
    )
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evidence_not_found")
    return evidence


def check_integrity(storage: EvidenceStorage, evidence: EvidenceObject) -> Integrity:
    try:
        storage.read_verified(evidence.storage_key, evidence.sha256, evidence.size_bytes)
        result = "verified"
    except IntegrityError as exc:
        result = exc.code
    return Integrity(status=result, checked_at=utcnow())


def evidence_detail(
    db: Session, storage: EvidenceStorage, evidence: EvidenceObject
) -> EvidenceDetail:
    entity_rows = db.execute(
        select(EntityEvidence, Entity)
        .join(Entity, Entity.id == EntityEvidence.entity_id)
        .where(
            EntityEvidence.evidence_id == evidence.id, EntityEvidence.case_id == evidence.case_id
        )
        .order_by(Entity.display_name)
        .limit(200)
    ).all()
    relationship_rows = db.execute(
        select(RelEvidence, Relationship)
        .join(Relationship, Relationship.id == RelEvidence.relationship_id)
        .where(RelEvidence.evidence_id == evidence.id, RelEvidence.case_id == evidence.case_id)
        .limit(200)
    ).all()
    observation_count = db.scalar(
        select(func.count()).select_from(Observation).where(Observation.evidence_id == evidence.id)
    )
    return EvidenceDetail(
        evidence=to_out(evidence),
        integrity=check_integrity(storage, evidence),
        linked_entities=[
            LinkedEntity(
                link_id=link.id,
                entity_id=entity.id,
                display_name=entity.display_name,
                entity_type=entity.entity_type,
                note=link.note,
            )
            for link, entity in entity_rows
        ],
        linked_relationships=[
            LinkedRelationship(
                reference_id=ref.id,
                relationship_id=rel.id,
                predicate=rel.predicate,
                stance=ref.stance,
                source_entity_id=rel.source_entity_id,
                target_entity_id=rel.target_entity_id,
            )
            for ref, rel in relationship_rows
        ],
        observation_count=observation_count or 0,
        index=_index_state(db, evidence.id),
        duplicate_of=find_duplicates(db, evidence.case_id, evidence.sha256, evidence.id),
    )


def read_content(storage: EvidenceStorage, evidence: EvidenceObject) -> bytes:
    try:
        return storage.read_verified(evidence.storage_key, evidence.sha256, evidence.size_bytes)
    except IntegrityError as exc:
        logger.error("evidence_integrity_failure", extra={"code": exc.code})
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.code) from None


def build_preview(
    storage: EvidenceStorage, settings: Settings, evidence: EvidenceObject
) -> EvidencePreview:
    content = read_content(storage, evidence)
    limit = settings.evidence_preview_max_bytes
    truncated = len(content) > limit
    text = content[:limit].decode("utf-8", errors="replace" if not truncated else "ignore")
    pretty: str | None = None
    if evidence.kind == EvidenceKind.JSON and not truncated:
        try:
            pretty = json.dumps(json.loads(text.removeprefix("﻿")), indent=2, ensure_ascii=False)
        except ValueError:
            pretty = None
    return EvidencePreview(
        evidence_id=evidence.id,
        kind=evidence.kind,
        encoding="utf-8",
        text=text,
        pretty_json=pretty,
        truncated=truncated,
        preview_bytes=min(len(content), limit),
        size_bytes=len(content),
    )


def _index_state(db: Session, evidence_id: uuid.UUID) -> EvidenceIndexOut | None:
    profile = indexing.active_profile(db)
    row = db.execute(
        select(
            EvidenceIndexState,
            indexing.derived_status_expression(profile.id if profile else None),
        ).where(EvidenceIndexState.evidence_id == evidence_id)
    ).one_or_none()
    if row is None:
        return None
    state, derived = row
    return EvidenceIndexOut(
        status=str(derived),
        chunk_count=state.chunk_count,
        attempts=state.attempts,
        error_code=state.error_code,
        error_detail=state.error_detail,
        indexed_at=state.indexed_at,
    )


def delete_evidence(
    db: Session,
    storage: EvidenceStorage,
    *,
    case_id: uuid.UUID,
    evidence_id: uuid.UUID,
    confirm_title: str,
) -> EvidenceDeletionOut:
    """Deliberately delete one imported evidence record and everything derived from it.

    The stored original is removed before the database row: an interruption leaves a record
    whose file is missing (reported by integrity checks and removable by retrying), never an
    unreferenced copy of the content. Quoted text in earlier AI citations is purged; those
    citations then report that their source was deleted.
    """
    lock_case_for_write(db, case_id)
    evidence = db.scalar(
        select(EvidenceObject)
        .where(EvidenceObject.id == evidence_id, EvidenceObject.case_id == case_id)
        .with_for_update()
    )
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evidence_not_found")
    if evidence.acquisition_method != AcquisitionMethod.AUTHORIZED_IMPORT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "evidence_part_of_execution",
                "message": (
                    "Evidence collected by a query execution is kept with its execution history."
                ),
            },
        )
    if confirm_title.strip() != evidence.title:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "confirmation_mismatch",
                "message": "Type the exact evidence title to confirm.",
            },
        )

    def count(model: Any, *conditions: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)

    result = EvidenceDeletionOut(
        evidence_id=evidence.id,
        removed_chunks=count(DocumentChunk, DocumentChunk.evidence_id == evidence.id),
        removed_relationship_references=count(
            RelationshipEvidence, RelationshipEvidence.evidence_id == evidence.id
        ),
        removed_entity_links=count(EntityEvidence, EntityEvidence.evidence_id == evidence.id),
        removed_notes=count(Note, Note.evidence_id == evidence.id),
        affected_citations=count(AiCitation, AiCitation.evidence_id == evidence.id),
    )
    db.execute(
        update(AiCitation)
        .where(AiCitation.evidence_id == evidence.id, AiCitation.case_id == case_id)
        .values(quote=None, source_char_start=None, source_char_end=None, json_pointer=None)
    )
    storage.remove_key(evidence.storage_key)
    db.delete(evidence)
    db.commit()
    logger.info(
        "evidence_deleted", extra={"case_ref": str(case_id)[:8], "chunks": result.removed_chunks}
    )
    return result
