---
phase: 15-actor-validator-l3-four-node-validation-chain-a-pre-mutation
plan: 02
subsystem: transactional-dispatcher
tags: [actor, require-human, pending-confirmations, idempotency, security, unit-tests, four-node-chain]
requires: [15-01]
provides: [require_human branch in _execute_transactional_tool, conn_str wired to call_actor_gate, ACT-02/ACT-04/ACT-05 unit tests]
affects: [apps/api/app/services/transactional/tools.py, apps/api/tests/unit/test_transactional_tools.py]
tech_stack_added: []
tech_stack_patterns: [PendingConfirmation ORM write, IntegrityError dedup, lazy-import-inside-function-body]
key_files_created: []
key_files_modified:
  - apps/api/app/services/transactional/tools.py
  - apps/api/tests/unit/test_transactional_tools.py
decisions:
  - "D-15-02-01: Mirror confirm_action_tool's synchronous get_sync_db pattern (no new asyncio.to_thread offload) — WR-03 offload is a tracked follow-up out of scope for Phase 15"
  - "D-15-02-02: Silent dedup on IntegrityError in require_human branch matches confirm_action_tool behavior; no existing-row re-fetch needed because the response only needs a non-error confirmation message"
  - "D-15-02-03: agent.py confirmed unchanged — async celery_chain Gatekeeper/Auditor/Strategist dispatch stays exactly as written in Phase 14 (ACT-05)"
metrics:
  duration: "~20 minutes"
  completed_date: "2026-06-30"
  tasks_completed: 2
  files_created: 0
  files_modified: 2
status: complete
---

# Phase 15 Plan 02: require_human Dispatcher Branch + Four-Node Structural Tests

## One-liner

Wired `conn_str` into `call_actor_gate` via a lazy import, added the `elif decision == "require_human":` branch that releases the idempotency reservation then writes a `PendingConfirmation` row with IntegrityError dedup, writes one audit row (`error="actor_require_human"`), and returns a NON-error awaiting-approval response without calling the adapter — 83 tests green.

## What Was Built

### Task 1: require_human dispatcher branch + conn_str wiring (`48a50d9`)

Two changes inside `_execute_transactional_tool` in `apps/api/app/services/transactional/tools.py`:

**conn_str lazy import (Pitfall 2 prevention):**
Extended the existing lazy import line inside the function body from:
```python
from app.services.agent_tools import _agent_id_var, _conversation_id_var  # noqa: PLC0415
```
to:
```python
from app.services.agent_tools import _agent_id_var, _conn_str_var, _conversation_id_var  # noqa: PLC0415
```
Added `conn_str = _conn_str_var.get()` immediately after. Updated `call_actor_gate` call to pass `conn_str` as the 6th positional arg. `_conn_str_var` remains exclusively inside the function body — the module-level position would cause the circular import documented in Pitfall 2 of 15-RESEARCH.md.

**require_human branch (step 5a):**
Added `elif decision == "require_human":` immediately after the existing `if decision == "block":` block, following exactly the order prescribed by 15-PATTERNS.md Analog 1 + Analog 2:

1. `await release_idempotency(agent_id, skill, validated.idempotency_key)` — FIRST (Pitfall 4: action will NOT proceed; free the reservation for a later retry after approval)
2. Build `PendingConfirmation(id=confirmation_id, agent_id, skill, arguments=raw_args, requested_at=now, expires_at=now+24h)`
3. `with get_sync_db() as db: db.add(row); try: db.commit() except IntegrityError: db.rollback()` — synchronous, mirrors `confirm_action_tool` exactly (D-15-02-01); silent dedup on `uq_pending_confirmations_unresolved` race (D-15-02-02)
4. `await write_audit_row(..., actor_decision=decision, actor_rationale=rationale, error="actor_require_human")` — AUD-01 symmetry
5. Return `{"content": [{"type": "text", "text": "...confirmation request...ID: {confirmation_id}..."}]}` — **no `is_error` key**, distinguishing it from the `block` branch

### Task 2: require_human tests + four-node structural assertion (`cb74503`)

Added `_mock_gate_require_human()` helper and three new test classes to `apps/api/tests/unit/test_transactional_tools.py`:

**TestActorRequireHuman (8 tests, ACT-04):**
| Test | Asserts |
|------|---------|
| `test_require_human_returns_no_is_error` | Response dict has no `is_error` key |
| `test_require_human_response_mentions_confirmation` | Response text contains "confirmation" |
| `test_require_human_releases_reservation` | `release_idempotency` called once |
| `test_require_human_writes_pending_confirmations_row` | `db.add(PendingConfirmation)` + `db.commit()` called |
| `test_require_human_writes_audit_row_with_actor_require_human_error` | 1 audit row with `error="actor_require_human"` and `actor_decision="require_human"` |
| `test_require_human_adapter_not_called` | `get_adapter` and `.place_order` NOT called (T-15-02) |
| `test_require_human_integrity_error_dedup_silent_rollback` | IntegrityError → `db.rollback()`, still NON-error response, audit row still written |
| `test_require_human_response_mentions_confirmation` | Response text references "confirmation" |

**TestActorMutatingGating (2 tests, ACT-02):**
| Test | Asserts |
|------|---------|
| `test_confirm_action_does_not_call_actor_gate` | `confirm_action_tool` (mutating=False) never calls `call_actor_gate` — SC1 negative assertion |
| `test_mutating_tool_calls_actor_gate` | `place_order_tool` (mutating=True) calls `call_actor_gate` exactly once |

**TestFourNodeStructuralAssertion (5 tests, ACT-05):**
| Test | Asserts |
|------|---------|
| `test_agent_py_celery_chain_dispatch_unchanged` | agent.py contains `celery_chain(`, `run_gatekeeper`, `run_auditor`, `run_strategist`, `.apply_async(queue="runtime")` |
| `test_agent_py_celery_chain_uses_si_chaining` | All three use `.si()` signature immutability |
| `test_actor_gate_called_before_get_adapter_in_dispatcher` | Source position: `call_actor_gate(` before `get_adapter(` in `_execute_transactional_tool` |
| `test_tools_py_contains_require_human_branch` | `elif decision == "require_human":` and `error="actor_require_human"` present |
| `test_tools_py_conn_str_var_lazy_import_only` | `_conn_str_var` import line is indented (never module-level) |

## Decisions Made

1. **Synchronous get_sync_db pattern (D-15-02-01):** Matched `confirm_action_tool`'s existing synchronous `get_sync_db()` pattern rather than introducing a new `asyncio.to_thread` offload. WR-03 (thread-offload for the blocking DB write in confirm_action + require_human) is an existing tracked follow-up and is out of scope for Phase 15. The plan explicitly specified this.

2. **Silent dedup without existing-row re-fetch (D-15-02-02):** On `IntegrityError`, the require_human branch rolls back and continues (logs `actor_require_human.duplicate_suppressed`). Unlike `confirm_action_tool`, it does NOT re-fetch the existing row — it just returns the "awaiting approval" message with the new `confirmation_id`. This is acceptable because the human approver resolves via the `pending_confirmations` table directly; the agent response text only needs to indicate the gate was hit.

3. **agent.py confirmed unchanged (D-15-02-03):** Verified source at lines 684-689 — the async `celery_chain(run_gatekeeper.si(...), run_auditor.si(...), run_strategist.si(...)).apply_async(queue="runtime")` block is exactly as committed in Phase 14. No modification was needed or made for ACT-05. The four-node chain structure was proven structurally via source assertions.

## Deviations from Plan

None — plan executed exactly as written. Both tasks implemented in order; all acceptance criteria met on first attempt except the `test_actor_gate_called_before_get_adapter_in_dispatcher` test which initially used a 5000-char slice too small to cover the full dispatcher body. Fixed to 14000-char slice (the dispatcher is ~8500 chars from its start to `call_actor_gate`). This is a test implementation detail, not a deviation from the plan's intent.

## Threat Model Coverage

| Threat ID | Status |
|-----------|--------|
| T-15-02 — Elevation of Privilege: require_human branch must return BEFORE step 6 | Mitigated: adapter `get_adapter` NOT called on require_human; `test_require_human_adapter_not_called` verifies |
| T-15-04 — Repudiation: idempotency vs require_human | Mitigated: `release_idempotency` called first; `uq_pending_confirmations_unresolved` caps outstanding rows |
| T-15-06 — Availability: orphan pending_confirmations accumulation | Mitigated: unique index + 24h TTL (`_CONFIRM_TTL_HOURS`); Phase 18 resolution/expiry sweeps |
| T-15-08 — Tampering: _conn_str_var import placement | Mitigated: lazy import only inside function body; `test_tools_py_conn_str_var_lazy_import_only` asserts no module-level import |
| T-15-SC — Tampering: no new package installs | Accepted: no new packages this plan |

## Verification Results

- `cd apps/api && python -m pytest tests/unit/test_transactional_tools.py -q` → 83 passed
- `cd apps/api && python -m pytest tests/unit/test_transactional_tools.py -k require_human -q` → 8 passed (≥2 required)
- `cd apps/api && python -m pytest tests/unit/test_transactional_tools.py tests/unit/test_actor_seam.py -q` → 97 passed
- Source assertions:
  - `elif decision == "require_human":` present in tools.py ✓
  - `error="actor_require_human"` present in tools.py ✓
  - `_conn_str_var` only on indented lazy import line inside `_execute_transactional_tool` ✓
  - `conn_str` passed as 6th arg to `call_actor_gate` ✓
  - `release_idempotency` at source position before `PendingConfirmation(` in the branch ✓
  - require_human return dict has no `is_error` key ✓
  - agent.py unchanged: `celery_chain(`, `run_gatekeeper.si(`, `run_auditor.si(`, `run_strategist.si(`, `.apply_async(queue="runtime")` all present ✓
- Full unit suite: 47 pre-existing failures (all unrelated to this plan — same set as 15-01)

## Self-Check: PASSED

- `apps/api/app/services/transactional/tools.py` modified with require_human branch ✓
- `apps/api/tests/unit/test_transactional_tools.py` extended with 3 new test classes ✓
- Task 1 commit `48a50d9` present in git history ✓
- Task 2 commit `cb74503` present in git history ✓
- No pre-existing test regressions caused by this plan ✓
