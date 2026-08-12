# TRACE — E2E-1 · signup → agent (2026-08-12)

**Plan:** `.dev/plans/260812-e2e1-signup-to-agent.md`. **Result: 12/12 assertions passed.**
**This is the first time the signup → agent path has ever been run.**

## The artefacts this run created — record them, they are needed and they cost money

| | |
|---|---|
| **Neon project id** | **`mute-dream-53534177`** — name `e2e1-probe-agent-e2e1-pro-87783405`, region `aws-us-east-1`, created `2026-08-12T21:53:54Z` |
| tenant_id | `32b3715c-36c6-46b3-859c-d6ec2e20c464` (local control DB) |
| agent_id | `c14d13a1-7401-4ff2-b563-c9c0a72e3fcb` |
| job_id | `a3b11977-fb21-4607-97da-cde556d36d80` |

**The project is deliberately NOT torn down** — E2E-2/3/4 need an ingested, queryable tenant DB.
**Delete by id only, never by name pattern** (the `nightly.yml` lesson). It is the only project in the
Neon account.

## What ran

`POST /api/v1/tenants` (201) → `POST /api/v1/agents` (202) → `provision_neon` →
`apply_migrations` (called synchronously inside `provision_neon`, per its 2026-05 Windows note).
Wall clock: agent created `23:53:51`, job complete `23:54:37` — **46 seconds**, of which ~3s was the
Neon API call and ~30s the 16-revision tenant chain.

Processes, all under a run-scoped overlay: `uvicorn` on `127.0.0.1:8000`, `celery ... -Q pipeline -P solo`.

## Assertions, observed

```
[PASS] agent.status == ready
[PASS] job.status == complete
[PASS] agents.neon_project_id is set                 mute-dream-53534177
[PASS] pooled conn string is NOT plaintext           292 bytes, first 12 = b'gAAAAABqfOt2'
[PASS] pooled conn string decrypts to a postgres URI host=ep-icy-sound-aufxtfgg-pooler.c-10.us-east-1.aws.neon.tech
[PASS] direct conn string is NOT plaintext           268 bytes
[PASS] direct ciphertext differs from pooled ciphertext
[PASS] direct decrypts and is NOT the pooled URI     direct host=ep-icy-sound-aufxtfgg.c-10.us-east-1.aws.neon.tech
[PASS] GET /projects/mute-dream-53534177 -> 200
[PASS] tenant alembic_version == 0016                24 tables, embeddings_vector_hnsw_idx present
[PASS] agents.schema_version == 0016
[PASS] all 6 lifecycle events emitted
```

Event sequence, in order: `job.started → neon.project.creating → neon.project.ready →
migrations.running → migrations.complete → job.complete`.

**Both halves of "encrypted at rest" were checked deliberately.** Ciphertext that never decrypts is as
broken as plaintext, so the test asserts the stored bytes are *not* a `postgres…` prefix **and** that
`fernet_decrypt` round-trips them to a real URI. The pooled/direct hosts differing is the RESEARCH.md
Pitfall 1 contract holding in practice — Alembic really did run on the non-pooled endpoint.

## The findings, and they are the return on the step

### 1. The app does not boot on this machine — `PLATFORM_CREDENTIAL_KEY` is missing

The **first** command of E2E-1 failed:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
PLATFORM_CREDENTIAL_KEY
  Field required [type=missing]
```

**Both** real env files lack it — `apps/api/.env` (17 keys) and the repo-root `.env` (17 keys).
E2E-0 fixed the *examples*; this is the actual environment, and E2E-0's closing note ("nothing has
booted the app") was correct within the hour.

Filed as **`1.22 · env-missing-platform-key`** (owner: it is HKDF master key material).
`hide_input_in_errors=True` held — no value leaked into the error.

### 2. The unit suite structurally cannot detect that — retro Family I again

`tests/conftest.py:33-65` sets **all 10** required vars with `os.environ.setdefault` at module scope,
including a freshly generated `PLATFORM_CREDENTIAL_KEY`. 2206 tests therefore run against a synthetic
environment that manufactures the exact key the real one is missing. The fixture is a claim about the
environment and nothing was required to evidence it. Filed as **`1.23 · conftest-masks-boot-contract`**.

### 3. `.env` points at PRODUCTION for both the control DB and Redis

```
CONTROL_DB_URL / CONTROL_DB_SYNC_URL -> ep-falling-glade-ac3zhiqu-pooler.sa-east-1.aws.neon.tech/neondb
REDIS_URL                            -> singular-ocelot-125167.upstash.io
```

CLAUDE.md says this; it is now **observed** rather than quoted. Every E2E process therefore ran under
an explicit overlay pointing at `localhost`, **with a pre-flight assertion that aborts if the resolved
host is not local**. Without that, E2E-1 would have written a test tenant into the production control
DB and published job events to production Redis.

### 4. `1.20 · clerk-dev-keys` confirmed from a second, independent angle

The API's own startup JWKS probe resolved
`https://bright-puma-63.clerk.accounts.dev/.well-known/jwks.json` — an `accounts.dev` host is by
definition a Clerk **development** instance. `1.20` was previously observed only in the e2e browser
console; the backend is on the same dev instance.

## Decisions

- **`PLATFORM_CREDENTIAL_KEY` was generated per-run and NOT written into `.env`.** It is the HKDF
  master key for per-tenant credential derivation (INT-01); choosing the owner's permanent key is not
  an agent's call, and a key silently written into `.env` becomes the key every future run derives
  from. The overlay lives in the session scratchpad only.
- **Real `NEON_API_KEY` and `NEON_ENCRYPTION_KEY` were used**, because a real project and the real
  Fernet path are the entire point of the step. Only the control DB and Redis were redirected.
- **E2E-1 is an observation, not a new pytest module**, per the plan. What it found that a test
  *should* have caught is filed as `1.23` rather than bolted on here.

## What this does NOT establish

- **No document has been ingested and no chunk or embedding exists.** The tenant DB is at `0016` with
  24 empty tables. That is E2E-2, and it is the first step that needs the `docling` `pipeline` extra
  (`1.10`, `4.4`).
- **No customer turn has run.** The agent has a soul and no corpus.
- **Nothing was proven about a *fresh clone*.** This machine has a populated `.env` minus one key; a
  genuinely empty environment has still never been stood up.
- **The 46-second provisioning time is one observation, not a baseline.** Do not tune anything on it.
