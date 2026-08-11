# HANDOFF — 2026-08-11

> **`.dev/BACKLOG.md` is the single ordered list of open work.** Read it before starting anything.
> This file is the current-state snapshot; that one is the queue.

> # IN FLIGHT — `chore/local-postgres`, unmerged, tip `d4f65e2`
>
> **The environment is real now, and both gates are green on it.** PostgreSQL 17.6 with pgvector
> 0.8.1 on `localhost:5432`, Redis on `:6379`, control DB at `0019`, a tenant probe DB at `0016`.
> `BACKLOG 0.2` is closed and `3.5` with it.
>
> **Measured 2026-08-11, on this tree, by running them:**
>
> ```
> integration  15 passed, 22 skipped, 24 deselected in 109.31s   (0 failed, 0 errors)
> unit       2164 passed, 13 skipped, 30 warnings in 397.83s     (0 failed)
> ```
>
> Run integration with `INTEGRATION_DB_URL=postgresql://wchats:wchats@localhost:5432/wchats_control`
> and `REDIS_URL=redis://localhost:6379/0`. The unit command is the one in CLAUDE.md, unchanged.
>
> **The branch changed only tests, docs and one config line.** `git diff --stat 3e7fb8e..d4f65e2 --
> apps/api/app/` is a single line: `hide_input_in_errors=True` on `Settings`. Every one of the
> thirteen defects fixed here was in the test suite, not the product.
>
> **Two things were destroying-or-leaking shaped, and both are closed:**
> - `nightly.yml` reclaimed Neon projects by **listing the account and matching names**
>   (`vrd-*` + `e2e`). It was simultaneously dead — `_project_slug` never emits that prefix — and
>   able to delete anything. Now an id-scoped ledger; nothing is listed, nothing matched by name.
> - `test_worker_kill.py` was the last un-stubbed provisioning dispatch, one exported
>   `NEON_API_KEY` away from creating real billable projects with no teardown. Ported onto the
>   in-worker Neon stub, and it **runs green for the first time in repo history** (62s).
>
> **Neon account: 8 baseline projects, verified present before and after all work. Nothing created,
> nothing deleted.** `C:/Users/Bantu/pg-setup/neon-baseline.txt` is the pin.
>
> **Read next:** `.dev/reference/260811-review-fix-mutation-proofs.md` — 13 mutation proofs with
> verbatim red and green, both gate runs, both baseline checks, and a section on the one guard that
> was measured to be a tautology and deleted. Traces: `260811-review-fixes.md`,
> `260810-neon-boundary.md`, `260810-query-dispatch.md`, `260810-sse-live-events.md`,
> `260810-local-postgres.md`, `260810-docling-gate.md`.
>
> **Next move:** `0.6` (one `count(*)`, sizes the labelling loop), then `0.1`/`0.4`. `BACKLOG 1.13`
> is newly named and cheap to start: 22 integration tests still skip, and a skip is unobserved.

> # MERGED — 2026-08-09, `main` is at `57be16b`
>
> `feat/d6-labelling-loop` merged with `--no-ff`, carrying `feat/d1-agent-invocation` with it (D6
> contained all 22 of D1's commits). 42 commits, 67 files, +35,033/-411, no conflicts. **The merged
> tree is byte-identical to the tip measured at 2112 passed / 12 skipped / 0 failed** — verified with
> `git diff feat/d6-labelling-loop main`, which is empty — so that observation carries to `main`
> without re-running.
>
> **Merged with `0.4` and `0.5` unsettled.** The owner's call, recorded, not argued. What changed:
> - **Nothing deployed.** No workflow triggers a deploy; the repo has only `ci.yml` and `nightly.yml`.
>   No eval has run against any production tenant.
> - **`0.4` moved rather than lapsed.** `celery_app.py:208` schedules `eval-nightly` at 02:00 UTC
>   daily, so the first beat worker running this code against production tenants sends customer rows
>   to the Ragas judge with `pii_firewall_applied=False`. There is no further merge in the way — the
>   next gate is a deploy, and deploys are not gated here.
> - **`0.5` was decided by merging.** The deviation is on `main`. The row stays open only to record
>   which way, and it is cheap either way.
>
> **What is live and what is inert.** Live: the eval invokes the real agent, and the deploy gate
> refuses a run that does not record having invoked it (including every pre-D1 run, which fails
> closed). Inert: the labelling loop. `alembic_tenant` 0016 has been applied to no database, so every
> label attempt returns 503, and per `2.28` the miner that was to fill the queue has never produced a
> row and cannot. Both behind `0.2`.
>
> **Next, in order:** `0.6` (one `count(*)` — sizes the whole loop and decides whether `2.28` and P4
> are worth building), `0.2` (a local PostgreSQL — unblocks the migration roundtrips, the calibration,
> and the metric ever being observed to move), then `0.4` before anything runs nightly.

> **STATE AS OF 2026-08-08, end of the D1 workflow.** All 14 agents completed. P1, P1b, P2, P3, their
> tier-1 reviews and bounded fixes, and — for the first time on this branch — **the tier-2 judge**.
> Verdict: **`mergeable: true`**, extracted to `.dev/reference/tier2-judge-d1.md`. Its one-line read:
> *"a correctly-shaped, fail-closed measurement pipeline that has never measured anything — which is
> a large improvement over a pipeline that confidently measured its own label, and is honestly
> labelled as such."*
>
> **Two owner decisions block the merge**, filed as `BACKLOG 0.4` and `0.5`: production customer rows
> can reach the Ragas judge API with the PII firewall off (an *egress* question `2.11` frames only as
> scoring fidelity), and the `alembic_tenant` migration the plan required does not exist — argued
> away coherently, but a written-contract deviation only you can accept.
>
> **Naming caveat for anything below and in `.dev/traces/`:** every in-phase reviewer on this branch
> was **tier-1**. Several commit messages, three trace filenames (`…-p1b-tier2-fixes.md`,
> `…-p2-review-fixes.md`, `…-p3-review-fixes.md`) and earlier BACKLOG text call them "tier-2". They
> are not. Tier-2 is the Fable judge, it ran once, and its output is the reference file above.

> **STATE AS OF 2026-08-09 — D6, `feat/d6-labelling-loop`, STACKED ON THE UNMERGED
> `feat/d1-agent-invocation` (`4179a5c`), not on `main`.** Merge only the top of the stack, and only
> once D1 lands. Nothing below has been rebased.
>
> - **P1 landed** (`alembic_tenant` 0016 + `app/services/label_service.py`): the `human_authored`
>   tier that `LABEL_TRUST_TIERS` had declared since D5 and nothing could produce, behind four
>   restrictions. **P2 landed** (`4962ff5`, review fixes `17a5774`): `GET .../eval-scenarios/unlabelled`
>   and `POST .../eval-scenarios/{id}/label`. **P3 landed** (`edb4fbb`). **P4 is unstarted**, by the
>   owner's "backend only this run".
> - **P3's finding is that the run record had gone stale, not that plumbing was missing.**
>   `VERIFIED_QA_PROMOTION_DECISION["reason"]` — stamped into `eval_runs.config` on every run — said
>   "no row is promotable until a correction UI produces human-verified answers". **P1 and P2 built
>   that correction UI**, and `human_authored` (rank 3) clears `VERIFIED_QA_MIN_TRUST_TIER` (rank 2),
>   so the pre-D6 guarantee — "unreachable by construction, no flag needed, and it becomes reachable
>   the moment a human tier exists" — had inverted from a feature into a hazard against the owner's
>   eval-only decision. Promotion is now held by **two independent locks**: the gate reads
>   `eval_scenarios.source` (which labelling never touches) and `enabled: False` refuses last, so a
>   row it refuses is *counted* under `promotion_disabled:eval_only` — which is the measurement of
>   what flipping the decision would actually promote. Downstream, a labelled row enters the eval,
>   joins **no** golden set (the label UPDATE assigns four columns and `dataset` is not one), and
>   raises the **denominator** without raising `scored` when the agent cannot answer it.
>   Long form and all 12 mutation proofs: `.dev/reference/d6-p3-label-downstream.md`.
> - **R2 fired twice on P3's own work and was not weakened** — once on prose naming the writer inside
>   a string constant, once on the new test module importing it. Two tests consequently live in
>   `test_label_provenance.py`; the new module reaches the tier through
>   `VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]`, pinned equal there.
> - **Branch suite OBSERVED at the D6 tip (`6dc4990`), 2026-08-09: 2112 passed, 12 skipped, 0 failed,
>   28 warnings, 493.34s.** Zero `FAILED`/`ERROR` lines. Detached run (`Start-Process`), not relayed.
>   This is the figure to merge on; the `edb4fbb` reading below predates four later commits.
> - **`feat/d6-labelling-loop` contains ALL of `feat/d1-agent-invocation`** — verified by
>   `git merge-base --is-ancestor`. `main..d6` is 42 commits, of which 22 are D1's. Merging D6 merges
>   D1 with it, so `0.4` and `0.5` sit in that path whichever branch is named. Rebasing D6 onto `main`
>   to separate them is not viable: D6's P3 builds on the eval structures D1 rewrote.
> - Branch suite at `edb4fbb`+: **2101 passed, 12 skipped**. Ignored-new-files control: **2077/12**,
>   which is the *measured* baseline at `1c2b471` — so no pre-existing test changed status. Note the
>   D6 workflow brief's "1873/11" is the branch point `4179a5c`, before P1 and P2 landed; it is not a
>   baseline for anything on this branch.
> - **`BACKLOG 2.4`'s "mined scenarios are inert by construction" is now narrowed but NOT closed.**
>   The tier exists, the routes exist, and **0016 has been applied to no database**, so every label
>   attempt on every tenant today returns a 503 naming the migration. No row has ever left the
>   unlabelled state. Behind `0.2` (no PostgreSQL here) like everything else.
> - The P2 adversarial review found **18 items, including four behaviour mutations that survived the
>   54 tests P2 shipped with** — the queue's sort direction, its `LIMIT`/`OFFSET` binding, the counts
>   identity, and three of four spellings of a forged write. All fixed at `17a5774`; the write is now
>   scoped so it cannot overwrite a golden-set answer, and **only a Clerk JWT may stamp a human tier**
>   (an API key is a machine credential and `label_service`'s guards are all in-process).
>   Long form: `.dev/reference/d6-p2-labelling-queue.md` §7.6, and
>   `.dev/reference/d6-p2-review-fixes.md`.
> - Branch suite at `17a5774`: **2077 passed, 12 skipped**. Ignored-new-files control: **1994/12**.

**In flight (2026-08-08): `feat/d1-agent-invocation`, unmerged.** P1 (the options seam, `ec5f445` +
`d15be3a`), P1b (recorded mode + the canary write order, `487ebbe` + `117de05`), **P2 — the eval
invokes the agent** (`d127b4d`) and the P2 review fixes (`b62186f` + `075550d`).
`eval.py` no longer sets `agent_response = reference_answer`: each scenario's question goes to the
customer agent through the seam with `side_effects="recorded"`, the agent's own text and its own
retrieved contexts are what get scored, failed scenarios are excluded and counted, a run below
`MIN_RESPONSE_RATE` reports `unknown`, and `config.agent_invoked` is written as an observation.
**P3 has not started** (BACKLOG 2.2) — but the ship-on-nothing window it was going to leave open is
closed in the interim: a run below the floor now writes **no `eval_results` at all**, so the gate
reads `EVAL_SIGNAL_NO_VALID_SCORES` and refuses. A run produced by the pre-P2 tautology still carries
scores and no `agent_invoked`, which is still P3's job.

**The tier-2 judge HAS now read P2** (at `7a7486e`): 17 findings, 7 unsupported claims, all
addressed in `b62186f`. Four changed what a run means — the below-floor fail-closed above; the
contexts handed to Ragas are now one untruncated string per chunk rather than a truncated repr of the
SDK block as a single element; the broker's `visibility_timeout` (3600) was below the worst case a run
stamps on itself (5400) and the idempotency window was 1/9 of it; and a responded turn with no
retrieve call is excluded and counted rather than scored 0 on three context metrics. Three of P2's
own guards were proved not to be guards — most sharply, a one-token fallback to the stored context
column passed all 163 eval tests.

**Branch suite OBSERVED at the tip (`1d85789`), 2026-08-08: 1873 passed, 11 skipped, 0 failed,
30 warnings, 366.48s.** 1884 collected. Zero `FAILED`/`ERROR` lines. This supersedes the 1821 below,
which was recorded at the P2 review fixes — P3 and its review fixes added tests after it.

Two notes for whoever runs it next, both learned the hard way here:

- **The gate takes ~6 minutes and fits the tooling fine.** Three consecutive backgrounded attempts
  were killed at ~3-4% and it was briefly written up as a wall-clock regression. That was wrong:
  a detached run (`Start-Process`, output redirected to a file) completed in 366s, faster than
  `main`'s 451s with 178 more tests. Cause of the kills unknown; if it recurs, detach rather than
  concluding anything about the suite.
- **~15 tests cost 14-16s each**, all in `test_agent_task.py`, `test_agent_turn_metrics.py`,
  `test_agent_turn_connection_batch.py` and `test_agent_options_seam.py` — about 225s of the 366s.
  A characteristic of the turn-path tests, not a regression. Isolated-module timings mislead here:
  `test_agent_task.py` alone reads 142s for 13 tests, which invites exactly the wrong conclusion.

Superseded figure: 1821 passed / 11 skipped / 0 failed (was 1795 at `7a7486e`, 1766 at `1d3a7bd`).
`mypy app` clean; `ruff check app tests` clean via `uvx ruff@latest` — ruff is **not** installed
in `apps/api/.venv`.
(`main`'s 1675/11/0 below is the pre-branch number and is not the figure to measure a delta against.)

Traces: `.dev/traces/260808-d1-p2-review-fixes.md` (latest), `.dev/traces/260808-d1-p2-invoke.md`.
Mutation proofs: `.dev/reference/p2-review-mutation-proofs.md` (23, red then green; **one did not go
red first time** and the fixture gap is recorded), `.dev/reference/p2-mutation-proofs.md` (19).
Earlier: `.dev/traces/260807-d1-p1b-recorded-mode.md`, `.dev/reference/p1b-mutation-proofs.md`.

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
