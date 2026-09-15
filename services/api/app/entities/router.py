from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.cases.access import ReadableCase, WritableCase
from app.deps import DbDep, PrincipalDep
from app.entities import graph, service
from app.entities.models import (
    EntityEvidence,
    EntityIdentifier,
    EntityType,
    IdentifierType,
    Observation,
    Origin,
    RelationshipEvidence,
    ReviewStatus,
)
from app.entities.schemas import (
    SUGGESTED_PREDICATES,
    EntityCreate,
    EntityDetail,
    EntitySummary,
    EntityUpdate,
    EvidenceLinkIn,
    GraphOut,
    IdentifierIn,
    IdentifierOut,
    ObservationOut,
    RelationshipCreate,
    RelationshipDetail,
    RelationshipOut,
    RelationshipReferenceIn,
    RelationshipUpdate,
    ReviewDecisionIn,
)
from app.schemas import LimitParam, OffsetParam, Page

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["entities"])


@router.get("/entity-vocabulary")
def vocabulary(case: ReadableCase) -> dict[str, list[str]]:
    return {
        "entity_types": [item.value for item in EntityType],
        "identifier_types": [item.value for item in IdentifierType],
        "suggested_predicates": list(SUGGESTED_PREDICATES),
        "origins": [item.value for item in Origin],
        "review_statuses": [item.value for item in ReviewStatus],
    }


@router.get("/entities")
def list_entities(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    entity_type: Annotated[EntityType | None, Query()] = None,
    origin: Annotated[Origin | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[EntitySummary]:
    items, total = service.list_entities(
        db, case.id, entity_type=entity_type, origin=origin, q=q, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/entities", status_code=status.HTTP_201_CREATED)
def create_entity(
    case: WritableCase, db: DbDep, principal: PrincipalDep, body: EntityCreate
) -> EntitySummary:
    entity = service.create_entity(db, case.id, principal.user, body)
    db.commit()
    return service.summarize(db, [entity])[0]


@router.get("/entities/{entity_id}")
def get_entity(case: ReadableCase, db: DbDep, entity_id: uuid.UUID) -> EntityDetail:
    return service.entity_detail(db, service.get_entity(db, case.id, entity_id))


@router.patch("/entities/{entity_id}")
def update_entity(
    case: WritableCase, db: DbDep, entity_id: uuid.UUID, body: EntityUpdate
) -> EntitySummary:
    entity = service.get_entity(db, case.id, entity_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(entity, field, value)
    db.commit()
    return service.summarize(db, [entity])[0]


@router.delete("/entities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entity(case: WritableCase, db: DbDep, entity_id: uuid.UUID) -> None:
    service.delete_entity(db, service.get_entity(db, case.id, entity_id))
    db.commit()


@router.post("/entities/{entity_id}/identifiers", status_code=status.HTTP_201_CREATED)
def add_identifier(
    case: WritableCase, db: DbDep, entity_id: uuid.UUID, body: IdentifierIn
) -> IdentifierOut:
    entity = service.get_entity(db, case.id, entity_id)
    identifier = service.add_identifier(db, entity, body)
    db.commit()
    return IdentifierOut.model_validate(identifier)


@router.delete(
    "/entities/{entity_id}/identifiers/{identifier_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_identifier(
    case: WritableCase, db: DbDep, entity_id: uuid.UUID, identifier_id: uuid.UUID
) -> None:
    entity = service.get_entity(db, case.id, entity_id)
    if entity.origin != Origin.ANALYST_ASSERTION:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="observed_identifier_immutable")
    identifier = db.scalar(
        select(EntityIdentifier).where(
            EntityIdentifier.id == identifier_id,
            EntityIdentifier.entity_id == entity.id,
            EntityIdentifier.case_id == case.id,
        )
    )
    if identifier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="identifier_not_found")
    db.delete(identifier)
    db.commit()


@router.post("/entities/{entity_id}/evidence-links", status_code=status.HTTP_201_CREATED)
def link_evidence(
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    entity_id: uuid.UUID,
    body: EvidenceLinkIn,
) -> dict[str, uuid.UUID]:
    entity = service.get_entity(db, case.id, entity_id)
    link = service.link_evidence(db, entity, principal.user, body.evidence_id, body.note)
    db.commit()
    return {"link_id": link.id}


@router.delete(
    "/entities/{entity_id}/evidence-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT
)
def unlink_evidence(
    case: WritableCase, db: DbDep, entity_id: uuid.UUID, link_id: uuid.UUID
) -> None:
    link = db.scalar(
        select(EntityEvidence).where(
            EntityEvidence.id == link_id,
            EntityEvidence.entity_id == entity_id,
            EntityEvidence.case_id == case.id,
        )
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="link_not_found")
    db.delete(link)
    db.commit()


@router.get("/observations")
def list_observations(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    query_run_id: Annotated[uuid.UUID | None, Query()] = None,
    evidence_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[ObservationOut]:
    conditions = [Observation.case_id == case.id]
    if entity_id is not None:
        conditions.append(Observation.entity_id == entity_id)
    if query_run_id is not None:
        conditions.append(Observation.query_run_id == query_run_id)
    if evidence_id is not None:
        conditions.append(Observation.evidence_id == evidence_id)
    total = db.scalar(select(func.count()).select_from(Observation).where(*conditions)) or 0
    rows = db.scalars(
        select(Observation)
        .where(*conditions)
        .order_by(Observation.collected_at, Observation.idempotency_key)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[ObservationOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/relationships")
def list_relationships(
    case: ReadableCase,
    db: DbDep,
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    origin: Annotated[Origin | None, Query()] = None,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    predicate: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[RelationshipOut]:
    items, total = service.list_relationships(
        db,
        case.id,
        entity_id=entity_id,
        origin=origin,
        review_status=review_status,
        predicate=predicate,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/relationships", status_code=status.HTTP_201_CREATED)
def create_relationship(
    case: WritableCase, db: DbDep, principal: PrincipalDep, body: RelationshipCreate
) -> RelationshipDetail:
    relationship = service.create_relationship(db, case.id, principal.user, body)
    db.commit()
    return service.relationship_detail(db, relationship)


@router.get("/relationships/{relationship_id}")
def get_relationship(
    case: ReadableCase, db: DbDep, relationship_id: uuid.UUID
) -> RelationshipDetail:
    return service.relationship_detail(db, service.get_relationship(db, case.id, relationship_id))


@router.patch("/relationships/{relationship_id}")
def update_relationship(
    case: WritableCase, db: DbDep, relationship_id: uuid.UUID, body: RelationshipUpdate
) -> RelationshipDetail:
    relationship = service.get_relationship(db, case.id, relationship_id)
    if relationship.origin != Origin.ANALYST_ASSERTION:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="observed_relationship_immutable")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "description" and value is None:
            continue
        setattr(relationship, field, value)
    if (
        relationship.valid_from
        and relationship.valid_to
        and relationship.valid_to < relationship.valid_from
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="valid_to must not be earlier than valid_from",
        )
    db.commit()
    return service.relationship_detail(db, relationship)


@router.post("/relationships/{relationship_id}/review")
def review_relationship(
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    relationship_id: uuid.UUID,
    body: ReviewDecisionIn,
) -> RelationshipDetail:
    relationship = service.get_relationship(db, case.id, relationship_id)
    service.record_review(db, relationship, principal.user, body.review_status, body.rationale)
    db.commit()
    return service.relationship_detail(db, relationship)


@router.post("/relationships/{relationship_id}/references", status_code=status.HTTP_201_CREATED)
def add_relationship_reference(
    case: WritableCase,
    db: DbDep,
    principal: PrincipalDep,
    relationship_id: uuid.UUID,
    body: RelationshipReferenceIn,
) -> RelationshipDetail:
    relationship = service.get_relationship(db, case.id, relationship_id)
    service.add_reference(db, relationship, principal.user, body)
    db.commit()
    return service.relationship_detail(db, relationship)


@router.delete(
    "/relationships/{relationship_id}/references/{reference_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_relationship_reference(
    case: WritableCase, db: DbDep, relationship_id: uuid.UUID, reference_id: uuid.UUID
) -> None:
    reference = db.scalar(
        select(RelationshipEvidence).where(
            RelationshipEvidence.id == reference_id,
            RelationshipEvidence.relationship_id == relationship_id,
            RelationshipEvidence.case_id == case.id,
        )
    )
    if reference is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reference_not_found")
    if reference.observation_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="observed_reference_immutable")
    db.delete(reference)
    db.commit()


@router.delete("/relationships/{relationship_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_relationship(case: WritableCase, db: DbDep, relationship_id: uuid.UUID) -> None:
    relationship = service.get_relationship(db, case.id, relationship_id)
    if relationship.origin != Origin.ANALYST_ASSERTION:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="observed_relationship_immutable")
    db.delete(relationship)
    db.commit()


@router.get("/graph")
def get_graph(
    case: ReadableCase,
    db: DbDep,
    focus_entity_id: Annotated[uuid.UUID | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=graph.MAX_DEPTH)] = 1,
    max_nodes: Annotated[int, Query(ge=1, le=graph.MAX_NODES)] = 60,
) -> GraphOut:
    if focus_entity_id is not None:
        service.get_entity(db, case.id, focus_entity_id)
    return graph.build_graph(
        db, case.id, focus_entity_id=focus_entity_id, depth=depth, max_nodes=max_nodes
    )
