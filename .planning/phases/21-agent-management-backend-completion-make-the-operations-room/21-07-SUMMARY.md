---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 07
subsystem: api
tags: [red-team, alembic, psycopg2, fastapi, coverage-matrix, ops-13]

# Dependency graph
requires:
  - phase: 21-06
    provides: tenant migration 0011 (eval_scenarios provenance) as the down_revision chain point
provides:
  - Tenant migration 0012 (red_team_strategies, red_team_probes, red_team_findings)
  - run_red_team first-class strategy/probe writes (idempotent upsert)
  - redteam_programme_service.read_programme (coverage rollup, ASR per cell)
  - GET /agents/{id}/red-team/programme endpoint
affects: [21-08 (populates red_team_findings + rewires deploy gate to read from it)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Coverage matrix as first-class DB object (harm-category x attack-strategy), not a per-run JSONB blob"
    - "UNIQUE(attack_vector) + ON CONFLICT DO NOTHING for idempotent strategy upsert inside an existing Celery task connection"
    - "Minimal FastAPI()-around-just-the-router test pattern (avoids app.main's pre-existing ragas/vertexai ModuleNotFoundError)"

key-files:
  created:
    - apps/api/alembic_tenant/versions/0012_red_team_programme.py
    - apps/api/app/services/redteam_programme_service.py
    - apps/api/tests/unit/test_migration_0012.py
    - apps/api/tests/unit/test_redteam_programme.py
  modified:
    - apps/api/app/worker/tasks/runtime/red_team.py
    - apps/api/app/api/v1/red_team.py

key-decisions:
  - "red_team_strategies/red_team_probes are global within the tenant DB (no agent_id column) because each agent already has its own dedicated Neon DB — mirrors the existing red_team_runs table, which also has no agent_id."
  - "harm_category is written as NULL on red_team_probes inserts — RedTeamFinding (red_team_service.py) has no harm_category field today; the column exists in the schema for 21-08 or a future finer-grained harm-classification pass."
  - "red_team_findings is created (3 tables, CHECK constraints, indexes) but intentionally NOT populated here and _fetch_red_team_summary_sync is untouched — that write path + deploy-gate rewire is 21-08's scope per the plan's prohibition."
  - "Coverage rollup ASR is findings_count / probes_tested, defaulting to 0.0 (never a divide-by-zero) when probes_tested is 0 — honest empty until 21-08 starts writing findings."

patterns-established:
  - "First-class programme tables (strategies/probes/findings) read via a single psycopg2 connection making 3 sequential queries (list strategies, list probes, coverage rollup), matching the deployment_service.py read idiom."

requirements-completed: [OPS-13]

# Metrics
duration: ~35min
completed: 2026-07-16
status: complete
---

# Phase 21 Plan 07: Red-Team Programme (OPS-13) Summary

**Migration 0012 creates red_team_strategies/red_team_probes/red_team_findings as first-class tables; run_red_team now upserts strategy/probe rows per finding; GET /agents/{id}/red-team/programme returns a harm-category x attack-strategy coverage rollup with ASR per cell.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 3/3 completed
- **Files modified:** 6 (2 new source files, 2 modified source files, 2 new test files)

## Accomplishments
- Tenant migration 0012 (down_revision 0011) creates red_team_strategies, red_team_probes, and red_team_findings with severity/status CHECK constraints and idempotent UNIQUE(attack_vector)
- run_red_team persists first-class strategy/probe rows (idempotent upsert) after the existing findings/run-row write, without touching the acks_late/idempotency guard
- New GET /agents/{id}/red-team/programme route returns {strategies, probes, coverage}, IDOR-guarded (404-not-403), with honest-empty coverage (ASR 0.0, no divide-by-zero) when no probes have been tested yet

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0012 — red_team_strategies + red_team_probes + red_team_findings** - `bcd73df` (feat)
2. **Task 2: run_red_team writes first-class strategy + probe rows** - `c285e65` (feat)
3. **Task 3: coverage rollup service + GET /agents/{id}/red-team/programme** - `7a696d6` (feat)

**Plan metadata:** committed alongside this summary by the orchestrator.

## Files Created/Modified
- `apps/api/alembic_tenant/versions/0012_red_team_programme.py` - Tenant migration: 3 CREATE TABLE IF NOT EXISTS (strategies, probes, findings), severity/status CHECKs, findings indexes, reverse-order downgrade
- `apps/api/app/worker/tasks/runtime/red_team.py` - Step 7b: idempotent strategy upsert (ON CONFLICT DO NOTHING) + one probe row per finding, on the existing `_agents_conn`, best-effort try/except so a write failure can't break the run
- `apps/api/app/services/redteam_programme_service.py` - `read_programme(conn_str, agent_id)`: lists strategies + probes, computes the coverage rollup (probes_tested, findings_count, high_severity_count, attack_success_rate) via a LEFT JOIN across strategies/probes/findings
- `apps/api/app/api/v1/red_team.py` - New `GET /agents/{agent_id}/red-team/programme` route, reusing the existing IDOR + conn_str resolution pattern from the other 3 red_team routes
- `apps/api/tests/unit/test_migration_0012.py` - Source assertions (revision/down_revision, table/column tokens, CHECK clauses, IF NOT EXISTS guards, downgrade ordering) + INTEGRATION_TESTS_ENABLED-gated DB roundtrip
- `apps/api/tests/unit/test_redteam_programme.py` - Writes tests (`-k writes`, strategy/probe insert counts + ON CONFLICT + run-row UPDATE unaffected), service tests (mocked-cursor coverage computation, honest-empty ASR), route tests (IDOR 404, agent-not-found 404, no-conn-string 404, happy path, empty-programme 200)

## Decisions Made
- red_team_strategies/red_team_probes carry no agent_id column — each agent has its own dedicated Neon DB, so the connection itself is the scoping boundary (same pattern as the pre-existing red_team_runs table).
- probe rows are inserted per-finding (not deduped by probe_message) — matches the plan's task language ("one probe row per finding's probe_message") and keeps the write path simple; dedup can be layered in later if the volume warrants it.
- Coverage rollup query joins on `strategy_id` only (not run_id) since red_team_findings isn't populated yet in this plan — the query already supports non-zero ASR once 21-08 starts writing findings rows.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None. Confirmed the pre-existing `app.main` -> `app.api.v1.evals` -> `ragas.llms.base` -> `langchain_community.chat_models.vertexai` `ModuleNotFoundError` is present on HEAD (reproduced via `pytest tests/unit -k red_team -q`, which collects unrelated route test files that import `app.main`); route tests for this plan use the minimal-`FastAPI()`-around-just-the-router pattern per the plan's guidance and are unaffected.

## User Setup Required

None - no external service configuration required. Migration 0012 applies automatically the next time a tenant DB runs its Alembic upgrade path (same mechanism as 0006/0011).

## Next Phase Readiness

- red_team_findings table exists with the right shape (run_id/strategy_id/probe_id FKs, severity/status CHECKs) for 21-08 to populate and wire to the deploy gate.
- redteam_programme_service.read_programme's coverage-rollup query already joins against red_team_findings — 21-08 only needs to start writing rows there; no query changes required.
- GET /agents/{id}/red-team/programme is live and registered (red_team.router already included in main.py) — the admin UI can start consuming it immediately, with coverage cells showing ASR 0.0 until 21-08 lands.

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-16*

## Self-Check: PASSED

All 6 claimed files found on disk; all 3 task commit hashes (bcd73df, c285e65, 7a696d6) found in git log.
