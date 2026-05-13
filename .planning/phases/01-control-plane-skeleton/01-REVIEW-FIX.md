---
phase: 01-control-plane-skeleton
fixed_at: 2026-05-13T00:00:00Z
review_path: .planning/phases/01-control-plane-skeleton/01-REVIEW.md
iteration: 1
fix_scope: critical_warning
findings_in_scope: 11
fixed: 11
skipped: 0
status: all_fixed
---

# Phase 01: Code Review Fix Report

**Fixed at:** 2026-05-13T00:00:00Z
**Source review:** .planning/phases/01-control-plane-skeleton/01-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 11
- Fixed: 11
- Skipped: 0

## Fixed Issues

### CR-01: `get_sync_db()` missing `@contextmanager`

**Files modified:** `apps/api/app/core/database.py`
**Commit:** 669d07a
**Applied fix:** Added `from contextlib import contextmanager` import and `@contextmanager` decorator to `get_sync_db()`. Without this the generator returned by `get_sync_db()` did not implement `__enter__`/`__exit__`, causing `TypeError` on every `with get_sync_db() as db:` call in both Celery tasks.

---

### CR-02: Fatal 4xx Neon error returns `None` to chain

**Files modified:** `apps/api/app/worker/tasks/pipeline/provision.py`
**Commit:** 0098095
**Applied fix:** Replaced `return None` on the fatal 4xx path with `raise Exception(f"Neon API fatal {status_code} — chain aborted")`. This immediately marks the Celery task as FAILURE without retrying and prevents `apply_migrations` from receiving `None` as its `result` argument and crashing with `TypeError: 'NoneType' object is not subscriptable`.

---

### CR-03: `verify_api_key` only catches `VerifyMismatchError`

**Files modified:** `apps/api/app/core/security.py`
**Commit:** 1fd3aa1
**Applied fix:** Updated import to include `InvalidHashError` and `VerificationError` alongside `VerifyMismatchError`. Updated the `except` clause to catch all three: `except (VerifyMismatchError, VerificationError, InvalidHashError)`. Updated the docstring to document all three caught exception types.

---

### CR-04: `apply_migrations` dereferences `agent` without null check

**Files modified:** `apps/api/app/worker/tasks/pipeline/migrations.py`
**Commit:** 2a16860
**Applied fix:** Added a null check immediately after `agent = db.get(Agent, agent_id)`: if `agent is None`, log an error and return early. This prevents `AttributeError: 'NoneType' object has no attribute 'status'` when the agent row is missing and avoids infinite retries under `acks_late=True`.

---

### CR-05: Connection string set as `sqlalchemy.url` in Alembic config

**Files modified:** `apps/api/app/services/migrations.py`
**Commit:** c15311f
**Applied fix:** Removed the `alembic_cfg.set_main_option("sqlalchemy.url", conn_string)` call. The connection is already injected via `alembic_cfg.attributes["connection"]` (the programmatic injection pattern), so setting `sqlalchemy.url` was redundant and stored the plaintext credential in the Config object where Alembic debug logging could expose it.

---

### CR-06: SSE event gap — subscribe AFTER DB replay

**Files modified:** `apps/api/app/services/sse.py`
**Commit:** e88f354
**Applied fix:** Moved `async with redis_client.pubsub() as pubsub` and `await pubsub.subscribe(...)` to before the DB replay query. The DB replay and Redis listen phase are now nested inside the same `async with` block. This eliminates the window between the DB query completing and the Redis subscribe starting where events could be published and permanently lost. Duplicate events (events in both DB replay and the live stream) are harmless as the client can deduplicate by SSE `id`.

---

### WR-01: `get_current_tenant` O(N) argon2 hashes per request

**Files modified:** `apps/api/app/core/security.py`, `apps/api/app/models/tenant.py`, `apps/api/app/api/deps.py`, `apps/api/app/api/v1/tenants.py`, `apps/api/alembic/versions/0002_tenant_api_key_prefix.py`
**Commit:** 2ea412a
**Applied fix:** Added `hmac_key_prefix(raw_key)` helper to `security.py` (first 16 hex chars of HMAC-SHA256 keyed on `ADMIN_KEY`). Added `api_key_prefix` nullable indexed column to the `Tenant` model. Created migration `0002_tenant_api_key_prefix.py` to add the column and index to the DB. Updated `create_tenant` to store the prefix on tenant creation. Updated `get_current_tenant` to do an O(1) indexed prefix lookup first, then a single `verify_api_key()` call; falls back to the old O(N) scan only for legacy rows where `api_key_prefix IS NULL`.

**Note:** Logic change — requires human verification that the HMAC prefix lookup and fallback scan behave correctly under all key formats.

---

### WR-02: `provision_neon` returns early with `project_id: None`

**Files modified:** `apps/api/app/worker/tasks/pipeline/provision.py`
**Commit:** 250581a
**Applied fix:** Replaced the silent `return {"agent_id": agent_id, "project_id": agent.neon_project_id}` (where `neon_project_id` is `None`) with a `log.error` + `raise ValueError(...)`. This aborts the chain instead of passing `project_id: None` downstream to `apply_migrations`, which would crash with `TypeError` when trying to decrypt a `None` connection string.

---

### WR-03: `wait_for_neon_ready` leaks engine on exception path

**Files modified:** `apps/api/app/services/neon.py`
**Commit:** 02967ff
**Applied fix:** Restructured the probe loop to create `engine` before the `try` block and added a `finally: engine.dispose()` clause. This ensures `engine.dispose()` is called on every iteration whether the probe succeeds or fails, preventing connection pool and file descriptor leaks for up to 9 failed probe attempts.

---

### WR-04: OpenAPI docs exposed unconditionally in production

**Files modified:** `apps/api/app/core/config.py`, `apps/api/app/main.py`
**Commit:** 303a317
**Applied fix:** Added `ENVIRONMENT: str = "development"` field to `Settings`. In `main.py`, set `docs_url=None if _is_production else "/docs"` and `redoc_url=None if _is_production else "/redoc"`. The interactive Swagger UI and ReDoc are now disabled when `ENVIRONMENT=production` is set in the deployment environment.

---

### WR-05: `apply_migrations` emit double-commit risk

**Files modified:** `apps/api/app/services/events.py`
**Commit:** 62dc3f8
**Applied fix:** Swapped the order in `emit()` to publish to Redis first (best-effort, lossy), then insert the `JobEvent` row and commit (durable). If a Redis publish raises before the DB commit, no row is persisted, so a task retry will not produce a duplicate `job_events` row. A Redis-only failure loses only the live broadcast — the DB commit that follows still creates the durable record for late-join replay.

---

## Skipped Issues

None — all 11 in-scope findings were fixed.

---

_Fixed: 2026-05-13T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
