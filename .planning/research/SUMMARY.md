# Project Research Summary

**Project:** Veridian
**Domain:** Production Multi-Tenant RAG Platform
**Researched:** 2026-05-12
**Confidence:** HIGH (stack fully verified against PyPI + official docs; architecture confirmed from Neon/Anthropic reference implementations; pitfalls sourced from independent production failure post-mortems)

---

## Executive Summary

Veridian is a production-grade, multi-tenant RAG platform built for a specific dual audience: non-technical SMB owners who need a deployable customer service agent, and hiring managers at AI-first companies who need evidence of LLMOps engineering depth. The research confirms that the PRD architectural choices are sound and production-proven. Per-tenant Neon project isolation is explicitly endorsed by Neon documentation and reference implementations. The Claude Agent SDK stateless `query()` pattern is the correct multi-tenant model. The Celery + Redis two-queue design is the right call for a solo developer who needs operational simplicity without sacrificing durability. The full stack has been verified against PyPI as of May 2026 with three material breaking changes the PRD did not anticipate: Langfuse v4 (March 2026 rewrite with incompatible API), Ragas 0.4.x (January 2026, import paths and sample schema changed), and pg_search (ParadeDB extension deprecated on Neon as of March 2026 -- use native tsvector).

The recommended build sequence is strictly linear through M4 because each layer is a hard dependency for the next: control plane (M1) -> ingestion pipeline (M2) -> hybrid retrieval (M3) -> reasoning engine + widget (M4). M4 is the first hireable artifact and the most important commercial milestone. After M4, M6 (eval) and M7 (red team) are parallelizable -- they both depend only on M4 existing, not on each other. M1 is disproportionately hard: it requires auth, Neon provisioning, Celery chain infrastructure, and SSE streaming all working together before a single document can be ingested. Treat M1 as a full sprint, not a foundation sprint.

The top risk profile is infrastructure reliability under failure scenarios rather than algorithmic difficulty. The hard problems are not whether retrieval will work but rather what happens when the Celery worker crashes mid-ingestion, what happens when Neon API returns ready before the database accepts connections, and what happens when the SSE stream is silently buffered by Nginx. All of these are well-understood in production Celery + Neon deployments, but they require deliberate design from M1 rather than being retrofittable. The chunking quality decisions made in M2 (table-aware ingestion, structure-aware boundaries, embedding preprocessing hash) similarly cannot be patched later without re-embedding the entire corpus.

---

## Key Findings

### Recommended Stack

The PRD stack is verified and current as of May 2026. All packages exist under the specified names, current versions are materially newer than the PRD implied, and three breaking changes require immediate attention. Eight production libraries absent from the PRD are mandatory at scale: asyncpg, tenacity, sse-starlette, PyJWT, python-multipart, pydantic-settings, structlog, and cryptography (Fernet). Pin Python to 3.11 or 3.12 and Pydantic to v2 globally -- both are hard requirements imposed by Langfuse v4 and Ragas 0.4.x.

**Core technologies:**

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| FastAPI | 0.136.1 | HTTP API layer | Industry standard async Python API; Pydantic-native |
| Pydantic | v2.x | Validation on all boundaries | Required by FastAPI, Langfuse v4, Ragas 0.4.x -- unified constraint |
| Celery | 5.6.3 | Task orchestration | Idempotent chains, acks_late, two-queue pattern |
| Redis | 7.x | Celery broker + SSE pub/sub | Lowest-latency broker; also powers SSE channel pattern |
| Alembic | 1.18.4 | Schema migrations | Async template required for per-tenant DB provisioning |
| claude-agent-sdk | 0.1.81 | Customer agents + red team agents | Iterative tool-calling loop; single-shot insufficient |
| anthropic | 0.101.0 | Judges, validators, structured outputs | Direct API for Gatekeeper/Auditor/Strategist; structured outputs in beta |
| Docling | 2.93.0 | Layout-aware document parsing | IBM Research; DocLayNet + TableFormer; LangChain integrated |
| Chonkie | 1.6.5 | Structure-aware chunking | RecursiveChunker + LateChunker; 33x faster than alternatives |
| voyageai | 0.3.7 | Embeddings + primary reranker | Best-in-class MTEB RAG benchmarks; rerank-2.5 instruction-following |
| cohere | 6.1.0 | Fallback reranker | Rerank 3.5/4.0; activate when Voyage unavailable |
| ragas | 0.4.3 | RAG evaluation metrics | v0.4 API break: new import paths, reference field, MetricResult objects |
| pyrit | 0.13.0 | Red team scaffolding | Attack orchestration; complement with custom Claude probes |
| langfuse | 4.6.1 | Observability, cost, latency | v4 rewrite (March 2026); start on v4, never install v3 |

**Three critical breaking changes:**
1. **Langfuse v4** (March 2026): Full API rewrite. `start_span()` -> `start_observation()`, `update_current_trace()` -> `propagate_attributes()`, non-LLM spans not exported by default. Any tutorial before March 2026 has the wrong API.
2. **Ragas 0.4.x** (January 2026): `ragas.metrics` -> `ragas.metrics.collections`; `ground_truths=["x"]` -> `reference="x"`; scores return `MetricResult` objects not floats. v0.3 code will break.
3. **pg_search (ParadeDB)** deprecated on Neon (March 2026). Use native `tsvector` + `ts_rank_cd` for BM25-style keyword search. This is the PRD stated fallback and is fully sufficient.

**Eight stack gaps not in PRD:**
- `asyncpg` -- async Postgres driver for tenant DB queries in async FastAPI routes
- `tenacity` -- retry with exponential backoff wrapping all Claude/Voyage/Cohere/Langfuse calls
- `sse-starlette` -- Server-Sent Events library for FastAPI job status streaming
- `PyJWT` -- JWT generation/validation for widget short-lived tokens
- `python-multipart` -- file upload handling for drag-and-drop document ingestion
- `pydantic-settings` -- config from environment variables (12-factor)
- `structlog` -- structured JSON logging for correlation with Langfuse traces
- `cryptography` (Fernet) -- AES-256 encryption for tenant connection strings at rest

---

### Expected Features

The feature set is well-defined. The PRD covers all the table stakes and most differentiators. Two M4 gaps require resolution before the demo ships.

**Must have (table stakes) -- M1 through M4:**
- Document ingestion (PDF, URLs, text) with live SSE progress stream
- Semantic + hybrid retrieval (vector + BM25 + RRF + Voyage rerank)
- Claude Agent SDK agent with four custom tools: retrieve, lookup_structured, escalate_to_human, clarify
- Preact iframe widget (<20kb gzipped) with basic theming
- Admin dashboard for the owner journey (signup -> ingest -> deploy)
- Source citations visible in widget responses (confirm in M4 scope)
- Human escalation path with conversation summary

**Should have (differentiators) -- M5 through M8:**
- Per-tenant Neon project isolation (M1 -- already a differentiator, not a table stake)
- Validation chain: Gatekeeper -> Auditor -> Strategist on every response, async after stream (M5)
- Automated Ragas eval suite with scenario generation and Celery beat schedule (M6)
- Three red team agents: prompt injection, data leakage, hallucination-under-pressure (M7)
- Pre-deployment checklist with orchestrator agent and human acknowledgment gate (M8)
- Langfuse trace-level observability with cost attribution per tenant (M5+)
- Neon branch-based eval isolation -- eval runs against a DB branch, never production (M6)

**Defer (v2+ or M9/M10):**
- Retrieval strategy synthesis from corpus shape analysis (M9)
- Weekly maintenance crons, drift detection, owner digest email (M10)
- Voice channel, multi-language support, CRM integrations, white-label mode
- Scheduled data refresh / auto-crawl (manual re-upload is v1)

**Two M4 gaps requiring resolution:**
1. **Source citations in widget** (MEDIUM priority): Retrieval trace is logged but not surfaced to end users. IBM/CHI 2025 research confirms citations improve trust more than confidence scores. Frontend-only change once trace is logged -- do it before M4 ships.
2. **Escalation UX in widget** (HIGH priority): The `escalate_to_human` tool exists in the agent SDK; what the widget renders when it fires is unspecified. Define before M4: widget shows conversation summary + capture form, owner receives via webhook or email. This is a clarification of existing functionality, not a new system.

---

### Architecture Approach

The architecture follows a 10-layer model with a strict separation between the deterministic programmatic core (L1-L4) and the agentic edges (L5-L9). The programmatic core handles everything that is testable, cheap to run, and deterministic: auth, provisioning, ingestion, chunking, embedding, and retrieval. The agentic edges handle open-ended judgment: customer agent reasoning, response validation, eval scoring, red teaming, and deployment recommendation. This separation means M1-M4 can be built and tested in isolation from the LLM-heavy M5-M8 layers.

**Major components:**

| Layer | Component | Responsibility |
|-------|-----------|---------------|
| L1 | Control Plane (FastAPI) | Auth, tenant CRUD, job dispatch, SSE, widget config endpoint |
| L2 | Orchestration Plane (Celery + Redis) | All long-running work: ingestion chains, chat, eval, red team |
| L3 | Data Plane | Control DB (shared Neon) + Tenant DB (one Neon project per tenant) |
| L4 | Retrieval Engine | pgvector HNSW + tsvector BM25 -> RRF SQL CTE -> Voyage Rerank |
| L5 | Reasoning Engine | Claude Agent SDK `query()`, system_prompt injected per call, four custom tools |
| L6 | Validation Chain | Gatekeeper -> Auditor -> Strategist, Claude Haiku, async after stream |
| L7 | Eval System | Ragas harness, scenario generation, Celery beat, Neon branch isolation |
| L8 | Red Team | Three Claude Agent SDK agents, PyRIT scaffolding, corpus injection tests |
| L9 | Pre-Deploy Checklist | Claude Sonnet orchestrator reads L7+L8 signals, writes recommendation |
| L10 | Widget Delivery | Preact iframe, CDN, short-lived JWT, runtime Celery queue |

**Five validated architecture decisions:**
1. **Per-tenant Neon projects**: Neon explicitly endorses this pattern. Schema-per-tenant closes off Neon branching for eval isolation -- a core differentiator. Solo developer viability is high; provisioning is ~30 lines of Python calling the Neon REST API.
2. **Celery chains**: Right choice given existing Redis dependency and solo developer constraint. Two queues (pipeline/runtime) from day one -- never share.
3. **SSE via Redis pub/sub**: Celery task publishes to `job:{job_id}` channel; FastAPI SSE subscribes and yields. Push model beats polling. Critical: add `X-Accel-Buffering: no` header or Nginx silently buffers the stream.
4. **Claude Agent SDK stateless `query()`**: System prompt is passed in `ClaudeAgentOptions` at every call, not at construction. Per-tenant parameterization is done at call time. Session continuity via `resume=session_id` stored in the conversations table.
5. **Validation chain async after stream**: Run Gatekeeper/Auditor/Strategist after the response is streamed to the user, never synchronously. Synchronous validation adds 1-3 seconds to every response.

---

### Critical Pitfalls

The top five pitfalls, ordered by impact and earliest phase of prevention:

1. **Chunk boundary splits destroy retrieval quality silently (M2)**: Fixed-size chunking cuts semantic units mid-sentence. Use Docling HybridChunker on the `DoclingDocument` object, not on raw extracted text. This is a build-time decision -- re-embedding the entire corpus is the only fix after the fact.

2. **Table flattening produces near-zero faithfulness on tabular queries (M2)**: Docling extracts table cells as text; standard chunking then fragments them. Tables must be a separate ingestion pathway: convert each table to a Markdown table string before chunking, or chunk at table granularity. This is a confirmed open issue in Docling HybridChunker. Custom logic required in the `chunk_documents` Celery task.

3. **acks_late does not make tasks idempotent (M1/M2)**: `acks_late=True` prevents task loss on worker crash but does not prevent duplicate execution. A task that embeds 5000 chunks, then crashes before acknowledging, re-runs and creates 5000 duplicate chunks. Prevention: deterministic `chunk_id` (hash of document_id + chunk index + content hash) + `ON CONFLICT (chunk_id) DO NOTHING` upserts on every insert.

4. **Neon cold starts break p95 latency at demo time (M4)**: Per-tenant Neon projects scale to zero by default. Cold starts are 300-800ms. For the M4 public demo, disable scale-to-zero on the demo tenant explicitly. For production, implement a `SELECT 1` keep-alive ping from Celery beat.

5. **Neon provisioning race condition causes PROVISIONING_FAILED in ~10% of concurrent signups (M1)**: `project.status == "active"` from the Neon API does not guarantee query-readiness. After the project returns active, add an explicit connection probe loop (`SELECT 1` with exponential backoff, max 10 retries) before dispatching Alembic migrations. Use a Redis distributed lock to prevent concurrent migration runners for the same tenant.

---

## Implications for Roadmap

The PRD M1-M10 milestone structure is architecturally correct and should be preserved as the roadmap phase structure. The research validates it with these additions: M1 is harder than implied, M6+M7 are parallelizable, and admin UI work must be distributed across milestones rather than deferred to M4.

### Phase 1 (M1): Control Plane Skeleton
**Rationale:** Everything else depends on this. Celery chains cannot be written until Celery exists. Ingestion cannot start until Neon provisioning works. SSE cannot stream until Redis pub/sub is wired up. M1 is the most dependency-dense milestone proportionally.
**Delivers:** FastAPI auth (email/password, JWT), tenant/agent CRUD, Neon project provisioning via Celery task, Redis pub/sub -> SSE job status streaming, control DB schema, Fernet-encrypted tenant credential storage.
**Addresses:** Table stakes: live progress feedback, admin dashboard foundations.
**Avoids:** Migration race condition (connection probe loop before Alembic), non-idempotent provisioning (check neon_project_id exists before calling Neon API), SSE Nginx buffering (X-Accel-Buffering: no header), chain partial failure state (task completion tracking table in control DB).
**Research flag:** STANDARD -- Celery/FastAPI/Neon patterns are well-documented. No deep research needed.

### Phase 2 (M2): Ingestion Pipeline
**Rationale:** Requires tenant DB to exist (M1). Chunking and embedding quality decisions made here cannot be patched later without full re-embedding. Gets the hard irreversible decisions right first.
**Delivers:** Docling layout-aware parsing (L2 Celery pipeline task), table-aware chunking path (separate pathway for tables to Markdown), Chonkie RecursiveChunker for hierarchical structure, Voyage embedding with preprocessing hash stored per chunk, ON CONFLICT (chunk_id) DO NOTHING upserts, SSE status per task step.
**Uses:** Docling 2.93.0, Chonkie 1.6.5, voyageai 0.3.7, asyncpg, tenacity (Voyage retry with backoff), python-multipart (file upload).
**Implements:** L2 pipeline queue tasks (parse, chunk, metadata, embed), L3 tenant DB population.
**Avoids:** Table flattening (custom table-as-Markdown path), chunk boundary splits (HybridChunker on DoclingDocument not raw text), embedding drift (preprocessing hash + pinned model version voyage-3-large), acks_late without idempotency (deterministic chunk_id + upserts), Voyage rate limits (batch at 128 chunks per request).
**Research flag:** STANDARD for ingestion patterns. RESEARCH NEEDED for table chunking specifics -- confirm optimal table-to-Markdown conversion strategy given the confirmed Docling HybridChunker open issue.

### Phase 3 (M3): Hybrid Retrieval
**Rationale:** Requires embedded chunks in tenant DB (M2). Retrieval quality is the ceiling on agent quality. M3 must have a demonstrably correct retrieval notebook before M4 begins.
**Delivers:** pgvector HNSW search with per-retriever minimum cosine threshold (0.7+), tsvector BM25 with per-retriever minimum ts_rank threshold, RRF SQL CTE fusion, MMR deduplication before fusion, Voyage Rerank-2.5 on top-30 candidates, retrieval strategy stored as JSON config.
**Implements:** L4 retrieval engine (pure SQL + pgvector HNSW + tsvector + Voyage).
**Avoids:** RRF hiding quality problems (min-score thresholds on both retrievers before fusion), HNSW degradation (REINDEX CONCURRENTLY task after batch ingestion), Voyage reranking k=100 (rerank only top-30 from fusion).
**Research flag:** STANDARD -- hybrid retrieval + RRF patterns are well-documented. pgvector HNSW configuration is straightforward.

### Phase 4 (M4): Reasoning Engine + Widget v0 (FIRST HIREABLE ARTIFACT)
**Rationale:** Requires retrieval working (M3). This is the milestone that matters for hiring. Ship fast once M3 is solid.
**Delivers:** Claude Agent SDK query() with per-tenant system_prompt, four custom tools (retrieve, lookup_structured, escalate_to_human, clarify) as in-process MCP servers, Langfuse trace instrumentation on agent calls and retrieve tool (instrument here not in M5), Preact widget (<20kb gzipped) with JWT auth + theming, source citations in widget responses, escalation UX in widget (conversation summary + capture form + owner webhook), public demo site with real ingested dataset, architecture blog post.
**Implements:** L5 reasoning engine, L10 widget delivery.
**Gap resolution required before M4 ships:** Source citations in widget (Gap 1 -- frontend-only change, Low complexity), Escalation UX in widget (Gap 2 -- clarify tool result surfacing, Low-Med complexity), conversation memory via resume=session_id (verify sliding window in system prompt), JWT refresh logic in widget (prevent 401 on conversations over 15 min), scale-to-zero disabled on demo tenant.
**Avoids:** Anti-pattern of building agent before retrieval is solid (M3 demo notebook is the gate), context bleed between tenants (stateless query() with no shared state), connection string leakage into Celery queue (pass tenant_id, decrypt at execution time).
**Research flag:** STANDARD for Claude Agent SDK (official docs confirmed). RESEARCH NEEDED for Preact widget CSP compatibility with SMB websites and optimal JWT-in-iframe delivery pattern.

### Phase 5 (M5): Validation Chain
**Rationale:** Wraps M4 responses. Requires agent running. No hard dependencies on M6 or M7.
**Delivers:** Gatekeeper (scope check, structured output), Auditor (faithfulness/grounding check with citation-span approach), Strategist (retrieval improvement signals), all running async after response is streamed, Langfuse generation logging per judge call, sampling rate config with hardcoded step-down thresholds.
**Uses:** anthropic 0.101.0 (Haiku for judges), Langfuse 4.6.1 v4 API (start_observation(), opt-in non-LLM spans).
**Implements:** L6 validation chain.
**Avoids:** Validation chain blocking response stream (enforce async pattern), validation chain as rubber stamp (hardcode sampling step-down thresholds with explicit triggers -- never drop below 30% without grounding_rate > 0.92 for 7 days).
**Research flag:** STANDARD -- async validation after SSE stream is a well-understood pattern.

### Phase 6 (M6): Eval System
**Rationale:** Requires M5 validation telemetry as training signal for scenario mining. Parallelizable with M7 after M4 ships -- both only need M4, not each other.
**Delivers:** Ragas harness (v0.4 API: ascore with kwargs, reference= not ground_truths=, MetricResult.value), scenario generation from corpus at build time, Celery beat schedule for nightly eval runs, Neon branch per eval run (created before deleted after), human-labeled ground truth set for calibration (20-30 scenarios), eval dashboard in admin UI.
**Uses:** ragas 0.4.3 (v0.4 API from scratch), Neon branching API, Celery beat.
**Implements:** L7 eval system.
**Avoids:** Ragas judge calibration problem (human-labeled calibration set mandatory; validate Haiku judge correlation > 0.75 with human labels before trusting scores), eval branch accumulation (automate branch deletion after run), running evals against production DB (always use branch credentials with read-only access).
**Research flag:** RESEARCH NEEDED -- Ragas v0.4 API is fresh (January 2026); most online examples use v0.3. Need explicit v0.4 patterns for the custom harness.

### Phase 7 (M7): Red Team
**Rationale:** Requires M4 (an agent to attack). Parallelizable with M6. Start as soon as M4 ships if timeline permits.
**Delivers:** Three Claude Agent SDK adversarial agents (prompt injection, data leakage, hallucination-under-pressure), PyRIT scaffolding for attack orchestration and severity classification, hardcoded rule-based secondary severity check for known critical patterns (cannot be overridden by LLM classification), corpus injection canary test (plant known injection strings verify agent resists).
**Uses:** pyrit 0.13.0, claude-agent-sdk 0.1.81 (sub-agent pattern via AgentDefinition).
**Implements:** L8 red team.
**Avoids:** Red team missing indirect prompt injection (extend data leakage agent to test corpus-planted injections), severity classification drift (secondary rule-based check for known critical patterns), red team surfacing issues that should have been fixed in M2/M3.
**Research flag:** RESEARCH NEEDED -- PyRIT sub-agent patterns for Claude-specific surfaces; indirect injection test patterns in the retrieval path.

### Phase 8 (M8): Pre-Deployment Checklist + Human Gate
**Rationale:** Requires M6 (eval results) + M7 (red team findings) to aggregate. After M8, the full non-technical owner journey is validated end-to-end.
**Delivers:** Claude Sonnet orchestrator agent that reads all L7+L8 signals and writes a structured report (ship/ship_with_warnings/block), owner-facing plain-language deployment report, per-warning acknowledgment UI (not a single accept-all button), acknowledgment logging with reasoning field, escalation webhook/ticket creation when human handoff triggers.
**Implements:** L9 pre-deployment checklist.
**Avoids:** Checklist as rubber stamp (force per-warning acknowledgment; block must actually trigger; severity thresholds cannot be placeholders).
**Research flag:** STANDARD -- orchestrator agent pattern is the same query() pattern as L5, just with different system prompt and tools.

### Phase 9 (M9): Retrieval Strategy Synthesis
**Rationale:** Requires M3 (retrieval) + M2 (corpus shape data). Replaces hand-written JSON retrieval configs with agent-generated configs. Does not require M3 to be re-architected.
**Delivers:** Strategist agent that analyzes corpus shape (document types, size distribution, domain vocabulary density) and generates per-tenant retrieval strategy JSON config.
**Research flag:** RESEARCH NEEDED -- limited public documentation on automated retrieval strategy synthesis; likely requires experimentation.

### Phase 10 (M10): Maintenance + Observability Polish
**Rationale:** Requires full system running (M8+). Automates the operational tasks that were manual in M1-M9.
**Delivers:** Weekly red team crons, monthly eval drift detection, owner digest email (conversation counts, eval drift, escalation rate, data freshness warnings), alerting on metric regressions, HNSW REINDEX automation, Neon scale-to-zero keep-alive pings for active tenants.
**Research flag:** STANDARD -- Celery beat scheduling is well-documented.

---

### Phase Ordering Rationale

**M1-M4 strict linearity is non-negotiable.** Each layer is a hard dependency for the next. There is no useful demo at M2 without M1 provisioning working, no retrieval demo without M2 chunks in the DB, and no agent demo without M3 retrieval emitting correct candidates. Do not attempt to parallelize M1-M4 across subsystems -- the integration cost exceeds the time saved.

**M6 and M7 are parallelizable after M4.** This is the one timeline acceleration lever available. Both depend only on a running agent (M4). If the hiring deadline is acute after M4 ships, M7 (red team) can start while M6 (eval) is being built. M8 waits for both, but M5 (validation chain) can be shipped before either.

**Admin UI must be distributed across milestones.** The PRD requires a polished admin UI. Building all UI in M4 creates a blocker. The SSE progress stream consumer and file upload UI are required for M1/M2 demos. Agent configuration UI is required for M4. Do not defer all frontend work to M4.

**M9 and M10 are low-risk to defer.** Both improve the system without changing its core architecture. If the hiring timeline requires stopping at M8, M9 and M10 strengthen the portfolio without being prerequisites for the public demo or the architectural blog post.

---

### Research Flags

**Phases needing deeper research during planning:**

| Phase | Why Research Is Needed |
|-------|----------------------|
| M2 | Table-aware chunking implementation specifics; confirmed Docling HybridChunker open issue with table structure |
| M4 | Preact widget CSP compatibility with SMB websites; JWT delivery pattern inside iframe (postMessage vs URL hash fragment) |
| M6 | Ragas v0.4 API is fresh (January 2026); most examples online use v0.3; confirm ascore(), MetricResult, llm_factory() patterns |
| M7 | PyRIT custom Claude probe patterns; indirect injection testing in the retrieval path |
| M9 | Automated retrieval strategy synthesis; limited public documentation |

**Phases with standard patterns (skip research-phase):**

| Phase | Why Research Can Be Skipped |
|-------|---------------------------|
| M1 | FastAPI + Celery + Redis + Neon patterns are extensively documented; auth patterns are standard |
| M3 | Hybrid retrieval + RRF + pgvector HNSW patterns are well-documented with production examples |
| M5 | Async validation after SSE stream is a known pattern; Claude structured outputs are documented |
| M8 | Orchestrator agent follows the same query() pattern as L5 |
| M10 | Celery beat scheduling is standard; all components are already built |

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All packages verified against PyPI as of 2026-05-12; version numbers sourced directly; three breaking changes confirmed from official migration guides |
| Features | MEDIUM-HIGH | Table stakes and differentiators from multiple competitor analysis sources; two M4 gaps from IBM/CHI 2025 research and Salesforce 2025 data; some SMB UX claims from single sources |
| Architecture | HIGH | Per-tenant Neon pattern confirmed from Neon official docs + neondatabase/db-per-tenant reference implementation; Claude Agent SDK system_prompt in ClaudeAgentOptions confirmed from official docs; Celery acks_late patterns from Vinta production guide |
| Pitfalls | HIGH | Critical pitfalls sourced from independent production failure post-mortems (Towards Data Science, Nineleaps, DEV Community) plus official Docling issue tracker and Neon production checklist |

**Overall confidence:** HIGH

### Gaps to Address

- **Table chunking implementation** (M2 planning): Docling table flattening is a confirmed open issue but the optimal implementation strategy (table-as-Markdown vs row-with-headers vs single-chunk-per-table) depends on corpus type. Design during M2 planning against the actual demo dataset.
- **Ragas v0.4 harness design** (M6 planning): v0.4 was released January 2026. The migration guide is available but the custom harness pattern for Claude judges with ground truth calibration needs explicit design in M6 planning.
- **Widget JWT delivery pattern** (M4 planning): Confirm whether postMessage or URL hash fragment is the right pattern for passing the short-lived JWT into the Preact iframe context, given SMB website CSP constraints.
- **Conversation memory in Claude Agent SDK** (M4 planning): Verify the resume=session_id pattern correctly maintains sliding window context for multi-turn conversations without requiring manual message history injection into the system prompt.
- **M6/M7 parallel execution scope** (after M4): If the hiring timeline is tight after M4, decide before starting M5 which of M6 and M7 to start in parallel. M7 is faster to demonstrate and has direct portfolio signal.

---

## Sources

### Primary (HIGH confidence)
- PyPI: claude-agent-sdk 0.1.81 -- https://pypi.org/project/claude-agent-sdk/
- Claude Agent SDK official docs -- https://code.claude.com/docs/en/agent-sdk/overview
- Claude Agent SDK custom tools -- https://code.claude.com/docs/en/agent-sdk/custom-tools
- PyPI: anthropic 0.101.0 -- https://pypi.org/project/anthropic/
- PyPI: docling 2.93.0 -- https://pypi.org/project/docling/
- PyPI: chonkie 1.6.5 -- https://pypi.org/project/chonkie/
- PyPI: ragas 0.4.3 -- https://pypi.org/project/ragas/
- Ragas v0.3->v0.4 migration guide -- https://docs.ragas.io/en/stable/howtos/migrations/migrate_from_v03_to_v04/
- PyPI: langfuse 4.6.1 -- https://pypi.org/project/langfuse/
- Langfuse v3->v4 migration guide -- https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4
- PyPI: voyageai 0.3.7 -- https://pypi.org/project/voyageai/
- PyPI: celery 5.6.3 -- https://pypi.org/project/celery/
- Neon pgvector docs -- https://neon.com/docs/extensions/pgvector
- Neon pg_search deprecation -- https://neon.com/docs/extensions/pg_search
- Neon multitenancy guide -- https://neon.com/docs/guides/multitenancy
- Neon multi-tenant RAG blog post -- https://neon.com/blog/multi-tenant-rag
- neondatabase/db-per-tenant reference implementation -- https://github.com/neondatabase/db-per-tenant
- Neon production checklist -- https://neon.com/docs/get-started/production-checklist
- Fernet symmetric encryption -- https://cryptography.io/en/latest/fernet/
- Advanced Celery: idempotency, retries -- https://www.vintasoftware.com/blog/celery-wild-tips-and-tricks-run-async-tasks-real-world
- Docling table structure issue (confirmed) -- https://github.com/docling-project/docling-serve/issues/484
- Docling chunking concepts -- https://docling-project.github.io/docling/concepts/chunking/
- Trust Me on This: citations in RAG (IBM/CHI 2025) -- https://arxiv.org/abs/2601.14460

### Secondary (MEDIUM confidence)
- Orchestrating AI tasks: Celery vs Temporal -- https://dasroot.net/posts/2026/02/orchestrating-ai-tasks-celery-temporal/
- FastAPI SSE + Celery: real-time notifications -- https://dev.to/enlabe/notificaciones-en-tiempo-real-con-sse-fastapi-y-celery-3hb9
- Why 95% of RAG apps leak data -- https://medium.com/@pswaraj0614/why-95-of-rag-apps-leak-data-across-users-and-how-i-fixed-it-0e9ded006a8c
- Building successful multi-tenant RAG -- https://www.thenile.dev/blog/multi-tenant-rag
- Your chunks failed your RAG in production -- https://towardsdatascience.com/your-chunks-failed-your-rag-in-production/
- Embedding drift: the quiet killer of retrieval quality -- https://dev.to/dowhatmatters/embedding-drift-the-quiet-killer-of-retrieval-quality-in-rag-systems-4l5m
- Hybrid retrieval with RRF -- https://avchauzov.github.io/blog/2025/hybrid-retrieval-rrf-rank-fusion/
- The vector hangover: HNSW index memory bloat -- https://tech-champion.com/database/the-vector-hangover-hnsw-index-memory-bloat-in-production-rag/
- 10 RAG shifts redefining production AI in 2026 -- https://medium.com/microsoftazure/10-rag-shifts-redefining-production-ai-in-2026-7acbdd66076c
- Chatbot to human handoff: complete guide -- https://www.spurnow.com/en/blogs/chatbot-to-human-handoff
- Securing AI agents: red teaming with PyRIT -- https://techcommunity.microsoft.com/blog/appsonazureblog/securing-your-ai-agents-before-they-ship-red-teaming-with-microsoft-pyrit/4515514
- Evaluating the evaluators: RAG metrics -- https://www.tweag.io/blog/2025-02-27-rag-evaluation/
- 5 AI portfolio projects that get you hired in 2026 -- https://dev.to/klement_gunndu/5-ai-portfolio-projects-that-actually-get-you-hired-in-2026-5bpl
- RAG production failure: why demos do not scale -- https://www.nineleaps.com/rag-production-failure-why-demos-dont-scale/
- The Fin AI engine (Intercom) -- https://fin.ai/ai-engine

---
*Research completed: 2026-05-12*
*Ready for roadmap: yes*