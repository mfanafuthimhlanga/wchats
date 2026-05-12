# Veridian — M1 PRD: Control Plane Skeleton

> **Milestone:** M1 of 10
> **Parent PRD:** `PRD.md`
> **Status:** Ready for implementation
> **Owner:** Mfanafuthi Mhlanga
> **Target duration:** 2 weeks of focused build time
> **Last updated:** 2026-05-12

---

## 1. Purpose of this milestone

M1 establishes the foundation every subsequent milestone depends on: a FastAPI control plane that can accept an agent-creation request, dispatch it to Celery, provision a dedicated Neon project for the tenant, run schema migrations against the new database, and stream live status back to the caller via Server-Sent Events.

Nothing about ingestion, retrieval, or agent behaviour exists yet. M1 is pure plumbing — but it is the plumbing that proves the architectural thesis. By the end of M1, a curl command creates a tenant, kicks off a real Celery chain, and the browser watches a real Neon project come into existence in real time. That demo alone validates the "programmatic core, agentic edges" claim at the infrastructure level.

## 2. Scope

### In scope

- FastAPI application skeleton with auth, tenant model, agent CRUD endpoints, SSE status endpoint.
- Control DB schema (tenants, agents, jobs, job_events) on a shared Neon project.
- Celery + Redis setup with two queues (`pipeline`, `runtime`).
- The `provision_neon` Celery task: calls Neon API, polls for readiness, runs Alembic migrations against the new tenant DB, stores the encrypted connection string.
- Per-tenant Alembic migration that creates the v1 tenant schema (documents, chunks, embeddings, chunk_metadata, conversations, messages, tool_calls, eval_runs, eval_results, red_team_runs — empty tables, no logic).
- SSE job status pattern: every Celery task emits structured events to a Redis pub/sub channel; the SSE endpoint subscribes and streams to the client.
- Local development setup via docker-compose (Postgres, Redis, FastAPI, Celery worker, Celery beat).
- A working CI pipeline (lint, type-check, test) on GitHub Actions.
- A demo script (`scripts/demo_m1.sh`) that drives the full flow end-to-end.

### Out of scope

- Document parsing, chunking, embedding, retrieval, agent execution. All deferred to M2+.
- Real billing, real auth providers (a simple API key auth is sufficient for M1).
- Frontend admin UI. The demo runs from curl + a browser tab on the SSE endpoint.
- Widget delivery.
- Validation chain, evals, red team.
- Cost tracking, observability beyond basic logging.

## 3. Success criteria

M1 is shipped when all of the following are true:

1. `POST /agents` with a valid payload returns `202 Accepted` with a `job_id` and creates `tenant`, `agent`, and `job` rows in the control DB.
2. Within 60 seconds (typical Neon project creation time), a new Neon project exists under the platform's Neon account, Alembic migrations have been applied to it, and its encrypted connection string is stored on the `agent` row.
3. `GET /jobs/{job_id}/events` returns a live SSE stream that emits at least the following events in order: `job.started`, `neon.project.creating`, `neon.project.ready`, `migrations.running`, `migrations.complete`, `job.complete`.
4. A worker dying mid-task (kill -9 the Celery worker after `neon.project.ready` but before `migrations.complete`) results in the task being retried, not the tenant DB being left in a half-migrated state.
5. The demo script runs from a clean docker-compose up, completes without manual intervention, and prints a final block showing the tenant ID, agent ID, and connection string of the new tenant DB.
6. Unit test coverage on the orchestration logic above 80%. Integration test that exercises the full chain against a real Neon test project, run nightly in CI.
7. README documents the architecture and includes a recorded asciinema or video of the demo running.

## 4. Architecture

### 4.1 Component layout

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI control plane                   │
│  /agents (POST, GET)   /jobs/{id}/events (SSE)   /health    │
└──────────────┬──────────────────────────────────┬───────────┘
               │                                  │
               │ dispatch                         │ subscribe
               ▼                                  ▼
       ┌──────────────┐                  ┌──────────────────┐
       │ Celery queue │                  │ Redis pub/sub    │
       │  (pipeline)  │                  │ job_events:{id}  │
       └──────┬───────┘                  └──────────────────┘
              │                                  ▲
              │ task                             │ publish
              ▼                                  │
       ┌─────────────────────────────────────────┴──────────┐
       │              Celery worker (pipeline)              │
       │                                                    │
       │   provision_neon  →  apply_migrations              │
       │        │                  │                        │
       │        ▼                  ▼                        │
       │   Neon API           Alembic                       │
       └────────────────────────────────────────────────────┘
                            │
                            ▼
              ┌──────────────────────────┐
              │   Control DB (Neon)      │
              │   tenants, agents,       │
              │   jobs, job_events       │
              └──────────────────────────┘
```

### 4.2 The canonical M1 chain

```python
chain(
    provision_neon.s(tenant_id, agent_id),
    apply_migrations.s(),
).apply_async(queue="pipeline")
```

Two tasks. Each is idempotent. Each emits SSE events at start, on progress, and on completion. Each writes its result to the `job` row.

In later milestones this chain grows to ten-plus tasks. M1 establishes the pattern with two so the wiring is proven before complexity is added.

### 4.3 Why this shape

- **FastAPI never does work inline.** Every long-running operation goes to Celery. The API thread returns in milliseconds. This is the discipline that prevents the system from degrading as more milestones add work.
- **Celery with `acks_late=True` and idempotent tasks.** A worker can die at any point and the task is retried. The tenant DB is never left half-built. This is the production-rigor signal at the orchestration level.
- **Redis pub/sub for SSE.** Celery tasks publish events to a per-job channel. The SSE endpoint subscribes when a client connects. No polling, no database hammering, no shared task state outside Redis. Trivial to scale horizontally — multiple FastAPI instances can each subscribe to the same channel.
- **Two queues from day one.** Even though M1 only uses `pipeline`, the `runtime` queue is configured. Adding it later means reconfiguring deployed workers, which is exactly the kind of avoidable migration that bites portfolio projects.
- **Alembic per tenant.** The tenant migrations are versioned independently of the control DB migrations. M1 establishes the pattern where each tenant DB tracks its own schema version, so future migrations can be rolled out tenant-by-tenant if needed.

## 5. Data model (control DB)

All on a single shared Neon project. Postgres 15+.

```sql
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    api_key     TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE TABLE agents (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                UUID NOT NULL REFERENCES tenants(id),
    name                     TEXT NOT NULL,
    soul                     JSONB NOT NULL,           -- identity, voice, do/do-not
    role                     TEXT NOT NULL,            -- 'support' | 'sales' | 'helpdesk'
    neon_project_id          TEXT,                     -- set after provisioning
    neon_connection_string   BYTEA,                    -- encrypted at rest
    schema_version           TEXT,                     -- alembic revision id
    status                   TEXT NOT NULL DEFAULT 'pending',
                                                       -- pending | provisioning | ready | failed
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at               TIMESTAMPTZ
);
CREATE INDEX agents_tenant_id_idx ON agents(tenant_id);

CREATE TABLE jobs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants(id),
    agent_id     UUID REFERENCES agents(id),
    kind         TEXT NOT NULL,                        -- 'create_agent' in M1
    status       TEXT NOT NULL DEFAULT 'pending',
                                                       -- pending | running | complete | failed
    error        TEXT,
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_agent_id_idx ON jobs(agent_id);

CREATE TABLE job_events (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES jobs(id),
    event_type  TEXT NOT NULL,                         -- e.g. 'neon.project.creating'
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX job_events_job_id_created_at_idx ON job_events(job_id, created_at);
```

`job_events` exists both as the durable record (so an SSE client connecting late can be backfilled) and as a debugging audit trail. Redis pub/sub is the live channel; `job_events` is the log.

### 5.1 Tenant DB schema (v1)

Created by the per-tenant Alembic migration. Empty in M1; populated by M2+.

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type  TEXT NOT NULL,                        -- 'pdf' | 'url' | 'image' | 'csv'
    source_uri   TEXT NOT NULL,
    title        TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INT NOT NULL,
    content      TEXT NOT NULL,
    token_count  INT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX chunks_document_id_idx ON chunks(document_id);
CREATE INDEX chunks_content_tsv_idx ON chunks USING GIN (to_tsvector('english', content));

CREATE TABLE embeddings (
    chunk_id     UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model        TEXT NOT NULL,
    vector       VECTOR(1024) NOT NULL,                -- Voyage default
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX embeddings_vector_hnsw_idx ON embeddings
    USING hnsw (vector vector_cosine_ops);

CREATE TABLE chunk_metadata (
    chunk_id      UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    summary       TEXT,
    keywords      TEXT[],
    questions     TEXT[],
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id  TEXT,                                 -- end-user identifier from widget
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ
);

CREATE TABLE messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,                    -- 'user' | 'assistant' | 'system'
    content          TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_id_idx ON messages(conversation_id);

CREATE TABLE tool_calls (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id   UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_name    TEXT NOT NULL,
    arguments    JSONB NOT NULL,
    result       JSONB,
    latency_ms   INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT NOT NULL,                        -- 'scheduled' | 'pre_deployment' | 'manual'
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE eval_results (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_id   UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    scenario_id   TEXT NOT NULL,
    metric        TEXT NOT NULL,
    score         NUMERIC,
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE red_team_runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind           TEXT NOT NULL,                      -- 'pre_deployment' | 'weekly'
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    findings       JSONB,
    max_severity   TEXT
);
```

These tables exist empty in M1. They are defined now so the Alembic baseline is correct and M2+ migrations are additive, not foundational.

## 6. API surface

All routes require `X-API-Key` header. Auth is API-key-only in M1.

### 6.1 `POST /tenants`

Bootstraps a tenant (manual operation, not user-facing). Returns the new API key.

```json
{ "name": "Acme Coffee Roasters" }
```

Response `201`:

```json
{
  "id": "uuid",
  "name": "Acme Coffee Roasters",
  "api_key": "vrd_live_..."
}
```

### 6.2 `POST /agents`

```json
{
  "name": "Acme Support",
  "soul": {
    "voice": "warm, direct, plain language",
    "do": ["cite our policies", "offer human escalation"],
    "do_not": ["promise refunds", "make up product details"]
  },
  "role": "support"
}
```

Response `202`:

```json
{
  "agent_id": "uuid",
  "job_id": "uuid",
  "status": "pending",
  "events_url": "/jobs/{job_id}/events"
}
```

### 6.3 `GET /agents/{id}`

Returns the agent's current state including provisioning status.

### 6.4 `GET /jobs/{job_id}`

Returns the job row plus its event log (paginated).

### 6.5 `GET /jobs/{job_id}/events`

Server-Sent Events stream. On connect:

1. Replays all `job_events` rows for the job from the database (so a late-joining client sees full history).
2. Subscribes to Redis channel `job_events:{job_id}` and forwards each published event.
3. Closes the stream when a terminal event (`job.complete` or `job.failed`) is received.

Each event is emitted as:

```
event: neon.project.creating
data: {"job_id": "uuid", "at": "2026-05-12T..."}

```

### 6.6 `GET /health`

Returns `200` with `{"status": "ok", "redis": "ok", "db": "ok"}`. Used by docker-compose health checks and CI.

## 7. Celery tasks

### 7.1 `provision_neon(tenant_id, agent_id)`

1. Mark `agent.status = 'provisioning'`.
2. Emit `neon.project.creating`.
3. Call Neon API: `POST /projects` with name `vrd-{agent_id}` and the configured region.
4. Poll `GET /projects/{project_id}` every 2 seconds until status is `active` or timeout (90s) hits.
5. On success: encrypt the connection string with the platform's `NEON_ENCRYPTION_KEY` (Fernet), write `agent.neon_project_id` and `agent.neon_connection_string`.
6. Emit `neon.project.ready`.
7. Return `{"agent_id": ..., "project_id": ...}` for the next task in the chain.

Idempotency: if `agent.neon_project_id` is already set, skip provisioning and return early. This handles retry-after-partial-success.

Failure modes:

- Neon API 4xx → mark `job.status = 'failed'`, set `job.error`, emit `job.failed`, do not retry.
- Neon API 5xx or timeout → Celery retries with exponential backoff up to 3 times.
- Connection-string encryption failure → fatal, no retry, surface as `job.failed`.

### 7.2 `apply_migrations(result)`

1. Decrypt the connection string from the previous task's result.
2. Emit `migrations.running`.
3. Run Alembic upgrade head against the tenant DB using a programmatic Alembic config.
4. On success, write `agent.schema_version` with the final revision ID and `agent.status = 'ready'`.
5. Emit `migrations.complete` and `job.complete`.

Idempotency: Alembic is naturally idempotent — running `upgrade head` against an already-migrated DB is a no-op. No additional guard needed.

Failure modes:

- Connection failure → Celery retries with exponential backoff up to 3 times.
- Migration error → fatal, `job.failed`, the half-migrated DB is logged and flagged for manual inspection. (In M10 we add automated cleanup; M1 trusts the operator to clean up failed test tenants.)

### 7.3 Event-emission helper

Both tasks use a single helper:

```python
def emit(job_id: UUID, event_type: str, payload: dict | None = None) -> None:
    payload = payload or {}
    payload["at"] = datetime.now(timezone.utc).isoformat()
    # Persist
    db.execute(insert(job_events).values(job_id=job_id, event_type=event_type, payload=payload))
    db.commit()
    # Publish
    redis.publish(f"job_events:{job_id}", json.dumps({"event_type": event_type, "payload": payload}))
```

Every task emits at start, at each meaningful checkpoint, and at completion or failure. The vocabulary is dot-separated and forward-extensible: `neon.project.*`, `migrations.*`, future `parsing.*`, `chunking.*`, `embedding.*`, etc.

## 8. Local development

### 8.1 docker-compose

Services:

- `postgres` — control DB (used in tests; production runs against Neon).
- `redis` — broker and pub/sub.
- `api` — FastAPI via `uvicorn --reload`.
- `worker_pipeline` — Celery worker on the `pipeline` queue.
- `worker_runtime` — Celery worker on the `runtime` queue (idle in M1, present so the topology is correct).
- `beat` — Celery beat (idle in M1).

A `.env.example` documents the required variables: `NEON_API_KEY`, `NEON_REGION`, `NEON_ENCRYPTION_KEY`, `CONTROL_DB_URL`, `REDIS_URL`, `LOG_LEVEL`.

### 8.2 Demo script

`scripts/demo_m1.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

API="http://localhost:8000"

# Bootstrap a tenant
TENANT_RESP=$(curl -s -X POST $API/tenants \
    -H "X-Admin-Key: $ADMIN_KEY" \
    -d '{"name": "Demo Tenant"}')
API_KEY=$(echo $TENANT_RESP | jq -r .api_key)

# Create an agent
AGENT_RESP=$(curl -s -X POST $API/agents \
    -H "X-API-Key: $API_KEY" \
    -d @scripts/fixtures/demo_agent.json)
JOB_ID=$(echo $AGENT_RESP | jq -r .job_id)
AGENT_ID=$(echo $AGENT_RESP | jq -r .agent_id)

echo "Streaming job events for $JOB_ID..."
curl -N -H "X-API-Key: $API_KEY" $API/jobs/$JOB_ID/events

# Final state
curl -s -H "X-API-Key: $API_KEY" $API/agents/$AGENT_ID | jq
```

Demo passes when the SSE stream emits all six required events and the final `GET /agents/{id}` shows `status: ready` with `neon_project_id` and `schema_version` set.

## 9. Testing strategy

### 9.1 Unit tests

- Pydantic schema validation for `POST /agents` payloads (valid, missing fields, invalid role values).
- Connection-string encryption round-trip.
- Event-emission helper writes to both DB and Redis.
- Task idempotency: calling `provision_neon` twice with the same agent_id results in one Neon project, not two.

### 9.2 Integration tests

- Full chain run against a mocked Neon API (use `respx` or similar) and a real local Postgres for the tenant DB. Asserts: tenant DB has the v1 schema after the chain completes, `agent.status = 'ready'`, all expected events were emitted in order.
- Worker-kill test: start the chain, kill the worker between `neon.project.ready` and `migrations.complete`, restart the worker, assert the task completes successfully and the agent ends in `ready` state.
- SSE late-join test: start the chain, wait until the chain is half-done, open an SSE connection, assert the client receives all events emitted before connection plus all events emitted after.

### 9.3 Nightly CI integration test

A single end-to-end test against a real Neon test account, scheduled nightly. Creates an agent, asserts the Neon project exists, asserts the migrations applied, deletes the Neon project at teardown. This is the test that proves the platform works against the real cloud, not just mocks.

## 10. Observability (M1 baseline)

Full observability lands in M10. M1 ships only the minimum needed for debugging:

- Structured JSON logs from FastAPI and Celery (via `structlog`).
- A request ID propagated from FastAPI → Celery via task headers, included in every log line.
- Sentry SDK initialised, but only configured if `SENTRY_DSN` is set.
- No Langfuse yet — that lands in M5 when there are actual LLM calls to trace.

## 11. Security

- API keys hashed in the DB (argon2), never stored or logged in plaintext.
- Connection strings encrypted at rest with Fernet keyed from `NEON_ENCRYPTION_KEY` (32 random bytes, stored in the platform's secret manager, not in source).
- `NEON_API_KEY` is platform-scoped, not exposed to tenants.
- All API responses set `Cache-Control: no-store`.
- CORS locked down to known origins (the future admin UI); the widget origin allowance lands in M4.

## 12. Risks specific to M1

**Neon API rate limits hit during testing.**
Mitigation: the nightly integration test deletes its project at teardown; local development uses a small dedicated Neon org with a project quota the operator monitors.

**Connection-string encryption key rotation is not addressed in M1.**
Accepted. This becomes a real concern at M10 when production tenants exist. For M1, key rotation is documented as a known gap.

**Per-tenant Alembic state drift.**
If a tenant's migrations advance past the platform's current head (e.g., an old tenant on a new code version), behaviour is undefined. M1 mitigates by always running `upgrade head`, which is forward-only. Backward migration is explicitly unsupported and will be revisited at M9 or M10 if the issue arises.

**The dev experience of running ten services in docker-compose is heavy.**
Mitigation: a `make dev-light` target runs only `postgres`, `redis`, and `api`, with the worker run on the host for faster iteration on task code.

## 13. Deliverables checklist

- [ ] FastAPI app with the routes listed in §6, OpenAPI docs at `/docs`.
- [ ] Alembic migration set for the control DB.
- [ ] Alembic migration set for the v1 tenant schema.
- [ ] `provision_neon` and `apply_migrations` Celery tasks with full test coverage.
- [ ] SSE endpoint with replay-then-subscribe behaviour.
- [ ] docker-compose with all six services.
- [ ] `.env.example` and a `README.md` section on local setup.
- [ ] `scripts/demo_m1.sh` running clean.
- [ ] GitHub Actions: lint (ruff), type-check (mypy or pyright), unit + integration tests on every PR; nightly E2E against Neon test account.
- [ ] Recorded demo (asciinema or video) embedded in the repo README.
- [ ] M1 retrospective: a short `docs/M1-retro.md` capturing what changed during build, what surprised you, and what to do differently in M2.

## 14. What M1 unlocks

When M1 ships, every subsequent milestone has:

- A place to write its work (tenant DBs that exist and have the right schema).
- A way to dispatch work (Celery chains with proven status reporting).
- A way to show the user what's happening (SSE pattern that any future task can plug into by emitting the right events).
- A test pattern (mocked-Neon integration + nightly-real E2E) that subsequent milestones extend rather than reinvent.

M2 — ingestion — is then a strict extension: add new tasks to the chain, emit new event types, write to tables that already exist. The architectural contract is set in M1 and held forever after.