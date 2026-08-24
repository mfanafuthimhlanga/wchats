# The Harness cut (#39)

Ticket #39, decision #6 on map #4, ADR 0003. Three commits on `chore/harness-cut`,
stacked on `chore/gates-as-standards` (#38): the ADR first (`6514329`), the cut
(`2e6550a`, 17 files, +133/-9,076), the review round (`f78c561`). Issue #28 closes with
the cut: the never-used Neon branch provision and delete are gone.

## What went, what stayed

The three groups from #6's inventory went with their tests: `decision_eval_service.py`
whole (1,897 lines), the label queue and trust-tier and promotion set across
`app/api/v1/evals.py` and `app/services/eval_service.py` (1,371 net), and branch
isolation in `run_eval_suite` (705 to 613 lines). The observed eval path survived
unchanged; the Spec review diffed `run_eval_suite` before and after and found only the
branch machinery gone, with writes, events and retry semantics identical.

The inventory was wrong on two names, caught by caller-grep before deletion:

- `_query_tenant_db_sync` stays; `list_eval_runs` calls it three times and
  `get_eval_run_results` once.
- `is_human_label_tier` stays; `label_service.py` imports and calls it.

Kept deliberately, though nothing in `app/` calls them today: `neon.py`'s
`create_branch` and `delete_branch` (branch isolation has a named way back under
Mellow's `ship`, and these are its client), and `deps.py`'s credential helpers
(another ticket's subject). `label_service.py` survives callerless; its docstring says
so, and R2's boundary still guards it.

## What review caught

- The cut left `test_eval_agent_invocation.py` patching the three deleted branch
  functions, which errored 5 tests and made the full tier red
  (`2349 passed, 13 skipped, 5 errors`). All five test surviving behaviour; the dead
  patches went, the tests stayed.
- Stale prose at eleven sites described the label queue, promotion or the deleted
  labelling route in the present tense; all aligned to HEAD. Lock and query counts in
  comments disagreed with the code at five sites; the code counts won (two locks,
  three scenario queries).
- The ADR claimed `decision_eval` migrations remain; no alembic file mentions the
  name (the service read the still-served `tool_calls_audit` table). Corrected, along
  with the measured 1,371 net for group 2.
- `SCENARIO_SOURCE_TRUST_TIER` survived with test-only readers; deleted with them.

## The gate on its own authors

Two moments worth keeping. The stale machinery from #38 enumerated the baseline shrink
mechanically: LIZARD 131 to 123 entries (the 8 predicted on issue #8), source
assertions 46 files/138 sites to 44/119, snapshots pruned in step. And during the
review fixes the static gate went red because two docstring rewrites grew pinned
functions by one line each; the prose was trimmed rather than the pins raised.

## Observed

- `static gates passed in 18.5s.` after the review round, exit 0.
- Six nearest test files after fixes: `276 passed, 1 skipped in 68.73s`.
- Whole suite collects 2,441 tests with no collection errors.
- Em and en dashes on inserted lines: 0.

After the review round, observed at f78c561: `full gates passed in 1158.6s.`, exit 0.
