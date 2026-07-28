---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
plan: 05
subsystem: docs
tags: [documentation, pytest, celery, capability-envelopes, pending-confirmations, red-team-mode]

# Dependency graph
requires:
  - phase: 22-owner-capability-control-pending-confirmation-resolution-clo
    plan: 01
    provides: "CAP-05's server-side comparator fix and OD-1/OD-2/OD-3's Open Decisions text, quoted and correctly attributed in the guide"
  - phase: 22-owner-capability-control-pending-confirmation-resolution-clo
    plan: 04
    provides: "The shipped Deploy page copy strings (Enabled captions, staged-confirm text, empty state, denial translations) this plan's guide correction quotes verbatim"
provides:
  - "docs/guides/owner-capability-guide.md corrected: no longer claims a skill cannot be enabled or requires direct database action; new 'When an action needs your approval' section documents the queue and states plainly that approving does not verify a customer's identity"
  - "apps/api/tests/integration/test_act07_resolve_live.py — ACT-07's gated live-database proof, authored, collectible, deferred to plan 22-06's operator run"
  - "22-VALIDATION.md's planner placeholders filled: all 14 Per-Task Verification Map rows assigned real task ids, four rows' Automated Command corrected to the route module, guard-removal demonstration inventory added"
affects: ["22-06"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Route function called directly (no ASGITransport, no app.main) for the live-DB integration test — an AsyncSession bound to the ephemeral DB and a SimpleNamespace tenant stand-in are passed straight into resolve_pending_confirmation, avoiding FastAPI dependency-injection machinery entirely"
    - "Celery bind=True task exercised without a broker by calling the task instance directly (task(confirmation_id)) rather than .delay()/.apply() — the standard synchronous-unit-test pattern for a bound task"
    - "red_team_mode() entered in the synchronous calling scope before a nested asyncio.run() (inside the Celery task body) — contextvars.copy_context() at Task creation time carries the flag into the coroutine without threading it through the task's single confirmation_id argument"

key-files:
  created:
    - apps/api/tests/integration/test_act07_resolve_live.py
  modified:
    - docs/guides/owner-capability-guide.md
    - .planning/phases/22-owner-capability-control-pending-confirmation-resolution-clo/22-VALIDATION.md

key-decisions:
  - "Reworded two docstring sentences in the gated integration module to avoid literal substring collisions with the plan's own negative-assertion grep gates (docker-compose, settings.CONTROL_DB_URL) — the same over-broad-gate class 22-04-SUMMARY.md already documented for data-gate/idempotency_key. The docstring's meaning is unchanged; only the exact literal that would trip a substring grep was reworded."
  - "In the gated module's second test, ran the winning row's execution for real (not skipped) before attempting the second resolve, so the 'no second tool_calls_audit row' assertion is meaningful (count stays at one) rather than vacuously true (count stays at zero because nothing ever executed)."
  - "Seeded every capability envelope with rate_limit=None in the gated module, so apply_rate_and_constraint_checks never reaches its Redis pipeline — the module needs only a local PostgreSQL server to run, not Redis, keeping its environment footprint to CLAUDE.md rule 9's minimum."

requirements-completed: [CAP-05, ACT-07]

coverage:
  - id: D1
    description: "Owner guide corrected — no stale locked-enabled claims survive, all 12 UI-SPEC-locked copy strings present verbatim and checked against the live deploy page in both directions, new approval-queue section states plainly that approving does not verify a customer's identity"
    requirement: "CAP-05"
    verification:
      - kind: other
        ref: "grep verify script (plan Task 1 <verify>, both automated blocks) — GUIDE-SCREEN-PARITY-OK, GUIDE-CORRECTION-OK"
        status: pass
    human_judgment: false
  - id: D2
    description: "ACT-07's gated live-database integration module authored, collects cleanly with all three tests skipped in this environment, never references the production control-database setting"
    requirement: "ACT-07"
    verification:
      - kind: other
        ref: "pytest tests/integration/test_act07_resolve_live.py --collect-only -q -> 3 tests collected; pytest tests/integration/test_act07_resolve_live.py -q -> 3 skipped"
        status: pass
    human_judgment: true
    rationale: "The module's own correctness against a real database cannot be established in this environment (no local PostgreSQL server exists here). Collection and the plan's negative-assertion gates are proven; the live red/green run is deferred to plan 22-06's operator gate per the plan's own scope."
  - id: D3
    description: "22-VALIDATION.md's planner placeholders filled: 14/14 map rows carry real task/plan/wave ids, four rows' commands corrected to the route module with a recorded reason, migration row struck with why, guard-removal demonstration inventory added"
    requirement: "CAP-05"
    verification:
      - kind: other
        ref: "plan Task 3 <verify> automated python check — VALIDATION-FILL-OK"
        status: pass
    human_judgment: false

duration: 65min
completed: 2026-07-28
status: complete
---

# Phase 22 Plan 05: Guide Correction, ACT-07 Live Proof, Validation Fill Summary

**Corrected `docs/guides/owner-capability-guide.md` to match the shipped CAP-05/ACT-07 behaviour (removing the stale "cannot re-enable" claim, adding a full approval-queue section that states approving never re-verifies identity), authored ACT-07's gated live-database integration module, and filled every planner placeholder in `22-VALIDATION.md`.**

## Performance

- **Duration:** ~65 min (estimated — reading five source files plus the deploy page, writing and verifying the guide correction, designing and writing a 586-line gated integration module against source read directly from six backend files, filling the validation document)
- **Started:** 2026-07-28T19:30:00Z (estimated)
- **Completed:** 2026-07-28T20:34:08Z
- **Tasks:** 3/3 planned
- **Files modified:** 3 (1 created, 2 modified)

## Accomplishments

- **Task 1 (CAP-05 doc correction):** Rewrote the Enabled subsection's second paragraph entirely — deleted every sentence asserting the removed platform-default lock, the quoted "Cannot re-enable" caption, and the "direct database action" claim. Replaced with the shipped staged-confirm behaviour: the two resting captions, the staged question/button labels for an already-deployed agent, and the unstaged-immediate-save path for a not-yet-deployed one. Corrected the tighten-only section to state that Enabled is the one field the platform default no longer bounds (the other five dimensions remain bounded, unchanged). Added a full new "When an action needs your approval" section covering the pending-confirmation queue: what a row tells you, what approving/rejecting/expiry each do, the honest two-step outcome (`Awaiting execution.` before `executed`/`not_executed`), why a tightened envelope can refuse an already-approved request, and — stated plainly per OD-1 — that approving authorises the action but never re-verifies the customer's identity. Extended the friction section and the enforcement/references sections with the three new backing modules (`pending_confirmations.py`, `confirmation_resolution.py`, `worker/tasks/runtime/confirmations.py`). Every one of the 12 UI-SPEC-locked copy strings the plan names is present, byte-identical, in both the guide and the live `deploy/page.tsx` — checked in both directions so a paraphrase on either side would be caught.
- **Task 2 (ACT-07 live-DB proof):** Authored `apps/api/tests/integration/test_act07_resolve_live.py` (586 lines), gated exactly as `test_red_team_rtx.py`: an `INTEGRATION_TESTS_ENABLED` module-scope constant and `pytestmark` skip, standard-library/pytest-only imports at module scope (every `app.*` symbol imported lazily inside the fixture/test that needs it, confirmed importable with zero environment variables set outside pytest). An ephemeral control database is created and migrated to head `0019` (OD-3: no new column, no `0020`) and dropped in a `finally`. Three tests call the real `resolve_pending_confirmation` route function directly (no ASGI transport, no `app.main`) against a real `AsyncSession`, then run the real Celery task body (`resolve_approved_confirmation`, called directly rather than through a broker) inside `red_team_mode()` — reaching the stub provider adapter through the shipped red-team short-circuit so no real credential or money movement is needed. Each asserts against real database state (`pending_confirmations`, `tool_calls_audit`, `tool_idempotency_keys`), never a log line: exactly-once execution, the atomic claim refusing a second resolve with 409 and writing no second audit row, and a ceiling tightened after approval denying execution with `capability.denial:max_amount_cents`.
- **Task 3 (validation doc fill):** Filled all 14 Per-Task Verification Map rows with real `<plan>-T<n>` task ids, plan numbers, and waves, matching the plan's explicit assignment table and cross-checked against each named plan's actual task list and names. Corrected the four rows (reject, expiry, concurrent-resolve, ownership) the planner routed to `tests/unit/test_pending_confirmation_routes.py` instead of the resolver module the draft assumed, with a note recording the reassignment and its reason. Struck the `Control migration 0020 up/down roundtrip` manual-verification row per its own skip instruction, recording that OD-3 closed with a read-time lookup instead of a new column. Recorded OD-5's closure (explicit keyword-only parameter contract, not ContextVar seeding) under Wave 0 Requirements, naming the resolver's own signature and its three source-absence tests as the artifact. Added a six-entry guard-removal demonstration inventory (the ceiling guard from 22-01; the rate-and-constraint call and the Actor-symbol absence from 22-02; the unconditional dispatch, the claim guard, and the actor-decision predicate from 22-03), cross-verified against each plan's own SUMMARY to confirm all six were actually observed red-then-green, not merely claimed.

## Task Commits

Each task was committed atomically:

1. **Task 1: Correct the owner guide for CAP-05 and extend it for the approval queue** - `70e0c0c` (docs)
2. **Task 2: Author ACT-07's gated live-database integration module** - `16ae32a` (test)
3. **Task 3: Fill the validation document's planner placeholders** - `d033f28` (docs)

_Plan metadata commit follows this SUMMARY._

## Files Created/Modified

- `docs/guides/owner-capability-guide.md` — Enabled subsection rewritten, tighten-only section corrected, new "When an action needs your approval" section added, friction/enforcement/references sections extended
- `apps/api/tests/integration/test_act07_resolve_live.py` — new gated integration module (created)
- `.planning/phases/22-owner-capability-control-pending-confirmation-resolution-clo/22-VALIDATION.md` — planner placeholders filled, reassignment note, struck migration row, guard-removal inventory added

## Decisions Made

- **Reworded two docstring sentences in the gated module to avoid literal substring collisions with the plan's own negative-assertion grep gates.** The first draft's docstring said "no docker-compose step" (to assert the ABSENCE of Docker) and named `settings.CONTROL_DB_URL` (to assert the module never references it) — both tripped the plan's own `grep -nE 'docker-compose|...'` and `grep -qF 'settings.CONTROL_DB_URL'` gates as false positives, because those gates match the literal substring regardless of surrounding negation. Reworded both sentences to convey the identical meaning without the exact literal (e.g., "the platform's own production database configuration" instead of the attribute-access string). This is the same over-broad-gate class 22-04-SUMMARY.md already documented for `data-gate`/`idempotency_key` — the gate is a whole-string/substring check, not a semantic one.
- **Ran the winning row's execution for real inside the second test** (`test_second_resolve_is_rejected_by_the_database_claim`), rather than only testing the double-claim at the route level with no execution at all. This makes the "the refused second resolve must produce no second `tool_calls_audit` row" assertion meaningful (count stays at exactly one) instead of vacuously true (count would stay at zero regardless of whether the claim guard worked, since nothing executed either way).
- **Seeded every capability envelope in the gated module with `rate_limit=None`.** `apply_rate_and_constraint_checks` only reaches its Redis INCR+EXPIRE pipeline when `rate_limit` is set; leaving it unset keeps the module's environment footprint to a single local PostgreSQL server (no local Redis needed) while still exercising the ceiling check under test.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Two gated-module docstring sentences tripped the plan's own negative-assertion grep gates as false positives**
- **Found during:** Task 2, running the plan's own `<verify>` block for the first time
- **Issue:** The docstring's prose describing what the module does NOT do ("no docker-compose step", "never constructs a connection from `settings.CONTROL_DB_URL`") contained the exact literal substrings the plan's negative gates search for, causing `grep -nE 'docker-compose|...'` and `grep -qF 'settings.CONTROL_DB_URL'` to fail against a module that in fact satisfied both requirements.
- **Fix:** Reworded both sentences to the identical meaning without the flagged literal ("no containerized orchestration tool", "the platform's own production database configuration").
- **Files modified:** `apps/api/tests/integration/test_act07_resolve_live.py`
- **Verification:** Both automated `<verify>` blocks for Task 2 re-run clean after the wording change; no other content changed.
- **Committed in:** `16ae32a` (Task 2 commit — the file was corrected before its first commit, so no separate fix commit was needed)

---

**Total deviations:** 1 auto-fixed (1 bug — gate false positive, same class already documented in 22-04-SUMMARY.md)
**Impact on plan:** Cosmetic wording fix only; no behavioural change to the module. No scope creep.

## Issues Encountered

- The plan's Task 3 automated `<verify>` block's final scope check (`git diff --name-only | grep -v '^\.planning/' | ...`) reports a false positive when run against the full working tree, because this repository's working tree already carries unrelated pre-existing deletions and untracked files (`start_admin.py`, `start_api.bat`, `.agents/`, `AGENTS.md`, `PRODUCT.md`, `SECURITY.md`, `prototypes/`, `scripts/*`, etc.) that predate this session and are unrelated to any of this plan's three tasks. Confirmed the actual scope constraint holds by checking the STAGED diff for Task 3's own commit (`git diff --cached --name-only`), which shows exactly one file: `22-VALIDATION.md`. Resolved by verifying scope correctly rather than by touching the unrelated pre-existing working-tree state (out of scope for this task).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The document CAP-05 falsifies is corrected in the same phase as the behaviour change, source-anchored rather than reviewed by eye: every quoted string is checked against the live deploy page in both directions.
- ACT-07's live-database proof is authored, collectible, and ready for plan 22-06's operator to run once a local PostgreSQL server exists — it has never been run live in this environment.
- `22-VALIDATION.md` carries no remaining `_planner_` token; every row names a real task, and the guard-removal demonstration inventory lets a reader confirm all six mutations ran without opening 22-01/22-02/22-03 individually.
- Full unit suite: 1179 passed, 8 skipped, 0 failed (at or above the 1136-passed baseline). `apps/api/pyproject.toml` byte-unchanged.
- Plan 22-06 can now hand `docs/guides/owner-capability-guide.md` to the VER-01 SC2 tester as the corrected artifact, and run the gated integration module against a real local PostgreSQL server.

---
*Phase: 22-owner-capability-control-pending-confirmation-resolution-clo*
*Completed: 2026-07-28*

## Self-Check: PASSED

All three modified/created files exist on disk (`docs/guides/owner-capability-guide.md`,
`apps/api/tests/integration/test_act07_resolve_live.py`,
`.planning/phases/22-owner-capability-control-pending-confirmation-resolution-clo/22-VALIDATION.md`).
All three task commit hashes (`70e0c0c`, `16ae32a`, `d033f28`) resolve in `git log --oneline --all`.
This SUMMARY.md exists at its declared path.
