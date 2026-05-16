# Plan 05 Summary — Unit Tests: 33 Retrieval Tests Green

## Status
COMPLETE — no changes needed. Prior Wave 3 agents had already replaced all xfail stubs with real, passing tests.

## Test Results

### `tests/unit/retrieval/` — 33 passed, 0 failed, 0 xfail

**`test_retrieval_service.py` (23 tests)**

| Class | Test | Verifies |
|---|---|---|
| TestRetrievalStrategy | test_defaults_from_empty_dict | All 6 default field values |
| TestRetrievalStrategy | test_partial_override | Partial overrides leave other defaults intact |
| TestRetrievalStrategy | test_extra_fields_ignored | extra="ignore" on unknown keys |
| TestEmbedQuery | test_uses_query_input_type | input_type="query" passed to Voyage embed |
| TestEmbedQuery | test_returns_first_embedding | Returns embeddings[0] only |
| TestVectorSearch | test_returns_cosine_scored_dicts | Dict shape: chunk_id, content, document_id, cosine_score, rank |
| TestVectorSearch | test_connection_closed_in_finally | psycopg2 conn.close() called in finally block |
| TestVectorSearch | test_connection_closed_on_exception | conn.close() called even when execute raises |
| TestBM25Search | test_returns_bm25_scored_dicts | Dict shape with bm25_score key |
| TestBM25Search | test_connection_closed_in_finally | psycopg2 conn.close() in finally block |
| TestRRFFuse | test_returns_three_key_dict | Returns dict with "fused", "vector_candidates", "bm25_candidates" |
| TestRRFFuse | test_fused_row_structure | Fused row has rrf_score, cosine_score, bm25_score, vector_rank, bm25_rank |
| TestRRFFuse | test_rrf_math_k60_formula | 60.0 is a SQL literal, not a parameter |
| TestRerank | test_voyage_rerank_called_with_correct_args | model="rerank-2", top_k, truncation=True |
| TestRerank | test_rerank_threshold_filters_results | Results below threshold excluded |
| TestRerank | test_cohere_fallback_on_voyage_exception | _cohere_rerank called when Voyage raises |
| TestRerank | test_result_sorted_descending_by_rerank_score | Output sorted by rerank_score DESC |
| TestRerank | test_rerank_score_added_to_dict | rerank_score key added, original keys preserved |
| TestBuildTrace | test_returns_four_key_dict | Keys: vector_candidates, bm25_candidates, fused_candidates, reranked_candidates |
| TestBuildTrace | test_content_truncated_to_200_chars | Content > 200 chars truncated to exactly 200 |
| TestBuildTrace | test_short_content_not_truncated | Content <= 200 chars passed through unchanged |
| TestBuildTrace | test_custom_max_content | max_content parameter controls truncation limit |
| TestBuildTrace | test_original_candidates_not_mutated | Input lists not modified in-place |

**`test_retrieve_task.py` (10 tests)**

| Test | Verifies |
|---|---|
| test_retrieve_and_rank_acks_late | acks_late is True (CLAUDE.md non-negotiable) |
| test_retrieve_and_rank_queue | queue == "runtime" |
| test_retrieve_and_rank_task_name | name == "retrieve_and_rank" |
| test_retrieve_and_rank_max_retries | max_retries == 3 |
| test_retrieve_and_rank_signature | (job_id, agent_id, query) — no conn_str/connection_string |
| test_retrieve_and_rank_idempotent | Returns {} immediately if query.complete event row exists |
| test_retrieve_and_rank_agent_not_found | Returns {} gracefully when agent not found |
| test_retrieve_and_rank_job_not_found | Returns {} gracefully when job not found |
| test_retrieve_and_rank_happy_path | 5 SSE events in order, job.status="complete", returns {} |
| test_celery_app_includes_runtime_retrieve | runtime.retrieve in celery_app.conf.include |

## Prior Agent Work

The Wave 3 agent executing Plans 02/03 had already replaced all xfail stubs with real, comprehensive tests. The test file contained far more coverage than the 6 stubs originally called for in Plan 01 — 23 service tests and 10 task tests instead of 4+2.

## Full Unit Suite Status

- Retrieval tests: **33/33 passed**
- Full unit suite (excluding retrieval): 5 pre-existing failures unrelated to retrieval
  - `test_chunking_service.py` — HybridChunker attribute missing
  - `test_docling_service.py` — DocumentStream mock issue
  - `test_parse_task.py` — parse task mock issues
  - `test_services.py` — Neon project creation mock issues

These 5 failures pre-date Plan 05 and are unrelated to M3 hybrid retrieval work.
