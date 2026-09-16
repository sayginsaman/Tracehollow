# Review package — tracehollow-ai-eval-v3

Run generated 2026-09-16T21:13:57.749911+00:00 with ollama:qwen3:8b (prompts {'plan': 'plan-v2', 'answer': 'answer-v14'}).

- `claims.csv`: 97 claims; 49 of kind fact, count or conflict that answer the question form the claim-support denominator. Claims marked `answers_question=no` are context: judge them with the same labels, but they are counted separately.
- `questions.csv`: 55 questions (answer, abstention and conflict labels).
- `removed-claims.csv`: 6 claims the validator removed before display, with the reason. They are not reviewed for support and are not in any denominator; they are here so the filter can be checked for removing too much or too little.

Follow the rubric in `docs/testing/ai-evaluation/README.md#human-review`. Copy both files to `claims-reviewed-<reviewer_id>.csv` and `questions-reviewed-<reviewer_id>.csv`, fill in only the reviewer columns, then run `python -m app.ai.evaluation.review summarize <this run directory>`.

Status: no reviewer labels are included when this package is generated.
