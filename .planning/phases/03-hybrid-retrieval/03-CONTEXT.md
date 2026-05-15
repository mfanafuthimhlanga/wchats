# Phase 3: M3 Hybrid Retrieval — Context

**Gathered:** 2026-05-15
**Status:** Ready for planning
**Source:** PRD Express Path (prd.md §M3 + prd.md Layer 5 + REQUIREMENTS.md RET-01–RET-08)

<domain>
## Phase Boundary

M3 implements the retrieval engine that M4's reasoning agent will call. It is entirely
programmatic — no Claude agents, no LLM calls at retrieval time (query embedding and Voyage
rerank are external API calls, not agents). M3 proves retrieval quality before the agent
is built, which is the gate that makes M4 defensible.

Deliverables:
- Vector search (pgvector HNSW) + BM25 (`tsvector` + `ts_rank_cd`) running in parallel
- Reciprocal Rank Fusion via SQL CTE
- Voyage Rerank (`rerank-2`) as primary; Cohere Rerank as fallback
- Per-tenant retrieval strategy stored as JSONB on the `agents` row (control DB)
- Celery `retrieve_and_rank` task on the `runtime` queue
- `POST /agents/{id}/query` FastAPI route → 202 + job_id
- Full retrieval trace in the `query.complete` SSE event payload
- Alembic control-DB migration: `0003_agent_retrieval_strategy.py`
- Jupyter demo notebook: `notebooks/demo_m3.ipynb`

This phase does NOT include:
- `verified_qa` lookup (table empty until M6; lookup path deferred)
- LLM-based query expansion (deferred to M9 / M4)
- Auto-generated retrieval strategies (M9)
- Admin UI exposure of retrieval config (M4+)

</domain>

<decisions>
## Implementation Decisions

### Schema — Control DB Migration (0003)

**Locked:** Single Alembic control-DB migration: `apps/api/alembic/versions/0003_agent_retrieval_strategy.py`

Adds to `agents`:
```sql
ALTER TABLE agents ADD COLUMN retrieval_strategy JSONB NOT NULL DEFAULT '{}'::jsonb;
```

Default strategy JSON shape (used when column is `{}`):
```json
{
  "vector_k": 20,
  "bm25_k": 20,
  "final_k": 5,
  "rerank_threshold": 0.0,
  "query_expansion": false,
  "metadata_filters": []
}
```

**No tenant DB migration required.** The BM25 GIN index (`chunks_content_tsv_idx`) and HNSW vector
index (`embeddings_vector_hnsw_idx`) were created in `0001_tenant_v1_schema.py`. They are already present.

### BM25 Implementation (RET-02) — LOCKED

**No pg_search, no pgbm25.** Deprecated on Neon March 2026 (CLAUDE.md constraint).
Use only native Postgres `tsvector` + `ts_rank_cd`.

Query pattern:
```sql
SELECT c.id, c.content, c.document_id,
       ts_rank_cd(to_tsvector('english', c.content), plainto_tsquery('english', :query)) AS bm25_score
FROM chunks c
WHERE to_tsvector('english', c.content) @@ plainto_tsquery('english', :query)
ORDER BY bm25_score DESC
LIMIT :bm25_k
```

The existing GIN index `chunks_content_tsv_idx ON chunks USING GIN (to_tsvector('english', content))`
is used automatically by this query.

### Vector Search Implementation (RET-01) — LOCKED

Query embeds the user query via Voyage (`voyage-3` — same pinned model as M2) then searches:
```sql
SELECT e.chunk_id, c.content, c.document_id,
       1 - (e.vector <=> :query_vector::vector) AS cosine_score
FROM embeddings e
JOIN chunks c ON c.id = e.chunk_id
ORDER BY e.vector <=> :query_vector::vector
LIMIT :vector_k
```

Existing HNSW index `embeddings_vector_hnsw_idx` is used automatically.

Voyage query embedding model: `voyage-3` (same as ingestion — MUST match to avoid dimension mismatch).
Embed via `voyageai.Client().embed([query], model="voyage-3", input_type="query").embeddings[0]`.

### RRF Fusion (RET-03) — LOCKED

Single SQL CTE combining vector and BM25 ranked results. RRF formula: `1 / (60 + rank)`.

```sql
WITH vector_ranked AS (
    -- vector search results with rank
),
bm25_ranked AS (
    -- bm25 search results with rank
),
fused AS (
    SELECT
        COALESCE(v.chunk_id, b.chunk_id) AS chunk_id,
        COALESCE(v.content, b.content) AS content,
        COALESCE(v.document_id, b.document_id) AS document_id,
        COALESCE(1.0 / (60 + v.rank), 0) + COALESCE(1.0 / (60 + b.rank), 0) AS rrf_score,
        v.cosine_score,
        b.bm25_score,
        v.rank AS vector_rank,
        b.rank AS bm25_rank
    FROM vector_ranked v
    FULL OUTER JOIN bm25_ranked b ON v.chunk_id = b.chunk_id
)
SELECT * FROM fused ORDER BY rrf_score DESC LIMIT :final_k
```

The full CTE is executed as a single psycopg2 query against the tenant DB.

### Voyage Rerank (RET-04) — LOCKED

After RRF fusion, the `final_k` candidates are passed to Voyage Rerank:
```python
result = voyageai.Client().rerank(
    query=query,
    documents=[c["content"] for c in fused_candidates],
    model="rerank-2",
    top_k=final_k,
)
```

**Cohere Rerank fallback:** If Voyage raises an exception, fall back to `cohere.Client().rerank(...)`.
`COHERE_API_KEY` added to Settings (optional — only required if Voyage rerank fails).
Fallback logs a warning via structlog; does not raise to the caller.

`rerank_threshold` in strategy config: minimum score to include a result. Applied after reranking.
Default 0.0 (no filtering). Higher threshold yields fewer, higher-confidence results.

### Retrieval Strategy (RET-05) — LOCKED

Stored as JSONB on `agents.retrieval_strategy` (control DB). Retrieved at task runtime alongside
the connection string. Never passed as Celery task arguments.

Strategy fields (all optional with defaults):
```python
class RetrievalStrategy(BaseModel):
    vector_k: int = 20        # candidates from HNSW search
    bm25_k: int = 20          # candidates from BM25 search
    final_k: int = 5          # results after rerank
    rerank_threshold: float = 0.0  # min rerank score to include
    query_expansion: bool = False  # deferred to M9
    metadata_filters: list[dict] = []  # entity-based filters (M4+)
```

For M3, strategies are set by writing directly to `agents.retrieval_strategy` via the existing
`PATCH /agents/{id}` endpoint or directly in the DB during demo setup.

### Celery Task Contract (RET-01–RET-06) — LOCKED

New task: `retrieve_and_rank` on the `runtime` queue.

```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=2,
    queue="runtime",
    name="retrieve_and_rank",
)
def retrieve_and_rank(self, job_id: str, agent_id: str, query: str) -> dict:
    ...
```

Task args: `job_id`, `agent_id`, `query` — NO connection strings, NO API keys.
Connection string fetched+decrypted from control DB by `agent_id` (M1/M2 pattern).
Retrieval strategy fetched from `agents.retrieval_strategy` at runtime.

SSE events emitted:
- `query.started` — task begins
- `query.embedding` — query embedded (Voyage call complete)
- `query.searching` — parallel vector+BM25 in progress
- `query.reranking` — Voyage rerank call complete
- `query.complete` — results + full trace in payload

`query.complete` payload structure:
```json
{
  "query": "...",
  "results": [
    {
      "chunk_id": "uuid",
      "content": "...",
      "document_id": "uuid",
      "rerank_score": 0.95,
      "rrf_score": 0.041,
      "cosine_score": 0.87,
      "bm25_score": 0.12,
      "vector_rank": 2,
      "bm25_rank": 5
    }
  ],
  "trace": {
    "vector_candidates": [...],
    "bm25_candidates": [...],
    "fused_candidates": [...],
    "reranked_candidates": [...]
  },
  "strategy_used": {...}
}
```

Idempotency: job_id is unique; if `query.complete` already emitted for this job, return early.

### FastAPI Route (RET-01–RET-06) — LOCKED

New router: `apps/api/app/api/v1/query.py`

```
POST /agents/{agent_id}/query
  Body: {"query": "...", "filters": [...]}  (filters optional, M4+ use)
  → 202 {"job_id": "uuid", "status": "pending", "events_url": "/jobs/{job_id}/events"}

GET  /agents/{agent_id}/queries
  → 200 list of past query jobs for this agent
```

Validation: agent exists + belongs to tenant + status = 'ready' (same pattern as documents.py).
Job kind: `'query_agent'`.
Dispatches: `retrieve_and_rank.apply_async(args=[job_id, agent_id, query], queue="runtime")`.

### Full Retrieval Trace (RET-06) — LOCKED

The `query.complete` event payload includes `trace` with four candidate sets:
- `vector_candidates`: top `vector_k` from HNSW search with cosine scores
- `bm25_candidates`: top `bm25_k` from BM25 search with ts_rank_cd scores
- `fused_candidates`: top `final_k` from RRF with fusion scores + individual ranks
- `reranked_candidates`: final results after Voyage rerank, sorted by rerank score

Each candidate includes `chunk_id`, `content` (truncated to 200 chars for trace), `document_id`,
and all available scores. Full content in `results` only.

This satisfies RET-06: "which path matched, fusion scores, rerank deltas" is visible in trace.

### Demo Notebook (RET-08) — LOCKED

`notebooks/demo_m3.ipynb`:
1. Setup cell: connect to control DB, pick an agent with `status='ready'` and M2 data
2. POST /agents/{id}/query → capture job_id
3. Poll until `query.complete` event arrives
4. Display `vector_candidates` as a dataframe
5. Display `bm25_candidates` as a dataframe
6. Display `fused_candidates` with RRF scores as a dataframe
7. Display `reranked_candidates` (final answer) as a dataframe with rerank delta vs RRF
8. Assert vector-only, BM25-only, and hybrid produce meaningfully different candidate sets

Notebook has no hardcoded secrets; reads from `.env` via `python-dotenv`.

### Testing Strategy

- **Unit:** Mock psycopg2 (tenant DB), mock Voyage client (embed + rerank), test RRF math,
  test strategy defaults/merging, test `rerank_threshold` filtering.
- **Integration:** Real local Postgres with `chunks` + `embeddings` fixture data,
  mocked Voyage via unittest.mock, `CELERY_TASK_ALWAYS_EAGER=True`.
- **E2E (RETRIEVAL_E2E_ENABLED=1):** Real Voyage API + real M2 tenant DB.
  Must be guarded (not run by default in CI).

### Celery Queues (from CLAUDE.md)

- All ingestion/build tasks: `pipeline` queue (unchanged from M1/M2)
- `retrieve_and_rank`: `runtime` queue — **query execution is a runtime operation**
- No changes to M1/M2 queue routing

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### PRDs
- `prd.md` — Full PRD; §M3 "Hybrid retrieval" for milestone scope; Layer 5 "Retrieval engine" for exact pipeline design (verified_qa lookup is Layer 4/M6 concern — skip for M3)
- `.planning/REQUIREMENTS.md` — RET-01 through RET-08 (all 8 must appear in plan requirements fields)
- `.planning/ROADMAP.md` — M3 milestone, success criteria

### Phase 3 Context
- `.planning/phases/03-hybrid-retrieval/03-CONTEXT.md` — this file (locked decisions)

### Prior Phases (read for patterns, do not duplicate)
- `.planning/phases/02-ingestion-pipeline/02-CONTEXT.md` — exact tenant DB schema state after M2; entity extraction decisions; M2 wave structure as precedent
- `.planning/phases/01-control-plane-skeleton/01-CONTEXT.md` — M1 patterns for Celery, SSE, emit()

### M1/M2 Codebase (pattern source — read before implementing)
- `apps/api/app/worker/tasks/pipeline/provision.py` — `acks_late=True`, idempotency guard pattern
- `apps/api/app/worker/tasks/pipeline/migrations.py` — tenant DB connection fetch+decrypt pattern
- `apps/api/app/worker/tasks/pipeline/embed.py` — voyageai client pattern (already in codebase from M2)
- `apps/api/app/services/events.py` — emit() helper (reuse for query.* events)
- `apps/api/app/core/config.py` — Settings pattern (add COHERE_API_KEY as Optional[str])
- `apps/api/alembic/versions/0001_control_db_initial.py` — agents table DDL (read before writing 0003)
- `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py` — chunks/embeddings/GIN index DDL (confirms indexes exist, no re-creation needed)
- `apps/api/alembic_tenant/versions/0002_documents_ingestion_columns.py` — M2 migration pattern
- `apps/api/app/api/v1/documents.py` — route pattern for new query.py router
- `apps/api/app/worker/celery_app.py` — queue definitions, SSL config

### CLAUDE.md Constraints (enforced in all plans)
- No pg_search / pgbm25 (native tsvector + ts_rank_cd only)
- `acks_late=True` AND idempotency on every Celery task
- Connection strings never in Celery task args
- FastAPI never does work inline
- Langfuse v4 API if observability added (not required in M3 plans)

</canonical_refs>

<specifics>
## Specific Ideas

### BM25 vs Vector Comparison (RET-08 notebook requirement)

The demo notebook MUST show "meaningfully different candidate sets" (ROADMAP.md M3 success
criteria #2). For a typical business document, vector-only will rank semantically similar chunks
high even when keywords don't match; BM25 will rank chunks containing exact query terms
high even when semantically different. A query like "What is the refund policy?" typically
has BM25 ranking the policy section #1 while vector may rank FAQ chunks about refunds higher.
The notebook should pick a query that demonstrates this divergence clearly.

### Voyage Rerank Model

Use `rerank-2` (not `rerank-1` or `rerank-lite-1`). Verify available models via
`voyageai.Client().rerank(...)` at implementation time if uncertain. The `voyageai` library
version installed in M2 should support `rerank-2`.

### psycopg2 for Tenant DB

M2 tasks use psycopg2 (sync) for tenant DB writes. M3 retrieval task follows the same pattern
— psycopg2 for the RRF SQL CTE (complex multi-result query). Do not introduce asyncpg for
tenant DB access in M3 (keep consistency with M2 pattern).

### RRF k=60 constant

The RRF constant `k=60` is the Elasticsearch default and is appropriate for this use case.
It provides a balanced fusion that does not over-weight the top result from either path.
This is a locked implementation detail — do not parameterize it.

</specifics>

<deferred>
## Deferred Ideas

- `verified_qa` lookup before hybrid search — M6 (table exists but is empty until M6 seeds it)
- LLM-based query expansion — M9 (strategy field `query_expansion` added but always `false` in M3)
- Entity-based metadata filters — M4+ (field `metadata_filters` added but empty in M3)
- Auto-generated retrieval strategies — M9 (strategies hand-written per tenant in M3)
- Cohere Rerank as primary (not just fallback) — decision revisited in M9 strategy synthesis
- `PATCH /agents/{id}/retrieval_strategy` dedicated endpoint — not required for M3 demo; direct DB write in notebook setup is sufficient
- Admin UI exposure of retrieval trace — M4
- Streaming retrieval results via SSE (current design: batch results in query.complete event) — future UX improvement

</deferred>

---

*Phase: 03-hybrid-retrieval*
*Context gathered: 2026-05-15 via PRD Express Path (prd.md §M3 + prd.md Layer 5 + REQUIREMENTS.md)*
