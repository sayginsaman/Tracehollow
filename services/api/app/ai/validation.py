"""Server-side validation of model output.

The model's JSON is untrusted, and a verified citation only shows that a passage exists. Four
further things are checked here, deterministically, from the structured fields the model must
supply with every factual claim (subject, attribute, value, as_of):

1. **Support** — the asserted value must actually appear in a cited passage (or in the cited
   database result). A claim that asserts what a record does *not* say, including an absence,
   has no support and is removed.
2. **Applicability** — when the question names an identifier (a domain, account, address, hash),
   a claim about a different identifier of the same kind cannot answer it. Such claims are kept
   as context and marked as not answering the question; they never make the answer look answered.
3. **Contradiction and change** — claims about the same subject and attribute whose values differ
   are merged into one `conflict` claim citing every side, with each record and its dates. When
   the values carry different `as_of` dates the difference is a change over time, not a
   contradiction, and both facts are kept with a note.
4. **Answer status** — computed from what survived: `insufficient_evidence` when no supported,
   applicable claim answers the question, `partially_answered` when something is still missing.

The analyst sees only what survives, plus an account of what was removed and why.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.ai.chunking import pointer_at
from app.ai.models import CitationStatus
from app.ai.providers.base import as_list
from app.ai.retrieval import RetrievedChunk
from app.ai.text import extract_identifiers, fold_for_search, locate_quote
from app.ai.tools import ToolResult

MAX_CLAIMS = 20
MAX_ABOUT_CHARS = 200
# Values shorter than this are compared by folded substring; longer ones also fuzzily.
SHORT_VALUE = 4
# The server-side checks an answer passes before it is shown, recorded with every evaluation run
# so a published result states what was enforced when it was produced.
DETERMINISTIC_CHECKS = (
    "citation_resolves_to_this_case",
    "quote_found_in_hash_verified_original",
    "value_present_in_cited_evidence",
    "subject_present_in_cited_evidence",
    "subject_compared_with_question_identifiers",
    "count_number_present_in_database_result",
    "conflict_needs_two_verified_sources",
    "differences_grouped_and_disclosed_with_every_side",
    "status_computed_after_validation",
    "secret_values_removed",
)

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DIGITS = re.compile(r"\d+")
# A date inside a longer value, such as the date part of a timestamp.
_DATE_IN_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
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
class ClaimAbout:
    """What the model says a claim is about; the server verifies it, never trusts it."""

    subject: str = ""
    attribute: str = ""
    value: str = ""
    as_of: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "attribute": self.attribute,
            "value": self.value,
            "as_of": self.as_of,
        }


@dataclass
class ValidatedClaim:
    text: str
    kind: str
    citations: list[ValidatedCitation]
    about: ClaimAbout = field(default_factory=ClaimAbout)
    # False when the claim is supported but does not answer the question that was asked.
    answers_question: bool = True
    # "question_subject", "other_subject" or "unspecified" (no comparable identifier).
    applicability: str = "unspecified"
    # For a disclosed difference: "disagreement" (comparable time) or "change_over_time".
    difference_type: str = ""


@dataclass
class RemovedClaim:
    kind: str
    reason: str
    citation_labels: list[str]
    # Kept for audit and human review of the filter itself; never shown as an answer.
    text: str = ""
    about: dict[str, str] = field(default_factory=dict)


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
            "status": self.status,
            "claims_answering_the_question": sum(
                1
                for claim in self.claims
                if claim.kind in ("fact", "count", "conflict") and claim.answers_question
            ),
            "claims_shown_as_context": sum(
                1
                for claim in self.claims
                if claim.kind in ("fact", "count", "conflict") and not claim.answers_question
            ),
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
    question: str,
    evidence: dict[str, RetrievedChunk],
    tools: dict[str, ToolResult],
    secrets: list[str],
) -> ValidatedAnswer:
    answer = ValidatedAnswer(status="insufficient_evidence", claims=[], limitations=[])
    question_identifiers = _identifier_map(question)
    question_years = _years(question)
    recast: Counter[str] = Counter()

    partially_verified = 0
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
        about = _about(item.get("about"), secrets)
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
        excerpt = text[:300]
        if kind in SUPPORT_KINDS and not accepted:
            answer.removed.append(
                RemovedClaim(kind, "no_verified_citation", labels, excerpt, about=about.as_dict())
            )
            continue
        if kind == "conflict" and len(_distinct_sources(accepted)) < 2:
            if len(set(labels)) == 1:
                # One side of a difference, written as its own "conflict" claim: it is what one
                # record states. Checked as that, and compared with the other side by the server.
                kind = "fact"
                recast["conflict"] += 1
            else:
                # It cited several records but only one verified: a one-sided fragment.
                answer.removed.append(
                    RemovedClaim(
                        kind,
                        "conflict_without_two_verified_sources",
                        labels,
                        excerpt,
                        about=about.as_dict(),
                    )
                )
                continue
        if accepted and len(accepted) < len(citations):
            partially_verified += 1
        if kind == "count":
            tool_citations = [citation for citation in accepted if citation.tool is not None]
            passages = [citation for citation in accepted if citation.chunk is not None]
            allowed = set().union(
                *(
                    _numbers({"result": c.tool.result, "arguments": c.tool.arguments})
                    for c in tool_citations
                    if c.tool
                )
            )
            if not tool_citations or not _claim_numbers(text) <= allowed:
                if not passages:
                    reason = (
                        "count_without_database_result"
                        if not tool_citations
                        else "number_not_in_database_result"
                    )
                    answer.removed.append(
                        RemovedClaim(kind, reason, labels, excerpt, about=about.as_dict())
                    )
                    continue
                # Not a count of the case: a number a record states. It keeps only its passages
                # and must pass the same checks as any statement; it is never shown as a count.
                kind = "fact"
                accepted = passages
                recast["count"] += 1
        if kind == "fact" and not _value_supported(about.value, accepted):
            # A verified citation only proves the passage exists; the asserted value must be in it.
            answer.removed.append(
                RemovedClaim(
                    kind, "value_not_in_cited_evidence", labels, excerpt, about=about.as_dict()
                )
            )
            continue
        if kind in ("fact", "conflict") and not _subject_supported(about.subject, accepted):
            # The passage must be about the subject the claim names, not a neighbouring one.
            answer.removed.append(
                RemovedClaim(
                    kind, "subject_not_in_cited_evidence", labels, excerpt, about=about.as_dict()
                )
            )
            continue
        if kind == "fact" and _dated_event_mismatch(about, text, accepted):
            # A date quoted as one event (created) asserted as another (expires).
            answer.removed.append(
                RemovedClaim(
                    kind, "attribute_not_in_cited_evidence", labels, excerpt, about=about.as_dict()
                )
            )
            continue
        denied = False
        if kind == "fact" and not _negative(about.value):
            text_denies = _negative(_clause_with(text, about.value))
            if not text_denies and any(
                _negative(_clause_with(c.quote or "", about.value, strict=True)) for c in accepted
            ):
                # The claim states a value that the sentence it quotes denies.
                answer.removed.append(
                    RemovedClaim(
                        kind,
                        "value_negated_in_cited_evidence",
                        labels,
                        excerpt,
                        about=about.as_dict(),
                    )
                )
                continue
            # "X is not associated with Y" reports that Y does not apply. It is true, but it
            # cannot answer which value does.
            denied = text_denies
        applicability = _applicability(question_identifiers, about.subject)
        if (
            applicability != "other_subject"
            and kind == "fact"
            and _other_period(question_years, about, accepted)
        ):
            applicability = "other_period"
        answers_question = bool(item.get("answers_question", True))
        if applicability in ("other_subject", "other_period"):
            answers_question = False
        if denied and answers_question:
            answers_question = False
            recast["denied"] += 1
        answer.claims.append(
            ValidatedClaim(
                text=text,
                kind=kind,
                citations=accepted,
                about=about,
                answers_question=answers_question,
                applicability=applicability,
            )
        )

    raw_limitations = as_list(raw.get("limitations"))
    for limitation in raw_limitations[:MAX_LIMITATIONS]:
        cleaned, redactions = _redact(str(limitation).strip()[:500], secrets)
        answer.secret_redactions += redactions
        if cleaned:
            answer.limitations.append(cleaned)

    if recast["conflict"]:
        answer.server_notes.append(
            f"{recast['conflict']} statement(s) labelled as a conflict cited a single record; each "
            "was checked as what that record states, and the server compared the records itself."
        )
    if recast["count"]:
        answer.server_notes.append(
            f"{recast['count']} number(s) labelled as a count of the case were not in any cited "
            "database result; each was checked as a number its cited record states, not as a count."
        )
    if recast["denied"]:
        answer.server_notes.append(
            f"{recast['denied']} statement(s) say that a value does not apply; they are shown as "
            "context because they cannot answer which value does."
        )
    _disclose_differences(answer)
    _finish(answer, partially_verified)
    return answer


def _finish(answer: ValidatedAnswer, partially_verified: int) -> None:
    """Compute the status from what survived and explain every intervention."""
    answering = [
        claim
        for claim in answer.claims
        if claim.kind in ("fact", "count", "conflict") and claim.answers_question
    ]
    context = [
        claim
        for claim in answer.claims
        if claim.kind in ("fact", "count", "conflict") and not claim.answers_question
    ]
    off_subject = [claim for claim in context if claim.applicability == "other_subject"]
    off_period = [claim for claim in context if claim.applicability == "other_period"]
    if not answering:
        answer.status = "insufficient_evidence"
        if not any(claim.kind == "insufficient" for claim in answer.claims):
            answer.claims.append(
                ValidatedClaim(
                    text="The available case material does not answer this question.",
                    kind="insufficient",
                    citations=[],
                    answers_question=False,
                )
            )
    elif any(claim.kind == "insufficient" for claim in answer.claims) or answer.removed or context:
        answer.status = "partially_answered"
    else:
        answer.status = "answered"
    if off_subject:
        answer.server_notes.append(
            f"{len(off_subject)} statement(s) are about another subject than the question and are "
            "shown only as context."
        )
    if off_period:
        answer.server_notes.append(
            f"{len(off_period)} statement(s) are about another period than the question names and "
            "are shown only as context."
        )
    if len(context) > len(off_subject) + len(off_period):
        answer.server_notes.append(
            f"{len(context) - len(off_subject) - len(off_period)} statement(s) are shown as "
            "context; they do not answer the question that was asked."
        )
    if answer.removed:
        reasons = sorted({removed.reason for removed in answer.removed})
        answer.server_notes.append(
            f"{len(answer.removed)} statement(s) were removed because they could not be verified "
            f"({', '.join(reasons)})."
        )
        if any(removed.reason == "value_not_in_cited_evidence" for removed in answer.removed):
            answer.server_notes.append(
                "A removed statement asserted something its cited passage does not support."
            )
    if partially_verified:
        answer.server_notes.append(
            f"{partially_verified} statement(s) are shown with only the citations that could be "
            "verified; their other references were rejected."
        )


# -- deterministic checks ------------------------------------------------------------------------


def _about(raw: Any, secrets: list[str]) -> ClaimAbout:
    if not isinstance(raw, dict):
        return ClaimAbout()

    def field_value(name: str) -> str:
        cleaned, _ = _redact(str(raw.get(name, "")).strip()[:MAX_ABOUT_CHARS], secrets)
        return cleaned

    return ClaimAbout(
        subject=field_value("subject"),
        attribute=field_value("attribute"),
        value=field_value("value"),
        as_of=field_value("as_of"),
    )


def _identifier_map(text: str) -> dict[str, set[str]]:
    """Normalized identifiers in ``text``, grouped by kind (domain, email, ip, hash, ...)."""
    found: dict[str, set[str]] = {}
    for key in extract_identifiers(text):
        kind, _, value = key.partition(":")
        if value:
            found.setdefault(kind, set()).add(value)
    return found


def _applicability(question_identifiers: dict[str, set[str]], subject: str) -> str:
    """Whether a claim's subject is the subject the question asked about.

    Only exact, normalized identifiers of the same kind are compared: a similar name is never
    treated as the same subject, and a missing identifier leaves the question open rather than
    discarding the claim.
    """
    if not question_identifiers or not subject.strip():
        return "unspecified"
    subject_identifiers = _identifier_map(subject)
    shared = set(question_identifiers) & set(subject_identifiers)
    if not shared:
        return "unspecified"
    for kind in shared:
        if question_identifiers[kind] & subject_identifiers[kind]:
            return "question_subject"
    return "other_subject"


# Negation in English and Turkish, matched on accent-folded text. Turkish negative verb forms are
# matched by their suffix; the aorist negative (-maz/-mez) is left out because it also ends common
# surnames such as Yılmaz.
_NEGATION = re.compile(
    r"\b(not|no|never|none|neither|nor|without|cannot|degil\w*|yok|yoktur|yoktu|hic|asla)\b"
    r"|n't\b"
    r"|\b\w{2,}(m[iu]yor|madi|medi|mamis|memis|mamakta|memekte)\w*\b"
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _negative(text: str) -> bool:
    return bool(text) and _NEGATION.search(fold_for_search(text)) is not None


# A sentence ends at a newline, or at . ! ? ; followed by the end or by a capital letter, so that
# "99.1", "ornek.example" and "Deniz Tedarik A.Ş. kurumuna" do not end one.
_SENTENCE_END = re.compile(r"\n|[.!?;](?=\s*$|\s+[A-ZÇĞİÖŞÜ])")


def _clause_with(text: str, value: str, *, strict: bool = False) -> str:
    """The sentence of ``text`` that contains ``value``.

    When the value is not found, the whole text is returned, or nothing when ``strict``: a
    negation elsewhere in a quote says nothing about a value that is not in it.
    """
    folded_text, folded_value = fold_for_search(text), fold_for_search(value)
    position = folded_text.find(folded_value) if folded_value else -1
    if position < 0:
        return "" if strict else text
    start = 0
    end = len(text)
    for match in _SENTENCE_END.finditer(text):
        if match.end() <= position:
            start = match.end()
        elif match.start() >= position + len(folded_value):
            end = match.start()
            break
    return text[start:end]


# What a date is the date of, in English and Turkish, matched on accent-folded text.
_DATE_EVENTS = {
    "created": (
        r"\b(created?|creation|registered|registration date|tescil\w*|olusturul\w*|kuruldu)\b"
    ),
    "expires": (
        r"\b(expir\w*|expiry|valid_to|valid until|until|sona er\w*|bitis\w*|gecerlilik sonu)\b"
    ),
    "updated": r"\b(updated?|modified|last changed|guncellen\w*|degistiril\w*)\b",
    "opened": r"\b(opened|launch\w*|went live|kullanima ac\w*|acildi|acti)\b",
    "closed": r"\b(closed|shut down|discontinued|kapatil\w*|kapandi|kapatti)\b",
    "published": r"\b(published|announced|duyur\w*|yayimla\w*|bildirdi)\b",
}


def _date_events(text: str) -> set[str]:
    folded = fold_for_search(text or "")
    return {event for event, pattern in _DATE_EVENTS.items() if re.search(pattern, folded)}


def _dated_event_mismatch(about: ClaimAbout, text: str, citations: list[ValidatedCitation]) -> bool:
    """Whether a date is asserted as a different event from the one its quoted sentence dates.

    "Expires on 2025-11-04" quoted from ``/created: "2025-11-04"`` states a creation date as an
    expiry: the value is in the quote, the subject is right, and the claim is still false.
    Decided only when both the claim and the quoted sentence name an event.
    """
    if not _YEAR.search(about.value):
        return False
    claimed = _date_events(f"{about.attribute} {text}")
    if not claimed:
        return False
    quoted: set[str] = set()
    for citation in citations:
        quoted |= _date_events(_clause_with(citation.quote or "", about.value, strict=True))
    return bool(quoted) and not claimed & quoted


def _years(text: str) -> set[str]:
    return set(_YEAR.findall(text or ""))


def _other_period(
    question_years: set[str], about: ClaimAbout, citations: list[ValidatedCitation]
) -> bool:
    """Whether the claim is about a different year from every year the question names.

    Decided only when both sides name a year: the claim's stated period and quoted excerpts are
    read, and a claim that names none is left alone.
    """
    if not question_years:
        return False
    claim_years = _years(about.as_of)
    for citation in citations:
        claim_years |= _years(citation.quote or "")
    return bool(claim_years) and not claim_years & question_years


def _subject_supported(subject: str, citations: list[ValidatedCitation]) -> bool:
    """Whether a cited passage is about the subject the claim names.

    Checked only when the subject contains an identifier, and then exactly: a passage about
    ``destek.ornek.example`` does not become a passage about ``ornek.example`` because one name
    ends with the other. A subject with no identifier (an organisation, a role, "the portal")
    cannot be checked this way and is left to the reviewer.
    """
    wanted = set(extract_identifiers(subject))
    if not wanted:
        return True
    for citation in citations:
        if citation.tool is not None:
            return True
        chunk = citation.chunk
        if chunk is None:
            continue
        if wanted & set(extract_identifiers(chunk.text)):
            return True
    return False


def _value_supported(value: str, citations: list[ValidatedCitation]) -> bool:
    """The asserted value must be inside what the claim cites: its quoted excerpts or database data.

    The excerpt, not the whole passage: a value that appears somewhere else in a long passage is
    not shown to be what the quoted words say, and the prompt requires the quote to contain it.
    A database result is read the same way, so citing one does not vouch for any value. A list
    ("ns1.example, ns2.example") is supported when every item is. Short values must stand as a
    whole token, so "1" is not found inside "2026-09-10".
    """
    needle = value.strip()
    if not needle:
        return False
    texts = [citation.quote for citation in citations if citation.quote]
    texts += [
        json.dumps(
            {"result": citation.tool.result, "arguments": citation.tool.arguments},
            ensure_ascii=False,
        )
        for citation in citations
        if citation.tool is not None
    ]
    folded_texts = [fold_for_search(text) for text in texts]
    for item in _list_items(needle):
        folded = fold_for_search(item)
        if not folded:
            return False
        if not any(_contains_value(text, folded) for text in folded_texts) and not any(
            _date_match(item, text) for text in texts
        ):
            return False
    return True


def _list_items(value: str) -> list[str]:
    """The items of a listed value, or the value itself when it is not a list."""
    items = [item.strip() for item in re.split(r"\s*(?:[,;]|\band\b|\bve\b|&)\s*", value)]
    items = [item for item in items if item]
    return items if len(items) > 1 else [value]


def _contains_value(haystack: str, needle: str) -> bool:
    """Whether ``needle`` occurs as a whole value, not inside a longer name or number."""
    pattern = rf"(?<![0-9a-z._@-]){re.escape(needle)}(?![0-9a-z_@-])"
    return re.search(pattern, haystack) is not None


def _date_match(value: str, text: str) -> bool:
    """A date written differently (``1 Eylül 2026``) still matches ``2026-09-01`` in the source."""
    numbers = {int(item) for item in _DIGITS.findall(value)}
    if not numbers:
        return False
    for match in _ISO_DATE.finditer(text):
        parts = {int(part) for part in match.groups()}
        if numbers <= parts | {int(str(part)) for part in match.groups()}:
            return True
    return False


def _distinct_sources(citations: list[ValidatedCitation]) -> set[str]:
    """Which records a set of citations comes from (a tool result counts as one record)."""
    sources = set()
    for citation in citations:
        if citation.chunk is not None:
            sources.add(f"evidence:{citation.chunk.evidence_id}")
        elif citation.tool is not None:
            sources.add(f"tool:{citation.label}")
    return sources


def _subject_key(subject: str) -> str:
    identifiers = _identifier_map(subject)
    if identifiers:
        return "|".join(
            sorted(f"{kind}:{value}" for kind, values in identifiers.items() for value in values)
        )
    return fold_for_search(subject)


def _record_label(claim: ValidatedClaim) -> str:
    for citation in claim.citations:
        chunk = citation.chunk
        if chunk is None:
            continue
        when = chunk.source_published_at_original or (
            chunk.source_published_at.date().isoformat() if chunk.source_published_at else None
        )
        moment = (
            f"published {when}" if when else f"collected {chunk.collected_at.date().isoformat()}"
        )
        return f"{chunk.evidence_title} ({moment})"
    for citation in claim.citations:
        if citation.tool is not None:
            return f"database result {citation.label}"
    return "unattributed record"


DIFFERENCE_EXPLANATIONS = {
    "change_over_time": (
        " The records give different times for these values, so this is a change over time "
        "rather than a disagreement; compare the dates."
    ),
    "disagreement": " The records cover the same time or give none, so they disagree.",
    "undetermined": (
        " Neither record says which period its value covers, and they were published at "
        "different times, so this may be a change rather than a disagreement; compare the dates."
    ),
}
DIFFERENCE_NOTES = {
    "change_over_time": "a change over time",
    "disagreement": "a disagreement",
    "undetermined": "a difference whose cause the records do not settle",
}


def _record_time(claim: ValidatedClaim) -> str:
    """The publication date of the first cited record, or ``""`` when it has none."""
    for citation in claim.citations:
        chunk = citation.chunk
        if chunk is not None and chunk.source_published_at is not None:
            return chunk.source_published_at.date().isoformat()
    return ""


def _period_key(value: str) -> str:
    """The period an ``as_of`` names, so one wording of a date is not read as a different time.

    "9 Eylül 2026", "2026-09-09" and "2026-09-09T15:00:00+03:00" are the same day and must not be
    reported as a change over time. Dates are compared by the numbers they contain, so a record
    that names only a day still matches the same day written in full. When two wordings cannot be
    told apart this way they count as the same period, which yields "disagreement": the weaker
    statement of the two.
    """
    text = value.strip()
    if not text:
        return ""
    # A timestamp carries an hour and an offset that say nothing about which day it is, so when a
    # full date is present only that date counts.
    iso = _DATE_IN_TEXT.search(text)
    found = iso.groups() if iso else _DIGITS.findall(text)
    numbers = sorted({int(item) for item in found})
    return ",".join(str(number) for number in numbers) if numbers else fold_for_search(text)


def _difference_type(sides: list[list[ValidatedClaim]]) -> str:
    """Whether differing values are a change over time, a disagreement, or cannot be told apart.

    Only the period each record gives for its own value settles this. A publication date is
    metadata about the record, not about when the value held, so differing publication dates
    alone leave the question open and are reported as open rather than resolved either way.
    Several records giving one value form one side; a side's periods are all of theirs.
    """
    stated = [{_period_key(claim.about.as_of) for claim in side} - {""} for side in sides]
    if all(stated):
        shared = any(stated[i] & stated[j] for i in range(len(sides)) for j in range(i))
        return "disagreement" if shared else "change_over_time"
    published = [{_record_time(claim) for claim in side} - {""} for side in sides]
    if all(published) and not any(
        published[i] & published[j] for i in range(len(sides)) for j in range(i)
    ):
        return "undetermined"
    return "disagreement"


def _value_kind(value: str) -> str:
    """A coarse kind for a value, so a host name is never compared with a company name."""
    identifiers = extract_identifiers(value)
    if identifiers:
        return identifiers[0].split(":", 1)[0]
    if _ISO_DATE.search(value):
        return "date"
    if re.fullmatch(r"[\d][\d.,\s]*", value.strip()):
        return "number"
    return "text"


def _same_property(first: ValidatedClaim, second: ValidatedClaim) -> bool:
    """Whether two claims state the same property of the same subject.

    The property must carry the same label. Treating labels that merely share a word as the same
    property reported "Örnek A.Ş." and "TR" as a disagreement, because ``registrant.organization``
    and ``registrant.country`` share "registrant": two fields of one record, not two sides. A
    conflict the analyst has to disprove is worse than a difference left as two cited facts, so
    when the model labels one property two ways the sides stay visible and separate instead.
    """
    if _subject_key(first.about.subject) != _subject_key(second.about.subject):
        return False
    if _value_kind(first.about.value) != _value_kind(second.about.value):
        return False
    if fold_for_search(first.about.attribute) != fold_for_search(second.about.attribute):
        return False
    # Two announcement dates of one company are not two sides when one quote is about
    # ornek.example and the other about destek.ornek.example: they date different things.
    values = {fold_for_search(first.about.value), fold_for_search(second.about.value)}
    first_ids, second_ids = (
        _quoted_identifiers(first) - values,
        _quoted_identifiers(second) - values,
    )
    return not (first_ids and second_ids and not first_ids & second_ids)


def _quoted_identifiers(claim: ValidatedClaim) -> set[str]:
    found: set[str] = set()
    for citation in claim.citations:
        found |= {key.split(":", 1)[1] for key in extract_identifiers(citation.quote or "")}
    return found


def _comparable_groups(claims: list[ValidatedClaim]) -> list[list[ValidatedClaim]]:
    """Claims about the same property of the same subject, in the order they were written."""
    groups: list[list[ValidatedClaim]] = []
    for claim in claims:
        for group in groups:
            if all(_same_property(claim, member) for member in group):
                group.append(claim)
                break
        else:
            groups.append([claim])
    return groups


def _disclose_differences(answer: ValidatedAnswer) -> None:
    """Group supported facts that answer the same thing and disclose every difference.

    The grouping and the comparison are the server's; the model only reports what each record
    states. A difference is always shown with every side cited, and every side keeps its own
    record label. Claims the model kept as context take part too, so a side cannot be dropped by
    demoting it.
    """
    comparable = [
        claim
        for claim in answer.claims
        if claim.kind == "fact"
        and claim.applicability != "other_subject"
        and claim.about.attribute.strip()
        and claim.about.value.strip()
    ]
    for members in _comparable_groups(comparable):
        if len(members) < 2:
            continue
        by_value: dict[str, list[ValidatedClaim]] = {}
        for claim in members:
            by_value.setdefault(fold_for_search(claim.about.value), []).append(claim)
        sides = list(by_value.values())
        sources = _distinct_sources([c for claim in members for c in claim.citations])
        if len(sides) < 2 or len(sources) < 2:
            continue
        difference = _difference_type(sides)
        # One entry per value, naming every record that gives it, so a value two records agree on
        # is not presented as two sides.
        summary = "; ".join(
            f"{side[0].about.value} ("
            + ", ".join(dict.fromkeys(_record_label(claim) for claim in side))
            + ")"
            for side in sides
        )
        explanation = DIFFERENCE_EXPLANATIONS[difference]
        wording = " ".join(dict.fromkeys(claim.text.rstrip() for claim in members))
        merged = ValidatedClaim(
            text=f"{wording} — {summary}.{explanation}",
            kind="conflict",
            citations=[citation for claim in members for citation in claim.citations],
            about=ClaimAbout(
                subject=members[0].about.subject,
                attribute=members[0].about.attribute,
                value="; ".join(side[0].about.value for side in sides),
            ),
            answers_question=any(claim.answers_question for claim in members),
            applicability=members[0].applicability,
            difference_type=difference,
        )
        position = min(answer.claims.index(claim) for claim in members)
        answer.claims[position] = merged
        for claim in members:
            if claim is not merged and claim in answer.claims:
                answer.claims.remove(claim)
        answer.server_notes.append(
            "The cited records give different values for the same subject and property; every "
            f"side is shown, as {DIFFERENCE_NOTES[difference]}."
        )


def render_plain_text(answer: ValidatedAnswer) -> str:
    """Plain-text rendering stored with the message (the UI renders structured claims)."""
    lines = []
    for claim in answer.claims:
        refs = " ".join(f"[{citation.label}]" for citation in claim.citations)
        lines.append(f"{claim.text} {refs}".strip())
    return "\n".join(lines)
