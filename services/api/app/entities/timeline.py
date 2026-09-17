"""Case timeline built from observations (the existing temporal record), not a separate model.

Every observation lands in exactly one section:

``dated``
    It has an instant on the UTC timeline: the event time a source reported, or, when only that is
    known, the time the source says the item was published. The basis is always stated.
``local_time_only``
    The source gave a wall-clock time without a timezone (for example a WhatsApp export imported
    with the timezone left unknown). It is ordered by that local time and never mixed with UTC.
``undated``
    Only the collection time is known. Collection time says when Tracehollow retrieved the item,
    not when anything happened, so these items are listed separately.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.entities.models import Entity, Observation
from app.entities.schemas import TimelineItem, TimelineOut
from app.evidence.models import EvidenceObject

Section = Literal["dated", "local_time_only", "undated"]
_SUMMARY_KEYS = ("text", "title", "caption", "biography", "description", "og_title", "full_name")


def _local_time() -> ColumnElement[str]:
    column: ColumnElement[str] = Observation.payload["local_time"].astext
    return column


def _section_condition(section: Section) -> ColumnElement[bool]:
    dated = or_(Observation.event_time.is_not(None), Observation.source_published_at.is_not(None))
    has_local = and_(_local_time().is_not(None), _local_time() != "")
    if section == "dated":
        return dated
    if section == "local_time_only":
        return and_(~dated, has_local)
    return and_(~dated, ~has_local)


def _summary(payload: dict[str, Any]) -> str | None:
    for key in _SUMMARY_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text[:280] + ("…" if len(text) > 280 else "")
    return None


def _label(payload: dict[str, Any]) -> str | None:
    for key in ("sender_label", "author_display_name", "username", "channel", "forwarded_from"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return None


def build_timeline(
    db: Session,
    case_id: uuid.UUID,
    *,
    section: Section,
    entity_id: uuid.UUID | None,
    evidence_id: uuid.UUID | None,
    observation_type: str | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    offset: int,
) -> TimelineOut:
    base: list[ColumnElement[bool]] = [Observation.case_id == case_id]
    if entity_id is not None:
        base.append(Observation.entity_id == entity_id)
    if evidence_id is not None:
        base.append(Observation.evidence_id == evidence_id)
    if observation_type is not None:
        base.append(Observation.observation_type == observation_type)
    instant = func.coalesce(Observation.event_time, Observation.source_published_at)
    sections: dict[str, int] = {}
    names: tuple[Section, ...] = ("dated", "local_time_only", "undated")
    for name in names:
        conditions = [*base, _section_condition(name)]
        if name == "dated":
            if start is not None:
                conditions.append(instant >= start)
            if end is not None:
                conditions.append(instant <= end)
        sections[name] = int(
            db.scalar(select(func.count()).select_from(Observation).where(*conditions)) or 0
        )
    conditions = [*base, _section_condition(section)]
    if section == "dated":
        if start is not None:
            conditions.append(instant >= start)
        if end is not None:
            conditions.append(instant <= end)
        order = [instant, Observation.collected_at, Observation.idempotency_key]
    elif section == "local_time_only":
        order = [_local_time(), Observation.collected_at, Observation.idempotency_key]
    else:
        order = [Observation.collected_at, Observation.idempotency_key]
    rows = db.execute(
        select(Observation, EvidenceObject, Entity)
        .outerjoin(
            EvidenceObject,
            and_(
                EvidenceObject.id == Observation.evidence_id,
                EvidenceObject.case_id == Observation.case_id,
            ),
        )
        .outerjoin(
            Entity, and_(Entity.id == Observation.entity_id, Entity.case_id == Observation.case_id)
        )
        .where(*conditions)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    ).all()
    items = []
    for observation, evidence, entity in rows:
        payload = observation.payload or {}
        notes: list[str] = []
        if observation.event_time is not None:
            basis = "event_time"
            when = observation.event_time
        elif observation.source_published_at is not None:
            basis = "source_published_at"
            when = observation.source_published_at
            notes.append(
                "Publication time as the source reports it; it does not establish when the "
                "underlying event happened."
            )
        elif payload.get("local_time"):
            basis = "local_time_without_timezone"
            when = None
            notes.append(
                "Local wall-clock time from the source without a timezone; it cannot be placed "
                "on the UTC timeline."
            )
        else:
            basis = "collected_at_only"
            when = None
            notes.append("Only the retrieval time is known.")
        time_basis_note = payload.get("time_basis")
        if isinstance(time_basis_note, str) and time_basis_note.startswith(
            ("nonexistent", "ambig")
        ):
            notes.append(f"Source time note: {time_basis_note}.")
        if payload.get("edited") is True or payload.get("edited_marker") is True:
            notes.append("The source marks this item as edited; earlier content is not available.")
        items.append(
            TimelineItem(
                observation_id=observation.id,
                observation_type=observation.observation_type,
                time=when,
                time_basis=basis,
                local_time=payload.get("local_time")
                if isinstance(payload.get("local_time"), str)
                else None,
                timestamp_text=payload.get("timestamp_text")
                if isinstance(payload.get("timestamp_text"), str)
                else None,
                collected_at=observation.collected_at,
                source_published_at=observation.source_published_at,
                summary=_summary(payload),
                source_label=_label(payload),
                source_object_id=observation.source_object_id,
                entity_id=entity.id if entity is not None else None,
                entity_name=entity.display_name if entity is not None else None,
                evidence_id=evidence.id if evidence is not None else None,
                evidence_title=evidence.title if evidence is not None else None,
                acquisition_method=evidence.acquisition_method if evidence is not None else None,
                connector_id=evidence.connector_id if evidence is not None else None,
                connector_run_id=observation.connector_run_id,
                location={
                    key: payload[key]
                    for key in ("line_start", "line_end", "char_start", "char_end")
                    if isinstance(payload.get(key), int)
                }
                or None,
                notes=notes,
            )
        )
    return TimelineOut(
        section=section,
        items=items,
        total=sections[section],
        limit=limit,
        offset=offset,
        sections=sections,
    )
