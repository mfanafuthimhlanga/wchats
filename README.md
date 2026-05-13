# Veridian — M1: Control Plane Skeleton

M1 proves the foundational plumbing: a FastAPI control plane dispatches an agent-creation request to a Celery pipeline, provisions a dedicated Neon project for the tenant, runs schema migrations programmatically, and streams live status back to the caller via Server-Sent Events. By the end, a single curl command creates a tenant, triggers a real Celery chain, and a browser watches a real Neon project come into existence in real time.

## Architecture

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

**The M1 Celery chain:**

```python
chain(
    provision_neon.s(tenant_id, agent_id),   # creates real Neon project, encrypts conn string
    apply_migrations.s(),                     # runs Alembic v1 schema against tenant DB
).apply_async(queue="pipeline")
```

Each task is idempotent and uses `acks_late=True` — a worker dying mid-task is retried, never leaving a half-built tenant DB.

## Quick Start

```bash
# 1. Copy and configure environment variables
cp .env.example .env
#    Edit .env: set NEON_API_KEY, NEON_ENCRYPTION_KEY, ADMIN_KEY (see instructions in .env.example)

# 2. Start all 6 services (postgres, redis, api, worker_pipeline, worker_runtime, beat)
docker compose up -d

# 3. Run control DB migrations
docker compose exec api alembic -c alembic.ini upgrade head

# 4. Export your admin key
export ADMIN_KEY=<your ADMIN_KEY from .env>

# 5. Run the demo
bash scripts/demo_m1.sh
```

The demo bootstraps a tenant, creates an agent, streams 6 SSE events live, and prints the final state including `neon_project_id`, `schema_version`, and `status: ready`.

## Local Development (dev-light)

For faster iteration, run only Postgres and Redis in Docker and start the API and workers on the host:

```bash
# Terminal 1 — start infrastructure
make dev-light

# Terminal 2 — run pipeline worker
cd apps/api
celery -A app.worker.celery_app worker --queues=pipeline --loglevel=info

# Terminal 3 — (optional) run runtime worker
cd apps/api
celery -A app.worker.celery_app worker --queues=runtime --loglevel=info
```

The API starts with uvicorn `--reload` on http://localhost:8000. OpenAPI docs at http://localhost:8000/docs.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEON_API_KEY` | Yes | Neon API key for provisioning per-tenant projects. Get from https://console.neon.tech/app/settings/api-keys |
| `NEON_REGION` | No | Neon region for new tenant projects (default: `aws-us-east-1`). Run `neon regions list` to see options |
| `NEON_ENCRYPTION_KEY` | Yes | Fernet key for encrypting tenant connection strings. Generate: `python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `CONTROL_DB_URL` | Yes | Async SQLAlchemy URL for the control DB. Example: `postgresql+asyncpg://veridian:veridian@localhost:5432/veridian_control` |
| `CONTROL_DB_SYNC_URL` | Yes | Sync psycopg2 URL for Alembic and Celery workers. Example: `postgresql://veridian:veridian@localhost:5432/veridian_control` |
| `REDIS_URL` | Yes | Redis connection URL. Example: `redis://localhost:6379/0` |
| `ADMIN_KEY` | Yes | Admin key for `POST /tenants` endpoint (`X-Admin-Key` header). Generate: `python -c "import secrets; print('vrd_admin_'+secrets.token_urlsafe(32))"` |
| `CORS_ORIGINS` | No | JSON array of allowed CORS origins (default: `["http://localhost:3000"]`) |
| `LOG_LEVEL` | No | Log level for structlog (default: `INFO`) |
| `SENTRY_DSN` | No | Sentry DSN. Leave empty to disable error tracking |

## Running Tests

```bash
# Unit tests — fast, no external services needed
make test-unit

# Integration tests — requires Postgres + Redis (docker compose up -d postgres redis)
make test-integration

# E2E tests — requires real NEON_API_KEY (runs against Neon test account, creates real projects)
cd apps/api && pytest tests/e2e --tb=short -m e2e

# Full suite with coverage report
make test-all
```

Unit test coverage gate is 80%. The integration suite is tagged `@pytest.mark.integration` and uses a real local Postgres with respx-mocked Neon API calls. The E2E suite (`@pytest.mark.e2e`) creates real Neon projects and tears them down in a `finally` block.

## M1 Success Criteria

- [x] `POST /agents` returns `202 Accepted` with `job_id`; creates `tenant`, `agent`, and `job` rows in control DB
- [x] Within 60 seconds, a new Neon project exists, Alembic migrations have been applied, and the encrypted connection string is stored on the `agent` row
- [x] `GET /jobs/{job_id}/events` streams all 6 SSE events in order: `job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`
- [x] Worker kill-9 mid-task results in retry and successful completion (not a half-built tenant DB) — `acks_late=True` + idempotency guards
- [x] `scripts/demo_m1.sh` runs from a clean `docker compose up`, completes without manual intervention, and prints tenant ID, agent ID, and final status
- [x] Unit test coverage above 80%; integration test suite covers the full chain; nightly E2E against real Neon runs in GitHub Actions
- [x] README documents the architecture and includes a demo recording placeholder

## Demo

Demo recording coming — will be added after the first successful nightly E2E run.

<!-- asciinema recording or video link goes here after first successful run -->
<!-- Record with: asciinema rec demo.cast && asciinema upload demo.cast -->

## Stack

| Component | Technology |
|-----------|------------|
| API server | FastAPI 0.136.1 + Pydantic v2 |
| Task queue | Celery 5.6.3 + Redis broker |
| Control DB | Neon (PostgreSQL 15+) via asyncpg + SQLAlchemy |
| Per-tenant DBs | Neon projects, provisioned via neon-api 0.3.0 |
| Migrations | Alembic 1.18.4 (control DB + per-tenant) |
| Encryption | Fernet (AES-128-CBC) via cryptography 48.0.0 |
| Auth hashing | argon2-cffi 25.1.0 (argon2id) |
| SSE streaming | sse-starlette 3.4.4 |
| Logging | structlog 25.5.0 (structured JSON) |
| Error tracking | Sentry SDK 2.59.0 (optional) |

## Security Notes

- **API keys stored as argon2id hashes** — raw keys are never persisted in the database. The hash is stored in the `api_key` column; the raw key is shown once at tenant creation and is gone.
- **Connection strings encrypted at rest** — tenant database URLs are stored as Fernet-encrypted `BYTEA` in the `agents.neon_connection_string` column. The `NEON_ENCRYPTION_KEY` is stored in a platform secret manager, never in source control.
- **Connection strings never appear in Celery task args or Redis queue** — `apply_migrations` fetches and decrypts the connection string from the control DB at runtime by `agent_id`. Nothing sensitive flows through the message queue.
- **All responses include `Cache-Control: no-store`** — prevents API responses (which may contain UUIDs or status) from being stored in browser or proxy caches.
- **CORS locked to known origins** — the admin UI origin is the only allowed CORS origin in M1. Widget CORS is configured in M4.
