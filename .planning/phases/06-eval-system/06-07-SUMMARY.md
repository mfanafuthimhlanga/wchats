---
phase: 06-eval-system
plan: "06-07"
subsystem: api
tags: [fastapi, evals, celery, idor, psycopg2, asyncio]

requires:
  - phase: 06-05
    provides: eval_service.py and run_eval_suite Celery task
  - phase: 06-06
    provides: Neon branch management and scenario service

provides:
  - GET /api/v1/agents/{agent_id}/eval-runs — list eval runs with aggregate Ragas metric scores
  - GET /api/v1/agents/{agent_id}/eval-runs/{run_id}/results — per-scenario scores with passed flag
  - POST /api/v1/agents/{agent_id}/eval-runs/trigger — manual eval dispatch (202 Accepted)
  - apps/api/app/api/v1/evals.py — full FastAPI router
  - Router registered in apps/api/app/main.py
  - 17 unit tests covering all routes, IDOR prevention, and CTL-08 compliance

affects:
  - 06-08 (Next.js eval dashboard — consumes these three routes)
  - demo_m6.sh (calls POST /trigger)

tech-stack:
  added: []
  patterns:
    - asyncio.to_thread for blocking psycopg2 calls inside async FastAPI handlers
    - fernet_decrypt + psycopg2 tenant DB access pattern (same as validators.py)
    - 404-on-IDOR pattern (not 403) to prevent tenant enumeration

key-files:
  created:
    - apps/api/app/api/v1/evals.py
    - apps/api/tests/unit/test_eval_routes.py
  modified:
    - apps/api/app/main.py

key-decisions:
  - "Return 404 (not 403) on IDOR mismatch — prevents tenant enumeration by leaking existence"
  - "asyncio.to_thread wraps blocking psycopg2 calls — avoids blocking FastAPI event loop"
  - "POST /trigger dispatches only agent_id to Celery — no conn_str (CTL-08 / D-18)"
  - "NULL metric scores map to 0.0 in aggregate_scores — avoids None in JSON response"
  - "passed flag uses EVAL_FAITHFULNESS_THRESHOLD (0.90) for all four metrics (D-21)"

patterns-established:
  - "Tenant DB access from FastAPI: db.get(Agent) for IDOR → fernet_decrypt → asyncio.to_thread(_query_tenant_db_sync)"
  - "IDOR prevention: 404 on agent not found OR tenant_id mismatch — same code path"

requirements-completed:
  - EVL-04
  - EVL-06
  - EVL-07

duration: 25min
completed: 2026-05-23
---

# Phase 06-07: FastAPI Eval Routes Summary

**Three authenticated eval API routes wired to the tenant DB — list runs, per-scenario results, and manual trigger — all with IDOR prevention and 17 passing unit tests.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-05-23
- **Tasks:** 4 (evals.py creation, trigger route, main.py registration, unit tests)
- **Files created:** 2
- **Files modified:** 1
- **Tests:** 17 / 17 passed

## Accomplishments

### Task 1: evals.py FastAPI router (GET routes)

Created `apps/api/app/api/v1/evals.py` with:

- `_query_tenant_db_sync` helper wrapping psycopg2 in `asyncio.to_thread` to avoid blocking the event loop
- `GET /agents/{agent_id}/eval-runs` — queries tenant DB with an aggregate SQL JOIN across `eval_runs` and `eval_results`, returns `eval_runs` list with per-run `aggregate_scores` dict (4 metrics, NULL → 0.0)
- `GET /agents/{agent_id}/eval-runs/{run_id}/results` — queries per-scenario metric rows, groups by `scenario_id`, computes `passed` boolean from `EVAL_FAITHFULNESS_THRESHOLD`

### Task 3: POST /trigger route

Added `POST /agents/{agent_id}/eval-runs/trigger` (status_code=202):
- Guards: agent must exist (404), belong to tenant (404), and be in `ready` state (400)
- Dispatches `run_eval_suite.apply_async(kwargs={"agent_id": str(agent_id)}, queue="runtime")` — no conn_str (CTL-08)
- Returns `{"status": "queued", "task_id": ..., "agent_id": ...}` immediately

### Task 2: main.py registration

Added `evals` to the v1 import and `app.include_router(evals.router, prefix="/api/v1")` after agent_chat registration.

### Unit tests

17 tests across three test classes:
- `TestListEvalRuns` — happy path, null scores, 404 not found, 404 IDOR, auth required
- `TestGetEvalRunResults` — response shape, passed=True, passed=False, empty results, 404 IDOR, auth required
- `TestTriggerEvalRun` — 202 response, CTL-08 compliance, 400 not-ready, 404 not found, 404 IDOR, auth required

## Verification

All plan checks passed:
```
python -c "import ast; ast.parse(open('app/api/v1/evals.py').read()); print('parse ok')"
# → parse ok

python -c "from app.api.v1.evals import router; print([r.path for r in router.routes])"
# → ['/agents/{agent_id}/eval-runs', '/agents/{agent_id}/eval-runs/{run_id}/results', '/agents/{agent_id}/eval-runs/trigger']

python -c "from app.api.v1.evals import router; paths=[r.path for r in router.routes]; assert '/agents/{agent_id}/eval-runs/trigger' in paths; print('trigger route ok')"
# → trigger route ok

python -c "src=open('app/api/v1/evals.py').read(); assert 'status_code=202' in src; assert 'queue=\"runtime\"' in src; print('trigger constraints ok')"
# → trigger constraints ok

pytest tests/unit/test_eval_routes.py -v
# → 17 passed
```

## Notes

- All three routes use `get_current_tenant` dependency — no anonymous access
- IDOR pattern returns 404 on mismatch (not 403) to avoid revealing agent existence to wrong tenants
- The eval dashboard frontend (06-08) can now call these routes via Bearer auth from Clerk
