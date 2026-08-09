# P1b tier-2 fixes — the eval's remaining live edges

Branch `feat/d1-agent-invocation`. Follows `260807-d1-p1b-recorded-mode.md`, which shipped recorded
mode with its guard at step 5.5 of the transactional dispatcher. A tier-2 read of that commit found
the guard was a money guard **at one point of a two-dimensional space** (six skills × seven
dispatcher outcomes) and that two outcomes bypassed it entirely.

## Observed gate

- before: `1716 passed, 11 skipped, 28 warnings in 351.56s (0:05:51)` (at `b7619fe`)
- after:  `1766 passed, 11 skipped, 28 warnings in 404.69s (0:06:44)` (at `df0a0b7`)

`+50`, no test deleted: `test_recorded_side_effects.py` 11 → 55 (`+44`),
`test_agent_options_seam.py` 28 → 31 (`+3`), `test_decision_eval_service.py` 119 → 122 (`+3`). The
intermediate run at `0580ea8` was `1 failed, 1762 passed` — the failure was
`test_the_ast_walk_actually_finds_the_dispatcher_vocabulary`, an existing guard catching the marker
wrapper, and it is the reason the decision-eval work in `df0a0b7` exists.

Lint/types after: `ruff … app/ tests/` → `All checks passed!`;
`mypy app/` → `Success: no issues found in 132 source files`.

One pre-existing artifact worth naming so a future reader does not mistake it for a regression:
running `test_recorded_side_effects.py` (or `test_retrieval_metrics.py`) BEFORE
`test_transactional_tools.py` in a hand-picked subset yields `74 failed` with
`'function' object has no attribute 'handler'` — those files install a passthrough `@tool` fake when
no SDK is in `sys.modules`. Confirmed identical at `b7619fe` with the same file selection. The full
suite's alphabetical order does not hit it; a subset run can.

## What was actually wrong

**Two doors to a live ProviderAdapter that step 5.5 could not see.**

1. `require_human` (tools.py step 5) returns before 5.5 and wrote a `pending_confirmations` row with
   the agent's full arguments. That row is not inert: `_is_confirm_action_shaped` does not filter it
   (it carries `idempotency_key`, never `action_reference`), it appears in the owner's approval queue
   unmarked, and approving it runs `resolve_confirmation_task → execute_approved_confirmation →
   _execute_adapter_and_audit → get_adapter_for_skill` — a real Stripe/Shopify/Woo/Calendly call. A
   nightly eval scenario provoking a large refund queued a real refund for the owner to approve.
2. The step-3 idempotency `replay` arm returned a previously stored **real** provider result.
   `idempotency_key` is model-supplied on every mutating Input model and models produce deterministic
   keys, so an eval scenario mined from a production conversation could collide with a completed
   call and read "Refund of R45.00 issued".

**Three shared or tenant-visible edges the eval kept.**

3. `escalate_to_human_tool` still committed `UPDATE conversations … escalated=true` against the
   tenant DB. Only the mail was swapped, at the seam. The existing guard called the seam's
   `notify_fn` closure directly and never entered the tool, so it could not see the UPDATE.
4. Step 4 INCR'd `ratelimit:{agent}:{skill}:{window}` — no mode dimension — so an overnight eval
   consumed the tenant's real per-skill budget and the next real customer refund read `rate_limit`.
5. `confirm_action_tool` does not use the dispatcher and wrote a real row on every eval scenario in
   which the agent chose to ask for approval.

**Measurement that could not move.** `record_suppressed_side_effect` had three call sites and not one
was on a denial, block, IDV-refusal or `require_human` path, so `get_recorded_side_effects()` omitted
the entire *refused* column of the audit's confusion matrix — P2 could not tell "the agent never
tried" from "the agent tried and the envelope stopped it", which are scored oppositely. And only step
5.5's audit row carried `RECORDED_NOT_EXECUTED`: a recorded `actor_block` row was byte-identical to a
production one, the exact contamination that constant's own comment says it exists to prevent.

**Fidelity.** The recorded tool result told the agent, in its own context, that it was running in
evaluation mode and instructed it not to tell the customer the action completed. Every token after
that was produced by an evaluation-aware agent, and Faithfulness/AnswerRelevancy score exactly those
tokens — the drift class approach (b) exists to close, wearing a different hat.

## Decisions

- **`require_human`: no row rather than a stamped row.** Stamping needs the approval route *and* the
  resolver to fail closed on the stamp, which spreads the eval's concern into the human-approval path
  — the coupling `test_the_shared_adapter_helper_stays_free_of_the_mode` exists to prevent. The Actor
  verdict is not lost: it is in the recorded entry and the (marked) audit row, and the verdict was
  always what carried the eval signal, not the row.
- **Namespacing rather than suppressing, for both shared counters.** Recorded mode reserves
  `recorded:{key}` in `tool_idempotency_keys` and INCRs `ratelimit:recorded:…`. Suppressing the INCR
  would make "the agent kept refunding past its limit" unfalsifiable — the same mistake as handing
  the eval a read-only tool subset. Namespacing the idempotency key also makes a recorded replay
  unreachable (a recorded execution releases, never finalizes, so nothing is ever stored under a
  recorded key); the step-3 mode check is kept anyway, because the namespace is one edit from being
  lost.
- **The recorded transcript no longer discloses the evaluation frame.** The owner's requirement was
  "unmissable, never a silent success", and an `is_error` opening `NOT EXECUTED:` satisfies it
  without telling the model it is being watched. The eval-only marker moved to the audit row and the
  recorded entry, which is where the human grader and P2 read. **Stated as a tradeoff:** a human
  reading only a transcript can no longer distinguish a recorded call from a real provider outage;
  they must read the audit row. That is the correct place for it.
- **Recorded rows are `None` in the decision eval, not `refuse`.** `_ERROR_DISPOSITIONS` gets
  `RECORDED_NOT_EXECUTED` first. The Actor did decide, so the row is not noise — but it decided about
  a scenario. Admitting it would build the Actor's supervised set half from requests that never
  happened, with the eval (whose scenarios are chosen to provoke refusals) supplying that half. This
  was not planned work: `test_the_ast_walk_actually_finds_the_dispatcher_vocabulary` went red on the
  marker wrapper and forced the question, which is that guard working exactly as designed.
- **Sticky-mode leak closed by construction, in two places.** `build_agent_options` resets to the
  safe default before anything that can throw, and `build_tool_server` publishes the mode *after*
  `create_sdk_mcp_server` rather than before it. Neither change alone passes both halves of the test.

## Deviation from the finding list

- Finding 12 (`red_team_probe` builds the customer agent by hand) said itself it was out of scope.
  Landed as `BACKLOG 2.9` plus a correction to the allowlist comment in the seam suite, which
  previously described that module as "adversaries and tooling, not the customer agent" — untrue of
  this one, since the RTX victim IS the customer agent.
- One item was added that no finding named: the decision eval's classification of recorded rows
  (above), forced by an existing guard.

## What cannot be proven here

No PostgreSQL on this machine. Nothing below the unit layer was observed: no `pending_confirmations`
row was really written or really not written, no Redis key was really INCR'd, no
`UPDATE conversations` was really suppressed. Every claim in this file is a unit-level observation
against mocks, with the mutation proofs below as the evidence that the mocks are wired to the code
under test. The end-to-end proof stays behind `BACKLOG 0.2`.

Also unobserved: **no recorded-mode turn has ever run against a real Claude SDK.** Recorded mode's
effect on what the agent *says* after a `NOT EXECUTED` tool result is untested by construction — the
fidelity argument for removing the evaluation-frame disclosure is a reasoned one, not a measured one.

## Mutation proofs

18 guards, each mutated, run red, restored with `git checkout HEAD -- <file>`, run green. Observed
output recorded in the phase report rather than paraphrased. Two are worth naming here:

- **M8 was incomplete and said so.** The bulk replacement reached 6 of the declined-recording sites
  and not the `in_progress` one; the `in_progress` case passed through a mutation meant to kill it.
  M10 was added to mutate that site specifically, and it went red.
- **The first draft of the sticky-mode test passed for no reason.** `build_agent_options` was not
  imported in that scope, `pytest.raises(Exception)` swallowed the `NameError`, and the seam was
  never called. Caught because the assertion still failed; fixed by matching the expected exception
  by message rather than by `Exception`.

## Files

```
apps/api/app/services/transactional/tools.py             the two bypass doors, the marker, the sink
apps/api/app/services/transactional/enforcement.py       rate-key namespace
apps/api/app/services/agent_tools.py                     escalation UPDATE, already_escalated shape,
                                                         reset_side_effect_context, publish-last
apps/api/app/worker/tasks/runtime/agent.py               reset before anything that can throw
apps/api/app/services/decision_eval_service.py           recorded rows are not decisions
apps/api/tests/unit/test_recorded_side_effects.py        11 -> 55 tests
apps/api/tests/unit/test_agent_options_seam.py           28 -> 31 tests
apps/api/tests/unit/test_decision_eval_service.py        122 tests (walk unwrapped, 3 added)
```
