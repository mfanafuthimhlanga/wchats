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

The control-plane **REST API is the single source of truth.** Provisioning is exposed in **three thin, composable surfaces — not MCP-only** — because the operations are typed and governed, but the *journey* (and several individual operations) are multi-step.

**Representative operation set** — curated, one-to-one with existing REST endpoints; richer than the PRD's 6-op sketch, but still not the whole API. Grouped by journey stage:
- *Create / configure:* `create_agent` (name + role + soul), `update_agent_soul`, `set_widget_config`
- *Ingest:* `ingest_documents` (file uploads — PDF/PNG/JPG/MD) **and** `ingest_urls` (URL list) — the knowledge base spans both, so these are **separate, explicit verbs**; a single `ingest_documents` that silently also took URLs would misrepresent the capability. [future: `ingest_structured` for CSV/exports, ADV-02]
- *Build / evaluate:* `trigger_eval`, `get_eval_summary`, `trigger_red_team`, `get_red_team_summary`
- *Deploy:* `run_predeploy_checklist`, `check_deployment_status`, `approve_deployment`, `get_embed_snippet`
- *Observe:* `get_audit_log`

The three surfaces:
1. **A `wchats` CLI** over the REST API — most token-lean (~80 tokens vs. full schemas) and the natural **code-execution** path (the agent scripts the multi-step flow; intermediate data never enters model context). Also the human/CI ergonomic path.
2. **A W Chats provisioning Skill** encoding the end-to-end playbook + every sub-step (see below) — ~100 tokens at scan, full body on demand. The "judgment/procedure" layer that sits on top.
3. **A thin, curated MCP server** exposing the operation set above (not a kitchen-sink wrapper) — for broad dev-tool compatibility (Claude Code, Cursor, OpenClaw all speak MCP). Curated and deferral-friendly.

**Rich inputs and sub-steps live in the Skill — not in tool sprawl or bloated descriptions.** The operations are not atomic one-liners: `create_agent` needs a name, a role, and a structured **soul** (voice + do-list + donot-list — the same `AgentSoulUpdate` shape the admin UI uses); ingest spans documents and URLs; deploy is a sequence (checklist → acknowledge each warning → approve). Two rules keep this clean:
- **Tools stay typed and mirror the REST API.** `create_agent(name, role, soul:{voice, do_list, donot_list})` carries the full field contract in its JSON schema (or, equivalently, `create_agent(name, role)` + `update_agent_soul(...)` as two typed calls). The model fills typed fields; it never guesses shape from prose.
- **The Skill carries the procedure and the judgment** — required fields, the right order (create → soul → ingest → eval → checklist → approve), *how to compose a good* role/voice/do/donot, when to poll an async job, how to read an eval summary before approving. This is the "Skills = the playbook on top of MCP" split: the nuance belongs in the Skill (loaded on demand), **never crammed into tool descriptions** (which would re-introduce the context bloat we are avoiding).

So the three surfaces are not redundant: **MCP/CLI = typed access (the *what*); Skill = the playbook incl. every sub-step and required field (the *how*).**

**Sequencing:** all three surfaces ship **together as part of v1.2** (owner decision, 2026-06-29) — not phased. The framing is **REST-core / CLI+Skill-lean / MCP-for-reach**, not MCP-only. At this curated (~14 typed-op) surface the bloat critique does not bite; anchoring on the REST API keeps Tool Search / code-execution available if it grows.

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
