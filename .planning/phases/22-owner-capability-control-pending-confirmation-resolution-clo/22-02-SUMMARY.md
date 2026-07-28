---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
plan: 02
subsystem: api
tags: [transactional-dispatcher, confirmation-resolution, idempotency, capability-envelope, python, pytest]

# Dependency graph
requires:
  - phase: 22-owner-capability-control-pending-confirmation-resolution-clo
    plan: 01
    provides: "enabled: False -> True as a legal owner-reachable capability transition (CAP-05) — this plan's resolver re-checks against that live envelope"
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: "check_capability_access / apply_rate_and_constraint_checks split, reserve_idempotency reservation engine, write_audit_row, pending_confirmations table"
provides:
  - "SKILL_INPUT_MODELS — the hand-written skill-to-Input-model map for the six mutating skills, confirm_action deliberately excluded"
  - "_execute_adapter_and_audit — steps 6-7 (adapter execute + audit + finalize) extracted once, called by both the live-turn dispatcher and the resolver"
  - "confirmation_resolution.execute_approved_confirmation — ACT-07's terminating execution path for a human approval, re-running capability/idempotency/rate checks against the LIVE envelope and skipping only the IDV gate and the Actor seam, both enforced by source-absence tests"
affects: [22-03, 22-04]

tech-stack:
  added: []
  patterns:
    - "Shared enforcement-step extraction: one function (_execute_adapter_and_audit) called from two entry points (live-turn dispatcher, out-of-band resolver) instead of two hand-maintained copies"
    - "Source-absence regression test (18-05 pattern) for a security-relevant skip: read the module file from disk, assert a forbidden symbol appears zero times, with a diagnostic message naming the symbol and the reason its presence is a defect"
    - "Guard-removal (mutation) demonstration as a runnable, restored-from-HEAD verification step — applied to both a live-envelope re-check and an absence assertion in the same plan"
    - "Explicit keyword-only parameter contract in place of ContextVar seeding for code that runs outside a live agent turn (OD-5)"

key-files:
  created:
    - apps/api/app/services/transactional/confirmation_resolution.py
    - apps/api/tests/unit/test_confirmation_resolution.py
  modified:
    - apps/api/app/services/transactional/schemas.py
    - apps/api/app/services/transactional/tools.py
    - apps/api/tests/unit/test_transactional_tools.py

key-decisions:
  - "SKILL_INPUT_MODELS omits confirm_action deliberately — it has no adapter method and no idempotency_key (mutating=False), so including it would make an unexecutable skill look executable to the resolver. Its key set is asserted equal to the registry's mutating set by a unit test."
  - "apply_rate_and_constraint_checks is called with the raw arguments dict (not the validated Pydantic model), mirroring the live-turn dispatcher's own call site exactly (tools.py:514 passes raw_args, not validated) rather than 'fixing' what looks like a docstring/behavior mismatch — behavior parity with the shipped dispatcher was the explicit requirement, not a bug hunt into enforcement.py's constraint-check internals."
  - "Fixed a pre-existing Phase-16 structural test (test_actor_gate_called_before_get_adapter_in_dispatcher) that string-scanned _execute_transactional_tool's source for an inline get_adapter_for_skill( call — Task 1's extraction intentionally removed that inline call, which the task's own acceptance criteria required. Rule-1 auto-fix: updated the test to assert call_actor_gate precedes the new _execute_adapter_and_audit( call site, and added a companion assertion that get_adapter_for_skill lives inside the extracted helper, preserving the exact invariant (Actor runs before the adapter) the test existed to prove."

requirements-completed: [ACT-07]

coverage:
  - id: D1
    description: "SKILL_INPUT_MODELS maps exactly the six mutating skills to their Input models; confirm_action is absent; key set is pinned equal to the registry's mutating set"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "inline verify script (Task 1) — set(SKILL_INPUT_MODELS) == mutating set, len == 6, confirm_action absent, issue_refund maps to IssueRefundInput"
        status: pass
    human_judgment: false
  - id: D2
    description: "Steps 6-7 (adapter execute + audit + finalize) extracted once into _execute_adapter_and_audit; the live-turn dispatcher calls it exactly once and no longer inlines adapter resolution"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_transactional_tools.py (89 tests, full file) + inline verify script (Task 1) asserting _execute_transactional_tool calls the helper exactly once"
        status: pass
    human_judgment: false
  - id: D3
    description: "A human approval drives the provider adapter exactly once on the healthy path"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_confirmation_resolution.py::TestApprovedExecution::test_approve_calls_adapter_exactly_once"
        status: pass
    human_judgment: false
  - id: D4
    description: "The resolver re-runs capability and rate/constraint checks against the LIVE capability_envelopes row, never a stored snapshot — a tightened ceiling or a disabled skill denies execution"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_confirmation_resolution.py::TestLiveEnvelope::test_tightened_ceiling_denies_execution, test_disabled_skill_denies_execution, test_live_envelope_is_read_not_snapshot"
        status: pass
      - kind: other
        ref: "manual mutation run — see 'Guard-Removal Demonstrations' section below for real red/green output (demonstration a)"
        status: pass
    human_judgment: false
  - id: D5
    description: "The resolver's module source contains zero references to the Actor seam function, the identity-verification session check, identity_service, agent_tools, build_tool_server, or any of the four dispatcher ContextVars"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_confirmation_resolution.py::TestResolverAbsence (3 tests)"
        status: pass
      - kind: other
        ref: "manual mutation run — see 'Guard-Removal Demonstrations' section below for real red/green output (demonstration b)"
        status: pass
    human_judgment: false
  - id: D6
    description: "Exactly one tool_calls_audit row is written on every terminal resolver outcome (executed, denied, invalid); replay and in_progress write zero, matching AUD-01 asymmetry"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_confirmation_resolution.py::TestApprovedExecution::test_audit_row_written_on_every_terminal_outcome (5 parametrized cases), TestIdempotency::test_replay_and_in_progress_never_execute, test_args_mismatch_denies_with_one_audit_row"
        status: pass
    human_judgment: false
  - id: D7
    description: "NULL stored arguments and a confirm_action-skill row are denied with a named reason and one audit row, never a crash and never an adapter call"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_confirmation_resolution.py::TestNullTolerance::test_null_arguments_denied_not_crashed, test_confirm_action_skill_is_not_executable"
        status: pass
    human_judgment: false
  - id: D8
    description: "A fresh idempotency reservation inside the resolver, keyed on the row's stored idempotency_key, so a redelivery finds replay or in_progress rather than executing twice"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_confirmation_resolution.py::TestIdempotency::test_replay_and_in_progress_never_execute (parametrized, 2 cases)"
        status: pass
    human_judgment: false

duration: 55min
completed: 2026-07-28
status: complete
---

# Phase 22 Plan 02: ACT-07 confirmation resolution core Summary

**Built `execute_approved_confirmation`, the steps-2/3/4/6/7 dispatcher subset that terminates a human approval — re-checking capability, idempotency and rate/ceiling against the LIVE envelope while skipping the Actor seam and the identity-verification gate, both skips enforced by literal source-absence tests, both proven by observed red-then-green guard-removal mutations.**

## Performance

- **Duration:** 55 min (approximate; commit-to-commit span for the three task commits was 31 min — 16:56:58 to 17:28:24 — plus discovery/read-first review before the first commit)
- **Started:** ~2026-07-28T16:33:00+02:00 (estimated, plan/context reading)
- **Completed:** 2026-07-28T17:28:24+02:00 (Task 3 commit)
- **Tasks:** 3/3 planned
- **Files modified:** 5 (2 created, 3 modified)

## Accomplishments

- `SKILL_INPUT_MODELS` added to `schemas.py`: a hand-written, definition-time map from each of the six mutating skills to its Input model, pinned equal to `{k for k, v in TOOL_REGISTRY.items() if v.mutating}` by a unit test, with `confirm_action` deliberately excluded (no adapter method, no idempotency key).
- `tools.py`'s inline steps 6-7 (adapter execute + audit row + finalize idempotency, formerly lines 487-577) extracted verbatim into `_execute_adapter_and_audit`, a keyword-only coroutine now called by both `_execute_transactional_tool` (the live-turn dispatcher) and the new resolver — one implementation instead of two hand-maintained copies (T-22-ACT-15). Pure refactor: the dispatcher's own 89-test file passes unchanged, and the full unit suite matched the pre-extraction baseline exactly (1145 passed, 8 skipped, 0 failed) before Task 3 added new tests.
- `confirmation_resolution.py` created: `execute_approved_confirmation(*, confirmation_id, agent_id, skill, arguments, conn_str) -> ResolutionOutcome`. Re-runs step 2 (`check_capability_access`), step 3 (`reserve_idempotency`, fresh), step 4 (`apply_rate_and_constraint_checks`) against the live envelope, then delegates to the shared `_execute_adapter_and_audit` for steps 6-7 with `decision="approved_by_human"`. Deliberately skips step 2.5 (IDV — OD-1, no customer session outside a live turn) and step 5 (the Actor seam — the human approval IS that verdict), both explained by concept only in the module docstring and enforced by literal absence of the forbidden symbols in the file's own source.
- `test_confirmation_resolution.py` created: 18 tests across `TestResolverAbsence`, `TestApprovedExecution`, `TestLiveEnvelope`, `TestNullTolerance`, and `TestIdempotency`. Every DB/Redis/network boundary mocked — patched at the names each module (`confirmation_resolution.py` and `tools.py`) imports them under, not where they are defined.
- **Both required guard-removal demonstrations actually executed** (real red-then-green output below), not merely asserted in prose.
- Full unit suite after all three tasks: **1162 passed, 8 skipped, 0 failed** (baseline after 22-01 was 1145 passed, 8 skipped, 0 failed — above baseline, per `<verification>`).

## Guard-Removal Demonstrations (real output)

### (a) Rate-and-constraint call hard-coded to no-denial (T-22-ACT-04)

Applied mutant (`app/services/transactional/confirmation_resolution.py`):
```
- rate_denial = await apply_rate_and_constraint_checks(agent_id, skill, snapshot, arguments)
+ rate_denial = None  # MUTANT: hard-coded no-denial result
```
Ran the targeted test against the mutant:
```
FAILED tests/unit/test_confirmation_resolution.py::TestLiveEnvelope::test_tightened_ceiling_denies_execution
AssertionError: assert 'executed' == 'denied'
1 failed in 23.02s
```
Restored `confirmation_resolution.py` from `HEAD` (`git checkout --`, run unconditionally before any pass/fail assertion). Confirmed clean via `git status --short`. Re-ran:
```
1 passed in 16.81s
```
Red-then-green observed directly.

### (b) Actor-seam symbol appended to the resolver module (T-22-ACT-05)

Applied mutant (appended one line to `confirmation_resolution.py`):
```
_MUTANT_ACTOR_REFERENCE = "call_actor_gate"
```
Ran the targeted absence test against the mutant:
```
FAILED tests/unit/test_confirmation_resolution.py::TestResolverAbsence::test_resolver_never_references_call_actor_gate
AssertionError: confirmation_resolution.py references call_actor_gate ...
1 failed in 30.70s
```
Restored `confirmation_resolution.py` from `HEAD` (`git checkout --`, unconditional). Confirmed clean. Re-ran:
```
1 passed in 24.30s
T-22-ACT-05-GUARD-DEMONSTRATED
```
Red-then-green observed directly.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add SKILL_INPUT_MODELS and extract steps 6-7 into a shared helper** - `503eb08` (refactor)
2. **Task 2: Build the narrow resolver — steps 2/3/4/6/7 against the live envelope** - `69468fa` (feat)
3. **Task 3: Prove the resolver's absences — the tests that must be seen to fail** - `bbe6e40` (test)

_Plan metadata commit follows this SUMMARY._

## Files Created/Modified

- `apps/api/app/services/transactional/schemas.py` — added `SKILL_INPUT_MODELS`, the hand-written skill-to-Input-model map
- `apps/api/app/services/transactional/tools.py` — extracted steps 6-7 into `_execute_adapter_and_audit`; `_execute_transactional_tool` now delegates to it
- `apps/api/tests/unit/test_transactional_tools.py` — (unplanned, Rule-1) updated `TestFourNodeStructuralAssertion::test_actor_gate_called_before_get_adapter_in_dispatcher`, invalidated by the Task 1 extraction it required
- `apps/api/app/services/transactional/confirmation_resolution.py` — new: `ResolutionOutcome`, `execute_approved_confirmation`
- `apps/api/tests/unit/test_confirmation_resolution.py` — new: 18 tests proving the resolver's absences, live-envelope re-checks, audit symmetry, and idempotency asymmetry

## Decisions Made

- **`apply_rate_and_constraint_checks` called with the raw `arguments` dict, not the validated Pydantic model.** The live-turn dispatcher's own step 4 call site (`tools.py:514`, unchanged by this plan) passes `raw_args` — the raw dict — not `validated`, despite `enforcement.py`'s docstring describing a "Validated Pydantic input model" parameter. Matching the dispatcher's actual shipped call exactly (behavior parity being the explicit requirement for this plan) took priority over investigating or "fixing" that apparent docstring/implementation mismatch, which is out of this plan's scope and untouched by any test here.
- **Fixed `test_transactional_tools.py`'s `test_actor_gate_called_before_get_adapter_in_dispatcher`.** This pre-existing Phase-16 test string-scanned `_execute_transactional_tool`'s source for an inline `get_adapter_for_skill(` call to prove the Actor gate runs before adapter resolution. Task 1's extraction (required by this plan's own acceptance criteria: "`_execute_transactional_tool` no longer contains an inline `get_adapter_for_skill(` call") made that literal token absent from the dispatcher's own source by design. Updated the test to assert `call_actor_gate` precedes the dispatcher's new `_execute_adapter_and_audit(` call site, plus a companion assertion that `get_adapter_for_skill` lives inside the extracted helper — preserving the exact "Actor before adapter" invariant the test existed to prove, with the correct new evidence.
- **Adapter-await counting and audit-kwarg assertions patch at two separate module namespaces.** `confirmation_resolution.py` and `tools.py` each did their own `from app.services.transactional.audit import write_audit_row` (and similarly for `release_idempotency`), so each module's local binding had to be patched separately in tests — patching only `app.services.transactional.audit.write_audit_row` would not have intercepted either caller's already-bound reference. `test_confirmation_resolution.py`'s `_patch_resolver_boundary` context manager patches both `confirmation_resolution.*` (the resolver's own steps 1-6 audit writes) and `tools.*` (the shared helper's steps 6-7 audit write, get_adapter_for_skill, finalize_idempotency).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stale Phase-16 structural test invalidated by the required steps-6-7 extraction**
- **Found during:** Task 1 full-suite verification (`pytest tests/unit -q ...`)
- **Issue:** `TestFourNodeStructuralAssertion::test_actor_gate_called_before_get_adapter_in_dispatcher` string-scanned `_execute_transactional_tool`'s source for a literal `get_adapter_for_skill(` call site to prove ordering (Actor before adapter). Task 1's extraction — required by this task's own acceptance criteria — moved that literal call site out of the dispatcher's source entirely (into `_execute_adapter_and_audit`, defined immediately above it), so the test's `dispatcher_body.index("get_adapter_for_skill(")` raised `ValueError` (substring not found).
- **Fix:** Updated the test to assert `call_actor_gate` precedes the dispatcher's `_execute_adapter_and_audit(` call site (the new step 6/7 entry point), plus a companion assertion that `get_adapter_for_skill` is called inside the extracted helper and that the helper is defined above the dispatcher in the file. Preserves the exact invariant under test.
- **Files modified:** `apps/api/tests/unit/test_transactional_tools.py`
- **Verification:** `pytest tests/unit/test_transactional_tools.py -q` → 89 passed. Full suite → 1145 passed, 8 skipped, 0 failed (matched baseline exactly after Task 1, before Task 3 added new tests).
- **Committed in:** `503eb08` (Task 1 commit — fixed inline as part of the same task since it was directly caused by that task's own required change, not a separate commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug: structural test invalidated by an intentional, required source change)
**Impact on plan:** Necessary for the plan's own full-suite success gate. No scope creep — the fix only re-pointed the test's assertion targets to the new (equivalent) call sites; it did not weaken, remove, or add new claims beyond preserving the original invariant.

## Issues Encountered

None beyond the one Rule-1 fix documented above. Both guard-removal demonstrations went red on the first attempt and green immediately after restore — no iteration needed.

## Next Phase Readiness

- ACT-07's execution core is closed. `confirmation_resolution.execute_approved_confirmation` is ready to be called from plan 22-03's resolve route/Celery task — it takes `agent_id` and `conn_str` as explicit parameters (OD-5), so the caller is responsible for claiming the `pending_confirmations` row and decrypting the tenant connection string before calling in.
- `ResolutionOutcome.outcome` values (`"executed"`, `"denied"`, `"invalid"`, `"replay"`, `"in_progress"`) and `.reason` (the raw error string, non-`None` on every non-executed/non-replay/non-in_progress outcome) are the contract plan 22-03's route/task and plan 22-04's UI-facing execution-outcome lookup (OD-3, resolved in `22-01-PLAN.md`) will read from.
- `decision="approved_by_human"` is written to every audit row this resolver produces on execution — this is the exact discriminator OD-3's read-time `tool_calls_audit` join needs to tell a resolver-driven row apart from the original `require_human` row (same `agent_id`, `skill`, `arguments`).
- No migration, no new dependency, `pyproject.toml` byte-unchanged — confirmed mechanically (`git diff --quiet -- apps/api/pyproject.toml`), not merely asserted. Nothing added under `apps/api/alembic/`.
- Plans `22-03` through `22-06` are unblocked and depend on nothing this plan left incomplete.

---
*Phase: 22-owner-capability-control-pending-confirmation-resolution-clo*
*Completed: 2026-07-28*

## Self-Check: PASSED

All five created/modified source files plus this SUMMARY.md exist on disk.
All three task commit hashes (`503eb08`, `69468fa`, `bbe6e40`) resolve in
`git log --oneline --all`.
