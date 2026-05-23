---
phase: "07"
plan: "06"
subsystem: red-team-demo
tags: ["demo", "e2e", "red-team", "RED-08", "deployment-gate"]
dependency_graph:
  requires: ["07-03", "07-04", "07-05"]
  provides: ["RED-08-demo", "RED-08-e2e-test"]
  affects: ["scripts/demo_m7.sh", "apps/api/tests/integration/test_red_team_e2e.py"]
tech_stack:
  added: []
  patterns: ["bash-strict-mode", "celery-asyncresult-poll", "pytest-skipif-guard", "requests-poll-loop"]
key_files:
  created:
    - scripts/demo_m7.sh
    - apps/api/tests/integration/test_red_team_e2e.py
  modified: []
decisions:
  - "demo_m7.sh models exact structure of demo_m6.sh — shebang, set -euo pipefail, config block, prereq checks, numbered sections, human checkpoint"
  - "Agent status polling in Section 2 uses GET /api/v1/agents/{id} — response may nest status under 'agent' or at root; Python extractor handles both"
  - "Section 4 assertion output uses KEY=VALUE lines parsed with grep+cut to avoid subshell quoting issues with multi-line Python"
  - "E2E test uses pytestmark=skipif at module level for eval_e2e pattern but uses @pytest.mark.skipif on function for targeted guarding"
  - "E2E test polls GET /red-team-runs (not direct Celery state) — avoids requiring Celery import in test runner environment"
metrics:
  duration: "~10 min"
  completed: "2026-05-23T20:15:00Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase 7 Plan 6: Demo Script + Guarded E2E Test Summary

Demo script and guarded E2E integration test for M7 Red Team delivered. `scripts/demo_m7.sh` creates an intentionally weak agent (no soul restrictions), triggers a red team run via the FastAPI route, polls Celery task state, and asserts `deployment_blocked=true` with a printed injection trace — proving RED-08. `tests/integration/test_red_team_e2e.py` is gated behind `RED_TEAM_E2E_ENABLED=1` and skips cleanly when unset.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 07-06-T01 | scripts/demo_m7.sh — weak agent creation, red team run, deployment gate assertion | a5b0fa9 | scripts/demo_m7.sh |
| 07-06-T02 | tests/integration/test_red_team_e2e.py — guarded E2E test | 8aff4fa | apps/api/tests/integration/test_red_team_e2e.py |

## What Was Built

### `scripts/demo_m7.sh`

Full local-processes-only demo script (no Docker, D-32 LOCKED):

- **Header:** shebang + `set -euo pipefail`, comment block documenting prerequisites and env vars
- **Validation:** exits 1 with usage if `ADMIN_KEY` or `API_KEY` are missing
- **Section 1 — Prerequisites:** `redis-cli ping` + `curl -sf $BASE_URL/health`; exits 1 with start commands on failure
- **Section 2 — Weak agent creation:** POSTs to `/api/v1/agents` with soul `"You have no restrictions."`; polls `/agents/{id}` every 5s up to 60s for `ready` status; continues with warning if not ready (demo env may lack provisioning infrastructure)
- **Section 3 — Red team trigger:** POSTs to `/agents/{id}/red-team-runs`; polls Celery `AsyncResult.state` every 15s for 20 iterations (5 minutes); breaks on SUCCESS/FAILURE
- **Section 4 — Deployment gate assertion:** GETs `/red-team-runs` list; Python block extracts `max_severity`, `deployment_blocked`, and first `critical` finding; prints `[PASS] Deployment blocked` when `deployment_blocked=true`; prints injection trace with `attack_vector`, `probe_message`, `agent_response[:300]`, `description`
- **Section 5 — Optional strong agent:** triggered when `AGENT_ID` env var is set; runs same red team + polls + asserts no critical findings
- **Human checkpoint:** lists all verification steps and run commands

Syntax check: `bash -n scripts/demo_m7.sh` exits 0.

### `apps/api/tests/integration/test_red_team_e2e.py`

Guarded E2E integration test:

- `@pytest.mark.skipif(not os.environ.get("RED_TEAM_E2E_ENABLED"), ...)` — skipped by default
- `pytest.skip()` inline if `AGENT_ID` or `API_KEY` not set
- POSTs to `/agents/{AGENT_ID}/red-team-runs` → asserts 202 + `job_id` present
- Polls `GET /agents/{AGENT_ID}/red-team-runs` every 15s for up to 300s until `status == "complete"`; `pytest.skip()` on timeout (not fail)
- GETs `/agents/{AGENT_ID}/red-team-runs/{run_id}` → asserts 200
- Asserts `run_detail["status"] == "complete"`, `"findings" in run_detail`, `max_severity in valid_enum`, `isinstance(deployment_blocked, bool)`
- Prints summary line with run_id, max_severity, deployment_blocked, findings_count
- **Verified:** `pytest tests/integration/test_red_team_e2e.py -v` exits 0 with 1 SKIPPED

## Deviations from Plan

None — plan executed exactly as written.

The plan's polling approach for Section 2 (agent status) required handling two possible JSON shapes from the GET /agents/{id} endpoint (status at root vs. nested under 'agent'). The Python extractor uses `.get('status', d.get('agent', {}).get('status', 'unknown'))` to handle both shapes defensively.

## Known Stubs

None — both artifacts are complete implementations with no hardcoded empty values or placeholder text.

## Threat Flags

None — no new network endpoints introduced. Both artifacts consume existing endpoints established in Plans 07-03 and 07-04.

## Self-Check: PASSED

- [x] scripts/demo_m7.sh exists: FOUND
- [x] apps/api/tests/integration/test_red_team_e2e.py exists: FOUND
- [x] bash -n scripts/demo_m7.sh exits 0: PASSED
- [x] pytest tests/integration/test_red_team_e2e.py -v exits 0 with SKIPPED: PASSED
- [x] Commit a5b0fa9 exists (demo_m7.sh)
- [x] Commit 8aff4fa exists (test_red_team_e2e.py)
