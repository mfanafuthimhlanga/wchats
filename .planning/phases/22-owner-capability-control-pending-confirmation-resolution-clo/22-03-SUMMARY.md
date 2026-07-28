---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
plan: 03
subsystem: api
tags: [fastapi, celery, sqlalchemy, raw-sql, pydantic, idor, python, pytest]

# Dependency graph
requires:
  - phase: 22-owner-capability-control-pending-confirmation-resolution-clo
    plan: 02
    provides: "execute_approved_confirmation(*, confirmation_id, agent_id, skill, arguments, conn_str) -> ResolutionOutcome, SKILL_INPUT_MODELS, and decision='approved_by_human' as the audit discriminator"
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: "_get_owned_agent IDOR guard convention, pending_confirmations table, tool_calls_audit table"
provides:
  - "GET /agents/{agent_id}/pending-confirmations — the approver's triage queue, total/stable ordering, OD-3 read-time execution-outcome lookup"
  - "POST /agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve — the atomic UPDATE ... WHERE resolved_at IS NULL ... RETURNING claim, expiry forced inside the same statement, commit-before-dispatch (OD-6)"
  - "resolve_approved_confirmation — the runtime-queue Celery task taking only confirmation_id, bridging into execute_approved_confirmation via asyncio.run"
affects: [22-04]

tech-stack:
  added: []
  patterns:
    - "Atomic UPDATE ... WHERE <guard> ... RETURNING as the sole concurrency control for a resolve/claim route — no read-then-write check, mirroring reserve_idempotency's own INSERT ... ON CONFLICT ... RETURNING idiom"
    - "Read-time audit-row lookup (OD-3) in place of a denormalized execution-outcome column — the resolver's own audit write is the single source of truth, discriminated by a decision-marker predicate"
    - "SQL-text assertions (mock_db.execute.call_args) as the mechanical proof for guard-removal demonstrations that a fully-mocked DB boundary cannot otherwise exercise"

key-files:
  created:
    - apps/api/app/schemas/pending_confirmation.py
    - apps/api/app/api/v1/pending_confirmations.py
    - apps/api/app/worker/tasks/runtime/confirmations.py
    - apps/api/tests/unit/test_pending_confirmation_routes.py
  modified:
    - apps/api/app/main.py
    - apps/api/app/worker/celery_app.py

key-decisions:
  - "Reworded five accidental occurrences of the literal 'resolved_at IS NULL ' (trailing space) down to exactly one, in the claim's own WHERE clause. The other four (a docstring mention, the GET query's WHERE clause, its ORDER BY CASE, and a second docstring mention) would have made Task 3's own guard-removal mutation script — which greps for that exact needle and replaces only the FIRST match in the file — silently mutate a comment or the wrong query instead of the claim it exists to test."
  - "The Celery task import in resolve_pending_confirmation is local to the function body, not module-level — not a style choice but a hard ordering constraint: Task 1's own verify script imports the route module (and app.main's OpenAPI schema) before app.worker.tasks.runtime.confirmations exists (it is Task 2's output). A module-level import would have made Task 1 uncommittable on its own."
  - "The resolve response never computes execution_outcome — it is always None. A freshly-dispatched task has not run by response time (the dispatch is fire-and-forget), so calling _execution_outcome_for here would either always return None (wasted round-trip) or, in a contrived redelivery/replay scenario, risk matching a stale row. The GET queue route is the only place an approver reads the real, eventually-consistent outcome."
  - "Guard-removal demonstrations (b) and (c) assert on the actual SQL text sent to db.execute() (via mock_db.execute.call_args), not just the mocked return value. A fully-mocked DB boundary can't exercise a live claim or a live predicate — a status-code-only assertion would stay green regardless of what SQL the route sent, so the mechanical proof has to read the query text itself."

requirements-completed: [ACT-07]

coverage:
  - id: D1
    description: "PendingConfirmationResolve rejects an unknown key and the literal 'expired' value; its only field is resolution"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "inline verify script (Task 1) + TestResolveRoute::test_body_rejects_an_action_payload"
        status: pass
    human_judgment: false
  - id: D2
    description: "_get_owned_agent is byte-identical to capability_envelopes.py's shipped copy and is the first statement of both route bodies"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "inline verify script (Task 1) — inspect.getsource equality + source-index ordering assertions"
        status: pass
    human_judgment: false
  - id: D3
    description: "The atomic claim (UPDATE ... WHERE resolved_at IS NULL ... RETURNING) is the sole concurrency control; a second resolve on the same row returns 409 and dispatches nothing"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_pending_confirmation_routes.py::TestResolveRoute::test_second_resolve_returns_409_and_never_enqueues"
        status: pass
      - kind: other
        ref: "manual mutation run — see Guard-Removal Demonstrations (b) below for real red/green output"
        status: pass
    human_judgment: false
  - id: D4
    description: "Expiry is forced inside the same atomic statement with a strict expires_at < now() comparison, regardless of what the caller requested"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_pending_confirmation_routes.py::TestResolveRoute::test_expired_row_is_forced_to_expired_and_never_enqueues + inline verify script asserting no 'expires_at <=' in source"
        status: pass
    human_judgment: false
  - id: D5
    description: "The claim commits before the execution task is dispatched (OD-6); rejection and the non-mutating confirm_action skill never dispatch"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_pending_confirmation_routes.py::TestResolveRoute::test_commit_precedes_enqueue, test_reject_never_enqueues, test_confirm_action_row_resolves_without_enqueue"
        status: pass
      - kind: other
        ref: "manual mutation run — see Guard-Removal Demonstrations (a) below for real red/green output"
        status: pass
    human_judgment: false
  - id: D6
    description: "Both routes 404 on a foreign agent, checked before the body or the confirmation id, leaking no existence information"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_pending_confirmation_routes.py::TestOwnership::test_both_routes_404_on_foreign_agent (parametrised GET/POST)"
        status: pass
    human_judgment: false
  - id: D7
    description: "The GET queue orders unresolved rows by deadline ascending with nulls last then id, and recently-resolved rows by resolution time descending then id — a total, stable ordering"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_pending_confirmation_routes.py::TestQueueList::test_ordering_is_total_and_stable"
        status: pass
    human_judgment: false
  - id: D8
    description: "The execution-outcome lookup matches on agent_id, skill, the arguments' idempotency key, and actor_decision='approved_by_human' — never the original require_human audit row that shares the first three"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_pending_confirmation_routes.py::TestExecutionOutcome (5 tests)"
        status: pass
      - kind: other
        ref: "manual mutation run — see Guard-Removal Demonstrations (c) below for real red/green output"
        status: pass
    human_judgment: false
  - id: D9
    description: "resolve_approved_confirmation is registered on the Celery app, reports acks_late=True and the runtime queue, and its only parameter after self is confirmation_id"
    requirement: "ACT-07"
    verification:
      - kind: unit
        ref: "inline verify script (Task 2)"
        status: pass
    human_judgment: false

duration: 55min
completed: 2026-07-28
status: complete
---

# Phase 22 Plan 03: ACT-07 pending-confirmation control surface Summary

**Built the approver's queue read, the atomic resolve claim, and the runtime-queue Celery task that performs the approved execution — `GET`/`POST .../resolve` under `/api/v1`, wired to 22-02's `execute_approved_confirmation` by confirmation id only.**

## Performance

- **Duration:** 55 min (approximate; commit-to-commit span for the four commits was 38 min — 18:05:37 to 18:43:49 — plus discovery/read-first review before the first commit and the three real guard-removal mutation runs)
- **Started:** ~2026-07-28T17:48:00+02:00 (estimated, plan/context reading)
- **Completed:** 2026-07-28T18:43:49+02:00 (Task 3 commit)
- **Tasks:** 3/3 planned (+ 1 unplanned Rule-1 fix commit between Task 2 and Task 3)
- **Files modified:** 6 (4 created, 2 modified)

## Accomplishments

- `schemas/pending_confirmation.py`: `PendingConfirmationResolve` (`extra="forbid"`, single `resolution: Literal["approved", "rejected"]` field — "expired" deliberately absent from the literal), `PendingConfirmationResponse`, `PendingConfirmationListResponse`.
- `api/v1/pending_confirmations.py`: `_get_owned_agent` copied byte-identical from `capability_envelopes.py`. `GET /agents/{agent_id}/pending-confirmations` returns unresolved rows (deadline ascending, nulls last, id tiebreak) then rows resolved within 24 hours (resolution time descending, id tiebreak), computing an `execution_outcome` for each approved row via `_execution_outcome_for` — a read-time `tool_calls_audit` lookup on `(agent_id, skill, arguments->>'idempotency_key', actor_decision='approved_by_human')` (OD-3, no `0020` migration). `POST .../resolve` claims the row with a single `UPDATE pending_confirmations SET resolved_at = now(), resolution = CASE WHEN expires_at IS NOT NULL AND expires_at < now() THEN 'expired' ELSE :resolution END WHERE id = :id AND agent_id = :agent_id AND resolved_at IS NULL RETURNING ...` — the entire concurrency control, expiry enforced inside the same statement with a strict `<` (never `<=`). `await db.commit()` happens before any dispatch (OD-6); the Celery task is imported locally inside the function body and dispatched via `.delay(confirmation_id)` only when the claimed resolution is `approved` and the claimed skill is a key of `SKILL_INPUT_MODELS` — a `confirm_action` row (no adapter method, no idempotency key) resolves but never dispatches.
- `worker/tasks/runtime/confirmations.py`: `resolve_approved_confirmation(self, confirmation_id: str) -> dict`, `bind=True, acks_late=True, queue="runtime"`, fully-qualified name matching the `staleness.py` convention. Re-reads the row, guards non-approved state, loads the agent, decrypts `conn_str` at runtime (never a task arg — CLAUDE.md rule 4), bridges into `execute_approved_confirmation` via `asyncio.run(...)`. Idempotency (CLAUDE.md rule 5's second half) is provided entirely by the resolver's own fresh reservation (22-02) — no second bespoke guard. Registered in `celery_app.py`'s `include=[...]` list; no routing or beat-schedule changes.
- `tests/unit/test_pending_confirmation_routes.py` (551 lines, 17 tests): `TestResolveRoute` (7), `TestOwnership` (1, parametrised GET/POST), `TestQueueList` (3), `TestExecutionOutcome` (5) — all route-level via `ASGITransport(app=app)`, the Celery dispatch patched at `app.worker.tasks.runtime.confirmations.resolve_approved_confirmation` (the exact name the route's local import resolves it under).
- **All three required guard-removal demonstrations actually executed** (real red-then-green output below), not merely asserted in prose.
- Full unit suite after all tasks: **1179 passed, 8 skipped, 0 failed** (baseline after 22-02 was 1162 passed, 8 skipped, 0 failed — 17 new tests, above baseline per `<verification>`).

## Guard-Removal Demonstrations (real output)

### (a) Unconditional dispatch (T-22-ACT-10 / the "approved value" dispatch gate)

Applied mutant (`app/api/v1/pending_confirmations.py`): replaced the `if claimed["resolution"] == "approved" and claimed["skill"] in SKILL_INPUT_MODELS:` gate with unconditional dispatch.

Ran the two targeted tests against the mutant:
```
FAILED tests/unit/test_pending_confirmation_routes.py::TestResolveRoute::test_reject_never_enqueues
FAILED tests/unit/test_pending_confirmation_routes.py::TestResolveRoute::test_expired_row_is_forced_to_expired_and_never_enqueues
AssertionError: Expected 'delay' to not have been called. Called 1 times.
2 failed in 68.11s
```
Restored `pending_confirmations.py` from `HEAD` (`git checkout --`, run unconditionally before any pass/fail assertion). Confirmed clean via `git status --short`. Re-ran:
```
2 passed in 49.58s
```
Red-then-green observed directly.

### (b) Claim guard dropped — `resolved_at IS NULL` removed from the WHERE clause (T-22-ACT-03)

Applied mutant: `s.replace('resolved_at IS NULL ', '', 1)` on `app/api/v1/pending_confirmations.py` (confirmed exactly one occurrence of the needle in the file before mutating — see Deviations below for why that had to be made true first).

Ran the targeted test against the mutant:
```
FAILED tests/unit/test_pending_confirmation_routes.py::TestResolveRoute::test_second_resolve_returns_409_and_never_enqueues
AssertionError: assert 'resolved_at IS NULL' in 'UPDATE pending_confirmations SET resolved_at = now(), ... AND agent_id = :agent_id AND RETURNING id, skill, ...'
1 failed in 51.87s
```
Restored from `HEAD` unconditionally. Confirmed clean. Re-ran:
```
1 passed in 55.36s
```
Red-then-green observed directly — and the failure output itself confirms the mutation hit the intended clause (the dangling `AND RETURNING` in the captured SQL text).

### (c) Actor-decision predicate dropped from the outcome lookup (T-22-ACT-11)

Applied mutant: removed the `"AND actor_decision = 'approved_by_human' "` line from `_execution_outcome_for`'s SQL in `app/api/v1/pending_confirmations.py`.

Ran the targeted test against the mutant:
```
FAILED tests/unit/test_pending_confirmation_routes.py::TestExecutionOutcome::test_outcome_ignores_the_original_require_human_audit_row
AssertionError: assert 'actor_decision' in "SELECT error, created_at FROM tool_calls_audit WHERE agent_id = :agent_id AND skill = :skill AND arguments->>'idempotency_key' = :idempotency_key ORDER BY created_at DESC LIMIT 1"
1 failed in 184.36s
```
Restored from `HEAD` unconditionally. Confirmed clean. Re-ran:
```
1 passed in 86.11s
```
Red-then-green observed directly.

## Task Commits

Each task was committed atomically:

1. **Task 1: Resolve-body schema, the queue read, and the atomic claim route** - `a50d5dd` (feat)
2. **Task 2: The runtime-queue execution task and its registration** - `7da8d58` (feat)
3. **[Unplanned Rule-1 fix] Make the resolved_at IS NULL claim guard the sole occurrence of its own literal** - `258e330` (fix)
4. **Task 3: Route-level proof — the claim, ordering, outcome lookup, guard** - `2600674` (test)

_Plan metadata commit follows this SUMMARY._

## Files Created/Modified

- `apps/api/app/schemas/pending_confirmation.py` — new: `PendingConfirmationResolve`, `PendingConfirmationResponse`, `PendingConfirmationListResponse`
- `apps/api/app/api/v1/pending_confirmations.py` — new: `_get_owned_agent` (byte-identical copy), `_execution_outcome_for`, `_row_to_response`, `list_pending_confirmations`, `resolve_pending_confirmation`
- `apps/api/app/worker/tasks/runtime/confirmations.py` — new: `resolve_approved_confirmation` Celery task
- `apps/api/app/main.py` — registered `pending_confirmations.router` under `/api/v1`
- `apps/api/app/worker/celery_app.py` — added `app.worker.tasks.runtime.confirmations` to the `include=[...]` autodiscovery list
- `apps/api/tests/unit/test_pending_confirmation_routes.py` — new: 17 route-level and unit-level tests

## Decisions Made

- **Local (not module-level) import of the Celery task inside `resolve_pending_confirmation`.** This is a hard ordering constraint, not a style preference: Task 1 is committed and independently verified (including importing `app.main` for the OpenAPI schema check) before `app.worker.tasks.runtime.confirmations` exists — that module is Task 2's own output. A module-level import in Task 1's file would have made Task 1's own `<verify>` block fail on a `ModuleNotFoundError`. This also happens to be the exact patch point Task 3's tests need ("the name the route resolves it under" per the plan's own instruction).
- **The resolve response never computes `execution_outcome`.** Only the GET queue route calls `_execution_outcome_for`. A freshly-dispatched task has not run by response time — the dispatch is fire-and-forget — so a lookup immediately after claiming would always return the honest "awaiting execution" `None` anyway, at the cost of an extra DB round-trip on every resolve call. The plan's own `<action>` text for Route 2 never mentions calling `_execution_outcome_for`, only for Route 1.
- **Guard-removal demonstrations (b) and (c) assert on the actual SQL text sent to `db.execute()`** (`str(mock_db.execute.call_args[0][0])`), not only on the mocked return value. Since the DB boundary is fully mocked in every route-level test (no live Postgres in this environment — confirmed unavailable per `STATE.md`), a mutation to the real SQL string cannot change what a scripted mock returns; only an assertion that reads the actual query text sent can catch it. `test_second_resolve_returns_409_and_never_enqueues` and `test_outcome_ignores_the_original_require_human_audit_row` both carry this text assertion specifically so they are meaningful guard-removal targets, not vacuous ones.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] The claim guard's own literal appeared five times in the file, breaking Task 3's own mutation script**
- **Found during:** Task 3, while designing the guard-removal demonstration for T-22-ACT-03
- **Issue:** `app/api/v1/pending_confirmations.py` contained the exact substring `'resolved_at IS NULL '` (with a trailing space) in five places: the module docstring, the `resolve_pending_confirmation` docstring, the GET query's `WHERE` clause, the GET query's `ORDER BY` `CASE` expression, and — the intended target — the resolve claim's `WHERE` clause. Task 3's own `<verify>` block (and this plan's guard-removal demonstration b) locates the needle with `s.find(needle)`/`s.replace(needle, '', 1)`, which mutates only the FIRST occurrence in raw file-byte order. Since the module docstring's occurrence sits earliest in the file, an unmodified mutation script would have silently stripped six characters out of a comment, leaving the real claim guard untouched — the guard-removal demonstration would then either false-pass (mutating nothing observable) or, worse, corrupt an unrelated query.
- **Fix:** Reworded the GET query's `WHERE` clause (swapped the `OR` operand order so `resolved_at IS NULL` is no longer followed by a space) and its `ORDER BY` `CASE` expression (restated using `IS NOT NULL` instead of `IS NULL`), and reworded both prose docstring mentions to reference the guard conceptually without reproducing its exact SQL text. Confirmed mechanically: the needle now appears exactly once in the file, in the claim's own `WHERE` clause.
- **Files modified:** `apps/api/app/api/v1/pending_confirmations.py`
- **Verification:** Re-ran Task 1's full inline verify script (still `ACT-07-ROUTES-SHAPE-OK`) and the complete `test_pending_confirmation_routes.py` suite (17 passed) after the rewording — behavior of both routes is unchanged, confirmed by the unchanged Task 1 shape-verify script and Task 3's full test suite.
- **Committed in:** `258e330` (separate fix commit between Task 2 and Task 3, since the defect was discovered while building Task 3's verification but lives entirely in Task 1's file)

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug: a guard-removal demonstration's own mutation script would have targeted the wrong text)
**Impact on plan:** Necessary for Task 3's own required guard-removal demonstration to be meaningful. No scope creep — the fix only reworded SQL/prose phrasing to remove an accidental literal collision; it did not change either route's behavior, which was re-verified against Task 1's own shape gate after the change.

## Issues Encountered

None beyond the one Rule-1 fix documented above. All three guard-removal demonstrations went red on the first attempt and green immediately after restore — no iteration needed.

## Next Phase Readiness

- ACT-07's control surface is closed: an approver can now read the queue and resolve a confirmation through the shipped route/task path 19-02's UAT found missing (VER-01 SC2's second structural blocker, `T-19-04`).
- `PendingConfirmationResponse`'s `execution_outcome`/`execution_error`/`executed_at` fields and the ordering/denial-translation contract are exactly what `22-UI-SPEC.md § Surface 2` needs — plan `22-04` (the Deploy-page UI) is unblocked and depends on nothing this plan left incomplete.
- No migration, no new dependency, `pyproject.toml` byte-unchanged — confirmed mechanically (`git diff --quiet -- apps/api/pyproject.toml`), not merely asserted. Nothing added under `apps/api/alembic/`.
- The accepted residual `T-22-ACT-09` (dispatch failing after a durable claim) leaves a row visibly `approved` with `execution_outcome: null` — the honest "awaiting execution" state `22-UI-SPEC.md` already designed for, not a silent success.

---
*Phase: 22-owner-capability-control-pending-confirmation-resolution-clo*
*Completed: 2026-07-28*

## Self-Check: PASSED

All six created/modified source files plus this SUMMARY.md exist on disk.
All four task commit hashes (`a50d5dd`, `7da8d58`, `258e330`, `2600674`) resolve in
`git log --oneline --all`.
