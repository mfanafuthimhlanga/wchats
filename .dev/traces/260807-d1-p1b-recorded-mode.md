# Trace — D1 / P1b: recorded mode + the canary write order (2026-08-07)

**Plan:** `.dev/plans/260807-d1-agent-invocation.md` § P1b · **Branch:** `feat/d1-agent-invocation`
· **Commits:** `487ebbe` (implementation), `117de05` (a guard its own mutation proof caught), this
trace
**Closes:** BACKLOG `2.5`, `2.6` — rows deleted in `487ebbe`, the same commit as the fix.
**Opens:** BACKLOG `2.7`, `2.8` — two things P1b discovered. Numbers `2.5`/`2.6` are deliberately
**not reused**: `agent.py`, `transactional/tools.py` and the plan all cite them for the closed
decisions.

**Gate, observed both sides, never asserted.**

| | |
|---|---|
| baseline at `9d81e34` | `1695 passed, 11 skipped, 28 warnings in 390.08s (0:06:30)` |
| after `487ebbe` | `1716 passed, 11 skipped, 30 warnings in 374.22s (0:06:14)` |
| after `117de05` (final) | `1716 passed, 11 skipped, 28 warnings in 352.60s (0:05:52)` |

+21 is exactly the 21 tests added (8 in `test_agent_options_seam.py`, 11 in the new
`test_recorded_side_effects.py`, 2 in `test_retrieval_metrics.py`). Nothing else moved.

The two warnings that come and go between runs are the pre-existing
`coroutine '_run_sdk_turn' was never awaited` / `AsyncMockMixin._execute_mock_call` RuntimeWarnings,
which surface only when the garbage collector happens to reach the discarded coroutine before the
run ends. They predate this branch (they are in the `af0f601` baseline output too) and are unrelated
to P1b.

---

## 1. Recorded mode (BACKLOG 2.5)

`build_agent_options` returned options carrying a live tool server bound to the tenant's real
`conn_str`. From P2 the eval calls that same seam, so a scenario in which the agent decided to refund
would have issued a real refund against the tenant's provider.

`side_effects: Literal["live", "recorded"]`, **mandatory, no default**. `live` is the chat path
unchanged. `recorded` swaps exactly the three edges the decision named:

| edge | live | recorded |
|---|---|---|
| `notify_fn` | `send_escalation_email` | recorded, no mail |
| retrieval-metrics writer | `write_retrieval_metrics` | recorded, no row |
| transactional `ProviderAdapter` | `get_adapter_for_skill` + execute | recorded, no provider call |

Nothing the agent can **see** or **choose** differs — all eleven tools, the same system prompt, the
same capability envelope, IDV gate, rate ceiling and Actor seam. That is the owner's rejected
alternative preserved structurally rather than by intention: strip the mutating skills and *"the
agent should refuse to refund here"* stops being able to fail, which is drift wearing a safety hat.
`test_recorded_mode_grants_exactly_the_same_capability_surface_as_live` is the guard that notices the
day someone "hardens" recorded mode by trimming the tool list.

**Unmissable, never a silent success.** `is_error: True`, text beginning `NOT EXECUTED:`, and none of
the adapter's output — no refund id, no confirmation message. A first draft of that guard banned
cheerful *words*; it flagged the honest sentence "no money moved", so it now bans the adapter's
*artefacts* instead. The distinction is the difference between a guard on the property and a guard on
the vocabulary.

**Retrievable, not debris.** Two ways, on purpose:
- `agent_tools.get_recorded_side_effects()` — in-process, for P2 to read after the turn returns. The
  sink is a list object installed by `build_tool_server` *before* `asyncio.run()` copies the context,
  so appends made inside the turn are visible to the caller afterwards; a `.set()` inside the turn
  would not be.
- a durable `tool_calls_audit` row marked `side_effects.recorded:not_executed`. AUD-01 symmetry
  holds — a recorded execution is still exactly one audit row — and the marker keeps eval rows out of
  any future labelled set for the Actor gate, which the measurement audit names as the ready-made
  supervised dataset.

**Where the branch lives.** Step 5.5 of `_execute_transactional_tool`, after the Actor gate, **not**
inside `_execute_adapter_and_audit`. Two reasons, both load-bearing and both pinned by
`test_the_shared_adapter_helper_stays_free_of_the_mode`:

1. Steps 1-5 are what the eval measures. Short-circuiting ahead of them would hand the recorded agent
   "not executed" where production hands it "access denied", and the rest of the turn would diverge
   from the product — the exact drift the seam exists to close.
2. The shared helper is also called by `confirmation_resolution.execute_approved_confirmation`, which
   `test_resolver_reads_no_dispatcher_contextvar` forbids from reading dispatcher ContextVars (OD-5).
   A check in the helper would make an approved refund's fate depend on ambient state nobody in that
   call stack set.

## 2. Canary ordering (BACKLOG 2.6) — resolve before, commit after

`_resolve_turn_prompt_version` is now a control-DB **read** returning
`(prompt_version_id, soul_override, needs_persist)`. `run_agent_turn` performs the
`_set_prompt_version_id` write once `build_agent_options` has returned, wrapped so a tenant-DB
failure still cannot fail a turn (T-21-09-05). A turn that dies in options-building re-rolls on
retry, as it did before P1.

`needs_persist` is **returned** rather than re-derived by the caller from
`existing_prompt_version_id is None`, so a future change to the resolution rules cannot leave two
copies of the logic silently disagreeing.

Narrowed consequence, stated rather than hidden: if the persist itself fails, the version still
served the turn and `turn_metrics` still attributes the turn to it — which is the honest record — and
only the stickiness is lost, so the next turn re-rolls. Under P1 a failure anywhere after the resolve
left the conversation stuck to a version that never spoke.

## 3. Deviations from the plan

- **"TWO changes to `agent.py`" was not achievable literally.** The metrics writer lives in
  `agent_tools.retrieve_tool` and the adapter in `transactional/tools.py`; a mode that only `agent.py`
  knows about suppresses nothing. Both were changed. `agent.py` remains the only place the mode is
  *chosen*.
- **`build_tool_server` takes the mode WITH a `"live"` default**, unlike the seam. Its pre-existing
  callers are `red_team.py` and `red_team_probe.py`, which must read real dispatcher verdict tags —
  the two genuinely sound red-team vectors per the measurement audit. A recorded default would turn
  them into theatre. The mandatory-no-default rule belongs where the eval path is chosen, which is
  the seam. Pinned both ways: `test_build_tool_server_defaults_to_live` and
  `test_the_seam_refuses_to_build_without_a_side_effects_mode`.
- **A pre-existing test was rewritten, not just updated.**
  `test_actor_gate_called_before_get_adapter_in_dispatcher` sliced the dispatcher with a fixed
  22 000-character window; step 5.5 pushed the adapter call past it and the test failed with a
  `ValueError` from `.index()`, which reads as a deleted call site rather than a stale constant. That
  constant had already been raised three times (14 000 → 20 000 → 22 000), each time for the same
  reason. Replaced with `ast.unparse` of the dispatcher node: same assertion, exact slice, no fourth
  bump, and no risk of a future bump reaching far enough to find the token in a body the test was
  never reading.

## 4. Mutation proofs

21 guards, each mutated, run, observed red, restored with `git checkout HEAD -- <file>`
unconditionally, run again, observed green. Mutation table and verbatim output:
`.dev/reference/p1b-mutation-proofs.md`.

**One of the 21 did not go red, and that is the most useful line in the file.**

```
M2 seam drops the unknown-mode ValueError
  RED:   1 passed in 10.57s
```

`test_the_seam_rejects_a_mode_it_does_not_implement` called the real `build_tool_server`, which
carries the same check one layer down and raises a `ValueError` whose message also contains
"side_effects" — so `match="side_effects"` matched the wrong guard's exception and the test was green
with the seam's `raise` deleted. It was proving the tool layer while claiming the seam. Fixed in
`117de05` (collaborators patched out, `match="build_agent_options: side_effects"`) and re-proved:
`RED: 1 failed in 8.95s / GREEN: 1 passed in 8.61s`. The seam's check is not redundant with the tool
layer's — it fires before any ContextVar is set and names the function the caller got wrong — but a
test cannot prove a guard it never reaches.

The two inverted canary guards were additionally observed **red against the previous commit
(`9d81e34`)**, before any implementation existed — the ordering that makes an inversion meaningful:

```
E  AssertionError: the canary choice was committed even though the options build failed
   (call_count=1). ...  assert 1 == 0
E  AssertionError: the commit did not follow the seam call (order=['commit', 'seam', 'sdk_turn']).
   ...  assert 0 > 1
2 failed, 19 deselected in 29.94s
```

One guard was caught being vacuous **before** it was trusted. The first refund fixture used
`amount_cents` where `IssueRefundInput` declares `refund_amount_cents`, so `issue_refund_tool`
returned a `ValidationError` before the dispatcher was ever entered — and
`test_recorded_mode_never_reaches_the_provider_adapter` was green with the adapter never called for a
reason that had nothing to do with recorded mode. Its live-mode partner failed and exposed it. Two
things changed as a result: `test_the_refund_fixture_actually_reaches_the_dispatcher` now pins the
fixture against the schema directly, and the money guard asserts the recorded text as well as the
absence of the call. This is BACKLOG 3.3's defect class, caught by having written the anti-tautology
partner first.

## 5. What was NOT proven

- **No PostgreSQL on this machine.** All 11 skips in the gate run are `-m integration`. They are
  **unobserved, never a pass**. `tests/integration/test_prompt_versions_e2e.py` was updated for the
  new `_resolve_turn_prompt_version` signature and **has not been executed** — the update is
  reviewable, not verified.
- **Recorded mode has never run against a real tenant, or a real agent turn.** Every proof here is a
  unit test with the adapter, the metrics writer and the mail sender mocked. That the real
  `ProviderAdapter` is not reached is proven at the `get_adapter_for_skill` boundary, not by watching
  a payment provider fail to receive a request.
- **Nothing calls the seam with `side_effects="recorded"` outside tests.** P2 is the caller and P2
  does not exist. The mode is proven correct and proven unused.
- **No tier-2 judge has read P1 or P1b.**

## 6. Discovered work (now BACKLOG 2.7, 2.8)

- **2.7 — P2 must choose what `conversation_id` it hands the seam, and escalation is the tell.**
  Recorded mode suppresses the escalation *mail*, which is what was settled. It does not suppress
  `_mark_conversation_escalated`, which UPDATEs the tenant `conversations` row. Approach (b) exists
  so the eval writes no `conversations` rows — so that UPDATE will match zero rows,
  `escalate_to_human_tool` will return `{"already_escalated": True}`, and **`notify_fn` will not be
  called at all**. An escalation scenario would then read "already escalated" where production reads
  "I've flagged this conversation", and the recorded notification would never fire. Not a defect in
  P1b; a decision P2 owns and must make deliberately.
- **2.8 — recorded mode does not bound the eval's Actor-gate spend.** Steps 1-5 run live by design,
  and the Actor gate is a synchronous Haiku call per mutating attempt. A scenario set that provokes
  many attempts bills per attempt, on top of the per-turn SDK call. Belongs with the plan's existing
  "cost and latency, unbounded by default" risk and its per-run ceiling.
