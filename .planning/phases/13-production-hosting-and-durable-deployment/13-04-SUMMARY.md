---
phase: 13-production-hosting-and-durable-deployment
plan: "04"
subsystem: embeddings
tags: [bedrock, titan-v2, reembed, backfill, celery, pipeline, PROD-06, acks_late, idempotent]
dependency_graph:
  requires: [13-02]
  provides: [reembed-corpus-task, bedrock-backfill, hnsw-reindex-direct-endpoint]
  affects: [celery_app, retrieval_quality, embeddings_table]
tech_stack:
  added: []
  patterns: [acks_late, model-id-filter, keyset-pagination, on-conflict-upsert, direct-endpoint-reindex, tenant-isolation]
key_files:
  created:
    - apps/api/app/worker/tasks/pipeline/reembed.py
    - apps/api/tests/unit/test_reembed_task.py
  modified:
    - apps/api/app/worker/celery_app.py
decisions:
  - "Batch SELECT uses model-id filter (IS DISTINCT FROM) so committed batches drop out naturally — no offset drift, naturally resumable"
  - "REINDEX uses neon_direct_connection_string in ISOLATION_LEVEL_AUTOCOMMIT to avoid PgBouncer transaction-mode incompatibility (Pitfall 7)"
  - "fernet_decrypt called twice at task start (pooled + direct) then both local vars used — conn_str never in task args or logs"
  - "Batch size 50: fits comfortably in a single Bedrock round-trip budget and keeps commit latency low for resumability"
  - "REINDEX wrapped in best-effort try/except — failure logs reembed.reindex_skipped but never loses committed vectors"
metrics:
  duration: "~18 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 1
status: complete
requirements: [PROD-06]
---

# Phase 13 Plan 04: reembed_corpus Backfill Task Summary

One-time, per-tenant Celery task `reembed_corpus(agent_id)` that migrates the Voyage-embedded chunk corpus onto Amazon Bedrock Titan Text Embeddings v2, registered on the `pipeline` queue and proven correct by 5 unit tests.

## What Was Built

### New task: `reembed.py`

`reembed_corpus(agent_id: str)` on the `pipeline` queue with `acks_late=True, max_retries=3, default_retry_delay=10`:

**Idempotency via model-id filter:**
```sql
SELECT c.id, c.content
FROM chunks c
LEFT JOIN embeddings e ON e.chunk_id = c.id
WHERE e.model IS DISTINCT FROM %s   -- NULL IS DISTINCT FROM target → TRUE (includes orphan chunks)
ORDER BY c.id
LIMIT %s
```
After a full migration this query returns zero rows, so the task exits immediately with `total_reembedded=0`. Re-running any number of times is safe.

**Resumable per-batch commits:** Each batch of up to 50 chunks is committed before the next batch is fetched. A mid-run kill leaves committed batches migrated; the model-id filter skips them on resume.

**ON CONFLICT upsert (write-level idempotency):**
```sql
INSERT INTO embeddings (chunk_id, model, vector)
VALUES (%s, %s, %s::vector)
ON CONFLICT (chunk_id) DO UPDATE
    SET model = EXCLUDED.model, vector = EXCLUDED.vector, created_at = now()
```

**Bedrock embedding path:**
`bedrock_embedding_service.embed_texts(texts, "document")` — the 1024-dim guard in 13-02 ensures no dim-mismatch vector can reach the HNSW index.

**REINDEX on direct endpoint (Pitfall 7):**
After all batches, opens a SEPARATE psycopg2 connection from `neon_direct_connection_string` (not the pooled endpoint), sets `ISOLATION_LEVEL_AUTOCOMMIT`, and runs `REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx`. Best-effort: exception logs `reembed.reindex_skipped`, never re-raises.

**Tenant isolation (T-13-04-01):** The task takes a single `agent_id`, decrypts only that agent's two connection strings, and never loops other tenants. Cross-tenant write is structurally impossible.

**Security (T-13-04-03):** `conn_str` and `direct_conn_str` are local variables from `fernet_decrypt` — they appear in no log calls. Only `agent_id`, batch counts, and error types are logged.

### Celery registration

`"app.worker.tasks.pipeline.reembed"` added to `include=[...]` in `celery_app.conf.update(...)`. The existing `task_routes` wildcard `"app.worker.tasks.pipeline.*": {"queue": "pipeline"}` routes it to the pipeline queue automatically.

### Tests: `test_reembed_task.py`

| Test | Behavior | Result |
|------|----------|--------|
| `test_reembed_corpus_acks_late` | acks_late=True, max_retries=3, delay=10 | PASS |
| `test_reembed_corpus_migrates_chunks` (case a) | embed_texts called with chunk texts; ON CONFLICT upsert; total_reembedded=2 | PASS |
| `test_reembed_corpus_idempotent_when_fully_migrated` (case b) | fetchall→[] immediately; embed_texts call_count=0; zero INSERTs | PASS |
| `test_reembed_corpus_tenant_isolation` (case c) | db.get called once; exactly 2 decrypt calls; exactly 2 connect calls | PASS |
| `test_reembed_corpus_reindex_uses_direct_connection` (case d) | 2nd connect uses direct conn; set_isolation_level(AUTOCOMMIT) before REINDEX execute | PASS |

```
pytest tests/unit/test_reembed_task.py -x -q
5 passed in 1.62s
```

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `4631559` | test | Add failing tests for reembed_corpus task (RED) |
| `19676be` | feat | Implement reembed_corpus Celery task (GREEN) |
| `29d6f09` | feat | Register reembed_corpus on pipeline queue in celery_app include list |

## Deviations from Plan

None — plan executed exactly as written. TDD RED/GREEN sequence followed; both commits exist in git log.

## Threat Surface Scan

No new network endpoints. The Bedrock call path already exists via `bedrock_embedding_service` (introduced in 13-02). The new surface introduced here is:

| Flag | File | Description |
|------|------|-------------|
| threat_flag: cross-tenant-write | reembed.py | Mitigated: single agent_id → single tenant DB; no tenant loop; test asserts db.get called once |
| threat_flag: conn-str-logging | reembed.py | Mitigated: conn_str local variable, never referenced in log.* calls |

Both were in the plan's threat model (T-13-04-01, T-13-04-03) and are fully mitigated.

## Known Stubs

None. The task is fully wired: bedrock_embedding_service is the live Bedrock client from 13-02 (lazy-initialized; no mock data flows to production). The live per-tenant run against real Bedrock and retrieval-quality regression check are the 13-08 gate (autonomous:false).

## Self-Check: PASSED

- `apps/api/app/worker/tasks/pipeline/reembed.py` FOUND
- `apps/api/tests/unit/test_reembed_task.py` FOUND
- Commit `4631559` (RED test) FOUND
- Commit `19676be` (GREEN task) FOUND
- Commit `29d6f09` (celery_app registration) FOUND
- `grep "acks_late=True" reembed.py` → line 64 FOUND
- `grep "ON CONFLICT (chunk_id) DO UPDATE" reembed.py` → line 165 FOUND
- `grep "neon_direct_connection_string" reembed.py` → line 111 FOUND
- `grep -i "for .* in .*tenants\|all_agents\|db.query(Agent).all" reembed.py` → no match PASS
- `python -c "from app.worker.celery_app import celery_app; assert 'app.worker.tasks.pipeline.reembed' in celery_app.conf.include; print('reembed registered')"` → "reembed registered" PASS
- `pytest tests/unit/test_reembed_task.py -x -q` → 5 passed PASS
