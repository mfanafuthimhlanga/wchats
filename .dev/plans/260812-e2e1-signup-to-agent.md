# E2E-1 · signup → agent

**Plan step:** `PRODUCTION-READINESS.md` §4, Phase A, E2E-1. **Follows:** E2E-0 (`2482283`).
**Goal:** `POST /tenants` → `POST /agents` → a **real Neon project provisioned**, its connection
string **encrypted at rest**, and the tenant migration chain at head **`0016`** on that new project.

## Established before writing this plan (all OBSERVED this session)

1. **The app does not boot on this machine.** `Settings()` raises `ValidationError`:
   `PLATFORM_CREDENTIAL_KEY  Field required`. **Both** real env files lack it —
   `apps/api/.env` (17 keys) and the repo-root `.env` (17 keys). E2E-0 fixed the *examples*; this is
   the *actual* environment, and it is the first thing E2E-1 touched.
2. **The unit suite could never have caught that.** `tests/conftest.py:33-65` sets all 10 required
   vars with `os.environ.setdefault` at module scope, including a freshly generated
   `PLATFORM_CREDENTIAL_KEY`. 2206 tests run against a synthetic environment that manufactures the
   key the real one is missing. Retro **Family I** exactly: a fixture is a claim about a boundary,
   and nothing required anyone to evidence it.
3. **`.env` points at PRODUCTION, both of them:**
   - `CONTROL_DB_URL` / `CONTROL_DB_SYNC_URL` → `ep-falling-glade-ac3zhiqu-pooler.sa-east-1.aws.neon.tech/neondb`
   - `REDIS_URL` → `singular-ocelot-125167.upstash.io`
   This is the CLAUDE.md warning, confirmed by observation rather than quoted.
4. **Local infrastructure is up**: PostgreSQL `5432` OPEN, control DB at **`0019` (head)**, 19 tables,
   with leftover rows from prior integration runs (14 tenants / 10 agents / 10 jobs). Redis `6379`
   OPEN.
5. `NEON_API_KEY` present (69 chars), `ADMIN_KEY` present (53 chars).

## Approach

**Every process gets an explicit env overlay. Nothing runs against ambient `.env`.**

```
CONTROL_DB_URL      = postgresql+asyncpg://wchats:wchats@localhost:5432/wchats_control
CONTROL_DB_SYNC_URL = postgresql://wchats:wchats@localhost:5432/wchats_control
REDIS_URL           = redis://localhost:6379/0
PLATFORM_CREDENTIAL_KEY = <generated per run, NOT written to .env>
```

Everything else inherits: `NEON_API_KEY` and `NEON_ENCRYPTION_KEY` stay **real**, because a real
Neon project and the real Fernet path are the whole point of the step.

**The overlay is asserted, not assumed.** Each process prints the control-DB host it resolved before
doing any work; a host that is not `localhost` aborts the run. A test tenant in the production
control DB is not undoable by an apology.

**`PLATFORM_CREDENTIAL_KEY` is run-scoped and is NOT written into `.env`.** It is HKDF master key
material for per-tenant credential derivation (INT-01). Generating one for a disposable local
control DB is safe; choosing the owner's permanent key is not an agent's call, and writing one into
their `.env` would silently become the key every future run derives from.

## Steps

1. Start `uvicorn app.main:app --port 8000` under the overlay. Confirm `/health`.
2. Start `celery -A app.worker.celery_app worker -Q pipeline -P solo` under the overlay.
   `-P solo` is required on Windows — `provision.py:307-322` documents why the async dispatch path
   breaks here.
3. `POST /api/v1/tenants` with `X-Admin-Key` → capture `tenant_id` + the plaintext `api_key`
   (returned once, by design).
4. `POST /api/v1/agents` with `X-API-Key` → capture `agent_id`, `job_id`. Route returns 202 and does
   no work inline.
5. Follow the job to terminal state (`GET /api/v1/jobs/{job_id}/events`, else poll the row).
6. Assertions, each against the control DB / Neon directly, not against the API's own account:
   - `agents.neon_project_id` is set, and the project **exists in the Neon API** by that id.
   - `agents.neon_connection_string` is **BYTEA that is not the plaintext URI**, and
     `fernet_decrypt` round-trips it to a `postgresql://` URI. Both halves matter: ciphertext that
     never decrypts is as broken as plaintext.
   - `agents.neon_direct_connection_string` likewise, and **distinct** from the pooled one.
   - The **tenant chain is at `0016`** on the new project, read from its own `alembic_version`.
   - `agents.status == 'ready'`, `jobs.status == 'complete'`, and the event sequence
     `job.started → neon.project.creating → neon.project.ready → migrations.running →
     migrations.complete → job.complete` is present in `job_events`.

## Risks

- **Writing to production.** Highest risk in the whole step. Mitigated by the overlay + the
  pre-flight host assertion above.
- **A real Neon project is created and left running.** That is intended — E2E-2/3/4 need an
  ingested, queryable tenant DB, so it must NOT be torn down at the end of E2E-1. **Record the
  project id**, and delete by **id** only, never by name pattern (the `nightly.yml` lesson).
- **A partial failure leaves a Neon project with no agent row pointing at it** — the classic orphan.
  The idempotency guard's state B covers a retry, but an abandoned run needs the id from the log.
- **4 GB RAM**: uvicorn + celery + the unit venv at once. One thing at a time; do not run any suite
  concurrently.
- `_project_slug` uses `tenant.clerk_user_id` when present, else the tenant name. The test tenant has
  no Clerk user, so the slug comes from the name — pick a name that is obviously a test artifact.

## Tests

E2E-1 is an **observation**, not a new pytest module: the assertions above are run against the live
result and recorded verbatim in the trace. Anything it finds that *should* have been caught by a
test gets a test, filed as its own backlog row.

## Definition of done

The six assertions in step 6, observed and recorded with their actual output, plus the Neon project
id written into the trace and HANDOFF so the next step can use it and the owner can delete it.
