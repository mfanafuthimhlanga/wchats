# Trace: verdict-decide (ticket 17, #54)

Branch `feat/verdict-decide` off `b0c25e7`. Workflow `wf_ed0e5ba1-4d3`, four slices, each
implementer + adversarial reviewer + fixer, tier-2 judge `merge_after`
(`.dev/reference/260831-verdict-decide-tier2.md`). Closes #54 and #36 on merge.

## Commits

- `eae64c9` S1: `app/domain/verdict.py`, pure `decide()` over ten rule functions, 65 table
  tests, five mutation demonstrations observed red then green.
- `e8254ee` S1 fix: golden gates on `scenarios_passed == attempted`, split across
  `golden_failure` / `golden_unconfirmed`. This is the RULE_VERSION 2 amendment, recorded
  on #19 (2026-08-31 comment), owner acceptance at merge.
- `2a86944` + `b68c13f` S2: `RedTeamResult.from_payload` and `read_red_team_result`;
  round-trip identity pinned, every key required.
- `e704497` S3: the checklist sequences both jobs and waits terminal.
- `f55d052` S3 fix: the wait re-queues with `wait_state` instead of sleeping in the solo
  worker's only slot; `did_not_finish` signals for a half the ceiling cut off.
- `b8e615f` S4: `verdict.outcome` is the recommendation, `submit_report` loses its
  recommendation field, threshold text leaves the prompt.
- `bf148e2` S4 fix: a malformed narration costs the prose, never the verdict.
- `57f8b50` the f55d052 review, applied: guarded re-queue, unreadable continuations close
  their run, value-checked `wait_state`, honest warning copy and docstrings; the task keeps
  its pinned lizard shape via `_continue_wait` / `_hand_off` / `_wait_continues`.

## Decisions and deviations

- Wilson 95% for the exploratory CI; output bounds clamped to [0, 1] (the closed form
  returns an algebraic zero floats print as `-0.0`). Inputs refuse, never clamp.
- Coverage below the 0.90 floor blocks rather than warns (ADR 0007).
- `block_on_high` crosses the domain seam as a parameter; the chosen value is recorded in
  the stored reason sentence.
- `RULE_VERSION` in `verdict.py` is 2 and collides by name with `eval_result.py`'s 1: #126.

## Breakage the run itself surfaced

- S3's first cut slept inside the Celery task on the same `runtime` queue as the jobs it
  dispatched: one solo slot, so the wait could never be satisfied. The fixer's re-queue
  design closed it; the fixer then died mid-response (API error) after committing
  `f55d052`, so that commit ran without a tier-1 read.
- The S3 verify pass judged the pre-commit working tree; its five blockers were closed by
  `f55d052` plus the S4 commits, confirmed by the dedicated review below.
- The workflow script treated a dead reviewer as an approval path; fixed in the script and
  logged as an FM-013 recurrence before results existed.

## The f55d052 review (2026-08-31, in-session)

All five S3-verify blockers CLOSED at head, with file:line evidence. Fourteen further
findings: nine fixed in `57f8b50`, five filed (#127 kind-unscoped red-team latest read,
#128 since-reader masking, #129 chain outliving the 60-minute guard, #130 known-failed
dispatch burns the ceiling, #131 plausible-zero fallbacks), and the redelivered-duplicate
mechanics plus the #125 residuals recorded on those issues.

## Observed

- `scripts/gates.py full` at `bf148e2`: **4032 passed, 13 skipped, 806s pytest; full gates
  passed in 967.0s.**
- Live probe 2026-08-31 against `wchats_tenant_probe` (PostgreSQL 17.6, localhost): all
  five new statements executed; both since-readers, the status-filtered latest read and the
  `::uuid` result read returned empty-table `None`, which every caller treats as absent.
- Guard mutations at `57f8b50`, each restored from HEAD after the red:
  statuses-value guard neutered -> `1 failed, 3 passed`, restored `4 passed`;
  timestamp parse skipped -> `2 failed, 2 passed`, restored `4 passed`;
  requeue failure reports success -> `1 failed, 1 passed`, restored `2 passed`;
  hand_off ignores the failed requeue -> `1 failed, 1 passed`, restored `2 passed`;
  unreadable continuation no longer closes its run -> `1 failed`, restored `1 passed`.
- `scripts/gates.py full` at `57f8b50` and the mypy head-versus-main counts: recorded in
  the PR body when run.
