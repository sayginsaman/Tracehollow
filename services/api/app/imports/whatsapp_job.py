"""Processing job for an imported WhatsApp export (``whatsapp_export``).

Reads the stored original (the chat ``.txt`` or the exported ``.zip``), parses it, and records in
one transaction:

* for a ZIP, the chat text file as its own evidence record (byte-exact) and every other file as an
  attachment record; for a plain ``.txt`` the original itself is the chat text;
* one observation per message or system event, citing its line range and character offsets in
  the chat text, keeping the timestamp as written, the local time, and a UTC instant only when
  the analyst gave a timezone;
* attachment references resolved to ``present``, ``missing`` or ``omitted_by_export``.

When the date order cannot be proven from the export and the analyst chose ``auto``, the job stops
in ``needs_input`` and derives nothing.
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, insert

from app.ai import indexing
from app.db.session import session_scope
from app.entities.models import Observation
from app.evidence.models import EvidenceKind, EvidenceObject
from app.evidence.storage import IntegrityError
from app.imports import jobs
from app.imports.archives import (
    ArchiveLimits,
    ArchiveRejectedError,
    ArchiveReport,
    iter_members,
    list_regular_names,
    read_member,
    sniff_content_type,
)
from app.imports.derived import (
    DerivedPart,
    StagedParts,
    evidence_row,
    stage_part,
    supersede_previous,
)
from app.imports.models import ProcessingJobType, ProcessingStatus
from app.imports.whatsapp import (
    ParsedMessage,
    ParseResult,
    WhatsAppParseError,
    local_iso,
    parse_export,
)

OBSERVATION_TYPES = ("whatsapp_message", "whatsapp_system_event")
MAX_PAYLOAD_TEXT = 10_000
_INSERT_BATCH = 1000
_LABEL_NOTE = "Label shown in the export; not a verified identity, account or phone number."


def archive_limits(settings: Any) -> ArchiveLimits:
    return ArchiveLimits(
        max_members=settings.import_archive_max_members,
        max_member_bytes=settings.import_archive_max_member_bytes,
        max_total_bytes=settings.import_archive_max_total_bytes,
        max_ratio=settings.import_archive_max_ratio,
    )


def choose_chat_text(names: list[str]) -> str:
    """The exported chat file: ``_chat.txt`` (iOS), ``WhatsApp Chat*.txt`` (Android), or the
    only text file. Anything else is ambiguous and reported, never guessed."""
    texts = [name for name in names if name.lower().endswith(".txt")]
    ios = [name for name in texts if PurePosixPath(name).name == "_chat.txt"]
    android = [
        name for name in texts if PurePosixPath(name).name.lower().startswith("whatsapp chat")
    ]
    for matches in (ios, android):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise jobs.ProcessingError(
                "multiple_chat_texts",
                "The archive contains several chat text files: " + ", ".join(matches[:10]),
            )
    if len(texts) == 1:
        return texts[0]
    if not texts:
        raise jobs.ProcessingError(
            "chat_text_not_found",
            "The archive contains no .txt file. Export the chat again and import the ZIP or the "
            "chat text file WhatsApp created.",
        )
    raise jobs.ProcessingError(
        "multiple_chat_texts",
        "The archive contains several text files and none is named like a WhatsApp chat: "
        + ", ".join(texts[:10]),
    )


def _load_original(ctx: jobs.ProcessingContext, evidence_id: uuid.UUID) -> EvidenceObject:
    with session_scope(ctx.session_factory) as db:
        original = db.get(EvidenceObject, evidence_id)
        if original is None:
            raise jobs.LeaseLostError
        db.expunge(original)
    return original


def _message_payload(
    message: ParsedMessage, parsed: ParseResult, job_id: uuid.UUID
) -> dict[str, Any]:
    if message.utc_datetime is not None:
        time_basis = "utc_from_analyst_timezone"
    elif message.time_note is not None:
        time_basis = message.time_note
    elif parsed.timezone == "unknown":
        time_basis = "local_time_timezone_unknown"
    else:
        time_basis = "date_order_unresolved"
    return {
        "index": message.index,
        "kind": message.kind,
        "sender_label": message.sender_label,
        "sender_label_note": _LABEL_NOTE if message.sender_label is not None else None,
        "text": message.text[:MAX_PAYLOAD_TEXT],
        "text_truncated": len(message.text) > MAX_PAYLOAD_TEXT,
        "line_start": message.line_start,
        "line_end": message.line_end,
        "char_start": message.char_start,
        "char_end": message.char_end,
        "timestamp_text": message.timestamp_text,
        "local_time": local_iso(message.local_datetime),
        "timezone": parsed.timezone,
        "time_basis": time_basis,
        "date_order": parsed.date_order.order,
        "date_order_basis": parsed.date_order.basis,
        "layout": message.layout,
        "classification_basis": message.classification_basis,
        "attachments": [
            {
                "reference": ref.reference,
                "marker": ref.marker,
                "status": ref.status,
                "evidence_id": ref.evidence_id,
            }
            for ref in message.attachments
        ],
        "edited_marker": message.edited_marker,
        "deleted_marker": message.deleted_marker,
        "processing_job_id": str(job_id),
    }


def _summary(
    parsed: ParseResult,
    *,
    chat_evidence_id: uuid.UUID | None,
    chat_member: str | None,
    report: ArchiveReport | None,
    attachment_parts: list[DerivedPart],
) -> tuple[dict[str, Any], list[str]]:
    refs = [ref for m in parsed.messages for ref in m.attachments]
    referenced = {ref.reference for ref in refs if ref.reference}
    counts = {
        "references": len(refs),
        "present": sum(1 for ref in refs if ref.status == "present"),
        "missing": sum(1 for ref in refs if ref.status == "missing"),
        "omitted_by_export": sum(1 for ref in refs if ref.status == "omitted_by_export"),
        "files_stored": len(attachment_parts),
        "files_not_referenced": sum(
            1
            for part in attachment_parts
            if part.metadata.get("member_name") not in referenced
            and part.metadata.get("base_name") not in referenced
        ),
    }
    local_times = [m.local_datetime for m in parsed.messages if m.local_datetime is not None]
    gaps: list[str] = []
    limitations = [
        "Sender labels are the names or numbers the exporting phone showed. They are not verified "
        "identities, and no account or phone number was derived from them."
    ]
    if parsed.timezone == "unknown":
        limitations.append(
            "No timezone was given: times are the exporting phone's local wall-clock times and are "
            "not placed on the UTC timeline."
        )
    if counts["missing"]:
        gaps.append(f"{counts['missing']} referenced attachment(s) are not in the import")
    if counts["omitted_by_export"]:
        limitations.append(
            f"{counts['omitted_by_export']} message(s) say media was omitted when the chat was "
            "exported; that media was never part of the export."
        )
    if report is not None and report.skipped:
        gaps.append(f"{len(report.skipped)} archive entr(y/ies) were skipped")
    if parsed.invalid_timestamps:
        gaps.append(f"{parsed.invalid_timestamps} timestamp(s) do not fit the chosen date order")
    if parsed.time_notes:
        gaps.append(
            "some local times fall into a daylight-saving change and have no single UTC instant: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(parsed.time_notes.items()))
        )
    if parsed.truncated_at_line is not None:
        gaps.append(f"parsing stopped at line {parsed.truncated_at_line} (message limit)")
    result: dict[str, Any] = {
        "parser_version": parsed.parser_version,
        "layouts": parsed.layout_counts,
        "messages": sum(1 for m in parsed.messages if m.kind == "message"),
        "system_events": sum(1 for m in parsed.messages if m.kind == "system"),
        "participants": parsed.participants()[:50],
        "date_order": {
            "order": parsed.date_order.order,
            "basis": parsed.date_order.basis,
            "explanation": parsed.date_order.explanation,
            "proof_line": parsed.date_order.proof_line,
        },
        "timezone": parsed.timezone,
        "clock": parsed.clock,
        "preamble_lines": parsed.preamble_lines,
        "invalid_timestamps": parsed.invalid_timestamps,
        "time_notes": parsed.time_notes,
        "truncated_at_line": parsed.truncated_at_line,
        "first_local_time": local_iso(min(local_times)) if local_times else None,
        "last_local_time": local_iso(max(local_times)) if local_times else None,
        "chat_text_evidence_id": str(chat_evidence_id) if chat_evidence_id else None,
        "chat_member": chat_member,
        "attachments": counts,
        "skipped_archive_entries": [
            {"name": item.name, "reason": item.reason}
            for item in (report.skipped[:200] if report else [])
        ],
        "gaps": gaps,
        "limitations": limitations,
    }
    return result, gaps


def process(ctx: jobs.ProcessingContext, job: jobs.JobSnapshot, token: uuid.UUID) -> str:
    settings = ctx.settings
    date_order = str(job.options.get("date_order", "auto"))
    timezone = str(job.options.get("timezone", "unknown"))
    original = _load_original(ctx, job.evidence_id)
    try:
        content = ctx.storage.read_verified(
            original.storage_key, original.sha256, original.size_bytes
        )
    except IntegrityError as exc:
        raise jobs.ProcessingError(
            exc.code, "The stored original failed its integrity check."
        ) from None

    limits = archive_limits(settings)
    report: ArchiveReport | None = None
    chat_member: str | None = None
    names: list[str] = []
    if original.kind == EvidenceKind.ARCHIVE:
        report = ArchiveReport()
        try:
            names = list_regular_names(content, limits, report)
            chat_member = choose_chat_text(names)
            chat_bytes = read_member(content, chat_member, limits)
        except ArchiveRejectedError as exc:
            raise jobs.ProcessingError(exc.code, exc.message) from None
        if chat_bytes is None:
            raise jobs.ProcessingError(
                "chat_text_unreadable", f"The chat text file {chat_member} could not be read."
            )
    elif original.kind == EvidenceKind.TEXT:
        chat_bytes = content
    else:
        raise jobs.ProcessingError(
            "unsupported_original",
            "Only a WhatsApp chat .txt file or its .zip export can be parsed.",
        )
    if len(chat_bytes) > settings.import_max_chat_text_bytes:
        raise jobs.ProcessingError(
            "chat_text_too_large",
            f"The chat text is larger than {settings.import_max_chat_text_bytes} bytes.",
        )
    try:
        text = chat_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise jobs.ProcessingError(
            "invalid_encoding", "The chat text is not UTF-8, which WhatsApp exports use."
        ) from None
    member_names = frozenset(names) | frozenset(PurePosixPath(name).name for name in names)
    try:
        parsed = parse_export(
            text,
            date_order=date_order,
            timezone=timezone,
            max_messages=settings.whatsapp_max_messages,
            member_names=member_names - ({chat_member} if chat_member else set()),
        )
    except WhatsAppParseError as exc:
        raise jobs.ProcessingError(exc.code, exc.message) from None

    if parsed.needs_input:
        with jobs.guarded_write(ctx, job.id, token) as (_db, row, _case):
            jobs.finish(
                _db,
                row,
                ProcessingStatus.NEEDS_INPUT,
                result={
                    "parser_version": parsed.parser_version,
                    "layouts": parsed.layout_counts,
                    "messages_detected": len(parsed.messages),
                },
                needs_input={
                    "field": "date_order",
                    "question": "Which date order does this export use?",
                    "basis": parsed.date_order.basis,
                    "explanation": parsed.date_order.explanation,
                    "samples": list(parsed.date_order.samples),
                    "choices": ["day_first", "month_first", "year_first"],
                },
            )
        return str(ProcessingStatus.NEEDS_INPUT)

    jobs.renew(ctx, job.id, token)
    staged = StagedParts(ctx.storage)
    try:
        chat_part: DerivedPart | None = None
        attachment_parts: list[DerivedPart] = []
        by_name: dict[str, str] = {}
        if original.kind == EvidenceKind.ARCHIVE and chat_member is not None:
            chat_part = stage_part(
                staged,
                job.case_id,
                chat_bytes,
                kind=EvidenceKind.TEXT,
                content_type="text/plain; charset=utf-8",
                title=f"{original.title} — chat text",
                part="chat_text",
                original_filename=PurePosixPath(chat_member).name,
                metadata={
                    "derivation": "whatsapp_chat_text",
                    "member_name": chat_member,
                    "parser_version": parsed.parser_version,
                },
            )
            second_pass = ArchiveReport()
            for position, member in enumerate(iter_members(content, limits, second_pass)):
                if member.name == chat_member:
                    continue
                if position % 50 == 49:
                    jobs.renew(ctx, job.id, token)
                kind, media_type = sniff_content_type(member.data)
                part = stage_part(
                    staged,
                    job.case_id,
                    member.data,
                    kind=kind,
                    content_type=media_type,
                    title=f"Attachment: {member.base_name}",
                    part=f"attachment:{len(attachment_parts)}",
                    original_filename=member.base_name,
                    metadata={
                        "derivation": "whatsapp_attachment",
                        "member_name": member.name,
                        "base_name": member.base_name,
                        "media_type_from_bytes": media_type,
                        "note": "Stored as an inert file. It is never rendered or executed.",
                    },
                )
                attachment_parts.append(part)
                by_name.setdefault(member.name, str(part.evidence_id))
                by_name.setdefault(member.base_name, str(part.evidence_id))
            if report is not None:
                known = {(item.name, item.reason) for item in report.skipped}
                report.skipped.extend(
                    item for item in second_pass.skipped if (item.name, item.reason) not in known
                )
        for message in parsed.messages:
            for ref in message.attachments:
                if ref.status == "omitted_by_export" or ref.reference is None:
                    continue
                evidence_id = by_name.get(ref.reference)
                ref.status = "present" if evidence_id else "missing"
                ref.evidence_id = evidence_id

        chat_evidence_id = chat_part.evidence_id if chat_part else original.id
        result, gaps = _summary(
            parsed,
            chat_evidence_id=chat_evidence_id,
            chat_member=chat_member,
            report=report,
            attachment_parts=attachment_parts,
        )
        with jobs.guarded_write(ctx, job.id, token) as (db, row, case):
            fresh_original = db.get(EvidenceObject, job.evidence_id)
            if fresh_original is None:
                raise jobs.LeaseLostError
            superseded = supersede_previous(
                db,
                original_id=job.evidence_id,
                job_type=ProcessingJobType.WHATSAPP_EXPORT,
                current_job_id=job.id,
            )
            for part in ([chat_part] if chat_part else []) + attachment_parts:
                db.add(evidence_row(fresh_original, part, job.id))
            db.flush()
            if chat_part is None:
                # A plain-text original is its own chat text: replace what an earlier job derived.
                db.execute(
                    delete(Observation).where(
                        Observation.evidence_id == original.id,
                        Observation.observation_type.in_(OBSERVATION_TYPES),
                    )
                )
            rows = [
                {
                    "id": uuid.uuid4(),
                    "case_id": job.case_id,
                    "evidence_id": chat_evidence_id,
                    "observation_type": (
                        "whatsapp_message" if message.kind == "message" else "whatsapp_system_event"
                    ),
                    "source_object_id": f"line:{message.line_start}",
                    "payload": _message_payload(message, parsed, job.id),
                    "collected_at": original.collected_at,
                    "event_time": message.utc_datetime,
                    "source_published_at": None,
                    "idempotency_key": f"whatsapp:{chat_evidence_id}:{message.index:07d}",
                }
                for message in parsed.messages
            ]
            for start in range(0, len(rows), _INSERT_BATCH):
                db.execute(insert(Observation), rows[start : start + _INSERT_BATCH])
            if chat_part is not None:
                indexing.mark_evidence_for_indexing(
                    db,
                    settings,
                    case_id=job.case_id,
                    evidence_id=chat_part.evidence_id,
                    ai_mode=case.ai_mode,
                )
            result["observations"] = len(rows)
            status = ProcessingStatus.PARTIAL if gaps else ProcessingStatus.COMPLETED
            jobs.finish(db, row, status, result=result)
        staged.keys.clear()
    except BaseException:
        staged.discard()
        raise
    for key in superseded:
        ctx.storage.remove_key(key)
    return str(status)
