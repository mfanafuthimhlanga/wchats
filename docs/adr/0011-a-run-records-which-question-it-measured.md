# 0011: A run records which question it measured, and the deploy gate reads it

Status: accepted. Built in #236 for issue #233 and #242 for issue #235, 2026-09-11 to
2026-09-12. The console half is #240.

## What was decided

Since ADR 0010 a relevancy pass rate can come from rewritten questions, from raw
questions, or from a run where every rewrite failed and fell back. A number that could
mean any of those is not evidence, so every eval run stamps four counts once its scores
are written:

```json
"question_resolution": {
  "relevancy_scored": 41, "multi_turn": 10, "rewritten": 8, "raw_question_fallback": 2
}
```

The counts nest. `rewritten` and `raw_question_fallback` partition `multi_turn`, which
sits inside `relevancy_scored`. `QuestionResolution` in `app/domain/eval_result.py`
refuses construction when the nesting breaks or the halves do not add up, so both shares
the gate divides are always interpretable.

The denominator is rows the Judge returned an attributed relevancy for, not rows handed
to the resolver and not rows sent to the Judge. The Judge can return fewer rows than it
was sent, and a metric can raise for one sample and yield `None`; such a row has no
relevancy to qualify.

`EvalResult` is the authority. `eval_runs.config` carries the same patch for readers of
the loose record, written once, in the scoring branch and after `write_eval_results`. A
run below the measurement floor writes no scores and leaves the key absent. A stored
record with the key removed reads as four zeros, which is a reading of an older run and
not the fail-open that `agent_invoked` refuses.

## The two rules

`app/services/deployment_service.py` lifts the counts onto the summary and refuses in
two cases. Either one blocks; the owner cannot acknowledge past it.

| Rule | Fires when | Floor |
|---|---|---|
| contaminated number | `raw_question_fallback / relevancy_scored > MAX_RAW_QUESTION_FALLBACK_SHARE` | none |
| resolver failure | `raw_question_fallback / multi_turn > MAX_RESOLVER_FAILURE_SHARE` | `multi_turn >= MIN_MULTI_TURN_FOR_RESOLVER_RATE` |

Both shares are 0.5 and the floor is 10. They are assertions, not measurements. Nothing
has yet measured how far relevancy moves when a follow-up is scored raw, and #58's first
calibration is where that number comes from. A majority is the one threshold defensible
without inventing precision; ten is the golden-pair floor #19 already holds.

The thresholds live beside the rules rather than in `verdict.py`. ADR 0007's table
covers signals `decide()` folds; these refuse before `decide()` sees a relevancy number
at all, on the same principle that a missing measurement is never a pass.

## Consequences a reader will meet

- On a corpus where multi-turn rows are a minority the contaminated rule cannot fire,
  since the fallback count is bounded by `multi_turn`. The resolver rule is the one that
  bites, and only from ten multi-turn rows upward. A staging run meant to observe these
  rules needs at least ten scenarios carrying turns.
- Retuning either constant is a one-line change plus #58's number in its comment. Moving
  them to `verdict.py` would be a separate decision and belongs with the next revision of
  ADR 0007's table.
