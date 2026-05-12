# Veridian — Roadmap

**Project:** Veridian RAG Platform
**Mode:** Vertical MVP
**Granularity:** Standard
**Coverage:** 82/82 v1 requirements mapped
**Last updated:** 2026-05-12

---

## Phases

- [ ] **Phase 1: M1 — Control Plane Skeleton** - FastAPI, auth, Neon provisioning, SSE streaming
- [ ] **Phase 2: M2 — Ingestion Pipeline** - Structure-aware document parsing, chunking, metadata, embedding
- [ ] **Phase 3: M3 — Hybrid Retrieval** - pgvector + BM25 + RRF fusion + Voyage reranking
- [ ] **Phase 4: M4 — Reasoning Engine + Widget v0** - Claude agent, Preact iframe widget, public demo [FIRST HIREABLE ARTIFACT]
- [ ] **Phase 5: M5 — Validation Chain** - Gatekeeper + Auditor + Strategist wrapping every response
- [ ] **Phase 6: M6 — Eval System** - Ragas metrics, scenario generation, Celery beat, eval dashboard
- [ ] **Phase 7: M7 — Red Team** - Three adversarial agents, severity classification, pre-deployment gate
- [ ] **Phase 8: M8 — Pre-deployment Checklist + Human Validation** - Orchestrator agent, owner approval gate, full journey validated
- [ ] **Phase 9: M9 — Retrieval Strategy Synthesis** - Strategist agent generates per-tenant retrieval configs
- [ ] **Phase 10: M10 — Maintenance Crons + Observability Polish** - Automated operations, digest email, alerting

---

## Phase Details

### Phase 1: M1 — Control Plane Skeleton

**Goal:** Establish the infrastructure foundation — FastAPI control plane, auth, per-tenant Neon project provisioning via Celery chains, and live SSE job status streaming — so every subsequent milestone has a proven place to dispatch work, store data, and show the user what's happening.
**Mode:** mvp
**Depends on:** Nothing (first phase)
**Requirements:** CTL-01, CTL-02, CTL-03, CTL-04, CTL-05, CTL-06, CTL-07, CTL-08, CTL-09, CTL-10, CTL-11, CTL-12, CTL-13, CTL-14, CTL-15

**Success Criteria** (what must be TRUE):
  1. A developer runs `curl POST /agents` and receives `202 Accepted` with a `job_id`; within 60 seconds a real Neon project exists, migrations have been applied, and the agent row shows `status: ready`.
  2. A browser tab pointed at `GET /jobs/{job_id}/events` displays all six SSE events in order (`job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`) without any manual polling or page refresh.
  3. Killing the Celery worker mid-chain and restarting it results in the chain completing successfully — the tenant DB is never left half-migrated.
  4. `scripts/demo_m1.sh` runs from `docker-compose up` on a clean machine, prints tenant ID, agent ID, and Neon project ID, and exits zero.
  5. Unit test coverage on orchestration logic is above 80%; nightly CI E2E test creates a real Neon project, asserts the schema, and deletes it at teardown.

**Demo:** A curl command creates an agent and a browser tab streams the entire provisioning timeline — Neon project appearing in real time — with no code beyond the demo script.
**Plans:** TBD
**UI hint:** no

---

### Phase 2: M2 — Ingestion Pipeline

**Goal:** Transform raw business documents (PDF, image, URL) into queryable, semantically enriched chunks stored in the tenant Neon DB — with structure-aware parsing and metadata that cannot be patched later without full re-embedding.
**Mode:** mvp
**Depends on:** Phase 1
**Requirements:** ING-01, ING-02, ING-03, ING-04, ING-05, ING-06, ING-07, ING-08, ING-09, ING-10

**Success Criteria** (what must be TRUE):
  1. A user uploads a real business PDF through the API; the `chunks` and `chunk_metadata` tables in the tenant DB populate with summaries, keywords, and hypothetical questions attached to every chunk.
  2. Tables in the source document appear as discrete Markdown-table rows in the chunks table — not as fragmented prose — so tabular queries return coherent results.
  3. The ingestion progress is visible as a live SSE stream emitting named events for each stage (`parsing`, `chunking`, `metadata`, `embedding`) without manual polling.
  4. Uploading the same document a second time (retry or duplicate) produces no duplicate chunks — deterministic chunk UUIDs and upserts make the full chain idempotent end-to-end.

**Demo:** Upload a real business PDF via the API, then inspect the resulting `chunks` and `chunk_metadata` tables in the tenant DB showing summaries, keywords, and questions on every row.
**Plans:** TBD
**UI hint:** no

---

### Phase 3: M3 — Hybrid Retrieval

**Goal:** Deliver a retrieval engine that combines pgvector HNSW semantic search, native BM25 keyword search, RRF fusion, and Voyage reranking — all configurable via per-tenant JSON strategy — so retrieval quality is the ceiling on agent quality before the agent is built.
**Mode:** mvp
**Depends on:** Phase 2
**Requirements:** RET-01, RET-02, RET-03, RET-04, RET-05, RET-06, RET-07, RET-08

**Success Criteria** (what must be TRUE):
  1. A query against the M2 tenant DB returns ranked, cited chunks with the full retrieval trace visible in the response: which search path matched, fusion scores, and rerank delta for each candidate.
  2. Vector-only queries, keyword-only queries, and hybrid queries each return meaningfully different candidate sets — confirming that both paths contribute to fusion, not just one.
  3. The retrieval strategy (k values, rerank threshold, expansion flag, metadata filters) can be changed by editing a JSON config file with no code changes required.
  4. A Jupyter notebook demonstrates the candidate sets at each retrieval stage (raw vector results, raw BM25 results, post-RRF fused list, post-rerank final list) on a real query.

**Demo:** A query notebook running against the M2 tenant DB shows the candidate sets at each retrieval stage — vector, BM25, fused, reranked — for a real business question.
**Plans:** TBD
**UI hint:** no

---

### Phase 4: M4 — Reasoning Engine + Widget v0

> **FIRST HIREABLE ARTIFACT.** M1–M4 is a complete portfolio piece. The public demo and architecture blog post ship with this phase. Everything from M5 onward strengthens what already exists here.

**Goal:** Wire the retrieval engine to a Claude Agent SDK agent with four custom tools, deliver a Preact iframe widget under 20kb gzipped, and publish a live public demo site — making Veridian a demonstrable, deployed, end-to-end RAG product that answers real questions on real data.
**Mode:** mvp
**Depends on:** Phase 3
**Requirements:** AGT-01, AGT-02, AGT-03, AGT-04, AGT-05, AGT-06, AGT-07, AGT-08, AGT-09, AGT-10, AGT-11

**Success Criteria** (what must be TRUE):
  1. A visitor to the public test site can type a question into the embedded widget and receive a grounded answer with a source citation footer ("Based on: [document name, section]") — entirely from ingested real data, with no hardcoded responses.
  2. A conversation persists across multiple turns — the agent remembers prior context within the session — and a new browser session starts a fresh conversation.
  3. When the agent fires `escalate_to_human`, the widget displays a conversation summary and capture form, and the owner receives a notification (email or dashboard alert) with the reason and context.
  4. The widget bundle is under 20kb gzipped, loads via iframe embed on a plain HTML page, and passes cross-origin requests correctly with CORS and CSP headers configured.
  5. An owner uses the admin UI's structured soul editor (voice, do list, do-not list) to configure the agent's identity — not a blank textarea.

**Demo:** A public test site with a working embedded widget answering questions on real ingested data — accessible to anyone with the URL.
**Plans:** TBD
**UI hint:** yes

---

### Phase 5: M5 — Validation Chain

**Goal:** Wrap every agent response with three async Claude judges — Gatekeeper (scope), Auditor (grounding), Strategist (brand alignment) — logging structured verdicts to Langfuse so every production response has an observable quality signal without blocking the user.
**Mode:** mvp
**Depends on:** Phase 4
**Requirements:** VAL-01, VAL-02, VAL-03, VAL-04, VAL-05, VAL-06, VAL-07

**Success Criteria** (what must be TRUE):
  1. Every response served by the widget has three structured validation verdicts visible in Langfuse within seconds of the response being streamed — the user never waits for validators to run.
  2. An adversarial query that receives a partially grounded answer shows an Auditor verdict of `partial` or `ungrounded` with specific citation spans in Langfuse — not a generic pass.
  3. When the Auditor flags the same retrieval pattern as `ungrounded` repeatedly, the `strategy_resynthesis_flagged` field is set on the agent row — confirming the feedback loop into retrieval is active.
  4. A developer can walk through a Langfuse trace showing all three judge outputs, their structured Pydantic payloads, and the Haiku cost per call for a single conversation turn.

**Demo:** An adversarial query sent through the widget, followed by a Langfuse trace walkthrough showing how each of the three validators scored the response.
**Plans:** TBD
**UI hint:** no

---

### Phase 6: M6 — Eval System

**Goal:** Automate nightly Ragas-based evaluation runs against Neon DB branches, mine production conversations for failing scenarios, and surface pass rates over time in the admin UI — creating the feedback flywheel where production traffic continuously improves the eval suite.
**Mode:** mvp
**Depends on:** Phase 5
**Requirements:** EVL-01, EVL-02, EVL-03, EVL-04, EVL-05, EVL-06, EVL-07, EVL-08

**Success Criteria** (what must be TRUE):
  1. Nightly Celery beat triggers an eval run automatically; the run executes against a Neon branch (never the production DB branch) and the branch is deleted after the run completes.
  2. The admin UI eval dashboard shows per-metric pass rates (Faithfulness, Answer Relevance, Context Precision, Context Recall) over time as a chart — not just a single aggregate number.
  3. The eval dashboard shows individual scenario pass/fail results — an owner can see exactly which question failed, not just that "eval declined 5%."
  4. At least one scenario in the eval suite was automatically mined from a production conversation where the Gatekeeper or Auditor flagged an issue — confirming the mining pipeline is live.

**Demo:** The eval dashboard showing a real run with per-metric pass rates, per-scenario pass/fail detail, and at least one scenario mined from synthetic production traffic.
**Plans:** TBD
**UI hint:** yes

---

### Phase 7: M7 — Red Team

**Goal:** Deploy three Claude Agent SDK adversarial agents (prompt injection, data leakage, hallucination-under-pressure) that probe the deployed customer service agent, classify findings by severity, and block deployment on critical findings.
**Mode:** mvp
**Depends on:** Phase 4
**Requirements:** RED-01, RED-02, RED-03, RED-04, RED-05, RED-06, RED-07, RED-08

**Success Criteria** (what must be TRUE):
  1. An intentionally weak agent (with an exploitable system prompt) fails the pre-deployment red team run — the run returns a `critical` severity finding and deployment is blocked without any manual override.
  2. The captured prompt injection trace shows the full attack sequence — multiple adversarial turns, the probe, and the agent's response — not just a pass/fail verdict.
  3. The data leakage agent fails to extract cross-tenant data, PII, or raw retrieval context from a correctly configured agent — confirmed by the red team run report.
  4. The corpus injection canary test (a known injection string planted in ingested content) is caught at ingestion sanitization (M2 prevention, M7 verification) — the agent does not execute it.

**Demo:** An intentionally weak agent fails pre-deployment with a captured prompt injection trace showing severity classification and the specific attack sequence.
**Plans:** TBD
**UI hint:** no

---

### Phase 8: M8 — Pre-deployment Checklist + Human Validation

**Goal:** Complete the non-technical owner journey end-to-end — an orchestrator agent reads all eval and red team signals and writes a plain-language deployment report, the owner acknowledges each warning individually, and a recorded video proves a non-developer can go from signup to deployed widget unassisted.
**Mode:** mvp
**Depends on:** Phase 6, Phase 7
**Requirements:** DEP-01, DEP-02, DEP-03, DEP-04, DEP-05, DEP-06, DEP-07, DEP-08

**Success Criteria** (what must be TRUE):
  1. A non-technical tester completes the full owner journey — signup → ingest → pre-deployment checklist → agent live — without assistance, and the widget is embedded and answering questions at the end.
  2. The deployment report in the admin UI is in plain language with expandable technical detail; a critical finding results in a `block` recommendation and the deploy button is disabled.
  3. When the report is `ship_with_warnings`, the owner must acknowledge each warning individually — a single "accept all" is not available — and each acknowledgment is logged with a reasoning field.
  4. On final approval, the iframe widget snippet appears in the UI and the agent goes live — the owner copies it and pastes it into any web page.

**Demo:** A recorded video of a non-developer going from signup to a deployed, embedded widget — the canonical happy path end-to-end.
**Plans:** TBD
**UI hint:** yes

---

### Phase 9: M9 — Retrieval Strategy Synthesis

**Goal:** Replace the hand-written per-tenant retrieval JSON configs from M3 with a strategist agent that analyzes corpus shape (document types, size distribution, domain vocabulary) and generates an optimized retrieval config automatically — so new agents require zero manual strategy authoring.
**Mode:** mvp
**Depends on:** Phase 3, Phase 2
**Requirements:** STR-01, STR-02, STR-03

**Success Criteria** (what must be TRUE):
  1. A new agent created after M9 ships receives an automatically generated retrieval strategy with no manual JSON editing required — the strategy is present in the agent row after ingestion completes.
  2. Two tenants with visibly different data shapes (e.g., a table-heavy catalogue vs. a prose-heavy FAQ) receive meaningfully different strategy configs — different k values, different rerank thresholds, or different expansion settings.
  3. The auto-generated strategies produce measurably better Ragas retrieval metrics (Context Precision, Context Recall) than the default config on both tenants — confirmed by running an eval run before and after strategy synthesis.

**Demo:** Two tenants with different data shapes each receive a visibly different auto-generated retrieval strategy, with improved eval metrics versus the default config shown side by side.
**Plans:** TBD
**UI hint:** no

---

### Phase 10: M10 — Maintenance Crons + Observability Polish

**Goal:** Make all post-deployment operations autonomous — weekly red team crons, monthly eval drift detection, owner digest emails, Langfuse dashboards with latency and cost attribution, and alerting on metric regressions — so deployed agents are continuously monitored without operator intervention.
**Mode:** mvp
**Depends on:** Phase 8
**Requirements:** OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, OPS-06

**Success Criteria** (what must be TRUE):
  1. A weekly red team cron fires automatically for each deployed agent with no operator action; the owner receives an email with severity-classified findings within the same scheduled window.
  2. The owner receives a weekly digest email containing conversation counts, eval drift summary, red team findings, and escalation rate — all populated from real platform data.
  3. The Langfuse dashboard shows p50/p95/p99 latency, cost per conversation, judge outputs, and grounding rates — all in one view, without manual query construction.
  4. A simulated metric regression (e.g., eval pass rate drop, latency spike) triggers an alert that is visible in the admin UI and sent to the owner — confirming the alerting pipeline is live, not just configured.

**Demo:** A live Langfuse dashboard plus an example weekly digest email showing all operational signals for a deployed agent.
**Plans:** TBD
**UI hint:** yes

---

## Phase Overview Table

| # | Name | Goal (short) | Requirements | Key Demo |
|---|------|--------------|:------------:|----------|
| 1 | M1 — Control Plane Skeleton | FastAPI + Celery + Neon provisioning + SSE | 15 | curl creates agent; browser streams Neon provisioning live |
| 2 | M2 — Ingestion Pipeline | Docling + Chonkie + metadata + Voyage embedding | 10 | Upload PDF; inspect enriched chunks and metadata in tenant DB |
| 3 | M3 — Hybrid Retrieval | pgvector + BM25 + RRF + Voyage rerank | 8 | Notebook shows candidates at each retrieval stage |
| 4 | M4 — Reasoning Engine + Widget v0 | Claude agent + Preact widget + public demo [HIREABLE] | 11 | Public test site with live embedded widget on real data |
| 5 | M5 — Validation Chain | Async Gatekeeper + Auditor + Strategist on every response | 7 | Langfuse trace showing all three judge verdicts for adversarial query |
| 6 | M6 — Eval System | Ragas nightly evals + scenario mining + eval dashboard | 8 | Eval dashboard with real run and mined scenarios |
| 7 | M7 — Red Team | Three adversarial agents + severity classification + deployment gate | 8 | Weak agent blocked at deployment with captured injection trace |
| 8 | M8 — Pre-deployment Checklist | Orchestrator agent + owner approval + full journey validated | 8 | Video of non-developer completing signup-to-deployed-widget |
| 9 | M9 — Retrieval Strategy Synthesis | Strategist auto-generates per-tenant retrieval config | 3 | Two tenants get different strategies with improved metrics |
| 10 | M10 — Maintenance + Observability | Autonomous crons + digest email + Langfuse dashboards + alerting | 6 | Live dashboard plus example digest email |

**Total v1 requirements:** 84
**Mapped:** 84 ✓
**Unmapped:** 0 ✓

---

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. M1 — Control Plane Skeleton | 0/? | Not started | - |
| 2. M2 — Ingestion Pipeline | 0/? | Not started | - |
| 3. M3 — Hybrid Retrieval | 0/? | Not started | - |
| 4. M4 — Reasoning Engine + Widget v0 | 0/? | Not started | - |
| 5. M5 — Validation Chain | 0/? | Not started | - |
| 6. M6 — Eval System | 0/? | Not started | - |
| 7. M7 — Red Team | 0/? | Not started | - |
| 8. M8 — Pre-deployment Checklist | 0/? | Not started | - |
| 9. M9 — Retrieval Strategy Synthesis | 0/? | Not started | - |
| 10. M10 — Maintenance + Observability | 0/? | Not started | - |

---

*Roadmap created: 2026-05-12*
*Next: `/gsd-plan-phase 1` to begin planning Phase 1*
