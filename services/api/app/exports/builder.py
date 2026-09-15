"""Case exports (PRD FR-09, Phase 1: JSON and CSV with provenance references and a manifest).

Columns are listed explicitly, so internal fields (password hashes, sessions, storage paths,
lease tokens, worker hostnames) cannot leak into an export by accident. Original evidence bytes
are referenced by ``evidence_id`` and ``sha256`` rather than embedded.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.auth.models import User
from app.cases.models import Case, Note
from app.entities.models import (
    AnalystDecision,
    Entity,
    EntityEvidence,
    EntityIdentifier,
    Observation,
    Relationship,
    RelationshipEvidence,
)
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.exports.csv_safety import neutralize
from app.queries.models import ConnectorOutcome, ConnectorRun, QueryRun, SavedQuery

FORMAT = "tracehollow.case-export"
FORMAT_VERSION = 1
MAX_RECORDS_PER_TABLE = 50_000

# (export table name, model, columns). Columns ending in "_username" are resolved from user ids.
TABLES: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    (
        "entities",
        Entity,
        (
            "id",
            "entity_type",
            "display_name",
            "description",
            "attributes",
            "origin",
            "created_by_query_run_id",
            "created_at",
            "updated_at",
        ),
    ),
    (
        "entity_identifiers",
        EntityIdentifier,
        (
            "id",
            "entity_id",
            "identifier_type",
            "platform",
            "original_value",
            "normalized_value",
            "created_at",
        ),
    ),
    (
        "relationships",
        Relationship,
        (
            "id",
            "source_entity_id",
            "target_entity_id",
            "predicate",
            "origin",
            "review_status",
            "description",
            "valid_from",
            "valid_to",
            "created_by_query_run_id",
            "created_at",
            "updated_at",
        ),
    ),
    (
        "relationship_references",
        RelationshipEvidence,
        ("id", "relationship_id", "stance", "evidence_id", "observation_id", "note", "created_at"),
    ),
    (
        "entity_evidence_links",
        EntityEvidence,
        ("id", "entity_id", "evidence_id", "note", "created_at"),
    ),
    (
        "analyst_decisions",
        AnalystDecision,
        (
            "id",
            "relationship_id",
            "decision_type",
            "previous_value",
            "new_value",
            "rationale",
            "decided_by_user_id",
            "decided_at",
        ),
    ),
    (
        "notes",
        Note,
        (
            "id",
            "entity_id",
            "relationship_id",
            "evidence_id",
            "body",
            "created_by_user_id",
            "created_at",
            "updated_at",
        ),
    ),
    (
        "evidence",
        EvidenceObject,
        (
            "id",
            "kind",
            "title",
            "original_filename",
            "content_type",
            "size_bytes",
            "sha256",
            "acquisition_method",
            "import_origin",
            "source_reference",
            "source_published_at",
            "collected_at",
            "created_at",
            "imported_by_user_id",
            "connector_id",
            "connector_version",
            "query_run_id",
            "connector_run_id",
            "page_index",
            "description",
        ),
    ),
    (
        "observations",
        Observation,
        (
            "id",
            "entity_id",
            "evidence_id",
            "query_run_id",
            "connector_run_id",
            "observation_type",
            "source_object_id",
            "payload",
            "collected_at",
            "event_time",
            "source_published_at",
            "created_at",
        ),
    ),
    (
        "saved_queries",
        SavedQuery,
        (
            "id",
            "name",
            "input_type",
            "input_value",
            "connector_ids",
            "collection_mode",
            "parameters",
            "limits",
            "created_by_user_id",
            "created_at",
            "updated_at",
        ),
    ),
    (
        "query_runs",
        QueryRun,
        (
            "id",
            "saved_query_id",
            "run_number",
            "status",
            "parameters_snapshot",
            "requested_by_user_id",
            "queued_at",
            "started_at",
            "finished_at",
            "cancel_requested_at",
            "error_code",
        ),
    ),
    (
        "connector_runs",
        ConnectorRun,
        (
            "id",
            "query_run_id",
            "position",
            "connector_id",
            "connector_version",
            "status",
            "outcome",
            "pages_completed",
            "items_collected",
            "fetch_attempts",
            "retries",
            "last_error_code",
            "last_error_detail",
            "retry_after_seconds",
            "coverage",
            "coverage_note",
            "quota_usage",
            "started_at",
            "finished_at",
        ),
    ),
)

# Deterministic ordering for tables without a created_at column.
ORDER_BY: dict[str, Any] = {
    "analyst_decisions": AnalystDecision.decided_at,
    "query_runs": QueryRun.queued_at,
    "connector_runs": ConnectorRun.started_at,
}

USER_COLUMNS = {
    "decided_by_user_id": "decided_by",
    "created_by_user_id": "created_by",
    "imported_by_user_id": "imported_by",
    "requested_by_user_id": "requested_by",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def collect(db: Session, case: Case) -> dict[str, list[dict[str, Any]]]:
    usernames: dict[uuid.UUID, str] = {
        user_id: username for user_id, username in db.execute(select(User.id, User.username))
    }
    data: dict[str, list[dict[str, Any]]] = {
        "case": [
            {
                "id": str(case.id),
                "title": case.title,
                "purpose": case.purpose,
                "scope": case.scope,
                "tags": list(case.tags),
                "status": case.status,
                "created_by": usernames.get(case.created_by_user_id)
                if case.created_by_user_id
                else None,
                "created_at": case.created_at.isoformat(),
                "updated_at": case.updated_at.isoformat(),
                "archived_at": case.archived_at.isoformat() if case.archived_at else None,
            }
        ]
    }
    for name, model, columns in TABLES:
        count = (
            db.scalar(select(func.count()).select_from(model).where(model.case_id == case.id)) or 0
        )
        if count > MAX_RECORDS_PER_TABLE:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="export_too_large")
        order = ORDER_BY.get(name, getattr(model, "created_at", None))
        rows = db.scalars(select(model).where(model.case_id == case.id).order_by(order, model.id))
        records = []
        for row in rows:
            record: dict[str, Any] = {}
            for column in columns:
                value = getattr(row, column)
                if column in USER_COLUMNS:
                    record[USER_COLUMNS[column]] = usernames.get(value) if value else None
                else:
                    record[column] = _plain(value)
            if name == "evidence":
                record["synthetic"] = row.acquisition_method == AcquisitionMethod.SYNTHETIC_FIXTURE
            records.append(record)
        data[name] = records
    return data


def build_manifest(
    db: Session, case: Case, data: dict[str, list[dict[str, Any]]], schema_revision: str
) -> dict[str, Any]:
    evidence = data.get("evidence", [])
    methods: dict[str, int] = {}
    for item in evidence:
        methods[item["acquisition_method"]] = methods.get(item["acquisition_method"], 0) + 1
    run_numbers = {item["id"]: item["run_number"] for item in data.get("query_runs", [])}
    gaps = [
        {
            "query_run_id": item["query_run_id"],
            "run_number": run_numbers.get(item["query_run_id"]),
            "connector_id": item["connector_id"],
            "status": item["status"],
            "outcome": item["outcome"],
            "coverage_note": item["coverage_note"],
        }
        for item in data.get("connector_runs", [])
        if item["outcome"] not in (ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS)
    ]
    collected = [item["collected_at"] for item in evidence if item.get("collected_at")]
    published = [
        item["source_published_at"] for item in evidence if item.get("source_published_at")
    ]
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": {
            "application": "tracehollow",
            "api_version": __version__,
            "schema_revision": schema_revision,
        },
        "case_id": str(case.id),
        "record_counts": {name: len(records) for name, records in data.items()},
        "source_dates": {
            "earliest_collected_at": min(collected) if collected else None,
            "latest_collected_at": max(collected) if collected else None,
            "earliest_source_published_at": min(published) if published else None,
            "latest_source_published_at": max(published) if published else None,
        },
        "acquisition_methods": methods,
        "synthetic_data_present": methods.get(AcquisitionMethod.SYNTHETIC_FIXTURE, 0) > 0,
        "coverage_gaps": gaps,
        "ai_generated_content": "none; AI features are not implemented in this version",
        "semantics": {
            "timestamps": (
                "UTC ISO 8601. collected_at = retrieval or import time; created_at = processing "
                "time; source_published_at and event_time come from the source when known."
            ),
            "sha256": (
                "Detects changes to stored bytes. It does not prove authorship or authenticity."
            ),
            "origins": "observed | deterministic_derivation | ai_suggestion | analyst_assertion",
            "candidate_accounts": (
                "Accounts from connectors are candidates, not identity assertions."
            ),
        },
        "evidence_content_included": False,
        "redaction": "No redaction was applied. This file contains case content.",
        "excluded": [
            "user password hashes, sessions and tokens",
            "application secrets and configuration",
            "internal storage paths, leases and worker details",
            "original evidence bytes (referenced by evidence id and sha256)",
        ],
    }


def _canonical(data: Any) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")


def build_json_export(manifest: dict[str, Any], data: dict[str, list[dict[str, Any]]]) -> bytes:
    manifest = {**manifest, "data_sha256": hashlib.sha256(_canonical(data)).hexdigest()}
    document = {"manifest": manifest, "data": data}
    return json.dumps(document, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")


def _csv_bytes(records: Sequence[dict[str, Any]], columns: Sequence[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for record in records:
        row = []
        for column in columns:
            value = record.get(column)
            if isinstance(value, dict | list):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)
            row.append(neutralize(value))
        writer.writerow(row)
    # UTF-8 with BOM so spreadsheet applications display non-ASCII (e.g. Turkish) text correctly.
    return "﻿".encode() + buffer.getvalue().encode("utf-8")


def build_csv_export(manifest: dict[str, Any], data: dict[str, list[dict[str, Any]]]) -> bytes:
    files: dict[str, bytes] = {}
    for name, records in data.items():
        columns = list(records[0].keys()) if records else _columns_for(name)
        files[f"{name}.csv"] = _csv_bytes(records, columns)
    manifest = {
        **manifest,
        "csv": {
            "encoding": "UTF-8 with byte order mark",
            "formula_neutralization": "Cells starting with = + - @ tab or carriage return are "
            "prefixed with a single quote.",
            "nested_values": "JSON-encoded",
        },
        "files": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "records": len(data[name[:-4]])}
            for name, content in files.items()
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default),
        )
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _columns_for(name: str) -> list[str]:
    if name == "case":
        return [
            "id",
            "title",
            "purpose",
            "scope",
            "tags",
            "status",
            "created_by",
            "created_at",
            "updated_at",
            "archived_at",
        ]
    for table, _model, columns in TABLES:
        if table == name:
            result = [USER_COLUMNS.get(column, column) for column in columns]
            if name == "evidence":
                result.append("synthetic")
            return result
    return []
