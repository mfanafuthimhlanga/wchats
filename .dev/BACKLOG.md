# BACKLOG — the single ordered list

**Every open item, with its source.** Written 2026-08-06 because outstanding work had accumulated
across five files and one temp-directory journal, and "what is next?" could only be answered by
reading all of them. If an item is not here, it is not tracked.

**Maintenance rule:** a phase that closes an item deletes its row here in the same commit that lands
the fix. A phase that discovers work adds a row. `.dev/traces/` records what happened; this file
records what has not.

Legend — **[owner]** needs a human, **[blocked]** has an external precondition, **[code]** is ordinary work.

---

## 0. Blocked on you, and nothing above them is trustworthy

| # | Item | Source |
|---|---|---|
| 0.1 | **[blocked, then owner]** Score the 10 rows in `apps/api/tests/evals/calibration/human_scores.csv`. Until then every LLM judge in the system is uncalibrated — Gatekeeper, Auditor, Strategist, `classify_severity`, and the **Actor gate that runs before money moves**. The harness exists and gates at Spearman ≥ 0.75; no agent may fill that column. **Corrected 2026-08-07: this is not yet owner work — it is behind `0.2`.** `--check` exits 3 and names one blocker: `responses/` has never been captured (0 on disk, 20 scenarios). `capture_responses.py` needs `AGENT_E2E_ENABLED=1`, a live API, a provisioned *and ingested* agent, and its plaintext key. There is nothing to score yet, by a human or anyone else. | audit D7 |
| 0.2 | **[blocked]** Install a local PostgreSQL server. One precondition unblocks `VER-01`, `AUD-03`, `CAP-03`, the 6 blocked `23-UAT.md` checkpoints, the 4 `human_needed` items in `23-VERIFICATION.md`, and the 3 migration roundtrips below. Nothing listens on 5432-5435; `CONTROL_DB_URL` is live Neon production and is never a substitute. | HANDOFF |
| 0.4 | **[owner] BLOCKS THE D1 MERGE. Production customer rows can reach the Ragas judge API.** Up to 60 nightly eval turns run against the tenant's **production** `conn_str`; `lookup_structured` returns `SELECT *` customer rows into the transcript; that transcript goes to the judge API with `pii_firewall_applied=False`. `2.11` records this only as a *scoring-fidelity* question — whether a firewall deflection should be scored as an answer. The **egress** reading is addressed nowhere on the branch. Decide before the first real eval run: firewall on the eval path, or accepted egress, named as such. | D1 tier-2 must-fix #2 |
| 0.5 | **[owner] BLOCKS THE D1 MERGE. Settle the missing `alembic_tenant` migration.** The plan and the workflow prompt both required one; none exists and `0015` remains head. P2 put `agent_invoked` inside `eval_runs.config`, the JSONB that `0013` already added, so the implementers argue there is no DDL to write — coherent, and it avoids a second home for the one gate-facing claim. But it is a **deviation from a written contract**, and only you can accept it. One signed-off line in this file or HANDOFF suffices; silence does not. | D1 tier-2 must-fix #1 |
| 0.3 | **[owner]** Actions minutes / spending limit at `github.com/settings/billing`. Two runs cancelled at 15m03s and 15m02s with every job killed — Lint included, which takes 11s. Until this lifts CI reports nothing and the gate is unreadable. | HANDOFF |

## 1. CI — finish what the cap interrupted

> **PAUSED by the owner, 2026-08-07.** The wall-clock cap is the binding constraint and it is a
> billing question, not a code one. `1.1` and `1.2` cannot execute until `0.3` lifts — they need a
> runner. `1.3` (the flake) and `1.4` (frontend gates absent from `ci.yml`) are local work and stay
> available. Do not spend runner minutes probing the cap.

| # | Item | Source |
|---|---|---|
| 1.1 | Confirm Unit + Integration actually pass on a runner. Causes were found and fixed (no Redis service; `tenants(api_key)` → `api_key_hash` per migration `0006`) but have **never executed remotely**. | HANDOFF |
| 1.2 | **`--cov-fail-under=80` has never run in CI history.** Local measurement is **80.86%** — a 0.86-point margin, and it *fell* from 81.17% because clearing F401 deleted covered import lines. Report the real number; do not lower the threshold. | ci-green log |
| 1.3 | **The 9% flake.** `test_services.py::TestWaitForNeonReady::test_wait_for_neon_ready_retries_then_succeeds` failed 1 in 11 identical runs. Diagnosis is written up: it patches the shared `time` module attribute while five Langfuse daemon threads run. Next step is a `--tb=long` loop to confirm which assertion fails; if it is `assert_called_once_with(1)`, the fix is `assert_any_call(1)`. | ci-green log |
| 1.4 | Frontend gates (`tsc`, `check:no-dusk-tokens`, `check:ops-room-wiring`, playwright) are **not in `ci.yml` at all**. | ci-green non-goals |
| 1.5 | `nightly.yml` E2E also failing, pre-existing, never diagnosed. | ci-green non-goals |

## 2. D1 — the measurement, and what still stands between it and a gate

**Was the headline.** Everything the eval-foundation branch built was scaffolding around a metric that
could not move. **P2 made it move** (`d127b4d`) and **P3 built the gate that reads it** (`5011f97`,
carried through to the approve route in `8b124d4`). What remains here is the consequences P2 and P3
themselves created.

> **Status 2026-08-08: P1 + P1b + P2 + P3 + P3's tier-2 review fixes.**
> `feat/d1-agent-invocation` carries the seam (`ec5f445`), its hardened guard (`d15be3a`), P1b
> (recorded mode + the canary write reorder, `487ebbe` + `117de05`), **P2 — the eval now invokes the
> agent** (`d127b4d`), **P3 — the deploy gate now refuses a run that does not record having invoked
> it** (`5011f97`) and **the P3 review fixes** (`8b124d4`, `9106412`), which carried the refusal
> through to `POST /approve-deployment`, stopped the owner-facing warning narrating a cause it did
> not observe, refused a run whose own terminal status is not `complete`, and extended the
> first-eval dispatch to the historical population. `2.1`, `2.2` and `2.3` are closed and their rows
> deleted per the maintenance rule. Traces: `.dev/traces/260808-d1-p2-invoke.md`,
> `260808-d1-p3-gate.md`, `260808-d1-p3-review-fixes.md`. Mutation proofs:
> `.dev/reference/p2-mutation-proofs.md`, `p2-review-mutation-proofs.md`,
> `p3-review-mutation-proofs.md`.
>
> **P3 needed no migration, and that is a finding rather than an omission.** The plan's P3 section
> assumed `agent_invoked` was a new column; P2 put it inside `eval_runs.config`, the JSONB that
> `alembic_tenant` 0013 already added, so there is no DDL to write. A dedicated column would create a
> second home for the one gate-facing claim — the disagreement `eval_service.invocation_provenance`'s
> docstring exists to prevent — and a backfill stamping `agent_invoked: false` onto history would
> change no gate outcome, because absent and false are refused identically. `0015` remains the tenant
> head.
>
> **The accepted cost, now live:** every eval run stored before this branch, and every tenant DB older
> than `alembic_tenant` 0013 (no `config` column at all), fails closed at the deploy gate until that
> DB is re-migrated and a fresh eval runs.
>
> **The metric has not been observed to move**, and cannot be on this machine: no end-to-end eval run
> is possible without `0.2`. P2 is unit-proven and unprovable end to end, exactly as the plan said.
>
> **THE TIER-2 JUDGE HAS NOW RUN, once, over the whole branch — 2026-08-08.** Verdict extracted to
> **`.dev/reference/tier2-judge-d1.md`** per the CLAUDE.md rule (it was also the judge's own
> must-fix #3). `mergeable: true`, with 3 must-fix items, 10 unproven claims, 10 evidence mismatches
> and 8 new backlog items — rows `2.20`–`2.27` below. Two of its three must-fix items are **owner
> decisions** and are filed at the top of this file as `0.4` and `0.5`, because a merge should not
> proceed past them.
>
> **Provenance, corrected a second time.** Every in-phase reviewer on this branch was **tier-1**: a
> session-model agent that investigated the codebase and asked "what is broken?". The P1 fix commit
> (`d15be3a`), the former rows `2.5`/`2.6`, and this block's own earlier text all credited "tier-2"
> for P1, P2 and P3 reviews. They were not. Tier-2 is a **Fable** judge reading a *bounded artifact*
> and asking "do the claims match the evidence?", and until 2026-08-08 it had read nothing here. The
> mislabel recurred across three separate phases, which makes it a process defect rather than a
> slip — see `2.17`.
>
> The judge's one-line read: *"a correctly-shaped, fail-closed measurement pipeline that has never
> measured anything — which is a large improvement over a pipeline that confidently measured its own
> label, and is honestly labelled as such."*

Numbering note: `2.1`, `2.2`, `2.3`, `2.5`, `2.6`, `2.7` and `2.13` are closed and their rows deleted
per the maintenance rule — `2.2` the deploy gate itself (2026-08-08, P3: `apply_signal_evidence_gate`
refuses `agent_invoked` false OR absent, `_fetch_eval_summary_sync` derives the fifth signal state
`agent_not_invoked` from `eval_runs.config` and suppresses the tautology's scores rather than letting
the orchestrator narrate them), `2.1` the tautology itself and `2.3` the config tuple stamped on it
(both
2026-08-08, P2: `agent_response` is the agent's own text, `retrieved_contexts` are the agent's own
retrieve result, and the eval serves the production prompt version the run is attributed to rather
than the agent's live soul columns), `2.5` recorded mode, `2.6` the canary write order, `2.7` the
escalation `conversation_id` decision. The numbers are **not reused** — `agent.py`,
`transactional/tools.py`, `eval.py` and the plan all cite "BACKLOG 2.1"/"2.5"/"2.6" for those
decisions, and a reader following one of those comments must not land on an unrelated row.
`2.13` (the truncated retrieve capture) closed 2026-08-08 in the P2-review pass: `agent.py` now
decodes the framed retrieve payload into one untruncated string per chunk on a second
`tool_calls_log` key that `_persist_messages` ignores, exactly as the row proposed, and the eval
scores that instead of the repr. `retrieved_context_at_cap` is now derived from the per-chunk cap
that can actually cut the evidence rather than from the audit capture, which five chunks exceed by
construction — it was ~100% on every retrieving turn and was a constant wearing an observation's
name.

| # | Item | Source |
|---|---|---|
| 2.4 | Mined scenarios are inert by construction — written with `reference_answer=''`, selected by `WHERE reference_answer != ''`. EVL-03 produces write-only data. **Narrowed by D6 P1 + P2:** the tier exists (`alembic_tenant` 0016, `label_service`) and the route pair exists (`GET .../eval-scenarios/unlabelled`, `POST .../{id}/label`), so the state is no longer terminal *in code*. It is still terminal *in fact*: **0016 has never been applied to any database**, so every label attempt on every tenant today returns 503, and no row has ever left the unlabelled state. Behind `0.2`. P3 (what a label does downstream) is unstarted. | audit D6 · narrowed D6 P2 |
| 2.8 | **Recorded mode does not bound the eval's Actor-gate spend.** Steps 1-5 of the transactional dispatcher run live by design — the envelope, IDV gate, rate ceiling and Actor seam are what the eval measures. The Actor gate is a synchronous Haiku call per mutating attempt, so a scenario set that provokes many attempts bills per attempt on top of the per-turn SDK call. **P2 bounds the TURNS (`AGENT_INVOCATION_MAX_CALLS_PER_RUN = 60`) but not the attempts within a turn**, so this stays open, narrower. | D1/P1b |
| 2.9 | **`red_team_probe._build_transactional_probe_fn` builds the CUSTOMER agent by hand, not through the seam.** `red_team_probe.py:313-329` constructs its own `ClaudeAgentOptions` with `_PROBE_MODEL` and `_ALLOWED_TOOLS`, so the RTX victim turn is an agent with a different model and a different tool list from the one production serves and the eval measures — RTX-01's confused-deputy findings are therefore about an adjacent agent. `MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS` grandfathers it; the allowlist comment now says why rather than implying it is an adversary. Route it through `build_agent_options(side_effects=...)`. | P1b tier-2 #12 |
| 2.10 | **Recorded rows are excluded from the decision eval, and the exclusion is a whole cell of its denominator.** `observed_disposition` classifies `side_effects.recorded:not_executed*` as `None` (`recorded_not_executed`), which is right — an eval's own rows must never become its evidence — but it means an agent driven mainly by the nightly eval contributes nothing to `valid`. **P2 has landed, so this is now checkable**: measure whether the decision eval's `valid` is dominated by production traffic or starved by eval traffic, and report `unknown` rather than a thin `pass`. | P1b tier-2 follow-on |
| 2.11 | **The eval scores the agent's raw text; production serves the PII-firewall deflection.** `run_agent_turn` runs `scan_response` before a customer sees a reply; the eval path deliberately does not, because a deflection is not an answer and scoring one would measure the firewall's hit rate as if it were the agent's grounding. Recorded on every run as `agent_invocation.pii_firewall_applied: false`. Decide whether firewall hits should be a counted, excluded category (the same shape as a failed turn) rather than scored as ordinary answers. | D1/P2 |
| 2.12 | **`AGENT_INVOCATION_MAX_CALLS_PER_RUN` (60) sits below `GOLDEN_SET_SOFT_CEILING` (200).** A tenant designating more than 60 golden rows gets the first 60 invoked and the remainder reported as `ceiling_skipped_golden` — the paired per-item delta the golden set exists for does not cover the tail that night, and *which* rows are covered is stable, so the tail is never measured at all. Reported loudly, not resolved: reconcile the two ceilings, or rotate which golden rows are covered when the set exceeds the call budget. | D1/P2 |
| 2.14 | **`update_eval_run_config`'s jsonb merge has never executed against a database.** `config = COALESCE(config,'{}'::jsonb) || %(patch)s::jsonb` is asserted at the call site against a cursor double. It is the write that turns `agent_invoked` into an observation, and P3's gate reads exactly what it writes. Behind `0.2`, same standing debt as `3.5`. | D1/P2 |
| 2.15 | **A responded turn with no retrieve call is dropped from scoring entirely, including AnswerRelevancy.** Faithfulness / ContextPrecision / ContextRecall over an empty context list are structurally 0 or NaN, so those rows are excluded and counted (`no_retrieval`) rather than scored 0 — but AnswerRelevancy is well defined without contexts and is lost with them. Ragas 0.4.x `evaluate()` runs one metric list over one dataset, so scoring them needs a second `evaluate()` call with a narrower metric list and per-row NULLs for the three context metrics. An agent that legitimately answers many questions from its soul is measured on fewer rows than it answered. | D1/P2 review |
| 2.16 | **`coverage_rate` is reported and nothing gates on it.** `response_rate` divides by what the per-run ceiling ALLOWED; `coverage_rate` divides by what the tenant designated, so a tenant with 200 labelled rows whose first 60 all answer reports response_rate 1.0 and coverage 0.3. Both travel on the run. `MIN_RESPONSE_RATE` is applied to the first only — deliberately, because gating on coverage would permanently block every tenant above the ceiling (see `2.12`, the same collision). **P3 has landed and did NOT take this on** — it gates `agent_invoked` only, so `coverage_rate` still travels on the run and still gates nothing. Stated rather than quietly carried forward. | D1/P2 review |
| 2.17 | **A below-floor run's surviving scores are discarded, not stored un-gated.** The interim fail-closed in `run_eval_suite` skips `run_ragas_eval` entirely when the invocation is below the floor, so the 2 real observations from a 2-of-40 run are never computed and never stored. It was the honest choice while the gate could not refuse them; **P3 has now landed, so the precondition is met** — a below-floor run records `agent_invoked=false` and the gate refuses it on that basis alone. Score the survivors and store them for debugging, and delete the `run_ragas_eval` skip. | D1/P2 review |
| 2.18 | **A pre-0013 tenant re-dispatches an eval on every readiness check.** The P3 review extended step 4b to fire for `agent_not_invoked` with `agent_invoked is None` — the historical population, which converges because a fresh run on a 0013+ tenant writes the key either way. A tenant DB with no `config` column cannot record it, so absence recurs there and every readiness check queues another `generate_eval_suite -> run_eval_suite` chain, bounded only by `run_eval_suite`'s in-flight idempotency window. Cheapest fix is `0.2` plus the migration roundtrip (`3.5`); the alternative is a "this tenant cannot record provenance" state the dispatch reads. | P3 tier-2 #3 |
| 2.20 | **Tier-1 review outputs are lost to a temp directory.** This branch lost **17 of 48 tier-1 findings** and 7 of 13 unsupported-claim entries — unadjudicatable, because the workflow journal does not survive the session. The CLAUDE.md persistence rule covers only the tier-2 judgement. Extend it to tier-1: every reviewer's output lands in `.dev/reference/` or a committed trace before the run ends. | D1 tier-2 must-fix #3 |
| 2.21 | **44 of 122 claimed mutation proofs have no surviving record** — P1 ×12, P1-fix ×12, P1b-fix ×18 — including all of P1, the phase the plan itself calls "the whole bet". Either re-run them or formally write them off in writing. Also harden the protocol: record each proof's selector and import order (the repo has a known fake-`@tool` import artifact producing 74 spurious failures in hand-picked subsets), and adopt P3-fix's sha256-both-sides restore as standard. Extends `3.9`. | D1 tier-2 backlog #2 |
| 2.22 | **The scorable floor can be thin.** A run with 60 responses but only 3 retrieving turns is `MEASURED` with **3 scored observations gating a deploy**. Coverage is reported; nothing bounds the scorable fraction. Decide whether a scorable-rate floor belongs alongside `MIN_SCORED_OBSERVATIONS`. Adjacent to `2.15`/`2.16`, not identical. | D1 tier-2 backlog #8 |
| 2.23 | **The admin console has no consumer for `agent_invoked`, `eval_signal` or the new `warning_id`.** The owner-facing account of a refused deploy exists only as API payload text. The four frontend gates were never run against this branch. | D1 tier-2 backlog #4 |
| 2.24 | **Pre-existing `sys.modules` pollution.** `test_agent_task.py` installs a fake `claude_agent_sdk` and never removes it, so class identity depends on collection order (observed directly by P1-fix). The passthrough `@tool` fake in `test_recorded_side_effects.py` / `test_retrieval_metrics.py` causes a 74-failure ordering artifact. | D1 tier-2 backlog #5 |
| 2.25 | **Lint claims depend on a network fetch.** `ruff` is not installed in `apps/api/.venv`; the branch's clean-lint claims came from `uvx ruff@latest`, which leaves no pinned artifact and cannot be reproduced offline. Install it into the venv or pin the version in the gate command. | D1 tier-2 backlog #6 |
| 2.26 | **Only P1 ran the ignored-new-files control.** Later phases proved their delta by def-test-count arithmetic, which cannot see a pre-existing test silently changing status. Adopt the control (run the gate with the new test file `--ignore`d and compare to baseline) as the standard per-phase proof. | D1 tier-2 backlog #7 |
| 2.27 | **`_build_transactional_probe_fn` egress** — see `2.9`; the judge raised the same seam bypass independently. Kept as one row there. | D1 tier-2 |
| 2.19 | **`checklist_runs` has no gate version, so a stored 'ship' outlives the rules that produced it.** `POST /approve-deployment` validates against a `recommendation` frozen at checklist time. The P3 review closed D1's slice by re-reading `report.eval_summary.agent_invoked` at approve time, and `5.1` is the same hole on the red-team half. The general form: stamp a gate-version integer on the run at write time and 422 any run below the current version. Needs a control-DB migration, so it is behind `0.2`. Until then each new gate condition has to remember to add its own approve-time re-read, which is exactly the "a floor every consumer must remember to reapply is a floor nobody has" failure. | P3 tier-2 #1 |

## 3. Verification debt from the eval branch

| # | Item | Source |
|---|---|---|
| 3.1 | **Pre-P4 red-team runs remain shippable evidence.** A run stored while 5 of 7 attackers had no tools still reads `signal='measured'` with clean findings; unrecorded coverage only warns and substitutes the current build's 7/7. Fence or invalidate them. | tier-2 #7 |
| 3.2 | **`write_eval_results`' column names are pinned by no test.** Tier-1 rewrote the INSERT to the D3 names and the whole suite stayed green — on a branch whose D3 *was* a column-name mistake. | tier-2 #2 |
| 3.3 | The `human_scores.csv` write-ban misses `with open(path, 'w')`, and its guard was demonstrated only inside the complement of its own blind spot. | tier-2 #8 |
| 3.4 | `test_eval_e2e.py` exercises `run_eval_for_agent`, which has **zero production callers**. Passing e2e asserting the wrong surface. | tier-2 #3 |
| 3.5 | Migrations `0013`/`0014`/`0015` verified by source-text assertions only. **No `ALTER TABLE` on this branch has ever executed anywhere.** Run the roundtrips before the next tenant is provisioned. | tier-2 #4 |
| 3.6 | The decision eval (P3, 1865 lines) has **scored zero real audit rows** — no driver exists, every run reports `valid=0`. It is a scorer, not yet an eval. | tier-2 #10 |
| 3.7 | SDK attacker wiring is proven only against a fake harness that structurally cannot detect the wiring being removed. Nothing pins the three-way name agreement (`create_sdk_mcp_server` ↔ `mcp_servers` key ↔ `mcp__{name}__` prefix). | tier-2 #5 |
| 3.8 | Three deterministic red-team vectors still `except Exception: return []` — clean over an unobserved run — while coverage asserts all seven valid. Also `identity_bypass` vs `identity_verification_bypass` vocabulary split breaks the coverage↔findings join. | tier-2 #6 |
| 3.9 | 20 of the 72 guard demonstrations (P2, P3) are implementer self-reports; tier-1 reproduced none of them. Spot-reproduce 2–3, highest value first: the D3 column-name revert. | tier-2 #11 |
| 3.10 | `_run_orchestrator_loop` reports "was never awaited" — `run_orchestrator` is never executed anywhere, so every claim about the prompt's prose blocking conditions is untested. Four phases and tier-1 read past it. | tier-2 #16 · retro A.5 |
| 3.11 | Remaining tier-2 items not itemised here: **#12–#15, #17** and evidence mismatches **#1–#8**. Full text in `.dev/reference/tier2-judge-eval-foundation.md`. | — |

## 4. Test-suite integrity

| # | Item | Source |
|---|---|---|
| 4.1 | **5 `patch()` sites name symbols that do not exist** — 4× `HybridChunker` in `test_ingestion_chain.py` (580/720/865/955), 1× `get_adapter` in `test_actor_latency.py:221`. Pinned in `test_patch_targets_resolve.py::_KNOWN_BROKEN`; re-measure with that module's `__main__`. | ci-green log |
| 4.2 | `test_integration_e2e.py` has **zero T-16-01 credential-leak coverage** — the block built two strings, looped six patterns, and had `pass` as the body. Dead code removed; the real assertion still needs writing. | ci-green log |
| 4.3 | `test_parse_task.py`'s `mock_parse.assert_not_called()` is vacuously true — `parse.py` only ever calls `parse_document_from_bytes`. | ci-green log |
| 4.4 | The 10 docling-gated tests have **never run in repo history**; no job installs the `pipeline` extra. | ci-green log |
| 4.5 | `pyproject.toml:42` declares `PyJWT[cryptography]==2.12.1`; no such extra exists, so uv warns and it is silently ignored. | ci-green log |
| 4.6 | **An agent ContextVar leaks across the whole pytest process.** `agent_tools.build_tool_server()` sets `_agent_id_var` and never clears it (correctly — it is setting up a turn), and `tests/unit/test_agent_tools.py:686` calls it with `agent_id='agent-reset-test'`, so that value is live for **every subsequent test in the process**. `test_label_provenance.py` needed an autouse fixture to establish its own precondition; any future test reading that var gets a stale agent id, and `agent_tools.py:152` claims ContextVars exist to prevent exactly this bleed. Fix: run it inside `contextvars.copy_context()`, or reset what the test sets. **It has now bitten a second module and cost a second identical fixture — D6 P2, 2026-08-09.** `test_eval_label_queue.py` passed 55/55 in isolation and **failed 11** in the full suite; the label route's R4 guard was correctly reporting `agent_id='agent-reset-test'` and returning 500. Every module that touches the human-label path will pay this until it is fixed at the source. The leak is fail-CLOSED (it refuses more, never less), which is why it surfaced as 500s rather than as forged labels. | D6 P1 §8.4 · review #8 · D6 P2 |
| 4.7 | **`labelled_by` names an ACCOUNT, not a person, and `human_verified` still has no writer.** *Narrowed by D6 P2, which closed the pin this row was filed for:* `ScenarioLabelRequest` forbids extra fields and carries exactly one, so a caller can name neither the author nor the tier, and `_label_principal()` derives `labelled_by` from the authenticated principal — pinned by `test_the_body_may_not_name_the_author`, `test_no_other_provenance_field_may_be_submitted_either` and `test_the_author_is_derived_from_the_authenticated_principal`. What remains is that `get_current_tenant` resolves to a **Tenant**, and does not report which of its two credential paths ran, so `human_authored` is stamped `tenant:<uuid>` — true, and weaker than the column implies. Narrowing it to a person needs a principal-aware dependency in `app/api/deps.py`. Separately, a machine-drafted candidate a human approves is `human_verified`, which still has no writer; 0016's CHECK admits the value so adding one is code, not a migration. | D6 P1 review #3 · narrowed D6 P2 |
| 4.9 | **The labelling queue's ordering has never been executed by a database, and one of its keys is a Postgres-only construct.** `array_position(%(source_priority)s::text[], source) ASC NULLS LAST` is asserted at the SQL-string level and against a recording cursor; the row order Postgres would actually produce has not been observed. There is also **no index** supporting `WHERE NOT (reference_answer != '')` with that ORDER BY — fine while the table is small, a sequential scan plus a sort once it is not. Measure the plan and decide on an index once `0.2` lands. | D6 P2 |
| 4.10 | **Nothing measures what mining actually yields.** The plan's stated next move after P1–P3 is "stop and measure what mining actually yields before any console work", and the queue route is now the instrument for it — `counts.unlabelled` over `counts.total`. `mine_production_scenarios` `continue`s past every job whose `jobs.conversation_id` is absent, and its own docstring admits the emit payload carries neither `conversation_id` nor `question`, so **zero is a plausible reading** and would be a finding about the miner rather than about the queue. Cannot be run here: behind `0.2`. P4 (the console queue) stays unstarted until this number exists. | D6 plan · D6 P2 |
| 4.8 | **R3's residual static blind spot, stated so it is not rediscovered as a surprise.** The two scans (composed-SQL reconstruction, name-level absence pin) see every forgery shape the review probed. Neither sees an identifier assembled from fragments — `"label" + "_trust_tier"` — inside `eval_service.py`, which the name pin must allowlist because it declares the column name. No static check of this shape can. It is why R4 is the last line, and it is the reason the wall's claim is "no shape anyone has devised passes unnoticed" rather than "forgery is impossible". Closing it needs a runtime guard on the write, not a stronger scan. | D6 P1 review #1 |

## 5. Milestone v1.2 closure

| # | Item | Source |
|---|---|---|
| 5.1 | **OPS-15 server gap.** `POST /approve-deployment` gates on the frozen `run.recommendation`, never live `open_findings`. Console can no longer deploy over a critical finding; any script still can. Fails closed, so not a hole — but the milestone audit calls it a blocker. **The eval sibling of this was closed 2026-08-08** (`8b124d4`: the route re-reads `report.eval_summary.agent_invoked` rather than trusting the frozen verdict); this row is the red-team half, and `2.19` is the general form that would close both. | v1.2 audit |
| 5.2 | `REQUIREMENTS.md`: `WIRE-01..05` has **zero rows**; the v1.2 rollup sentence is stale; the `OPS-01..06` collision is live (lines 274-279 hold them Phase 10 `Pending` while line 415 ticks them complete via Phase 21). | v1.2 audit |
| 5.3 | Nyquist `status: draft` on `20/21/23-VALIDATION.md`; Phase 20 has no `20-SECURITY.md`. | v1.2 audit |
| 5.4 | Console renders unknown as `0 critical · 0 high` with a **Pass chip** (`deploy/page.tsx:2428`). Family B closed in the gate, alive at the surface. | trace · tier-2 #5 mismatch |
| 5.5 | `eval/page.tsx` unguarded `res.scores.faithfulness` — 5 call sites, grep-confirmed. | Phase 23 |

## 6. The ladder beyond D1 — not yet planned anywhere

From the data-science framing. Each depends on everything above it.

| # | Item |
|---|---|
| 6.1 | **Golden-set refresh policy.** A fixed set gets overfit; the rotating exploratory set and a promotion/retirement rule are what stop that. |
| 6.2 | **Per-tenant baselines.** Absolute thresholds (0.70/0.85) are the enemy of autonomy — 0.85 means different things on a 40-page and a 4000-page corpus. Needs trailing median + variance over persisted runs, and a **cold-start policy** for the tenant with no history, which is every new signup. |
| 6.3 | **Regression detection over threshold checking** — "2σ below this tenant's own trailing baseline on the same items" survives across tenants; "below 0.85" does not. |
| 6.4 | **Label efficiency — and it costs a schema change, not an `ORDER BY`.** *Re-scoped by D6 P2, which tried to build it and found the signal unjoinable.* Judge `confidence` is emitted onto `job_events`, and `validators.py` calls `emit()` with the session from `get_sync_db()` — so it is a **control-DB** row, while `eval_scenarios` is in the tenant's own Neon project. No SQL join spans them, and application-side correlation has no key either: `store_scenarios` inserts no `job_id`, no `conversation_id` and not even `origin_trace_id`, and `mine_production_scenarios` selects `job_id` + `verdict` only, discarding `payload->>'confidence'` at the point it reads the event. The one tenant-side confidence column, `verified_qa_candidates.auditor_confidence` (0004), is written by `run_auditor` only for **grounded** turns above threshold — the complement of the fail/ungrounded/partial turns the queue is built from. So the work is: carry a key (and the confidence) onto the scenario row at mining time, migrate for it, and accept that it is **retroactively empty for every row already mined**. The P2 queue is ordered by origin trust tier then oldest-first and says `by_uncertainty: false` in its own payload so nothing downstream mistakes it for this. |
| 6.5 | **Wire `message_feedback` into the dataset.** Shipped Phase 23, feeds a CSAT tile and nothing else — the only *direct customer label* in the system. |
| 6.6 | **Automated prompt evolution (SkillOpt).** Two constraints first: the optimizer may never modify a capability envelope (structurally, not by instruction), and the fitness function must include refusal correctness or it learns to refuse less. Pointing it at today's scores optimizes a tautology. |

---

## Suggested order

**Revised 2026-08-07.** The earlier order said `0.1` and `0.2` were cheap and could run in parallel
with anything. `0.1` is not parallel to `0.2` — it is **behind** it, because the responses a human
would score cannot be captured without a database. Corrected above.

`0.2` (a local PostgreSQL) is now the single highest-leverage owner action: it unblocks `0.1`, the
three migration roundtrips, `VER-01`/`AUD-03`/`CAP-03`, the 6 UAT checkpoints, the 4 `human_needed`
items — and the end-to-end proof of §2. **§1 is paused** on the billing cap, which is not a code
problem. So the working order is **§2 (D1)** — unblocked for implementation and unit proof, blocked
only for its end-to-end observation — then §3's verification debt, much of which dissolves once the
metric moves, then §5 to close the milestone. §6 is the product ambition and is gated on all of it.
