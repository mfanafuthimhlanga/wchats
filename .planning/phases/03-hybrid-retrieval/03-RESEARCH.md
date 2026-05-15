# Phase 3: Hybrid Retrieval — Research

**Researched:** 2026-05-16
**Domain:** pgvector HNSW + native PostgreSQL tsvector BM25 + Reciprocal Rank Fusion + Voyage Rerank
**Confidence:** HIGH (all core claims verified against codebase or installed library)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Single Alembic control-DB migration: `apps/api/alembic/versions/0003_agent_retrieval_strategy.py` — adds `retrieval_strategy JSONB NOT NULL DEFAULT '{}'::jsonb` to `agents`
- No tenant DB migration required — GIN index (`chunks_content_tsv_idx`) and HNSW index (`embeddings_vector_hnsw_idx`) already present from 0001_tenant_v1_schema.py
- BM25 uses only native `tsvector` + `ts_rank_cd` — no pg_search, no pgbm25 (deprecated on Neon March 2026)
- RRF constant k=60 — locked, not parameterized
- RRF fused as a single SQL CTE query via psycopg2 against the tenant DB
- `voyage-3` for query embedding (`input_type="query"`) — MUST match the `voyage-3` used during ingestion; dimension is 1024
- Voyage rerank model: `rerank-2` (not rerank-1, not rerank-lite-1)
- Cohere Rerank as fallback only — if Voyage raises, fall back; log warning, do not raise
- `retrieve_and_rank` task on `runtime` queue with `acks_late=True`, `max_retries=3`, `default_retry_delay=2`
- Task args: `(job_id, agent_id, query)` only — no connection strings, no API keys
- Retrieval strategy fetched from `agents.retrieval_strategy` at runtime alongside connection string
- SSE events: `query.started`, `query.embedding`, `query.searching`, `query.reranking`, `query.complete`
- `query.complete` payload structure: `{query, results, trace, strategy_used}` — trace contains four candidate sets
- Route: `POST /agents/{agent_id}/query` → 202 + job_id; `GET /agents/{agent_id}/queries` → list
- Job kind: `query_agent`
- Agent validation: agent exists + belongs to tenant + status = 'ready'
- Idempotency: if `query.complete` already emitted for job_id, return early
- New router: `apps/api/app/api/v1/query.py`
- Demo notebook: `notebooks/demo_m3.ipynb` — reads from `.env`, polls for `query.complete`, displays four dataframes

### Claude's Discretion
- Cohere fallback implementation details (exact import path, error handling shape)
- `RetrievalStrategy` Pydantic model location (new file vs inline in task)
- Notebook polling interval and timeout values

### Deferred Ideas (OUT OF SCOPE)
- `verified_qa` lookup before hybrid search (M6)
- LLM-based query expansion (M9)
- Entity-based metadata filters (M4+)
- Auto-generated retrieval strategies (M9)
- Cohere Rerank as primary (not fallback) — revisited in M9
- Dedicated `PATCH /agents/{id}/retrieval_strategy` endpoint — direct DB write in notebook setup is sufficient for M3
- Admin UI exposure of retrieval trace (M4)
- Streaming retrieval results via SSE (future)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RET-01 | Query executes pgvector HNSW search against tenant embeddings | Vector search SQL verified against existing HNSW index; `input_type="query"` embedding confirmed |
| RET-02 | BM25 via native `tsvector` + `ts_rank_cd` (no pg_search/pgbm25) | GIN index confirmed in 0001_tenant_v1_schema.py; native-only BM25 SQL pattern locked in CONTEXT.md |
| RET-03 | Vector + BM25 fused via RRF in single SQL CTE | Full CTE SQL locked in CONTEXT.md; psycopg2 execution pattern confirmed from embed.py |
| RET-04 | Voyage Rerank primary (rerank-2), Cohere Rerank fallback | voyageai 0.3.7 installed; `rerank()` signature verified from source; cohere 5.x API confirmed |
| RET-05 | Per-tenant retrieval strategy JSONB config | `RetrievalStrategy` Pydantic model shape locked in CONTEXT.md; JSONB column migration specified |
| RET-06 | Full retrieval trace in `query.complete` payload | Trace structure locked in CONTEXT.md; four candidate sets with full scores |
| RET-07 | Strategies hand-written per tenant at M3 | Direct DB write pattern confirmed; no endpoint required |
| RET-08 | Demo Jupyter notebook against M2 tenant DB | Notebook structure locked in CONTEXT.md; polling pattern researchable |
</phase_requirements>

---

## Summary

M3 is a pure infrastructure phase — no LLM calls at retrieval time, no Claude agents. It wires four deterministic components (HNSW vector search, native BM25, RRF SQL fusion, Voyage Rerank) into a single Celery task that emits a full trace via SSE. Every external API call (Voyage embed, Voyage rerank) is already used in M2 — M3 reuses the same `voyageai.Client()` singleton pattern with the key distinction that query embedding uses `input_type="query"` rather than `"document"`.

The entire retrieval pipeline executes inside a single Celery task (`retrieve_and_rank`) on the `runtime` queue. FastAPI dispatches it asynchronously (202 → job_id), and the caller polls `GET /jobs/{job_id}/events` for the `query.complete` event. This matches the M1/M2 SSE pattern exactly — the only new work is the retrieval logic inside the task and the `query.py` router.

The Alembic migration is trivial (one ALTER TABLE ADD COLUMN). The tenant DB requires zero changes — all indexes were created in 0001_tenant_v1_schema.py. The implementation risk is concentrated in (a) the RRF SQL CTE with a FULL OUTER JOIN and (b) the RerankingResult score-extraction path, both of which are fully documented below.

**Primary recommendation:** Build the `retrieve_and_rank` task and `query.py` router following the exact patterns from `embed.py` and `documents.py`. The RRF CTE is the most complex SQL written in this project so far — test it with a unit test that asserts known ranks produce expected RRF scores before wiring it to live data.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Query embedding (Voyage) | Celery task (runtime queue) | — | CPU-bound external API call; FastAPI never does work inline |
| HNSW vector search | Celery task → tenant DB (Neon) | — | SQL query against pgvector index; runs in task |
| BM25 keyword search | Celery task → tenant DB (Neon) | — | SQL query against GIN tsvector index; runs in task |
| RRF fusion | Celery task → tenant DB (Neon) | — | Single SQL CTE with FULL OUTER JOIN; DB does the work |
| Voyage Rerank | Celery task (runtime queue) | — | External API call; belongs in task not route |
| Retrieval strategy config | Control DB (agents.retrieval_strategy) | Pydantic model in task | JSONB fetched at runtime by agent_id |
| SSE event emission | Celery task → Redis pub/sub → job_events | — | Reuses emit() helper from M1 |
| Route dispatch | FastAPI API tier | — | POST /agents/{id}/query → 202, dispatch task |
| Job + event persistence | Control DB | — | Existing jobs + job_events tables |
| Demo notebook | Client tier (Jupyter) | — | Calls API, polls SSE, visualizes trace |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| voyageai | 0.3.7 | Query embedding + Voyage Rerank | Already installed from M2; same client instance |
| psycopg2-binary | 2.9.12 | RRF CTE execution against tenant DB | M2 pattern; sync required for Celery tasks |
| cohere | 5.x (to add) | Rerank fallback only | Simple fallback; only needed if Voyage raises |

[VERIFIED: pyproject.toml line 25 — `voyageai==0.3.7`; line 15 — `psycopg2-binary==2.9.12`]

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | >=2.0,<3.0 | `RetrievalStrategy` model | Validate/parse strategy JSONB at runtime |
| structlog | 25.5.0 | Task logging | Same as M2 — bind contextvars in task |
| tenacity | 9.1.2 | Retry Voyage API calls | Same retry pattern as embedding_service.py |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| psycopg2 (sync) for tenant DB | asyncpg | CONTEXT.md explicitly locks psycopg2 for consistency with M2 |
| single CTE SQL | two separate queries + Python merge | Python merge loses SQL-level FULL OUTER JOIN; CTE is more correct |
| `rerank-2` | `rerank-lite-1` | rerank-lite-1 is faster/cheaper; locked to rerank-2 per CONTEXT.md |

**Installation:**
```bash
pip install "cohere>=5.0,<7.0"
```
(Add to pyproject.toml dependencies)

---

## Architecture Patterns

### System Architecture Diagram

```
POST /agents/{id}/query
        │
        ▼
FastAPI: query.py router
  ├── Validate agent (exists, tenant-scoped, status=ready)
  ├── Create jobs row (kind='query_agent', status='pending')
  └── apply_async(retrieve_and_rank, args=[job_id, agent_id, query], queue='runtime')
        │
        ▼ (202 returned to caller)

GET /jobs/{job_id}/events  (SSE stream, caller polls)
        │
        ▼

Celery: retrieve_and_rank (runtime queue)
  ├── Idempotency guard: query.complete already emitted? → return early
  ├── Fetch agent from control DB (connection string + retrieval_strategy)
  ├── Decrypt neon_connection_string
  ├── Parse RetrievalStrategy from agents.retrieval_strategy JSONB
  ├── emit(query.started)
  │
  ├── Voyage embed query (input_type="query", model="voyage-3")
  │       └── emit(query.embedding)
  │
  ├── RRF SQL CTE against tenant DB (psycopg2)
  │       ├── vector_ranked CTE  ← HNSW cosine search
  │       ├── bm25_ranked CTE    ← GIN ts_rank_cd search
  │       └── fused CTE          ← FULL OUTER JOIN + 1/(60+rank)
  │       └── emit(query.searching)
  │
  ├── Voyage rerank (model="rerank-2", top_k=final_k)
  │       └── Cohere fallback if Voyage raises
  │       └── emit(query.reranking)
  │
  ├── Apply rerank_threshold filter
  ├── Build results + trace payload
  └── emit(query.complete, {query, results, trace, strategy_used})
      └── job.status = 'complete'
```

### Recommended Project Structure
```
apps/api/
├── app/
│   ├── api/v1/
│   │   └── query.py                     # NEW: POST /agents/{id}/query, GET /agents/{id}/queries
│   ├── schemas/
│   │   └── query.py                     # NEW: QueryRequest, QueryResponse, QueryJobResponse
│   ├── worker/tasks/runtime/
│   │   └── retrieve.py                  # NEW: retrieve_and_rank task
│   └── services/
│       └── retrieval_service.py         # NEW: RRF CTE, rerank logic, RetrievalStrategy model
├── alembic/versions/
│   └── 0003_agent_retrieval_strategy.py # NEW: ADD COLUMN retrieval_strategy JSONB
└── notebooks/
    └── demo_m3.ipynb                    # NEW: demo notebook
```

**Note on task module path:** CONTEXT.md places the task in the `runtime` subpackage (`app.worker.tasks.runtime`). The `celery_app.py` task_routes already routes `app.worker.tasks.runtime.*` to the `runtime` queue automatically. [VERIFIED: celery_app.py line 88]

### Pattern 1: Voyage Query Embedding (input_type="query")

Query embedding MUST use `input_type="query"`, not `"document"`. The M2 `embedding_service.py` uses `input_type="document"` for chunk ingestion. Using the wrong type silently degrades retrieval quality because the embedding model prepends different prompts for each type.

```python
# Source: embedding_service.py + voyageai 0.3.7 installed library
# CRITICAL: input_type="query" for retrieval, "document" was used during ingestion
from app.services.embedding_service import _get_vo

vo = _get_vo()  # reuse lazy-initialized singleton; avoids pkg_resources import issue
result = vo.embed([query_text], model="voyage-3", input_type="query")
query_vector = result.embeddings[0]  # list[float], len == 1024
```

[VERIFIED: embedding_service.py _embed_batch line 104 uses `input_type="document"`; query path must differ]

### Pattern 2: RRF SQL CTE (psycopg2)

The complete RRF CTE with rank window functions and FULL OUTER JOIN. The `::vector` cast is required because psycopg2 passes the vector as a Python list stringified; pgvector needs the explicit cast.

```sql
-- Source: CONTEXT.md locked SQL + verified against 0001_tenant_v1_schema.py index names
WITH vector_ranked AS (
    SELECT
        e.chunk_id,
        c.content,
        c.document_id,
        1 - (e.vector <=> %(query_vector)s::vector) AS cosine_score,
        ROW_NUMBER() OVER (ORDER BY e.vector <=> %(query_vector)s::vector) AS rank
    FROM embeddings e
    JOIN chunks c ON c.id = e.chunk_id
    ORDER BY e.vector <=> %(query_vector)s::vector
    LIMIT %(vector_k)s
),
bm25_ranked AS (
    SELECT
        c.id AS chunk_id,
        c.content,
        c.document_id,
        ts_rank_cd(to_tsvector('english', c.content),
                   plainto_tsquery('english', %(query)s)) AS bm25_score,
        ROW_NUMBER() OVER (
            ORDER BY ts_rank_cd(to_tsvector('english', c.content),
                                plainto_tsquery('english', %(query)s)) DESC
        ) AS rank
    FROM chunks c
    WHERE to_tsvector('english', c.content) @@ plainto_tsquery('english', %(query)s)
    ORDER BY bm25_score DESC
    LIMIT %(bm25_k)s
),
fused AS (
    SELECT
        COALESCE(v.chunk_id, b.chunk_id)         AS chunk_id,
        COALESCE(v.content, b.content)           AS content,
        COALESCE(v.document_id, b.document_id)   AS document_id,
        COALESCE(1.0 / (60.0 + v.rank), 0.0)
            + COALESCE(1.0 / (60.0 + b.rank), 0.0) AS rrf_score,
        v.cosine_score,
        b.bm25_score,
        v.rank AS vector_rank,
        b.rank  AS bm25_rank
    FROM vector_ranked v
    FULL OUTER JOIN bm25_ranked b ON v.chunk_id = b.chunk_id
)
SELECT chunk_id, content, document_id, rrf_score,
       cosine_score, bm25_score, vector_rank, bm25_rank
FROM fused
ORDER BY rrf_score DESC
LIMIT %(final_k)s
```

**psycopg2 execution:**
```python
# Source: embed.py + migrations.py patterns (verified from codebase)
tenant_conn = psycopg2.connect(conn_str)  # conn_str from fernet_decrypt()
try:
    with tenant_conn.cursor() as cur:
        cur.execute(RRF_SQL, {
            "query_vector": str(query_vector),  # stringify list for pgvector cast
            "query": query_text,
            "vector_k": strategy.vector_k,
            "bm25_k": strategy.bm25_k,
            "final_k": strategy.final_k,
        })
        rows = cur.fetchall()
finally:
    tenant_conn.close()
```

[VERIFIED: embed.py uses `str(vec)` for pgvector insert line 235; psycopg2.connect pattern confirmed]

### Pattern 3: Voyage Rerank (verified from installed voyageai 0.3.7)

```python
# Source: voyageai 0.3.7 installed — inspect.getsource(voyageai.Client.rerank) confirmed
# RerankingResult = namedtuple("RerankingResult", ["index", "document", "relevance_score"])
# RerankingObject.results: List[RerankingResult]
from app.services.embedding_service import _get_vo

vo = _get_vo()
reranking = vo.rerank(
    query=query_text,
    documents=[c["content"] for c in fused_candidates],
    model="rerank-2",
    top_k=strategy.final_k,
    truncation=True,
)

# Score extraction (namedtuple access)
for r in reranking.results:
    chunk = fused_candidates[r.index]
    rerank_score = r.relevance_score  # float, 0.0–1.0
    if rerank_score >= strategy.rerank_threshold:
        # include in final results
```

[VERIFIED: voyageai 0.3.7 source — RerankingResult._fields == ('index', 'document', 'relevance_score')]

### Pattern 4: Cohere Rerank Fallback

```python
# Source: cohere 5.x/6.x SDK — cohere.ClientV2 is current API
# ASSUMED for exact import path — use cohere>=5.0,<7.0
import cohere

co = cohere.ClientV2(api_key=settings.COHERE_API_KEY)
response = co.rerank(
    model="rerank-v3.5",       # or "rerank-english-v3.0"
    query=query_text,
    documents=[c["content"] for c in fused_candidates],
    top_n=strategy.final_k,
)
for r in response.results:
    rerank_score = r.relevance_score  # float 0-1
```

**Fallback wrapper pattern:**
```python
try:
    reranked = _voyage_rerank(vo, query_text, fused_candidates, strategy)
except Exception as exc:
    log.warning("retrieve_and_rank.voyage_rerank_failed_falling_back",
                error_type=type(exc).__name__)
    reranked = _cohere_rerank(query_text, fused_candidates, strategy)
```

[CITED: docs.cohere.com/v1/reference/rerank — `relevance_score` confirmed; exact cohere model string ASSUMED]

### Pattern 5: Idempotency Guard

```python
# Source: provision.py idempotency guard pattern + emit() helper
# Check if query.complete already emitted for this job_id
with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
    if agent is None:
        log.error("retrieve_and_rank.agent_not_found", agent_id=agent_id)
        return {}

    # Idempotency: check job_events for query.complete
    from sqlalchemy import text as sa_text
    existing = db.execute(
        sa_text("SELECT 1 FROM job_events WHERE job_id = :jid AND event_type = 'query.complete' LIMIT 1"),
        {"jid": job_id}
    ).fetchone()
    if existing:
        log.info("retrieve_and_rank.already_complete", job_id=job_id)
        return {}
```

[VERIFIED: job_events table schema confirmed in 0001_control_db_initial.py — (id, job_id, event_type, payload, created_at)]

### Pattern 6: Alembic 0003 Control DB Migration

```python
# Source: 0002_tenant_api_key_prefix.py pattern (op.add_column with nullable/default)
# down_revision = "0002" to chain from latest control DB migration
revision: str = "0003"
down_revision: Union[str, None] = "0002"

def upgrade() -> None:
    op.execute("""
        ALTER TABLE agents
        ADD COLUMN retrieval_strategy JSONB NOT NULL DEFAULT '{}'::jsonb
    """)

def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS retrieval_strategy")
```

[VERIFIED: 0002_tenant_api_key_prefix.py confirmed as latest control DB migration (no 0003 file exists)]

### Pattern 7: retrieve_and_rank Task Skeleton

```python
# Source: embed.py + provision.py patterns (verified from codebase)
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=2,
    queue="runtime",
    name="retrieve_and_rank",
)
def retrieve_and_rank(self, job_id: str, agent_id: str, query: str) -> dict:
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        # ... idempotency check, fetch strategy, decrypt conn_str
        strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})
        conn_str = fernet_decrypt(agent.neon_connection_string)

        emit(job_id, "query.started", {"agent_id": agent_id}, db, _redis)
        # ... embed, search, fuse, rerank, emit events, build payload
        emit(job_id, "query.complete", payload, db, _redis)

        job = db.get(Job, job_id)
        job.status = "complete"
        db.commit()
    return {}
```

### Pattern 8: FastAPI query.py Router

```python
# Source: documents.py route pattern (verified from codebase)
@router.post("/agents/{agent_id}/query", status_code=202, response_model=QueryJobResponse)
async def query_agent(
    agent_id: UUID,
    body: QueryRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> QueryJobResponse:
    # Validate agent (same as documents.py lines 105-119)
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != "ready":
        raise HTTPException(status_code=409, detail=f"Agent is not ready (status={agent.status})")

    # Create job row
    job = Job(tenant_id=tenant.id, agent_id=agent.id, kind="query_agent", status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch to runtime queue
    retrieve_and_rank.apply_async(
        args=[str(job.id), str(agent.id), body.query],
        queue="runtime",
    )

    return QueryJobResponse(
        job_id=job.id,
        status="pending",
        events_url=f"/jobs/{job.id}/events",
    )
```

### Anti-Patterns to Avoid
- **`input_type="document"` for query embedding:** Silently degrades retrieval. Always use `input_type="query"` for the user's search query.
- **Passing vector as Python list without `::vector` cast:** psycopg2 sends it as a PostgreSQL array literal. pgvector needs `%(vec)s::vector` explicit cast.
- **Running FULL OUTER JOIN without COALESCE:** Both sides of the join can be NULL; `COALESCE(v.chunk_id, b.chunk_id)` is required for correct chunk identity.
- **Closing psycopg2 connection inside `with tenant_conn.cursor() as cur:`:** `with psycopg2.Connection as conn:` manages the transaction context, not the connection lifecycle. Open the connection outside `with`, close it in a `finally` block — same pattern as embed.py.
- **Calling `voyageai.Client()` at module level without lazy init:** The `_get_vo()` lazy singleton from embedding_service.py avoids the `aiohttp` hang in spawned subprocesses. Reuse `_get_vo()` from the existing module.
- **Passing connection string in Celery task args:** CLAUDE.md non-negotiable rule. Always fetch from control DB by `agent_id`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Query embedding | Custom HTTP to Voyage API | `_get_vo().embed([query], model="voyage-3", input_type="query")` | `_get_vo()` handles lazy init, pkg_resources workaround; already in codebase |
| Reranking | Sort by cosine score yourself | `_get_vo().rerank(query, documents, model="rerank-2")` | Voyage rerank uses a separate cross-encoder; cosine-only misses semantic nuance |
| BM25 scoring | Implement BM25 in Python | `ts_rank_cd(to_tsvector(...), plainto_tsquery(...))` in SQL | Native Postgres BM25 via GIN index is already indexed; zero code to maintain |
| RRF fusion | Sort in Python after two DB queries | Single SQL CTE with FULL OUTER JOIN | DB does the merge; single round-trip; avoids loading all candidates into memory |
| Event persistence + pub/sub | Write to job_events manually | `emit(job_id, event_type, payload, db, _redis)` | Existing helper handles Redis publish + DB insert atomically |
| Fernet decryption | Access env var directly | `fernet_decrypt(agent.neon_connection_string)` | M1 security helper; never bypass |

---

## Common Pitfalls

### Pitfall 1: Wrong `input_type` for Query Embedding
**What goes wrong:** Query uses `input_type="document"` (copied from `_embed_batch`). Retrieval works but quality degrades — cosine similarity between document-typed queries and document-typed chunks is lower than query-typed vs document-typed.
**Why it happens:** `embedding_service.py` hardcodes `input_type="document"` for the ingestion path. M3 is the first place the query path exists.
**How to avoid:** Use `_get_vo().embed([query], model="voyage-3", input_type="query")` inline in the task (NOT via `embed_chunks()`).
**Warning signs:** Cosine scores are uniformly lower than expected; vector-only results rank obvious matches poorly.

[VERIFIED: docs.voyageai.com/docs/embeddings — `input_type="query"` prepends a different prompt than `"document"`]

### Pitfall 2: psycopg2 Vector Literal Format
**What goes wrong:** `cur.execute(SQL, {"query_vector": query_vector})` passes a Python list. psycopg2 adapts it as `{0.1, 0.2, ...}` (PostgreSQL array syntax). pgvector needs `[0.1, 0.2, ...]` format with `::vector` cast.
**Why it happens:** psycopg2 has no native pgvector type adapter.
**How to avoid:** Use `str(query_vector)` and `%(query_vector)s::vector` in the SQL. This is the same pattern as embed.py line 235: `str(vec)`.
**Warning signs:** `ERROR: cannot cast type integer[] to vector` or similar cast errors at query time.

[VERIFIED: embed.py line 235 uses `str(vec)` for INSERT INTO embeddings; same pattern required for SELECT]

### Pitfall 3: FULL OUTER JOIN NULL Handling in RRF
**What goes wrong:** A chunk that appears in vector results but not BM25 results (or vice versa) has NULL on one side of the join. Without `COALESCE(v.rank, 0)` for the NULL-side rank contribution, the RRF formula breaks.
**Why it happens:** FULL OUTER JOIN produces NULLs on the side where no match exists.
**How to avoid:** `COALESCE(1.0 / (60.0 + v.rank), 0.0) + COALESCE(1.0 / (60.0 + b.rank), 0.0)` — when rank is NULL (chunk absent from that path), contribution is 0.0.

[VERIFIED: CONTEXT.md locked SQL uses `COALESCE(1.0 / (60 + v.rank), 0) + COALESCE(1.0 / (60 + b.rank), 0)`]

### Pitfall 4: Celery Task Module Path vs Queue Routing
**What goes wrong:** Task placed in `app.worker.tasks.pipeline.retrieve` routes to the `pipeline` queue instead of `runtime`.
**Why it happens:** `celery_app.task_routes` maps `app.worker.tasks.pipeline.*` → `pipeline`. A new task in the pipeline subpackage will be routed to the wrong queue.
**How to avoid:** Place `retrieve_and_rank` in `app.worker.tasks.runtime.retrieve` — the task_routes map `app.worker.tasks.runtime.*` → `runtime` queue automatically.
**Warning signs:** Queries pile up in the pipeline queue; pipeline workers process retrieval tasks intermixed with ingestion.

[VERIFIED: celery_app.py lines 86-89 confirm task_routes pattern]

### Pitfall 5: celery_app.include Does Not Have Runtime Tasks
**What goes wrong:** Worker fails to discover `retrieve_and_rank` at startup because the module is not in `include`.
**Why it happens:** The M1/M2 `include` list only has pipeline tasks.
**How to avoid:** Add `"app.worker.tasks.runtime.retrieve"` to `celery_app.conf.include`.

[VERIFIED: celery_app.py lines 56-63 — current include list has no runtime task module]

### Pitfall 6: Voyage Rerank `top_k` vs `final_k`
**What goes wrong:** `top_k=None` (default) returns all candidates reranked. If the fused candidates list is large, this wastes tokens and returns more results than `final_k`.
**Why it happens:** `top_k` defaults to None in `voyageai.Client.rerank`.
**How to avoid:** Always pass `top_k=strategy.final_k` explicitly.

[VERIFIED: voyageai 0.3.7 source — `top_k: Optional[int] = None` default]

### Pitfall 7: Cohere ClientV2 vs Client
**What goes wrong:** `cohere.Client()` is the old v1 API (deprecated in cohere>=5.0). `cohere.ClientV2()` is required.
**Why it happens:** Historical naming; the fallback may silently use deprecated API.
**How to avoid:** Use `cohere.ClientV2(api_key=settings.COHERE_API_KEY)` for the fallback.

[CITED: docs.cohere.com/v2/changelog/v2-api-release — ClientV2 is current]

---

## Code Examples

### RRF Math Verification (unit test anchor)

For known inputs, verify the formula produces expected output:

```python
# Two chunks: chunk_A appears rank=1 in vector, rank=2 in BM25
# chunk_B appears rank=2 in vector, rank=1 in BM25
# chunk_C appears rank=3 in vector only

# chunk_A: 1/(60+1) + 1/(60+2) = 0.016393 + 0.016129 = 0.032522
# chunk_B: 1/(60+2) + 1/(60+1) = 0.016129 + 0.016393 = 0.032522
# chunk_C: 1/(60+3) + 0         = 0.015873

# chunk_A and chunk_B tie; chunk_C is lowest
# This is the expected unit test assertion for test_rrf_math_known_ranks
```

### Notebook Polling Pattern

```python
# notebooks/demo_m3.ipynb — poll until query.complete
import time, requests

def poll_query_complete(base_url, job_id, api_key, timeout=60):
    """Poll GET /jobs/{job_id}/events until query.complete arrives."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            f"{base_url}/jobs/{job_id}/events",
            headers={"X-API-Key": api_key},
            stream=True,
            timeout=30,
        )
        for line in resp.iter_lines():
            if line and line.startswith(b"data:"):
                import json
                event = json.loads(line[5:])
                if event.get("event_type") == "query.complete":
                    return event["payload"]
        time.sleep(1)
    raise TimeoutError(f"query.complete not received within {timeout}s")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pg_search / pgbm25 extension for BM25 | Native `tsvector` + `ts_rank_cd` | Neon deprecated pg_search/pgbm25 March 2026 | All BM25 must use standard Postgres; no extension required |
| `rerank-1` / `rerank-lite-1` | `rerank-2` | Voyage rerank model upgrade | rerank-2 is the current recommended model |
| `cohere.Client()` v1 | `cohere.ClientV2()` | cohere SDK v5.0+ | ClientV2 is required for current Cohere API |

**Deprecated/outdated:**
- `pg_search` / `pgbm25` extensions: deprecated on Neon March 2026; removed from all new Neon projects
- `voyageai.get_embedding()` / `voyageai.get_embeddings()` module-level functions: still present in 0.3.7 but `Client()` instance is the correct pattern

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Cohere fallback uses `cohere.ClientV2` with `model="rerank-v3.5"` | Code Examples / Pattern 4 | Wrong model string raises API error; use `"rerank-english-v3.0"` as safe alternative |
| A2 | `cohere>=5.0,<7.0` is the correct version constraint for the fallback | Standard Stack | Version mismatch could mean different method signature |

---

## Open Questions

1. **Cohere model string for rerank fallback**
   - What we know: Cohere v2 API supports `rerank-v4.0-pro` and `rerank-english-v3.0`
   - What's unclear: Which model string is best for M3 (cost vs quality tradeoff)
   - Recommendation: Use `"rerank-english-v3.0"` for the fallback — stable, well-documented, cheaper than v4.0-pro

2. **psycopg2 vector casting with 1024-dim vector**
   - What we know: `str(vec)` works in embed.py for INSERT; same approach required for SELECT
   - What's unclear: Whether string format of a 1024-element Python list triggers any psycopg2 string length limit
   - Recommendation: Proceed with `str(query_vector)` (same as M2); test in integration test

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| voyageai | Query embedding + Voyage Rerank | ✓ | 0.3.7 | — |
| psycopg2-binary | Tenant DB RRF query | ✓ | 2.9.12 | — |
| cohere | Rerank fallback | ✗ | — | Not a blocker; Voyage is primary; add to pyproject.toml |
| PostgreSQL (local) | Integration tests | ✓ (confirmed in integration/conftest.py) | via docker-compose | — |
| Redis (local) | SSE pub/sub in tests | ✓ (confirmed in conftest.py) | via docker-compose | — |

**Missing dependencies with no fallback:** None — Voyage is primary; cohere is a fallback and only needed when Voyage raises.

**Missing dependencies with fallback:** `cohere` — add `"cohere>=5.0,<7.0"` to `pyproject.toml` dependencies.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (no version pin in pyproject.toml dev deps) |
| Config file | `apps/api/pyproject.toml` — `[tool.pytest.ini_options]` |
| Quick run command | `pytest apps/api/tests/unit/test_retrieval*.py -x` |
| Full suite command | `pytest apps/api/tests/ -m "not integration and not e2e" --cov=app --cov-report=term-missing` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RET-01 | HNSW vector search returns cosine-ranked chunks | unit (mock psycopg2) | `pytest apps/api/tests/unit/test_retrieval_task.py::test_vector_search_sql_shape -x` | ❌ Wave 0 |
| RET-02 | BM25 `ts_rank_cd` query executes against GIN index | unit (mock psycopg2) | `pytest apps/api/tests/unit/test_retrieval_task.py::test_bm25_sql_shape -x` | ❌ Wave 0 |
| RET-03 | RRF fusion produces correct scores for known ranks | unit (pure math) | `pytest apps/api/tests/unit/test_rrf_math.py -x` | ❌ Wave 0 |
| RET-04 | Voyage rerank called with correct args; Cohere fallback fires on Voyage exception | unit (mock voyageai) | `pytest apps/api/tests/unit/test_retrieval_task.py::test_voyage_rerank_called -x` | ❌ Wave 0 |
| RET-04 | Cohere fallback fires when Voyage raises | unit (mock both) | `pytest apps/api/tests/unit/test_retrieval_task.py::test_cohere_fallback_on_voyage_error -x` | ❌ Wave 0 |
| RET-05 | `RetrievalStrategy` defaults applied when `agents.retrieval_strategy={}` | unit | `pytest apps/api/tests/unit/test_retrieval_strategy.py -x` | ❌ Wave 0 |
| RET-05 | `rerank_threshold=0.5` filters out low-score results | unit | `pytest apps/api/tests/unit/test_retrieval_task.py::test_threshold_filtering -x` | ❌ Wave 0 |
| RET-06 | `query.complete` payload contains `trace` with four candidate sets | unit | `pytest apps/api/tests/unit/test_retrieval_task.py::test_query_complete_trace_shape -x` | ❌ Wave 0 |
| RET-01–06 | Full chain: embed → search → rerank → emit SSE (mocked Voyage) | integration | `pytest apps/api/tests/integration/test_retrieval_chain.py -m integration -x` | ❌ Wave 0 |
| RET-01–06 | Full chain: real Voyage + real M2 tenant DB | e2e (guarded) | `RETRIEVAL_E2E_ENABLED=1 pytest apps/api/tests/e2e/test_retrieval_e2e.py -m e2e -x` | ❌ Wave 0 |
| RET-07 | Strategy hand-written in DB; task reads it correctly | integration (fixture) | included in test_retrieval_chain.py | ❌ Wave 0 |
| RET-08 | Notebook executes without error | manual | `jupyter nbconvert --to notebook --execute notebooks/demo_m3.ipynb` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest apps/api/tests/unit/test_retrieval*.py -x`
- **Per wave merge:** `pytest apps/api/tests/ -m "not integration and not e2e" --cov=app --cov-report=term-missing`
- **Phase gate:** Full unit suite green + integration test green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `apps/api/tests/unit/test_rrf_math.py` — pure unit test for RRF formula (RET-03)
- [ ] `apps/api/tests/unit/test_retrieval_strategy.py` — RetrievalStrategy Pydantic model defaults and validation (RET-05)
- [ ] `apps/api/tests/unit/test_retrieval_task.py` — mock psycopg2 + mock voyageai task tests (RET-01, 02, 04, 05, 06)
- [ ] `apps/api/tests/integration/test_retrieval_chain.py` — real Postgres + mocked Voyage (RET-01–07); needs fixture chunks + embeddings
- [ ] `apps/api/tests/e2e/test_retrieval_e2e.py` — guarded by `RETRIEVAL_E2E_ENABLED=1` (RET-01–06)
- [ ] Integration test fixture: INSERT chunks + embeddings rows into test tenant DB (needed by test_retrieval_chain.py)

**Integration fixture pattern (from test_worker_kill pattern + integration/conftest.py):**
```python
@pytest.fixture
def retrieval_fixture(db_session, test_agent_ready):
    """Insert 5 chunks + embeddings into the tenant DB for retrieval tests."""
    # test_agent_ready: agent with status='ready', neon_connection_string set
    # Decrypt conn_str, psycopg2.connect, INSERT 5 chunks + embeddings
    # Yield the agent_id + inserted chunk_ids
    # Teardown: DELETE FROM embeddings; DELETE FROM chunks
```

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes (route) | Existing `get_current_tenant` dependency (X-API-Key) |
| V3 Session Management | no | SSE events are job-scoped, not session-scoped |
| V4 Access Control | yes | Agent ownership check: `Agent.tenant_id == tenant.id` |
| V5 Input Validation | yes | Pydantic `QueryRequest` schema; `RetrievalStrategy` with field defaults |
| V6 Cryptography | yes (existing) | `fernet_decrypt()` for connection string; NEVER bypass |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Cross-tenant data exposure | Information Disclosure | Agent ownership check in route: `Agent.tenant_id == tenant.id AND Agent.deleted_at.is_(None)` |
| Connection string in Celery task args | Information Disclosure | Never pass conn_str as task arg; fetch from control DB by agent_id |
| Query text logged verbatim | Information Disclosure | Log query hash or truncated prefix only; never log full query content |
| `retrieval_strategy` injection via JSONB | Tampering | Parsed through `RetrievalStrategy.model_validate()` before use; unknown fields ignored |
| Rerank API key exposure | Information Disclosure | `VOYAGE_API_KEY` and `COHERE_API_KEY` read from `settings`; never passed to logs |

---

## Sources

### Primary (HIGH confidence)
- `apps/api/app/services/embedding_service.py` — voyageai Client lazy init pattern, EMBEDDING_MODEL="voyage-3", input_type="document" for ingestion
- `apps/api/app/worker/tasks/pipeline/embed.py` — psycopg2 connection/execute/close pattern, str(vec) for pgvector, REINDEX CONCURRENTLY AUTOCOMMIT
- `apps/api/app/worker/tasks/pipeline/provision.py` — acks_late=True, idempotency guard, get_sync_db context manager
- `apps/api/app/worker/tasks/pipeline/migrations.py` — fernet_decrypt, get_sync_db, emit() pattern
- `apps/api/app/services/events.py` — emit() signature and side effects
- `apps/api/app/api/v1/documents.py` — FastAPI route pattern (agent validation, job creation, chain dispatch)
- `apps/api/app/worker/celery_app.py` — queue topology, task_routes, include list
- `apps/api/app/core/config.py` — Settings pattern, VOYAGE_API_KEY existing
- `apps/api/alembic/versions/0001_control_db_initial.py` — agents table DDL (no retrieval_strategy column yet)
- `apps/api/alembic/versions/0002_tenant_api_key_prefix.py` — down_revision="0001", op.add_column pattern
- `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py` — GIN index `chunks_content_tsv_idx` confirmed, HNSW index confirmed
- `apps/api/alembic_tenant/versions/0002_documents_ingestion_columns.py` — entities/chunk_entities tables confirmed
- `.planning/phases/03-hybrid-retrieval/03-CONTEXT.md` — all locked decisions
- `voyageai 0.3.7` installed library — `inspect.getsource(Client.rerank)` confirms signature; `RerankingResult._fields == ('index', 'document', 'relevance_score')`

### Secondary (MEDIUM confidence)
- docs.voyageai.com/docs/embeddings — `input_type="query"` vs `"document"` distinction confirmed
- docs.voyageai.com/docs/reranker — RerankingObject structure confirmed
- docs.cohere.com/v1/reference/rerank — Cohere v1 rerank parameters: `model`, `query`, `documents`, `top_n`; `relevance_score` return field

### Tertiary (LOW confidence)
- Cohere model string `"rerank-v3.5"` or `"rerank-english-v3.0"` for fallback — not verified against installed library (cohere not installed); use docs as reference

---

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — all installed library versions verified from pyproject.toml and `pip show`
- Architecture Patterns: HIGH — all patterns derived from existing M1/M2 codebase
- RRF SQL: HIGH — locked in CONTEXT.md; RRF math verified analytically
- Voyage Rerank API: HIGH — verified from voyageai 0.3.7 installed source
- Cohere Fallback: MEDIUM — API shape verified from docs; model string ASSUMED
- Pitfalls: HIGH — derived from existing codebase patterns and verified schemas

**Research date:** 2026-05-16
**Valid until:** 2026-06-16 (30 days — voyageai 0.3.7 is pinned; no fast-moving external dependencies)
