from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChangeSetStatus(enum.StrEnum):
    # No compatible earlier collection: this one becomes the baseline.
    BASELINE_ESTABLISHED = "baseline_established"
    # Compared with a compatible baseline; nothing meaningful differs within the coverage.
    NO_MEANINGFUL_CHANGE = "no_meaningful_change"
    CHANGES_DETECTED = "changes_detected"
    # The collection failed, was partial, rate limited, truncated or stopped: missing items are
    # unknown and nothing is called removed.
    UNKNOWN = "unknown"
    # The earlier collection used a different connector version, input, parameters or scope.
    BASELINE_INCOMPATIBLE = "baseline_incompatible"


class ChangeKind(enum.StrEnum):
    NEW = "new"
    CHANGED = "changed"
    # Present in the baseline, absent from a *complete* later collection. Absence within the
    # recorded scope only; it does not prove deletion.
    NOT_OBSERVED = "not_observed"
    CONFLICTING = "conflicting"
    # Present in the baseline, absent from an incomplete later collection.
    UNKNOWN = "unknown"


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class ChangeSet(Base):
    """Deterministic comparison of one connector run with its compatible baseline."""

    __tablename__ = "change_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    monitor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monitors.id", ondelete="SET NULL")
    )
    occurrence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monitor_occurrences.id", ondelete="SET NULL")
    )
    query_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_runs.id", ondelete="CASCADE"))
    connector_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_runs.id", ondelete="CASCADE")
    )
    connector_id: Mapped[str] = mapped_column(String(100))
    connector_version: Mapped[str] = mapped_column(String(32))
    baseline_query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL")
    )
    baseline_connector_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_runs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32))
    # Compatibility fingerprint (connector version, input, parameters, scope) of this collection.
    fingerprint: Mapped[str] = mapped_column(String(64))
    coverage_complete: Mapped[bool]
    baseline_coverage_complete: Mapped[bool | None]
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Plain-language limits of this comparison (incompatible keys, incomplete coverage, caps).
    limitations: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    truncated: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("status", ChangeSetStatus), name="status_valid"),
        Index("uq_change_sets_connector_run", "connector_run_id", unique=True),
        Index("ix_change_sets_case_created", "case_id", "created_at"),
        Index("ix_change_sets_monitor_created", "monitor_id", "created_at"),
    )


class ChangeEvent(Base):
    """One item-level difference, with the runs, observations and evidence behind it.

    Observation and evidence references are plain identifiers (no foreign keys) so an event keeps
    naming what it was based on after retention removes a record; readers check whether the record
    still exists and show the limitation when it does not.
    """

    __tablename__ = "change_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    observation_type: Mapped[str] = mapped_column(String(64))
    source_object_id: Mapped[str | None] = mapped_column(String(512))
    field: Mapped[str | None] = mapped_column(String(100))
    previous_value: Mapped[str | None] = mapped_column(Text)
    current_value: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None]
    previous_observation_id: Mapped[uuid.UUID | None]
    current_observation_id: Mapped[uuid.UUID | None]
    previous_evidence_id: Mapped[uuid.UUID | None]
    current_evidence_id: Mapped[uuid.UUID | None]
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("kind", ChangeKind), name="kind_valid"),
        Index("ix_change_events_change_set", "change_set_id"),
        Index("ix_change_events_case_created", "case_id", "created_at"),
    )
