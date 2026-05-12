# Architecture Research

**Domain:** Production Multi-Tenant RAG Platform
**Researched:** 2026-05-12
**Confidence:** HIGH (all seven questions answered from official docs + verified primary sources)

---

## Standard Architecture

### System Overview

```
                         ┌─────────────────────────────────────────────┐
                         │              CONTROL PLANE (L1)              │
                         │  FastAPI · stateless · Pydantic boundaries   │
                         │  /auth  /agents  /jobs  /widget/:id/config   │
                         │           SSE  /jobs/:id/stream              │
                         └──────────┬────────────────────┬─────────────┘
                                    │ dispatch            │ SSE stream
                         ┌──────────▼──────────┐         │
                         │  ORCHESTRATION (L2)  │         │
                         │  Celery + Redis      │ ────────┘
                         │  pipeline queue      │  pub/sub channel
                         │  runtime  queue      │  per job_id
                         └──────────┬───────────┘
                                    │ task chain
          ┌─────────────────────────▼──────────────────────────────────┐
          │                   DATA PLANE (L3)                          │
          │   Control DB (Neon, shared)       Tenant DB (Neon, 1:1)    │
          │   tenants / agents / jobs         documents / chunks       │
          │   tenant_credentials (encrypted)  embeddings (pgvector)    │
          │                                   conversations / messages  │
          └─────────────────────────┬──────────────────────────────────┘
                                    │ tenant connection string
          ┌─────────────────────────▼──────────────────────────────────┐
          │               RETRIEVAL ENGINE (L4)                        │
          │   pgvector HNSW  +  BM25 (tsvector)  →  RRF (SQL CTE)     │
          │                       ↓ Voyage Rerank                      │
          │         top_k chunks + metadata + retrieval trace          │
          └─────────────────────────┬──────────────────────────────────┘
                                    │ chunks + context
          ┌─────────────────────────▼──────────────────────────────────┐
          │               REASONING ENGINE (L5)                        │
          │   Claude Agent SDK  ·  query(prompt, options)              │
          │   system_prompt = agent soul (per-tenant, injected)        │
          │   tools: retrieve / lookup_structured / escalate / clarify │
          └─────────────────────────┬──────────────────────────────────┘
                                    │ raw response
          ┌─────────────────────────▼──────────────────────────────────┐
          │              VALIDATION CHAIN (L6)                         │
          │   Gatekeeper → Auditor → Strategist                        │
          │   (async after stream, Claude API Haiku, structured output) │
          └──────┬──────────────────┬──────────────────────────────────┘
                 │ logs             │ signals
          ┌──────▼──────┐   ┌──────▼──────────────────────────────────┐
          │  Langfuse   │   │         EVAL + RED TEAM (L7/L8)          │
          │  traces     │   │  Ragas harness · Celery beat schedule    │
          │  cost/lat   │   │  3 red team agents · severity classify   │
          └─────────────┘   └──────┬──────────────────────────────────┘
                                   │ results
          ┌────────────────────────▼──────────────────────────────────┐
          │         PRE-DEPLOYMENT CHECKLIST (L9)                     │
          │  Orchestrator agent (Sonnet) reads all signals            │
          │  Writes structured report: ship / ship_with_warnings / block│
          │  Human validation gate → owner approves                   │
          └────────────────────────┬──────────────────────────────────┘
                                   │ on approval
          ┌────────────────────────▼──────────────────────────────────┐
          │              WIDGET DELIVERY (L10)                        │
          │  Preact iframe · CDN bundle < 20kb gzipped                │
          │  GET /widget/:agent_id/config → JWT + theming             │
          │  Chat traffic: widget → FastAPI → runtime queue → L5      │
          └───────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|---------------|----------------------|
| Control Plane (L1) | Auth, tenant CRUD, job dispatch, SSE, widget config | FastAPI + Pydantic, modular routers, no inline work |
| Orchestration Plane (L2) | All long-running work, task chains, queue routing | Celery 5.x, Redis broker+backend, `acks_late=True` |
| Data Plane — Control DB (L3) | Tenants, agents, jobs, billing, encrypted conn strings | Neon shared project, Alembic migrations |
| Data Plane — Tenant DB (L3) | Per-tenant vectors, conversations, eval runs | One Neon project per tenant, pgvector + tsvector |
| Retrieval Engine (L4) | Deterministic hybrid search, RRF fusion, rerank | Pure SQL + pgvector HNSW, Voyage Rerank |
| Reasoning Engine (L5) | Customer service agent, tool-calling loop | `claude_agent_sdk.query()`, system_prompt injected per call |
| Validation Chain (L6) | Gatekeeper, Auditor, Strategist on every response | Claude API direct (Haiku), Pydantic structured output |
| Eval System (L7) | Scheduled Ragas metrics, scenario mining | Celery beat, custom harness, LLM judges |
| Red Team (L8) | Adversarial probing pre-deploy + weekly | Claude Agent SDK, 3 specialized agents, PyRIT scaffolding |
| Pre-Deploy Checklist (L9) | Aggregate all signals, write recommendation | Claude Agent SDK (Sonnet), structured report |
| Widget Delivery (L10) | Serve iframe to any website, chat traffic routing | Preact, CDN, short-lived JWT, runtime Celery queue |

---

## Recommended Project Structure

```
veridian/
├── apps/
│   ├── api/                          # FastAPI application
│   │   ├── alembic/                  # migrations (control DB schema)
│   │   │   └── versions/
│   │   ├── app/
│   │   │   ├── main.py               # FastAPI app factory
│   │   │   ├── core/
│   │   │   │   ├── config.py         # pydantic-settings, env vars
│   │   │   │   ├── security.py       # JWT, password hashing
│   │   │   │   └── database.py       # SQLAlchemy engine, session factory
│   │   │   ├── api/
│   │   │   │   ├── v1/
│   │   │   │   │   ├── auth.py
│   │   │   │   │   ├── agents.py
│   │   │   │   │   ├── jobs.py       # SSE endpoint lives here
│   │   │   │   │   ├── ingest.py
│   │   │   │   │   └── widget.py
│   │   │   ├── models/               # SQLAlchemy ORM models (control DB)
│   │   │   │   ├── tenant.py
│   │   │   │   ├── agent.py
│   │   │   │   ├── job.py
│   │   │   │   └── credential.py     # encrypted conn string storage
│   │   │   ├── schemas/              # Pydantic request/response models
│   │   │   ├── services/
│   │   │   │   ├── neon.py           # Neon API client, project provisioning
│   │   │   │   ├── credential.py     # Fernet encrypt/decrypt
│   │   │   │   └── sse.py            # Redis pub/sub → SSE generator
│   │   │   └── worker/               # Celery application
│   │   │       ├── celery_app.py     # Celery factory, queue config
│   │   │       ├── tasks/
│   │   │       │   ├── pipeline/     # ingestion chain tasks
│   │   │       │   │   ├── provision.py
│   │   │       │   │   ├── parse.py
│   │   │       │   │   ├── chunk.py
│   │   │       │   │   ├── metadata.py
│   │   │       │   │   ├── embed.py
│   │   │       │   │   └── strategy.py
│   │   │       │   └── runtime/      # eval, red team, maintenance
│   │   │       │       ├── eval.py
│   │   │       │       ├── red_team.py
│   │   │       │       └── checklist.py
│   │   │       └── chains.py         # compose Celery chains here
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   │
│   └── web/                          # Next.js admin UI
│       ├── app/                      # App Router
│       │   ├── (auth)/               # login, signup
│       │   ├── (dashboard)/          # agent list, create
│       │   ├── agents/[id]/
│       │   │   ├── ingest/           # upload + SSE progress stream
│       │   │   ├── eval/             # eval dashboard
│       │   │   ├── redteam/          # red team reports
│       │   │   └── deploy/           # checklist + validation gate
│       ├── components/
│       ├── lib/
│       │   └── api-client.ts         # generated from FastAPI OpenAPI spec
│       └── package.json
│
├── packages/
│   └── widget/                       # Preact iframe widget
│       ├── src/
│       │   ├── Widget.tsx
│       │   ├── Chat.tsx
│       │   └── index.ts
│       ├── dist/                     # built artefact, served from CDN
│       └── package.json
│
├── scripts/
│   ├── seed_control_db.py
│   └── migrate_tenant.py             # run Alembic against a specific conn string
│
├── docker-compose.yml                # api + worker + redis (dev)
├── docker-compose.prod.yml
└── .env.example
```

**Rationale for this structure:**
- `apps/api` collocates FastAPI + Celery because they share models, schemas, and service classes. Splitting them into separate repos creates import coordination pain for a solo developer.
- Two Celery task subdirectories (`pipeline/`, `runtime/`) mirror the two queue names; the file path signals which queue a task belongs to.
- `services/` holds stateless helper classes (Neon API client, Fernet credential service, SSE generator) that both the API routers and Celery tasks can import.
- `packages/widget/` is isolated because it has a different build pipeline (esbuild/Vite targeting <20kb) and ships to a CDN separately from the API.
- `apps/web` is a pure consumer of the FastAPI spec. Generate TypeScript types with `hey-api` from the OpenAPI spec to maintain type safety across the boundary without a shared codebase dependency.

---

## Architectural Patterns

### Pattern 1: One Neon Project Per Tenant (Confirmed Production Pattern)

**Verdict:** Use it. Neon explicitly endorses and documents this pattern; some users manage hundreds of thousands of projects with one engineer.

**Why it beats schema-per-tenant for this project:**

| Concern | Per-Project | Schema-Per-Tenant |
|---------|------------|-------------------|
| Data isolation | Database-level — impossible to cross | Application-level — one missing filter leaks |
| Eval branching | Native Neon branch per tenant DB | Branching a schema inside shared DB is awkward |
| pgvector index per tenant | Own HNSW index, tuned per tenant | Shared table, all tenants' vectors mixed |
| PITR | Per-tenant point-in-time recovery | Affects all tenants |
| Cost at zero usage | Scales to zero independently | Shared compute, harder to zero |
| Operational complexity | Linear with tenant count, API-automated | Complex RLS + schema routing as scale grows |

**The schema-per-tenant path closes off Neon branching for eval isolation** — that is a core differentiator in the PRD. Do not compromise it.

**Solo developer viability:** HIGH. Neon's API handles all provisioning. No infra management. The provisioning Celery task is ~30 lines.

### Pattern 2: Celery Chains for the Ingestion Pipeline

**Verdict:** Celery chains are the right abstraction for this project. Prefect and Temporal are over-engineering for a solo developer at this stage.

| Criterion | Celery Chains | Temporal | Prefect |
|-----------|--------------|---------|---------|
| Infrastructure overhead | Redis (already needed) | Temporal Server + persistence store | Prefect server or cloud |
| Learning curve | Low (Python-native) | High (new mental model) | Medium |
| Visibility tooling | Flower (add-on) | Built-in UI | Built-in UI |
| Durability | `acks_late` + idempotent tasks covers 90% | Durable execution built-in | Better than Celery |
| Solo dev viability | Excellent | Poor (weeks of infra setup) | Good |
| RAG pipeline fit | Chains map 1:1 to the task sequence | Overkill for 10-step linear chains | Reasonable |

**The key Celery configurations that matter:**
- `acks_late = True` — worker crash does not lose the task; it gets redelivered
- `task_acks_on_failure_or_timeout = False` — failed tasks stay in queue for retry
- `task_reject_on_worker_lost = True` — explicit rejection on worker loss
- Each task writes its status to the jobs table in the control DB *before and after* execution so SSE can replay history on reconnect

### Pattern 3: SSE via Redis Pub/Sub (Push Model)

The production pattern for Celery job status streaming:

```
Celery task → redis.publish(f"job:{job_id}", json.dumps(event))
FastAPI SSE  → redis.subscribe(f"job:{job_id}") → yield to client
```

**Why pub/sub over polling:**
- Polling Celery's result backend adds 500ms+ per poll interval to perceived latency
- Pub/sub is push: the SSE client sees the event as fast as the task publishes it
- The SSE endpoint uses `sse-starlette` (production-grade, W3C compliant, handles client disconnect)

**Critical production gotcha:** Nginx buffers SSE by default (holds until ~16KB accumulates). Add `X-Accel-Buffering: no` to every SSE response header. Without this, the onboarding progress stream appears frozen.

**Reconnect pattern:** On SSE reconnect, the endpoint replays all events for the job from the control DB's `job_events` table using the `Last-Event-ID` header. This means job events must be persisted (not only published to Redis) so a browser refresh mid-onboarding does not lose state.

### Pattern 4: Per-Tenant Credential Management (Fernet + Control DB)

```
On provisioning:
  1. POST to Neon API → receive connection_uri
  2. Fernet.encrypt(connection_uri, key=CREDENTIAL_ENCRYPTION_KEY)
  3. Store encrypted bytes in control DB: credentials(tenant_id, encrypted_conn_string)
  4. CREDENTIAL_ENCRYPTION_KEY lives in environment (never in DB)

At task execution time:
  1. Celery task receives tenant_id (not connection string) as argument
  2. Task fetches + decrypts: get_tenant_conn_string(tenant_id)
  3. Opens asyncpg connection directly (no pooling across tenants)
  4. Connection closed at task end

Alternative (simpler): call Neon API at runtime to fetch URI
  → Neon's reference implementation does this (db-per-tenant repo)
  → Trades one DB lookup for one API call
  → Higher latency but zero storage of secrets
  → Valid for low-volume onboarding flows; less suitable for hot retrieval path
```

**Recommendation for Veridian:** Fernet + control DB for the retrieval hot path (L4 and L5 need fast credential lookup on every query). Neon API fetch is acceptable for provisioning (once per tenant) and for the eval/red-team paths (infrequent).

**Key table in control DB:**
```sql
CREATE TABLE tenant_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    encrypted_conn_string BYTEA NOT NULL,
    neon_project_id TEXT NOT NULL,     -- for Neon API calls (PITR, branching)
    created_at TIMESTAMPTZ DEFAULT now(),
    rotated_at TIMESTAMPTZ
);
```

**Key rotation:** Fernet's `MultiFernet` supports key rotation without downtime — add new key to front of key list, re-encrypt old credentials in background, remove old key. For v1, rotation is a `scripts/` task.

### Pattern 5: Claude Agent SDK — Stateless Per-Call Instantiation

**How the SDK actually works (confirmed from official docs):** The `query()` function is stateless and call-level. `system_prompt` is passed as a field inside `ClaudeAgentOptions` at every invocation — not at class construction time. There is no "agent instance" you construct and reuse.

**Correct multi-tenant pattern:**

```python
# services/agent_runner.py
from claude_agent_sdk import query, ClaudeAgentOptions
from app.services.retrieval import create_retrieve_tool
from app.services.structured import create_lookup_tool

async def run_customer_agent(
    user_message: str,
    agent: AgentModel,           # has: name, soul, role, tools config
    tenant_conn_string: str,
) -> AsyncIterator[Message]:
    system_prompt = build_system_prompt(agent)  # soul + role + tool instructions
    tools = build_tool_options(agent, tenant_conn_string)
    
    async for message in query(
        prompt=user_message,
        options=ClaudeAgentOptions(
            system_prompt=system_prompt,          # per-tenant, per-call
            allowed_tools=tools,
            permission_mode="dontAsk",            # headless; tools pre-approved
        ),
    ):
        yield message
```

**What this means:**
- No shared mutable state between tenants. Each chat turn is a fresh `query()` call.
- Session continuity is handled by `resume=session_id` in `ClaudeAgentOptions` — you capture the `session_id` from the `SystemMessage.data["session_id"]` field on the first turn and pass it on subsequent turns.
- The "one class, instantiated per tenant" pattern is wrong for this SDK. The correct model is "one function, parameterised per call."
- The red team agents and the pre-deployment orchestrator agent follow the same pattern — same `query()` function, different `system_prompt` and `allowed_tools`.

**Sub-agents for red team and orchestrator:** The SDK supports `AgentDefinition` objects passed via `agents={}` in `ClaudeAgentOptions`. Red team agents are best modelled as sub-agents orchestrated by a coordinator, which matches the iterative probe-observe-refine loop described in the PRD.

---

## Data Flow

### Onboarding (M1–M4 canonical path)

```
Browser                FastAPI              Redis              Celery Workers         Neon
  │                      │                    │                     │                  │
  │─POST /agents────────▶│                    │                     │                  │
  │                      │─publish job_id────▶│                     │                  │
  │                      │─dispatch chain─────────────────────────▶│                  │
  │◀─201 {job_id}────────│                    │                     │                  │
  │                      │                    │                     │                  │
  │─GET /jobs/:id/stream▶│                    │                     │                  │
  │◀═══SSE stream════════│◀───sub(job:id)─────│                     │                  │
  │                      │                    │                     │                  │
  │                      │                    │◀──publish(provision_start)─────────────│
  │◀══ event: provision ═│                    │                     │─POST /projects──▶│
  │                      │                    │                     │◀─project_id──────│
  │                      │                    │                     │─run migrations───▶│
  │                      │                    │◀──publish(provision_done)──────────────│
  │◀══ event: ready ═════│                    │                     │                  │
  │                      │                    │                     │                  │
  │                      │                    │◀──publish(parse_start)─────────────────│
  │◀══ event: parsing ═══│                    │    [... each task publishes events ...]│
  │                      │                    │                     │                  │
  │                      │                    │◀──publish(checklist_done)──────────────│
  │◀══ event: ready_for_review ══════════════│                     │                  │
  │─POST /agents/:id/approve────────────────▶│                     │                  │
  │◀─200 {widget_snippet}────────────────────│                     │                  │
```

### Chat Request (post-deployment)

```
Widget  →  FastAPI /chat  →  Celery runtime queue  →  Agent runner
                                                           │
                                                    retrieve() tool call
                                                           │
                                                    Tenant Neon DB (L4)
                                                    pgvector + BM25 + RRF + rerank
                                                           │
                                                    top_k chunks → Agent
                                                           │
                                                    Agent response streamed
                                                           │
                           FastAPI streams to widget via SSE
                                                           │
                                                    [async] Validation chain (L6)
                                                    Langfuse trace logged
```

---

## Build Order

### Strict Dependencies (cannot proceed without)

```
L3 Control DB schema
  → L3 Tenant DB schema (Alembic migrations must work before provisioning)
    → L1 Auth + Tenant CRUD (needs control DB)
      → L2 Celery + Redis + SSE (needs L1 to dispatch jobs, L3 to write status)
        → L2 provision_neon task (needs L2 infrastructure + L3 tenant schema)
          → M1 complete: "SSE streams provisioning of a real Neon project"

L2 provision_neon → L2 parse + chunk + embed tasks → L3 Tenant DB populated
  → L4 Retrieval engine (needs chunks + embeddings in Neon)
    → L5 Reasoning engine (needs L4 retrieve() tool to be callable)
      → L10 Widget (needs L5 agent running behind an endpoint)
        → M4 complete: "end-to-end demo works"

L5 Reasoning engine → L6 Validation chain (wraps L5 responses)
  → L7 Eval system (needs L6 judge outputs as production telemetry)
    → L8 Red team (runs against the deployed L5 agent)
      → L9 Pre-deployment checklist (reads L7 + L8 results)
        → M8 complete: "non-technical tester completes full journey"
```

### Build Order by Milestone

| Milestone | Must Have Before | Produces |
|-----------|-----------------|---------|
| M1 | Nothing | L1 auth, L3 control DB, L2 Celery/Redis, SSE pattern, Neon provisioning |
| M2 | M1 (tenant DB exists) | L2 ingestion chain (parse → chunk → embed), L3 tenant DB populated |
| M3 | M2 (chunks in DB) | L4 retrieval engine (vector + BM25 + RRF + rerank) |
| M4 | M3 (retrieval working) | L5 reasoning engine, L10 widget v0 |
| M5 | M4 (agent running) | L6 validation chain |
| M6 | M5 (validation telemetry) | L7 eval system |
| M7 | M4 (agent to attack) | L8 red team agents |
| M8 | M6 + M7 (eval + red team results) | L9 pre-deployment checklist |
| M9 | M3 + M2 (data shape available) | Strategy synthesis agent (replaces hand-written configs) |
| M10 | M8 (full system running) | Crons, drift detection, digest emails |

**Critical insight:** M7 (red team) only needs M4 (a running agent to attack). It does NOT need M6 (eval) to be complete. M7 and M6 are parallelisable. The PRD sequences them M6→M7 for readability, but if timeline pressure demands it, M7 can start once M4 ships.

**What M1 must deliver precisely:** The SSE job status pattern is the hardest part of M1 and the foundation everything else sits on. Every subsequent milestone adds tasks to the Celery chain; the pub/sub→SSE infrastructure must be correct before any of that is meaningful. Do not move to M2 without a working SSE stream showing real Celery task state transitions.

---

## Key Architecture Decisions

### Decision 1: Per-Tenant Neon Projects — VALIDATED

The PRD's choice is correct and proven. Neon's own reference implementation (the `neondatabase/db-per-tenant` repo) shows exactly this pattern. The official docs explicitly state "we generally don't recommend schema-per-tenant for SaaS applications."

**Why it matters beyond isolation:** Neon branching for eval isolation (PRD Layer 7 — "nightly evals run against a branch") is **only possible with the project-per-tenant model**. Schema-per-tenant on a shared DB cannot use Neon branching for per-tenant eval isolation. This is not a minor tradeoff — it is the eval architecture's foundation.

**Operational reality for a solo developer:** Provisioning is ~30 lines of Python calling the Neon REST API. Projects spin up in ~2 seconds. Empty projects cost nothing (scale-to-zero). There is no ongoing per-project operational burden.

### Decision 2: Celery Chains — VALIDATED with Caveats

Celery chains are the right choice given the existing Redis dependency and solo developer constraint. The caveats:

- **Visibility is weak out of the box.** Add Flower (Celery's monitoring dashboard) from day one. Without it, debugging failed chains in production is painful.
- **Celery's chain error propagation is coarse.** A failure in step 4 of a 10-step chain gives you the exception but not the full preceding context. Mitigate by logging structured events (not just exceptions) to the job_events table at every task boundary.
- **`acks_late` is mandatory**, not optional, given the PRD's requirement that a worker dying mid-task does not corrupt a tenant DB. Without `acks_late`, a crashed worker silently drops the task.

### Decision 3: Two Celery Queues — VALIDATED

Queue separation is the right call. The `pipeline` queue handles ingestion chains (high memory, low concurrency, acceptable latency). The `runtime` queue handles chat queries and scheduled eval/red-team runs (low memory, high concurrency, latency-sensitive). A nightly red team cron must not starve a customer onboarding.

**Worker configuration:**
```
pipeline workers: 2–4 processes, high memory limit (Docling + LLM calls)
runtime workers:  8–16 processes, low memory limit (retrieval + agent calls)
```

### Decision 4: Claude Agent SDK for Customer Agents — VALIDATED

The `query()` function with `system_prompt` in `ClaudeAgentOptions` is exactly the right abstraction. The per-tenant parameterisation is done at call time (inject soul + role into `system_prompt`), not at class construction time. This is stateless, testable, and scales cleanly to multiple concurrent tenants.

**Important:** The Agent SDK runs an agent loop internally. For the retrieval tool, implement `retrieve()` as a standard Python async function that calls L4 directly — do not use the SDK's built-in `Read`/`Bash` tools for retrieval. The customer service agent only needs the four custom tools listed in the PRD: `retrieve`, `lookup_structured`, `escalate_to_human`, `clarify`.

**Sessions for chat continuity:** Capture `session_id` from the first chat turn's `SystemMessage`, store it in the `conversations` table, and pass `resume=session_id` on subsequent turns. This gives the agent conversation memory without building a manual message history.

### Decision 5: Validation Chain Async After Stream — VALIDATED

The PRD's design — stream the agent response to the user, then run the validation chain async — is the correct production pattern. Running Gatekeeper/Auditor/Strategist synchronously would add 1–3 seconds to every response (three Haiku calls). Async validation preserves sub-4-second p95 latency while still capturing all quality signals in Langfuse.

**Edge case:** If Auditor returns `ungrounded` on a response that has already been shown to the user, the platform has no way to retract it. The PRD's mitigation (sampling rate + persistent failure flagging) is correct. For v1, the validation chain is a quality monitoring tool, not a pre-send filter.

---

## Anti-Patterns

### Critical Anti-Patterns (cause rewrites or data incidents)

#### Anti-Pattern 1: Mixing tenant vectors in a shared pgvector table with application-level filters

**What goes wrong:** All tenants' embeddings coexist in one table. Tenant isolation relies entirely on `WHERE tenant_id = ?` in every query. One forgotten filter exposes every vector in the database.

**Why it's tempting:** Simpler to start, avoids Neon provisioning logic.

**Why it's fatal:** pgvector `<->` (cosine) searches do not respect RLS by default without explicit policy configuration. Application-level filters are easy to miss in new code paths. In embedding space, a query from Tenant A may be semantically nearest to Tenant B's confidential document — the filter is the only barrier.

**Prevention:** Use the per-project model. Each tenant's embeddings live in their own Neon project's `embeddings` table. The infrastructure makes cross-tenant access impossible, not just convention.

#### Anti-Pattern 2: Performing any long-running work in the FastAPI request thread

**What goes wrong:** Docling parsing, LLM calls, Voyage embedding — any of these in a request handler blocks the async event loop. Under load, all requests stall. FastAPI appears hung.

**Prevention:** The PRD's rule — "no background work runs in the request thread" — must be enforced at code review. Every operation that touches an external API or does significant computation goes through Celery. The API surface dispatches and returns a job_id.

#### Anti-Pattern 3: Passing the connection string as a Celery task argument

**What goes wrong:** Celery task arguments are serialised into the Redis queue as JSON. Connection strings (which include passwords) appear in Redis memory, Flower UI, and Celery result backend in plaintext.

**Prevention:** Pass `tenant_id` as the task argument. Each task looks up and decrypts the connection string at execution time from the control DB. The secret never enters the queue.

#### Anti-Pattern 4: Building the reasoning engine (L5) before retrieval (L4) is solid

**What goes wrong:** The agent's quality is directly determined by retrieval quality. Building an agent on top of broken retrieval produces an agent that hallucinates — not because the agent is misconfigured, but because the context it receives is wrong. This is hard to diagnose if both layers are being developed simultaneously.

**Prevention:** M3 (retrieval) must be demonstrably correct before M4 (agent) starts. The M3 demo — "a query notebook showing candidate sets at each retrieval stage" — is the acceptance gate. Do not start the agent loop until retrieval is emitting the right chunks.

#### Anti-Pattern 5: Skipping idempotency on Celery tasks

**What goes wrong:** With `acks_late=True`, any task can be retried after a worker crash. A non-idempotent task that creates a Neon project will create a second project on retry, leaving an orphaned project that costs money and confuses tenant resolution.

**Prevention:** Every task in the pipeline chain must be idempotent. For provisioning: check if `neon_project_id` already exists for the tenant before calling the Neon API. For embedding: use `ON CONFLICT (chunk_id) DO NOTHING`. For metadata generation: check `chunk_metadata` table before calling Claude.

### Moderate Anti-Patterns

#### Anti-Pattern 6: Using WebSockets instead of SSE for job status

WebSocket connections require stateful server infrastructure, making horizontal scaling harder. Job status streaming is one-directional (server → client). SSE over HTTP/2 is sufficient and simpler. The `sse-starlette` library handles reconnection and client disconnect detection.

#### Anti-Pattern 7: Hardcoding retrieval strategy parameters

Using fixed `k=10`, `threshold=0.7` values across all tenants produces mediocre results. A corpus of 500 product SKUs needs a very different strategy than a corpus of 300 policy documents. The PRD's JSON config approach (generated at build time by a strategy agent) is the right pattern. Even before M9 (automated synthesis), write hand-tuned configs as JSON files per tenant, not as constants in code.

#### Anti-Pattern 8: Running eval against the production tenant DB

Evals insert test queries and observe responses from the deployed agent. Running these against production data risks:
- Filling conversation history with synthetic traffic
- Hitting rate limits that affect real users
- Eval writes (storing results) competing with production reads

**Prevention:** Neon branching is the exact solution. Create a branch before each eval run, execute eval on the branch, discard the branch after. This is a first-class Neon API operation.

#### Anti-Pattern 9: Treating the Claude Agent SDK as a class to subclass

**What goes wrong:** Developers wrap `query()` in a class and add agent-specific logic in `__init__`. This creates stateful objects that accumulate session state across calls, leading to context bleed between tenant conversations.

**Prevention:** Keep agent calls as pure functions. `query()` receives all configuration at call time. State (session_id) is stored externally (in the DB), not in Python objects.

### Minor Anti-Patterns

#### Anti-Pattern 10: Forgetting `X-Accel-Buffering: no` on SSE endpoints

Nginx silently buffers SSE responses until the buffer fills (~16KB). The user sees no progress for minutes, then all events arrive at once. This is invisible in development (no Nginx proxy). Add the header from day one.

#### Anti-Pattern 11: Not setting `CELERY_TASK_ALWAYS_EAGER=True` in test config

Without eager mode, tests that call `.delay()` or `.apply_async()` spawn real Celery workers or silently skip task execution. Use `CELERY_TASK_ALWAYS_EAGER=True` in test settings to run tasks synchronously in-process.

#### Anti-Pattern 12: Building the admin UI in parallel with the API

The Next.js admin UI is a consumer of the FastAPI OpenAPI spec. Building both simultaneously means the API contract changes constantly and the UI is always chasing. The right sequence: define API shape → write Pydantic schemas → generate TypeScript client → build UI against stable types. Each milestone should finish the API surface before the UI for that milestone is built.

---

## Phase-Specific Warnings

| Phase / Milestone | Likely Pitfall | Mitigation |
|------------------|----------------|-----------|
| M1: SSE implementation | Nginx buffering, Redis channel naming collisions, missed `acks_late` | Set `X-Accel-Buffering: no`, use `job:{job_id}` channel names, configure `acks_late` from day one |
| M1: Neon provisioning task | Retry creates second project (idempotency) | Check `neon_project_id` exists before provisioning; store immediately on first creation |
| M2: Docling parsing | Memory spikes on large PDFs block the pipeline queue | Set worker memory limits; run Docling in separate worker pool if needed |
| M2: Voyage embedding API | Rate limits cause silent task failures | Implement exponential backoff on Voyage API calls; store embedding progress per chunk |
| M3: RRF fusion | Off-by-one in rank normalisation produces wrong fusion scores | Unit-test the RRF SQL CTE against known result sets before integrating |
| M3: pgvector HNSW | Default HNSW parameters (`m=16, ef=64`) may not suit tenant data size | Store HNSW config in retrieval strategy JSON; allow per-tenant tuning |
| M4: Agent SDK session continuity | Session state lost between deploys | Store `session_id` in DB; test resume path explicitly |
| M5: Validation chain latency | Gatekeeper + Auditor + Strategist adds latency if run synchronously | Enforce async after stream; never block on validation before response reaches user |
| M6: Eval branch management | Branches not cleaned up → storage cost accumulates | Delete eval branches after run; add cleanup task to Celery beat |
| M7: Red team false positives | Critical severity classifications block all deployments | Verify severity taxonomy with test cases; only critical blocks — high warns |
| M8: Checklist as rubber stamp | Owner clicks through without reading | Force per-warning acknowledgment; log acknowledgments with reasoning field |

---

## Sources

- [Neon Multitenancy Guide](https://neon.com/docs/guides/multitenancy) — official Neon guidance on project-per-tenant vs schema-per-tenant (HIGH confidence)
- [Multi-Tenant RAG With One Neon Project Per User — Neon Blog](https://neon.com/blog/multi-tenant-rag) — production reference implementation (HIGH confidence)
- [neondatabase/db-per-tenant on GitHub](https://github.com/neondatabase/db-per-tenant) — reference code: connection string retrieval via Neon API, provisioning flow, control DB structure (HIGH confidence)
- [Claude Agent SDK Overview — Official Docs](https://code.claude.com/docs/en/agent-sdk/overview) — confirmed `system_prompt` in `ClaudeAgentOptions`, `query()` as stateless call, session continuity via `resume=` (HIGH confidence)
- [Claude Agent SDK Quickstart — Official Docs](https://code.claude.com/docs/en/agent-sdk/quickstart) — confirmed `system_prompt` field syntax, `permission_mode: "dontAsk"` for headless agents (HIGH confidence)
- [Orchestrating AI Tasks: Celery vs Temporal](https://dasroot.net/posts/2026/02/orchestrating-ai-tasks-celery-temporal/) — Celery vs Temporal tradeoffs for AI workloads (MEDIUM confidence)
- [sse-starlette on PyPI](https://pypi.org/project/sse-starlette/) — production SSE library for FastAPI (HIGH confidence)
- [FastAPI SSE + Celery: Real-Time Notifications](https://dev.to/enlabe/notificaciones-en-tiempo-real-con-sse-fastapi-y-celery-3hb9) — Redis pub/sub → SSE production pattern (MEDIUM confidence)
- [Why 95% of RAG Apps Leak Data](https://medium.com/@pswaraj0614/why-95-of-rag-apps-leak-data-across-users-and-how-i-fixed-it-0e9ded006a8c) — technical mechanisms of cross-tenant data leakage in RAG (MEDIUM confidence)
- [Building Successful Multi-Tenant RAG Applications — The Nile](https://www.thenile.dev/blog/multi-tenant-rag) — connection pool anti-patterns, data isolation in shared vector tables (MEDIUM confidence)
- [Advanced Celery: Idempotency, Retries, Error Handling — Vinta](https://www.vintasoftware.com/blog/celery-wild-tips-and-tricks-run-async-tasks-real-world) — `acks_late`, idempotent task patterns (HIGH confidence)
- [Fernet Symmetric Encryption — cryptography.io](https://cryptography.io/en/latest/fernet/) — AES-128-CBC + HMAC-SHA256, `MultiFernet` key rotation (HIGH confidence)
- [10 Essential Lessons for Running Celery in Production](https://medium.com/@hankehly/10-essential-lessons-for-running-celery-workloads-in-production-720ce5a05a17) — production Celery configuration, worker process limits (MEDIUM confidence)
