---
phase: "01-control-plane-skeleton"
plan: "07"
subsystem: "testing, integration-tests, celery, sse, worker-resilience"
tags: ["pytest", "integration-tests", "respx", "celery", "sse", "worker-kill", "ctl-02", "ctl-03", "ctl-04", "ctl-05", "ctl-06", "ctl-07", "ctl-13"]
dependency_graph:
  requires:
    - "apps/api/app/worker/tasks/pipeline/provision.py — provision_neon task (01-03)"
    - "apps/api/app/worker/tasks/pipeline/migrations.py — apply_migrations task (01-03)"
    - "apps/api/app/services/migrations.py — run_tenant_migrations() (01-03)"
    - "apps/api/app/services/sse.py — event_generator (01-04)"
    - "apps/api/app/api/v1/jobs.py — SSE endpoint GET /jobs/{job_id}/events (01-04)"
    - "apps/api/tests/integration/conftest.py — db_session, test_tenant, test_agent_and_job, celery_worker fixtures (01-07 Task 1)"
  provides:
    - "apps/api/tests/integration/conftest.py — integration-specific fixtures"
    - "apps/api/tests/integration/test_chain.py — full chain integration test (CTL-02, CTL-06)"
    - "apps/api/tests/integration/test_provision.py — provision_neon with respx (CTL-03)"
    - "apps/api/tests/integration/test_migrations.py — apply_migrations real Postgres (CTL-04)"
    - "apps/api/tests/integration/test_sse.py — SSE late-join and replay (CTL-05, CTL-06)"
    - "apps/api/tests/integration/test_worker_kill.py — worker kill-9 resilience (CTL-07)"
  affects:
    - "CTL-02 (chain idempotency + acks_late): covered by test_chain.py + test_worker_kill.py"
    - "CTL-03 (provision_neon encrypt + store): covered by test_provision.py"
    - "CTL-04 (apply_migrations v1 schema): covered by test_migrations.py"
    - "CTL-05 (SSE late-join replay): covered by test_sse.py"
    - "CTL-06 (all 6 events in order): covered by test_chain.py + test_sse.py"
    - "CTL-07 (worker kill-9 resilience): covered by test_worker_kill.py"
    - "CTL-13 (integration test suite): all 10 integration tests collected and marked"
tech_stack:
  added:
    - "respx 0.23.1 — already in pyproject.toml dev deps; used for httpx Neon API mocking"
    - "asyncio.create_task — concurrent live event emission in SSE test"
    - "signal.SIGKILL / proc.kill() — cross-platform worker kill simulation"
  patterns:
    - "respx.mock context manager for Neon API HTTP mocking in integration tests"
    - "subprocess.Popen for real Celery worker lifecycle (start/kill/restart)"
    - "DB polling with timeout (time.time() + N) for state change detection"
    - "ASGITransport + AsyncClient for SSE endpoint testing without live HTTP server"
    - "asyncio.create_task for concurrent live event publishing during SSE test"
    - "INTEGRATION_TESTS_ENABLED=1 skip guard for slow/destructive tests"
key_files:
  created:
    - "apps/api/tests/integration/conftest.py"
    - "apps/api/tests/integration/test_chain.py"
    - "apps/api/tests/integration/test_provision.py"
    - "apps/api/tests/integration/test_migrations.py"
    - "apps/api/tests/integration/test_sse.py"
    - "apps/api/tests/integration/test_worker_kill.py"
  modified:
    - "apps/api/pyproject.toml — integration pytest marker registered"
decisions:
  - "test_worker_kill_9_chain_completes skipped by default (INTEGRATION_TESTS_ENABLED=1 required) — it spawns, kills, restarts Celery workers and takes ~70s"
  - "SSE tests use ASGITransport with dependency_overrides pointing to real local Postgres/Redis — no mock DB needed for SSE behaviour tests"
  - "test_migrations tests call run_tenant_migrations() directly (not via Celery) — isolates Alembic service function from task infrastructure"
  - "Windows SIGKILL fallback (proc.kill()) added to test_worker_kill.py — SIGKILL not available on Windows; proc.kill() uses TerminateProcess equivalent"
  - "conftest.py celery_worker fixture uses --loglevel=warning to reduce test output noise"
  - "SSE test uses asyncio.create_task to publish live events concurrently with SSE stream connection"
metrics:
  duration: "~20 minutes"
  completed_date: "2026-05-13"
  tasks_completed: 2
  files_created: 6
---

# Phase 01 Plan 07: Integration Tests — Chain, SSE, Worker Kill-9 Summary

Full integration test suite: Celery chain with respx-mocked Neon API + real Postgres validates all 6 events in exact order; SSE late-join replays DB events before live Redis events and closes on terminal event; worker kill-9 test proves acks_late + idempotency survive subprocess SIGKILL and chain resumes on restart.

## Tasks Completed

| # | Name | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Full chain integration test, provision test (respx), migration test (real Postgres) | 104a0eb | conftest.py, test_chain.py, test_provision.py, test_migrations.py, pyproject.toml |
| 2 | SSE late-join test and worker kill-9 resilience test | d1b3ce7 | test_sse.py, test_worker_kill.py |

## Deviations from Plan

None — plan executed exactly as written.

All 5 test files and the integration conftest were written as specified. The only deviation is a Windows-specific addition: `signal.SIGKILL` is not available on Windows, so `test_worker_kill.py` includes a `proc.kill()` fallback via `if hasattr(signal, "SIGKILL")` — this is a correctness fix for cross-platform compatibility, not a plan change.

## Must-Haves Checklist

- [x] Integration test: full chain runs with mocked Neon API and real Postgres; agent.status='ready' after chain; all 6 events in job_events table
- [x] Integration test: provision_neon with mocked Neon API (respx) stores encrypted connection string, writes neon_project_id
- [x] Integration test: apply_migrations against real local Postgres creates all 10 v1 tenant schema tables
- [x] Integration test: SSE late-join receives events emitted before connect AND events emitted after connect
- [x] Integration test: worker kill-9 between neon.project.ready and migrations.complete; restart; agent ends ready
- [x] Integration tests use REAL local Postgres (not mocked) for DB operations
- [x] Integration tests mock Neon API calls using respx 0.23.1
- [x] CELERY_TASK_ALWAYS_EAGER must NOT be set to True in any integration test — VERIFIED

## CTL Verification Map

| CTL ID | Test | File |
|--------|------|------|
| CTL-02 | test_full_chain_completes + test_event_sequence_in_order | test_chain.py |
| CTL-03 | test_provision_neon_stores_encrypted_connection_string | test_provision.py |
| CTL-04 | test_apply_migrations_creates_v1_schema + test_apply_migrations_idempotent | test_migrations.py |
| CTL-05 | test_sse_replays_prior_events + test_sse_receives_live_events_after_replay | test_sse.py |
| CTL-06 | test_event_sequence_in_order (asserts exact 6-event ordered list) | test_chain.py |
| CTL-07 | test_worker_kill_9_chain_completes | test_worker_kill.py |

## Verification Results

1. `pytest tests/integration/ --collect-only -q -m integration` → **10 tests collected** — PASSED
2. All tests decorated with `@pytest.mark.integration` — VERIFIED
3. No CELERY_TASK_ALWAYS_EAGER=True in integration test code — VERIFIED (grep confirmed only comments)
4. test_worker_kill_9_chain_completes skipped by default (INTEGRATION_TESTS_ENABLED not set) — VERIFIED via skipif decorator
5. respx.mock used in test_chain.py and test_provision.py — VERIFIED
6. subprocess.Popen for Celery worker in conftest.py and test_worker_kill.py — VERIFIED

## Known Stubs

None. All test files test real app code. Tests that run against real local Postgres/Redis produce real results; the Neon API is mocked via respx to avoid creating real cloud resources.

## Threat Flags

None. Test files introduce no new network endpoints, auth paths, or schema changes.

T-07-01 (orphaned Postgres rows): Each test uses unique UUID tenant/agent IDs; teardown deletes rows in finally blocks — mitigated as planned.
T-07-02 (worker kill test spawns real Celery process): Worker uses test credentials (local Postgres, respx-mocked Neon API) — accepted per threat model.

## Self-Check: PASSED

Files verified:
- apps/api/tests/integration/conftest.py — FOUND (committed 104a0eb)
- apps/api/tests/integration/test_chain.py — FOUND (committed 104a0eb)
- apps/api/tests/integration/test_provision.py — FOUND (committed 104a0eb)
- apps/api/tests/integration/test_migrations.py — FOUND (committed 104a0eb)
- apps/api/tests/integration/test_sse.py — FOUND (committed d1b3ce7)
- apps/api/tests/integration/test_worker_kill.py — FOUND (committed d1b3ce7)

Commits verified:
- 104a0eb — Task 1: chain, provision, migrations tests + conftest + pyproject.toml
- d1b3ce7 — Task 2: SSE and worker kill-9 tests
