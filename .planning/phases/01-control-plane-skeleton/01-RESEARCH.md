# Phase 1: Control Plane Skeleton — Research

**Researched:** 2026-05-12
**Domain:** FastAPI + Celery + Neon + SSE + Alembic per-tenant provisioning
**Confidence:** HIGH (all major components verified against PyPI registry, official docs, and project planning documents)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- FastAPI with Pydantic; OpenAPI docs at `/docs`
- Routes: `POST /tenants`, `POST /agents`, `GET /agents/{id}`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/events` (SSE), `GET /health`
- All routes require `X-API-Key` header; admin bootstrap uses `X-Admin-Key`
- API key auth only; keys stored hashed with argon2, never stored or logged in plaintext
- `Cache-Control: no-store` on all API responses
- CORS locked to known origins (admin UI only; widget CORS lands in M4)
- `acks_late=True` on every task — non-negotiable
- Two queues always present: `pipeline` (ingestion/build) and `runtime` (evals, agent calls)
- Celery beat service running from day one (idle in M1)
- Connection strings NEVER passed as Celery task args; tasks fetch and decrypt from control DB at runtime
- Celery chain: `chain(provision_neon.s(tenant_id, agent_id), apply_migrations.s()).apply_async(queue="pipeline")`
- `provision_neon` task: calls Neon API, polls until active, encrypts with Fernet, stores as BYTEA
- `apply_migrations` task: decrypts from DB (not from chain arg), runs Alembic upgrade head programmatically
- Idempotency guards on all tasks
- Single `emit(job_id, event_type, payload)` helper: persists to `job_events` table + publishes to Redis `job_events:{job_id}`
- SSE endpoint: replay all `job_events` rows → subscribe Redis channel → close on terminal event
- Six SSE events in order: `job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`
- Fernet encryption keyed from `NEON_ENCRYPTION_KEY` (32 random bytes); stored as BYTEA
- Control DB schema: `tenants`, `agents`, `jobs`, `job_events` on shared Neon project (exact schema in CONTEXT.md §5)
- Tenant DB v1 schema created by per-tenant Alembic migration (exact schema in CONTEXT.md §5.1)
- docker-compose: 6 services: `postgres`, `redis`, `api`, `worker_pipeline`, `worker_runtime`, `beat`
- structlog for structured JSON logs; request ID propagated FastAPI → Celery via task headers
- Sentry SDK initialized only if `SENTRY_DSN` set
- `.env.example` with: `NEON_API_KEY`, `NEON_REGION`, `NEON_ENCRYPTION_KEY`, `CONTROL_DB_URL`, `REDIS_URL`, `LOG_LEVEL`
- `make dev-light` target: runs only `postgres`, `redis`, `api`; workers run on host

### Claude's Discretion

- Project directory layout and Python module structure
- Alembic `env.py` and migrations folder structure
- Docker base image choice (Python version, slim/full)
- Makefile targets beyond `dev-light`
- pytest configuration and test directory layout
- Whether to use `asyncpg` vs `psycopg` for async DB access in FastAPI
- Redis client library choice (`redis-py` sync vs `aioredis` / `redis.asyncio`)
- Whether to use `sqlalchemy` ORM or raw SQL for control DB queries

### Deferred Ideas (OUT OF SCOPE)

- Connection-string encryption key rotation → M10
- Automated cleanup of failed tenant DBs → M10
- Real auth providers (OAuth) → v2/AUTH-01
- Frontend admin UI → M4
- Widget delivery → M4
- Validation chain, evals, red team → M5–M7
- Cost tracking, full observability → M10
- Neon branch exposure as user-facing feature → v2/ADV-01

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CTL-01 | `POST /agents` returns `202 Accepted` with `job_id`, creates tenant/agent/job rows in control DB | FastAPI + Pydantic schemas; SQLAlchemy async for DB writes; UUID generation |
| CTL-02 | Celery chain (`provision_neon` → `apply_migrations`) runs idempotently with `acks_late=True` | Celery 5.6.3 chain pattern; `task_reject_on_worker_lost`; idempotency guard patterns |
| CTL-03 | `provision_neon` calls Neon API, polls until ready, encrypts connection string with Fernet | neon-api 0.3.0; Fernet from `cryptography` 48.0.0; operations poll against `finished` status |
| CTL-04 | `apply_migrations` runs per-tenant Alembic migration (v1 schema: 10 tables) | Alembic 1.18.4 programmatic `command.upgrade(cfg, "head")` via `cfg.attributes['connection']` |
| CTL-05 | `GET /jobs/{id}/events` returns live SSE stream that replays prior events then forwards new events | sse-starlette 3.4.4; Redis pub/sub with `redis.asyncio`; DB replay before subscribe |
| CTL-06 | SSE stream emits all six required events in order | `emit()` helper: DB persist + Redis publish; event sequence enforced by task order |
| CTL-07 | Worker kill-9 mid-chain results in retry and successful completion | `acks_late=True` + `task_reject_on_worker_lost=True`; idempotency guards prevent duplicate Neon projects |
| CTL-08 | Connection strings never passed as Celery task arguments | `apply_migrations` fetches + decrypts from control DB by `agent_id` at runtime |
| CTL-09 | API key auth (`X-API-Key`), keys stored hashed (argon2), never logged | `argon2-cffi` 25.1.0 `PasswordHasher`; FastAPI dependency injection for auth |
| CTL-10 | `GET /health` returns `200` with redis and DB status | Async health check hitting Redis PING and `SELECT 1` against control DB |
| CTL-11 | `docker-compose up` starts all six services | docker-compose with `healthcheck` + `depends_on: condition: service_healthy` |
| CTL-12 | `scripts/demo_m1.sh` runs clean from scratch | Bash script using `curl` + `jq`; documented in PRD §8.2 |
| CTL-13 | Unit test coverage >80%; integration test exercises full chain | pytest + pytest-asyncio 1.3.0; `respx` 0.23.1 for mocked Neon API; real local Postgres |
| CTL-14 | Nightly CI E2E test against real Neon test account | GitHub Actions nightly workflow; real Neon project creation + teardown |
| CTL-15 | README includes architecture diagram and recorded demo | Architecture diagram + asciinema or video |

</phase_requirements>

---

## Summary

M1 builds the entire infrastructure foundation from scratch: no existing code, no existing DB, no existing services. Every design decision made here is carried forward to M10. The primary technical complexity is NOT writing the API routes — it is the correct composition of four subtle sub-systems: (1) Celery chain with `acks_late` + idempotency that survives worker kill, (2) programmatic Alembic running against a runtime-injected Neon connection string, (3) SSE with a replay-then-subscribe pattern using Redis pub/sub, and (4) the `emit()` helper that atomically writes to both Postgres and Redis.

The second-most important concern is getting the project structure right so that M2–M10 have clean extension points. The architecture research document already provides a tested project structure; this research phase confirms all library APIs and fills the implementation gaps.

**Primary recommendation:** Implement in this strict build order — (1) project skeleton + config + DB models, (2) Alembic migrations (control DB), (3) Fernet + argon2 helpers, (4) `emit()` helper, (5) Celery app + queue config, (6) `provision_neon` task, (7) `apply_migrations` task, (8) FastAPI routes, (9) SSE endpoint, (10) docker-compose, (11) tests. Each step is testable before the next.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| API routing + auth | API (FastAPI) | — | Request validation, auth check, immediate 202 dispatch; no inline work |
| Tenant/agent/job persistence | API (FastAPI) | Data (Neon control DB) | API writes rows; DB stores state |
| Celery chain dispatch | API (FastAPI) → Orchestration (Celery) | — | FastAPI dispatches; Celery owns execution lifecycle |
| Neon project provisioning | Orchestration (Celery `pipeline` queue) | External (Neon API) | Long-running, must survive worker restart |
| Per-tenant Alembic migration | Orchestration (Celery `pipeline` queue) | Data (Neon tenant DB) | Runs programmatically inside task; writes to tenant DB |
| SSE event streaming | API (FastAPI) | Orchestration (Redis pub/sub) | FastAPI streams to client; Redis is the live event bus |
| Event durability (replay) | Data (Neon control DB `job_events`) | — | `job_events` table is the durable log; Redis is ephemeral |
| Credential encryption | Orchestration (Celery task) | Data (Neon control DB BYTEA column) | Task encrypts then stores; API never sees plaintext |
| Health checks | API (FastAPI) | Orchestration (Redis PING) | FastAPI probes Redis + DB, returns structured JSON |

---

## Standard Stack

### Core (all verified against PyPI registry on 2026-05-12)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | 0.136.1 | HTTP API framework | Industry standard async Python API; Pydantic-native; OpenAPI out of the box |
| pydantic | v2.x | Request/response validation | Required by FastAPI; aligned with Langfuse v4 and Ragas 0.4.x (future milestones) |
| pydantic-settings | 2.14.1 | Config from environment variables | 12-factor config; typed settings object |
| celery | 5.6.3 | Task queue + chain orchestration | Production/Stable; `acks_late`, chains, beat scheduler; Redis broker |
| redis (redis-py) | 7.4.0 | Celery broker/result backend + pub/sub | `redis.asyncio` sub-module provides async pub/sub for SSE |
| sqlalchemy | 2.0.49 | ORM for control DB (async) | 2.x async engine required for Alembic async template; maps models to `job_events` etc. |
| alembic | 1.18.4 | DB schema migrations (control + per-tenant) | Standard SQLAlchemy migration tool; programmatic `command.upgrade()` for Celery task use |
| asyncpg | 0.31.0 | Async Postgres driver for FastAPI routes | 5x faster than psycopg for async read paths; used via SQLAlchemy async engine |
| psycopg2-binary | 2.9.12 | Sync Postgres driver for Celery tasks | Celery workers are sync processes; needed for Alembic CLI and sync DB writes in tasks |
| cryptography | 48.0.0 | Fernet symmetric encryption of connection strings | Ships `Fernet` and `MultiFernet`; AES-128-CBC + HMAC-SHA256 |
| argon2-cffi | 25.1.0 | API key hashing | Argon2id default; `PasswordHasher.hash()` + `verify()` |
| sse-starlette | 3.4.4 | SSE streaming from FastAPI | W3C SSE compliant; `EventSourceResponse` + `ServerSentEvent`; handles disconnect |
| structlog | 25.5.0 | Structured JSON logging | `bind_contextvars()` for request ID propagation; Celery signal integration |
| sentry-sdk | 2.59.0 | Error tracking (conditional on SENTRY_DSN) | Zero-cost when DSN not set; integrations for FastAPI and Celery |
| httpx | 0.28.1 | Async HTTP client (for health checks, future use) | Replaces `requests` on async paths |
| neon-api | 0.3.0 | Neon project/operation management | `project_create()`, `operations()`, `connection_uri()` |

[VERIFIED: pip index versions for all packages above on 2026-05-12]

### Testing

| Library | Version | Purpose |
|---------|---------|---------|
| pytest | latest | Test runner |
| pytest-asyncio | 1.3.0 | Async test support for FastAPI routes |
| respx | 0.23.1 | Mock httpx requests (Neon API calls) |
| httpx | 0.28.1 | TestClient for FastAPI endpoints |

[VERIFIED: pip index versions on 2026-05-12]

### Installation

```bash
pip install fastapi[standard] pydantic pydantic-settings celery[redis] \
  sqlalchemy[asyncio] alembic asyncpg psycopg2-binary \
  cryptography argon2-cffi sse-starlette structlog sentry-sdk httpx neon-api \
  redis

# Dev/test only
pip install pytest pytest-asyncio respx
```

---

## Architecture Patterns

### System Architecture Diagram

```
HTTP Client
     │
     │ POST /agents (202 Accepted)
     ▼
┌──────────────────────────────────────────┐
│           FastAPI control plane           │
│  auth dep → route handler → 202 response │
│  Writes: tenants, agents, jobs rows       │
└───────┬──────────────────────┬───────────┘
        │ dispatch chain        │ subscribe
        │ to `pipeline` queue   │ (SSE endpoint)
        ▼                       ▼
┌───────────────┐    ┌──────────────────────────┐
│ Redis broker  │    │   Redis pub/sub channel   │
│ (Celery)      │    │   job_events:{job_id}     │
└───────┬───────┘    └──────────────────────────┘
        │                       ▲
        │ task consumed          │ publish events
        ▼                       │
┌───────────────────────────────┴──────────────────┐
│         Celery worker: `pipeline` queue           │
│                                                   │
│  provision_neon(tenant_id, agent_id)              │
│    1. check idempotency (agent.neon_project_id?)  │
│    2. emit("job.started")                         │
│    3. POST /projects to Neon API                  │
│    4. poll GET /projects/{id}/operations → finish │
│    5. Fernet.encrypt(conn_uri)                    │
│    6. write agent.neon_project_id + BYTEA         │
│    7. emit("neon.project.ready") → DB + Redis     │
│    └── returns {agent_id, project_id}             │
│                                                   │
│  apply_migrations({agent_id, project_id})         │
│    1. fetch + decrypt conn_string from control DB │
│    2. emit("migrations.running")                  │
│    3. alembic command.upgrade(cfg, "head")        │
│    4. write agent.schema_version, status='ready'  │
│    5. emit("migrations.complete") + "job.complete"│
└───────────────────────────────────────────────────┘
        │ reads/writes
        ▼
┌───────────────────────────────────────────────────┐
│   Control DB (Neon shared project)                │
│   tenants / agents / jobs / job_events            │
└───────────────────────────────────────────────────┘
        │ Alembic upgrade head applied against
        ▼
┌───────────────────────────────────────────────────┐
│   Tenant DB (Neon per-agent project)              │
│   documents / chunks / embeddings / ...           │
│   (empty in M1; populated M2+)                    │
└───────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
veridian/
├── apps/
│   └── api/
│       ├── alembic/
│       │   ├── env.py               # supports cfg.attributes['connection'] injection
│       │   └── versions/            # control DB migration files
│       ├── alembic_tenant/
│       │   ├── env.py               # same pattern, separate script_location
│       │   └── versions/            # tenant DB migration files (v1 schema)
│       ├── app/
│       │   ├── main.py              # FastAPI app factory, lifespan, routers
│       │   ├── core/
│       │   │   ├── config.py        # pydantic-settings BaseSettings
│       │   │   ├── database.py      # async SQLAlchemy engine + session factory
│       │   │   ├── security.py      # argon2 hash/verify; Fernet encrypt/decrypt
│       │   │   └── logging.py       # structlog setup with JSON renderer
│       │   ├── api/
│       │   │   ├── deps.py          # auth dependency (verify X-API-Key)
│       │   │   └── v1/
│       │   │       ├── tenants.py   # POST /tenants
│       │   │       ├── agents.py    # POST /agents, GET /agents/{id}
│       │   │       ├── jobs.py      # GET /jobs/{id}, GET /jobs/{id}/events (SSE)
│       │   │       └── health.py    # GET /health
│       │   ├── models/              # SQLAlchemy ORM models (control DB)
│       │   │   ├── tenant.py
│       │   │   ├── agent.py
│       │   │   ├── job.py
│       │   │   └── job_event.py
│       │   ├── schemas/             # Pydantic request/response schemas
│       │   │   ├── tenant.py
│       │   │   ├── agent.py
│       │   │   └── job.py
│       │   ├── services/
│       │   │   ├── neon.py          # Neon API client wrapper (project_create, poll)
│       │   │   ├── events.py        # emit() helper: DB persist + Redis publish
│       │   │   └── sse.py           # SSE generator: replay + subscribe
│       │   └── worker/
│       │       ├── celery_app.py    # Celery factory: queues, routes, config
│       │       └── tasks/
│       │           └── pipeline/
│       │               ├── provision.py     # provision_neon task
│       │               └── migrations.py    # apply_migrations task
│       ├── tests/
│       │   ├── conftest.py
│       │   ├── unit/
│       │   │   ├── test_security.py         # Fernet round-trip, argon2 hash/verify
│       │   │   ├── test_emit.py             # emit() writes to DB + Redis
│       │   │   └── test_schemas.py          # Pydantic validation
│       │   └── integration/
│       │       ├── test_chain.py            # full chain (respx mocked Neon + real PG)
│       │       ├── test_sse.py              # late-join test
│       │       └── test_worker_kill.py      # kill-9 + resume test
│       ├── Dockerfile
│       ├── pyproject.toml
│       └── Makefile
├── scripts/
│   ├── demo_m1.sh
│   └── fixtures/
│       └── demo_agent.json
├── docker-compose.yml
└── .env.example
```

**Two Alembic folders:** `alembic/` is for the control DB (ran at startup). `alembic_tenant/` is for per-tenant DBs (ran programmatically from Celery task). Each has its own `env.py` and `versions/` directory. Both `env.py` files support the `cfg.attributes['connection']` injection pattern for programmatic use. [ASSUMED: naming `alembic_tenant/` — other valid choices exist]

---

### Pattern 1: FastAPI Async SSE with Redis Pub/Sub (Replay-Then-Subscribe)

**What:** The SSE endpoint first replays all `job_events` rows from Postgres (for late-joining clients), then subscribes to the Redis pub/sub channel and streams new events. Closes when a terminal event arrives.

**When to use:** `GET /jobs/{job_id}/events`

**Key implementation points:**
- Use `sse-starlette` `EventSourceResponse` wrapping an async generator
- Use `redis.asyncio` (included in `redis` 7.x package) for async pub/sub inside the async generator
- Check `request.is_disconnected()` periodically to clean up orphaned connections
- Set `X-Accel-Buffering: no` response header to prevent nginx from buffering SSE

```python
# Source: sse-starlette docs (v3.4.4) + redis.asyncio pattern
from sse_starlette import EventSourceResponse, ServerSentEvent
from redis.asyncio import Redis
import json

async def event_generator(request: Request, job_id: UUID, db: AsyncSession, redis_client: Redis):
    # Phase 1: Replay history from DB
    past_events = await db.execute(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at)
    )
    for evt in past_events.scalars():
        yield ServerSentEvent(
            data=json.dumps(evt.payload),
            event=evt.event_type,
            id=str(evt.id),
        )
        # Check if already terminal — skip subscribe phase
        if evt.event_type in ("job.complete", "job.failed"):
            return

    # Phase 2: Subscribe and stream live events
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(f"job_events:{job_id}")
        async for message in pubsub.listen():
            if await request.is_disconnected():
                break
            if message["type"] == "message":
                data = json.loads(message["data"])
                yield ServerSentEvent(
                    data=json.dumps(data["payload"]),
                    event=data["event_type"],
                )
                if data["event_type"] in ("job.complete", "job.failed"):
                    break

@router.get("/jobs/{job_id}/events")
async def stream_events(
    request: Request,
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    _: Tenant = Depends(get_current_tenant),
):
    response = EventSourceResponse(event_generator(request, job_id, db, redis_client))
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-store"
    return response
```

[VERIFIED: sse-starlette 3.4.4 from GitHub README; redis.asyncio pubsub from redis-py docs]

---

### Pattern 2: `emit()` Helper — Atomic DB Persist + Redis Publish

**What:** Single function called by every Celery task at every checkpoint. Writes to `job_events` table (durable) and publishes to Redis channel (live stream).

**Critical detail:** Use a SYNC database session inside Celery tasks (Celery workers are sync processes). Use psycopg2 or SQLAlchemy sync session — NOT asyncpg/asyncio.

```python
# Source: Pattern from PRD §7.3; implementation uses sqlalchemy sync session
from datetime import datetime, timezone
import json
from uuid import UUID
from sqlalchemy.orm import Session
from redis import Redis as SyncRedis

def emit(
    job_id: UUID,
    event_type: str,
    payload: dict | None,
    db: Session,
    redis: SyncRedis,
) -> None:
    payload = payload or {}
    payload["at"] = datetime.now(timezone.utc).isoformat()

    # Persist to job_events (durable replay log)
    event = JobEvent(job_id=job_id, event_type=event_type, payload=payload)
    db.add(event)
    db.commit()

    # Publish to Redis pub/sub (live stream)
    redis.publish(
        f"job_events:{job_id}",
        json.dumps({"event_type": event_type, "payload": payload}),
    )
```

[CITED: prd-M1.md §7.3]

---

### Pattern 3: Celery Tasks with `acks_late` + Idempotency Guard

**What:** Every task checks whether it has already run before doing any work. `acks_late=True` ensures the task message is redelivered if the worker crashes. `task_reject_on_worker_lost=True` ensures the message is sent back to the queue (not silently dropped) on unexpected worker death.

**The idempotency guard pattern:**

```python
# Source: Celery 5.6.3 docs + Vinta Software production patterns
from celery import shared_task
from app.core.database import get_sync_db
from app.models.agent import Agent

@shared_task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def provision_neon(self, tenant_id: str, agent_id: str) -> dict:
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)

        # Idempotency guard — if already provisioned, return early
        if agent.neon_project_id:
            return {"agent_id": agent_id, "project_id": agent.neon_project_id}

        emit(agent.job_id, "neon.project.creating", {}, db, get_sync_redis())

        try:
            project = create_neon_project(agent_id)  # calls neon-api
        except NeonClientError as exc:
            if exc.status_code in range(400, 500):
                # 4xx — fatal, no retry
                agent.status = "failed"
                job = db.get(Job, agent.job_id)
                job.status = "failed"
                job.error = str(exc)
                db.commit()
                emit(agent.job_id, "job.failed", {"error": str(exc)}, db, get_sync_redis())
                return
            # 5xx or timeout — retry
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)

        # Encrypt and store
        encrypted = fernet_encrypt(project.connection_uri)
        agent.neon_project_id = project.id
        agent.neon_connection_string = encrypted  # BYTEA
        db.commit()

        emit(agent.job_id, "neon.project.ready", {"project_id": project.id}, db, get_sync_redis())
        return {"agent_id": agent_id, "project_id": project.id}
```

[VERIFIED: Celery 5.6.3 docs task config; bind=True for self.retry access; ARCHITECTURE.md]

---

### Pattern 4: Celery Chain — Context Passing Without Connection String in Args

**What:** The chain's return value from `provision_neon` is `{"agent_id": ..., "project_id": ...}`. The `apply_migrations` task receives this dict as its first argument. It uses `agent_id` to fetch and decrypt the connection string from the control DB — the connection string is never in the chain argument.

```python
# Source: PRD §4.2 + CONTEXT.md §Celery Chain
from celery import chain
from app.worker.tasks.pipeline.provision import provision_neon
from app.worker.tasks.pipeline.migrations import apply_migrations

def dispatch_create_agent_chain(tenant_id: str, agent_id: str) -> None:
    chain(
        provision_neon.s(tenant_id, agent_id),
        apply_migrations.s(),       # receives {"agent_id": ..., "project_id": ...}
    ).apply_async(queue="pipeline")


@shared_task(bind=True, acks_late=True, max_retries=3, queue="pipeline")
def apply_migrations(self, result: dict) -> None:
    agent_id = result["agent_id"]

    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)

        # Fetch and decrypt — NOT from task args
        conn_string = fernet_decrypt(agent.neon_connection_string)

        emit(agent.job_id, "migrations.running", {}, db, get_sync_redis())

        try:
            run_tenant_migrations(conn_string)   # programmatic alembic
        except Exception as exc:
            # Fatal — do not retry migration errors (leave for manual inspection)
            agent.status = "failed"
            db.get(Job, agent.job_id).status = "failed"
            db.commit()
            emit(agent.job_id, "job.failed", {"error": str(exc)}, db, get_sync_redis())
            return

        # Record schema version
        agent.schema_version = get_current_alembic_revision(conn_string)
        agent.status = "ready"
        db.get(Job, agent.job_id).status = "complete"
        db.commit()

        emit(agent.job_id, "migrations.complete", {}, db, get_sync_redis())
        emit(agent.job_id, "job.complete", {}, db, get_sync_redis())
```

[CITED: CONTEXT.md §Celery Chain decision; CLAUDE.md rule: connection strings never in task args]

---

### Pattern 5: Programmatic Alembic Per-Tenant Migration

**What:** Run `alembic upgrade head` inside a Celery task against a runtime-injected connection string (not from alembic.ini).

**Two-step approach:**
1. `env.py` checks `config.attributes['connection']` — uses it if present, falls back to `alembic.ini` for CLI use
2. Task calls `command.upgrade(cfg, "head")` with connection injected via `cfg.attributes`

```python
# alembic_tenant/env.py — key section
def run_migrations_online() -> None:
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
```

```python
# app/services/migrations.py
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool

def run_tenant_migrations(conn_string: str) -> None:
    """Run alembic upgrade head against a tenant DB via programmatic API."""
    # Alembic config pointing to the TENANT migration folder
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "alembic_tenant")
    alembic_cfg.set_main_option("sqlalchemy.url", conn_string)

    # Use NullPool for short-lived migration connection (no connection pooling)
    engine = create_engine(conn_string, poolclass=pool.NullPool)
    with engine.begin() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")


def get_current_alembic_revision(conn_string: str) -> str | None:
    """Return the current alembic revision ID applied to this DB."""
    from alembic.runtime.migration import MigrationContext
    engine = create_engine(conn_string, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()
```

[VERIFIED: Alembic 1.18.4 cookbook — `cfg.attributes['connection']` injection pattern; CITED: alembic.sqlalchemy.org/en/latest/cookbook.html]

---

### Pattern 6: Neon API Project Provisioning + Operation Polling

**What:** Create a Neon project via the Python SDK, then poll until all operations reach `finished` status before fetching the connection URI.

**Neon operation status lifecycle:** `scheduling` → `running` → `finished` (terminal). Also possible: `failed`, `cancelled`, `skipped`.

```python
# app/services/neon.py
import time
from neon_api import NeonAPI
from app.core.config import settings

def create_neon_project(agent_id: str) -> dict:
    """
    Create a Neon project and wait until all initial operations finish.
    Returns dict with 'id' (project_id) and 'connection_uri'.
    """
    client = NeonAPI(api_key=settings.NEON_API_KEY)

    # Create project
    response = client.project_create(project={
        "name": f"vrd-{agent_id}",
        "region_id": settings.NEON_REGION,
        "pg_version": 17,
    })
    project_id = response.project.id

    # Poll operations until all finish or timeout
    deadline = time.time() + 90
    while time.time() < deadline:
        ops = client.operations(project_id)
        pending = [op for op in ops.operations if op.status not in ("finished", "skipped", "cancelled")]
        if not pending:
            break
        time.sleep(2)
    else:
        raise TimeoutError(f"Neon project {project_id} did not become ready in 90s")

    # Fetch pooled connection URI (use pooler for application traffic)
    uri_response = client.connection_uri(
        project_id=project_id,
        database_name="neondb",
        role_name="neondb_owner",
        pooled=True,   # use PgBouncer endpoint for application traffic
    )
    return {"id": project_id, "connection_uri": uri_response.uri}
```

**Key notes:**
- Poll operations (not project status) — the Neon docs explicitly state to poll operation status [CITED: neon.com/docs/manage/operations]
- Use `pooled=True` in `connection_uri()` call for application traffic (prevents hitting Neon's direct connection limits under concurrent Celery workers) [CITED: PITFALLS.md performance traps]
- Default database name on a new Neon project is `neondb`; default role is `neondb_owner` [ASSUMED — verify against actual Neon project response at implementation time]

[VERIFIED: neon-api 0.3.0 PyPI; Neon operations docs; neon.com/docs/manage/operations]

---

### Pattern 7: Fernet Encryption for Connection Strings

**What:** Symmetric encryption of connection strings stored as BYTEA in Postgres.

```python
# app/core/security.py
import os
import base64
from cryptography.fernet import Fernet

def get_fernet() -> Fernet:
    """Get Fernet instance from environment key."""
    raw_key = os.environ["NEON_ENCRYPTION_KEY"]
    # Key must be URL-safe base64-encoded 32 bytes
    # Generate with: base64.urlsafe_b64encode(os.urandom(32))
    return Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)

def fernet_encrypt(plaintext: str) -> bytes:
    """Encrypt a connection string. Returns bytes for BYTEA storage."""
    f = get_fernet()
    return f.encrypt(plaintext.encode())

def fernet_decrypt(ciphertext: bytes) -> str:
    """Decrypt BYTEA ciphertext. Returns plaintext connection string."""
    f = get_fernet()
    return f.decrypt(ciphertext).decode()
```

**Key generation for `.env.example`:**
```python
import os, base64
key = base64.urlsafe_b64encode(os.urandom(32))
print(key.decode())  # put this in NEON_ENCRYPTION_KEY
```

[VERIFIED: cryptography.io/en/latest/fernet/ — Fernet takes URL-safe base64 32-byte key]

---

### Pattern 8: Argon2 API Key Hashing

**What:** Hash API keys before storage. Never store or log plaintext keys.

```python
# app/core/security.py
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# For API keys (not passwords) — default params are appropriate.
# time_cost=3, memory_cost=65536 (64MiB), parallelism=4 (Argon2id default)
_ph = PasswordHasher()

def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage. Returns encoded hash string."""
    return _ph.hash(raw_key)

def verify_api_key(stored_hash: str, raw_key: str) -> bool:
    """Verify an API key against its stored hash."""
    try:
        return _ph.verify(stored_hash, raw_key)
    except VerifyMismatchError:
        return False
```

**API key format:** `vrd_live_{secrets.token_urlsafe(32)}` — prefix makes keys identifiable in logs before they are masked.

[VERIFIED: argon2-cffi 25.1.0 docs; PasswordHasher.hash() and verify() signatures]

---

### Pattern 9: Celery Two-Queue Configuration

**What:** Define both `pipeline` and `runtime` queues at startup. Route tasks explicitly. Use `kombu.Queue` and `kombu.Exchange`.

```python
# app/worker/celery_app.py
from celery import Celery
from kombu import Exchange, Queue
from app.core.config import settings

app = Celery("veridian")

app.conf.update(
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,

    # Queue definitions
    task_queues=(
        Queue("pipeline", Exchange("pipeline", type="direct"), routing_key="pipeline"),
        Queue("runtime",  Exchange("runtime",  type="direct"), routing_key="runtime"),
    ),
    task_default_queue="runtime",

    # Route tasks to queues explicitly
    task_routes={
        "app.worker.tasks.pipeline.*": {"queue": "pipeline"},
        "app.worker.tasks.runtime.*":  {"queue": "runtime"},
    },

    # Reliability settings — apply globally
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,
)
```

**Worker startup commands (for docker-compose):**
```bash
# pipeline worker
celery -A app.worker.celery_app worker --queues=pipeline --hostname=pipeline@%h --loglevel=info

# runtime worker (idle in M1, topology correct)
celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%h --loglevel=info

# beat scheduler (idle in M1)
celery -A app.worker.celery_app beat --loglevel=info
```

[VERIFIED: Celery 5.6.3 routing docs; kombu Queue/Exchange from docs.celeryq.dev/en/stable/userguide/routing.html]

---

### Pattern 10: structlog Setup with Request ID Propagation to Celery

**What:** Structured JSON logging from FastAPI with a `request_id` bound per request, propagated to Celery tasks via task headers.

```python
# app/core/logging.py
import logging
import structlog

def configure_logging(log_level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    logging.basicConfig(level=log_level.upper())

# FastAPI middleware — pure ASGI (NOT BaseHTTPMiddleware, which loses context)
import uuid
from starlette.types import ASGIApp, Receive, Scope, Send

class RequestIdMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request_id = str(uuid.uuid4())
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(request_id=request_id)
        await self.app(scope, receive, send)
```

**Propagation to Celery via task headers:**
```python
# In FastAPI route, before dispatching chain:
from celery.app.task import Task
from structlog.contextvars import get_contextvars

def dispatch_create_agent_chain(tenant_id: str, agent_id: str) -> None:
    ctx = get_contextvars()
    chain(
        provision_neon.s(tenant_id, agent_id),
        apply_migrations.s(),
    ).apply_async(
        queue="pipeline",
        headers={"request_id": ctx.get("request_id", "")},
    )

# In Celery signal handler, bind task headers to structlog context:
from celery import signals

@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        task_id=task_id,
        task_name=task.name,
        request_id=task.request.get("headers", {}).get("request_id", ""),
    )
```

[VERIFIED: structlog 25.5.0 docs — contextvars module; CITED: structlog.org/en/stable/frameworks.html — Celery signal pattern]

**Note on middleware:** Use pure ASGI middleware (not `@app.middleware("http")` / `BaseHTTPMiddleware`) for structlog context binding. BaseHTTPMiddleware runs the endpoint in a task group, creating a copy of the context — `bind_contextvars()` calls in handlers do not propagate back to middleware. [CITED: GitHub fastapi/fastapi #4696]

---

### Pattern 11: docker-compose with Health-Check-Dependent Startup

**What:** Services must not start until their dependencies are healthy. Use `condition: service_healthy` in `depends_on`.

```yaml
# docker-compose.yml (key sections)
services:
  postgres:
    image: postgres:17-alpine
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    build: apps/api
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker_pipeline:
    build: apps/api
    command: celery -A app.worker.celery_app worker --queues=pipeline --hostname=pipeline@%h
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker_runtime:
    build: apps/api
    command: celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%h
    depends_on:
      redis:
        condition: service_healthy

  beat:
    build: apps/api
    command: celery -A app.worker.celery_app beat
    depends_on:
      redis:
        condition: service_healthy
```

[VERIFIED: Docker Compose docs — `condition: service_healthy`; CITED: docs.docker.com/compose/how-tos/startup-order/]

---

### Anti-Patterns to Avoid

- **Passing connection string as Celery task arg:** Plaintext appears in Redis, Flower UI, result backend. Use `agent_id` only; fetch at runtime. [CLAUDE.md rule]
- **Async inside Celery tasks:** Celery workers are sync processes. `asyncio.run()` inside a task blocks the entire worker. Use sync SQLAlchemy sessions and sync Redis client in tasks.
- **`BaseHTTPMiddleware` for request ID:** Creates context copy; bind_contextvars in handlers doesn't propagate back. Use pure ASGI middleware.
- **Nginx SSE buffering:** Without `X-Accel-Buffering: no`, events queue until the buffer fills (~16KB). All events arrive at once. Always set this header.
- **`acks_late` without idempotency guard:** `acks_late=True` ensures retry on crash — but does NOT prevent duplicate Neon project creation. Guard is: check `agent.neon_project_id` before calling Neon API.
- **Using `pooled=False` connection string for application Celery tasks:** Direct (non-pooled) connections hit Neon's connection limits when multiple Celery workers connect simultaneously. Use pooled endpoint for application traffic; direct endpoint only for Alembic migrations (poolers don't support DDL transactions).
- **Running Alembic migration through PgBouncer pooler:** PgBouncer in transaction mode does not support `SET search_path`, advisory locks, or DDL. Use the DIRECT (non-pooled) connection string for Alembic migrations. Use the POOLED connection string for application queries.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SSE streaming | Custom chunked response | `sse-starlette` `EventSourceResponse` | W3C compliant; disconnect detection; retry header; battle-tested |
| Symmetric encryption | Custom AES wrapper | `cryptography.fernet.Fernet` | AES-128-CBC + HMAC-SHA256; authenticated; key rotation via MultiFernet |
| Password/key hashing | Custom PBKDF2/bcrypt | `argon2-cffi` `PasswordHasher` | Argon2id wins PHC; memory-hard; default params tuned for production |
| Retry/backoff | Custom sleep loops | Celery's `self.retry(countdown=...)` | Exponential backoff; max_retries; proper ack management |
| Task serialization | Custom protocol | Celery JSON serializer (default) | JSON is inspectable, cross-language; pickle is a security risk |
| DB migration | Custom DDL scripts | Alembic | Revision tracking in `alembic_version` table; programmatic API; per-tenant safe |
| Env config | Manual `os.environ` | `pydantic-settings` `BaseSettings` | Type-safe; `.env` file loading; validation on startup |
| Structured logs | Manual JSON formatting | `structlog` | contextvars; processor pipeline; zero-cost when disabled |

---

## Common Pitfalls

### Pitfall 1: Alembic Using Pooler Connection String for DDL (Migration Fails Silently)

**What goes wrong:** `CREATE EXTENSION IF NOT EXISTS vector` fails or hangs when run through PgBouncer in transaction pooling mode. Migrations appear to succeed but the `vector` extension is never created; M2 embedding inserts fail.

**Why it happens:** PgBouncer's transaction pooling mode does not support DDL that requires persistent session state. The `CREATE EXTENSION` command and Alembic's own advisory lock for migration coordination both break.

**How to avoid:** Pass the DIRECT (non-pooled) connection string to Alembic migrations. Only use the pooled connection string for application-layer queries (FastAPI routes, retrieval). Store both URIs: fetch the pooled version with `pooled=True`, the direct version with `pooled=False` (or without the `-pooler` suffix in the hostname).

**Warning signs:** `alembic_version` table shows latest revision but `\dx` in psql shows `vector` extension is missing.

[CITED: PITFALLS.md integration gotchas — "Celery + Neon" section; VERIFIED via Neon connection docs]

---

### Pitfall 2: Worker Kill-9 Creates Duplicate Neon Projects

**What goes wrong:** `provision_neon` calls the Neon API, creates the project, then the worker is killed before writing `agent.neon_project_id` to the DB. On retry, the idempotency check (`if agent.neon_project_id`) fails (it's still None), so a second Neon project is created. Tenant has an orphaned project costing money.

**Why it happens:** `acks_late` causes the task to retry — correct behavior. But the idempotency guard relies on a DB write that didn't complete before the crash.

**How to avoid:** Write `agent.neon_project_id` to the DB IMMEDIATELY after getting the project ID back from the Neon API (before further processing). The guard check at task start will catch the second attempt. The connection string encryption and storage come after — those can be re-run safely.

**Implementation order inside `provision_neon`:**
1. Idempotency check
2. Emit job.started (first event in the chain)
3. Emit neon.project.creating
4. Call Neon API → get `project_id`
5. **Write `agent.neon_project_id` to DB immediately** (commit)
6. Poll operations until finished
7. Fetch connection URI (both pooled and direct)
8. Encrypt and write both BYTEA columns
9. Emit `neon.project.ready`

[CITED: PITFALLS.md §Pitfall 8 — acks_late without idempotency; ARCHITECTURE.md anti-pattern 5]

---

### Pitfall 3: Race Condition Between Neon "Active" Status and Query Readiness

**What goes wrong:** Neon operations show `finished` but the compute endpoint isn't fully warm. The `apply_migrations` task immediately connects and gets `connection refused`.

**Why it happens:** Neon is eventually consistent. Operation `finished` does not guarantee immediate query readiness. [CITED: PITFALLS.md §Pitfall 7]

**How to avoid:** After all operations are `finished`, add a connection probe loop before the migration task runs:
```python
def wait_for_neon_ready(conn_string: str, max_attempts: int = 10) -> None:
    from sqlalchemy import create_engine, text, pool
    import time
    for attempt in range(max_attempts):
        try:
            engine = create_engine(conn_string, poolclass=pool.NullPool)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception:
            time.sleep(2 ** attempt)  # exponential backoff
    raise RuntimeError("Neon project not query-ready after probe retries")
```

---

### Pitfall 4: SSE Stream Hangs Because Redis Pub/Sub Returns No Messages

**What goes wrong:** The `provision_neon` task completes and emits `job.complete` before the SSE client connects. The SSE endpoint replays past events from DB, then subscribes to a Redis channel that will never receive another message. The SSE connection hangs open indefinitely.

**Why it happens:** The DB replay phase closes the stream on terminal events. But if the terminal event happened AND is already in the DB replay, the subscribe phase never starts. This is correct. The bug occurs if the DB replay doesn't check for terminal events.

**How to avoid:** In the event generator, after replaying each DB event, check whether it is terminal. If so, `return` from the generator immediately — do not enter the subscribe phase.

[CITED: CONTEXT.md §SSE endpoint behaviour]

---

### Pitfall 5: Celery Chain Stops on First Task Failure — Tenant Left in `provisioning` State

**What goes wrong:** `provision_neon` fails fatally (Neon 4xx). The chain halts. `agent.status` was set to `provisioning` at the start of the task. The job.status is set to `failed`. But if the route to set `agent.status = 'failed'` is missed in the error handler, the agent stays in `provisioning` forever.

**How to avoid:** In every fatal failure path, ensure BOTH `job.status = 'failed'` AND `agent.status = 'failed'` are written, AND `job.error` is populated, AND `job.finished_at` is set. Use a context manager or helper that handles all four fields atomically.

---

### Pitfall 6: `structlog` contextvars Not Cleared Between Celery Tasks

**What goes wrong:** Celery worker processes are persistent. If contextvars from a previous task are not cleared before the next task starts, log lines for Task B include `request_id` from Task A.

**How to avoid:** In the `task_prerun` signal handler, always call `structlog.contextvars.clear_contextvars()` before `bind_contextvars()`.

[CITED: structlog docs — contextvars module; structlog Celery integration pattern]

---

### Pitfall 7: `pytest` with `CELERY_TASK_ALWAYS_EAGER` vs Real Workers

**What goes wrong:** Integration tests that start a real Celery worker and send tasks via `.delay()` will time out or be skipped if `CELERY_TASK_ALWAYS_EAGER=True` is set globally. The worker-kill test (CTL-07) MUST use a real worker, not eager mode.

**How to avoid:** Never set `CELERY_TASK_ALWAYS_EAGER=True` in integration tests. Integration tests that test chain behavior, retries, and worker-kill scenarios must start a real worker subprocess and dispatch via `.apply_async()`. Poll DB for expected state changes (e.g., `agent.status == "ready"`) with a timeout loop. Use pytest fixtures that start/stop a worker process for these tests.

[CITED: ARCHITECTURE.md anti-pattern 11]

---

## Code Examples

### Health Check Endpoint

```python
# app/api/v1/health.py
from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

router = APIRouter()

@router.get("/health")
async def health(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)):
    db_ok = "ok"
    redis_ok = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = "error"
    try:
        await redis.ping()
    except Exception:
        redis_ok = "error"
    return {"status": "ok", "redis": redis_ok, "db": db_ok}
```

### API Key Authentication Dependency

```python
# app/api/deps.py
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import verify_api_key
from app.models.tenant import Tenant

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

async def get_current_tenant(
    api_key: str = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    tenant = await db.execute(
        select(Tenant).where(Tenant.deleted_at.is_(None))
    )
    for row in tenant.scalars():
        if verify_api_key(row.api_key_hash, api_key):
            return row
    raise HTTPException(status_code=401, detail="Invalid API key")
```

**Note:** The above does a full table scan when verifying. For production at M1 scale (few tenants), this is acceptable. Store a hash prefix as a lookup key for future optimization. [ASSUMED: linear scan acceptable at M1 tenant count]

### Pydantic Settings (pydantic-settings v2)

```python
# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neon
    NEON_API_KEY: str
    NEON_REGION: str = "aws-us-east-1"
    NEON_ENCRYPTION_KEY: bytes  # base64url-encoded 32 bytes

    # Database
    CONTROL_DB_URL: str  # postgresql+asyncpg://... for FastAPI
    CONTROL_DB_SYNC_URL: str  # postgresql://... for Celery/Alembic

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Observability
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str | None = None

    # Auth
    ADMIN_KEY: str  # for X-Admin-Key on POST /tenants

settings = Settings()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| WebSockets for job status | SSE via `sse-starlette` | Industry consensus 2023+ | Simpler; unidirectional; scales easily with Redis pub/sub |
| Schema-per-tenant Postgres | Project-per-tenant (Neon) | Neon made this feasible ~2023 | True isolation; eval branching; scale to zero |
| bcrypt for API keys | Argon2id (`argon2-cffi`) | PHC winner 2015; default in argon2-cffi v21+ | Memory-hard; GPU-resistant; parameterizable |
| AES manual implementation | `cryptography.fernet.Fernet` | `cryptography` library matured ~2014 | Authenticated encryption; no rolling-your-own crypto |
| Alembic CLI via subprocess | Alembic programmatic `command.upgrade()` | Alembic has supported this since v0.6; `cfg.attributes` pattern v0.9+ | No subprocess spawn; cleaner error handling; testable |
| `aioredis` (separate package) | `redis.asyncio` (in `redis-py` 4.2+) | `aioredis` merged into `redis-py` 2022 | One package; maintained by Redis Labs |

**Deprecated/outdated:**
- `aioredis` as a standalone package: merged into `redis-py` as `redis.asyncio`. Install `redis[asyncio]` or just `redis`.
- `BaseHTTPMiddleware` for context propagation in FastAPI: creates context isolation bug; use pure ASGI middleware.

---

## Open Questions (RESOLVED)

1. **RESOLVED: Neon project default role and database names**
   - What we know: neon-api SDK `connection_uri(project_id, database_name, role_name)` requires explicit names
   - RESOLVED: Use `neondb` as database_name and `neondb_owner` as role_name — these are the Neon defaults for newly created projects. Log the full project_create() response on the first real run to verify. Parameterize as `NEON_DEFAULT_DATABASE` / `NEON_DEFAULT_ROLE` in Settings if the response shows different names.

2. **RESOLVED: Alembic tenant migration script_location as absolute vs relative path**
   - What we know: `alembic_cfg.set_main_option("script_location", "alembic_tenant")` works relative to CWD
   - RESOLVED: Use `Path(__file__).parent.parent / "alembic_tenant"` for an absolute path rooted at the package. When the Celery task runs from an arbitrary CWD, the absolute path derived from `__file__` always resolves correctly.

3. **RESOLVED: Redis connection reuse in Celery tasks**
   - What we know: Each task needs a sync Redis client for `emit()`. Creating a new client per task call is wasteful.
   - RESOLVED: Create a module-level SyncRedis client instance in the task module (`_redis = redis.from_url(settings.REDIS_URL)`). It is shared across task invocations within the same worker process. Each Celery worker process has its own module-level instance — no cross-process sharing issues.

---

## Environment Availability

> Step 2.6 check: M1 depends on Redis, Postgres, and the Neon API.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis | Celery broker, SSE pub/sub | ✓ (via docker-compose) | 7.x | — (no viable fallback; required by design) |
| Postgres | Control DB (docker-compose) | ✓ (via docker-compose) | 17-alpine | — |
| Neon API | `provision_neon` task | ✓ (HTTP API, internet-dependent) | v2 API | Mocked via `respx` in integration tests |
| Python 3.11+ | All libraries | ✓ | 3.11 or 3.12 | — |
| Docker + Compose | Local dev | [ASSUMED: available on dev machine] | v2 | Can run services on host |

**Missing dependencies with no fallback:**
- Neon API key (`NEON_API_KEY`) required for production provisioning. Nightly CI E2E (CTL-14) requires a real Neon test account. Integration tests (CTL-13) mock the Neon API via `respx`.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml` (pytest section) |
| Quick run command | `pytest tests/unit -x --tb=short` |
| Full suite command | `pytest tests/ -x --tb=short` |
| Coverage command | `pytest tests/ --cov=app --cov-report=term-missing` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CTL-01 | POST /agents returns 202 with job_id | unit (FastAPI TestClient) | `pytest tests/unit/test_routes.py::test_post_agents -x` | Wave 0 |
| CTL-02 | Chain runs idempotently with acks_late | integration | `pytest tests/integration/test_chain.py -x` | Wave 0 |
| CTL-03 | provision_neon encrypts + stores conn string | integration (respx) | `pytest tests/integration/test_provision.py -x` | Wave 0 |
| CTL-04 | apply_migrations runs v1 tenant schema | integration (real local PG) | `pytest tests/integration/test_migrations.py -x` | Wave 0 |
| CTL-05 | SSE replays prior events on late connect | integration | `pytest tests/integration/test_sse.py::test_late_join -x` | Wave 0 |
| CTL-06 | All 6 events emitted in order | integration | `pytest tests/integration/test_chain.py::test_event_sequence -x` | Wave 0 |
| CTL-07 | Worker kill-9 → retry → completes | integration (real worker) | `pytest tests/integration/test_worker_kill.py -x` | Wave 0 |
| CTL-08 | Connection string not in task args | unit (inspect args) | `pytest tests/unit/test_task_args.py -x` | Wave 0 |
| CTL-09 | argon2 hash/verify round-trip | unit | `pytest tests/unit/test_security.py::test_api_key_hash -x` | Wave 0 |
| CTL-09 | Auth rejects wrong API key | unit | `pytest tests/unit/test_auth.py::test_invalid_key -x` | Wave 0 |
| CTL-10 | GET /health returns 200 with redis+db ok | unit | `pytest tests/unit/test_health.py -x` | Wave 0 |
| CTL-11 | docker-compose up (6 services) | manual smoke | `docker-compose up -d && docker-compose ps` | — |
| CTL-12 | demo_m1.sh runs clean | manual / CI E2E | `bash scripts/demo_m1.sh` | Wave 0 |
| CTL-13 | Unit coverage >80% | coverage | `pytest tests/unit --cov=app --cov-fail-under=80` | Wave 0 |
| CTL-14 | Nightly E2E real Neon | CI nightly | GitHub Actions nightly job | Wave 0 |
| CTL-15 | README + diagram + demo | manual | Code review | — |

### Sampling Rate

- **Per task commit:** `pytest tests/unit -x --tb=short -q`
- **Per wave merge:** `pytest tests/ -x --tb=short`
- **Phase gate:** Full suite green + coverage ≥ 80% before `/gsd-verify-work`

### Wave 0 Gaps (all test files need to be created)

- [ ] `tests/conftest.py` — shared fixtures: async DB session, sync DB session, mock Redis, test tenant
- [ ] `tests/unit/test_security.py` — Fernet round-trip; argon2 hash/verify
- [ ] `tests/unit/test_schemas.py` — Pydantic validation for POST /agents payloads
- [ ] `tests/unit/test_emit.py` — emit() writes to both DB and Redis
- [ ] `tests/unit/test_task_args.py` — assert provision_neon signature has no connection string param
- [ ] `tests/unit/test_health.py` — health endpoint unit test
- [ ] `tests/unit/test_auth.py` — auth dependency unit tests
- [ ] `tests/unit/test_routes.py` — route unit tests (POST /agents, GET /agents/{id})
- [ ] `tests/integration/test_chain.py` — full chain (respx + real local PG)
- [ ] `tests/integration/test_provision.py` — provision_neon with mocked Neon API
- [ ] `tests/integration/test_migrations.py` — apply_migrations against real local PG
- [ ] `tests/integration/test_sse.py` — late-join SSE test
- [ ] `tests/integration/test_worker_kill.py` — kill-9 resilience test
- [ ] `scripts/demo_m1.sh` — demo script
- [ ] `.github/workflows/ci.yml` — PR CI (lint + type-check + unit + integration)
- [ ] `.github/workflows/nightly.yml` — E2E against real Neon

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | argon2-cffi `PasswordHasher`; API key in header only |
| V3 Session Management | no | Stateless API key auth; no sessions in M1 |
| V4 Access Control | yes | All routes require valid API key; admin routes require ADMIN_KEY |
| V5 Input Validation | yes | Pydantic v2 models on all request bodies; enum validation for `role` field |
| V6 Cryptography | yes | Fernet (AES-128-CBC + HMAC-SHA256) for connection strings; never hand-rolled |
| V7 Error Handling | yes | Structured errors; never leak DB details or connection strings in error responses |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| API key in logs | Information Disclosure | Never log `X-API-Key` header; structlog processors strip sensitive headers |
| Connection string in Redis (Celery queue) | Information Disclosure | Pass `agent_id` only; tasks fetch + decrypt at runtime |
| CORS misconfiguration | Elevation of Privilege | Lock CORS to admin UI origin only; no wildcard in M1 |
| Timing attack on API key compare | Spoofing | argon2-cffi `verify()` is constant-time resistant |
| SQL injection via soul/name fields | Tampering | SQLAlchemy parameterized queries; no raw SQL with user input |
| Fernet key in source code | Information Disclosure | Key in environment variable only; `.env.example` has placeholder |

**`Cache-Control: no-store`:** Must be set on ALL responses (not just auth endpoints). [CITED: CONTEXT.md §Authentication; CTL-09 requirement]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | New Neon projects default to `neondb` database + `neondb_owner` role | Pattern 6 (Neon provisioning) | `connection_uri()` call fails with wrong params; project creation succeeds but can't connect |
| A2 | Linear API key lookup (full table scan) acceptable at M1 tenant count | Pattern: API Key Auth | Performance degradation if tenant count grows quickly in testing; minor, add lookup optimization |
| A3 | `alembic_tenant/` as folder name is an acceptable choice for Claude's Discretion | Project Structure | Cosmetic; any name works |
| A4 | Module-level sync Redis client shared across Celery task invocations in same process | Pattern 10 (emit helper) | Connection leaks if process forks unexpectedly; mitigate by using redis connection pool |
| A5 | Python 3.11 or 3.12 available in dev environment | Environment | All libraries support 3.11+; no action needed unless 3.9/3.10 must be supported |
| A6 | Docker + Docker Compose v2 available on dev machine | Environment | `make dev-light` and docker-compose commands fail; must install Docker |

---

## Sources

### Primary (HIGH confidence)
- PyPI registry (pip index versions, 2026-05-12) — all package versions verified
- `CONTEXT.md` — all locked decisions cited directly
- `prd-M1.md` — canonical source for exact schemas, task logic, API surface
- `ARCHITECTURE.md` — project-level architecture decisions (verified 2026-05-12)
- `STACK.md` — stack validation against official sources (2026-05-12)
- `PITFALLS.md` — pitfall catalog (verified 2026-05-12)
- cryptography.io/en/latest/fernet/ — Fernet API verified
- argon2-cffi.readthedocs.io/en/stable/api.html — PasswordHasher API verified
- docs.celeryq.dev/en/stable/ — Celery 5.6.3 tasks + routing docs verified
- alembic.sqlalchemy.org/en/latest/cookbook.html — programmatic upgrade pattern verified
- sse-starlette GitHub README (v3.4.4) — EventSourceResponse + disconnect pattern verified
- neon.com/docs/manage/operations — Neon operation status values verified

### Secondary (MEDIUM confidence)
- structlog.org/en/stable/frameworks.html — Celery signal integration pattern
- GitHub fastapi/fastapi #4696 — BaseHTTPMiddleware context isolation bug
- docs.docker.com/compose/how-tos/startup-order/ — `condition: service_healthy`

### Tertiary (LOW confidence)
- Neon default database/role name assumptions — verify against first API response

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified against PyPI on 2026-05-12
- Architecture patterns: HIGH — core patterns from verified official docs; SSE + Alembic programmatic patterns from official sources
- Pitfalls: HIGH — derived from project-level PITFALLS.md (verified) + additional implementation-specific gaps found during research
- Neon API behavior: MEDIUM — operation polling status values verified; default project names ASSUMED

**Research date:** 2026-05-12
**Valid until:** 2026-06-12 (libraries stable; Neon API v2 has no announced breaking changes)
