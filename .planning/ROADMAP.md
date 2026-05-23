# Veridian — Roadmap

**Project:** Veridian RAG Platform
**Mode:** Vertical MVP (per milestone)
**Structure:** 10 milestones → each milestone broken into GSD phases when it becomes active
**Coverage:** 84/84 v1 requirements mapped
**Last updated:** 2026-05-16

---

## Milestone Overview

Each milestone has its own PRD (`prd-MN.md`) and its own phase breakdown.
Phases within a milestone are planned via `/gsd-discuss-phase` when the milestone becomes active.

| # | Milestone | Goal (short) | Requirements | PRD | Status |
|---|-----------|--------------|:------------:|-----|--------|
| M1 | Control Plane Skeleton | FastAPI + Celery + Neon provisioning + SSE | 15 | `prd-M1.md` | ✓ Complete (8/8) |
| M2 | Ingestion Pipeline | Docling + Chonkie + metadata + Voyage embedding | 10 | `prd-M2.md` | ✓ Complete (7/7 plans) |
| M3 | Hybrid Retrieval | pgvector + BM25 + RRF + Voyage rerank | 8 | `prd-M3.md` | ✓ Complete (7/7 plans) |
| M4 | Reasoning Engine + Widget v0 | Claude agent + Preact widget + public demo **[HIREABLE ARTIFACT]** | 11 | `prd-M4.md` | ○ Pending |
| M5 | Validation Chain | Async Gatekeeper + Auditor + Strategist on every response | 7 | `prd-M5.md` | ○ Pending |
| M6 | Eval System | Ragas nightly evals + scenario mining + eval dashboard | 8 | `prd-M6.md` | ✓ Complete (9/9 plans) |
| M7 | Red Team | Three adversarial agents + severity classification + deployment gate | 8 | `prd-M7.md` | ✓ Complete (6/6 plans) |
| M8 | Pre-deployment Checklist | Orchestrator agent + owner approval gate + journey validated | 8 | `prd-M8.md` | ○ Pending |
| M9 | Retrieval Strategy Synthesis | Strategist auto-generates per-tenant retrieval configs | 3 | `prd-M9.md` | ○ Pending |
| M10 | Maintenance + Observability | Autonomous crons + digest email + Langfuse dashboards + alerting | 6 | `prd-M10.md` | ○ Pending |

**Total v1 requirements:** 84 | **Mapped:** 84 ✓ | **Unmapped:** 0 ✓

---

## Milestone Details

### M1 — Control Plane Skeleton

**Goal:** Establish the infrastructure foundation — FastAPI control plane, auth, per-tenant Neon project provisioning via Celery chains, and live SSE job status streaming.
**PRD:** `prd-M1.md` (complete)
**Requirements:** CTL-01 through CTL-15
**Depends on:** Nothing (first milestone)
**Phases:** Planned — 8 plans, 7 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 01-01 | Project skeleton, ORM models, both Alembic migrations | ✓ Complete |
| Wave 2 | 01-02 | Security helpers (Fernet/argon2), emit() helper, Celery app | ✓ Complete |
| Wave 3 | 01-03 | Neon service, migration service, provision_neon + apply_migrations tasks | ✓ Complete |
| Wave 4 | 01-04 | FastAPI routes, auth dependency, SSE endpoint | ✓ Complete |
| Wave 5 | 01-05 | docker-compose, Dockerfile, .env.example, demo script | ✓ Complete |
| Wave 6 *(parallel)* | 01-06 | Unit tests (100 tests, 80.41% coverage) | ✓ Complete |
| Wave 6 *(parallel)* | 01-07 | Integration tests | ✓ Complete |
| Wave 7 *(blocked on Wave 6 completion)* | 01-08 | GitHub Actions CI + nightly E2E + README | ✓ Complete |

**Cross-cutting constraints:**
- `acks_late=True` AND idempotency guard on every Celery task (CTL-02, CTL-07)
- Connection strings never in Celery task args (CTL-08)
- FastAPI never does work inline — all events emitted from Celery tasks

**Success Criteria:**
1. `POST /agents` → Neon project provisioned, migrations applied, `status: ready` — all within 60 seconds.
2. Browser tab at `GET /jobs/{id}/events` streams all six required events in order without polling.
3. Worker kill-9 mid-chain → task retries and completes — tenant DB never half-migrated.
4. `scripts/demo_m1.sh` runs clean from `docker-compose up` and exits zero.
5. Unit coverage >80%; nightly CI E2E creates and destroys a real Neon project.

**Demo:** `curl POST /agents` → browser streams Neon project coming into existence in real time.
**UI hint:** no

---

### M2 — Ingestion Pipeline

**Goal:** Transform raw business documents into queryable, semantically enriched chunks with structure-aware parsing and metadata that cannot be patched later without full re-embedding.
**PRD:** `prd-M2.md` (TBD — write before starting M2)
**Requirements:** ING-01 through ING-10
**Depends on:** M1
**Phases:** Complete — 7 plans, 7 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 02-01 | Foundation: deps, Settings, 0002 migration (entities + chunk_entities), chunk_id + sanitize utils | ✓ Complete |
| Wave 2 | 02-02 | parse_documents Celery task + docling_service wrapper (Layer 1 source_hash idempotency) | ✓ Complete |
| Wave 3 | 02-03 | chunk_documents task + two-path chunking_service (text + Markdown table; Layer 2 uuid5+ON CONFLICT) | ✓ Complete |
| Wave 4 | 02-04 | generate_metadata task + Haiku metadata_service (single call: summary+keywords+questions+entities; Layer 3 skip) | ✓ Complete |
| Wave 5 | 02-05 | embed_and_migrate task + Voyage embedding_service (voyage-3 pinned, 128-batch, REINDEX CONCURRENTLY; Layer 4 ON CONFLICT) | ✓ Complete |
| Wave 6 | 02-06 | POST/GET /agents/{id}/documents routes + chain dispatch + full-chain integration tests (idempotency proof) | ✓ Complete |
| Wave 7 | 02-07 | Demo PDF fixture + scripts/demo_m2.sh + INGESTION_E2E_ENABLED test (ING-10 close) | ✓ Complete |

**Cross-cutting constraints:**
- `acks_late=True` AND idempotency guard on every Celery task (4-layer end-to-end)
- Connection strings never in Celery task args; tasks fetch by agent_id from control DB
- Tables NEVER fed to HybridChunker (PITFALLS.md §2 — table.export_to_markdown dedicated path)
- voyage-3 PINNED — never voyage-latest (PITFALLS.md §3)
- Entity extraction in SAME Haiku call as metadata (single structured output)
- Chunk text sanitized (sanitize_chunk_text) before DB write (PITFALLS.md §11)

**Success Criteria:**
1. Upload real business PDF → `chunks` and `chunk_metadata` tables populate with summaries, keywords, and hypothetical questions on every chunk.
2. Tables in source documents appear as structured Markdown rows in chunks — not fragmented prose.
3. Ingestion progress streams via SSE (`parsing` → `chunking` → `metadata` → `embedding`) without polling.
4. Uploading same document twice produces no duplicates — deterministic chunk UUIDs, upsert idempotency end-to-end.

**Demo:** Upload a real PDF via API; inspect enriched chunks and metadata in the tenant DB.
**UI hint:** no

---

### M3 — Hybrid Retrieval

**Goal:** Deliver a configurable retrieval engine combining pgvector HNSW, native BM25, RRF fusion, and Voyage reranking so retrieval quality is proven before the agent is built.
**PRD:** `prd-M3.md` (TBD)
**Requirements:** RET-01 through RET-08
**Depends on:** M2
**Phases:** Complete — 7 plans, 7 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 03-01 | Alembic 0003 migration (retrieval_strategy JSONB) + COHERE_API_KEY in Settings + Agent ORM + Wave 0 test stubs | ✓ Complete |
| Wave 2 | 03-02 | retrieval_service.py — RetrievalStrategy + vector_search + bm25_search + rrf_fuse + rerank + build_trace | ✓ Complete |
| Wave 3 | 03-03 | retrieve_and_rank Celery task (runtime queue) + celery_app include update | ✓ Complete |
| Wave 4 | 03-04 | FastAPI query router (POST /agents/{id}/query, GET /agents/{id}/queries) + schemas + main.py registration | ✓ Complete |
| Wave 5 | 03-05 | Full unit tests (retrieval_service + retrieve_and_rank — replace xfail stubs) | ✓ Complete |
| Wave 6 | 03-06 | Integration test (test_query_route.py — replace stub) + guarded E2E test | ✓ Complete |
| Wave 7 | 03-07 | Demo notebook (notebooks/demo_m3.ipynb) + scripts/demo_m3.sh (1 human checkpoint) | ✓ Complete |

**Cross-cutting constraints:**
- `acks_late=True` AND idempotency guard on retrieve_and_rank task
- Connection strings never in Celery task args — fetched from control DB by agent_id
- FastAPI never does work inline — POST /agents/{id}/query dispatches to runtime queue
- voyage-3 PINNED with input_type="query" (different from ingestion input_type="document")
- BM25 via native tsvector + ts_rank_cd ONLY — no pg_search, no pgbm25 (deprecated Neon March 2026)
- RRF constant k=60 hardcoded (not parameterized)
- psycopg2 vector cast: %(query_vector)s::vector with str(query_vector) as parameter

**Success Criteria:**
1. Query returns ranked chunks with full trace: which path matched, fusion scores, rerank deltas.
2. Vector-only, keyword-only, and hybrid queries each return meaningfully different candidate sets.
3. Retrieval strategy (k values, rerank threshold, expansion flag) changeable via JSON config with no code changes.
4. Jupyter notebook shows candidates at each stage on a real query against M2 tenant DB.

**Demo:** Query notebook showing candidate sets at each retrieval stage — vector, BM25, fused, reranked.
**UI hint:** no

---

### M4 — Reasoning Engine + Widget v0

> **FIRST HIREABLE ARTIFACT.** M1–M4 is a complete portfolio piece. Public demo + architecture blog post ship here. M5–M10 strengthen what already exists.

**Goal:** Wire retrieval to a Claude Agent SDK agent with four tools, deliver a Preact iframe widget under 20kb gzipped, and publish a live public demo.
**PRD:** `prd-M4.md` (TBD)
**Requirements:** AGT-01 through AGT-11
**Depends on:** M3
**Phases:** Planned — 8 plans, 7 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 04-01 | Schema foundation: control DB 0004 soul fields + tenant DB 0003 conversations (R-01 fix) + Settings JWT_SECRET/SMTP_* + pyproject pins (claude-agent-sdk, python-jose) | ✓ Complete |
| Wave 2 | 04-02 | Agent domain core: build_system_prompt assembler + four MCP tools (retrieve, lookup_structured allowlist, escalate_to_human, clarify) + build_tool_server factory + 11 unit tests | ✓ Complete |
| Wave 3 | 04-03 | run_agent_turn Celery task (asyncio.run bridge, R-02 sdk_session_id capture, idempotency, citation extraction, escalation routing) + escalation SMTP helper + celery_app include + 6 unit tests | ✓ Complete |
| Wave 4 | 04-04 | FastAPI routes: agent_chat (POST chat + GET conversations) + widget (config + chat + PUBLIC SSE per R-03) + JWT + rate limit + CORS + 25 unit tests | ✓ Complete |
| Wave 5 *(parallel)* | 04-05 | Preact widget — apps/widget/ scaffold + components + Design G CSS + Vite IIFE build + zlib bundle-size gate ≤20kb | ✓ Complete |
| Wave 5 *(parallel)* | 04-06 | Next.js admin Soul Editor + PATCH /agents/{id} backend + AgentSoulUpdate schema + 6 unit tests | ✓ Complete |
| Wave 6 | 04-07 | Integration test (guarded) + eval harness (judge.py + run_evals.py covering D1-D8) + 20 scenario JSON files (6 golden/5 edge/5 adversarial/4 oos) + eval SQL fixture | ✓ Complete |
| ~~Wave 7~~ | ~~04-08~~ | ~~Static demo page + Bash & PowerShell orchestrator scripts + guarded E2E test~~ | ✗ Superseded by 04-09 + 04-10 |
| Wave 8 | 04-09 | Demo cleanup: rename eval fixture (Bella Vista → Acme Consulting); replace demo page with sign-in redirect; add guarded E2E test; generic provision_agent scripts; delete old demo scripts | ○ Pending |
| Wave 9 *(blocked on 04-09)* | 04-10 | Clerk full platform auth: PyJWT JWKS FastAPI middleware; dual-auth (Bearer + X-API-Key fallback); svix webhook provisioning; /me/provision self-heal; @clerk/nextjs admin UI (middleware + layout + sign-in + soul editor migration); CORS Authorization+PATCH | ○ Pending |
| Phase 4.2 Wave 1 | 04.2-01 | Frontend foundation: fix globals.css tokens (--bg, status colors, shadows); make ''/'' public; sign-up page; TopNav component; nested app/agents/layout.tsx | ○ Pending |
| Phase 4.2 Wave 1 *(parallel)* | 04.2-02 | Backend foundation: Alembic 0009 widget_config column + Agent ORM field; 4 Pydantic schemas (AgentListResponse, WidgetColorsSchema, WidgetTypographySchema, WidgetConfigUpdate); 3 new routes (GET /agents list, GET/POST /agents/{id}/widget-config); 7 unit tests | ○ Pending |
| Phase 4.2 Wave 2 *(blocked on 04.2-01 + 04.2-02)* | 04.2-03 | Landing page (/) server component + dashboard (/agents) client component listing agents via GET /api/v1/agents + AgentCard component | ○ Pending |
| Phase 4.2 Wave 2 *(blocked on 04.2-01)* | 04.2-04 | Agent journey view (/agents/[id]) with JourneyStepper + StepSubtaskCard + create-agent wizard (polling) + ingest/eval stubs | ○ Pending |
| Phase 4.2 Wave 2 *(blocked on 04.2-01 + 04.2-02)* | 04.2-05 | Deploy page (/agents/[id]/deploy): Embed Code tab + full Customise Widget tab (3-column panel: appearance + 9 color pickers + typography + live preview) wired to widget-config endpoints | ○ Pending |

**Cross-cutting constraints:**
- claude-agent-sdk==0.1.81 PINNED (do not upgrade — 0.2.82 is forbidden per CLAUDE.md)
- asyncio.run() inside Celery task (NEVER loop.run_until_complete — broken in Python 3.12)
- acks_late=True AND idempotency guard on run_agent_turn
- Connection strings never in Celery task args — fetched from control DB by agent_id
- FastAPI never does work inline — POST chat dispatches to runtime queue
- Widget bundle ≤20480 bytes gzipped (G-01 hard CI gate)
- ALLOWED_LOOKUP_TABLES frozenset enforced in lookup_structured (G-04 hard block)
- R-01 BLOCKING: tenant DB migration 0003 fixes conversations schema gap (agent_id + metadata)
- R-02: SDK session_id captured from ResultMessage and stored in conversations.metadata
- R-03: GET /widget/jobs/{job_id}/events is PUBLIC (EventSource cannot send X-API-Key header)
- Design G (Parchment & Wine) palette locked — widget + admin + demo all use it

**Success Criteria:**
1. Public test site visitor asks a question → grounded answer with source citation footer from real ingested data.
2. Conversation persists across multiple turns; new browser session starts fresh.
3. `escalate_to_human` fires → widget shows summary + capture form; owner receives notification.
4. Widget bundle under 20kb gzipped; loads via iframe on plain HTML with correct CORS/CSP.
5. Soul editor uses structured fields (voice, do, do-not) — not a blank textarea.

**Demo:** Public test site with live embedded widget answering questions on real ingested data.
**UI hint:** yes

---

### M5 — Validation Chain

**Goal:** Wrap every agent response with three async Claude judges — Gatekeeper, Auditor, Strategist — logging structured verdicts to Langfuse v4.
**PRD:** `prd-M5.md` (TBD)
**Requirements:** VAL-01 through VAL-07
**Depends on:** M4
**Phases:** Planned — 5 plans, 5 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 05-01 | Foundation: langfuse pin + LANGFUSE_* settings + confidence threshold; control DB 0010 (strategy_resynthesis_flagged) + tenant DB 0004 (verified_qa_candidates); Agent ORM field; Wave-0 test scaffold | [x] |
| Wave 2 | 05-02 | validation_service.py: 3 verdict Pydantic models + 3 Haiku tool-use judge calls + Langfuse v3 logging helper (TDD) | [x] |
| Wave 3 | 05-03 | validators.py: run_gatekeeper / run_auditor / run_strategist runtime tasks + verified_qa_candidates insert (D-19) + resynthesis flag (VAL-06) + celery_app include | [x] |
| Wave 4 | 05-04 | agent.py: capture retrieve tool result for Auditor + dispatch gatekeeper to auditor to strategist chain after agent.response (chain not chord) | [x] |
| Wave 5 | 05-05 | demo_m5.sh (adversarial query, 3 verdicts, local processes) + guarded E2E test + Langfuse walkthrough checkpoint (VAL-07) | [x] Complete |

**Plans:** 5 plans, **all complete** ✓

**Cross-cutting constraints:**
- acks_late=True AND idempotency guard on all three validator tasks
- Connection strings never in Celery task args — Auditor decrypts conn_str at runtime by agent_id (CTL-08)
- FastAPI never does work inline — validators are runtime-queue tasks dispatched from run_agent_turn
- Celery chord BROKEN on worker_pool=solo — dispatch via chain with .si() immutable signatures
- Langfuse v3 (3.12.1): start_as_current_generation() only — never start_span()/start_generation()/.trace()
- No asyncio in validators — synchronous anthropic Haiku calls (not Agent SDK per D-02)
- No Docker — demo_m5.sh targets local processes only

**Success Criteria:**
1. Every widget response has three structured verdicts in Langfuse within seconds — user never waits.
2. Adversarial query producing partial grounding shows Auditor `partial`/`ungrounded` with specific citation spans.
3. Repeated `ungrounded` failures set `strategy_resynthesis_flagged` on the agent row.
4. Developer walks through Langfuse trace showing all three verdicts, Pydantic payloads, and Haiku cost per turn.

**Demo:** Adversarial query → Langfuse trace walkthrough of all three judge verdicts.
**UI hint:** no

---

### M6 — Eval System

**Goal:** Automate nightly Ragas 0.4.x evals against Neon DB branches, mine production conversations for failing scenarios, surface pass rates in admin UI.
**PRD:** `prd-M6.md` (TBD)
**Requirements:** EVL-01 through EVL-08
**Depends on:** M5
**Phases:** In progress — 9 plans, 5 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 06-01 | Foundation: tenant DB 0005 (verified_qa + eval_scenarios), Settings eval thresholds, Celery beat schedule | [x] Complete |
| Wave 2 | 06-02 | eval_service.py: Ragas 0.4.x harness (4 metrics) + Neon branch management (neon_service.py) | [x] Complete |
| Wave 2 | 06-03 | scenario_service.py: Claude API scenario generator + production conversation mining | [x] Complete |
| Wave 3 | 06-04 | run_eval_suite Celery task (runtime queue) + run_eval_suite_beat dispatcher + celery_app wiring | [x] Complete |
| Wave 3 | 06-05 | verified_qa promotion + retrieval_service.py verified_qa_lookup (cache-hit path) | [x] Complete |
| Wave 4 | 06-06 | FastAPI eval routes: GET /agents/{id}/eval-runs + GET /agents/{id}/eval-runs/{run_id}/results | [x] Complete |
| Wave 4 | 06-07 | Next.js eval dashboard page: /agents/[id]/evals (Pass Rates tab + Scenarios tab) | [x] Complete |
| Wave 5 | 06-08 | Unit + integration tests for eval_service, scenario_service, run_eval_suite | [x] Complete |
| Wave 5 | 06-09 | scripts/demo_m6.sh + guarded E2E test + demo_m6 verification | [x] Complete |

**Success Criteria:**
1. Nightly Celery beat triggers eval run; executes against a Neon branch; branch deleted after run.
2. Admin UI shows per-metric pass rates (Faithfulness, Answer Relevance, Context Precision, Context Recall) over time as a chart.
3. Dashboard shows individual scenario pass/fail — not just aggregate scores.
4. At least one scenario mined from a production conversation where Gatekeeper/Auditor flagged an issue.

**Demo:** Eval dashboard with real run, per-metric charts, per-scenario detail, and at least one mined scenario.
**UI hint:** yes

---

### M7 — Red Team

**Goal:** Three Claude Agent SDK adversarial agents probe the deployed agent, classify findings by severity, and block deployment on critical findings.
**PRD:** `prd-M7.md` (TBD)
**Requirements:** RED-01 through RED-08
**Depends on:** M4 only — **parallelizable with M6**
**Phases:** In progress — 6 plans, 5 waves

| Wave | Plan | Objective | Status |
|------|------|-----------|--------|
| Wave 1 | 07-01 | Foundation: tenant DB 0006 (status + deployment_blocked on red_team_runs) + RED_TEAM_* settings + 2 xfail test stubs | [x] Complete |
| Wave 2 | 07-02 | red_team_service.py scaffold + 3 agent classes (PromptInjection, DataLeakage, Hallucination) + Haiku severity classifier | ✓ Complete |
| Wave 3 | 07-03 | run_red_team Celery task (runtime queue) + celery_app beat schedule (red-team-weekly) + idempotency guard | ✓ Complete |
| Wave 3 | 07-04 | FastAPI red team routes: POST /agents/{id}/red-team-runs + GET results | ✓ Complete |
| Wave 4 | 07-05 | Unit tests: de-xfail 2 stubs + test_run_red_team + deployment gate assertions | ✓ Complete |
| Wave 5 | 07-06 | scripts/demo_m7.sh (weak agent fixture + prompt injection trace + critical finding + deployment blocked) + guarded E2E | ✓ Complete |

**Success Criteria:**
1. Intentionally weak agent fails pre-deployment red team with `critical` finding; deployment blocked.
2. Captured injection trace shows full attack sequence — multiple turns, probe, agent response.
3. Data leakage agent fails to extract cross-tenant data or PII from a correctly configured agent.
4. Corpus injection canary (planted at M2 ingestion) is caught at sanitization — agent does not execute it.

**Demo:** Weak agent blocked at deployment with captured prompt injection trace showing severity classification.
**UI hint:** no

---

### M8 — Pre-deployment Checklist + Human Validation

**Goal:** Complete the non-technical owner journey — orchestrator reads all signals, writes plain-language report, owner approves, widget goes live; a recorded video proves a non-developer can do it unassisted.
**PRD:** `prd-M8.md` (TBD)
**Requirements:** DEP-01 through DEP-08
**Depends on:** M6 and M7
**Phases:** Defined when M8 becomes active

**Success Criteria:**
1. Non-technical tester completes full journey (signup → ingest → approve → widget live) without assistance.
2. Critical finding → `block` recommendation → deploy button disabled.
3. `ship_with_warnings` → each warning acknowledged individually; no "accept all"; acknowledgments logged.
4. On approval → iframe snippet shown; owner pastes into any web page; agent is live.

**Demo:** Recorded video of a non-developer completing the canonical happy path end-to-end.
**UI hint:** yes

---

### M9 — Retrieval Strategy Synthesis

**Goal:** Replace hand-written M3 retrieval configs with a strategist agent that analyzes corpus shape and generates optimized configs automatically.
**PRD:** `prd-M9.md` (TBD)
**Requirements:** STR-01 through STR-03
**Depends on:** M2, M3
**Phases:** Defined when M9 becomes active

**Success Criteria:**
1. New agent after M9 receives auto-generated strategy — no manual JSON editing required.
2. Two tenants with different data shapes receive meaningfully different configs.
3. Auto-generated strategies produce better Ragas metrics vs default config — confirmed by eval run comparison.

**Demo:** Two tenants with different data shapes get different auto-generated strategies with improved eval metrics.
**UI hint:** no

---

### M10 — Maintenance Crons + Observability Polish

**Goal:** Make all post-deployment operations autonomous — weekly red team, monthly drift detection, owner digest emails, Langfuse dashboards, alerting.
**PRD:** `prd-M10.md` (TBD)
**Requirements:** OPS-01 through OPS-06
**Depends on:** M8
**Phases:** Defined when M10 becomes active

**Success Criteria:**
1. Weekly red team cron fires per deployed agent; owner receives email with severity-classified findings.
2. Owner receives weekly digest: conversation counts, eval drift, red team findings, escalation rate.
3. Langfuse dashboard shows p50/p95/p99 latency, cost/conversation, judge outputs, grounding rates in one view.
4. Simulated metric regression triggers alert visible in admin UI and sent to owner.

**Demo:** Live Langfuse dashboard plus example weekly digest email.
**UI hint:** yes

---

## Dependency Graph

```
M1 → M2 → M3 → M4 → M5 → M6 ┐
                              ├─→ M8 → M10
                    M4 → M7 ──┘
                    M2, M3 → M9 (independent, after M3)
```

M6 and M7 are parallelizable — both only require M4 to be complete.

---

## How to Start

```
/clear
/gsd-discuss-phase 1    ← begin M1 phase planning
```

Each milestone uses its own PRD file as the source of truth for phase decomposition.
When starting a new milestone: write `prd-MN.md` first, then run `/gsd-discuss-phase`.

---

*Roadmap created: 2026-05-12*
*Last updated: 2026-05-23 — M7 COMPLETE (6/6 plans): demo_m7.sh (weak agent + deployment gate assertion + injection trace) + guarded E2E test (RED_TEAM_E2E_ENABLED); RED-08 satisfied*
