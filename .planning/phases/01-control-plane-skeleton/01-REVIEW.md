---
phase: 01-control-plane-skeleton
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 29
files_reviewed_list:
  - apps/api/app/core/config.py
  - apps/api/app/core/database.py
  - apps/api/app/core/logging.py
  - apps/api/app/core/security.py
  - apps/api/app/main.py
  - apps/api/app/api/deps.py
  - apps/api/app/api/v1/agents.py
  - apps/api/app/api/v1/health.py
  - apps/api/app/api/v1/jobs.py
  - apps/api/app/api/v1/tenants.py
  - apps/api/app/models/base.py
  - apps/api/app/models/agent.py
  - apps/api/app/models/job.py
  - apps/api/app/models/job_event.py
  - apps/api/app/models/tenant.py
  - apps/api/app/schemas/agent.py
  - apps/api/app/schemas/job.py
  - apps/api/app/schemas/tenant.py
  - apps/api/app/services/events.py
  - apps/api/app/services/migrations.py
  - apps/api/app/services/neon.py
  - apps/api/app/services/sse.py
  - apps/api/app/worker/celery_app.py
  - apps/api/app/worker/tasks/pipeline/migrations.py
  - apps/api/app/worker/tasks/pipeline/provision.py
  - apps/api/alembic/env.py
  - apps/api/alembic/versions/0001_control_db_initial.py
  - apps/api/alembic_tenant/env.py
  - apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py
findings:
  critical: 6
  warning: 5
  info: 2
  total: 13
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-05-13T00:00:00Z
**Depth:** standard
**Files Reviewed:** 29
**Status:** issues_found

## Summary

Reviewed the complete M1 control-plane skeleton: FastAPI application factory, Celery worker, two pipeline tasks, SSE streaming, Neon provisioning service, Alembic migrations (control DB and per-tenant), ORM models, Pydantic schemas, and security helpers.

The security architecture is sound — Fernet-encrypted connection strings, argon2id key hashing, no secrets in task args, proper CORS lockdown, and structlog context isolation are all correctly implemented. The high-level design is correct.

However, six blockers were found that would prevent the system from running at all or cause silent security failures: the most severe is a Python `TypeError` that crashes every Celery task invocation (the sync DB session helper is a generator, not a context manager), and a chain-propagation bug where a fatal 4xx Neon API error causes `apply_migrations` to retry infinitely on a `None` result. Three additional blockers involve uncaught exceptions in authentication, a null-pointer dereference in task code, and a connection string leaked into Alembic config.

---

## Critical Issues

### CR-01: `get_sync_db()` is a plain generator — `with get_sync_db() as db` raises `TypeError` at runtime

**File:** `apps/api/app/core/database.py:55`
**Also affects:** `apps/api/app/worker/tasks/pipeline/provision.py:83`, `apps/api/app/worker/tasks/pipeline/migrations.py:89`

**Issue:** `get_sync_db()` is declared as a plain generator function (`yield` without `@contextmanager`). A generator object does not implement the context manager protocol (`__enter__` / `__exit__`). Every call to `with get_sync_db() as db:` in both Celery tasks raises `TypeError: 'generator' object does not support the context manager protocol`. This means **no pipeline task can run**. Verified locally:

```
TypeError: 'generator' object does not support the context manager protocol
```

**Fix:** Add `@contextmanager` from `contextlib`:

```python
# apps/api/app/core/database.py
from contextlib import contextmanager
from collections.abc import Generator

@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """Celery task helper: yields a sync SQLAlchemy session."""
    with SyncSessionFactory() as session:
        yield session
```

No changes required in the task files — they already use `with get_sync_db() as db:` correctly once the decorator is added.

---

### CR-02: Fatal 4xx Neon error returns `None` to Celery chain; `apply_migrations` crashes and retries infinitely

**File:** `apps/api/app/worker/tasks/pipeline/provision.py:160`
**Also affects:** `apps/api/app/worker/tasks/pipeline/migrations.py:87`

**Issue:** When the Neon API returns a 4xx status code, `provision_neon` takes the fatal path and returns `None` (line 160). In a Celery chain, the return value of the previous task is passed as the first positional argument to the next task. `apply_migrations` receives `None` as its `result` parameter and immediately executes `agent_id = result["agent_id"]` (line 87), raising `TypeError: 'NoneType' object is not subscriptable`. Because `acks_late=True` and `max_retries=3`, this re-queues the task three more times before giving up — wasting resources and producing misleading error logs for what was already marked a fatal failure.

**Fix:** Return a sentinel dict that `apply_migrations` recognises as a no-op, or raise a non-retriable exception to abort the chain:

```python
# provision.py — fatal 4xx path: raise an exception that stops the chain
# Replace `return None` with:
raise Exception(f"Neon API fatal {status_code} — chain aborted")
# (provision_neon has no more retries after self.retry exhaustion for this branch,
#  but since we don't call self.retry here, this exception surfaces immediately
#  and Celery marks the task as FAILURE without further retries)
```

Alternatively, add a guard in `apply_migrations`:

```python
def apply_migrations(self, result: dict) -> None:
    if result is None:
        log.info("apply_migrations.skipped_null_result")
        return
    agent_id = result["agent_id"]
    ...
```

---

### CR-03: `verify_api_key` only catches `VerifyMismatchError` — `InvalidHashError` and `VerificationError` propagate as HTTP 500

**File:** `apps/api/app/core/security.py:94`
**Also affects:** `apps/api/app/api/deps.py:56`

**Issue:** `argon2-cffi`'s `PasswordHasher.verify()` raises three distinct exception types:
- `VerifyMismatchError` — wrong password (caught correctly)
- `VerificationError` — hash computation failed (e.g. corrupted params)  
- `InvalidHashError` — the stored value is not a valid argon2 hash

Only `VerifyMismatchError` is caught. If the database contains a corrupted hash (hardware bit-flip, migration error, manual edit), every authentication attempt against that tenant raises an uncaught `InvalidHashError` or `VerificationError`, propagating as an HTTP 500 and leaking an internal error through FastAPI's default exception handler. Confirmed by running:

```
argon2.exceptions.InvalidHashError  # raised for non-argon2 strings
argon2.exceptions.VerificationError  # raised for malformed-but-argon2-like strings
```

**Fix:**

```python
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

def verify_api_key(stored_hash: str, raw_key: str) -> bool:
    try:
        return _ph.verify(stored_hash, raw_key)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
```

---

### CR-04: `apply_migrations` dereferences `agent` without null check

**File:** `apps/api/app/worker/tasks/pipeline/migrations.py:90-96`

**Issue:** `agent = db.get(Agent, agent_id)` returns `None` if the agent row does not exist (deleted between task dispatch and execution, or corrupt `result` dict). Line 96 (`if agent.status == "ready"`) immediately raises `AttributeError: 'NoneType' object has no attribute 'status'`. Because `acks_late=True` is set, this crashes the task and requeues it up to `max_retries=3` times before giving up, stalling the pipeline queue with repeated failures.

By contrast, `provision_neon` correctly guards its `db.get()` result (line 85-87 of provision.py).

**Fix:**

```python
agent = db.get(Agent, agent_id)
if agent is None:
    log.error("apply_migrations.agent_not_found", agent_id=agent_id)
    return  # Nothing to do; idempotent exit

if agent.status == "ready":
    ...
```

---

### CR-05: Connection string written to Alembic config object as `sqlalchemy.url` — credential exposure via Alembic debug logging

**File:** `apps/api/app/services/migrations.py:67`

**Issue:** `alembic_cfg.set_main_option("sqlalchemy.url", conn_string)` stores the raw (decrypted) tenant connection string in the Alembic `Config` object's `main_options` dict. While `alembic_tenant/env.py` correctly uses `config.attributes["connection"]` and ignores `sqlalchemy.url` in programmatic mode, the URL is still stored in the config object. If Alembic's log level is set to DEBUG (which `fileConfig(config.config_file_name)` in env.py can activate), some Alembic internal operations log config keys. Additionally, any future Alembic plugin, hook, or monkey-patch that reads `config.get_main_option("sqlalchemy.url")` will receive the plaintext credential. This violates the T-03-02 threat mitigation.

**Fix:** Do not set `sqlalchemy.url` when using the programmatic connection injection pattern. The connection is already injected via `alembic_cfg.attributes["connection"]` — setting `sqlalchemy.url` is redundant and dangerous:

```python
def run_tenant_migrations(conn_string: str) -> None:
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(_ALEMBIC_TENANT_DIR))
    # Do NOT set sqlalchemy.url here — connection is injected via attributes["connection"]

    engine = create_engine(conn_string, poolclass=pool.NullPool)
    with engine.begin() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")
    engine.dispose()
```

---

### CR-06: SSE event gap — events published between DB replay end and Redis subscribe are permanently lost

**File:** `apps/api/app/services/sse.py:59-81`

**Issue:** The SSE generator performs Phase 1 (DB replay) then Phase 2 (Redis subscribe) sequentially. There is a window between the last `db.execute()` completing and `pubsub.subscribe()` completing where Celery can publish events to Redis. Those events are never stored in Redis after they are consumed (pub/sub, not a queue), and they have already been committed to the DB — but Phase 2 only sees events published *after* it subscribes. If a task emits two rapid events (e.g., `migrations.running` followed by `migrations.complete`), a fast client connecting mid-run may miss `migrations.complete` in Phase 1 (not yet committed) and miss it in Phase 2 (already published before subscribe). The client then hangs waiting for a terminal event that already occurred.

This is distinct from the "already-complete job" case (which is handled by Pitfall 4). This affects *in-progress* jobs where events arrive between the two phases.

**Fix:** Subscribe to Redis *before* querying the DB, then replay DB history, then consume live events. This eliminates the gap at the cost of receiving some duplicate events (which are harmless — the client deduplicates by SSE `id`):

```python
async with redis_client.pubsub() as pubsub:
    await pubsub.subscribe(f"job_events:{job_id}")

    # Phase 1: DB replay AFTER subscribing to avoid the gap
    past = await db.execute(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at)
    )
    for evt in past.scalars():
        if await request.is_disconnected():
            return
        yield ServerSentEvent(data=json.dumps(evt.payload), event=evt.event_type, id=str(evt.id))
        if evt.event_type in TERMINAL_EVENTS:
            return

    # Phase 2: Live events (subscribe already established above)
    async for message in pubsub.listen():
        ...
```

---

## Warnings

### WR-01: `get_current_tenant` loads all tenants into memory and runs O(N) argon2 hashes per request

**File:** `apps/api/app/api/deps.py:53-57`

**Issue:** Every authenticated request fetches all non-deleted tenant rows (`select(Tenant)` with no limit), then iterates, running a full argon2id hash comparison for each. Argon2id is intentionally slow (~200ms per verify). With N tenants, worst-case latency is `N × 200ms`. At 10 tenants, the worst case before returning 401 is 2 seconds. This is a DoS amplifier — a burst of requests with invalid keys that exhaust before matching forces the event loop to serialize hashing work, degrading all concurrent legitimate requests.

The comment says "iterate over all non-deleted tenants and runs argon2 verify() against each" — this design is documented, but is unsafe for any realistic tenant count.

**Fix:** Store a HMAC-SHA256 prefix of the raw key (first 8 bytes, hex) as an indexed lookup column. The prefix is not secret and allows a direct `WHERE` lookup before argon2 verification, making it O(1) DB + O(1) argon2 rather than O(N) of both. Alternatively, add a `LIMIT 1` and pre-filter on a non-secret prefix embedded in the key format (`vrd_live_` prefix is already present and unique per tenant if stored).

---

### WR-02: `provision_neon` returns early with `project_id: None` when job is not found after idempotency guard passes

**File:** `apps/api/app/worker/tasks/pipeline/provision.py:110-112`

**Issue:** The code path at lines 105-112 queries for a job that is not `complete`. If no such job is found, the comment says "already completed" — but at this point, `agent.neon_project_id` is still `None` (the idempotency guard at line 92 already confirmed the project was NOT provisioned). This is a contradictory state: the agent was not provisioned, but there is no active job. The function returns `{"agent_id": agent_id, "project_id": None}` which flows into `apply_migrations`, where `project_id: None` is silently unused but `agent_id` is used to fetch an agent with no connection strings. `apply_migrations` then calls `fernet_decrypt(agent.neon_direct_connection_string)` on a `None` value (line 117), raising `TypeError` and causing retries.

**Fix:** Treat this as an error, not a no-op:

```python
if not job:
    log.error(
        "provision_neon.no_job_found_for_unprovisioned_agent",
        agent_id=agent_id,
    )
    # Do not return a result — nothing downstream can proceed
    raise ValueError(f"No active job found for unprovisioned agent {agent_id}")
```

---

### WR-03: `wait_for_neon_ready` leaks SQLAlchemy engine on exception path

**File:** `apps/api/app/services/neon.py:142-154`

**Issue:** Inside the probe loop, `engine = create_engine(...)` is called on every attempt. On success, `engine.dispose()` is called before returning (line 145). However, on exception, control jumps directly to the `except` block — `engine.dispose()` is never called for the failed attempt. For `max_attempts=10`, up to 9 engine objects leak their connection pools. Under rapid retries this accumulates file descriptors and TCP connections.

**Fix:** Use a `try/finally` or restructure to ensure disposal:

```python
for attempt in range(max_attempts):
    engine = create_engine(conn_string, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("neon.compute_ready", attempt=attempt)
        return
    except Exception:
        if attempt == max_attempts - 1:
            raise RuntimeError(
                f"Neon project not query-ready after {max_attempts} probe attempts"
            )
        backoff = 2**attempt
        log.debug("neon.compute_probe_waiting", attempt=attempt, backoff_s=backoff)
        time.sleep(backoff)
    finally:
        engine.dispose()
```

Note: `NullPool` means each `engine.connect()` opens a fresh connection rather than pooling, so `dispose()` inside the loop is safe and correct.

---

### WR-04: OpenAPI docs exposed unconditionally in production (`/docs`, `/redoc`)

**File:** `apps/api/app/main.py:58-59`

**Issue:** `docs_url="/docs"` and `redoc_url="/redoc"` are set unconditionally. In production, the interactive Swagger UI allows any visitor to enumerate all API endpoints, inspect request/response schemas, and attempt calls directly. There is no `ENVIRONMENT` or `DEBUG` config flag to suppress these in production. For an API whose CORS is locked to `http://localhost:3000` and whose only auth is API key + admin key, exposing the full schema aids attackers in crafting credential-stuffing or admin-key brute-force attempts.

**Fix:** Conditionally expose docs:

```python
# config.py — add:
ENVIRONMENT: str = "development"  # "production" disables docs

# main.py:
app = FastAPI(
    title="Veridian Control Plane",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)
```

---

### WR-05: `apply_migrations` emits `migrations.running` before the DB session scope ends, then commits inside `emit()` — double-commit risk on session reuse

**File:** `apps/api/app/worker/tasks/pipeline/migrations.py:122`, `apps/api/app/services/events.py:74`

**Issue:** After `emit(job.id, "migrations.running", ...)` is called at line 122, `emit()` inserts a `JobEvent` row and calls `db.commit()` (events.py line 74). The same `db` session is then used for subsequent ORM operations (`agent.schema_version = revision`, `db.commit()` at line 169). While SQLAlchemy sessions support multiple commits per session, the `expire_on_commit=False` setting (set in `database.py`) prevents automatic expiry, which means previously loaded ORM objects remain valid across commits. This is correct for M1. However, if `emit()` raises an exception after inserting the event row but before `redis.publish()` (e.g., Redis connection lost), the DB commit has already happened and the event is persisted, but the exception propagates up to the task, which then retries — potentially re-emitting the event and inserting a duplicate row into `job_events`. Since there is no uniqueness constraint on `job_events`, this produces duplicate events in the SSE stream.

**Fix:** Publish to Redis before committing to DB, accepting that a Redis-only failure loses the live broadcast (which is acceptable — the DB row is the durable record and late-join replay handles recovery):

```python
# In emit(), publish first (lossy), then commit (durable):
redis.publish(f"job_events:{job_id}", message)  # best-effort live delivery
db.add(event)
db.commit()  # durable record
```

Or, add a `(job_id, event_type, created_at)` partial uniqueness constraint and use `ON CONFLICT DO NOTHING` on insert.

---

## Info

### IN-01: `jobs` table missing index on `tenant_id` (model has it, migration does not create it in ORM)

**File:** `apps/api/app/models/job.py:41-43`

**Issue:** The `Job` model defines `Index("jobs_agent_id_idx", "agent_id")` but no index on `tenant_id`. The `get_job` route filters `Job.tenant_id == tenant.id` on every call. With many jobs per system, this is a sequential scan without an index. The migration `0001_control_db_initial.py` also only creates `jobs_agent_id_idx`. This is not a correctness bug but will cause slow queries at scale.

**Fix:** Add an index in both the model and the migration:

```python
# models/job.py
__table_args__ = (
    Index("jobs_agent_id_idx", "agent_id"),
    Index("jobs_tenant_id_idx", "tenant_id"),
)
```

```sql
-- migration
CREATE INDEX jobs_tenant_id_idx ON jobs(tenant_id);
```

---

### IN-02: `configure_logging` is called at application startup but structlog's `merge_contextvars` processor requires `contextvars_context_class` when `context_class=dict`

**File:** `apps/api/app/core/logging.py:27-37`

**Issue:** The structlog pipeline includes `structlog.contextvars.merge_contextvars` as the first processor, but the configuration uses `context_class=dict`. The `merge_contextvars` processor reads from structlog's contextvars-based context (set via `bind_contextvars()`), not from the `context_class` dict. These are separate contexts. With `context_class=dict`, bound variables from `structlog.get_logger().bind(...)` live in the dict context, while variables from `bind_contextvars(request_id=...)` live in the contextvars context. `merge_contextvars` merges the latter into the event dict correctly. However, `context_class=dict` with `logger_factory=LoggerFactory()` is a non-standard combination that may produce unexpected behavior when the same logger is shared across coroutines. The `AsyncBoundLogger` and `contextvars` integration is designed to work with `contextvars_context_class`, not `dict`.

This is low risk for M1 (the middleware always clears and re-binds contextvars per request), but could cause log bleeding in edge cases under high concurrency if a coroutine inherits a non-cleared dict context.

**Fix:** Verify the configuration is stable, or explicitly use `wrapper_class=structlog.stdlib.AsyncBoundLogger` for async code paths.

---

_Reviewed: 2026-05-13T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
