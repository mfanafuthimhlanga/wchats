# D1/P2 — mutation proofs, verbatim

**A negative test never observed to fail is indistinguishable from a tautology.** Every guard added by
P2 was mutated, run, observed red, restored from `HEAD` unconditionally (`git checkout -- <file>`,
with a byte-for-byte round-trip assertion), and run again to observe green.

Runner: `scratchpad/mutate.py` — applies a single unique replacement, runs pytest, prints the
`FAILED …` lines and the summary line, restores from `HEAD`, asserts the file round-tripped, runs
again. Baseline for the file under test: `24 passed` (23 before the guard rewrite in M13).

Target unless stated: `tests/unit/test_eval_agent_invocation.py`.

---

## M1 — the eval asks the seam for LIVE side effects

`app/worker/tasks/runtime/eval.py`: `side_effects="recorded"` → `side_effects="live"`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_eval_path_never_asks_for_live_side_effects | FAILED tests/unit/test_eval_agent_invocation.py::test_the_turn_goes_through_the_seam_and_asks_for_recorded_side_effects | 2 failed, 21 passed in 20.47s
GREEN : 23 passed in 18.30s
```

## M2 — reinstate the tautology in the fetched scenario dict

`eval.py`: re-add `"agent_response": row[3],` beside `"reference_answer": row[3],`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_no_scenario_dict_in_the_eval_can_carry_the_label_as_the_prediction | 1 failed, 22 passed in 21.46s
GREEN : 23 passed in 18.72s
```

## M3 — score the label instead of the agent's text

`eval.py`: `"agent_response": response_text,` → `"agent_response": scenario["reference_answer"],`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_no_scenario_dict_in_the_eval_can_carry_the_label_as_the_prediction | FAILED tests/unit/test_eval_agent_invocation.py::test_a_scored_row_never_carries_the_reference_answer_as_its_response | FAILED tests/unit/test_eval_agent_invocation.py::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | 3 failed, 20 passed in 20.92s
GREEN : 23 passed in 19.48s
```

## M4 — score against the stored contexts instead of the agent's

`eval.py`: `"retrieved_contexts": contexts,` → `scenario["stored_retrieved_contexts"]`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_contexts_scored_are_the_ones_the_agent_retrieved | FAILED tests/unit/test_eval_agent_invocation.py::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | 2 failed, 21 passed in 21.23s
GREEN : 23 passed in 18.25s
```

## M5 — score a turn that produced no text

`eval.py`: `if response_text.strip():` → `if True:`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_a_turn_that_returns_no_text_is_counted_apart_from_one_that_raised | 1 failed, 22 passed in 20.64s
GREEN : 23 passed in 17.97s
```

## M6 — score a FAILED scenario instead of excluding it

`eval.py`: in the `except`, also `scored_rows.append({**scenario, "agent_response": "", …})`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_a_failing_scenario_is_excluded_and_counted_never_scored_zero | FAILED tests/unit/test_eval_agent_invocation.py::test_a_run_below_the_response_rate_floor_reports_unknown | 2 failed, 21 passed in 20.16s
GREEN : 23 passed in 18.15s
```

## M7 — the status is always 'measured'

`app/services/eval_service.py`: the `status = (… if attempted and response_rate >= MIN_RESPONSE_RATE
else …)` block → `status = AGENT_INVOCATION_MEASURED`.
Targets: `test_eval_agent_invocation.py` **and** `test_eval_service.py`.

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_a_run_below_the_response_rate_floor_reports_unknown | FAILED tests/unit/test_eval_agent_invocation.py::test_a_run_that_invoked_nothing_is_unknown_not_measured | 2 failed, 126 passed in 21.21s
GREEN : 128 passed in 18.01s
```

## M8 — `agent_invoked` drops the conjunction

`eval_service.py`: `invoked = bool(… status == AGENT_INVOCATION_MEASURED)` →
`bool(agent_invocation and agent_invocation.get("attempted"))`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_a_run_below_the_response_rate_floor_reports_unknown | FAILED tests/unit/test_eval_service.py::TestBuildEvalRunConfig::test_a_run_below_the_floor_is_not_certified_even_though_it_invoked | 2 failed, 126 passed in 20.51s
GREEN : 128 passed in 18.00s
```

## M9 — the loop leaves the process in recorded mode

`eval.py`: `finally: reset_side_effect_context()` → `finally: pass`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_side_effect_mode_is_returned_to_live_when_the_loop_ends | 1 failed, 22 passed in 20.76s
GREEN : 23 passed in 20.59s
```

## M10 — the per-run ceiling is removed

`eval.py`: `invocable = scenarios[:AGENT_INVOCATION_MAX_CALLS_PER_RUN]` → `list(scenarios)`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_per_run_ceiling_bounds_the_calls_and_says_what_it_skipped | 1 failed, 22 passed in 20.36s
GREEN : 23 passed in 18.84s
```

## M11 — the provenance is never written

`eval.py`: `invocation_recorded = update_eval_run_config(...)` → `invocation_recorded = False`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_run_records_that_the_agent_was_invoked | FAILED tests/unit/test_eval_agent_invocation.py::test_the_row_exists_before_the_first_turn_and_is_corrected_after_the_last | 2 failed, 21 passed in 20.85s
GREEN : 23 passed in 18.12s
```

## M12 — the provenance is patched AFTER scoring

`eval.py`: move `update_eval_run_config(...)` below `run_ragas_eval(...)`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_row_exists_before_the_first_turn_and_is_corrected_after_the_last | 1 failed, 22 passed in 26.96s
GREEN : 23 passed in 26.50s
```

## M13 — a second copy of the retrieve cap

`app/worker/tasks/runtime/agent.py`: `[:RETRIEVE_RESULT_CAPTURE_CHARS]` → `[:1800]`

**First attempt: the guard did not fail.**

```
RED   : 23 passed in 24.28s
GREEN : 23 passed in 25.06s
```

`test_the_retrieve_cap_is_read_from_the_turn_path_not_copied` read `inspect.getsource` and asserted
the constant's NAME appeared and `[:1800]` did not. The name appears in the **comment** above the
slice, and the mutated literal spanned three lines so `[:1800]` was not a substring. Both halves were
satisfied by prose — BACKLOG 3.3's defect class, caught only because the mutation was run.

Rewritten as `test_the_turn_bounds_are_read_from_one_copy_of_the_number`, parametrized over both
extracted constants, reading the **AST**: the constant must be defined exactly once at module scope,
the integer must appear nowhere else in the file, and something must Load the name. Re-run:

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_turn_bounds_are_read_from_one_copy_of_the_number[RETRIEVE_RESULT_CAPTURE_CHARS] | 1 failed, 23 passed in 27.13s
GREEN : 24 passed in 18.95s
```

## M13b — a second copy of the turn timeout

`agent.py`: `timeout=AGENT_TURN_TIMEOUT_S,` → `timeout=90,`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_turn_bounds_are_read_from_one_copy_of_the_number[AGENT_TURN_TIMEOUT_S] | 1 failed, 23 passed in 19.93s
GREEN : 24 passed in 18.79s
```

## M14 — the concurrency guard is removed

`eval.py`: `if AGENT_INVOCATION_CONCURRENCY != 1:` → `if False:`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_loop_refuses_to_run_if_the_concurrency_bound_moves_without_it | 1 failed, 22 passed in 25.65s
GREEN : 23 passed in 19.73s
```

## M15 — the stored column is bound to the name the scorer reads

`eval.py`: `"stored_retrieved_contexts": row[4] …` → `"retrieved_contexts": row[4] …`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_stored_context_column_is_not_named_what_the_scorer_reads | FAILED tests/unit/test_eval_agent_invocation.py::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | 2 failed, 22 passed in 19.70s
GREEN : 24 passed in 18.57s
```

## M16 — the eval turn drops the prompt version's soul fields

`eval.py`: `soul_override=soul_override,` → `soul_override=None,`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_turn_serves_the_prompt_version_the_run_is_attributed_to | 1 failed, 23 passed in 25.14s
GREEN : 24 passed in 18.39s
```

## M17 — the eval turn is handed a session-shaped event sink

`eval.py`: `sink = _EvalEventSink()` → an anonymous class with the same three methods

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_eval_turn_writes_no_job_events | 1 failed, 23 passed in 19.97s
GREEN : 24 passed in 18.21s
```

## M18 — only the first scenario is invoked

`eval.py`: `for scenario in invocable:` → `for scenario in invocable[:1]:`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_agent_is_invoked_once_per_scenario | FAILED tests/unit/test_eval_agent_invocation.py::test_a_failing_scenario_is_excluded_and_counted_never_scored_zero | FAILED tests/unit/test_eval_agent_invocation.py::test_a_turn_that_returns_no_text_is_counted_apart_from_one_that_raised | FAILED tests/unit/test_eval_agent_invocation.py::test_a_run_below_the_response_rate_floor_reports_unknown | FAILED tests/unit/test_eval_agent_invocation.py::test_the_mutating_skill_attempts_are_carried_out_of_the_turn | FAILED tests/unit/test_eval_agent_invocation.py::test_the_per_run_ceiling_bounds_the_calls_and_says_what_it_skipped | FAILED tests/unit/test_eval_agent_invocation.py::test_the_task_hands_the_scorer_agent_responses_and_agent_contexts | FAILED tests/unit/test_eval_agent_invocation.py::test_the_run_records_that_the_agent_was_invoked | FAILED tests/unit/test_eval_agent_invocation.py::test_the_row_exists_before_the_first_turn_and_is_corrected_after_the_last | 9 failed, 15 passed in 20.72s
GREEN : 24 passed in 17.67s
```

## M20 — the recorded attempts are read only on the success path

`eval.py`: move `record["side_effects"] = get_recorded_side_effects()` inside `if turn is not None:`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_an_attempt_made_by_a_scenario_that_then_failed_is_still_recorded | 1 failed, 23 passed in 20.34s
GREEN : 24 passed in 17.62s
```

## M21 — a context exactly at the cap is not reported as truncated

`eval.py`: `len(str(c)) >= RETRIEVE_RESULT_CAPTURE_CHARS` → `>`

```
RED   : FAILED tests/unit/test_eval_agent_invocation.py::test_the_bounds_the_run_ran_under_are_on_the_run | 1 failed, 23 passed in 20.17s
GREEN : 24 passed in 17.58s
```

---

**Not mutated, and therefore not proven:** `M19` was folded into `M20` (both express "the recorded
attempts stop reaching the run"); the three `TestUpdateEvalRunConfig` cases in `test_eval_service.py`
were written against a cursor double and their SQL has never executed against a database.
