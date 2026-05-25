# Phase 9: Retrieval Strategy Synthesis - Pattern Map

**Mapped:** 2026-05-25
**Files analyzed:** 7 new/modified files
**Analogs found:** 7 / 7

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/api/app/services/strategy_service.py` | service | request-response (sync signal collect → async Agent SDK) | `apps/api/app/services/deployment_service.py` | exact |
| `apps/api/app/worker/tasks/pipeline/strategy.py` | Celery pipeline task | CRUD (read corpus, write strategy to control DB) | `apps/api/app/worker/tasks/runtime/deployment.py` | exact |
| `apps/api/app/services/retrieval_service.py` (modify) | service | request-response + batch (query expansion path) | self — existing functions `embed_query`, `rrf_fuse` | self-analog |
| `apps/api/app/api/v1/documents.py` (modify) | route / chain wiring | request-response | self — existing chain at lines 241-251 | self-analog |
| `apps/api/app/worker/celery_app.py` (modify) | config | — | self — existing `include` list at lines 69-88 | self-analog |
| `scripts/demo_m9.sh` | demo script | request-response + polling | `scripts/demo_m8.sh` | exact |
| `tests/unit/test_strategy_service.py` + `test_strategy_task.py` | test | — | existing test files under `apps/api/tests/unit/` | role-match |

---

## Pattern Assignments

### `apps/api/app/services/strategy_service.py` (service, request-response)

**Analog:** `apps/api/app/services/deployment_service.py`

**Imports pattern** (lines 13-31):
```python
from __future__ import annotations

import asyncio
import json
from typing import Literal

import psycopg2
import structlog
from pydantic import BaseModel
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ToolUseBlock,
)
from app.core.config import settings

SONNET_MODEL = "claude-sonnet-4-6"
log = structlog.get_logger(__name__)
```

**System prompt constant pattern** (lines 64-86):
```python
_DEPLOYMENT_SYSTEM_PROMPT = """\
You are the pre-deployment readiness orchestrator for a customer-service AI agent.
...
Call submit_report exactly once with your assessment.
"""
```
Copy this block exactly — use a module-level `_STRATEGIST_SYSTEM_PROMPT` string constant with triple-quoted docstring. The strategist prompt encodes all heuristics from RESEARCH.md §Strategist Prompt Design (chunk_count thresholds, avg_chunk_len thresholds, table_ratio, entity_count).

**Tool schema pattern** (lines 93-131):
```python
_TOOL_SUBMIT_REPORT = {
    "name": "submit_report",
    "description": "Submit the deployment readiness report with recommendation and warnings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": ["ship", "ship_with_warnings", "block"],
            },
            ...
        },
        "required": ["recommendation", "summary", "warnings"],
    },
}
```
Replace `_TOOL_SUBMIT_REPORT` with `_TOOL_GENERATE_STRATEGY` using the six `RetrievalStrategy` fields as properties. All six must be in `"required"`. The `metadata_filters` property uses `"type": "array"` with `"items": {"type": "object"}`.

**Sync psycopg2 signal collection pattern** (lines 153-186, generalised):
```python
def _fetch_corpus_stats_sync(agent_id: str, conn_str: str) -> dict:
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            doc_row = cur.fetchone()
            document_count = int(doc_row[0]) if doc_row else 0
            # ... more queries ...
            return {
                "document_count": document_count,
                "chunk_count": chunk_count,
                "last_ingested_at": last_ingested_at,
            }
    finally:
        conn.close()
```
The pattern: one `psycopg2.connect(conn_str, connect_timeout=10)`, try/finally/close, `with conn.cursor()` for each query, defensive `int(row[0] or 0)` / `float(row[2] or 0)` coercions on all aggregates. Apply this pattern to all corpus shape queries (size distribution, table ratio, entity count, doc type mix). NEVER log conn_str.

**Async SDK loop pattern** (lines 309-335):
```python
async def _run_orchestrator_loop(
    signals_json: str,
    result_container: dict,
) -> None:
    options = ClaudeAgentOptions(
        model=SONNET_MODEL,
        system_prompt=_DEPLOYMENT_SYSTEM_PROMPT,
        max_turns=5,
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Here are the agent's quality signals:\n\n{signals_json}\n\n"
            "Assess deployment readiness and call submit_report."
        )
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "submit_report":
                        result_container["report"] = block.input
                        return
```
Replace `"submit_report"` with `"generate_strategy"`, `result_container["report"]` with `result_container["strategy"]`, and the query string with the corpus signals prompt. Use `max_turns=3` (single-turn expected; 3 gives room if model reasons first).

**Sync bridge pattern** (lines 338-354):
```python
def run_orchestrator(signals_json: str, result_container: dict) -> None:
    try:
        asyncio.run(
            asyncio.wait_for(
                _run_orchestrator_loop(signals_json, result_container),
                timeout=120.0,
            )
        )
    except Exception as exc:
        log.warning("deployment_orchestrator.failed", error=str(exc))
```
Copy exactly as `run_strategist`. Reduce timeout to 60.0 (single-turn; no multi-step reasoning). Never use `loop.run_until_complete()`.

**NOTE — no `_call_orchestrator_async` shim needed in strategy_service.py.** The `deployment.py` task uses a shim because `run_orchestrator` itself calls `asyncio.run` internally. For `strategy.py`, the Celery task will call `asyncio.run(asyncio.wait_for(_run_strategist_loop(...), timeout=60.0))` directly — no extra shim layer.

---

### `apps/api/app/worker/tasks/pipeline/strategy.py` (Celery pipeline task, CRUD)

**Analog:** `apps/api/app/worker/tasks/runtime/deployment.py` (overall structure) and `apps/api/app/worker/tasks/pipeline/embed.py` (task decorator + result dict pattern)

**Imports pattern** (embed.py lines 55-73, deployment.py lines 28-48):
```python
from __future__ import annotations

import asyncio
import json

import structlog

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.events import emit          # for strategy.synthesized SSE event
from app.services.retrieval_service import RetrievalStrategy
from app.services.strategy_service import _fetch_corpus_signals_sync, _run_strategist_loop
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)
```

**Task decorator pattern** (embed.py lines 81-87):
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def embed_and_migrate(self, result: dict) -> dict:
```
For `synthesize_retrieval_strategy` use `max_retries=2`, `default_retry_delay=10`. Queue must be `"pipeline"` (D-06 locked). Task signature MUST be `(self, result: dict) -> dict` — receives embed result dict as positional arg.

**Result dict extraction pattern** (embed.py lines 110-120):
```python
tenant_id = result.get("tenant_id")
agent_id = result.get("agent_id")
job_id = result.get("job_id")
document_ids = result.get("document_ids")

if not all([tenant_id, agent_id, job_id, document_ids is not None]):
    log.error(
        "embed_and_migrate.invalid_result_dict",
        keys=list(result.keys()),
    )
    return result
```
Copy this pattern exactly. Extract `agent_id` (and optionally `job_id` for SSE emit). Return `result` unchanged on early exit.

**Control DB fetch + conn_str decrypt pattern** (embed.py lines 122-153, deployment.py lines 91-100):
```python
with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
    if agent is None or not agent.neon_connection_string:
        log.error("run_deployment_checklist.agent_not_found", agent_id=agent_id)
        return {}
    conn_str = fernet_decrypt(agent.neon_connection_string)
```
Decrypt inside `get_sync_db()` context. Never log conn_str.

**Idempotency guard pattern** (deployment.py lines 108-118, plus M9-specific flag check from RESEARCH.md Pitfall 3):
```python
with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
    if agent is None:
        return result
    # Idempotency: skip if strategy already set AND resynthesis not flagged
    if (
        agent.retrieval_strategy
        and agent.retrieval_strategy != {}
        and not agent.strategy_resynthesis_flagged
    ):
        log.info("synthesize_retrieval_strategy.idempotent_skip", agent_id=agent_id)
        return result
    conn_str = fernet_decrypt(agent.neon_connection_string)
```
After successful synthesis, clear the flag: `agent.strategy_resynthesis_flagged = False` in the same `db.commit()` block.

**asyncio.run bridge pattern** (deployment.py lines 206-218):
```python
result_container: dict = {}
try:
    asyncio.run(
        asyncio.wait_for(
            _call_orchestrator_async(signals_json, result_container),
            timeout=120.0,
        )
    )
except Exception as exc:
    log.error(
        "run_deployment_checklist.orchestrator_failed",
        agent_id=agent_id,
        run_id=run_id,
        error=str(exc),
    )
```
For M9, call `_run_strategist_loop` directly (no shim needed — see note above). Use `timeout=60.0`.

**Strategy validate + write pattern** (deployment.py lines 226-250, adapted for M9):
```python
raw = result_container.get("strategy", {})
try:
    strategy = RetrievalStrategy.model_validate(raw)
except Exception as val_exc:
    log.warning("synthesize_retrieval_strategy.validation_failed", error=str(val_exc))
    strategy = RetrievalStrategy()   # fall back to defaults

with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
    if agent is not None:
        agent.retrieval_strategy = strategy.model_dump()
        agent.strategy_resynthesis_flagged = False
        db.commit()

log.info("synthesize_retrieval_strategy.complete", agent_id=agent_id)
return result   # pass-through for chain compatibility
```

**Retry + failure re-raise pattern** (embed.py lines 380-393):
```python
if self.request.retries >= self.max_retries:
    # mark failed and re-raise
    raise
else:
    raise self.retry(exc=exc, countdown=2**self.request.retries)
```

**SSE event emit pattern** (embed.py lines 206-212):
```python
emit(
    job_id,
    "strategy.synthesized",
    {"agent_id": agent_id},
    db,
    _redis,
)
```
Emit a single `strategy.synthesized` event after successful DB write. Requires module-level `_redis` client (copy SSL setup from embed.py lines 76-78).

---

### `apps/api/app/services/retrieval_service.py` (modify — add query expansion path)

**Analog:** self — existing `embed_query`, `rrf_fuse`, `vector_search`, `bm25_search` functions

**RetrievalStrategy model** (lines 42-58):
```python
class RetrievalStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vector_k: int = 20
    bm25_k: int = 20
    final_k: int = 5
    rerank_threshold: float = 0.0
    query_expansion: bool = False  # deferred to M9; always False in M3
    metadata_filters: list[dict] = []
```
Remove the `# deferred to M9; always False in M3` comment from line 57. The field stays `bool = False` (default); strategist enables it for qualifying corpora.

**embed_query pattern** (lines 65-78):
```python
def embed_query(query_text: str) -> list[float]:
    return _get_vo().embed([query_text], model="voyage-3", input_type="query").embeddings[0]
```
`_expand_query` calls this per variant. For batch efficiency, use `_get_vo().embed([q1, q2, q3], model="voyage-3", input_type="query").embeddings` to get all 3 vectors in one Voyage round-trip.

**psycopg2 connection pattern** (lines 178-186):
```python
conn = psycopg2.connect(conn_str)
try:
    with conn.cursor() as cur:
        cur.execute(sql, {...})
        rows = cur.fetchall()
finally:
    conn.close()
```
`rrf_fuse_with_expansion` calls `rrf_fuse` (which uses this pattern internally) — no new psycopg2 connection needed in the expansion wrapper.

**rrf_fuse return shape** (lines 365-369):
```python
return {
    "fused": fused_rows,
    "vector_candidates": vector_cands,
    "bm25_candidates": bm25_cands,
}
```
`rrf_fuse_with_expansion` must return the same shape. The `fused` key carries the merged-and-sorted results. `vector_candidates` and `bm25_candidates` are `[]` in the expansion path (trace simplified, per RESEARCH.md).

**New functions to add — insertion point:** After `rrf_fuse` definition (after line 370), before `_cohere_rerank` (before line 377). Two new functions:

1. `_expand_query(query_text: str) -> list[str]` — calls `anthropic.Anthropic().messages.create(model="claude-haiku-4-5", ...)`. Import `anthropic` at call site (not module top-level — matches the lazy `import cohere` pattern at line 399). Returns `[query_text] + variants[:2]`.

2. `rrf_fuse_with_expansion(conn_str, query_vector, query_text, strategy) -> dict` — entry point when `strategy.query_expansion` is True. Calls `_expand_query`, batch-embeds variants, calls `rrf_fuse` per variant, merges by best `rrf_score` per `chunk_id`, sorts and caps to `strategy.final_k`.

---

### `apps/api/app/api/v1/documents.py` (modify — chain extension)

**Analog:** self — chain dispatch at lines 241-251

**Current chain** (lines 241-251):
```python
chain(
    parse_documents.s(
        str(tenant.id), str(agent.id), str(job.id), document_ids
    ),
    chunk_documents.s(),
    generate_metadata.s(),
    embed_and_migrate.s(),
).apply_async(
    queue="pipeline",
    headers={"request_id": ctx.get("request_id", "")},
)
```

**M9 modification — add import + 5th link:**
```python
from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy

chain(
    parse_documents.s(
        str(tenant.id), str(agent.id), str(job.id), document_ids
    ),
    chunk_documents.s(),
    generate_metadata.s(),
    embed_and_migrate.s(),
    synthesize_retrieval_strategy.s(),    # M9: receives embed result dict
).apply_async(
    queue="pipeline",
    headers={"request_id": ctx.get("request_id", "")},
)
```

The import goes with the other pipeline task imports at lines 65-68. The `.s()` form ensures `synthesize_retrieval_strategy` receives `embed_and_migrate`'s return dict `{"tenant_id", "agent_id", "job_id", "document_ids"}` as its first positional argument.

---

### `apps/api/app/worker/celery_app.py` (modify — register new task module)

**Analog:** self — `include` list at lines 69-88

**Current include list tail** (lines 85-88):
```python
        # M8: deployment checklist task (runtime queue)
        "app.worker.tasks.runtime.deployment",
    ],
```

**M9 addition — append after the M8 entry:**
```python
        # M8: deployment checklist task (runtime queue)
        "app.worker.tasks.runtime.deployment",
        # M9: retrieval strategy synthesis (pipeline queue)
        "app.worker.tasks.pipeline.strategy",
    ],
```

No other changes to `celery_app.py`. The `task_routes` wildcard `"app.worker.tasks.pipeline.*": {"queue": "pipeline"}` (lines 111-112) already routes the new task to the pipeline queue automatically.

---

### `scripts/demo_m9.sh` (new demo script)

**Analog:** `scripts/demo_m8.sh`

**Header/config block** (demo_m8.sh lines 1-35):
```bash
#!/usr/bin/env bash
# demo_m9.sh — Veridian M9 Retrieval Strategy Synthesis demo
#
# Demonstrates STR-01/STR-02/STR-03: two tenants with different corpora
# receive meaningfully different RetrievalStrategy configs; eval comparison
# confirms auto-generated strategy outperforms empty-dict default.
#
# Prerequisites (ALL local — no Docker):
#   1. Redis:    redis-server (running on localhost:6379)
#   2. Postgres: local install (running on localhost:5432)
#   3. API:      uvicorn app.main:app --reload       (from apps/api/)
#   4. Worker:   celery -A app.worker.celery_app worker --queues pipeline,runtime
#
# Required env vars:
#   ADMIN_KEY   — X-Admin-Key header
#   API_KEY     — X-API-Key for tenant auth
#
# Optional env vars:
#   BASE_URL    — FastAPI base URL (default: http://localhost:8000)

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:-}"
API_KEY="${API_KEY:-}"
```

**Prerequisites check** (demo_m8.sh lines 62-79):
```bash
if ! redis-cli ping >/dev/null 2>&1; then
    echo "ERROR: Redis is not reachable. Start with: redis-server"
    exit 1
fi
if ! curl -sf --max-time 5 "$BASE_URL/health" >/dev/null 2>&1; then
    echo "ERROR: FastAPI not reachable at $BASE_URL/health"
    exit 1
fi
```
Copy verbatim.

**Agent creation + polling** (demo_m8.sh lines 87-134):
```bash
AGENT_RESPONSE=$(curl -sf -X POST "$BASE_URL/api/v1/agents" \
    -H "X-Admin-Key: $ADMIN_KEY" \
    -H "Content-Type: application/json" \
    -d '{"name": "...", "soul": {...}, "role": "customer_support"}' 2>/dev/null || echo "")
AGENT_ID=$(echo "$AGENT_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])" ...)

# Poll for agent status (up to 120 seconds)
for i in $(seq 1 24); do
    AGENT_STATUS=$(curl -sf --max-time 5 -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/agents/$AGENT_ID" ...)
    if [[ "$AGENT_STATUS" == "ready" ]]; then break; fi
    sleep 5
done
```
Repeat this block twice — once for Tenant A (dense PDF corpus) and once for Tenant B (FAQ plain-text corpus). Use different soul/name values to distinguish.

**Strategy polling pattern** — after each ingest trigger, poll `GET /agents/{id}` and extract `retrieval_strategy` until it is non-empty `{}`:
```bash
for i in $(seq 1 40); do
    STRATEGY=$(curl -sf --max-time 5 -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID_A" | \
        python -c "import sys,json; d=json.load(sys.stdin); a=d.get('agent',d); print(json.dumps(a.get('retrieval_strategy', {})))" 2>/dev/null || echo "{}")
    if [[ "$STRATEGY" != "{}" ]]; then break; fi
    sleep 3
done
```

**Eval comparison pattern** (STR-03 — from RESEARCH.md Pattern 5):
```bash
# Trigger eval with synthesized strategy
RESULT_A=$(curl -sf -X POST "$BASE_URL/api/v1/agents/$AGENT_ID_A/eval-runs" \
    -H "X-API-Key: $API_KEY")
RUN_ID_A=$(echo "$RESULT_A" | python -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))")

# Patch strategy back to empty (default)
curl -sf -X PATCH "$BASE_URL/api/v1/agents/$AGENT_ID_A" \
    -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
    -d '{"retrieval_strategy": {}}'

# Trigger second eval (default strategy)
RESULT_B=$(curl -sf -X POST "$BASE_URL/api/v1/agents/$AGENT_ID_A/eval-runs" \
    -H "X-API-Key: $API_KEY")
RUN_ID_B=$(echo "$RESULT_B" | python -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))")
```

**Polling loop** (demo_m8.sh lines 175-202):
```bash
MAX_POLLS=60
POLL_COUNT=0
RUN_STATUS="running"

while [[ $POLL_COUNT -lt $MAX_POLLS ]]; do
    POLL_RESP=$(curl -sf --max-time 10 -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID/checklist-runs/$CHECKLIST_RUN_ID" 2>/dev/null || echo "{}")
    RUN_STATUS=$(echo "$POLL_RESP" | python -c "..." 2>/dev/null || echo "running")
    if [[ "$RUN_STATUS" == "complete" ]] || [[ "$RUN_STATUS" == "failed" ]]; then break; fi
    POLL_COUNT=$((POLL_COUNT + 1))
    sleep 3
done
```
Copy this pattern for polling both eval runs (RUN_ID_A and RUN_ID_B) until both are `complete`.

**Assertion section** (demo_m8.sh lines 336-362):
M9 assertions replace M8 assertions:
- `[PASS] Tenant A vector_k != Tenant B vector_k` (STR-02 — configs differ)
- `[PASS] synthesized strategy faithfulness > default strategy faithfulness` (STR-03 — measurably better)
- `[PASS] query_expansion is true for Tenant B (FAQ corpus)` (STR-02)

**python -c JSON extraction** (demo_m8.sh lines 104, 121, 154):
```bash
# Pattern throughout — reuse this exact invocation style
FIELD=$(echo "$RESPONSE" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('field', ''))" 2>/dev/null || echo "")
```
Use `python` (not `python3`) — matches demo_m8.sh convention.

---

## Shared Patterns

### Connection String Security (CTL-08)
**Source:** `apps/api/app/worker/tasks/pipeline/embed.py` (docstring line 36-40) and `apps/api/app/worker/tasks/runtime/deployment.py` (docstring lines 7-8)
**Apply to:** `strategy.py` Celery task and `strategy_service.py`
```
- Task receives result dict containing agent_id only
- conn_str fetched from control DB at runtime via: fernet_decrypt(agent.neon_connection_string)
- conn_str NEVER logged, NEVER in task args, NEVER passed through chain
```

### acks_late + Idempotency (CLAUDE.md non-negotiable)
**Source:** `apps/api/app/worker/tasks/pipeline/embed.py` lines 81-87
**Apply to:** `strategy.py`
```python
@celery_app.task(
    bind=True,
    acks_late=True,       # REQUIRED — message acked AFTER task returns
    max_retries=2,
    default_retry_delay=10,
    queue="pipeline",
)
```
Both `acks_late=True` AND the idempotency guard (`if agent.retrieval_strategy and != {} and not agent.strategy_resynthesis_flagged`) are separately required.

### get_sync_db() ORM Context Manager
**Source:** `apps/api/app/worker/tasks/runtime/deployment.py` lines 91-100, 108-118, 124-129
**Apply to:** `strategy.py`
```python
with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
    ...
    db.commit()
```
Each `with get_sync_db()` block is a separate context (session closed after each `with`). Do not reuse a session across the idempotency check and the final write — open a new `get_sync_db()` context for each DB operation.

### asyncio.run Bridge
**Source:** `apps/api/app/services/deployment_service.py` lines 338-354; `apps/api/app/worker/tasks/runtime/deployment.py` lines 206-218
**Apply to:** `strategy.py`
```python
asyncio.run(
    asyncio.wait_for(
        _run_strategist_loop(signals_json, result_container),
        timeout=60.0,
    )
)
```
Always `asyncio.run(asyncio.wait_for(coro, timeout=N))`. Never `loop.run_until_complete()`. Never `nest_asyncio`. This is Python 3.12 solo pool safe.

### Structured Logging (structlog)
**Source:** `apps/api/app/services/deployment_service.py` line 31; `apps/api/app/worker/tasks/pipeline/embed.py` line 73
**Apply to:** All new files
```python
import structlog
log = structlog.get_logger(__name__)

# Usage:
log.info("synthesize_retrieval_strategy.complete", agent_id=agent_id)
log.warning("synthesize_retrieval_strategy.validation_failed", error=str(val_exc))
log.error("synthesize_retrieval_strategy.agent_not_found", agent_id=agent_id)
```
Log key format: `module_name.event_slug`. Agent IDs are safe to log; conn_str is never logged.

### psycopg2 try/finally/close
**Source:** `apps/api/app/services/retrieval_service.py` lines 178-186; `apps/api/app/services/deployment_service.py` lines 158-185
**Apply to:** `strategy_service.py` corpus signal collection
```python
conn = psycopg2.connect(conn_str, connect_timeout=10)
try:
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        value = int(row[0] or 0)   # defensive coercion — AVG() on empty set returns None
finally:
    conn.close()
```
Never use `with psycopg2.connect(...) as conn:` — that form implicitly starts a transaction context (see embed.py lines 293-299 for the REINDEX CONCURRENTLY isolation requirement explanation).

### Pydantic model_validate with extra="ignore"
**Source:** `apps/api/app/services/retrieval_service.py` lines 50-51
**Apply to:** `strategy.py` — validation of LLM tool output
```python
class RetrievalStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ...
```
`RetrievalStrategy.model_validate(raw)` handles string→bool and string→int coercion by default (Pydantic v2). Wrap in try/except and fall back to `RetrievalStrategy()` defaults on `ValidationError`.

### Chain Pass-Through Return
**Source:** `apps/api/app/worker/tasks/pipeline/embed.py` lines 398-403
**Apply to:** `strategy.py`
```python
return {
    "tenant_id": tenant_id,
    "agent_id": agent_id,
    "job_id": job_id,
    "document_ids": document_ids,
}
```
`synthesize_retrieval_strategy` is the terminal task in M9's chain (no task after it yet). Still return `result` unchanged — convention for chain compatibility and future extension.

### Agent ORM Fields (for M9 writes)
**Source:** `apps/api/app/models/agent.py` lines 47-57
```python
retrieval_strategy: Mapped[dict] = mapped_column(
    JSONB, nullable=False, server_default=text("'{}'::jsonb")
)
strategy_resynthesis_flagged: Mapped[bool] = mapped_column(
    nullable=False, server_default=text("false")
)
```
Both fields exist since M3/M5 migrations. Write both in the same `db.commit()` block: `agent.retrieval_strategy = strategy.model_dump()` and `agent.strategy_resynthesis_flagged = False`.

---

## No Analog Found

All files have close codebase analogs. No entries in this section.

---

## Metadata

**Analog search scope:** `apps/api/app/services/`, `apps/api/app/worker/tasks/pipeline/`, `apps/api/app/worker/tasks/runtime/`, `apps/api/app/api/v1/`, `apps/api/app/models/`, `apps/api/app/worker/`, `scripts/`
**Files scanned:** 7 analog files read in full
**Pattern extraction date:** 2026-05-25
