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
