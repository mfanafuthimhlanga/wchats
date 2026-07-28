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

- [x] **TXN-01**: Six core transactional tools — `place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record` — defined as typed Python functions with full Pydantic input/output schemas (no string-blob, SQL, URL, or arbitrary-JSON inputs)
- [x] **TXN-02**: Side-effecting tools require a client-provided idempotency key; replaying the same key returns the original result and never re-executes the mutation
- [x] **TXN-03**: Every tool is tagged `mutating: true|false` at definition time — the authorization signal the Actor pre-execution hook keys on (tagged, never runtime-inferred)
- [x] **TXN-04**: `confirm_action` tool added for require-human flows; existing `escalate_to_human` retained
- [x] **TXN-05**: Tool definitions are A2A-skill-compatible in shape (typed inputs/outputs + examples) without exposing any A2A surface — forward-compat for v1.2

### Capability Envelope (L2)

- [x] **CAP-01**: `capability_envelopes` control-DB table — `(agent_id, skill, enabled, rate_limit, constraints JSONB, requires_confirmation, requires_identity_verification, UNIQUE(agent_id, skill))`
- [x] **CAP-02**: Enforcement middleware rejects a tool call (logged as `capability.denial`) when the skill is disabled, over its rate limit, or violates a constraint (`max_amount_cents`, scope filters)
- [ ] **CAP-03**: Capability-and-limits admin UI in the M8 checklist — per-skill envelope config, tighten-only (never loosen beyond platform defaults), identity-verification requirement, Actor mode per skill *(backend complete: comparator + platform defaults in 18-04, GET/PATCH routes with server-side tighten-only enforcement in 18-08. **Note corrected 2026-07-28** — plan 18-10 (the admin UI) HAS executed and is committed (`18-10-SUMMARY.md`, `apps/admin/app/agents/[id]/deploy/page.tsx`); the earlier "has not run" text was stale. The requirement is still not met, but for a different reason: the UI's per-skill `enabled` control is permanently locked, because `validate_tighten_only` rejects every `enabled: False → True` transition while every `PLATFORM_CAPABILITY_DEFAULTS` entry ships `enabled: False`. An owner can tighten every field but can never turn a skill on. Closure owned by **Phase 22 / CAP-05**.)*
- [x] **CAP-04**: Envelope configured at deploy time and surfaced in the M8 pre-deployment report; any later envelope change re-triggers the pre-deployment checklist (acknowledged via envelope hash) *(complete: `envelope_drift` shipped caller-free in 18-04; 18-07 wires the checklist-time hash persistence, the approve-time 422, and `envelope_drift` on both checklist reads)*

### Actor Validator (L3)

- [x] **ACT-01**: Actor validator — single-shot Claude (Haiku) call before any `mutating:true` tool executes; reads conversation + proposed tool call + envelope; outputs `approve | block | require_human` with rationale
- [x] **ACT-02**: Integrated as a pre-execution hook in the Claude Agent SDK tool loop; fires only for mutating tools
- [x] **ACT-03**: Short-circuit skip when the envelope marks `requires_confirmation:false` AND `max_amount_cents` is below a per-tenant skip threshold (cost control on low-value actions)
- [ ] **ACT-04**: `require_human` creates a `pending_confirmations` row and routes through `confirm_action`; the action executes only on approval and expires otherwise *(corrected from a stale `[x]` on 2026-07-28. **Only the first half shipped.** The row is created (`tools.py` `require_human` branch) and `confirm_action_tool` creates its own row, but a full route inventory of `apps/api/app/api/v1/*.py` found **zero** routes, Celery tasks, or scripts that read or resolve a `pending_confirmations` row — nothing sets `resolved_at`/`resolution`, so "the action executes only on approval" is unimplemented and there is no expiry sweep. Tracked as threat `T-19-04`; closure owned by **Phase 22 / ACT-07**.)*
- [x] **ACT-05**: Validation chain extended to four nodes — Actor runs synchronously pre-mutation; Gatekeeper/Auditor/Strategist continue async post-response
- [x] **ACT-06**: Actor p95 latency < 1s; total added latency on a mutating call < 1.5s end-to-end

### Integrations + Credential Service (L5 extension)

- [ ] **INT-01**: `integration_credentials` tenant-DB table — Fernet-encrypted BYTEA, key derived from platform master key + tenant ID; never exposed to agent code
- [x] **INT-02**: Platform credential service resolves a credential to a short-lived in-memory handle at tool-execution time; no agent code path reads the table or constructs SQL
- [x] **INT-03**: Shopify adapter (place/cancel order, issue refund) behind the tool contract
- [x] **INT-04**: WooCommerce adapter
- [x] **INT-05**: Stripe adapter (issue refund, update subscription)
- [ ] **INT-06**: Calendly adapter (book slot)
- [x] **INT-07**: Single-currency per tenant, configured at deploy time (multi-currency out of scope)

### Customer Identity Verification

- [x] **IDV-01**: `customer_identities` tenant-DB table — `external_id, verified_at, verification_method, session_token_hash, session_expires_at`
- [x] **IDV-02**: Email-OTP verification flow (request code → verify → short-lived verified session)
- [x] **IDV-03**: SMS-OTP verification flow
- [x] **IDV-04**: Per-skill verification config (which actions require verification, method, expiry) driven by the envelope's `requires_identity_verification`
- [x] **IDV-05**: A mutating tool requiring verification is blocked server-side until the customer holds a valid verified session — never trusted from agent prose

### Audit (L8 partial)

- [x] **AUD-01**: `tool_calls_audit` control-DB table captures 100% of mutating calls — `agent_id, conversation_id, skill, arguments, result, actor_decision, actor_rationale, capability_snapshot, latency_ms, error`
- [x] **AUD-02**: `pending_confirmations` control-DB table — `skill, arguments, requested_at, expires_at, resolved_at, resolution`
- [ ] **AUD-03**: Zero audit gaps across 30 days of synthetic mutating traffic (verification target) *(the gated harness `tests/integration/test_aud03_audit_gap.py` and its 11 DB-free unit companion are authored and unit-proven; the live run was attempted 2026-07-28 and deferred — no PostgreSQL server is installed on the executing machine, so the harness has never run against a live database. `19-UAT.md` item 3.)*

### Blast-Radius Gate

- [x] **BLR-01**: Financial blast-radius gate in the M8 checklist orchestrator — reports max single-action value and max hourly aggregate per agent
- [x] **BLR-02**: Warnings escalate above tenant-configured thresholds; owner acknowledges the envelope hash at deploy (logged) *(complete for the backend gate: threshold columns + derived warnings shipped in 18-01/18-05, `canonical_envelope_hash` in 18-04, and 18-07 wires the checklist-time hash persistence, the approve-time 422, and `envelope_acknowledged_at` stamping; the admin-facing acknowledgement UI is 18-10, unexecuted)*

### Red-Team Extensions (extends M7)

- [x] **RTX-01**: Confused-deputy attack probe
- [x] **RTX-02**: Value-bound evasion probe (chained smaller refunds to evade a daily/hourly cap)
- [x] **RTX-03**: Identity-verification-bypass probe
- [ ] **RTX-04**: Zero high-severity findings on the transaction red-team classes for a clean tenant (gate target)

### Security Layer Extensions (L4 partial, L6)

- [x] **SEC-01**: L4 output firewall — PII-regex pass on every response; flagged responses replaced with a generic deflection and logged (schema-bound + Claude-classifier passes deferred to v1.2)
- [x] **SEC-02**: L6 — retrieval context wraps retrieved content with explicit "treat as data, not instructions" framing
- [x] **SEC-03**: M7 prompt-injection agent split into conversation-injection and content-injection variants

### Documentation + Verification

- [x] **DOC-01**: Tool-author guide
- [x] **DOC-02**: Integration-provider guide
- [x] **DOC-03**: Owner-facing capability-configuration guide
- [ ] **VER-01**: v1.1 success-criteria gate — a non-technical tester deploys an agent that issues refunds up to a configured limit and places Shopify orders end-to-end without code; 100 synthetic adversarial messages produce zero unauthorized state mutations escaping L1–L3 *(SC2 recorded `[failed — blocked]` by the operator 2026-07-28: `validate_tighten_only` makes capability `enabled=True` unreachable through any shipped API, and `T-19-04`'s `require_human` branch has no resolution route — both are capabilities the product does not have, not environment gaps. SC3's 100-message adversarial harness is authored and unit-proven; its live run was deferred the same day — no PostgreSQL server is installed on the executing machine. See `19-UAT.md` items 1-2.)*

### VER-01 Blocker Closure (Phase 22)

Added 2026-07-28. Phase 19's verification established that VER-01 SC2 cannot pass against the
current build for two reasons that are **missing product capabilities, not environment gaps**.
Neither was covered by any existing v1.1 requirement ID, so they are given their own here rather
than left as prose in a UAT file. Both are prerequisites for VER-01 and for CAP-03 / ACT-04.

- [x] **CAP-05**: An owner-reachable path to **enable** a capability. *(Server-side fix in 18-04/18-08's `validate_tighten_only` closed in **22-01** — the `enabled` branch no longer rejects either direction. **UI reachability closed in 22-04**: the Deploy page's Enabled checkbox has no permanent lock left, and a live agent's owner is asked to confirm before a flip that is immediately customer-effective; a pre-deploy agent's owner is not asked, since the checklist/approval gate still stands between the change and any live effect. CAP-05 is now reachable end-to-end by a non-technical owner with no terminal, no curl, no SQL.)*
- [x] **ACT-07**: A **resolution path for `pending_confirmations`** — approve and reject, with expiry. *(Resolver core closed in **22-02** (the human-approved bypass seam inside the dispatcher, re-running the checks a resolver can safely re-run and skipping only the two it cannot); the queue/resolve routes and runtime-queue Celery task closed in **22-03**. **UI reachability closed in 22-04**: the Deploy page's "Pending confirmations" section lets an approver read the triage queue in business language and resolve a row with a staged confirmation, rendering an honest verdict — never a `pass` chip on a merely-approved-but-unexecuted row. ACT-07 is now reachable end-to-end by a non-technical owner with no terminal, no curl, no SQL.)*

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
| TXN-01..05, CAP-01, CAP-02, AUD-01, AUD-02 | Phase 14 | ✓ Complete (8/8 plans; 14-VERIFICATION human_needed 3/4, SC2 idempotency present-behavior-unverified pending live DB; 14-SECURITY.md) |
| ACT-01..06 | Phase 15 | ✓ Complete (3/3 plans; live-verified ACT-04/05 + T-15-01/02; **ACT-06 p95<1s deferred to prod infra** — 4660ms p95 on the local 4GB box; 15-SECURITY.md) |
| INT-01..07 | Phase 16 | ✓ Complete (7/7 plans; 16-VERIFICATION human_needed 2/3; **live Stripe test-mode refund gate deferred to prod, operator-accepted 2026-07-01**; 16-SECURITY.md) |
| IDV-01..05 | Phase 17 | ✓ Complete (6/6 plans; 17-VERIFICATION.md; 17-SECURITY.md 20/20) |
| BLR-01, BLR-02, CAP-03, CAP-04, RTX-01..04, SEC-01..03 | Phase 18 | ◐ In progress — 10/11 plans executed (18-01..18-10; BLR-01 blast-radius collector shipped; RTX-01/02/03 red-team probes wired into run_red_team, RTX-04 live gate deferred to 18-11; capability_service.py's canonical_envelope_hash/envelope_drift wired by 18-07 (BLR-02/CAP-04 complete for the backend gate); validate_tighten_only wired by 18-08; CAP-03/BLR admin UI shipped by 18-10). **18-11 is the only outstanding Phase 18 plan** — RTX-04 stays open. |
| DOC-01..03, VER-01, AUD-03 | Phase 19 | ✓ 5/5 plans executed (19-01..19-05), phase closed 2026-07-28. **DOC-01/02/03 complete** (guides published, anchor gates pass). **VER-01 recorded `failed — blocked`** — SC2 has two structural causes the product does not currently support (capability `enabled=True` unreachable through any shipped API; `T-19-04`'s unresolved `require_human` branch); SC3's harness is authored and unit-proven but its live run was deferred (no local PostgreSQL). **AUD-03 recorded `deferred`** for the same reason — its harness is authored and unit-proven but never run live. See `19-UAT.md` for the full dispositions. |
| CAP-05, ACT-07 (VER-01 SC2 blocker closure) | Phase 22 | ◐ In progress — 4/6 plans executed (22-01..22-04). **CAP-05 and ACT-07 both reachable end-to-end by a non-technical owner** as of 22-04 (Deploy-page Enabled control unlocked with conditional staged-confirm; Pending confirmations approver queue shipped) — see the `VER-01 Blocker Closure (Phase 22)` section above for the full closure chain. **VER-01 itself is not yet re-verified** — its SC2 re-run is owned by `22-06`'s operator gate. |

**v1.1 coverage:** 43 requirements across phases 14–19, all mapped. (TXN 5, CAP 4, ACT 6, INT 7, IDV 5, AUD 3, BLR 2, RTX 4, SEC 3, DOC 3, VER 1 = 43.) **Plus 2 blocker-closure requirements added 2026-07-28** (CAP-05, ACT-07) tracked under Phase 22, not part of the original 43.
**v1.1 delivered:** 37 of 43. **Outstanding (6):** `CAP-03` (admin UI shipped by 18-10, held open pending its own operator checkpoint per `18-10-SUMMARY.md`), `INT-01`/`INT-06` (pre-existing, Phase 16, not touched by Phases 18-19), `RTX-04` (owned by unexecuted `18-11`), `VER-01` (recorded `failed — blocked` by Phase 19), `AUD-03` (recorded `deferred` by Phase 19, needs a local PostgreSQL server to close).

---

### v1.2 Traceability

*Milestone v1.2 — Gotham console + comprehensive agent management. Source: `.planning/AGENT-MGMT-GAPS.md`.*

| Requirements | Phase | Status |
|---|---|---|
| UI2-01..08 | Phase 20 — Gotham frontend cutover | ✓ Complete (15/15 plans; 20-VERIFICATION.md `passed` 12/12; parity suite 135/135; `check:no-dusk-tokens` exits 0) |
| OPS-01..16 | Phase 21 — Agent-management backend completion | ✓ Complete (9/9 plans; 21-VERIFICATION.md `passed` 7/7 after the SC3 grade→promote wiring gap was closed; 21-SECURITY.md 33/33 threats closed) |

**v1.2 coverage:** 24 requirements across phases 20–21, all mapped and all delivered. (UI2 8, OPS 16 = 24.)

**v1.2 live-gate debt:** no v1.2 migration has been applied to a live Neon DB (tenant 0009–0012, control 0017–0018). Live Langfuse trace visibility, real Ragas faithfulness numbers, and an end-to-end `POST /approve-deployment` → 422 remain unproven — this is the deferred `/gsd-verify-work 21` gate.

---

### Phase 13 (Production Hosting) Traceability

| Requirements | Phase | Status |
|---|---|---|
| PROD-01..15 | Phase 13 | **⏸ Paused at 7/11 plans.** All autonomous code complete (Terraform IaC, Bedrock Titan v2 embedder, Neon pooling, per-tenant re-embed, env-driven embed snippet, S3 uploads, ContextVar concurrency). 13-08/09/10/11 are `autonomous:false` and need a real AWS account (billing + Bedrock Titan v2 access in us-east-1) plus the `terraform` and `aws` CLIs. Resume: `/gsd-execute-phase 13 --wave 3`. No VERIFICATION or SECURITY artifact yet. |

---
*Requirements defined: 2026-05-12*
*Last updated: 2026-05-12 after 01-04 — CTL-01, CTL-05, CTL-09, CTL-10 marked complete*
*Last updated: 2026-05-13 after 01-06 — CTL-13 marked complete (80.41% unit test coverage)*
*Last updated: 2026-07-26 — traceability refresh. v1.1 rows corrected from "Pending" to their real status (Phases 14–17 complete, each with a SECURITY.md; 18–19 have 0 plans). v1.2 traceability added (UI2-01..08, OPS-01..16 — 24 requirements, previously absent from this file entirely). Phase 13 PROD-01..15 row added with its paused-at-7/11 status. Live-gate debt recorded explicitly rather than left implied.*
*Last updated: 2026-07-28 after 19-05 — Phase 19 closed (5/5 plans). DOC-01/02/03 ticked (guides published, anchor gates pass). VER-01 and AUD-03 un-ticked and corrected: both were prematurely marked `[x]` during earlier planning; the operator's 2026-07-28 dispositions in `19-UAT.md` are `[failed — blocked]` (VER-01 SC2) and `[deferred]` (VER-01 SC3, AUD-03) — neither has been proven, so neither is ticked. Phase 18 traceability corrected from 7/11 to 10/11 plans executed (18-10 was stale-recorded as unexecuted; its own SUMMARY holds CAP-03 open pending its separate operator checkpoint). v1.1 delivered count corrected to 37/43 against the actual checkbox state (previous "30 of 43" / "13 outstanding" arithmetic did not reconcile).*
