# 09-01 SUMMARY — Retrieval Strategy Synthesis: Foundation

## What was built

### 1. `apps/api/app/services/strategy_service.py`
Modeled exactly on `deployment_service.py`. Contains:
- `_fetch_corpus_signals_sync(agent_id, conn_str)` — four psycopg2 queries (chunk volume/size, table ratio, entity count, doc types). Uses `try/finally/conn.close()` pattern. Defensive `int(x or 0)` / `float(x or 0)` coercion on all aggregates. Connection string never logged.
- `_STRATEGIST_SYSTEM_PROMPT` — encodes all 6 heuristics: chunk_count buckets → vector_k/bm25_k; avg_chunk_len ranges → rerank_threshold/query_expansion; table_ratio/entity_count adjustments; final_k = min(5, vector_k // 4).
- `_TOOL_GENERATE_STRATEGY` — tool schema with all 6 RetrievalStrategy fields in `required`.
- `_run_strategist_loop(signals_json, result_container)` — async Agent SDK loop (max_turns=3) that captures `generate_strategy` ToolUseBlock into result_container as a side-effect.
- `run_strategist(signals_json, result_container)` — synchronous bridge: `asyncio.run(asyncio.wait_for(..., timeout=60.0))`, swallows exceptions with `log.warning`.

### 2. `apps/api/app/services/retrieval_service.py` + `apps/api/app/worker/celery_app.py`
- Removed M3 deferral comment from `query_expansion: bool = False` field.
- Added `_expand_query(query_text)` — lazy `import anthropic` inside function body; calls `claude-haiku-4-5` for 2 alternative phrasings; returns `[original] + variants[:2]`.
- Added `rrf_fuse_with_expansion(conn_str, query_vector, query_text, strategy)` — passthrough when `query_expansion=False`; otherwise batch-embeds all variants in ONE Voyage call, runs rrf_fuse per variant, merges keeping highest rrf_score per chunk_id, returns top `final_k`.
- Registered `"app.worker.tasks.pipeline.strategy"` in celery_app.py include list (after M8 deployment entry).

### 3. Wave-0 xfail test stubs (9 stubs total)
- `tests/unit/test_strategy_service.py` — 5 stubs: `test_corpus_signals_shape`, `test_strategy_validate_string_inputs`, `test_run_strategist_calls_asyncio_run`, `test_expand_query_returns_three`, `test_expansion_calls_rrf_fuse_per_variant`
- `tests/unit/test_strategy_task.py` — 4 stubs: `test_strategy_written_to_db`, `test_receives_embed_result_dict`, `test_idempotency_skip`, `test_resynthesis_flag_bypasses_guard`
- All decorated `@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")`
- task stubs guard import inside test body to handle missing module gracefully

## Verification results

### Import check (Task 1)
```
cd apps/api && python -c "from app.services.strategy_service import ..."
ok
```

### File content check (Task 2)
```
cd apps/api && python -c "... assert 'rrf_fuse_with_expansion' in src ..."
ok
```

### pytest xfail count (Task 3)
```
9 xfailed in 3.35s
```

## Commit SHAs
- `c8541e0` — feat(09-01): add strategy_service.py
- `337c21f` — feat(09-01): close M3 deferral — query expansion path + celery registration
- `a455ebf` — test(09-01): Wave-0 xfail stubs (9 stubs)

## Deviations from plan
None. All 3 tasks implemented exactly as specified.
