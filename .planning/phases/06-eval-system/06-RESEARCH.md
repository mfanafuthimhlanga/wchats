# Phase 6: Eval System — Research

**Compiled:** 2026-05-23
**Covers:** All 9 priority areas from the research brief
**Source files read:** 06-CONTEXT.md, REQUIREMENTS.md, CLAUDE.md, retrieval_service.py, neon.py, celery_app.py, config.py, 0004_verified_qa_candidates.py, 0001_tenant_v1_schema.py, 05-CONTEXT.md, 05-02-PLAN.md, 04-07-PLAN.md

---

## 1. Ragas 0.4.x API

### Import paths (D-01 LOCKED)

```python
from ragas.metrics.collections import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
)
```

Do NOT use `from ragas.metrics import Faithfulness` — that is the 0.3.x path and has been removed.

### EvaluationDataset construction

Ragas 0.4.x ships its own dataset type — it does NOT require the HuggingFace `datasets` library as a mandatory dependency (the HF integration was demoted to optional). Build from a list of dicts using `EvaluationDataset.from_list()`:

```python
from ragas import EvaluationDataset

samples = [
    {
        "user_input": scenario["question"],          # the user's query
        "response": scenario["answer"],              # the agent's generated answer
        "retrieved_contexts": scenario["retrieved_contexts"],  # list[str] of chunk content
        "reference": scenario["reference_answer"],  # ground truth answer (D-02 LOCKED: NOT ground_truths)
    }
    for scenario in eval_scenarios
]
dataset = EvaluationDataset.from_list(samples)
```

Field name notes:
- `user_input` (renamed from `question` in 0.3.x)
- `response` (renamed from `answer` in some variants)
- `retrieved_contexts` — a `list[str]`, one string per retrieved chunk
- `reference` — the gold-standard reference answer (D-02 LOCKED — renamed from `ground_truths`)

### Calling `evaluate()`

```python
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from anthropic import Anthropic

# Ragas 0.4.x requires an LLM wrapper. Use Haiku for cost efficiency.
# Option A: Use ragas built-in Anthropic support
from ragas.llms import AnthropicLLM  # if available in 0.4.x build
llm = AnthropicLLM(model="claude-haiku-4-5")

# Option B (safer — known to work): wrap via langchain_anthropic
from langchain_anthropic import ChatAnthropic
from ragas.llms import LangchainLLMWrapper
llm = LangchainLLMWrapper(ChatAnthropic(model="claude-haiku-4-5"))

metrics = [Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()]
results = evaluate(dataset=dataset, metrics=metrics, llm=llm)
```

`evaluate()` returns a `ragas.result.EvaluationResult` object. The object behaves like a dict — `results[metric_name]` returns a `MetricResult`.

### Extracting scores from MetricResult (D-03 LOCKED)

```python
# Overall (mean across all scenarios for a metric):
faithfulness_mean = results["faithfulness"]   # float

# Per-scenario scores (to write to eval_results table):
df = results.to_pandas()
# df has columns: user_input, response, retrieved_contexts, reference,
#                 faithfulness, answer_relevancy, context_precision, context_recall
# Each row = one scenario; each metric column = float score for that row
for idx, row in df.iterrows():
    scenario_id = eval_scenarios[idx]["id"]
    for metric_name in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
        score = row[metric_name]  # float or NaN if not computable
```

Column names in the DataFrame match the metric class names in snake_case: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`.

### What `reference` means

`reference` is the authoritative ground-truth answer used by Faithfulness and ContextRecall to verify that the agent's response is grounded in (and recalls) the relevant context. It is NOT a retrieved document — it is the human-verified correct answer to the question. At scenario generation time, the scenario generator writes a reference answer based on the chunk content it used to generate the question. At eval time, Ragas compares the agent's live response against this reference using the LLM judge.

### Dataset construction — no HuggingFace required

Ragas 0.4.x uses `EvaluationDataset.from_list(list_of_dicts)` natively. The HuggingFace `datasets.Dataset.from_dict()` path still works as an alternative input format but is not required. Using `EvaluationDataset.from_list()` is the canonical 0.4.x approach and avoids a heavy dependency.

---

## 2. Neon Branching API

### How existing neon.py calls the Neon API

`neon.py` uses the `requests` library directly (not the `neon_api` SDK — the SDK was abandoned after discovering it drops HTTP status codes on error). Auth is via Bearer token using `settings.NEON_API_KEY`. All calls go to `https://console.neon.tech/api/v2`. The existing `_neon_headers()` helper returns the correct headers dict and should be reused for branch API calls.

### Create branch endpoint

```
POST https://console.neon.tech/api/v2/projects/{project_id}/branches
Authorization: Bearer {NEON_API_KEY}
Content-Type: application/json

{
    "branch": {
        "name": "eval-{run_id}",
        "parent_id": null   # null = branch from the default branch (main)
    },
    "endpoints": [
        {
            "type": "read_write"
        }
    ]
}
```

The `endpoints` array is required — without it, the branch has no compute endpoint and cannot be queried. `parent_id: null` branches from the project's default branch (the production branch).

### Create branch response

```json
{
    "branch": {
        "id": "br-xxxx-yyyy",
        "project_id": "proj-xxxx",
        "name": "eval-{run_id}",
        "parent_id": "br-parent-id",
        "created_at": "...",
        "updated_at": "..."
    },
    "endpoints": [
        {
            "id": "ep-xxxx",
            "type": "read_write",
            "host": "ep-xxxx.aws-us-east-1.neon.tech"
        }
    ],
    "operations": [...]
}
```

Key response fields:
- `branch.id` — the branch ID needed to delete the branch later
- `endpoints[0].host` — the compute endpoint hostname

### Getting the connection string for the branch

After creating the branch, fetch the connection URI using the same `/connection_uri` endpoint pattern as `create_neon_project()`:

```python
r = requests.get(
    f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
    headers=_neon_headers(),
    params={
        "database_name": "neondb",
        "role_name": "neondb_owner",
        "pooled": "false",        # direct URI for Alembic/psycopg2 in eval tasks
        "branch_id": branch_id,   # the branch.id from the create response
    },
    timeout=15,
)
conn_str = r.json()["uri"]
```

The `branch_id` query parameter scopes the connection URI to the specific branch.

### Branch readiness / polling

Branch creation is asynchronous on Neon. The `operations` array in the create response contains operation IDs — but polling operations is unreliable on free tier (the existing `neon.py` design note explicitly documents this and avoids operations polling). The recommended approach is to use the existing `wait_for_neon_ready(conn_str)` probe loop after fetching the branch connection URI. This is the same pattern used for project creation: fetch the URI immediately, then probe until `SELECT 1` succeeds.

Recommended polling interval: 5 seconds, max 12 attempts (60 seconds total). The existing `wait_for_neon_ready()` uses exponential backoff with a 60s cap — for eval branches (which are warm because they fork from an existing compute) a simpler linear 5s retry is sufficient but using the existing helper avoids code duplication.

### Delete branch endpoint

```
DELETE https://console.neon.tech/api/v2/projects/{project_id}/branches/{branch_id}
Authorization: Bearer {NEON_API_KEY}

# No request body required
# Success: 200 with {"branch": {...}, "operations": [...]}
```

### `neon_service.py` additions (D-17 LOCKED)

Add two methods following the existing patterns in `neon.py`:

```python
def create_branch(project_id: str, branch_name: str) -> tuple[str, str]:
    """Create a Neon branch and return (branch_id, connection_string).
    
    Args:
        project_id: The Neon project ID (stored on agent.neon_project_id).
        branch_name: Human-readable name, e.g. "eval-{run_id}".
    
    Returns:
        (branch_id, conn_str) — branch_id for later deletion;
        conn_str for queries within this eval run only (D-18: never stored).
    
    Raises:
        NeonHTTPError: On any non-2xx response.
    """
    r = requests.post(
        f"{_NEON_API_BASE}/projects/{project_id}/branches",
        headers=_neon_headers(),
        json={
            "branch": {"name": branch_name},
            "endpoints": [{"type": "read_write"}],
        },
        timeout=30,
    )
    if not r.ok:
        raise NeonHTTPError(r.status_code, r.text[:200])
    
    data = r.json()
    branch_id = data["branch"]["id"]
    log.debug("neon.branch_created", project_id=project_id, branch_id=branch_id)
    
    # Fetch connection URI for this branch
    r_uri = requests.get(
        f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
        headers=_neon_headers(),
        params={
            "database_name": "neondb",
            "role_name": "neondb_owner",
            "pooled": "false",
            "branch_id": branch_id,
        },
        timeout=15,
    )
    if not r_uri.ok:
        raise NeonHTTPError(r_uri.status_code, r_uri.text[:200])
    
    return branch_id, r_uri.json()["uri"]


def delete_branch(project_id: str, branch_id: str) -> None:
    """Delete a Neon branch. Called in the eval task finally block (D-10).
    
    Args:
        project_id: The Neon project ID.
        branch_id: The branch ID returned by create_branch().
    
    Raises:
        NeonHTTPError: On any non-2xx response.
    """
    r = requests.delete(
        f"{_NEON_API_BASE}/projects/{project_id}/branches/{branch_id}",
        headers=_neon_headers(),
        timeout=30,
    )
    if not r.ok:
        raise NeonHTTPError(r.status_code, r.text[:200])
    log.debug("neon.branch_deleted", project_id=project_id, branch_id=branch_id)
```

Security note (T-03-02 pattern): Never log the connection string — only `project_id` and `branch_id`.

---

## 3. Celery Beat Configuration

### Adding `beat_schedule` to existing `celery_app.py`

The existing `celery_app.conf.update(...)` dict in `celery_app.py` should be extended with two new keys: `beat_schedule` and the new task module in `include`. This is M6's first use of Celery beat — it has not been used in M1–M5.

```python
from celery.schedules import crontab

# In celery_app.conf.update(...):
include=[
    # ... existing entries ...
    "app.worker.tasks.runtime.eval",   # M6: eval suite + scenario mining
],

beat_schedule={
    "eval-nightly": {
        "task": "app.worker.tasks.runtime.eval.run_eval_suite_beat",
        "schedule": crontab(hour=2, minute=0),  # 02:00 UTC daily
    },
    "mine-scenarios-nightly": {
        "task": "app.worker.tasks.runtime.eval.mine_eval_scenarios_beat",
        "schedule": crontab(hour=1, minute=30),  # 01:30 UTC daily, before eval
    },
},
```

Note: The context file (D-16 deferred) suggests combining mining into `run_eval_suite` for M6 simplicity. If that decision holds, omit the second beat entry and call `mine_eval_scenarios` as a step inside `run_eval_suite_beat` before dispatching individual agent evals.

### Beat dispatch pattern — per-agent fan-out

The beat task is a thin dispatcher that queries the control DB for ready agents and dispatches individual `run_eval_suite` tasks per agent. The beat task itself does minimal work:

```python
@celery_app.task(
    name="app.worker.tasks.runtime.eval.run_eval_suite_beat",
    bind=True,
    acks_late=True,
    queue="runtime",
)
def run_eval_suite_beat(self):
    """Beat-triggered dispatcher: find all ready agents, dispatch per-agent eval."""
    with get_sync_db() as db:
        agents = db.execute(
            select(Agent).where(Agent.status == "ready")
        ).scalars().all()
    
    for agent in agents:
        run_eval_suite.apply_async(
            kwargs={"agent_id": str(agent.id)},
            queue="runtime",
        )
```

The individual `run_eval_suite` task receives only `agent_id` (not connection strings — CLAUDE.md rule CTL-08).

### Celery beat on Windows (solo pool)

Celery beat is a separate process from workers — it does NOT run tasks; it only schedules them by placing messages on the queue. The `worker_pool="solo"` setting in `celery_app.py` applies to the worker process, not the beat process. Beat on Windows runs fine because it never does multiprocessing. The beat process is a single-threaded event loop that wakes up, checks the schedule, and publishes task messages to Redis.

Commands for local dev (from `apps/api/`):
```bash
# Beat (separate terminal, required for M6+)
celery -A app.worker.celery_app beat --loglevel=info

# Workers (as before — two separate terminals)
celery -A app.worker.celery_app worker --queues pipeline --loglevel=info
celery -A app.worker.celery_app worker --queues runtime --loglevel=info
```

Beat does not need `--pool=solo` because it is not a worker.

### Beat database (schedule persistence)

By default Celery beat stores the last-run schedule in a `celerybeat-schedule` file in the CWD. This is fine for local dev. For production, use `django-celery-beat` or `redbeat` — but those are M10 concerns.

---

## 4. verified_qa Retrieval Integration

### Existing retrieval_service.py patterns to match

`retrieval_service.py` uses `psycopg2.connect(conn_str)` with a `try/finally/close` pattern (NOT `with conn:` context manager). Query parameters use `%(name)s` notation. Vectors are stringified as `str(query_vector)` and cast with `::vector`. Results are returned as a list of dicts.

### `verified_qa_lookup` function signature (D-24 LOCKED)

```python
def verified_qa_lookup(
    conn_str: str,
    query_vector: list[float],
    threshold: float,
) -> Optional[dict]:
    """Check verified_qa for a cached answer matching the query.
    
    Called BEFORE hybrid search (D-24). On hit, returns the cached answer
    and updates last_used_at + use_count (D-26). On miss, returns None.
    
    Args:
        conn_str: Decrypted tenant DB connection string.
        query_vector: 1024-dim float vector from embed_query().
        threshold: Cosine similarity threshold (settings.VERIFIED_QA_HIT_THRESHOLD = 0.93).
    
    Returns:
        Dict with keys: answer (str), citations (list), similarity (float)
        or None if no match above threshold.
    """
```

### SQL for the cosine similarity lookup

```sql
-- Lookup query
SELECT id, answer, citations,
       1 - (question_vector <=> %(qv)s::vector) AS similarity
FROM verified_qa
WHERE invalidated_at IS NULL
  AND 1 - (question_vector <=> %(qv)s::vector) >= %(threshold)s
ORDER BY similarity DESC
LIMIT 1
```

### SQL for updating the hit counter (D-26)

After a cache hit, run an UPDATE in the same psycopg2 connection before closing:

```sql
UPDATE verified_qa
SET last_used_at = NOW(), use_count = use_count + 1
WHERE id = %(row_id)s
```

### Full function implementation pattern

```python
def verified_qa_lookup(
    conn_str: str,
    query_vector: list[float],
    threshold: float,
) -> Optional[dict]:
    sql_lookup = """
        SELECT id, answer, citations,
               1 - (question_vector <=> %(qv)s::vector) AS similarity
        FROM verified_qa
        WHERE invalidated_at IS NULL
          AND 1 - (question_vector <=> %(qv)s::vector) >= %(threshold)s
        ORDER BY similarity DESC
        LIMIT 1
    """
    sql_update = """
        UPDATE verified_qa
        SET last_used_at = NOW(), use_count = use_count + 1
        WHERE id = %(row_id)s
    """
    conn = psycopg2.connect(conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(sql_lookup, {
                "qv": str(query_vector),
                "threshold": threshold,
            })
            row = cur.fetchone()
            if row is None:
                return None
            row_id, answer, citations, similarity = row
            cur.execute(sql_update, {"row_id": row_id})
        conn.commit()
    finally:
        conn.close()
    
    return {
        "answer": answer,
        "citations": citations,  # already JSONB — psycopg2 returns as Python dict/list
        "similarity": float(similarity),
        "source": "verified_qa_cache",
    }
```

### Integration into the existing retrieve_and_rank task flow

The `retrieve_and_rank` Celery task (in `apps/api/app/worker/tasks/runtime/retrieve.py`) should call `verified_qa_lookup` as the first step after embedding the query, before `rrf_fuse`. If it returns a non-None result, skip hybrid search entirely and return the cached answer with a trace field `"cache_hit": True`.

The trace payload for a cache hit should include the `similarity` score so the demo can show the cosine score that triggered the cache skip.

---

## 5. Scenario Generation

### Service location

New file: `apps/api/app/services/scenario_service.py`

### Claude API prompt structure for scenario generation

Use the Anthropic API directly (not Agent SDK) with Haiku model (D-12 LOCKED). Force structured output via `tool_choice` — the same pattern used in `validation_service.py`:

```python
HAIKU_MODEL = "claude-haiku-4-5"

SCENARIO_TOOL = {
    "name": "submit_scenarios",
    "description": "Submit generated eval scenarios as structured JSON.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scenarios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "reference_answer": {"type": "string"},
                        "scenario_category": {
                            "type": "string",
                            "enum": ["factual", "edge_case", "out_of_scope", "multi_step"]
                        }
                    },
                    "required": ["question", "reference_answer", "scenario_category"]
                },
                "minItems": 3,
                "maxItems": 10,
            }
        },
        "required": ["scenarios"]
    }
}

def generate_scenarios_from_chunks(chunks: list[dict], n: int = 5) -> list[dict]:
    """Generate n eval scenarios from a batch of chunks using Haiku.
    
    Args:
        chunks: List of dicts with at least 'content' key (chunk text).
        n: Number of scenarios to generate (3-10).
    
    Returns:
        List of dicts: {question, reference_answer, scenario_category, retrieved_contexts}
    """
    # Concatenate chunk content for the prompt
    chunk_text = "\n\n---\n\n".join(
        f"CHUNK {i+1}:\n{c['content']}" for i, c in enumerate(chunks[:5])
    )
    
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        system=(
            "You are an evaluation scenario generator. Given business knowledge base content, "
            "generate realistic customer service questions a user might ask, along with reference "
            "answers grounded in the provided content. Generate exactly the number requested. "
            "Call submit_scenarios with your output."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Generate {n} evaluation scenarios from this knowledge base content.\n\n"
                f"KNOWLEDGE BASE CONTENT:\n{chunk_text}\n\n"
                "For each scenario: write a realistic user question, the correct reference answer "
                "based ONLY on the provided content, and classify the scenario category."
            )
        }],
        tools=[SCENARIO_TOOL],
        tool_choice={"type": "tool", "name": "submit_scenarios"},
    )
    
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_scenarios":
            raw_scenarios = block.input["scenarios"]
            # Attach retrieved_contexts from the source chunks
            chunk_contents = [c["content"] for c in chunks[:5]]
            return [
                {
                    "question": s["question"],
                    "reference_answer": s["reference_answer"],
                    "scenario_category": s["scenario_category"],
                    "retrieved_contexts": chunk_contents,
                    "source": "generated",
                }
                for s in raw_scenarios
            ]
    
    raise ValueError("No tool_use block returned by scenario generator")
```

### Batching strategy

Recommended: process chunks in batches of 5, generate 5 scenarios per batch. For a tenant with 100 chunks: 20 batches x 5 scenarios = 100 scenarios. However, 20-30 scenarios total is usually sufficient for meaningful eval coverage. Implement a cap: generate at most `min(len(chunks) // 3, 30)` scenarios with a floor of 10.

### `eval_scenarios` storage

After generation, store in the tenant DB `eval_scenarios` table with `source='generated'` (D-13). The `retrieved_contexts` list maps to the `retrieved_contexts JSONB` column.

---

## 6. Production Conversation Mining

### The cross-DB join challenge

`job_events` lives in the **control DB** (accessed via `get_sync_db()` / `CONTROL_DB_SYNC_URL`). `messages` and `conversations` live in each **tenant DB** (per-Neon-project, accessed via the per-agent `neon_connection_string`).

These are separate Postgres servers — no JOIN across them is possible in SQL. The mining task must:
1. Query the control DB for failed validation events to get `conversation_id` values.
2. Query the tenant DB using those `conversation_id` values to fetch the actual messages.

### Step 1 — Control DB query for flagged conversations

```sql
-- In control DB: find conversations with validation failures
SELECT DISTINCT
    je.payload->>'conversation_id' AS conversation_id,
    je.payload->>'question'        AS question,
    je.payload->>'verdict'         AS verdict
FROM job_events je
WHERE je.event_type IN ('gatekeeper.complete', 'auditor.complete')
  AND je.payload->>'verdict' IN ('fail', 'ungrounded', 'partial')
  AND je.agent_id = %(agent_id)s
  AND je.created_at > NOW() - INTERVAL '24 hours'
```

Note: The actual `job_events` column structure should be verified against the M5 implementation. The M5 validators emit events via `app.services.events.emit()`. The payload likely contains `conversation_id` as a top-level key based on M5 validator patterns. Confirm the exact payload shape in `validators.py` before writing the mining query.

### Step 2 — Tenant DB query for message content

```python
def _fetch_messages_for_conversation(
    tenant_conn_str: str,
    conversation_id: str,
) -> list[dict]:
    """Fetch user question + agent response from a conversation."""
    sql = """
        SELECT role, content, created_at
        FROM messages
        WHERE conversation_id = %(conv_id)s::uuid
        ORDER BY created_at ASC
    """
    conn = psycopg2.connect(tenant_conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {"conv_id": conversation_id})
            rows = cur.fetchall()
    finally:
        conn.close()
    
    return [{"role": row[0], "content": row[1]} for row in rows]
```

### Mining task logic

```python
def mine_eval_scenarios(agent_id: str, tenant_conn_str: str, control_db) -> list[dict]:
    """Mine mined scenarios from production conversations with validation failures.
    
    Returns list of dicts ready to insert into eval_scenarios with source='mined'.
    """
    # Step 1: Get flagged conversation IDs from control DB
    flagged = control_db.execute(
        text("""
            SELECT DISTINCT 
                payload->>'conversation_id' as conversation_id,
                payload->>'question' as question
            FROM job_events
            WHERE event_type IN ('gatekeeper.complete', 'auditor.complete')
              AND payload->>'verdict' IN ('fail', 'ungrounded', 'partial')
              AND agent_id = :agent_id
              AND created_at > NOW() - INTERVAL '7 days'
        """),
        {"agent_id": agent_id}
    ).fetchall()
    
    mined = []
    for row in flagged:
        conv_id, question = row.conversation_id, row.question
        if not conv_id or not question:
            continue
        
        # Step 2: Get messages from tenant DB
        messages = _fetch_messages_for_conversation(tenant_conn_str, conv_id)
        
        # Extract assistant response
        turns = {m["role"]: m["content"] for m in messages}
        if "user" not in turns or "assistant" not in turns:
            continue
        
        # Use the question from job_events payload (already extracted by validators)
        # Generate a reference answer via Haiku (similar to scenario generation)
        # Or store with empty reference_answer and flag for human review
        mined.append({
            "question": question or turns["user"],
            "reference_answer": "",  # mined scenarios have no ground truth initially
            "retrieved_contexts": [],  # populated when scenario is run
            "source": "mined",
            "scenario_category": "production_failure",
        })
    
    return mined
```

### Important: M5 job_events payload structure

Before implementing the mining query, read `apps/api/app/worker/tasks/runtime/validators.py` in full to verify the exact payload keys emitted by `call_gatekeeper`, `call_auditor`, and `call_strategist`. The M5 PLAN confirms validators emit events via `app.services.events.emit()` with a payload dict, but the exact structure (whether `conversation_id` and `question` are top-level payload keys vs nested) must be confirmed from the actual code.

### Mined scenario reference answers

Mined scenarios initially have no `reference_answer`. Two options:
1. Store `reference_answer = ""` and skip those scenarios in Ragas evaluation (ContextRecall and Faithfulness require a reference).
2. Use the agent's actual response as a provisional reference and flag it for later human review.

Recommended for M6: use Haiku to generate a reference answer from the retrieved contexts stored in the job_events payload (if available), falling back to the agent response text. This keeps the scenario runnable immediately.

---

## 7. Celery Beat + Solo Pool Details

### Beat is independent of worker pool

The `worker_pool="solo"` setting in `celery_app.py` configures the **worker** process's execution model. The **beat** process is entirely separate:
- Beat has no pool — it is single-threaded and never executes tasks
- Beat reads the `beat_schedule` from `celery_app.conf` and publishes messages to Redis broker
- Beat process command: `celery -A app.worker.celery_app beat --loglevel=info`
- Worker process command: `celery -A app.worker.celery_app worker --queues runtime --loglevel=info`

### Beat on Windows

Beat works correctly on Windows. The Windows-specific billiard bug (documented in `celery_app.py` module header) only affects the worker's prefork pool — which was fixed by setting `worker_pool="solo"`. Beat never forks, so it is not affected.

### Beat schedule storage

Default: `celerybeat-schedule` file in CWD (`apps/api/`). This file is a shelve database tracking last-run times. Add it to `.gitignore` if not already present.

### Beat in the dev startup sequence

M6 adds beat as a required local process. The startup sequence becomes:
1. `redis-server` (or use existing Redis)
2. PostgreSQL (already running)
3. `uvicorn app.main:app --reload` (FastAPI)
4. `celery -A app.worker.celery_app worker --queues pipeline --loglevel=info`
5. `celery -A app.worker.celery_app worker --queues runtime --loglevel=info`
6. `celery -A app.worker.celery_app beat --loglevel=info` **← NEW in M6**
7. `pnpm dev` in `apps/admin/` (Next.js admin)

For the demo script (`scripts/demo_m6.sh`), the beat trigger should be simulated by calling `run_eval_suite.apply_async()` directly rather than waiting for the nightly schedule.

---

## 8. Existing M4 Eval Harness Relationship

### M4 harness (test-time, offline)

Location: `apps/api/tests/evals/`
- `judge.py` — LLM judge calling `claude-sonnet-4-5-20251001` directly
- `run_evals.py` — pytest-based harness with 20 pre-defined scenario JSON files
- `scenarios/S-001_*.json` through `S-020_*.json` — static JSON files committed to git
- `fixtures/demo_business_tenant.sql` — Bella Vista Coffee demo corpus

Purpose: Proves the M4 agent meets 8 evaluation dimensions (D1-D8). Runs as `pytest tests/evals/` or with `AGENT_E2E_ENABLED=1`. This is a **developer tool** — run manually or in CI.

### M6 harness (production runtime, Celery-scheduled)

Location: new files under `apps/api/app/`
- `app/services/eval_service.py` — Ragas 0.4.x harness (4 metrics)
- `app/services/scenario_service.py` — scenario generator + production mining
- `app/worker/tasks/runtime/eval.py` — Celery tasks (`run_eval_suite`, `run_eval_suite_beat`, `generate_eval_suite`, `mine_eval_scenarios_beat`)
- Tenant DB tables `eval_scenarios` and `verified_qa` (migration 0005)
- Existing tables `eval_runs` and `eval_results` (migration 0001 — already exist)

Purpose: Production quality gate that runs automatically every night, seeds `verified_qa` from passing scenarios, and feeds the retrieval cache. This is a **production system** — not a developer test tool.

### They coexist and serve different purposes

| Dimension | M4 Harness | M6 Harness |
|-----------|------------|------------|
| Triggered by | Developer / CI | Celery beat (nightly) |
| Scenarios | Static JSON files in git | Dynamic rows in `eval_scenarios` DB table |
| Metrics | Custom LLM judge (8 dimensions) | Ragas 0.4.x (4 metrics: Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall) |
| Output | Terminal report + pass/fail | `eval_runs` + `eval_results` DB rows + admin UI |
| Side effects | None | Promotes passing scenarios to `verified_qa` |
| Isolation | No Neon branching | Neon branch per run (D-10) |
| LLM model | Sonnet (judge) | Haiku (eval LLM via Ragas) |

M6 does NOT replace M4. M4 scenarios (`S-001` through `S-020`) can be used as seed data for the M6 `eval_scenarios` DB table if desired, but this is not required.

### Seeding `eval_scenarios` from M4 scenarios

The `generate_eval_suite` Celery task (dispatched at agent build time) can optionally bootstrap with a subset of M4's static scenarios converted to the DB format. The M4 scenario fields map as follows:
- `turns[0].message` → `question`
- `human_label_notes` or generated text → `reference_answer`
- `[]` → `retrieved_contexts` (populated at eval run time)
- `"generated"` → `source`

---

## 9. Admin UI Patterns

### Existing admin UI patterns

The admin is Next.js 16.2.6 with Clerk auth and TanStack Query. Pages are `'use client'` components that use `useAuth()` to get a Bearer token, then fetch from FastAPI via `fetch()` wrapped in TanStack `useQuery`. No SWR — only `@tanstack/react-query` is used.

Existing pattern from `apps/admin/app/agents/page.tsx`:
```tsx
'use client'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'

const { getToken, isLoaded, isSignedIn } = useAuth()
const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

const query = useQuery({
  queryKey: ['agents'],
  queryFn: async () => {
    const token = await getToken()
    const res = await fetch(`${apiBase}/api/v1/...`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    return res.json()
  },
  enabled: isLoaded && !!isSignedIn,
  staleTime: 30_000,
})
```

### Page location (D-29 LOCKED)

The stub page already exists at `apps/admin/app/agents/[id]/eval/page.tsx` and currently shows a "Coming soon" placeholder. M6 replaces this with the full eval dashboard.

Note: The existing path is `/agents/[id]/eval/` (without the `s`) — the context doc says `/agents/[id]/evals` (with `s`). The existing file is at `eval/page.tsx`. Keep the existing path (`/agents/[id]/eval`) unless the context decision explicitly requires renaming.

### Recharts installation

Recharts is NOT in `apps/admin/package.json` — it must be added:
```bash
cd apps/admin && pnpm add recharts
```

The package.json currently has: `@clerk/nextjs`, `@tanstack/react-query`, `lucide-react`, `next`, `react`, `react-dom`. No chart library is present.

### Data shape for eval dashboard

Two API endpoints needed (D-30 LOCKED):

**GET /api/v1/agents/{id}/eval-runs** — list of runs with aggregate scores:
```json
{
  "eval_runs": [
    {
      "id": "uuid",
      "started_at": "2026-05-23T02:00:00Z",
      "finished_at": "2026-05-23T02:04:31Z",
      "status": "complete",
      "scenario_count": 25,
      "aggregate_scores": {
        "faithfulness": 0.87,
        "answer_relevancy": 0.91,
        "context_precision": 0.83,
        "context_recall": 0.79
      }
    }
  ]
}
```

**GET /api/v1/agents/{id}/eval-runs/{run_id}/results** — per-scenario results:
```json
{
  "results": [
    {
      "scenario_id": "uuid",
      "question": "What is your return policy?",
      "source": "generated",
      "scores": {
        "faithfulness": 0.95,
        "answer_relevancy": 0.88,
        "context_precision": 0.90,
        "context_recall": 0.85
      },
      "passed": true
    }
  ]
}
```

### Pass rates time-series chart shape (D-29, D-31)

```tsx
// Data shape for Recharts LineChart
const chartData = evalRuns.map(run => ({
  date: new Date(run.started_at).toLocaleDateString(),
  faithfulness: run.aggregate_scores.faithfulness,
  answer_relevancy: run.aggregate_scores.answer_relevancy,
  context_precision: run.aggregate_scores.context_precision,
  context_recall: run.aggregate_scores.context_recall,
}))

// Recharts import
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
```

### FastAPI route implementation

These routes should live in a new file `apps/api/app/api/v1/evals.py` following the pattern of `agents.py`:

```python
router = APIRouter(tags=["evals"])

@router.get("/agents/{agent_id}/eval-runs")
async def list_eval_runs(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # Verify tenant owns the agent (IDOR prevention)
    # Fetch eval_runs from tenant DB via agent.neon_connection_string
    # Aggregate scores from eval_results JOIN eval_runs
    ...

@router.get("/agents/{agent_id}/eval-runs/{run_id}/results")
async def get_eval_run_results(
    agent_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # Fetch from tenant DB: eval_results JOIN eval_scenarios WHERE eval_run_id = run_id
    ...
```

Note: The `eval_runs` and `eval_results` tables are in the **tenant DB** (per-Neon-project), NOT the control DB. The FastAPI route must use `psycopg2.connect(fernet_decrypt(agent.neon_connection_string))` to query them — the same pattern used in `validators.py` for querying the tenant DB. The control DB's `get_async_db()` only reaches the control DB (tenants, agents, jobs, job_events).

---

## 10. File Structure — New Files to Create

```
apps/api/
  app/
    services/
      eval_service.py            # Ragas 0.4.x harness (EVL-01)
      scenario_service.py        # Scenario generator + mining (EVL-02, EVL-03)
    worker/
      tasks/
        runtime/
          eval.py                # run_eval_suite, run_eval_suite_beat, generate_eval_suite, mine_eval_scenarios_beat tasks (EVL-04)
    api/
      v1/
        evals.py                 # GET /agents/{id}/eval-runs, GET /agents/{id}/eval-runs/{run_id}/results (EVL-06, EVL-07)
  alembic_tenant/
    versions/
      0005_verified_qa_eval_scenarios.py  # verified_qa + eval_scenarios tables (D-05, D-06)

apps/admin/
  app/
    agents/
      [id]/
        eval/
          page.tsx               # REPLACE existing stub with full dashboard (D-29)

scripts/
  demo_m6.sh                    # Demo script (D-32, EVL-08)
```

### Files to modify (not create)

```
apps/api/app/services/neon.py              # add create_branch, delete_branch (D-17)
apps/api/app/services/retrieval_service.py # add verified_qa_lookup (D-24)
apps/api/app/core/config.py                # add EVAL_FAITHFULNESS_THRESHOLD, EVAL_RELEVANCY_THRESHOLD, VERIFIED_QA_HIT_THRESHOLD (D-28)
apps/api/app/worker/celery_app.py          # add beat_schedule + include eval task module (D-19)
apps/api/app/worker/tasks/runtime/retrieve.py  # call verified_qa_lookup before hybrid search (D-24)
apps/api/app/api/v1/__init__.py or router  # register evals router
apps/admin/package.json                    # add recharts dependency
```

---

## 11. Key Implementation Pitfalls and Constraints

### Ragas LLM dependency

Ragas 0.4.x requires an LLM for metric computation (Faithfulness, AnswerRelevancy use LLM-based scoring). The `llm=` parameter is mandatory in `evaluate()`. Using `langchain-anthropic` with `ChatAnthropic(model="claude-haiku-4-5")` is the safest approach given the existing Anthropic setup in the project.

### eval_runs.scenario_id column type

The existing `eval_results` table (migration 0001) has `scenario_id TEXT NOT NULL`. The new `eval_scenarios` table has `id UUID`. When writing results, cast: `str(scenario.id)` for the `scenario_id` column. Alternatively, consider an Alembic migration 0005 that alters `eval_results.scenario_id` to UUID type — but changing a column type on an existing table has risks. The simpler approach is to keep it as TEXT and store the UUID string.

### Neon branch isolation scope

The branch connection string is fetched inside `run_eval_suite`, passed as a local variable to `eval_service.run_eval_for_agent(branch_conn_str, ...)`, and deleted in the `finally` block. It is NEVER stored in the DB, NEVER passed as a Celery task argument, NEVER logged (T-03-02 pattern). The eval service receives it as a direct function argument from within the same task execution.

### Ragas 0.4.x LLM rate limits

Running 20-30 scenarios through 4 Ragas metrics means approximately 80-120 LLM judge calls per eval run. At Haiku pricing this is inexpensive (~$0.01-0.05 per run) but can hit Anthropic rate limits if running many agents concurrently. Add `time.sleep(0.1)` between scenario batches or use Ragas's built-in `async_run=False` mode with a retry decorator.

### verified_qa HNSW index maintenance

The `verified_qa` table's HNSW index (`verified_qa_vector_idx`) is built at table creation time (migration 0005). As rows are inserted via promotion, the index updates automatically. No manual index maintenance is needed for M6 volumes (hundreds of rows).

### Admin UI — tenant DB queries from FastAPI

The eval dashboard FastAPI routes must query the **tenant DB** directly (not the control DB). The pattern is the same as `validators.py`:
1. Fetch agent from control DB: `agent = await db.get(Agent, agent_id)` — verify tenant ownership
2. Decrypt connection string: `conn_str = fernet_decrypt(agent.neon_connection_string)`
3. Query tenant DB with psycopg2: `conn = psycopg2.connect(conn_str)` with try/finally

This means the eval API routes are synchronous DB calls inside async FastAPI handlers. Use `asyncio.to_thread()` to avoid blocking the event loop, following the pattern established in agent.py for CPU-bound operations.

### Celery beat schedule — avoid running during business hours

Setting the beat schedule to `crontab(hour=2, minute=0)` (02:00 UTC) avoids peak traffic hours for US East business tenants. Mining at 01:30 UTC before the eval run ensures fresh scenarios are available.

---

## RESEARCH COMPLETE
