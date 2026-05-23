# Phase 6: Eval System — Context

**Gathered:** 2026-05-23
**Status:** Ready for planning
**Source:** PRD Express Path (prd.md)

<domain>
## Phase Boundary

Phase 6 delivers the automated nightly eval system for Veridian: a Ragas 0.4.x harness measuring four metrics per scenario, Celery beat scheduling per deployed agent, a scenario generator that builds the eval suite from tenant domain knowledge at build time, production conversation mining that turns flagged responses into new scenarios, Neon branch-per-eval-run isolation, and a `verified_qa` promotion side-effect where passing scenarios are written to the tenant's verified knowledge layer.

The admin UI gains an eval dashboard (Next.js) showing per-metric pass rates over time and individual scenario pass/fail detail. The `retrieval_service.py` is extended to consult `verified_qa` before hybrid search so the demo can show a cache hit skipping vector search entirely.

**What this phase delivers:**
- Tenant DB 0005: `verified_qa` table + `eval_scenarios` table
- `eval_service.py`: Ragas 0.4.x harness (four metrics)
- `scenario_service.py`: scenario generator (Claude API) + production mining
- Neon branch management methods (`neon_service.py`)
- `run_eval_suite` Celery task + Celery beat schedule
- `verified_qa` retrieval-time consultation in `retrieval_service.py`
- FastAPI routes: `GET /agents/{id}/eval-runs` + `GET /agents/{id}/eval-runs/{run_id}/results`
- Next.js eval dashboard page: `/agents/[id]/evals`
- `scripts/demo_m6.sh` + guarded E2E test

**What this phase does NOT build:**
- Red team agents (M7)
- Pre-deployment checklist orchestrator (M8)
- Owner approval UI for `verified_qa_candidates` (M8)
- Retrieval strategy synthesis (M9)
- Weekly digest email and Langfuse dashboards (M10)
- Per-tenant eval configuration UI (deferred — global defaults used)

</domain>

<decisions>
## Implementation Decisions

### Ragas API (CLAUDE.md constraint — non-negotiable)
- **D-01 [LOCKED]** Ragas 0.4.x API only — import path: `from ragas.metrics.collections import Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall`; do NOT use `ragas.metrics` (0.3.x path — removed)
- **D-02 [LOCKED]** Dataset field for reference answers is `reference` (not `ground_truths` — renamed in 0.4.x)
- **D-03 [LOCKED]** Metric return type is `MetricResult` — access score via `.score` attribute
- **D-04 [LOCKED]** Four metrics measured per scenario: Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall

### Database Schema — Tenant DB
- **D-05 [LOCKED]** Tenant DB migration 0005 adds `verified_qa` table with the exact schema from PRD Layer 4:
  - `id UUID PK`, `question TEXT`, `question_vector VECTOR(1024)`, `answer TEXT`, `citations JSONB`, `source TEXT CHECK ('sandbox_test','production_promotion','human_authored')`, `faithfulness NUMERIC`, `relevance NUMERIC`, `promoted_at TIMESTAMPTZ`, `promoted_by TEXT`, `last_used_at TIMESTAMPTZ`, `use_count INT DEFAULT 0`, `invalidated_at TIMESTAMPTZ`
  - HNSW index on `question_vector vector_cosine_ops`
- **D-06 [LOCKED]** Tenant DB migration 0005 also adds `eval_scenarios` table:
  - `id UUID PK`, `source TEXT CHECK ('generated','mined')`, `question TEXT NOT NULL`, `reference_answer TEXT NOT NULL`, `retrieved_contexts JSONB NOT NULL DEFAULT '[]'`, `scenario_category TEXT`, `created_at TIMESTAMPTZ`, `last_run_at TIMESTAMPTZ`, `run_count INT DEFAULT 0`
- **D-07 [LOCKED]** `eval_runs` and `eval_results` tables already exist in tenant DB (0001 migration); M6 populates them
- **D-08** Control DB: no new migration needed — Celery beat covers all agents with `status='ready'`; agent `eval_enabled` can be inferred from status (defer per-agent toggle to M8)

### Eval Harness
- **D-09 [LOCKED]** Eval runs on the `runtime` Celery queue (not pipeline) — eval is a recurring operational task
- **D-10 [LOCKED]** Each eval run creates a Neon branch before executing, deletes it after (in `finally` block) — branch name: `eval-{run_id}`
- **D-11 [LOCKED]** Results written to `eval_runs` (status, started_at, finished_at) and `eval_results` (scenario_id, metric, score, detail) in the tenant DB

### Scenario Generation
- **D-12 [LOCKED]** Scenario generator uses Claude API direct (not Agent SDK) — Haiku model for cost; single call per batch of N chunks
- **D-13 [LOCKED]** Generated scenarios stored in `eval_scenarios` with `source='generated'`
- **D-14 [LOCKED]** Trigger: `generate_eval_suite` Celery task dispatched as part of the PRD §6 Layer 2 agent-creation chain (after `build_reasoning_engine`, before `run_sandbox_evals`); M6 implements this task

### Production Conversation Mining
- **D-15 [LOCKED]** Mining scans `messages` joined to validation event data (job_events where event_type IN ('gatekeeper.complete','auditor.complete') AND verdict IN ('fail','ungrounded','partial'))
- **D-16 [LOCKED]** Mined scenarios stored in `eval_scenarios` with `source='mined'`; mining runs as a separate Celery task (`mine_eval_scenarios`) triggered by Celery beat alongside the nightly eval

### Neon Branch Management
- **D-17 [LOCKED]** `neon_service.py` gains `create_branch(project_id, branch_name) -> str` (returns connection string for branch) and `delete_branch(project_id, branch_id) -> None`; Neon REST API: `POST /projects/{project_id}/branches` / `DELETE /projects/{project_id}/branches/{branch_id}`
- **D-18 [LOCKED]** Branch connection string used ONLY within the eval run; never stored in control DB; passed as local variable inside the Celery task

### Celery Beat Schedule
- **D-19 [LOCKED]** `celery_app.py` gains `beat_schedule` config: one entry `eval-nightly` running `run_eval_suite` at `02:00 UTC` daily; `beat_schedule` uses `crontab(hour=2, minute=0)`
- **D-20 [LOCKED]** `run_eval_suite` is a Celery task (`acks_late=True`, idempotency guard) on the `runtime` queue; receives `agent_id: str` (not connection string per CLAUDE.md); fetches all ready agents and dispatches per-agent

### verified_qa Promotion
- **D-21 [LOCKED]** Threshold: `faithfulness_score >= EVAL_FAITHFULNESS_THRESHOLD` AND `answer_relevancy_score >= EVAL_RELEVANCY_THRESHOLD` (both default 0.90; use existing `VERIFIED_QA_CONFIDENCE_THRESHOLD` for now — M9 adds per-tenant config)
- **D-22 [LOCKED]** Promoted rows written to `verified_qa` with `source = 'sandbox_test'`; `promoted_by = 'system'`
- **D-23 [LOCKED]** `question_vector` populated via Voyage embedding of the `question` text at promotion time

### verified_qa Retrieval-Time Consultation (PRD Layer 5)
- **D-24 [LOCKED]** `retrieval_service.py` gains a `verified_qa_lookup(query_vector, tenant_conn, threshold) -> Optional[dict]` step BEFORE hybrid search
- **D-25 [LOCKED]** Similarity check: cosine similarity ≥ `VERIFIED_QA_HIT_THRESHOLD` (default 0.93, from PRD; add to Settings)
- **D-26 [LOCKED]** On hit: return cached answer + citations; update `last_used_at` and increment `use_count`; skip vector + BM25 entirely
- **D-27 [LOCKED]** On miss: fall through to existing hybrid search path (no change to hybrid path)

### Settings Additions
- **D-28 [LOCKED]** Add to `Settings` (config.py):
  - `EVAL_FAITHFULNESS_THRESHOLD: float = 0.90`
  - `EVAL_RELEVANCY_THRESHOLD: float = 0.90`
  - `VERIFIED_QA_HIT_THRESHOLD: float = 0.93`

### Admin UI Dashboard
- **D-29 [LOCKED]** New Next.js page `/agents/[id]/evals` — two tabs: "Pass Rates" (per-metric time-series chart) + "Scenarios" (individual pass/fail grid)
- **D-30 [LOCKED]** New FastAPI routes: `GET /agents/{id}/eval-runs` (list of runs with aggregate scores) and `GET /agents/{id}/eval-runs/{run_id}/results` (per-scenario results)
- **D-31** Chart library: use Recharts (already available in Next.js ecosystem, lightweight)

### Demo
- **D-32 [LOCKED]** `scripts/demo_m6.sh` — local processes only (no Docker); triggers eval run, polls for completion, inspects `verified_qa` entries populated, runs a widget query that hits the cache, shows trace skipping vector search

### Claude's Discretion
- Exact Ragas 0.4.x dataset construction (whether to use `datasets.Dataset.from_list()` or pandas DataFrame → `EvaluationDataset`)
- Number of scenarios generated per tenant at build time (recommended: 20-30 based on corpus size)
- Whether `eval_scenarios` table needs a `tags` JSONB column for category filtering in the dashboard
- Batch size for scenario generation (recommended: generate N questions per N chunks, batch up to 50)
- Whether to implement `mine_eval_scenarios` as a separate beat entry or as a step inside `run_eval_suite`
- Exact Neon branch creation polling interval (recommended: 5s, max 60s)
- Whether the eval dashboard uses server-side rendering or client-side fetch from the FastAPI API

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project guidelines
- `CLAUDE.md` — stack, Ragas 0.4.x constraint, no Docker constraint, Celery queue names (`pipeline`/`runtime`), connection string rules, Langfuse v4 constraint

### Phase 5 patterns (what this phase extends)
- `.planning/phases/05-validation-chain/05-CONTEXT.md` — validator architecture, Langfuse patterns, verified_qa_candidates design (D-18 through D-21)
- `.planning/phases/05-validation-chain/05-02-PLAN.md` — Haiku judge call patterns (validation_service.py), Langfuse logging helper
- `.planning/phases/05-validation-chain/05-03-PLAN.md` — validators.py Celery task patterns (acks_late, idempotency, runtime queue)

### Phase 4 patterns
- `.planning/phases/04-reasoning-engine-widget/04-07-PLAN.md` — existing eval harness (judge.py, run_evals.py, 20 scenario files) — M6 extends this, does not replace it
- `.planning/phases/04-reasoning-engine-widget/04-CONTEXT.md` — Celery task patterns, SSE patterns

### Existing code
- `apps/api/app/worker/celery_app.py` — Celery app config; M6 adds `beat_schedule` + `include` entries for eval tasks
- `apps/api/app/core/config.py` — Settings; M6 adds `EVAL_FAITHFULNESS_THRESHOLD`, `EVAL_RELEVANCY_THRESHOLD`, `VERIFIED_QA_HIT_THRESHOLD`
- `apps/api/app/services/retrieval_service.py` — M6 extends with `verified_qa_lookup` before hybrid search
- `apps/api/app/services/neon.py` — M6 adds `create_branch` and `delete_branch` methods
- `apps/api/alembic_tenant/versions/0004_verified_qa_candidates.py` — prior migration pattern for tenant DB; 0005 follows same pattern

### Database
- `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py` — original tenant schema; `eval_runs` and `eval_results` tables already exist here
- `apps/api/alembic_tenant/versions/0004_verified_qa_candidates.py` — most recent tenant migration to follow

### Requirements
- `.planning/REQUIREMENTS.md` — EVL-01 through EVL-08 (Phase 6 requirements)

### Architecture
- `prd.md` §6 Layer 4 — Verified Knowledge Layer specification (verified_qa schema, promotion paths, invalidation)
- `prd.md` §6 Layer 5 — Retrieval engine verified_qa lookup spec (cosine threshold 0.93, cache-hit path)
- `prd.md` §6 Layer 8 — Eval system specification (harness, judges, Ragas metrics, scenario sources)
- `prd.md` §9 M6 — Milestone scope and end state

</canonical_refs>

<specifics>
## Specific Ideas

### From prd.md Layer 8 (Eval system)
> "The harness is code. Celery beat schedules runs; the harness executes test scenarios against the deployed agent and captures full traces."
> "Judges are single-shot Claude calls with structured outputs, implementing Ragas-style metrics: Faithfulness, Answer relevance, Context precision, Context recall"
> "Test scenario sources: 1. Generated at build time by a scenario-generator agent reading the tenant's domain. 2. Mined from production conversations where Gatekeeper or Auditor flagged issues."

### From prd.md M6 — Verified-knowledge seeding
> "Every scenario that scores above the promotion thresholds (faithfulness ≥ 0.90 *and* answer relevance ≥ 0.90 by default; both tunable per tenant) is written to the tenant's `verified_qa` table with `source = 'sandbox_test'`."
> "The eval system is therefore not just a quality gate — it is the primary builder of the verified knowledge layer."
> "By the time an agent reaches pre-deployment, its hardest scenario questions are already answered from cache."

### From prd.md M6 — Demo requirement
> "Eval dashboard showing a real run with the resulting verified_qa entries highlighted. Run a query in the widget that hits a cached answer; show the trace skipping vector search entirely."

### From prd.md Layer 5 (Retrieval engine)
> "verified_qa_lookup (cosine ≥ threshold → return cached answer + citations)"
> "On miss: ... query_expansion (optional, LLM) → parallel(vector_search, bm25_search) → RRF_fusion → cross_encoder_rerank → return top_k"

### Ragas 0.4.x constraint (CLAUDE.md)
> "Ragas 0.4.x API only. Import paths and metric return types changed from 0.3.x — use `ragas.metrics.collections`, `MetricResult`, and `reference` (not `ground_truths`)."

### Celery beat (CLAUDE.md dev env table)
> "Celery beat: `celery -A app.worker.celery_app beat --loglevel=info` (M6+, from `apps/api/`)"
> This is M6's first time Celery beat is used — the beat process is now required for local dev.

### Neon branching for eval isolation (prd.md §3 Layer 3)
> "Nightly evals run against a branch of the tenant's DB so production traffic is never affected. This is a genuine differentiator and a defensible architectural choice."

### verified_qa schema (prd.md §6 Layer 4 — exact SQL)
```sql
CREATE TABLE verified_qa (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question         TEXT NOT NULL,
    question_vector  VECTOR(1024) NOT NULL,
    answer           TEXT NOT NULL,
    citations        JSONB NOT NULL,
    source           TEXT NOT NULL,
    faithfulness     NUMERIC,
    relevance        NUMERIC,
    promoted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_by      TEXT,
    last_used_at     TIMESTAMPTZ,
    use_count        INT DEFAULT 0,
    invalidated_at   TIMESTAMPTZ
);
CREATE INDEX verified_qa_vector_idx ON verified_qa
    USING hnsw (question_vector vector_cosine_ops);
```

</specifics>

<deferred>
## Deferred Ideas

- Per-tenant eval threshold configuration (EVAL_FAITHFULNESS_THRESHOLD, EVAL_RELEVANCY_THRESHOLD tunable per-tenant) — M9 alongside retrieval strategy synthesis
- Owner-facing verified_qa candidate approval UI — M8
- Automatic threshold auto-tuning from production trace mining — M9
- `conversation_insights` table and GraphRAG monthly Celery job — M10
- Red team weekly cron — M7
- Pre-deployment checklist verified_qa depth gate — M8
- `verified_qa` invalidation on document re-ingestion — noted in PRD §11 (risk mitigation); implement in M6 migration but wire the invalidation trigger in M8 when ingestion lifecycle is fully understood
- Celery beat `mine_eval_scenarios` as a separate daily task (combine into `run_eval_suite` for M6 simplicity; split if scheduling needs diverge)

</deferred>

---

*Phase: 06-eval-system*
*Context gathered: 2026-05-23 via PRD Express Path (prd.md)*
