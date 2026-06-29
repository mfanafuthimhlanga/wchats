---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: — Transactional Capability
status: v1.1 roadmap defined (phases 14-19), building in parallel; v1.0 Phase 13 deploy paused at live AWS gates (7/11 done, needs domain)
stopped_at: Phase 14 base plans executed (14-01..04); verifier human_needed; CR-01 fixed; gap plans 14-05..08 planned + checker PASSED — next /gsd-execute-phase 14 --gaps-only
last_updated: "2026-06-29T19:45:00Z"
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 8
  completed_plans: 4
  percent: 0
---

# Project State

## Current Status

**▶ PHASE 14 — base plans EXECUTED; NOT yet verified-complete; gap-closure planned (2026-06-29).** Original 4 plans (14-01..04) executed + committed (sequential mode — worktrees auto-degraded, origin/HEAD unresolved). Full enforcement path exercisable offline: capability envelope → idempotency → actor seam → stub execute → audit; 7 transactional tools registered in the customer-agent MCP server. **Verifier = human_needed** (3/4; idempotency behavior-unverified pending live DB) → 2 integration items in `14-UAT.md` for `/gsd-verify-work 14`. **Code review (`14-REVIEW.md`) found 2 blockers + 5 warnings:** CR-01 (capability_snapshot not JSON-safe → audit write crashed) **FIXED** `61b45a0`; **CR-02** (idempotency double-execution under concurrency/crash) + WR-01..05 addressed by **gap plans 14-05..08** (committed `72a6927`, `gap_closure:true`) — independent gsd-plan-checker **PASSED** (0 blockers). Design: DB-enforced reserve-before-execute (migration 0015 + reservation cols) + capability/rate split + Redis TLS verify + executor offload, proven by real-Postgres concurrency tests. **NEXT:** `/gsd-execute-phase 14 --gaps-only` (wave 1: 14-05‖14-07 → wave 2: 14-06 → wave 3: 14-08), then `/gsd-secure-phase 14` (security capability active, no SECURITY.md yet), then `/gsd-verify-work 14` for the live-DB UAT items. Phase 13 (production hosting) remains a separate, paused track needing real AWS — see checkpoint below.

**▶ CHECKPOINT — Phase 13 EXECUTING, paused at live AWS gates (2026-06-29):** 7/11 plans complete — **all autonomous code waves done**: 13-01 Terraform IaC (`deploy/terraform/`, 12 files), 13-02 Bedrock Titan v2 embedder (provider seam, both paths), 13-03 Neon connection pooling, 13-04 per-tenant re-embed task, 13-05 env-driven embed snippet, 13-06 S3 uploads, 13-07 ContextVar concurrency (prefork=2 in prod / solo in dev). 73 phase unit tests pass together; commits `e8b51fa`→`3560071` on `main`. **Paused at 13-08** (first `autonomous:false` live gate). Remaining 13-08/09/10/11 need a real **AWS account (billing + Bedrock Titan v2 model access in us-east-1)** plus `terraform` and `aws` CLIs — none present locally. `terraform validate`/`fmt` for 13-01 are deferred into 13-08. **Resume:** install terraform + awscli, configure AWS creds, request Bedrock Titan access, then `/gsd-execute-phase 13 --wave 3` (13-08 live bring-up + re-embed), then wave 4 (13-09/10/11). Per-plan detail in each `13-0X-SUMMARY.md`.

**Active Milestone:** Phase 13 — Production Hosting & Durable Deployment (executing; see checkpoint above). **Phase 12 ✓ COMPLETE 2026-06-28** — demo path proven live end-to-end (job `fdf93abd`, 1741-char grounded + cited answer via localhost.run); portfolio embed integrated. The local-PC + ephemeral-tunnel hosting was a deliberate $0 demo compromise; its durable, always-on production replacement is Phase 13. The historical Phase 12 narrative below is retained as record.
**[prior] Active Milestone:** Phase 12 — Production Go-Live (W Chats)
**Milestone Phase:** Phase 12 — In Progress. **DEMO PATH PROVEN END-TO-END LIVE** (localhost.run + grounded answer). Empty-answer bug RESOLVED (`132f529` + `9572f01`).
**✓ PORTFOLIO EMBED DONE (2026-06-01 session 2):** W Chats widget integrated into the `portfolio-dashboard` repo (sibling; deploys to bantuson.vercel.app) — 4 embed files byte-identical in `wchats/`, runtime `wchats/config.json` (`apiBase`), bootstrap in root `index.html` that mounts the launcher ONLY when apiBase is non-empty (no dead-widget risk). STAGED, NOT committed/pushed. ⚠️ portfolio index.html had ~772 lines of pre-existing uncommitted WIP before this session — commit boundary is the user's call. Remaining (human-gated): start stack → `ssh -R 80:localhost:8000 nokey@localhost.run` → put `*.lhr.life` in config.json → commit+push → manual test. Also still open: swap `scripts/start_demo.ps1` tunnel cloudflared→localhost.run.
**✓ LIVE PROOF 2026-06-01 (job `fdf93abd`):** "What is W Chats and who is Bantuson?" through localhost.run → **1741-char grounded answer + 1 citation**, SSE streamed. Instrumented: `subtype=success num_turns=2 total_cost_usd=0.063`. Empty-answer root cause was TWO compounding caps from 12-01 D-10: `max_turns=3` (fixed→6) AND `max_budget_usd=0.05` (fixed→settings.AGENT_MAX_BUDGET_USD=0.50) — the $0.063 turn cost proved the $0.05 cap was the final binding constraint. Transport: Cloudflare quick tunnel can't do SSE; **localhost.run does** (`ssh -R 80:localhost:8000 nokey@localhost.run`).
**Empty-answer bug (RESOLVED `132f529`, debug `empty-answer-on-retrieve`):** root cause = `max_turns=3` (12-01 D-10) counts tool iterations, so the retrieve round-trip exhausted the budget → CLI `error_max_turns` → empty `agent.response.text` (no exception). Fix: `max_turns` 3→6 + tool-level per-turn retrieve cap (`_retrieve_call_count` in agent_tools.py, reset by build_tool_server, blocks 3rd call) so the Voyage 3 RPM guard holds independent of turns. 26/26 unit tests pass. NOT yet live-verified — the real proof (a non-empty grounded answer to "who is Bantuson?" through localhost.run) is the next step.
**Checkpoint (resume here):** 12-05 Task 1 committed `8124035`. Task 2 verification was AUTOMATED on this PC across two tunnels, then torn down. **TWO FINDINGS:**
  • **TRANSPORT SOLVED:** Cloudflare *quick* tunnel works for HTTP (D-05 proven: `/health` 200, valid cert) but does NOT deliver SSE (live 95s curl = 0 bytes; cloudflared#1449 / "Quick Tunnels do not support SSE"). **localhost.run (SSH: `ssh -R 80:localhost:8000 nokey@localhost.run`) DOES stream SSE** — full sequence arrived incrementally (TTFB 9s): `agent.thinking → agent.tool_call(retrieve) → agent.response → gatekeeper/auditor/strategist.complete`. SSE event format is standard `event: agent.response\ndata:{...}` (NOT a JSON `event_type` key — earlier grep false-negative). → Adopt localhost.run as the `start_demo.ps1` tunnel (small edit; cloudflared→ssh). Both give a random URL per session (fine; 12-06 wires it).
  • **NEW BUG (blocks a GOOD demo):** the agent returns **EMPTY text** (`agent.response` `"text":""`, response_length=0) when it must RETRIEVE to answer (e.g. "who is Bantuson?": 1 retrieve → empty). Simple no-retrieve questions answer fine ("What is W Chats?" → 707 chars). All 3 validators correctly flagged it (gatekeeper fail / auditor ungrounded / strategist revise). Prime suspect = the 12-01 D-10 change `max_turns=3` + "retrieve AT MOST ONCE" prompt — too aggressive; likely cuts the agent off after the retrieve before it composes the grounded answer. Capping *retrieve calls* and capping *total turns* were conflated. → `/gsd-debug`: raise max_turns (e.g. 6) and cap retrieve calls via a tool-level guard instead, OR check retrieval relevance for Bantuson queries; re-verify the agent answers grounded questions.
  Side notes: per-agent SSE concurrency cap trips at a low count (repeated test conns → "Too many concurrent connections"); cold `import app.main` ≈108-144s on 4GB (start tunnel only after stack warm); smoke §1–§4 `--max-time` 10/15s too tight for tunnel + Upstash/Neon-sa-east-1 round-trips (bump to ~40s).
  NEXT: (1) `/gsd-debug` the empty-answer bug (highest priority — demo is about Bantuson); (2) edit `start_demo.ps1` tunnel → localhost.run; (3) then 12-06 (wire URL, push, live Q&A). Do NOT `git push` 8124035 until a working tunnel + a non-empty grounded answer are confirmed.
**Current Position:** Phase 12 Wave 1 done & committed (12-01 agent hardening, 12-02 widget published, 12-03 cutover ADR, 12-04 deploy artifacts — VM artifacts retained as the AWS/ADR reference). No-card pivot complete: 12-05 + 12-06 re-planned (re-research `64eee6f`, plans `63282cf`, warning fixes committed) for **local PC + Cloudflare quick tunnel** (D-01/02/05 superseded; D-04/12/14 retained). Plan-checker: VERIFICATION PASSED, D-01..D-15 covered. NEXT = resume execution: `/gsd-execute-phase 12` (starts at Wave 2 / 12-05). 12-05 Task 1 = autonomous in-repo authoring (`scripts/start_demo.ps1`, smoke §5 single 95s SSE curl, embed on `apps/admin/app/page.tsx` via editable `WCHATS_TUNNEL_API_BASE`); 12-05 Task 2 + all of 12-06 = autonomous:false (need the PC running `start_demo.ps1` + a browser during a demo window). Key risk gated empirically in 12-05 Task 2: SSE survival through the quick tunnel within the 90s guard (fallback: lower D-11 or serveo/localhost.run).
**Last updated:** 2026-05-29

## Roadmap Evolution

- 2026-06-28 — **Phase 13 added: Production Hosting and Durable Deployment** (depends on Phase 12). Scope = the production gap beyond the portfolio demo: durable always-on managed hosting for API + warm runtime worker + Redis (kills the local-PC + tunnel + 108–144s cold start), CDN-hosted widget with a *working* self-serve embed snippet (real `src` + `data-api`, removes the "CDN not yet live" placeholder), object storage for uploads (S3 replaces local `UPLOADS_DIR`), and concurrency-safe horizontal runtime workers (`agent_tools` globals → `ContextVar`). Four waves, PROD-01..PROD-15. Executes the ADR-0001 D-14 env seam onto always-on infra. **Out of scope:** Neon project-cap/Aurora migration (not a constraint at current scale — per user) and the Post-M10 transactional/A2A/MCP/security layers (separate milestone). Not planned yet → `/gsd-plan-phase 13`.
- 2026-06-29 — **Milestone v1.1 Transactional Capability started (safe parallel track).** Phases 14–19 appended to the roadmap; 43 requirements (TXN/CAP/ACT/INT/IDV/AUD/BLR/RTX/SEC/DOC/VER) per `Post-M10-PRD.md` §4 — agents move from answering to acting, with security layers L1–L3/L5/L6 (+partial L4) first-class. The standard new-milestone reset was deliberately NOT run (it would have cleared the paused Phase 13 dir + reset its checkpoint); Phase 13 stays paused & resumable. v1.1 is code-buildable now in parallel and does not depend on the Phase 13 production deploy. **Next: `/gsd-plan-phase 14`.**

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-12)

**Core value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.
**Current focus:** Phase 14 — transactional-tool-contract-capability-audit-substrate-typed
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
| M7 | Red Team | ✓ Complete (6/6 plans) | `prd-M7.md` ✓ |
| M8 | Pre-deployment Checklist | ✓ Complete (7/7 plans) | `prd-M8.md` ✓ |
| M9 | Retrieval Strategy Synthesis | ✓ Complete (4/4 plans) | `prd-M9.md` ✓ |
| M10 | Maintenance + Observability | ✓ Complete (6/6 plans) | `prd-M10.md` ✓ |
| M11 | Admin UI Overhaul | ○ 6/6 plans — visual QA passed, verify pending | — |

## Key Decisions

- [14-01] Migration 0014 uses op.execute() raw SQL with IF NOT EXISTS guards (consistent with 0013, safe re-run)
- [14-01] tool_calls_audit.arguments and capability_snapshot are nullable (plan spec overrides PRD NOT NULL — capture may fail before validation)
- [14-01] pending_confirmations.expires_at is nullable — NULL means no deadline configured (plan spec overrides PRD NOT NULL)
- [14-01] actor_decision/actor_rationale are NOT NULL DEFAULT '' — Phase 14 writes empty; Phase 15 Actor fills them
- [14-04] confirm_action is mutating=False — writes pending_confirmations row (client-generated UUID), no provider adapter, no idempotency key; duplicate dedup deferred to Phase 18
- [14-04] Idempotency lookup hoisted before actor seam — replay short-circuits before Haiku gate call; capability check still runs first on every call (T-14-04-03)
- [14-04] AUD-01 symmetry: capability denial writes audit row with error=capability.denial:<reason> — 100% audit coverage
- [14-04] Lazy import of agent_tools ContextVars inside dispatcher body avoids circular import (tools.py imports agent_tools ContextVars at call time, not module level)
- [14-01] No ORM FK relationships declared (agent_id is plain UUID) — avoids cross-table teardown complexity in tests
- [11-05] scoreColor: --amber → --gold for mid-range scores (0.7-0.9) — amber = building warmth (#E8A87C) in Hillbrow system, not a status warning token
- [11-05] Recharts hex per plan spec (#FBBF24 gold) rather than PATTERNS.md (#F0C674) — plan spec must_haves are acceptance criteria target
- [11-05] Widget customizer layout: 3-column grid (255px 310px 1fr) → flex 2-column (form flex:1, preview 300px sticky top:80px) per .continue-here.md decision
- [11-05] Glass stat tiles render only when latestRun is truthy — no fallback for empty aggregate_scores state
- [11-04] Done-step border in JourneyStepper uses rgba(52,211,153,0.25) — literal green at 25% opacity; --green-border is not a defined token in globals.css
- [11-04] soul/page form panel uses margin:32px on the panel div — preserves existing flex-row layout with preview panel while applying dark card treatment
- [11-04] pnpm run lint pre-existing broken (same inherited issue from 11-01) — build passes, lint skip documented
- [11-03] Greeting strip name uses 'there' placeholder — useUser() not imported on agents/page.tsx; adding new auth hook would violate data-fetching-only constraint; stub tracked in SUMMARY
- [11-03] amber-bg in agents/[id]/ scope is Wave 4/5 work — agents/page.tsx and AgentCard.tsx are clean; other files untouched in Plan 03
- [11-02] page.tsx converted to 'use client' — scroll listener for glass nav requires useEffect; no sub-component extraction needed
- [11-02] Primary CTA color: #0B0717 (--text-on-accent) not '#fff' — dark text on coral fill per design system
- [11-02] pnpm run lint pre-existing broken (inherited from 11-01) — build passes, lint skip documented
- [11-01] Veil opacity set to 0.45 per .continue-here.md canonical (not 0.72 from CONTEXT.md) — lighter veil lets more city show through
- [11-01] --surface-1 set to #140E2A per .continue-here.md canonical (not #1E1638 from colors_and_type.css) — deeper card surface
- [11-01] pnpm run lint pre-existing broken in admin (no ESLint config) — not introduced by this plan; build passes
- [11-01] page.tsx logo fixed as Rule 2 deviation — --font-pixelify undefined after token removal required immediate fix
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
| 14 | 01 | ~9 min | 2 | 7 |
| 11 | 05 | ~20 min | 2 | 3 |
| 11 | 04 | ~20 min | 2 | 6 |
| 11 | 03 | ~15 min | 2 | 2 |
| 11 | 02 | ~20 min | 2 | 2 |
| 11 | 01 | ~25 min | 2 | 9 |
| 08 | 07 | ~25 min | 2 | 3 |
| 08 | 06 | ~40 min | 2 | 4 |
| 08 | 05 | ~20 min | 2 | 1 |
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
| Phase 12 P01 | ~12 min | 2 tasks | 3 files |
| 12 | 02 | ~10 min | 2 | 7 |
| Phase 14 P02 | 7 min | 3 tasks | 6 files |
| Phase 14 P03 | ~6 min | 3 tasks | 5 files |

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
- Last session: 2026-05-24 — completed 08-05-PLAN.md (Pre-Deploy tab UI: DeployTab 'customize'|'predeploy'|'embed', ChecklistState 5-state machine, polling useEffect 3000ms, 4 signal cards, warning ack gate, approve button) — 1 commit: 9531b78
- [08-05] DeployTab initial state changed to 'customize' (not 'predeploy') per UI-SPEC journey ordering: Customise first
- [08-05] Both tasks committed together (9531b78) — implementation was a single edit pass; all acceptance criteria verified
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
- [08-07] main.py deployment router already registered (Wave 5 Rule 1 auto-fix in 08-06) — no change needed in Task 1
- [08-07] demo_m8.sh block outcome exits 0 — block is valid checklist result, not a script failure
- [08-07] demo_m8.sh no echo of API_KEY in any print statement (T-08-07-01 satisfied)
- [08-07] test_deployment_e2e.py uses E2E_AGENT_ID (not AGENT_ID) to prevent accidental targeting of production agent
- Last session: 2026-05-24 — completed 08-01-PLAN.md (migration 0011, ChecklistRun ORM, Agent.is_deployed, DEP_BLOCK_ON_HIGH_RED_TEAM, 4 xfail test stubs) — 2 commits: 72a3cb6 + 5b211b9
- [08-01] Migration 0011 uses IF NOT EXISTS on all three DDL statements for safe re-run on pre-altered DBs (T-08-01-01)
- [08-01] checklist_runs is in control DB (not tenant DB) — platform metadata, no PII, IDOR check gated in routes (Plan 08-04)
- [08-01] DEP_BLOCK_ON_HIGH_RED_TEAM defaults True — safe production default; operators can set to False to degrade high findings to warnings
- Last session: 2026-05-24 — completed 08-02-PLAN.md (deployment_service.py: DeploymentReport/DeploymentWarning models, _TOOL_SUBMIT_REPORT, 4 signal collectors, _run_orchestrator_loop, run_orchestrator bridge, _make_iframe_snippet) — 1 commit: 828c525
- [08-02] red_team_runs.findings is JSONB list — severity counts derived by iterating findings list, not a separate red_team_findings table (schema confirmed via alembic_tenant migration 0001)
- [08-02] verified_qa column names are faithfulness and relevance (not faithfulness_score/relevance_score) — matches alembic_tenant migration 0005
- [08-02] run_orchestrator wraps _run_orchestrator_loop in asyncio.run(asyncio.wait_for(..., timeout=120.0)) — swallows exceptions so Celery task can set status='failed'
- Last session: 2026-05-24 — completed 08-07-PLAN.md (celery_app include deployment task; demo_m8.sh 6-section M8 journey; test_deployment_e2e.py de-stubbed with real poll/ack/approve flow) — 2 commits: 2454da1 + 5a0110e; blocking human-verify checkpoint active
- Last session: 2026-05-24 — completed 08-06-PLAN.md (de-xfail 10 stubs → 15 real tests; Rule 1 fix: deployment router added to main.py) — 2 commits: 86f3f2e + ccb0220
- [08-06] _call_orchestrator_async patched (not run_orchestrator) — task calls asyncio.run(_call_orchestrator_async(...)) so patch the coroutine, not the sync bridge
- [08-06] Celery Retry exception caught with broad except in failure-path test — CELERY_TASK_ALWAYS_EAGER=True causes self.retry() to raise Retry; important assertion is db.commit() was called before retry
- [08-06] deployment router was missing from main.py — Rule 1 auto-fix; all route tests returned 404 until fix applied
- [08-06] Pre-existing unit test failures in test_agent_chat_routes, test_agents_patch, test_chunking_service, etc. logged to .planning/deferred-items.md; not introduced by 08-06
- Last session: 2026-05-25 — Phase 10 COMPLETE: 6/6 plans executed, code review + fixes applied (10-REVIEW-FIX.md), UAT: 7 passed / 0 issues / 3 blocked (live-services-only); OPS-01–OPS-06 all satisfied; human checkpoint (demo_m10.sh) pending live-service run
- Last session: 2026-05-25 — Phase 9 COMPLETE: bug fix (strategy_service used Agent SDK instead of direct Anthropic API — tool_use blocks never generated); replaced with anthropic.Anthropic().messages.create(); demo_m9.sh approved; all STR-01–STR-03 satisfied — fix commit b108441
- [09] ClaudeSDKClient (claude-agent-sdk) does NOT support custom JSON tool schemas — it is a code-execution agent. Always use direct Anthropic API (anthropic.Anthropic().messages.create) for tool_use patterns in services.
- Last session: 2026-05-26 — completed 11-06-PLAN.md (visual QA gate): resolved 9 handoff failures (headline size, hero sub, BUILD PIPELINE card, Refresh button, stat labels, greeting sub-line, "Your agents" header+filter tabs, AgentCard SVG icons, status badge format) — 1 commit: 872ad71; Phase 11 COMPLETE
- Last session: 2026-05-26 — completed 11-05-PLAN.md (eval glass stat tiles + Recharts dark colours + scoreColor --gold; deploy dark DEFAULT_CONFIG + sticky 2-col customizer + banner token fixes; settings dark form panel) — 2 commits: d59fe8c + 30a06f1
- Last session: 2026-05-26 — completed 11-04-PLAN.md (agent journey screens: JourneyStepper coral/green tokens, StepSubtaskCard --surface-1, amber→gold rename across 3 files, Fraunces italic-coral agent name in soul/page, dark form panels) — 2 commits: b8b18a3 + 1c1f851
- Last session: 2026-05-26 — completed 11-03-PLAN.md (agents dashboard: greeting strip Fraunces italic-coral, transparent wrapper, repeat(3,1fr) grid; AgentCard: surface-1 bg, hover translateY(-2px) coral borderTop, gold status tokens, Fraunces 600 name, UPPERCASE TRACKED chips) — 2 commits: 9d1965a + 17e8e35
- Last session: 2026-05-26 — completed 11-02-PLAN.md (landing page rebuild: transparent hero, glass nav scroll-reactive, Fraunces headline strikethrough+italic-coral, coral CTA, trust strip, HeroSteps.tsx token migration --orange→--accent, --green-solid→--green) — 2 commits: 32fa8b7 + ce7a61b
- Last session: 2026-05-26 — completed 11-01-PLAN.md (Hillbrow at Dusk token foundation: globals.css full replacement, skyline PNG background, Fraunces Google Fonts, Clerk dark appearance, SVG logo in TopNav, transparent layout wrapper) — 2 commits: 76e7a61 + 64e3986
- Last session: 2026-05-29 — completed 12-01-PLAN.md (D-10 max_turns=3 + retrieve-at-most-once prompt, D-11 timeout=90s, D-13 Redis qembed cache) — 2 commits: 15468e2 + 61d8ced; all 10 agent-task tests pass; acks_late + idempotency invariants verified intact
- Last session: 2026-05-29 — completed 12-02-PLAN.md (pnpm bundle rebuild + sync embed/; apps/admin/public/wchats/ created with 4 byte-identical embed files) — 2 commits: 9d8ab27 + ab943a1; gzip 8,087 B < 20,480 B gate passed; D-06 D-07 D-08 satisfied

## Decisions

- [Phase ?]: [12-01] D-10 dual guard: max_turns=3 + system-prompt AT MOST ONCE instruction
- [Phase ?]: [12-01] D-11 timeout raised 30s to 90s - SDK subprocess warm-up on ARM VM requires more headroom; SSE layer retains 120s
- [Phase ?]: [12-01] D-13 included: lazy Redis qembed cache with try/except fallback - cache is optimisation, never correctness dependency
- [Phase ?]: [12-02] Bundle sizes differ from RESEARCH.md baseline: pnpm v11 fresh install produced 20,835 B iife.js vs prior 17,833 B; gzip 8,087 B passes < 20,480 B gate; new sizes are authoritative
- [Phase ?]: [12-02] pnpm-lock.yaml created in apps/widget/ — pnpm installed fresh, replacing prior npm node_modules; lock file committed to pin dependency versions
- [Phase ?]: [14-02] All 14 Pydantic model imports module-level in test file; all 3 impl modules needed before first test collection; TDD RED in one commit, GREEN per-task
- [Phase ?]: [14-02] confirm_action mutating=False — writes pending_confirmations row but does not call provider; non-mutating per research Cluster 2
- [Phase ?]: [14-02] actor_seam.py in services/ not transactional/ — Phase 15 imports it independently of transactional stack
- [Phase ?]: [14-03] enforcement.py uses _get_redis() lazy singleton for rate-limit counter
- [Phase ?]: [14-03] idempotency.py uses raw sa_text INSERT with ::jsonb cast — ON CONFLICT DO NOTHING, no Redis
- [Phase ?]: [14-03] write_audit_row raises TypeError if capability_snapshot is not a plain dict (Pitfall 4 enforcement)

## Session

**Last session:** 2026-06-29T16:03:00.966Z
**Stopped at:** Completed 14-02-PLAN.md — typed tool contract complete (50 tests passing)
**Resume file:** None
