# Veridian — Project Guide

## Project Context

See `.planning/PROJECT.md` for full context, requirements, and key decisions.

**Core value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.

**Current milestone:** M3 — Hybrid Retrieval (M1 ✓, M2 ✓)
**State:** `.planning/STATE.md`  
**Roadmap:** `.planning/ROADMAP.md`

## GSD Workflow

This project uses the GSD (Get Shit Done) planning discipline.

### Phase lifecycle

```
/gsd-discuss-phase N   →  gather context, clarify approach
/gsd-plan-phase N      →  create PLAN.md with tasks
/gsd-execute-phase N   →  execute plans with atomic commits
/gsd-verify-work N     →  validate requirements were met
```

### Rules

1. **Never skip milestones.** Each milestone's success criteria must be verified before the next begins. See `.planning/ROADMAP.md` for the current active milestone.
2. **No work outside the active phase.** If a future concern is discovered, log it in `.planning/STATE.md` and stay in the current phase.
3. **Atomic commits.** Every plan step commits its own artifact. Never batch multiple steps into one commit.
4. **Connection strings never in Celery task args.** Tasks receive `tenant_id`; they fetch and decrypt from the control DB at runtime.
5. **`acks_late=True` AND idempotency.** These are separate requirements — both are always required on every Celery task.
6. **Langfuse v4 API only.** Do not use pre-v4 Langfuse patterns — `start_span()`/`start_generation()` are gone.
7. **Ragas 0.4.x API only.** Import paths and metric return types changed from 0.3.x — use `ragas.metrics.collections`, `MetricResult`, and `reference` (not `ground_truths`).
8. **No pg_search / pgbm25.** Deprecated on Neon March 2026. BM25 uses native `tsvector` + `ts_rank_cd` only.
9. **No Docker.** Development runs locally on a 4 GB RAM machine. Docker requires 6 GB+ minimum and was abandoned during M2. All demo scripts, verification steps, and run instructions target local processes: Redis (`redis-server`), PostgreSQL (local install), FastAPI (`uvicorn`), Celery worker (`celery -A app.worker.celery_app worker`). Never suggest `docker-compose up` or container-based workflows.

## Stack

```
Backend:     FastAPI + Pydantic + Celery + Redis + Alembic
Data:        Neon (control DB + per-tenant), pgvector (HNSW)
Agents:      claude-agent-sdk 0.1.81 (customer agents, strategists, red teamers)
             Claude API direct / Haiku (judges, Gatekeeper, Auditor, Strategist)
Ingestion:   Docling (layout-aware), Chonkie ≥1.6.5 (structure-aware)
Embeddings:  voyageai (embed + rerank), cohere fallback
Evals:       ragas 0.4.x + custom harness
Red team:    pyrit + custom Claude probes
Observ:      langfuse 4.x
Admin UI:    Next.js
Widget:      Preact (<20kb gzipped)
```

## Architecture principles

- **FastAPI never does work inline.** All long-running operations go to Celery.
- **Two Celery queues always present:** `pipeline` (ingestion/build) and `runtime` (evals, agent calls).
- **SSE via Redis pub/sub.** Celery tasks publish to `job_events:{job_id}`; SSE endpoint subscribes. Persist events to `job_events` table for late-join replay.
- **Per-tenant Neon projects** (not schema-per-tenant) — required for Neon branching in evals.
- **Claude Agent SDK is stateless.** `system_prompt` is passed in `ClaudeAgentOptions` at every call. Session continuity uses `resume=session_id`.
- **Programmatic core, agentic edges.** Deterministic code for anything testable; Claude agents for open-ended judgment only.

## M1–M4 priority

M4 is the **first hireable artifact**. Scope decisions prioritize speed to M4. Everything from M5 onward strengthens the portfolio piece.
