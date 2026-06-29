# Requirements: Veridian

**Defined:** 2026-05-12
**Core Value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.

## v1 Requirements

Requirements for full platform delivery (M1–M10). M1–M4 is the first hireable artifact.

---

### Control Plane (M1)

- [x] **CTL-01**: `POST /agents` returns `202 Accepted` with `job_id`, creates tenant/agent/job rows in control DB
- [x] **CTL-02**: Celery chain (`provision_neon` → `apply_migrations`) runs idempotently with `acks_late=True`
- [x] **CTL-03**: `provision_neon` task calls Neon API, polls until ready, encrypts connection string with Fernet, stores on agent row
- [x] **CTL-04**: `apply_migrations` task runs per-tenant Alembic migration against new Neon project (v1 schema: documents, chunks, embeddings, chunk_metadata, conversations, messages, tool_calls, eval_runs, eval_results, red_team_runs)
- [x] **CTL-05**: `GET /jobs/{id}/events` returns live SSE stream that replays prior events on connect, then forwards new events from Redis pub/sub in real time
- [x] **CTL-06**: SSE stream emits all six required events in order: `job.started` → `neon.project.creating` → `neon.project.ready` → `migrations.running` → `migrations.complete` → `job.complete`
- [x] **CTL-07**: Worker kill-9 between any two tasks results in retry and successful completion — tenant DB is never left in a half-migrated state
- [x] **CTL-08**: Connection strings are never passed as Celery task arguments; tasks fetch and decrypt from control DB at execution time
- [x] **CTL-09**: API key auth (`X-API-Key`), keys stored hashed (argon2), never logged
- [x] **CTL-10**: `GET /health` returns `200` with redis and DB status
- [x] **CTL-11**: `docker-compose up` starts all six services (postgres, redis, api, worker_pipeline, worker_runtime, beat)
- [x] **CTL-12**: `scripts/demo_m1.sh` runs clean from scratch, prints tenant ID, agent ID, and Neon project ID on completion
- [x] **CTL-13**: Unit test coverage on orchestration logic above 80%; integration test exercises full chain
- [x] **CTL-14**: Nightly CI E2E test against real Neon test account (creates project, asserts schema, deletes at teardown)
- [x] **CTL-15**: README includes architecture diagram and recorded demo (asciinema or video)

---

### Ingestion Pipeline (M2)

- [x] **ING-01**: User can upload PDF, image, and URL list for ingestion
- [x] **ING-02**: Documents parsed with layout awareness (Docling) preserving headings, tables, lists
- [x] **ING-03**: Tables ingested via a dedicated table-aware path that preserves row/column relationships (not flattened to prose)
- [x] **ING-04**: Chunks generated with structure-awareness (Chonkie) — boundaries respect document structure, not arbitrary token counts
- [x] **ING-05**: Chunks have deterministic UUIDs (document_id + ordinal hash) to ensure upsert idempotency on retry
- [x] **ING-06**: Each chunk enriched with: summary, keywords list, and hypothetical questions list (via Claude API, Haiku)
- [x] **ING-07**: Chunks embedded via Voyage (`voyage-3` or current equivalent) and stored in tenant DB with HNSW index (`vector_cosine_ops`)
- [x] **ING-08**: Ingestion progress streamed to owner via SSE (parsing → chunking → metadata → embedding events)
- [x] **ING-09**: Full ingestion chain (`parse_documents` → `chunk_documents` → `generate_metadata` → `embed_and_migrate`) is idempotent end-to-end
- [x] **ING-10**: Demo: upload a real business PDF, inspect resulting `chunks` and `chunk_metadata` tables with summaries, keywords, and questions attached

---

### Hybrid Retrieval (M3)

- [x] **RET-01**: Query executes pgvector HNSW search against tenant embeddings
- [x] **RET-02**: Query executes BM25 keyword search via native Postgres `tsvector` + `ts_rank_cd` (no pg_search/pgbm25 — deprecated on Neon)
- [x] **RET-03**: Vector and keyword results fused via Reciprocal Rank Fusion in a single SQL CTE
- [x] **RET-04**: Fused results reranked via Voyage Rerank (Cohere Rerank as fallback)
- [x] **RET-05**: Retrieval strategy stored as per-tenant JSON config (k values, rerank threshold, expansion on/off, metadata filters)
- [x] **RET-06**: Full retrieval trace visible in response: which path matched, fusion scores, rerank deltas
- [x] **RET-07**: Strategies are hand-written per tenant at M3 (automated via M9)
- [x] **RET-08**: Demo: query notebook against M2 tenant DB showing candidate sets at each retrieval stage

---

### Reasoning Engine + Widget (M4)

- [ ] **AGT-01**: Customer service agent built on Claude Agent SDK with four tools: `retrieve(query, filters)`, `lookup_structured(table, filters)`, `escalate_to_human(reason, context)`, `clarify(question)`
- [ ] **AGT-02**: Agent system prompt assembled at call time from agent soul (voice, do, do-not) and role
- [ ] **AGT-03**: Session continuity: `session_id` captured from first turn, stored in conversations table, passed on subsequent turns via `resume=session_id`
- [ ] **AGT-04**: Widget responses include source citation footer ("Based on: [document name, section]")
- [ ] **AGT-05**: Escalation UX defined: when `escalate_to_human` fires, owner receives notification (email or dashboard alert) with reason and conversation context
- [ ] **AGT-06**: Preact iframe widget loads, calls `/widget/{agent_id}/config`, receives theming + agent ID + short-lived JWT, all chat traffic proxied through FastAPI → Celery runtime queue
- [ ] **AGT-07**: Widget bundle under 20kb gzipped
- [ ] **AGT-08**: `GET /widget/{agent_id}/config` serves theming, agent ID, and short-lived JWT to the iframe
- [ ] **AGT-09**: Widget CORS and CSP headers configured for cross-origin embedding
- [ ] **AGT-10**: End-to-end demo works on public test site: ingest real document → retrieve → agent → widget answers questions
- [ ] **AGT-11**: Agent soul editor in admin UI uses structured fields (voice, do list, do-not list) — not a blank textarea

---

### Validation Chain (M5)

- [ ] **VAL-01**: Gatekeeper judges every agent response: "Does this address the user's actual question?" → `pass | fail | needs_clarification`
- [ ] **VAL-02**: Auditor checks every factual claim is supported by retrieved context → `grounded | ungrounded | partial` with citation spans
- [ ] **VAL-03**: Strategist checks response is coherent, on-brand, and aligned with agent role → `ship | revise | escalate`
- [ ] **VAL-04**: All three validators use Claude API (Haiku), run async after response is streamed to user
- [ ] **VAL-05**: All validator outputs structured (Pydantic-validated) and logged to Langfuse v4
- [ ] **VAL-06**: Persistent Auditor `ungrounded` failures on a given retrieval pattern set a `strategy_resynthesis_flagged` field on the agent row
- [ ] **VAL-07**: Demo: adversarial query in widget, walk through how each validator scored the response in Langfuse

---

### Eval System (M6)

- [ ] **EVL-01**: Eval harness measures four Ragas 0.4.x metrics: Faithfulness, Answer Relevance, Context Precision, Context Recall
- [ ] **EVL-02**: Scenario generator agent creates eval suite from tenant domain at build time (Claude API)
- [ ] **EVL-03**: Production conversations where Gatekeeper or Auditor flagged issues are automatically mined into new eval scenarios
- [ ] **EVL-04**: Celery beat schedules nightly eval runs per deployed agent
- [ ] **EVL-05**: Eval runs execute against a Neon branch of the tenant DB (never against the production branch)
- [ ] **EVL-06**: Owner sees eval pass rates over time per metric in admin UI dashboard
- [ ] **EVL-07**: Eval dashboard shows individual scenario pass/fail, not just aggregate scores
- [ ] **EVL-08**: Demo: eval dashboard showing a real run including scenarios mined from synthetic production traffic

---

### Red Team (M7)

- [ ] **RED-01**: Prompt injection agent (Claude Agent SDK) probes for jailbreaks, role hijacks, instruction overrides
- [ ] **RED-02**: Data leakage agent attempts to extract cross-tenant data, PII, system prompts, raw retrieval context
- [ ] **RED-03**: Hallucination-under-pressure agent uses adversarial framing, leading questions, false premises
- [ ] **RED-04**: Chunk text is sanitized in the ingestion pipeline before M7 runs — corpus injection is prevented at M2, verified by M7
- [ ] **RED-05**: Red team findings classified by severity: low / medium / high / critical
- [ ] **RED-06**: Pre-deployment red team run is a blocking checklist item — `critical` findings block deployment, `high` warns
- [ ] **RED-07**: Weekly cron red team run per deployed agent; findings emailed to business owner
- [ ] **RED-08**: Demo: intentionally weak agent fails pre-deployment with captured prompt injection trace

---

### Pre-deployment Checklist (M8)

- [x] **DEP-01**: Orchestrator agent (Claude Agent SDK, Sonnet) reads eval results, red team findings, latency (p50/p95/p99), cost, and corpus coverage analysis
- [x] **DEP-02**: Orchestrator writes structured deployment recommendation: `ship | ship_with_warnings | block`
- [x] **DEP-03**: `block` triggered by: any critical eval failure or high/critical red team finding
- [ ] **DEP-04**: Owner sees plain-language deployment report in admin UI with expandable technical detail
- [ ] **DEP-05**: Owner must acknowledge each warning individually before `ship_with_warnings` proceeds; acknowledgments logged
- [ ] **DEP-06**: On approval, iframe widget snippet is shown and the agent goes live
- [ ] **DEP-07**: A non-technical tester completes the full journey unassisted: signup → ingest → deploy → widget live
- [ ] **DEP-08**: Demo: recorded video of a non-developer completing the canonical happy path

---

### Retrieval Strategy Synthesis (M9)

- [ ] **STR-01**: Strategist agent generates per-tenant retrieval config from data shape analysis (corpus size distribution, document type mix, structured vs unstructured ratio, domain detection)
- [ ] **STR-02**: Synthesized config replaces hand-written strategies from M3 — no manual strategy authoring required for new agents after M9
- [ ] **STR-03**: Demo: two tenants with different data shapes receive visibly different strategies and improved retrieval metrics vs default config

---

### Operations + Observability (M10)

- [ ] **OPS-01**: Weekly red team cron runs automatically per deployed agent without operator intervention
- [ ] **OPS-02**: Monthly eval drift detection flags agents whose metrics have regressed since last month
- [ ] **OPS-03**: Owner receives weekly digest email: conversation counts, eval drift, red team findings, escalation rate
- [ ] **OPS-04**: Langfuse v4 dashboards: latency (p50/p95/p99), cost per conversation, judge outputs, grounding rates
- [ ] **OPS-05**: Alerting on metric regressions (eval pass rate drops, latency spikes, critical red team finding)
- [ ] **OPS-06**: Demo: live dashboard plus example digest email

---

## v2 Requirements

Deferred to post-v1. Acknowledged but not in current roadmap.

### Data Management

- **DAT-01**: Scheduled re-crawl of source URLs (manual re-upload only in v1)
- **DAT-02**: Webhook-driven data refresh
- **DAT-03**: Corpus coverage heatmap showing which parts of the corpus are reachable
- **DAT-04**: Data freshness signals in owner dashboard

### Auth + Access

- **AUTH-01**: OAuth login (Google, GitHub) — email/password and API key sufficient for v1
- **AUTH-02**: Team members and multiple admin users per tenant
- **AUTH-03**: Role-based access control within a tenant

### Billing

- **BILL-01**: Per-agent flat fee, per-conversation, or per-tenant-month billing — deferred until M8 demo data exists
- **BILL-02**: Onboarding cost guard showing real-time API cost during ingestion

### Advanced Features

- **ADV-01**: Neon branch exposure as a user-facing feature ("test changes safely before they go live")
- **ADV-02**: Structured data ingestion path (CSV/order exports) as a first-class path — currently rides the document pipeline
- **ADV-03**: Conversation export and audit log
- **ADV-04**: Advanced view for technical users (retrieval strategy, eval scenarios, red team config)

---

## Out of Scope

| Feature | Reason |
|---------|--------|
| Voice channel | Text only for v1 — high complexity, different product |
| Multi-language support | English only — translation layer is additive work |
| Custom model hosting | Claude only on agent side — deliberate architectural constraint |
| Mobile-native SDKs | iframe-only delivery for v1 |
| Real-time WebSocket owner-watching | SSE is cheaper and sufficient |
| Confidence score display in widget | Research shows confidence scores reduce user trust (IBM/CHI 2025) |
| Built-in live chat inbox | Competes with Intercom/Zendesk — not the product |
| CRM integrations | Out of scope for v1 portfolio milestone |

---

## Traceability

| Requirement | Phase | Milestone | Status |
|-------------|-------|-----------|--------|
| CTL-01 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-04) |
| CTL-02 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-03) |
| CTL-03 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-03) |
| CTL-04 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-03) |
| CTL-05 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-04) |
| CTL-06 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-03) |
| CTL-07 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-03) |
| CTL-08 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-03) |
| CTL-09 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-04) |
| CTL-10 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-04) |
| CTL-11 | Phase 1 | M1 — Control Plane Skeleton | Pending |
| CTL-12 | Phase 1 | M1 — Control Plane Skeleton | Pending |
| CTL-13 | Phase 1 | M1 — Control Plane Skeleton | Complete (01-06) |
| CTL-14 | Phase 1 | M1 — Control Plane Skeleton | Pending |
| CTL-15 | Phase 1 | M1 — Control Plane Skeleton | Pending |
| ING-01 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-02 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-03 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-04 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-05 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-06 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-07 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-08 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-09 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| ING-10 | Phase 2 | M2 — Ingestion Pipeline | Pending |
| RET-01 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-02 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-03 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-04 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-05 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-06 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-07 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| RET-08 | Phase 3 | M3 — Hybrid Retrieval | Pending |
| AGT-01 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-02 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-03 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-04 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-05 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-06 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-07 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-08 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-09 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-10 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| AGT-11 | Phase 4 | M4 — Reasoning Engine + Widget v0 [HIREABLE] | Pending |
| VAL-01 | Phase 5 | M5 — Validation Chain | Pending |
| VAL-02 | Phase 5 | M5 — Validation Chain | Pending |
| VAL-03 | Phase 5 | M5 — Validation Chain | Pending |
| VAL-04 | Phase 5 | M5 — Validation Chain | Pending |
| VAL-05 | Phase 5 | M5 — Validation Chain | Pending |
| VAL-06 | Phase 5 | M5 — Validation Chain | Pending |
| VAL-07 | Phase 5 | M5 — Validation Chain | Pending |
| EVL-01 | Phase 6 | M6 — Eval System | Pending |
| EVL-02 | Phase 6 | M6 — Eval System | Pending |
| EVL-03 | Phase 6 | M6 — Eval System | Pending |
| EVL-04 | Phase 6 | M6 — Eval System | Pending |
| EVL-05 | Phase 6 | M6 — Eval System | Pending |
| EVL-06 | Phase 6 | M6 — Eval System | Pending |
| EVL-07 | Phase 6 | M6 — Eval System | Pending |
| EVL-08 | Phase 6 | M6 — Eval System | Pending |
| RED-01 | Phase 7 | M7 — Red Team | Pending |
| RED-02 | Phase 7 | M7 — Red Team | Pending |
| RED-03 | Phase 7 | M7 — Red Team | Pending |
| RED-04 | Phase 7 | M7 — Red Team | Pending |
| RED-05 | Phase 7 | M7 — Red Team | Pending |
| RED-06 | Phase 7 | M7 — Red Team | Pending |
| RED-07 | Phase 7 | M7 — Red Team | Pending |
| RED-08 | Phase 7 | M7 — Red Team | Pending |
| DEP-01 | Phase 8 | M8 — Pre-deployment Checklist | Complete |
| DEP-02 | Phase 8 | M8 — Pre-deployment Checklist | Complete |
| DEP-03 | Phase 8 | M8 — Pre-deployment Checklist | Complete |
| DEP-04 | Phase 8 | M8 — Pre-deployment Checklist | Pending |
| DEP-05 | Phase 8 | M8 — Pre-deployment Checklist | Pending |
| DEP-06 | Phase 8 | M8 — Pre-deployment Checklist | Pending |
| DEP-07 | Phase 8 | M8 — Pre-deployment Checklist | Pending |
| DEP-08 | Phase 8 | M8 — Pre-deployment Checklist | Pending |
| STR-01 | Phase 9 | M9 — Retrieval Strategy Synthesis | Pending |
| STR-02 | Phase 9 | M9 — Retrieval Strategy Synthesis | Pending |
| STR-03 | Phase 9 | M9 — Retrieval Strategy Synthesis | Pending |
| OPS-01 | Phase 10 | M10 — Maintenance + Observability | Pending |
| OPS-02 | Phase 10 | M10 — Maintenance + Observability | Pending |
| OPS-03 | Phase 10 | M10 — Maintenance + Observability | Pending |
| OPS-04 | Phase 10 | M10 — Maintenance + Observability | Pending |
| OPS-05 | Phase 10 | M10 — Maintenance + Observability | Pending |
| OPS-06 | Phase 10 | M10 — Maintenance + Observability | Pending |

**Coverage:**

- v1 requirements: 84 total
- Mapped to phases: 84
- Unmapped: 0 ✓

**Note:** Original count stated 82; recount confirmed 84 (CTL: 15, ING: 10, RET: 8, AGT: 11, VAL: 7, EVL: 8, RED: 8, DEP: 8, STR: 3, OPS: 6 = 84).

---

## Milestone v1.1 Requirements — Transactional Capability

**Defined:** 2026-06-29
**Source:** `Post-M10-PRD.md` §4. Moves deployed agents from informational (answering) → transactional (acting), with security layers L1–L3 / L5 / L6 (+ partial L4) as first-class deliverables. Phases 14–19 (continue numbering from v1.0's Phase 13).

> v1.1 builds on the live M1–M11 platform. It does **not** depend on Phase 13's production AWS deploy (paused on a domain purchase) — v1.1 is code-buildable in parallel.

### Transactional Tool Contract (L1)

- [ ] **TXN-01**: Six core transactional tools — `place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record` — defined as typed Python functions with full Pydantic input/output schemas (no string-blob, SQL, URL, or arbitrary-JSON inputs)
- [x] **TXN-02**: Side-effecting tools require a client-provided idempotency key; replaying the same key returns the original result and never re-executes the mutation
- [ ] **TXN-03**: Every tool is tagged `mutating: true|false` at definition time — the authorization signal the Actor pre-execution hook keys on (tagged, never runtime-inferred)
- [ ] **TXN-04**: `confirm_action` tool added for require-human flows; existing `escalate_to_human` retained
- [ ] **TXN-05**: Tool definitions are A2A-skill-compatible in shape (typed inputs/outputs + examples) without exposing any A2A surface — forward-compat for v1.2

### Capability Envelope (L2)

- [x] **CAP-01**: `capability_envelopes` control-DB table — `(agent_id, skill, enabled, rate_limit, constraints JSONB, requires_confirmation, requires_identity_verification, UNIQUE(agent_id, skill))`
- [ ] **CAP-02**: Enforcement middleware rejects a tool call (logged as `capability.denial`) when the skill is disabled, over its rate limit, or violates a constraint (`max_amount_cents`, scope filters)
- [ ] **CAP-03**: Capability-and-limits admin UI in the M8 checklist — per-skill envelope config, tighten-only (never loosen beyond platform defaults), identity-verification requirement, Actor mode per skill
- [ ] **CAP-04**: Envelope configured at deploy time and surfaced in the M8 pre-deployment report; any later envelope change re-triggers the pre-deployment checklist (acknowledged via envelope hash)

### Actor Validator (L3)

- [ ] **ACT-01**: Actor validator — single-shot Claude (Haiku) call before any `mutating:true` tool executes; reads conversation + proposed tool call + envelope; outputs `approve | block | require_human` with rationale
- [ ] **ACT-02**: Integrated as a pre-execution hook in the Claude Agent SDK tool loop; fires only for mutating tools
- [ ] **ACT-03**: Short-circuit skip when the envelope marks `requires_confirmation:false` AND `max_amount_cents` is below a per-tenant skip threshold (cost control on low-value actions)
- [ ] **ACT-04**: `require_human` creates a `pending_confirmations` row and routes through `confirm_action`; the action executes only on approval and expires otherwise
- [ ] **ACT-05**: Validation chain extended to four nodes — Actor runs synchronously pre-mutation; Gatekeeper/Auditor/Strategist continue async post-response
- [ ] **ACT-06**: Actor p95 latency < 1s; total added latency on a mutating call < 1.5s end-to-end

### Integrations + Credential Service (L5 extension)

- [ ] **INT-01**: `integration_credentials` tenant-DB table — Fernet-encrypted BYTEA, key derived from platform master key + tenant ID; never exposed to agent code
- [ ] **INT-02**: Platform credential service resolves a credential to a short-lived in-memory handle at tool-execution time; no agent code path reads the table or constructs SQL
- [ ] **INT-03**: Shopify adapter (place/cancel order, issue refund) behind the tool contract
- [ ] **INT-04**: WooCommerce adapter
- [ ] **INT-05**: Stripe adapter (issue refund, update subscription)
- [ ] **INT-06**: Calendly adapter (book slot)
- [ ] **INT-07**: Single-currency per tenant, configured at deploy time (multi-currency out of scope)

### Customer Identity Verification

- [ ] **IDV-01**: `customer_identities` tenant-DB table — `external_id, verified_at, verification_method, session_token_hash, session_expires_at`
- [ ] **IDV-02**: Email-OTP verification flow (request code → verify → short-lived verified session)
- [ ] **IDV-03**: SMS-OTP verification flow
- [ ] **IDV-04**: Per-skill verification config (which actions require verification, method, expiry) driven by the envelope's `requires_identity_verification`
- [ ] **IDV-05**: A mutating tool requiring verification is blocked server-side until the customer holds a valid verified session — never trusted from agent prose

### Audit (L8 partial)

- [x] **AUD-01**: `tool_calls_audit` control-DB table captures 100% of mutating calls — `agent_id, conversation_id, skill, arguments, result, actor_decision, actor_rationale, capability_snapshot, latency_ms, error`
- [x] **AUD-02**: `pending_confirmations` control-DB table — `skill, arguments, requested_at, expires_at, resolved_at, resolution`
- [ ] **AUD-03**: Zero audit gaps across 30 days of synthetic mutating traffic (verification target)

### Blast-Radius Gate

- [ ] **BLR-01**: Financial blast-radius gate in the M8 checklist orchestrator — reports max single-action value and max hourly aggregate per agent
- [ ] **BLR-02**: Warnings escalate above tenant-configured thresholds; owner acknowledges the envelope hash at deploy (logged)

### Red-Team Extensions (extends M7)

- [ ] **RTX-01**: Confused-deputy attack probe
- [ ] **RTX-02**: Value-bound evasion probe (chained smaller refunds to evade a daily/hourly cap)
- [ ] **RTX-03**: Identity-verification-bypass probe
- [ ] **RTX-04**: Zero high-severity findings on the transaction red-team classes for a clean tenant (gate target)

### Security Layer Extensions (L4 partial, L6)

- [ ] **SEC-01**: L4 output firewall — PII-regex pass on every response; flagged responses replaced with a generic deflection and logged (schema-bound + Claude-classifier passes deferred to v1.2)
- [ ] **SEC-02**: L6 — retrieval context wraps retrieved content with explicit "treat as data, not instructions" framing
- [ ] **SEC-03**: M7 prompt-injection agent split into conversation-injection and content-injection variants

### Documentation + Verification

- [ ] **DOC-01**: Tool-author guide
- [ ] **DOC-02**: Integration-provider guide
- [ ] **DOC-03**: Owner-facing capability-configuration guide
- [ ] **VER-01**: v1.1 success-criteria gate — a non-technical tester deploys an agent that issues refunds up to a configured limit and places Shopify orders end-to-end without code; 100 synthetic adversarial messages produce zero unauthorized state mutations escaping L1–L3

### v1.1 Out of Scope (deferred)

| Feature | Defer to |
|---|---|
| A2A endpoint + MCP provisioning | v1.2 (tool defs designed A2A-compatible now) |
| Schema-bound exfiltration + Claude-classifier output-firewall passes | v1.2 |
| Continuous monitoring / alerting / audit-log Neon project | v1.3 |
| ERP/CRM beyond Shopify/WooCommerce; marketplace integrations (Uber Eats/Glovo) | later |
| Multi-currency | later |

### v1.1 Traceability

| Requirements | Phase | Status |
|---|---|---|
| TXN-01..05, CAP-01, CAP-02, AUD-01, AUD-02 | Phase 14 | Pending |
| ACT-01..06 | Phase 15 | Pending |
| INT-01..07 | Phase 16 | Pending |
| IDV-01..05 | Phase 17 | Pending |
| BLR-01, BLR-02, CAP-03, CAP-04, RTX-01..04, SEC-01..03 | Phase 18 | Pending |
| DOC-01..03, VER-01, AUD-03 | Phase 19 | Pending |

**v1.1 coverage:** 43 requirements across phases 14–19, all mapped. (TXN 5, CAP 4, ACT 6, INT 7, IDV 5, AUD 3, BLR 2, RTX 4, SEC 3, DOC 3, VER 1 = 43.)

---
*Requirements defined: 2026-05-12*
*Last updated: 2026-05-12 after 01-04 — CTL-01, CTL-05, CTL-09, CTL-10 marked complete*
*Last updated: 2026-05-13 after 01-06 — CTL-13 marked complete (80.41% unit test coverage)*
