"""Records derived from an imported original by a processing job.

Derived records are ordinary evidence rows: acquisition ``authorized_import``, the original's
import origin, ``derived_from_evidence_id`` pointing at the original, and the processing job and
part name that make a repeated delivery harmless. Their bytes are written to the evidence store
before the recording transaction; if that transaction does not commit, the caller removes them
(and ``reconcile-evidence`` quarantines any file a crash leaves behind).

Processing an original again supersedes what earlier jobs of the same type derived from it: those
rows are deleted in the transaction that records the new results, and their files are removed
after it commits. The original itself is never changed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.models import AiCitation
from app.db.base import utcnow
from app.evidence.importing import sanitize_filename
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.imports.models import ProcessingJob


@dataclass
class StagedParts:
    """Files written for one job attempt, removed again unless the recording commits."""

    storage: EvidenceStorage
    keys: list[str] = field(default_factory=list)

    def discard(self) -> None:
        for key in self.keys:
            self.storage.remove_key(key)
        self.keys.clear()


@dataclass(frozen=True, slots=True)
class DerivedPart:
    evidence_id: uuid.UUID
    storage_key: str
    sha256: str
    size_bytes: int
    kind: str
    content_type: str
    title: str
    part: str
    original_filename: str | None
    metadata: dict[str, Any]


def stage_part(
    staged: StagedParts,
    case_id: uuid.UUID,
    content: bytes,
    *,
    kind: str,
    content_type: str,
    title: str,
    part: str,
    original_filename: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DerivedPart:
    evidence_id = uuid.uuid4()
    key = EvidenceStorage.key_for(case_id, evidence_id)
    stored = staged.storage.store(key, content)
    staged.keys.append(key)
    return DerivedPart(
        evidence_id=evidence_id,
        storage_key=key,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        kind=kind,
        content_type=content_type,
        title=title[:300],
        part=part[:32],
        original_filename=sanitize_filename(original_filename).value if original_filename else None,
        metadata=metadata or {},
    )


def evidence_row(original: EvidenceObject, part: DerivedPart, job_id: uuid.UUID) -> EvidenceObject:
    return EvidenceObject(
        id=part.evidence_id,
        case_id=original.case_id,
        kind=part.kind,
        title=part.title,
        original_filename=part.original_filename,
        content_type=part.content_type[:100],
        size_bytes=part.size_bytes,
        sha256=part.sha256,
        storage_key=part.storage_key,
        acquisition_method=AcquisitionMethod.AUTHORIZED_IMPORT,
        import_origin=original.import_origin or "Derived from an authorized import",
        source_reference=original.source_reference,
        source_published_at=original.source_published_at,
        source_published_at_original=original.source_published_at_original,
        collected_at=utcnow(),
        imported_by_user_id=original.imported_by_user_id,
        description=f"Derived from “{original.title}” by processing.",
        derived_from_evidence_id=original.id,
        processing_job_id=job_id,
        page_part=part.part,
        collection_metadata=part.metadata,
    )


def supersede_previous(
    db: Session, *, original_id: uuid.UUID, job_type: str, current_job_id: uuid.UUID
) -> list[str]:
    """Delete records earlier jobs of this type derived from the original, and anything later
    processing derived from those (text extracted from a PDF attachment); return their keys."""
    rows = list(
        db.execute(
            select(EvidenceObject.id, EvidenceObject.storage_key)
            .join(ProcessingJob, ProcessingJob.id == EvidenceObject.processing_job_id)
            .where(
                ProcessingJob.evidence_id == original_id,
                ProcessingJob.job_type == job_type,
                ProcessingJob.id != current_job_id,
            )
        ).all()
    )
    frontier = [row.id for row in rows]
    seen = set(frontier)
    while frontier:
        children = [
            row
            for row in db.execute(
                select(EvidenceObject.id, EvidenceObject.storage_key).where(
                    EvidenceObject.derived_from_evidence_id.in_(frontier),
                    EvidenceObject.processing_job_id.is_not(None),
                )
            ).all()
            if row.id not in seen
        ]
        frontier = [row.id for row in children]
        seen.update(frontier)
        rows.extend(children)
    if not rows:
        return []
    ids = [row.id for row in rows]
    db.execute(
        update(AiCitation)
        .where(AiCitation.evidence_id.in_(ids))
        .values(quote=None, source_char_start=None, source_char_end=None, json_pointer=None)
    )
    for evidence in db.scalars(select(EvidenceObject).where(EvidenceObject.id.in_(ids))):
        db.delete(evidence)
    return [row.storage_key for row in rows]
