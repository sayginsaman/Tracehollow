"""Human review package for an evaluation run, and a summary of completed reviewer labels.

``build_package`` turns a run's ``results.json`` into two worksheets:

- ``claims.csv``: one row per generated claim, with the exact cited quotes, the whole cited
  passage and its source dates (or the database result), for claim-support labels;
- ``questions.csv``: one row per question, for answer-level labels (completeness, abstention,
  conflict handling), so unanswered questions are measured separately from claim support.

``summarize`` reads completed copies (``claims-reviewed-<reviewer>.csv`` and
``questions-reviewed-<reviewer>.csv``), validates every label against the rubric and computes
the measures. It never fills in a missing label: an incompletely reviewed file yields counts and
no rate. Labels from reviewers whose ``reviewer_type`` is not ``human`` are reported in a
separate section and never count toward PRD Phase 3 criterion 5.

Usage::

    python -m app.ai.evaluation.review build <run-dir>
    python -m app.ai.evaluation.review summarize <run-dir> [--write]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUBRIC = "docs/testing/ai-evaluation/README.md#human-review"
SUPPORT_TARGET = 0.90

# Claim kinds whose support is measured (PRD criterion 5 denominator).
SUPPORT_KINDS = ("fact", "count", "conflict")
CLAIM_LABELS: dict[str, tuple[str, ...]] = {
    "fact": ("supported", "partially_supported", "unsupported", "mislabelled"),
    "count": ("supported", "partially_supported", "unsupported", "mislabelled"),
    "conflict": ("supported", "partially_supported", "unsupported", "mislabelled"),
    "inference": ("acceptable_inference", "unacceptable_inference", "mislabelled"),
    "insufficient": ("appropriate_abstention", "unnecessary_abstention", "mislabelled"),
}
QUESTION_LABELS = (
    "complete",
    "incomplete",
    "incorrect",
    "appropriate_abstention",
    "unnecessary_abstention",
    "answered_without_support",
    "correct_refusal",
    "incorrect_refusal",
)
CONFLICT_LABELS = (
    "not_applicable",
    "both_sides_labelled",
    "both_sides_unlabelled",
    "one_side_only",
    "conflict_invented",
)
REVIEWER_TYPES = ("human", "model")
_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

CLAIM_FIELDS = [
    "item_id",
    "question_id",
    "split",
    "category",
    "expectation",
    "question",
    "reference_answer",
    "answer_status",
    "claim_index",
    "claim_kind",
    "answers_question",
    "applicability",
    "claim_subject",
    "claim_attribute",
    "claim_value",
    "claim_as_of",
    "difference_type",
    "in_support_denominator",
    "claim_text",
    "citations",
    "cited_passages",
    "validator_notes",
    "support_label",
    "reviewer_notes",
    "reviewer_id",
    "reviewer_type",
    "reviewed_at",
]
QUESTION_FIELDS = [
    "question_id",
    "split",
    "category",
    "expectation",
    "question",
    "reference_answer",
    "answer_status",
    "claims",
    "limitations",
    "coverage_notes",
    "answer_label",
    "conflict_label",
    "reviewer_notes",
    "reviewer_id",
    "reviewer_type",
    "reviewed_at",
]
REMOVED_FIELDS = [
    "item_id",
    "question_id",
    "split",
    "category",
    "question",
    "answer_status",
    "claim_kind",
    "removal_reason",
    "citation_labels",
    "claim_subject",
    "claim_attribute",
    "claim_value",
    "removed_text",
]
REVIEWER_COLUMNS = {
    "support_label",
    "answer_label",
    "conflict_label",
    "reviewer_notes",
    "reviewer_id",
    "reviewer_type",
    "reviewed_at",
}


def _citation_line(ref: dict[str, Any]) -> str:
    if ref.get("type") == "tool":
        arguments = json.dumps(ref.get("tool_arguments") or {}, ensure_ascii=False, sort_keys=True)
        return f"[{ref['label']}] database tool {ref.get('tool')} {arguments}"
    published = ref.get("source_published_at") or "unknown"
    collected = ref.get("collected_at") or "unknown"
    pointer = f"; JSON {ref['json_pointer']}" if ref.get("json_pointer") else ""
    title = ref.get("evidence_title")
    quote = ref.get("quote") or ""
    return (
        f"[{ref['label']}] {title} (published {published}; collected {collected}{pointer}) "
        f"quote: «{quote}»"
    )


def _passage_line(ref: dict[str, Any]) -> str:
    if ref.get("type") == "tool":
        result = ref.get("tool_result") or {}
        description = result.get("description", ref.get("tool"))
        value = result.get("count", json.dumps(result, ensure_ascii=False, sort_keys=True)[:800])
        return f"[{ref['label']}] {description}: {value}"
    return f"[{ref['label']}] {ref.get('passage') or '(passage not recorded)'}"


def _validator_notes(result: dict[str, Any]) -> str:
    validation = result.get("validation") or {}
    parts = []
    removed = validation.get("claims_removed") or []
    if removed:
        reasons = Counter(entry.get("reason", "unknown") for entry in removed)
        parts.append(
            "removed before display: " + ", ".join(f"{k} x{v}" for k, v in sorted(reasons.items()))
        )
    rejected = validation.get("citations_rejected") or []
    if rejected:
        parts.append(f"{len(rejected)} model reference(s) rejected")
    parts.extend(validation.get("server_notes") or [])
    return "; ".join(parts)


def build_package(summary: dict[str, Any], directory: Path) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    claim_rows = []
    question_rows = []
    removed_rows = []
    for result in summary["results"]:
        status = result.get("answer_status") or result.get("error_code") or result.get("run_status")
        common = {
            "question_id": result["id"],
            "split": result.get("split", "development"),
            "category": result["category"],
            "expectation": result.get("expectation", "unspecified"),
            "question": result["question"],
            "reference_answer": result.get("reference_answer", ""),
            "answer_status": status,
        }
        notes = _validator_notes(result)
        for index, claim in enumerate(result.get("claims") or []):
            refs = claim.get("citations") or []
            claim_rows.append(
                {
                    **common,
                    "item_id": f"{result['id']}-c{index}",
                    "claim_index": index,
                    "claim_kind": claim.get("kind"),
                    "answers_question": "yes" if claim.get("answers_question", True) else "no",
                    "applicability": claim.get("applicability", "unspecified"),
                    "claim_subject": (claim.get("about") or {}).get("subject", ""),
                    "claim_attribute": (claim.get("about") or {}).get("attribute", ""),
                    "claim_value": (claim.get("about") or {}).get("value", ""),
                    "claim_as_of": (claim.get("about") or {}).get("as_of", ""),
                    "difference_type": claim.get("difference_type", ""),
                    "in_support_denominator": "yes"
                    if claim.get("kind") in SUPPORT_KINDS and claim.get("answers_question", True)
                    else "no",
                    "claim_text": claim.get("text"),
                    "citations": "\n".join(_citation_line(ref) for ref in refs),
                    "cited_passages": "\n".join(_passage_line(ref) for ref in refs),
                    "validator_notes": notes,
                    "_refs": refs,
                }
            )
        for index, entry in enumerate((result.get("validation") or {}).get("claims_removed") or []):
            # Removed claims never reach a reader, so they are not reviewed for support. They
            # are published so the filter itself can be checked for over- and under-removal.
            removed_rows.append(
                {
                    "item_id": f"{result['id']}-r{index}",
                    "question_id": result["id"],
                    "split": common["split"],
                    "category": common["category"],
                    "question": common["question"],
                    "answer_status": status,
                    "claim_kind": entry.get("kind", ""),
                    "removal_reason": entry.get("reason", ""),
                    "citation_labels": " ".join(entry.get("citation_labels") or []),
                    "claim_subject": (entry.get("about") or {}).get("subject", ""),
                    "claim_attribute": (entry.get("about") or {}).get("attribute", ""),
                    "claim_value": (entry.get("about") or {}).get("value", ""),
                    "removed_text": entry.get("text", ""),
                }
            )
        question_rows.append(
            {
                **common,
                "claims": "\n".join(
                    f"{index}. [{claim.get('kind')}] {claim.get('text')}"
                    for index, claim in enumerate(result.get("claims") or [])
                )
                or "(no claims)",
                "limitations": "\n".join(result.get("limitations") or []),
                "coverage_notes": "\n".join(result.get("coverage_notes") or []),
            }
        )
    _write_csv(directory / "claims.csv", CLAIM_FIELDS, claim_rows)
    _write_csv(directory / "questions.csv", QUESTION_FIELDS, question_rows)
    _write_csv(directory / "removed-claims.csv", REMOVED_FIELDS, removed_rows)
    counts = {
        "claims": len(claim_rows),
        "support_denominator": sum(1 for r in claim_rows if r["in_support_denominator"] == "yes"),
        "questions": len(question_rows),
        "removed_claims": len(removed_rows),
    }
    (directory / "README.md").write_text(
        "\n".join(
            [
                f"# Review package — {summary['dataset_version']}",
                "",
                f"Run generated {summary['generated_at']} with {summary['providers']} "
                f"(prompts {summary.get('prompt_versions', {})}).",
                "",
                f"- `claims.csv`: {counts['claims']} claims; {counts['support_denominator']} of "
                "kind fact, count or conflict that answer the question form the claim-support "
                "denominator. Claims marked `answers_question=no` are context: judge them with "
                "the same labels, but they are counted separately.",
                f"- `questions.csv`: {counts['questions']} questions (answer, abstention and "
                "conflict labels).",
                f"- `removed-claims.csv`: {counts['removed_claims']} claims the validator "
                "removed before display, with the reason. They are not reviewed for support "
                "and are not in any denominator; they are here so the filter can be checked "
                "for removing too much or too little.",
                "",
                f"Follow the rubric in `{RUBRIC}`. Copy both files to "
                "`claims-reviewed-<reviewer_id>.csv` and `questions-reviewed-<reviewer_id>.csv`, "
                "fill in only the reviewer columns, then run "
                "`python -m app.ai.evaluation.review summarize <this run directory>`.",
                "",
                "Status: no reviewer labels are included when this package is generated.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_worksheet(summary, directory, claim_rows, question_rows, removed_rows)
    return counts


DECISIONS = "\n".join(
    [
        "| Write in `support_label` | Use when |",
        "| --- | --- |",
        "| `supported` | The cited passages say what the claim says, for the subject and period "
        "it names |",
        "| `partially_supported` | Part of it is supported; the rest overstates, adds detail, or "
        "shows only one side of a difference |",
        "| `unsupported` | The passages do not say it, say something else, or are about a "
        "different subject |",
        "| *(leave empty)* | You are unsure or the passage is unclear. An empty label is not a "
        "pass: the rate is computed only when every row in the denominator is labelled |",
    ]
)


def _fence(text: str) -> str:
    """Quote a passage so evidence text cannot be read as worksheet formatting."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines()) or ">"


def _worksheet_claim(index: int, total: int, row: dict[str, Any]) -> list[str]:
    lines = [
        f"### Claim {index} of {total} — `{row['item_id']}`",
        "",
        f"**Question ({row['question_id']}, {row['split']}, {row['category']}):** "
        f"{row['question']}",
        "",
        f"**Claim to judge** (kind `{row['claim_kind']}`): {row['claim_text']}",
        "",
    ]
    if row.get("difference_type"):
        lines += [
            f"The server presents this as a difference of type `{row['difference_type']}`. "
            "Judge whether both sides are really stated by the records it cites.",
            "",
        ]
    if row["answers_question"] == "no":
        lines += [
            "The server marked this as **context**: it does not answer the question. It is "
            "counted separately from the support rate; still say whether the passages support it.",
            "",
        ]
    lines.append("**What it cites:**")
    lines.append("")
    refs = row.get("_refs") or []
    if not refs:
        lines += ["(no citation)", ""]
    for ref in refs:
        lines += [
            f"- {_citation_line(ref)}",
            "",
            "  The whole passage this quote comes from:",
            "",
            _fence(_passage_line(ref)),
            "",
        ]
    if row["validator_notes"]:
        lines += [f"*Server notes for this answer: {row['validator_notes']}*", ""]
    lines += [
        f"**Decision for `{row['item_id']}`:** `supported` / `partially_supported` / "
        "`unsupported` / leave empty if unsure",
        "",
        "---",
        "",
    ]
    return lines


def _worksheet_question(index: int, total: int, row: dict[str, Any]) -> list[str]:
    lines = [
        f"### Question {index} of {total} — `{row['question_id']}` ({row['split']}, "
        f"{row['category']})",
        "",
        f"**Question:** {row['question']}",
        "",
        f"**The whole answer** (status `{row['answer_status']}`):",
        "",
        _fence(row["claims"]),
        "",
    ]
    if row["limitations"]:
        lines += ["**Limitations it stated:**", "", _fence(row["limitations"]), ""]
    if row["coverage_notes"]:
        lines += ["**Coverage notes:**", "", _fence(row["coverage_notes"]), ""]
    lines += [
        f"*The dataset author expected this question to be* **{row['expectation']}** "
        f"*and wrote this reference answer:* {row['reference_answer'] or '(none)'}",
        "",
        f"**`answer_label` for `{row['question_id']}`:** one of "
        + ", ".join(f"`{label}`" for label in QUESTION_LABELS),
        "",
        f"**`conflict_label` for `{row['question_id']}`:** one of "
        + ", ".join(f"`{label}`" for label in CONFLICT_LABELS),
        "",
        "---",
        "",
    ]
    return lines


def _write_worksheet(
    summary: dict[str, Any],
    directory: Path,
    claim_rows: list[dict[str, Any]],
    question_rows: list[dict[str, Any]],
    removed_rows: list[dict[str, Any]],
) -> None:
    """A readable worksheet with the same content as the CSVs, for reviewing without a spreadsheet.

    Everything a decision needs is on the page: the question, the whole answer, the claim, the
    exact quote and the passage it came from. The CSVs stay the file the summary tool reads.
    """
    revision = summary.get("code_revision") or {}
    denominator = [row for row in claim_rows if row["in_support_denominator"] == "yes"]
    context = [row for row in claim_rows if row["in_support_denominator"] != "yes"]
    lines = [
        f"# Review worksheet — {summary['dataset_version']}",
        "",
        "This is the same material as `claims.csv` and `questions.csv`, laid out to be read. "
        "Write your decisions into those CSV files (or tell the assistant, which will transcribe "
        "them and record who decided what).",
        "",
        "## What produced these answers",
        "",
        f"- Dataset `{summary['dataset_version']}`, SHA-256 `{summary.get('dataset_sha256')}`",
        f"- Code revision `{revision.get('commit')}`"
        + (" (with uncommitted changes)" if revision.get("uncommitted_changes") else ""),
        f"- Prompts {summary.get('prompt_versions')}, model "
        f"`{summary.get('generation_model')}` digest "
        f"`{str((summary.get('model_digests') or {}).get(summary.get('generation_model')))[:12]}`",
        f"- Generation settings {summary.get('generation_settings')}",
        "",
        "## How to decide",
        "",
        "Judge each claim **only** against the passages printed under it. Do not use outside "
        "knowledge, and do not treat the reference answer as ground truth: it is the dataset "
        "author's expectation, and it can be wrong.",
        "",
        "A citation that resolves proves only that the passage exists in this case. It does not "
        "prove that the passage says what the claim says.",
        "",
        DECISIONS,
        "",
        f"The support rate (PRD Phase 3 criterion 5) is `supported` divided by the "
        f"{len(denominator)} claims below that are in the denominator. Claims shown as context "
        f"({len(context)}) and claims the validator removed ({len(removed_rows)}) are never in it.",
        "",
        f"Full rubric: `{RUBRIC}`.",
        "",
        "## Claims",
        "",
    ]
    for index, row in enumerate(claim_rows, start=1):
        lines += _worksheet_claim(index, len(claim_rows), row)
    lines += ["## Questions", "", ""]
    for index, row in enumerate(question_rows, start=1):
        lines += _worksheet_question(index, len(question_rows), row)
    if removed_rows:
        lines += [
            "## Claims the validator removed (not reviewed, not in any denominator)",
            "",
            "These never reached a reader. They are here so the filter itself can be judged: "
            "tell the assistant if one of them should have been shown.",
            "",
            "| Question | Reason | Value it asserted | Removed text |",
            "| --- | --- | --- | --- |",
        ]
        for row in removed_rows:
            text = row["removed_text"].replace("|", "\\|").replace("\n", " ")
            value = row["claim_value"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {row['question_id']} | `{row['removal_reason']}` | {value} | {text} |")
        lines.append("")
    (directory / "worksheet.md").write_text("\n".join(lines), encoding="utf-8")


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class ReviewError(ValueError):
    pass


@dataclass
class ReviewerLabels:
    reviewer_id: str
    reviewer_type: str
    claims: dict[str, dict[str, str]] = field(default_factory=dict)
    questions: dict[str, dict[str, str]] = field(default_factory=dict)


def _check_unchanged(
    generated: dict[str, dict[str, str]], reviewed: list[dict[str, str]], key: str, path: Path
) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    errors = []
    for row in reviewed:
        item = row.get(key, "")
        if item not in generated:
            errors.append(f"{path.name}: unknown {key} {item!r}")
            continue
        changed = [
            name
            for name, value in generated[item].items()
            if name not in REVIEWER_COLUMNS and row.get(name, "") != value
        ]
        if changed:
            errors.append(f"{path.name}: {item} has edited non-reviewer columns {changed}")
        rows[item] = row
    if errors:
        raise ReviewError("\n".join(errors))
    return rows


def _reviewer_identity(rows: list[dict[str, str]], path: Path) -> tuple[str, str]:
    identities = {
        (row.get("reviewer_id", "").strip(), row.get("reviewer_type", "").strip())
        for row in rows
        if any(row.get(column, "").strip() for column in ("support_label", "answer_label"))
    }
    if not identities:
        raise ReviewError(f"{path.name}: no labels")
    if len(identities) != 1:
        raise ReviewError(f"{path.name}: labelled rows must share one reviewer_id/reviewer_type")
    reviewer_id, reviewer_type = identities.pop()
    if not _REVIEWER_ID.match(reviewer_id):
        raise ReviewError(f"{path.name}: reviewer_id {reviewer_id!r} is missing or invalid")
    if reviewer_type not in REVIEWER_TYPES:
        raise ReviewError(f"{path.name}: reviewer_type must be one of {REVIEWER_TYPES}")
    return reviewer_id, reviewer_type


def load_reviews(run_dir: Path) -> list[ReviewerLabels]:
    package = run_dir / "review"
    generated_claims = {row["item_id"]: row for row in _read_csv(package / "claims.csv")}
    generated_questions = {row["question_id"]: row for row in _read_csv(package / "questions.csv")}
    reviewers: dict[str, ReviewerLabels] = {}
    errors: list[str] = []
    for path in sorted(package.glob("claims-reviewed-*.csv")):
        rows = _read_csv(path)
        try:
            checked = _check_unchanged(generated_claims, rows, "item_id", path)
            reviewer_id, reviewer_type = _reviewer_identity(rows, path)
        except ReviewError as exc:
            errors.append(str(exc))
            continue
        for item, row in checked.items():
            label = row.get("support_label", "").strip()
            kind = generated_claims[item]["claim_kind"]
            if label and label not in CLAIM_LABELS.get(kind, ()):
                errors.append(f"{path.name}: {item} ({kind}) has label {label!r}")
        labels = reviewers.setdefault(reviewer_id, ReviewerLabels(reviewer_id, reviewer_type))
        if labels.reviewer_type != reviewer_type:
            errors.append(f"{path.name}: reviewer_type differs from the questions file")
        labels.claims = checked
    for path in sorted(package.glob("questions-reviewed-*.csv")):
        rows = _read_csv(path)
        try:
            checked = _check_unchanged(generated_questions, rows, "question_id", path)
            reviewer_id, reviewer_type = _reviewer_identity(rows, path)
        except ReviewError as exc:
            errors.append(str(exc))
            continue
        for item, row in checked.items():
            answer = row.get("answer_label", "").strip()
            conflict = row.get("conflict_label", "").strip()
            if answer and answer not in QUESTION_LABELS:
                errors.append(f"{path.name}: {item} has answer_label {answer!r}")
            if conflict and conflict not in CONFLICT_LABELS:
                errors.append(f"{path.name}: {item} has conflict_label {conflict!r}")
        labels = reviewers.setdefault(reviewer_id, ReviewerLabels(reviewer_id, reviewer_type))
        if labels.reviewer_type != reviewer_type:
            errors.append(f"{path.name}: reviewer_type differs from the claims file")
        labels.questions = checked
    if errors:
        raise ReviewError("\n".join(errors))
    return list(reviewers.values())


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def reviewer_measures(
    labels: ReviewerLabels,
    generated_claims: dict[str, dict[str, str]],
    generated_questions: dict[str, dict[str, str]],
) -> dict[str, Any]:
    support_items = [
        item for item, row in generated_claims.items() if row["in_support_denominator"] == "yes"
    ]
    support_labels = Counter(
        labels.claims.get(item, {}).get("support_label", "").strip() or "unlabelled"
        for item in support_items
    )
    unlabelled_support = support_labels.get("unlabelled", 0)
    support_complete = unlabelled_support == 0 and bool(support_items)
    supported = support_labels.get("supported", 0)

    def labelled(kind: str) -> Counter[str]:
        return Counter(
            labels.claims.get(item, {}).get("support_label", "").strip() or "unlabelled"
            for item, row in generated_claims.items()
            if row["claim_kind"] == kind
        )

    by_split: dict[str, dict[str, Any]] = {}
    for split in sorted({row["split"] for row in generated_claims.values()}):
        items = [item for item in support_items if generated_claims[item]["split"] == split]
        counts = Counter(
            labels.claims.get(item, {}).get("support_label", "").strip() or "unlabelled"
            for item in items
        )
        by_split[split] = {
            "denominator": len(items),
            "labels": dict(sorted(counts.items())),
            "claim_support_rate": _rate(counts.get("supported", 0), len(items))
            if counts.get("unlabelled", 0) == 0
            else None,
        }

    answers: Counter[str] = Counter()
    groups: dict[str, Counter[str]] = {}
    conflicts: Counter[str] = Counter()
    for question_id, row in generated_questions.items():
        answer = (
            labels.questions.get(question_id, {}).get("answer_label", "").strip() or "unlabelled"
        )
        answers[answer] += 1
        groups.setdefault(row["expectation"], Counter())[answer] += 1
        conflict = labels.questions.get(question_id, {}).get("conflict_label", "").strip()
        if conflict and conflict != "not_applicable":
            conflicts[conflict] += 1
    answerable = groups.get("answerable", Counter())
    unanswerable = groups.get("unanswerable", Counter())
    return {
        "reviewer_id": labels.reviewer_id,
        "reviewer_type": labels.reviewer_type,
        "claim_support": {
            "denominator": len(support_items),
            "labels": dict(sorted(support_labels.items())),
            "complete": support_complete,
            "claim_support_rate": _rate(supported, len(support_items))
            if support_complete
            else None,
            "meets_target": (supported / len(support_items) >= SUPPORT_TARGET)
            if support_complete
            else None,
            "by_split": by_split,
        },
        "inference": dict(sorted(labelled("inference").items())),
        "insufficient_claims": dict(sorted(labelled("insufficient").items())),
        "questions": {
            "labels": dict(sorted(answers.items())),
            "answerable": {
                "questions": sum(answerable.values()),
                "unnecessary_abstention": answerable.get("unnecessary_abstention", 0),
                "complete": answerable.get("complete", 0),
                "incomplete": answerable.get("incomplete", 0),
                "incorrect": answerable.get("incorrect", 0),
            },
            "unanswerable": {
                "questions": sum(unanswerable.values()),
                "appropriate_abstention": unanswerable.get("appropriate_abstention", 0),
                "answered_without_support": unanswerable.get("answered_without_support", 0),
            },
            "conflict_handling": dict(sorted(conflicts.items())),
        },
    }


def agreement(first: ReviewerLabels, second: ReviewerLabels) -> dict[str, Any]:
    shared = [
        item
        for item in first.claims
        if first.claims[item].get("support_label", "").strip()
        and second.claims.get(item, {}).get("support_label", "").strip()
    ]
    disagreements = [
        item
        for item in shared
        if first.claims[item]["support_label"].strip()
        != second.claims[item]["support_label"].strip()
    ]
    return {
        "reviewers": [first.reviewer_id, second.reviewer_id],
        "claims_labelled_by_both": len(shared),
        "percent_agreement": _rate(len(shared) - len(disagreements), len(shared)),
        "disagreements": disagreements,
    }


def summarize(run_dir: Path) -> dict[str, Any]:
    package = run_dir / "review"
    generated_claims = {row["item_id"]: row for row in _read_csv(package / "claims.csv")}
    generated_questions = {row["question_id"]: row for row in _read_csv(package / "questions.csv")}
    reviews = load_reviews(run_dir)
    measures = [reviewer_measures(r, generated_claims, generated_questions) for r in reviews]
    humans = [m for m in measures if m["reviewer_type"] == "human"]
    human_labels = [r for r in reviews if r.reviewer_type == "human"]
    complete_humans = [m for m in humans if m["claim_support"]["complete"]]
    if not humans:
        criterion = "pending: no human reviewer labels"
    elif not complete_humans:
        criterion = "pending: human review incomplete"
    elif all(m["claim_support"]["meets_target"] for m in complete_humans):
        criterion = "met by every complete human review"
    else:
        criterion = "not met by at least one complete human review"
    return {
        "run": run_dir.name,
        "support_denominator": sum(
            1 for row in generated_claims.values() if row["in_support_denominator"] == "yes"
        ),
        "questions": len(generated_questions),
        "prd_phase3_criterion5": criterion,
        "human_reviews": humans,
        "human_agreement": [
            agreement(a, b)
            for index, a in enumerate(human_labels)
            for b in human_labels[index + 1 :]
        ],
        "non_human_reviews": [m for m in measures if m["reviewer_type"] != "human"],
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Review summary — {result['run']}",
        "",
        "- Claim-support denominator (fact, count and conflict claims): "
        f"{result['support_denominator']}",
        f"- Questions: {result['questions']}",
        f"- PRD Phase 3 criterion 5: **{result['prd_phase3_criterion5']}**",
        "",
    ]

    def section(title: str, reviews: list[dict[str, Any]]) -> None:
        lines.extend([f"## {title}", ""])
        if not reviews:
            lines.extend(["None.", ""])
        for review in reviews:
            support = review["claim_support"]
            rate = support["claim_support_rate"]
            shown = f"{rate * 100:.1f}%" if rate is not None else "not computed (review incomplete)"
            lines.extend(
                [
                    f"### {review['reviewer_id']} ({review['reviewer_type']})",
                    "",
                    f"- Claim support: {shown}; labels {support['labels']}",
                    f"- By split: {json.dumps(support['by_split'], ensure_ascii=False)}",
                    f"- Inference claims: {review['inference']}",
                    f"- Insufficient claims: {review['insufficient_claims']}",
                ]
            )
            question_labels = review["questions"]["labels"]
            if set(question_labels) <= {"unlabelled"}:
                # Zero counts would read as questions judged and failed; none were judged.
                lines.append(
                    f"- Questions: not labelled by this reviewer "
                    f"({question_labels.get('unlabelled', 0)} unlabelled); answer, abstention "
                    "and conflict measures are not available"
                )
            else:
                lines.extend(
                    [
                        f"- Question labels: {question_labels}",
                        f"- Answerable questions: {review['questions']['answerable']}",
                        f"- Unanswerable questions: {review['questions']['unanswerable']}",
                        f"- Conflict handling: {review['questions']['conflict_handling']}",
                    ]
                )
            lines.append("")

    section("Human reviews (count toward PRD criterion 5)", result["human_reviews"])
    if result["human_agreement"]:
        lines.extend(["## Agreement between human reviewers", ""])
        lines.extend(
            f"- {json.dumps(item, ensure_ascii=False)}" for item in result["human_agreement"]
        )
        lines.append("")
    section(
        "Non-human reviews (not human review; never count toward PRD criterion 5)",
        result["non_human_reviews"],
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="write review/claims.csv and review/questions.csv")
    build.add_argument("run_dir", type=Path)
    summary = sub.add_parser("summarize", help="validate reviewer labels and compute measures")
    summary.add_argument("run_dir", type=Path)
    summary.add_argument("--write", action="store_true", help="also write review/summary.md")
    args = parser.parse_args(argv)
    if args.command == "build":
        results = json.loads((args.run_dir / "results.json").read_text(encoding="utf-8"))
        print(json.dumps(build_package(results, args.run_dir / "review")))
        return 0
    try:
        result = summarize(args.run_dir)
    except ReviewError as exc:
        print(f"Review files are invalid:\n{exc}", file=sys.stderr)
        return 2
    markdown = render_markdown(result)
    if args.write:
        (args.run_dir / "review" / "summary.md").write_text(markdown + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
