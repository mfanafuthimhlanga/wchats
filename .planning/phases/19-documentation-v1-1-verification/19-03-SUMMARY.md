---
phase: 19-documentation-v1-1-verification
plan: 03
subsystem: testing
tags: [pytest, audit, postgres, sqlalchemy, red-team-probe, tool-calls-audit]

# Dependency graph
requires:
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: red_team_probe.py (red_team_mode(), invoke_probe_tool, CLEAN_TENANT_ENVELOPES, CLEAN_TENANT_SPEC) and the ephemeral-DB fixture pattern (test_red_team_rtx.py)
  - phase: 14-transactional-tool-contract-and-dispatcher
    provides: write_audit_row / tool_calls_audit schema (no created_at parameter, server_default=now() only)
provides:
  - "compute_audit_gap(invocations, audit_rows, *, window_start, window_days) -> dict — pure, DB-free per-day coverage-parity helper (UTC bucketing, inclusive 30-day window, out-of-window tally, vacuous-pass guard)"
  - "apps/api/tests/integration/test_aud03_audit_gap.py — INTEGRATION_TESTS_ENABLED-gated AUD-03 harness: seeds 30 synthetic days of backdated tool_calls_audit rows against an ephemeral control DB, driving real dispatcher invocations on success and two config-driven rejection branches per day"
  - "apps/api/tests/unit/test_audit_gap_arithmetic.py — 11 DB-free unit tests proving every correctness claim compute_audit_gap's docstring makes"
affects: [19-05-live-gates, 19-UAT]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy (function-body) import of app.services.red_team_probe inside fixtures/tests rather than at module scope, so importing the test module never triggers app.core.config's module-level Settings() validation — required for `python -c \"import ...\"` to succeed outside pytest (conftest.py's env-var setdefault calls haven't run yet). Matches why test_red_team_rtx.py itself never imports red_team_probe at module level."
    - "Seeded-backdated-rows construction for a synthetic time window: write real rows through the real code path, then rewrite created_at via a single parameterised UPDATE ... make_interval(...) WHERE id = ANY(:ids), scoped to ids the harness itself collected — never a clock-injection seam on the production write path."
    - "Coverage-parity arithmetic factored out as a pure function so a gated/autonomous:false integration harness still has a DB-free unit companion that actually runs and proves the correctness claims now."

key-files:
  created:
    - apps/api/tests/integration/test_aud03_audit_gap.py
    - apps/api/tests/unit/test_audit_gap_arithmetic.py
  modified: []

key-decisions:
  - "AUD-03's 30-day window is built solely by direct SQL backdating of real tool_calls_audit rows after a real dispatcher run — no clock abstraction, no freezegun/time-machine, no created_at parameter added to write_audit_row (OD-3, 19-01-PLAN.md)."
  - "Each synthetic day's batch drives one deterministic success (place_order, tuned below settings.ACTOR_SKIP_MAX_AMOUNT_CENTS so the Actor's skip short-circuit engages and no live ANTHROPIC_API_KEY is required) plus two deterministic rejections (book_slot left disabled -> capability denial; issue_refund over its envelope ceiling -> max_amount_cents denial) so per-day parity proves AUD-01 symmetry on rejection branches, not only the happy path."
  - "Moved app.services.red_team_probe imports from module scope into the fixtures/test that need them (deviation from the plan's literal action text) so the module-level import stays free of app.core.config's Settings() validation dependency, letting the bare `python -c \"import ...\"` acceptance check succeed outside pytest."

requirements-completed: [AUD-03]

coverage:
  - id: D1
    description: "compute_audit_gap correctly buckets timestamps by UTC calendar date, covers the inclusive 30-day window, tallies out-of-window rows, and reports a zero-traffic window as vacuous rather than clean"
    requirement: "AUD-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_audit_gap_arithmetic.py (11 tests)"
        status: pass
    human_judgment: false
  - id: D2
    description: "apps/api/tests/integration/test_aud03_audit_gap.py collects cleanly and skips (1 skipped, 0 errors) in this environment, and imports outside pytest without requiring a live DB"
    requirement: "AUD-03"
    verification:
      - kind: unit
        ref: "pytest tests/integration/test_aud03_audit_gap.py -q (1 skipped)"
        status: pass
      - kind: other
        ref: "python -c \"import tests.integration.test_aud03_audit_gap as m; assert m.AUDIT_WINDOW_DAYS == 30\" (~1.5s)"
        status: pass
    human_judgment: false
  - id: D3
    description: "A real 30-day synthetic run against a live migrated control DB reports a per-day delta of zero across all 30 days with at least one day carrying traffic"
    requirement: "AUD-03"
    verification: []
    human_judgment: true
    rationale: "No live local Postgres/Redis in this execution environment — this is the plan's own verification:backstop truth, deferred to the operator's live run in 19-05-PLAN.md and transcribed into 19-UAT.md, never silently passed."

# Metrics
duration: 20min
completed: 2026-07-28
status: complete
---

# Phase 19 Plan 03: AUD-03 Zero-Audit-Gap Gate Summary

**Seeded-backdated-rows 30-day audit-gap harness plus a pure, DB-free `compute_audit_gap` coverage-parity helper — 11 unit tests prove the arithmetic now; the gated live run is deferred to the operator in plan 19-05.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-28T00:43:46+02:00
- **Tasks:** 2/2
- **Files modified:** 2 (both new)

## Accomplishments

- `apps/api/tests/integration/test_aud03_audit_gap.py`: `INTEGRATION_TESTS_ENABLED`-gated harness that seeds 30 synthetic days of mutating traffic against an ephemeral control DB (create -> migrate to alembic head `0019` -> yield -> `pg_terminate_backend` -> drop, all in `finally`), driving real dispatcher invocations via `invoke_probe_tool` inside `red_team_mode()` windows — one deterministic success and two deterministic rejections (capability denial, `max_amount_cents` denial) per synthetic day — then backdating that batch's real `tool_calls_audit` rows with a single `UPDATE ... SET created_at = created_at - make_interval(days => :d) WHERE id = ANY(:ids)` statement scoped to ids the harness itself collected.
- `compute_audit_gap(invocations, audit_rows, *, window_start, window_days)`: a pure function (no DB, no I/O, no `app.*` dependency) that buckets both sequences by UTC calendar date (`.astimezone(timezone.utc).date()`), builds an inclusive 30-entry `per_day` mapping (zero-traffic days present with zeroes, never a missing key), tallies out-of-window rows separately, and sets `vacuous=True` whenever `total_invocations == 0` so a zero-delta result over a zero-traffic window can never pass as clean.
- `apps/api/tests/unit/test_audit_gap_arithmetic.py`: 11 tests, DB-free, importing `compute_audit_gap` directly from the gated harness (never duplicated) — proves matched-count zero-delta, single-missing-row detection, the day-0..day-29 inclusive boundary on both edges, midnight-boundary adjacency, non-UTC-timezone bucketing, order independence via a seeded shuffle, the zero-traffic vacuous guard, and the naive-datetime `ValueError`.
- The invocation tally the gated harness compares against is recorded by the harness's own in-memory counter at attempt time — never re-derived from a second query against `tool_calls_audit` (T-19-11 guard against a parity check that could never fail).

## Task Commits

Each task was committed atomically:

1. **Task 1: Author the AUD-03 gated harness and its pure coverage-parity helper** - `fa62704` (test)
2. **Task 2: Prove the coverage-parity arithmetic without a database** - `8da213b` (test)

_No TDD RED/GREEN split — both tasks are pure test-file authorship with no production code under `apps/api/app/` touched; each commit's own test suite (skip-collection for Task 1, all-green for Task 2) was verified before committing._

## Files Created/Modified

- `apps/api/tests/integration/test_aud03_audit_gap.py` - AUD-03 gated harness + `compute_audit_gap`/`AUDIT_WINDOW_DAYS`
- `apps/api/tests/unit/test_audit_gap_arithmetic.py` - 11 DB-free unit tests proving the arithmetic

## Decisions Made

- **Actor-skip tuning avoids a live-Anthropic-key requirement.** `call_actor_gate` is not short-circuited by `red_team_mode()` — it makes a real Haiku call unless `capability_snapshot.requires_confirmation is False AND max_amount_cents < settings.ACTOR_SKIP_MAX_AMOUNT_CENTS`. The harness's `place_order` envelope override sets `max_amount_cents = settings.ACTOR_SKIP_MAX_AMOUNT_CENTS - 1` (mirroring the 19-02 demo tenant's own R4.99 construction) so the "success" call per batch never needs a live `ANTHROPIC_API_KEY`, matching the gate's own skip reason text (Postgres + Redis only).
- **`place_order`/`issue_refund` rate limits raised to `1000/hour` in the harness's own envelope fixture.** 30 batches run in real wall-clock minutes would otherwise share one Redis rate window under `CLEAN_TENANT_ENVELOPES`'s stock `5/hour`/`2/hour` limits, letting an incidental rate-limit denial mask the intended `max_amount_cents` denial. Parity would still have held either way (any rejection still writes exactly one audit row), but the raised limits make the intended denial reason actually fire, matching the plan's "deterministic from configuration" framing.
- **`book_slot` deliberately forced `enabled=False`** in the harness's own envelope fixture (none of `CLEAN_TENANT_ENVELOPES`'s six rows ship disabled) — the concrete config-driven capability-denial branch each batch exercises.
- **A verified session is established once at fixture setup** (a real `customer_identities` row + `hash_session_token`, mirroring `test_value_bound_evasion`'s pattern in `test_red_team_rtx.py`) so `issue_refund`'s `requires_identity_verification=True` row reaches Step 4 (the rate/constraint check) rather than being blocked earlier at Step 2.5 (IDV) — required for the over-ceiling refund to actually exercise the `max_amount_cents` denial path the plan names.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Moved `app.services.red_team_probe` imports from module scope to lazy (function-body) imports**
- **Found during:** Task 1, running the plan's own `<verify>` bare-import command (`python -c "import tests.integration.test_aud03_audit_gap as m; ..."`).
- **Issue:** The plan's `<action>` text specified module-level imports of `app.services.red_team_probe` symbols ("standard library, pytest, and app.services.red_team_probe symbols only"). `red_team_probe.py` imports `app.core.config`, whose module executes `settings = Settings()` eagerly at import time — this raised `pydantic_core.ValidationError` for the missing `PLATFORM_CREDENTIAL_KEY` field when run outside pytest, because `tests/conftest.py`'s `os.environ.setdefault(...)` calls (which supply that value for the test suite) hadn't run yet. Under `pytest`, the root `conftest.py` is imported first, so this was invisible in the `pytest -q` run but broke the plan's own second `<automated>` verify line.
- **Fix:** Moved the `CLEAN_TENANT_ENVELOPES` / `CLEAN_TENANT_SPEC` / `invoke_probe_tool` / `red_team_mode` imports out of module scope and into the specific fixture/test function bodies that use them (`_aud03_envelope_rows`, `aud03_tenant`, `test_zero_audit_gaps_across_synthetic_30_day_window`). This is the same lazy-import discipline `test_red_team_rtx.py` already uses for exactly this reason (confirmed by re-reading its imports — it never imports `red_team_probe` at module level either).
- **Files modified:** `apps/api/tests/integration/test_aud03_audit_gap.py` (also updated the module docstring's "Module-level import discipline" paragraph to state and explain the corrected rule).
- **Verification:** `python -c "import tests.integration.test_aud03_audit_gap as m; assert m.AUDIT_WINDOW_DAYS == 30; assert callable(m.compute_audit_gap); print('AUD03-MODULE-OK')"` now completes in ~1.5s with no error; `pytest tests/integration/test_aud03_audit_gap.py -q` still reports 1 skipped, 0 errors.
- **Committed in:** `fa62704` (Task 1 commit — the fix was applied before the task's first commit, not as a follow-up).

---

**Total deviations:** 1 auto-fixed (1 blocking).
**Impact on plan:** Necessary to satisfy the plan's own bare-import acceptance criterion in this environment; strictly improves the module's stated goal ("keep the module level cheap and DB-free") rather than working against it. No scope creep — no file outside the plan's two named artifacts was touched.

## Issues Encountered

- The plan's `<read_first>` correctly identified that `test_red_team_rtx.py` never imports `app.services.red_team_probe` at module scope, but the `<action>` text for this plan explicitly directed module-level imports of `red_team_probe` symbols. Re-reading the analog file more carefully during verification (rather than trusting the action text's summary) surfaced the actual reason for that omission — see Deviation 1 above.

## User Setup Required

None - no external service configuration required. The gated harness needs real local Postgres and Redis to actually run (per its `INTEGRATION_TESTS_ENABLED=1` skip reason); that live run is deferred to `19-05-PLAN.md`'s operator gate, not a setup step for this plan.

## Next Phase Readiness

- Both new test files are committed, collect cleanly, and the full unit suite is green at 1122 passed / 8 skipped / 0 failed (1111 baseline from 19-02 + 11 new unit tests), strictly above the plan's required floor.
- `apps/api/pyproject.toml` and `apps/api/app/services/transactional/audit.py` are byte-identical to before this plan (verified via `git diff --quiet`) — no dependency added, no production write path touched.
- **Outstanding:** the gated `test_zero_audit_gaps_across_synthetic_30_day_window` itself has never been run against a real Postgres/Redis in any environment (this one included) — its correctness rests on careful source-reading (dispatcher enforcement order, Actor skip condition, rate-limit windowing) rather than an executed proof. `19-05-PLAN.md` Task 1 is where the operator runs it for real and records the per-day table in `19-UAT.md`; this SUMMARY's `verification: backstop` coverage entry (D3) reflects that honestly rather than claiming it passed.

---
*Phase: 19-documentation-v1-1-verification*
*Completed: 2026-07-28*

## Self-Check: PASSED

- FOUND: apps/api/tests/integration/test_aud03_audit_gap.py
- FOUND: apps/api/tests/unit/test_audit_gap_arithmetic.py
- FOUND: .planning/phases/19-documentation-v1-1-verification/19-03-SUMMARY.md
- FOUND commit: fa62704
- FOUND commit: 8da213b
