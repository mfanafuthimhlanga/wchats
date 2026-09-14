# Eval ceiling revoke (#207)

The deployment checklist stops waiting at its ceiling and reports
`eval_did_not_finish`. Until this change that report was the only thing that
happened, so the eval kept calling the Judge on the tenant's money.

## Cause

`_wait_continues` ends the wait at `CHECKLIST_WAIT_CEILING_S` and the checklist
never revokes the eval task it dispatched, while `celery_app.py` sets no
`task_time_limit` or `soft_time_limit`, so nothing in the system ends a run that
outgrows the wait.

## The fix

Four parts, all fail-closed.

### a. The ceiling revokes what it gave up on

`_dispatch_eval_run` returns the Celery id of the chain's `run_eval_suite`
message instead of a bare `True`, and `_open_wait` carries it as
`eval_task_id`. `_close_the_wait` replaces the bare `_log_wait_outcome` call at
the end of the wait. It logs the outcome, then revokes the eval task and closes
the still-running `eval_runs` row out as `did_not_finish`.

**The signal is SIGUSR1.** That is the signal billiard's pool installs
`soft_timeout_sighandler` on, and that handler raises `SoftTimeLimitExceeded`
inside the running task, which is the exception `run_eval_suite` catches to
write the rows it scored. SIGTERM does not do that. billiard's
`_shutdown_handler` raises `SystemExit`, a BaseException that neither
`except SoftTimeLimitExceeded` nor `except Exception` catches, so a SIGTERM
revoke stops the spend and discards every scored row on the way out. SIGKILL
runs no handler at all.

**A revoke acks the message.** `Request.on_failure` announces a revoked task as
handled, so `acks_late=True` does not hand the eval to a second worker.

A wait opened before this change carries no id. That reads as "cannot revoke"
and is logged, because revoking an id this checklist did not dispatch would stop
somebody else's run.

### b. The eval carries its own time limits, in one ordering

Four bounds, one ordering, pinned by `test_the_four_bounds_are_ordered`:

```
checklist ceiling <= EVAL_SOFT_TIME_LIMIT_S
                   < EVAL_HARD_TIME_LIMIT_S
                   < BROKER_VISIBILITY_TIMEOUT_S
        6300              6600                6900         7200
```

The checklist gives up first, so a revoke is what normally stops an eval and the
soft limit is the backstop for an eval nobody is waiting on. Then the soft
limit, so the handler writes what it scored. Then the hard kill. All of it
inside the broker's visibility timeout, because `acks_late=True` redelivers past
that and a second worker would rerun the whole eval.

The three gaps that produce the three relations are settings, not literals:
`EVAL_SOFT_LIMIT_HANDOVER_S`, `EVAL_SOFT_LIMIT_HANDLER_S` and
`EVAL_REDELIVERY_MARGIN_S`, all 300 s, documented together in
`app/core/config.py`.

**The first relation is not free.** `checklist_wait_ceiling_s` grows with the
rows the eval will score (#213), so the widest wait the code can open is
`AGENT_INVOCATION_MAX_CALLS_PER_RUN * CHECKLIST_WAIT_PER_SCENARIO_S`, which is
7200 s: above the soft limit and at the visibility timeout. That function now
caps itself at `CHECKLIST_WAIT_CAP_S`, the soft limit less the handover.

`except SoftTimeLimitExceeded` sits before `except Exception`, because it is an
Exception subclass and order decides it. The handler writes the score rows the
Judge returned, marks the run `did_not_finish` with the reason, and returns.
Returning acks the message. Raising would buy the redelivery the limit exists to
prevent, and a rerun would learn nothing the lost attempt did not, because
whatever exhausted the limit is still true and the rows this attempt paid for
are already written.

The run id mint sits before that try and the INSERT inside `_opened_run` within
it, so a limit landing in the INSERT reaches the handler instead of leaving a
row saying `running` forever. `_opened_run` is called from its own two-line try
rather than the big one, because `task.retry` signals by raising and outside a
worker it re-raises the original exception, which the big handler would record
as a failure of a run that had not started.

**Partial rows carry their sample index.** `_score_samples` appends
`(index, row)` and `run_ragas_eval` sorts on the index before attribution. Rows
finish in whatever order the judge answers, `EVAL_SCORING_CONCURRENCY` at a
time, and `attribute_returned_rows` falls back to POSITION whenever the returned
count equals the sent count, so a sink that happened to hold every row would
have written each scenario's four scores against a neighbour.

**`partial` is filled on the success path too.** Six tenant DB round trips stand
between scoring returning and the run being marked complete, and a limit landing
in any of them would otherwise reach the handler with an empty accumulator and
throw a fully scored run away. The second fill replaces the first rather than
appending, so an interrupted run does not write every judge decision twice.

### c. A stopped run is never read as a pass

Already true, now pinned. `did_not_finish` stays out of
`TERMINAL_RUN_STATUSES`, so `poll_terminal_statuses` keeps reading the half as
absent and `_awaited_record` hands `decide()` None, which blocks.
`_fetch_eval_summary_sync` answers any status other than `complete` with
`EVAL_SIGNAL_RUN_FAILED`, so a later checklist blocks on it too. Three tests
cover the three readers, one of them against a record whose every metric is
above its threshold.

**What this does to the next checklist.** A ceiling expiry now leaves the
newest non-running eval row saying `did_not_finish`, and
`_fetch_eval_summary_sync` reads the LATEST run. So the deploy gate reports
`run_failed` and blocks until a fresh eval completes, even where the previous
night's run was clean. That is the fail-closed direction, and it converges,
because the checklist dispatches an eval on every pass.

A run that finishes after the checklist closed its row out marks itself
`complete`, overwriting the `did_not_finish`. That is the intended direction.
A run that reached the end of its own body has a record and knows what it
measured, and `test_the_completing_write_overwrites_did_not_finish` pins it.

### d. The rows a stopped run leaves behind have a denominator

`GET /agents/{id}/eval-runs/{run_id}/results` and the MCP `get_eval_results`
returned the judge rows with nothing saying how many there should have been. An
eval stopped at scenario 12 of 31 leaves twelve scenarios of real rows, and
twelve passes out of twelve reads as a clean run. The response now carries
`run: {status, attempted, valid, scored}` beside `results`. Every count is None
for a run that wrote no record, which is unknown rather than zero.

## Files

| File | Change |
|---|---|
| `app/worker/tasks/runtime/deployment.py` | `_dispatch_eval_run` returns the task id; `_open_wait` carries it; `_revoked_eval_task`, `_marked_did_not_finish` and `_close_the_wait` added; `checklist_wait_ceiling_s` capped; the call site swaps one line |
| `app/worker/tasks/runtime/eval.py` | the time limits and the cap, the decorator options, `_stopped_by_the_time_limit`, the `except SoftTimeLimitExceeded` clause, `_opened_run` and `_record_empty_run` extracted, `partial` threaded through `_score_run` and `_record_and_judge` |
| `app/services/eval_service.py` | `EVAL_RUN_DID_NOT_FINISH`, `mark_eval_run_did_not_finish` and `close_newest_running_eval_run`; `_score_samples` takes an indexed `sink`; `run_ragas_eval` takes `partial` and fills it twice; `_ragas_samples`, `_attributed`, `_sink_rows` and `_fill_partial` extracted |
| `app/core/config.py` | the three gaps the four bounds are derived from |
| `app/services/usage_service.py` | `by_job` and `jobs`, `_job_rows`, `_job_purposes`, `_JOB_LIMIT`; `_dearest_first` takes a limit |
| `app/api/v1/evals.py` | the run header beside the judge rows; `_fetch_run_header` and `_scenarios_from_rows` |
| `app/api/v1/metrics.py` | `by_job` and `jobs` in the response shape |
| `app/api/mcp.py` | one phrase each on `get_usage` and `get_eval_results` |
| `scripts/gates.py`, `tests/unit/test_gates.py` | three lizard pins lowered |

## Per-job usage grain

`by_job` is the dearest twenty jobs in the window, one row per job id, with
`purposes`, `is_turn` and the same money keys every other grain carries, and
`jobs` beside it is how many there were. It reuses `_grouped` and `_money`.
`is_turn` comes out of the `turns` count `_grouped` already computes, which for
a group that is one job is one or zero.

A ledger row with no `job_id` is absent. It stays in `total` and `by_purpose`,
where it is honest, rather than pooling under a null that would read as one
enormous job and would raise on the sort.

## Tests

| Test | File |
|---|---|
| the ceiling revokes the eval task it gave up on (the regression test) | `tests/unit/test_deployment_task.py` |
| the revoke asks for SIGUSR1, an eval that reached terminal is never revoked, a pre-#207 state revokes nothing, a wait that dispatched nothing closes no row | same |
| the still-running row is closed out against this wait's own boundary | same |
| the interrupted task returns, writes its rows, marks `did_not_finish`, and never reaches the generic handler | `tests/unit/test_eval_task.py` |
| the four bounds are ordered against the WIDEST wait the code can open, and the cap is what holds the first relation | same |
| a limit during the INSERT still closes the row, and an ordinary insert failure still retries | same |
| a run that finishes after the ceiling closed its row marks itself complete | same |
| a full sink in completion order still lands every row on its own scenario | `tests/unit/test_eval_service.py` |
| scoring that returned leaves every row in the accumulator, and the accumulator is replaced rather than appended to | same |
| `mark_eval_run_did_not_finish` moves only a running row and stamps the reason once, on one connection | same |
| `close_newest_running_eval_run` takes the newest since the boundary, on one connection | same |
| a `did_not_finish` run reaches the gate as a block and the wait as absent | `tests/unit/test_deployment_service.py` |
| hundreds of rows wait for the soft limit's cap, which is the tighter of the two caps | `tests/unit/test_checklist_ceiling_scales.py` |
| the results route carries the run's status and denominators, and an unreadable header costs only itself | `tests/unit/test_eval_routes.py` |
| `by_job` figures priced a second way, the cap, the order, `is_turn`, the null job, and the `jobs` count | `tests/unit/test_usage_service.py` |

## Mutations

Nine, each applied, the named test observed red, then the file restored byte for
byte and observed green.

### a. Delete the `_revoked_eval_task` call in `_close_the_wait`

`pytest tests/unit/test_deployment_task.py::TestTheCeilingStopsTheSpend`,
2 failed / 4 passed:

```
E       AssertionError: the checklist reported eval_did_not_finish and revoked nothing, so the eval it gave up on is still calling the Judge on the tenant's money (#207)
E       assert []
E       AttributeError: 'NoneType' object has no attribute 'kwargs'
```

### f. Revert the revoke signal to SIGTERM

Same file, 1 failed / 5 passed:

```
E       AssertionError: SIGTERM raises SystemExit in the child and SIGKILL runs no handler at all; either one discards the rows the tenant already paid for
E       assert 'SIGTERM' == 'SIGUSR1'
```

### b. Remove `except SoftTimeLimitExceeded` so the task raises

`pytest tests/unit/test_eval_task.py -k SoftTimeLimit`, 5 failed:

```
E       KeyError: 'status'
[error    ] run_eval_suite.eval_failed  error=SoftTimeLimitExceeded() error_type=SoftTimeLimitExceeded
E       AssertionError: the partial scores went nowhere, so the interruption threw away twelve judge calls the tenant was billed for
E       assert [] == ['postgresql://production/tenant']
```

### c. Write `finished` instead of `did_not_finish`

`pytest tests/unit/test_eval_service.py -k ClosingOutARun`, 1 failed / 4 passed:

```
E       AssertionError: assert {'status': 'f...d': 'run-207'} == {'status': 'd...d': 'run-207'}
E         Differing items:
E         {'status': 'finished'} != {'status': 'did_not_finish'}
```

The `did_not_finish` reader tests stay green under this one. They import the
constant, so a renamed value moves with them; the literal in the writer's own
test is what holds the status name.

### d. Drop `soft_time_limit` from the decorator

`pytest tests/unit/test_eval_task.py -k TimeLimits`, 1 failed / 4 passed:

```
E       assert None == 6600
E        +  where None = <@task: app.worker.tasks.runtime.eval.run_eval_suite of wchats>.soft_time_limit
E        +  and   6600 = mod.EVAL_SOFT_TIME_LIMIT_S
```

### g. Remove the sink's index sort

`pytest tests/unit/test_eval_service.py -k TheSinkKeeps`, 1 failed / 1 passed.
The score on each row is its own sample index over ten, so the mis-attribution
is legible in the numbers:

```
E       AssertionError: a scenario was handed the score of whichever row happened to finish in its position: [{'scenario_id': 's0', 'faithfulness': 0.3, ...}, {'scenario_id': 's1', 'faithfulness': 0.0, ...}, {'scenario_id': 's2', 'faithfulness': 0.4, ...}, {'scenario_id': 's3', 'faithfulness': 0.1, ...}, {'scenario_id': 's4', 'faithfulness': 0.2, ...}]
E       assert [0.3, 0.0, 0.4, 0.1, 0.2] == [0.0, 0.1, 0.2, 0.3, 0.4]
```

### h. Drop the success-path `partial` fill

`pytest tests/unit/test_eval_service.py -k AWholeRunSurvives`, 2 failed:

```
E       AssertionError: assert [] == ['s0', 's1', 's2']
E       AssertionError: assert ['stale'] == ['s0']
```

### i. Remove `checklist_wait_ceiling_s`'s cap

`pytest tests/unit/test_eval_task.py -k TimeLimits`, 1 failed / 4 passed:

```
E       AssertionError: the widest checklist wait is 7200s against a soft limit of 6600s, so the worker stops an eval the checklist is still waiting on and the report describes a run that was interrupted on its behalf without knowing it
E       assert 7200 <= 6600
```

### e. Delete the `by_job` cap

`pytest tests/unit/test_usage_service.py -k ByJob`, 3 failed / 7 passed:

```
E       AssertionError: assert 25 == 20
E       assert 0.00836 < 0.00836
```

## Same shape elsewhere

A long task on `runtime` or `pipeline` with no time limit of its own. None of
these is fixed here.

| Task | Where | Bound it advertises |
|---|---|---|
| `run_red_team` | `app/worker/tasks/runtime/red_team.py:657` | a 90 minute idempotency window and nothing that enforces it; the checklist waits on it under the same ceiling and now revokes only the eval half |
| `run_deployment_checklist` | `app/worker/tasks/runtime/deployment.py:1720` | each pass is short, but a pass that hangs on a tenant DB read holds the slot with no limit |
| `run_agent_turn` | `app/worker/tasks/runtime/agent.py:1068` | `AGENT_TURN_TIMEOUT_S` inside the task body, no task-level limit |
| `promote_trace_to_scenario` | `app/worker/tasks/runtime/bench.py:129` | none |
| `run_retrieval_faithfulness` | `app/worker/tasks/runtime/retrieval_eval.py:565` | none |
| `parse_documents` | `app/worker/tasks/pipeline/parse.py:100` | docling on a 4 GB box, no limit |
| `chunk_documents` | `app/worker/tasks/pipeline/chunk.py:99` | none |
| `embed_and_migrate` | `app/worker/tasks/pipeline/embed.py:101` | none |
| `reembed_corpus` | `app/worker/tasks/pipeline/reembed.py:78` | a whole corpus, no limit |
| `generate_eval_suite` | `app/worker/tasks/runtime/eval.py:1947` | none |

Two facts a fix for them has to start from. Every one of these is
`acks_late=True`, so a limit later than `BROKER_VISIBILITY_TIMEOUT_S` is
redelivered before it fires. And a revoke only reaches a task whose worker holds
the id, which is the first risk below.

## Risks this leaves open

**A revoked id lives in one worker's memory.** `celery_app.control.revoke`
broadcasts to the workers that are up, and each one keeps the id in a local set.
This deployment starts workers with `--without-mingle` and no `--statedb`, so a
worker that restarts between the revoke and the moment the id would matter has
no record of it. The window that matters is the handoff inside the dispatched
chain. The checklist revokes `run_eval_suite`'s id while `generate_eval_suite`
may still be running, and a restart in between loses the revoke. The eval then
runs to its own soft limit, which is the backstop this ticket also adds, so the
spend is bounded either way. Closing it properly means `--statedb` on the
runtime worker.

**`eval_results` has no unique key over (run, scenario, metric).** Nothing in
the schema stops the same judge decision being written twice for one run. The
interruption handler is the new path that could do it, and `_fill_partial`
replacing rather than appending is what stops it today. A constraint would stop
it in the database, where it belongs.

**The checklist cannot name the run it dispatched.** `run_eval_suite` mints its
`eval_runs` id after it starts and nothing carries it back over the broker, so
`close_newest_running_eval_run` identifies the row by the boundary and the
'running' status. A run of this agent's started by the nightly beat inside the
same window would be closed out instead. The `eval_task_id` guard narrows it to
waits that actually dispatched, and the run's own handler usually gets there
first.
