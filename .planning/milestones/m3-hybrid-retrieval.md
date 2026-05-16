# M3 — Hybrid Retrieval: Milestone Archive

**Completed:** 2026-05-16  
**Commits:** 15 (019b591 → 7e2685e)  
**Files changed:** 20 files, +2981 / -29 lines  
**Requirements:** RET-01 through RET-08 — all 8 satisfied  
**Human checkpoint:** PASSED — `demo_m3.sh` exit 0, notebook 4 DataFrames verified, Cell 8 divergence confirmed  

---

## What Was Shipped

A configurable retrieval engine combining pgvector HNSW, native BM25 (tsvector + ts_rank_cd), RRF fusion, and Voyage reranking — with full per-stage trace visibility and a demo notebook proving retrieval quality before the agent is built.

### Core deliverables

| Artifact | Description |
|----------|-------------|
| `apps/api/alembic/versions/0003_agent_retrieval_strategy.py` | Control DB migration — adds `retrieval_strategy JSONB` to agents |
| `apps/api/app/services/retrieval_service.py` | All retrieval primitives: `embed_query`, `vector_search`, `bm25_search`, `rrf_fuse`, `rerank`, `build_trace`, `RetrievalStrategy` |
| `apps/api/app/worker/tasks/runtime/retrieve.py` | `retrieve_and_rank` Celery task — runtime queue, acks_late=True, 5 SSE events, idempotency guard |
| `apps/api/app/api/v1/query.py` | `POST /agents/{id}/query` (202) + `GET /agents/{id}/queries` (200) |
| `apps/api/app/schemas/query.py` | `QueryRequest`, `QueryJobResponse`, `QueryJobItem`, `QueryListResponse` |
| `notebooks/demo_m3.ipynb` | 9-cell portfolio notebook — 4 candidate-set DataFrames + divergence assertion |
| `scripts/demo_m3.sh` | Shell smoke test — POSTs query, polls query.complete, exits 0 |

### Test coverage

| Suite | Count | Status |
|-------|-------|--------|
| `tests/unit/retrieval/test_retrieval_service.py` | 23 | All passing |
| `tests/unit/retrieval/test_retrieve_task.py` | 10 | All passing |
| `tests/integration/test_query_route.py` | 3 | All passing |
| `tests/e2e/test_retrieval_e2e.py` | 1 | Guarded (RETRIEVAL_E2E_ENABLED=1) |

---

## Requirements Satisfied

| Requirement | Implementation |
|-------------|---------------|
| RET-01 | pgvector HNSW cosine search via `vector_search()`, `voyage-3` query embedding |
| RET-02 | Native `tsvector` + `ts_rank_cd` BM25 — no pg_search/pgbm25 |
| RET-03 | RRF fusion SQL CTE with k=60 constant in `rrf_fuse()` |
| RET-04 | Voyage `rerank-2` primary; Cohere ClientV2 lazy-import fallback |
| RET-05 | `RetrievalStrategy` Pydantic model stored as JSONB on `agents.retrieval_strategy` |
| RET-06 | Full 4-stage trace (`vector_candidates`, `bm25_candidates`, `fused_candidates`, `reranked_candidates`) in `query.complete` payload |
| RET-07 | `retrieve_and_rank` task: `acks_late=True`, `queue="runtime"`, idempotency guard, max_retries=3 |
| RET-08 | `notebooks/demo_m3.ipynb` — human verified against real M2 tenant DB, 4 DataFrames non-empty, divergence confirmed |

---

## Key Decisions

- `rrf_fuse()` returns `dict` with 3 keys (`fused`, `vector_candidates`, `bm25_candidates`) — not a plain list — so the task can build the full trace without a second DB round-trip
- Integration tests mock `retrieve_and_rank.apply_async` (not the task body) since integration conftest sets `CELERY_TASK_ALWAYS_EAGER="False"`
- RRF k=60 is a hardcoded SQL literal, not a parameter — matches Elasticsearch default
- psycopg2 (sync) for all tenant DB access — consistent with M2 pattern, no asyncpg introduced
- `input_type="query"` (not "document") for Voyage embedding — different path from M2 ingestion

---

## Phase Breakdown

| Wave | Plan | Objective | Commit |
|------|------|-----------|--------|
| 1 | 03-01 | Migration 0003, COHERE_API_KEY, Agent ORM, Wave 0 stubs | 019b591 |
| 2 | 03-02 | retrieval_service.py — all 7 primitives | 87deea6 |
| 3 | 03-03 | retrieve_and_rank Celery task | 278e4ee |
| 4 | 03-04 | FastAPI query router (POST + GET) | 9d85978, a52e4bc |
| 5 | 03-05 | Unit tests (already green from Waves 2/3) | — |
| 6 | 03-06 | Integration tests (3) + guarded E2E | 56bd4b3 |
| 7 | 03-07 | demo_m3.ipynb + demo_m3.sh | 77f0fe5 |

---

## Success Criteria Verification

| Criterion | Result |
|-----------|--------|
| Query returns ranked chunks with full trace | PASS |
| Vector-only, keyword-only, hybrid produce meaningfully different sets | PASS — Cell 8 divergence confirmed |
| Strategy changeable via JSONB with no code changes | PASS |
| Notebook shows candidates at each stage against real M2 tenant DB | PASS |
