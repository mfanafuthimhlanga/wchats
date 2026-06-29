# Veridian

## What This Is

Veridian is a production RAG platform where non-technical small business owners drop in their business data and get a customer service agent — provisioned on per-tenant Neon databases, deployable to any website via iframe, and continuously evaluated and red-teamed without operator intervention. The platform is open source, built by Mzansi Agentive Pty Ltd, and serves dual purpose: a portfolio artifact targeting senior AI/ML engineering roles, and the foundation for a commercial product serving the SMB market.

## Core Value

A non-technical business owner completes the full journey — signup → ingest → deploy — and gets a customer service agent that is actually defensible: grounded, evaluated, and red-teamed before it goes live.

## Current Milestone: v1.1 — Transactional Capability

**Goal:** Move deployed W Chats agents from informational (answering) to transactional (acting on the customer's behalf — place orders, issue refunds, book slots), with eight-layer security (L1–L3 / L5 / L6 + partial L4) as a first-class, non-deferred part of the milestone. Source: `Post-M10-PRD.md` §4.

**Target features:**
- Typed transactional tool contract (`place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record`, `confirm_action`) with idempotency keys
- Actor validator (L3): a pre-mutation Haiku gate in the Agent SDK tool loop
- Per-skill capability envelopes (L2) + enforcement middleware + admin UI
- Integration adapters (Shopify, WooCommerce, Stripe, Calendly) with encrypted, server-held credentials
- Customer identity verification (email/SMS OTP, per-skill, server-enforced)
- Financial blast-radius gate, tool-call audit log, transaction-specific red-team probes

**Phases:** 14–19 (continue numbering from v1.0). **Status:** roadmap + requirements defined (43 reqs); building in parallel while v1.0's Phase 13 production deploy is parked on a domain purchase (Phase 13 is 7/11 code plans done, resumable).

> **Prior milestone (v1.0):** M1–M11 + Phase 12 (live demo) complete; Phase 13 (production AWS hosting) paused at its live-deploy gates pending a domain + Bedrock account activation. v1.1 does not depend on it.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Non-technical user completes signup → ingest → deploy without touching code
- [ ] Per-tenant Neon project provisioned automatically on agent creation
- [ ] Structure-aware document ingestion (Docling + Chonkie) with metadata enrichment
- [ ] Hybrid retrieval: pgvector + BM25 + RRF fusion + Voyage reranking
- [ ] Customer service agent via Claude Agent SDK with retrieve, lookup, escalate, clarify tools
- [ ] Validation chain (Gatekeeper + Auditor + Strategist) wrapping every response
- [ ] Eval system: Ragas metrics, scenario generation, Celery beat schedule
- [ ] Red team system: three agents (prompt injection, data leakage, hallucination-under-pressure)
- [ ] Pre-deployment checklist with orchestrator agent and human validation gate
- [ ] iframe widget deliverable (Preact, <20kb gzipped)
- [ ] Polished admin UI (Next.js) for the full owner journey
- [ ] Live public demo site with a real deployed agent (M4 milestone)
- [ ] Architecture blog post published alongside M4 demo

### Out of Scope

- Voice channel — text only for v1
- Multi-language — English only
- Custom model hosting — Claude only on the agent side
- Mobile-native SDKs — iframe-only delivery
- General-purpose vector DB — Neon-only architectural story
- OAuth login — email/password sufficient for v1
- Real-time chat between users
- Owner-side scheduled data refresh — manual re-upload only for v1

## Context

**Author:** Mfanafuthi Mhlanga (Bantuson) — Mzansi Agentive Pty Ltd. Solo developer.

**Motivation:** Two converged goals. Primary: land a senior AI/ML engineering role (actively job hunting, targeting AI-first startups, big tech AI teams, and enterprise AI/ML teams). Secondary: build a commercial product serving SMBs who lack the engineering depth to ship defensible AI agents.

**Portfolio signal:** M4 is explicitly described as "the first hireable artifact" in the PRD. Everything from M5 onward strengthens it. M1–M4 alone is a portfolio piece. The public demo + architecture blog post is the delivery package for the hiring market.

**Timeline pressure:** Actively job hunting now. M4 must be reachable in weeks, not months. The architecture is ambitious — scope decisions must weigh speed to M4 against technical depth.

**Architectural philosophy:** Programmatic core, agentic edges. Deterministic code for anything testable and cheap to run; Claude agents for open-ended judgment. Agents are inside the loop, not the loop.

**Milestones defined in PRD (M1–M10):**
- M1: Control plane skeleton (FastAPI, auth, Neon provisioning, SSE)
- M2: Ingestion pipeline (Docling, Chonkie, metadata enrichment, Voyage embeddings)
- M3: Hybrid retrieval (pgvector + BM25 + RRF + Voyage rerank)
- M4: Reasoning engine + widget v0 ← **first hireable artifact**
- M5: Validation chain (Gatekeeper, Auditor, Strategist)
- M6: Eval system (Ragas, scenario generation, Celery beat)
- M7: Red team (three Claude Agent SDK agents)
- M8: Pre-deployment checklist + human validation gate
- M9: Retrieval strategy synthesis (strategist agent)
- M10: Maintenance crons + observability polish

## Constraints

- **Stack**: FastAPI, Pydantic, Celery, Redis, Alembic, Neon (pgvector), Claude Agent SDK, Claude API, Docling, Chonkie, Voyage (embed + rerank), Cohere Rerank fallback, Ragas, PyRIT, Langfuse, Next.js (admin UI), Preact (widget) — decided in PRD, not up for revision
- **Solo developer**: Architecture must be buildable by one person; scope must be honest about that
- **Timeline**: M4 is the milestone that matters for hiring. It must come fast.
- **Polished UI required**: The admin UI is part of the demo; it needs to look production-ready, not scaffolding
- **Open source**: Code is public from day one; architecture decisions are portfolio signals
- **No Docker**: Development machine has 4 GB RAM — Docker requires 6 GB+ minimum and was abandoned during M2. All services run as local processes. Demo scripts and verification steps must target local runs only. Never generate `docker-compose` or container-based instructions.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Per-tenant Neon projects (not schemas) | True isolation; Neon branching for eval isolation is a differentiator | — Pending |
| Celery + Redis for all long-running work | Idempotent tasks, SSE status streaming, acks_late — no work in request thread | — Pending |
| Two Celery queues (pipeline / runtime) | Prevent nightly red team runs from starving customer onboarding | — Pending |
| Claude Agent SDK for customer agents + red teamers | Iterative tool-calling loop required; single-shot won't capture depth | — Pending |
| Claude API (Haiku) for judges + validators | Cost discipline on the hot path; validators run async after response streamed | — Pending |
| Preact for widget | <20kb gzipped target; SMB websites are already bloated | — Pending |
| Retrieval strategy stored as JSON config | Generated once at build time; runtime is pure code reading config | — Pending |
| Portfolio-first, commercial-second | Active hiring need; M4 must land before commercial strategy is relevant | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-12 after initialization*
*Updated 2026-06-29 — added Current Milestone v1.1 (Transactional Capability, phases 14–19) per Post-M10-PRD §4; v1.0 Phase 13 production deploy paused on domain purchase.*
