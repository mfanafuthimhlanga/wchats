# Plan 02 Summary — retrieval_service primitives

**Phase:** 03-hybrid-retrieval
**Plan:** 02
**Status:** Complete
**Commit:** `feat(03-02): retrieval_service — RetrievalStrategy, vector/BM25/RRF/rerank primitives`

---

## File Created

`apps/api/app/services/retrieval_service.py`

---

## Functions Exported

| Function | Signature | Purpose |
|----------|-----------|---------|
| `RetrievalStrategy` | `class RetrievalStrategy(BaseModel)` | Pydantic config model for per-tenant retrieval parameters |
| `embed_query` | `(query_text: str) -> list[float]` | Voyage voyage-3 query embedding with `input_type="query"` |
| `vector_search` | `(conn_str: str, query_vector: list[float], vector_k: int) -> list[dict]` | pgvector HNSW cosine search |
| `bm25_search` | `(conn_str: str, query_text: str, bm25_k: int) -> list[dict]` | Native tsvector + ts_rank_cd search |
| `rrf_fuse` | `(conn_str: str, query_vector: list[float], query_text: str, strategy: RetrievalStrategy) -> dict` | Full RRF CTE + individual candidate lists |
| `rerank` | `(query_text: str, candidates: list[dict], strategy: RetrievalStrategy) -> list[dict]` | Voyage rerank-2 primary; Cohere fallback |
| `build_trace` | `(vector_candidates, bm25_candidates, fused_candidates, reranked_candidates, max_content=200) -> dict` | Trace dict with truncated content for SSE payload |

---

## Key Implementation Decisions

### `rrf_fuse` returns dict with 3 keys
`rrf_fuse` returns `{"fused": list[dict], "vector_candidates": list[dict], "bm25_candidates": list[dict]}`.
This design lets Plan 03 (`retrieve_and_rank` task) include all three candidate sets in the `query.complete`
trace without executing additional queries. The RRF CTE runs once; individual searches run separately for
the trace only.

### RRF k=60 is a SQL literal
The constant `60.0` appears directly in the `_RRF_SQL` string as `COALESCE(1.0 / (60.0 + v.rank), 0.0)`.
It is NOT a psycopg2 parameter. This is locked per CONTEXT.md.

### psycopg2 try/finally/close pattern
All three DB-hitting functions (`vector_search`, `bm25_search`, `rrf_fuse`) use:
```python
conn = psycopg2.connect(conn_str)
try:
    with conn.cursor() as cur:
        ...
finally:
    conn.close()
```
This matches the embed.py pattern and avoids implicit transaction wrapping from using
the connection as a context manager.

### `input_type="query"` for embed_query
`embed_query` calls `_get_vo().embed(..., input_type="query")`. The ingestion path uses
`input_type="document"`. Using the wrong type silently degrades retrieval quality.

### Cohere import is lazy
`import cohere` appears inside `_cohere_rerank()` body only. This keeps cohere optional
at module load time — it is a fallback dependency that need not be installed unless
Voyage rerank fails.

### No deprecated Neon extensions
The BM25 implementation uses only native `tsvector` + `ts_rank_cd`. No deprecated
extensions are referenced anywhere in the file.

---

## Verification Results

```
python -c "from app.services.retrieval_service import ..."
# → all exports OK

python -c "import inspect, app.services.retrieval_service as rs; ..."
# → no pg_search/pgbm25, k=60.0 present, input_type present OK

pytest tests/unit/retrieval/test_retrieval_service.py -x -q
# → 23 passed in 1.26s
```

---

## Tests Written

`apps/api/tests/unit/retrieval/test_retrieval_service.py` — 23 tests covering:

- `RetrievalStrategy` defaults, partial override, extra field ignore
- `embed_query` uses `input_type="query"`, returns index [0]
- `vector_search` returns cosine-scored dicts, closes connection in finally (including on exception)
- `bm25_search` returns bm25-scored dicts, closes connection in finally
- `rrf_fuse` returns 3-key dict, fused row structure, k=60 present as SQL literal
- `rerank` calls Voyage with correct args, threshold filtering, sort order, score key added
- `rerank` Cohere fallback fires on Voyage exception
- `build_trace` 4-key structure, 200-char truncation, custom max_content, no mutation of originals
