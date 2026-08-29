# 260829, EvalResult and the Judge records (#51)

Branch `feat/eval-result`, six slices on one branch, adversary and the tier-2 judge once
at the end. The execution plan is the 2026-08-29 comment on #51. Closes nothing yet;
#51 closes when slice 6 lands.

## Slice 1, the record and its writer

Three commits: the domain record, tenant migration 0022, the writer plus the task change.

### Files

- `apps/api/app/domain/eval_result.py` (new). `Measurement`, `Invocation`,
  `DatasetOutcome`, `Cost`, `EvalResult`, `InvocationStatus`, `InvalidEvalResult`,
  `cost_of_run`, `CONTEXT_PROXY_VERSION`, `COST_UNKNOWN`, `RULE_VERSION`.
- `apps/api/alembic_tenant/versions/0022_eval_run_result.py` (new). `eval_runs.result`
  JSONB, nullable, 0021's shape.
- `apps/api/app/services/eval_service.py`. `build_eval_result`, `dataset_outcomes`,
  `read_run_ledger`, `run_judge_identity`, `served_agent_model`, `write_eval_result`,
  `read_eval_result`.
- `apps/api/app/worker/tasks/runtime/eval.py`. `run_eval_suite` builds the record, writes
  it, and returns it through the new pure `_run_report`.
- `apps/api/scripts/gates.py`. `run_eval_suite` pin 611 to 596.
- Tests: `tests/unit/test_eval_result_type.py` (new, 56), `test_migration_tenant_0022.py`
  (new, 20), plus additions to `test_eval_service.py` and `test_eval_task.py` and the head
  assertion moved out of `test_migration_tenant_0021.py`.

Readers are untouched. `api/v1/evals.py` and `deployment_service` still recompute; slices
3 and 4 point them at `read_eval_result`.

### Decisions

**`InvocationStatus` holds two values, `measured` and `unknown`.** They are what
`summarise_agent_invocation` returns (`eval_service.py:757`, the `status` key, off
`AGENT_INVOCATION_MEASURED` / `AGENT_INVOCATION_UNKNOWN` at `eval_service.py:593-595`).
`AGENT_INVOCATION_NOT_STARTED` is not in the enum. It describes a run whose invocation
phase never reported, and such a run reaches no record at all: the absence is
`eval_runs.result` being NULL, which `read_eval_result` returns as None. A third value
would give a written record two ways to say the same nothing.

**The counters are the summariser's own names.** The plan named `invoked`, `errored` and
`timed_out`; the summariser produces `responded`, `failed` and `empty`, and reports no
timeout count at all (a timeout lands in `failed` and in the `errors` histogram). The
record takes `valid`, `attempted`, `responded`, `scorable`, `failed`, `empty` plus #103's
`responses_deflected`, `scored_responses_deflected` and `deflection_detectors`. Renaming
any of them would put a second vocabulary on one observation.

**`requested_model` is the agent's, not the judge's.** `AGENT_TURN_MODEL`, the constant
`build_eval_run_config` already stamps as `config["model_id"]` and the one
`PURPOSE_ROUTES["agent_turn"]` is built from. The judges' model is inside
`judge_identity`, so reading it into `requested_model` as well would be one field holding
two claims.

**`served_model` comes off the run's own ledger rows, and is None twice over.** The
distinct `served_model` values among `model_calls` rows for this run whose purpose is
`agent_turn`. Exactly one is reported. None when there are no such rows, and None when
two disagree, with a warning naming them. A run served by two models has no single served
model, and picking one would invent it.

**`judge_identity` is run-level and collapses to None on disagreement.**
`judge_identity_for` is per metric and the four routes could name four Judges. All four
name one today. `run_judge_identity` reports it only when the four agree and are complete,
and logs the identities when they do not. `build_eval_run_config` still copies
`route_for(JUDGE_PURPOSES[0])` into `config` as a two-field partial identity; the
2026-08-25 comment on #51 asks for that copy to be completed or deleted, and it is slice
2's, beside the per-call identity on the `eval_results` rows.

**The per-dataset counts all come from `summarise_run_validity`.** The plan sourced
`attempted` from `dataset_composition`. Both count the same fetched rows by the same rule,
so taking one number from each would be two derivations of one figure, which is the defect
this record removes. Composition still stamps `config["dataset"]` and is unchanged.

**`cost_of_run` lives in the domain beside `Cost`.** No rows is `COST_UNKNOWN`, never
zero: the ledger hook fails open, so an unrecorded run costs as much as a recorded one and
only the log tells them apart. A model the book refuses costs the run its whole money
figure rather than a partial sum, and the rand fails separately from the dollars. Both
rules are `usage_rollup`'s, one table over. Decimal arithmetic, float at the jsonb
boundary.

**`_run_report` is a new pure function in `eval.py`.** Extracting it is what let
`run_eval_suite` end below its pin rather than above it: the record cost the task four
call lines and the report is thirteen keys with the reasoning that used to sit inline.

**Signature style follows the neighbours, not the plan's sketch.** The plan wrote
`write_eval_result(conn, run_id, result)`. Every writer in `eval_service` takes a dsn last
and owns its connection (`update_eval_run_config`, `update_eval_run_status`,
`write_eval_results`), and the task holds a dsn rather than a connection, so the shipped
pair is `write_eval_result(run_id, result, conn_str)` and
`read_eval_result(run_id, conn_str)`.

### Observed

Migration round trip, local `wchats_tenant_probe` through `run_tenant_migrations`:

```
before                 revision=0021  eval_runs.result=None
after upgrade head     revision=0022  eval_runs.result=('jsonb', 'YES', None)
after downgrade 0021   revision=0021  eval_runs.result=None
after re-upgrade       revision=0022  eval_runs.result=('jsonb', 'YES', None)
```

Mutation, `dataset_outcomes` dropping the exploratory dataset, restored with
`git checkout HEAD --`:

```
MUTATION APPLIED: dataset_outcomes drops the exploratory dataset
FAILED test_eval_task.py::TestGoldenSetIsHeldFixed::test_the_run_reports_the_two_datasets_separately
FAILED test_eval_task.py::TestGoldenSetIsHeldFixed::test_a_tenant_without_the_dataset_column_degrades_and_says_so
FAILED test_eval_task.py::TestValidityDenominators::test_the_run_reports_all_three_counts
FAILED test_eval_task.py::TestValidityDenominators::test_scored_is_below_valid_when_ragas_returns_fewer_rows
FAILED test_eval_task.py::TestValidityDenominators::test_zero_valid_scenarios_reports_unknown_never_a_pass_rate
FAILED test_eval_task.py::TestTheRunWritesItsRecord::test_the_per_dataset_numbers_are_the_summarisers_own
FAILED test_eval_task.py::TestTheRunWritesItsRecord::test_the_record_reports_both_datasets
FAILED test_eval_service.py::TestBuildEvalResult::test_the_two_datasets_are_never_pooled
FAILED test_eval_service.py::TestBuildEvalResult::test_a_metric_over_nothing_stays_unmeasured_rather_than_zero
FAILED test_eval_service.py::TestBuildEvalResult::test_the_run_level_counts_are_the_summarisers_totals
FAILED test_eval_service.py::TestWriteAndReadEvalResult::test_a_stored_payload_that_breaks_a_rule_reads_as_unmeasured
11 failed, 132 passed in 47.30s

restored: 143 passed in 44.59s
```

Suites:

```
test_eval_result_type test_eval_task test_eval_service test_eval_agent_invocation
test_eval_routes test_eval_run_rates test_gates          322 passed in 51.12s
tests/unit                                  3425 passed, 13 skipped in 622.81s
```

Gates, `scripts/gates.py static`, passed in 7.4s:

```
ruff: clean against the 0 pinned baseline violation(s)
Contracts: 3 kept, 0 broken
complexity: clean against the 115 pinned function(s)
source assertions: clean against the 44 pinned file(s), 113 site(s)
```

Lizard, the one pin that moved:

```
app/worker/tasks/runtime/eval.py:732: run_eval_suite has 303 NLOC, 28 CCN, 596 length
```

`_run_report`, `build_eval_result` and the four other new functions stay under the
`-C 15 -L 60 -a 11` thresholds, so none of them takes a baseline entry.

### Open for the later slices

- `build_eval_run_config`'s `judge_model_id` / `judge_reasoning_effort` pair is still a
  partial identity with no prompt version (#51 comment, 2026-08-25). Slice 2.
- `read_eval_result` has no caller yet. Slices 3 and 4.
- `CONTEXT_PROXY_VERSION` covers this task's proxy only. `retrieval_eval` stamps its own
  in slice 6 (#84).

## Slice 2, the judge rows

Three commits: tenant migration 0023, the domain record, the writer plus its
readers and the task call site. Criterion 2 of the ticket.

### Files

- `apps/api/alembic_tenant/versions/0023_eval_result_judge_row.py` (new).
  `eval_results` gains `binary_verdict BOOLEAN`, `threshold NUMERIC`,
  `judge_identity JSONB`, `ledger_purpose TEXT`, all nullable, all commented,
  0022's shape.
- `apps/api/app/domain/judge_record.py` (new). `JudgeRecord`,
  `InvalidJudgeRecord`, `verdict_for`, `JudgeRecord.scored`, `payload`,
  `from_payload`.
- `apps/api/app/services/eval_service.py`. `threshold_for` and
  `build_judge_records` replace `result_detail`; `write_eval_results` takes
  records and fills the columns; `_judge_row_params` and `_INSERT_EVAL_RESULT`
  hold the row assembly; `_placed_score_rows` extracted from `run_ragas_eval`,
  which now also returns `judge_records`.
- `apps/api/app/worker/tasks/runtime/eval.py`. `_NOTHING_SCORED` replaces the
  inline below-floor dict; the write takes `results["judge_records"]`.
- `apps/api/app/api/v1/evals.py`. `_gate_thresholds` calls `threshold_for`,
  hoisted out of the per-scenario loop; the `settings` import goes with it.
- `apps/api/scripts/gates.py`. Two pins lowered, none raised.
- Tests: `tests/unit/test_judge_record_type.py` (new, 52),
  `test_migration_tenant_0023.py` (new, 28), the head assertion moved out of
  `test_migration_tenant_0022.py`, `TestJudgeIdentityLandsOnTheScore` rewritten
  as `TestTheJudgeRowCarriesItsOwnDecision` plus `TestTheThresholdIsDefinedOnce`
  and `TestBuildJudgeRecords`, and the `run_ragas_eval` doubles in
  `test_eval_task.py` and `test_eval_agent_invocation.py`.

### The ledger reference, and the evidence for it

**It is a purpose, not a call id, and the grain is the metric within the run.**
The row's judge calls are the `model_calls` whose `job_id` is the row's
`eval_run_id` and whose `purpose` is `ledger_purpose`. Not per scenario.

The investigation the plan asked for, three findings, each against the code:

1. **No caller anywhere holds a `model_calls.id`.** `record_model_call`
   (`model_client.py:1025`) builds its params as
   `(str(uuid.uuid4()), *(...))` and returns None, and `Recorder` is a callable
   returning None. The id exists only inside the INSERT. Capturing one would
   mean changing the signature of the function every ledger write in the system
   goes through.
2. **The hook that writes the row cannot see a scenario.** The row comes from
   `on_response` inside `_hook_async_client`, an httpx response hook. What it
   has is a `CallContext`: purpose, tenant_id, agent_id, job_id. Ragas drives
   the judge from `metric.ascore(...)` and the HTTP call happens several frames
   below anything that knows which sample is being scored.
3. **One `ascore` is several calls.** Faithfulness runs statement extraction and
   then an NLI pass. So a per-scenario reference would owe the row a list of
   ids, not an id, even if 1 and 2 were solved.

What the ledger DOES separate is the purpose. `JUDGE_PURPOSES` gives each metric
its own routing key and `_run_ledger` binds `job_id = run_id`, so
`(eval_run_id, ledger_purpose)` is a real, queryable reference that is finer than
the run and honest about stopping there. The column comment and the
`JudgeRecord` docstring both say the grain is the metric and not the scenario,
so a reader who attributes one scenario's judge spend to forty has to ignore two
statements to do it.

It is stored rather than derived from `metric` through
`JUDGE_PURPOSE_BY_METRIC`, because that map is read live and re-routing a metric
would restate history. Same reason `judge_identity` is stored rather than looked
up through `route_for`.

### Decisions

**`detail` goes in NULL, and the column stays.** Nothing this writer knows is
left to put in a jsonb once the identity has a column. The column is not dropped
because the blob stops being WRITTEN, which redeploying the previous build
reverses, while a dropped column takes every historical row's blob with it and
leaves the rollback nothing to restore. `test_the_detail_column_is_not_dropped_by_this_migration`
pins that.

**Two metrics are gated and two are not.** `EVAL_FAITHFULNESS_THRESHOLD` and
`EVAL_RELEVANCY_THRESHOLD` are the only two in `config.py`, and
`GATED_METRIC_KEYS` in the route names the same pair.  `context_precision` and
`context_recall` get NULL threshold and NULL verdict. Not False, and not a
borrowed 0.90: a reader aggregating verdicts would otherwise count two extra
failures on every scenario in the table.

**An unscored metric is a row with no score.** `build_judge_records` emits four
records per scored scenario whatever the judge returned. Skipping the ones with
no score would make an unscored dimension indistinguishable from a scenario
nobody sent, and `summarise_run_validity`'s per-metric observation counts would
lose the denominator rather than show the hole.

**`__post_init__` refuses a wrong verdict, and the check is only reachable
through `from_payload`.** Writers go through `JudgeRecord.scored`, which derives
the verdict from `verdict_for`, so they cannot supply a wrong one and the check
is vacuous against them. It is not vacuous against a stored row: one written
when the gate was 0.80 and read after the gate moved to 0.90 disagrees with its
own numbers, and `test_a_stored_row_whose_verdict_moved_with_the_threshold_is_refused`
is that case. The comparison itself is pinned by value tests, not by the
self-check.

**`run_ragas_eval` returns the records rather than the task building them.**
`scores` and `judge_records` are two grains of one set of attributed rows.
Building the second at the call site would put a second derivation between the
number the task reports and the row it writes, which is the defect #51 removes
one grain up.

**A NaN score is refused by the record.** `_score_samples` already converts NaN
to None, twice. Without the refusal that conversion could be dropped upstream
and a NaN, losing every comparison, would land as a quiet False.

**`evals.py` calls `threshold_for` instead of naming the two settings.** Adding
`threshold_for` while leaving the route's own copy would have created the second
definition this ticket exists to remove. It is one dict comprehension, hoisted
to `_gate_thresholds` and out of the per-scenario loop where it was being
rebuilt once per row. The route still recomputes the verdict; slice 3 deletes
that.

### Readers of `eval_results.detail`

**There are none, and that is measured.** Every reader of the table selects
`score` and `metric` and nothing else:

| Reader | What it selects |
|---|---|
| `api/v1/evals.py` `_LIST_RUNS_SQL` | `COUNT(DISTINCT res.scenario_id)`, `AVG(... res.score)` |
| `api/v1/evals.py` per-dataset SQL | the same, grouped by dataset |
| `api/v1/evals.py` `_GET_RUN_RESULTS_SQL` | `res.scenario_id, res.metric, res.score` |
| `alert_service.py:40` | `AVG(er.score)` where `metric = 'faithfulness'` |
| `digest_service.py:53` | the same query, one service over |
| `deployment_service.py:753,771` | `AVG(score)`, `COUNT(score)`, `COUNT(DISTINCT scenario_id)` |

So one reader was touched, `api/v1/evals.py`, and only for the threshold
definition above. No route was restructured. Nothing in `app/` or `tests/` reads
the blob, which is why `test_the_score_row_survives_alongside_the_identity` was
the only test that ever asserted on it and why deleting it costs nothing.

### The exception-text grep (#96's class)

```
$ grep -rnE 'str\(exc\)|str\(e\)|f"\{exc\}|repr\(exc\)' \
    app/services/eval_service.py app/worker/tasks/runtime/eval.py \
    app/api/v1/evals.py app/domain/judge_record.py
eval_service.py:1337,1819,1842,1858,1874,2067,2165,2345,2394   error=str(exc),
eval.py:547,907,995,1147,1318,1418,1437                        error=str(exc),

$ ... | grep -v 'error=str(exc)'
(no output)
```

Sixteen sites, every one a `log.warning` / `log.error` kwarg. Logs are not rows.

One further site is not a `str(exc)` and was checked by hand,
`eval_service.py:758`:

```
754    errors: dict[str, int] = {}
755    for record in records:
756        error = record.get("error")
757        if error:
758            errors[str(error)] = errors.get(str(error), 0) + 1
```

`record["error"]` is set in exactly one place, `eval.py:541`, and it is already
`type(exc).__name__`. So the histogram that reaches `eval_runs.config` carries
class names, and `str()` of a class name is that class name.

**Nothing changed, because nothing needed to.** No exception message reaches
`eval_results`, `eval_runs.config` or any other jsonb the owner reads back on
this path.

### Observed

Migration round trip, local `wchats_tenant_probe` through `run_tenant_migrations`:

```
before                 revision=0022  binary_verdict=None threshold=None
                                      judge_identity=None ledger_purpose=None
after upgrade head     revision=0023  binary_verdict=('boolean', 'YES', None)
                                      threshold=('numeric', 'YES', None)
                                      judge_identity=('jsonb', 'YES', None)
                                      ledger_purpose=('text', 'YES', None)
after downgrade 0022   revision=0022  all four absent again
after re-upgrade       revision=0023  all four back, same types, still nullable
```

Mutation 1, `verdict_for` flipping `>=` to `>`, restored with
`git checkout HEAD --`:

```
MUTATION APPLIED: verdict_for returns score > threshold
FAILED test_eval_service.py::TestTheJudgeRowCarriesItsOwnDecision::test_the_verdict_is_the_comparison_and_not_a_copy_of_the_score
FAILED test_judge_record_type.py::TestTheVerdictFollowsTheThreshold::test_a_score_exactly_on_the_threshold_passes
FAILED test_judge_record_type.py::TestTheVerdictFollowsTheThreshold::test_verdict_for_is_the_whole_rule[0.9-0.9-True]
3 failed, 170 passed in 40.99s

restored: 173 passed in 38.42s
```

Mutation 2, `threshold_for` dropping the `answer_relevancy` gate, restored the
same way:

```
MUTATION APPLIED: threshold_for no longer returns EVAL_RELEVANCY_THRESHOLD
FAILED test_eval_service.py::TestTheJudgeRowCarriesItsOwnDecision::test_a_gated_metric_carries_its_threshold_and_its_verdict
FAILED test_eval_service.py::TestTheJudgeRowCarriesItsOwnDecision::test_an_unscored_metric_is_a_row_with_no_score_and_no_verdict
FAILED test_eval_service.py::TestTheThresholdIsDefinedOnce::test_only_the_two_gated_metrics_have_a_threshold
FAILED test_eval_service.py::TestTheThresholdIsDefinedOnce::test_the_route_reads_the_same_gate_the_writer_stored
FAILED test_eval_routes.py::TestGetEvalRunResults::test_returns_200_with_results_shape
FAILED test_eval_routes.py::TestGetEvalRunResults::test_passed_flag_true_when_all_scores_above_threshold
FAILED test_eval_routes.py::TestGetEvalRunResults::test_passed_flag_false_when_gated_score_below_threshold
FAILED test_eval_routes.py::TestGetEvalRunResults::test_passed_flag_ignores_ungated_metrics_below_threshold
8 failed, 145 passed in 45.97s

restored: 153 passed in 47.41s
```

The second mutation reaching the ROUTE tests is the evidence that the route and
the writer now read one definition. Before this slice it would have failed only
the service tests.

Suites, foreground:

```
test_judge_record_type test_migration_tenant_0023 test_eval_service
test_eval_task test_eval_routes test_deployment_service   373 passed in 47.37s

test_migration_tenant_0022 test_eval_agent_invocation test_eval_run_rates
test_gates test_eval_result_type                          166 passed in 30.43s
```

Gates, `scripts/gates.py static`, passed in 7.1s:

```
ruff: clean against the 0 pinned baseline violation(s)
Contracts: 3 kept, 0 broken
complexity: clean against the 115 pinned function(s)
source assertions: clean against the 44 pinned file(s), 113 site(s)
```

Lizard, the two pins that moved, both down:

```
app/services/eval_service.py  run_ragas_eval        17 CCN, 164 length  (was 24, 169)
app/api/v1/evals.py           get_eval_run_results  20 CCN, 104 length  (was 20, 107)
app/worker/tasks/runtime/eval.py  run_eval_suite    28 CCN, 596 length  (unchanged)
```

`write_eval_results` crossed the 60-length standard on the wider INSERT, so
`_judge_row_params` took the row assembly back out and it takes no new pin.
`build_judge_records`, `threshold_for`, `_placed_score_rows`, `_gate_thresholds`
and everything in `judge_record.py` are all under `-C 15 -L 60 -a 11`.

### Open, carried forward

- **`build_eval_run_config`'s `judge_model_id` / `judge_reasoning_effort` pair
  is still a two-field partial identity** (#51 comment, 2026-08-25). Slice 1's
  trace put it here and this slice did not do it. The per-row identity that the
  comment offered as the replacement now exists, so the choice is live, but it
  is not a judge row and completing or deleting it is its own decision with its
  own reasoning: the config copy is written at INSERT, before the first turn, so
  it is the only judge identity a run that never finished has. It has a test
  defending it (`test_judge_model_is_recorded_separately_from_the_agent_model`)
  and it feeds `AGENT_DEPENDENT_DIMENSIONS`. Slice 3 touches `eval_runs.config`
  readers and is where it belongs.
- `api/v1/evals.py` still recomputes the verdict from `_gate_thresholds` rather
  than reading `binary_verdict` off the row. Slice 3, criterion 1, closes #26.
- `deployment_service`, `alert_service` and `digest_service` still run their own
  `AVG`/`COUNT` over `eval_results`. Slice 4.
