---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 03
subsystem: api
tags: [postgres, alembic, psycopg2, retrieval, rag-health, contextvar, celery]

# Dependency graph
requires:
  - phase: 21-01
    provides: tenant migration chain at head 0009 (turn_metrics/message_feedback), the agent_tools.py ContextVar pattern
provides:
  - Tenant migration 0010 (retrieval_metrics table)
  - retrieval_metrics_service.py write/read helpers
  - retrieve_tool instrumentation writing one retrieval_metrics row per call
  - _job_id_var ContextVar threaded through build_tool_server -> retrieve_tool
affects: [21-04 (sampled faithfulness task + GET /agents/{id}/retrieval-health read endpoint)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Tenant-DB write from inside an MCP tool's own closure (never reconstructed in agent.py) when the data (rank/score) only exists in that closure"
    - "New ContextVar transport (_job_id_var) following the established read-into-local-before-run_in_executor discipline (Pitfall 4)"
    - "Pseudo-label retrieval evaluation: using the reranker's own final selection as the relevance signal to score the pre-rerank fusion ranking (recall@k/nDCG@10/MRR) when no per-query ground truth exists"

key-files:
  created:
    - apps/api/alembic_tenant/versions/0010_retrieval_metrics.py
    - apps/api/app/services/retrieval_metrics_service.py
    - apps/api/tests/unit/test_migration_0010.py
    - apps/api/tests/unit/test_retrieval_metrics.py
  modified:
    - apps/api/app/services/agent_tools.py
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/tests/unit/test_agent_tools.py

key-decisions:
  - "recall_at_k/ndcg_at_10/mrr are computed against the pre-rerank RRF fusion ranking using the reranker's own final selection as the relevance signal — there is no per-query human-labeled ground truth in production, so this is the honest, standard 'stronger downstream ranker as pseudo-label' technique rather than a fabricated metric"
  - "cited_chunk_rank is the top returned (rerank-selected) chunk's position in the pre-rerank fused ranking — a direct, literal 'reranker lift' signal"
  - "retrieved_tokens is computed AFTER the existing content-truncation loop so it reflects what actually reaches the agent's context window; carried_never_cited_tokens uses the untruncated fused-candidate content lengths (rerank() returns copies, so truncating the returned chunks does not mutate the fused list)"
  - "write_retrieval_metrics wraps its own try/except (never raises) — the mitigation for T-21-03-02 lives in the service helper itself, not duplicated at the retrieve_tool call site"

requirements-completed: [OPS-05, OPS-06]

# Metrics
duration: unmeasured (no start timestamp captured this session)
completed: 2026-07-15
status: complete
---

# Phase 21 Plan 03: Retrieval-Health Instrumentation (OPS-05/06) Summary

**Tenant migration 0010 creates `retrieval_metrics`; `retrieve_tool` now writes one row per call with BM25/vector/RRF/rerank scores, reranker lift, recall@k/nDCG@10/MRR (pseudo-labeled from the reranker's own selection), cited-chunk rank, and context-window utilization/compaction vitals — all computed inside the tool's own closure and threaded to a new `_job_id_var` ContextVar.**

## Performance

- **Duration:** not measured this session (start timestamp not captured)
- **Completed:** 2026-07-15
- **Tasks:** 2/2 completed
- **Files modified:** 7 (4 created, 3 modified)

## Accomplishments
- Tenant migration `0010_retrieval_metrics.py` (down_revision `0009`) creates `retrieval_metrics` with all 16 OPS-05/06 columns plus nullable `citation_coverage`/`faithfulness` (reserved for 21-04's sampled Ragas faithfulness task)
- `retrieval_metrics_service.py`: `write_retrieval_metrics` (executor-safe, self-contained try/except so a write failure only logs — never fails the retrieve call or the turn) and `read_retrieval_health` (aggregate query for 21-04's read endpoint, with honest `"not tracked yet"` sentinels on zero rows or all-NULL columns)
- `retrieve_tool` (`agent_tools.py`) now computes and writes one `retrieval_metrics` row per call, entirely from data already in its own closure (`rrf_result` + `reranked`) — never reconstructed in `agent.py`, since rank/score data never crosses back into the SDK loop
- New `_job_id_var` ContextVar threaded through `build_tool_server(..., job_id=job_id)` -> `agent.py`'s call site -> read into a local at the top of `retrieve_tool` (Pitfall 4 guarded — never `.get()` inside the `run_in_executor` lambda)
- No fabricated "filters applied" metric (Pitfall 7) — verified by a dedicated test

## Task Commits

Each task was committed atomically:

1. **Task 1: Tenant migration 0010 + retrieval_metrics_service helpers** - `7d90ca0` (feat)
2. **Task 2 (TDD RED): failing tests for retrieve_tool instrumentation** - `0c2c53e` (test)
2. **Task 2 (TDD GREEN): instrument retrieve_tool + thread job_id ContextVar** - `4b0cf82` (feat)

_Task 2 used the RED -> GREEN TDD cycle (`tdd="true"`); no separate REFACTOR commit was needed — the implementation was clean on first pass._

## Files Created/Modified
- `apps/api/alembic_tenant/versions/0010_retrieval_metrics.py` - tenant migration creating `retrieval_metrics` (raw SQL + `IF NOT EXISTS`, mirrors 0009's convention)
- `apps/api/app/services/retrieval_metrics_service.py` - `write_retrieval_metrics` (never raises) + `read_retrieval_health` (21-04 read helper)
- `apps/api/app/services/agent_tools.py` - `_job_id_var` ContextVar, `CONTEXT_WINDOW_BUDGET` constant, `build_tool_server(job_id=...)` param, `retrieve_tool` OPS-05/06 metrics computation + write
- `apps/api/app/worker/tasks/runtime/agent.py` - `build_tool_server(..., job_id=job_id)` call-site update
- `apps/api/tests/unit/test_migration_0010.py` - migration source assertions + `INTEGRATION_TESTS_ENABLED`-gated DB roundtrip
- `apps/api/tests/unit/test_retrieval_metrics.py` - retrieve_tool metrics computation, job_id ContextVar plumbing (Pitfall 4 regression), empty-job_id warning path, no-filters-key assertion, empty-candidate degradation
- `apps/api/tests/unit/test_agent_tools.py` - patched `write_retrieval_metrics` in the two existing `retrieve_tool`-invoking tests (deviation, see below)

## Decisions Made
- **Pseudo-labeled retrieval quality metrics:** With no per-query human-labeled ground truth available in production, `recall_at_k`/`ndcg_at_10`/`mrr` are computed by treating the reranker's final selection as the best available relevance signal and scoring the pre-rerank RRF fusion ranking against it. This is the standard "stronger downstream ranker as pseudo-label" technique for unlabeled production retrieval evaluation — it is honest (computed from real rank data, not fabricated) and it is exactly what makes "reranker lift" a meaningful number (21-DOMAIN-NOTES.md §3).
- **`cited_chunk_rank`:** defined as the top returned (rerank-selected) chunk's position in the pre-rerank fused ranking — a direct measure of how far the reranker had to reach to find the chunk it ultimately surfaced first.
- **Token accounting order:** `retrieved_tokens` is computed from the already-truncated `chunks` list (post `_CONTENT_CHAR_LIMIT` truncation, matching what actually reaches the agent's context window); `carried_never_cited_tokens`/`compaction_ratio` use the untruncated `fused` candidate content lengths, since `rerank()` returns copies of the fused dicts (`chunk = dict(candidates[r.index])`), so truncating `chunks` in place never mutates the `fused` list's original content.
- **Write-failure isolation lives in the service helper, not the call site:** `write_retrieval_metrics` wraps its own connect/insert in try/except and logs+swallows any exception, satisfying T-21-03-02 without needing a duplicate try/except around the `run_in_executor` call in `retrieve_tool`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `rerank_top_score` read defensively via `.get()` instead of `[...]`**
- **Found during:** Task 2 GREEN implementation, running the existing `test_agent_tools.py` suite
- **Issue:** `reranked[0]["rerank_score"]` raised `KeyError` against `test_retrieve_truncates_to_max_chunks`'s existing fixture, which mocks `rerank()` to return chunk dicts without a `rerank_score` key (that test predates OPS-05/06 and only exercises truncation behavior)
- **Fix:** Changed to `reranked[0].get("rerank_score")` — the real `retrieval_service.rerank()` always sets this key, so production behavior is unchanged; the defensive read just avoids crashing on incomplete mocks
- **Files modified:** `apps/api/app/services/agent_tools.py`
- **Verification:** `pytest tests/unit/test_agent_tools.py -q` passes
- **Committed in:** `4b0cf82` (Task 2 GREEN commit)

**2. [Rule 3 - Blocking] Patched `write_retrieval_metrics` in two pre-existing `retrieve_tool` tests**
- **Found during:** Task 2 GREEN implementation, running the existing `test_agent_tools.py` suite
- **Issue:** `test_retrieve_truncates_to_max_chunks` and `test_retrieve_tool_logs_warning_on_unused_filters` call `retrieve_tool` without mocking the new write path — without a patch, these tests would attempt a real `psycopg2.connect()` (against whatever `conn_str` a prior test in the same file left in the shared ContextVar), adding multi-second network-timeout latency to the suite and risking an accidental write to a real local Postgres instance if one happens to be reachable with matching credentials
- **Fix:** Added `patch("app.services.agent_tools.write_retrieval_metrics")` to both tests' patch blocks
- **Files modified:** `apps/api/tests/unit/test_agent_tools.py`
- **Verification:** Both tests now run in well under a second each (previously 4-5s); full `test_agent_tools.py` + `test_agent_tools_contextvar.py` suite: 22 passed
- **Committed in:** `4b0cf82` (Task 2 GREEN commit)

---

**Total deviations:** 2 auto-fixed (1 bug, 1 blocking-issue test hygiene fix)
**Impact on plan:** Both fixes were required to keep the existing test suite green and fast after adding the new write path. No scope creep — no production behavior changed beyond what the plan specified.

## Issues Encountered
- `pytest tests/unit -k "retriev or agent" -q` (the plan's suggested broad regression command) fails to *collect* 15 unrelated test modules that import `app.main`, due to a pre-existing `ModuleNotFoundError: langchain_community.chat_models.vertexai` (documented in this plan's `key_context` as a known, unrelated issue). Verified this phase's actual changes are unaffected by running the directly relevant test files together instead: `test_agent_tools.py`, `test_agent_tools_contextvar.py`, `test_retrieval_metrics.py`, `test_migration_0010.py`, `test_migration_0009.py`, `test_agent_task.py`, `test_agent_turn_metrics.py`, `tests/unit/retrieval/` — 130 passed, 2 skipped (integration-gated), 2 pre-existing failures in `test_retrieval_service.py::TestEmbedQuery` confirmed via `git stash` to fail identically on the pre-plan commit (real AWS Bedrock `ValidationException`, unrelated to this plan's `agent_tools.py`/migration changes).

## User Setup Required
None - no external service configuration required. `retrieval_metrics` rows will populate automatically on the next live `retrieve_tool` call once this migration is applied to a real tenant DB (`alembic -c alembic_tenant.ini upgrade head`).

## Next Phase Readiness
- `retrieval_metrics` table + `write_retrieval_metrics`/`read_retrieval_health` helpers are ready for 21-04 to (a) add the sampled Ragas faithfulness task that `UPDATE`s `citation_coverage`/`faithfulness` on existing rows, and (b) wire `read_retrieval_health` behind a `GET /agents/{id}/retrieval-health` endpoint using the same IDOR pattern as `evals.py`/`red_team.py`.
- No blockers. Migration 0010 has not yet been applied to a live tenant Neon DB (integration roundtrip test is env-gated and was not run against a live DB this session) — recommend running it as part of 21-04's or a later live-verification gate, consistent with the Phase 13-16 precedent.

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-15*

## Self-Check: PASSED

All 8 created/modified files verified present on disk; all 3 task commit hashes (`7d90ca0`, `0c2c53e`, `4b0cf82`) verified present in `git log`.
