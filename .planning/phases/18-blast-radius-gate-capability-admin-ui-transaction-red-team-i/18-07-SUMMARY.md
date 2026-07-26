---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 07
subsystem: api
tags: [envelope-hash, sha256, deployment-gate, blast-radius, fail-closed, sqlalchemy]

# Dependency graph
requires:
  - phase: 18-01
    provides: "checklist_runs.envelope_hash / envelope_acknowledged_at columns (migration 0019)"
  - phase: 18-04
    provides: "capability_service.HASHED_ENVELOPE_FIELDS, canonical_envelope_hash, envelope_drift — caller-free by design"
  - phase: 18-05
    provides: "deployment_service.py's get_sync_db/text import block and _fetch_blast_radius_sync collector shape this plan's reader matches"
provides:
  - "deployment_service._fetch_envelope_rows_sync / _compute_envelope_hash_sync — the sync envelope-hash reader, called from run_deployment_checklist Step 4/6"
  - "api.v1.deployment._fetch_envelope_rows / _current_envelope_hash — the async twin, called from the list route, detail route, and approve_deployment"
  - "POST /approve-deployment's fourth 422 validation (envelope drift, including a NULL recorded hash)"
  - "run.envelope_acknowledged_at stamped inside the existing approve call — no new endpoint"
  - "envelope_drift surfaced on both checklist reads (list, detail), computed once per request"
affects: [18-08, 18-10, 18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Sync/async twin readers projecting the identical field set at the query layer (never SELECTing id/agent_id/updated_at) and delegating hashing to one shared canonicaliser, so two independent call sites can never disagree on a hash"
    - "Fail-closed 4th validation appended after existing 422 checks, not inserted ahead of them, so a more severe pre-existing gate is never masked"

key-files:
  created: []
  modified:
    - apps/api/app/services/deployment_service.py
    - apps/api/app/worker/tasks/runtime/deployment.py
    - apps/api/app/api/v1/deployment.py
    - apps/api/tests/unit/test_deployment_service.py
    - apps/api/tests/unit/test_deployment_task.py
    - apps/api/tests/unit/test_deployment_routes.py

key-decisions:
  - "Both readers (sync in deployment_service.py, async in api/v1/deployment.py) project exactly capability_service.HASHED_ENVELOPE_FIELDS at the SQL/SQLAlchemy query layer, excluding id/agent_id/updated_at from the SELECT column list — a stronger, structural guarantee than trusting the canonicaliser to drop them after the fact (OD-2 / Pitfall 2)"
  - "envelope_hash is computed in its own guarded try/except block in Step 4, separate from the signals dict — it is persisted directly onto the checklist run, not narrated to the Sonnet orchestrator"
  - "The checklist task never stamps envelope_acknowledged_at — only approve_deployment does, because acknowledgement is the owner's act at approve time, not the platform's at checklist time"
  - "The fourth approve-time validation is appended after the three shipped 422 checks (status/block/warnings), never inserted ahead of them, so a blocked or incomplete run still reports its own more severe 422 first"
  - "_run_to_dict's live_envelope_hash parameter defaults to None and reports envelope_drift=True in that case — an unsupplied live hash is never evidence of a match, matching envelope_drift's own fail-closed direction for a missing recorded hash"

patterns-established:
  - "Pattern: sync/async twin envelope-hash readers — the checklist task and the approve route can never disagree on what 'the current envelope' hashes to because both delegate to the single capability_service.canonical_envelope_hash"

requirements-completed: [BLR-02, CAP-04]

coverage:
  - id: D1
    description: "A completed checklist run records the envelope hash that was live when it ran, in the same transaction as its status (envelope_hash persisted in run_deployment_checklist Step 6, before db.commit())"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_service.py::test_envelope_hash_stability"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_service.py::TestEnvelopeHashSync (3 tests)"
        status: pass
    human_judgment: false
  - id: D2
    description: "POST /approve-deployment returns 422 when the live envelope hash differs from the run's recorded hash, asserted at the route via a real ASGI request, with the agent's is_deployed flag proven still False"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_routes.py::test_approve_deployment_envelope_drift_422"
        status: pass
    human_judgment: false
  - id: D3
    description: "A run with a NULL envelope_hash cannot be approved — an absent acknowledgement is drift, not a match"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_routes.py::TestApproveDeployment::test_approve_rejects_run_with_null_envelope_hash"
        status: pass
    human_judgment: false
  - id: D4
    description: "The new envelope-drift check is appended after the three shipped 422 validations, not inserted ahead of them — a blocked run still reports the blocked-deployment detail, never the envelope detail"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_routes.py::TestApproveDeployment::test_envelope_drift_check_runs_after_the_three_existing_validations"
        status: pass
    human_judgment: false
  - id: D5
    description: "A successful approval stamps envelope_acknowledged_at alongside approved_at/approved_by, and the audit log records the approved envelope_hash"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_routes.py::TestApproveDeployment::test_approve_succeeds_when_envelope_hash_matches"
        status: pass
    human_judgment: false
  - id: D6
    description: "Both checklist reads (list, detail) surface envelope_drift, computed from exactly one live-envelope-hash query per request (not once per run)"
    requirement: "CAP-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_routes.py::TestChecklistReadEnvelopeDrift (3 tests)"
        status: pass
    human_judgment: false
  - id: D7
    description: "The Phase 21 red-team block gate and the existing warning-acknowledgement gate are unchanged and still fire; the IDOR pattern and the acknowledge route are untouched"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_deployment_routes.py::TestApproveDeployment::test_approve_rejects_blocked, TestAcknowledge::test_approve_blocked_when_warnings_unacked (both pre-existing, still pass)"
        status: pass
    human_judgment: false

# Metrics
duration: ~25min
completed: 2026-07-27
status: complete
---

# Phase 18 Plan 07: BLR-02 Envelope-Hash Deploy Gate Summary

**Wires 18-04's caller-free `canonical_envelope_hash`/`envelope_drift` into the deploy pipeline: the checklist task persists a sha256 envelope hash on every completed run, `POST /approve-deployment` gains a fourth fail-closed 422 (drift or a NULL recorded hash), the approve call stamps `envelope_acknowledged_at`, and both checklist reads surface `envelope_drift` from one live-hash query per request.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-27T~01:20Z
- **Completed:** 2026-07-27T01:47:49+02:00
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments
- `deployment_service._fetch_envelope_rows_sync` / `_compute_envelope_hash_sync`: the sync control-DB reader, projecting exactly the seven `HASHED_ENVELOPE_FIELDS` at the query layer (id/agent_id/updated_at never in the SELECT list) and delegating hashing to `capability_service.canonical_envelope_hash` — never re-implementing it.
- `run_deployment_checklist` Step 4 gains a sixth guarded collector computing the envelope hash (None on failure — fail-closed, since `envelope_drift` treats an absent hash as drift); Step 6 persists it in the same transaction as status/report/warnings. The task never stamps `envelope_acknowledged_at`.
- `api.v1.deployment._fetch_envelope_rows` / `_current_envelope_hash`: the async twin, byte-for-byte equivalent field projection to the sync reader.
- `_run_to_dict` gains `envelope_hash`, `envelope_acknowledged_at`, and `envelope_drift` (fail-closed to `True` when no live hash is supplied). Both checklist reads compute the live hash once per request, not once per run.
- `approve_deployment` gains a fourth validation — positioned after the `ship_with_warnings` check and before the mutation — raising 422 on drift or a NULL recorded hash. On success, `run.envelope_acknowledged_at` is stamped alongside `approved_at`/`approved_by`, and the audit log records `envelope_hash`.
- 11 new unit tests: `test_envelope_hash_stability` + `TestEnvelopeHashSync` (4, sync side); `test_approve_deployment_envelope_drift_422` (module scope, asserted at the route through `ASGITransport`) + 3 more `TestApproveDeployment` cases + `TestChecklistReadEnvelopeDrift` (3) (7, route side).

## Task Commits

Each task was committed atomically:

1. **Task 1: Sync envelope-hash reader + persistence on the checklist run** - `3618596` (feat)
2. **Task 2: Approve-time 422 gate, acknowledgement stamping, envelope_drift on checklist reads** - `cb79167` (feat)
3. **Task 3: Route-level 422 test — asserted at the route, not below it** - `0069012` (test)

_No plan-metadata commit yet — this SUMMARY.md and STATE.md updates are committed separately per the final_commit step._

## Files Created/Modified
- `apps/api/app/services/deployment_service.py` - `_fetch_envelope_rows_sync`, `_compute_envelope_hash_sync`; imports `canonical_envelope_hash`
- `apps/api/app/worker/tasks/runtime/deployment.py` - Step 4 sixth guarded collector (envelope hash); Step 6 persists `run_obj.envelope_hash`; docstrings updated
- `apps/api/app/api/v1/deployment.py` - `_fetch_envelope_rows`, `_current_envelope_hash`; `_run_to_dict` extended; list/detail routes compute live hash once; `approve_deployment` gains the fourth 422 + acknowledgement stamp + audit log field
- `apps/api/tests/unit/test_deployment_service.py` - `test_envelope_hash_stability` (module scope) + `TestEnvelopeHashSync` (3 methods)
- `apps/api/tests/unit/test_deployment_task.py` - `_compute_envelope_hash_sync` patched in the 6 pre-existing tests that reach Step 4 (deviation, see below)
- `apps/api/tests/unit/test_deployment_routes.py` - `_make_complete_checklist_run`/`_make_mock_db` extended with envelope kwargs; `test_approve_deployment_envelope_drift_422` (module scope) + 6 more new tests; 1 pre-existing test updated to supply a matching hash (deviation, see below)

## Decisions Made
- Both envelope-hash readers exclude `id`/`agent_id`/`updated_at` at the SQL/SQLAlchemy SELECT layer, not merely by trusting the canonicaliser to drop them — a stronger, structural guarantee against a non-semantic column ever reaching the hash (OD-2, Pitfall 2). Plan-specified, no deviation.
- The envelope hash is computed as its own guarded block in Step 4, deliberately separate from the `signals` dict passed to the Sonnet orchestrator — it is a persisted audit field, not a narrative quality signal. Plan-specified.
- The fourth approve-time validation is appended strictly after the three shipped 422 checks, never inserted ahead of them, so a blocked/incomplete run's own more severe 422 is never masked by the new envelope check — proven by `test_envelope_drift_check_runs_after_the_three_existing_validations`. Plan-specified.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Patched `_compute_envelope_hash_sync` in the 6 pre-existing `test_deployment_task.py` tests that reach Step 4**
- **Found during:** Task 1 verification (`pytest tests/unit/test_deployment_service.py tests/unit/test_deployment_task.py`)
- **Issue:** `test_deployment_task.py` is not in this plan's `files_modified` list, but Step 4's new envelope-hash collector opens its own `get_sync_db()` session inside `deployment_service.py` (not the module-level `get_sync_db` those tests already patch) — exactly the situation the file's own docstring already documents for `_fetch_blast_radius_sync`. Left unpatched, all 6 tests that reach Step 4 attempted a real connection to `localhost:5432`; Step 4's own guarded `try/except` caught the failure so the tests still passed, but the module went from ~5s to 28s.
- **Fix:** Added `patch("app.worker.tasks.runtime.deployment._compute_envelope_hash_sync", return_value="test-envelope-hash")` alongside the existing `_fetch_blast_radius_sync` patch in each of the 6 affected tests, mirroring the file's own established convention. Updated the module docstring to document the new pattern.
- **Files modified:** `apps/api/tests/unit/test_deployment_task.py`
- **Verification:** `pytest tests/unit/test_deployment_task.py -q` — 7 passed in 4.68s (down from 28.09s unpatched)
- **Committed in:** `3618596` (Task 1 commit)

**2. [Rule 1 - Bug] Supplied a matching `envelope_hash` to the pre-existing `test_approve_sets_is_deployed_true`**
- **Found during:** Task 3, running the full route-test file after extending `_make_mock_db`
- **Issue:** This DEP-06 test (unmodified pre-Task-3) constructed a checklist run with no `envelope_hash` at all. Under the new fail-closed BLR-02 gate — "a run with a NULL envelope_hash cannot be approved" — the test's expected `200`/`deployed=True` outcome would now correctly 422, because a historical run with no recorded hash is drift by design (T-18-BLR-02), not a pre-existing bug this plan introduced.
- **Fix:** Added `envelope_hash=canonical_envelope_hash([])` to the test's `_make_complete_checklist_run(...)` call, matching the mock db's default empty `envelope_rows`, so the pre-existing DEP-06 assertion (approve succeeds, `is_deployed=True`) is proven true under a matching hash rather than an absent one — the test's original intent is unchanged, only the fixture now supplies the input the new fail-closed gate requires to distinguish "hash matches" from "hash absent."
- **Files modified:** `apps/api/tests/unit/test_deployment_routes.py`
- **Verification:** `pytest tests/unit/test_deployment_routes.py::TestApproveDeployment::test_approve_sets_is_deployed_true -x` passes
- **Committed in:** `0069012` (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — bugs/necessary corrections surfaced by the new fail-closed gate against pre-existing test fixtures, not introduced by this plan's design)
**Impact on plan:** Both fixes were required to keep the full suite green under the plan's own must_haves ("a NULL envelope_hash cannot be approved") without weakening the fail-closed gate. No scope creep — no source behavior was changed to accommodate either fix, only test fixtures.

## Issues Encountered
None beyond the two deviations above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The cross-wave seam plan 18-04 shipped caller-free is now fully wired: `canonical_envelope_hash` has two real call sites (checklist task, approve route) and `envelope_drift` has three (checklist read, approve gate). This plan's own acceptance criteria asserted every call site exists, per the phase's explicit correction of the Phase 21 `promote_trace_to_scenario` failure pattern.
- Full unit suite: 1063 → 1074 passed (+11), 8 skipped, 0 failed. `apps/api/pyproject.toml` unchanged (`git diff --exit-code` exits 0) — no new dependency.
- The end-to-end live round trip (apply migration 0019 to a real control DB, run a checklist, edit an envelope, confirm `POST /approve-deployment` returns 422 against real data) remains deferred to plan 18-11 (`autonomous: false`), consistent with 18-01's stated constraint — no live Neon DB currently holds any v1.2 migration.
- No blockers for the next plan in the wave sequence (18-08, CAP-03's PATCH route wiring `validate_tighten_only`).

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-27*
