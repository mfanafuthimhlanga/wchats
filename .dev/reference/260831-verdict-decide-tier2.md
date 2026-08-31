# Ticket 17 tier-2 judgement: verdict-decide

The Fable tier-2 judge's reading of branch `feat/verdict-decide` at `bf148e2` (workflow
`wf_ed0e5ba1-4d3`, 2026-08-31), for whoever handles the merge or reopens this work.

**Verdict: merge_after.** The implementation matches the ticket's four criteria; six
conditions stand between the branch and the merge.

## The conditions, and their disposition

1. `scripts/gates.py full` observed green at `bf148e2`. The last full-suite observation
   predates S4 entirely. DONE IN SESSION when green output is quoted in the PR; a
   `gates_green: true` boolean from an agent is a claim, not an observation.
2. The RULE_VERSION 2 golden amendment recorded on decision #19, or the owner accepts the
   deviation at merge. Recorded: comment of 2026-08-31 on #19. The owner's merge is the
   acceptance gesture; a rejection reverts `e8254ee`'s golden rule function alone.
3. `f55d052` (the S3 fix) landed without a tier-1 review after its author died mid-run.
   DONE: a dedicated review ran in-session on 2026-08-31. All five S3-verify blockers are
   CLOSED at head; its fourteen further findings became commit `57f8b50` (nine fixes, with
   guard mutations observed red then green) and issues #127 through #131.
4. The workflow script versioned: committed as `.dev/workflows/verdict-decide.workflow.js`.
5. Issues owed by the same-day rule: #124 (acks_late double-continuation residual),
   #125 (pre-try stretch leaves a checklist row 'running' forever), #126 (RULE_VERSION
   name collision between eval_result and verdict). All three opened 2026-08-31.
6. DONE: the new SQL executed once against the local `wchats_tenant_probe` cluster on
   2026-08-31; every statement ran, and the empty-table `None` rows read as absent.

## Asserted but unproven, at judgement time

- Full gates green at head: asserted by every implementer, observed only up to `e704497`.
- `_READ_RED_TEAM_RUN_RESULT_SQL` (with its `::uuid` cast), both `*_since` status readers,
  `SELECT now()`, and `_RED_TEAM_LATEST_SQL`'s `status <> 'running'` filter: never executed
  against PostgreSQL.
- The re-queue wait never ran against a real broker: Celery is not eager in tests,
  `_requeue_wait` is patched everywhere, and the ceiling has never been observed to expire
  against a genuinely slow run. The queue-starvation fix is proven at unit level only.

## What the judge confirmed against the evidence

- `Verdict` refuses an outcome that is not the fold of its own reasons, on construction and
  on read; the mutation demonstrations quote observed red and green for the guard, the
  missing-key refusals, the zero-trials refusal and the Wilson bound clamp.
- The thresholds exist only as `app/domain/verdict.py` module constants; the orchestrator
  prompt at head carries no threshold number.
- The stale-signal shape (a checklist reading `no_runs` for the run it just started) is
  closed by sequencing and pinned by a regression test.

## The one open design residual

The S3 verify pass judged a pre-`f55d052` tree and its five blockers read as history now,
but its fourth finding survives as #124: a re-queued continuation recognises its own
'running' row, and nothing fences a redelivered duplicate of the continuation itself.
