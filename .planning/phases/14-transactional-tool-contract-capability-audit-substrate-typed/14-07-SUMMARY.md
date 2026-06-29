---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "07"
subsystem: transactional-enforcement
tags: [capability, enforcement, redis, tls, rate-limit, offload, asyncio]
status: complete

dependency_graph:
  requires:
    - 14-04-SUMMARY.md  # dispatcher + check_capability_envelope (facade caller)
  provides:
    - check_capability_access (side-effect-free authorization gate for 14-08 reorder)
    - apply_rate_and_constraint_checks (side-effecting rate-limit + constraint gate)
    - check_capability_envelope (retained facade — unchanged contract for 14-04 dispatcher)
    - REDIS_TLS_INSECURE config flag
  affects:
    - 14-08-PLAN.md  # can now reorder: access → idempotency → rate/constraint (WR-01 substrate)

tech_stack:
  added: []
  patterns:
    - asyncio.to_thread for blocking get_sync_db and Redis pipeline calls
    - Redis pipeline() for atomic INCR+EXPIRE (IN-01)
    - ssl.CERT_REQUIRED by default for rediss:// connections (WR-04)

key_files:
  created:
    - apps/api/tests/unit/test_transactional_offload.py
  modified:
    - apps/api/app/services/transactional/enforcement.py
    - apps/api/app/services/transactional/audit.py
    - apps/api/app/core/config.py
    - apps/api/tests/unit/test_capability_enforcement.py

decisions:
  - check_capability_access returns (snapshot, denial_or_None) with NO Redis; only the
    no_envelope_row + disabled fail-closed checks; enables 14-08 to run authorization
    before idempotency lookup without incurring a rate-limit side effect on replays
  - apply_rate_and_constraint_checks is purely side-effecting: pipeline INCR/EXPIRE + max_amount_cents
  - check_capability_envelope retained as a thin facade (access then rate/constraint) — the
    14-04 dispatcher (tools.py) and all existing tests call it unchanged
  - asyncio.to_thread chosen over loop.run_in_executor for simpler closure semantics;
    consistent with the plan guidance; blocking work captured in local lambdas before handoff
  - REDIS_TLS_INSECURE defaults False (verify-on); True requires explicit env var + warning log

metrics:
  duration: 29m
  completed: 2026-06-29
  tasks_completed: 2
  tasks_total: 2
  files_modified: 4
  files_created: 1
  commits: 2
---

# Phase 14 Plan 07: Enforcement Split + Redis Hardening Summary

Authorization split from the side-effecting rate-limit increment, Redis TLS hardened to CERT_REQUIRED by default, INCR/EXPIRE pipelined atomically, falsy-zero amount fixed, and blocking DB/Redis calls offloaded via asyncio.to_thread across enforcement.py and audit.py.

## What Was Built

### Task 1 — RED tests (commit `58c995a`)

- **`TestEnforcementSplit`** added to `test_capability_enforcement.py` (lazy deferred imports inside each method to keep the existing test classes unaffected at collection time):
  - `check_capability_access`: missing row → `no_envelope_row`, disabled → `disabled`, enabled → `(snapshot, None)` with NO Redis call asserted.
  - `apply_rate_and_constraint_checks`: over-limit → `rate_limit`, under-limit → `None`, `max_amount_cents` → denial.
  - **IN-02**: `amount_cents=0` + `refund_amount_cents=999` + `max_amount_cents=100` → passes (zero is a real amount, not falsy fallthrough).
  - **IN-01**: `redis.pipeline()` and `pipe.execute()` called; direct `redis.incr()` and `redis.expire()` NOT called.
  - **WR-04**: `rediss://` + `REDIS_TLS_INSECURE=False` → `ssl_cert_reqs=ssl.CERT_REQUIRED` captured from `from_url` kwargs; `REDIS_TLS_INSECURE=True` → TLS warning emitted.
  - Facade tests: `check_capability_envelope` still returns correct results for no_row, disabled, rate_limit, full pass.
- **`test_transactional_offload.py`** (new file) — WR-03 tracking wrapper: replaces `asyncio.to_thread` with a tracker that also executes the callable; asserts it is called for `write_audit_row`, `check_capability_access`, and `apply_rate_and_constraint_checks`.

### Task 2 — GREEN implementation (commit `1a482ae`)

**`app/core/config.py`:**
- Added `REDIS_TLS_INSECURE: bool = False` next to `REDIS_URL`; docstring notes MITM exposure and production restriction.

**`app/services/transactional/enforcement.py`** — full rewrite:
- `_get_redis()` hardened (WR-04): for `rediss://` URLs, passes `ssl_cert_reqs=ssl.CERT_REQUIRED` + `ssl_check_hostname=True` by default; when `REDIS_TLS_INSECURE=True`, uses `ssl.CERT_NONE` AND emits `log.warning("redis.tls_verification_disabled", ...)`.
- `check_capability_access(agent_id, skill) -> tuple[dict, str | None]`: blocking `_read_envelope()` lambda offloaded via `asyncio.to_thread`; applies only `no_envelope_row` and `disabled` fail-closed checks; no Redis access.
- `apply_rate_and_constraint_checks(agent_id, skill, snapshot, args) -> str | None`: blocking `_do_rate_limit_pipeline()` lambda offloaded via `asyncio.to_thread`; issues `pipe.incr(); pipe.expire(); pipe.execute()` atomically (IN-01); explicit `None`-check for `amount_cents` (IN-02).
- `check_capability_envelope(agent_id, skill, args) -> tuple[dict, str | None]`: retained thin facade — `await check_capability_access(...)` first; if denial returns immediately (no rate-limit incr on denied calls); else `await apply_rate_and_constraint_checks(...)`.

**`app/services/transactional/audit.py`** (WR-03):
- Added `import asyncio`.
- `write_audit_row`: blocking `get_sync_db` + `db.add(row)` + `db.commit()` wrapped in `_sync_write()` lambda, offloaded via `await asyncio.to_thread(_sync_write)`.

**`test_capability_enforcement.py`** — Rule 1 fix:
- `TestCheckCapabilityEnvelope::test_rate_limit_denial` mock updated from `mock_redis.incr.return_value = 3` to `mock_redis.pipeline.return_value = mock_pipe; mock_pipe.execute.return_value = [3, 1]` to match the new pipeline API.

## Verification Results

```
tests/unit/test_capability_enforcement.py    36 passed
tests/unit/test_transactional_offload.py      3 passed
tests/unit/test_transactional_tools.py       30 passed
-k "capability or enforcement or idempotency or transactional"  134 passed, 1 pre-existing error
python -c "... assert settings.REDIS_TLS_INSECURE is False; print('ok')"  → ok
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `TestCheckCapabilityEnvelope::test_rate_limit_denial` mock incompatible with pipelined INCR/EXPIRE**
- **Found during:** Task 2 GREEN (first test run)
- **Issue:** The existing test set up `mock_redis.incr.return_value = 3` which matched the old monolithic `redis_client.incr(redis_key)` API. After the split, `apply_rate_and_constraint_checks` uses `client.pipeline(); pipe.incr(); pipe.expire(); pipe.execute()`. The uninitialized pipeline mock returned `MagicMock()` from `execute()`, causing `MagicMock() > 2` → `TypeError`.
- **Fix:** Updated the mock to `mock_redis.pipeline.return_value = mock_pipe; mock_pipe.execute.return_value = [3, 1]` — the test intent (rate-limit denial when count exceeds max) is preserved.
- **Files modified:** `apps/api/tests/unit/test_capability_enforcement.py` (one test method in existing class)
- **Commit:** `1a482ae`

## Threat Surface Scan

| Flag | File | Description |
|------|------|-------------|
| threat_flag: tls_hardened | apps/api/app/services/transactional/enforcement.py | WR-04 mitigated: rediss:// now requires CERT_REQUIRED by default; REDIS_TLS_INSECURE=True requires explicit config + warning log |

T-14-07-01 (MITM via CERT_NONE) → **mitigated** by _get_redis() hardening.
T-14-07-02 (fail-open regression) → **mitigated**: facade returns denial before any side effect.
T-14-07-03 (falsy-zero bypass) → **mitigated** by IN-02 explicit None-check.
T-14-07-04 (TTL-less key leak) → **mitigated** by IN-01 pipeline.

## Self-Check: PASSED

All files confirmed present on disk. Both task commits exist in git log:
- `58c995a` test(14-07): RED — enforcement split, TLS posture, IN-01/IN-02, WR-03 offload
- `1a482ae` feat(14-07): enforcement split, Redis TLS verify, pipelined INCR/EXPIRE, falsy-zero fix, offload
