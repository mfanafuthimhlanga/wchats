# Phase 9: Retrieval Strategy Synthesis — Research

**Researched:** 2026-05-25
**Domain:** Claude Agent SDK strategist pattern, corpus shape analysis SQL, query expansion, Celery pipeline chain integration, Ragas eval comparison
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01** The strategist is a Claude Agent SDK agent (Sonnet-tier), not a rule-based algorithm
- **D-02** Corpus shape analysis dimensions: corpus size distribution (chunk count, document count, avg chunk size), document type mix (PDF/Markdown/plain text ratios), structured vs unstructured ratio (table chunks vs prose chunks), domain detection (chunk content + metadata keywords)
- **D-03** Generated strategy written to `agents.retrieval_strategy` JSONB — no new migration required (column exists since migration 0003)
- **D-04** Strategy schema is the existing `RetrievalStrategy` Pydantic model in `retrieval_service.py` — fields: `vector_k`, `bm25_k`, `final_k`, `rerank_threshold`, `query_expansion`, `metadata_filters`
- **D-05** `query_expansion` was explicitly deferred to M9; M9 MUST implement the expansion path in `retrieval_service.py` so the strategist can legitimately set it `true`
- **D-06** `synthesize_retrieval_strategy` fires after `embed_and_migrate` — pipeline queue
- **D-07** Task receives `agent_id`; fetches decrypted `conn_str` from control DB at runtime
- **D-08** Task is idempotent with `acks_late=True`
- **D-09 [STR-01]** New agent after M9 receives auto-generated strategy — no manual JSON editing required
- **D-10 [STR-02]** Two tenants with different data shapes get meaningfully different configs
- **D-11 [STR-03]** Auto-generated strategies produce measurably better Ragas metrics vs default config
- **D-12** Demo: `scripts/demo_m9.sh` provisions two tenants with different data shapes, triggers synthesis, prints both resulting `retrieval_strategy` JSONB configs side-by-side
- **D-13** Demo shows eval metric comparison: one tenant with auto-generated strategy vs same tenant with default `{}` config

### Claude's Discretion

- Specific SQL queries for corpus shape analysis
- Whether to use single Agent SDK turn or multi-turn loop for the strategist
- Test structure (unit + integration coverage)
- Whether to add additional `RetrievalStrategy` fields beyond existing six
- Whether to add a `GET /agents/{id}/retrieval-strategy` inspection endpoint
- Timeout / retry settings for the synthesis task
- Query expansion implementation detail (LLM expansion prompt, number of expansions)

### Deferred Ideas (OUT OF SCOPE)

- Per-tenant strategy versioning or history tracking
- User-facing UI for manual strategy override
- Real-time adaptive strategies (build-time only)
- Multi-language domain detection (English only)
- Automated strategy re-synthesis on corpus updates (M10 candidate)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STR-01 | New agent after M9 receives auto-generated strategy — no manual JSON editing required | D-06 Celery chain ensures synthesis fires automatically after every ingestion; D-03 writes to existing JSONB column |
| STR-02 | Two tenants with different data shapes receive meaningfully different configs | Corpus shape SQL extracts size/type/density signals; strategist prompt heuristics map signals to different k-values and flags |
| STR-03 | Auto-generated strategies produce measurably better Ragas metrics vs default config — confirmed by eval run comparison | `run_eval_suite` receives `agent_id`; strategy comparison runs two separate invocations against same eval scenario set |
</phase_requirements>

---

## Summary

Phase 9 wires a Claude Agent SDK strategist into the ingestion pipeline so every newly ingested corpus automatically receives an optimized `RetrievalStrategy` JSON config. The strategist reads corpus shape signals from the tenant DB (chunk count, avg length, table-chunk ratio, entity density, document type mix), reasons over these signals to produce values for all six `RetrievalStrategy` fields including the previously deferred `query_expansion` flag, then writes the result to `agents.retrieval_strategy` JSONB.

The deliverable has three parts: (1) a `synthesize_retrieval_strategy` Celery pipeline task inserted after `embed_and_migrate` in `documents.py`; (2) the query expansion code path in `retrieval_service.py` that actually executes when `query_expansion=true`; (3) a demo script and eval comparison that prove two different corpora receive different strategies and that the synthesized strategy outperforms the empty-dict default.

The phase is small in scope (3 requirements, no migrations, no new UI), but requires careful integration work: the chain wiring must extend an already-complete pipeline in `documents.py`, the Agent SDK async bridge must follow the exact `asyncio.run(asyncio.wait_for(...))` pattern established in `deployment_service.py`, and the eval comparison for STR-03 must use `run_eval_suite` as a black box rather than patching its internals.

**Primary recommendation:** Model the strategist service on `deployment_service.py` exactly — single-turn SDK call, sync corpus signal collection via psycopg2, side-effect tool capture, `asyncio.run` bridge in the Celery task. Implement query expansion as a single synchronous Anthropic Haiku call that produces 2-3 variant queries, merged results fed back through the existing `rrf_fuse` path.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Corpus shape analysis SQL | Database / Storage | — | Queries run directly against tenant Neon DB via psycopg2; no ORM layer in tenant context |
| Strategist LLM reasoning | API / Backend (Celery worker) | — | Pipeline task on the `pipeline` queue, same as embed/chunk tasks |
| Strategy write | API / Backend (Celery worker) | Database / Storage | Task updates `agents.retrieval_strategy` on control DB via SQLAlchemy ORM |
| Query expansion execution | API / Backend (runtime path) | — | Lives in `retrieval_service.py`; called from `retrieve_and_rank` task at query time |
| Eval comparison | API / Backend (Celery worker) | — | `run_eval_suite` is an existing runtime task; STR-03 comparison triggers two runs via script |
| Demo script | API / Backend (local shell) | — | Bash script invoking the API; no Docker; follows `demo_m8.sh` pattern |

---

## Standard Stack

### Core (all already installed — verified in codebase)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| claude-agent-sdk | 0.1.81 (PINNED) | Strategist single-turn SDK call | Matches M8 deployment_service.py pattern; CLAUDE.md forbids upgrade |
| anthropic | Current in pyproject.toml | Haiku judge + query expansion LLM call | Direct API for non-agent synchronous calls |
| psycopg2 | Current | Corpus shape SQL against tenant DB | Consistent with all prior Celery tasks in this codebase |
| SQLAlchemy (ORM) | Current | Write strategy to control DB Agent row | Consistent with `get_sync_db()` pattern in embed.py and agent.py |
| structlog | Current | Structured logging in task and service | Project-wide standard |

[VERIFIED: codebase grep — all libraries present in existing tasks]

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| voyageai | Current (pinned voyage-3) | Query expansion embedding for variant queries | When query_expansion=True at retrieval time; same client as embed_query() |
| ragas 0.4.x | Current | STR-03 eval comparison | run_eval_suite is the existing black-box; no new Ragas imports needed in M9 |

[VERIFIED: codebase — already imported in eval_service.py and embedding_service.py]

### No New Dependencies Required

M9 requires zero new package installs. All needed libraries are already in the project.

---

## Architecture Patterns

### System Architecture Diagram

```
POST /agents/{id}/documents
         |
         v
  documents.py (FastAPI)
         |
         v (chain dispatch)
  parse_documents  [pipeline queue]
         |
  chunk_documents  [pipeline queue]
         |
  generate_metadata  [pipeline queue]
         |
  embed_and_migrate  [pipeline queue]
         |
  synthesize_retrieval_strategy  [pipeline queue]  ← NEW: M9 chain extension
         |
         +-- psycopg2 → tenant DB
         |   corpus shape SQL queries (chunk count, avg length, table ratio, entity count, doc types)
         |
         +-- _run_strategist_loop (async) → Claude Agent SDK (Sonnet)
         |   single-turn: signals JSON → generate_strategy tool call
         |
         +-- result_container["strategy"] → RetrievalStrategy.model_validate()
         |
         v
   agents.retrieval_strategy = strategy_dict  [control DB via SQLAlchemy]


Query time (already exists in retrieve_and_rank):
  strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})
  if strategy.query_expansion:
      expanded_queries = _expand_query(query_text)   ← NEW path in retrieval_service.py
      # run vector_search + bm25_search for each variant
      # merge candidates before rrf_fuse
  else:
      rrf_result = rrf_fuse(conn_str, query_vector, query_text, strategy)
```

### Recommended Project Structure

```
apps/api/app/
├── services/
│   ├── retrieval_service.py      # ADD: _expand_query() + query_expansion path in rrf_fuse
│   └── strategy_service.py       # NEW: corpus shape analysis + strategist agent loop
├── worker/
│   └── tasks/
│       └── pipeline/
│           ├── embed.py          # unchanged
│           └── strategy.py       # NEW: synthesize_retrieval_strategy Celery task
scripts/
└── demo_m9.sh                    # NEW: two-tenant demo + eval comparison
tests/unit/
├── test_strategy_service.py      # NEW: corpus shape, prompt building, JSON validation
└── test_strategy_task.py         # NEW: Celery task wiring, idempotency, chain arg passing
```

### Pattern 1: Strategist Service (modeled on deployment_service.py)

**What:** Collect corpus signals synchronously → call Agent SDK in a single async turn → capture tool result
**When to use:** Single-turn structured-output agents in Celery tasks
**Example:**

```python
# Source: deployment_service.py lines 309-353 (verified in codebase)

# 1. Sync signal collection (psycopg2 — matches all prior patterns)
def _fetch_corpus_signals_sync(agent_id: str, conn_str: str) -> dict:
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT document_id), AVG(LENGTH(content)) FROM chunks")
            row = cur.fetchone()
            chunk_count, doc_count, avg_chunk_len = row[0], row[1], float(row[2] or 0)
            # ... additional queries for table ratio, entity count, doc types
    finally:
        conn.close()
    return {"chunk_count": chunk_count, "doc_count": doc_count, "avg_chunk_len": avg_chunk_len, ...}

# 2. Async Agent SDK loop (single turn, side-effect tool)
async def _run_strategist_loop(signals_json: str, result_container: dict) -> None:
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        system_prompt=_STRATEGIST_SYSTEM_PROMPT,
        max_turns=3,  # single-turn expected; 3 gives the model room if it reasons first
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"Corpus signals:\n\n{signals_json}\n\nCall generate_strategy.")
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "generate_strategy":
                        result_container["strategy"] = block.input
                        return

# 3. Sync bridge (identical to deployment_service.run_orchestrator)
def run_strategist(signals_json: str, result_container: dict) -> None:
    try:
        asyncio.run(
            asyncio.wait_for(
                _run_strategist_loop(signals_json, result_container),
                timeout=60.0,
            )
        )
    except Exception as exc:
        log.warning("run_strategist.failed", error=str(exc))
```

[VERIFIED: deployment_service.py lines 309-353 — exact pattern confirmed in codebase]

### Pattern 2: Pipeline Chain Extension (documents.py chain wiring)

**What:** Extend the 4-task ingestion chain with a 5th pipeline task
**When to use:** New post-ingestion build-time work that needs corpus data
**Example:**

```python
# Source: documents.py lines 241-251 (verified in codebase)
# CURRENT (4-task chain):
chain(
    parse_documents.s(str(tenant.id), str(agent.id), str(job.id), document_ids),
    chunk_documents.s(),
    generate_metadata.s(),
    embed_and_migrate.s(),
).apply_async(queue="pipeline", headers={"request_id": ctx.get("request_id", "")})

# M9 ADDITION (5-task chain):
from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy

chain(
    parse_documents.s(str(tenant.id), str(agent.id), str(job.id), document_ids),
    chunk_documents.s(),
    generate_metadata.s(),
    embed_and_migrate.s(),
    synthesize_retrieval_strategy.s(),   # receives result dict from embed_and_migrate
).apply_async(queue="pipeline", headers={"request_id": ctx.get("request_id", "")})
```

The `.s()` (signature) form means `synthesize_retrieval_strategy` receives the return dict from `embed_and_migrate` as its first positional argument. That dict is `{"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}`. The task extracts `agent_id` from it and proceeds.

[VERIFIED: embed_and_migrate return value at line 398-404 in embed.py]

### Pattern 3: Celery Pipeline Task Skeleton (synthesize_retrieval_strategy)

**What:** Pipeline-queue task that receives result dict, fetches own conn_str, writes back to control DB
**When to use:** Any pipeline task that needs to write to the Agent row after corpus is ready
**Example:**

```python
# Source: embed.py task pattern (verified in codebase)
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=10,
    queue="pipeline",
)
def synthesize_retrieval_strategy(self, result: dict) -> dict:
    agent_id = result.get("agent_id")
    # ... defensive validation ...

    # Idempotency: check if strategy already set (not empty dict)
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            return result
        # Only re-synthesize if strategy is empty ({}) or if explicitly flagged
        if agent.retrieval_strategy and agent.retrieval_strategy != {}:
            log.info("synthesize_retrieval_strategy.idempotent_skip", agent_id=agent_id)
            return result
        conn_str = fernet_decrypt(agent.neon_connection_string)

    # Collect corpus signals from tenant DB
    signals = _fetch_corpus_signals_sync(agent_id, conn_str)

    # Run strategist (asyncio.run bridge)
    result_container: dict = {}
    run_strategist(json.dumps(signals), result_container)

    # Validate and write strategy
    raw = result_container.get("strategy", {})
    strategy = RetrievalStrategy.model_validate(raw)

    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is not None:
            agent.retrieval_strategy = strategy.model_dump()
            db.commit()

    log.info("synthesize_retrieval_strategy.complete", agent_id=agent_id)
    return result  # pass-through for chain compatibility
```

[VERIFIED: acks_late=True, idempotency guard, conn_str pattern all match embed.py and deployment task]

### Pattern 4: Query Expansion Implementation

**What:** When `query_expansion=True`, generate 2-3 variant queries via Haiku, run multi-vector search, merge candidates
**When to use:** Short/sparse corpora (FAQ-style, avg chunk < 150 chars)
**Example:**

```python
# NEW addition to retrieval_service.py

def _expand_query(query_text: str) -> list[str]:
    """Generate 2 query variants via Anthropic Haiku for sparse corpus retrieval.

    Returns original query + 2 expansions. Uses synchronous anthropic.Anthropic()
    client (NOT Agent SDK) — no tool-calling needed, just a structured text response.
    Safe to call from the sync Celery task context.
    """
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"Generate 2 alternative phrasings of this search query for a customer service "
                f"knowledge base. Return ONLY the 2 queries, one per line, no numbering:\n\n{query_text}"
            ),
        }],
    )
    variants = [line.strip() for line in msg.content[0].text.strip().split("\n") if line.strip()]
    # Always include original; cap at 3 total to bound latency
    return [query_text] + variants[:2]


def rrf_fuse_with_expansion(
    conn_str: str,
    query_vector: list[float],
    query_text: str,
    strategy: RetrievalStrategy,
) -> dict:
    """Entry point when query_expansion=True. Runs rrf_fuse for each variant and merges."""
    if not strategy.query_expansion:
        return rrf_fuse(conn_str, query_vector, query_text, strategy)

    variants = _expand_query(query_text)
    all_fused: dict[str, dict] = {}  # chunk_id → best-score dict

    for variant in variants:
        variant_vector = embed_query(variant)
        result = rrf_fuse(conn_str, variant_vector, variant, strategy)
        for row in result["fused"]:
            cid = row["chunk_id"]
            if cid not in all_fused or row["rrf_score"] > all_fused[cid]["rrf_score"]:
                all_fused[cid] = row

    merged = sorted(all_fused.values(), key=lambda r: r["rrf_score"], reverse=True)[:strategy.final_k]
    # Return same shape as rrf_fuse() for downstream rerank() compatibility
    return {
        "fused": merged,
        "vector_candidates": [],  # trace simplified for expanded path
        "bm25_candidates": [],
    }
```

[ASSUMED] Query expansion via 2-3 variants calling the existing `embed_query` + `rrf_fuse` is the standard pattern. Latency implication: 3x Voyage embed calls + 3x RRF SQL queries. On a 4 GB RAM local machine this is acceptable for retrieval (no Docker, running as local process). Haiku call is < 1 second. Total expansion overhead estimated 2-4 seconds per query — acceptable for this use case.

### Pattern 5: STR-03 Eval Comparison

**What:** Run `run_eval_suite` twice against the same tenant — once with auto-generated strategy, once with `{}` default — compare Ragas means
**When to use:** Demo/verification for STR-03

The comparison works as follows in the demo script:

```bash
# demo_m9.sh STR-03 section

# 1. Tenant A already has synthesized strategy in agents.retrieval_strategy
# 2. Trigger eval with current (synthesized) strategy → capture run_id_A
RESULT_A=$(curl -s -X POST ".../agents/$AGENT_ID/eval-runs" -H "X-API-Key: $API_KEY")
RUN_ID_A=$(echo "$RESULT_A" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('run_id',''))")

# 3. Patch strategy back to empty dict (default)
curl -s -X PATCH ".../agents/$AGENT_ID" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"retrieval_strategy": {}}'

# 4. Trigger second eval → capture run_id_B
RESULT_B=$(curl -s -X POST ".../agents/$AGENT_ID/eval-runs" -H "X-API-Key: $API_KEY")
RUN_ID_B=$(echo "$RESULT_B" | python3 -c "...")

# 5. Poll both to completion, then compare GET /eval-runs/{id}/results metrics
```

The `run_eval_suite` task does NOT need modification for STR-03. It already reads `agent.retrieval_strategy` through the normal `retrieve_and_rank` path during eval. The comparison is achieved by patching the strategy between two runs.

[VERIFIED: run_eval_suite in eval.py does not take a strategy override param — it reads from DB at runtime via the normal retrieval path]

### Anti-Patterns to Avoid

- **Passing conn_str in task args:** Task receives `result: dict` from embed; `agent_id` is extracted from it; conn_str fetched at runtime. Never serialize conn_str into the chain.
- **asyncio.wait_for() OUTSIDE asyncio.run():** Always `asyncio.run(asyncio.wait_for(coro, timeout=N))`. Never `asyncio.wait_for(asyncio.run(...))` — the latter is wrong and blocks outside any event loop.
- **loop.run_until_complete():** Broken in Python 3.12 on the solo pool worker. Use `asyncio.run()` exclusively.
- **Adding migration for strategy column:** Column already exists on `agents` since migration 0003. No migration needed for M9.
- **Hardcoding strategy values in the task:** The strategist agent must generate values; the task only validates and persists. Task logic must not replicate the LLM reasoning with if/else.
- **Calling `run_eval_suite` directly from the task:** STR-03 comparison is a demo/verification concern only. `synthesize_retrieval_strategy` must not dispatch eval runs — that is the beat schedule's domain.
- **Using `asyncio.run()` inside the strategist service when called from a runtime context that already has an event loop:** The Celery `solo` pool means no active loop in the main thread, so `asyncio.run()` is always safe. Do NOT use `nest_asyncio`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Agent SDK async bridge | Custom event loop management | `asyncio.run(asyncio.wait_for(...))` pattern from deployment_service.py | Wrong loop lifecycle causes subtle failures in Python 3.12 solo pool |
| BM25 ranking | Custom tf-idf | Native `tsvector + ts_rank_cd` (already in retrieval_service.py) | pg_search deprecated Neon March 2026; native path already proven |
| JSON schema validation of strategy output | Manual key checking | `RetrievalStrategy.model_validate(raw)` with `extra="ignore"` | Pydantic handles type coercion, defaults, and unknown-field tolerance |
| Eval metric comparison | Custom Ragas runner | `run_eval_suite` task (already in eval.py) | Full Neon branch isolation, scenario loading, metric writing already wired |
| LLM call for query expansion | Raw HTTP to Anthropic API | `anthropic.Anthropic().messages.create()` | Client already available; consistent with how validators.py makes Haiku calls |

---

## Corpus Shape Analysis SQL

The following SQL queries run against the tenant DB. All confirmed against the existing tenant schema from `0001_tenant_v1_schema.py`.

```sql
-- 1. Size distribution
SELECT
    COUNT(*) AS chunk_count,
    COUNT(DISTINCT document_id) AS doc_count,
    AVG(LENGTH(content))::float AS avg_chunk_len,
    MAX(LENGTH(content)) AS max_chunk_len,
    MIN(LENGTH(content)) AS min_chunk_len
FROM chunks;

-- 2. Structured ratio proxy (pipe character indicates table row)
SELECT
    SUM(CASE WHEN content LIKE '%|%' THEN 1 ELSE 0 END) AS table_chunks,
    COUNT(*) AS total_chunks
FROM chunks;

-- 3. Entity density (from entities table)
SELECT COUNT(DISTINCT e.id) AS entity_count
FROM entities e;

-- 4. Domain keywords (from chunk_metadata.keywords JSONB array)
SELECT m.keywords
FROM chunk_metadata m
LIMIT 50;

-- 5. Document type mix (source_type column on documents)
SELECT source_type, COUNT(*) AS count
FROM documents
GROUP BY source_type;
```

[VERIFIED: `chunks`, `entities`, `chunk_metadata`, `documents` tables all confirmed in tenant schema from prior migration files and deployment_service._fetch_corpus_stats_sync pattern]

---

## Strategist Prompt Design

The system prompt must encode the heuristic mapping from corpus signals to strategy values. This is the "intelligence" of the M9 feature:

```
You are a retrieval strategy optimizer for a RAG system.
Given corpus shape signals, call generate_strategy exactly once with optimized values.

CORPUS → STRATEGY HEURISTICS:

chunk_count > 5000: vector_k=30, bm25_k=25 (large corpus needs wider initial net)
chunk_count 1000-5000: vector_k=20, bm25_k=20 (default)
chunk_count < 1000: vector_k=15, bm25_k=15 (small corpus, tight focus)

avg_chunk_len > 400 (dense prose): rerank_threshold=0.3 (aggressive filtering)
avg_chunk_len < 150 (FAQ/short): query_expansion=true (expand sparse queries)
avg_chunk_len 150-400: rerank_threshold=0.1, query_expansion=false

table_ratio > 0.20 (>20% structured): increase bm25_k by 5 (structured text = keyword-rich)
entity_count > 500 (entity-rich): metadata_filters=[{"field": "entity_type"}] hint

final_k: always min(5, vector_k // 4) — never return more than needed
```

The tool schema `generate_strategy` mirrors the six `RetrievalStrategy` fields exactly. `metadata_filters` should be `[]` unless entity density is high.

---

## Common Pitfalls

### Pitfall 1: Chain Task Receives Wrong Argument Shape

**What goes wrong:** `synthesize_retrieval_strategy` is appended to the chain with `.s()` — it receives the return dict from `embed_and_migrate` as its first argument `result: dict`. If the task signature is `def synthesize_retrieval_strategy(self, agent_id: str)` instead of `def synthesize_retrieval_strategy(self, result: dict)`, the chain fails at dispatch time with a type error.
**Why it happens:** Confusion between standalone task invocation (`apply_async(kwargs={"agent_id": ...})`) and chained invocation (positional arg injection).
**How to avoid:** Always define `synthesize_retrieval_strategy(self, result: dict)` matching the embed.py signature. Extract `agent_id = result.get("agent_id")` inside the body. Return `result` unchanged at the end for chain compatibility.
**Warning signs:** `TypeError: synthesize_retrieval_strategy() got an unexpected keyword argument` in Celery worker logs.

### Pitfall 2: asyncio.run() Inside Already-Running Event Loop

**What goes wrong:** If `synthesize_retrieval_strategy` is ever called from a context with an active event loop (e.g., triggered by a test using `CELERY_TASK_ALWAYS_EAGER=True` inside an async test function), `asyncio.run()` raises `RuntimeError: This event loop is already running`.
**Why it happens:** `asyncio.run()` creates a new event loop; calling it inside an existing one is forbidden.
**How to avoid:** Tests for `synthesize_retrieval_strategy` must patch `asyncio.run` at the module boundary (same pattern as `test_deployment_task.py` which patches `app.worker.tasks.runtime.deployment.asyncio.run`). Never use `nest_asyncio` — it masks real bugs.
**Warning signs:** `RuntimeError: This event loop is already running` in test output.

### Pitfall 3: Idempotency Over-Strictness Blocking Re-synthesis

**What goes wrong:** Checking `if agent.retrieval_strategy != {}` to skip re-synthesis means that if the first synthesis produced a valid (non-empty) strategy, all subsequent ingestion runs on the same agent skip synthesis. This is correct for M9 (strategy is build-time once). But if `strategy_resynthesis_flagged` is set by the M5 auditor, the guard must be bypassed.
**Why it happens:** The M5 auditor sets `strategy_resynthesis_flagged=True` on the Agent row when repeated ungrounded failures occur. M9's synthesis task should re-run if this flag is set.
**How to avoid:** Idempotency guard: `if agent.retrieval_strategy and agent.retrieval_strategy != {} and not agent.strategy_resynthesis_flagged: return result`. After synthesis, clear the flag: `agent.strategy_resynthesis_flagged = False`.
**Warning signs:** `strategy_resynthesis_flagged=True` persists on an agent row indefinitely after M9 is in place.

### Pitfall 4: Strategy JSON Not Round-Tripping Through model_validate

**What goes wrong:** The Agent SDK returns `generate_strategy` tool input as a dict. If the model produces `"query_expansion": "true"` (string) instead of `true` (bool), Pydantic raises a `ValidationError`. Similarly `"vector_k": "30"` (string) instead of `30` (int).
**Why it happens:** LLMs sometimes stringify JSON fields.
**How to avoid:** `RetrievalStrategy.model_validate(raw)` with `model_config = ConfigDict(extra="ignore")` handles type coercion for str→bool and str→int via Pydantic v2's default coercers. Test this explicitly with string inputs. If validation fails, log a warning and fall back to `RetrievalStrategy()` defaults.
**Warning signs:** `pydantic.ValidationError` in `synthesize_retrieval_strategy` task logs.

### Pitfall 5: Query Expansion Adds 3x Voyage Embed Calls at Query Time

**What goes wrong:** For a FAQ tenant with `query_expansion=True`, every retrieval call makes 3 embed calls to Voyage (original + 2 variants) instead of 1. On a 4 GB RAM machine running locally, this adds ~2-4 seconds of latency per query.
**Why it happens:** Each variant needs its own embedding for HNSW vector search.
**How to avoid:** Cap expansion at 2 variants (3 total including original). The Voyage batch embed call can batch all 3 in a single API call: `_get_vo().embed([query, var1, var2], model="voyage-3", input_type="query").embeddings` returns 3 vectors. This reduces latency to ~1 Voyage round-trip instead of 3 sequential ones.
**Warning signs:** `retrieve_and_rank` task duration > 10 seconds when `query_expansion=True`.

### Pitfall 6: Strategist Times Out on Empty Corpus

**What goes wrong:** If `synthesize_retrieval_strategy` fires immediately after `embed_and_migrate` but the tenant corpus is very small (e.g., a test agent with 1 document), corpus signals like `avg_chunk_len` might be `None` from `AVG()` on an empty set, causing the JSON serialization to fail.
**Why it happens:** PostgreSQL `AVG()` on zero rows returns `NULL`, psycopg2 returns `None`.
**How to avoid:** Defensive coercion in `_fetch_corpus_signals_sync`: `avg_chunk_len = float(row[2] or 0)`. All aggregate functions should default to safe zero values on empty result.
**Warning signs:** `json.dumps(signals)` raises `TypeError: Object of type NoneType is not JSON serializable`.

---

## Code Examples

### Corpus Signal Collection (verified against tenant schema)

```python
# Source: verified against alembic_tenant/versions/0001_tenant_v1_schema.py + deployment_service.py pattern

def _fetch_corpus_signals_sync(agent_id: str, conn_str: str) -> dict:
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            # Size distribution
            cur.execute("""
                SELECT COUNT(*), COUNT(DISTINCT document_id),
                       AVG(LENGTH(content)), MAX(LENGTH(content))
                FROM chunks
            """)
            row = cur.fetchone()
            chunk_count = int(row[0] or 0)
            doc_count = int(row[1] or 0)
            avg_chunk_len = float(row[2] or 0)
            max_chunk_len = int(row[3] or 0)

            # Structured ratio
            cur.execute("""
                SELECT SUM(CASE WHEN content LIKE '%|%' THEN 1 ELSE 0 END),
                       COUNT(*)
                FROM chunks
            """)
            trow = cur.fetchone()
            table_chunks = int(trow[0] or 0)
            total_chunks = int(trow[1] or 1)
            table_ratio = table_chunks / total_chunks

            # Entity density
            cur.execute("SELECT COUNT(*) FROM entities")
            entity_count = int(cur.fetchone()[0] or 0)

            # Document type mix
            cur.execute("SELECT source_type, COUNT(*) FROM documents GROUP BY source_type")
            doc_types = {r[0]: int(r[1]) for r in cur.fetchall()}

    finally:
        conn.close()

    return {
        "chunk_count": chunk_count,
        "doc_count": doc_count,
        "avg_chunk_len": avg_chunk_len,
        "max_chunk_len": max_chunk_len,
        "table_ratio": round(table_ratio, 3),
        "entity_count": entity_count,
        "doc_types": doc_types,
    }
```

### Strategy Tool Schema for Agent SDK

```python
# Tool schema for generate_strategy — mirrors RetrievalStrategy fields exactly
_TOOL_GENERATE_STRATEGY = {
    "name": "generate_strategy",
    "description": "Submit the optimized retrieval strategy for this corpus.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vector_k": {"type": "integer", "description": "HNSW candidates (10-50)"},
            "bm25_k": {"type": "integer", "description": "BM25 candidates (10-50)"},
            "final_k": {"type": "integer", "description": "Results after rerank (3-10)"},
            "rerank_threshold": {"type": "number", "description": "Min rerank score (0.0-0.5)"},
            "query_expansion": {"type": "boolean", "description": "true for short/sparse corpora"},
            "metadata_filters": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Entity-based filters; [] unless entity_count > 500",
            },
        },
        "required": ["vector_k", "bm25_k", "final_k", "rerank_threshold", "query_expansion", "metadata_filters"],
    },
}
```

### Celery Registry Update

```python
# celery_app.py include list addition (after M8 deployment entry):
# M9: retrieval strategy synthesis (pipeline queue)
"app.worker.tasks.pipeline.strategy",
```

---

## Runtime State Inventory

> Not applicable — this is a greenfield pipeline task addition, not a rename/refactor phase. No stored state, live service config, OS-registered state, secrets, or build artifacts carry names that are being changed.

**None found in any category — verified: M9 adds new code paths, does not rename existing ones.**

---

## Phase Sizing

With 3 requirements (STR-01, STR-02, STR-03), no migrations, no new UI, and no new dependencies, this phase maps to 4 PLAN.md files in 4 waves:

| Wave | Plan | Objective |
|------|------|-----------|
| Wave 1 | 09-01 | `strategy_service.py` — corpus signal SQL + strategist loop + `run_strategist` bridge; `_expand_query` in `retrieval_service.py`; `celery_app.py` include + Wave 0 test stubs (xfail) |
| Wave 2 | 09-02 | `apps/api/app/worker/tasks/pipeline/strategy.py` — `synthesize_retrieval_strategy` task; `documents.py` chain extension; idempotency + `strategy_resynthesis_flagged` clear |
| Wave 3 | 09-03 | Unit tests: de-xfail stubs; `test_strategy_service.py` + `test_strategy_task.py` |
| Wave 4 | 09-04 | `scripts/demo_m9.sh` — two-tenant demo + STR-03 eval comparison; guarded E2E test (`STRATEGY_E2E_ENABLED=1`) |

This is 4 plans, which matches the minimum viable wave structure for this scope. Wave 1 and Wave 2 are sequential (service before task). Wave 3 is blocked on Wave 2. Wave 4 is blocked on Wave 3.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hand-written `retrieval_strategy = {}` default (M3) | Strategist agent generates per-corpus config (M9) | M9 | Every new tenant gets an optimized config; zero operator effort |
| `query_expansion: bool = False` always (M3 comment) | `query_expansion` path implemented; strategist can enable it | M9 | Short-answer / FAQ corpora get better retrieval coverage |
| Strategy changeable only by direct DB write or PATCH /agents | Automatic synthesis post-ingestion | M9 | Closes the "configuration gap" for non-technical owners |

**Deprecated/outdated:**
- The inline comment `# deferred to M9; always False in M3` in `retrieval_service.py` line 57 must be removed when the expansion path is implemented

---

## Open Questions

1. **Should `synthesize_retrieval_strategy` emit SSE events?**
   - What we know: Other pipeline tasks emit SSE events (parsing.started, embedding.complete, etc.)
   - What's unclear: Whether the admin UI or demo needs to show "strategy synthesis in progress"
   - Recommendation: Emit a single `strategy.synthesized` event at completion — follows existing emit pattern, costs one Redis publish, useful for demo verification. The job that wraps the synthesis is the existing ingestion job, so use that `job_id` from the result dict.

2. **Should `strategy_resynthesis_flagged` be cleared after synthesis?**
   - What we know: M5 sets this flag on repeated Auditor failures; M9 CONTEXT.md does not explicitly address clearing it
   - What's unclear: Should M9's synthesis automatically clear the flag, or leave it for operator review?
   - Recommendation: Clear the flag after successful synthesis — otherwise it accumulates indefinitely. Add `agent.strategy_resynthesis_flagged = False` in the `db.commit()` block.

3. **Does the STR-03 eval comparison require real Ragas scores, or is a score structure comparison sufficient?**
   - What we know: STR-03 says "measurably better Ragas metrics vs default config" — requires real numbers
   - What's unclear: The eval takes time (Neon branch creation, Ragas LLM calls); is the demo blocking on eval completion?
   - Recommendation: `demo_m9.sh` triggers eval and polls until complete (matching `demo_m8.sh` polling pattern, up to 120 seconds). Both eval runs must complete before the script prints the comparison.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis | Celery broker + task queue | Assumed available | Local instance | None — required |
| PostgreSQL (local + Neon) | Corpus signal SQL, control DB writes | Assumed available | Local + Neon tenant | None — required |
| ANTHROPIC_API_KEY | Claude Sonnet SDK call, Haiku query expansion | Available in settings.py | — | None — required |
| VOYAGE_API_KEY | Query expansion embed calls | Available in settings.py | — | None — required |
| claude-agent-sdk 0.1.81 | Strategist loop | Pinned in pyproject.toml | 0.1.81 | None — do not upgrade |

[VERIFIED: All env vars present in `config.py`; all packages pinned in existing pyproject.toml via prior milestones]

**No missing dependencies identified.** M9 uses only what is already installed.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | `apps/api/pyproject.toml` (existing pytest config) |
| Quick run command | `cd apps/api && python -m pytest tests/unit/test_strategy_service.py tests/unit/test_strategy_task.py -x -q` |
| Full suite command | `cd apps/api && python -m pytest tests/ -x -q --ignore=tests/e2e` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STR-01 | `synthesize_retrieval_strategy` task updates `agents.retrieval_strategy` with non-empty dict | unit | `pytest tests/unit/test_strategy_task.py::test_strategy_written_to_db -x` | Wave 0 |
| STR-01 | Chain wiring — task is 5th link after `embed_and_migrate` | unit | `pytest tests/unit/test_strategy_task.py::test_receives_embed_result_dict -x` | Wave 0 |
| STR-01 | Idempotency — task skips if strategy already set | unit | `pytest tests/unit/test_strategy_task.py::test_idempotency_skip -x` | Wave 0 |
| STR-01 | `strategy_resynthesis_flagged=True` bypasses idempotency guard | unit | `pytest tests/unit/test_strategy_task.py::test_resynthesis_flag_bypasses_guard -x` | Wave 0 |
| STR-02 | `_fetch_corpus_signals_sync` returns correct shape from psycopg2 mock | unit | `pytest tests/unit/test_strategy_service.py::test_corpus_signals_shape -x` | Wave 0 |
| STR-02 | `RetrievalStrategy.model_validate(raw)` accepts string-typed fields from LLM | unit | `pytest tests/unit/test_strategy_service.py::test_strategy_validate_string_inputs -x` | Wave 0 |
| STR-02 | `run_strategist` bridge calls asyncio.run correctly | unit | `pytest tests/unit/test_strategy_service.py::test_run_strategist_calls_asyncio_run -x` | Wave 0 |
| STR-03 | `_expand_query` returns list of 3 queries (original + 2 variants) | unit | `pytest tests/unit/test_strategy_service.py::test_expand_query_returns_three -x` | Wave 0 |
| STR-03 | `rrf_fuse_with_expansion` calls `rrf_fuse` 3 times when expansion=True | unit | `pytest tests/unit/test_strategy_service.py::test_expansion_calls_rrf_fuse_per_variant -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `cd apps/api && python -m pytest tests/unit/test_strategy_service.py tests/unit/test_strategy_task.py -x -q`
- **Per wave merge:** `cd apps/api && python -m pytest tests/ -x -q --ignore=tests/e2e`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps

- [ ] `tests/unit/test_strategy_service.py` — covers STR-01 service layer, STR-02 signal shape, STR-03 expansion
- [ ] `tests/unit/test_strategy_task.py` — covers STR-01 task wiring, idempotency, chain arg format
- [ ] `tests/e2e/test_strategy_e2e.py` — covers STR-01–STR-03 end-to-end (guarded: `STRATEGY_E2E_ENABLED=1`)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | yes | `agent_id` extracted from chain result dict; control DB fetch validates agent exists before any tenant DB access |
| V5 Input Validation | yes | `RetrievalStrategy.model_validate(raw)` with `extra="ignore"` — LLM output never trusted as raw SQL or path; Pydantic coerces and bounds-checks |
| V6 Cryptography | yes | `fernet_decrypt(agent.neon_connection_string)` — same pattern as all prior tasks; conn_str never logged |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| LLM prompt injection via corpus content (chunk keywords feed into strategist context) | Tampering | Corpus signals are numerical aggregates (count, avg, ratio) — no raw chunk text sent to LLM; keywords summarized as counts only |
| Strategy override via crafted corpus (adversary uploads document with content that manipulates signal ratios) | Tampering | Signals are statistical aggregates across the entire corpus; single-document manipulation has minimal effect; no SQL injection possible via chunk content |
| conn_str in Celery task args | Information Disclosure | Task receives `result: dict` with `agent_id` only; conn_str fetched at runtime from control DB — same protection as every prior task |
| LLM output with invalid JSON for `metadata_filters` | Denial of Service | `RetrievalStrategy.model_validate(raw)` with `extra="ignore"` gracefully rejects; fallback to `RetrievalStrategy()` defaults on ValidationError |

---

## Sources

### Primary (HIGH confidence)

- `apps/api/app/services/deployment_service.py` — Agent SDK orchestrator pattern (fully read; `_run_orchestrator_loop`, `run_orchestrator`, signal collection functions all verified)
- `apps/api/app/worker/tasks/pipeline/embed.py` — terminal pipeline task; chain return value format verified at lines 398-404
- `apps/api/app/api/v1/documents.py` — chain dispatch verified at lines 241-251; 5th task insertion point confirmed
- `apps/api/app/services/retrieval_service.py` — `RetrievalStrategy` model (all 6 fields), `rrf_fuse` signature, `embed_query` pattern — all verified
- `apps/api/app/worker/celery_app.py` — `include` list, queue topology, `task_routes` pattern — all verified
- `apps/api/app/models/agent.py` — `retrieval_strategy` JSONB field, `strategy_resynthesis_flagged` bool field — both verified at lines 47-57
- `apps/api/app/worker/tasks/runtime/eval.py` — `run_eval_suite(self, agent_id: str)` signature confirmed; no strategy override param exists
- `apps/api/app/core/config.py` — `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY` in Settings — verified

### Secondary (MEDIUM confidence)

- `.planning/phases/09-retrieval-strategy-synthesis/09-CONTEXT.md` — CONTEXT.md locked decisions D-01 through D-13; all constraints verified against codebase
- `.planning/phases/03-hybrid-retrieval/03-CONTEXT.md` — M3 k=60 literal constraint, BM25 native-only constraint, voyage-3 pinned — all confirmed in retrieval_service.py

### Tertiary (LOW confidence)

- [ASSUMED] Query expansion via 2-3 variants is the standard approach; Voyage batch embed for all variants reduces latency vs sequential calls

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Query expansion via 2-3 Haiku-generated variants calling `_expand_query()` is the right implementation detail | Pattern 4: Query Expansion | If LLM-generated variants are too similar to original, expansion provides no retrieval benefit; mitigation: prompt diversity instruction |
| A2 | `asyncio.run()` inside the Celery solo pool worker is safe with no active event loop | Pattern 1, Pitfall 2 | If there is already an active loop in some code path, tasks will fail with RuntimeError; mitigation: test with `CELERY_TASK_ALWAYS_EAGER=True` and patch `asyncio.run` |
| A3 | Voyage batch embed (`embed([q1, q2, q3])`) is supported and returns 3 separate vectors | Pattern 4 | If batch embed fails for 3 items, fallback to sequential embed calls; voyageai client batch pattern confirmed in embedding_service.py |

**Only 3 assumptions — all other findings verified directly in codebase.**

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries confirmed present in codebase; no new installs required
- Architecture: HIGH — deployment_service.py orchestrator pattern directly replicable; chain wiring verified in documents.py
- Chain wiring: HIGH — embed.py return value format confirmed; documents.py chain dispatch confirmed
- Query expansion: MEDIUM — implementation approach derived from retrieval_service.py patterns; specific Voyage batch API behavior assumed from embedding_service.py usage
- Pitfalls: HIGH — derived from actual codebase patterns and STATE.md documented decisions

**Research date:** 2026-05-25
**Valid until:** 2026-06-25 (stable stack; no fast-moving dependencies)
