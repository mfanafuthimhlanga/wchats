# ADR-0002: Agent Tool Consumption & Provisioning-Surface Strategy

**Status:** Proposed
**Date:** 2026-06-29
**Deciders:** Bantuson
**Informs:** Phase 16 (v1.1 integration adapters) now; v1.2 "agent-native protocol surface" later (`Post-M10-PRD.md` §5)

---

## Context

Two related questions surface as W Chats becomes transactional (v1.1) and agent-native (v1.2):

1. **Consumption** — how does a deployed W Chats agent *call* external providers (Stripe, Shopify, WooCommerce, Calendly) to take actions?
2. **Provisioning** — how does W Chats *expose* its control plane so agents/developers can provision and configure W Chats agents from their tools (the PRD's "MCP-provisionable from developer tools")?

Both intersect a finding the agent-tooling ecosystem converged on in 2025–2026: **loading all MCP tool definitions upfront is expensive and degrades tool selection.**

- MCP loads every tool's full schema at session start by protocol design — ~550–1,400 tokens per tool; seven typical servers ≈ 67k tokens (~34% of a 200k window) before the user speaks. ([apideck](https://www.apideck.com/blog/mcp-server-eating-context-window-cli-alternative), [junia.ai](https://www.junia.ai/blog/mcp-context-window-problem))
- Fewer, more-relevant tools measurably improves tool-selection accuracy (Anthropic: Opus 4 49%→74%, Opus 4.5 79.5%→88.1% with lazy loading). ([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp))
- Mitigations that emerged: **deferred/tool-search loading**, **code-execution-with-MCP** (present servers as code APIs; 150k→2k tokens, 98.7% reduction; intermediate data stays out of model context), **Agent Skills** (~100 tokens at scan, full body on demand), and **CLIs** (~80-token prompt replaces schemas; `--help` progressive disclosure). ([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp), [llamaindex](https://www.llamaindex.ai/blog/skills-vs-mcp-tools-for-agents-when-to-use-what))
- Practitioner consensus: **Skills compose *on top of* MCP, not as a replacement.** Reach for MCP/CLI when *integration* (live, governed access) is the pain; reach for a Skill when *judgment/procedure* is the pain. Small, curated tool sets avoid the bloat entirely.

---

## Decision

### Part A — Agent → provider consumption (v1.1, Phase 16)

Deployed agents take external actions **only through W Chats' own narrow, typed tools** (one tool per business action — `issue_refund`, `update_subscription`, `place_order`, …), which call the provider's **server-side SDK/REST** behind L1 (typed contract) → L2 (capability envelope) → L3 (Actor validator) → audit.

**We do NOT mount provider MCP servers or vendor "agent toolkits" (e.g. the Stripe Agent Toolkit) into the agent.** Three independent reasons, all pointing the same way:

1. **Security** — a provider MCP/toolkit hands raw provider operations to the LLM, bypassing the Actor validator, capability envelope, and audit log that are the entire point of v1.1.
2. **Context** — a provider MCP server dumps that provider's *whole* API into the agent context (Stripe's MCP exposes the full surface); our curated set is the "fewer, more relevant tools" that improves selection accuracy.
3. **Blast radius** — a typed tool exposes exactly one action with typed args; an MCP firehose exposes everything the credential can do.

Provider specifics:
- **Stripe (INT-05):** Refunds API (`issue_refund`), Subscriptions API (`update_subscription`), Checkout Session / Payment Link for `place_order` (no card handling). Pass the TXN-02 client idempotency key straight to Stripe's native `Idempotency-Key`. Store a per-tenant **Stripe Restricted API Key** scoped to only the enabled skills — defense-in-depth at the Stripe layer mirroring the L2 envelope.
- **Shopify / WooCommerce / Calendly:** same pattern — provider SDK/REST behind typed tools, with scope-restricted credentials.

The Stripe Agent Toolkit and Stripe MCP server remain useful as *references* (which ops to expose, request/response shapes), not as the agent-facing layer.

### Part B — W Chats control-plane provisioning surface (v1.2)

The control-plane **REST API is the single source of truth.** Provisioning is exposed in **three thin, composable surfaces — not MCP-only** — because the provisioning op set is small (~6 ops) and stable, but the *journey* is a multi-step playbook:

1. **A `wchats` CLI** wrapping the REST API — the most token-lean surface (~80 tokens vs. full schemas) and the natural **code-execution** path: a developer-agent scripts the multi-step flow and intermediate data never enters the model context. Doubles as the human/CI ergonomic path. **Primary lean path.**
2. **A W Chats provisioning Skill** encoding the end-to-end playbook (`create_agent → ingest → wait → eval → pre-deploy checklist → approve`, plus the gotchas) — ~100 tokens at scan, full instructions on demand. The "judgment/procedure" layer that sits on top.
3. **A thin, curated MCP server** exposing only the ~6 control operations (`create_agent`, `ingest_documents`, `check_deployment_status`, `get_eval_summary`, `approve_deployment`, `get_audit_log`) — for broad dev-tool compatibility (Claude Code, Cursor, OpenClaw all speak MCP). Curated and deferral-friendly; **never a kitchen-sink wrapper** of the whole API.

**Sequencing:** ship the **CLI + Skill first** (cheap, token-efficient, covers code-execution agents), then add the **thin MCP server** for ecosystem reach (honoring the PRD's "MCP-provisionable" promise). This reframes the PRD's "MCP provisioning" from *MCP-primary* to **REST-core / CLI+Skill-lean / MCP-for-reach.** At a 6-tool surface the bloat critique does not bite — but anchoring on the REST API keeps us free to add Tool Search / code-execution as the surface grows.

---

## Consequences

**Positive**
- The L1–L3 security boundary is preserved exactly because the agent never sees a raw provider/control tool.
- Context-efficient by construction: narrow tools for consumption, progressive disclosure for provisioning.
- Broad agent-ecosystem compatibility (MCP) without paying the bloat tax (CLI + Skill carry the load).
- Aligns with Anthropic's code-execution guidance and the "skills compose on top of MCP" consensus.

**Negative / tradeoffs**
- Three provisioning surfaces over one API is more to maintain — mitigated by all three wrapping a single REST API; the CLI and Skill are individually cheap.
- The MCP server is still net-new work for v1.2; its value is ecosystem reach, not efficiency.
- Code-execution provisioning (if pursued) needs a sandboxed execution environment — deferred until/if the surface grows enough to justify it.

---

## Links
- `Post-M10-PRD.md` §5 (v1.2 agent-native protocol surface — MCP/A2A) and §4 (v1.1 transactional, the consumption side).
- `.planning/ROADMAP.md` Phase 16 (integration adapters — Part A applies now).
- `docs/adr/0001-cloud-native-cutover.md` (hosting; unrelated but same ADR series).
- Research: [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) · [llamaindex — Skills vs MCP](https://www.llamaindex.ai/blog/skills-vs-mcp-tools-for-agents-when-to-use-what) · [apideck — MCP context window](https://www.apideck.com/blog/mcp-server-eating-context-window-cli-alternative) · [Stripe Agent Toolkit / MCP](https://docs.stripe.com/mcp)
</content>
