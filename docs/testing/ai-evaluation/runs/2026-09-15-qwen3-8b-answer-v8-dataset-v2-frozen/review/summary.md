# Review summary — 2026-09-15-qwen3-8b-answer-v8-dataset-v2-frozen

- Claim-support denominator (fact, count and conflict claims): 59
- Questions: 41
- PRD Phase 3 criterion 5: **pending: no human reviewer labels**

## Human reviews (count toward PRD criterion 5)

None.

## Non-human reviews (not human review; never count toward PRD criterion 5)

### assistant-claude-opus-5 (model)

- Claim support: 83.0%; labels {'mislabelled': 6, 'partially_supported': 1, 'supported': 49, 'unsupported': 3}
- By split: {"development": {"denominator": 44, "labels": {"mislabelled": 2, "partially_supported": 1, "supported": 40, "unsupported": 1}, "claim_support_rate": 0.9091}, "holdout": {"denominator": 15, "labels": {"mislabelled": 4, "supported": 9, "unsupported": 2}, "claim_support_rate": 0.6}}
- Inference claims: {}
- Insufficient claims: {'appropriate_abstention': 12, 'mislabelled': 1}
- Answerable questions: {'questions': 28, 'unnecessary_abstention': 0, 'complete': 23, 'incomplete': 2, 'incorrect': 3}
- Unanswerable questions: {'questions': 9, 'appropriate_abstention': 6, 'answered_without_support': 3}
- Conflict handling: {'both_sides_unlabelled': 4}

