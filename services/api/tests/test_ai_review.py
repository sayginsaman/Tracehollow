"""Review package and reviewer-label summary: labels are validated, never invented."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from app.ai.evaluation import review


def _chunk(label: str, quote: str) -> dict[str, Any]:
    return {
        "label": label,
        "type": "chunk",
        "evidence_key": "report",
        "evidence_title": "Report",
        "quote": quote,
        "tool": None,
        "passage": f"Context before. {quote} Context after.",
        "json_pointer": None,
        "source_published_at": "2026-09-05T12:00:00+00:00",
        "collected_at": "2026-09-06T10:00:00+00:00",
        "tool_arguments": None,
        "tool_result": None,
    }


def _summary() -> dict[str, Any]:
    def question(
        qid: str, expectation: str, status: str, claims: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "id": qid,
            "split": "holdout" if qid.startswith("h") else "development",
            "category": "supported_fact",
            "expectation": expectation,
            "question": f"Question {qid}?",
            "reference_answer": "Reference.",
            "answer_status": status,
            "error_code": None,
            "run_status": "completed",
            "claims": claims,
            "limitations": [],
            "coverage_notes": ["1 connector run(s) in this case were partial."],
            "validation": {
                "claims_removed": [
                    {
                        "kind": "fact",
                        "reason": "no_verified_citation",
                        "citation_labels": ["E9"],
                        "text": "An unverifiable statement the reader never saw.",
                    }
                ]
            },
        }

    tool = {
        "label": "T1",
        "type": "tool",
        "evidence_key": None,
        "evidence_title": None,
        "quote": None,
        "tool": "count_evidence",
        "passage": None,
        "json_pointer": None,
        "source_published_at": None,
        "collected_at": None,
        "tool_arguments": {"kind": "json"},
        "tool_result": {"description": "JSON evidence records", "count": 2},
    }
    return {
        "dataset_version": "test",
        "generated_at": "2026-09-15T00:00:00+00:00",
        "providers": "test",
        "prompt_versions": {"plan": "p", "answer": "a"},
        "results": [
            question(
                "q01",
                "answerable",
                "answered",
                [
                    {"kind": "fact", "text": "A.", "citations": [_chunk("E1", "A is true.")]},
                    {"kind": "count", "text": "2 records.", "citations": [tool]},
                ],
            ),
            question(
                "h01",
                "answerable",
                "partially_answered",
                [
                    {"kind": "conflict", "text": "B or C.", "citations": [_chunk("E2", "B.")]},
                    {"kind": "inference", "text": "Maybe D.", "citations": []},
                ],
            ),
            question(
                "q02",
                "unanswerable",
                "insufficient_evidence",
                [{"kind": "insufficient", "text": "Not stated.", "citations": []}],
            ),
        ],
    }


def _label(
    run: Path,
    reviewer: str,
    reviewer_type: str,
    claims: dict[str, str],
    answers: dict[str, str],
) -> None:
    package = run / "review"
    for source, target, key, column, labels in (
        ("claims.csv", f"claims-reviewed-{reviewer}.csv", "item_id", "support_label", claims),
        (
            "questions.csv",
            f"questions-reviewed-{reviewer}.csv",
            "question_id",
            "answer_label",
            answers,
        ),
    ):
        with (package / source).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        for row in rows:
            if row[key] in labels:
                row[column] = labels[row[key]]
                row["reviewer_id"] = reviewer
                row["reviewer_type"] = reviewer_type
                row["reviewed_at"] = "2026-09-16"
        with (package / target).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


@pytest.fixture
def run(tmp_path: Path) -> Path:
    counts = review.build_package(_summary(), tmp_path / "review")
    assert counts == {
        "claims": 5,
        "support_denominator": 3,
        "questions": 3,
        "removed_claims": 3,
    }
    return tmp_path


def test_claims_the_validator_removed_are_published_separately_and_not_reviewed(
    run: Path,
) -> None:
    with (run / "review" / "removed-claims.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    # Each removal carries its reason and its text, so the filter itself can be judged.
    assert all(row["removal_reason"] for row in rows)
    # A claim removed for containing a secret keeps no text; every other removal keeps its own.
    assert all(
        row["removed_text"] or row["removal_reason"] == "secret_value_detected" for row in rows
    )
    # They are not in claims.csv, so they cannot reach a support denominator.
    with (run / "review" / "claims.csv").open(encoding="utf-8-sig") as handle:
        claim_ids = {row["item_id"] for row in csv.DictReader(handle)}
    assert not claim_ids & {row["item_id"] for row in rows}
    assert "removed-claims.csv" in (run / "review" / "README.md").read_text(encoding="utf-8")


def test_package_carries_passages_dates_tool_results_and_empty_reviewer_columns(run: Path) -> None:
    with (run / "review" / "claims.csv").open(encoding="utf-8-sig") as handle:
        rows = {row["item_id"]: row for row in csv.DictReader(handle)}
    fact = rows["q01-c0"]
    assert "Context before. A is true. Context after." in fact["cited_passages"]
    assert "published 2026-09-05" in fact["citations"]
    assert "quote: «A is true.»" in fact["citations"]
    assert rows["q01-c1"]["cited_passages"] == "[T1] JSON evidence records: 2"
    assert [rows[i]["in_support_denominator"] for i in sorted(rows)] == [
        "yes",
        "no",
        "yes",
        "yes",
        "no",
    ]
    assert "removed before display: no_verified_citation x1" in fact["validator_notes"]
    assert all(
        row.get(column, "") == "" for row in rows.values() for column in review.REVIEWER_COLUMNS
    )


def test_no_labels_leaves_the_criterion_pending(run: Path) -> None:
    result = review.summarize(run)
    assert result["prd_phase3_criterion5"] == "pending: no human reviewer labels"
    assert result["human_reviews"] == []


def test_incomplete_human_review_reports_counts_but_no_rate(run: Path) -> None:
    _label(run, "analyst-1", "human", {"q01-c0": "supported"}, {"q01": "complete"})
    result = review.summarize(run)
    support = result["human_reviews"][0]["claim_support"]
    assert support["complete"] is False
    assert support["claim_support_rate"] is None
    assert support["labels"] == {"supported": 1, "unlabelled": 2}
    assert result["prd_phase3_criterion5"] == "pending: human review incomplete"


def test_complete_human_review_computes_rate_on_fact_count_conflict_only(run: Path) -> None:
    _label(
        run,
        "analyst-1",
        "human",
        {
            "q01-c0": "supported",
            "q01-c1": "supported",
            "h01-c0": "partially_supported",
            "h01-c1": "acceptable_inference",
            "q02-c0": "appropriate_abstention",
        },
        {"q01": "complete", "h01": "incomplete", "q02": "appropriate_abstention"},
    )
    result = review.summarize(run)
    measures = result["human_reviews"][0]
    assert measures["claim_support"]["claim_support_rate"] == round(2 / 3, 4)
    assert measures["claim_support"]["meets_target"] is False
    assert measures["claim_support"]["by_split"]["holdout"]["claim_support_rate"] == 0.0
    assert measures["inference"] == {"acceptable_inference": 1}
    assert measures["questions"]["unanswerable"] == {
        "questions": 1,
        "appropriate_abstention": 1,
        "answered_without_support": 0,
    }
    assert result["prd_phase3_criterion5"] == "not met by at least one complete human review"


def test_model_review_never_counts_as_human_review(run: Path) -> None:
    labels = {"q01-c0": "supported", "q01-c1": "supported", "h01-c0": "supported"}
    _label(run, "assistant-model", "model", labels, {"q01": "complete"})
    result = review.summarize(run)
    assert result["human_reviews"] == []
    assert result["non_human_reviews"][0]["claim_support"]["claim_support_rate"] == 1.0
    assert result["prd_phase3_criterion5"] == "pending: no human reviewer labels"
    assert "never count toward PRD criterion 5" in review.render_markdown(result)


def test_labels_that_do_not_fit_the_claim_kind_are_rejected(run: Path) -> None:
    _label(run, "analyst-1", "human", {"q02-c0": "supported"}, {"q02": "complete"})
    with pytest.raises(review.ReviewError, match=r"q02-c0 \(insufficient\) has label 'supported'"):
        review.summarize(run)


def test_edited_claim_text_is_rejected(run: Path) -> None:
    _label(run, "analyst-1", "human", {"q01-c0": "supported"}, {"q01": "complete"})
    path = run / "review" / "claims-reviewed-analyst-1.csv"
    path.write_text(path.read_text(encoding="utf-8-sig").replace("A.,", "A (edited).,"), "utf-8")
    with pytest.raises(review.ReviewError, match="edited non-reviewer columns"):
        review.summarize(run)


def test_missing_reviewer_identity_is_rejected(run: Path) -> None:
    _label(run, "", "human", {"q01-c0": "supported"}, {"q01": "complete"})
    renamed = run / "review" / "claims-reviewed-anonymous.csv"
    (run / "review" / "claims-reviewed-.csv").rename(renamed)
    with pytest.raises(review.ReviewError, match="reviewer_id"):
        review.summarize(run)
