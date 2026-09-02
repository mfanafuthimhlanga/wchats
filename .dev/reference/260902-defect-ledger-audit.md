# The defect ledger, measured against the test tree (2026-09-02)

For anyone picking a defect off the issue list. Each open non-CI issue was read against
`apps/api/tests` to see whether a test can make it fail. Numbers are from `main` at
`26ea5d9`.

## The measurement

35 open defects, excluding CI.

| pin state | count | meaning |
|---|---|---|
| UNPINNED | 22 | no test refers to the defect |
| MENTIONED | 10 | a source or test comment cites the issue; nothing asserts the fixed behaviour |
| PINNED | 3 | a test exists, two of them for half the defect (#125, #79) |

Two tests pinned the bug rather than the fix. `test_the_selection_is_deployed_only_never_ready`
asserted `status` was absent from the fan-out WHERE clause (#134). `test_model_client.py`
asserted `provider_for_base_url(None) == "anthropic"` (#88). Both were rewritten before
their fix, and both went red on the unfixed code first. #151 and #153 carry the runs.

The gates themselves, run on this box the same day:

```
ruff 0, import contracts 3 kept, complexity 115 pinned, source assertions 44 files,
4171 collected, 4158 passed, 13 skipped, 24.5 min
```

Every gate is a count pin, so green means "no worse than the day the pin was set". Read
the number under each pin, never the colour.

## Where the defects sit

Kinds: correctness 14, data-integrity 8, security 3, observability 2, code-quality 3,
test-quality 2, config 2, UI 2.

The three largest files, `deployment_service.py`, `eval_service.py` and
`red_team_service.py`, hold 7.9k lines and 14 of the 35 defects. The mypy count sat in
four files: `validation_service` 63, `red_team_service` 28, `actor_seam` 21,
`strategy_service` 21, out of 153. All four are call-site files of the Luna migration, so
#153 brought the total to 22 without a typing pass.

## Pairs that are one fix

- **#121 and #131** share `_fetch_verified_qa_stats_sync`. Carry `None` instead of the
  zero dict and both close.
- **#124 and #85** are one class, acks_late redelivery against sequential-only guards, on
  two tasks. #125 and #129 sit on the same checklist row and the same 60-minute guard.
- **#144** has thirteen `CERT_NONE` sites under `app/`, not the five its body names. Only
  `transactional/enforcement.py` reads `REDIS_TLS_INSECURE`.

## How to pick one up

1. Read the issue's comments, not only the body. Today's corrections live there: the
   `CERT_NONE` count on #144, the moved path on #35, the taken migration number on #120.
2. Write the failing test first and paste its red into the commit body. The 22 UNPINNED
   rows are the ones where that step is the whole fix.
3. When a fix drops a pinned count (mypy, lizard, source assertions), lower the pin in the
   same commit. The gate fails on a file under its pin.

## Worktree facts that cost time

- Copy the main tree's `apps/api/.mypy_cache` (about 190 MB) into a worktree before the
  first mypy run. Cold, mypy does not finish in twenty minutes on this box. Warm, 90 s.
- A worktree has no venv. Junction it:
  `cmd //c "mklink /J <worktree>\apps\api\.venv <main>\apps\api\.venv"`, and `rmdir` the
  link at the end.
- A cold import of `app.worker.tasks.runtime.eval` takes about two minutes before the
  first test in `test_eval_task.py` runs. Budget for it; it is not a hang.
