# Answer relevancy stops gating a deploy

Faithfulness becomes the single judged metric a deploy gates on. Answer relevancy keeps its
score, its row, its run record, its route payload and its console channel, and carries no
verdict. Issue: #285. Related: #270, #274, #276, #277.

The owner passed relevancy on 30 of 30 labelled rows of run 735fb9fa and on 28 of 46 of
0a99f7ab. A gate needs a dimension the owner sometimes fails, or kappa is undefined by
construction and the gate can never be shown to catch anything. Ragas answer relevancy
failed 47 of 49 rows the owner passed; the typed relevance Judge of #276 passed every row
the owner passed.

## Data shape

Nothing migrates. `eval_results` keeps four rows per scenario and relevancy's row changes
values, not columns:

```
metric           score   threshold   binary_verdict
faithfulness     0.94    0.90        true
answer_relevancy 0.42    NULL        NULL      <- was 0.90 / false
context_precision 0.71   NULL        NULL
context_recall   0.66    NULL        NULL
```

`threshold_for("answer_relevancy")` returning None is the single cause. `build_judge_records`
stamps the threshold it returns, `verdict_for` gives None for a None threshold, and every
reader downstream already treats a NULL verdict as "no decision here" because
`context_precision` and `context_recall` have always been in that state.

A scenario's `passed` is `scenario_verdict` over one verdict instead of two, on the route and
in `dataset_verdict_counts`, so `scenarios_passed`, `scenarios_failed` and
`scenarios_unmeasured` all come off faithfulness alone.

Rows written before this change keep the threshold and verdict they were judged against.
`JudgeRecord` stores the gate on the row for exactly this reason, so an old run still reads
as the run it was.

## Call sites

| File | Change |
|---|---|
| `app/services/eval_service.py` | `GATED_METRIC_KEYS = ("faithfulness",)`; `threshold_for` drops its relevancy arm; three comment blocks that call relevancy gated (`RESOLVED_INPUT_METRICS`, the gate definition, `question_resolution_provenance`) |
| `app/core/config.py` | `EVAL_RELEVANCY_THRESHOLD` removed, section comment points at ADR 0014 |
| `apps/api/.env.example` | the commented `EVAL_RELEVANCY_THRESHOLD` line goes with it |
| `app/api/v1/evals.py` | imports `GATED_METRIC_KEYS`, no logic change; the comment at the import and the two docstrings naming "the two GATED metrics" |
| `app/services/deployment_service.py` | `_unmeasured_gated_metrics` iterates the tuple, no logic change; the `_quality_evidence_warning` docstring stops naming relevancy as a metric that can be missing |
| `app/worker/tasks/runtime/deployment.py` | nothing. The collector reads `_fetch_eval_summary_sync`, which reads the record |
| `app/domain/eval_result.py` | nothing. `DatasetOutcome` counts arrive from `dataset_verdict_counts` |
| `app/domain/judge_record.py` | nothing. `scenario_verdict` over one verdict is the same rule |
| `app/domain/verdict.py` | nothing. `golden_failure` reads `scenarios_failed`, which now moves only on faithfulness |
| `app/api/mcp.py` | nothing. `get_eval_results` proxies the v1 route and carries whatever it returns |
| `tests/evals/calibration/calibrate_run.py` | `GATED_METRICS = ("faithfulness",)`; the sheet's instruction line drops its relevancy sentence |
| `tests/evals/calibration/compute_correlation.py` | nothing. `read_second_pass`, the human ceiling and the artifact read whatever dimensions the sheet holds |
| `apps/admin` | nothing. See below |

### What the console reads

`grep` over `apps/admin` for `answer_relevancy`, `gated` and `threshold` finds no type, chart
or component that marks a channel as gated:

- `evalSeries.ts` `EVAL_CHANNELS` lists all four channels for the telemetry chart. All four
  stay reported, so the list is unchanged.
- `eval/page.tsx` prints `s.scores.answer_relevancy` in the ledger and reads `s.passed` for
  the verdict chip. `passed` is computed by the API from the stored verdicts, so the chip
  follows the new set with no client change.
- `TelemetryChart.tsx` draws one horizontal `GATE 0.90` line across the whole chart, not a
  per-channel mark. It already spans two ungated channels.
- `deploy/evalReadiness.ts` counts "scored scenarios the judge never reached a gated verdict
  on" from the API's number.

## Tests

New, in the files that already own each surface:

| Test | File | Asserts |
|---|---|---|
| `TestARelevancyFailureNeverReachesGoldenFailure`, four cases | `tests/unit/test_eval_service.py` | four golden rows failing relevancy and clearing faithfulness reach `(scored 4, passed 4, failed 0)`, `_rule_golden_failure` returns `()`, the relevancy Measurement is still on the record, and a failed faithfulness row still blocks |
| `test_answer_relevancy_has_no_threshold` | `tests/unit/test_eval_service.py` | `threshold_for("answer_relevancy") is None` and `GATED_METRIC_KEYS == ("faithfulness",)` |
| `test_a_failed_relevancy_row_leaves_the_scenario_passing` | `tests/unit/test_eval_service.py` | `dataset_verdict_counts` reads `(1, 0, 0)` over a row scoring 0.01 relevancy |
| `test_a_relevancy_row_that_fails_still_ships_and_still_reports` | `tests/unit/test_eval_routes.py` | `passed` is True and the payload still carries score 0.02 with `verdict` and `threshold` None |
| `test_a_thirty_scenario_run_asks_the_owner_for_thirty_faithfulness_labels` | `tests/unit/test_calibrate_run.py` | 30 rows, every `dimension` faithfulness, 30 distinct scenarios |
| `test_the_unmeasured_gated_metrics_reader_asks_for_faithfulness_alone` | `tests/unit/test_deployment_service.py` | a faithfulness-only dataset is admissible, a relevancy-only one reports `["faithfulness"]` missing |
| `test_a_run_failing_relevancy_on_every_row_is_measured_and_ships` | `tests/unit/test_deployment_service.py` | signal `measured`, recommendation `ship`, no warnings, relevancy Measurement still on the summary |
| `test_a_run_that_scored_no_relevancy_still_ships_on_faithfulness` | `tests/unit/test_deployment_service.py` | the run that blocked before ADR 0014 now ships |

Updated, each naming ADR 0014 where the old reason was relevancy gating:

- `test_eval_service.py`: `test_a_gated_metric_carries_its_threshold_and_its_verdict` renamed
  to `test_the_gated_metric_...` and its relevancy assertions moved into
  `test_an_ungated_metric_carries_neither_a_threshold_nor_a_verdict`;
  `test_an_unscored_metric_is_a_row_with_no_score_and_no_verdict`;
  `test_only_the_two_gated_metrics_have_a_threshold` renamed to
  `test_only_the_gated_metric_has_a_threshold`;
  `test_one_false_gated_verdict_fails_the_scenario`, now failing on faithfulness;
  `test_a_missing_gated_score_is_unmeasured_and_beats_the_failure`.
- `test_eval_routes.py`: `test_passed_flag_true_when_both_stored_verdicts_are_true` renamed to
  `test_passed_flag_true_when_the_stored_gated_verdict_is_true`;
  `test_passed_flag_false_when_a_gated_verdict_is_false`, now failing on faithfulness;
  `test_passed_flag_ignores_ungated_metrics_below_threshold`;
  `test_a_gated_metric_that_is_missing_makes_the_verdict_unknown`, now missing faithfulness;
  `test_an_ungated_metric_carries_no_verdict_and_no_gate`, now over three metrics.
- `test_calibrate_run.py`: the sheet and scoring tests that counted `2 * len(GATED_METRICS)`.
- `test_question_resolution_provenance.py`: the module docstring opened on "`answer_relevancy`
  is in `GATED_METRIC_KEYS`, so its pass rate blocks a deploy".

## Mutations

Each applied to the working tree, run, observed red, restored from a byte snapshot taken
before the edit and checked by sha256, run again, observed green. The branch has no commit
yet, so `HEAD` is not the restore source.

| # | Mutation | Observed red | Restore |
|---|---|---|---|
| a | `GATED_METRIC_KEYS` takes `answer_relevancy` back | 3 failed, 3 passed. `test_every_row_failing_relevancy_leaves_the_golden_set_passing`, `test_a_failed_faithfulness_row_still_blocks`, `test_the_unmeasured_gated_metrics_reader_asks_for_faithfulness_alone` (`assert ['answer_relevancy'] == []`) | sha256 `a40ef5f4...62338` before and after, 6 passed |
| b | `threshold_for` returns `0.90` for `answer_relevancy` | 2 failed, 3 passed. `test_answer_relevancy_has_no_threshold` and `test_a_relevancy_row_that_fails_still_ships_and_still_reports` (`{'verdict': False} != {'verdict': None}`, `{'threshold': 0.9} != {'threshold': None}`) | sha256 `a40ef5f4...62338` before and after, 5 passed |
| c | `calibrate_run.GATED_METRICS` takes both dimensions | 2 failed. `test_a_thirty_scenario_run_asks_the_owner_for_thirty_faithfulness_labels` and `test_the_sheet_has_one_row_per_gated_metric_with_the_text_and_no_verdict` (`Extra items in the left set: 'answer_relevancy'`) | sha256 `d0ba19b3...65622` before and after, 2 passed |

## #276 conflict list

#276 is not on `main`. It adds the typed relevance Judge and per-dimension calibration and
touches four files this branch also touches. Merge order matters; rebase whichever lands
second.

| File | This branch | #276 | Resolution |
|---|---|---|---|
| `app/services/eval_service.py` | `GATED_METRIC_KEYS` shrinks to one entry, `threshold_for` loses its relevancy arm | adds the relevance Judge and, if it keeps a threshold for it, a third arm | keep this branch's tuple and function. A relevance Judge dimension is reported, so it takes no threshold |
| `tests/evals/calibration/calibrate_run.py` | `GATED_METRICS` shrinks, `write_sheet` emits one dimension | per-dimension calibration widens the sheet to the dimensions it calibrates | #276's dimension list wins for the sheet, with faithfulness the only gated entry |
| `tests/evals/calibration/compute_correlation.py` | untouched | per-dimension kappa, artifact shape | no conflict from this branch |
| `app/domain/calibration_status.py` | untouched | per-dimension status | no conflict from this branch |

ADR 0013 is not on `main` either. The sentence #285 asks for, saying the relevance Judge is
reported rather than gated, belongs on 0013 when #276 lands.

## Open, not closed here

ADR 0011's two relevancy provenance rules still block a deploy.
`_relevancy_provenance_cause` in `deployment_service.py` refuses a launch when most
multi-turn rows were scored against a raw follow-up question, and the refusal is not
acknowledgeable. That rule was written because relevancy gated, and #285 does not name it.
It stays on this branch so the change is the one that was asked for; whether a reported-only
metric may refuse a launch over its provenance is the next decision, and it amends ADR 0011.
