---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
plan: "03"
subsystem: agent-runtime
tags: [idv, contextvars, celery, agent-tools, transport-rail]
dependency_graph:
  requires: [17-01]
  provides: [_verified_session_token_var ContextVar, build_tool_server verified_session_token param, run_agent_turn 5th positional param]
  affects: [17-06-enforcement-gate, apps/api/app/services/transactional/tools.py]
tech_stack:
  added: []
  patterns: [ContextVar task-scoped isolation, NEVER-log token parity with message T-04-03-05]
key_files:
  created: [apps/api/tests/unit/test_agent_tools_contextvar.py (extended)]
  modified:
    - apps/api/app/services/agent_tools.py
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/tests/unit/test_agent_tools_contextvar.py
decisions:
  - "[17-03] _verified_session_token_var uses empty-string default — means 'no verified session, all non-IDV tool calls pass through' (IDV-05)"
  - "[17-03] verified_session_token added as LAST kwarg of build_tool_server (after tenant_id) — all existing call sites are backward-compatible"
  - "[17-03] Token set via _verified_session_token_var.set() alongside other ContextVar sets in build_tool_server body — not logged anywhere (T-04-03-05)"
  - "[17-03] run_agent_turn 5th param has default '' — existing 4-arg apply_async dispatches remain valid until 17-05 wires the call"
metrics:
  duration: "~10 min"
  completed: "2026-07-01"
  tasks: 3
  files: 3
status: complete
---

# Phase 17 Plan 03: IDV-05 ContextVar Transport Rail Summary

**One-liner:** `_verified_session_token_var` ContextVar + `build_tool_server` / `run_agent_turn` param threading for IDV-05 enforcement plumbing — fully backward-compatible, token never logged.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Add `_verified_session_token_var` ContextVar + `build_tool_server` param | `1348c2e` | `apps/api/app/services/agent_tools.py` |
| 2 | Thread `verified_session_token` through `run_agent_turn` | `0b74b90` | `apps/api/app/worker/tasks/runtime/agent.py` |
| 3 | Unit tests: ContextVar isolation + no-log (3 new tests, 6 total pass) | `694fb4e` | `apps/api/tests/unit/test_agent_tools_contextvar.py` |

## What Was Built

The IDV-05 transport rail carries a customer's verified session token from the Celery task arg into a task-scoped ContextVar, so the transactional dispatcher (Plan 17-06) can read it deterministically at enforcement time. This plan adds NO enforcement logic — only the transport rail.

**`apps/api/app/services/agent_tools.py`**
- New `_verified_session_token_var: ContextVar[str] = ContextVar("verified_session_token", default="")` declared after `_retrieve_call_count_var` with a comment explaining the empty-string default means "no verified session — all non-IDV tool calls pass through" (IDV-05, Phase 17).
- `build_tool_server` gains `verified_session_token: str = ""` as last keyword param (after `tenant_id`).
- Body calls `_verified_session_token_var.set(verified_session_token)` alongside the other ContextVar sets.
- Token does NOT appear in the `build_tool_server.ready` debug log line or any other log call.

**`apps/api/app/worker/tasks/runtime/agent.py`**
- `run_agent_turn` gains `verified_session_token: str = ""` as 5th positional param after `conversation_id`.
- Docstring "Task args" list updated: `verified_session_token` listed with explicit NEVER logged note (parity with `message`, T-04-03-05).
- `build_tool_server(...)` call extended to pass `verified_session_token=verified_session_token`.
- Token absent from all `structlog` / `log.*` lines.

**`apps/api/tests/unit/test_agent_tools_contextvar.py`**
- `test_verified_session_token_var_default_empty`: asserts the ContextVar defaults to `""` in a fresh `copy_context()`.
- `test_build_tool_server_sets_verified_session_token`: calls `build_tool_server` with `verified_session_token="tok_abc"` and asserts the ContextVar reads back `"tok_abc"`.
- `test_default_empty_when_omitted`: omits the arg entirely and asserts ContextVar stays `""` (backward compatibility).
- All 6 tests pass (3 pre-existing isolation tests + 3 new IDV-05 tests).

## Verification

```
python -m pytest tests/unit/test_agent_tools_contextvar.py -x -q
6 passed in 17.37s
```

Grep confirms `verified_session_token` appears in zero `log.*` calls in `agent_tools.py` and `agent.py`.

Signature inspection confirms:
- `inspect.signature(build_tool_server).parameters` includes `verified_session_token` with default `""`
- `inspect.signature(run_agent_turn.run).parameters` includes `verified_session_token` with default `""`

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Verified

| Threat ID | Category | Mitigation Status |
|-----------|----------|-------------------|
| T-17-11 | Information Disclosure (token in logs) | Verified: zero `log.*` references to `verified_session_token` in both files |
| T-17-12 | Tampering (ContextVar cross-request bleed) | Verified: `ContextVar.set()` scoped to task context; `test_two_context_no_bleed` (pre-existing) + `test_default_empty_when_omitted` (new) confirm isolation |
| T-17-17 | Spoofing (agent fabricating token) | Accept: token origin is the Celery task arg only; enforcement gate in 17-06 prevents agent from setting ContextVar |

## Self-Check: PASSED

- `apps/api/app/services/agent_tools.py` — modified and committed `1348c2e`
- `apps/api/app/worker/tasks/runtime/agent.py` — modified and committed `0b74b90`
- `apps/api/tests/unit/test_agent_tools_contextvar.py` — extended and committed `694fb4e`
- All 3 commits exist: `git log --oneline -5` confirms `694fb4e`, `0b74b90`, `1348c2e`
- 6 tests pass; token absent from all log calls in both source files
