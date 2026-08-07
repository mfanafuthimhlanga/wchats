# HANDOFF — 2026-08-06

> **`.dev/BACKLOG.md` is the single ordered list of open work.** Read it before starting anything.
> This file is the current-state snapshot; that one is the queue.

**In flight (2026-08-08): `feat/d1-agent-invocation`, unmerged.** P1 (the options seam, `ec5f445` +
`d15be3a`), P1b (recorded mode + the canary write order, `487ebbe` + `117de05`) and **P2 — the eval
invokes the agent** (`d127b4d`). `eval.py` no longer sets `agent_response = reference_answer`: each
scenario's question goes to the customer agent through the seam with `side_effects="recorded"`, the
agent's own text and its own retrieved contexts are what get scored, failed scenarios are excluded and
counted, a run below `MIN_RESPONSE_RATE` reports `unknown`, and `config.agent_invoked` is written as an
observation. **P3 has not started** — nothing reads `agent_invoked` yet, so the deploy gate still ships
on a present-but-meaningless eval signal (BACKLOG 2.2).

Branch suite: **1795 passed / 11 skipped / 0 failed**, measured against a clean stashed baseline of
**1766 / 11 / 0** at `1d3a7bd`. (`main`'s 1675/11/0 below is the pre-branch number and is not the
figure to measure a P2 delta against.) `mypy app` clean; `ruff check app tests` clean via
`uvx ruff@latest` — ruff is **not** installed in `apps/api/.venv`.

Trace: `.dev/traces/260808-d1-p2-invoke.md`. Mutation proofs: `.dev/reference/p2-mutation-proofs.md`
(19 mutations, red then green; **one guard was found proving a comment** and was rewritten — see the
M13 entry). Earlier: `.dev/traces/260807-d1-p1b-recorded-mode.md`,
`.dev/reference/p1b-mutation-proofs.md`. **No tier-2 judge has read this branch.**

**Unprovable here, and stated as such:** no end-to-end eval run, no live SDK turn, and
`update_eval_run_config`'s jsonb merge has never executed against a database. All behind BACKLOG `0.2`.

**All three PRs are merged. `main` is at `fd47133`** — the `.dev` convention (#1), the eval foundation
(#2) and the CI repair (#3). Suite 1199 → **1675 passed / 11 skipped / 0 failed**; ruff 461 → 0;
mypy 75 → 0. Trace: `.dev/traces/260805-eval-foundation.md`.

**Merge gotcha worth remembering:** the three stacked PRs were merged in one chained `&&` command.
All three reported MERGED, but only #1 reached `main` — #2 and #3 merged into their *original base
branches*, because GitHub had not retargeted them yet. `&&` waits for the CLI call to return, not for
the retarget. Closed by merging `origin/feat/eval-foundation` (which by then held all three) into
`main`. **Next time: merge only the top of the stack, once everything below it has landed there.**

## CI is red for an environmental reason, not a code one

Two consecutive runs died with every job `cancelled` at **15m03s** and **15m02s** — including Lint,
which takes 11 seconds. That is a hard wall-clock cap at the account/runner level, not a workflow or
code fault. Check Actions minutes and the spending limit at `github.com/settings/billing`; until it
lifts, CI cannot report anything and the gate is unreadable again.

**Settled by the remote gate** (run on `a4a03fb`, before the cap bit): Lint (ruff) **pass**,
Type-check (mypy) **pass**. Those two carried the 536 violations.

**Never yet executed on a runner:** Unit and Integration. Both had real, now-fixed causes — the unit
job had no Redis service (`test_agent_task.py` drives the Celery result backend against a real
client), and `conftest.py` inserted into `tenants(api_key)`, a column migration `0006` renamed to
`api_key_hash`. `-x` was also dropped: it halted the unit run at the first failure and reported
"1 failed / 76 passed" while hiding ~1600 tests.

**`--cov-fail-under=80` has still never executed in this project's history.** Real coverage is
unknown. If it lands below 80 the check fails for a true reason — report it, do not lower it.

**The tier-2 verdict stands as the honest read of what merged:** *"an honest and well-guarded
instrument-building milestone, mergeable as such — but do not read it as 'the platform is now
evaluated': nothing on this branch has yet measured a real agent, and the one live signal the gate
consumes is still vacuous."*

**The next phase is already named: D1.** The eval still sets `agent_response = reference_answer`, so
the deploy gate now fail-closes on an *absent* signal while shipping on a *present* one that measures
nothing. The config tuple stamps provenance on that tautology, which makes it look credible. This was
a gap in the plan, not the execution — the audit named target leakage as defect #1 and the four
phases assigned it to nobody.

---

## Where the product actually is

**Milestone v1.2 (Gotham console + agent management) is NOT closeable.** Phases 20, 21, 23 all
executed; the 2026-08-04 audit (`.planning/v1.2-MILESTONE-AUDIT.md`) returned `gaps_found` — 27/29
requirements satisfied, 4/5 integration flows wired. Phase 23 closed the ops-room wiring seam
(11/11 gate) and `3701b05` closed the console half of the deploy-gate contradiction.

**Two things block the milestone:**

1. **OPS-15 — server-side gap.** `POST /approve-deployment` (`deployment.py:348+`) gates on the
   frozen `run.recommendation` and never consults live `open_findings`. A critical finding raised
   after a clean checklist run is still accepted **by the API**; the console can no longer be used to
   do it, but any script or curl can. Fails closed, so not a security hole. Backend change with its
   own threat surface — needs a plan.
2. **`REQUIREMENTS.md` traceability.** `WIRE-01..05` has **zero rows** (grep-confirmed). The v1.2
   rollup sentence is stale. The `OPS-01..06` collision is live: lines 274-279 hold them as Phase 10
   / M10 `Pending`, while line 415's `OPS-01..16 | Phase 21 | ✓ Complete` falsely ticks the weekly
   cron, digest email and alerting that Phase 21 never built.

**Open but not blocking:** Nyquist `status: draft` on `20/21/23-VALIDATION.md`; Phase 20 has no
`20-SECURITY.md`; the `eval/page.tsx` unguarded-read twin (`res.scores.faithfulness`, 5 call sites).

## The live-gate backlog — one root cause

`VER-01`, `AUD-03`, `CAP-03`, the 6 blocked UAT items in `23-UAT.md`, and the 4 `human_needed` items
in `23-VERIFICATION.md` all share one precondition: **there is no PostgreSQL server on this
machine.** Stale `postgresql-x64-17` service pointing at a deleted binary; nothing on 5432-5435.
`CONTROL_DB_URL` is live Neon production and is never a substitute. No v1.2 migration has been
applied to a live Neon DB.

Installing a local PostgreSQL clears all of it with no planning or code work.

## What this session found (the reason the plan exists)

Full detail in `.dev/reference/measurement-layer-audit.md`. Seven defects in the measurement layer:

- **D1** the RAG eval never invokes the agent — the label is used as the prediction (`eval.py:200`)
- **D2** every eval result is written to a Neon branch that is deleted in `finally` (`eval.py:281-313`)
- **D3** the deploy gate's eval query uses columns that do not exist → fails **open**
  (`deployment_service.py:201`)
- **D4** 5 of 7 red-team attackers were never given their tools → report clean
  (`red_team_service.py:179/197` defined, never referenced)
- **D5** filing a *failing* trace stores its answer as ground truth (`bench.py:147`)
- **D6** mined scenarios are inert by construction (`reference_answer=''` vs `WHERE != ''`)
- **D7** every LLM judge is uncalibrated; the harness exists with zero labels entered

**Hard ordering constraint:** D2 currently masks D5. Fixing the write-back without fixing the label
inversion in the same change activates a path that serves a human-flagged failure to customers via
`verified_qa_lookup`.

## Toolchain state

- `apps/api/.venv` is **restored** (2026-08-07): pytest 9.1.1, 396 packages. The earlier "shell, 34
  packages, no pytest" reading is superseded. If it is disk-cleaned again: `cd apps/api && uv sync
  --extra dev`, and run one `uv` at a time — two concurrent runs deadlock on the wheel cache lock.
- `apps/admin/node_modules` and `apps/widget/node_modules` are present.
- Backend suite baseline **OBSERVED 2026-08-07 at `af0f601` (main): 1675 passed, 11 skipped,
  0 failed, 30 warnings, 451s.** Supersedes the 2026-08-05 `fd8fa20` reading of 1199/8/0/202s, which
  predates the eval-foundation merge. Any phase claiming a delta measures against 1675/11.
  (Wall clock roughly doubled with the test count; 451s is the number to expect, not a warning sign.)

## Next move

1. ~~Finish the toolchain restore, observe the real baseline.~~ **Done 2026-08-07** — see above.
2. ~~Run `.dev/workflows/eval-foundation.workflow.js` on `feat/eval-foundation`.~~ **Superseded** —
   that branch merged at `fd47133`; the workflow is archived in `.dev/workflows/` as the reference
   implementation of the tier-2 pattern, not as pending work.
3. **CI (§1) is paused by the owner, 2026-08-07** — blocked on the Actions billing cap (`0.3`), which
   is not a code problem. `1.3` and `1.4` remain available as local work.
4. Step 0 of the ladder is **owner work, not agent work**: score
   `apps/api/tests/evals/calibration/human_scores.csv`. Nothing above it can be trusted until judges
   are calibrated. The workflow prepares the inputs; it cannot supply the judgement.
5. The unblocked headline is **D1** (`BACKLOG` §2) — `app/worker/tasks/runtime/eval.py:374-375`
   still sets `"agent_response": row[3]`, where `row[3]` is `reference_answer`.
