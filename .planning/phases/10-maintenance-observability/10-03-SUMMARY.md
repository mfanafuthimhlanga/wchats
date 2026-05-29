---
plan: 10-03
phase: 10-maintenance-observability
status: complete
commits:
  - ad80cf8
  - feb432d
---

## Summary

Alert pipeline: alert_service.py + alert.py Celery tasks + observability FastAPI routes + main.py wiring.

## What was built

- **app/services/alert_service.py** — `check_and_write_alerts`: evaluates faithfulness < ALERT_FAITHFULNESS_THRESHOLD → eval_regression alert; critical_count >= ALERT_RED_TEAM_CRITICAL_COUNT → red_team_critical alert. Duplicate active-alert guard. `send_alert_email` fire-and-forget SMTP.
- **app/models/alert.py** — Fixed DateTime(timezone=True) (TIMESTAMPTZ import unavailable in this SQLAlchemy version).
- **app/worker/tasks/runtime/alert.py** — `run_alert_check_beat` (acks_late, fans out per deployed agent) + `run_alert_check` (acks_late, per-agent).
- **app/worker/celery_app.py** — alert task include + `alert-daily` beat (crontab daily 04:00 UTC).
- **app/api/v1/observability.py** — `GET /agents/{id}/alerts` (unresolved list) + `POST /agents/{id}/alerts/{id}/resolve`. IDOR guard returns 403.
- **app/main.py** — observability router registered.

## Verification

- All 3 imports verified: alert_service, alert task, observability router

## Self-Check: PASSED
