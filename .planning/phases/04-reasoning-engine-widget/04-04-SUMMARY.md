---
phase: 04-reasoning-engine-widget
plan: "04"
subsystem: api-routes
tags: [fastapi, jwt, cors, sse, rate-limit, widget, agent-chat]
dependency_graph:
  requires: [04-03]
  provides: [agent-chat-routes, widget-routes, jwt-helpers, public-sse]
  affects: [04-05, 04-06, 04-07]
tech_stack:
  added: [python-jose==3.5.0]
  patterns: [HTTPBearer, EventSourceResponse-reuse, Redis-INCR-rate-limit, psycopg2-tenant-db]
key_files:
  created:
    - apps/api/app/schemas/agent_chat.py
    - apps/api/app/schemas/widget.py
    - apps/api/app/api/v1/agent_chat.py
    - apps/api/app/api/v1/widget.py
    - apps/api/tests/unit/test_jwt.py
    - apps/api/tests/unit/test_agent_chat_routes.py
    - apps/api/tests/unit/test_widget_routes.py
  modified:
    - apps/api/app/main.py
decisions:
  - "events_url in AgentChatResponse uses /widget/jobs/{id}/events (public path) so admin callers get the same response shape as widget callers — they already have X-API-Key for /jobs/{id}/events if needed"
  - "SSE test patches event_generator directly (not Redis/DB mocks) to avoid 3-second POLL_INTERVAL_S hang — event_generator integration is already covered by test_sse.py"
  - "python-jose installed via pip install python-jose[cryptography] — was missing from local environment despite being in pyproject.toml"
metrics:
  duration_seconds: 846
  completed: "2026-05-16"
  tasks_completed: 2
  files_created: 7
  files_modified: 1
  tests_added: 25
---

# Phase 04 Plan 04: FastAPI Routes (Agent Chat + Widget) Summary

One-liner: JWT-authenticated widget routes (config/chat/SSE) and X-API-Key agent chat routes wired into main.py with Redis rate-limit, CORS headers, and public SSE endpoint reusing event_generator (R-03 resolution).

## Schema Field Shapes

### AgentChatRequest
| Field | Type | Validation |
|-------|------|------------|
| message | str | Field(min_length=1, max_length=2000) |
| conversation_id | UUID \| None | None (optional) |

### AgentChatResponse (202)
| Field | Type | Default |
|-------|------|---------|
| job_id | UUID | — |
| status | str | "pending" |
| events_url | str | "/widget/jobs/{job.id}/events" |
| conversation_id | UUID \| None | None |

### ConversationListItem
| Field | Type | Source |
|-------|------|--------|
| id | UUID | conversations.id |
| created_at | datetime | conversations.created_at |
| escalated | bool | metadata->>'escalated' (COALESCE false) |
| message_count | int | COUNT(*) subquery from messages |

### ConversationListResponse
| Field | Type |
|-------|------|
| conversations | list[ConversationListItem] |

### WidgetConfigResponse
| Field | Type |
|-------|------|
| agent_id | UUID |
| name | str |
| theming | dict |
| jwt | str |

### WidgetChatRequest / WidgetChatResponse
Mirror AgentChatRequest / AgentChatResponse exactly (same field shapes).

## Route Summary

| Path | Method | Auth | Status | Notes |
|------|--------|------|--------|-------|
| /agents/{agent_id}/chat | POST | X-API-Key | 202 | Dispatches run_agent_turn to runtime queue |
| /agents/{agent_id}/conversations | GET | X-API-Key | 200 | Returns top 50 conversations via psycopg2 |
| /widget/{agent_id}/config | GET | None (public) | 200 | Generates JWT; sets CORS + Cache-Control headers |
| /widget/{agent_id}/chat | POST | Bearer JWT | 202 | Rate-limited; dispatches run_agent_turn |
| /widget/jobs/{job_id}/events | GET | None (public) | 200 | SSE stream; reuses event_generator (R-03) |
| /widget/{agent_id}/config | OPTIONS | None | 204 | CORS preflight |
| /widget/{agent_id}/chat | OPTIONS | None | 204 | CORS preflight |
| /widget/jobs/{job_id}/events | OPTIONS | None | 204 | CORS preflight |

## JWT Helper Signatures + Claim Shape

```python
# widget.py — module-level, exported for tests

def create_widget_jwt(agent_id: str) -> str:
    """Returns HS256 JWT with {sub: 'widget', agent_id: str, exp: now+900}."""

def validate_widget_jwt(token: str, expected_agent_id: str) -> dict:
    """Returns decoded claims or raises HTTPException 401."""
```

Claim shape:
```json
{"sub": "widget", "agent_id": "<uuid-str>", "exp": <unix-timestamp>}
```

Algorithm: HS256 with settings.JWT_SECRET. Expiry: 900 seconds (15 minutes).

## Rate Limit Key Format + Threshold

```python
bucket = str(int(time.time()) // 60)   # rotates each 60-second window
key    = f"rate:{agent_id}:{bucket}"   # per-agent, per-minute
count  = await redis_client.incr(key)
if count == 1: await redis_client.expire(key, 60)
if count > 60: raise HTTPException(429, headers={"Retry-After": "60"})
```

Threshold: **60 requests per minute per agent_id** (T-04-04-06).

## R-03 SSE Endpoint Reuse (Verified)

`GET /widget/jobs/{job_id}/events` reuses `from app.services.sse import event_generator` without modification. The public endpoint only adds:
- `Access-Control-Allow-Origin: *` (EventSource cannot send Authorization headers)
- `X-Accel-Buffering: no` (nginx buffering prevention)
- `Cache-Control: no-store` (belt-and-suspenders)

Security: job_id is a server-generated UUID4 (~122 bits entropy) — cannot be guessed. Widget JWT validated separately at POST /widget/{id}/chat time.

## Unit Test Count Per File

| File | Tests | Coverage |
|------|-------|----------|
| test_jwt.py | 5 | create/validate: agent_id claim, expiry, tampered, expired, mismatch |
| test_agent_chat_routes.py | 9 | POST 202/404/409/422/403, GET conversations, API key guard (×2) |
| test_widget_routes.py | 11 | config 200/404, chat 202/401/401/429, SSE 200, OPTIONS 204 (×3) |
| **Total** | **25** | |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Dependency] python-jose not installed in local environment**
- **Found during:** Task 1 (pre-execution check)
- **Issue:** `python-jose[cryptography]` was in pyproject.toml but not pip-installed
- **Fix:** `pip install python-jose[cryptography]` — brought environment in line with declared deps
- **Files modified:** None (environment fix only)
- **Commit:** N/A

**2. [Rule 1 - Bug] SSE test hanging on event_generator poll loop**
- **Found during:** Task 2 (test execution)
- **Issue:** Initial mock used empty async generator for listen() but event_generator's Phase 2 loop uses `asyncio.wait_for(_next_pubsub_message, timeout=POLL_INTERVAL_S)` — causing a 3-second block per loop iteration
- **Fix:** Patched `app.api.v1.widget.event_generator` directly with a no-op async generator so the SSE response opens and closes immediately; the event_generator integration itself is tested in test_sse.py
- **Files modified:** apps/api/tests/unit/test_widget_routes.py
- **Commit:** 280de75

## Threat Surface Scan

No new trust boundaries introduced beyond what was specified in the plan's threat model. All mitigations in the STRIDE register (T-04-04-01 through T-04-04-09) are implemented:
- JWT stored in widget module scope only (enforced in Wave 5 widget plan)
- agent_id claim validated on every POST /widget/{id}/chat call
- JWTError caught and converted to 401
- Global CORSMiddleware unchanged (settings.CORS_ORIGINS preserved)
- Conversation ownership validated via WHERE id=%s AND agent_id=%s
- Redis INCR rate limit at 60/min per agent_id
- job_id UUID4 entropy guards SSE endpoint
- message NEVER logged
- Pydantic Field(max_length=2000) enforced

## Self-Check

Verified files exist:
- apps/api/app/schemas/agent_chat.py — FOUND
- apps/api/app/schemas/widget.py — FOUND
- apps/api/app/api/v1/agent_chat.py — FOUND
- apps/api/app/api/v1/widget.py — FOUND
- apps/api/tests/unit/test_jwt.py — FOUND
- apps/api/tests/unit/test_agent_chat_routes.py — FOUND
- apps/api/tests/unit/test_widget_routes.py — FOUND

Commits verified:
- d5030f1: feat(04-04): add agent_chat + widget FastAPI routers
- 280de75: feat(04-04): add JWT, agent_chat, and widget route unit tests

Route registration verified: all 5 paths present in app.routes

Test results: 25 passed, 0 failed

## Self-Check: PASSED
