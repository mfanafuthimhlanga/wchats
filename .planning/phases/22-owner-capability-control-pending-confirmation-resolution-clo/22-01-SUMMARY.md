---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
plan: 01
subsystem: api
tags: [capability-envelopes, tighten-only, comparator, python, pytest]

# Dependency graph
requires:
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: "validate_tighten_only comparator, PLATFORM_CAPABILITY_DEFAULTS, the PATCH capability-envelopes route (18-08)"
provides:
  - "enabled: False -> True is now a legal, owner-reachable PATCH transition for every mutating skill"
  - "The five other tighten-only dimensions (rate_limit, ceiling, requires_confirmation, requires_identity_verification, actor_mode) proven bit-for-bit unchanged"
affects: [22-04, 22-05, owner-capability-guide.md]

tech-stack:
  added: []
  patterns:
    - "Guard-removal (mutation) demonstration as a runnable <verify> step: mutate, assert red, restore from HEAD unconditionally, assert green"
    - "Diff-scope gate: grep the real committed diff for sibling-branch tokens to mechanically bound an over-application risk"

key-files:
  created: []
  modified:
    - apps/api/app/services/capability_service.py
    - apps/api/tests/unit/test_capability_routes.py
    - apps/api/tests/unit/test_capability_service.py

key-decisions:
  - "Kept the empty `if \"enabled\" in proposed: pass` branch (with explanatory comment) rather than deleting the guard entirely, per explicit plan instruction — the named branch makes the removed-rule decision visible at the exact site a future reader would otherwise have to re-derive it. The project's ruff config (select = [\"E\", \"F\", \"I\"]) does not include simplify rules that would flag `if X: pass`, so this carries no lint cost."
  - "Fixed apps/api/tests/unit/test_capability_service.py (outside the plan's stated files_modified) as a Rule-1 auto-fix: two Phase-18 assertions hard-coded the pre-CAP-05 rejection (`== \"loosen_enabled\"`), which is now false by design. Left as-is, the plan's own full-suite success gate (0 failed) would not be met. Only the `enabled`-specific assertions and one loosening-direction demo payload were touched; every other assertion in the file is untouched."

requirements-completed: [CAP-05]

coverage:
  - id: D1
    description: "validate_tighten_only accepts enabled: False->True and True->False unconditionally, with no platform-default gate"
    requirement: "CAP-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_capability_service.py::test_validate_tighten_only"
        status: pass
      - kind: unit
        ref: "tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_enables_a_disabled_skill_for_every_mutating_skill"
        status: pass
    human_judgment: false
  - id: D2
    description: "Enabling a skill via PATCH changes no other field on the row (route-level proof)"
    requirement: "CAP-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_enable_does_not_change_any_other_field"
        status: pass
    human_judgment: false
  - id: D3
    description: "A first PATCH write of {enabled: true} against a skill with no stored row creates the row at platform defaults on every other field"
    requirement: "CAP-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_first_write_enable_creates_row_at_platform_defaults"
        status: pass
    human_judgment: false
  - id: D4
    description: "A legal enabled change cannot smuggle an illegal sibling field change through the fixed branch order"
    requirement: "CAP-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_enable_plus_illegal_other_field_still_rejected"
        status: pass
    human_judgment: false
  - id: D5
    description: "PLATFORM_CAPABILITY_DEFAULTS is untouched — every entry still ships enabled=False (fail-closed provisioning posture)"
    requirement: "CAP-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_capability_routes.py::test_platform_defaults_still_ship_every_skill_disabled"
        status: pass
    human_judgment: false
  - id: D6
    description: "The other five tighten-only dimensions (rate_limit, ceiling, requires_confirmation, requires_identity_verification, actor_mode) reject every loosening direction, unmodified"
    requirement: "CAP-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_rejects_each_loosening_field (5 parametrize cases)"
        status: pass
    human_judgment: false
  - id: D7
    description: "The ceiling guard-removal demonstration: mutating validate_tighten_only's constraints branch turns the ceiling tests red; restoring from HEAD turns them green"
    requirement: "CAP-05"
    verification:
      - kind: other
        ref: "manual mutation run — see 'Guard-Removal Demonstration' section below for real red/green output"
        status: pass
    human_judgment: false

duration: 46min
completed: 2026-07-28
status: complete
---

# Phase 22 Plan 01: CAP-05 enabled-toggle backend closure Summary

**Removed the permanently-False platform-default gate from `validate_tighten_only`'s `enabled` branch, making `enabled: False -> True` an owner-reachable PATCH transition for all six mutating skills, while proving the other five tighten-only dimensions are bit-for-bit unchanged via a diff-scope gate and an observed red-then-green ceiling-guard mutation.**

## Performance

- **Duration:** 46 min (commit-to-commit; session was interrupted once by an API connection error mid-Task-2 and resumed from the coordinator's tree assessment)
- **Started:** 2026-07-28T15:46:53+02:00 (Task 1 commit)
- **Completed:** 2026-07-28T16:32:10+02:00 (final fix commit)
- **Tasks:** 2/2 planned, plus 1 unplanned Rule-1 fix
- **Files modified:** 3

## Accomplishments

- `validate_tighten_only`'s `enabled` branch no longer reads `default_entry` or rejects — both directions (`False->True`, `True->False`) return `None` unconditionally. The docstring's `enabled:` bullet was corrected to state the shipped rule.
- Diff-scope gate run against the real committed diff of `capability_service.py`: zero hits for any of the five sibling branches' verdict/parser tokens or `PLATFORM_CAPABILITY_DEFAULTS`. `PLATFORM_CAPABILITY_DEFAULTS`, `pyproject.toml`, and `apps/api/alembic/` are all byte-unchanged.
- `test_capability_routes.py`: the `enabled` case retired from `test_patch_rejects_each_loosening_field`'s parametrize (6 -> 5 ids, ids: `rate_limit`, `max_amount_cents`, `requires_confirmation`, `requires_identity_verification`, `actor_mode`); four new CAP-05 tests added (`test_patch_enables_a_disabled_skill_for_every_mutating_skill` parametrised over all six mutating skills, `test_patch_enable_does_not_change_any_other_field`, `test_patch_first_write_enable_creates_row_at_platform_defaults`, `test_enable_plus_illegal_other_field_still_rejected`) plus module-scope `test_platform_defaults_still_ship_every_skill_disabled`.
- **Guard-removal demonstration actually executed** (not merely asserted in prose — see full output below): mutating the ceiling guard (`if proposed_max > current_max:` -> `if False:`) turned `test_patch_rejects_each_loosening_field[max_amount_cents]` and `test_enable_plus_illegal_other_field_still_rejected` red (2 failed); restoring `capability_service.py` from `HEAD` via `git checkout --` (unconditional, before the pass/fail assertion) turned both green again.
- Full unit suite: **1145 passed, 8 skipped, 0 failed** (baseline 1136 passed) — above baseline, per `<verification>`.

## Guard-Removal Demonstration (real output)

Applied mutant (`app/services/capability_service.py`, constraints branch):
```
MUTANT-APPLIED
```
Ran `pytest tests/unit/test_capability_routes.py -q -k "max_amount or illegal_other_field"` against the mutant:
```
tests\unit\test_capability_routes.py:356: AssertionError  (test_patch_rejects_each_loosening_field[max_amount_cents])
tests\unit\test_capability_routes.py:566: AssertionError  (test_enable_plus_illegal_other_field_still_rejected)
    assert response.status_code == 422
E   assert 200 == 422
FAILED tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_rejects_each_loosening_field[max_amount_cents]
FAILED tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_enable_plus_illegal_other_field_still_rejected
2 failed, 25 deselected in 39.09s
```
Restored `apps/api/app/services/capability_service.py` from `HEAD` (`git checkout --`, run unconditionally, before any pass/fail assertion — a mid-way failure in this sequence cannot leave the tree dirty). Confirmed clean: `git status --short` / `git diff --stat` on the file returned nothing.
Re-ran the same selection against the restored file:
```
..                                                                       [100%]
2 passed, 25 deselected in 37.54s
T-22-CAP-01-GUARD-DEMONSTRATED
```
Red-then-green observed directly, not inferred.

## Task Commits

Each task was committed atomically:

1. **Task 1: Remove the platform-default gate from validate_tighten_only's enabled branch** - `618d705` (fix)
2. **Task 2: Re-point the comparator test suite and prove the other five dimensions unchanged** - `38d5d4f` (test)
3. **Unplanned Rule-1 fix: update Phase-18 comparator tests for CAP-05's enabled rule** - `45ad4c8` (fix)

_Plan metadata commit follows this SUMMARY._

## Files Created/Modified

- `apps/api/app/services/capability_service.py` — `validate_tighten_only`'s `enabled` branch gate removed; docstring `enabled:` bullet corrected
- `apps/api/tests/unit/test_capability_routes.py` — `enabled` retired from the loosening parametrize; 4 new CAP-05 tests + 1 module-scope fail-closed-posture test added
- `apps/api/tests/unit/test_capability_service.py` — (unplanned) two Phase-18 assertions updated to the CAP-05 rule; not in this plan's stated `files_modified`, added as a Rule-1 auto-fix (see Deviations)

## Decisions Made

- **Kept the empty `if "enabled" in proposed: pass` branch.** The plan explicitly required not deleting the guard ("a silently absent branch reads as an oversight") and the project's ruff config (`select = ["E", "F", "I"]`) has no simplify rule that would flag it. The coordinator raised this as a design question mid-session; judgement was to follow the plan's explicit instruction rather than override it, since the rationale (visibility at the exact removed-rule site) is sound and the plan itself pre-empted the "future reader cleans it up" concern.
- **Fixed `test_capability_service.py` even though it's outside the plan's `files_modified`.** This is a Rule-1 auto-fix, not scope creep into a second loosening case in `test_capability_routes.py` (which the plan explicitly prohibited and which was NOT touched). The pre-existing file asserted the exact rule this plan intentionally removes; leaving it red would fail the plan's own `<verification>` full-suite gate.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Two Phase-18 assertions in test_capability_service.py asserted the removed `loosen_enabled` rejection**
- **Found during:** Task 2 full-suite verification (`pytest tests/unit -q ...`)
- **Issue:** `test_validate_tighten_only` and `test_tighten_only_enforced_below_route` both hard-coded `validate_tighten_only({"enabled": False}, {"enabled": True}) == "loosen_enabled"` — the exact rejection Task 1 intentionally removed. This file is not in `22-01-PLAN.md`'s `files_modified` list; the plan's own scope statement ("this plan touches only capability_service.py and test_capability_routes.py") did not anticipate this pre-existing Phase-18 test file.
- **Fix:** Updated the `enabled` assertions in `test_validate_tighten_only` to `is None` for both directions (matching the shipped rule), with an updated docstring ("five of the six comparable fields..."). Updated `test_tighten_only_enforced_below_route`'s HTTP-machinery-free demonstration payload from `enabled` (no longer a loosening direction) to `rate_limit` (still `loosen_rate_limit`), preserving the test's original intent (prove a direct call returns a reason string with no HTTP object involved).
- **Files modified:** `apps/api/tests/unit/test_capability_service.py`
- **Verification:** `pytest tests/unit/test_capability_service.py -q` → 15 passed. Full suite → 1145 passed, 8 skipped, 0 failed.
- **Committed in:** `45ad4c8`

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug: stale test assertion, direct consequence of the intentional Task 1 change)
**Impact on plan:** Necessary for the plan's own full-suite success gate. No scope creep — no second `enabled`/loosening case was reopened in `test_capability_routes.py`, and every other assertion in `test_capability_service.py` (rate_limit, ceiling, requires_confirmation, requires_identity_verification, actor_mode, mixed-payload, no-op, drift, hash tests) is byte-identical.

## Issues Encountered

- The session was interrupted once by an API connection error mid-way through the Task 2 guard-removal demonstration. The mutation-and-restore sequence had already completed correctly and cleanly at the point of interruption (verified via `git status`/`git diff` after resuming — `capability_service.py` was clean vs `HEAD`); the final green re-confirmation and `T-22-CAP-01-GUARD-DEMONSTRATED` echo were re-run explicitly on resume to produce a complete, directly-observed red-then-green record rather than relying on the pre-interruption partial output.
- First diff-scope gate run failed on a false-positive-by-design self-check: the `enabled` branch's own explanatory comment used the literal string `PLATFORM_CAPABILITY_DEFAULTS` (documenting *why* the gate was removed), which the gate — correctly — flags as any added/removed line matching that token. Reworded the comment to say "platform-default entry" instead of the literal constant name, preserving the same explanation without tripping the mechanical scope check. This is the gate working as designed, not a gate defect.

## Next Phase Readiness

- CAP-05's backend half is closed. `docs/guides/owner-capability-guide.md` still states the pre-CAP-05 rule (per Phase 19's CR-01 fix) and is now factually false — this falsification is explicitly owned by plan `22-05` in the same phase, per `STATE.md`'s Phase 22 planning note.
- Plans `22-02` through `22-06` (ACT-07 resolver substrate, resolve route, admin UI, docs correction, closure) are unblocked and depend on nothing this plan left incomplete. No `files_modified` overlap with this plan.
- No migration, no new dependency, `pyproject.toml` byte-unchanged — confirmed mechanically, not asserted.

---
*Phase: 22-owner-capability-control-pending-confirmation-resolution-clo*
*Completed: 2026-07-28*

## Self-Check: PASSED

All created/modified files exist on disk (`capability_service.py`,
`test_capability_routes.py`, `test_capability_service.py`, this SUMMARY.md).
All three commit hashes (`618d705`, `38d5d4f`, `45ad4c8`) resolve in
`git log --oneline --all`.
