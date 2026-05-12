# RAG Platform — Production PRD

> **Working name:** TBD (placeholder: `rag-platform`)
> **Author:** Mfanafuthi Mhlanga (Bantuson / Mzansi Agentive Pty Ltd)
> **Status:** Draft v1
> **Last updated:** 2026-05-12

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
  → generate_metadata
  → embed_and_migrate
  → synthesize_retrieval_strategy
  → build_reasoning_engine
  → generate_eval_suite
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

- `documents`, `chunks`, `embeddings` (pgvector), `chunk_metadata`
- `conversations`, `messages`, `tool_calls`
- `eval_runs`, `eval_results`, `red_team_runs`

A global control DB (also Neon, shared) holds tenants, agents, jobs, billing, and cross-tenant eval aggregates.

Provisioning flow:

1. `POST /projects` to Neon API.
2. Poll until project is ready.
3. Run Alembic migrations against the new connection string.
4. Store encrypted connection string in control DB.

**Neon branching for eval isolation.** Nightly evals run against a branch of the tenant's DB so production traffic is never affected. This is a genuine differentiator and a defensible architectural choice.

### Layer 4 — Retrieval engine (programmatic)

Per-tenant strategy, deterministic execution:

```
query
  → query_expansion (optional, LLM)
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

### Layer 5 — Reasoning engine (Claude Agent SDK)

The customer service agent itself, one instance per tenant. Tools exposed:

- `retrieve(query, filters)` — calls Layer 4.
- `lookup_structured(table, filters)` — direct Postgres for things like order status, account info.
- `escalate_to_human(reason, context)` — safety valve.
- `clarify(question)` — when the planner determines more info is needed.

The planner is implicit in the SDK's tool-execution loop. The agent's "soul" (name, identity, role) becomes the system prompt. Role-specific tool subsets differentiate one agent from another.

### Layer 6 — Validation chain (single-shot Claude API)

Three sequential Claude calls wrapping every agent response. All Haiku-tier for cost.

- **Gatekeeper** — "Does this response address the user's actual question?" → `pass | fail | needs_clarification`.
- **Auditor** — "Is every factual claim in this response supported by the retrieved context?" → `grounded | ungrounded | partial` with citation spans.
- **Strategist** — "Is this response coherent, on-brand, and aligned with the agent's role?" → `ship | revise | escalate`.

Outputs are structured (Pydantic-validated), logged to Langfuse, and feed back into the eval system as production telemetry. Persistent Auditor failures on a given retrieval pattern trigger a strategy re-synthesis flag.

### Layer 7 — Eval system (programmatic harness, agentic judges)

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

### Layer 8 — Red team (Claude Agent SDK)

Three red team agents, each with tools to probe the deployed customer service agent:

- **Prompt injection agent** — jailbreaks, role hijacks, instruction overrides.
- **Data leakage agent** — attempts to extract other tenants' data, PII, system prompts, raw retrieval context.
- **Hallucination-under-pressure agent** — pushes the agent toward confident wrong answers via adversarial framing, leading questions, false premises.

These need the Agent SDK because they iterate — try an attack, observe response, refine, try again. Single-shot will not capture the depth.

Runs:

- Pre-deployment (blocking checklist item).
- Weekly cron per deployed agent. Findings emailed to the business owner with severity classifications.

Built on PyRIT scaffolding where it adds value; custom Claude-driven probes where PyRIT does not cover the domain (Claude-specific jailbreak surfaces, business-context attacks).

### Layer 9 — Pre-deployment checklist (orchestrator agent)

The human-validated gate. A strategist-tier agent (Agent SDK, Sonnet) reads:

- Eval suite results — pass rates per metric, scenario-level pass/fail.
- Red team results — severity-classified findings.
- Cost and latency — p50, p95, p99.
- Coverage analysis — which parts of the ingested corpus are reachable via the current retrieval strategy.

It writes a structured deployment report with a recommendation:

- `ship` — all gates green.
- `ship_with_warnings` — non-critical gaps flagged for owner acknowledgment.
- `block` — any critical eval failure or high/critical red team finding.

The owner sees a plain-language summary with expandable details. On approval, the iframe widget goes live.

### Layer 10 — Widget delivery

Static JS bundle on a CDN. The iframe loads, calls `/widget/{agent_id}/config`, receives theming, agent ID, and a short-lived JWT. All chat traffic flows back through FastAPI → Celery `runtime` queue → Agent SDK.

Bundle target: under 20kb gzipped. SMB websites are already bloated; widget weight is a real differentiator.

## 7. Stack

```
Backend:     FastAPI, Pydantic, Celery, Redis, Alembic
Data:        Postgres (control DB on Neon), Neon (per-tenant), pgvector
Agents:      Claude Agent SDK (customer agents, strategists, red teamers)
             Claude API direct (judges, Gatekeeper, Auditor, Strategist)
Ingestion:   Docling (layout-aware parsing), Chonkie (structure-aware chunking)
Embeddings:  Voyage (embed + rerank), Cohere Rerank fallback
Evals:       Ragas metrics framework, custom harness on top
Red team:    PyRIT scaffolding, custom Claude probes
Observ:      Langfuse (traces, judge outputs, latency, cost)
Admin UI:    Next.js
Widget:      Preact (under 20kb gzipped target)
```

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

Docling parsing for documents, image, URL ingestion paths. Structure-aware chunking via Chonkie. HyDE-style metadata generation (summary, keywords, hypothetical questions) per chunk via Claude API. Embedding via Voyage. Migration into tenant Neon DB.

**End state:** Drop in a PDF, watch it become queryable chunks in the tenant DB with summaries, keywords, and questions attached.

**Demo:** Upload a real business PDF, inspect the resulting `chunks` and `chunk_metadata` tables.

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

**End state:** Every production response has three structured judgments attached, visible in Langfuse and in the admin UI.

**Demo:** Adversarial query in the widget, walk through how each validator scored the response.

### M6 — Eval system

Ragas metrics, scenario generation agent, Celery beat schedule, eval dashboard in admin UI, production-trace mining.

**End state:** Nightly evals run automatically. Failing scenarios are mined from production. Owner sees pass rates over time.

**Demo:** Eval dashboard showing a real run, including scenarios mined from synthetic production traffic.

### M7 — Red team

Three red team agents (Agent SDK). Severity classification (low/medium/high/critical). Pre-deployment runs blocking on critical findings.

**End state:** Red team report blocks deployment for high-severity findings. Owner sees a plain-language summary of attempted attacks.

**Demo:** Intentionally weak agent fails pre-deployment with a captured prompt injection trace.

### M8 — Pre-deployment checklist and human validation

Orchestrator agent reads all signals, generates deployment recommendation. Business owner approves through dashboard. Full user journey works start to finish for a non-technical user.

**End state:** A non-technical tester completes the canonical happy path unassisted.

**Demo:** Recorded video of a non-developer going from signup to deployed widget.

### M9 — Retrieval strategy synthesis

Strategist agent generates per-tenant retrieval config from data shape analysis (corpus size distribution, document type mix, structured vs unstructured ratio, domain detection).

**End state:** No more hand-written strategies. Onboarding produces a tailored retrieval config per tenant.

**Demo:** Two tenants with very different data shapes get visibly different strategies and improved retrieval metrics versus a default config.

### M10 — Maintenance crons and observability polish

Weekly red team, monthly eval drift detection, Langfuse dashboards, owner-facing weekly digest email, alerting on metric regressions.

**End state:** Deployed agents are continuously monitored without operator intervention. Owners receive digests and alerts.

**Demo:** Live dashboard plus an example digest email.

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

## 12. Open questions

- Pricing model. Per-agent flat fee, per-conversation, or per-tenant-month with conversation tiers. Defer until M8 demo data exists.
- Whether to expose the Neon branch for eval as a feature ("test changes safely before they go live") or keep it as an internal implementation detail.
- Whether structured data (CSV product catalogues, order exports) gets its own ingestion path or rides the document pipeline. Leaning toward separate path with a `lookup_structured` tool, but defer the decision to M2.
- Owner-side data refresh. Manual re-upload, scheduled re-crawl of source URLs, or webhook-driven. Likely all three eventually; M10 candidate.

## 13. What this PRD is not

It is not a specification. It is a contract between the author and the work. Each milestone gets its own PRD with task-level detail before implementation begins. This document exists to keep the project from drifting and to anchor the public repo from day one.