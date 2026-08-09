# P2 review fixes — mutation proofs (2026-08-08)

Twenty-three guards added or repaired while closing the tier-2 findings against
`feat/d1-agent-invocation` P2. Every one was **run** in both directions:

1. apply the mutation, run the guard, record the output;
2. `git checkout HEAD -- <file>` **unconditionally**, run again, record the output.

A negative test never observed to fail is indistinguishable from a tautology, and
this branch has already produced two of those — the retrieve-cap guard that was
proving a comment (`7a7486e`), and the context guard that a one-token fallback
walked straight past. Both are in the list below, now with the case that
separates them.

Driver: `scratchpad/mutate.py` (session-local, not versioned). Selectors are run
with `-p no:randomly` so a red is the mutation, not an ordering.

**One mutation did not go red on the first attempt** and is recorded as such in
the trace: `at-cap-measured-against-the-audit-capture` passed 2/2 against the
cap tests as first written, because neither fixture separated the 1800-char
audit capture from the 2000-char per-chunk cap. `075550d` adds the production
shape — three 700-char chunks, whose repr exceeds the audit cap while no chunk
is anywhere near the chunk cap — and the mutation then goes red.

---

### stored-context-fallback

`app/worker/tasks/runtime/eval.py`

```
"retrieved_contexts": contexts,  ->  "retrieved_contexts": contexts or scenario["stored_retrieved_contexts"],
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_stored_context_column_is_never_read_back_by_the_eval`
- **observed RED:** `1 failed in 19.47s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 12.75s`

### eval-dispatches-the-chat-task

`app/worker/tasks/runtime/eval.py`

```
sink = _EvalEventSink()  ->  from app.worker.tasks.runtime.agent import run_agent_turn  # noqa: F401
    sink = _EvalEventSink()
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_eval_reaches_a_turn_only_through_the_seam_and_never_through_the_task`
- **observed RED:** `1 failed in 14.58s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 12.33s`

### eval-invokes-agent-constant-lies

`app/services/eval_service.py`

```
EVAL_INVOKES_AGENT = True  ->  EVAL_INVOKES_AGENT = False
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_constant_that_claims_the_agent_is_invoked_is_pinned_to_the_code`
- **observed RED:** `1 failed in 15.07s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `1 passed in 13.24s`

### no-retrieval-row-is-scored-anyway

`app/worker/tasks/runtime/eval.py`

```
if record["responded"] and contexts:  ->  if record["responded"]:
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_a_scenario_the_agent_answered_without_retrieving_never_reaches_the_scorer`
- **observed RED:** `1 failed in 20.80s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 17.87s`

### unparsed-counted-as-no-retrieval

`app/worker/tasks/runtime/eval.py`

```
if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
                        record["retrieve_un  ->  if False:
                        record["retrieve_unparsed"] += 1
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_a_retrieve_result_that_cannot_be_read_is_counted_apart_from_no_retrieval`
- **observed RED:** `1 failed in 20.07s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 17.77s`

### absolute-floor-removed

`app/services/eval_service.py`

```
and scorable >= MIN_SCORED_OBSERVATIONS  ->  and scorable >= 0
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_a_run_with_too_few_scored_rows_is_unknown_however_good_its_rate_is`
- **observed RED:** `1 failed in 20.22s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `1 passed in 17.87s`

### coverage-divides-by-attempted

`app/services/eval_service.py`

```
coverage_rate = (responded / valid) if valid else None  ->  coverage_rate = (responded / attempted) if attempted else None
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_run_reports_coverage_against_what_the_tenant_designated`
- **observed RED:** `1 failed in 20.18s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `1 passed in 17.86s`

### below-floor-run-scores-anyway

`app/worker/tasks/runtime/eval.py`

```
if invocation["status"] != AGENT_INVOCATION_MEASURED:  ->  if False:
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_a_run_below_the_floor_writes_no_scores_and_so_cannot_report_a_pass`
- **observed RED:** `1 failed in 20.20s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 17.88s`

### scored-source-from-attempted

`app/services/eval_service.py`

```
if scorable:
        scored_response_source = EVAL_SCORED_RESPONSE_SOURCE  ->  if attempted:
        scored_response_source = EVAL_SCORED_RESPONSE_SOURCE
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_a_run_where_nothing_reached_the_scorer_does_not_claim_agent_sourced_scores`
- **observed RED:** `1 failed in 20.88s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `1 passed in 20.07s`

### sink-not-emptied-per-iteration

`app/worker/tasks/runtime/eval.py`

```
reset_side_effect_context()
            record: dict = {  ->  record: dict = {
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_an_attempt_is_attributed_to_the_scenario_that_made_it_and_no_other`
- **observed RED:** `1 failed in 20.27s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 18.12s`

### eval-keeps-its-own-copy-of-the-timeout

`app/worker/tasks/runtime/eval.py`

```
from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S  # noqa: PLC0415

    return AGENT_TURN_TIMEOU  ->  AGENT_TURN_TIMEOUT_S = 90

    return AGENT_TURN_TIMEOUT_S
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_eval_imports_the_turn_bounds_rather_than_restating_them`
- **observed RED:** `1 failed, 1 passed in 20.33s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `2 passed in 18.30s`

### visibility-timeout-below-the-ceiling

`app/worker/celery_app.py`

```
BROKER_VISIBILITY_TIMEOUT_S = 7200  ->  BROKER_VISIBILITY_TIMEOUT_S = 3600
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_broker_lets_a_run_reach_the_ceiling_the_run_advertises`
- **observed RED:** `1 failed in 20.23s`
- **observed GREEN (after `git checkout HEAD -- app/worker/celery_app.py`):** `1 passed in 17.68s`

### idempotency-window-back-to-ten-minutes

`app/worker/tasks/runtime/eval.py`

```
AND started_at > NOW() - (%s * INTERVAL '1 second')
                    LIMIT 1
                    """,
       ->  AND started_at > NOW() - INTERVAL '10 minutes'
                    LIMIT 1
                    """,
          
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_idempotency_window_covers_a_run_that_uses_its_whole_ceiling`
- **observed RED:** `1 failed in 15.27s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 17.74s`

### gate-reads-the-in-flight-run

`app/services/deployment_service.py`

```
"SELECT id, finished_at, status, config FROM eval_runs "
                        "WHERE kind = %s AND status <  ->  "SELECT id, finished_at, status, config FROM eval_runs "
                        "WHERE kind = %s "
          
```

- selector: `tests/unit/test_deployment_service.py::TestSignalCollectionFunctions::test_an_in_flight_run_does_not_shadow_the_last_finished_one`
- **observed RED:** `1 failed in 2.66s`
- **observed GREEN (after `git checkout HEAD -- app/services/deployment_service.py`):** `1 passed in 2.02s`

### promotion-writes-the-agents-own-answer

`app/services/eval_service.py`

```
answer = promotable_answer(scenario)  ->  answer = scenario.get("agent_response", "")
```

- selector: `tests/unit/test_eval_service.py::TestPromoteToVerifiedQA::test_the_promoted_answer_is_the_label_not_the_agents_own_text`
- **observed RED:** `1 failed in 13.23s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `1 passed in 12.27s`

### blank-label-promoted

`app/services/eval_service.py`

```
if not promotable_answer(scenario):
            _refuse("no_promotable_answer")
            continue  ->  if False:
            _refuse("no_promotable_answer")
            continue
```

- selector: `tests/unit/test_eval_service.py::TestPromoteToVerifiedQA::test_a_row_with_no_label_is_refused_rather_than_promoted_blank`
- **observed RED:** `1 failed in 14.50s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `1 passed in 11.34s`

### second-orchestrator-accepts-the-tautology

`app/services/eval_service.py`

```
if tautologies:
        raise ValueError(  ->  if False:
        raise ValueError(
```

- selector: `tests/unit/test_eval_service.py::TestTheSecondOrchestrator`
- **observed RED:** `3 failed in 19.07s`
- **observed GREEN (after `git checkout HEAD -- app/services/eval_service.py`):** `3 passed in 11.02s`

### chunks-flattened-back-into-one-blob

`app/worker/tasks/runtime/agent.py`

```
texts: list[str] = []
    for chunk in chunks:  ->  return [payload.strip()]
    texts: list[str] = []
    for chunk in chunks:
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_scored_context_is_the_chunk_text_not_a_repr_of_the_transport`
- **observed RED:** `1 failed in 20.28s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/agent.py`):** `1 passed in 17.45s`

### unreadable-payload-reported-as-empty

`app/worker/tasks/runtime/agent.py`

```
except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None  ->  except (ValueError, SyntaxError, MemoryError, RecursionError):
        return []
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_an_unreadable_retrieve_payload_is_none_and_not_an_empty_retrieval`
- **observed RED:** `1 failed in 20.06s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/agent.py`):** `1 passed in 17.71s`

### emit-gains-a-flush

`app/services/events.py`

```
db.add(event)
    db.commit()  ->  db.add(event)
    db.flush()
    db.commit()
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_eval_turn_writes_no_job_events`
- **observed RED:** `1 failed in 20.11s`
- **observed GREEN (after `git checkout HEAD -- app/services/events.py`):** `1 passed in 17.56s`

### retry-re-buys-the-invocation

`app/worker/tasks/runtime/eval.py`

```
if agent_was_invoked:
            log.error(  ->  if False:
            log.error(
```

- selector: `tests/unit/test_eval_task.py::TestBranchDeletion::test_a_failure_after_the_invocation_does_not_re_buy_sixty_sdk_turns`
- **observed RED:** `1 failed in 19.89s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `1 passed in 18.91s`

### second-copy-of-the-turn-timeout

`app/worker/tasks/runtime/agent.py`

```
timeout=AGENT_TURN_TIMEOUT_S,  ->  timeout=90,
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_the_turn_bounds_are_read_from_one_copy_of_the_number`
- **observed RED:** `1 failed, 1 passed in 19.96s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/agent.py`):** `2 passed in 17.83s`

### at-cap-measured-against-the-audit-capture

`app/worker/tasks/runtime/eval.py`

```
if any(len(c) >= CHUNK_CONTENT_CHAR_LIMIT for c in chunks):  ->  if any(len(str(tc.get('result'))) >= RETRIEVE_RESULT_CAPTURE_CHARS for _ in [0]):
```

- selector: `tests/unit/test_eval_agent_invocation.py::test_a_full_retrieval_of_uncut_chunks_is_not_reported_as_truncated tests/unit/test_eval_agent_invocation.py::test_a_run_that_only_ever_saw_short_chunks_reports_none_at_the_cap tests/unit/test_eval_agent_invocation.py::test_the_bounds_the_run_ran_under_are_on_the_run`
- **observed RED:** `1 failed, 2 passed in 20.98s`
- **observed GREEN (after `git checkout HEAD -- app/worker/tasks/runtime/eval.py`):** `3 passed in 18.13s`
