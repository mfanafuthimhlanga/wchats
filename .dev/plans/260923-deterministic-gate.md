# The deploy gate reads rules, not judges

Owner decision 2026-09-23. Faithfulness keeps its column and its 0.80 gate; the instrument
behind it becomes `app/domain/grounding.py`, a word-overlap rule measured on the planted
benchmark with no model call. The judges leave the gate path. Branch `feat/deterministic-gate`.

## Shape

Precedent: #274 swapped the instrument behind `answer_relevancy` and kept the column name.
The same move here keeps the console, the routes, `eval_results`, the claims review and the
deploy rules untouched.

- `app/domain/grounding.py`: `ground(response, contexts) -> Grounding` with `.score` (grounded
  share of sentences) and `.claims` (one `Claim` payload per sentence, reason readable). Pure.
  `GROUNDING_IDENTITY` is the `JudgeIdentity` a rule-scored row stores: model `rule:grounding`.
- `eval_service`: the faithfulness cell comes from `ground`, no ragas; `judge_identity_for`
  answers `GROUNDING_IDENTITY` for it; the default metric set the eval task scores is
  faithfulness alone, the other four rows written unscored as an unmeasured dimension always was.
- `calibration_service`: a rule identity is calibrated by construction; the pinned recall test is
  its calibration artifact.
- The eval task stops calling the question resolver. The rejudge task scores faithfulness alone.
- ADR 0015 records it. `tests/unit/test_grounding.py` pins the rule and the benchmark numbers.

## Measured 2026-09-23, `ground_rows.py`, each plant scored on its own

| variant | planted flagged | real answers at 0.80 | sentences flagged |
|---|---|---|---|
| best passage, floor 0.4 | 9 of 10 | 8 of 30 | 103 of 260 |
| best passage, floor 0.5 | 9 of 10 | 6 of 30 | 127 of 260 |
| best passage, floor 0.6 | 10 of 10 | 1 of 30 | 167 of 260 |

Floor 0.4 is the rule. The first measurement scored each plant inside its answer and read
10 of 10 at 0.5; a neighbouring fragment on one line had earned the flag. The adversary pass
caught it.

## Stages

1. This branch: the rule, the wiring above, the tests, the ADR. The judge modules stay on
   disk and are not called by the eval task.
2. Delete `judge_llm`, `relevance_judge`, `question_resolution`, `faithfulness_metric`, the
   ragas plumbing and dependency, the rejudge task's judge path; drop the four unscored
   columns from `METRIC_KEYS` with a migration note. One issue.
3. Red team severity by rule: `classify_severity` is a model call and Row 6 reads its
   `critical`. Map vector and verdict tag to severity in a table. One issue.

## Gates

- unit: the rule (sentence split, passages, stem floor, density tie, number rule, empty
  contexts, the decline rule, the number rule's thousands separator, a real tie), the benchmark
  recall pinned at 9 of 10 and the table above pinned, the eval
  service wiring (identity, cell, no ragas call), calibration of a rule identity.
- `scripts/gates.py full`.
- mutation: drop the number rule, observe red (number test, benchmark pin); restore. Observed
  2026-09-23 on the first cut: 3 failed, 13 passed; restored byte-identical; 16 passed. The
  adversary's second pass repeated it on the final cut: 2 red.
