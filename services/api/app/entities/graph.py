"""Bounded relationship graph queries. The browser never receives the whole case graph."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, union_all
from sqlalchemy.orm import Session

from app.entities.models import Entity, Relationship
from app.entities.schemas import GraphEdge, GraphNode, GraphOut

MAX_NODES = 150
MAX_EDGES = 400
MAX_DEPTH = 2


def build_graph(
    db: Session,
    case_id: uuid.UUID,
    *,
    focus_entity_id: uuid.UUID | None,
    depth: int,
    max_nodes: int,
) -> GraphOut:
    depth = max(1, min(depth, MAX_DEPTH))
    max_nodes = max(1, min(max_nodes, MAX_NODES))
    total_entities = (
        db.scalar(select(func.count()).select_from(Entity).where(Entity.case_id == case_id)) or 0
    )
    total_relationships = (
        db.scalar(
            select(func.count()).select_from(Relationship).where(Relationship.case_id == case_id)
        )
        or 0
    )
    truncated = False

    if focus_entity_id is not None:
        node_ids: list[uuid.UUID] = [focus_entity_id]
        frontier = {focus_entity_id}
        for _ in range(depth):
            if not frontier or len(node_ids) >= max_nodes:
                break
            neighbours = db.execute(
                select(Relationship.source_entity_id, Relationship.target_entity_id)
                .where(
                    Relationship.case_id == case_id,
                    or_(
                        Relationship.source_entity_id.in_(frontier),
                        Relationship.target_entity_id.in_(frontier),
                    ),
                )
                .order_by(Relationship.created_at)
                .limit(MAX_EDGES + 1)
            ).all()
            if len(neighbours) > MAX_EDGES:
                truncated = True
            next_frontier: set[uuid.UUID] = set()
            for source, target in neighbours[:MAX_EDGES]:
                for candidate in (source, target):
                    if candidate not in node_ids:
                        if len(node_ids) >= max_nodes:
                            truncated = True
                            break
                        node_ids.append(candidate)
                        next_frontier.add(candidate)
            frontier = next_frontier
    else:
        endpoints = union_all(
            select(Relationship.source_entity_id.label("entity_id")).where(
                Relationship.case_id == case_id
            ),
            select(Relationship.target_entity_id.label("entity_id")).where(
                Relationship.case_id == case_id
            ),
        ).subquery()
        degree = (
            select(endpoints.c.entity_id, func.count().label("degree"))
            .group_by(endpoints.c.entity_id)
            .subquery()
        )
        ranked = db.execute(
            select(Entity.id)
            .outerjoin(degree, degree.c.entity_id == Entity.id)
            .where(Entity.case_id == case_id)
            .order_by(func.coalesce(degree.c.degree, 0).desc(), Entity.display_name, Entity.id)
            .limit(max_nodes)
        ).scalars()
        node_ids = list(ranked)
        truncated = total_entities > len(node_ids)

    entities = {
        entity.id: entity
        for entity in db.scalars(
            select(Entity).where(Entity.case_id == case_id, Entity.id.in_(node_ids))
        )
    }
    ordered_ids = [entity_id for entity_id in node_ids if entity_id in entities]
    edges = list(
        db.scalars(
            select(Relationship)
            .where(
                Relationship.case_id == case_id,
                Relationship.source_entity_id.in_(ordered_ids),
                Relationship.target_entity_id.in_(ordered_ids),
            )
            .order_by(Relationship.created_at)
            .limit(MAX_EDGES + 1)
        )
    )
    if len(edges) > MAX_EDGES:
        truncated = True
        edges = edges[:MAX_EDGES]
    return GraphOut(
        nodes=[
            GraphNode(
                id=entities[entity_id].id,
                label=entities[entity_id].display_name,
                entity_type=entities[entity_id].entity_type,
                origin=entities[entity_id].origin,
            )
            for entity_id in ordered_ids
        ],
        edges=[
            GraphEdge(
                id=edge.id,
                source=edge.source_entity_id,
                target=edge.target_entity_id,
                predicate=edge.predicate,
                origin=edge.origin,
                review_status=edge.review_status,
            )
            for edge in edges
        ],
        focus_entity_id=focus_entity_id,
        depth=depth,
        max_nodes=max_nodes,
        truncated=truncated,
        total_entities=total_entities,
        total_relationships=total_relationships,
    )
