---
slug: delete-agent-405
status: resolved
trigger: manual
created: 2026-05-21
goal: find_and_fix
---

# Debug: Delete Agent 405 Method Not Allowed

## Symptoms

- Delete agent from admin UI returns HTTP 405 Method Not Allowed
- Error trace:
  ```
  intercept-console-error.ts:48 Error: Delete failed (HTTP 405)
      at handleDelete (page.tsx:62:13)
      at async runDelete (AgentCard.tsx:99:7)
  ```
- DELETE /agents/{id} endpoint supposedly added in commit 0c30bc2

## Current Focus

**hypothesis:** CONFIRMED — uvicorn process is running stale code loaded before commit 0c30bc2; DELETE route is missing from the live server's route table.
**next_action:** Restart uvicorn.

## Evidence

- timestamp: 2026-05-21T10:05:00Z
  finding: |
    curl -X DELETE http://localhost:8000/api/v1/agents/00000000-0000-0000-0000-000000000000 -H "Authorization: Bearer TEST"
    returns HTTP 405 from the live server.

- timestamp: 2026-05-21T10:05:10Z
  finding: |
    GET http://localhost:8000/openapi.json does NOT include DELETE /api/v1/agents/{agent_id}
    in its paths. All other routes (GET, POST, PATCH) for agents are present.
    This proves the running process never loaded the new route.

- timestamp: 2026-05-21T10:05:20Z
  finding: |
    apps/api/app/api/v1/agents.py line 208 has @router.delete("/agents/{agent_id}", status_code=204)
    — route IS present in the source file on disk (committed in 0c30bc2).

- timestamp: 2026-05-21T10:05:25Z
  finding: |
    apps/admin/app/agents/page.tsx:57 sends fetch() with method: 'DELETE' to
    ${apiBase}/api/v1/agents/${agentId}. NEXT_PUBLIC_API_BASE=http://localhost:8000.
    URL and method are correct — issue is server-side.

- timestamp: 2026-05-21T10:05:30Z
  finding: |
    No Next.js API route handlers exist (apps/admin/app/api/ directory is absent).
    next.config.mjs has no rewrites or proxy rules. Clerk middleware file is
    named proxy.ts (not middleware.ts) so it is not loaded — but this is
    irrelevant to the 405 since the UI hits FastAPI directly, not via Next.js.

- timestamp: 2026-05-21T10:05:35Z
  finding: |
    CORS allow_methods includes DELETE (main.py line 118). CORS preflight OPTIONS
    returns 200 and Access-Control-Allow-Methods: GET, POST, PATCH, DELETE.
    CORS is not the cause.

  conclusion: |
    Root cause is stale uvicorn process. The server was started before the DELETE
    route was committed and has not been reloaded. Route is absent from the live
    route table but present in source on disk.

## Resolution

- root_cause: uvicorn is running stale code from before commit 0c30bc2; the DELETE /api/v1/agents/{agent_id} route exists on disk but was never loaded into the live process.
- fix: Restart the uvicorn process so it reloads the updated agents.py. The route code is correct and no code changes are needed.
