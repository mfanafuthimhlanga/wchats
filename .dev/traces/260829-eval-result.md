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

## Slice 3, the routes read the record

Two commits: the list route with its tests (`bc2c917`, closes #26), the results route
with its tests and the admin type change (`8720ff5`).

### Files

- `apps/api/app/api/v1/evals.py`. `_LIST_EVAL_RUNS_SQL` selects `er.result` and
  aggregates nothing; `_LIST_EVAL_RUNS_PRE_0022_SQL` is the fallback;
  `_LIST_EVAL_RUN_DATASETS_SQL` and `_gate_thresholds` are gone. New:
  `_unmeasured_metrics`, `_metrics_of`, `_dataset_block` (rewritten to take an
  `EvalResult`), `_run_level_metrics`, `_record_of`, `_eval_run_block`,
  `_fetch_eval_runs`, `_judge_reading`, `_fetch_run_results`,
  `_GET_RUN_RESULTS_PRE_0023_SQL`, `RESULT_PRESENT` / `RESULT_ABSENT`.
- `apps/api/app/services/eval_service.py`. Docstring only: the
  `summarise_run_validity` note about the route's duplicate SQL, and the
  requested/served rule beside `AGENT_DEPENDENT_DIMENSIONS`.
- `apps/api/scripts/gates.py`. Both route pins lowered.
- `apps/admin/app/agents/[id]/page.tsx`, `apps/admin/app/agents/[id]/eval/page.tsx`.
  `scenario_count: number | null`, plus a `scenarioCount()` helper on the agent page.
- Tests: `tests/unit/test_eval_routes.py`, 32 to 42.

### The number the record does not hold

**The four run-level averages have no source, and the route does not compute one.**
`EvalResult` stores a `Measurement` per dataset per metric and deliberately no pooled
mean. `.dev/reference/260818-llm-eval-fundamentals.md` section 11 forbids a pooled rate
outright, and `summarise_run_validity`'s docstring says the same thing in the code: a
golden mean and an exploratory mean answer different questions, and one number over both
moves whenever the exploratory draw moves while looking like a quality change.

So the route reports a run-level `metrics` block only when there is nothing to pool:
exactly one dataset scored a row. Then that dataset's measurements ARE the run's, copied
through verbatim, and `metrics_dataset` names it. When both halves scored, the four
metrics read unmeasured, `metrics_dataset` is null, and the numbers stay under `datasets`.
Every number in the response is lifted, never averaged.

This is a selection, not an arithmetic, and it is stated in the response so no reader can
mistake a dataset's number for a run's. It degrades to unknown, never to a fabricated
zero. What it costs: **a tenant with a designated golden set now gets no run-level number
at all**, and the admin's eval-page chart plots `aggregate_scores`, which reads 0.0 for an
unmeasured metric. On today's ordinary tenant (no golden rows designated, exploratory
scores everything) the chart is unaffected. On a golden-set tenant the chart would flatten
to zero, and the fix is a console that plots the two halves as two series. That is a UI
change, out of this slice's scope, and it is open work.

`datasets.unattributed` has the same shape of gap and is reported as nulls.
`summarise_run_validity` counts result rows whose scenario no longer exists;
`EvalResult` stores no such field. The route used to recount them in its own SQL. Nulls
say the response cannot answer; a zero would assert the run had none.

### Decisions

**A recordless run reports `result: "absent"` with null counts.** Three states reach it,
all reading the same way to the console: a tenant DB predating tenant migration 0022, a
run that died before `write_eval_result`, and a stored payload that fails
`EvalResult.from_payload` on the way out. The log says which. Zero was the alternative
and it is indistinguishable from a run that attempted nothing.

**The list route falls back rather than failing on a pre-0022 tenant.** The wide SELECT
raises `UndefinedColumn` before returning a row, so `_fetch_eval_runs` catches exactly
that and runs the narrow query, appending None per row. Only `UndefinedColumn` degrades;
`test_a_genuine_query_failure_is_not_swallowed_as_a_missing_column` pins a connection
failure surfacing. The same pattern covers pre-0023 rows on the results route.

**The per-scenario verdict keeps None-first ordering.** Any NULL `binary_verdict` on a
gated metric gives `passed: null`, even when the other gated verdict is False. Kleene
conjunction would give False there and is arguably sharper, but None is the shipped
contract, the admin renders it as a fail either way (`s.passed ? 'pass' : 'fail'`), and
changing verdicts was not this slice's job.

**Gatedness still comes from `GATED_METRIC_KEYS`, not from the row.** A row's
`threshold IS NULL` cannot be read as "ungated": every pre-0023 row has NULL there, so
deriving the gated set per row would make `all([])` true and pass every legacy scenario.
`test_eval_service.py::test_the_route_reads_the_same_gate_the_writer_stored` pins every
entry in the tuple to a non-None `threshold_for`.

**`build_eval_run_config`'s judge pair stays, and now says what it is.** Config holds the
judge the run REQUESTED at insert; the record holds the identity that SERVED
(`EvalResult.judge_identity`, `served_model`, and the per-call identity on every judge
row). No reader in the routes presents the config pair as served. The full statement went
beside `AGENT_DEPENDENT_DIMENSIONS` at module scope rather than into the function
docstring, because the docstring lives inside a lizard length pin and documentation is not
a reason to raise one.

### Admin readers found

Two files read these routes, and neither reads `metrics`, `datasets`,
`scored_scenario_count` or the ledger's per-dataset split:

- `apps/admin/app/agents/[id]/eval/page.tsx` typed `scenario_count: number` (unused on the
  page) and plots `aggregate_scores` through `.toFixed(2)`. It reads `passed: boolean` per
  scenario. Only the type changed.
- `apps/admin/app/agents/[id]/page.tsx` typed `scenario_count: number` and renders it twice,
  through `pluralize()` in the section head and as a channel readout. Both now go through
  `scenarioCount()`, which says "no record" for null, and the readout falls back to "n/a".

`aggregate_scores` stays `number` on both sides. It is the numeric compatibility
projection the module docstring has always called a lie, and making it nullable is the
frontend change this slice is not.

### Observed

Suites, one foreground run:

```
test_eval_routes test_eval_run_rates test_eval_service test_eval_task
test_eval_result_type test_eval_agent_invocation test_promote_trace
test_judge_record_type                                  388 passed in 50.39s
```

`test_eval_run_rates.py` is not a route test. It drives `tests/evals/run_evals.py`'s
deterministic collector and was named in the slice brief as one; it is run here because it
was named, and it passes.

Gates, `scripts/gates.py static`, passed in 8.7s:

```
ruff: clean against the 0 pinned baseline violation(s)
Contracts: 3 kept, 0 broken
complexity: clean against the 115 pinned function(s)
source assertions: clean against the 44 pinned file(s), 113 site(s)
```

Lizard, both pins lowered:

```
      49     13    288      2      87 list_eval_runs@403-489@app/api/v1/evals.py
      51     17    373      3      98 get_eval_run_results@575-672@app/api/v1/evals.py
```

`list_eval_runs` 25 CCN / 160 length becomes 13 / 87; `get_eval_run_results` 20 / 104
becomes 17 / 98. Neither pin was raised. `build_eval_run_config` and
`summarise_run_validity` each grew on docstring lines alone and were trimmed back under
their existing pins rather than repinned.

`npx tsc --noEmit` in `apps/admin`, exit 0, no errors. No install was needed:
`node_modules` and `node_modules/typescript` were present and tsc resolved every import,
so `pnpm install --frozen-lockfile` was never run.

Mutation, the list route joining `eval_results` and counting `scenario_id` again:

```
MUTATION APPLIED: list_eval_runs joins eval_results and counts scenario_id again
FAILED test_eval_routes.py::TestListEvalRuns::test_the_numbers_survive_every_result_row_being_deleted
FAILED test_eval_routes.py::TestListEvalRuns::test_the_list_query_aggregates_nothing
2 failed, 35 deselected in 32.47s

test_the_numbers_survive_every_result_row_being_deleted:
    E       IndexError: list index out of range
    tests\unit\test_eval_routes.py:529: IndexError
```

The IndexError is the point. With the join back, the runs query names `eval_results`, the
fake returns the empty table for it, and `response.json()["eval_runs"][0]` has nothing to
index: every run vanished from the console because the result rows were deleted.

Restored with `git checkout HEAD -- app/api/v1/evals.py`:

```
37 passed in 32.84s
```

### Open, carried forward

- **A golden-set tenant now has no run-level metric number.** The console has to plot
  `datasets.golden` and `datasets.exploratory` as two series, or read `metrics` only when
  `metrics_dataset` is set. Until it does, `aggregate_scores` reads 0.0 for such a tenant
  and that is the fabricated-collapse reading the module docstring warns about. Frontend
  work, opened as #119.
- **`EvalResult` carries no `unattributed` count**, so the list route reports nulls for it.
  Either the record grows the field or the response drops the key. A decision for
  whoever touches the record next.
- `deployment_service`, `alert_service` and `digest_service` still run their own
  `AVG`/`COUNT` over `eval_results`. Slice 4.

## Slice 4, the deployment service and the two digests read the record

Two commits: the deploy collector and its gate with tests (`d1f87c2`), the alert
and digest readers with tests (`88030bb`).

### Files

- `apps/api/app/domain/eval_result.py`. `UNMEASURED`, `unmeasured_metrics`,
  `metrics_of`, `scoring_datasets`, `run_level_metrics`. Slice 3's no-pooling
  rule, moved down below both readers.
- `apps/api/app/api/v1/evals.py`. `_unmeasured_metrics`, `_metrics_of` and
  `_run_level_metrics` become renderers over the domain functions; `_rendered`
  is new; `_UNMEASURED_METRIC` is gone; `GATED_METRIC_KEYS` is imported.
- `apps/api/app/services/eval_service.py`. `GATED_METRIC_KEYS` beside
  `threshold_for`; `latest_run_record` and `latest_faithfulness` beside
  `read_eval_result`.
- `apps/api/app/services/deployment_service.py`. `EVAL_SIGNAL_NO_RECORD`,
  `DENOMINATOR_SOURCE_EVAL_RECORD`, `RESULT_PRESENT` / `RESULT_ABSENT`,
  `UNMEASURED_READING`, `_readings`, `_dataset_block`, `_pass_rates`,
  `_record_counts`, `_latest_run`, `_record_of`, `_scenario_verdicts`,
  `_never_evaluated_warning`, `_signal_unavailable_warning`,
  `_unmeasured_gated_metrics`, `_quality_evidence_warning`,
  `_eval_evidence_warnings`. Gone: `DENOMINATOR_SOURCE_RUN_CONFIG`,
  `DENOMINATOR_SOURCE_EVAL_RESULTS`, `_attempted_from_run_config`, both
  `eval_results` aggregations. `EVAL_SUMMARY_UNAVAILABLE_SIGNAL` is now built
  through `_eval_summary` rather than hand-written.
- `apps/api/app/services/alert_service.py`. `latest_faithfulness_reading` owns
  the connection; `_get_latest_faithfulness` is the number alone.
- `apps/api/app/services/digest_service.py`. `_collect_digest_stats` calls
  `latest_faithfulness` down its existing connection and carries
  `faithfulness_dataset`; `send_digest_email` names the dataset and says "not
  measured".
- `apps/api/app/worker/tasks/runtime/eval.py`. Three stale comments describing
  the deleted `AVG(score)` path.
- `apps/api/scripts/gates.py`. Four pins lowered, none raised, none added.
- Tests: `test_deployment_service.py` (105 to 118), `test_deployment_task.py`,
  `test_alert_service.py` (3 to 7), `test_digest_service.py` (4 to 6),
  `test_label_downstream.py`.

### How the gate's inputs changed

`_fetch_eval_summary_sync` issued three statements: the run row, `SELECT metric,
AVG(score), COUNT(score) ... GROUP BY metric`, and a `COUNT(DISTINCT
scenario_id)` pair. The two aggregations are gone. It issues the run row (now
selecting `result` beside `config`) and one read of the stored gated verdicts.

The payload keeps every key it had and gains `result`, `pass_rates_dataset`,
`metrics`, `datasets`, `invocation`, `cost`, `context_proxy_version` and
`unmeasured_scenarios`. `scenario_count` and `scored_scenario_count` became
nullable, because a payload with no record knows neither and a zero asserts that
the run covered nothing.

**`denominator_source` has one value now.** It used to choose between
`run_config` and `eval_results` and label which. Both parsers are deleted: the
record carries all three counts, `eval_result` is the only source, and there is
no floor to fall back to. A run without a record reports nulls.

**A seventh signal state, `no_record`.** A run that completed, invoked the agent
and wrote no `eval_runs.result`. It is asked AFTER the invocation claim, so the
pre-D1 population, which predates migration 0022 as well, still gets the
tautology warning that names what is actually wrong with it.

**`failing_scenarios` stopped being `rate < 0.70`.** It was
`sum(1 for v in rates.values() if v < 0.70)` over the metric averages, which
counted failing METRICS under a scenario's name. It counts scenarios off the
stored `binary_verdict` rows now, unmeasured-first (the ordering
`get_eval_run_results` renders `passed` with), with `unmeasured_scenarios`
counting the undecided ones separately. Both are null on a tenant DB that
predates migration 0023.

### What the gate does with the three cases

| Input | Signal | Gate |
|---|---|---|
| absent record | `no_record` | `block`, `eval_signal_unavailable` |
| NULL verdict on a gated metric | `measured` | counted as unmeasured, not as a failure; the run still ships if the metrics measured |
| verdicts unreadable at all | `measured` | `block`, `eval_quality_unmeasured` |
| both datasets scored | `measured`, `pass_rates` null | `ship` survives; evidence read per dataset |
| gated metric measured on no dataset | `measured` | `block`, `eval_quality_unmeasured` |

**The gate applies no quality threshold, and this slice did not give it one.**
The brief anticipated that it might be passing on a pooled mean; it was not. The
0.85 ship bar and the [0.70, 0.85) warning band are prose in
`_DEPLOYMENT_SYSTEM_PROMPT` and always were, and
`test_the_ship_bar_is_prose_in_the_prompt_not_code_in_the_gate` pins that both
ways now, by source and by behaviour. What the gate gained is one refusal that is
still about evidence rather than quality: a `measured` signal whose gated metrics
were measured on NO dataset, which is reachable now that the collector no longer
manufactures an average. The judge can return `context_precision` and nothing
else; `scored` is then above zero and the gate's evidence is not.

It reads that per DATASET and not from the run level, deliberately. A run whose
two halves both scored has no run-level reading at all, so a gate reading
`metrics` or `pass_rates` alone would refuse every tenant with a designated
golden set while its numbers sat one key over. Null in `pass_rates` means both
"we measured nothing" and "we measured both halves", and shipping over the second
because it looks like the first is the fail-open the per-dataset read closes.

### What the Orchestrator prompt now receives

The whole `eval_summary` dict is `json.dumps`ed onto the orchestrator payload, so
the new keys reach it without a wiring change. The prompt gained:

- the seventh state, `no_record`, in the blocking-condition enumeration
- `TWO DATASETS, NEVER AVERAGED TOGETHER`, naming `datasets.golden` and
  `datasets.exploratory`, each with three counts and four
  `{value, measured, observations}` readings
- the instruction to apply the existing ship/warn/block thresholds to EACH
  measured dataset, and that a run ships only if every dataset that measured a
  metric clears the bar for it
- `pass_rates_dataset`, to be quoted whenever the run-level numbers are quoted
- `failing_scenarios` and `unmeasured_scenarios` as two counts, with null meaning
  unreadable rather than zero

The thresholds themselves are untouched (ticket 17, #54 and #36). What changed is
which numbers they are applied to.

### Decisions

**The no-pooling rule moved into `app.domain`.** Slice 3 put it in
`api/v1/evals.py` as three private helpers. The deploy gate asks the same
question, and `app.services` may not import `app.api`, so the alternative was a
second copy of a selection rule. Two copies is how a deploy gate and the screen
the owner is looking at come to name different datasets, which is the defect one
grain up that this whole ticket removes. The route keeps its renderers and its
response shape is byte-identical; its 42 tests were untouched and stayed green.

**`agent_invoked` still comes from `eval_runs.config`, and the record's
invocation block travels beside it.** They are one observation:
`invocation_provenance` derives `agent_invoked` from
`agent_invocation["status"] == MEASURED`, which is the same field the record's
`InvocationStatus` holds. The config copy is written at the invocation phase,
before scoring, so it is the only invocation claim a run that died before
`write_eval_result` has at all, and the gate's whole D1 refusal rests on it. Same
reasoning slice 3 applied to `build_eval_run_config`'s judge pair. The comment on
`_record_counts` says which is which so a reader does not treat them as two
independent claims.

**`GATED_METRIC_KEYS` moved to `eval_service`, beside `threshold_for`.** It lived
in the route. The collector needs the same pair to count verdicts, and
`app.services` cannot import from `app.api`. `test_the_route_reads_the_same_gate_the_writer_stored`
still pins every entry to a non-None `threshold_for`.

**The scenario verdict count is unmeasured-first.** A scenario with one NULL
gated verdict and one False is counted once, as unmeasured. That matches
`get_eval_run_results`'s `None if None in verdicts else all(verdicts)`, so the
console and the deploy report describe the same scenario the same way. A scenario
with fewer gated rows than there are gated metrics is unmeasured too: a missing
row and a NULL verdict are the same absence.

**Three run-row fallbacks, not two.** A tenant at 0013 through 0021 has `config`
and no `result`. Degrading straight to the pre-0013 SELECT would have dropped its
invocation claim and failed the whole population closed for a reason nothing on
that tenant can fix. `_latest_run` walks the three widths and logs which one
answered.

**`EVAL_SUMMARY_UNAVAILABLE_SIGNAL` goes through `_eval_summary`.** It was
hand-written and carried `scenario_count: 0` and `scored_scenario_count: 0` next
to a comment explaining why `valid_scenario_count` was None. The collector raised;
it counted nothing. Building it through the constructor makes the substitute and
a real absent signal drift-proof and settles the inconsistency.

**The alert and the digest have no number for a two-dataset tenant.** Same
consequence slice 3 recorded for the console, one reader over: the regression
alert cannot fire for a tenant with a designated golden set, because there is no
run-level faithfulness and averaging the two halves would produce a figure that
moves with the exploratory draw. An alert that fires on the draw is worse than
one that does not fire. Open work beside #119.

### The pre-existing breakage this slice found

**`test_label_downstream.py` was red on this branch before slice 4 touched it.**
Ten tests, `error="'judge_records'"`. Slice 2 made `run_ragas_eval` return
`judge_records` and `write_eval_results` take them, and that file's double
returned only `scores`, so `run_eval_suite` raised KeyError and every count in
the file read as a missing key rather than as a wrong number. Observed on `HEAD`
with the working tree stashed:

```
10 failed, 16 passed in 30.56s        (1d828fc, slice 4 not applied)
11 failed, 15 passed                  (slice 4 applied)
```

The eleventh is slice 4's own:
`test_the_pass_rate_query_cannot_exclude_a_labelled_row` asserted the deleted
`AVG(score), COUNT(score) FROM eval_results` string was present in the collector.
Rewritten as `test_the_deploy_gate_cannot_exclude_a_labelled_row`: the hop moved
to the record and the fact did not, because the per-dataset `Measurement` is
computed over every scored row of a dataset and none of `EvalResult`,
`DatasetOutcome` or `Measurement` carries a provenance field. `BACKLOG 4.12` is
still what would change it.

Fixed here rather than filed, because the branch cannot merge red and this is the
commit that touches the same seam. Two of the rewrites replaced
`inspect.getsource` calls with reads of the dataclass fields and one behavioural
assertion, because `SOURCE_ASSERTION_BASELINE` may not grow and a behaviour
assertion is what the baseline's comment asks for anyway.

### Observed

Mutation 1, the gate accepting an absent record. `_eval_evidence_warnings` reads
`if eval_signal not in (SHIPPABLE_SIGNAL, EVAL_SIGNAL_NO_RECORD)`:

```
MUTATION APPLIED: apply_signal_evidence_gate treats an absent record as passing
FAILED test_deployment_service.py::TestTheRecordIsTheOnlyDenominator::test_a_recordless_run_makes_the_gate_refuse
1 failed, 153 passed in 34.65s
```

**The recommendation still blocked.** Only the warning assertion failed. The
signal arm and `_quality_evidence_warning` are two floors under the same hole: a
run with no record also has no gated metric measured on any dataset, so the second
refusal caught what the first stopped catching. Recorded as one mutation with two
stages rather than dressed up as two independent defences, the same discipline
`.dev/reference/p3-review-mutation-proofs.md` applied to the P3 pair.

Mutation 2, both floors removed (`missing = []`, the `failing_scenarios is None`
arm dead), which is what it takes to observe a shipped deploy:

```
MUTATION 2 APPLIED: both floors under an absent record removed
FAILED ...TestTheRecordIsTheOnlyDenominator::test_a_recordless_run_makes_the_gate_refuse
FAILED ...TestTheTwoDatasetsAreNeverPooled::test_a_gated_metric_measured_on_no_dataset_refuses
FAILED ...TestScenarioVerdictCounts::test_a_pre_0023_tenant_reports_the_absence_rather_than_zero
3 failed, 151 passed in 31.53s

test_a_recordless_run_makes_the_gate_refuse:
    E  AssertionError: a run that recorded no measurement is unknown quality,
       and unknown quality may never approve a deploy
    E  assert 'ship' == 'block'
```

Restored with `git checkout HEAD -- app/services/deployment_service.py`:

```
154 passed in 30.84s
```

Suites, foreground:

```
test_deployment_service test_deployment_task test_eval_service test_eval_routes
test_alert_service test_digest_service test_label_downstream
                                                356 passed in 100.24s
tests/unit                                      3546 passed, 13 skipped
```

Gates, `scripts/gates.py static`, passed in 9.0s:

```
ruff: clean against the 0 pinned baseline violation(s)
Contracts: 3 kept, 0 broken
complexity: clean against the 115 pinned function(s)
source assertions: clean against the 44 pinned file(s), 113 site(s)
```

Lizard, the four pinned functions this slice owns, all lowered:

```
      28      2    171      6      66 _eval_summary@649-714            (was 5, 69)
     119     12    448      2     207 _fetch_eval_summary_sync@906     (was 18, 312)
     111     15    370      3     251 apply_signal_evidence_gate@1921  (was 20, 292)
     208     26  1066      2     372 run_deployment_checklist@162      (unchanged)
```

Plus `_collect_digest_stats` 12/81 to 10/71. `check_and_write_alerts` is
unchanged at 17/50: `_get_latest_faithfulness` kept its signature, so the caller
did not move. None of the seventeen new functions takes a baseline entry; all are
under `-C 15 -L 60 -a 11`.

### Open, carried forward

- **A two-dataset tenant has no run-level faithfulness for the regression
  alert**, so `eval_regression` cannot fire for it. Same root gap as #119, one
  reader over. Either the alert learns to compare per dataset, or the golden set
  gets its own threshold.
- **The admin deploy page still averages `pass_rates` across METRICS**
  (`avgPassRate`, `agents/[id]/deploy/page.tsx:468`) and reads
  `failing_scenarios ?? 0`. A null now reads as zero failures on that screen.
  Frontend work, beside #119.
- **`EvalResult` still carries no `unattributed` count**, so nothing downstream
  reports it. Slice 3 left the same note.
- Slice 5 owns generation and timeouts; slice 6 owns the retrieval proxy.

## Slice 5, generation, timeouts, and the verdict precedence

Three commits: the dataset write and refusal (#27), the failure record and the
timeout message (#25), the `_VERDICT_PATTERNS` ordering test (#97's debt).
Criterion 3 of the ticket.

### Files

- `apps/api/app/services/scenario_service.py`. `InvalidScenario`,
  `_scenario_dataset`; `store_scenarios` writes `dataset` and refuses a row
  without one; `generate_scenarios_from_chunks`, `mine_production_scenarios` and
  `insert_provenance_scenario` each write `exploratory`.
- `apps/api/app/domain/eval_result.py`. `DATASET_GOLDEN` / `DATASET_EXPLORATORY`
  become names, `ScenarioFailure`, `_require_failures`, `EvalResult.failures`.
- `apps/api/app/services/eval_service.py`. `_failure_report`;
  `summarise_agent_invocation` returns `failures`; `build_eval_result` carries
  them; `dataset_of`'s docstring says what its NULL fold is for.
- `apps/api/app/worker/tasks/runtime/eval.py`. `_failure_of`; the invocation
  record gains `error_message`.
- `apps/api/scripts/gates.py`. Two pins lowered.
- Tests: `test_scenario_service.py` (+9), `test_eval_result_type.py` (+11),
  `test_eval_task.py` (+4), `test_eval_agent_invocation.py` (+4),
  `test_eval_service.py` (+2), `test_red_team_probe.py` (+11, one parametrised
  over six).

### Decisions

**The refusal runs before the connection opens.** `store_scenarios` resolves
every row's dataset into a list, then inserts. A per-row check inside the loop
would have written the good rows ahead of the bad one and committed nothing,
leaving a half-stored suite whose count reads as a completed generation. The
test that pins this puts the bad row LAST, because a check that happens to run
first passes an all-or-nothing assertion for the wrong reason.

**`mine_production_scenarios` was not in the plan and had to be.** The plan named
two producers. There are four writers in the module and three of them reach
`eval_scenarios` through `store_scenarios`, so a miner that kept omitting the
column would have had every batch refused. `run_eval_suite` wraps the
mine-and-store pair in a best-effort try/except that logs `mine_failed`, so the
failure mode was one warning line a night and no mined scenarios ever again,
with every gate green. A test now drives the miner and the writer as a pair.

**No migration backfills the NULL rows, and `dataset_of` keeps folding them.**
The twenty rows of eval run 29754ceb are still in the table. Which of them
belongs in a golden set is the owner's call to make once, not a default an ALTER
can guess, so the fold stays and its docstring now says it exists for rows
written before this change and for nothing else. Nothing written after it
reaches `dataset_of` as NULL.

**`DATASET_GOLDEN` and `DATASET_EXPLORATORY` moved into `app.domain`, and
`scenario_service` imports them.** `eval_service` keeps its own pair under the
existing test that pins the two tuples together. The producer does not: a writer
holding its own copy of the vocabulary is how #27 survived four of them.

**The failure message is composed, never copied.** `_failure_of` gives a timeout
`agent turn exceeded 90s` and gives every other class its own name and nothing
more. `str(TimeoutError())` is the empty string, which is the whole of #25; and
`eval_runs.result` is jsonb the owner reads back, which is #96's boundary. A
phrase this module chose cannot carry a customer's words, a connection string or
a stack frame across it. The log line still carries `str(exc)`, because a log is
where an operator debugging a provider needs the raw string, and a test pins
that too so the rule is not read as "delete the text everywhere".

**`failures` lives on `EvalResult`, not on `Invocation`.** The plan said so and
the reason holds: `Invocation` is nine counters under the summariser's own
names, and a list of records is a different grain. The record refuses to hold
more failures than `invocation.failed`, which is worth something only because
`_failure_report` produces the histogram and the list in one walk.

**`RULE_VERSION` stays 1.** The field says a stored payload was written under
different rules and is not a like-for-like comparison. `failures` adds detail
and changes no number a comparison reads: the metrics, the counts and the cost
are what they were. A payload written before the field reads as no failure
detail, never as a run that lost no turns, because `invocation.failed` still
says it did.

**The ordering test is written against the whole list.** The parametrised case
covers every entry that is not `would_have_executed`, so a pattern added below
it tomorrow arrives inside the test rather than beside it. FM-004 is why each
case asserts both needles are in its fixture before it asserts a tag: a fixture
matching one pattern makes an ordering assertion vacuous, which is the
arrangement that hid this. A separate test pins that no second landed tag sits
lower down, since one would make the first entry's own argument false while
leaving the comment in place.

### Observed

Mutation, `_scenario_dataset` folding a missing dataset to exploratory instead
of refusing, restored with `git checkout HEAD --`:

```
MUTATION APPLIED: _scenario_dataset folds a missing dataset to exploratory instead of refusing
FAILED test_scenario_service.py::TestTheDatasetColumnIsWritten::test_a_row_without_a_dataset_is_refused_and_nothing_is_written
FAILED test_scenario_service.py::TestTheDatasetColumnIsWritten::test_a_dataset_nobody_reports_is_refused
FAILED test_scenario_service.py::TestTheDatasetColumnIsWritten::test_one_bad_row_in_a_batch_writes_none_of_them
3 failed, 11 passed in 7.53s

restored: 14 passed in 5.76s
```

Mutation, `_failure_of` returning `str(exc)` as the message:

```
MUTATION APPLIED: _failure_of returns str(exc) as the message
FAILED test_eval_agent_invocation.py::test_a_timed_out_turn_carries_the_budget_it_exceeded
FAILED test_eval_agent_invocation.py::test_a_failure_that_is_not_a_timeout_says_its_class_and_no_more
FAILED test_eval_agent_invocation.py::test_the_exceptions_own_text_reaches_no_field_of_the_observation
3 failed, 86 passed in 37.43s

restored: 89 passed in 32.63s
```

`test_eval_task.py` stayed green under that mutation, and correctly. Its fixture
doubles the invoker and supplies the message, so those four tests pin the carry
through to `eval_runs.result` rather than the composition. The composition is
pinned one module over, against the real `_invoke_agent_for_scenarios`.

Mutation, `would_have_executed` moved to the end of `_VERDICT_PATTERNS`:

```
MUTATION APPLIED: would_have_executed moved to the end of _VERDICT_PATTERNS
FAILED test_red_team_probe.py::test_would_have_executed_outranks_every_refusal_pattern[provider_not_configured]
FAILED test_red_team_probe.py::test_would_have_executed_outranks_every_refusal_pattern[capability_denied]
FAILED test_red_team_probe.py::test_would_have_executed_outranks_every_refusal_pattern[identity_required]
FAILED test_red_team_probe.py::test_would_have_executed_outranks_every_refusal_pattern[rate_denied]
FAILED test_red_team_probe.py::test_would_have_executed_outranks_every_refusal_pattern[actor_blocked]
FAILED test_red_team_probe.py::test_would_have_executed_outranks_every_refusal_pattern[awaiting_approval]
FAILED test_red_team_probe.py::test_the_list_order_is_the_precedence_the_comment_argues
7 failed, 54 passed in 11.74s

restored: 61 passed in 9.57s
```

Suites:

```
test_scenario_service test_eval_result_type test_eval_task test_eval_service
test_eval_agent_invocation test_red_team_probe test_transactional_tools
test_label_downstream                       481 passed in 45.12s
```

Gates, `scripts/gates.py static`, passed in 7.3s:

```
ruff: clean against the 0 pinned baseline violation(s)
Contracts: 3 kept, 0 broken
complexity: clean against the 115 pinned function(s)
source assertions: clean against the 44 pinned file(s), 113 site(s)
```

Lizard, the functions this slice touched:

```
scenario_service.py:111  generate_scenarios_from_chunks   7 CCN,  66 length  (pin held)
scenario_service.py:179  store_scenarios                  6 CCN,  55 length  (unpinned)
scenario_service.py:236  generate_eval_suite_for_agent    7 CCN,  66 length  (untouched)
scenario_service.py:309  insert_provenance_scenario       1 CCN,  58 length  (unpinned)
scenario_service.py:405  mine_production_scenarios       11 CCN, 114 length  (pin held)
eval_service.py:760      summarise_agent_invocation      31 CCN, 186 length  (was 33/186)
eval.py:448              _invoke_agent_for_scenarios     14 CCN, 223 length  (was 14/224)
```

Two pins lowered, none raised. Three functions had to trade lines to hold theirs:
`generate_scenarios_from_chunks` dropped a comment that restated the line under
it, `mine_production_scenarios` lost a docstring paragraph that contradicted
itself mid-sentence about what reaches the `job_events` payload, and
`_invoke_agent_for_scenarios` collapsed a three-line `sum()` that fits on one at
120 columns. The five new functions (`_scenario_dataset`, `_failure_of`,
`_failure_report`, `_require_failures`, and `ScenarioFailure`'s methods) are all
under `-C 15 -L 60 -a 11` and take no baseline entry.

`EvalResult.__post_init__` went over the standard when the failure checks landed
inline, at ccn 16 length 67, and the gate caught it. `_require_failures` is where
they went.

### Open, carried forward

- **The four `store_scenarios` callers are covered; a fifth writer is not.**
  The refusal lives in `store_scenarios`, and `insert_provenance_scenario` writes
  its own INSERT, so a new writer added beside them inherits nothing. The
  `test_label_provenance` statement scan is the shape that would catch it, one
  column over.
- **Nothing designates a golden row.** All four producers write `exploratory` and
  no path in the tree writes `golden`, so `summarise_run_validity` still reports
  `golden 0/0/0` for every tenant. The column is now honest about why: the rows
  say exploratory because they are, rather than saying nothing. The corpus ticket
  owns the designation path.
- **`generate_eval_suite` and the promote flow are untouched.** They call the
  writers this slice changed and need no edit, and neither has a test that drives
  a real row to the table.
