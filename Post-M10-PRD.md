# W.chats — Post-M10 PRD: v1.1 through v1.3

> **Parent PRD:** `PRD.md` (main W.chats platform PRD, v2)
> **Scope:** Post-M10 releases — transactional capability, agent-native protocol surface, and production hardening for agent traffic
> **Status:** Draft v1
> **Owner:** Mfanafuthi Mhlanga
> **Last updated:** 2026-06-29 (v1.2 §5.2: reframed MCP provisioning → REST-core CLI+Skill+MCP per ADR-0002; added ACP merchant compatibility; v1.1 §4.2: tool defs now A2A- and ACP-skill-compatible)
>
> *Prerequisite:* M10 ships first. Every section below assumes the M1–M10 platform is live, observability is in place, and the verified knowledge layer is compounding. Nothing in this document is a substitute for the M10 deliverables.

---

## 1. Purpose of this document

The M1–M10 PRD specifies a customer service platform: ingestion, retrieval, reasoning, validation, evals, red team, pre-deployment checklist, widget. By the end of M10, deployed agents answer customer questions and the platform monitors them continuously.

This document covers the three releases that follow, in order:

- **v1.1 — Transactional capability.** Agents move from answering questions to taking actions: placing orders, processing refunds, booking slots, updating accounts. The auth system, the Actor validator, the financial blast-radius gate, and the per-skill capability envelope ship here.
- **v1.2 — Agent-native protocol surface.** Every deployed W.chats agent becomes an A2A-compliant endpoint and the platform becomes MCP-provisionable from developer tools. The agent-card manifest, the JSON-RPC adapter, the per-counterparty quotas, and the registry listings ship here.
- **v1.3 — Production hardening for agent traffic.** Continuous monitoring, incident response, A2A-specific red team probes, alerting, audit infrastructure designed for the agent-traffic surface.

Each release builds on the previous one. Each ships with explicit security deliverables, not as a separate hardening pass but as part of the milestone's definition of done. The platform's threat model and the controls that address it are made first-class in this document, not deferred.

## 2. The threat model — what we are defending against

The architecture below earns its complexity against five specific threats. Each release addresses different combinations of them:

**T1. Prompt injection via customer message.** Customer types instructions attempting to override the agent's system prompt or coerce destructive actions. Already partially mitigated by M5's validation chain for informational responses; transactional responses need a separate pre-mutation control.

**T2. Prompt injection via ingested content.** A poisoned support document, a malicious URL the owner ingested, or a CSV with embedded instructions. The injection sits dormant in chunks and gets retrieved during normal conversation, steering the agent without the attacker being present in the chat.

**T3. Agentic counterparty abuse via A2A.** A malicious or misbehaving agent discovers the W.chats agent's A2A endpoint, authenticates legitimately (real OAuth token from a real provider), and abuses skills — replaying transactions, probing for sensitive data, attempting privilege escalation across skills, fuzzing for unanticipated behaviour.

**T4. Lateral movement between tenants.** Attacker compromises one W.chats agent and attempts to read or affect another tenant's data. The worst-case failure: a platform-wide breach rather than a single-tenant incident.

**T5. Exfiltration via legitimate output channels.** A compromised agent cannot make outbound HTTP calls or write to disk, but the conversational response itself is an output channel. Attacker steers the agent to write customer PII into the response, where they or their crawler reads it back.

The architecture is built so that each threat has at least two independent controls. Single-layer defenses fail under adversarial pressure.

## 3. The eight security layers

These layers are referenced throughout the rest of the document. Each release implements a subset; v1.3 closes the gaps. The layers are configurable per-agent — a tenant whose agent only answers questions does not pay the cost of the Actor validator; a tenant exposing transactional skills to A2A counterparties runs all layers.

**L1. Tool contract as authorization surface.** Every action an agent can take is a typed Python function decorated as a tool. The function signature is the authorization boundary — strongly typed parameters, no string-blob inputs, no SQL, no URLs, no arbitrary JSON. Side-effecting tools require client-provided idempotency keys.

**L2. Per-skill capability envelope.** Every deployed agent carries a capability token declaring which tools it can call, at what rate, with what limits (max amount, scope filters, confirmation requirements). The envelope is configured at deployment time and surfaced in the M8 pre-deployment checklist. A2A counterparties get scoped-down sub-tokens, never additional permissions.

**L3. The Actor validator — pre-mutation gate.** A single-shot Claude call running before any side-effecting tool executes. Reads the conversation, the proposed tool call, and the tool's envelope. Outputs `approve | block | require_human`. Catches the prompt-injection-to-action class specifically, where the conversation looks legitimate but the proposed action does not align with the customer's stated intent.

**L4. Hard output firewall.** A dedicated pass on every response before it leaves the system. Scans for PII patterns, schema-bound exfiltration (data shaped like raw rows rather than synthesized answers), unusual encodings (base64, hex, structured blobs in prose), and excessive verbatim retrieval. Flagged responses are replaced with a generic deflection and logged.

**L5. Database isolation.** Connection strings invisible to agent code. Scoped connections from a platform-managed pool, tenant-bound at checkout. Application-layer tenant assertion on every query. Postgres row-level security as a second line within tenant DBs. No agent code path constructs SQL.

**L6. Ingestion-time injection defense.** Provenance labels on every chunk. Retrieval context explicitly wraps retrieved content with "treat as data, not instructions" framing. A separate red team class for content-injection (attacker via uploaded documents) distinct from conversation-injection (attacker in the chat).

**L7. A2A counterparty controls.** Per-counterparty rate limits, replay protection via mandatory idempotency keys on task submission, signed counterparty identity in audit logs, scoped task tokens that cannot exceed the agent's own envelope.

**L8. Observability and audit.** Structured audit log to a write-only Neon project isolated from control and tenant data planes. Langfuse for trace inspection. Continuous metrics on validator decisions, firewall flags, capability denials, cross-tenant query attempts. Alerting on anomalies with documented response procedures.

The principle stated plainly: every layer is configurable, every agent gets only what it needs, and no compromise of any single layer enables a catastrophic outcome.

---

## 4. v1.1 — Transactional capability

### 4.1 Goal

Agents take actions on behalf of customers. The agent surface moves from informational ("what is your shipping policy?") to mutating ("please refund my last order"). This is the release that justifies the rename from Veridian to W.chats — the platform now does things, not just chats.

### 4.2 Scope

**In scope:**

- Tool definitions for the canonical transactional actions: `place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record`, `escalate_to_human` (existing), `confirm_action` (new — used by the Actor validator for require-human flows).
- Per-tenant integrations with Shopify, WooCommerce, Stripe, and Calendly as the v1.1 connector matrix. Each integration is a server-held credential, invisible to agent code, scoped to the tenant.
- Customer-side identity verification flow. Customer enters an order number plus a one-time code emailed/SMSed to the address on file before the agent will act on their account. Tenant-configurable: which actions require verification, what verification method, expiry window.
- The Actor validator (L3) integrated into the reasoning engine. Every call to a mutating tool routes through it before execution.
- Per-skill capability envelopes (L2). Tenant-configured at deployment time, surfaced in the M8 checklist UI.
- Financial blast-radius gate in the pre-deployment checklist. Orchestrator reports the maximum single-action value and the maximum hourly aggregate; warnings escalate above tenant-configured thresholds.
- Transaction-specific red team probe (extends M7's red team agents). Probes for confused-deputy attacks, value-bound evasion (chained smaller refunds), and identity-verification bypass.
- Audit log extended to cover every tool call with input parameters, output, latency, capability-token contents, and Actor validator decision.

**Out of scope:**

- A2A surface. v1.2 territory. Tool definitions are designed to be A2A- **and ACP-** skill-compatible (typed inputs, typed outputs, examples) but the A2A/ACP servers themselves are not part of v1.1. (ACP = the Agentic Commerce Protocol — OpenAI + Stripe's open, Apache-2.0 standard for agent↔merchant commerce; see v1.2 §5.2.)
- ERP/CRM integrations beyond Shopify/WooCommerce. Mid-market expansion lives later.
- Marketplace integrations (Uber Eats, Glovo, etc.). Same architecture, future release.
- Multi-currency complexity. v1.1 assumes single-currency operation per tenant, configured at deployment time.

### 4.3 Architectural additions

**Reasoning engine.** The Claude Agent SDK tool loop gains the Actor validator as a pre-execution hook. The hook fires for any tool flagged `mutating: true` in its definition. Tools are tagged at definition time, not inferred at runtime. The hook is short-circuited if the tool's capability envelope marks it as `requires_confirmation: false` and `max_amount_cents` is below a per-tenant skip threshold (saves Claude cost on low-value actions like reading order status).

**Control DB.** New tables:

```sql
CREATE TABLE capability_envelopes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    UUID NOT NULL REFERENCES agents(id),
    skill       TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT false,
    rate_limit  TEXT,                                -- e.g. "5/hour"
    constraints JSONB NOT NULL DEFAULT '{}',         -- max_amount_cents, scope filters, etc.
    requires_confirmation BOOLEAN NOT NULL DEFAULT false,
    requires_identity_verification BOOLEAN NOT NULL DEFAULT false,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, skill)
);

CREATE TABLE tool_calls_audit (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    conversation_id UUID,
    skill           TEXT NOT NULL,
    arguments       JSONB NOT NULL,
    result          JSONB,
    actor_decision  TEXT,                            -- 'approve' | 'block' | 'require_human'
    actor_rationale TEXT,
    capability_snapshot JSONB NOT NULL,              -- envelope at time of call
    latency_ms      INT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pending_confirmations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    skill           TEXT NOT NULL,
    arguments       JSONB NOT NULL,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT                             -- 'approved' | 'rejected' | 'expired'
);
```

**Per-tenant Neon (additions to v1 schema).** New tables in tenant DBs:

```sql
CREATE TABLE customer_identities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id  TEXT NOT NULL,                      -- email, phone, or platform user id
    verified_at  TIMESTAMPTZ,
    verification_method TEXT,                        -- 'email_otp' | 'sms_otp' | 'platform_oauth'
    session_token_hash TEXT,
    session_expires_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE integration_credentials (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider     TEXT NOT NULL,                      -- 'shopify' | 'stripe' | 'woocommerce' | 'calendly'
    credential   BYTEA NOT NULL,                     -- encrypted, never exposed to agent
    scopes       JSONB,
    expires_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`integration_credentials` are encrypted with a tenant-specific key derived from the platform's master key and the tenant ID. Agents never read this table directly — tool implementations resolve credentials via a platform service that returns a short-lived in-memory handle.

**Validation chain.** The validation chain extends to four nodes:

```
[reasoning produces response or tool call]
  ↓
  is it a mutating tool call?
  ├── yes → Actor (pre-execution) → tool runs → Gatekeeper, Auditor, Strategist (post-response)
  └── no  → Gatekeeper, Auditor, Strategist (post-response)
```

The Actor runs synchronously on the request path. The other three continue to run asynchronously after the response streams to the user. Total added latency on mutating calls: one Haiku-tier call (typically 400-800ms p95), which is acceptable given the customer is about to be told their order was placed.

### 4.4 Security layers active in v1.1

| Layer | Status in v1.1 |
|---|---|
| L1 Tool contract | **Full.** All transactional tools are typed Python functions. No string-blob inputs anywhere in the v1.1 tool set. |
| L2 Capability envelope | **Full.** Per-skill envelopes shipped, configured at deployment, surfaced in M8 checklist. |
| L3 Actor validator | **Full.** Pre-mutation gate on every mutating tool call. |
| L4 Output firewall | **Partial.** PII regex pass shipped for v1.1. Schema-bound exfiltration detection and Claude-classifier pass deferred to v1.2 where the A2A surface makes them more important. |
| L5 Database isolation | **Full.** Connection scoping and RLS shipped in M1; v1.1 adds the `integration_credentials` encryption pattern and the platform credential service. |
| L6 Ingestion injection defense | **Full.** Provenance labels already shipped in M2; v1.1 adds the "treat as data not instructions" wrapper to retrieval context and splits the M7 prompt injection red team agent into conversation-injection and content-injection variants. |
| L7 A2A counterparty controls | **Not applicable.** No A2A surface yet. |
| L8 Observability | **Partial.** Audit log extended for tool calls. Continuous alerting deferred to v1.3. |

### 4.5 Per-agent configurability

Every tenant configures the security posture at deployment time. The M8 checklist UI is extended with a "Capabilities and limits" section:

- For each available skill, the owner sees a default envelope and can tighten it (never loosen beyond platform defaults).
- For transactional skills, the owner sees the financial blast radius (max single, max hourly, max daily) and acknowledges it explicitly.
- Identity verification requirements per skill are configured here. Common defaults: read-only skills require no verification, refunds require email OTP, account changes require email or SMS OTP, anything over a tenant-configured value requires human-in-the-loop confirmation.
- Actor validator behaviour per skill: always-on, sample at rate N, off (only allowed for non-mutating skills).
- The summary is displayed in the deployment report and the owner approves it. Approval is logged with the envelope hash; any later change to the envelope re-triggers the pre-deployment checklist.

The principle: an agent that only answers questions runs with L1, L4 (PII pass), L5, L6, L8. An agent that issues refunds adds L2, L3 with full Actor coverage. An agent that places orders with values over a threshold also requires human-in-the-loop on the highest tier. Cost scales with risk.

### 4.6 Success criteria

- Non-technical tester can deploy an agent that issues refunds up to a configured limit and place orders against a Shopify store, end-to-end, without writing code.
- Red team probe in M7 (with v1.1 extensions): zero high-severity findings on transaction-specific attack classes (confused deputy, value-bound evasion, identity-verification bypass) for a clean tenant.
- Actor validator p95 latency under 1 second; total added latency on mutating calls under 1.5 seconds end-to-end.
- Tool call audit log captures 100% of mutating calls with full input/output/decision/envelope snapshot. Zero gaps in 30 days of synthetic traffic.
- A simulated platform-wide test where 100 synthetic adversarial customer messages run against a deployed test agent: zero unauthorized state mutations escape Layer 1-3 controls.

### 4.7 Deliverables checklist

- [ ] Six core transactional tool definitions with full Pydantic schemas, idempotency-key handling, and typed return values.
- [ ] Shopify, WooCommerce, Stripe, Calendly integration adapters with encrypted credential storage.
- [ ] Customer identity verification flow (email OTP + SMS OTP), per-skill configuration.
- [ ] Actor validator implementation, integrated as a pre-execution hook in the Agent SDK loop.
- [ ] Capability envelope table, enforcement middleware, and admin UI in the M8 checklist.
- [ ] Financial blast-radius gate in the pre-deployment checklist orchestrator agent.
- [ ] Transaction-specific red team agent extension in M7's framework.
- [ ] Tool call audit log table and the platform credential service.
- [ ] Documentation: tool-author guide, integration-provider guide, owner-facing capability configuration guide.
- [ ] v1.1 retrospective.

### 4.8 Target duration

6-8 weeks of focused build time, parallelizable across the four integration adapters once the tool framework and Actor validator are stable.

---

## 5. v1.2 — Agent-native protocol surface

### 5.1 Goal

Every deployed W.chats agent becomes accessible to other agents. The same agent that serves a human via iframe is also discoverable by A2A-compliant counterparties (OpenClaw, Hermes, Claude Code agents, ChatGPT in agentic mode, enterprise procurement bots, the 150-plus organizations in the A2A ecosystem) and provisionable from developer tools via MCP.

This release puts W.chats on the same protocol layer as Salesforce, ServiceNow, and SAP for SMB customer service and commerce — a segment none of those vendors serve.

### 5.2 Scope

**In scope:**

- **A2A server per deployed agent.** Every agent exposes an Agent Card at `https://{agent-domain}/.well-known/agent-card.json` plus a JSON-RPC endpoint serving the A2A task lifecycle.
- **Skill schemas for the existing tool set.** Each tool from M1-v1.1 is exposed as an A2A skill with name, description, input/output schemas, examples, and supported modes (text, structured JSON, both).
- **Task lifecycle adapter.** A2A's task states (`submitted | working | input-required | completed | failed | canceled | rejected`) mapped to the platform's existing Celery job model and SSE event stream. A2A's status-update channel reuses the SSE infrastructure built in M1.
- **A2A authentication.** Bearer-token issuance per counterparty with scoped permissions, replay protection via mandatory idempotency keys on task submission, signed counterparty identity claims captured in the audit log.
- **Provisioning surface — REST-core, exposed three ways (MCP + CLI + Skill).** W.chats becomes provisionable from developer tools. Per `docs/adr/0002-agent-tool-and-provisioning-strategy.md`, the control-plane REST API is the single source of truth, exposed as: a token-lean `wchats` **CLI** (the code-execution path), a provisioning **Skill** that encodes the multi-step playbook and per-operation required fields (e.g. `create_agent` needs name + role + a structured soul), and a thin curated **MCP server** for broad dev-tool reach (Claude Code, Cursor, OpenClaw). **Not MCP-only** — MCP loads every tool schema into context, so the CLI + Skill carry token efficiency and the playbook while MCP carries compatibility. Curated op set (1:1 with REST endpoints): `create_agent`, `update_agent_soul`, `set_widget_config`, `ingest_documents`, `ingest_urls`, `trigger_eval`, `get_eval_summary`, `trigger_red_team`, `get_red_team_summary`, `run_predeploy_checklist`, `check_deployment_status`, `approve_deployment`, `get_embed_snippet`, `get_audit_log` — `ingest_documents` and `ingest_urls` are deliberately **separate verbs** (the knowledge base spans uploaded files *and* URLs). All three surfaces ship together in v1.2.
- **ACP merchant compatibility (Agentic Commerce Protocol).** Building on v1.1's transactional tools, each deployed W.chats agent can expose an ACP merchant surface — making its SMB tenant discoverable and *sellable through* buyer-side agents (ChatGPT shopping, agent commerce) via the OpenAI + Stripe open standard. v1.1 tool definitions are already designed ACP-skill-compatible; v1.2 adds the merchant endpoints + manifest. This puts W.chats SMBs into the agent-commerce channel the same way A2A puts them into the agent-interop channel — a segment incumbents (Salesforce/ServiceNow/SAP) do not serve.
- **Registry submissions.** Listings in the major public A2A registries (Linux Foundation, Google Cloud, Microsoft, AWS) and the major MCP registries (Anthropic's, mcp.so, the community-maintained ones).
- **Multi-format manifest generation.** A single internal source-of-truth produces agent-card.json (A2A), mcp.json (MCP), openai-agent.json (OpenAI agent SDK), and the ACP merchant manifest at `.well-known` paths. Adding new manifest formats is a serialization change, not an architecture change.
- **Output firewall completion.** The schema-bound exfiltration detection and Claude-classifier pass deferred from v1.1 ship here, because A2A counterparties materially increase the exfiltration attack surface.

**Out of scope:**

- Bidirectional agent collaboration where W.chats agents call out to *other* A2A endpoints. Plausible future direction, not v1.2.
- Federated agent marketplaces (anything beyond appearing in existing registries).
- Custom signature schemes or W3C verifiable credentials for counterparty identity. The standard A2A auth schemes are sufficient for v1.2.

### 5.3 Architectural additions

**Agent Card schema (per deployed agent).** Generated from the agent's database row plus its capability envelope plus its skill set. Example structure (abbreviated):

```json
{
  "name": "Acme Coffee Roasters — Customer Service",
  "description": "Customer service and commerce agent for Acme Coffee Roasters. Answers product questions, checks order status, issues refunds up to R500 with customer verification.",
  "provider": { "name": "W.chats", "url": "https://w.chats" },
  "url": "https://acme-coffee.w.chats/a2a",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "authentication": {
    "schemes": ["Bearer"],
    "tokenEndpoint": "https://acme-coffee.w.chats/a2a/token"
  },
  "skills": [
    {
      "id": "answer_question",
      "name": "Answer customer question",
      "description": "Answer a question about Acme Coffee Roasters' products, policies, or services using verified business data.",
      "inputModes": ["text"],
      "outputModes": ["text", "structured"],
      "examples": ["Do you ship to Cape Town?", "What's in your Table Mountain Espresso blend?"]
    },
    {
      "id": "check_order_status",
      "name": "Check order status",
      "description": "Look up the current status and delivery information for a customer's order.",
      "inputModes": ["text", "structured"],
      "outputModes": ["text", "structured"],
      "examples": ["Where is order ACME-12345?"]
    }
  ]
}
```

**Task lifecycle mapping.** A2A `Task` objects map onto the platform's existing job model with a translation layer. An incoming A2A task creates a `job` row with `kind = 'a2a_task'` and a `runtime` queue Celery task. SSE events get translated to A2A status updates. Task IDs are externally-visible UUIDs distinct from internal job IDs (no information leakage about job ordering or volume).

**Skill execution.** A2A skill invocation routes through the same reasoning engine that serves the iframe widget. The difference is the input/output mode — structured-mode skills return JSON conforming to the skill's output schema rather than prose. The reasoning engine produces both representations; the response shape is chosen by the caller's `Accept` declaration in the task submission.

**Per-counterparty rate limits.** Each issued Bearer token has its own quota bucket. Rate limit configuration: per-tenant, with per-counterparty overrides for known partners. Token Bucket algorithm, Redis-backed, with a fast-path zero-RTT check.

**MCP server.** A separate FastAPI service exposing the MCP protocol over the standard transports (stdio, SSE). MCP tools wrap the existing REST API; the MCP server is stateless and authenticates via API keys issued to the tenant or to delegated developers.

**Manifest generator.** A single Pydantic model representing the canonical agent description. Three serializers produce A2A Agent Card, MCP server manifest, and OpenAI agent manifest. Each serializer is a thin function. Adding a fourth format is a 20-line change.

### 5.4 Security layers active in v1.2

| Layer | Status in v1.2 |
|---|---|
| L1 Tool contract | **Full.** A2A skills are bound to the same typed tool functions as the iframe path. No new tool surface for A2A — the surface is the existing tools, exposed through a different transport. |
| L2 Capability envelope | **Full + extended.** Counterparty tokens get sub-envelopes scoped down from the agent's own envelope. Never up. Configured at token issuance time. |
| L3 Actor validator | **Full.** Continues to gate every mutating call regardless of whether the caller is a human via iframe or an agent via A2A. |
| L4 Output firewall | **Full.** v1.1's PII pass + the schema-bound exfiltration detection and Claude-classifier pass deferred from v1.1. Firewall runs at three checkpoints: before iframe responses, before A2A task results, before any structured output in API responses. |
| L5 Database isolation | **Full + extended.** A2A traffic goes through the same connection scoping as iframe traffic. Cross-tenant A2A access attempts get the same fail-closed behaviour. |
| L6 Ingestion injection defense | **Full.** Unchanged from v1.1. |
| L7 A2A counterparty controls | **Full.** Per-counterparty rate limits, idempotency keys, signed identity in audit log, scoped task tokens. |
| L8 Observability | **Partial.** Audit log extended for A2A task traffic. Continuous alerting still v1.3 territory. |

### 5.5 Per-agent configurability

The M8 checklist UI is extended with a new section: "Agent-native access."

- Owner chooses whether to expose the A2A endpoint at all. Default: off. Owners who don't want their agent talking to other agents can keep the iframe-only experience.
- If enabled, owner chooses which skills are A2A-accessible. A skill exposed via iframe is not automatically exposed via A2A. Read-only skills (`answer_question`, `check_order_status`) might be A2A-accessible by default; mutating skills (`issue_refund`, `place_order`) require explicit owner opt-in.
- Owner configures counterparty policy: open (any A2A-compliant client can authenticate), allowlist (only specified counterparty domains), or invitation (counterparties must be added by token issuance).
- Owner configures per-counterparty rate limits and value caps. The defaults inherit the agent's own envelope; counterparty-specific overrides tighten them further.
- For high-trust partners (e.g., a known marketplace), the owner can configure a delegated-approval mode where the partner's identity claim is sufficient for actions normally requiring human-in-the-loop. This is the "I trust OpenClaw to act on behalf of its authenticated user" knob.

Configuration is logged and re-triggers the pre-deployment checklist if changed.

### 5.6 Success criteria

- A real A2A-compliant client (the Linux Foundation conformance harness plus at least one of Claude Code, OpenClaw, or Hermes) successfully discovers, authenticates, and submits a task to a W.chats agent end-to-end without any custom integration code.
- Agent Card validates against the A2A v1 schema and renders correctly in the main public registries.
- An MCP-provisioned agent (created via Claude Code or Cursor's MCP integration) completes the full M1-M8 pipeline without human intervention beyond the final approval step.
- A2A red team probes (see §6 for v1.3 detail; v1.2 ships the probe framework): zero critical findings on a clean tenant for cross-skill privilege escalation, capability token forgery, replay attacks, and rate-limit evasion via task fragmentation.
- Output firewall p95 added latency: under 300ms for the regex+classifier pass.
- Three concrete partner integrations demonstrated publicly: Claude Code provisioning, OpenClaw consuming, one third (TBD by ship date).

### 5.7 Deliverables checklist

- [ ] A2A server implementation: agent card endpoint, JSON-RPC adapter, task lifecycle adapter, SSE-to-A2A status translation.
- [ ] Skill schemas for the existing tool set (10-15 skills depending on tenant configuration).
- [ ] Per-counterparty token issuance and rate limiting.
- [ ] MCP server for control plane API.
- [ ] Multi-format manifest generator (A2A, MCP, OpenAI) from canonical Pydantic model.
- [ ] Output firewall extensions (schema-bound exfiltration, Claude classifier).
- [ ] Owner-facing agent-native access configuration in M8 checklist.
- [ ] Registry submissions: at least four major registries (LF, Google Cloud, MS, AWS for A2A; Anthropic and mcp.so for MCP).
- [ ] A2A red team probe framework (probes themselves are v1.3 work).
- [ ] Documentation: A2A integration guide for counterparties, MCP integration guide for developers, owner-facing "what is agent-native access" plain-language explainer.
- [ ] v1.2 retrospective.

### 5.8 Target duration

5-7 weeks. The A2A server is the main work; MCP provisioning and manifest generation are 1-2 weeks each running in parallel.

---

## 6. v1.3 — Production hardening for agent traffic

### 6.1 Goal

The platform is now exposed to two distinct traffic classes: humans via iframe (with all the well-understood failure modes of consumer software) and agents via A2A (with adversarial behaviour patterns that are still being characterized industry-wide). v1.3 makes the platform production-grade against both, with continuous monitoring, alerting, and incident response infrastructure designed for the agent-traffic surface.

### 6.2 Scope

**In scope:**

- **Audit log infrastructure.** A dedicated write-only Neon project, isolated from control and tenant data planes, receiving structured audit events from every security-relevant code path. Append-only by application contract, role-based access for incident response, retention policy per event class.
- **Continuous monitoring and alerting.** Metrics published from each security layer to a central observability stack (Prometheus + Grafana or equivalent). Alerting on documented anomaly patterns with response runbooks.
- **A2A-specific red team agents.** The probe framework shipped in v1.2 is populated with actual probes: cross-skill privilege escalation, capability token replay, task chaining attacks, rate-limit evasion via task fragmentation, identity-verification bypass via delegated approval abuse.
- **Incident response runbooks.** Documented procedures for the highest-likelihood incident classes (compromised tenant, exfiltration attempt detected, A2A counterparty abuse, integration credential leak).
- **Per-tenant security posture review.** A quarterly automated review where the orchestrator agent reads the tenant's audit log, eval drift, red team findings, and capability changes, and produces a posture report for the owner.
- **Penetration test against the platform.** An external pen test conducted before v1.3 declares done, scoped to A2A surface, transactional flows, and tenant isolation. Findings remediated before public announcement.

**Out of scope:**

- SOC 2 / ISO 27001 certification. These follow from the controls in v1.3 but the certification process itself is a separate workstream (typically 6-12 months elapsed) that begins after v1.3 ships.
- Customer-facing security marketing (e.g., a public trust center). v1.3 is the engineering substrate; marketing follows.
- Bug bounty program. Set up after the external pen test confirms baseline hygiene.

### 6.3 Architectural additions

**Audit log architecture.** A third Neon project per region (`audit`), isolated from the platform's `control` and per-tenant data planes. Write-only from the application's perspective: every security-relevant code path emits an event to a Redis stream, which is drained by a dedicated audit-writer worker that performs the only INSERT operations against the audit project.

Event classes captured:

- `tool.call` — every tool invocation with full inputs, outputs, Actor decision, envelope snapshot.
- `actor.decision` — every Actor validator decision with rationale.
- `firewall.flag` — every output firewall flag with the redacted-shape representation of the flagged content.
- `capability.denial` — every capability envelope rejection.
- `auth.event` — token issuance, refresh, revocation, failed verification.
- `a2a.task.lifecycle` — every A2A task state transition.
- `cross_tenant.attempt` — should always be zero; any non-zero is an immediate page.
- `ingestion.injection_detected` — content-injection red team probe matches in production traffic.

Each event carries a tenant ID, an agent ID, a conversation/task ID, a timestamp, and an actor identity (customer, A2A counterparty, owner, internal-system).

**Alerting topology.** Three tiers:

- **P0 — page on-call.** Cross-tenant query attempt, capability envelope bypass detected, audit log write failure, integration credential service failure.
- **P1 — notify within an hour.** Actor block rate spike on a tenant (>5x baseline), firewall flag rate spike, Auditor `ungrounded` rate spike, rapid configuration changes on a high-value agent.
- **P2 — daily digest.** Drift in eval metrics, slow drift in capability denial rates, unusual A2A counterparty traffic patterns.

Each tier has a documented runbook. P0 runbooks include the kill switch — a per-agent emergency stop that revokes all tokens, disables the A2A endpoint, and notifies the owner with one CLI command.

**A2A-specific red team probes.** Four new agents in the M7 framework:

- *Cross-skill privilege escalation probe* — attempts to chain skill calls to achieve outcomes the individual skills' envelopes shouldn't permit (e.g., chaining many small refunds to evade a daily cap, using a read skill to discover an order ID then issuing a refund without verification).
- *Capability token abuse probe* — attempts replay, forgery, fragmentation, and elevation of A2A tokens.
- *Identity verification bypass probe* — attempts to abuse delegated-approval mode, exploits race conditions in the verification flow, attempts session fixation.
- *Tenant isolation probe* — attempts to read or affect a different tenant's data through any available skill, with multiple attack vectors (path traversal in IDs, SQL-shaped strings in skill parameters, encoded payloads).

These run weekly per deployed agent, with results going to the owner's weekly digest.

**Posture review.** A quarterly automated review generated by an orchestrator-tier agent. Inputs: the tenant's audit log for the period, eval metric trends, red team findings, capability changes, integration health. Output: a plain-language posture report for the owner covering "what changed, what risks emerged, what we'd recommend," approved or acknowledged in the dashboard.

### 6.4 Security layers active in v1.3

| Layer | Status in v1.3 |
|---|---|
| L1 Tool contract | **Full.** No changes. |
| L2 Capability envelope | **Full + monitoring.** Envelope change events captured in audit log; envelope drift over time is part of the posture review. |
| L3 Actor validator | **Full + monitoring.** Continuous metrics on decision distribution per tenant; alerting on anomalies. |
| L4 Output firewall | **Full + monitoring.** Flag rates monitored continuously; sustained spikes trigger P1 alerts. |
| L5 Database isolation | **Full + monitoring.** Cross-tenant query attempts trigger P0 immediately. |
| L6 Ingestion injection defense | **Full + monitoring.** Detection events in production traffic captured and surfaced. |
| L7 A2A counterparty controls | **Full + monitoring + probes.** All v1.2 controls plus the four new red team probes. |
| L8 Observability | **Full.** Audit log infrastructure, three-tier alerting, P0 runbooks with kill-switch, quarterly posture reviews. |

### 6.5 Per-agent configurability

The v1.3 configuration surface is smaller because most of v1.3 is platform-level infrastructure. What is configurable per agent:

- **Audit log retention** — owner can choose between platform defaults (90 days hot, 1 year cold) and premium tiers offering longer retention.
- **Alerting recipients** — owner configures who receives P1 notifications for their agent. Default: owner's email. Premium tier: webhook to owner's existing on-call system.
- **Posture review cadence** — defaults to quarterly; premium tier can opt into monthly.
- **Red team probe intensity** — defaults to weekly per-agent runs of the standard probes; premium tier can opt into daily runs and access to additional advanced probes.
- **Kill switch policy** — owner configures who can invoke the per-agent kill switch (owner only; owner + delegated security contact; platform-on-call in extreme cases with owner notification).

### 6.6 Success criteria

- External pen test completes with no critical findings unaddressed at ship.
- 30 days of synthetic traffic post-launch produces zero P0 alerts.
- Mean time to detect (MTTD) for injected anomalies in a controlled test: under 5 minutes for P0, under 30 minutes for P1.
- Kill switch verified to revoke all access within 60 seconds of invocation; verified quarterly via game day exercises.
- Owner posture review generated and delivered for every deployed agent at the end of the first quarter post-v1.3.
- Three platform-wide tabletop exercises completed with the documented runbooks before public announcement: simulated tenant compromise, simulated mass A2A abuse campaign, simulated integration credential leak.

### 6.7 Deliverables checklist

- [ ] Audit log Neon project, audit-writer service, event-class definitions.
- [ ] Metrics emission from every security layer.
- [ ] Alerting topology (P0/P1/P2) with documented runbooks for each.
- [ ] Per-agent kill switch implementation and CLI.
- [ ] Four A2A-specific red team probe agents in the M7 framework.
- [ ] Quarterly posture review orchestrator and owner-facing report template.
- [ ] External pen test conducted and findings remediated.
- [ ] Three tabletop exercises with the runbooks documented and refined.
- [ ] Public-facing documentation: threat model summary, controls overview, responsible disclosure policy.
- [ ] v1.3 retrospective and post-mortem of any pen test findings.

### 6.8 Target duration

4-6 weeks of focused build time. The audit infrastructure and alerting topology are the bulk of the engineering work; the red team probes and posture review extend frameworks built in earlier milestones.

---

## 7. Cumulative result after v1.3

The platform that exists at the end of v1.3 is:

- **A customer service agent platform** for SMBs (M1-M10) — proven, defensible, revenue-generating.
- **A commerce agent platform** (v1.1) — agents take action, place orders, process refunds, book slots, on behalf of customers, with eight layers of defense against prompt injection and exfiltration.
- **An A2A-native platform** (v1.2) — every deployed agent is discoverable and usable by any A2A-compliant counterparty, with scoped permissions and the same security envelope that protects human interactions.
- **MCP-provisionable** (v1.2) — Claude Code, Cursor, OpenClaw, and any other MCP-compatible developer tool can provision and configure agents through standard protocols, with no W.chats-specific integration code.
- **Production-hardened against agent-traffic threats** (v1.3) — continuous monitoring, alerting, audit infrastructure, external pen test, documented incident response.

The portfolio narrative that follows naturally:

> *W.chats is the first SMB customer service and commerce platform built for the agent-traffic era. Eight configurable security layers, A2A-native deployment, MCP-provisionable from developer tools, with eval-driven verification and continuous red teaming. Every deployed agent gets only the controls it needs — a question-answering agent runs lean; an agent that places orders for A2A counterparties runs the full envelope. Built on open standards (A2A, MCP), open infrastructure (Neon, Postgres, Voyage), and an open source codebase.*

That paragraph is technically defensible, market-relevant, and uniquely true of W.chats at the time it ships. The frontier is wide open in this segment and the work is well-defined.

## 8. What this document is not

It is not a v1.1/1.2/1.3 implementation specification. Each release will spawn its own implementation PRDs with task-level detail, the same way M1-M10 did. This document exists to establish the strategic shape, the security architecture, and the configurable-per-agent principle before implementation begins on the first post-M10 release.

## 9. Open questions

- Pricing tier structure post-v1.1. Transactional agents are materially more expensive to run (Actor validator, more eval coverage, more red team probes). Likely a new tier above the M10 baseline; specifics deferred until v1.1 cost telemetry exists.
- Whether to publish the threat model and controls overview as a public "trust center" document at v1.3, or wait for SOC 2 readiness. Leaning toward publishing earlier — the security architecture is a portfolio asset and the SOC 2 process benefits from existing public documentation.
- Whether v1.2's MCP provisioning surface should support multi-tenant developer accounts (an agency provisioning agents for many clients through a single MCP connection) at launch or as a fast-follow. The agency segment is the GTM lever, so leaning toward launch.
- Long-term: whether to build a federated W.chats agent network where W.chats agents discover and call *each other* (a coffee roaster's agent recommending a complementary tea shop's agent). Architecturally available once v1.2 ships; product question of whether and when.

## 10. Dependencies and assumptions

- M10 has shipped. The audit log infrastructure in v1.3 assumes Langfuse is in place, the verified knowledge layer is compounding, and the eval harness is generating real telemetry.
- Anthropic continues to ship Claude API and Agent SDK. The platform depends on Haiku-tier availability for the Actor and the validation chain; Sonnet-tier for the orchestrator and strategist agents.
- A2A v1 specification remains stable. Minor version updates are accommodated through the manifest serializer; a breaking v2 would require a re-architecture and is not part of v1.2 scope.
- Neon's per-project provisioning, branching, and isolation guarantees remain as documented. The audit-log Neon project assumption depends on Neon's ability to enforce strict role-based access between projects.
