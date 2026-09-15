"""Coverage limitations reported with every answer, computed from the database."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.indexing import active_profile, index_counts
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.queries.models import ConnectorOutcome, ConnectorRun

COMPLETE_OUTCOMES = (ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS)


def case_coverage(db: Session, case_id: uuid.UUID, *, semantic_used: bool) -> dict[str, Any]:
    counts = index_counts(db, case_id)
    profile = active_profile(db)
    incomplete_runs = int(
        db.scalar(
            select(func.count())
            .select_from(ConnectorRun)
            .where(
                ConnectorRun.case_id == case_id,
                (ConnectorRun.outcome.is_(None)) | (ConnectorRun.outcome.not_in(COMPLETE_OUTCOMES)),
            )
        )
        or 0
    )
    synthetic = int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceObject)
            .where(
                EvidenceObject.case_id == case_id,
                EvidenceObject.acquisition_method == AcquisitionMethod.SYNTHETIC_FIXTURE,
            )
        )
        or 0
    )
    notes: list[str] = []
    not_searchable = counts["pending"] + counts["indexing"] + counts["failed"] + counts["canceled"]
    if not_searchable:
        notes.append(
            f"{not_searchable} of {counts['total']} evidence record(s) are not indexed yet or "
            "failed indexing, so their text was not searched."
        )
    if counts["stale"]:
        notes.append(
            f"{counts['stale']} evidence record(s) have an outdated index (older chunking or "
            "embedding configuration) and may be missing from search until the index is rebuilt."
        )
    if not semantic_used:
        notes.append(
            "Semantic search was not available for this answer; only keyword and identifier "
            "search ran."
        )
    if incomplete_runs:
        notes.append(
            f"{incomplete_runs} connector run(s) in this case were partial, failed or canceled; "
            "missing information may exist outside the collected evidence."
        )
    if synthetic:
        notes.append(
            f"{synthetic} evidence record(s) are synthetic fixture data, not observations of "
            "real sources."
        )
    return {
        "index": counts,
        "active_embedding_profile": None
        if profile is None
        else {
            "provider": profile.provider,
            "model": profile.model,
            "dimensions": profile.dimensions,
        },
        "incomplete_connector_runs": incomplete_runs,
        "synthetic_evidence": synthetic,
        "semantic_search_used": semantic_used,
        "notes": notes,
    }
