# 09-02 Wave 2 — Execution Summary

## What was built

### 1. `apps/api/app/worker/tasks/pipeline/strategy.py`
New Celery pipeline task `synthesize_retrieval_strategy`:
- Signature: `(self, result: dict) -> dict` — chain pass-through pattern (same as embed.py)
- `acks_late=True`, `queue="pipeline"`, `max_retries=2`
- Module-level `_redis` client (SSL-aware, strips query params from REDIS_URL)
- Idempotency guard: skips if `agent.retrieval_strategy != {}` AND `strategy_resynthesis_flagged=False`
- Fetches `conn_str` via `fernet_decrypt(agent.neon_connection_string)` — never logged, never in args
- Calls `_fetch_corpus_signals_sync(agent_id, conn_str)` then `_run_strategist_loop` via `asyncio.run(asyncio.wait_for(..., timeout=60.0))`
- Validates output via `RetrievalStrategy.model_validate(raw)` — falls back to `RetrievalStrategy()` defaults on failure
- Writes `agent.retrieval_strategy` and clears `strategy_resynthesis_flagged` in a separate DB session
- Emits `strategy.synthesized` SSE event via `emit()`
- Retry pattern: transient DB/fetch errors retried with `countdown=2**retries`; strategist failures fall through to defaults (not retried)

### 2. `apps/api/app/api/v1/documents.py`
- Added import: `from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy`
- Appended `synthesize_retrieval_strategy.s()` as the 5th link in the Celery chain after `embed_and_migrate.s()`
- Chain is now: `parse_documents → chunk_documents → generate_metadata → embed_and_migrate → synthesize_retrieval_strategy`
- `.apply_async(queue="pipeline", headers=...)` unchanged

### 3. `apps/admin/app/agents/[id]/ingest/page.tsx`
- Inserted one line in `EVENT_LABELS` after `'embedding.complete'`:
  `'strategy.synthesized': 'Optimising retrieval strategy...',`
- British spelling ("Optimising") and ellipsis locked as specified

## Verification command outputs

**Task 1:**
```
ok ['result'] True pipeline
```

**Task 2 (chain wiring):**
```
ok
```

**Task 2 (circular import check):**
```
ok
```

**Task 3:**
```
ok
```

## Commit SHAs

| Task | SHA | Message |
|------|-----|---------|
| Task 1 | `e3df22f` | feat(09-02): add synthesize_retrieval_strategy Celery pipeline task |
| Task 2 | `86c1128` | feat(09-02): wire synthesize_retrieval_strategy as 5th chain link in documents.py |
| Task 3 | `d25bda0` | feat(09-02): add strategy.synthesized EVENT_LABEL to ingest page |

## Deviations

None. Implementation follows the plan specification exactly:
- Idempotency guard uses the exact `agent.retrieval_strategy and agent.retrieval_strategy != {} and not agent.strategy_resynthesis_flagged` check
- Two separate `get_sync_db()` blocks (idempotency/fetch + write) as specified
- Strategist failures do NOT trigger retry (fall through to defaults) as specified
- `return result` (unchanged dict) as chain pass-through
