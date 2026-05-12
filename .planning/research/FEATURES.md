# Feature Research

**Domain:** Production RAG Platform / Customer Service Agent
**Researched:** 2026-05-12
**Confidence:** MEDIUM-HIGH (competitor features from multiple verified sources; some SMB UX claims from single sources)

---

## Feature Landscape

### Table Stakes (Users and Owners Expect These)

Features whose absence makes the product feel incomplete or untrustworthy. Competitors already ship these.

| Feature | Why Expected | Complexity | PRD Status | Notes |
|---------|--------------|------------|------------|-------|
| Document ingestion (PDF, URLs, text) | Every competitor from Chatbase to Intercom Fin ships this; it is the entry point | Low-Med | M2 — covered | Docling handles layout-aware parsing |
| Semantic (vector) retrieval | Baseline since 2023; users assume grounded answers | Med | M3 — covered | pgvector HNSW |
| Hybrid retrieval (vector + keyword) | Pure vector misses exact-match queries; all serious platforms combine both | Med | M3 — covered | BM25 via tsvector + RRF fusion |
| iframe / embeddable widget | Every no-code chatbot platform ships a copy-paste embed code | Low | M4 — covered | Preact, <20kb target |
| Live progress feedback during build | Non-technical users need to feel the system is working, not stalled | Low | M1 — covered | SSE job streaming |
| Grounded answers (no hallucination on known facts) | Intercom Fin's core promise; users now expect sourced answers, not AI confabulation | High | M5 — partially (Auditor) | Auditor validates groundedness per response |
| Source citation / attribution | IBM/CHI 2025 research: citations improve trust more than confidence scores alone | Low-Med | Not explicit in PRD | Currently implicit in retrieval trace; needs surfacing in widget |
| Human escalation path | 80% of users will not use chatbots without a clear escape to human help (Spurnow 2025) | Low | M4 — tool exists | `escalate_to_human` tool is present; escalation UX in widget is not specified |
| Context preservation on escalation | 78% trust drop when customers repeat themselves after handoff (Salesforce 2025) | Med | Not specified in PRD | Escalation summary auto-generated from conversation history |
| Basic analytics / conversation counts | Every platform from Tidio to Voiceflow ships a dashboard; owners expect to see usage | Low | M10 — planned (digest) | Owner-facing digest; real-time dashboard not specified until M10 |
| Theming / brand customization of widget | Every SMB chatbot platform lets you match brand colors, name, avatar | Low | M4 — partial | Widget config endpoint handles theming; depth of options not specified |
| Admin dashboard for agent management | Owners need a web UI they can use without a terminal | Med | Active — Next.js admin | Specified as polished requirement |
| Data re-upload / refresh | Owners update their price lists, policies; must be able to re-ingest | Low | M2 scope — manual re-upload | Out of scope for v1 (scheduled); manual re-upload is in |

---

### Differentiators (Competitive Advantage)

Features that set Veridian apart from Chatbase, Botpress, Intercom Fin, and Zendesk AI. These are either absent in competitors or exist only in enterprise tiers priced out of SMB reach.

| Feature | Value Proposition | Complexity | PRD Status | Audience |
|---------|-------------------|------------|------------|----------|
| Per-tenant Neon project isolation | True data isolation without shared-schema multi-tenancy risk; no cross-tenant data leakage possible at the DB layer | High | M1 — covered | Hiring managers: demonstrates production-grade multi-tenancy. SMB owners: "your data never mixes with anyone else's" |
| Neon branch-based eval isolation | Nightly evals run against a DB branch, never touching production traffic | High | M6 — covered | Hiring managers: architectural elegance. Owners: "we test your agent without slowing it down" |
| Pre-deployment red team gate | Three adversarial agents attempt prompt injection, data leakage, and hallucination-under-pressure before deployment | Very High | M7/M8 — covered | Hiring managers: demonstrates security-aware AI engineering. Owners: "we tried to break it before you went live" |
| Automated Ragas eval suite with scenario generation | Scenarios auto-generated from the tenant's domain at build time, then improved from production failures | Very High | M6 — covered | Hiring managers: production flywheel. Owners: "your agent gets tested every night" |
| Validation chain (Gatekeeper / Auditor / Strategist) | Every response has three structured LLM-as-judge assessments logged to Langfuse; persistent failures flag re-synthesis | High | M5 — covered | Hiring managers: demonstrates LLMOps discipline. Owners: "every answer is checked before it reaches your customers" |
| Plain-language deployment report with human gate | Owner sees a deployment recommendation and must acknowledge warnings individually; acknowledgments logged | Med | M8 — covered | Hiring managers: responsible AI gate. Owners: "you decide when you're ready to go live" |
| Retrieval strategy synthesis (per-tenant) | Strategist agent generates retrieval config from corpus shape analysis; not one-size-fits-all | Very High | M9 — covered | Hiring managers: agentic system design. Owners: "the platform learns from your specific data" |
| Continuous post-deployment monitoring with owner digest | Weekly metrics email: conversation counts, eval drift, red team findings, escalation rate | Med | M10 — covered | Owners: passive confidence. Hiring managers: production maintenance story |
| Structure-aware ingestion (Docling) | Tables, headers, captions preserved as semantic units, not shredded by fixed-length chunking | High | M2 — covered | Hiring managers: chunking quality is a known RAG failure mode; fixing it is a signal |
| HyDE-style metadata enrichment per chunk | Summary, keywords, hypothetical questions per chunk improve retrieval recall beyond raw text | Med | M2 — covered | Hiring managers: demonstrates retrieval quality thinking above naive embedding |
| Langfuse trace-level observability | Full trace per request: prompts, tool calls, retrieval steps, validation outputs, latency, cost | Med | M5 — covered | Hiring managers: LLMOps literacy. Owners: "we can see what the agent did and why" |
| Sub-20kb widget bundle | SMB websites are bloated; a lightweight embed avoids Core Web Vitals regressions | Med | M4 — covered | SMB owners: no website slowdown. Hiring managers: delivery discipline |
| Agentic tools (retrieve, lookup, escalate, clarify) | Agent picks the right retrieval path; structured data queried directly rather than embedded | High | M4 — covered | Hiring managers: proper tool-use design vs naive RAG. Owners: "it knows when to look up your order database" |

---

### Anti-Features (Commonly Requested, Often Problematic)

Features SMB owners frequently ask for that create engineering debt, scope bloat, or reliability problems disproportionate to their value at this stage.

| Anti-Feature | Why Requested | Why Problematic | Alternative in Veridian |
|--------------|---------------|-----------------|------------------------|
| Voice / phone channel | "My customers call, not type" — sounds natural | Real-time voice requires sub-300ms RAG (vector queries alone are 50–300ms round trip); architecture changes fundamentally; latency budget consumed by TTS/STT stack | Out of scope v1. Text-first proves the retrieval/eval story cleanly. Add voice in v2 with a streaming retrieval layer |
| Multi-language support | SMBs with multilingual customer bases | Embedding models are language-specific; multilingual embeddings underperform monolingual ones; eval scenarios and red team probes need translation; doubles QA surface | Out of scope v1. English-first is defensible. Language detection + graceful fallback message is a one-day addition when needed |
| Live chat (human agent inbox built in) | "I want to answer manually sometimes" | Turns a RAG platform into a help desk; competing with Intercom, Freshdesk, Zendesk on their strongest ground; operational complexity explodes (online status, routing, SLAs) | Escalate to existing tools. `escalate_to_human` generates a ticket or webhook; owner uses their existing inbox |
| CRM and ticketing integrations (Zendesk, HubSpot, Salesforce) | Owners want everything in one place | Each integration is a maintenance surface; auth flows, schema changes, rate limits. Chatbase users report hitting integration walls as their primary complaint | Webhook/HTTP action on escalation for v1. Owner configures their own Zapier or Make.com if needed |
| Custom model hosting / fine-tuning | "I want a model trained on my data" | Fine-tuning for customer service RAG consistently underperforms well-prompted frontier models with good retrieval; requires GPU infra, retraining pipelines, version management | Retrieval quality plus Voyage embeddings provides data-specific grounding without model hosting. Claude API is the reasoning layer |
| Confidence score visible to end users | "Show me how sure the bot is" | IBM/CHI 2025 research: raw confidence scores do not increase user trust and often decrease it when the score is meaningless to non-technical users; source citations work better | Show citations and source document names instead. Reserve confidence scores for internal logging (Langfuse) and eval dashboards |
| Real-time websocket chat (owner watching live) | "I want to see conversations happening" | WebSocket infrastructure on top of an already SSE-based stack adds complexity; owner watching live does not help them; they need async digests and alerts | Weekly digest email + alert-on-critical-finding covers the owner's actual need. Langfuse traces give engineers the live view |
| Scheduled data refresh / auto-crawl | "My website updates daily, re-index automatically" | Incremental re-embedding is a non-trivial correctness problem (stale chunk detection, delta identification, re-embedding costs); adds Celery beat jobs with failure modes that surface as silent quality degradation | Manual re-upload for v1. Flagged as M10 or post-v1. The PRD already defers this correctly |
| Multi-agent orchestration exposed to owner | "Let me build my own agent workflows" | Turns Veridian into a Botpress/Voiceflow competitor; the SMB owner doesn't want to build flows; the hiring manager wants to see a complete vertical product, not a platform | The agent soul (identity + role) is all the owner configures. The orchestration is opaque and handled by the system |
| White-label / reseller mode | Agency owners want to resell | Multi-tenancy within a tenant is a fundamentally different architecture; adds billing complexity; premature for v1 | Single-level tenancy for v1. Reseller mode is a commercial feature for post-M8 roadmap |
| Knowledge base UI editor (collaborative authoring) | "Let me edit what the bot knows" | Risks owner corrupting the embedding index by editing raw chunks; versioning and re-embedding on edit are non-trivial; Chatbase users hit this wall and migrate away | Re-upload source documents. The ingestion pipeline is the source of truth. If owners need a KB editor, that is a separate product surface |

---

## Feature Dependencies

```
M1: Auth + tenant model + Neon provisioning + SSE
  └─ M2: Ingestion pipeline (requires tenant DB to exist)
       └─ M3: Hybrid retrieval (requires embedded chunks)
            └─ M4: Reasoning engine + widget (requires retrieval)
                 ├─ M5: Validation chain (wraps M4 responses)
                 │    └─ M6: Eval system (consumes M5 validation signals as training data for scenario mining)
                 │         └─ M7: Red team (needs an agent to probe, needs eval framework for severity scoring)
                 │              └─ M8: Pre-deployment checklist (aggregates M6 + M7 results)
                 │                   └─ M9: Retrieval strategy synthesis (replaces hand-written M3 configs)
                 │                        └─ M10: Maintenance crons + observability polish (requires all prior layers)
                 └─ Source citations in widget (M4 enhancement, not a new milestone)
                 └─ Escalation UX in widget (M4 gap — see PRD Gap Analysis below)
```

Key dependency note: M4 (first hireable artifact) depends on M1–M3 being solid. M5–M8 are additive. M9 replaces a manual step in M3 without requiring M3 to be re-architected.

---

## MVP Definition

### Launch With (M1–M4)

What must exist for the public demo to be hireable and for an SMB owner to see the value:

- Signup, agent creation with "soul" configuration (name, identity, role)
- Document + URL ingestion with live progress stream
- Hybrid retrieval (vector + BM25 + RRF + Voyage rerank)
- Claude Agent SDK agent with retrieve, lookup, escalate, clarify tools
- Preact iframe widget (<20kb) with basic theming
- Source attribution visible in widget responses (citations to source document names)
- Escalation to human with conversation summary (escalation UX specified — see gap below)
- Admin dashboard showing agent status and basic conversation count
- Public demo site with a real ingested dataset

What M4 deliberately omits and why:
- Validation chain: async layer; adding it post-M4 does not require changing M4 architecture
- Eval system: requires production traffic first to be meaningful
- Red team: requires the agent to exist first
- Retrieval strategy synthesis: hand-written strategies are fine for demo

### Add After Validation (M5–M8)

What transforms the demo into a defensible production system:

- M5: Response validation chain (Gatekeeper, Auditor, Strategist) with Langfuse logging
- M6: Eval system with Ragas metrics, scenario generation, Celery beat schedule, eval dashboard
- M7: Red team (three adversarial agents), severity classification, deployment blocking on critical findings
- M8: Pre-deployment checklist orchestrator with human gate; full non-technical user journey validated end-to-end
- Escalation webhook / ticket creation when human handoff is triggered

### Future Consideration (M9–M10)

What makes the platform self-maintaining:

- M9: Retrieval strategy synthesis from corpus shape analysis
- M10: Weekly red team crons, monthly eval drift detection, owner digest email, alerting on metric regressions, data freshness monitoring
- Post-M10: Scheduled re-crawl of source URLs (currently deferred as manual re-upload)

---

## PRD Gap Analysis

Features the PRD does not explicitly address that production RAG platforms in 2026 typically need. These are not blockers for M4 but represent known gaps that will surface during M5–M10 or in commercial validation.

### Gap 1 — Source Citations in Widget Responses (MEDIUM priority, M4 enhancement)

**What it is:** Showing the user which document or section a response was drawn from, inline in the chat.

**Why it matters:** IBM/CHI 2025 research found citations improve user trust more than confidence scores or longer explanations. CustomGPT's primary differentiator is citation auditability. Intercom Fin grounds every answer and surfaces the source.

**PRD status:** The retrieval engine captures full trace (which chunks contributed), but the widget spec does not mention surfacing citations to the end user.

**Recommendation:** Add a citations panel or "Based on: [document name]" footer to widget responses before M4 ships. This is a frontend-only change once retrieval trace is logged. Complexity: Low.

---

### Gap 2 — Escalation UX Specification in Widget (HIGH priority, M4 gap)

**What it is:** The visual flow and data capture when `escalate_to_human` fires in the widget — does a form appear? Does the conversation summary get shown? How does the owner receive it?

**Why it matters:** 78% trust drop when customers must repeat context after handoff (Salesforce 2025). 80% of users require a visible, low-friction escape to a human. The `escalate_to_human` tool exists in the agent but the delivery mechanism is not specified.

**PRD status:** `escalate_to_human(reason, context)` tool is defined. The M4 widget spec and M8 full journey section do not describe what happens in the widget when this tool fires.

**Recommendation:** Before M8, define: (a) widget shows a summary and capture form when escalation fires, (b) a webhook or email sends conversation summary to the business owner. This is not a new system — it is a clarification of how the existing tool result surfaces. Complexity: Low-Med.

---

### Gap 3 — Corpus Coverage Monitoring (MEDIUM priority, M6/M10 candidate)

**What it is:** Detecting which parts of the ingested corpus are never retrieved in production queries — a proxy for "questions the agent cannot answer because the data isn't there."

**Why it matters:** Most RAG failures in production are coverage failures, not ranking failures. An owner adds a FAQ doc but the questions asked are about a topic not covered. Without coverage monitoring, the owner doesn't know what data to add.

**PRD status:** The pre-deployment checklist (M8) includes a "coverage analysis" item, but post-deployment coverage drift is not tracked.

**Recommendation:** Add coverage heatmap to M10 observability polish. Track which chunks are retrieved with frequency and flag chunks with zero hits as candidate deletions, and surface "topics with no matching documents" from escalation summaries. Complexity: Med.

---

### Gap 4 — Data Freshness / Staleness Signal for Owner (LOW priority, M10 candidate)

**What it is:** Warning the owner when their ingested documents are old (e.g., "your price list was last uploaded 6 months ago — consider refreshing it").

**Why it matters:** The RAG freshness paradox is a documented production failure mode. Owners forget to update their data; the agent confidently answers from stale information. Competitors don't address this; it is a genuine gap.

**PRD status:** Not mentioned. Manual re-upload is in scope for v1, but there is no prompt or reminder mechanism.

**Recommendation:** In M10, add a document freshness score to the weekly digest email: "Price list last updated 6 months ago." No automated re-crawl needed — just a visibility layer over existing metadata. Complexity: Low.

---

### Gap 5 — Conversation-Level Memory Within a Session (MEDIUM priority, M4 architecture decision)

**What it is:** The agent remembering earlier turns in a conversation when the user asks a follow-up question (e.g., "What about the refund policy for that?" referring to a product discussed two turns ago).

**Why it matters:** Without conversation memory, the agent treats every turn as independent, producing incoherent multi-turn conversations. All production customer service agents maintain session context. This is not optional for a real customer service UX.

**PRD status:** The `conversations` and `messages` tables are in the schema. The Agent SDK's tool-calling loop is session-aware. But the system prompt construction does not mention how prior messages are injected as context.

**Recommendation:** Confirm before M4 that the agent system prompt includes a sliding window of recent conversation turns. This is an SDK configuration decision, not a new system. Complexity: Low (if SDK handles it — verify in M4 planning).

---

### Gap 6 — Tenant Onboarding Cost Guard (LOW priority, M1/M2 candidate)

**What it is:** A hard cap or soft warning when a single ingestion job is projected to exceed a cost threshold (e.g., a corpus of 10,000 pages would cost $50+ in Voyage API calls).

**Why it matters:** The PRD targets <$5 per 200-page corpus. Owners can accidentally upload entire file shares. Without a guard, a single bad upload bankrupts the cost model.

**PRD status:** Not mentioned. The cost target is in the success metrics but no enforcement mechanism is described.

**Recommendation:** Add a pre-ingest estimate: "This upload (~500 pages) will cost approximately $X to process. Continue?" Show estimate before Celery chain starts. Complexity: Low.

---

### Gap 7 — Agent "Soul" Guardrails Template (LOW priority, M4 UX)

**What it is:** Helping non-technical owners write an effective system prompt (agent identity, do/do-not list, tone) without writing a blank text box.

**Why it matters:** Botpress and Voiceflow both discovered that blank-slate prompt authoring overwhelms non-technical users and produces poor agents. A template with examples ("Hi, I'm [name]. I help with [topic]. I never discuss competitor pricing.") dramatically improves first-run quality.

**PRD status:** The "soul" concept is defined (name, identity, role) but the UI spec does not describe how this is elicited from the owner.

**Recommendation:** Add a structured soul editor with three fields (agent name, role description, do-not-discuss list) plus a preview of the assembled system prompt. Optionally generate a draft from the uploaded documents. Complexity: Low-Med.

---

### Gap 8 — Audit Log / Conversation Export (LOW priority, post-M8)

**What it is:** Owner can download their conversation history or view individual sessions in the admin UI.

**Why it matters:** GDPR right-to-access requires being able to provide conversation history if a customer asks. SMB owners in regulated sectors (healthcare, legal) need this. Even non-regulated owners want to review what their agent is saying.

**PRD status:** Conversations and messages are stored in the per-tenant DB. No export or owner-facing conversation viewer is mentioned.

**Recommendation:** Add a conversation browser to the admin dashboard (M8 or M10 scope). Export as CSV. Complexity: Low.

---

## Hiring Manager vs. Business Owner Feature Map

| Feature | Impresses Hiring Manager | Impresses SMB Owner |
|---------|--------------------------|---------------------|
| Per-tenant Neon project isolation | Strong: production-grade multi-tenancy, not schema tricks | Weak: invisible to owner |
| Neon branch eval isolation | Strong: demonstrates Neon's branching as an architectural choice, not just storage | Moderate: "tests don't affect live customers" |
| Validation chain (3 LLM judges) | Strong: LLMOps discipline, structured outputs, async-after-stream pattern | Weak: invisible, but powers "every answer is checked" message |
| Ragas eval with scenario generation + flywheel | Very strong: production eval loop is the hardest RAG engineering problem | Moderate: "your agent gets tested every night" |
| Red team (3 adversarial agents, Claude Agent SDK) | Very strong: AI safety engineering depth, iterative attack patterns | Moderate: "we tried to break it before you went live" |
| Pre-deployment human gate with acknowledgment logging | Strong: responsible AI design | Strong: owners feel in control |
| Langfuse trace observability | Strong: LLMOps tool literacy, cost/latency discipline | Weak: technical surface |
| Hybrid retrieval + RRF + Voyage rerank | Strong: retrieval quality over naive embedding | Weak: invisible |
| Structure-aware chunking (Docling) | Strong: chunking is a known failure mode; addressing it is a signal | Weak: invisible |
| Source citations in widget | Moderate: expected in 2026 | Strong: "it tells you where it got the answer" |
| Escalation with conversation summary | Moderate: expected pattern | Very strong: solves the "repeat yourself" frustration |
| <20kb widget | Moderate: delivery discipline | Strong: "it doesn't slow down my website" |
| Weekly digest email | Weak: trivial engineering | Very strong: "I know what's happening without logging in" |
| Plain-language deployment report | Moderate: demonstrates UX thinking for non-technical users | Very strong: owner's decision gate |

---

## Sources

- [The Best Pre-Built Enterprise RAG Platforms in 2025 — Firecrawl](https://www.firecrawl.dev/blog/best-enterprise-rag-platforms-2025)
- [Enterprise RAG Platforms Comparison 2026 — Atlan](https://atlan.com/know/enterprise-rag-platforms-comparison/)
- [10 RAG Shifts Redefining Production AI in 2026 — Microsoft Azure / Medium](https://medium.com/microsoftazure/10-rag-shifts-redefining-production-ai-in-2026-7acbdd66076c)
- [AI Customer Service Challenges and Solutions — Decagon](https://decagon.ai/blog/ai-chatbot-challenges)
- [Production RAG in 2025: Evaluation, CI/CD, Observability — Dextralabs](https://dextralabs.com/blog/production-rag-in-2025-evaluation-cicd-observability/)
- [The Architect's Guide to Production RAG — Ragie](https://www.ragie.ai/blog/the-architects-guide-to-production-rag-navigating-challenges-and-building-scalable-ai)
- [The RAG Freshness Paradox — RAGAboutIt](https://ragaboutit.com/the-rag-freshness-paradox-why-your-enterprise-agents-are-making-decisions-on-yesterdays-data/)
- [The Fin AI Engine — Intercom](https://fin.ai/ai-engine)
- [When Chatbots Go Wrong: The New Risk Landscape — EdgeTier](https://www.edgetier.com/chatbots-the-new-risk-in-ai-customer-service/)
- [Trust Me on This: A User Study of Trustworthiness for RAG Responses — arXiv](https://arxiv.org/abs/2601.14460)
- [Multi-Tenant RAG With One Neon Project Per User — Neon](https://neon.com/blog/multi-tenant-rag)
- [Zendesk Acquires Forethought for Self-Learning AI Agents — CMSWire](https://www.cmswire.com/customer-experience/zendesk-acquires-forethought-for-self-learning-ai-agents/)
- [Chatbot to Human Handoff: Complete Guide — Spurnow](https://www.spurnow.com/en/blogs/chatbot-to-human-handoff)
- [Escalation Design: Why AI Fails at the Handoff — Bucher + Suter](https://www.bucher-suter.com/escalation-design-why-ai-fails-at-the-handoff-not-the-automation/)
- [Chatbase vs Tidio — Chatbase Blog](https://www.chatbase.co/blog/tidio)
- [5 AI Portfolio Projects That Actually Get You Hired in 2026 — DEV Community](https://dev.to/klement_gunndu/5-ai-portfolio-projects-that-actually-get-you-hired-in-2026-5bpl)
- [OWASP Top 10 for LLMs 2025 — DeepTeam](https://www.trydeepteam.com/docs/frameworks-owasp-top-10-for-llms)
- [RAG Evaluation: A Complete Guide for 2025 — Maxim](https://www.getmaxim.ai/articles/rag-evaluation-a-complete-guide-for-2025/)
