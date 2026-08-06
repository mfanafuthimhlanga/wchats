# Eval foundation — make the measurement layer mean something

**Branch:** `feat/eval-foundation` off `main` (@ `d72c519`) · **Law:**
`.dev/reference/measurement-layer-audit.md` · **Engine:** Workflow
`.dev/workflows/eval-foundation.workflow.js` — 4 sequential gated phases, each `impl → adversarial
review → bounded fix`, then the tier-2 judge.

**Scope:** `apps/api` only. No frontend. No new migration on the tenant tree beyond the two additive
columns P1 names. No new dependency.

---

## Why

The audit found seven defects that together mean: **the eval measures nothing, its results are
deleted, and the deploy gate that reads them fails open.** Every downstream ambition — drift
detection, per-tenant baselines, automated prompt evolution — is a function of this layer being
trustworthy. Optimizing against today's scores would optimize a tautology.

The dependency order is forced: **persistence → versioning → baselines → self-evolution.** This plan
is the first two links plus the transactional dimension, and it prepares (but cannot supply) the
owner-labelling step that gates trust in every automated judge.

## The ordering constraint that governs this whole plan

**D2 masks D5.** Eval results and `verified_qa` promotions are written to a Neon branch that is
deleted in `finally`, so the label inversion at `bench.py:147` is currently inert. `verified_qa` rows
are served to real customers by `retrieval_service.py:98` *before* hybrid search at 0.93 similarity.

**Fixing the write-back before or without the label fix ships a path that serves a human-flagged
failure to customers.** P1 therefore fixes the inversion and the persistence split in the same phase,
and P1's review is instructed to treat any promotion path reachable without a trust-tier check as a
blocker.

## Phases

### P1 — Persistence, the configuration tuple, and the label fix

**The split (the central decision).** The Neon branch exists so an eval never mutates tenant data
(D-10). Eval *results* are observations about a run, not tenant data. So:

- **Scoring** runs against the branch — unchanged.
- **`eval_runs` status + `eval_results` rows** are written to **production** (`conn_str`). A run must
  end in a terminal state on production or it never happened.
- **`verified_qa` promotion is disabled in this phase.** Not moved to production — *disabled*, behind
  an explicit trust-tier check that no `source='generated'` or `source='production'` row can pass
  today. Re-enabling it is a later decision with the label hierarchy behind it, not a side effect of
  fixing persistence. Record the disablement as a decision in the trace, with the reason.

**The tuple.** Additive migration on the tenant tree (next revision after `0012`): `eval_runs` gains
nullable `prompt_version_id UUID`, `config JSONB`. `config` carries at minimum `model_id`,
`retrieval_config_hash`, `envelope_hash`, `corpus_chunk_count`, `embedding_provider`. Follow
`0009:86`'s precedent (`turn_metrics.prompt_version_id`, nullable, additive). Populate at run start
from the same sources `deployment_service` already reads.

**The label fix.** `bench.py:147` must not store the agent's failing answer as `reference_answer`. A
filed failing trace has a question and a known-bad answer and **no ground truth** — store it honestly:
`reference_answer=''` (matching the `mined` convention, which makes it inert to the eval selector by
construction) plus the failing answer preserved in a non-label field. If that makes filing pointless
until a correction UI exists, say so in the trace rather than inventing a label.

**Tests:** a run's results are readable from production after the branch is deleted; a run reaches a
terminal status on production; branch deletion still happens on every path (mutate the `finally`,
observe red); promotion is unreachable for a model-generated row; a filed trace never produces a row
whose `reference_answer` equals the agent turn.

### P2 — Fix the gate, split the golden set, add validity denominators

**D3.** `deployment_service.py:201-202` → `metric`, `eval_run_id`. Add the same inner `try/except`
shape `_fetch_verified_qa_stats_sync` already uses, but returning a **distinguishable** value:
missing data is `None`/`unknown`, never `{}` that reads as "no failures".

**Fail-closed direction.** The orchestrator prompt's blocking conditions
(`deployment_service.py:76-89`) must treat "eval signal unavailable" as **not shippable**, not as
silently fine. `recommendation='block'` with a stated reason, or a new explicit
`insufficient_evidence` state — the implementer picks and records the choice; what is forbidden is
`ship` over an absent eval signal.

**Golden set.** `eval_scenarios` gains a nullable `dataset` column (`'golden' | 'exploratory'`).
`eval.py:177`'s `ORDER BY RANDOM() LIMIT 30` becomes: **all** golden rows, every run, unsampled, plus
a rotating exploratory sample. Report the two separately — a golden-set score and an exploratory
score are different measurements and averaging them destroys the paired comparison that makes the
golden set worth having.

**Validity denominators.** Every eval run and every red-team run reports `(attempted, valid,
findings/scored)`. `valid` is the denominator. Wire it through the API responses that the ops room
already reads. A rate without its denominator must not be constructible.

**Tests:** the gate blocks (or reports insufficient evidence) when the eval fetch fails — mutate the
column name back, observe the gate refuse; golden rows appear in every run over repeated invocations;
a run with zero valid scenarios reports `unknown`, never a pass rate.

### P3 — The transactional eval dimension

Nothing in the eval path references any transactional concept (grep-confirmed). RAG metrics cannot
express the transactional questions, which are decisions, not generations.

Build a **decision eval** over `tool_calls_audit` (`agent_id, skill, arguments, result,
actor_decision, actor_rationale, capability_snapshot, latency_ms, error, created_at`):

- A labelled fixture set of `(envelope, request) → expected_disposition` where disposition is
  `execute | refuse | require_human`. Derive the cases from `CLEAN_TENANT_ENVELOPES` and the six
  mutating skills so the fixture cannot drift from the shipped envelope shape.
- Score as a **confusion matrix**, reporting FP and FN separately and never averaging them into one
  number. FP (executed when it should have refused) is the critical error. FN (refused when it should
  have executed) is the one that drives owners to loosen envelopes and is currently unmeasured.
- Read-only against `tool_calls_audit` — this phase adds no column to it.

**Tests:** the matrix is computed from real audit rows, not mocked verdicts; FP and FN are separately
addressable; a fixture whose envelope no longer matches the shipped set fails loudly rather than
scoring against a stale assumption.

### P4 — Calibration inputs, and D4's dead attackers

**Calibration.** `tests/evals/calibration/human_scores.csv` has 10 rows and no scores;
`tests/evals/responses/` was never captured. Run `capture_responses.py`, populate every row the
harness needs, and leave `human_score` **empty** — that column is the owner's and an agent must never
fill it. Make `compute_correlation.py` distinguish "not calibrated yet" from "calibrated and passing"
in its exit semantics; today an unscored file exits 0 as "informational", which reads as success.

**D4.** Register `_TOOL_SEND_PROBE` / `_TOOL_REPORT_FINDING` with the four SDK attacker loops so the
attackers can actually probe, and **feed `probe_fn`'s return value back as the tool result** —
`:304`'s discarded return is the second half of the defect. Then delete the `asyncio.run` patches
from `test_red_team_service.py:165/195/221` and replace them with tests that exercise
`_run_agent_loop` against a fake client, so the region is covered rather than mocked away.

If the SDK's tool-registration surface makes this materially larger than a bounded fix, **stop and
report** rather than expanding scope — a partial fix that leaves the attackers silent is worse than
a recorded blocker, because it looks fixed.

**Tests:** an attacker loop with a stub client produces a finding end-to-end; the probe response
reaches the attacker; a run with zero valid probes reports `valid=0` and is not rendered as clean.

## Non-goals

- OPS-15's server-side `open_findings` check — real, blocking the milestone, and a separate plan.
- The `REQUIREMENTS.md` traceability corrections — separate, docs-only.
- Re-enabling `verified_qa` promotion (P1 disables it deliberately).
- Per-tenant baselines, drift detection, prompt optimization — all downstream of this plan.
- Any live-database proof. No PostgreSQL on this machine; `-m integration` harnesses skip, and a skip
  is unobserved, never a pass.

## Risks

- **P1's migration is the first tenant migration since `0012`.** It cannot be verified against a live
  DB here. Keep it strictly additive and nullable; a rollback must be a no-op.
- **Disabling promotion changes deploy-checklist behaviour** — `verified_qa_stats.row_count < 50` is a
  warning condition (`deployment_service.py:82`). Expect the checklist to warn more; that is honest,
  not a regression, but state it.
- **P4 may be unbounded.** The SDK tool-registration surface is unknown until read. The phase is
  written to permit an honest stop.
- **The baseline is unverified.** 1199/8 was recorded from an executor's output, not re-observed.
  Establish the real number before claiming any delta.
