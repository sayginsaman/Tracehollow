"""Recovery after interrupted evidence writes.

Writes stage a file in ``tmp/``, move it to its final key and then commit the metadata row. An
interruption can therefore leave:

* a staged ``tmp/*.part`` file (crash before promotion) -> deleted after the grace period;
* a promoted file without a metadata row (crash or failed commit after promotion) -> moved to
  ``quarantine/`` after the grace period, never silently deleted;
* a metadata row whose file is missing or altered (storage loss or tampering) -> reported. Rows
  are never deleted automatically; the evidence detail view shows the integrity failure.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.evidence.models import EvidenceObject
from app.evidence.storage import EvidenceStorage

logger = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    staged_removed: int = 0
    orphans_quarantined: list[str] = field(default_factory=list)
    missing_files: list[uuid.UUID] = field(default_factory=list)
    hash_mismatches: list[uuid.UUID] = field(default_factory=list)
    checked_records: int = 0


def reconcile(
    session_factory: sessionmaker[Session],
    storage: EvidenceStorage,
    *,
    grace_seconds: int,
    apply: bool,
    verify_hashes: bool = True,
) -> ReconcileReport:
    report = ReconcileReport()
    cutoff = time.time() - grace_seconds

    tmp_dir = storage.root / "tmp"
    if tmp_dir.is_dir():
        for staged in tmp_dir.glob("*.part"):
            if staged.stat().st_mtime < cutoff:
                report.staged_removed += 1
                if apply:
                    staged.unlink(missing_ok=True)

    with session_factory() as db:
        known: dict[str, uuid.UUID] = {
            key: evidence_id
            for key, evidence_id in db.execute(
                select(EvidenceObject.storage_key, EvidenceObject.id)
            )
        }
        cases_dir = storage.root / "cases"
        if cases_dir.is_dir():
            for path in cases_dir.glob("*/evidence/*"):
                if not path.is_file():
                    continue
                key = path.relative_to(storage.root).as_posix()
                if key in known or path.stat().st_mtime >= cutoff:
                    continue
                report.orphans_quarantined.append(key)
                if apply:
                    _quarantine(storage, path)

        for evidence in db.scalars(select(EvidenceObject).order_by(EvidenceObject.created_at)):
            report.checked_records += 1
            if not verify_hashes:
                if not storage.path_for_key(evidence.storage_key).is_file():
                    report.missing_files.append(evidence.id)
                continue
            result = storage.hash_file(evidence.storage_key)
            if result is None:
                report.missing_files.append(evidence.id)
            elif result != (evidence.sha256, evidence.size_bytes):
                report.hash_mismatches.append(evidence.id)

    logger.info(
        "evidence_reconciled",
        extra={
            "applied": apply,
            "staged_removed": report.staged_removed,
            "orphans": len(report.orphans_quarantined),
            "missing": len(report.missing_files),
            "hash_mismatches": len(report.hash_mismatches),
            "checked": report.checked_records,
        },
    )
    return report


def _quarantine(storage: EvidenceStorage, path: Path) -> None:
    target_dir = storage.root / "quarantine"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    case_part = path.parent.parent.name
    os.replace(path, target_dir / f"{stamp}-{case_part}-{path.name}")
