from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.auth.models import User
from app.entities import normalize
from app.entities.models import (
    AnalystDecision,
    Entity,
    EntityEvidence,
    EntityIdentifier,
    IdentifierType,
    Observation,
    Origin,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
)
from app.entities.schemas import (
    AnalystDecisionOut,
    EntityCreate,
    EntityDetail,
    EntityEvidenceOut,
    EntitySummary,
    IdentifierIn,
    IdentifierOut,
    RelationshipCreate,
    RelationshipDetail,
    RelationshipEntityRef,
    RelationshipOut,
    RelationshipReferenceIn,
    RelationshipReferenceOut,
    SharedIdentifier,
)
from app.evidence.models import EvidenceObject


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=detail)


def _escape_like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# -- entities ----------------------------------------------------------------------------------


def get_entity(db: Session, case_id: uuid.UUID, entity_id: uuid.UUID) -> Entity:
    entity = db.scalar(select(Entity).where(Entity.id == entity_id, Entity.case_id == case_id))
    if entity is None:
        raise _not_found("entity_not_found")
    return entity


def build_identifier(
    case_id: uuid.UUID, entity_id: uuid.UUID, identifier: IdentifierIn
) -> EntityIdentifier:
    try:
        normalized = normalize.normalize_identifier(identifier.identifier_type, identifier.value)
        platform = normalize.normalize_platform(identifier.platform)
    except normalize.IdentifierError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_identifier", "message": str(exc)},
        ) from None
    return EntityIdentifier(
        case_id=case_id,
        entity_id=entity_id,
        identifier_type=identifier.identifier_type,
        platform=platform,
        original_value=identifier.value.strip(),
        normalized_value=normalized,
    )


def _raise_identifier_conflict(db: Session, identifiers: Sequence[EntityIdentifier]) -> None:
    for identifier in identifiers:
        if identifier.identifier_type != IdentifierType.PLATFORM_ID:
            continue
        existing = db.scalar(
            select(EntityIdentifier.entity_id).where(
                EntityIdentifier.case_id == identifier.case_id,
                EntityIdentifier.identifier_type == IdentifierType.PLATFORM_ID,
                EntityIdentifier.platform == identifier.platform,
                EntityIdentifier.normalized_value == identifier.normalized_value,
                EntityIdentifier.entity_id != identifier.entity_id,
            )
        )
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "platform_id_already_assigned",
                    "message": "This stable platform ID already identifies another entity.",
                    "entity_id": str(existing),
                },
            )
    raise HTTPException(status.HTTP_409_CONFLICT, detail="identifier_already_present")


def create_entity(db: Session, case_id: uuid.UUID, user: User, body: EntityCreate) -> Entity:
    entity = Entity(
        id=uuid.uuid4(),
        case_id=case_id,
        entity_type=body.entity_type,
        display_name=body.display_name,
        description=body.description,
        attributes=body.attributes,
        origin=Origin.ANALYST_ASSERTION,
        created_by_user_id=user.id,
    )
    identifiers = [build_identifier(case_id, entity.id, item) for item in body.identifiers]
    db.add(entity)
    db.flush()
    db.add_all(identifiers)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        _raise_identifier_conflict(db, identifiers)
    return entity


def add_identifier(db: Session, entity: Entity, identifier: IdentifierIn) -> EntityIdentifier:
    row = build_identifier(entity.case_id, entity.id, identifier)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        _raise_identifier_conflict(db, [row])
    return row


def identifiers_for(
    db: Session, entity_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[IdentifierOut]]:
    ids = list(entity_ids)
    result: dict[uuid.UUID, list[IdentifierOut]] = {entity_id: [] for entity_id in ids}
    if not ids:
        return result
    rows = db.scalars(
        select(EntityIdentifier)
        .where(EntityIdentifier.entity_id.in_(ids))
        .order_by(EntityIdentifier.identifier_type, EntityIdentifier.normalized_value)
    )
    for row in rows:
        result[row.entity_id].append(IdentifierOut.model_validate(row))
    return result


def summarize(db: Session, entities: Sequence[Entity]) -> list[EntitySummary]:
    identifiers = identifiers_for(db, (entity.id for entity in entities))
    return [
        EntitySummary(
            id=entity.id,
            case_id=entity.case_id,
            entity_type=entity.entity_type,
            display_name=entity.display_name,
            description=entity.description,
            attributes=entity.attributes,
            origin=entity.origin,
            created_by_query_run_id=entity.created_by_query_run_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            identifiers=identifiers[entity.id],
        )
        for entity in entities
    ]


def list_entities(
    db: Session,
    case_id: uuid.UUID,
    *,
    entity_type: str | None,
    origin: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[EntitySummary], int]:
    conditions = [Entity.case_id == case_id]
    if entity_type:
        conditions.append(Entity.entity_type == entity_type)
    if origin:
        conditions.append(Entity.origin == origin)
    if q:
        pattern = _escape_like(q.strip())
        conditions.append(
            or_(
                Entity.display_name.ilike(pattern, escape="\\"),
                exists().where(
                    EntityIdentifier.entity_id == Entity.id,
                    or_(
                        EntityIdentifier.original_value.ilike(pattern, escape="\\"),
                        EntityIdentifier.normalized_value.ilike(pattern, escape="\\"),
                    ),
                ),
            )
        )
    total = db.scalar(select(func.count()).select_from(Entity).where(*conditions)) or 0
    entities = list(
        db.scalars(
            select(Entity)
            .where(*conditions)
            .order_by(Entity.display_name, Entity.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return summarize(db, entities), total


def delete_entity(db: Session, entity: Entity) -> None:
    if entity.origin != Origin.ANALYST_ASSERTION:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "entity_not_deletable",
                "message": "Only analyst-created entities can be deleted; observed entities keep "
                "their provenance until the case is deleted.",
            },
        )
    db.delete(entity)


def entity_detail(db: Session, entity: Entity) -> EntityDetail:
    evidence_rows = db.execute(
        select(EntityEvidence, EvidenceObject)
        .join(EvidenceObject, EvidenceObject.id == EntityEvidence.evidence_id)
        .where(EntityEvidence.entity_id == entity.id)
        .order_by(EntityEvidence.created_at)
        .limit(200)
    ).all()
    relationships = list(
        db.scalars(
            select(Relationship)
            .where(
                Relationship.case_id == entity.case_id,
                or_(
                    Relationship.source_entity_id == entity.id,
                    Relationship.target_entity_id == entity.id,
                ),
            )
            .order_by(Relationship.created_at)
            .limit(200)
        )
    )
    own = aliased(EntityIdentifier)
    other = aliased(EntityIdentifier)
    shared_rows = db.execute(
        select(other.identifier_type, other.normalized_value, Entity)
        .select_from(own)
        .join(
            other,
            (other.case_id == own.case_id)
            & (other.identifier_type == own.identifier_type)
            & (other.normalized_value == own.normalized_value)
            & (other.entity_id != own.entity_id),
        )
        .join(Entity, Entity.id == other.entity_id)
        .where(own.entity_id == entity.id)
        .limit(50)
    ).all()
    observation_count = db.scalar(
        select(func.count()).select_from(Observation).where(Observation.entity_id == entity.id)
    )
    return EntityDetail(
        entity=summarize(db, [entity])[0],
        linked_evidence=[
            EntityEvidenceOut(
                link_id=link.id,
                evidence_id=evidence.id,
                title=evidence.title,
                acquisition_method=evidence.acquisition_method,
                sha256=evidence.sha256,
                note=link.note,
                created_at=link.created_at,
            )
            for link, evidence in evidence_rows
        ],
        relationships=relationships_out(db, relationships),
        shared_identifiers=[
            SharedIdentifier(
                identifier_type=identifier_type,
                normalized_value=value,
                entity_id=other_entity.id,
                display_name=other_entity.display_name,
                entity_type=other_entity.entity_type,
            )
            for identifier_type, value, other_entity in shared_rows
        ],
        observation_count=observation_count or 0,
    )


def get_case_evidence_or_404(
    db: Session, case_id: uuid.UUID, evidence_id: uuid.UUID
) -> EvidenceObject:
    evidence = db.scalar(
        select(EvidenceObject).where(
            EvidenceObject.id == evidence_id, EvidenceObject.case_id == case_id
        )
    )
    if evidence is None:
        raise _not_found("evidence_not_found")
    return evidence


def link_evidence(
    db: Session, entity: Entity, user: User, evidence_id: uuid.UUID, note: str | None
) -> EntityEvidence:
    get_case_evidence_or_404(db, entity.case_id, evidence_id)
    link = EntityEvidence(
        case_id=entity.case_id,
        entity_id=entity.id,
        evidence_id=evidence_id,
        note=note,
        created_by_user_id=user.id,
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="evidence_already_linked") from None
    return link


# -- relationships -----------------------------------------------------------------------------


def get_relationship(db: Session, case_id: uuid.UUID, relationship_id: uuid.UUID) -> Relationship:
    relationship = db.scalar(
        select(Relationship).where(
            Relationship.id == relationship_id, Relationship.case_id == case_id
        )
    )
    if relationship is None:
        raise _not_found("relationship_not_found")
    return relationship


def relationships_out(db: Session, relationships: Sequence[Relationship]) -> list[RelationshipOut]:
    if not relationships:
        return []
    entity_ids = {r.source_entity_id for r in relationships} | {
        r.target_entity_id for r in relationships
    }
    entities = {
        entity.id: entity for entity in db.scalars(select(Entity).where(Entity.id.in_(entity_ids)))
    }
    counts: dict[uuid.UUID, int] = {
        relationship_id: count
        for relationship_id, count in db.execute(
            select(RelationshipEvidence.relationship_id, func.count())
            .where(RelationshipEvidence.relationship_id.in_([r.id for r in relationships]))
            .group_by(RelationshipEvidence.relationship_id)
        )
    }

    def ref(entity_id: uuid.UUID) -> RelationshipEntityRef:
        entity = entities[entity_id]
        return RelationshipEntityRef(
            id=entity.id, display_name=entity.display_name, entity_type=entity.entity_type
        )

    return [
        RelationshipOut(
            id=r.id,
            case_id=r.case_id,
            source=ref(r.source_entity_id),
            target=ref(r.target_entity_id),
            predicate=r.predicate,
            origin=r.origin,
            review_status=r.review_status,
            description=r.description,
            valid_from=r.valid_from,
            valid_to=r.valid_to,
            created_by_query_run_id=r.created_by_query_run_id,
            reference_count=int(counts.get(r.id, 0)),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in relationships
    ]


def create_relationship(
    db: Session, case_id: uuid.UUID, user: User, body: RelationshipCreate
) -> Relationship:
    get_entity(db, case_id, body.source_entity_id)
    get_entity(db, case_id, body.target_entity_id)
    for evidence_id in body.supporting_evidence_ids:
        get_case_evidence_or_404(db, case_id, evidence_id)
    relationship = Relationship(
        id=uuid.uuid4(),
        case_id=case_id,
        source_entity_id=body.source_entity_id,
        target_entity_id=body.target_entity_id,
        predicate=body.predicate,
        origin=Origin.ANALYST_ASSERTION,
        review_status=ReviewStatus.UNREVIEWED,
        description=body.description,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        created_by_user_id=user.id,
    )
    db.add(relationship)
    db.flush()
    for evidence_id in dict.fromkeys(body.supporting_evidence_ids):
        db.add(
            RelationshipEvidence(
                case_id=case_id,
                relationship_id=relationship.id,
                evidence_id=evidence_id,
                created_by_user_id=user.id,
            )
        )
    db.flush()
    return relationship


def list_relationships(
    db: Session,
    case_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None,
    origin: str | None,
    review_status: str | None,
    predicate: str | None,
    limit: int,
    offset: int,
) -> tuple[list[RelationshipOut], int]:
    conditions = [Relationship.case_id == case_id]
    if entity_id is not None:
        conditions.append(
            or_(
                Relationship.source_entity_id == entity_id,
                Relationship.target_entity_id == entity_id,
            )
        )
    if origin:
        conditions.append(Relationship.origin == origin)
    if review_status:
        conditions.append(Relationship.review_status == review_status)
    if predicate:
        conditions.append(Relationship.predicate == predicate)
    total = db.scalar(select(func.count()).select_from(Relationship).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Relationship)
            .where(*conditions)
            .order_by(Relationship.created_at.desc(), Relationship.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return relationships_out(db, rows), total


def relationship_detail(db: Session, relationship: Relationship) -> RelationshipDetail:
    base = relationships_out(db, [relationship])[0]
    rows = db.execute(
        select(RelationshipEvidence, EvidenceObject, Observation)
        .outerjoin(EvidenceObject, EvidenceObject.id == RelationshipEvidence.evidence_id)
        .outerjoin(Observation, Observation.id == RelationshipEvidence.observation_id)
        .where(RelationshipEvidence.relationship_id == relationship.id)
        .order_by(RelationshipEvidence.created_at)
        .limit(500)
    ).all()
    references = []
    for reference, evidence, observation in rows:
        linked_evidence = evidence
        if linked_evidence is None and observation is not None and observation.evidence_id:
            linked_evidence = db.get(EvidenceObject, observation.evidence_id)
        references.append(
            RelationshipReferenceOut(
                id=reference.id,
                stance=reference.stance,
                note=reference.note,
                evidence_id=linked_evidence.id if linked_evidence else None,
                evidence_title=linked_evidence.title if linked_evidence else None,
                evidence_acquisition_method=(
                    linked_evidence.acquisition_method if linked_evidence else None
                ),
                evidence_sha256=linked_evidence.sha256 if linked_evidence else None,
                observation_id=observation.id if observation else None,
                observation_type=observation.observation_type if observation else None,
                observation_collected_at=observation.collected_at if observation else None,
                query_run_id=observation.query_run_id if observation else None,
                created_at=reference.created_at,
            )
        )
    decisions = db.scalars(
        select(AnalystDecision)
        .where(AnalystDecision.relationship_id == relationship.id)
        .order_by(AnalystDecision.decided_at)
    )
    return RelationshipDetail(
        **base.model_dump(),
        references=references,
        decisions=[AnalystDecisionOut.model_validate(d) for d in decisions],
    )


def record_review(
    db: Session,
    relationship: Relationship,
    user: User,
    new_status: ReviewStatus,
    rationale: str | None,
) -> None:
    if relationship.review_status == new_status:
        return
    db.add(
        AnalystDecision(
            case_id=relationship.case_id,
            relationship_id=relationship.id,
            previous_value=relationship.review_status,
            new_value=new_status,
            rationale=rationale,
            decided_by_user_id=user.id,
        )
    )
    relationship.review_status = new_status


def add_reference(
    db: Session, relationship: Relationship, user: User, body: RelationshipReferenceIn
) -> RelationshipEvidence:
    if body.evidence_id is not None:
        get_case_evidence_or_404(db, relationship.case_id, body.evidence_id)
    if body.observation_id is not None:
        observation = db.scalar(
            select(Observation).where(
                Observation.id == body.observation_id,
                Observation.case_id == relationship.case_id,
            )
        )
        if observation is None:
            raise _not_found("observation_not_found")
    reference = RelationshipEvidence(
        case_id=relationship.case_id,
        relationship_id=relationship.id,
        evidence_id=body.evidence_id,
        observation_id=body.observation_id,
        stance=body.stance,
        note=body.note,
        created_by_user_id=user.id,
    )
    db.add(reference)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="reference_already_present") from None
    return reference
