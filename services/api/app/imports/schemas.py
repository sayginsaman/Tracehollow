from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.schemas import EvidenceOut

DateOrderChoice = Literal["auto", "day_first", "month_first", "year_first"]
OcrMode = Literal["off", "if_needed", "always"]


class DerivedEvidenceOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    page_part: str | None
    size_bytes: int
    content_type: str


class ProcessingJobOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    evidence_id: uuid.UUID
    job_type: str
    status: str
    options: dict[str, Any]
    result: dict[str, Any]
    # Present while the job waits for an analyst decision (for example the date order).
    needs_input: dict[str, Any] | None
    attempts: int
    error_code: str | None
    error_detail: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ProcessingJobDetail(ProcessingJobOut):
    derived_evidence: list[DerivedEvidenceOut] = Field(default_factory=list)
    derived_evidence_total: int = 0
    observation_count: int = 0


class ImportAccepted(BaseModel):
    evidence: EvidenceOut
    job: ProcessingJobOut
    filename_sanitized: bool
    duplicate_of: list[uuid.UUID]


class ProcessingInputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_order: Literal["day_first", "month_first", "year_first"]
    # IANA timezone name or "unknown"; omitted keeps the timezone chosen at import.
    timezone: Annotated[str | None, Field(max_length=64)] = None


class ProcessingStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: Literal["whatsapp_export", "document_text"]
    date_order: DateOrderChoice = "auto"
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "unknown"
    ocr: OcrMode = "if_needed"
