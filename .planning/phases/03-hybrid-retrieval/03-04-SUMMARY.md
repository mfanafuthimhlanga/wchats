# Plan 03-04 Summary — Query Router

## What was built

### Schemas — `apps/api/app/schemas/query.py`

| Class | Purpose |
|---|---|
| `QueryRequest` | POST body — `query: str` (min_length=1), `filters: list[dict]` (M4+ placeholder) |
| `QueryJobResponse` | 202 response — `job_id`, `status`, `events_url` |
| `QueryJobItem` | Single item in list — `job_id`, `status`, `kind`, `created_at`, `finished_at` (nullable). `ConfigDict(from_attributes=True)` |
| `QueryListResponse` | GET /queries response — `jobs: list[QueryJobItem]` |

Note: `Job.id` (ORM primary key) is mapped explicitly to `QueryJobItem.job_id` — no aliasing trickery needed because the constructor call uses keyword args.

### Route endpoints — `apps/api/app/api/v1/query.py`

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/agents/{agent_id}/query` | 202 | Dispatch hybrid retrieval job |
| GET | `/agents/{agent_id}/queries` | 200 | List last 50 query_agent jobs |

**POST /agents/{agent_id}/query — validation chain:**
1. Agent exists + `Agent.tenant_id == tenant.id` + `deleted_at IS NULL` → 404 if missing
2. `agent.status == "ready"` → 409 if not ready (detail includes actual status)
3. Create `Job(kind="query_agent", status="pending")` in control DB
4. `retrieve_and_rank.apply_async(args=[job_id, agent_id, body.query], queue="runtime")`
5. Return `QueryJobResponse(job_id, status="pending", events_url="/jobs/{id}/events")`

**GET /agents/{agent_id}/queries:**
- Same agent ownership validation → 404 if not found
- `SELECT jobs WHERE agent_id=? AND kind='query_agent' ORDER BY created_at DESC LIMIT 50`
- Maps rows to `QueryJobItem` with explicit `job_id=j.id` constructor

### main.py registration — `apps/api/app/main.py`

Added `query` to the v1 import and `app.include_router(query.router)` after existing routers. No other changes.

## Job kind

`"query_agent"` — used in both job creation and the GET filter.

## Security notes

- `body.query` is **never logged** — it only appears in `apply_async` args and comments
- `log.info("query_agent.dispatched", ...)` emits only `agent_id` and `job_id`
- `tenant_id` comes from the authenticated `Tenant` (X-API-Key), never from the request body
- Agent scoped by `Agent.tenant_id == tenant.id` on all routes — T-02-06-01 pattern

## Verification results

```
schemas OK
routes: [..., '/agents/{agent_id}/query', '/agents/{agent_id}/queries']
main.py router OK
route import OK
1 test collected in 0.11s
```

`body.query` grep — only in comments and `apply_async` args; zero occurrences in any `log.*` call.

## Commit

`9d85978 feat(03-04): query router — POST /agents/{id}/query + GET /agents/{id}/queries`
