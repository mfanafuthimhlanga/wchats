---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
plan: "06"
subsystem: transactional-dispatcher
tags: [idv, security, enforcement, dispatcher, audit]
dependency_graph:
  requires: ["17-03", "17-04", "17-05"]
  provides: ["IDV-04", "IDV-05"]
  affects: ["apps/api/app/services/transactional/tools.py"]
tech_stack:
  added: []
  patterns: ["lazy-import-circular-break", "ContextVar-gate", "AUD-01-symmetry"]
key_files:
  created: []
  modified:
    - apps/api/app/services/transactional/tools.py
    - apps/api/tests/unit/test_transactional_tools.py
decisions:
  - "[17-06] Step 2.5 placed BEFORE reserve_idempotency (Step 3) — T-17-21: blocked calls never consume idempotency slot"
  - "[17-06] check_verified_session imported lazily inside dispatcher body — avoids circular import (Pitfall 7)"
  - "[17-06] _verified_session_token_var added to existing lazy import tuple (same import line as _agent_id_var etc.)"
  - "[17-06] Both IDV block branches use the same write_audit_row keyword signature as capability.denial (AUD-01 symmetry)"
  - "[17-06] Snapshot snapshot.get('requires_identity_verification', False) guards the whole block — IDV-04 envelope-driven"
metrics:
  duration: "~15 min"
  completed: "2026-07-01T19:08:21Z"
  tasks_completed: 2
  files_modified: 2
status: complete
---

# Phase 17 Plan 06: Step 2.5 IDV Enforcement Gate Summary

Server-side enforcement gate for identity verification: `_execute_transactional_tool` now blocks
mutating calls whose capability envelope has `requires_identity_verification=true` unless a valid,
non-expired verified session is present, writing one audit row per block path before idempotency
reservation.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 RED | Add failing IDV gate tests | 02280de | tests/unit/test_transactional_tools.py |
| 1 GREEN | Insert Step 2.5 IDV gate in dispatcher | 3d7f5a8 | app/services/transactional/tools.py |
| 2 | IDV enforcement + ordering + audit + IDV-04 skip tests | 9eae3c0 | tests/unit/test_transactional_tools.py |

## What Was Built

### Step 2.5 IDV Gate in `_execute_transactional_tool`

Inserted between Step 2 (capability check) and Step 3 (reserve idempotency):

1. Guard: `if snapshot.get("requires_identity_verification", False)` — IDV-04 envelope-driven, skips gate when false
2. Read `vst = _verified_session_token_var.get()` (ContextVar set by `build_tool_server` from Celery task arg in 17-05)
3. **Block 1 (no token):** If `not vst` — write one audit row (`error="identity_verification.required"`) and return `is_error` WITHOUT reaching `reserve_idempotency`
4. **Block 2 (invalid/expired):** Lazily import and call `check_verified_session(agent_id, vst, conn_str)` — if `False`, write one audit row (`error="identity_verification.invalid_or_expired"`) and return `is_error` WITHOUT reaching `reserve_idempotency`
5. **Pass:** Token valid → fall through to Step 3 (`reserve_idempotency`) unchanged

### Lazy Import Extension

The existing lazy import line at the top of `_execute_transactional_tool`:
```python
from app.services.agent_tools import _agent_id_var, _conn_str_var, _conversation_id_var
```
Extended to:
```python
from app.services.agent_tools import (
    _agent_id_var, _conn_str_var, _conversation_id_var, _verified_session_token_var,
)
```

`check_verified_session` is imported separately inside the IDV gate block itself (inner lazy import to break circular dependency chain, Pitfall 7).

### Six IDV Tests in `test_transactional_tools.py` (class `TestIDVGate`)

| Test | Scenario | Key Assertion |
|------|----------|---------------|
| `test_idv_blocks_without_session` | required=True, vst="" | `is_error=True`, adapter NOT called |
| `test_idv_blocks_expired_session` | required=True, invalid token, CVS→False | `is_error=True`, adapter NOT called |
| `test_idv_passes_with_valid_session` | required=True, valid token, CVS→True | no `is_error`, adapter called, `check_verified_session` called once |
| `test_idv_skipped_when_not_required` | required=False, vst="" | adapter called normally (IDV-04) |
| `test_idv_audit_row_written` | Both block paths | one audit row each with correct error string |
| `test_idv_before_idempotency` | required=True, vst="" | `reserve_idempotency` NOT called (T-17-21) |

## Verification

```
python -m pytest tests/unit/test_transactional_tools.py -x -q
89 passed in 7.21s
```

Source assertions confirmed:
- `_verified_session_token_var` in lazy import (indented, function-body only)
- `identity_verification.required` and `identity_verification.invalid_or_expired` errors present
- `check_verified_session` imported inside the function body (not module level)
- `snapshot.get("requires_identity_verification", False)` guard present
- Step 2.5 IDV gate at dispatcher offset 4580 < Step 3 reserve at offset 7322 (ordering OK)
- `reserve_idempotency` NOT called inside the IDV gate block

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Structural-assertion slice width too narrow after Step 2.5 insertion**
- **Found during:** Task 2 verification
- **Issue:** `TestFourNodeStructuralAssertion.test_actor_gate_called_before_get_adapter_in_dispatcher` used a hardcoded 14,000-char slice of the dispatcher body. Step 2.5 added ~1,500 chars, pushing `get_adapter_for_skill` beyond the slice boundary (`ValueError: substring not found`).
- **Fix:** Increased slice width from 14,000 to 20,000 chars in the structural assertion test, with an inline comment explaining the change.
- **Files modified:** `apps/api/tests/unit/test_transactional_tools.py`
- **Commit:** 9eae3c0

## Threat Mitigations Confirmed

| Threat ID | Category | Mitigation |
|-----------|----------|------------|
| T-17-13 | Elevation of Privilege (agent-prose bypass) | Gate is deterministic Python in Step 2.5; agent output not consulted |
| T-17-14 | Elevation of Privilege (replay/expiry) | `check_verified_session` queries `session_expires_at > NOW()` on every call |
| T-17-21 | DoS / logic (idempotency slot) | Gate runs BEFORE `reserve_idempotency`; blocked calls never consume the slot |
| T-17-01 | IDOR (cross-tenant session) | Lookup uses per-tenant `conn_str` (OD-1 boundary) |
| T-17-22 | Repudiation (missing audit) | Both block paths write exactly one `tool_calls_audit` row (AUD-01 symmetry) |

## Self-Check: PASSED

All required files exist on disk. All commits verified in git log:
- 02280de — test(17-06): add failing IDV enforcement gate tests
- 3d7f5a8 — feat(17-06): insert Step 2.5 IDV gate in _execute_transactional_tool
- 9eae3c0 — test(17-06): IDV enforcement + ordering + audit + IDV-04 skip coverage

Test run: 89 passed, 0 failed.
