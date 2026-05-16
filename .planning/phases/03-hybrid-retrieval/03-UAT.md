---
phase: 3
slug: hybrid-retrieval
status: approved
verified_by: human
verified_at: 2026-05-16
session: resume
---

# UAT — M3 Hybrid Retrieval

## Verification Method

Human checkpoint: user ran `scripts/demo_m3.sh` + `notebooks/demo_m3.ipynb` against a real M2 tenant DB and confirmed all gates passed.

## Success Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | Query returns ranked chunks with full trace (path matched, fusion scores, rerank deltas) | PASS | `query.complete` payload contains `trace` with `vector_candidates`, `bm25_candidates`, `fused_candidates`, `reranked_candidates` |
| 2 | Vector-only, keyword-only, and hybrid queries return meaningfully different candidate sets | PASS | Cell 8 printed divergence confirmation; top-5 overlap < 5 |
| 3 | Retrieval strategy (k values, rerank threshold, expansion flag) changeable via JSONB config with no code changes | PASS | `RetrievalStrategy` Pydantic model with defaults; stored in `agents.retrieval_strategy` JSONB |
| 4 | Jupyter notebook shows candidates at each stage on real query against M2 tenant DB | PASS | All 4 DataFrames non-empty: `vector_candidates` (cosine_score), `bm25_candidates` (bm25_score), `fused_candidates` (rrf_score), `reranked` (rerank_delta) |

## Demo Script Result

```
bash scripts/demo_m3.sh
→ === M3 Demo: PASSED ===   (exit 0)
```

## Requirement Coverage

| Requirement | Satisfied By | Status |
|-------------|-------------|--------|
| RET-01 (vector search HNSW) | `retrieval_service.vector_search()` + `retrieve_and_rank` task | PASS |
| RET-02 (BM25 native tsvector) | `retrieval_service.bm25_search()` — no pg_search/pgbm25 | PASS |
| RET-03 (RRF fusion k=60) | `retrieval_service.rrf_fuse()` SQL CTE | PASS |
| RET-04 (Voyage rerank + Cohere fallback) | `retrieval_service.rerank()` — rerank-2 + ClientV2 fallback | PASS |
| RET-05 (per-tenant JSONB strategy) | `agents.retrieval_strategy` + migration 0003 | PASS |
| RET-06 (full retrieval trace in query.complete) | `build_trace()` + task payload | PASS |
| RET-07 (Celery runtime queue, acks_late, idempotency) | `retrieve_and_rank` task | PASS |
| RET-08 (demo notebook 4 stages) | `notebooks/demo_m3.ipynb` — human verified | PASS |

## Unit/Integration Test Coverage

| Suite | Count | Status |
|-------|-------|--------|
| `tests/unit/retrieval/test_retrieval_service.py` | 23 | All passing |
| `tests/unit/retrieval/test_retrieve_task.py` | 10 | All passing |
| `tests/integration/test_query_route.py` | 3 | All passing |
| `tests/e2e/test_retrieval_e2e.py` | 1 | Guarded (RETRIEVAL_E2E_ENABLED=1) |

## Verdict

**APPROVED** — all M3 success criteria met, all RET-01–RET-08 requirements satisfied, human checkpoint passed.
