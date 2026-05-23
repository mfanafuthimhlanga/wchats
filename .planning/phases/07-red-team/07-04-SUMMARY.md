---
phase: "07"
plan: "04"
subsystem: red-team-api
tags: [fastapi, routes, schemas, pydantic, red-team, idor, celery]
dependency_graph:
  requires: ["07-01", "07-03"]
  provides: ["RED-06-api"]
  affects: ["apps/api/app/api/v1/red_team.py", "apps/api/app/schemas/red_team.py", "apps/api/app/main.py"]
tech_stack:
  added: []
  patterns: ["psycopg2-via-asyncio.to_thread", "IDOR-prevention", "202-dispatch", "conn_str-decrypt-at-runtime"]
key_files:
  created:
    - apps/api/app/schemas/red_team.py
    - apps/api/app/api/v1/red_team.py
  modified:
    - apps/api/app/main.py
decisions:
  - POST trigger passes only agent_id to run_red_team (CTL-08 — conn_str never in task kwargs)
  - run_id in trigger response uses task.id as correlator — actual DB run_id assigned inside task
  - findings defaults to [] when psycopg2 returns None for JSONB column
  - deployment_blocked defaults to False for old rows where column may be None
metrics:
  duration: "~10 min"
  completed: "2026-05-23"
  tasks: 1
  files: 3
---

# Phase 7 Plan 4: FastAPI Routes + Schemas — Red Team Endpoints Summary

Red team API surface implemented: three FastAPI routes and four Pydantic schemas for the `red_team_runs` endpoint group, registered in `main.py`. All routes follow the exact pattern established in `evals.py` — X-API-Key auth, IDOR check, psycopg2-via-asyncio.to_thread for tenant DB queries, and 202 dispatch for the trigger route.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 07-04-T01 | Schemas + FastAPI router + main.py registration | 46eac62 |

## What Was Built

### `apps/api/app/schemas/red_team.py`

Four Pydantic models:
- `RedTeamRunResponse` — id, kind, status, started_at, finished_at, `findings: list[dict[str, Any]]`, max_severity, deployment_blocked
- `RedTeamRunListResponse` — wraps a list of `RedTeamRunResponse`
- `RedTeamTriggerRequest` — empty body model with docstring
- `RedTeamTriggerResponse` — job_id, run_id, message

### `apps/api/app/api/v1/red_team.py`

Three routes on `APIRouter(tags=["red_team"])`:

1. **GET `/agents/{agent_id}/red-team-runs`** — lists up to 20 runs ordered by `started_at DESC`; IDOR check; psycopg2-via-asyncio.to_thread; returns `{"runs": [...]}`

2. **GET `/agents/{agent_id}/red-team-runs/{run_id}`** — fetches single run; 404 if not found; returns `{"run": {...}}`

3. **POST `/agents/{agent_id}/red-team-runs`** (status_code=202) — validates agent is ready; dispatches `run_red_team.apply_async(kwargs={"agent_id": str(agent_id)}, queue="runtime")`; returns `{"job_id": task.id, "run_id": task.id, "message": "Red team run queued"}`

All three routes:
- Require `Depends(get_current_tenant)` for X-API-Key auth
- Check `agent.tenant_id != tenant.id` → raise 404 (IDOR prevention)
- Fetch conn_str via `fernet_decrypt` — never logged

### `apps/api/app/main.py`

Added `red_team` to the import line alongside other v1 routers. Registered `app.include_router(red_team.router, prefix="/api/v1")` after the evals router.

## Verification

```
python -c "from app.api.v1.red_team import router; print(router.tags)"
# Output: ['red_team']
```

Import succeeds. All acceptance criteria met.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all routes are fully wired. The `run_id` returned from POST trigger uses `task.id` as a correlator (by design — the actual DB `run_id` is generated inside the Celery task, not at dispatch time). This is documented in the plan and not a stub.

## Threat Flags

None — no new network endpoints beyond those specified in the plan. All routes follow established IDOR and auth patterns.

## Self-Check: PASSED

- `apps/api/app/schemas/red_team.py` exists: FOUND
- `apps/api/app/api/v1/red_team.py` exists: FOUND
- `apps/api/app/main.py` updated: FOUND
- Commit 46eac62: FOUND
