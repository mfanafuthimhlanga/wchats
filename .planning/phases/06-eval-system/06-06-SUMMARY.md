---
phase: "06"
plan: "06-06"
title: "retrieval_service.py — verified_qa_lookup before hybrid search"
status: complete
completed: 2026-05-23
commits:
  - cec0ed3  # feat(06-06): add verified_qa_lookup to retrieval_service.py
  - f839241  # feat(06-06): wire verified_qa_lookup into retrieve_and_rank task
  - adb9792  # test(06-06): unit tests for verified_qa_lookup hit/miss paths + task wiring
---

## What Was Done

### Task 1 — verified_qa_lookup() in retrieval_service.py

Added `verified_qa_lookup(conn_str, query_vector, threshold) -> Optional[dict]` to
`apps/api/app/services/retrieval_service.py`, inserted after `embed_query` and before
`vector_search`, exactly as specified by D-24.

Key implementation details:
- SQL lookup uses `1 - (question_vector <=> %(qv)s::vector) >= %(threshold)s` for cosine
  filtering (D-25) and `WHERE invalidated_at IS NULL` to skip invalidated rows.
- On hit: UPDATE executes in the same psycopg2 cursor to set `last_used_at = NOW()` and
  increment `use_count = use_count + 1` (D-26), then `conn.commit()`.
- On miss: `log.debug("verified_qa_lookup.miss")` followed by `return None` (D-27).
- Returns `{"answer", "citations", "similarity": float(similarity), "source": "verified_qa_cache"}` on hit.
- Matches existing `psycopg2.connect(conn_str)` + `try/finally/conn.close()` pattern from
  `vector_search` and `bm25_search` — no context-manager deviation.
- `str(query_vector)` cast to `%(qv)s::vector` matches the existing `str(vec)::vector`
  pattern used throughout retrieval_service.py and embed.py.

### Task 2 — Wire into retrieve_and_rank Celery task

Updated `apps/api/app/worker/tasks/runtime/retrieve.py`:
- Added `verified_qa_lookup` to the import from `app.services.retrieval_service`.
- Inserted cache lookup block after `embed_query(query)` / `query.embedding` event but
  BEFORE `rrf_fuse()` call — satisfying D-24 non-negotiable ordering.
- On hit: emits `query.complete` with `trace={"cache_hit": True, "similarity": ..., "source": "verified_qa_cache"}` and returns `{}` without executing `rrf_fuse`, `rerank`, or `build_trace` (D-26 early exit).
- On miss (cache_hit is None): falls through to the existing `rrf_fuse` call with no
  changes to the hybrid search path (D-27 zero regression).
- Cache hit also marks `job.status = "complete"` and `db.commit()` — consistent with
  the normal completion path.

### Task 3 — Unit tests

Added `TestVerifiedQALookup` (11 tests) to `test_retrieval_service.py`:
- Hit path: correct keys, UPDATE SQL executed, commit called.
- Miss path: returns None, UPDATE NOT called (execute call_count == 1).
- psycopg2 try/finally: `conn.close()` called on hit, miss, and exception.
- `str(query_vector)` passed as `"qv"` param; `threshold` passed correctly.
- `float()` coercion of similarity (covers Decimal return from NUMERIC column).
- `invalidated_at IS NULL` present in the SELECT SQL.

Added 3 new tests to `test_retrieve_task.py`:
- `test_retrieve_and_rank_cache_hit_skips_hybrid_search`: `rrf_fuse` not called on hit.
- `test_retrieve_and_rank_cache_hit_payload_has_cache_trace`: `query.complete` payload
  contains `cache_hit=True`, `source="verified_qa_cache"`, correct `similarity`.
- `test_retrieve_and_rank_verified_qa_lookup_called_with_threshold`: `verified_qa_lookup`
  called with `threshold=settings.VERIFIED_QA_HIT_THRESHOLD` (D-25).

Updated existing happy-path test to mock `verified_qa_lookup` returning `None` (cache miss),
preserving the 5-event sequence expectation.

All 24 tests in the retrieval unit test suite pass (13 task + 11 service).

## Acceptance Criteria Status

- [x] `apps/api/app/services/retrieval_service.py` contains `def verified_qa_lookup(conn_str: str, query_vector: list[float], threshold: float) -> Optional[dict]:`
- [x] Function uses psycopg2 try/finally/close pattern (matching existing vector_search pattern)
- [x] SQL lookup uses `1 - (question_vector <=> %(qv)s::vector) >= %(threshold)s` (D-25)
- [x] SQL lookup filters `WHERE invalidated_at IS NULL`
- [x] UPDATE `last_used_at = NOW(), use_count = use_count + 1` on cache hit (D-26)
- [x] Returns None on cache miss (D-27)
- [x] Returns dict with keys: answer, citations, similarity, source="verified_qa_cache" on hit
- [x] File still contains all existing functions: embed_query, vector_search, bm25_search, rrf_fuse, rerank, build_trace
- [x] `python -c "from app.services.retrieval_service import verified_qa_lookup; import inspect; print(inspect.signature(verified_qa_lookup))"` exits 0
- [x] `retrieve_and_rank` imports `verified_qa_lookup` from `app.services.retrieval_service`
- [x] `retrieve_and_rank` calls `verified_qa_lookup` BEFORE `rrf_fuse`
- [x] Called with `threshold=settings.VERIFIED_QA_HIT_THRESHOLD` (D-25)
- [x] Task returns early with `trace={"cache_hit": True, ...}` on verified_qa hit
- [x] If cache_hit is None, falls through to existing hybrid search unchanged (D-27)
- [x] `python -c "from app.worker.tasks.runtime.retrieve import retrieve_and_rank; print(retrieve_and_rank.name)"` exits 0
- [x] Unit tests cover hit and miss paths (11 + 3 = 14 new tests)
- [x] Each task committed individually (3 atomic commits)
- [x] STATE.md and ROADMAP.md not modified

## Files Modified

- `apps/api/app/services/retrieval_service.py` — +67 lines (verified_qa_lookup function)
- `apps/api/app/worker/tasks/runtime/retrieve.py` — +39 lines (import + cache lookup block)
- `apps/api/tests/unit/retrieval/test_retrieval_service.py` — +166 lines (TestVerifiedQALookup class)
- `apps/api/tests/unit/retrieval/test_retrieve_task.py` — +183 lines (3 new tests + updated happy-path)

## Key Decisions

No deviations from the plan. All four D-24 through D-27 locked decisions honored exactly.
The miss path in the task makes no changes whatsoever to the existing `rrf_fuse` / `rerank` /
`build_trace` / `emit` sequence — regression risk is zero.
