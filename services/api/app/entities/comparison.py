"""Side-by-side comparison of two to four entities, read from existing records only.

The comparison never merges, links or edits anything. It reports:

* identifiers shared by several entities and those only one has, and conflicting stable IDs on
  the same platform (which indicate different accounts);
* direct relationships between the compared entities and neighbours they share, with origin and
  review status;
* source coverage: which collections and imports produced each entity's observations, and with
  what outcome;
* observation dates (event, publication, collection) per entity;
* changes between successive observations of the same source object, and items not seen in a
  later collection. An item missing from a failed, partial or truncated collection is *unknown*,
  never *deleted*, and a different date alone is never called a real-world change;
* conflicts (different values for the same field from different sources, contradicting evidence
  references) and unresolved questions (unreviewed relationships and suggestions).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from itertools import pairwise
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.entities.models import (
    Entity,
    EntityEvidence,
    EntityIdentifier,
    Observation,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
    Stance,
)
from app.entities.schemas import (
    ComparedEntity,
    ComparisonAbsence,
    ComparisonChange,
    ComparisonConflict,
    ComparisonIdentifier,
    ComparisonOut,
    ComparisonRelationship,
    SourceCoverage,
)
from app.evidence.models import EvidenceObject
from app.queries.models import ConnectorOutcome, ConnectorRun

MAX_ENTITIES = 4
MAX_OBSERVATIONS_PER_ENTITY = 2000
MAX_CHANGES = 200
# Payload fields compared across sources and over time. Counters change constantly and are
# reported as changes, not conflicts.
CONFLICT_FIELDS = (
    "name", "full_name", "title", "display_name", "website", "blog", "location", "country",
    "email", "company", "description", "biography", "custom_url",
)  # fmt: skip
IGNORED_CHANGE_FIELDS = frozenset(
    {"capability", "access_method", "processing_job_id", "note", "fields_not_available"}
)
COMPLETE_STOP = "complete"


def _value(value: Any) -> str | None:
    if value is None or value == "" or value == []:
        return None
    return str(value)[:500]


def compare(db: Session, case_id: uuid.UUID, entity_ids: list[uuid.UUID]) -> ComparisonOut:
    unique = list(dict.fromkeys(entity_ids))
    if not 2 <= len(unique) <= MAX_ENTITIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_comparison",
                "message": f"Compare between 2 and {MAX_ENTITIES} different entities.",
            },
        )
    entities = {
        entity.id: entity
        for entity in db.scalars(
            select(Entity).where(Entity.case_id == case_id, Entity.id.in_(unique))
        )
    }
    if len(entities) != len(unique):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="entity_not_found")

    identifiers = _identifiers(db, case_id, unique)
    relationships, shared_neighbours, unresolved = _relationships(db, case_id, unique)
    observations = {
        entity_id: list(
            db.execute(
                select(Observation, EvidenceObject)
                .outerjoin(EvidenceObject, EvidenceObject.id == Observation.evidence_id)
                .where(Observation.case_id == case_id, Observation.entity_id == entity_id)
                .order_by(Observation.collected_at, Observation.idempotency_key)
                .limit(MAX_OBSERVATIONS_PER_ENTITY)
            ).all()
        )
        for entity_id in unique
    }
    compared = []
    changes: list[ComparisonChange] = []
    absences: list[ComparisonAbsence] = []
    conflicts: list[ComparisonConflict] = []
    for entity_id in unique:
        entity = entities[entity_id]
        rows = observations[entity_id]
        compared.append(_summary(db, case_id, entity, rows))
        changes.extend(_changes(entity_id, rows))
        conflicts.extend(_field_conflicts(entity_id, rows))
        absences.extend(_absences(db, case_id, entity_id, rows))
    conflicts.extend(_contradictions(db, case_id, unique))
    for row in identifiers:
        if row.kind == "conflicting_platform_id":
            conflicts.append(
                ComparisonConflict(
                    kind="different_stable_ids",
                    entity_ids=row.entity_ids,
                    field=f"platform_id ({row.platform})",
                    values=row.values,
                    note=(
                        "Different stable IDs on the same platform identify different accounts, "
                        "even if names or usernames match."
                    ),
                    evidence_ids=[],
                )
            )
    for row in identifiers:
        if row.kind == "shared" and not any(
            rel.review_status == ReviewStatus.ACCEPTED
            and {rel.source_entity_id, rel.target_entity_id} <= set(row.entity_ids)
            for rel in relationships
        ):
            unresolved.append(
                f"{', '.join(entities[e].display_name for e in row.entity_ids)} share the "
                f"{row.identifier_type} '{row.values[0]}' with no accepted relationship between "
                "them. A shared identifier is a lead to review, not proof that they are the same."
            )
    return ComparisonOut(
        entities=compared,
        identifiers=identifiers,
        relationships=relationships,
        shared_neighbours=shared_neighbours,
        changes=changes[:MAX_CHANGES],
        changes_truncated=len(changes) > MAX_CHANGES,
        absences=absences,
        conflicts=conflicts,
        unresolved=unresolved,
        merge_policy=(
            "Tracehollow never merges entities automatically. Shared identifiers, similar names "
            "and matching dates are shown for review; an analyst decides through reviewed "
            "relationships."
        ),
    )


def _identifiers(
    db: Session, case_id: uuid.UUID, entity_ids: list[uuid.UUID]
) -> list[ComparisonIdentifier]:
    grouped: dict[tuple[str, str | None, str], set[uuid.UUID]] = defaultdict(set)
    originals: dict[tuple[str, str | None, str], str] = {}
    for identifier in db.scalars(
        select(EntityIdentifier)
        .where(EntityIdentifier.case_id == case_id, EntityIdentifier.entity_id.in_(entity_ids))
        .order_by(EntityIdentifier.created_at)
    ):
        key = (identifier.identifier_type, identifier.platform, identifier.normalized_value)
        grouped[key].add(identifier.entity_id)
        originals.setdefault(key, identifier.original_value)
    rows = [
        ComparisonIdentifier(
            kind="shared" if len(owners) > 1 else "only_one",
            identifier_type=identifier_type,
            platform=platform,
            values=[originals[(identifier_type, platform, normalized)]],
            entity_ids=[entity_id for entity_id in entity_ids if entity_id in owners],
        )
        for (identifier_type, platform, normalized), owners in grouped.items()
    ]
    by_platform: dict[str, dict[uuid.UUID, str]] = defaultdict(dict)
    for (identifier_type, platform, normalized), holders in grouped.items():
        if identifier_type == "platform_id" and platform:
            for owner in holders:
                by_platform[platform][owner] = originals[(identifier_type, platform, normalized)]
    for platform, ids in by_platform.items():
        if len(set(ids.values())) > 1 and len(ids) > 1:
            rows.append(
                ComparisonIdentifier(
                    kind="conflicting_platform_id",
                    identifier_type="platform_id",
                    platform=platform,
                    values=[ids[e] for e in entity_ids if e in ids],
                    entity_ids=[e for e in entity_ids if e in ids],
                )
            )
    order = {"shared": 0, "conflicting_platform_id": 1, "only_one": 2}
    return sorted(rows, key=lambda r: (order[r.kind], r.identifier_type, r.values[0]))


def _relationships(
    db: Session, case_id: uuid.UUID, entity_ids: list[uuid.UUID]
) -> tuple[list[ComparisonRelationship], list[dict[str, Any]], list[str]]:
    compared = set(entity_ids)
    rows = list(
        db.scalars(
            select(Relationship)
            .where(
                Relationship.case_id == case_id,
                or_(
                    Relationship.source_entity_id.in_(entity_ids),
                    Relationship.target_entity_id.in_(entity_ids),
                ),
            )
            .order_by(Relationship.created_at)
            .limit(2000)
        )
    )
    names = {
        entity.id: entity.display_name
        for entity in db.scalars(
            select(Entity).where(
                Entity.case_id == case_id,
                Entity.id.in_(
                    {r.source_entity_id for r in rows} | {r.target_entity_id for r in rows}
                ),
            )
        )
    }
    reference_counts: dict[uuid.UUID, int] = {
        relationship_id: count
        for relationship_id, count in db.execute(
            select(RelationshipEvidence.relationship_id, func.count())
            .where(RelationshipEvidence.relationship_id.in_([r.id for r in rows]))
            .group_by(RelationshipEvidence.relationship_id)
        )
    }
    direct: list[ComparisonRelationship] = []
    neighbours: dict[uuid.UUID, dict[uuid.UUID, list[str]]] = defaultdict(lambda: defaultdict(list))
    unresolved: list[str] = []
    for rel in rows:
        endpoints = {rel.source_entity_id, rel.target_entity_id}
        out = ComparisonRelationship(
            id=rel.id,
            source_entity_id=rel.source_entity_id,
            source_name=names.get(rel.source_entity_id, ""),
            target_entity_id=rel.target_entity_id,
            target_name=names.get(rel.target_entity_id, ""),
            predicate=rel.predicate,
            origin=rel.origin,
            review_status=rel.review_status,
            reference_count=int(reference_counts.get(rel.id, 0)),
            between_compared=endpoints <= compared,
        )
        if endpoints <= compared:
            direct.append(out)
        else:
            inside = next(iter(endpoints & compared))
            outside = next(iter(endpoints - compared))
            neighbours[outside][inside].append(f"{rel.predicate} ({rel.review_status})")
        if endpoints & compared and rel.review_status == ReviewStatus.UNREVIEWED:
            unresolved.append(
                f"Unreviewed {rel.origin.replace('_', ' ')} relationship: "
                f"{names.get(rel.source_entity_id, '?')} {rel.predicate} "
                f"{names.get(rel.target_entity_id, '?')}."
            )
    shared = [
        {
            "entity_id": str(outside),
            "display_name": names.get(outside, ""),
            "connections": {str(inside): preds for inside, preds in links.items()},
        }
        for outside, links in neighbours.items()
        if len(links) > 1
    ]
    return direct, shared, unresolved


def _summary(db: Session, case_id: uuid.UUID, entity: Entity, rows: list[Any]) -> ComparedEntity:
    def span(values: list[datetime | None]) -> list[datetime] | None:
        present = [value for value in values if value is not None]
        return [min(present), max(present)] if present else None

    runs = {obs.connector_run_id for obs, _evidence in rows if obs.connector_run_id}
    run_rows = (
        {
            run.id: run
            for run in db.scalars(
                select(ConnectorRun).where(
                    ConnectorRun.case_id == case_id, ConnectorRun.id.in_(runs)
                )
            )
        }
        if runs
        else {}
    )
    coverage: dict[tuple[str, str], SourceCoverage] = {}
    for obs, evidence in rows:
        if evidence is None:
            continue
        run = run_rows.get(obs.connector_run_id) if obs.connector_run_id else None
        source = evidence.connector_id or "authorized_import"
        key = (source, evidence.acquisition_method)
        item = coverage.setdefault(
            key,
            SourceCoverage(
                source=source,
                acquisition_method=evidence.acquisition_method,
                collection_mode=evidence.collection_mode,
                observations=0,
                connector_runs=[],
                first_collected_at=obs.collected_at,
                last_collected_at=obs.collected_at,
            ),
        )
        item.observations += 1
        item.first_collected_at = min(item.first_collected_at, obs.collected_at)
        item.last_collected_at = max(item.last_collected_at, obs.collected_at)
        if run is not None and not any(r["id"] == str(run.id) for r in item.connector_runs):
            item.connector_runs.append(
                {
                    "id": str(run.id),
                    "outcome": run.outcome,
                    "stopped_reason": (run.coverage or {}).get("stopped_reason"),
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                }
            )
    linked = (
        db.scalar(
            select(func.count())
            .select_from(EntityEvidence)
            .where(EntityEvidence.entity_id == entity.id, EntityEvidence.case_id == case_id)
        )
        or 0
    )
    return ComparedEntity(
        id=entity.id,
        display_name=entity.display_name,
        entity_type=entity.entity_type,
        origin=entity.origin,
        observation_count=len(rows),
        linked_evidence_count=int(linked),
        event_time_span=span([obs.event_time for obs, _ in rows]),
        published_span=span([obs.source_published_at for obs, _ in rows]),
        collected_span=span([obs.collected_at for obs, _ in rows]),
        coverage=sorted(coverage.values(), key=lambda c: (c.source, c.acquisition_method)),
    )


def _changes(entity_id: uuid.UUID, rows: list[Any]) -> list[ComparisonChange]:
    """Field differences between successive observations of the same source object."""
    series: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for obs, _evidence in rows:
        series[(obs.observation_type, obs.source_object_id or "")].append(obs)
    changes = []
    for (observation_type, source_object_id), items in series.items():
        for previous, current in pairwise(items):
            fields = (set(previous.payload) | set(current.payload)) - IGNORED_CHANGE_FIELDS
            for field in sorted(fields):
                before, after = (
                    _value(previous.payload.get(field)),
                    _value(current.payload.get(field)),
                )
                if before == after:
                    continue
                changes.append(
                    ComparisonChange(
                        entity_id=entity_id,
                        observation_type=observation_type,
                        source_object_id=source_object_id or None,
                        field=field,
                        previous=before,
                        current=after,
                        previous_observation_id=previous.id,
                        current_observation_id=current.id,
                        previous_collected_at=previous.collected_at,
                        current_collected_at=current.collected_at,
                        previous_evidence_id=previous.evidence_id,
                        current_evidence_id=current.evidence_id,
                        note=(
                            "The source reported a different value in a later collection. The "
                            "change happened at an unknown time between the two collections; "
                            "collection and publication dates do not date it."
                        ),
                    )
                )
    return changes


def _field_conflicts(entity_id: uuid.UUID, rows: list[Any]) -> list[ComparisonConflict]:
    """The same field with different values from different sources, latest value per source."""
    latest: dict[str, dict[str, tuple[str, uuid.UUID | None]]] = defaultdict(dict)
    for obs, evidence in rows:
        source = (evidence.connector_id if evidence is not None else None) or "authorized_import"
        for field in CONFLICT_FIELDS:
            value = _value(obs.payload.get(field))
            if value is not None:
                latest[field][source] = (value, obs.evidence_id)
    conflicts = []
    for field, by_source in latest.items():
        values = {value for value, _evidence in by_source.values()}
        if len(values) > 1 and len(by_source) > 1:
            conflicts.append(
                ComparisonConflict(
                    kind="different_values_across_sources",
                    entity_ids=[entity_id],
                    field=field,
                    values=[
                        f"{source}: {value}" for source, (value, _e) in sorted(by_source.items())
                    ],
                    note=(
                        "Sources disagree. Neither value is preferred automatically; they may "
                        "reflect different times, formats or errors."
                    ),
                    evidence_ids=[e for _v, e in by_source.values() if e is not None],
                )
            )
    return conflicts


def _contradictions(
    db: Session, case_id: uuid.UUID, entity_ids: list[uuid.UUID]
) -> list[ComparisonConflict]:
    rows = db.execute(
        select(Relationship, RelationshipEvidence)
        .join(RelationshipEvidence, RelationshipEvidence.relationship_id == Relationship.id)
        .where(
            Relationship.case_id == case_id,
            RelationshipEvidence.stance == Stance.CONTRADICTS,
            or_(
                Relationship.source_entity_id.in_(entity_ids),
                Relationship.target_entity_id.in_(entity_ids),
            ),
        )
        .limit(200)
    ).all()
    return [
        ComparisonConflict(
            kind="contradicting_evidence",
            entity_ids=[e for e in entity_ids if e in (rel.source_entity_id, rel.target_entity_id)],
            field=f"relationship {rel.predicate}",
            values=[ref.note or "evidence recorded as contradicting"],
            note="Evidence references on this relationship contradict it.",
            evidence_ids=[ref.evidence_id] if ref.evidence_id else [],
        )
        for rel, ref in rows
    ]


def _absences(
    db: Session, case_id: uuid.UUID, entity_id: uuid.UUID, rows: list[Any]
) -> list[ComparisonAbsence]:
    """Items seen in one collection of an entity and not in the next collection by that source."""
    run_ids = list({obs.connector_run_id for obs, _ in rows if obs.connector_run_id})
    if len(run_ids) < 2:
        return []
    runs = list(
        db.scalars(
            select(ConnectorRun)
            .where(ConnectorRun.case_id == case_id, ConnectorRun.id.in_(run_ids))
            .order_by(ConnectorRun.finished_at)
        )
    )
    items: dict[uuid.UUID, set[tuple[str, str]]] = defaultdict(set)
    for observation_type, source_object_id, connector_run_id in db.execute(
        select(
            Observation.observation_type, Observation.source_object_id, Observation.connector_run_id
        ).where(Observation.case_id == case_id, Observation.connector_run_id.in_(run_ids))
    ):
        if source_object_id:
            items[connector_run_id].add((observation_type, source_object_id))
    by_connector: dict[str, list[ConnectorRun]] = defaultdict(list)
    for run in runs:
        by_connector[run.connector_id].append(run)
    result = []
    for connector_id, series in by_connector.items():
        for earlier, later in pairwise(series):
            missing = sorted(items[earlier.id] - items[later.id])
            if not missing:
                continue
            stopped = (later.coverage or {}).get("stopped_reason")
            complete = (
                later.outcome in (ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS)
                and stopped == COMPLETE_STOP
            )
            result.append(
                ComparisonAbsence(
                    entity_id=entity_id,
                    connector_id=connector_id,
                    earlier_run_id=earlier.id,
                    later_run_id=later.id,
                    later_run_outcome=later.outcome,
                    later_run_stopped_reason=stopped,
                    items=[f"{kind}:{object_id}" for kind, object_id in missing[:100]],
                    items_total=len(missing),
                    interpretation=(
                        "not_observed_in_later_complete_collection"
                        if complete
                        else "unknown_later_collection_incomplete"
                    ),
                    note=(
                        "Not returned by a later collection that finished completely. This does "
                        "not prove deletion: items can be hidden, made private, moved or excluded "
                        "by the source."
                        if complete
                        else "The later collection failed, was partial or stopped early, so "
                        "whether these items still exist is unknown."
                    ),
                )
            )
    return result
