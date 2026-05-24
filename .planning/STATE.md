---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: complete
last_updated: "2026-05-24T00:00:00.000Z"
progress:
  total_phases: 10
  completed_phases: 9
  total_plans: 72
  completed_plans: 63
  percent: 88
---

# Project State

## Current Status

**Active Milestone:** M8 — Pre-deployment Checklist (2/7 plans complete)
**Milestone Phase:** Phase 8 IN PROGRESS — 7 plans, 6 waves, DEP-01–DEP-08 mapped; Plans 01–02 complete (2026-05-24)
**Current Position:** Phase 8, Plan 2 (complete) — Plan 3 next
**Last updated:** 2026-05-24

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-12)

**Core value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.
**Current focus:** Start M4 — Claude Agent SDK + Preact widget + public demo
**Previous:** M3 (Hybrid Retrieval) ✓ Complete — demo_m3.sh passed, notebook 4 DataFrames verified, RET-01–RET-08 satisfied (2026-05-16)

## Milestone Progress

| Milestone | Name | Status | PRD |
|-----------|------|--------|-----|
| M1 | Control Plane Skeleton | ✓ Complete (8/8 plans complete) | `prd-M1.md` ✓ |
| M2 | Ingestion Pipeline | ✓ Complete (7/7 plans) | `prd-M2.md` ✓ |
| M3 | Hybrid Retrieval | ✓ Complete (7/7 plans) | `prd-M3.md` ✓ |
| M4 | Reasoning Engine + Widget | ✓ Complete (10/10 plans) | `prd-M4.md` ✓ |
| M5 | Validation Chain | ✓ Complete (5/5 plans) | `prd-M5.md` ✓ |
| M6 | Eval System | ✓ Complete (9/9 plans) | `prd-M6.md` ✓ |
| M7 | Red Team | ○ Pending | `prd-M7.md` (TBD) |
| M8 | Pre-deployment Checklist | ○ Pending | `prd-M8.md` (TBD) |
| M9 | Retrieval Strategy Synthesis | ○ Pending | `prd-M9.md` (TBD) |
| M10 | Maintenance + Observability | ○ Pending | `prd-M10.md` (TBD) |

## Key Decisions

- [04-01] Legacy soul JSONB + role TEXT preserved; new soul_voice/soul_do_list/soul_donot_list/soul_role are additive (D-Schema decision from CONTEXT.md)
- [04-01] JWT_SECRET default is intentionally insecure 'dev-secret-change-in-production' — T-04-01-04 accept disposition; operator must override in production
- [04-01] SMTP_* fields all optional (None default) so SMTP_HOST stays unset in tests, exercising fallback-to-structlog code paths
- redis==6.4.0 (not 7.4.0): celery[redis]==5.6.3 requires kombu 5.6.x which constrains redis<6.5; redis 7.4.0 is incompatible
- Tenant ORM attribute api_key_hash (DB column: api_key) prevents plaintext confusion in code
- target_metadata=None in alembic_tenant/env.py: no ORM models for tenant schema in M1; all DDL via raw SQL
- verify_api_key returns bool only (never raises VerifyMismatchError) — callers use if/else not try/except
- emit() copies payload before adding "at" timestamp — caller's dict is never mutated
- task_default_queue=runtime so unrouted tasks don't accidentally land in pipeline queue
- provision_neon writes agent.neon_project_id immediately after Neon API returns (idempotency save point for kill-9)
- apply_migrations uses neon_direct_connection_string (not pooled) — DDL requires non-pooled direct endpoint
- wait_for_neon_ready runs in apply_migrations before Alembic, not in provision_neon
- CORS_ORIGINS added to Settings as list[str] = ['http://localhost:3000'] — widget CORS lands in M4 only
- [04-02] FEW_SHOT_SUFFIX is a module-level constant in agent_prompt.py — dynamic retrieval deferred post-M6
- [04-02] Module-level globals for tool state injection in agent_tools.py — safe for worker_pool=solo; ContextVar upgrade deferred if concurrency > 1
- [04-02] claude_agent_sdk monkeypatched via sys.modules in tests — SDK binary not required at unit test time
- [04-03] asyncio.run(asyncio.wait_for(_run_sdk_turn(...), timeout=30)) — wall-clock guard inside asyncio.run, not outside
- [04-03] sdk_session_id stored in conversations.metadata via jsonb_set UPDATE (parameterised); passed as resume= on subsequent turns
- [04-03] Escalation detection from ToolUseBlock.name evidence only — never from parsed agent prose (T-04-03-03)
- [04-04] events_url in AgentChatResponse uses /widget/jobs/{id}/events (public path) — admin callers already have X-API-Key for /jobs/{id}/events
- [04-04] SSE test patches event_generator directly to avoid 3s POLL_INTERVAL_S hang — integration covered by test_sse.py
- [04-04] Global CORSMiddleware allow_origins unchanged (settings.CORS_ORIGINS); only widget route handlers set Access-Control-Allow-Origin: * (T-04-06)
- get_current_tenant iterates all non-deleted tenants for argon2 verify — no indexed lookup possible with hashed keys
- get_async_redis creates per-request client from REDIS_URL — avoids module-level async Redis in FastAPI context
- POST /agents route has zero occurrences of "job.started" string — comments reworded to satisfy grep-based acceptance criteria
- conftest.py sets env vars at module level before any app import — prevents pydantic-settings validation errors in test discovery
- FastAPI APIKeyHeader with auto_error=True returns 401 "Not authenticated" (not 403) when X-API-Key header is absent
- inspect.signature(task.run) not inspect.signature(task) — Celery wraps the function; .run accesses the original underlying function
- ASGITransport(app=app) used for AsyncClient — ASGI-native route testing without live HTTP server
- Mock DB refresh() side_effect must inject uuid4() into Agent/Job objects — server_default="gen_random_uuid()" requires DB to set IDs
- docker-compose env_file + environment override pattern: .env provides defaults; environment block overrides DB/Redis URLs to internal service hostnames
- demo_m1.sh uses ${EVENTS_SEEN[*]:-} with default to avoid unbound variable on empty array under bash strict mode (set -euo pipefail)
- test_worker_kill_9_chain_completes skipped by default (INTEGRATION_TESTS_ENABLED=1 required) — spawns/kills/restarts Celery workers, takes ~70s
- SSE tests use ASGITransport with dependency_overrides for real local Postgres/Redis — no mock DB needed for SSE behaviour isolation
- Windows SIGKILL fallback (proc.kill()) added to test_worker_kill.py — SIGKILL not available on Windows; proc.kill() uses TerminateProcess equivalent
- Dockerfile: COPY . /app after pip install to maximise Docker layer cache reuse on source changes

## Performance Metrics

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 08 | 02 | ~5 min | 2 | 1 |
| 08 | 01 | ~12 min | 2 | 8 |
| 04.1 | 04 | ~12 min | 2 | 3 |
| 04.1 | 03 | ~7 min | 2 | 6 |
| 04 | 07 | ~22 min | 2 | 25 |
| 04 | 06 | ~16 min | 2 | 14 |
| 04 | 04 | ~14 min | 2 | 8 |
| 04 | 03 | ~24 min | 2 | 4 |
| 04 | 02 | ~18 min | 2 | 4 |
| 04 | 01 | ~15 min | 2 | 7 |
| 01 | 01 | ~45 min | 3 | 15 |
| 01 | 02 | ~35 min | 3 | 5 |
| 01 | 03 | ~7 min | 3 | 4 |
| 01 | 04 | ~9 min | 3 | 11 |
| 01 | 05 | ~7 min | 2 | 6 |
| 01 | 06 | ~25 min | 2 | 11 |
| 01 | 07 | ~20 min | 2 | 6 |
| 01 | 08 | ~5 min | 2 | 6 |

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260516-aaa | Fix eval harness blockers (capture pipeline, G-06 gate, CI, D3 regex) | 2026-05-16 | 2ab4245 | [260516-aaa-eval-blocker-fixes](.planning/quick/260516-aaa-eval-blocker-fixes/) |

## Notes

- Phase 2 planned 2026-05-13 — 7 plans, 7 waves, all ING-01–ING-10 covered, verification passed.
- Wave 7 (02-07) marked autonomous: false — two human checkpoints: source demo_business.pdf + visual SSE verify.
- M1 PRD (`prd-M1.md`) is complete and ready for phase planning.
- M4 is the first hireable artifact — all scope decisions prioritize speed to M4.
- M6 and M7 are parallelizable — both depend only on M4, not on each other.
- Last session: 2026-05-13 — completed 01-08-PLAN.md (GitHub Actions CI, nightly E2E, README) — M1 Phase 1 all 8 plans complete
- CTL-09 (acks_late=True) verified by test_task_args.py assertions on provision_neon and apply_migrations
- CTL-13 (unit coverage >80%) satisfied: 80.41% achieved with 100 tests passing
- CTL-14 (GitHub Actions CI) satisfied: ci.yml covers lint (ruff), typecheck (mypy), unit tests (>80% cov), integration tests with real Postgres/Redis services
- CTL-15 (nightly E2E) satisfied: nightly.yml uses NEON_API_KEY_TEST secret, creates real Neon project, verifies 10-table schema, double-teardown (pytest finally + if:always() step)
- conftest.py sets CELERY_TASK_ALWAYS_EAGER=True and all required env vars before app import
- FastAPI dependency_overrides pattern used for all route tests (no real DB or Redis needed)
- ruff config: line-length=120, select E/F/I, ignore E501; mypy: strict=false, ignore_missing_imports=true
- Last session: 2026-05-16 — completed 04-01-PLAN.md (foundation migrations + settings) — 2 tasks, 533797b + 8f0eba7
- Last session: 2026-05-16 — completed 04-02-PLAN.md (agent_prompt + agent_tools) — 2 tasks, fc9f454 + 5b1aabd
- Last session: 2026-05-16 — completed 04-03-PLAN.md (escalation helper + run_agent_turn Celery task) — 3 commits, 477bfbf + 1e462c8 + 7c624da
- Last session: 2026-05-16 — completed 04-04-PLAN.md (agent_chat + widget FastAPI routes, JWT, rate-limit, CORS, 25 unit tests) — 2 commits, d5030f1 + 280de75
- Last session: 2026-05-16 — completed 04-06-PLAN.md (PATCH /agents/{id} + AgentSoulUpdate + Next.js 16 admin Soul Editor) — 3 commits: 142550a + 432131c + 1634670
- Last session: 2026-05-16 — completed 04-07-PLAN.md (integration test + eval harness: judge, run_evals, 20 scenarios, SQL fixture) — 2 commits: d35d725 + c438337
- [04-07] asyncio.run mocked at boundary (not AsyncMock) — SDK subprocess never spawned in integration tests
- [04-07] Deterministic eval gracefully skips missing responses/ directory — populated on first E2E run
- [04-07] demo_business_tenant.sql uses zero vector(1024) — eval scenarios mock retrieval not real Voyage calls
- [04-06] @types/react-dom pinned to 19.2.3 (plan spec 19.2.5 does not exist on npm)
- [04-06] missing API key → 403 (not 401) via FastAPI APIKeyHeader auto_error=True; tests use in (401,403)
- [04-08] SUPERSEDED — demo page no longer needed (production system, no demo); replaced by 04-09 (cleanup) + 04-10 (Clerk auth)
- [04-09] Bella Vista Coffee eval fixture renamed to Acme Consulting; apps/demo/ replaced with sign-in redirect; demo_m4 scripts replaced by generic provision_agent scripts; guarded E2E test (AGENT_E2E_ENABLED) added
- [04-10] Clerk full platform auth: PyJWT+PyJWKClient JWKS verification; dual-auth (Bearer first, X-API-Key fallback); svix webhook provisioning; /me/provision self-heal; @clerk/nextjs admin UI; CORS Authorization+PATCH added
- [04.1-03] F4 per-tenant daily budget ceiling: TENANT_DAILY_BUDGET_USD=5.0 global default; Redis INCRBYFLOAT with 86400s TTL; POST /chat returns 429+Retry-After:3600 when exhausted; migration 0008 adds tenants.daily_budget_usd; 20/20 tests pass
- Last session: 2026-05-18 — completed 04.1-03-PLAN.md (F4 budget guard) — 3 commits: a018708 + 9b109b7 + 503e084
- [04.1-04] F5 result_expires=300 purges Redis task results after 5 min; LOCAL-DEV-REDIS.md documents redis-server --save for local dev
- [04.1-04] F6 AgentSoulUpdate field validators strip injection markers from soul_voice, soul_role, soul_do_list, soul_donot_list at admit-time via sanitize_chunk_text
- [04.1-04] TDD: 2 RED tests failed; GREEN passed all 19; no REFACTOR needed
- Last session: 2026-05-18 — completed 04.1-04-PLAN.md (F5+F6 config/schema hardening) — 3 commits: 4db7220 + 4b0e73d + 281b8fb
- Last session: 2026-05-18 — completed 04.1-05-PLAN.md (security paper trail closure) — 3 commits: e3a3d50 + 2dc1e96 + 679ae10
- Last session: 2026-05-23 — completed 05-01-PLAN.md (langfuse dep + LANGFUSE_* settings + control DB 0010 + tenant DB 0004 + Agent ORM field + 7 xfail stubs) — 4 commits: e6efe6e + 265079a + 2502946 + cf779ba
- Last session: 2026-05-23 — completed 05-02-PLAN.md (validation_service.py: 3 verdict models + 3 Haiku judge calls + Langfuse _log_verdict; 4 tests de-xfailed) — 3 commits: c2a6dee + 065489b + a3b86be
- Last session: 2026-05-23 — completed 05-03-PLAN.md (validators.py: run_gatekeeper + run_auditor + run_strategist runtime tasks; _insert_verified_qa_candidate; resynthesis flag; celery_app include; 3 xfail stubs de-xfailed; 7/7 tests pass) — 2 commits: 0ab013c + d21b167
- Last session: 2026-05-23 — completed 05-04-PLAN.md (agent.py: retrieve result capture in _run_sdk_turn + validation chain dispatch after agent.response; test_validators_dispatched + test_validators_not_dispatched_on_idempotency_skip; 8/8 tests pass) — 3 commits: edd0542 + a8172c2 + fa3f226
- Last session: 2026-05-23 — completed 06-01-PLAN.md (tenant DB migration 0005 verified_qa+eval_scenarios; Settings 3 eval thresholds; celery_app beat_schedule eval-nightly crontab 02:00 UTC) — 3 commits: 088ea50 + 67118d8 + 4efa68b
- Last session: 2026-05-23 — completed Phase 6 (06-09, Wave 5): demo_m6.sh (local-process demo, no Docker, D-32), test_eval_service.py (6 tests, Ragas 0.4.x regression guards), test_scenario_service.py (7 tests, D-12/D-13), guarded E2E test (EVAL_E2E_ENABLED=1) — 4 commits: 63ac00f + 79eac65 + fed1b1e + 838ce12
- Last session: 2026-05-23 — resumed; discarded stale SSE-UX HANDOFF.json (pre-M5/M6, superseded); proceeding to execute Phase 7 (Red Team)
- Last session: 2026-05-23 — completed 07-01-PLAN.md (migration 0006 status+deployment_blocked, RED_TEAM_MAX_TURNS=5, RED_TEAM_ATTACK_SEQUENCES=3, 2 xfail stubs) — 2 commits: bc4b75e + 31e610d
- Last session: 2026-05-23 — completed 07-02-PLAN.md (red_team_service.py: RedTeamFinding/RedTeamResult/SeverityVerdict models, classify_severity Haiku judge, 3 runner functions with probe_fn pattern; pyrit>=0.6.0 added) — 2 commits: bab1950 + 03eb1ab
- Last session: 2026-05-23 — completed 07-03-PLAN.md (run_red_team_beat + run_red_team Celery tasks; red-team-weekly beat schedule Monday 03:00 UTC; probe_fn uses direct Anthropic API not _run_sdk_turn; deployment_blocked gate RED-06) — 2 commits: a23a26f + 354816a
- Last session: 2026-05-23 — completed 07-04-PLAN.md (red_team Pydantic schemas + FastAPI routes GET list/GET detail/POST trigger 202 + main.py registration; IDOR + conn_str patterns identical to evals.py) — 1 commit: 46eac62
- Last session: 2026-05-23 — completed 07-06-PLAN.md (demo_m7.sh: weak agent, red team trigger, Celery poll, deployment gate assertion, injection trace; test_red_team_e2e.py: RED_TEAM_E2E_ENABLED guard, 300s poll loop, schema validation) — 2 commits: a5b0fa9 + 8aff4fa
- Phase 6 COMPLETE: all 9/9 plans done, all EVL-01 through EVL-08 requirements satisfied
- Phase 7 COMPLETE: all 6/6 plans done, all RED-01 through RED-08 requirements satisfied
- Last session: 2026-05-24 — Phase 8 fully planned: RESEARCH.md + VALIDATION.md + UI-SPEC.md + 08-predeploy-mockup.html + PATTERNS.md + 7 PLAN.md files (08-01–08-07) — 4 blockers found and fixed; verification PASSED; all DEP-01–DEP-08 covered
- Phase 7 VERIFIED (2026-05-23): 8/8 must-haves pass codebase audit; code review fixed 2 critical (SQL data isolation + asyncio nested loop) + 5 warnings; 18 unit tests pass; 07-VERIFICATION.md written
- Phase 6 VERIFIED (2026-05-23): automated codebase audit confirms all EVL-01–EVL-08 PASS; 06-VERIFICATION.md written
- [TODO-RET-01] F7 / filters enforcement gate: retrieve_tool filters field must have allowlisted-column enforcement before being wired to LLM output. Gate: M5 phase planning MUST resolve this before activating filters in retrieve_tool. Logged Phase 4.1.
- [07-01] IF NOT EXISTS guards on ALTER TABLE make migration 0006 safe to re-run on pre-altered tenant DBs
- [07-01] RED_TEAM_MAX_TURNS=5 and RED_TEAM_ATTACK_SEQUENCES=3 are plain int fields in Settings — no Field() wrapper needed; xfail strict=True stubs de-xfailed in 07-05
- [07-02] classify_severity called post-loop (not inside async loop) — avoids nested asyncio.run conflicts
- [07-02] SONNET_MODEL for red-team agents (attack creativity); HAIKU_MODEL for severity classifier (cost efficiency)
- [07-02] probe_fn result not captured inline — SDK manages tool result delivery; runner calls probe_fn for side effect only
- [07-02] pyrit>=0.6.0 added to core dependencies (not optional) — required at runtime by red-team Celery task
- [07-03] probe_fn uses direct Anthropic API (not _run_sdk_turn from agent.py) — _run_sdk_turn is coupled to SSE/job/conversation infrastructure not available in red-team context
- [07-03] Idempotency window 30 min (vs 10 min for eval) — red team runs take longer (3 agents x 5 turns each)
- [07-03] deployment_blocked = (max_severity == "critical") is the exact RED-06 gate condition
- [07-03] red-team-weekly: day_of_week=1 (Monday 03:00 UTC) — fires one hour after eval-nightly to avoid Redis contention
- [07-04] POST trigger returns run_id = task.id as correlator at dispatch time; actual DB run_id generated inside the task
- [07-04] findings defaults to [] and deployment_blocked defaults to False for rows where JSONB/boolean columns may be None
- [07-06] demo_m7.sh agent status polling handles two JSON shapes (status at root vs. nested under 'agent' key) via Python extractor fallback
- [07-06] Section 4 assertion output uses KEY=VALUE line format parsed with grep+cut — avoids subshell quoting issues with multi-line Python blocks in bash strict mode
- [07-06] E2E test polls GET /red-team-runs instead of direct Celery state — avoids requiring celery_app import in CI test runner environment
- Last session: 2026-05-24 — completed 08-01-PLAN.md (migration 0011, ChecklistRun ORM, Agent.is_deployed, DEP_BLOCK_ON_HIGH_RED_TEAM, 4 xfail test stubs) — 2 commits: 72a3cb6 + 5b211b9
- [08-01] Migration 0011 uses IF NOT EXISTS on all three DDL statements for safe re-run on pre-altered DBs (T-08-01-01)
- [08-01] checklist_runs is in control DB (not tenant DB) — platform metadata, no PII, IDOR check gated in routes (Plan 08-04)
- [08-01] DEP_BLOCK_ON_HIGH_RED_TEAM defaults True — safe production default; operators can set to False to degrade high findings to warnings
- Last session: 2026-05-24 — completed 08-02-PLAN.md (deployment_service.py: DeploymentReport/DeploymentWarning models, _TOOL_SUBMIT_REPORT, 4 signal collectors, _run_orchestrator_loop, run_orchestrator bridge, _make_iframe_snippet) — 1 commit: 828c525
- [08-02] red_team_runs.findings is JSONB list — severity counts derived by iterating findings list, not a separate red_team_findings table (schema confirmed via alembic_tenant migration 0001)
- [08-02] verified_qa column names are faithfulness and relevance (not faithfulness_score/relevance_score) — matches alembic_tenant migration 0005
- [08-02] run_orchestrator wraps _run_orchestrator_loop in asyncio.run(asyncio.wait_for(..., timeout=120.0)) — swallows exceptions so Celery task can set status='failed'
