# Trace — eval foundation (2026-08-05 → 06)

**Plan:** `.dev/plans/260805-eval-foundation.md` · **Audit:** `.dev/reference/measurement-layer-audit.md` ·
**Branch:** `feat/eval-foundation` off `chore/dev-workflow-convention` (`fd8fa20`) ·
**Engine:** `.dev/workflows/eval-foundation.workflow.js`, run `wf_b3321f17-511` — 15 agents, 4.1M
subagent tokens, 11h32m, 1 agent lost to a transient `ENOTFOUND` and relaunched.

**Commits:** `c9d3ec4` + `14fc83f` (P1) · `f144834` + `f824ab1` (P2) · `d3d79ca` + `e1f8729` (P3) ·
`7339ff6` + `60a4a5d` (P4)

**Gate:** 1199 → **1657 passed, 11 skipped, 0 failed**. Baseline observed at `fd8fa20` before the run
(1199/8/0, 202s), so every delta below is measured, not inherited. 11 skips are `-m integration`
harnesses — no PostgreSQL on this machine, **unobserved, never a pass**.

**Migrations:** `0013` (eval_runs config tuple), `0014`, `0015` (red-team coverage). All additive,
nullable, no-op rollback. **None has been executed anywhere** — every `_db_roundtrip` test skips.

---

## What changed

- **P1 — persistence split, config tuple, label fix.** `eval_runs` insert, terminal status and all
  `eval_results` now write to production; scoring keeps the branch. `promote_to_verified_qa` disabled
  behind a **trust-tier rank comparison**, unreachable for all four sources the `0011` CHECK allows —
  and the unreachability tests parse that source list out of the migration, so a new source without a
  tier fails the gate. The promotion machinery stays alive and tested against a hypothetical
  human-authored source, so "nothing promoted" stays distinguishable from "path is dead". Migration
  `0013` stamps `prompt_version_id` + `config`. `bench.py` stores `reference_answer=''` for a filed
  trace instead of the agent's failing answer.
- **P2 — gate repair, golden set, denominators.** `deployment_service.py:201`'s `metric_name`/`run_id`
  corrected to `metric`/`eval_run_id`; absent eval signal now **blocks** rather than substituting an
  empty dict. `eval_scenarios.dataset` splits golden (every run, unsampled) from exploratory
  (rotating). Runs report `(attempted, valid, scored)`. Red-team coverage persisted (`0015`).
- **P3 — transactional decision eval.** A scorer over `tool_calls_audit` producing a confusion matrix
  with FP and FN separately addressable, fixtures derived from `CLEAN_TENANT_ENVELOPES` so they cannot
  drift from the shipped envelope set. Read-only.
- **P4 — calibration semantics + D4.** SDK attackers given their tools via `create_sdk_mcp_server`;
  `compute_correlation.py` now distinguishes "not calibrated yet" from "calibrated and passing".
  `human_scores.csv` left untouched by design.

## Review outcome

Every phase came back `fix_required`. **24 blockers** raised, all fixed; **~90 non-blocking concerns**
forwarded between phases. **72 guard demonstrations** claimed across 8 commits; tier-1 independently
reproduced 6 (4 of P1's, 2 of P4's) and reports doing so for neither P2 nor P3.

One implementer caught its own tautology mid-demonstration: the narrow-`except` guard on
`insert_eval_run` **stayed green** under mutation because the test injected failure on both the wide
and narrow INSERT, so the fallback re-raised and the test passed for the wrong reason. It rewrote the
test and re-ran the mutation to a real red. That is the discipline working on itself.

## What the tier-2 judge found (Fable, bounded artifact)

Verdict, verbatim in part: *"an honest and well-guarded instrument-building milestone, and mergeable
as such — but do not read it as 'the platform is now evaluated': nothing on this branch has yet
measured a real agent, and the one live signal the gate consumes is still vacuous."*

**17 claimed-but-unproven, 8 evidence mismatches.** The load-bearing ones:

1. **D1 is still shipped, and the branch made it look credible.** `eval.py` still sets
   `agent_response = row[3]`. The gate now fail-closes on an *absent* eval signal while shipping on a
   *present* one that measures nothing about the agent, and no gate test reads `config.agent_invoked`.
   **This is a defect in the plan, not the execution** — the audit named target leakage as defect #1
   and the four phases assign it to nobody. P1's reviewer flagged it; P1's implementer correctly
   refused to expand scope into it.
2. **Pre-P4 red-team runs remain shippable evidence.** A run stored while five of seven attackers had
   no tools still reads `signal='measured'` with clean findings; unrecorded coverage only warns and is
   substituted with the *current* build's now-7/7 capability. Nothing fences the fake-clean era.
3. **`write_eval_results`' column names are pinned by no test.** Tier-1 rewrote the INSERT to the D3
   names and the whole suite stayed green — the single most on-theme unpinned surface on a branch
   whose D3 was exactly a column-name mistake.
4. **The `human_scores.csv` write-ban has a blind spot.** The substring list misses
   `with open(HUMAN_SCORES_CSV, 'w')`, and GUARD 8's red demonstration used `csv.DictWriter` — a form
   that *is* on the list. The guard was demonstrated only inside the complement of its own blind spot.
5. **`test_eval_e2e.py` asserts the wrong surface.** It exercises `run_eval_for_agent`, which has zero
   production callers — a duplicate of the sequence nothing runs, offered as end-to-end evidence.

## Decisions (beyond plan)

- **P1 fix removed `branch_conn_str` from the scoring path entirely.** The reviewer found the branch
  was created, probed and deleted with *no statement ever executed against it* — isolation nobody
  used. Now behind `EVAL_SCORING_REQUIRES_BRANCH` (False), both positions tested, and branch
  acquisition moved inside the `try/finally` so a failed readiness probe no longer strands a branch.
- **Unmeasured metrics are not emitted as `null` at the API render layer.** `eval/page.tsx` types
  those fields `number` and calls `.toFixed(2)`; since D2 meant `eval_results` never existed on
  production, emitting null would throw on page load for every tenant. The honest signal moved into
  new fields (`metrics.{value,measured}`, `scored_scenario_count`, tri-state `passed`); the coerced
  fields are documented as compatibility projections and the console migration is **owed**.
- **P2 declined to wire eval into the ingestion chain.** `generate_eval_suite` is dispatched by
  **nothing** in `app/` — verified. Rather than add a Haiku scenario generation plus a full Ragas run
  to every document upload inside a review fix, the checklist now starts the first eval when it finds
  `no_runs`, once per agent.
- **P3 built the scorer, not a driver.** A driver needs capability envelopes seeded into the control
  DB (a write, outside a read-only phase), the real dispatcher and Redis. **No real audit row has ever
  been scored** — every run today reports `valid=0 / signal='no_observations'`.
- **P4 did not run `capture_responses.py`.** It needs a live ingested agent behind a running FastAPI.
  Hand-writing plausible response JSON would produce a Spearman number over invented agent text —
  worse than no calibration, because it would read as a completed measurement. Built an offline
  `--check` readiness reporter instead.
- **One blocker rejected as a false positive with evidence** (`60a4a5d`): a `description` column on
  `red_team_findings` was unnecessary because `_correlate_description` already recovers it from the
  run's findings JSONB; executed against a real invalid-observation finding, the console read returned
  the full text. A test pins the round trip instead of a migration adding a column for a problem that
  does not exist.

## Owed

- **D1 needs its own phase.** Until an eval actually invokes the agent, the gate's one live signal is
  vacuous and the config tuple certifies dimensions with no influence on the score.
- Fence or invalidate pre-P4 red-team runs.
- Pin `write_eval_results`' column list against the migration source.
- Widen the `human_scores.csv` write-ban and re-run GUARD 8 in its blind spot.
- Console still renders unknown as `0 critical · 0 high` with a Pass chip (`deploy/page.tsx:2428`) —
  Family B closed in the gate, alive at the surface.
- Run the three migration roundtrips against any disposable Postgres before the next tenant is
  provisioned.
- Spot-reproduce two or three P2/P3 guard mutations (the D3 column-name revert is highest value).
