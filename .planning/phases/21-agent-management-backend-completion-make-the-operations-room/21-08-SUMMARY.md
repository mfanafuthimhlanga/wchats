---
plan: 21-08
phase: 21
wave: 5
requirements-completed: [OPS-14, OPS-15]
status: complete
tasks: 3/3
completed: 2026-07-16
---

# 21-08 SUMMARY — Findings as first-class objects + deploy-gate wiring (OPS-14, OPS-15)

> **Note on authorship:** the executing agent was terminated by a weekly usage limit after
> committing Tasks 1–2 and after writing (but not committing) Task 3's changes. The
> orchestrator verified the on-disk Task-3 work against the plan, ran the targeted tests,
> committed it, and authored this summary. No work was lost or redone.

## What shipped

**OPS-14 — findings become first-class, triageable rows**
- `run_red_team` now writes **one `red_team_findings` row per finding** (severity, status),
  replacing the embedded `red_team_runs.findings` JSONB blob as the source of truth.
  Commit `588999b`.
- **Containing/closing a critical finding files it into `eval_scenarios`** with
  `source='red_team'` and `provenance=<finding-id>`, reusing the **shared
  `insert_provenance_scenario` path** introduced by 21-06 (one insertion path, two callers —
  production traces and red-team findings feed the same flywheel). Commit `a35c1fb`.

**OPS-15 — the deploy gate reads live findings**
- `_fetch_red_team_summary_sync` (`apps/api/app/services/deployment_service.py`) rewired to
  read the first-class table: `SELECT severity, COUNT(*) FROM red_team_findings WHERE
  status = 'open' GROUP BY severity`, with `deployment_blocked = critical_count > 0`.
- Consequence: a **live open critical finding drives `run_deployment_checklist` to
  `recommendation='block'`, so `POST /approve-deployment` returns 422** — and it does so
  regardless of which run produced the finding (a finding stays live across runs until
  contained/closed), which the old "latest run's JSONB" read could not express.
- `last_run_at` still comes from the most recent `red_team_runs` row (`red_team_findings`
  carries no run timestamp of its own) — documented in the function docstring.
- Commit `9d776ad`.

## Files changed
- `apps/api/app/worker/tasks/runtime/red_team.py` — per-finding row writes + contain/close → scenario filing
- `apps/api/app/services/deployment_service.py` — `_fetch_red_team_summary_sync` rewire
- `apps/api/tests/integration/test_deploy_gate_redteam.py` (new) — `INTEGRATION_TESTS_ENABLED`-gated roundtrip: spins an ephemeral tenant DB migrated to alembic head, seeds a live open critical finding, and exercises the **real** `_fetch_red_team_summary_sync` SQL (proving the signal reaching the recommendation came from a real Postgres read, not a stub)

## Verification
- `pytest tests/unit/test_deployment_task.py tests/integration/test_deploy_gate_redteam.py -q`
  → **3 passed, 3 skipped** (skips are the correctly-gated integration roundtrips).
- Source assertions: `red_team_findings` + `WHERE status = 'open'` + `deployment_blocked =
  counts["critical"] > 0` all present in `deployment_service.py`.
- Shared-path reuse confirmed: contain/close calls `insert_provenance_scenario(...,
  source='red_team')` rather than duplicating an insert.

## Known limitation (pre-existing, not introduced here)
`tests/unit/test_deployment_routes.py` cannot be collected because importing `app.main`
fails via `ragas → ragas.llms.base → langchain_community.chat_models.vertexai`
(`ModuleNotFoundError` — that module was sunset/moved in the installed
`langchain_community`). This is a **dependency-version mismatch in the environment**,
confirmed pre-existing on unmodified HEAD by multiple plans this phase (21-02, 21-05,
21-06, 21-09). It blocks *route-level* assertion of the 422 only; the blocking **signal**
itself is verified at the service layer and against a real DB by the integration test.
**Follow-up:** repair the ragas/langchain_community pairing, then assert the 422 end-to-end
at the `/gsd-verify-work 21` live gate.

## Deviations
None from the plan. One process deviation: Task 3 was committed by the orchestrator rather
than the executor (usage-limit termination, see note above).
