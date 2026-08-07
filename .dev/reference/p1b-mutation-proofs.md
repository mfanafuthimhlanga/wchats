# P1b mutation proofs — 21 guards, observed red and green

**Trace:** `.dev/traces/260807-d1-p1b-recorded-mode.md` · **Commits:** `487ebbe`, `117de05` ·
**Branch:** `feat/d1-agent-invocation`

CLAUDE.md: *"A negative test never observed to fail is indistinguishable from a tautology. For any
guard, absence pin, or fail-closed path: mutate the guard, observe red, restore from `HEAD`
unconditionally, observe green. Record the observed output, not the intention."*

Procedure, per guard: apply the mutation → run the named node ids → record the summary line →
`git checkout HEAD -- <file>` **unconditionally**, not conditionally on the red → run again → record.
The restore is unconditional so a mutation that failed to go red still leaves a clean tree, which is
how M2 below was caught rather than quietly skipped.

Paths are relative to `apps/api`. Runs used
`.venv/Scripts/python.exe -m pytest <nodes> -q --tb=no`.

---

## The one that did not go red

**M2 — `build_agent_options` drops its unknown-mode `ValueError`.**

First run, against `487ebbe`:

```
M2 seam drops the unknown-mode ValueError
  RED:   1 passed in 10.57s
  GREEN: 1 passed in 10.02s
```

`test_the_seam_rejects_a_mode_it_does_not_implement` called the REAL `build_tool_server`, which
carries the same check one layer down and raises a `ValueError` whose message also contains
"side_effects". The test was demonstrating the tool layer's guard while claiming to demonstrate the
seam's, and `match="side_effects"` was loose enough not to notice.

The seam's check is **not** redundant with `build_tool_server`'s: it fires before any per-task
ContextVar is set and before the system prompt is assembled, and its message names
`build_agent_options`, which is where the caller made the mistake. But a test cannot prove a guard it
never reaches.

Fixed in `117de05`: collaborators patched out, `match="build_agent_options: side_effects"`. Re-run:

```
M2 seam drops the unknown-mode ValueError
  RED:   1 failed in 8.95s
  GREEN: 1 passed in 8.61s
```

---

## Verbatim harness output — all 21

The M2 lines below are the **post-fix** re-run; every other line is from the single pass against
`487ebbe`. M20 and M21 were re-run alongside M2 and reproduced identically.

```
M1 seam side_effects gains a default
  RED:   1 failed in 15.46s
  GREEN: 1 passed in 10.33s
M2 seam drops the unknown-mode ValueError
  RED:   1 failed in 8.95s
  GREEN: 1 passed in 8.61s
M3 chat path asks for recorded side effects
  RED:   1 failed in 23.42s
  GREEN: 1 passed in 26.58s
M4 seam always wires the real escalation mail
  RED:   1 failed in 10.79s
  GREEN: 1 passed in 11.41s
M5 recorded mode strips issue_refund from allowed_tools
  RED:   1 failed in 11.68s
  GREEN: 1 passed in 10.56s
M6 seam accepts the mode and hardcodes live into the tool server
  RED:   1 failed, 1 passed in 10.66s
  GREEN: 2 passed in 9.67s
M7 canary write deleted entirely
  RED:   1 failed in 24.97s
  GREEN: 1 passed in 23.89s
M8 canary write moved back ahead of the options build (the P1 behaviour)
  RED:   2 failed in 24.11s
  GREEN: 2 passed in 25.75s
M9 dispatcher never takes the recorded branch
  RED:   1 failed in 8.89s
  GREEN: 1 passed in 7.37s
M10 dispatcher always takes the recorded branch
  RED:   1 failed in 7.29s
  GREEN: 1 passed in 7.24s
M11 recorded branch suppresses but does not record
  RED:   1 failed in 7.42s
  GREEN: 1 passed in 7.19s
M12 recorded audit row is not marked as recorded
  RED:   1 failed in 7.13s
  GREEN: 1 passed in 7.19s
M13 recorded branch strands the idempotency reservation
  RED:   1 failed in 7.40s
  GREEN: 1 passed in 7.25s
M14 recorded return is a cheerful confirmation
  RED:   1 failed in 7.27s
  GREEN: 1 passed in 7.23s
M15 recorded branch moved into the shared resolver helper
  RED:   1 failed in 0.80s
  GREEN: 1 passed in 0.79s
M16 retrieve always writes its metrics row
  RED:   1 failed in 0.85s
  GREEN: 1 passed in 0.79s
M17 retrieve never writes its metrics row
  RED:   1 failed in 0.78s
  GREEN: 1 passed in 0.80s
M18 build_tool_server does not publish the mode
  RED:   1 failed in 7.19s
  GREEN: 1 passed in 7.26s
M19 build_tool_server does not reset the recording sink
  RED:   1 failed in 7.17s
  GREEN: 1 passed in 7.59s
M20 build_tool_server accepts an unknown mode
  RED:   1 failed in 7.12s
  GREEN: 1 passed in 0.73s
M21 build_tool_server defaults to recorded
  RED:   1 failed in 7.09s
  GREEN: 1 passed in 7.60s
```

`M20 GREEN: 1 passed in 0.73s` is not an anomaly worth chasing: several restored runs land under a
second because the interpreter and the fake-SDK import are already warm in that process. The `RED`
and `GREEN` runs of a pair are always separate processes.

---

## The mutations, so this is re-runnable

| # | file | mutation | node ids run |
|---|---|---|---|
| M1 | `app/worker/tasks/runtime/agent.py` | `side_effects: SideEffectMode` → `= "live"` | `test_agent_options_seam.py::test_the_seam_refuses_to_build_without_a_side_effects_mode` |
| M2 | `agent.py` | delete the seam's `if side_effects not in SIDE_EFFECT_MODES: raise ValueError(...)` | `..::test_the_seam_rejects_a_mode_it_does_not_implement` |
| M3 | `agent.py` | `run_agent_turn` passes `side_effects="recorded"` | `..::test_the_seam_receives_the_turn_s_own_inputs` |
| M4 | `agent.py` | `notify_fn` conditional's test → `if True` (always the mail closure) | `..::test_recorded_mode_records_the_escalation_instead_of_sending_it` |
| M5 | `agent.py` | `issue_refund` present in `allowed_tools` only when `side_effects == "live"` | `..::test_recorded_mode_grants_exactly_the_same_capability_surface_as_live` |
| M6 | `agent.py` | seam passes `side_effects="live"` to `build_tool_server` regardless of its own argument | `..::test_the_seam_threads_the_mode_into_the_tool_server` (both params) |
| M7 | `agent.py` | post-build canary write guarded by `if False and ...` | `..::test_the_canary_choice_is_committed_once_the_options_exist` |
| M8 | `agent.py` | canary write ALSO placed immediately before `build_agent_options` (the P1 behaviour) | `..::test_the_canary_choice_is_not_committed_when_the_options_build_fails` + `..::test_the_canary_choice_is_committed_once_the_options_exist` |
| M9 | `app/services/transactional/tools.py` | recorded branch condition → `== "no-such-mode"` | `test_recorded_side_effects.py::test_recorded_mode_never_reaches_the_provider_adapter` |
| M10 | `tools.py` | recorded branch condition → `if True` | `..::test_live_mode_still_reaches_the_provider_adapter` |
| M11 | `tools.py` | `record_suppressed_side_effect(...)` replaced by a no-op lambda | `..::test_the_recorded_refund_attempt_is_retrievable` |
| M12 | `tools.py` | `RECORDED_NOT_EXECUTED = ""` | `..::test_recorded_mode_still_writes_its_audit_row` |
| M13 | `tools.py` | delete `await release_idempotency(...)` from the recorded branch | `..::test_recorded_mode_releases_the_idempotency_reservation` |
| M14 | `tools.py` | recorded return text starts `Done.` instead of `NOT EXECUTED:` | `..::test_the_recorded_refund_is_returned_as_an_unmissable_failure` |
| M15 | `tools.py` | `_execute_adapter_and_audit` given a reference to `_side_effects_var` | `..::test_the_shared_adapter_helper_stays_free_of_the_mode` |
| M16 | `app/services/agent_tools.py` | retrieve's recorded branch → `if False` (always writes) | `test_retrieval_metrics.py::test_recorded_mode_does_not_write_the_retrieval_metrics_row` |
| M17 | `agent_tools.py` | retrieve's recorded branch → `if True` (never writes) | `..::test_live_mode_still_writes_the_retrieval_metrics_row` |
| M18 | `agent_tools.py` | `build_tool_server` stops calling `_side_effects_var.set(...)` | `test_recorded_side_effects.py::test_build_tool_server_publishes_the_mode_and_a_fresh_sink` |
| M19 | `agent_tools.py` | `build_tool_server` stops resetting `_recorded_side_effects_var` | same node |
| M20 | `agent_tools.py` | delete `build_tool_server`'s unknown-mode `ValueError` | `..::test_build_tool_server_rejects_a_mode_it_does_not_implement` |
| M21 | `agent_tools.py` | `build_tool_server`'s default → `"recorded"` | `..::test_build_tool_server_defaults_to_live` |

M9/M10, M16/M17 and M1/M21 are deliberate **pairs**: each "the guard fires" mutation has a partner
that removes the opposite behaviour, because `assert_not_called` is satisfied by a code path that was
never entered. That pairing is what caught the vacuous refund fixture during development — see the
trace, § 4.

---

## Separately: the two inverted canary guards, red against the PREVIOUS commit

These are the only guards whose red was observed against real prior behaviour rather than an
injected mutation, which is what an inversion requires. Run at `9d81e34`, before any P1b code
existed:

```
E  AssertionError: the canary choice was committed even though the options build failed
   (call_count=1). The conversation is now sticky to a prompt version that never served a turn,
   and the Celery retry can no longer re-roll it. ...
   assert 1 == 0
    +  where 1 = <MagicMock name='_set_prompt_version_id'>.call_count

E  AssertionError: the commit did not follow the seam call (order=['commit', 'seam', 'sdk_turn']).
   Committing first is exactly the P1 behaviour BACKLOG 2.6 settled against ...
   assert 0 > 1

FAILED tests/unit/test_agent_options_seam.py::test_the_canary_choice_is_not_committed_when_the_options_build_fails
FAILED tests/unit/test_agent_options_seam.py::test_the_canary_choice_is_committed_once_the_options_exist
2 failed, 19 deselected in 29.94s
```
