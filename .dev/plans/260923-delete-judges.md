# Delete the judge modules and the ragas plumbing (#296, ADR 0015 stage 2)

Branch `chore/delete-judges` off `main` at `d956e0a`. ADR 0015 took every judge off the
gate path on 2026-09-23; this removes the code that is on disk and not called, and the
one judge still called, the sampled live-turn faithfulness in `retrieval_eval.py`, moves
onto the same grounding rule. After this, no module under `app/` builds a client for a
judge purpose and `ragas` is not a dependency.

## What goes

| Delete | Because |
|---|---|
| `app/services/judge_llm.py`, `relevance_judge.py`, `question_resolution.py`, `faithfulness_metric.py` | nothing on the gate path calls them |
| `eval_service`: `_VoyageRagasEmbedding`, `_build_instructor_llm`, `_build_ragas_metrics`, `_resolved_inputs`, `_ragas_cell`, `_relevance_cell`, `_score_samples`, the ragas half of `_judge_samples`, `question_resolution_provenance`, `_METRIC_ASCORE_ARGS`, `RAGAS_COLUMN_BY_METRIC`, `RESOLVED_INPUT_METRICS`, `JUDGE_PURPOSES`, `JUDGE_PURPOSE_BY_METRIC`, `RELEVANCE_METRIC`, the `judge_key_spread` log field | the rule reads dicts; no dataset, no client |
| `model_client`: routes `judge_faithfulness`, `judge_answer_relevancy`, `judge_context_precision`, `judge_context_recall`, `judge_relevance`, `eval_question_resolution`, `judge_retrieval_faithfulness`; `PURPOSE_KEY_SETTINGS` and `judge_key_spread` | no purpose bills under them |
| `config`: the five `OPENAI_API_KEY_JUDGE_*` fields | read only by `PURPOSE_KEY_SETTINGS` |
| `eval.py`: the `question_resolution_provenance` stamp in `_score_run` | the resolver is gone; `EvalResult.question_resolution` keeps its default |
| `retrieval_eval.py`: `_build_instructor_llm`, `_build_faithfulness_metrics`, `_compute_ragas_faithfulness`, `JUDGE_PURPOSE`, `judge_identity()` | replaced by `ground(response, chunks).score` and `GROUNDING_IDENTITY` |
| `METRIC_KEYS` shrinks to `("faithfulness",)` | the other four were written unscored on every run since ADR 0015 |
| `ragas` in `pyproject.toml`, the langchain-community and related pins that exist only for it | nothing imports it |
| `tests/evals/calibration/benchmark/judge_rows.py` | superseded by `ground_rows.py` |
| CLAUDE.md rule 4 | no ragas API to hold to |
| console `EVAL_CHANNELS`: `answer_relevancy`, `context_recall`, `context_precision` | channels that measure nothing |

## What stays, and why

- `calibration_judge` route and `tests/evals/judge.py`: the M4 harness, not a gate.
- `EvalResult.question_resolution` and its reader in `deployment_service`: old records
  carry the counts; new records carry the default zeros. #286 owns the rule itself.
- `eval_results` rows with the four retired metric names: history. Nothing deletes rows.

## Rows that carry a retired metric

`eval_results.metric` holds `answer_relevancy`, `context_precision`, `context_recall` and
`ragas_answer_relevancy` on every run before this lands, and stored `EvalResult` records
carry them under `metrics`. `RETIRED_METRIC_KEYS` in `app/domain/eval_result.py` names
the four; `DatasetOutcome.from_payload` drops them, the validator refuses any other
unknown name, and `metrics_of` never reports them. A reader that wants the old numbers
reads the rows directly.

## Order

1. Backend deletion and tests, one agent, owns `apps/api/**` except `pyproject.toml`'s
   lock step.
2. Console channels, one agent, owns `apps/admin/**`.
3. `uv lock` and `uv sync --extra dev --extra pipeline`, `gates.py full`, admin gates,
   CLAUDE.md rule 4, ADR 0015 "what follows" line, this plan's numbers.
4. Adversary pass, then PR against `main`.

## Gates

- Unit suite before, on `d956e0a`: 1 failed (the ragas import-path test), 5503 passed, 14
  skipped. After: green, and the count drops by the deleted tests.
- `grep -rn "judge_llm\|relevance_judge\|question_resolution_provenance\|faithfulness_metric\|judge_key_spread\|PURPOSE_KEY_SETTINGS\|OPENAI_API_KEY_JUDGE\|from ragas\|import ragas" apps/api/app apps/api/scripts`
  returns nothing. `run_ragas_eval` keeps its name, and `question_resolution` stays as the
  reader of old records.
- `PURPOSE_ROUTES` has no `judge_*` key.
- A stored `EvalResult` payload with the four retired keys loads; one with `nonsense` refuses.
- `retrieval_eval` writes `judge_identity` as `rule:grounding` and calls no client.
- admin: `tsc --noEmit`, `test:unit`, `check:chart-render`.
