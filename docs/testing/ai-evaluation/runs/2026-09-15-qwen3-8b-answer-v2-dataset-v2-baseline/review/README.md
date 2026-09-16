# Review package — tracehollow-ai-eval-v2

Run generated 2026-09-15T22:12:23.293631+00:00 with ollama:qwen3:8b (prompts {'plan': 'plan-v2', 'answer': 'answer-v2'}).

- `claims.csv`: 92 claims; 56 of kind fact, count or conflict form the claim-support denominator.
- `questions.csv`: 41 questions (answer, abstention and conflict labels).

Follow the rubric in `docs/testing/ai-evaluation/README.md#human-review`. Copy both files to `claims-reviewed-<reviewer_id>.csv` and `questions-reviewed-<reviewer_id>.csv`, fill in only the reviewer columns, then run `python -m app.ai.evaluation.review summarize <this run directory>`.

Status: no reviewer labels are included when this package is generated.
