# RAG Platform — Production PRD

> **Working name:** TBD (placeholder: `rag-platform`)
> **Author:** Mfanafuthi Mhlanga (Bantuson / Mzansi Agentive Pty Ltd)
> **Status:** Draft v2
> **Last updated:** 2026-05-16
>
> *Changelog (v1 → v2):* Added Verified Knowledge Layer between data plane and retrieval engine (new Layer 4; subsequent layers renumbered). Added entity extraction to M2. Added Conversation-Insights Engine to M10. Added paragraph-level extensions to M5, M6, and M8 covering verified-knowledge promotion paths.
> *Changelog (v2 → current):* Added Development Environment constraint (§ Dev Environment) — local-only runs, no Docker.

---

## 1. One-line summary

A production RAG platform where non-technical small business owners drop in their business data (documents, images, URLs, structured exports) and ship a customer service agent — provisioned on per-tenant Neon databases, deployable to any website via iframe, and continuously evaluated and red-teamed without operator intervention.

## 2. Problem

Small businesses cannot afford the engineering depth required to ship a customer service agent that is actually safe to deploy. The serious work is not the chat UI — it is the layer underneath: structure-aware ingestion, hybrid retrieval, query planning, response grounding, evaluation, red teaming, and ongoing maintenance. Existing tools either give the owner a chat widget with no rigour, or hand them an SDK and assume they will build the rigour themselves. Neither outcome ships a defensible agent.

This platform closes that gap. The owner answers domain questions in plain language; the system does the engineering.

## 3. Goals and non-goals

**Goals**

- A non-technical user completes the full journey — signup → ingest → deploy — without touching code or infrastructure.
- Every deployed agent passes a pre-deployment checklist covering eval pass rates, red team severity, latency, cost, and corpus coverage.
- Per-tenant data isolation via dedicated Neon projects; eval branches via Neon's branching feature.
- Continuous post-deployment maintenance: scheduled evals, weekly red team runs, drift alerts.
- Open source. The platform itself is the portfolio artifact.

**Non-goals (v1)**

- Voice channel. Text only.
- Multi-language. English only.
- Custom model hosting. Claude only on the agent side.
- Mobile-native SDKs. iframe-only delivery.
- A general-purpose vector DB. Neon-only is the architectural story.

## 4. Target user

The buyer is the small business owner — non-technical, time-poor, owns the customer relationship. The platform must be operable end-to-end by someone who has used Stripe and Mailchimp but never written code. Power users (technical founders, ops leads) get an "advanced" view that exposes retrieval strategy, eval scenarios, and red team configuration, but advanced is opt-in.

## 5. Architectural philosophy

**Programmatic core, agentic edges.**

Deterministic code handles anything that benefits from being testable, reproducible, and cheap to run — parsing, chunking, embedding, retrieval execution, migrations, scheduling, widget delivery, harness orchestration.

Claude agents handle anything that requires open-ended judgment — metadata enrichment, retrieval strategy synthesis, the customer service agent itself, LLM judges, red team probes, validation chains, and the pre-deployment recommendation.

The agents are inside the loop. They are not the loop.

## 6. System architecture

### Layer 1 — Control plane (FastAPI)

Stateless API surface. Responsibilities:

- Auth, tenant management, agent CRUD.
- Job dispatch to Celery; never performs long-running work inline.
- Widget config endpoint (`GET /widget/{agent_id}/config`) serving theming, agent ID, and short-lived JWTs to the iframe.
- Real-time job status via Server-Sent Events. Onboarding UX requires the user to watch their agent get built; SSE is the cheapest way to deliver that without WebSocket complexity.

One FastAPI app, modular routers, Pydantic on every boundary. No background work runs in the request thread.

### Layer 2 — Orchestration plane (Celery + Redis)

Every long-running operation is a Celery chain. The canonical agent-creation chain:

```
provision_neon
  → parse_documents
  → chunk_documents
  → generate_metadata          (summaries, keywords, questions, entities)
  → embed_and_migrate
  → synthesize_retrieval_strategy
  → build_reasoning_engine
  → generate_eval_suite
  → run_sandbox_evals           (seeds verified_qa from passing scenarios)
  → generate_red_team_suite
  → run_pre_deployment_checklist
  → await_human_validation
```

Each task is idempotent, writes status to the control DB, and emits an SSE event. Celery is configured with `acks_late=True` so a worker dying mid-task does not corrupt a tenant DB.

Two queues:

- `pipeline` — long ingestion/build chains. Low concurrency, high memory per worker.
- `runtime` — eval crons, red team runs, per-query enrichment. High concurrency, low memory.

Queue separation prevents a nightly red team run from starving a customer onboarding pipeline.

### Layer 3 — Data plane (Neon, per-tenant)

One Neon project per business. Within each tenant DB:

- `documents`, `chunks`, `embeddings` (pgvector), `chunk_metadata`, `entities`, `chunk_entities`
- `verified_qa` (verified knowledge layer — see Layer 4)
- `conversations`, `messages`, `tool_calls`
- `eval_runs`, `eval_results`, `red_team_runs`
- `conversation_insights` (graph artifacts produced by the monthly Conversation-Insights Engine — see M10)

A global control DB (also Neon, shared) holds tenants, agents, jobs, billing, and cross-tenant eval aggregates.

Provisioning flow:

1. `POST /projects` to Neon API.
2. Poll until project is ready.
3. Run Alembic migrations against the new connection string.
4. Store encrypted connection string in control DB.

**Neon branching for eval isolation.** Nightly evals run against a branch of the tenant's DB so production traffic is never affected. This is a genuine differentiator and a defensible architectural choice.

### Layer 4 — Verified Knowledge Layer (per-tenant, compounding)

The platform's answer to the "knowledge layer" pattern Pinecone formalised in Nexus, implemented natively in each tenant's Neon DB. The goal is the same — push reasoning upstream, serve trusted answers without re-deriving them at inference — but the implementation is open Postgres, owned by the tenant, and fed by the platform's own evaluation harness and validation chain rather than a separate vendor product.

Structure: a `verified_qa` table on each tenant DB, holding question/answer pairs that have been graded as faithful and grounded by the platform's own judges. Each row carries the original question embedding (for semantic match), the cited answer, the eval scores that earned it promotion, the source of promotion (`sandbox_test`, `production_promotion`, `human_authored`), and an invalidation timestamp tied to source-document lifecycle.

```sql
CREATE TABLE verified_qa (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question         TEXT NOT NULL,
    question_vector  VECTOR(1024) NOT NULL,
    answer           TEXT NOT NULL,
    citations        JSONB NOT NULL,
    source           TEXT NOT NULL,        -- 'sandbox_test' | 'production_promotion' | 'human_authored'
    faithfulness     NUMERIC,
    relevance        NUMERIC,
    promoted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_by      TEXT,                 -- 'system' | tenant_user_id
    last_used_at     TIMESTAMPTZ,
    use_count        INT DEFAULT 0,
    invalidated_at   TIMESTAMPTZ
);
CREATE INDEX verified_qa_vector_idx ON verified_qa
    USING hnsw (question_vector vector_cosine_ops);
```

**Promotion paths:**

1. **Sandbox-tested answers** (M6). When the eval harness runs scenario-generator questions against the agent and the judges return high faithfulness *and* high answer relevance, the question/answer pair is written to `verified_qa` with `source = 'sandbox_test'`. This means every passing pre-deployment test compounds into the agent's permanent knowledge — agents arrive at deployment having already answered their hardest test questions correctly, and those answers are served from cache.
2. **Production promotions** (M5 + M8). When the Auditor classifies a production response as `grounded` with high confidence, the response becomes a *candidate* for promotion, surfaced in the owner's weekly digest. Owner approves with one click; row lands in `verified_qa` with `source = 'production_promotion'`.
3. **Human-authored entries.** Owner can write canonical Q&A pairs directly through the admin UI (rare in v1, but the schema supports it).

**Retrieval-time consultation.** Layer 5 (retrieval engine) consults `verified_qa` first, before hybrid search. On semantic similarity above a tenant-configurable threshold (default 0.93 cosine), the cached answer is returned with its original citations and `last_used_at` / `use_count` updated. On miss, retrieval falls through to the standard hybrid path. The threshold is high by design — false hits on verified Q&A are worse than cache misses.

**Invalidation.** When a source document is re-ingested or deleted, any `verified_qa` row whose `citations` reference the affected document is marked `invalidated_at` and stops being served. This handles the staleness problem that kills naive semantic caches. Invalidated rows are not deleted — they are kept for audit and for re-promotion if the new ingestion produces a still-correct answer.

**Why this matters for the architecture story.** The verified knowledge layer is not a separate product bolted on; it is the natural output of running evals and validation in production. Every test cycle, every red-team probe the agent survives, every production conversation that passed scrutiny — all of it compounds into a per-tenant knowledge asset that makes the agent faster and cheaper over time without any operator intervention. This is the same insight Nexus is built around, served from open Postgres on infrastructure the tenant already owns.

### Layer 5 — Retrieval engine (programmatic)

Per-tenant strategy, deterministic execution:

```
query
  → embed_query
  → verified_qa_lookup (cosine ≥ threshold → return cached answer + citations)
  → on miss:
      query_expansion (optional, LLM)
      → parallel(vector_search, bm25_search)
      → RRF_fusion
      → cross_encoder_rerank
      → return top_k
```

Components:

- Vector: pgvector with HNSW indexes.
- Keyword: Postgres `tsvector` + `ts_rank_cd`, or the `pgbm25` extension if available.
- Fusion: Reciprocal Rank Fusion in SQL (single CTE).
- Reranker: Voyage Rerank (or Cohere Rerank as fallback).

The retrieval *strategy* — k values, rerank thresholds, expansion on/off, metadata filters — is generated once at build time by a strategist agent looking at the tenant's data shape, then stored as JSON config. Runtime is pure code reading that config.

### Layer 6 — Reasoning engine (Claude Agent SDK)

The customer service agent itself, one instance per tenant. Tools exposed:

- `retrieve(query, filters)` — calls Layer 5.
- `lookup_structured(table, filters)` — direct Postgres for things like order status, account info.
- `escalate_to_human(reason, context)` — safety valve.
- `clarify(question)` — when the planner determines more info is needed.

The planner is implicit in the SDK's tool-execution loop. The agent's "soul" (name, identity, role) becomes the system prompt. Role-specific tool subsets differentiate one agent from another.

### Layer 7 — Validation chain (single-shot Claude API)

Three sequential Claude calls wrapping every agent response. All Haiku-tier for cost.

- **Gatekeeper** — "Does this response address the user's actual question?" → `pass | fail | needs_clarification`.
- **Auditor** — "Is every factual claim in this response supported by the retrieved context?" → `grounded | ungrounded | partial` with citation spans.
- **Strategist** — "Is this response coherent, on-brand, and aligned with the agent's role?" → `ship | revise | escalate`.

Outputs are structured (Pydantic-validated), logged to Langfuse, and feed back into the eval system as production telemetry. Persistent Auditor failures on a given retrieval pattern trigger a strategy re-synthesis flag. **Auditor `grounded` responses with confidence above the per-tenant promotion threshold also become verified-knowledge candidates** — queued for owner approval in the weekly digest. This is the production-to-knowledge-layer flywheel: every conversation the agent gets right under scrutiny is a candidate to become canonical.

### Layer 8 — Eval system (programmatic harness, agentic judges)

The harness is code. Celery beat schedules runs; the harness executes test scenarios against the deployed agent and captures full traces.

Judges are single-shot Claude calls with structured outputs, implementing Ragas-style metrics:

- Faithfulness
- Answer relevance
- Context precision
- Context recall

Plus latency and cost from Langfuse trace data.

**Test scenario sources:**

1. Generated at build time by a scenario-generator agent reading the tenant's domain.
2. Mined from production conversations where Gatekeeper or Auditor flagged issues.

This is the flywheel: production traffic improves the eval suite, which improves the next deployment.

**Verified-knowledge promotion side-effect.** The harness has a second, equally important output: every scenario that scores above the promotion thresholds (faithfulness ≥ 0.90, answer relevance ≥ 0.90 by default; both tunable per tenant) is written to the tenant's `verified_qa` table with `source = 'sandbox_test'`. The eval system is therefore not just a quality gate — it is the primary builder of the verified knowledge layer. By the time an agent reaches pre-deployment, its hardest scenario questions are already answered from cache.

### Layer 9 — Red team (Claude Agent SDK)

Three red team agents, each with tools to probe the deployed customer service agent:

- **Prompt injection agent** — jailbreaks, role hijacks, instruction overrides.
- **Data leakage agent** — attempts to extract other tenants' data, PII, system prompts, raw retrieval context.
- **Hallucination-under-pressure agent** — pushes the agent toward confident wrong answers via adversarial framing, leading questions, false premises.

These need the Agent SDK because they iterate — try an attack, observe response, refine, try again. Single-shot will not capture the depth.

Runs:

- Pre-deployment (blocking checklist item).
- Weekly cron per deployed agent. Findings emailed to the business owner with severity classifications.

Built on PyRIT scaffolding where it adds value; custom Claude-driven probes where PyRIT does not cover the domain (Claude-specific jailbreak surfaces, business-context attacks).

### Layer 10 — Pre-deployment checklist (orchestrator agent)

The human-validated gate. A strategist-tier agent (Agent SDK, Sonnet) reads:

- Eval suite results — pass rates per metric, scenario-level pass/fail.
- Red team results — severity-classified findings.
- Cost and latency — p50, p95, p99.
- Coverage analysis — which parts of the ingested corpus are reachable via the current retrieval strategy.
- **Verified knowledge depth** — how many `verified_qa` rows the agent enters deployment with, what fraction of the eval scenarios are now cached, expected post-deployment cache-hit rate based on production-trace mining if available.

It writes a structured deployment report with a recommendation:

- `ship` — all gates green.
- `ship_with_warnings` — non-critical gaps flagged for owner acknowledgment.
- `block` — any critical eval failure or high/critical red team finding.

The owner sees a plain-language summary with expandable details. On approval, the iframe widget goes live.

### Layer 11 — Widget delivery

Static JS bundle on a CDN. The iframe loads, calls `/widget/{agent_id}/config`, receives theming, agent ID, and a short-lived JWT. All chat traffic flows back through FastAPI → Celery `runtime` queue → Agent SDK.

Bundle target: under 20kb gzipped. SMB websites are already bloated; widget weight is a real differentiator.

## 7. Stack

```
Backend:     FastAPI, Pydantic, Celery, Redis, Alembic
Data:        Postgres (control DB on Neon), Neon (per-tenant), pgvector
Agents:      Claude Agent SDK (customer agents, strategists, red teamers)
             Claude API direct (judges, Gatekeeper, Auditor, Strategist,
                                metadata enrichment, entity extraction)
Ingestion:   Docling (layout-aware parsing), Chonkie (structure-aware chunking)
Embeddings:  Voyage (embed + rerank), Cohere Rerank fallback
Evals:       Ragas metrics framework, custom harness on top
Red team:    PyRIT scaffolding, custom Claude probes
Insights:    microsoft/graphrag library (monthly conversation-graph builds)
Observ:      Langfuse (traces, judge outputs, latency, cost)
Admin UI:    Next.js
Widget:      Preact (under 20kb gzipped target)
```

## 7b. Development Environment

**Constraint: local-only, no Docker.**

The development machine has 4 GB RAM. Docker Compose with all services (Postgres, Redis, FastAPI, Celery worker, Celery beat) requires a minimum of 6 GB and was abandoned during M2 after two failed attempts. All builds from M3 onward target local process execution.

**How services are started:**

| Service | Local command |
|---------|---------------|
| Redis | `redis-server` |
| PostgreSQL | local install, e.g. `pg_ctl start` |
| FastAPI | `uvicorn app.main:app --reload --port 8000` (from `apps/api/`) |
| Celery worker | `celery -A app.worker.celery_app worker -Q pipeline,runtime --loglevel=info` (from `apps/api/`) |
| Celery beat | `celery -A app.worker.celery_app beat --loglevel=info` (M6+, from `apps/api/`) |

**Implications for all phase plans:**
- Demo scripts (`scripts/demo_*.sh`) use `BASE_URL=http://localhost:8000` as default — no Docker host aliases.
- Alembic migrations run via `cd apps/api && alembic upgrade head` against a local Postgres URL.
- No `docker-compose.yml` targets, no container health checks, no service hostname overrides.
- Integration tests use `TEST_DATABASE_URL` pointing at a local Postgres database.
- `.env` file lives at `apps/api/.env` and is auto-discovered by pydantic-settings.

## 8. User journey (canonical happy path)

1. **Signup.** Email, business name, tier selection.
2. **Create agent.** Owner names the agent, writes the identity ("soul" — voice, tone, do/do-not list), selects the role from a curated list (customer support, sales qualification, internal helpdesk).
3. **Ingest data.** Drag-and-drop documents, URL list, optional structured exports (CSV of products, orders, etc.). Owner sees a live progress stream.
4. **System provisions.** Neon project created. Owner sees "Building your agent's memory…" with sub-status updates from each Celery task.
5. **Processing.** Parsing → chunking → metadata generation → embedding → migration. Owner does not see these terms; they see "Reading your documents… Organising what we learned… Teaching your agent…"
6. **Strategy synthesis.** Strategist agent generates retrieval config. Reasoning engine assembles tools. Eval suite and red team suite generated.
7. **Pre-deployment checklist.** Sandbox tests run. LLM judges score. Latency and cost measured. Red team attacks executed. Coverage analysed.
8. **Human validation.** Owner sees the deployment report. Plain-language recommendation, expandable details. Owner approves (or fixes flagged issues — usually adding missing data).
9. **Deploy.** iframe snippet shown. Owner pastes into their website.
10. **Live.** Continuous monitoring kicks in. Owner gets a weekly digest: conversation counts, eval drift, red team findings, escalation rate.

## 9. Milestones

Each milestone is shippable and demonstrable on its own. Following GSD discipline.

### M1 — Control plane skeleton

FastAPI app, auth, tenant model, Neon provisioning task, control DB schema, SSE job status pattern.

**End state:** `POST /agents` creates a tenant, dispatches a Celery task, provisions a Neon project, runs migrations against it, and emits SSE status updates the entire way.

**Demo:** Curl creates an agent, browser tab streams the provisioning timeline.

### M2 — Ingestion pipeline

Docling parsing for documents, image, URL ingestion paths. Structure-aware chunking via Chonkie. HyDE-style metadata generation (summary, keywords, hypothetical questions) per chunk via Claude API. **Entity extraction** alongside metadata generation: each chunk gets a list of named entities (products, people, places, policies, named processes) extracted in the same Claude call, written to an `entities` table and joined to chunks via `chunk_entities`. Embedding via Voyage. Migration into tenant Neon DB.

The entity layer is cheap (a few cents per document, runs in the same Claude call as summary/keyword extraction) and unlocks metadata-filtered retrieval at runtime ("return chunks that mention the entity `Returns Policy`"). It is also the substrate the Conversation-Insights Engine (M10) reuses when it builds graphs over conversation logs.

**End state:** Drop in a PDF, watch it become queryable chunks in the tenant DB with summaries, keywords, questions, and entity tags attached. The `entities` table has a clean entity vocabulary for the tenant's domain.

**Demo:** Upload a real business PDF, inspect the resulting `chunks`, `chunk_metadata`, and `entities` tables.

### M3 — Hybrid retrieval

Vector + BM25 + RRF + Voyage rerank, all in code, configurable via retrieval strategy JSON. Strategies are hand-written at this milestone.

**End state:** Query a tenant DB and get back ranked, cited chunks with full retrieval trace (which path matched, fusion scores, rerank deltas).

**Demo:** A query notebook running against the M2 tenant DB, showing candidate sets at each retrieval stage.

### M4 — Reasoning engine + widget v0

Agent SDK integration, tool definitions, system prompt assembled from agent soul, basic Preact iframe widget.

**End state:** A deployed agent answers questions on a test site. End-to-end demo works: ingest → retrieve → agent → widget.

**Demo:** Public test site embedded with a working agent answering questions on real ingested data.

**This is the first hireable artifact.** Everything from M5 onward strengthens it; M1–M4 alone is a portfolio piece.

### M5 — Validation chain

Gatekeeper, Auditor, Strategist wrapping every response. Langfuse integration. Structured outputs captured.

**Verified-knowledge promotion path.** When the Auditor returns `grounded` with confidence above the per-tenant promotion threshold (default 0.90), the response is marked as a verified-knowledge candidate and queued for owner approval in the weekly digest. This is where the production flywheel begins: every conversation the agent gets right under scrutiny becomes a candidate to become canonical. M5 ships the candidate-marking and queueing; the approval UI lands in M8 alongside the rest of the owner-facing dashboard.

**End state:** Every production response has three structured judgments attached, visible in Langfuse and in the admin UI. Promotion candidates are queued in a `verified_qa_candidates` staging table for later owner approval.

**Demo:** Adversarial query in the widget, walk through how each validator scored the response. Show a grounded response landing in the candidate queue.

### M6 — Eval system

Ragas metrics, scenario generation agent, Celery beat schedule, eval dashboard in admin UI, production-trace mining.

**Verified-knowledge seeding.** The eval harness gains a side-effect: every scenario that scores above the promotion thresholds (faithfulness ≥ 0.90 *and* answer relevance ≥ 0.90 by default) is written to the tenant's `verified_qa` table with `source = 'sandbox_test'`. This means M6 is not only the quality gate — it is the primary builder of the verified knowledge layer. By the time an agent reaches the pre-deployment checklist in M8, its hardest scenario questions are already cached as canonical answers, served from `verified_qa` rather than re-derived from the corpus on every customer query.

The retrieval engine (Layer 5) already consults `verified_qa` first; M6 is what makes that cache non-empty at deployment time.

**End state:** Nightly evals run automatically. Failing scenarios are mined from production. Owner sees pass rates over time. Passing scenarios visibly populate the `verified_qa` table; the dashboard shows the cache filling.

**Demo:** Eval dashboard showing a real run with the resulting verified_qa entries highlighted. Run a query in the widget that hits a cached answer; show the trace skipping vector search entirely.

### M7 — Red team

Three red team agents (Agent SDK). Severity classification (low/medium/high/critical). Pre-deployment runs blocking on critical findings.

**End state:** Red team report blocks deployment for high-severity findings. Owner sees a plain-language summary of attempted attacks.

**Demo:** Intentionally weak agent fails pre-deployment with a captured prompt injection trace.

### M8 — Pre-deployment checklist and human validation

Orchestrator agent reads all signals, generates deployment recommendation. Business owner approves through dashboard. Full user journey works start to finish for a non-technical user.

**Verified-knowledge depth as a checklist gate.** The orchestrator agent now reads `verified_qa` row count and eval-scenario coverage as a first-class signal. An agent shipping with zero verified Q&A pairs is technically allowed but flagged in the deployment report — it means the eval suite was thin or the scenarios all failed. The owner sees this as plain-language guidance ("Your agent will be answering every customer question from scratch on day one. We recommend at least N verified answers before going live.") rather than as a hard block.

**Verified-knowledge candidate review.** M8 ships the owner-facing UI for the candidate queue M5 began populating. The weekly digest (and a dashboard tab) surfaces grounded production responses awaiting approval. Owner sees the question, the agent's answer, the cited sources, and the Auditor's confidence — clicks Approve (lands in `verified_qa` with `source = 'production_promotion'`), Reject (discarded with a recorded reason), or Edit (owner rewrites the canonical answer, lands with `source = 'human_authored'`). This is where the verified knowledge layer becomes a thing the owner consciously curates rather than something the system silently accumulates.

**End state:** A non-technical tester completes the canonical happy path unassisted, including the first weekly digest cycle where they approve a handful of verified-knowledge candidates.

**Demo:** Recorded video of a non-developer going from signup to deployed widget, then returning a week later to review and approve their first batch of verified answers.

### M9 — Retrieval strategy synthesis

Strategist agent generates per-tenant retrieval config from data shape analysis (corpus size distribution, document type mix, structured vs unstructured ratio, domain detection).

**End state:** No more hand-written strategies. Onboarding produces a tailored retrieval config per tenant.

**Demo:** Two tenants with very different data shapes get visibly different strategies and improved retrieval metrics versus a default config.

### M10 — Maintenance crons and observability polish

Weekly red team, monthly eval drift detection, Langfuse dashboards, owner-facing weekly digest email, alerting on metric regressions.

**Conversation-Insights Engine.** A monthly Celery beat job per tenant that runs Microsoft's open-source `graphrag` library over the tenant's `messages` table (not the source corpus). Output is a community-detected, summarised knowledge graph stored in `conversation_insights`, surfaced in the admin dashboard as a tab the owner reads like a monthly support-team meeting brief:

- Recurring topics and how they cluster.
- The most common questions the agent could not answer well (low Auditor confidence, frequent escalations).
- Entity-level analysis: which products, policies, or processes drive the most confusion.
- Sentiment trends and escalation hotspots.

This is the analytical surface where GraphRAG-style community summarisation genuinely earns its cost. It does not run at query time. It is not part of the customer-facing retrieval path. It is the owner's monthly business-intelligence digest, generated from the agent's own conversation logs. Entities extracted in M2 are reused here as graph seeds, which makes the build cheaper and more accurate than starting from scratch.

**End state:** Deployed agents are continuously monitored without operator intervention. Owners receive weekly operational digests and monthly conversation-insights briefs.

**Demo:** Live dashboard plus an example weekly digest email plus a generated conversation-insights brief showing real clusters from simulated production traffic.

## 10. Success metrics

**Platform metrics**

- Time from signup to deployed widget for a non-technical tester: target under 30 minutes.
- Pre-deployment checklist pass rate for first-time builds on clean data: target above 70%.
- Median onboarding cost per tenant (Claude API + Voyage + infra): target under $5 for a 200-page corpus.

**Agent quality metrics (per deployed agent)**

- Faithfulness (Ragas): target above 0.85.
- Answer relevance: target above 0.85.
- Auditor `grounded` rate on production traffic: target above 0.90.
- Critical red team findings on weekly cron: target zero.
- p95 response latency: target under 4 seconds end-to-end.

**Verified knowledge layer metrics**

- `verified_qa` row count at deployment time: target above 50 per agent (driven by M6 eval coverage).
- Cache-hit rate on production traffic (queries served from `verified_qa` vs hybrid retrieval): target above 30% by 4 weeks post-deployment.
- Cost per resolved query at month 3 vs week 1: target reduction above 40% (compounding effect of the verified knowledge layer).
- Owner approval rate on verified-knowledge candidates: tracked but not targeted; informs threshold tuning.

**Portfolio metrics**

- GitHub stars: tracked but not optimised for.
- Public M4 demo: live and operable by anyone with the URL.
- Architecture deep-dive blog post: published alongside M4.

## 11. Risks and mitigations

**Scope creep across M5–M10.**
Mitigation: M1–M4 ship publicly the day M4 lands. Subsequent milestones are additive, not blocking. The repo's README leads with the current milestone and the roadmap.

**Per-tenant Neon cost at scale.**
Mitigation: tenant DBs scale to zero on Neon by default. Branching used aggressively for evals to avoid duplicating storage. Cost monitoring built into M10.

**Claude API cost on the validation chain.**
Mitigation: Haiku-tier for all three validators. Validators run async after response is streamed to user; user does not wait on validation. Sampling rate is configurable per tenant (100% for new agents, can step down after confidence builds).

**Red team false positives blocking deployment.**
Mitigation: severity classification is conservative by design; only `critical` blocks. `high` warns. Owner can override with acknowledgment for any non-critical finding.

**Pre-deployment checklist becoming a rubber stamp.**
Mitigation: the orchestrator agent's report is structured and forces the owner to acknowledge each warning individually. Acknowledgments are logged. If an agent later fails in production on something acknowledged at deploy time, the digest surfaces that lineage.

**Verified knowledge layer serving stale answers.**
Mitigation: invalidation is tied to source-document lifecycle — re-ingesting or deleting a cited document marks all `verified_qa` rows referencing it as invalidated. Additionally, the cosine threshold for cache hits is deliberately high (0.93 default) so that paraphrases of genuinely different questions do not collide. The Auditor still runs on cache-served responses at a sampling rate, so a stale answer that escapes invalidation will eventually be flagged.

**Verified knowledge layer entrenching early mistakes.**
Mitigation: a single low-faithfulness production event tied to a `verified_qa` row marks that row for re-review in the next weekly digest. Owners can demote or edit any verified answer at any time. `use_count` makes the rows that matter most easy to identify and audit.

**Conversation-Insights Engine cost.**
Mitigation: monthly cadence, not weekly. Runs against `messages` not the source corpus (smaller, more focused). Entities extracted at M2 ingestion are reused as graph seeds, eliminating the most expensive part of GraphRAG indexing. Tenants with low conversation volume can be skipped automatically below a threshold.

## 12. Open questions

- Pricing model. Per-agent flat fee, per-conversation, or per-tenant-month with conversation tiers. Defer until M8 demo data exists.
- Whether to expose the Neon branch for eval as a feature ("test changes safely before they go live") or keep it as an internal implementation detail.
- Whether structured data (CSV product catalogues, order exports) gets its own ingestion path or rides the document pipeline. Leaning toward separate path with a `lookup_structured` tool, but defer the decision to M2.
- Owner-side data refresh. Manual re-upload, scheduled re-crawl of source URLs, or webhook-driven. Likely all three eventually; M10 candidate.
- Optimal cache-hit similarity threshold for `verified_qa`. Default 0.93 is a starting guess; needs empirical tuning per tenant once M6 produces real data. Likely becomes an auto-tuned value in M9 alongside retrieval-strategy synthesis.
- Storage shape for `conversation_insights`. Microsoft's graphrag library writes to parquet/JSON by default; whether to keep that or normalise into Postgres tables for queryability from the admin UI is a M10 build-time decision.

## 13. What this PRD is not

It is not a specification. It is a contract between the author and the work. Each milestone gets its own PRD with task-level detail before implementation begins. This document exists to keep the project from drifting and to anchor the public repo from day one.