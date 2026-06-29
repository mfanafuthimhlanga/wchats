---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "04"
subsystem: transactional-tools
tags: [transactional, tool-contract, capability, actor-seam, idempotency, audit, mcp]
dependency_graph:
  requires: ["14-01", "14-02", "14-03"]
  provides: ["transactional-tool-execution-path", "mcp-tool-registration"]
  affects: ["customer-agent-runtime", "tool-server"]
tech_stack:
  added: []
  patterns:
    - "single-dispatcher pattern (enforcement order encoded once for all 6 mutating tools)"
    - "lazy-import circular-dependency break (tools.py imports agent_tools ContextVars at call time)"
    - "AUD-01 symmetry (capability denial writes audit row before returning is_error)"
    - "idempotency-before-seam replay optimization (skip Haiku gate on cache hit)"
    - "client-generated UUID for PendingConfirmation (avoid DB flush/refresh dependency)"
key_files:
  created:
    - apps/api/app/services/transactional/tools.py
    - apps/api/tests/unit/test_transactional_tools.py
  modified:
    - apps/api/app/services/agent_tools.py
    - apps/api/app/worker/tasks/runtime/agent.py
decisions:
  - "confirm_action is mutating=False — writes pending_confirmations row, no provider adapter, no idempotency key (Open Question 1 resolved)"
  - "Idempotency LOOKUP hoisted before actor seam — replay short-circuits before Haiku gate call (Open Question 2 resolved; capability check still runs first on every call)"
  - "AUD-01 symmetry: capability denial now also writes a tool_calls_audit row with error='capability.denial:<reason>' — every tool entry produces exactly one audit row"
  - "Lazy import of agent_tools ContextVars inside dispatcher body avoids circular import (tools.py ← agent_tools.py ← tools.py)"
  - "PendingConfirmation.id generated client-side with uuid4() so str(row.id) works before DB flush/refresh"
metrics:
  duration_seconds: 1200
  completed_at: "2026-06-29T18:45:00Z"
  tasks_completed: 3
  files_created: 2
  files_modified: 2
status: complete
---

# Phase 14 Plan 04: Transactional Tool Handlers + Registration Summary

Wire the L1 contract to a real execution path: `_execute_transactional_tool` dispatcher + 6 mutating handlers + `confirm_action_tool`, registered into the customer-agent tool loop.

## What Was Built

### `transactional/tools.py` — Core deliverable

**`_execute_transactional_tool(skill, validated, raw_args, adapter_method)`** encodes the enforcement order ONCE for all 6 mutating tools:

1. **Capability check** (fail-closed) — any denial writes an audit row (`capability.denial:<reason>`) and returns `is_error`. AUD-01 symmetry now covers 100% of tool entries.
2. **Idempotency lookup** (short-circuit on hit) — replay returns the stored result BEFORE calling `call_actor_gate`. Capability check still runs first, but the Haiku gate is skipped on replays (cost/latency optimization, documented per plan guidance).
3. **Actor seam** — `call_actor_gate` is awaited before the adapter on every fresh mutating execution. Block path: write audit row with `actor_decision="block"`, `error="actor_block"`, return `is_error`. Seam is unbypassable.
4. **Adapter execute** — `getattr(get_adapter(agent_id), adapter_method)(validated, agent_id)` in `try/except`, capturing `latency_ms` and `error_str`.
5. **Audit row** — `write_audit_row` called on BOTH success and error paths (AUD-01).
6. **Store idempotency** — on success: `store_idempotency` (ON CONFLICT DO NOTHING), then return `tool_response`.

**6 mutating `@tool` handlers** (`place_order_tool`, `cancel_order_tool`, `issue_refund_tool`, `update_subscription_tool`, `book_slot_tool`, `update_customer_record_tool`): each validates Pydantic input (ValidationError → `is_error` with no DB calls) and delegates to the shared dispatcher.

**`confirm_action_tool`** (mutating=False): validates `ConfirmActionInput`, writes a `PendingConfirmation` row (client-generated UUID, 24-hour TTL, `resolved_at`/`resolution` NULL). No provider adapter, no idempotency key. Duplicate-confirm dedup deferred to Phase 18.

**Registry attachment**: After all 7 handlers are defined, `TOOL_REGISTRY[skill].sdk_tool = handler` for each. Registry is now the single source linking metadata ↔ SdkMcpTool.

### `agent_tools.py` — `build_tool_server` extended

Lazy-imports the 7 new handlers inside `build_tool_server` (deferred import to break circular dependency). Appends to the `tools=[...]` argument of `create_sdk_mcp_server`, bringing total tool count from 4 to 11. Original 4 tools (`retrieve`, `lookup_structured`, `escalate_to_human`, `clarify`) retained per TXN-04.

### `agent.py` — `allowed_tools` extended

7 new `mcp__customer-tools__*` entries added to `allowed_tools`. Comment clarifies that listing suppresses SDK permission prompts only; the fail-closed capability envelope in each handler is the real gate (T-14-04-03).

### `test_transactional_tools.py` — 30 tests, all green

Full TDD RED→GREEN cycle across 3 tasks:
- Task 1: bad-schema rejection (no DB calls), capability denial with AUD-01 audit symmetry, idempotency replay (adapter once + single audit), actor block (no adapter + audit), adapter error path
- Task 2: confirm_action writes pending_confirmations row (no adapter), TOOL_REGISTRY sdk_tool attachment
- Task 3: build_tool_server has 11 tools, agent.py has 7 new allowed_tool strings

Tests use `asyncio.run()` throughout (Python 3.12 closed-loop compliance). Module-level import of `agent_tools` is deferred inside `_set_context()` to avoid breaking `test_agent_tools.py`'s `sys.modules` monkeypatch guard.

## Deviations from Plan

### Auto-fixed Issues

**[Rule 1 - Bug] Windows encoding issue in source-file assertion test**
- **Found during:** Task 3 GREEN verification
- **Issue:** `open(agent_py_path)` defaulted to `cp1252` on Windows; agent.py contains UTF-8 em dashes (`—`) → `UnicodeDecodeError`
- **Fix:** Changed to `open(agent_py_path, encoding="utf-8")`
- **Files modified:** `tests/unit/test_transactional_tools.py`
- **Commit:** a386112

**[Rule 1 - Bug] Module-level import in test file broke test_agent_tools.py SDK monkeypatch**
- **Found during:** Task 3 GREEN - broader `-k` test run
- **Issue:** Original test file had `from app.services.agent_tools import _agent_id_var, _conversation_id_var` at module level. During pytest collection, this caused `claude_agent_sdk` to be imported before `test_agent_tools.py`'s `sys.modules` monkeypatch guard could install the fake SDK. When `test_transactional_tools.py` then ran its tests, `tools.py` was imported with the fake decorator (returning plain functions, not `SdkMcpTool`), causing `.handler` attribute errors.
- **Note:** The `test_agent_tools.py` test isolation fragility is pre-existing (confirmed: it fails in the full suite even without `test_transactional_tools.py`); the fix was to prevent making it worse.
- **Fix:** Moved the ContextVar import inside `_set_context()` (lazy import, executed only when the function is called, not at module collection time)
- **Files modified:** `tests/unit/test_transactional_tools.py`
- **Commit:** a386112

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced. All changes are within existing tool execution paths. Threat mitigations verified:

| Threat | Mitigation Verified |
|--------|---------------------|
| T-14-04-01 (bypassed actor seam) | `call_actor_gate` awaited in dispatcher before adapter on every fresh execution |
| T-14-04-02 (replay double-execute) | Idempotency short-circuit + `store_idempotency` ON CONFLICT DO NOTHING |
| T-14-04-03 (capability bypass via allowed_tools) | Comment in agent.py documents that listing != granting; handler enforces fail-closed |
| T-14-04-04 (cross-tenant audit rows) | `agent_id` sourced from per-call ContextVar |
| T-14-04-05 (confirm_action duplicates) | Accepted — Phase 18 deferred per plan |

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| `tools.py` exists | FOUND |
| `test_transactional_tools.py` exists | FOUND |
| RED commit 2696222 | FOUND |
| feat GREEN commit e36eca3 | FOUND |
| feat GREEN commit a386112 | FOUND |
| `pytest test_transactional_tools.py` | 30 passed |
| `pytest -k "transactional or capability or idempotency"` | 115 passed |
| `grep -c call_actor_gate tools.py` | 4 (≥ 1) |
| `grep -c check_capability_envelope tools.py` | 3 (≥ 1) |
| `grep -c mcp__customer-tools__place_order agent.py` | 1 (≥ 1) |
| `grep escalate_to_human agent.py` | 6 (retained) |
