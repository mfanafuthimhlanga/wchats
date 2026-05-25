# Phase 9: Retrieval Strategy Synthesis — Context

**Gathered:** 2026-05-25
**Status:** Ready for planning
**Source:** PRD Express Path (prd.md — M9 section + ROADMAP.md)

<domain>
## Phase Boundary

Phase 9 delivers a strategist agent that auto-generates per-tenant `RetrievalStrategy` configs from corpus shape analysis, replacing all hand-written M3 defaults. The agent reads the tenant's data shape after ingestion (corpus size distribution, document type mix, structured vs unstructured ratio, domain detection), generates optimal values for all `RetrievalStrategy` fields (including `query_expansion`, which was deferred from M3), and writes the result to `agents.retrieval_strategy`. A `synthesize_retrieval_strategy` Celery task is inserted into the pipeline chain after `embed_and_migrate`. The demo proves two tenants with different corpora get meaningfully different configs; an eval comparison confirms improvement over the M3 defaults.

**What is NOT in scope:**
- Real-time strategy adjustment (strategy is generated once at build time)
- User-facing UI for manual strategy editing
- Retrieval strategy versioning or history
- Multi-language domain detection (English only)

</domain>

<decisions>
## Implementation Decisions

### Agent Implementation
- **D-01 [LOCKED]** The strategist is a Claude Agent SDK agent (Sonnet-tier for reasoning quality), not a rule-based algorithm — per PRD §Layer 5: "generated once at build time by a strategist agent looking at the tenant's data shape"
- **D-02 [LOCKED]** Corpus shape analysis dimensions are: corpus size distribution (chunk count, document count, avg chunk size), document type mix (PDF/Markdown/plain text ratios), structured vs unstructured ratio (table chunks vs prose chunks), and domain detection (detected from chunk content + metadata keywords)

### Storage
- **D-03 [LOCKED]** Generated strategy is written to `agents.retrieval_strategy` JSONB column (already exists since M3 migration 0003) — no new migration required for the column itself
- **D-04 [LOCKED]** Strategy schema is the existing `RetrievalStrategy` Pydantic model in `retrieval_service.py` — fields: `vector_k`, `bm25_k`, `final_k`, `rerank_threshold`, `query_expansion`, `metadata_filters`

### query_expansion Unlock
- **D-05 [LOCKED]** `query_expansion` was explicitly deferred to M9 in M3 code (`query_expansion: bool = False  # deferred to M9; always False in M3`). M9 MUST implement the query expansion path in `retrieval_service.py` so the strategist can legitimately set it to `true` for corpora that benefit from it

### Celery Chain Position
- **D-06 [LOCKED]** `synthesize_retrieval_strategy` is a pipeline-queue task that fires after `embed_and_migrate` completes — per PRD §Layer 2 Celery chain order: `embed_and_migrate → synthesize_retrieval_strategy`
- **D-07 [LOCKED]** Task receives `agent_id`; fetches decrypted `conn_str` from control DB at runtime — connection strings never in Celery task args (CLAUDE.md non-negotiable)
- **D-08 [LOCKED]** Task is idempotent with `acks_late=True` (CLAUDE.md non-negotiable)

### Success Requirements
- **D-09 [LOCKED — STR-01]** New agent after M9 receives auto-generated strategy — no manual JSON editing required
- **D-10 [LOCKED — STR-02]** Two tenants with different data shapes (e.g., dense technical PDF corpus vs sparse FAQ plain-text corpus) receive meaningfully different configs (different `vector_k`, `bm25_k`, `query_expansion`, `rerank_threshold` values)
- **D-11 [LOCKED — STR-03]** Auto-generated strategies produce measurably better Ragas metrics vs default config — confirmed by running `run_eval_suite` with strategy vs default

### Demo
- **D-12 [LOCKED]** Demo script `scripts/demo_m9.sh` provisions two tenants with different data shapes, triggers synthesis for each, and prints both resulting `retrieval_strategy` JSONB configs side-by-side showing they differ
- **D-13 [LOCKED]** Demo shows eval metric comparison: one tenant with auto-generated strategy vs. same tenant with default `{}` config (empty RetrievalStrategy)

### Claude's Discretion
- Specific SQL queries for corpus shape analysis (chunk counts, type distribution, entity density)
- Whether to use a single Agent SDK turn or a multi-turn loop for the strategist
- Test structure (unit + integration coverage)
- What additional `RetrievalStrategy` fields (if any) to add beyond existing six
- Whether to add a `GET /agents/{id}/retrieval-strategy` inspection endpoint
- Timeout / retry settings for the synthesis task
- Query expansion implementation detail (LLM expansion prompt, number of expansions)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Retrieval strategy model + execution
- `apps/api/app/services/retrieval_service.py` — `RetrievalStrategy` Pydantic model (all 6 fields), `embed_query`, `rrf_fuse`, `rerank`, `build_trace` — this is the code the strategist's output feeds into
- `apps/api/app/models/agent.py` — `Agent.retrieval_strategy` JSONB field (line 47), ORM pattern

### Pipeline chain integration point
- `apps/api/app/worker/tasks/pipeline/embed.py` — terminal ingestion task; `synthesize_retrieval_strategy` is chained after this
- `apps/api/app/worker/celery_app.py` — task include list; new task module must be registered here

### Settings + auth pattern
- `apps/api/app/core/config.py` — `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, Settings pattern

### Agent SDK usage pattern (strategist implementation reference)
- `apps/api/app/services/deployment_service.py` — current Agent SDK orchestrator pattern to replicate
- `apps/api/app/services/agent_prompt.py` — system prompt assembly pattern
- `apps/api/app/worker/tasks/runtime/agent.py` — `asyncio.run(asyncio.wait_for(...))` bridge pattern for SDK in Celery

### Eval comparison (STR-03)
- `apps/api/app/worker/tasks/runtime/eval.py` — `run_eval_suite` task for metric comparison
- `apps/api/app/services/eval_service.py` — eval execution functions

### Prior phase context (M3 decisions to respect)
- `.planning/phases/03-hybrid-retrieval/03-CONTEXT.md` — M3 locked decisions: k=60 hardcoded SQL literal (not parameterized), BM25 tsvector-only, Voyage voyage-3 pinned

</canonical_refs>

<specifics>
## Specific Ideas

**Corpus shape analysis SQL (for the synthesis task):**
These are the queries the strategist needs to produce its analysis:
- `SELECT COUNT(*) AS chunk_count, COUNT(DISTINCT document_id) AS doc_count, AVG(LENGTH(content)) AS avg_chunk_length FROM chunks`
- `SELECT SUM(CASE WHEN content LIKE '%|%' THEN 1 ELSE 0 END) AS table_chunks, COUNT(*) AS total FROM chunks` (structured ratio proxy)
- Entity density: `SELECT COUNT(DISTINCT e.name) AS entity_count FROM entities e`
- Domain keywords from `chunk_metadata` table's `keywords` JSONB array

**RetrievalStrategy tuning heuristics (starting point for strategist prompt):**
- Large corpus (>5000 chunks): increase `vector_k` to 30, `bm25_k` to 25
- High entity density: enable `metadata_filters`, suggest entity-based filter path
- Dense prose (avg chunk >400 chars): `rerank_threshold` 0.3 (more aggressive filtering)
- Short FAQ-style chunks (avg <150 chars): `query_expansion: true` (compensates for sparse matches)
- High table chunk ratio (>20%): `bm25_k` weight increase (structured text keywords)

**Two-tenant demo differentiation:**
- Tenant A: Dense technical manual (PDFs, 2000+ chunks, long prose) → higher k values, rerank filtering, expansion off
- Tenant B: FAQ/knowledge-base text (plain text, 200 chunks, short answers) → lower k values, query_expansion on, low rerank_threshold

</specifics>

<deferred>
## Deferred Ideas

- Per-tenant strategy versioning or history tracking (not in v1 scope)
- User-facing UI for manual strategy override (owner never edits JSON in v1)
- Real-time adaptive strategies (strategy is build-time only in v1)
- Multi-language domain detection (English only per PRD non-goals)
- Automated strategy re-synthesis on corpus updates (M10 candidate)

</deferred>

---

*Phase: 09-retrieval-strategy-synthesis*
*Context gathered: 2026-05-25 via PRD Express Path (prd.md M9 + ROADMAP.md)*
