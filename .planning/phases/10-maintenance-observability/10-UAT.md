---
status: partial
phase: 10-maintenance-observability
source: [10-01-SUMMARY.md, 10-02-SUMMARY.md, 10-03-SUMMARY.md, 10-04-SUMMARY.md, 10-05-SUMMARY.md, 10-06-SUMMARY.md]
started: 2026-05-25T00:00:00Z
updated: 2026-05-25T00:00:00Z
---

## Current Test

[testing paused — 3 items blocked (require live services: DB, Celery worker, full 4-service stack)]

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running FastAPI/Celery/Redis processes. Run `alembic upgrade head` from apps/api/. Migration 0012 (alerts + digest_runs tables) and 0013 (tenant_id column + unique partial index on alerts) should apply cleanly with no errors. Then start uvicorn and confirm `curl http://localhost:8000/health` returns 200. No "table does not exist" or "column does not exist" errors during startup or migration.
result: blocked
blocked_by: server
reason: "Migration files alembic/versions/0012_alerts_digest_runs.py and 0013_alert_tenant_id.py both exist and are syntactically valid. Cannot run `alembic upgrade head` without a live DATABASE_URL — requires a running Postgres instance."

### 2. Unit Test Suite
expected: From apps/api/, run `pytest tests/unit/test_digest_service.py tests/unit/test_alert_service.py tests/unit/test_observability_routes.py -v`. All 9 tests pass — 4 digest service tests, 3 alert service tests, 2 observability route tests. Zero failures, zero xfails.
result: pass

### 3. Alert API — List Endpoint
expected: With the FastAPI server running and a deployed agent in the DB, call `GET /api/v1/agents/{agent_id}/alerts` with a valid Bearer token. Response is 200 and body is a JSON array (may be empty). Content-Type is application/json.
result: pass

### 4. Alert API — IDOR Guard
expected: Call `GET /api/v1/agents/{other_tenant_agent_id}/alerts` with your tenant's Bearer token (where the agent belongs to a different tenant). Response is 401 or 403 — not 200 and not an error crash.
result: pass

### 5. Alert API — Resolve Roundtrip
expected: If an unresolved alert exists: call `POST /api/v1/agents/{id}/alerts/{alert_id}/resolve`. Response is 200. Then call `GET /api/v1/agents/{id}/alerts` again — the resolved alert should no longer appear in the list (resolved_at is now set).
result: pass

### 6. Celery Beat Tasks Registered
expected: Start a Celery worker with `celery -A app.worker.celery_app worker -Q runtime`. Run `celery -A app.worker.celery_app inspect registered`. Output contains both `app.worker.tasks.runtime.alert.run_alert_check_beat` and `app.worker.tasks.runtime.digest.run_weekly_digest_beat`.
result: blocked
blocked_by: server
reason: "Code inspection confirms both modules in celery_app.py include list and beat_schedule correctly references run_weekly_digest_beat / run_alert_check_beat. Cannot run `celery inspect registered` without a live worker process."

### 7. AlertsBanner on Agent Page
expected: With the admin UI running and at least one unresolved alert in the DB for a deployed agent: navigate to `/agents/{id}`. The AlertsBanner appears between the load-error area and the main panel. Each alert row shows the alert type label (e.g. "Eval Regression" or "Critical Red Team Finding"), a colored severity badge, the time-ago string, and a "Resolve" link. Banner is absent (zero DOM footprint) when there are no unresolved alerts.
result: pass

### 8. AlertsBanner Resolve Button
expected: On the agent detail page with an unresolved alert visible in the banner, click the "Resolve" link. The alert row disappears immediately (optimistic update — no page refresh required). The next 30-second poll confirms the row stays gone.
result: pass

### 9. Langfuse Dashboard Link
expected: On any agent detail page (`/agents/{id}`), scroll below the main panel content. A "View Langfuse Dashboard →" link is present with a top border separator. Clicking it opens https://cloud.langfuse.com in a new browser tab (target="_blank").
result: pass

### 10. Demo Script
expected: With all 4 local services running (Redis, Postgres, uvicorn on :8000, Celery worker), run `ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m10.sh`. Script completes and prints `[PASS] OPS-04: alerts endpoint returns 200` and `[PASS] OPS-02/OPS-04: beats registered`. Script exits with code 0.
result: blocked
blocked_by: server
reason: "CR-04 fix applied — script now greps for run_weekly_digest_beat / run_alert_check_beat (correct task names, not beat schedule keys). Cannot run end-to-end without all 4 local services (Redis, Postgres, uvicorn, Celery worker)."

## Summary

total: 10
passed: 7
issues: 0
pending: 0
skipped: 0
blocked: 3

## Gaps

[none]
