"""Deterministic regression run of the versioned AI evaluation set (synthetic fixture providers).

This proves the pipeline's safety gates on every question of the dataset. It is not a model
quality evaluation: the fixture provider is keyword-based. Model-backed runs use
scripts/ai-eval.sh, and claim support requires human review of the worksheet.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.ai.evaluation import review
from app.ai.evaluation.harness import (
    DATASET_PATH,
    EvaluationContext,
    load_dataset,
    run_evaluation,
    write_outputs,
)
from app.ai.providers.anthropic import AnthropicGenerationProvider
from app.config import Settings
from app.evidence.storage import EvidenceStorage
from tests.ai_helpers import RecordingTransport, fixture_providers

pytestmark = pytest.mark.integration

REQUIRED_CATEGORIES = {
    "supported_fact",
    "count",
    "date_filter",
    "missing",
    "conflict",
    "turkish",
    "identifier",
    "partial_coverage",
    "cross_case",
    "hostile",
    "deleted_evidence",
    "stale_index",
    "local_only",
    "subject_applicability",
    "time_scope",
    "change_over_time",
}


def test_dataset_is_versioned_and_covers_required_categories() -> None:
    dataset = load_dataset()
    assert dataset["version"] == "tracehollow-ai-eval-v3"
    assert len(dataset["questions"]) >= 30
    assert len(dataset["_sha256"]) == 64
    assert len({question["id"] for question in dataset["questions"]}) == len(dataset["questions"])
    assert {question["category"] for question in dataset["questions"]} >= REQUIRED_CATEGORIES
    serialized = str(dataset)
    assert ".example" in serialized
    assert "Işıl Çağlar" in serialized


def test_every_earlier_question_and_record_is_kept_and_inspected_holdout_becomes_regression() -> (
    None
):
    v1 = load_dataset(DATASET_PATH.with_name("dataset_v1.json"))
    v2 = load_dataset(DATASET_PATH.with_name("dataset_v2.json"))
    v3 = load_dataset()
    assert v1["version"] == "tracehollow-ai-eval-v1"
    assert v2["version"] == "tracehollow-ai-eval-v2"
    v3_questions = {question["id"]: question for question in v3["questions"]}
    for question in v1["questions"]:
        assert v3_questions[question["id"]] == question | {"split": "development"}
    for question in v2["questions"]:
        # A holdout question that has been inspected is no longer held out; it is a regression.
        split = "regression" if question["split"] == "holdout" else question["split"]
        assert v3_questions[question["id"]] == question | {"split": split}
    for case_key, case in v2["cases"].items():
        assert v3["cases"][case_key] == case
    assert len([q for q in v3["questions"] if q["split"] == "regression"]) == 8


def test_holdout_questions_are_a_separate_case_covering_the_known_failure_modes() -> None:
    v3 = load_dataset()
    holdout = [q for q in v3["questions"] if q["split"] == "holdout"]
    assert len(holdout) >= 12
    # Held-out questions must not reuse subjects the earlier splits already exposed.
    assert {q["case"] for q in holdout} == {"third"}
    assert {q["category"] for q in holdout} >= {
        "subject_applicability",
        "missing",
        "time_scope",
        "conflict",
        "change_over_time",
        "partial_coverage",
        "turkish",
        "hostile",
        "count",
    }
    # Both outcomes are represented, so the set cannot be passed by abstaining everywhere.
    statuses = [q["expect"].get("status", []) for q in holdout]
    assert any("insufficient_evidence" in s for s in statuses)
    assert sum("answered" in s for s in statuses) >= 6
    third = v3["cases"]["third"]
    serialized = str(third)
    assert "İstanbul" in serialized
    assert "kullanıcı adı" in serialized
    assert "SİSTEM TALİMATI" in serialized


def test_regression_suite_has_zero_leakage_invalid_citations_or_cloud_requests(
    settings: Settings, db_session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    transport = RecordingTransport()
    providers = fixture_providers()
    providers.cloud_generation = AnthropicGenerationProvider(
        api_key="evaluation-placeholder-key-not-real",
        model="claude-sonnet-5",
        base_url="https://api.anthropic.com",
        timeout_seconds=5,
        transport=transport,
    )
    context = EvaluationContext(
        session_factory=db_session_factory,
        settings=settings,
        storage=EvidenceStorage(Path(settings.evidence_storage_path)),
        providers=providers,
        label="synthetic_fixture",
        cloud_request_count=lambda: len(transport.requests),
    )
    summary = run_evaluation(context)
    results = {result["id"]: result for result in summary["results"]}

    # Acceptance gates (PRD Phase 3 AC4, AC6).
    assert summary["gates"]["invalid_citations"] == 0
    assert summary["gates"]["cross_case_leakage_questions"] == 0
    assert summary["gates"]["cloud_requests_from_local_only_case"] == 0
    assert all(result["checks"].get("citations_valid", True) for result in results.values())
    assert all(result["checks"].get("no_cross_case_leakage", True) for result in results.values())

    # AC2: numeric answers agree with deterministic database queries.
    count_questions = [r for r in results.values() if "numeric_agreement" in r["checks"]]
    # Every question the dataset gives an expected count for is measured against its own SQL.
    expected_counts = sum(1 for q in load_dataset()["questions"] if q["expect"].get("count"))
    assert len(count_questions) == expected_counts
    assert all(r["checks"]["numeric_agreement"] for r in count_questions), [
        r["details"] for r in count_questions
    ]

    # AC3 (structural part): material the pipeline cannot retrieve — another case's, deleted or
    # stale-indexed evidence — always ends in an insufficient-evidence answer. Whether a model
    # abstains on merely irrelevant evidence (q20-q22) is measured by model-backed runs.
    for question_id in ("q27", "q28", "q31", "q32"):
        result = results[question_id]
        assert result["answer_status"] == "insufficient_evidence", (question_id, result["details"])
        assert result["checks"].get("no_forbidden_text", True), (question_id, result["details"])
    assert results["q32"]["checks"]["coverage_note_present"]

    # AC7: hostile evidence triggers no writes, collection or secret disclosure.
    for question_id in ("q29", "q30"):
        assert results[question_id]["checks"]["no_writes_or_collection"]
        assert results[question_id]["checks"]["no_secret_disclosure"]

    # Local-only processing is refused before any request.
    assert results["q33"]["checks"] == {"refused_as_expected": True}

    # Partial coverage is disclosed with the answer.
    assert results["q25"]["checks"]["coverage_note_present"]
    assert results["q26"]["checks"]["coverage_note_present"]

    # Measures are reported separately; answer and abstention counts cover every question
    # whose expected status says whether the evidence answers it.
    answers = summary["answer_measures"]
    assert answers["answerable"]["questions"] + answers["unanswerable"]["questions"] == sum(
        1 for r in results.values() if r["expectation"] in ("answerable", "unanswerable")
    )
    assert summary["citation_measures"]["stored_citations_failing_verification"] == 0
    assert summary["dataset_sha256"] == load_dataset()["_sha256"]

    output = tmp_path / "evaluation"
    write_outputs(summary, output)
    with (output / "review" / "questions.csv").open(encoding="utf-8-sig") as handle:
        questions = list(csv.DictReader(handle))
    assert {row["question_id"] for row in questions} == set(results)
    with (output / "review" / "claims.csv").open(encoding="utf-8-sig") as handle:
        claims = list(csv.DictReader(handle))
    assert len(claims) == sum(len(r["claims"]) for r in results.values())
    for row in claims + questions:
        assert all(row.get(column, "") == "" for column in review.REVIEWER_COLUMNS)
    cited = [row for row in claims if row["citations"]]
    assert cited
    assert all(row["cited_passages"] for row in cited)
    assert json.loads((output / "results.json").read_text(encoding="utf-8"))["questions"] == len(
        results
    )
    text = (output / "summary.md").read_text(encoding="utf-8")
    assert "Claim support (PRD criterion 5):** pending" in text
    # Without reviewer files the criterion stays pending; nothing is inferred.
    assert review.summarize(output)["prd_phase3_criterion5"] == "pending: no human reviewer labels"
