"""Parser for user-provided WhatsApp chat exports ("Export chat" on Android and iOS).

The export is plain text that WhatsApp formats according to the phone's language and region, so
this parser recognizes *layouts* rather than languages:

* Android: ``31/12/2021, 23:59 - Label: message`` (also ``12/31/21, 11:59 PM - ...``,
  ``31.12.2021 23:59 - ...`` and ISO ``2021-12-31, 23:59 - ...``);
* iOS: ``[31.12.21, 23:59:59] Label: message`` (also with ``AM``/``PM``).

What it keeps and what it refuses to guess:

* **The original text is the evidence.** Every message records its line range and character
  offsets in the decoded export, and the timestamp exactly as written.
* **Date order is never guessed.** ``03/04/2021`` is 3 April or 4 March. The order is inferred only
  when some line proves it (a first number above 12 proves day-first, a second number above 12
  proves month-first). Otherwise the analyst must choose, and nothing is derived until they do.
* **The timezone is never guessed.** Exports carry none. With an explicit timezone, times are
  converted to UTC; with ``unknown``, local times are kept and no UTC instant is claimed. Local
  times that fall into a daylight-saving gap or overlap are flagged instead of resolved.
* **Labels are labels.** The text before ``: `` is whatever name or number the exporting phone
  showed. It is not a verified person, account or phone number, and nothing is merged on it.
* **Unrecognized lines are kept**: text before the first message is a preamble; other lines
  continue the previous message.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PARSER_VERSION = "whatsapp-export-1"

DateOrder = Literal["day_first", "month_first", "year_first"]
DATE_ORDER_CHOICES = ("auto", "day_first", "month_first", "year_first")

_SPACE = r"[ \u00a0\u202f]"
_DATE = r"(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"
_TIME = r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?)"
# 12-hour markers as WhatsApp writes them: AM/PM (a.m./p.m.) and the Turkish ÖÖ/ÖS.
_MERIDIEM = r"(?P<meridiem>[AaPp]\.?" + _SPACE + r"?[Mm]\.?|ÖÖ|ÖS|öö|ös)"
_ANDROID = re.compile(
    r"^\ufeff?\u200e?"
    + _DATE
    + r",?"
    + _SPACE
    + _TIME
    + r"(?:"
    + _SPACE
    + r"?"
    + _MERIDIEM
    + r")?"
    + _SPACE
    + r"[-\u2013]"
    + _SPACE
    + r"(?P<rest>.*)$"
)
_IOS = re.compile(
    r"^\ufeff?\u200e?\["
    + _DATE
    + r",?"
    + _SPACE
    + _TIME
    + r"(?:"
    + _SPACE
    + r"?"
    + _MERIDIEM
    + r")?\]"
    + _SPACE
    + r"(?P<rest>.*)$"
)
# A sender label is short and single-line; longer prefixes are part of a system notice.
_MAX_LABEL = 80

_ANDROID_ATTACHED = re.compile(
    r"^\u200e?(?P<name>[^<>:\"/\\|?*\n]{1,200}\.[A-Za-z0-9]{1,8}) \(file attached\)$"
)
_IOS_ATTACHED = re.compile(r"^\u200e?<attached: (?P<name>[^<>\n]{1,200})>$")
_OMITTED = re.compile(
    r"^\u200e?(?:<Media omitted>|(?:image|video|audio|sticker|GIF|document|Contact card) omitted)$"
)
_EDITED = re.compile(r"\u200e?<This message was edited>\s*$")
_DELETED = {
    "This message was deleted",
    "You deleted this message",
    "\u200eThis message was deleted",
}


class WhatsAppParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class AttachmentRef:
    """A file a message refers to. ``status`` is set when the export's files are matched."""

    reference: str | None
    marker: Literal["file_attached", "attached", "omitted", "member_name"]
    status: Literal["unmatched", "present", "missing", "omitted_by_export"] = "unmatched"
    evidence_id: str | None = None


@dataclass(slots=True)
class ParsedMessage:
    index: int
    layout: Literal["android", "ios"]
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    timestamp_text: str
    date_numbers: tuple[int, int, int]
    time_text: str
    meridiem: str | None
    sender_label: str | None
    kind: Literal["message", "system"]
    classification_basis: str
    text: str
    attachments: list[AttachmentRef] = field(default_factory=list)
    edited_marker: bool = False
    deleted_marker: bool = False
    local_datetime: datetime | None = None
    utc_datetime: datetime | None = None
    time_note: str | None = None


@dataclass(frozen=True, slots=True)
class DateOrderDecision:
    order: DateOrder | None
    basis: Literal["explicit", "inferred", "year_first_layout", "ambiguous", "inconsistent"]
    explanation: str
    proof_line: int | None = None
    samples: tuple[str, ...] = ()


@dataclass(slots=True)
class ParseResult:
    parser_version: str
    layout_counts: dict[str, int]
    messages: list[ParsedMessage]
    preamble_lines: int
    date_order: DateOrderDecision
    timezone: str
    clock: Literal["24h", "12h", "mixed", "none"]
    truncated_at_line: int | None = None
    invalid_timestamps: int = 0
    time_notes: dict[str, int] = field(default_factory=dict)

    @property
    def needs_input(self) -> bool:
        return self.date_order.order is None and bool(self.messages)

    def participants(self) -> list[dict[str, object]]:
        counts = Counter(m.sender_label for m in self.messages if m.kind == "message")
        return [
            {"label": label, "messages": count, "basis": "label shown in the export"}
            for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0] or ""))
            if label is not None
        ]


def validate_timezone(value: str) -> str:
    """``unknown`` or an IANA timezone name known to this installation."""
    value = value.strip()
    if value == "unknown":
        return value
    if not re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+){0,2}", value):
        raise WhatsAppParseError("invalid_timezone", "timezone must be an IANA name or 'unknown'")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise WhatsAppParseError(
            "invalid_timezone", f"'{value}' is not a timezone known to this installation"
        ) from None
    return value


@dataclass(slots=True)
class _Header:
    layout: Literal["android", "ios"]
    date: str
    time: str
    meridiem: str | None
    rest: str
    timestamp_text: str


def _match_header(line: str) -> _Header | None:
    for layout, pattern in (("ios", _IOS), ("android", _ANDROID)):
        match = pattern.match(line)
        if match is None:
            continue
        rest = match.group("rest")
        stamp = line[: match.start("rest")].rstrip(" -\u2013\u00a0\u202f")
        return _Header(
            layout=layout,  # type: ignore[arg-type]
            date=match.group("date"),
            time=match.group("time"),
            meridiem=match.group("meridiem"),
            rest=rest,
            timestamp_text=stamp.lstrip("\ufeff\u200e"),
        )
    return None


def _date_numbers(date: str) -> tuple[int, int, int]:
    parts = re.split(r"[./-]", date)
    return int(parts[0]), int(parts[1]), int(parts[2])


def decide_date_order(
    headers: list[tuple[int, tuple[int, int, int], str]], requested: str
) -> DateOrderDecision:
    """Decide how to read the date numbers, from the export itself or the analyst's choice.

    ``headers`` holds (line number, numbers, raw date text) for every timestamp line.
    """
    if not headers:
        return DateOrderDecision(None, "ambiguous", "The export contains no timestamped messages.")
    year_first = [h for h in headers if len(re.split(r"[./-]", h[2])[0]) == 4]
    day_proof = next((h for h in headers if h[1][0] > 12), None)
    month_proof = next((h for h in headers if h[1][1] > 12), None)
    if requested != "auto":
        contradiction = {"day_first": month_proof, "month_first": day_proof}.get(requested)
        explanation = f"The analyst chose {requested.replace('_', ' ')} date order."
        if contradiction is not None:
            explanation += (
                f" Line {contradiction[0]} ({contradiction[2]}) cannot be read that way and is "
                "reported as an invalid timestamp."
            )
        return DateOrderDecision(
            requested,  # type: ignore[arg-type]
            "explicit",
            explanation,
            proof_line=contradiction[0] if contradiction else None,
        )
    if year_first and len(year_first) == len(headers):
        return DateOrderDecision(
            "year_first",
            "year_first_layout",
            "Every date starts with a four-digit year (year-month-day).",
            proof_line=year_first[0][0],
        )
    samples = tuple(h[2] for h in headers[:5])
    if day_proof and month_proof:
        return DateOrderDecision(
            None,
            "inconsistent",
            f"Line {day_proof[0]} can only be day-first ({day_proof[2]}) but line "
            f"{month_proof[0]} can only be month-first ({month_proof[2]}). Choose the order the "
            "export uses; lines that contradict it are reported as invalid timestamps.",
            samples=(day_proof[2], month_proof[2]),
        )
    if day_proof:
        return DateOrderDecision(
            "day_first",
            "inferred",
            f"Line {day_proof[0]} ({day_proof[2]}) has a first number above 12, so dates are "
            "day-first.",
            proof_line=day_proof[0],
        )
    if month_proof:
        return DateOrderDecision(
            "month_first",
            "inferred",
            f"Line {month_proof[0]} ({month_proof[2]}) has a second number above 12, so dates "
            "are month-first.",
            proof_line=month_proof[0],
        )
    return DateOrderDecision(
        None,
        "ambiguous",
        "No date in the export has a number above 12, so day-first and month-first readings "
        "are both possible. Choose the order the exporting phone used.",
        samples=samples,
    )


def _local_datetime(
    numbers: tuple[int, int, int], time_text: str, meridiem: str | None, order: DateOrder
) -> datetime | None:
    a, b, c = numbers
    if order == "year_first":
        year, month, day = a, b, c
    elif order == "day_first":
        day, month, year = a, b, c
    else:
        month, day, year = a, b, c
    if year < 100:
        # WhatsApp dates two-digit years in the 2000s (the service started in 2009).
        year += 2000
    pieces = re.split(r"[:.]", time_text)
    hour, minute = int(pieces[0]), int(pieces[1])
    second = int(pieces[2]) if len(pieces) > 2 else 0
    if meridiem is not None:
        marker = meridiem.replace(".", "").replace(" ", "").replace("\u202f", "").upper()
        if not 1 <= hour <= 12:
            return None
        pm = marker in ("PM", "ÖS")
        hour = (hour % 12) + (12 if pm else 0)
    try:
        return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001 - local, no zone
    except ValueError:
        return None


def _to_utc(local: datetime, timezone: str) -> tuple[datetime | None, str | None]:
    if timezone == "unknown":
        return None, None
    zone = ZoneInfo(timezone)
    early = local.replace(tzinfo=zone, fold=0)
    late = local.replace(tzinfo=zone, fold=1)

    def exists(candidate: datetime) -> bool:
        return candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local

    if not exists(early) and not exists(late):
        # Skipped by a daylight-saving change: no clock in that zone showed this time.
        return None, "nonexistent_local_time"
    if early.utcoffset() != late.utcoffset():
        # Shown twice (clocks moved back): the export does not say which instant was meant.
        return None, "ambiguous_local_time"
    return early.astimezone(UTC), None


def _classify(
    header: _Header,
) -> tuple[str | None, Literal["message", "system"], str, str]:
    """Return (sender label, kind, basis, body)."""
    rest = header.rest
    separator = rest.find(": ")
    if header.layout == "ios":
        if separator <= 0 or separator > _MAX_LABEL:
            return None, "system", "no_label_separator", rest
        label, body = rest[:separator], rest[separator + 2 :]
        # iOS exports prefix notices from the chat itself (encryption, joins, subject changes)
        # with a left-to-right mark; media placeholders carry it too.
        if body.startswith("\u200e") and not (_IOS_ATTACHED.match(body) or _OMITTED.match(body)):
            return None, "system", "ios_left_to_right_mark", body.lstrip("\u200e")
        return label.lstrip("\u200e"), "message", "label_separator", body
    if separator <= 0 or separator > _MAX_LABEL or "\u201c" in rest[:separator]:
        return None, "system", "no_label_separator", rest
    return rest[:separator], "message", "label_separator", rest[separator + 2 :]


def _attachments(body_lines: list[str], member_names: frozenset[str]) -> list[AttachmentRef]:
    refs: list[AttachmentRef] = []
    for raw in body_lines:
        line = raw.strip()
        if match := _ANDROID_ATTACHED.match(line):
            refs.append(AttachmentRef(match.group("name"), "file_attached"))
        elif match := _IOS_ATTACHED.match(line):
            refs.append(AttachmentRef(match.group("name"), "attached"))
        elif _OMITTED.match(line):
            refs.append(AttachmentRef(None, "omitted", status="omitted_by_export"))
        else:
            # Other languages word the marker differently; an exact file name from the export
            # at the start of the line is still a reference.
            name = line.lstrip("\u200e").split(" (", 1)[0]
            if name in member_names:
                refs.append(AttachmentRef(name, "member_name"))
    return refs


def parse_export(
    text: str,
    *,
    date_order: str = "auto",
    timezone: str = "unknown",
    max_messages: int = 200_000,
    member_names: frozenset[str] = frozenset(),
) -> ParseResult:
    """Parse a decoded export. Offsets are character offsets into ``text`` as given."""
    if date_order not in DATE_ORDER_CHOICES:
        raise WhatsAppParseError(
            "invalid_date_order", f"date_order must be one of {DATE_ORDER_CHOICES}"
        )
    timezone = validate_timezone(timezone)

    lines: list[tuple[int, int, str]] = []  # (line number, char offset, content without newline)
    # Only LF (with an optional CR) ends a line: U+2028 and other separators str.splitlines()
    # honours stay inside a message, so line numbers match what a text editor shows.
    offset = 0
    raw_lines = text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    for number, raw in enumerate(raw_lines, start=1):
        content = raw.removesuffix("\r")
        lines.append((number, offset, content))
        offset += len(raw) + 1

    blocks: list[tuple[_Header, int, int]] = []  # header, first line index, last line index
    preamble = 0
    truncated_at: int | None = None
    for position, (number, _start, content) in enumerate(lines):
        header = _match_header(content)
        if header is not None:
            if len(blocks) >= max_messages:
                truncated_at = number
                break
            blocks.append((header, position, position))
        elif blocks:
            head, first, _last = blocks[-1]
            blocks[-1] = (head, first, position)
        elif content.strip():
            preamble += 1

    if not blocks:
        raise WhatsAppParseError(
            "no_messages",
            "No line matches a WhatsApp export layout (Android 'date, time - label: text' or iOS "
            "'[date, time] label: text'). Check that this is the exported chat text file.",
        )

    decision = decide_date_order(
        [(lines[first][0], _date_numbers(head.date), head.date) for head, first, _last in blocks],
        date_order,
    )
    layouts: Counter[str] = Counter()
    meridiems = {head.meridiem is not None for head, _f, _l in blocks}
    messages: list[ParsedMessage] = []
    invalid = 0
    notes: Counter[str] = Counter()
    for index, (head, first, last) in enumerate(blocks):
        layouts[head.layout] += 1
        label, kind, basis, body_first = _classify(head)
        body_lines = [body_first] + [lines[i][2] for i in range(first + 1, last + 1)]
        body = "\n".join(body_lines)
        edited = bool(_EDITED.search(body))
        if edited:
            body = _EDITED.sub("", body).rstrip()
        char_start = lines[first][1]
        char_end = lines[last][1] + len(lines[last][2])
        message = ParsedMessage(
            index=index,
            layout=head.layout,
            line_start=lines[first][0],
            line_end=lines[last][0],
            char_start=char_start,
            char_end=char_end,
            timestamp_text=head.timestamp_text,
            date_numbers=_date_numbers(head.date),
            time_text=head.time,
            meridiem=head.meridiem,
            sender_label=label,
            kind=kind,
            classification_basis=basis,
            text=body,
            attachments=_attachments(body_lines, member_names) if kind == "message" else [],
            edited_marker=edited,
            deleted_marker=body.strip() in _DELETED,
        )
        if decision.order is not None:
            local = _local_datetime(message.date_numbers, head.time, head.meridiem, decision.order)
            if local is None:
                invalid += 1
                message.time_note = "invalid_for_date_order"
            else:
                message.local_datetime = local
                message.utc_datetime, message.time_note = _to_utc(local, timezone)
            if message.time_note:
                notes[message.time_note] += 1
        messages.append(message)

    clock: Literal["24h", "12h", "mixed", "none"] = (
        "mixed" if meridiems == {True, False} else "12h" if meridiems == {True} else "24h"
    )
    return ParseResult(
        parser_version=PARSER_VERSION,
        layout_counts=dict(layouts),
        messages=messages,
        preamble_lines=preamble,
        date_order=decision,
        timezone=timezone,
        clock=clock,
        truncated_at_line=truncated_at,
        invalid_timestamps=invalid,
        time_notes=dict(notes),
    )


def local_iso(value: datetime | None) -> str | None:
    """Local wall-clock time without an offset, as the export shows it."""
    return value.isoformat(timespec="seconds") if value is not None else None


def looks_like_export(text: str, *, max_lines: int = 500) -> bool:
    """Cheap upload check: does a line near the start match an export layout?"""
    for number, line in enumerate(text.split("\n")):
        if number >= max_lines:
            return False
        if _match_header(line.removesuffix("\r")) is not None:
            return True
    return False
