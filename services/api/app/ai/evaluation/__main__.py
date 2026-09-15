"""Run the AI evaluation against a disposable database.

Usage (normally through scripts/ai-eval.sh, which creates the disposable database)::

    python -m app.ai.evaluation --providers configured --output ../../evaluation-output

The target database name must contain "eval" or "test": the run writes synthetic cases, users
and evidence and must never be pointed at an investigation database. A cloud provider, if any,
is replaced by a recording transport so no case material can leave the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.ai.evaluation.harness import EvaluationContext, load_dataset, run_evaluation, write_outputs
from app.ai.policy import build_providers
from app.ai.providers.anthropic import AnthropicGenerationProvider
from app.ai.providers.fixture import FixtureEmbeddingProvider, FixtureGenerationProvider
from app.config import get_settings
from app.db.session import create_db_engine, create_session_factory
from app.evidence.storage import EvidenceStorage


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--providers", choices=["fixture", "configured"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--only", default="", help="comma-separated question ids (smoke runs)")
    args = parser.parse_args()

    settings = get_settings()
    if not any(marker in settings.database_name for marker in ("eval", "test")):
        print("Refusing to run: the database name must contain 'eval' or 'test'.", file=sys.stderr)
        return 2

    import httpx2

    recorded: list[httpx2.Request] = []

    def refuse(request: httpx2.Request) -> httpx2.Response:
        recorded.append(request)
        return httpx2.Response(500)

    transport = httpx2.MockTransport(refuse)
    providers = build_providers(settings)
    if args.providers == "fixture":
        providers.local_generation = FixtureGenerationProvider()
        providers.embeddings = FixtureEmbeddingProvider()
    providers.cloud_generation = AnthropicGenerationProvider(
        api_key="evaluation-placeholder-key-not-real",
        model=settings.ai_cloud_model,
        base_url="https://api.anthropic.com",
        timeout_seconds=5,
        transport=transport,
    )
    label = (
        "synthetic_fixture"
        if args.providers == "fixture"
        else f"{providers.local_generation.name}:{providers.local_generation.model}"
    )
    context = EvaluationContext(
        session_factory=create_session_factory(create_db_engine(settings)),
        settings=settings,
        storage=EvidenceStorage(settings.evidence_storage_path),
        providers=providers,
        label=label,
        cloud_request_count=lambda: len(recorded),
    )
    dataset = load_dataset(args.dataset) if args.dataset else load_dataset()
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        dataset["questions"] = [q for q in dataset["questions"] if q["id"] in wanted]
    summary = run_evaluation(context, dataset)
    write_outputs(summary, args.output)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "providers",
                    "questions",
                    "questions_passing_all_automated_checks",
                    "gates",
                    "by_category",
                )
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
