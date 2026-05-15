# Plan 03-03 Summary — retrieve_and_rank Celery Task

## Status: COMPLETE

## Files Created / Modified

| File | Action |
|------|--------|
| `apps/api/app/worker/tasks/runtime/retrieve.py` | Created |
| `apps/api/app/worker/celery_app.py` | Updated (include list) |
| `apps/api/tests/unit/retrieval/test_retrieve_task.py` | Updated (stubs → real tests) |

---

## Task Signature and Decorator

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
```

- `acks_late=True` — message acknowledged after task completes (CLAUDE.md non-negotiable)
- `queue="runtime"` — runtime queue (CLAUDE.md non-negotiable: both queues always present)
- Args: `(job_id, agent_id, query)` only — `conn_str` is NEVER in task args
- Returns `{}` always — no sensitive data in return value

---

## SSE Event Sequence (5 events)

| Order | Event Type | Payload Keys |
|-------|-----------|--------------|
| 1 | `query.started` | `agent_id` |
| 2 | `query.embedding` | `model` ("voyage-3") |
| 3 | `query.searching` | `fused_count` |
| 4 | `query.reranking` | `reranked_count` |
| 5 | `query.complete` | `query`, `results`, `trace`, `strategy_used` |

On failure after max_retries: emits `query.failed` with `error` key.

---

## rrf_fuse Return Shape Used

`rrf_fuse()` returns a **dict** (not a list):

```python
rrf_result = rrf_fuse(conn_str, query_vector, query, strategy)
fused        = rrf_result["fused"]             # list[dict] — top final_k by RRF score
vector_cands = rrf_result["vector_candidates"] # list[dict] — top vector_k by cosine
bm25_cands   = rrf_result["bm25_candidates"]   # list[dict] — top bm25_k by ts_rank_cd
```

All three lists are passed to `build_trace()` for trace inclusion in `query.complete` payload.

---

## Idempotency Mechanism

READ guard on `job_events` table:

```sql
SELECT 1 FROM job_events
WHERE job_id = :jid AND event_type = 'query.complete' LIMIT 1
```

If a row exists → return `{}` immediately. Prevents duplicate events on retry/redelivery.

---

## Module-Level Redis Client

```python
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)
```

Same pattern as `embed.py` and `provision.py`.

---

## celery_app.py Change

```python
include=[
    ...
    "app.worker.tasks.pipeline.embed",
    # M3: hybrid retrieval task (runtime queue)
    "app.worker.tasks.runtime.retrieve",
],
```

---

## Verification Results

```
# Task attributes
task sig: (job_id: str, agent_id: str, query: str) -> dict
retrieve_and_rank task OK

# celery_app include
celery_app include OK: ['app.worker.tasks.pipeline.provision', 'app.worker.tasks.pipeline.migrations',
  'app.worker.tasks.pipeline.parse', 'app.worker.tasks.pipeline.chunk',
  'app.worker.tasks.pipeline.metadata', 'app.worker.tasks.pipeline.embed',
  'app.worker.tasks.runtime.retrieve']

# grep checks
apps/api/app/worker/tasks/runtime/retrieve.py:67:    acks_late=True,
# conn_str appears only internally (decrypt assignment + usage) — never in function signature

# pytest
10 passed in 2.60s
```

---

## Security Constraints Met

- `conn_str` fetched via `fernet_decrypt(agent.neon_connection_string)` at runtime
- Query text is NEVER logged (only `job_id`, `agent_id`, and counts)
- `return {}` always — no sensitive data leaks through return value
- Task args contain only `job_id`, `agent_id`, `query` — no credentials
