---
plan: 10-01
phase: 10-maintenance-observability
status: complete
commits:
  - a6d05e2
  - 0b4c251
---

## Summary

Migration 0012, Alert ORM, OPS Settings, OPS-01 beat fix, and 9 xfail stubs.

## What was built

- **alembic/versions/0012_alerts_digest_runs.py** — control DB migration creating `alerts` table (id, agent_id FK, alert_type, severity, message, triggered_at, resolved_at) and `digest_runs` table (id, agent_id FK, sent_at, payload JSONB). All DDL uses `IF NOT EXISTS` guards. Indexes on agent_id and resolved_at.
- **app/models/alert.py** — Alert SQLAlchemy ORM model mapped to `alerts` table with all columns.
- **app/core/config.py** — Added `ALERT_FAITHFULNESS_THRESHOLD: float = 0.6`, `ALERT_RED_TEAM_CRITICAL_COUNT: int = 1`, `DIGEST_ENABLED: bool = True`.
- **app/worker/tasks/runtime/red_team.py** — `run_red_team_beat` now queries `Agent.is_deployed == True` (was `Agent.status == 'ready'`) — OPS-01 partial.
- **tests/unit/test_digest_service.py** — 4 xfail stubs (stats shape, SMTP send, DIGEST_ENABLED=False, 7d idempotency).
- **tests/unit/test_alert_service.py** — 3 xfail stubs (eval regression alert, red_team critical alert, no-alert path).
- **tests/unit/test_observability_routes.py** — 2 xfail stubs (GET /alerts 200 list, IDOR 401/403).

## Verification

- `pytest tests/unit/test_strategy_service.py` — 5 passed (config import sanity)
- `pytest test_digest_service.py test_alert_service.py test_observability_routes.py` — 9 xfailed

## Self-Check: PASSED
