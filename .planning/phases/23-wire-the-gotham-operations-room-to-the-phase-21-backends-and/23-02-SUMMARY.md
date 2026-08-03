---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 02
subsystem: api
tags: [psycopg2, postgres, red-team, fastapi, pytest, jsonb-correlation]

# Dependency graph
requires:
  - phase: 21
    provides: "red_team_findings first-class table (migration 0012), the IDOR-guarded GET /agents/{id}/red-team/programme route, and the POST .../findings/{id}/contain route this plan's identifiers feed"
provides:
  - "open_findings: a fourth key on read_programme()'s response — the agent's open red_team_findings rows with real primary keys, ranked by explicit severity, each carrying a best-effort description recovered from its own run's JSONB snapshot"
affects: [23-06, 23-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Explicit CASE severity-rank expression in ORDER BY instead of a lexical DESC sort on a TEXT column with a CHECK-constrained value set (F-8 guard)"
    - "SQL-shape assertions via mock_cursor.execute.call_args_list — reused from the existing TestRunRedTeamProgrammeWrites INSERT/UPDATE-shape idiom, now applied to a SELECT statement's WHERE/ORDER BY text so a guard-removal mutation fails an actual pytest test, not only a standalone shape-gate script"

key-files:
  created: []
  modified:
    - apps/api/app/services/redteam_programme_service.py
    - apps/api/tests/unit/test_redteam_programme.py

key-decisions:
  - "Kept the row tuple to exactly the 10 columns read_programme needs (no severity-rank alias column) by inlining the CASE expression directly in ORDER BY rather than exposing it as a SELECT-list alias — simpler Python-side unpacking, and verified by careful regex simulation that it neither trips the negative lexical-DESC gate nor fails the positive CASE-rank gate."
  - "Added a defensive try/except around the whole of _correlate_description in addition to the isinstance() type guards — belt-and-suspenders in the same spirit as this codebase's own bench_tally docstring language, even though the isinstance guards alone are already exception-safe for every input shape a real JSONB column can produce."
  - "Wrote two dedicated SQL-shape pytest tests (test_open_findings_statement_excludes_contained_and_closed_findings, test_open_findings_statement_ranks_severity_explicitly_not_lexically) that inspect the captured statement text via mock_cursor.execute.call_args_list, rather than relying only on Task 1's standalone shape-gate script. Reasoning: this file's cursor is fully mocked, so fetchall() returns exactly what a test hands it regardless of what SQL text was executed — a mocked data-order test can never observe a WHERE/ORDER BY regression. The plan's Task 2 action text frames both guard-removal mutations as making 'a test... FAIL' (pytest, not just the shape script), so the only faithful way to honor that is a test that inspects the captured SQL string, exactly the pattern TestRunRedTeamProgrammeWrites already established for INSERT/UPDATE statements in this same file."
  - "Refreshed the two self-referential TestGetRedTeamProgrammeRoute fixtures (fake_programme, empty_programme) to carry open_findings: [] too. Not strictly required — read_programme is itself patched out in those tests, so the fixture is compared only against itself and would have kept passing at 3 keys — but leaving them at 3 keys would misrepresent the real 4-key contract to a future reader of 'the programme shape from mocked reader.'"

patterns-established:
  - "A CASE-based severity rank is the house pattern for any future TEXT-with-CHECK-constraint ordering in this codebase; a plain descending sort on such a column is now a demonstrated, gated anti-pattern (F-8)."

requirements-completed: [WIRE-04]

coverage:
  - id: D1
    description: "The programme response carries a fourth top-level list, open_findings, with each finding's real red_team_findings primary key — the identifier the contain route needs."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_open_findings_preserves_query_row_order_and_identifiers"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_correlation_hit_recovers_description_from_matching_snapshot_entry"
        status: pass
    human_judgment: false
  - id: D2
    description: "The list is filtered to open findings only; contained and closed findings never appear."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_open_findings_statement_excludes_contained_and_closed_findings"
        status: pass
    human_judgment: false
  - id: D3
    description: "Findings are ordered by an explicit severity rank (critical, high, medium, low), never by the lexical ordering of the severity string (F-8)."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_open_findings_statement_ranks_severity_explicitly_not_lexically"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_open_findings_preserves_query_row_order_and_identifiers"
        status: pass
    human_judgment: false
  - id: D4
    description: "A human-readable description is recovered per finding by correlating against the findings JSONB snapshot of that finding's own run (via the SQL join), and is null when no entry matches."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_correlation_hit_recovers_description_from_matching_snapshot_entry"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_correlation_miss_on_turn_count_returns_finding_with_null_description"
        status: pass
    human_judgment: false
  - id: D5
    description: "A finding whose description cannot be recovered — including a run row missing entirely — is still returned with its identifier and severity, and the read never raises."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_correlation_miss_on_turn_count_returns_finding_with_null_description"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestOpenFindings::test_null_run_snapshot_returns_finding_with_null_description_and_does_not_raise"
        status: pass
    human_judgment: false
  - id: D6
    description: "The three pre-existing top-level keys (strategies, probes, coverage) keep their exact shape and construction; nothing about them changes."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestReadProgrammeService::test_service_wires_mocked_cursor_and_computes_coverage"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestReadProgrammeService::test_service_empty_programme_returns_empty_lists"
        status: pass
    human_judgment: false
  - id: D7
    description: "The route serving this response is unchanged (returns the service dict verbatim) and its tenant-ownership IDOR guard (404-not-403) still holds."
    requirement: "WIRE-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestGetRedTeamProgrammeRoute::test_returns_404_on_cross_tenant_idor"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_redteam_programme.py#TestGetRedTeamProgrammeRoute::test_returns_404_when_agent_not_found"
        status: pass
      - kind: other
        ref: "git diff --quiet -- apps/api/app/api/v1/red_team.py (byte-unchanged)"
        status: pass
    human_judgment: false

duration: ~40min
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 02: Red-team open findings with real identifiers Summary

**`read_programme()` grows a fourth key, `open_findings` — the agent's open red_team_findings rows ranked by an explicit severity CASE (never the lexical-sort bug F-8 warned about), each carrying a description recovered by correlating against its own run's JSONB snapshot, defaulting to null on any miss rather than ever blocking the finding.**

## Performance

- **Duration:** ~40 min (estimate — commits 25 min apart at 10:09 and 10:34 local time; total includes upfront reading of ~10 source/planning files and a full baseline suite run before any edit)
- **Started:** ~2026-08-03T07:50:00Z (estimated)
- **Completed:** 2026-08-03T08:34:01Z
- **Tasks:** 2/2 completed
- **Files modified:** 2

## Accomplishments
- `redteam_programme_service.py` gained `_OPEN_FINDINGS_SQL` (findings LEFT JOINed to their own run's `findings` JSONB, filtered to `status = 'open'`, ranked by an explicit `CASE` expression — critical/high/medium/low — never a lexical `DESC` sort on the TEXT severity column), `_correlate_description()` (defensive, never-raising per-run description recovery), and the `open_findings` list/key in `read_programme()`'s return.
- Repaired the three pre-existing `TestReadProgrammeService` tests broken by the new fourth query (finding F-7 — three-element `side_effect` lists exhausted by a fourth `fetchall()` call; one test additionally pinned an exact three-key dict).
- Added `TestOpenFindings` (7 tests) covering all 6 behaviors the plan named plus row-order/identifier fidelity, including two SQL-shape tests that inspect the captured statement text directly — the mechanism that makes both mandatory guard-removal mutations fail an actual `pytest` test, not only a standalone script.
- Ran both guard-removal demonstrations for real: dropping the open-status filter, and replacing the CASE rank with a lexical `ORDER BY severity DESC`. Both observed red, both restored from HEAD, both reconfirmed green (details below).
- Full unit suite: **1199 passed, 8 skipped, 0 failed** (baseline was 1191/8/0 — the +8 is this plan's 7 new tests plus one net-new test from sibling plan 23-01 landing in the same shared suite).

## Task Commits

Each task was committed atomically, scoped to exactly its own file (`git commit ... -- <file>`) to protect against the shared git index across the three parallel Phase 23 executors:

1. **Task 1: The open-findings query and its per-run description correlation** - `432888b` (feat)
2. **Task 2: Repair the three broken cursor scripts and prove the four locked properties** - `039bba3` (test)

**Plan metadata:** commit hash recorded after this SUMMARY is committed (see below).

## Files Created/Modified
- `apps/api/app/services/redteam_programme_service.py` - Added `_OPEN_FINDINGS_SQL`, `_correlate_description()`, and the `open_findings` key on `read_programme()`'s return; extended the module and function docstrings; extended the structured log with `open_finding_count`.
- `apps/api/tests/unit/test_redteam_programme.py` - Repaired 3 existing tests (F-7), refreshed 2 route-fixture dicts, added `_make_programme_cursor()` helper + `TestOpenFindings` (7 tests).

## Decisions Made

1. **Inline CASE in ORDER BY, no rank alias column.** Kept `_OPEN_FINDINGS_SQL`'s SELECT list to exactly the 10 columns `read_programme` needs; the severity rank is computed only inside `ORDER BY`, never exposed as an extra column Python would have to unpack and discard. Verified by manual regex simulation (documented inline during execution) that this phrasing satisfies the positive `CASE...critical...high...medium` gate and the negative `ORDER BY\s+[a-z_.]*severity\s+DESC` gate simultaneously — the embedded space in `CASE f.severity` breaks any accidental match of the banned lexical-sort pattern.

2. **Defensive try/except in `_correlate_description`, on top of `isinstance` guards.** The `isinstance(run_findings, list)` / `isinstance(entry, dict)` checks are already exception-safe for every shape a real JSONB column can produce, but the threat model (T-23-GB-02) treats a correlation failure taking down the whole programme read as a high-severity risk, so the function is wrapped end-to-end regardless — belt-and-suspenders, matching this codebase's own established idiom (see `bench_service.py`'s "belt-and-suspenders" comment).

3. **SQL-shape pytest tests, not just the Task 1 shape-gate script, for the guard-removal demonstrations.** This file mocks the psycopg2 cursor entirely — `fetchall()` returns exactly what a test's `side_effect` list says, completely decoupled from the SQL text actually passed to `execute()`. That means a test asserting on *returned row order or content* can never fail when the SQL's `WHERE`/`ORDER BY` text is wrong; only a test that inspects the *captured statement string* (`mock_cursor.execute.call_args_list`) can. Task 2's plan text explicitly requires both guard-removal mutations to turn "a test" red, so `test_open_findings_statement_excludes_contained_and_closed_findings` and `test_open_findings_statement_ranks_severity_explicitly_not_lexically` inspect the captured SQL directly — the exact idiom `TestRunRedTeamProgrammeWrites` already uses above them in this same file for INSERT/UPDATE shape, now reused for a SELECT statement's WHERE/ORDER BY.

4. **Refreshed the two self-referential route-test fixtures.** `test_returns_200_with_programme_shape_from_mocked_reader` and `test_empty_programme_returns_empty_lists_not_404` both patch `read_programme` itself, so their local fixture dicts are compared only against themselves and would have kept passing unmodified at 3 keys. Added `"open_findings": []` to both anyway, since a fixture named "the programme shape from mocked reader" that no longer matches the real 4-key contract is a small but real form of drift.

## Deviations from Plan

None — plan executed exactly as written. The two SQL-shape tests and the two fixture touch-ups above are implementation judgment calls within the plan's stated design (a 4th query, a rank expression, per-run correlation, four locked properties proven by tests), not disagreements between the plan's instructions and what the source says. No case of "plan says X, source says Y" arose in this plan — every `<read_first>` pointer (route lines 285-333/414-461, migration 0012's exact column list and both indexes, the tenant-schema runs table, `RedTeamFinding`'s fields, `traces.py`'s correlation-technique docstring) matched source exactly on direct read.

### Guard-Removal Demonstrations (both required, both performed)

**(a) Dropped `WHERE f.status = 'open'` from `_OPEN_FINDINGS_SQL`.**
Ran `pytest tests/unit/test_redteam_programme.py -q`. Result: **1 failed, 16 passed** — the failing test was exactly `TestOpenFindings::test_open_findings_statement_excludes_contained_and_closed_findings`, asserting `"status = 'open'" in open_findings_sql` against the captured statement text. Restored via `git checkout HEAD -- apps/api/app/services/redteam_programme_service.py`; re-ran the same command: **17 passed**.

**(b) Replaced the CASE severity rank with `ORDER BY f.severity DESC, f.created_at DESC`.**
Ran `pytest tests/unit/test_redteam_programme.py -q`. Result: **1 failed, 16 passed** — the failing test was exactly `TestOpenFindings::test_open_findings_statement_ranks_severity_explicitly_not_lexically`. Independently, re-running Task 1's own standalone shape-gate script under the same mutation also raised `AssertionError: lexical descending sort on the severity column (F-8)` — doubly confirming the regression is caught two ways. Restored via `git checkout HEAD -- apps/api/app/services/redteam_programme_service.py`; re-ran the pytest command: **17 passed**.

## Issues Encountered

**Shared git index race with sibling executor 23-01 (self-corrected, no data loss).** After staging Task 1's file (`git add apps/api/app/services/redteam_programme_service.py`) but before this executor's own `git commit` ran, the parallel 23-01 executor's `git commit -m "..."` (no pathspec) swept up the already-staged file, landing it inside their commit (`1044edd`) alongside their own `agent.py` change. 23-01 detected this independently and self-corrected via `git reset` back one commit, then re-committed with only their own file (`249a94e`), leaving this plan's changes intact and unstaged in the working tree — verified byte-for-byte identical to the intended diff via `git diff` before re-committing. From Task 2 onward (and for Task 1's eventual re-commit), every commit in this plan used a file-scoped `git commit -m "..." -- <exact file>` rather than a bare `git commit`, specifically to prevent this plan's own commits from ever sweeping up a sibling's concurrently-staged work in the reverse direction. Both final commits (`432888b`, `039bba3`) were verified via `git show --stat` to contain exactly one file each.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `GET /agents/{id}/red-team/programme` now returns `open_findings` with real ids, ready for `23-06`'s Adversary region to call `POST .../findings/{id}/contain` against.
- `23-06` also needs this plan's `open_findings` list (not `latestRedTeamRun.deployment_blocked`) to fix the stale-verdict bug per `23-01-PLAN.md` OD-4 — `run_id` and `strategy_id` are both included on each finding specifically so the console can group under coverage rows and correlate exactly, per the plan's own reasoning.
- No blockers. `apps/api/app/api/v1/red_team.py`, `apps/api/pyproject.toml`, and both alembic trees are confirmed byte-unchanged/untouched throughout.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Completed: 2026-08-03*

## Self-Check: PASSED

- FOUND: `apps/api/app/services/redteam_programme_service.py`
- FOUND: `apps/api/tests/unit/test_redteam_programme.py`
- FOUND: `.planning/phases/23-wire-the-gotham-operations-room-to-the-phase-21-backends-and/23-02-SUMMARY.md`
- FOUND: commit `432888b` (Task 1)
- FOUND: commit `039bba3` (Task 2)
