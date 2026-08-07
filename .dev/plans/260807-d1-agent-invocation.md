# D1 — make the eval measure the agent

**Branch:** `feat/d1-agent-invocation` off `main` (`af0f601`) · **Baseline:** 1675 passed / 11 skipped
/ 0 failed / 451s, observed 2026-08-07 at `af0f601`.

**Scope:** `apps/api/app/worker/tasks/runtime/eval.py`, `apps/api/app/worker/tasks/runtime/agent.py`
(extraction only), `apps/api/app/services/deployment_service.py`, one `alembic_tenant` revision, and
their tests.

---

## Why

`eval.py:374-375`:

```python
# For M6: use reference_answer as proxy agent_response to test the eval harness
"agent_response": row[3],
```

`row[3]` is `reference_answer`. The label is the prediction. Faithfulness and AnswerRelevancy
approach 1.0 by construction, and no amount of scaffolding above this can move the number.

Three consequences already shipped on top of it (BACKLOG 2.1-2.3):

- the deploy gate fail-closes on an **absent** eval signal while shipping on a **present** one that
  measures nothing;
- the config tuple stamps `prompt_version_id` / `model_id` on the tautology, which makes it look
  provenanced and therefore credible;
- no gate test reads whether the agent was invoked at all, because nothing records it.

## The decision this plan turns on

**How does a Celery eval task invoke the customer agent?** Three readings, materially different work:

| | Approach | Cost |
|---|---|---|
| **a** | Call the `run_agent_turn` task per scenario | Exercises the true production path — but writes a `conversations` row, `messages` rows, `turn_metrics` and SSE events **per eval scenario** into the tenant's real data. Eval traffic then pollutes CSAT, cost dashboards and the mined-scenario source. |
| **b** | Extract the shared core — options construction + `_run_sdk_turn` — and call it from both | Same system prompt, tools and capability envelope as production, which is what actually determines agent behaviour. Persistence and SSE differ by design. Risk: the extracted seam drifts from the production caller and we measure something adjacent to the real thing, which is this repo's recurring defect. |
| **c** | HTTP against the running API, as `capture_responses.py` does | Most faithful, and unrunnable from inside a Celery task on this machine. |

**SETTLED by the owner, 2026-08-07: (b), the shared seam.** The drift risk is closed structurally
rather than by intention — one constructor for `ClaudeAgentOptions` that both callers must go
through, and a test that fails if `run_agent_turn` builds options by any other route. (b) over (a)
because eval traffic in `conversations` would corrupt `mine_production_scenarios`, which reads that
table: the eval would begin generating its own future test set from its own output.

**Also settled: P3 refuses an absent `agent_invoked`, not only an explicit `false`.** Every eval run
persisted to date was produced by the tautology and carries no such field, so a gate that refuses
only `false` would keep shipping on the whole of history — the exact shape of BACKLOG 3.1, where
pre-P4 red-team runs still read `signal='measured'` with clean findings. Consequence, accepted: once
P3 lands and before P2 does, the gate refuses every eval signal. That is a mid-branch state on one
branch, and it fails closed.

## Phases

### P1 — the seam

Extract options construction into one callable used by `run_agent_turn` and the eval. No behaviour
change to the chat path; the unit suite must stay at 1675/11/0 exactly.

**Test:** the drift guard — `run_agent_turn` cannot construct options except through the seam.
Mutate it, observe red, restore from `HEAD`, observe green, and record the observed output.

### P2 — invoke, and record that you invoked

**PRECONDITION, added 2026-08-07 after P1's tier-2 read (BACKLOG 2.5).** The seam returns options
carrying a **live** tool server bound to the tenant's real `conn_str`. `retrieve` writes
`retrieval_metrics`; `escalate_to_human` marks the conversation and sends mail; the six mutating
skills write `tool_calls_audit` and call the real `ProviderAdapter`. (b) was chosen over (a) to keep
eval traffic out of tenant data, and (b) as built still writes tenant tables **and can move money** —
one eval scenario in which the agent decides to refund executes a refund. P1's seam comment
pre-argued against adding the parameter P2 now needs; that argument does not survive a caller that
needs one. Settle it before the first invocation, not at runtime against a real tenant:

- **either** a mandatory `side_effects: Literal["live", "recorded"]` on the seam that swaps
  `notify_fn`, the metrics writer and the transactional adapter for no-ops on the eval path,
- **or** a read-only `allowed_tools` subset for the eval, plus a test that fails if any of
  `MUTATING_SKILLS` (named in `tests/unit/test_agent_options_seam.py`) reaches that path.

**SETTLED by the owner, 2026-08-07: recorded mode on the seam.** `side_effects: Literal["live",
"recorded"]`, mandatory, no default — a caller that does not state which it wants does not compile
past review. Chosen over the read-only subset because removing the mutating skills would mean the
eval measures an agent with fewer capabilities than production serves: a scenario testing *"the
agent should refuse to refund here"* could no longer fail, because the agent could not even try. That
is drift, and drift is the thing P1 exists to prevent.

Consequences that are now requirements:

- The no-op must be **unmissable, never a silent success.** A recorded `issue_refund` that returns a
  cheerful confirmation teaches the agent it succeeded and diverges the rest of the turn. It records
  the attempt and returns something the transcript shows plainly.
- **The recording is eval signal, not debris.** That the agent chose to call `issue_refund` is one of
  the more valuable things an eval can observe — capability-envelope adherence. Persist it.
- `live` stays the behaviour `run_agent_turn` has today, byte-for-byte. The chat path must not
  change; its 1695/11/0 is the evidence.

### P1b — the two agent.py changes P1's review surfaced

Both settled, both on the turn path, both needing their own red-observed test. Kept out of P2 so the
eval diff stays reviewable.

1. **Recorded mode**, above.
2. **Canary ordering (BACKLOG 2.6) — resolve before, commit after.** The soul fields genuinely must
   resolve before the system prompt is built, so `_resolve_turn_prompt_version` stays where P1 put
   it. What moves back is the *write*: `conversations.metadata.prompt_version_id` is committed only
   once `build_agent_options` has returned, so a turn that dies in options-building re-rolls as it
   did before P1. This inverts `test_the_canary_choice_is_committed_before_the_options_can_fail`,
   which currently pins P1's behaviour — that test must be rewritten to pin the new one, and observed
   red against the old code.

- `eval.py` calls the agent per scenario. `agent_response` becomes the agent's `response_text`.
- **`retrieved_contexts` must come from the agent's own `retrieve` result, not from `row[4]`.**
  Scoring faithfulness against contexts the agent never saw is D1 wearing a different hat.
- **`agent.py:588` truncates the retrieve result to 1800 chars.** Faithfulness over a truncated
  context marks a claim unsupported when the support was merely cut. Either carry the untruncated
  result on the eval path or record the truncation in the run's provenance. Do not leave it implicit.
- A scenario whose agent call fails is **excluded and counted**, never scored 0. Zero is not a low
  score, it is the absence of one — the lesson `compute_correlation.py:485` already learned.
- A run where too few scenarios produced a response reports `unknown`, never `pass`. Reuse the
  `MIN_PAIR_RATE` shape rather than inventing a second one.

**Tests:** the agent is invoked once per scenario; a failing scenario is excluded not zeroed; a run
below the response-rate floor reports `unknown`; `agent_response != reference_answer` for every
scored row — the regression pin for D1 itself.

### P3 — the gate learns to refuse a tautology

- New provenance field `agent_invoked` on the eval run, written by P2, migration in `alembic_tenant`.
- **The deploy gate refuses an eval signal with `agent_invoked` false or absent.** Absent matters:
  every run already persisted was produced by the tautology, so a gate that only checks `false`
  ships on all of history.
- Gate test that reads it, closing BACKLOG 2.2.

**Tests:** gate refuses `false`; gate refuses absent; gate accepts `true`. Mutate each, observe red.

## Out of scope, deliberately

- **D6** (mined scenarios inert — `reference_answer=''` vs `WHERE != ''`, BACKLOG 2.4). Same section,
  different change; folding it in makes the diff unreviewable.
- **D2/D5** (results written to a Neon branch deleted in `finally`; failing trace stored as ground
  truth). The audit's ordering constraint binds *those two to each other*, not to this: D1 changes no
  write-back path, so it lands alone safely. Do not touch `bench.py` here.
- Anything in §3's verification debt.

## Risks

- **Cost and latency, unbounded by default.** The golden set runs in FULL every eval plus
  `EXPLORATORY_SAMPLE_SIZE` rotating rows, at one SDK call each and a 90s per-turn timeout. A nightly
  task that was seconds of arithmetic becomes tens of minutes of live model calls per agent, billed.
  P2 needs a concurrency bound and a per-run ceiling, and both belong in provenance.
- **The metric will get worse, and that is the point.** Faithfulness falls from ~1.0 to whatever is
  true. Anyone reading the drop as a regression will be wrong; the drop is the instrument starting to
  work. Say so in the trace, and expect the deploy gate's 0.70/0.85 absolute thresholds to be wrong
  for the first time in a way that is visible (BACKLOG 6.2 is the real answer, and is not this phase).
- **The seam is the whole bet.** If the eval path and the chat path diverge, this replaces a tautology
  with a measurement of a thing that is not the product. The drift guard is load-bearing, not hygiene.

## What cannot be proven here

No PostgreSQL on this machine; `CONTROL_DB_URL` is live Neon production and is never a substitute.
So: the migration will not be applied, the integration suite will skip, and no end-to-end eval run
will be observed. **Unit-testable and reviewable in full; unprovable end to end.** The trace says
exactly that rather than implying coverage. This also puts the phase behind BACKLOG `0.2` for its
final proof, the same precondition that turned out to gate `0.1`.
