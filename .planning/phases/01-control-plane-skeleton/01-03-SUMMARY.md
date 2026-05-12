---
phase: "01-control-plane-skeleton"
plan: "03"
subsystem: "neon-provisioning, celery-chain"
tags: ["neon", "alembic", "celery", "idempotency", "fernet", "sse-events", "provision", "migrations"]
dependency_graph:
  requires:
    - "apps/api/app/core/security.py — fernet_encrypt, fernet_decrypt"
    - "apps/api/app/services/events.py — emit() helper"
    - "apps/api/app/worker/celery_app.py — celery_app instance, pipeline queue"
    - "apps/api/app/core/database.py — get_sync_db()"
    - "apps/api/app/models/agent.py — Agent with neon_project_id, neon_connection_string, neon_direct_connection_string"
    - "apps/api/app/models/job.py — Job with status, started_at, finished_at, error"
    - "apps/api/alembic_tenant/ — tenant migration scripts for run_tenant_migrations()"
  provides:
    - "apps/api/app/services/neon.py — create_neon_project(), wait_for_neon_ready()"
    - "apps/api/app/services/migrations.py — run_tenant_migrations(), get_current_alembic_revision()"
    - "apps/api/app/worker/tasks/pipeline/provision.py — provision_neon Celery task"
    - "apps/api/app/worker/tasks/pipeline/migrations.py — apply_migrations Celery task"
  affects:
    - "Wave 4 (01-04) — FastAPI POST /agents route dispatches this chain"
    - "Wave 5 (01-05) — SSE endpoint streams all 6 events emitted by this chain"
    - "All future Celery tasks — idempotency + acks_late pattern established here"
tech_stack:
  added: []
  patterns:
    - "Neon operation polling: op.status not in (finished, skipped, cancelled, failed) — not project status"
    - "Dual URI fetch: pooled=True for app traffic, pooled=False for Alembic DDL"
    - "Idempotency save point: write agent.neon_project_id to DB immediately after API returns, before polling"
    - "Absolute script_location via Path(__file__).parent.parent.parent / 'alembic_tenant'"
    - "Connection injection: alembic_cfg.attributes['connection'] = connection (NullPool engine)"
    - "Module-level _redis = redis.from_url(settings.REDIS_URL) shared per worker process"
    - "T-03-02: log only project_id; never log connection URIs or raw Neon API response"
key_files:
  created:
    - "apps/api/app/services/neon.py"
    - "apps/api/app/services/migrations.py"
    - "apps/api/app/worker/tasks/pipeline/provision.py"
    - "apps/api/app/worker/tasks/pipeline/migrations.py"
  modified: []
decisions:
  - "provision_neon writes agent.neon_project_id immediately after Neon API returns (before polling) — idempotency save point for kill-9 recovery"
  - "apply_migrations uses agent.neon_direct_connection_string (not neon_connection_string) — DDL requires non-pooled direct endpoint"
  - "Neon 4xx errors: fatal (no retry), both agent.status and job.status set to failed — Pitfall 5 fully handled"
  - "_TERMINAL_STATUSES = frozenset({'finished', 'skipped', 'cancelled', 'failed'}) — all four Neon terminal statuses covered"
  - "wait_for_neon_ready runs in apply_migrations (not in provision_neon) — probe before Alembic, not before URI fetch"
metrics:
  duration: "~7 minutes"
  completed_date: "2026-05-12"
  tasks_completed: 3
  files_created: 4
---

# Phase 01 Plan 03: Neon Provisioning Service, Migration Service, and Celery Chain Tasks Summary

M1 Celery chain implemented: `provision_neon` creates a Neon project with idempotent write-order safety, dual URI encryption, and all required SSE events; `apply_migrations` fetches the direct connection string from the control DB, probes readiness, and runs programmatic Alembic upgrade head.

## Tasks Completed

| # | Name | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Neon service and migration service | 4309bbe | app/services/neon.py, app/services/migrations.py |
| 2 | provision_neon Celery task with idempotency guard and correct write-order | c1e2526 | app/worker/tasks/pipeline/provision.py |
| 3 | apply_migrations Celery task — fetches conn string from DB, runs Alembic | d931f09 | app/worker/tasks/pipeline/migrations.py |

## Deviations from Plan

None — plan executed exactly as written.

All must_haves verified:
- Idempotency guard: `if agent.neon_project_id: return early` before any Neon API call
- Write-order: `agent.neon_project_id` written and committed immediately after `create_neon_project()` returns, before polling operations or encrypting URIs
- Operations polling: `op.status not in _TERMINAL_STATUSES` (finished, skipped, cancelled, failed)
- Dual URI: `pooled=True` → `neon_connection_string`, `pooled=False` → `neon_direct_connection_string`, both Fernet-encrypted as BYTEA
- Event order: `job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`
- apply_migrations fetches connection string from DB by agent_id via `fernet_decrypt(agent.neon_direct_connection_string)`
- apply_migrations runs `wait_for_neon_ready()` probe before Alembic
- apply_migrations writes `agent.schema_version` and `agent.status='ready'` on success
- Both tasks: `acks_late=True`, `max_retries=3`, `bind=True`
- Neon 4xx: fatal (no retry), sets both `agent.status='failed'` and `job.status='failed'`, emits `job.failed`
- Neon 5xx/timeout: `self.retry(exc=exc, countdown=2**self.request.retries)`
- Migration errors: fatal (no retry), same failure path as 4xx

## Verification Results

All 5 plan-specified checks pass:

1. `python -c "from app.worker.tasks.pipeline.provision import provision_neon; assert provision_neon.acks_late == True; print('provision_neon OK')"` — PASSED
2. `python -c "from app.worker.tasks.pipeline.migrations import apply_migrations; assert apply_migrations.acks_late == True; print('apply_migrations OK')"` — PASSED
3. `grep -c "neon_connection_string" app/worker/tasks/pipeline/migrations.py` — 0 (PASSED — only `neon_direct_connection_string` referenced)
4. `grep "neon_direct_connection_string" app/worker/tasks/pipeline/migrations.py` — present (PASSED)
5. `python -c "from app.services.neon import create_neon_project, wait_for_neon_ready; from app.services.migrations import run_tenant_migrations; print('services OK')"` — PASSED

## Known Stubs

None. All four files implement full production logic:
- `neon.py`: real Neon API calls, real operation polling, real URI fetch
- `migrations.py`: real programmatic Alembic with absolute path and connection injection
- `provision.py`: complete idempotency guard, event emission, failure modes
- `pipeline/migrations.py`: complete idempotency guard, probe, Alembic run, failure modes

No hardcoded connection strings, no placeholder returns, no TODO blocks in execution paths.

## Threat Flags

No new network endpoints or auth paths beyond the plan's threat model.

Threat mitigations implemented:

| Threat | Mitigation Applied |
|--------|-------------------|
| T-03-01: connection URI in return value | provision_neon returns only {agent_id, project_id} — verified |
| T-03-02: connection URI in logs | No log calls pass conn strings; only project_id logged |
| T-03-06: Neon API key in error messages | Exception handlers log only status_code and sanitised message |

## Self-Check: PASSED

Files verified to exist:
- apps/api/app/services/neon.py — FOUND
- apps/api/app/services/migrations.py — FOUND
- apps/api/app/worker/tasks/pipeline/provision.py — FOUND
- apps/api/app/worker/tasks/pipeline/migrations.py — FOUND

Commits verified:
- 4309bbe — Task 1: Neon service + migration service
- c1e2526 — Task 2: provision_neon task
- d931f09 — Task 3: apply_migrations task
