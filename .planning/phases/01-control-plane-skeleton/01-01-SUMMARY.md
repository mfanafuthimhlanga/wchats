---
phase: "01-control-plane-skeleton"
plan: "01"
subsystem: "foundation"
tags: ["project-skeleton", "orm", "alembic", "migrations", "config", "logging", "database"]
dependency_graph:
  requires: []
  provides:
    - "apps/api/pyproject.toml — dependency manifest with all M1 library versions"
    - "apps/api/app/core/config.py — Settings singleton importable by all modules"
    - "apps/api/app/core/database.py — async and sync SQLAlchemy engines + session factories"
    - "apps/api/app/core/logging.py — structlog JSON pipeline + pure ASGI RequestIdMiddleware"
    - "apps/api/app/models/* — four ORM models matching PRD §5 schema exactly"
    - "apps/api/alembic/versions/0001_control_db_initial.py — control DB migration"
    - "apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py — all 10 tenant v1 tables"
  affects:
    - "All subsequent plans in Phase 01 — imports Settings, Base, engines"
tech_stack:
  added:
    - "fastapi==0.136.1"
    - "pydantic-settings==2.14.1"
    - "sqlalchemy[asyncio]==2.0.49 (asyncpg driver for async engine)"
    - "alembic==1.18.4"
    - "asyncpg==0.31.0"
    - "psycopg2-binary==2.9.12"
    - "cryptography==48.0.0 (Fernet)"
    - "argon2-cffi==25.1.0"
    - "sse-starlette==3.4.4"
    - "structlog==25.5.0"
    - "sentry-sdk==2.59.0"
    - "celery[redis]==5.6.3"
    - "redis==6.4.0 (see deviations)"
    - "neon-api==0.3.0"
    - "httpx==0.28.1"
  patterns:
    - "pydantic-settings BaseSettings for 12-factor config"
    - "SQLAlchemy 2.x mapped_column() with Mapped[] type annotations"
    - "Pure ASGI middleware for structlog contextvars (not BaseHTTPMiddleware)"
    - "cfg.attributes['connection'] injection pattern for programmatic Alembic"
    - "NullPool on CLI Alembic path (Neon direct connections)"
key_files:
  created:
    - "apps/api/pyproject.toml"
    - "apps/api/app/core/config.py"
    - "apps/api/app/core/database.py"
    - "apps/api/app/core/logging.py"
    - "apps/api/app/models/base.py"
    - "apps/api/app/models/tenant.py"
    - "apps/api/app/models/agent.py"
    - "apps/api/app/models/job.py"
    - "apps/api/app/models/job_event.py"
    - "apps/api/alembic.ini"
    - "apps/api/alembic/env.py"
    - "apps/api/alembic/versions/0001_control_db_initial.py"
    - "apps/api/alembic_tenant/alembic.ini"
    - "apps/api/alembic_tenant/env.py"
    - "apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py"
    - ".gitignore"
  modified: []
decisions:
  - "redis==6.4.0 instead of redis==7.4.0 — celery[redis]==5.6.3 requires kombu 5.6.x which constrains redis<6.5"
  - "Tenant ORM attribute named api_key_hash (column: api_key) to prevent plaintext confusion in code"
  - "target_metadata=None in alembic_tenant/env.py — no ORM models for tenant schema in M1; all DDL via op.execute()"
metrics:
  duration: "~45 minutes"
  completed_date: "2026-05-12"
  tasks_completed: 3
  files_created: 15
---

# Phase 01 Plan 01: Project Skeleton, ORM Models, Alembic Migrations Summary

Wave 1 complete: FastAPI project skeleton with pydantic-settings config, dual SQLAlchemy engines (asyncpg for FastAPI, psycopg2 for Celery), structlog pure ASGI middleware, four ORM models matching PRD §5 exactly, and both Alembic migration sets with connection injection support.

## Tasks Completed

| # | Name | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Project skeleton, pyproject.toml, config, database, logging | c37a508 | pyproject.toml, core/config.py, core/database.py, core/logging.py |
| 2 | SQLAlchemy ORM models (all four control DB tables) | f681d5a | models/base.py, tenant.py, agent.py, job.py, job_event.py |
| 3 | Alembic control DB + tenant DB migrations | 32ead93 | alembic/env.py, alembic/versions/0001_*, alembic_tenant/env.py, alembic_tenant/versions/0001_* |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] redis==7.4.0 incompatible with celery[redis]==5.6.3**
- **Found during:** Task 1 (pip install)
- **Issue:** The plan specified `redis==7.4.0` and `celery[redis]==5.6.3`. However, `celery[redis]==5.6.3` pulls in `kombu 5.6.x` which constrains `redis!=4.5.5,!=5.0.2,<6.5,>=4.5.2`. redis 7.4.0 violates the `<6.5` constraint.
- **Fix:** Changed `redis==7.4.0` to `redis==6.4.0` (highest compatible version within the <6.5 constraint). The `redis.asyncio` submodule (needed for SSE pub/sub) is present from redis 4.2+ and fully functional in 6.4.0.
- **Files modified:** `apps/api/pyproject.toml`
- **Commit:** c37a508

**2. [Rule 1 - Bug] `BaseHTTPMiddleware` string in logging.py docstring failed verification**
- **Found during:** Post-task verification pass
- **Issue:** Plan acceptance criteria require zero occurrences of `BaseHTTPMiddleware` in `logging.py`. The initial docstring contained the string three times (in the explanation of why we don't use it).
- **Fix:** Rewrote the docstring to describe the Starlette base class by behavior rather than by class name, preserving the technical rationale without using the forbidden string.
- **Files modified:** `apps/api/app/core/logging.py`
- **Commit:** df697c2

### Design Decisions (Plan's Discretion)

- **api_key_hash ORM attribute name:** The Tenant model maps `api_key` DB column to ORM attribute `api_key_hash` to make it unambiguous in Python code that the stored value is an argon2 hash, not the raw key. Column name in the DB is unchanged (`api_key`), per PRD.
- **target_metadata=None in alembic_tenant:** Since there are no SQLAlchemy ORM models for tenant tables in M1 (all DDL is raw SQL in the migration), `target_metadata=None` is correct for autogenerate suppression.

## Verification Results

All five plan-specified checks pass:

1. `python -c "from app.core.config import settings; from app.core.database import get_async_db; from app.models import Base, Tenant, Agent, Job, JobEvent; print('Wave 1 imports OK')"` — PASSED
2. `grep -c "config.attributes.get" alembic/env.py` — 1 (PASSED)
3. `grep -c "config.attributes.get" alembic_tenant/env.py` — 1 (PASSED)
4. `grep -c "BaseHTTPMiddleware" app/core/logging.py` — 0 (PASSED)
5. `python -c "from app.models import Base; tables = list(Base.metadata.tables.keys()); assert 'tenants' in tables and 'agents' in tables and 'job_events' in tables"` — PASSED

## Known Stubs

None. This plan creates structural foundations only (schema, config, models). No UI rendering, no hardcoded empty values flowing to endpoints. All models and migrations are complete per PRD §5.

## Threat Flags

No new network endpoints, auth paths, or file access patterns introduced beyond what the plan's threat model covers. Settings `__repr__` suppression implemented per T-01-01 and T-01-02. No new surface beyond the threat register.

## Self-Check: PASSED

Files verified to exist:
- apps/api/pyproject.toml — FOUND
- apps/api/app/core/config.py — FOUND
- apps/api/app/core/database.py — FOUND
- apps/api/app/core/logging.py — FOUND
- apps/api/app/models/agent.py — FOUND
- apps/api/alembic/env.py — FOUND
- apps/api/alembic/versions/0001_control_db_initial.py — FOUND
- apps/api/alembic_tenant/env.py — FOUND
- apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py — FOUND

Commits verified:
- c37a508 — Task 1: project skeleton
- f681d5a — Task 2: ORM models
- 32ead93 — Task 3: Alembic migrations
- df697c2 — Fix: logging.py docstring
