"""Collect an explicitly selected set of case records and render them as one static HTML file.

Guarantees, tested in tests/test_reports.py:

* Every piece of case text passes through the redactor and ``html.escape``; no markup from
  evidence, titles, filenames, notes or model output is ever emitted as markup.
* The file contains no scripts, no event handlers, no external stylesheets, fonts or images, and
  a Content-Security-Policy that forbids loading anything. Opening it makes no network request.
* External originals appear as plain ``http(s)`` links the reader may choose to follow, marked
  as not bundled; bundled excerpts are separate sections with in-file anchors, so every citation
  resolves inside the file without the application.
* Stored credentials, sessions and internal storage paths are never read or written.
* Uncertainty is kept: relationship origin and review status, AI-generated labels and claim kinds,
  conflicts, changes, absences, unresolved questions and collection gaps.
"""

from __future__ import annotations

import contextlib
import html
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.ai.models import AiMessage, AiRun, MessageRole
from app.ai.service import citation_detail
from app.cases.models import Case, Note
from app.connectors import htmltext
from app.entities import comparison, timeline
from app.entities import service as entity_service
from app.entities.models import Entity, EntityIdentifier, Relationship
from app.evidence.models import TEXT_KINDS, EvidenceKind, EvidenceObject
from app.evidence.storage import EvidenceStorage, IntegrityError
from app.imports.models import ProcessingJob, ProcessingStatus
from app.queries.models import ConnectorOutcome, ConnectorRun, QueryRun
from app.reports.redaction import Redactor
from app.reports.schemas import ReportSelection

MAX_BUNDLED_EVIDENCE = 150
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; "
    "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)
_STYLE = """
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1d232b;background:#fff;
max-width:960px;margin:0 auto;padding:24px 16px}
h1{font-size:1.6em;margin:0 0 4px}
h2{margin-top:2em;border-bottom:1px solid #d5dae0;padding-bottom:4px}
h3{margin:1.2em 0 .3em}.meta{color:#566170;font-size:.9em}.badge{display:inline-block;
border:1px solid #9aa5b1;border-radius:4px;padding:0 6px;font-size:.8em;margin-right:4px}
.ai{border-color:#8a5cf6;color:#5b21b6}.warn{border-color:#b45309;color:#92400e}
.ok{border-color:#15803d;color:#166534}pre{white-space:pre-wrap;word-break:break-word;
background:#f5f7f9;border:1px solid #e1e5ea;padding:8px;border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:.9em}th,td{border:1px solid #e1e5ea;
padding:4px 6px;text-align:left;vertical-align:top}.excerpt mark{background:#fde68a}
section.evidence{border-left:3px solid #9aa5b1;padding-left:10px;margin:16px 0}
.notice{background:#fff7ed;border:1px solid #fed7aa;padding:8px;border-radius:4px}
@media print{a{color:inherit}}
"""


@dataclass
class Bundle:
    """Evidence excerpts bundled into the report, in order of first reference."""

    order: list[uuid.UUID] = field(default_factory=list)
    reasons: dict[uuid.UUID, set[str]] = field(default_factory=dict)
    overflow: int = 0

    def add(self, evidence_id: uuid.UUID | None, reason: str) -> str | None:
        if evidence_id is None:
            return None
        if evidence_id not in self.reasons:
            if len(self.order) >= MAX_BUNDLED_EVIDENCE:
                self.overflow += 1
                return None
            self.order.append(evidence_id)
            self.reasons[evidence_id] = set()
        self.reasons[evidence_id].add(reason)
        return anchor(evidence_id)


def anchor(evidence_id: uuid.UUID) -> str:
    return f"evidence-{evidence_id}"


class Html:
    """Minimal writer: text is always redacted and escaped; only fixed tags are emitted."""

    def __init__(self, redact: Redactor) -> None:
        self.redact = redact
        self.parts: list[str] = []

    def text(self, value: object) -> str:
        return html.escape(self.redact(value), quote=True)

    def raw(self, markup: str) -> None:
        self.parts.append(markup)

    def el(self, tag: str, value: object, cls: str | None = None, id_: str | None = None) -> None:
        attributes = ""
        if cls:
            attributes += f' class="{html.escape(cls)}"'
        if id_:
            attributes += f' id="{html.escape(id_)}"'
        self.parts.append(f"<{tag}{attributes}>{self.text(value)}</{tag}>")

    def badge(self, value: object, kind: str = "") -> str:
        return f'<span class="badge {html.escape(kind)}">{self.text(value)}</span>'

    def internal_link(self, target: str | None, label: object) -> str:
        if target is None:
            return self.text(label)
        return f'<a href="#{html.escape(target)}">{self.text(label)}</a>'

    def external_link(self, url: str | None) -> str:
        if not url:
            return ""
        cleaned = self.redact(url)
        parts = urlsplit(cleaned)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return self.text(cleaned)
        escaped = html.escape(cleaned, quote=True)
        return (
            f'<a href="{escaped}" rel="noopener noreferrer nofollow" referrerpolicy="no-referrer">'
            f'{escaped}</a> <span class="meta">(external original, not bundled; it may have '
            "changed or be unavailable)</span>"
        )

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        """Cells are pre-rendered markup produced by this writer's escaping helpers."""
        self.parts.append("<table><thead><tr>")
        self.parts.extend(f"<th>{html.escape(h)}</th>" for h in headers)
        self.parts.append("</tr></thead><tbody>")
        for row in rows:
            self.parts.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
        self.parts.append("</tbody></table>")

    def result(self) -> str:
        return "".join(self.parts)


def _dt(value: datetime | str | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _missing(kind: str, ids: list[uuid.UUID], found: set[uuid.UUID]) -> None:
    if set(ids) - found:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": f"{kind}_not_found",
                "message": f"A selected {kind} is not in this case.",
            },
        )


def build_report(
    db: Session,
    storage: EvidenceStorage,
    case: Case,
    selection: ReportSelection,
) -> tuple[str, dict[str, int], Redactor, list[str]]:
    terms = list(selection.redact_terms)
    if selection.redact_identifier_types:
        terms.extend(
            value
            for value in db.scalars(
                select(EntityIdentifier.original_value).where(
                    EntityIdentifier.case_id == case.id,
                    EntityIdentifier.identifier_type.in_(
                        [str(t) for t in selection.redact_identifier_types]
                    ),
                )
            )
        )
    redact = Redactor(terms)
    out = Html(redact)
    bundle = Bundle()
    warnings: list[str] = []
    counts: dict[str, int] = {}
    body = Html(redact)

    _entities(db, case, selection, body, bundle, counts)
    _relationships(db, case, selection, body, bundle, counts)
    if selection.comparison_entity_ids:
        _comparison(db, case, selection, body, bundle, counts)
    if selection.ai_message_ids:
        _ai_answers(db, storage, case, selection, body, bundle, counts, warnings)
    if selection.note_ids:
        _notes(db, case, selection, body, bundle, counts)
    if selection.include_timeline:
        _timeline(db, case, selection, body, bundle, counts)
    for evidence_id in selection.evidence_ids:
        bundle.add(evidence_id, "selected")
    evidence_html = Html(redact)
    _evidence(db, storage, case, selection, evidence_html, bundle, counts)
    coverage_html = Html(redact)
    if selection.include_coverage:
        _coverage(db, case, coverage_html, counts)
    if bundle.overflow:
        warnings.append(
            f"{bundle.overflow} further evidence reference(s) exceeded the bundle limit of "
            f"{MAX_BUNDLED_EVIDENCE} and are cited by ID only."
        )

    generated = datetime.now(UTC)
    title = selection.title or case.title
    out.raw(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="referrer" content="no-referrer">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
    )
    out.el("title", f"{title} - Tracehollow report")
    out.raw(f"<style>{_STYLE}</style></head><body>")
    out.el("h1", title)
    out.raw('<p class="meta">')
    out.raw(
        f"Case {out.text(case.id)} · generated {out.text(_dt(generated))} by Tracehollow "
        f"{out.text(__version__)} · contains only the selected records; it is not a complete case "
        "export."
    )
    out.raw("</p>")
    out.raw(
        '<p class="notice">Labels: <span class="badge ok">observed</span> collected from a source; '
        '<span class="badge">analyst assertion</span> recorded by an analyst; '
        '<span class="badge ai">AI-generated</span> produced by a model and not an analyst '
        'finding; <span class="badge warn">unreviewed</span> not yet reviewed. Bundled excerpts '
        "are copies inside this file; external links point to originals that may have changed.</p>"
    )
    if selection.include_case_purpose:
        out.raw('<h2 id="purpose">Case purpose and scope</h2>')
        out.el("h3", "Purpose")
        out.el("pre", case.purpose or "Not recorded.")
        out.el("h3", "Scope")
        out.el("pre", case.scope or "Not recorded.")
    out.raw(body.result())
    out.raw(evidence_html.result())
    out.raw(coverage_html.result())
    out.raw('<h2 id="about">About this report</h2><ul>')
    out.el("li", f"Analyst redactions applied: {redact.redactions}.")
    out.el("li", f"Credential-like values removed: {redact.credentials}.")
    out.el(
        "li",
        "Never included: stored credentials, session material, internal storage paths and "
        "worker details.",
    )
    out.el(
        "li",
        "SHA-256 values detect changes to stored bytes; they do not prove authorship or "
        "authenticity. Times are UTC unless marked as local time without a timezone.",
    )
    out.raw("</ul></body></html>")
    counts["bundled_evidence"] = len(bundle.order)
    return out.result(), counts, redact, warnings


# -- sections ----------------------------------------------------------------------------------


def _origin_badge(out: Html, origin: str, review_status: str) -> str:
    badges = []
    if origin == "ai_suggestion":
        badges.append(out.badge("AI-generated suggestion", "ai"))
    elif origin == "observed":
        badges.append(out.badge("observed", "ok"))
    else:
        badges.append(out.badge(origin.replace("_", " ")))
    if review_status == "unreviewed":
        badges.append(out.badge("unreviewed", "warn"))
    else:
        badges.append(out.badge(review_status))
    return "".join(badges)


def _entities(
    db: Session,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
) -> None:
    if not selection.entity_ids:
        return
    rows = list(
        db.scalars(
            select(Entity).where(Entity.case_id == case.id, Entity.id.in_(selection.entity_ids))
        )
    )
    _missing("entity", selection.entity_ids, {row.id for row in rows})
    out.raw('<h2 id="findings">Selected entities</h2>')
    by_id = {row.id: row for row in rows}
    for entity_id in dict.fromkeys(selection.entity_ids):
        entity = by_id[entity_id]
        detail = entity_service.entity_detail(db, entity)
        out.raw(f'<h3 id="entity-{entity.id}">{out.text(entity.display_name)} ')
        out.raw(out.badge(entity.entity_type) + out.badge(entity.origin.replace("_", " ")))
        out.raw("</h3>")
        if entity.description:
            out.el("p", entity.description)
        out.table(
            ["Identifier", "Platform", "Value"],
            [
                [
                    out.text(i.identifier_type),
                    out.text(i.platform or ""),
                    out.text(i.original_value),
                ]
                for i in detail.entity.identifiers
            ],
        )
        if detail.shared_identifiers:
            out.el(
                "p",
                "Other entities share an identifier with this one; they were not merged: "
                + "; ".join(
                    f"{s.display_name} ({s.identifier_type} {s.normalized_value})"
                    for s in detail.shared_identifiers
                ),
                cls="meta",
            )
        links = []
        for link in detail.linked_evidence:
            target = bundle.add(link.evidence_id, f"linked to {entity.display_name}")
            links.append(out.internal_link(target, link.title))
        if links:
            out.raw(f"<p>Linked evidence: {', '.join(links)}</p>")
        out.el("p", f"Observations recorded: {detail.observation_count}.", cls="meta")
    counts["entities"] = len(rows)


def _relationships(
    db: Session,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
) -> None:
    if not selection.relationship_ids:
        return
    rows = list(
        db.scalars(
            select(Relationship).where(
                Relationship.case_id == case.id, Relationship.id.in_(selection.relationship_ids)
            )
        )
    )
    _missing("relationship", selection.relationship_ids, {row.id for row in rows})
    out.raw('<h2 id="relationships">Selected relationships</h2>')
    for relationship in rows:
        detail = entity_service.relationship_detail(db, relationship)
        out.raw("<h3>")
        out.raw(
            f"{out.text(detail.source.display_name)} <em>{out.text(detail.predicate)}</em> "
            f"{out.text(detail.target.display_name)} "
        )
        out.raw(_origin_badge(out, detail.origin, detail.review_status))
        out.raw("</h3>")
        if detail.description:
            out.el("p", detail.description)
        if detail.valid_from or detail.valid_to:
            out.el(
                "p", f"Valid from {_dt(detail.valid_from)} to {_dt(detail.valid_to)}.", cls="meta"
            )
        references = []
        for reference in detail.references:
            target = bundle.add(reference.evidence_id, "relationship reference")
            label = f"{reference.stance}: {reference.evidence_title or reference.observation_id}"
            if reference.note:
                label += f" ({reference.note})"
            references.append(f"<li>{out.internal_link(target, label)}</li>")
        if references:
            out.raw("<ul>" + "".join(references) + "</ul>")
        else:
            out.el("p", "No evidence references recorded.", cls="meta")
        for decision in detail.decisions:
            out.el(
                "p",
                f"Analyst decision on {_dt(decision.decided_at)}: {decision.decision_type} "
                f"{decision.previous_value} → {decision.new_value}"
                + (f" ({decision.rationale})" if decision.rationale else ""),
                cls="meta",
            )
    counts["relationships"] = len(rows)


def _comparison(
    db: Session,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
) -> None:
    result = comparison.compare(db, case.id, selection.comparison_entity_ids)
    names = {entity.id: entity.display_name for entity in result.entities}
    out.raw('<h2 id="comparison">Entity comparison</h2>')
    out.el("p", result.merge_policy, cls="notice")
    out.table(
        ["Entity", "Type", "Observations", "Event times", "Collected", "Sources"],
        [
            [
                out.text(e.display_name),
                out.text(e.entity_type),
                out.text(e.observation_count),
                out.text(
                    " to ".join(_dt(v) for v in e.event_time_span) if e.event_time_span else "none"
                ),
                out.text(
                    " to ".join(_dt(v) for v in e.collected_span) if e.collected_span else "none"
                ),
                out.text(
                    "; ".join(
                        f"{c.source} ({c.acquisition_method}, {c.observations} observations, runs: "
                        + ", ".join(str(r.get("outcome")) for r in c.connector_runs)
                        + ")"
                        for c in e.coverage
                    )
                    or "none"
                ),
            ]
            for e in result.entities
        ],
    )
    out.el("h3", "Identifiers")
    out.table(
        ["Kind", "Type", "Platform", "Values", "Entities"],
        [
            [
                out.text(i.kind.replace("_", " ")),
                out.text(i.identifier_type),
                out.text(i.platform or ""),
                out.text(", ".join(i.values)),
                out.text(", ".join(names.get(e, str(e)) for e in i.entity_ids)),
            ]
            for i in result.identifiers
        ],
    )
    if result.relationships or result.shared_neighbours:
        out.el("h3", "Relationships between compared entities and shared neighbours")
        rows = [
            [
                out.text(f"{r.source_name} {r.predicate} {r.target_name}"),
                _origin_badge(out, r.origin, r.review_status),
            ]
            for r in result.relationships
        ] + [
            [
                out.text(f"Shared neighbour: {n['display_name']}"),
                out.text(
                    "; ".join(
                        f"{names.get(uuid.UUID(k), k)}: {', '.join(v)}"
                        for k, v in n["connections"].items()
                    )
                ),
            ]
            for n in result.shared_neighbours
        ]
        out.table(["Relationship", "Status"], rows)
    if result.changes:
        out.el("h3", "Changes between collections")
        out.table(
            ["Entity", "Item", "Field", "Earlier", "Later", "Collected"],
            [
                [
                    out.text(names.get(c.entity_id, "")),
                    out.text(f"{c.observation_type} {c.source_object_id or ''}"),
                    out.text(c.field),
                    out.internal_link(
                        bundle.add(c.previous_evidence_id, "comparison"), c.previous or "none"
                    ),
                    out.internal_link(
                        bundle.add(c.current_evidence_id, "comparison"), c.current or "none"
                    ),
                    out.text(f"{_dt(c.previous_collected_at)} → {_dt(c.current_collected_at)}"),
                ]
                for c in result.changes
            ],
        )
        out.el("p", result.changes[0].note, cls="meta")
    if result.absences:
        out.el("h3", "Items not seen in a later collection")
        for absence in result.absences:
            out.el(
                "p",
                f"{names.get(absence.entity_id, '')} · {absence.connector_id}: "
                f"{absence.items_total} "
                f"item(s) — {absence.interpretation.replace('_', ' ')}. {absence.note}",
            )
    out.el("h3", "Conflicts")
    if result.conflicts:
        out.raw("<ul>")
        for conflict in result.conflicts:
            refs = ", ".join(
                out.internal_link(bundle.add(e, "conflict"), "evidence")
                for e in conflict.evidence_ids
            )
            out.raw(
                f"<li>{out.badge('conflict', 'warn')}{out.text(conflict.field)}: "
                f"{out.text(' | '.join(conflict.values))}. {out.text(conflict.note)} {refs}</li>"
            )
        out.raw("</ul>")
    else:
        out.el("p", "No conflicts were detected in the recorded observations.", cls="meta")
    out.el("h3", "Unresolved questions")
    if result.unresolved:
        out.raw(
            "<ul>" + "".join(f"<li>{out.text(item)}</li>" for item in result.unresolved) + "</ul>"
        )
    else:
        out.el("p", "None recorded.", cls="meta")
    counts["comparisons"] = 1


def _ai_answers(
    db: Session,
    storage: EvidenceStorage,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
    warnings: list[str],
) -> None:
    rows = db.execute(
        select(AiMessage, AiRun)
        .join(AiRun, AiRun.id == AiMessage.ai_run_id)
        .where(
            AiMessage.case_id == case.id,
            AiMessage.id.in_(selection.ai_message_ids),
            AiMessage.role == MessageRole.ASSISTANT,
        )
    ).all()
    _missing("ai_answer", selection.ai_message_ids, {message.id for message, _run in rows})
    out.raw('<h2 id="ai-answers">AI-generated answers</h2>')
    out.el(
        "p",
        "These answers were generated by a model from case evidence and validated against their "
        "citations by the server. They are not analyst findings and have not been confirmed by "
        "this report.",
        cls="notice",
    )
    for message, run in rows:
        answer = message.answer or {}
        out.raw(f'<h3 id="answer-{message.id}">{out.badge("AI-generated", "ai")}')
        out.raw(out.text(run.question or "Question not recorded") + "</h3>")
        out.el(
            "p",
            f"Status {answer.get('status', 'unknown')} · provider {run.provider or 'unknown'} · "
            "model "
            f"{run.model or 'unknown'} · processed {run.processing_location or 'unknown'} · "
            f"prompt {run.prompt_template_version or 'unknown'} · {_dt(message.created_at)}"
            + (" · synthetic test model" if answer.get("synthetic_model") else ""),
            cls="meta",
        )
        claims = answer.get("claims", []) or []
        out.raw("<ol>")
        for index, claim in enumerate(claims):
            kind = str(claim.get("kind", "claim"))
            cls = "ai" if kind in ("inference", "insufficient") else ""
            links = []
            for citation in claim.get("citations", []) or []:
                try:
                    detail = citation_detail(
                        db, storage, case.id, uuid.UUID(citation["citation_id"])
                    )
                except (HTTPException, KeyError, ValueError):
                    links.append(out.text(f"[{citation.get('label', '?')}: unavailable]"))
                    continue
                passage = detail.passage
                if passage is None:
                    links.append(out.text(f"[{detail.label}: database tool result]"))
                    continue
                target = bundle.add(passage.evidence_id, "AI citation")
                where = []
                if passage.page:
                    where.append(f"page {passage.page}")
                if passage.line:
                    where.append(f"line {passage.line}")
                label = f"[{detail.label}{', ' + ', '.join(where) if where else ''}]"
                if passage.status != "available":
                    label += f" ({passage.status.replace('_', ' ')})"
                quote = passage.passage or passage.quote
                links.append(
                    out.internal_link(target, label)
                    + (f" <q>{out.text(quote[:400])}</q>" if quote else "")
                )
            out.raw(
                f'<li id="answer-{message.id}-claim-{index}">{out.badge(kind, cls)}'
                f"{out.text(claim.get('text', ''))}<br>{' '.join(links)}</li>"
            )
        out.raw("</ol>")
        for label, key in (("Limitations", "limitations"), ("Coverage notes", "coverage_notes")):
            values = answer.get(key) or []
            if values:
                out.el("p", f"{label}: " + " ".join(str(v) for v in values), cls="meta")
        removed = (run.validation or {}).get("removed_claims") or (run.validation or {}).get(
            "removed"
        )
        if removed:
            out.el(
                "p",
                f"The server removed {len(removed) if isinstance(removed, list) else removed} "
                "claim(s) "
                "that its citations did not support.",
                cls="meta",
            )
    if rows:
        warnings.append(
            "AI-generated answers are included; they are labelled and not analyst findings."
        )
    counts["ai_answers"] = len(rows)


def _notes(
    db: Session,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
) -> None:
    rows = list(
        db.scalars(select(Note).where(Note.case_id == case.id, Note.id.in_(selection.note_ids)))
    )
    _missing("note", selection.note_ids, {row.id for row in rows})
    out.raw('<h2 id="notes">Analyst notes</h2>')
    for note in rows:
        target = bundle.add(note.evidence_id, "note")
        out.raw(f"<h3>{out.badge('analyst note')}{out.text(_dt(note.created_at))}</h3>")
        out.el("pre", note.body)
        if target:
            out.raw(f"<p>About {out.internal_link(target, 'evidence')}</p>")
    counts["notes"] = len(rows)


def _timeline(
    db: Session,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
) -> None:
    if selection.timeline_entity_id is not None:
        entity_service.get_entity(db, case.id, selection.timeline_entity_id)
    out.raw('<h2 id="timeline">Timeline</h2>')
    total = 0
    for section, heading in (
        ("dated", "Dated items (UTC)"),
        ("local_time_only", "Local times without a timezone (not on the UTC timeline)"),
        ("undated", "Items with only a collection time"),
    ):
        result = timeline.build_timeline(
            db,
            case.id,
            section=section,  # type: ignore[arg-type]
            entity_id=selection.timeline_entity_id,
            evidence_id=None,
            observation_type=None,
            start=None,
            end=None,
            limit=selection.timeline_limit,
            offset=0,
        )
        if not result.items:
            continue
        out.el("h3", f"{heading} — {len(result.items)} of {result.total}")
        out.table(
            ["Time", "Basis", "Item", "Source"],
            [
                [
                    out.text(
                        _dt(item.time) if item.time else (item.local_time or _dt(item.collected_at))
                    ),
                    out.text(item.time_basis.replace("_", " ")),
                    out.text(
                        " · ".join(
                            part
                            for part in (item.source_label, item.summary or item.observation_type)
                            if part
                        )
                    )
                    + (
                        f'<br><span class="meta">{out.text(" ".join(item.notes))}</span>'
                        if item.notes
                        else ""
                    ),
                    out.internal_link(
                        bundle.add(item.evidence_id, "timeline"), item.evidence_title or "evidence"
                    ),
                ]
                for item in result.items
            ],
        )
        total += len(result.items)
    if total == 0:
        out.el("p", "No observations match.", cls="meta")
    counts["timeline_items"] = total


def _excerpt(
    storage: EvidenceStorage, evidence: EvidenceObject, limit: int
) -> tuple[str | None, str]:
    if evidence.kind not in TEXT_KINDS:
        return None, "Binary original: not bundled. Use the evidence ID and SHA-256 to locate it."
    try:
        content = storage.read_verified(evidence.storage_key, evidence.sha256, evidence.size_bytes)
    except IntegrityError as exc:
        return None, f"The stored original failed its integrity check ({exc.code}); no excerpt."
    encoding = (evidence.collection_metadata or {}).get("decoded_with")
    try:
        text = content.decode(encoding if isinstance(encoding, str) else "utf-8", errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    if evidence.kind == EvidenceKind.HTML:
        text = htmltext.extract(text, evidence.source_reference or "").text
    elif evidence.kind == EvidenceKind.JSON:
        with contextlib.suppress(ValueError):
            text = json.dumps(json.loads(text.removeprefix("\ufeff")), ensure_ascii=False, indent=2)
    truncated = len(text) > limit
    note = f"Excerpt: first {limit} of {len(text)} characters." if truncated else "Complete text."
    if evidence.kind == EvidenceKind.HTML:
        note += " Text extracted from the HTML snapshot; markup and scripts are not reproduced."
    return text[:limit], note


def _evidence(
    db: Session,
    storage: EvidenceStorage,
    case: Case,
    selection: ReportSelection,
    out: Html,
    bundle: Bundle,
    counts: dict[str, int],
) -> None:
    if not bundle.order:
        return
    rows = {
        row.id: row
        for row in db.scalars(
            select(EvidenceObject).where(
                EvidenceObject.case_id == case.id, EvidenceObject.id.in_(bundle.order)
            )
        )
    }
    _missing("evidence", selection.evidence_ids, set(rows))
    out.raw('<h2 id="evidence">Bundled evidence excerpts</h2>')
    for evidence_id in bundle.order:
        evidence = rows.get(evidence_id)
        if evidence is None:
            out.raw(f'<section class="evidence" id="{anchor(evidence_id)}">')
            out.el("p", "This evidence was deleted; only its reference remains.", cls="meta")
            out.raw("</section>")
            continue
        out.raw(f'<section class="evidence" id="{anchor(evidence.id)}">')
        out.el("h3", evidence.title)
        method = evidence.acquisition_method.replace("_", " ")
        source = (
            f"imported ({evidence.import_origin})"
            if evidence.acquisition_method == "authorized_import"
            else f"collected by {evidence.connector_id} ({evidence.collection_mode or 'synthetic'})"
            if evidence.connector_id
            else method
        )
        details = [
            ["Acquisition", out.text(f"{method}: {source}")],
            ["Kind", out.text(f"{evidence.kind} ({evidence.content_type})")],
            ["Collected or imported", out.text(_dt(evidence.collected_at))],
            [
                "Published (per source)",
                out.text(
                    _dt(evidence.source_published_at)
                    + (
                        f" (as written: {evidence.source_published_at_original})"
                        if evidence.source_published_at_original
                        else ""
                    )
                    if evidence.source_published_at
                    else "unknown"
                ),
            ],
            ["Evidence ID", out.text(evidence.id)],
            ["SHA-256", out.text(evidence.sha256)],
            ["Included because", out.text(", ".join(sorted(bundle.reasons[evidence.id])))],
        ]
        if evidence.original_filename:
            details.append(["Original filename", out.text(evidence.original_filename)])
        if evidence.source_reference:
            details.append(["Original location", out.external_link(evidence.source_reference)])
        if evidence.derived_from_evidence_id:
            details.append(
                [
                    "Derived from",
                    out.text(
                        f"evidence {evidence.derived_from_evidence_id} "
                        "("
                        + str(
                            (evidence.collection_metadata or {}).get("derivation")
                            or "derived record"
                        )
                        + ")"
                    ),
                ]
            )
        if (evidence.collection_metadata or {}).get("text_origin") == "ocr":
            details.append(
                ["Text origin", out.text("OCR: machine recognition, may contain errors")]
            )
        out.table(["Field", "Value"], details)
        excerpt, note = _excerpt(storage, evidence, selection.excerpt_chars)
        out.el("p", note, cls="meta")
        if excerpt is not None:
            out.el("pre", excerpt, cls="excerpt")
        out.raw("</section>")
    counts["evidence"] = len(rows)


def _coverage(db: Session, case: Case, out: Html, counts: dict[str, int]) -> None:
    out.raw('<h2 id="coverage">Collection dates and coverage gaps</h2>')
    dates = db.execute(
        select(
            func.min(EvidenceObject.collected_at),
            func.max(EvidenceObject.collected_at),
            func.min(EvidenceObject.source_published_at),
            func.max(EvidenceObject.source_published_at),
        ).where(EvidenceObject.case_id == case.id)
    ).one()
    methods = db.execute(
        select(EvidenceObject.acquisition_method, func.count())
        .where(EvidenceObject.case_id == case.id)
        .group_by(EvidenceObject.acquisition_method)
    ).all()
    out.el(
        "p",
        f"Evidence collected or imported between {_dt(dates[0])} and {_dt(dates[1])}; source "
        f"publication times between {_dt(dates[2])} and {_dt(dates[3])}. Records by acquisition: "
        + (", ".join(f"{method} {count}" for method, count in methods) or "none")
        + ".",
    )
    gaps = db.execute(
        select(ConnectorRun, QueryRun.run_number)
        .join(QueryRun, QueryRun.id == ConnectorRun.query_run_id)
        .where(
            ConnectorRun.case_id == case.id,
            ConnectorRun.outcome.is_not(None),
            ConnectorRun.outcome.not_in([ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS]),
        )
        .order_by(ConnectorRun.finished_at.desc())
        .limit(200)
    ).all()
    jobs = list(
        db.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.case_id == case.id,
                ProcessingJob.status.in_(
                    [
                        ProcessingStatus.PARTIAL,
                        ProcessingStatus.FAILED,
                        ProcessingStatus.NEEDS_INPUT,
                    ]
                ),
            )
            .order_by(ProcessingJob.created_at.desc())
            .limit(200)
        )
    )
    if not gaps and not jobs:
        out.el("p", "No failed, partial or blocked collections or processing jobs are recorded.")
    rows = [
        [
            out.text(f"Run {run_number} · {run.connector_id}"),
            out.text(run.outcome),
            out.text(run.last_error_code or (run.coverage or {}).get("stopped_reason") or ""),
            out.text(run.coverage_note or run.last_error_detail or ""),
            out.text(_dt(run.finished_at)),
        ]
        for run, run_number in gaps
    ] + [
        [
            out.text(f"Processing {job.job_type}"),
            out.text(job.status),
            out.text(job.error_code or ""),
            out.text(
                "; ".join(str(g) for g in (job.result or {}).get("gaps", []))
                or job.error_detail
                or ""
            ),
            out.text(_dt(job.finished_at or job.updated_at)),
        ]
        for job in jobs
    ]
    if rows:
        out.table(["Collection", "Outcome", "Code", "Detail", "Finished"], rows)
        out.el(
            "p",
            "Items missing from these collections are unknown, not absent: a failed or partial "
            "collection does not show that something does not exist.",
            cls="meta",
        )
    counts["coverage_gaps"] = len(rows)
