# The measurement layer — audit (2026-08-05)

Source reading of the eval, red-team and deploy-gate paths. Every claim below was verified by
reading the cited `file:line`, not taken from `.planning/` narrative. This note is the reason
`.dev/plans/260805-eval-foundation.md` exists.

**One-line summary:** the RAG eval measures nothing (the agent is never invoked and the label is
inside the prediction), its results are deleted at the end of every run, the deploy gate's eval half
fails open on a column-name typo, five of seven red-team attackers cannot probe, and the one human
label the system collects is stored as its own opposite.

---

## D1 — Target leakage: the agent is never invoked

`apps/api/app/worker/tasks/runtime/eval.py:200-201`

```python
# For M6: use reference_answer as proxy agent_response to test the eval harness
"agent_response": row[3],   # row[3] IS reference_answer
```

Ragas scores the reference answer against the contexts that reference answer was written from. For
`source='generated'` rows, `scenario_service.py:120` sets `retrieved_contexts = chunk_contents` — the
exact ≤5 chunks Haiku was instructed to answer "based ONLY on the provided content"
(`scenario_service.py:101-102`).

So Faithfulness and AnswerRelevancy approach 1.0 **by construction**. No retrieval runs. No agent
runs. Nothing about the deployed agent is measured. The scaffolding comment is honest about its own
origin — this was true in M6 and was never revisited.

**Tell:** high scores that arrive too easily. Distrust them first.

## D2 — Every result is deleted at the end of the run

`eval.py:227-241` inserts the `eval_runs` row on **production**. `eval.py:256` then creates the Neon
branch. Everything after writes to the branch:

| Write | Line | Target |
|---|---|---|
| `write_eval_results` | `:281` | `branch_conn_str` |
| `promote_to_verified_qa` | `:282` | `branch_conn_str` |
| `update_eval_run_status('complete')` | `:283` | `branch_conn_str` |
| `update_eval_run_status('failed')` | `:305` | `branch_conn_str` |
| `update_eval_run_status('failed')` — branch-create failure | `:267` | `conn_str` ← **production** |

`finally: delete_branch(...)` at `:313` destroys the branch.

Consequences:
- Production `eval_runs` rows sit at `status='running'` **permanently**. A successful run is
  indistinguishable from a hung one.
- The only status production can ever learn is `'failed'`, and only from the one failure mode that
  happens *before* the branch exists.
- `eval_results` never exists on production. `evals.py:87`'s `LEFT JOIN` therefore always yields NULL
  metrics.
- `promote_to_verified_qa` has **never written a durable row**. The verified_qa cache cannot be
  populated by evals.

The branch isolation intent (D-10: never evaluate against production) is correct. Results are
*observations about* the run, not tenant data — they belong on production. The execution conflated
the two.

## D3 — The deploy gate's eval query cannot execute

`apps/api/app/services/deployment_service.py:201-202`

```sql
SELECT metric_name, AVG(score) FROM eval_results WHERE run_id = %s GROUP BY metric_name
```

Schema (`alembic_tenant/versions/0001_tenant_v1_schema.py:165-174`) is `eval_run_id` and `metric`.
`evals.py:87` and `:205` use the correct names; **this is the only call site that does not.**

It raises `UndefinedColumn`, caught at `worker/tasks/runtime/deployment.py:157`, which substitutes
`{"pass_rates": {}, "failing_scenarios": 0, "scenario_count": 0}` and logs a warning.

The orchestrator's blocking condition *"Any eval metric pass_rate < 0.70"*
(`deployment_service.py:79`) evaluates against an empty dict and **can never fire**. The eval half of
the deploy gate fails **open**.

Note `_fetch_verified_qa_stats_sync` (`:277-298`) *does* have an inner try/except returning zeros —
so the defensive pattern was known and simply not applied to the eval fetch.

Also lesser: `_fetch_eval_summary_sync:189` selects the latest `eval_runs` row with no `kind` filter,
though `kind` is `m6:{agent_id}` — fine while one agent per tenant DB holds, wrong the moment it does
not.

## D4 — Five of seven red-team attackers cannot probe

`_TOOL_SEND_PROBE` (`red_team_service.py:179`) and `_TOOL_REPORT_FINDING` (`:197`) are defined at
module scope and **never referenced anywhere in the file**. All four `ClaudeAgentOptions(...)`
constructions (`:286`, `:390`, `:483`, `:936`) pass only `model`, `system_prompt`, `max_turns` — no
`tools`, no `mcp_servers`, no `allowed_tools`.

The loops then test `if block.name == "send_probe"` (`:302`, `:952`) against a tool the attacker was
never given. `raw_findings` stays empty → the runner returns `[]` → **the run reports clean.**

Second defect behind it: `:304` does `await asyncio.to_thread(probe_fn, probe_message)` and
**discards the return value**. Even once wired, the attacker would never see the victim's response,
so `report_finding`'s `agent_response` would be whatever it invents.

**Why 1199 tests stay green over this:** `tests/unit/test_red_team_service.py:165`, `:195`, `:221`
patch `app.services.red_team_service.asyncio.run` with a canned return, so `_run_agent_loop` — the
entire broken region — never executes under test.

Affected: `conversation_injection`, `data_leakage`, `hallucination`, `confused_deputy`. (Plus
`run_prompt_injection_agent`, the back-compat alias at `:347`.)

**Unaffected, and genuinely sound:**
- `content_injection` (`:774`) — seeds `POISONED_CHUNK_CANARY`, probes, substring-tests, removes in
  `finally`. A real oracle.
- `value_bound_evasion` / `identity_bypass` — deterministic, read real dispatcher `verdict_tag`s via
  `red_team_probe`. `:1076` treats `provider_not_configured` as a finding because the run was
  **"INVALID, not clean."** This is the best evaluation reasoning in the codebase and the seed of the
  validity-denominator rule.

## D5 — The human label is stored as its own opposite

`traces.py:84` lists **failing** traces (`status: str = "failing"`). The operator grades one `filed`.
`bench.py:143-151` then writes:

```python
insert_provenance_scenario(conn, source="production", question=question,
                           reference_answer=agent_turn,   # ← the agent's own FAILING answer
                           retrieved_contexts=[], ...)
```

A known-bad answer becomes the ground truth for that question. `eval.py` then fetches it and sets
`agent_response = reference_answer` (D1), scoring the bad answer against itself.

**Ordering hazard — this is load-bearing for the plan.** D2 currently masks D5: promotion writes to
the branch, so the poisoned answer never reaches `verified_qa`. `retrieval_service.py:98`
(`verified_qa_lookup`) serves `verified_qa` rows to real customers *before* hybrid search at 0.93
cosine similarity. **Fixing D2 without fixing D5 in the same change activates a path that serves a
human-flagged failure to customers.** `retrieved_contexts=[]` (`bench.py:148`) is incidental
protection only — faithfulness against empty context should fall below the 0.90 gate — not design.

## D6 — Mined scenarios are inert by construction

`mine_production_scenarios` (`scenario_service.py:348`) writes rows with `reference_answer=''`
(honestly — no ground truth exists for a production failure). `eval.py:175` selects
`WHERE reference_answer != ''`. Mined rows can never be selected.

Intended (the docstring at `:369` says so) but the consequence is that EVL-03 produces write-only
data.

## D7 — The judges are uncalibrated, and the harness for it already exists

`apps/api/tests/evals/calibration/compute_correlation.py` computes **Spearman rank correlation
between judge scores and human scores, gated at ≥ 0.75** before trusting automated judge results at
scale (cites `AI-SPEC.md §5.2`). 20 scenarios exist in `tests/evals/scenarios/`.

`tests/evals/calibration/human_scores.csv` has 10 rows and **every `human_score` cell is empty**.
`tests/evals/responses/` was never captured.

So every LLM verdict in the system is uncalibrated: Gatekeeper, Auditor, Strategist,
`classify_severity`, and — the one that matters most — the **Actor gate**, which runs synchronously
before money moves and returns approve / block / require_human.

The instrument was built to the right specification and never given a single label. The gap is ten
rows of human judgement wide, not a build.

---

## Coverage: transactional capabilities

| Surface | Red-teamed? | Evaluated? |
|---|---|---|
| RAG answering | partially (canary probe live; 4 conversational attackers dead) | no — see D1 |
| Transactional skills | **yes** — `red_team.py:389-400` routes 3 RTX runners through `transactional_probe_fn` against the real dispatcher; `CLEAN_TENANT_ENVELOPES` (`red_team_probe.py:406-453`) covers all six mutating skills | **no** — grep of `eval_service.py`, `scenario_service.py`, `eval.py` for `issue_refund\|transactional\|tool_call\|skill` returns nothing |

2 of 3 transactional security vectors are live (`confused_deputy` is dead per D4).

**The metric family is wrong for transactional work, not merely absent.** Faithfulness asks "is this
text grounded in that text." The transactional questions are *decisions*: did it pick the right
skill, extract the right amount, refuse when it should have, escalate rather than guess. That needs a
confusion matrix.

|  | Should execute | Should refuse |
|---|---|---|
| **Executed** | ✓ | **FP — money moves wrongly.** Critical. |
| **Refused** | **FN — friction.** | ✓ |

The FN cell has a product consequence already visible in the docs: an over-refusing agent frustrates
the owner, and the owner's instinct is to loosen the capability envelope.
`docs/guides/owner-capability-guide.md` is explicitly prohibited from presenting a loosened control
as a remedy for friction — so the failure mode is known socially and unmeasured technically.

**`tool_calls_audit` is the dataset this needs and nothing reads it for quality.**
`app/models/tool_calls_audit.py:31-57`: `agent_id, conversation_id, skill, arguments, result,
actor_decision, actor_rationale, capability_snapshot, latency_ms, error, created_at`. A complete
decision log — input, decision, rationale, configuration at decision time, outcome. Label a sample
and it is a supervised evaluation set for the Actor gate, with no new capture infrastructure.

## The version-attribution gap

`eval_runs` is `(id, kind, started_at, finished_at, status)` — `0001:152`. A score has no idea what
produced it.

Every version dimension is already captured *somewhere else*:

- `prompt_versions` — immutable, control DB, diff/canary/rollback (Phase 21)
- `turn_metrics.prompt_version_id` — nullable, `0009:86`, *"reserved for OPS-16"* — the precedent
  exists on the runtime fact table and was never extended to evals
- `_compute_envelope_hash_sync` — capability configuration, hashed
- `check_index_staleness` — corpus freshness
- model IDs, retrieval config, `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE`

**The unit of evaluation is not "the agent" but a configuration tuple:**
`(prompt_version_id, corpus_snapshot_id, model_id, retrieval_config_hash, envelope_hash)` → run over
dataset D → scores S. Without that join, two runs differing on one dimension cannot be compared, and
"what changed?" is unanswerable — which is the whole mechanism of continuous improvement.

## Sampling: drift detection is currently impossible

`eval.py:177` — `ORDER BY RANDOM() LIMIT 30`. A **different sample every night**. Run-to-run variance
is dominated by which 30 rows were drawn, not by anything the agent did.

A **fixed** golden set is not just more stable, it is a sharper instrument: identical items across
runs allow **paired per-item deltas** instead of unpaired mean comparison. At n=30 an unpaired 5-point
regression is invisible inside sampling noise; paired on the same 30 items it is detectable. Same
cost.

Tradeoff to state honestly: a fixed set gets overfit over time. That is what a rotating exploratory
set is for, plus a scheduled golden-set refresh policy.

## Label trust hierarchy (proposed, not yet enforced anywhere)

```
human-authored answer    → may gate a deploy, may be served to customers
human-verified answer    → may gate a deploy
customer thumbs-down     → labels a negative; never fabricates a positive
model-generated answer   → exploratory metrics only. Never gates. Never served.
```

The current promotion path violates the bottom line twice: a Haiku-written answer clears 0.90/0.90
and is served via `verified_qa_lookup`. Only D2 prevents it today, by accident.

**Human label sources already shipped and disconnected:**
1. `message_feedback` (`0009:100-108`) — `message_id, conversation_id, rating ('up'|'down'),
   csat_score 1-5`. Shipped Phase 23. Feeds a CSAT tile and nothing else. The only *direct customer
   label* in the system.
2. The owner's `filed` grade — real, miswired (D5).
3. Escalation events — implicit negatives.

**Label efficiency is the binding constraint** for a non-technical owner who will grade ~5 things a
week. `validators.py:220` already emits judge `confidence` into `job_events`. Ranking the bench by
low confidence or by judge disagreement (Gatekeeper pass vs Auditor ungrounded) is uncertainty
sampling — the ranking signal is already computed and discarded.

## On automated prompt evolution

`prompt_versions` (immutable, canary sticky per-conversation at turn dispatch, rollback, history
never overwritten) is the hard half of safe prompt evolution and it is already shipped. The missing
half is the fitness function.

Two constraints before any optimizer touches a deployed agent:

1. **The optimizer may never modify a capability envelope.** Prompts live *inside* the safety
   boundary; the envelope *is* the boundary. `validate_tighten_only` enforces this against humans;
   the same must hold for an automated proposer, structurally rather than by instruction.
2. **The fitness function must include refusal correctness.** Optimize for helpfulness alone and the
   optimizer learns to refuse less, because refusals look like failures — a direct attack on the
   behaviour the Actor exists to produce, invisible to every metric currently computed.

Pointing an optimizer at today's scores would optimize a tautology (D1): the score is invariant to
the prompt. Goodhart arrives fastest when the search is automated.
