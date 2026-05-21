---
slug: provision-ui-401-auth
status: resolved
trigger: "Provisioning stall + 401 console error on GET /api/v1/agents/{id}"
created: "2026-05-20"
updated: "2026-05-21"
phase: "04"
---

# Debug Session: Provisioning UI 401 Auth

## Symptom

Admin dashboard stalls at "Provisioning your agent…" — status stays `pending` forever.
Browser console: `GET http://localhost:8000/api/v1/agents/{id} 401 (Unauthorized)` at page.tsx:135.
POST /api/v1/agents succeeds (agent ID shown), but the UI never advances.

## Root Cause (confirmed 2026-05-21 via DB query)

**Backend was fine.** All agents in DB are `status=ready` with `status=complete` jobs, including the two
agents `c723c44b` (shown in UI as "pending") and `938504fa` (the 401 console error target).
The pipeline (`provision_neon` → `apply_migrations`) ran successfully every time.

Four compounding UI/test bugs caused the perceived stall:

### Bug 1 — UI silently swallows all non-2xx poll responses (FIXED)
`apps/admin/app/agents/new/page.tsx`  
Old polling loop had `if (!pollRes.ok) return` — swallowed 401/403/5xx silently.
When the Clerk JWT for a stale interval (agent `938504fa` from a prior tab) expired, the
poll returned 401. The interval kept firing with no error surfaced. UI froze at "pending".

**Fix already in working tree:** Poll now distinguishes 401/403 (abort + show actionable error),
5xx (tolerate up to `POLL_ERROR_TOLERANCE=3` ticks), and other non-2xx (abort immediately).

### Bug 2 — deps.py masks infra errors as 401 (FIXED)
`apps/api/app/api/deps.py`  
Broad `except Exception` in the JWT path returned HTTP 401 for DB connection errors and
JWKS network failures. Indistinguishable from a genuinely invalid token.

**Fix already in working tree:** `PyJWKClientConnectionError`/`PyJWKClientError` → 503.
Unexpected exceptions (DB errors) → 503. Only `InvalidTokenError` → 401.

### Bug 3 — Stale `chain` patch in test_routes.py (FIXED 2026-05-21)
`apps/api/tests/unit/test_routes.py`  
`patch("app.api.v1.agents.chain")` patched a non-existent import — `agents.py` removed the
`chain` import when switching to `provision_neon.apply_async` directly. Raised `AttributeError`,
failing all 6 POST tests. Also all URLs used `/agents` instead of `/api/v1/agents`.

**Fix applied:** Replaced with `patch("app.api.v1.agents.provision_neon")`, mocked
`mock_pn.apply_async = MagicMock()`. Fixed all URLs to `/api/v1/agents`.

### Bug 4 — Wrong URL prefix in test_clerk_jwt.py (FIXED 2026-05-21)
`apps/api/tests/unit/test_clerk_jwt.py`  
CLERK-03, CLERK-04, CLERK-07 all hit `/agents` (no `/api/v1` prefix). Routes are at `/api/v1/agents`.
- CLERK-07 asserted 401 but got 404 (route not found) → FAILED.
- CLERK-03/04 asserted `!= 401` and got 404 → PASSED for wrong reasons.
- Mock DB in CLERK-03/04 lacked `refresh()` side-effect to set `agent.id`/`job.id` after flush,
  causing Pydantic `ValidationError` on `AgentCreateResponse`.

**Fix applied:** URLs corrected to `/api/v1/agents`. Added `provision_neon` mock. Added
`mock_db.add = MagicMock()` (add() is sync). Added `refresh()` side-effect that sets UUIDs
via `isinstance(obj, Agent/Job)` checks. CLERK-03/04 assertions strengthened to `== 202`.

## Evidence

- timestamp: 2026-05-21
  finding: "DB query: all 5 agents are status=ready, all jobs status=complete. Backend pipeline
            never failed. c723c44b='may' (ready), 938504fa='gugu2' (ready). Stall was UI-only."
  source: direct psycopg2 query to control Neon DB

- timestamp: 2026-05-21
  finding: "Redis pipeline queue length = 0, celery queue = 0 — no stuck tasks in queues."
  source: direct Redis check via redis-py

- timestamp: 2026-05-21
  finding: "16/16 unit tests now pass after Bug 3+4 fixes."
  source: pytest tests/unit/test_routes.py tests/unit/test_clerk_jwt.py

## Resolution

root_cause: |
  Backend was healthy throughout. The 'pending' stall was caused by Bug 1: the old polling
  loop had `if (!pollRes.ok) return` which swallowed the 401 on a STALE interval for agent
  938504fa (a previous session's expired Clerk JWT). The UI appeared stuck because the interval
  for the NEW agent (c723c44b) was also blocked by the component-level state freeze.

fix: |
  Bug 1: page.tsx poll now explicitly handles 401/403 (stop + show auth error), 5xx (tolerate
         3 ticks), unexpected status (stop + show error). Token null check runs before fetch.
  Bug 2: deps.py now returns 503 for JWKS/infra errors, 401 only for InvalidTokenError.
  Bug 3: test_routes.py patch target fixed: provision_neon (not chain). URLs fixed: /api/v1/agents.
  Bug 4: test_clerk_jwt.py URLs fixed. Mock DB refresh side-effect added. Assertions strengthened.

Files changed:
  - apps/admin/app/agents/new/page.tsx (Bug 1)
  - apps/api/app/api/deps.py (Bug 2)
  - apps/api/tests/unit/test_routes.py (Bug 3)
  - apps/api/tests/unit/test_clerk_jwt.py (Bug 4)
  - apps/api/app/main.py (minor — pre-existing changes from prior session)
  - apps/api/app/api/v1/webhooks.py (minor — pre-existing changes from prior session)
