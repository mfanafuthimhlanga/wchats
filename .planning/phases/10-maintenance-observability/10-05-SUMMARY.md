---
phase: 10-maintenance-observability
plan: "05"
subsystem: test-suite
tags: [testing, unit-tests, observability, digest, alerts, xfail-removal]
dependency_graph:
  requires: [10-01, 10-02, 10-03]
  provides: [OPS-05]
  affects: [test_digest_service, test_alert_service, test_observability_routes]
tech_stack:
  added: []
  patterns:
    - "_make_sync_db_ctx helper for sync DB context manager mocking (from test_deployment_task.py)"
    - "monkeypatch.setattr for settings mutation in tests (not whole-object patch)"
    - "patch.object(settings, attr) for single-field settings patch"
    - "MagicMock() for SQLAlchemy execute result (keeps scalars().all() sync)"
    - "Direct db=mock_db injection to check_and_write_alerts (no get_sync_db patch)"
key_files:
  modified:
    - apps/api/tests/unit/test_digest_service.py
    - apps/api/tests/unit/test_alert_service.py
    - apps/api/tests/unit/test_observability_routes.py
decisions:
  - "psycopg2.connect patched at app.services.digest_service module boundary (not smtplib global)"
  - "db.execute.return_value.fetchone (not scalar_one_or_none) — matches actual digest_service.py implementation"
  - "AsyncMock execute result must be MagicMock for SQLAlchemy .scalars().all() to remain sync"
  - "check_and_write_alerts db passed directly — alert_service.py has no get_sync_db import"
  - "Default settings thresholds (ALERT_FAITHFULNESS_THRESHOLD=0.6, ALERT_RED_TEAM_CRITICAL_COUNT=1) used without override"
metrics:
  duration: "~15 min"
  completed: "2026-05-25"
  tasks: 3
  files_modified: 3
---

# Phase 10 Plan 05: De-xfail Unit Tests (Digest, Alert, Observability Routes) Summary

De-xfailed all 9 xfail stubs from 10-01 with correct implementations for digest service, alert service, and observability route tests.

## Tasks Completed

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | test_digest_service.py — 4 tests | f44712d | apps/api/tests/unit/test_digest_service.py |
| 2 | test_alert_service.py — 3 tests | 00d29dc | apps/api/tests/unit/test_alert_service.py |
| 3 | test_observability_routes.py — 2 tests | b48a500 | apps/api/tests/unit/test_observability_routes.py |

## What Was Built

All 9 xfail stubs from plan 10-01 are now real passing tests:

**test_digest_service.py (4 tests):**
- `test_collect_digest_stats_shape` — calls `_collect_digest_stats(agent_id, conn_str, db)` with 3 args; patches `psycopg2.connect` at module boundary; asserts result contains required keys
- `test_send_digest_email_calls_smtp` — patches `app.services.digest_service.smtplib.SMTP` (module boundary, not global); uses `monkeypatch.setattr` for settings; asserts `sendmail.assert_called_once()`
- `test_digest_beat_skips_when_disabled` — uses `patch.object(settings, 'DIGEST_ENABLED', False)`; calls `.run()` directly; asserts `{"dispatched": 0}`
- `test_digest_idempotency_within_7d` — uses `_make_sync_db_ctx` helper; sets `fetchone.return_value = MagicMock()` (row found = skip); asserts `{"status": "already_sent"}` and `send_digest_email` not called

**test_alert_service.py (3 tests):**
- `test_eval_regression_triggers_alert` — injects `db=mock_db` directly; `faithfulness=0.4` (below 0.6); asserts `mock_db.add.assert_called_once()` and `alert_type == "eval_regression"`
- `test_red_team_critical_triggers_alert` — `critical_red_team_count=2` (>= 1); asserts `alert_type == "red_team_critical"`
- `test_no_alert_when_thresholds_met` — `faithfulness=0.95, count=0`; asserts `not mock_db.add.called`

**test_observability_routes.py (2 tests):**
- `test_get_alerts_returns_list` — ASGITransport + dependency_overrides; `MagicMock()` for execute result (sync `.scalars().all()`); asserts 200 + list response
- `test_get_alerts_idor_guard` — attacker tenant_id != agent.tenant_id; asserts status in (401, 403); `dependency_overrides.clear()` teardown present

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed AsyncMock mock chain for SQLAlchemy execute result in test_observability_routes.py**
- **Found during:** Task 3 verification run
- **Issue:** `mock_db = AsyncMock()` made `db.execute.return_value.scalars` a coroutine, but `observability.py` calls `result.scalars().all()` synchronously (SQLAlchemy `CursorResult.scalars()` is not async). Test XPASS(strict) masked this — xfail was passing for the wrong reason.
- **Fix:** Changed `mock_db.execute.return_value.scalars.return_value.all.return_value = []` to `mock_result = MagicMock(); mock_result.scalars.return_value.all.return_value = []; mock_db.execute.return_value = mock_result`
- **Files modified:** apps/api/tests/unit/test_observability_routes.py
- **Commit:** b48a500

## Verification Results

```
test_digest_service.py  — 4 passed
test_alert_service.py   — 3 passed
test_observability_routes.py — 2 passed
Combined: 9 passed, 0 xfailed, 0 failed
Full unit suite: 363 passed, 49 failed (49 pre-existing — identical to baseline before this plan)
```

## Known Stubs

None — all 9 tests exercise real implementations.

## Threat Flags

None — test files only; no new network endpoints, auth paths, or schema changes introduced.

## Self-Check: PASSED

- apps/api/tests/unit/test_digest_service.py — exists, no xfail decorators, 4 tests pass
- apps/api/tests/unit/test_alert_service.py — exists, no xfail decorators, 3 tests pass
- apps/api/tests/unit/test_observability_routes.py — exists, no xfail decorators, 2 tests pass
- Commits f44712d, 00d29dc, b48a500 — all verified in git log
