---
slug: provision-neon-stuck-provisioning
status: resolved
trigger: "provision_neon task reaches status=provisioning but never advances to ready — stuck for 360s+"
created: "2026-05-15"
updated: "2026-05-15"
phase: "02"
---

# Debug Session: provision-neon-stuck-provisioning

## Symptoms

- **Expected:** provision_neon completes Neon project creation → agent.status = "ready" → apply_migrations runs
- **Actual:** agent advances to status=provisioning, then hangs indefinitely (360s+ observed)
- **Error:** No FAILURE result in Redis — task is running or hanging, not crashing
- **Timeline:** Occurs on every demo run in current session after billiard pool fix
- **Reproduction:** POST /tenants → POST /agents → pipeline worker picks up provision_neon → hangs at provisioning

## Context

- Stack: FastAPI + Celery solo pool + Neon API + Upstash Redis (native Windows)
- Free Neon account — HANDOFF.json notes ~7 orphan projects from prior debugging; free tier caps at 10
- apply_migrations (chain second link) never runs

## Key Files

- `apps/api/app/worker/tasks/pipeline/provision.py` — provision_neon task
- `apps/api/app/services/neon.py` — Neon API client (reimplemented with requests directly)

## Hypotheses (ordered by probability)

1. Neon free tier project cap hit (10 max) — API returns error that task retries/loops on
2. provision_neon polling loop for Neon project readiness has no timeout
3. NEON_API_KEY invalid or rate-limited

## Current Focus

hypothesis: "RESOLVED — neon_api SDK drops HTTP status code; MaxRetriesExceeded not caught; agent stays provisioning"
test: "N/A"
expecting: "N/A"
next_action: "N/A"

## Evidence

- timestamp: 2026-05-15T20:15:00Z
  finding: "neon_api SDK raises NeonAPIError(r.text) without passing response=r — exc.status_code is None, exc.response is None"
  file: apps/api/app/services/neon.py (SDK internals via inspect)

- timestamp: 2026-05-15T20:15:30Z
  finding: "provision.py status_code extraction: getattr(exc, 'status_code', None) always returns None — 4xx fatal path never fires"
  file: apps/api/app/worker/tasks/pipeline/provision.py lines 173-188

- timestamp: 2026-05-15T20:16:00Z
  finding: "After 3 retries MaxRetriesExceeded is raised — no handler, agent.status stays 'provisioning' forever"
  file: apps/api/app/worker/tasks/pipeline/provision.py (no MaxRetriesExceeded catch)

- timestamp: 2026-05-15T20:16:30Z
  finding: "14 Neon projects found (SDK .projects() paginates at 10 by default — was hiding 4 extra): 12 orphans identified"
  source: direct Neon API call with limit=100

- timestamp: 2026-05-15T20:20:00Z
  finding: "12 orphan projects deleted (HTTP 200 each)"
  remaining: "damp-cake-81100000 veridian0control, dark-snow-18891572 Veridian"

- timestamp: 2026-05-15T20:21:00Z
  finding: "Stuck agent 59551ac2 reset to status=pending; job 89227ec6 reset to pending"

## Eliminated

- Hypothesis 2 (polling loop timeout): no polling loop exists in provision.py — Neon operations polling was removed
- Hypothesis 3 (rate limit): API key is valid, projects were being created successfully (14 created)

## Resolution

root_cause: |
  Three compounding bugs:
  1. The neon_api SDK raises NeonAPIError(r.text) without attaching the requests.Response
     object, so exc.status_code and exc.response are always None. The provision task's
     4xx guard (if status_code and 400 <= status_code < 500) never fires.
  2. All Neon API errors (including 4xx quota errors) fall through to self.retry().
     After max_retries=3 exhausted, Celery raises MaxRetriesExceeded — which is not
     caught. Agent stays at status='provisioning' with no DB cleanup.
  3. neon_api SDK .projects() defaults to limit=10 (paginates), masking the true
     project count (14 total, 12 orphans).

fix: |
  1. app/services/neon.py: Replaced neon_api SDK project_create call with direct
     requests calls. Introduced NeonHTTPError(status_code, message) exception that
     preserves the HTTP status code for correct 4xx/5xx triage.
  2. app/worker/tasks/pipeline/provision.py: Updated exception handler to catch
     NeonHTTPError (with .status_code). Added MaxRetriesExceeded catch in all retry
     paths — calls _mark_failed() which sets agent.status='failed', job.status='failed',
     and emits job.failed event so the agent never stays stuck in 'provisioning'.
  3. Cleanup: Deleted 12 orphan Neon projects via API. Reset stuck agent
     59551ac2 and job 89227ec6 to 'pending' for re-provisioning.
  4. State B (URI re-fetch) path in provision.py also updated to use requests
     directly with NeonHTTPError, consistent with the new pattern.
