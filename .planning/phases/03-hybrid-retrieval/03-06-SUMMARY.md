# Plan 03-06 Summary — Integration Tests: Query Route + Guarded E2E

## What was built

### Integration tests — `apps/api/tests/integration/test_query_route.py`

Replaced the single `@pytest.mark.xfail` stub with **3 real integration tests**. Zero xfail remaining.

Each test uses the `_make_app_with_real_deps()` pattern (from `test_sse.py`) with `ASGITransport + AsyncClient` and `dependency_overrides` pointing to the local Postgres and Redis.

| Test | Assertions |
|------|------------|
| `test_post_query_returns_202` | 202 status, `job_id` present, `events_url` contains `/jobs/` and ends `/events`, `status=pending`, `apply_async` called once with correct `agent_id` and query text in args |
| `test_post_query_agent_not_found` | 404 status when agent UUID does not exist for the authenticated tenant |
| `test_get_queries_returns_list` | POST first (with mocked dispatch), then GET returns 200 with `jobs` list, `len >= 1`, `jobs[0]["kind"] == "query_agent"` |

**Fixture adaptations:**

- No `test_agent` fixture exists in integration conftest — created inline helper `_create_ready_agent()` that inserts a tenant + `status='ready'` agent with a Fernet-encrypted dummy `neon_connection_string` (mirrors `ready_agent_with_tenant_db` pattern from `test_ingestion_chain.py`).
- `CELERY_TASK_ALWAYS_EAGER` is `"False"` in integration conftest — mocked `retrieve_and_rank.apply_async` instead of running the task, isolating HTTP layer from Celery worker availability.
- `CAST(:soul AS jsonb)` used for JSONB column (avoids SQLAlchemy `::` named-param ambiguity — same pattern as `test_ingestion_chain.py`).
- `_delete_test_rows()` teardown in `finally` via `dependency_overrides.clear()` (T-07-01 pattern).

**Patch target:** `app.worker.tasks.runtime.retrieve.retrieve_and_rank.apply_async` — prevents real dispatch to Redis broker; no runtime Celery worker required for HTTP-layer tests.

### Guarded E2E test — `apps/api/tests/e2e/test_retrieval_e2e.py`

One E2E test guarded by `RETRIEVAL_E2E_ENABLED=1`:

```
pytest tests/e2e/test_retrieval_e2e.py -q
# → 1 skipped (RETRIEVAL_E2E_ENABLED=1 required)
```

**Guard pattern** (identical to `test_neon_e2e.py`):
```python
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not RETRIEVAL_E2E_ENABLED, reason="..."),
]
```

**E2E env vars required to run:**
- `RETRIEVAL_E2E_ENABLED=1`
- `RETRIEVAL_E2E_AGENT_ID` — UUID of a ready agent with ingested data
- `RETRIEVAL_E2E_API_KEY` — raw API key for that tenant
- `RETRIEVAL_E2E_BASE_URL` — defaults to `http://localhost:8000`

**E2E assertions:**
- POST returns 202
- Polls `GET /jobs/{job_id}/events` within 60s for `query.complete` event
- `query.complete` payload has: `results` (list), `trace` with `vector_candidates`, `bm25_candidates`, `fused_candidates`, `reranked_candidates`, and `strategy_used`

## Verification results

```
# Collect check — 3 tests, no import errors, no xfail
pytest tests/integration/test_query_route.py --collect-only -q
# → 3 tests collected

# E2E skip check
pytest tests/e2e/test_retrieval_e2e.py -q
# → 1 skipped (guard working)
```

Integration tests fail with `OperationalError: connection refused` when local Postgres is not running — correct expected behavior (not a code error).

## Files created / modified

- `apps/api/tests/integration/test_query_route.py` — replaced xfail stub (3 real tests)
- `apps/api/tests/e2e/test_retrieval_e2e.py` — new guarded E2E test
