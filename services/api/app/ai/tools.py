"""Registered, typed, read-only database tools for exact counts, dates and filters.

The model may only name a registered tool and supply arguments; arguments are validated with a
strict Pydantic model, the case id is always bound by the server, and every query is a fixed,
parameterized SQLAlchemy statement executed in a read-only transaction. Results describe the
entire authorized case, which is different from the excerpts retrieved for an answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from app.entities.models import (
    Entity,
    EntityIdentifier,
    EntityType,
    IdentifierType,
    Observation,
    Origin,
    Relationship,
    ReviewStatus,
)
from app.entities.normalize import IdentifierError, normalize_identifier
from app.evidence.models import AcquisitionMethod, EvidenceKind, EvidenceObject
from app.queries.models import ConnectorOutcome, ConnectorRun, QueryRun, RunStatus

MAX_LIST_ROWS = 20
MAX_COVERAGE_ROWS = 50


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateRange(_Args):
    collected_from: date | None = Field(
        default=None, description="Collected on or after (YYYY-MM-DD, UTC)"
    )
    collected_to: date | None = Field(
        default=None, description="Collected on or before (YYYY-MM-DD, UTC)"
    )


class CountEvidenceArgs(DateRange):
    kind: EvidenceKind | None = None
    acquisition_method: AcquisitionMethod | None = None
    published_from: date | None = Field(default=None, description="Source published on or after")
    published_to: date | None = Field(default=None, description="Source published on or before")


class ListEvidenceArgs(CountEvidenceArgs):
    order_by: str = Field(default="collected_at", pattern="^(collected_at|source_published_at)$")


class CountEntitiesArgs(_Args):
    entity_type: EntityType | None = None
    origin: Origin | None = None


class CountRelationshipsArgs(_Args):
    predicate: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    origin: Origin | None = None
    review_status: ReviewStatus | None = None


class CountQueryRunsArgs(_Args):
    status: RunStatus | None = None
    finished_from: date | None = None
    finished_to: date | None = None


class CountObservationsArgs(DateRange):
    pass


class FindEntitiesArgs(_Args):
    identifier: str = Field(min_length=1, max_length=300)


class NoArgs(_Args):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[_Args]
    run: Callable[[Session, uuid.UUID, Any], dict[str, Any]]


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _end_exclusive(day: date) -> datetime:
    return _start(day) + timedelta(days=1)


def _describe(label: str, filters: dict[str, Any]) -> str:
    active = {key: value for key, value in filters.items() if value is not None}
    if not active:
        return f"{label} in the entire case"
    parts = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in active.items())
    return f"{label} in the entire case ({parts})"


def _evidence_filters(statement: Select[Any], args: CountEvidenceArgs) -> Select[Any]:
    if args.kind is not None:
        statement = statement.where(EvidenceObject.kind == args.kind)
    if args.acquisition_method is not None:
        statement = statement.where(EvidenceObject.acquisition_method == args.acquisition_method)
    if args.collected_from is not None:
        statement = statement.where(EvidenceObject.collected_at >= _start(args.collected_from))
    if args.collected_to is not None:
        statement = statement.where(EvidenceObject.collected_at < _end_exclusive(args.collected_to))
    if args.published_from is not None:
        statement = statement.where(
            EvidenceObject.source_published_at >= _start(args.published_from)
        )
    if args.published_to is not None:
        statement = statement.where(
            EvidenceObject.source_published_at < _end_exclusive(args.published_to)
        )
    return statement


def _filters(args: _Args) -> dict[str, Any]:
    return {
        key: (
            value.isoformat()
            if isinstance(value, date)
            else str(value)
            if value is not None
            else None
        )
        for key, value in args.model_dump().items()
        if key != "order_by"
    }


def count_evidence(db: Session, case_id: uuid.UUID, args: CountEvidenceArgs) -> dict[str, Any]:
    statement = _evidence_filters(
        select(func.count()).select_from(EvidenceObject).where(EvidenceObject.case_id == case_id),
        args,
    )
    filters = _filters(args)
    return {
        "count": int(db.scalar(statement) or 0),
        "filters": filters,
        "description": _describe("Evidence records", filters),
    }


def list_evidence(db: Session, case_id: uuid.UUID, args: ListEvidenceArgs) -> dict[str, Any]:
    order = (
        EvidenceObject.source_published_at
        if args.order_by == "source_published_at"
        else EvidenceObject.collected_at
    )
    base = _evidence_filters(select(EvidenceObject).where(EvidenceObject.case_id == case_id), args)
    total = int(
        db.scalar(
            _evidence_filters(
                select(func.count())
                .select_from(EvidenceObject)
                .where(EvidenceObject.case_id == case_id),
                args,
            )
        )
        or 0
    )
    rows = db.scalars(
        base.order_by(order.asc().nulls_last(), EvidenceObject.id).limit(MAX_LIST_ROWS)
    )
    filters = _filters(args)
    return {
        "count": total,
        "returned": min(total, MAX_LIST_ROWS),
        "truncated": total > MAX_LIST_ROWS,
        "filters": filters,
        "description": _describe("Evidence records", filters),
        "records": [
            {
                "evidence_id": str(row.id),
                "title": row.title,
                "kind": row.kind,
                "acquisition_method": row.acquisition_method,
                "collected_at": row.collected_at.isoformat(),
                "source_published_at": row.source_published_at.isoformat()
                if row.source_published_at
                else None,
                "synthetic": row.acquisition_method == AcquisitionMethod.SYNTHETIC_FIXTURE,
            }
            for row in rows
        ],
    }


def count_entities(db: Session, case_id: uuid.UUID, args: CountEntitiesArgs) -> dict[str, Any]:
    statement = select(func.count()).select_from(Entity).where(Entity.case_id == case_id)
    if args.entity_type is not None:
        statement = statement.where(Entity.entity_type == args.entity_type)
    if args.origin is not None:
        statement = statement.where(Entity.origin == args.origin)
    filters = _filters(args)
    return {
        "count": int(db.scalar(statement) or 0),
        "filters": filters,
        "description": _describe("Entities", filters),
    }


def count_relationships(
    db: Session, case_id: uuid.UUID, args: CountRelationshipsArgs
) -> dict[str, Any]:
    statement = (
        select(func.count()).select_from(Relationship).where(Relationship.case_id == case_id)
    )
    if args.predicate is not None:
        statement = statement.where(Relationship.predicate == args.predicate)
    if args.origin is not None:
        statement = statement.where(Relationship.origin == args.origin)
    if args.review_status is not None:
        statement = statement.where(Relationship.review_status == args.review_status)
    filters = _filters(args)
    return {
        "count": int(db.scalar(statement) or 0),
        "filters": filters,
        "description": _describe("Relationships", filters),
    }


def count_query_runs(db: Session, case_id: uuid.UUID, args: CountQueryRunsArgs) -> dict[str, Any]:
    statement = select(func.count()).select_from(QueryRun).where(QueryRun.case_id == case_id)
    if args.status is not None:
        statement = statement.where(QueryRun.status == args.status)
    if args.finished_from is not None:
        statement = statement.where(QueryRun.finished_at >= _start(args.finished_from))
    if args.finished_to is not None:
        statement = statement.where(QueryRun.finished_at < _end_exclusive(args.finished_to))
    filters = _filters(args)
    return {
        "count": int(db.scalar(statement) or 0),
        "filters": filters,
        "description": _describe("Query executions", filters),
    }


def count_observations(
    db: Session, case_id: uuid.UUID, args: CountObservationsArgs
) -> dict[str, Any]:
    statement = select(func.count()).select_from(Observation).where(Observation.case_id == case_id)
    if args.collected_from is not None:
        statement = statement.where(Observation.collected_at >= _start(args.collected_from))
    if args.collected_to is not None:
        statement = statement.where(Observation.collected_at < _end_exclusive(args.collected_to))
    filters = _filters(args)
    return {
        "count": int(db.scalar(statement) or 0),
        "filters": filters,
        "description": _describe("Connector observations", filters),
    }


def connector_coverage(db: Session, case_id: uuid.UUID, args: NoArgs) -> dict[str, Any]:
    complete = (ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS)
    total = int(
        db.scalar(
            select(func.count()).select_from(ConnectorRun).where(ConnectorRun.case_id == case_id)
        )
        or 0
    )
    gaps = db.execute(
        select(
            QueryRun.run_number,
            ConnectorRun.connector_id,
            ConnectorRun.status,
            ConnectorRun.outcome,
            ConnectorRun.coverage_note,
            ConnectorRun.finished_at,
        )
        .join(QueryRun, QueryRun.id == ConnectorRun.query_run_id)
        .where(
            ConnectorRun.case_id == case_id,
            (ConnectorRun.outcome.is_(None)) | (ConnectorRun.outcome.not_in(complete)),
        )
        .order_by(ConnectorRun.finished_at.desc().nulls_first())
        .limit(MAX_COVERAGE_ROWS)
    ).all()
    return {
        "count": len(gaps),
        "connector_runs_total": total,
        "description": (
            "Connector runs in the entire case whose collection was incomplete, failed or canceled"
        ),
        "gaps": [
            {
                "run_number": row[0],
                "connector_id": row[1],
                "status": row[2],
                "outcome": row[3],
                "coverage_note": row[4],
                "finished_at": row[5].isoformat() if row[5] else None,
            }
            for row in gaps
        ],
        "note": "An incomplete or failed collection is not evidence that something does not exist.",
    }


def find_entities(db: Session, case_id: uuid.UUID, args: FindEntitiesArgs) -> dict[str, Any]:
    keys: set[str] = set()
    for identifier_type in IdentifierType:
        try:
            keys.add(normalize_identifier(identifier_type, args.identifier))
        except (IdentifierError, ValueError):
            continue
    matches = (
        select(Entity.id)
        .join(EntityIdentifier, EntityIdentifier.entity_id == Entity.id)
        .where(
            Entity.case_id == case_id, EntityIdentifier.normalized_value.in_(sorted(keys) or [""])
        )
    )
    statement = (
        select(Entity)
        .where(Entity.case_id == case_id, Entity.id.in_(matches))
        .order_by(Entity.created_at, Entity.id)
    )
    rows = list(db.scalars(statement.limit(MAX_LIST_ROWS)))
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    return {
        "count": total,
        "description": (
            f"Entities in the entire case with an identifier equal to {args.identifier!r}"
        ),
        "entities": [
            {
                "entity_id": str(row.id),
                "display_name": row.display_name,
                "entity_type": row.entity_type,
                "origin": row.origin,
            }
            for row in rows
        ],
        "note": (
            "Matching identifiers do not establish that entities are the same person or account."
        ),
    }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "count_evidence",
            "Count evidence records, optionally by kind, acquisition method, collection date or "
            "source publication date.",
            CountEvidenceArgs,
            count_evidence,
        ),
        Tool(
            "list_evidence",
            "List up to 20 evidence records (title, dates, acquisition method) with the same "
            "filters as count_evidence.",
            ListEvidenceArgs,
            list_evidence,
        ),
        Tool(
            "count_entities",
            "Count entities, optionally by entity type or origin.",
            CountEntitiesArgs,
            count_entities,
        ),
        Tool(
            "count_relationships",
            "Count relationships, optionally by predicate, origin or review status.",
            CountRelationshipsArgs,
            count_relationships,
        ),
        Tool(
            "count_query_runs",
            "Count query executions, optionally by status or finish date.",
            CountQueryRunsArgs,
            count_query_runs,
        ),
        Tool(
            "count_observations",
            "Count connector observations, optionally by collection date.",
            CountObservationsArgs,
            count_observations,
        ),
        Tool(
            "connector_coverage",
            "List connector runs whose collection was incomplete, failed or canceled.",
            NoArgs,
            connector_coverage,
        ),
        Tool(
            "find_entities",
            "Find entities that have an identifier equal to a value (domain, email, username, "
            "URL, IP, phone or name).",
            FindEntitiesArgs,
            find_entities,
        ),
    )
}

# Union of all argument names, used for the planning output schema (nulls are ignored).
ARGUMENT_NAMES = sorted({name for tool in TOOLS.values() for name in tool.args_model.model_fields})


def _resolve(spec: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in spec:
        return dict(definitions[spec["$ref"].rsplit("/", 1)[-1]])
    for option in spec.get("anyOf", []):
        if "$ref" in option or option.get("type") != "null":
            return {**_resolve(option, definitions), "description": spec.get("description")}
    return spec


def describe_tools() -> list[dict[str, Any]]:
    described = []
    for tool in TOOLS.values():
        schema = tool.args_model.model_json_schema()
        definitions = schema.get("$defs", {})
        arguments = {}
        for name, spec in schema.get("properties", {}).items():
            resolved = _resolve(spec, definitions)
            arguments[name] = {
                key: value
                for key, value in resolved.items()
                if key in ("type", "enum", "description", "format", "pattern") and value is not None
            }
        described.append(
            {"name": tool.name, "description": tool.description, "arguments": arguments}
        )
    return described


@dataclass
class ToolResult:
    ref: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class RejectedToolCall:
    name: str
    reason: str


def execute_tool_calls(
    db: Session, case_id: uuid.UUID, calls: list[Any], *, max_calls: int
) -> tuple[list[ToolResult], list[RejectedToolCall]]:
    """Validate and run planned tool calls in a read-only transaction."""
    results: list[ToolResult] = []
    rejected: list[RejectedToolCall] = []
    seen: set[str] = set()
    db.execute(text("SET TRANSACTION READ ONLY"))
    for call in calls[: max_calls * 2]:
        name = str(call.get("tool", "")) if isinstance(call, dict) else ""
        tool = TOOLS.get(name)
        if tool is None:
            rejected.append(RejectedToolCall(name[:64] or "(missing)", "unregistered_tool"))
            continue
        raw_arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        arguments = {key: value for key, value in raw_arguments.items() if value not in (None, "")}
        try:
            parsed = tool.args_model.model_validate(arguments)
        except ValidationError:
            rejected.append(RejectedToolCall(name, "invalid_arguments"))
            continue
        signature = f"{name}:{parsed.model_dump_json()}"
        if signature in seen:
            continue
        if len(results) >= max_calls:
            rejected.append(RejectedToolCall(name, "tool_call_limit"))
            continue
        seen.add(signature)
        results.append(
            ToolResult(
                ref=f"T{len(results) + 1}",
                name=name,
                arguments=parsed.model_dump(mode="json", exclude_none=True),
                result={**tool.run(db, case_id, parsed), "scope": "entire_case"},
            )
        )
    return results, rejected
