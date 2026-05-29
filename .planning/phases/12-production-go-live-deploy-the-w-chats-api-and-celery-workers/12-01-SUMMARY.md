---
phase: 12-production-go-live-deploy-the-w-chats-api-and-celery-workers
plan: "01"
subsystem: runtime-agent
tags: [celery, agent-sdk, voyage-rate-limit, redis-cache, unit-tests]
dependency_graph:
  requires: []
  provides:
    - max_turns=3 guard in run_agent_turn (D-10)
    - timeout=90s wall-clock guard in run_agent_turn (D-11)
    - retrieve-at-most-once system-prompt instruction (D-10 belt-and-suspenders)
    - Redis qembed: query-embedding cache in retrieve_tool (D-13)
  affects:
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/app/services/agent_tools.py
tech_stack:
  added: []
  patterns:
    - redis read-through cache with sha256 key + setex TTL
    - asyncio.wait_for timeout raised from 30s to 90s
    - ClaudeAgentOptions max_turns reduced from 10 to 3
key_files:
  created: []
  modified:
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/tests/unit/test_agent_task.py
    - apps/api/app/services/agent_tools.py
decisions:
  - "[12-01] D-10 dual guard: max_turns=3 + system-prompt AT MOST ONCE instruction — belt-and-suspenders because max_turns caps the loop but not per-turn retrieve count"
  - "[12-01] D-11 timeout raised 30s → 90s — SDK subprocess warm-up on ARM VM requires more headroom; SSE layer retains 120s (30s gap intact)"
  - "[12-01] D-13 included as low-complexity/high-value: lazy _get_qembed_redis() with try/except fallback — cache is optimisation, never correctness dependency"
  - "[12-01] qembed cache uses module-level lazy client (same ssl/url pattern as agent.py) — no new dependency, no new module-level import-time failure surface"
metrics:
  duration: "~12 min"
  completed: "2026-05-29"
  tasks: 2
  files: 3
---

# Phase 12 Plan 01: Agent Turn Hardening (D-10, D-11, D-13) Summary

**One-liner:** Surgical two-edit agent.py hardening — max_turns=3 + 90s timeout to survive Voyage 3RPM free tier and ARM VM warm-up, plus Redis qembed cache for repeat-query $0 path.

## Tasks Completed

| # | Task | Commit | Status |
|---|------|--------|--------|
| 1 | D-10 retrieve cap + D-11 wall-clock guard in agent.py with unit tests | 15468e2 | Done |
| 2 | D-13 optional Redis query-embedding cache in retrieve_tool | 61d8ced | Done |

## What Was Built

### Task 1 — agent.py surgical edits (D-10 + D-11)

**D-10 retrieve cap (two guards):**
- `max_turns=10` → `max_turns=3` in `ClaudeAgentOptions` constructor
- System-prompt append immediately after `build_system_prompt(agent)`:  
  `"IMPORTANT: Call the retrieve tool AT MOST ONCE per response. ..."`
- Stale comment on `_run_sdk_turn` docstring updated (timeout=30 → 90)
- Stale comment block near the `asyncio.wait_for` call updated (max_turns=10 → 3, timeout=30 → 90)

**D-11 wall-clock guard:**
- `asyncio.wait_for(..., timeout=30)` → `timeout=90`
- SSE layer `asyncio.timeout(120)` in widget.py left untouched (30s headroom preserved)

**Regression tests added to `test_agent_task.py`:**
- `test_max_turns_capped_to_three` — `FakeClaudeAgentOptions` captures kwargs, asserts `max_turns == 3`
- `test_wall_clock_guard_is_ninety_seconds` — patches `asyncio.wait_for` with fake coroutine, asserts `timeout == 90`
- Full file: 10/10 tests pass (8 pre-existing + 2 new)

**CLAUDE.md invariants verified:**
- `acks_late=True` on decorator (line 373): intact
- Idempotency guard (early-return when `agent.response` event exists): intact

### Task 2 — agent_tools.py Redis cache (D-13)

Added to `apps/api/app/services/agent_tools.py`:
- New imports: `hashlib`, `json`, `ssl`, `redis as redis_lib`
- `_get_qembed_redis()` — lazy module-level sync Redis client using same `rediss://` + `ssl_cert_reqs=CERT_NONE` pattern as `agent.py`
- `_embed_with_cache(q)` inner function inside `retrieve_tool`:
  - Cache key: `qembed:<sha256-hex-of-query-utf8>`
  - Cache hit: `json.loads(cached)` — zero Voyage calls
  - Cache miss: call `embed_query(q)`, then `setex(key, 3600, json.dumps(vector))`
  - Any exception: `log.warning` + fall back to direct `embed_query(q)`
- No new `pyproject.toml` dependency; `redis` already in dependencies

## Deviations from Plan

None — plan executed exactly as written. D-13 was marked as included (planner's discretion) and implemented cleanly without needing to refactor `retrieval_service.py` or the retrieve tool boundary.

## Verification Results

```
# D-10 grep
grep -n "max_turns=3" apps/api/app/worker/tasks/runtime/agent.py
→ 510, 532, 540 (comment + constructor + updated comment)

# D-11 grep
grep -n "timeout=90" apps/api/app/worker/tasks/runtime/agent.py
→ 539, 555 (comment + wait_for call)

# No stale values remain
grep -n "max_turns=10" → 0 matches
grep -n "timeout=30" in wait_for guard → 0 matches (docstring ref updated)

# INVARIANTS
grep -n "acks_late=True" → line 373 (intact)
grep -n "agent.response" → lines 10, 26, 389, 409, 416, 590, 606, 610 (idempotency guard intact)

# D-13 grep
grep -n "qembed:" apps/api/app/services/agent_tools.py → lines 233, 238, 243

# D-09 (no paid path)
grep -rn "voyage-payment|paid embedder" apps/api/app/ → 0 matches

# Tests
pytest tests/unit/test_agent_task.py -q → 10 passed, 4 warnings
```

## Known Stubs

None — all changes are production-ready code with no stubs or placeholders.

## Threat Flags

No new network endpoints, auth paths, or schema changes introduced. The query-embedding cache stores non-reversible float vectors over existing rediss:// TLS (T-12-01-03 / T-12-01-04 both `accept` disposition per plan threat model).

## Self-Check: PASSED

- `apps/api/app/worker/tasks/runtime/agent.py` — modified, committed 15468e2
- `apps/api/tests/unit/test_agent_task.py` — modified, committed 15468e2
- `apps/api/app/services/agent_tools.py` — modified, committed 61d8ced
- `pytest tests/unit/test_agent_task.py -q` → 10 passed
