---
plan: 10-02
phase: 10-maintenance-observability
status: complete
commits:
  - 0f3e0cd
  - 796ab86
---

## Summary

Weekly digest pipeline: digest_service.py + digest.py Celery tasks + celery_app.py wiring.

## What was built

- **app/services/digest_service.py** — `_collect_digest_stats` collects 4 metrics (conversation_count, faithfulness_score, critical_red_team_count, escalation_count) from control DB + tenant DB; `send_digest_email` sends plain-text SMTP email (fire-and-forget, same pattern as escalation.py).
- **app/worker/tasks/runtime/digest.py** — `run_weekly_digest_beat` (acks_late, fans out per `is_deployed=True` agent) + `run_weekly_digest` (acks_late, 7-day idempotency via `digest_runs`, fetches conn_str at runtime — CTL-08).
- **app/worker/celery_app.py** — Added `app.worker.tasks.runtime.digest` to include list; added `digest-weekly` beat entry (`crontab(hour=6, minute=0, day_of_week=0)` = Sunday 06:00 UTC).

## Verification

- `python -c "from app.worker.tasks.runtime.digest import run_weekly_digest_beat, run_weekly_digest; print('OK')"` — OK

## Self-Check: PASSED
