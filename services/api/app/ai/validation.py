"""Server-side validation of model output.

The model's JSON is untrusted. A reference counts only when it names a block that was actually
in the prompt; an evidence citation counts only when its quote is found in that block's text.
Factual claims without a verified citation are removed, count claims must match the cited
database result, and generated text is scanned for configured secret values. The analyst sees
only what survives, plus an account of what was removed and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.chunking import pointer_at
from app.ai.models import CitationStatus
from app.ai.providers.base import as_list
from app.ai.retrieval import RetrievedChunk
from app.ai.text import locate_quote
from app.ai.tools import ToolResult

MAX_CLAIMS = 20
MAX_CLAIM_CHARS = 1200
MAX_LIMITATIONS = 10
SUPPORT_KINDS = ("fact", "conflict")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
_REDACTED = "[redacted]"


@dataclass
class ValidatedCitation:
    label: str
    ref_type: str
    status: CitationStatus
    quote: str | None = None
    chunk: RetrievedChunk | None = None
    tool: ToolResult | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None
    json_pointer: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == CitationStatus.ACCEPTED


@dataclass
class ValidatedClaim:
    text: str
    kind: str
    citations: list[ValidatedCitation]


@dataclass
class RemovedClaim:
    kind: str
    reason: str
    citation_labels: list[str]


@dataclass
class ValidatedAnswer:
    status: str
    claims: list[ValidatedClaim]
    limitations: list[str]
    removed: list[RemovedClaim] = field(default_factory=list)
    rejected_citations: list[dict[str, str]] = field(default_factory=list)
    secret_redactions: int = 0
    server_notes: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        accepted = sum(
            1 for claim in self.claims for citation in claim.citations if citation.accepted
        )
        return {
            "model_status": self.status,
            "claims_kept": len(self.claims),
            "claims_removed": [removed.__dict__ for removed in self.removed],
            "citations_accepted": accepted,
            "citations_rejected": self.rejected_citations,
            "secret_redactions": self.secret_redactions,
            "server_notes": self.server_notes,
        }


def _numbers(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(str(int(value)) if float(value).is_integer() else str(value))
    elif isinstance(value, str):
        for match in re.findall(r"\d+", value):
            found.add(str(int(match)))
    elif isinstance(value, dict):
        for item in value.values():
            found |= _numbers(item)
    elif isinstance(value, list):
        for item in value:
            found |= _numbers(item)
    return found


def _claim_numbers(text: str) -> set[str]:
    values = set()
    for match in _NUMBER.findall(text):
        normalized = (
            match.replace(",", "").replace(".", "")
            if re.fullmatch(r"\d{1,3}([.,]\d{3})+", match)
            else match
        )
        for part in re.findall(r"\d+", normalized):
            values.add(str(int(part)))
    return values


def _redact(text: str, secrets: list[str]) -> tuple[str, int]:
    count = 0
    for secret in secrets:
        if secret and secret in text:
            count += text.count(secret)
            text = text.replace(secret, _REDACTED)
    return text, count


def _citation(
    raw: Any, evidence: dict[str, RetrievedChunk], tools: dict[str, ToolResult]
) -> ValidatedCitation:
    ref = str(raw.get("ref", "") if isinstance(raw, dict) else "").strip().upper()[:16]
    quote = str(raw.get("quote", "") if isinstance(raw, dict) else "")[:600]
    if ref in tools:
        return ValidatedCitation(ref, "tool", CitationStatus.ACCEPTED, tool=tools[ref])
    chunk = evidence.get(ref)
    if chunk is None:
        return ValidatedCitation(
            ref or "(missing)", "chunk", CitationStatus.REJECTED_UNKNOWN_REFERENCE, quote=quote
        )
    match = locate_quote(chunk.text, quote)
    if match is None:
        return ValidatedCitation(
            ref, "chunk", CitationStatus.REJECTED_QUOTE_NOT_FOUND, quote=quote, chunk=chunk
        )
    exact = chunk.text[match.start : match.end]
    citation = ValidatedCitation(ref, "chunk", CitationStatus.ACCEPTED, quote=exact, chunk=chunk)
    if chunk.kind == "text" and chunk.char_start is not None:
        citation.source_char_start = chunk.char_start + match.start
        citation.source_char_end = chunk.char_start + match.end
    elif chunk.json_locations:
        citation.json_pointer = pointer_at(chunk.json_locations, match.start)
    return citation


def validate_answer(
    raw: dict[str, Any],
    *,
    evidence: dict[str, RetrievedChunk],
    tools: dict[str, ToolResult],
    secrets: list[str],
) -> ValidatedAnswer:
    status = raw.get("status")
    if status not in ("answered", "partially_answered", "insufficient_evidence"):
        status = "insufficient_evidence"
    answer = ValidatedAnswer(status=str(status), claims=[], limitations=[])

    raw_claims = as_list(raw.get("claims"))
    if len(raw_claims) > MAX_CLAIMS:
        answer.server_notes.append(f"Only the first {MAX_CLAIMS} statements were considered.")
    for item in raw_claims[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            continue
        raw_kind = str(item.get("kind", ""))
        kind = (
            raw_kind
            if raw_kind in ("fact", "count", "inference", "conflict", "insufficient")
            else "fact"
        )
        text, redactions = _redact(str(item.get("text", "")).strip()[:MAX_CLAIM_CHARS], secrets)
        raw_citations = as_list(item.get("citations"))
        citations = [_citation(entry, evidence, tools) for entry in raw_citations[:8]]
        labels = [citation.label for citation in citations]
        for citation in citations:
            if not citation.accepted:
                answer.rejected_citations.append(
                    {"label": citation.label, "status": citation.status.value}
                )
        if redactions:
            answer.secret_redactions += redactions
            answer.removed.append(RemovedClaim(kind, "secret_value_detected", labels))
            continue
        if not text:
            continue
        accepted = [citation for citation in citations if citation.accepted]
        if kind in SUPPORT_KINDS and not accepted:
            answer.removed.append(RemovedClaim(kind, "no_verified_citation", labels))
            continue
        if kind == "count":
            tool_citations = [citation for citation in accepted if citation.tool is not None]
            if not tool_citations:
                answer.removed.append(RemovedClaim(kind, "count_without_database_result", labels))
                continue
            allowed = set().union(
                *(
                    _numbers({"result": c.tool.result, "arguments": c.tool.arguments})
                    for c in tool_citations
                    if c.tool
                )
            )
            if not _claim_numbers(text) <= allowed:
                answer.removed.append(RemovedClaim(kind, "number_not_in_database_result", labels))
                continue
        answer.claims.append(ValidatedClaim(text=text, kind=kind, citations=accepted))

    raw_limitations = as_list(raw.get("limitations"))
    for limitation in raw_limitations[:MAX_LIMITATIONS]:
        cleaned, redactions = _redact(str(limitation).strip()[:500], secrets)
        answer.secret_redactions += redactions
        if cleaned:
            answer.limitations.append(cleaned)

    supported = [claim for claim in answer.claims if claim.kind in ("fact", "count", "conflict")]
    if not supported:
        if answer.status != "insufficient_evidence":
            answer.server_notes.append(
                "No statement could be verified against the cited case material."
            )
        answer.status = "insufficient_evidence"
        answer.claims = [
            claim for claim in answer.claims if claim.kind in ("inference", "insufficient")
        ]
        if not any(claim.kind == "insufficient" for claim in answer.claims):
            answer.claims.insert(
                0,
                ValidatedClaim(
                    text="The available case material does not support an answer to this question.",
                    kind="insufficient",
                    citations=[],
                ),
            )
    elif answer.status == "insufficient_evidence" or (
        answer.removed and answer.status == "answered"
    ):
        answer.status = "partially_answered"
    if answer.removed:
        answer.server_notes.append(
            f"{len(answer.removed)} statement(s) were removed because they could not be verified."
        )
    return answer


def render_plain_text(answer: ValidatedAnswer) -> str:
    """Plain-text rendering stored with the message (the UI renders structured claims)."""
    lines = []
    for claim in answer.claims:
        refs = " ".join(f"[{citation.label}]" for citation in claim.citations)
        lines.append(f"{claim.text} {refs}".strip())
    return "\n".join(lines)
