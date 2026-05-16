---
phase: 04-reasoning-engine-widget
plan: "01"
subsystem: foundation-migrations
tags: [alembic, migrations, orm, settings, dependencies]
dependency_graph:
  requires: []
  provides:
    - control-db-migration-0004
    - tenant-db-migration-0003
    - agent-orm-soul-fields
    - jwt-settings
    - smtp-settings
    - claude-agent-sdk-pin
    - python-jose-pin
  affects:
    - apps/api/app/models/agent.py
    - apps/api/app/core/config.py
    - apps/api/tests/conftest.py
tech_stack:
  added:
    - claude-agent-sdk==0.1.81
    - python-jose[cryptography]==3.5.0
  patterns:
    - alembic op.execute() raw SQL migrations (no op.add_column helpers)
    - pydantic-settings BaseSettings field extension
    - SQLAlchemy mapped_column with server_default text()
key_files:
  created:
    - apps/api/alembic/versions/0004_agent_soul_fields.py
    - apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py
  modified:
    - apps/api/app/models/agent.py
    - apps/api/app/core/config.py
    - apps/api/pyproject.toml
    - apps/api/.env.example
    - apps/api/tests/conftest.py
decisions:
  - "Used op.execute() raw SQL per 0003 analog — no Alembic column helpers"
  - "Legacy soul JSONB + role TEXT preserved for M1 backward compatibility (D-Schema)"
  - "JWT_SECRET default value intentionally insecure ('dev-secret-change-in-production') per T-04-01-04 accept disposition"
  - "SMTP_* fields all optional (str | None = None) so SMTP_HOST remains unset in tests"
  - "Pre-existing e2e and unit test failures (16 before, 15 after) are out of scope — not caused by plan changes"
metrics:
  duration: "14m 51s"
  completed: "2026-05-16"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 5
---

# Phase 04 Plan 01: Foundation Migrations + Settings Summary

Wave 1 foundation: two Alembic migrations (control DB 0004, tenant DB 0003), Agent ORM soul field extension, JWT/SMTP Settings additions, dependency pins, and conftest JWT_SECRET env var — enabling all subsequent Phase 4 waves.

## What Was Built

### Control DB Migration 0004 (`alembic/versions/0004_agent_soul_fields.py`)

**Revision chain:** `revision = "0004"`, `down_revision = "0003"`

**Columns added to `agents` table:**

| Column | Type | Default | Nullable |
|--------|------|---------|----------|
| `soul_voice` | TEXT | — | YES |
| `soul_do_list` | JSONB | `'[]'::jsonb` | NO |
| `soul_donot_list` | JSONB | `'[]'::jsonb` | NO |
| `soul_role` | TEXT | — | YES |

**Downgrade:** drops `soul_role`, `soul_donot_list`, `soul_do_list`, `soul_voice` in reverse order using `DROP COLUMN IF EXISTS`.

Pattern: `op.execute()` raw SQL (exact analog of `0003_agent_retrieval_strategy.py`).

### Tenant DB Migration 0003 (`alembic_tenant/versions/0003_tenant_agent_conversations.py`)

**Revision chain:** `revision = "0003"`, `down_revision = "0002"`

**Columns added to `conversations` table:**

| Column | Type | Default | Nullable |
|--------|------|---------|----------|
| `agent_id` | UUID | — | YES |
| `created_at` | TIMESTAMPTZ | `now()` | NO |
| `metadata` | JSONB | `'{}'::jsonb` | NO |

**Index created:** `conversations_agent_id_idx ON conversations(agent_id)`

**Preserved (unchanged):** `id`, `external_id`, `started_at`, `ended_at`

**Downgrade:** drops `conversations_agent_id_idx` first, then drops `metadata`, `created_at`, `agent_id` in reverse — does NOT touch `external_id`/`started_at`/`ended_at`.

Fixes R-01 from 04-RESEARCH.md: schema mismatch that would crash `run_agent_turn` on first call.

### Agent ORM Extension (`app/models/agent.py`)

Four new `mapped_column` attributes added after `retrieval_strategy`, before `created_at`:

```python
soul_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
soul_do_list: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
soul_donot_list: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
soul_role: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Legacy `soul: Mapped[dict]` and `role: Mapped[str]` columns preserved for M1 backward compatibility.

### Settings Extension (`app/core/config.py`)

Five new fields added after `COHERE_API_KEY`, before `MAX_UPLOAD_SIZE_MB`:

| Field | Type | Default |
|-------|------|---------|
| `JWT_SECRET` | `str` | `"dev-secret-change-in-production"` |
| `SMTP_HOST` | `str \| None` | `None` |
| `SMTP_PORT` | `int` | `587` |
| `SMTP_FROM` | `str \| None` | `None` |
| `OWNER_EMAIL` | `str \| None` | `None` |

`__repr__` unchanged — still returns only `f"Settings(LOG_LEVEL={self.LOG_LEVEL!r})"` (T-04-01-01 mitigation).

### pyproject.toml Dependency Additions

Two new pins appended after `tenacity==9.1.2`:

```
"claude-agent-sdk==0.1.81",
"python-jose[cryptography]==3.5.0",
```

### conftest.py Environment Variable Addition

`os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-tests-only")` inserted after `MAX_UPLOAD_SIZE_MB` line, before `CELERY_TASK_ALWAYS_EAGER`. SMTP_* vars intentionally NOT set in tests — must remain `None` to exercise fallback-to-structlog code paths.

### .env.example Documentation

New section appended:

```
# M4 — Reasoning Engine + Widget
# JWT_SECRET=change-me-in-production
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_FROM=noreply@example.com
# OWNER_EMAIL=owner@example.com
```

## Test Results

- **Unit tests (--ignore=tests/e2e --ignore=tests/integration):** 186 passed, 15 failed (pre-existing)
- **Before plan changes:** 186 passed, 16 failed (one additional pre-existing failure resolved by JWT_SECRET env var now being set)
- **Net change:** +0 new failures, -1 pre-existing failure (JWT_SECRET test now passes)

Pre-existing failures are out of scope — they exist in e2e tests requiring real Postgres connections and unit tests with broken mock setups for `agents.chain` attribute.

## Deviations from Plan

None — plan executed exactly as written.

Pre-existing test failures noted above are not deviations; they were confirmed to exist before task execution via `git stash` baseline comparison.

## Known Stubs

None — no stub patterns introduced. All new columns have concrete DDL defaults. All Settings fields have concrete default values.

## Threat Flags

No new threat surface introduced beyond what the plan's threat model covers:
- T-04-01-01: `__repr__` verified unchanged — JWT_SECRET does not appear in repr output
- T-04-01-04: Default JWT_SECRET value contains explicit "dev-secret-change-in-production" warning string

## Self-Check: PASSED

Files verified:
- `apps/api/alembic/versions/0004_agent_soul_fields.py` — FOUND
- `apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py` — FOUND
- `apps/api/app/models/agent.py` — has soul_voice, soul_do_list, soul_donot_list, soul_role
- `apps/api/app/core/config.py` — has JWT_SECRET, SMTP_HOST, SMTP_PORT, SMTP_FROM, OWNER_EMAIL
- `apps/api/pyproject.toml` — has claude-agent-sdk==0.1.81, python-jose[cryptography]==3.5.0
- `apps/api/.env.example` — has M4 section with 5 commented lines
- `apps/api/tests/conftest.py` — has JWT_SECRET setdefault

Commits verified:
- `533797b` feat(04-01): add control DB migration 0004 + Agent ORM soul fields
- `8f0eba7` feat(04-01): add tenant migration 0003, JWT settings, pyproject deps
