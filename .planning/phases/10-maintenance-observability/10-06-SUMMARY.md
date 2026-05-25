---
phase: 10-maintenance-observability
plan: "06"
subsystem: observability
tags: [demo, e2e-tests, celery, alerts, observability]
dependency_graph:
  requires: [10-05]
  provides: [demo_m10.sh, test_observability_e2e.py]
  affects: [scripts/, apps/api/tests/e2e/]
tech_stack:
  added: []
  patterns:
    - demo_m9.sh structure (pipefail header, env guards, agent create+poll loop, ALL_PASSED assertion)
    - test_strategy_e2e.py pattern (OPS_E2E_ENABLED guard, pytestmark, synchronous httpx)
key_files:
  created:
    - scripts/demo_m10.sh
    - apps/api/tests/e2e/test_observability_e2e.py
  modified: []
decisions:
  - "demo_m10.sh uses apply_async (not delay) for run_alert_check to require live worker"
  - "E2E tests use synchronous httpx.get/post; no async def; no AsyncClient"
  - "Docker mention removed from comment block to satisfy grep -ci docker = 0 check"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-25T14:19:31Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 0
requirements: [OPS-06]
---

# Phase 10 Plan 06: Demo + E2E Tests Summary

**One-liner:** M10 demo script (5-section observability workflow) and guarded E2E test (OPS_E2E_ENABLED gate, synchronous httpx, alerts list + resolve roundtrip).

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | scripts/demo_m10.sh | 1b6c660 | scripts/demo_m10.sh |
| 2 | test_observability_e2e.py | ab65409 | apps/api/tests/e2e/test_observability_e2e.py |

## Verification Results

### Task 1: demo_m10.sh

All acceptance criteria passed:

```
SYNTAX OK                          (bash -n)
set -euo pipefail count: 1         (exactly once)
python3 count: 0                   (python only, not python3)
docker count: 0                    (no docker references)
apply_async count: 2               (>= 1 required)
Celery worker refs: 3              (>= 1 required)
PASS lines: 2                      ([PASS] OPS-04 and [PASS] OPS-02/OPS-04)
```

### Task 2: test_observability_e2e.py

All acceptance criteria passed:

```
OPS_E2E_ENABLED refs: 7            (>= 2 required)
async def count: 0                 (synchronous tests only)
AsyncClient count: 0               (no AsyncClient)
httpx.get/post count: 4            (>= 2 required)
pytest result: 2 skipped / 0 failed / 0 errors  (guard works)
```

## Script Structure (demo_m10.sh)

1. **Prerequisites** — redis-cli ping, curl /health
2. **Create + Deploy Agent** — POST /api/v1/agents → extract AGENT_ID → poll ready → trigger deployment → poll is_deployed
3. **Trigger Alert Check** — `apply_async(kwargs={'agent_id': AGENT_ID})` from `apps/api/` dir; requires Celery worker running
4. **Alerts Health Check** — curl -w "%{http_code}" for /alerts endpoint → `[PASS] OPS-04: alerts endpoint returns 200`
5. **Beat Registration** — `celery inspect registered` → grep `digest-weekly` + `alert-daily` → `[PASS] OPS-02/OPS-04: beats registered`

## E2E Test Coverage

- `test_ops04_alerts_endpoint_returns_list` — GET /agents/{id}/alerts → 200 + isinstance(list)
- `test_ops04_alert_resolve_roundtrip` — GET alerts → resolve first → GET again → resolved id absent; skips if no alerts

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Security] Comment block wording adjusted**
- **Found during:** Task 1 acceptance check
- **Issue:** Comment `# Prerequisites (ALL local — no Docker):` contained the word "Docker" causing `grep -ci "docker"` to return 1 instead of 0 per the must-have criteria
- **Fix:** Changed comment to `# Prerequisites (ALL local — all services run natively, no containers):`
- **Files modified:** scripts/demo_m10.sh
- **Commit:** 1b6c660

## Human Checkpoint (Pending)

**Type:** human-verify
**Action required:** Start all 4 local services, then run:
```bash
ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m10.sh
```
**Expected:** `[PASS] OPS-04: alerts endpoint returns 200` and `[PASS] OPS-02/OPS-04: beats registered`. Script exits 0.

## Known Stubs

None — both artifacts are complete implementations, not stubs.

## Threat Flags

No new network endpoints, auth paths, or trust boundary surfaces introduced. `demo_m10.sh` reads `ADMIN_KEY` and `API_KEY` from env vars and does not echo them (T-10-06-01 mitigated). E2E tests are guarded by `OPS_E2E_ENABLED=1` (T-10-06-02 accepted).

## Self-Check: PASSED

- scripts/demo_m10.sh — exists, syntax valid
- apps/api/tests/e2e/test_observability_e2e.py — exists, 2 skipped / 0 failed
- Commits 1b6c660 and ab65409 — verified in git log
