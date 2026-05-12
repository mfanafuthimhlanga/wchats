# Phase 1: Control Plane Skeleton — Context

**Gathered:** 2026-05-12
**Status:** Ready for planning
**Source:** PRD Express Path (prd-M1.md)

<domain>
## Phase Boundary

M1 establishes the infrastructure foundation every subsequent milestone depends on:

- FastAPI control plane (auth, tenant CRUD, agent CRUD, SSE job status endpoint, health)
- Control DB schema (tenants, agents, jobs, job_events) on a shared Neon project
- Celery + Redis: two queues (`pipeline`, `runtime`) configured from day one
- `provision_neon` Celery task: calls Neon API, polls for readiness, encrypts and stores connection string
- `apply_migrations` Celery task: runs programmatic Alembic `upgrade head` against the tenant DB
- Per-tenant Alembic migration for the v1 tenant schema (all M1–M10 tables, empty in M1)
- SSE pattern: tasks emit to Redis pub/sub; `job_events` table provides durable replay for late-joining clients
- Local dev via docker-compose (6 services), CI on GitHub Actions, demo script `scripts/demo_m1.sh`

**Out of scope:** document parsing, chunking, embedding, retrieval, agent execution, frontend admin UI,
widget delivery, validation chain, evals, red team, billing, cost tracking.

</domain>

<decisions>
## Implementation Decisions

### API Framework & Routes
- FastAPI with Pydantic; OpenAPI docs at `/docs`
- `POST /tenants` — bootstrap tenant (admin operation), returns `{id, name, api_key}` as `201`
- `POST /agents` — returns `202 Accepted` with `{agent_id, job_id, status, events_url}`
- `GET /agents/{id}` — current agent state including provisioning status
- `GET /jobs/{job_id}` — job row + paginated event log
- `GET /jobs/{job_id}/events` — SSE stream (replay-then-subscribe)
- `GET /health` — `200 {"status":"ok","redis":"ok","db":"ok"}`
- All routes require `X-API-Key` header (admin tenant-bootstrap route uses `X-Admin-Key`)

### Authentication
- API key auth only (`X-API-Key` header)
- Keys stored hashed with argon2, never stored or logged in plaintext
- `Cache-Control: no-store` on all API responses
- CORS locked to known origins (admin UI only; widget CORS lands in M4)

### Celery Configuration
- `acks_late=True` on every task — non-negotiable (CLAUDE.md rule)
- Two queues always present: `pipeline` (ingestion/build) and `runtime` (evals, agent calls)
- Celery beat service running from day one (idle in M1)
- Connection strings NEVER passed as Celery task args; tasks fetch and decrypt from control DB at runtime

### Celery Chain
```python
chain(
    provision_neon.s(tenant_id, agent_id),
    apply_migrations.s(),
).apply_async(queue="pipeline")
```

### `provision_neon(tenant_id, agent_id)` task
- Mark `agent.status = 'provisioning'`
- Emit `neon.project.creating`
- Call Neon API `POST /projects` with name `vrd-{agent_id}`
- Poll `GET /projects/{project_id}` every 2s until `active` or 90s timeout
- On success: encrypt connection string with Fernet (`NEON_ENCRYPTION_KEY`), write `agent.neon_project_id` and `agent.neon_connection_string` (BYTEA)
- Emit `neon.project.ready`
- Return `{"agent_id": ..., "project_id": ...}` for next chain task
- Idempotency: if `agent.neon_project_id` already set, skip and return early
- Neon 4xx → `job.status='failed'`, emit `job.failed`, no retry
- Neon 5xx or timeout → exponential backoff, up to 3 retries
- Encryption failure → fatal, no retry

### `apply_migrations(result)` task
- Decrypt connection string from previous task's result (fetch from DB by agent_id)
- Emit `migrations.running`
- Run `alembic upgrade head` programmatically against tenant DB
- Write `agent.schema_version` (final revision ID) and `agent.status = 'ready'`
- Emit `migrations.complete` and `job.complete`
- Idempotency: Alembic `upgrade head` is naturally idempotent (no-op if already applied)
- Connection failure → retry 3x exponential backoff
- Migration error → fatal, `job.failed`, log for manual inspection

### Event Emission Pattern
All six SSE events emitted in order: `job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`

Single `emit(job_id, event_type, payload)` helper:
- Persists row to `job_events` table (durable replay log)
- Publishes to Redis channel `job_events:{job_id}` (live stream)
- Adds `"at": utc_iso` to payload

SSE endpoint behaviour on connect:
1. Replay all `job_events` rows from DB (late-join backfill)
2. Subscribe to `job_events:{job_id}` Redis channel for live events
3. Close stream when terminal event (`job.complete` or `job.failed`) received

Each SSE event format:
```
event: neon.project.creating
data: {"job_id": "uuid", "at": "2026-05-12T..."}

```

### Encryption
- Fernet symmetric encryption
- Key: `NEON_ENCRYPTION_KEY` (32 random bytes from platform secret manager, not in source)
- Connection strings stored as `BYTEA` on `agent.neon_connection_string`

### Control DB Schema (shared Neon project)
Exact schema per PRD §5:
- `tenants(id UUID PK, name TEXT, api_key TEXT UNIQUE, created_at TIMESTAMPTZ, deleted_at TIMESTAMPTZ)`
- `agents(id UUID PK, tenant_id UUID FK, name TEXT, soul JSONB, role TEXT, neon_project_id TEXT, neon_connection_string BYTEA, schema_version TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ, deleted_at TIMESTAMPTZ)` + index on `tenant_id`
- `jobs(id UUID PK, tenant_id UUID FK, agent_id UUID FK, kind TEXT, status TEXT DEFAULT 'pending', error TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, created_at TIMESTAMPTZ)` + index on `agent_id`
- `job_events(id BIGSERIAL PK, job_id UUID FK, event_type TEXT, payload JSONB, created_at TIMESTAMPTZ)` + index on `(job_id, created_at)`

### Tenant DB Schema (per-tenant Neon project, v1)
Created by per-tenant Alembic migration (programmatic). Exact schema per PRD §5.1:
- `documents`, `chunks`, `embeddings`, `chunk_metadata`, `conversations`, `messages`, `tool_calls`, `eval_runs`, `eval_results`, `red_team_runs`
- `CREATE EXTENSION IF NOT EXISTS vector` and `pg_trgm`
- `chunks` has GIN tsvector index for BM25 (M3) and `embeddings` has HNSW index for pgvector (M2/M3)
- Tables are empty in M1 — schema only, no logic

### Local Development
- docker-compose with exactly 6 services: `postgres`, `redis`, `api`, `worker_pipeline`, `worker_runtime`, `beat`
- `api`: uvicorn with `--reload`
- `worker_runtime`: idle in M1, configured so topology is correct
- `beat`: idle in M1
- `.env.example` with: `NEON_API_KEY`, `NEON_REGION`, `NEON_ENCRYPTION_KEY`, `CONTROL_DB_URL`, `REDIS_URL`, `LOG_LEVEL`
- `make dev-light` target: runs only `postgres`, `redis`, `api`; workers run on host

### Observability (M1 baseline)
- structlog for structured JSON logs from FastAPI and Celery
- Request ID propagated FastAPI → Celery via task headers, in every log line
- Sentry SDK initialized, active only if `SENTRY_DSN` env var is set

### Testing Strategy
- Unit tests (>80% coverage on orchestration logic):
  - Pydantic schema validation for POST /agents
  - Fernet encrypt/decrypt round-trip
  - `emit()` helper writes to both DB and Redis
  - `provision_neon` idempotency (double-call → one Neon project)
- Integration tests (mocked Neon API via `respx` + real local Postgres):
  - Full chain: tenant DB has v1 schema, `agent.status='ready'`, all events emitted in order
  - Worker-kill test: kill worker mid-chain, restart, assert task completes, agent ends `ready`
  - SSE late-join test: connect mid-chain, assert both prior and subsequent events received
- Nightly CI E2E: real Neon test account, creates agent, asserts schema, deletes project at teardown

### CI Pipeline (GitHub Actions)
- On every PR: ruff (lint), mypy or pyright (type-check), unit tests, integration tests (mocked Neon)
- Nightly: E2E against real Neon test account

### Demo
- `scripts/demo_m1.sh`: bootstraps tenant, creates agent, streams SSE, prints final state
- Passes when: 6 SSE events emitted in order, final GET /agents shows `status:ready` with `neon_project_id` and `schema_version` set
- README with architecture diagram + recorded asciinema or video

### Deliverables (per PRD §13)
- FastAPI app (all routes listed above) with OpenAPI at /docs
- Alembic migration set for control DB
- Alembic migration set for v1 tenant schema
- `provision_neon` and `apply_migrations` tasks with full test coverage
- SSE endpoint with replay-then-subscribe
- docker-compose (6 services)
- `.env.example` + README local setup section
- `scripts/demo_m1.sh`
- GitHub Actions CI
- Recorded demo (asciinema or video) in README
- `docs/M1-retro.md` post-build retrospective

### Claude's Discretion
- Project directory layout and Python module structure
- Alembic `env.py` and migrations folder structure
- Docker base image choice (Python version, slim/full)
- Makefile targets beyond `dev-light`
- pytest configuration and test directory layout
- Whether to use `asyncpg` vs `psycopg` for async DB access in FastAPI
- Redis client library choice (`redis-py` sync vs `aioredis` / `redis.asyncio`)
- Whether to use `sqlalchemy` ORM or raw SQL for control DB queries

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Architecture & Constraints
- `.planning/PROJECT.md` — core value statement, technology decisions, architectural principles
- `.planning/ROADMAP.md` — M1 success criteria, dependency graph, milestone phases
- `CLAUDE.md` — project rules: `acks_late=True`, idempotency, connection-string security, queue names

### Requirements
- `.planning/REQUIREMENTS.md` — CTL-01 through CTL-15 (Phase 1 requirements)

### PRD (canonical source)
- `prd-M1.md` — complete M1 PRD with exact schemas, task logic, API surface, and test strategy

### Research (project-level)
- `.planning/research/ARCHITECTURE.md` — architectural decisions from project setup
- `.planning/research/STACK.md` — stack choices and rationale
- `.planning/research/PITFALLS.md` — known risks and mitigations

</canonical_refs>

<specifics>
## Specific Ideas

### Exact API response bodies (from PRD)
`POST /agents` → `202 {"agent_id":"uuid","job_id":"uuid","status":"pending","events_url":"/jobs/{job_id}/events"}`
`POST /tenants` → `201 {"id":"uuid","name":"...","api_key":"vrd_live_..."}`
`GET /health` → `200 {"status":"ok","redis":"ok","db":"ok"}`

### Exact SSE event sequence
`job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`

### Fernet key management
`NEON_ENCRYPTION_KEY` = 32 random bytes stored in platform secret manager. Key rotation is a known gap, deferred to M10.

### Neon project naming
Neon project name: `vrd-{agent_id}`

### Agent status lifecycle
`pending` → `provisioning` → `ready` (or `failed`)

### Job status lifecycle
`pending` → `running` → `complete` (or `failed`)

</specifics>

<deferred>
## Deferred Ideas

- Connection-string encryption key rotation → M10
- Automated cleanup of failed tenant DBs → M10
- Real auth providers (OAuth) → v2/AUTH-01
- Frontend admin UI → M4
- Widget delivery → M4
- Validation chain, evals, red team → M5–M7
- Cost tracking, full observability → M10
- Neon branch exposure as user-facing feature → v2/ADV-01
- `make dev-light` multi-worker config → Claude's discretion
- M1 retrospective (`docs/M1-retro.md`) → written post-build, not pre-planned

</deferred>

---

*Phase: 01-control-plane-skeleton*
*Context gathered: 2026-05-12 via PRD Express Path (prd-M1.md)*
