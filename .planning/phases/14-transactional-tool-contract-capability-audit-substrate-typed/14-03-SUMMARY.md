---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "03"
subsystem: transactional-enforcement
tags: [capability-enforcement, idempotency, audit, fail-closed, redis-rate-limit, control-db]
dependency_graph:
  requires: ["14-01", "14-02"]
  provides: ["enforcement.check_capability_envelope", "idempotency.check_idempotency", "idempotency.store_idempotency", "audit.write_audit_row"]
  affects: ["14-04"]
tech_stack:
  added: []
  patterns: [fail-closed-enforcement, redis-incr-rate-limit, on-conflict-do-nothing, control-db-idempotency, write-always-audit]
key_files:
  created:
    - apps/api/app/services/transactional/enforcement.py
    - apps/api/app/services/transactional/idempotency.py
    - apps/api/app/services/transactional/audit.py
    - apps/api/tests/unit/test_capability_enforcement.py
    - apps/api/tests/unit/test_tool_idempotency.py
  modified: []
decisions:
  - "[14-03] enforcement.py uses _get_redis() lazy singleton for rate-limit counter — matches pattern from agent_tools.py"
  - "[14-03] check_capability_envelope is async despite sync DB access — consistent with tool handler call site (async context)"
  - "[14-03] idempotency.py uses raw sa_text INSERT with ::jsonb cast — avoids ORM model instantiation for ON CONFLICT DO NOTHING insert"
  - "[14-03] write_audit_row raises TypeError if capability_snapshot is not a plain dict — enforces Pitfall 4 from RESEARCH.md at runtime"
  - "[14-03] audit.py committed alongside enforcement.py in Task 1 — test file imports both at collection time; TDD gate requires both to exist"
metrics:
  duration: "~6 minutes"
  completed: "2026-06-29T16:01:54Z"
  tasks: 3
  files: 5
status: complete
---

# Phase 14 Plan 03: Enforcement + Idempotency + Audit Helpers Summary

Fail-closed capability envelope enforcement (CAP-02), durable control-DB idempotency with ON CONFLICT DO NOTHING replay (TXN-02), and complete success+error audit row coverage (AUD-01) — all unit-tested with 25 tests green.

## What Was Built

### Task 1: Capability Envelope Enforcement (`enforcement.py`) + Audit (`audit.py`)

**`_parse_rate_limit(rate_str)`** parses `"N/<unit>"` format (`minute`/`hour`/`day`) to `(max_calls, window_secs)`. Returns `None` for `None`/empty/malformed inputs.

**`check_capability_envelope(agent_id, skill, args)`** is the security-critical access-control gate:
- Reads `capability_envelopes` row from control DB via `get_sync_db()` scoped to `(agent_id, skill)`.
- **FAIL-CLOSED**: missing row → `("no_envelope_row")` denial; `enabled=false` → `("disabled")` denial.
- Rate limit (when set): computes `window_key = time() // window_secs`, `ratelimit:{agent_id}:{skill}:{window_key}` Redis INCR + expire; count > max → `("rate_limit")` denial.
- Constraint `max_amount_cents`: reads `args.amount_cents` or `args.refund_amount_cents`; exceeds limit → `("max_amount_cents")` denial.
- `scope_filters`: Phase-16 documented no-op.
- Every denial emits `structlog.warning("capability.denial", ...)`.
- On pass: returns `(snapshot_dict, None)` where snapshot is `dict(row)` — plain dict, not ORM.

**`write_audit_row(...)`** writes one `tool_calls_audit` row per execution:
- Called on both success and error paths (never skipped).
- `actor_decision = ""`, `actor_rationale = ""` in Phase 14 (Phase 15 fills them).
- Validates `capability_snapshot` is a plain dict at runtime (`TypeError` if not).
- Commits via `get_sync_db()`.

### Task 2: Idempotency Helpers (`idempotency.py`)

**`check_idempotency(agent_id, skill, idempotency_key)`**: SELECT from `tool_idempotency_keys` WHERE `(agent_id, skill, idempotency_key)`; returns stored result dict or `None` on miss.

**`store_idempotency(agent_id, skill, idempotency_key, result)`**: INSERT with `ON CONFLICT (agent_id, skill, idempotency_key) DO NOTHING`. Concurrent retries under `acks_late=True` are safe — one row wins, subsequent calls silently succeed.

Module docstring explicitly documents the per-tool vs turn-level guard distinction and the rationale for control-DB (not Redis) storage.

### Task 3: Audit Coverage (tests in `test_capability_enforcement.py`)

`TestWriteAuditRow` covers 4 scenarios: success-path row write, error-path row write, actor fields are empty strings, capability_snapshot round-trips as dict.

## Tests

| File | Tests | Result |
|------|-------|--------|
| `test_capability_enforcement.py` | 17 | PASS |
| `test_tool_idempotency.py` | 8 | PASS |
| **Total** | **25** | **GREEN** |

## Deviations from Plan

### Auto-combined deliveries

**[Rule 2 - Missing Critical] `audit.py` created in Task 1 commit alongside `enforcement.py`**
- **Found during:** Task 1 (TDD implementation)
- **Issue:** `test_capability_enforcement.py` imports `write_audit_row` from `audit.py` at module level (collection time). If `audit.py` did not exist, the entire test file would fail to collect and the RED→GREEN TDD gate could not work. Both modules were needed simultaneously.
- **Fix:** Created `audit.py` alongside `enforcement.py` in the same commit. The TDD RED phase still confirmed import failure (before either file existed), meeting the RED gate requirement.
- **Files modified:** `apps/api/app/services/transactional/audit.py` (new)
- **Commit:** `23e5997`

No other deviations — plan executed as specified.

## Source Assertions Verified

```
grep -c 'capability.denial' enforcement.py  → 5  (>= 3 required)
grep -c 'enabled'           enforcement.py  → 3  (>= 1 required)
grep -c 'ON CONFLICT'       idempotency.py  → 4  (>= 1 required)
grep -c 'tool_calls_audit'  audit.py        → 5  (>= 1 required)
grep -c 'import redis'      idempotency.py  → 0  (no Redis — required)
```

## Threat Mitigations Satisfied

| Threat ID | Mitigation Applied |
|-----------|-------------------|
| T-14-03-01 | Fail-closed: None/disabled row → denial; no pass-through path |
| T-14-03-02 | ON CONFLICT DO NOTHING in control-DB table (survives Redis restart) |
| T-14-03-03 | Snapshot scoped to calling agent_id; plain dict; no cross-tenant data |
| T-14-03-04 | write_audit_row called on both success and error paths |
| T-14-03-05 | Redis INCR per (agent_id, skill, window) — approximate under concurrency; exact deferred Phase 18 |

## Commits

| Hash | Message |
|------|---------|
| `23e5997` | feat(14-03): capability enforcement + audit row writer (TDD RED→GREEN) |
| `a4e7782` | feat(14-03): control-DB idempotency check/store with ON CONFLICT DO NOTHING (TDD RED→GREEN) |

## Known Stubs

None — all three helpers are fully implemented with real behavior. The only Phase-14 stub is `actor_decision/actor_rationale = ""` (intentional; documented in plan spec; Phase 15 fills them).

## Self-Check: PASSED

- `apps/api/app/services/transactional/enforcement.py` — FOUND
- `apps/api/app/services/transactional/idempotency.py` — FOUND
- `apps/api/app/services/transactional/audit.py` — FOUND
- `apps/api/tests/unit/test_capability_enforcement.py` — FOUND
- `apps/api/tests/unit/test_tool_idempotency.py` — FOUND
- Commit `23e5997` — FOUND
- Commit `a4e7782` — FOUND
- 25 tests GREEN
