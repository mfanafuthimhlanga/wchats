# Phase 8: Pre-deployment Checklist + Human Validation — Research

**Researched:** 2026-05-23
**Domain:** Claude Agent SDK orchestrator, Celery runtime task, FastAPI IDOR-safe routes, Next.js tabbed UX, Alembic control DB migration
**Confidence:** HIGH — all findings verified against codebase; no external library lookups required; entire pattern set exists in M6/M7 code

## Summary

Phase 8 is an integration milestone, not an invention milestone. Every pattern it requires has already been built in M6 (eval task, eval routes) and M7 (Agent SDK orchestrator service, Celery red-team task, FastAPI IDOR routes). The planner should treat Phase 8 as a structured assembly of those proven patterns with the specific data model, blocking logic, and UX prescribed by CONTEXT.md.

The orchestrator (`deployment_service.py`) follows the exact `report_finding` side-effect tool pattern from `red_team_service.py`. Signals are collected with synchronous psycopg2 (safe in Celery tasks), then passed as structured JSON context to the Agent SDK Sonnet agent. The Celery task (`run_deployment_checklist`) mirrors `run_red_team` step-for-step. The five FastAPI routes mirror `red_team.py` plus two new mutation routes (acknowledge, approve). The admin UI tab is additive — a third tab prepended to the existing `page.tsx` which currently has `embed` and `design` tabs.

The only new concept is the `checklist_runs` table living in the **control DB** (not tenant DB), because deployment state is platform-level metadata. All signal queries go to the tenant DB via psycopg2, but the run record itself sits in the control DB and is managed with SQLAlchemy ORM (like `agents`, `tenants`, `jobs`).

**Primary recommendation:** Follow M7 patterns exactly. Do not invent new patterns. The research confirms all needed code patterns already exist and are verified-working.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Signal collection (eval, red team, corpus stats) | API / Backend (Celery task) | — | psycopg2 tenant DB queries; blocking I/O is fine in Celery sync task |
| Orchestrator reasoning (ship/block/warn) | API / Backend (Celery task) | — | Agent SDK call via asyncio.run bridge inside Celery task |
| Checklist run lifecycle state | API / Backend (control DB) | — | checklist_runs is platform metadata, not per-tenant data |
| Warning acknowledgment state | API / Backend (control DB) | — | warning_acknowledgments JSONB on checklist_runs row |
| Deployment approval, is_deployed flip | API / Backend (control DB) | — | agents.is_deployed updated on approve route |
| Pre-Deploy tab UX | Frontend Server (Next.js) | Browser / Client | React state machine with polling; Clerk Bearer auth |
| iframe snippet generation | API / Backend | — | Server-side: avoids client-side string construction |

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Orchestrator Architecture (DEP-01)**
- Uses Claude Agent SDK (Sonnet) — required by DEP-01
- `SONNET_MODEL = "claude-sonnet-4-6"` — same constant as `red_team_service.py`
- `claude-agent-sdk==0.1.81` PINNED
- `asyncio.run(asyncio.wait_for(..., timeout=120.0))` bridge in Celery task
- `submit_report` is a side-effect tool — runner captures `ToolUseBlock`, writes to `result_container`, no tool result sent back (same as M7 `report_finding` pattern)
- Signals collected programmatically FIRST (psycopg2), then passed as structured JSON context

**Signal Collection Functions (sync psycopg2 — safe in Celery)**
- `_fetch_eval_summary_sync(agent_id, conn_str)` — tenant DB `eval_runs` + `eval_results`
- `_fetch_red_team_summary_sync(agent_id, conn_str)` — tenant DB `red_team_runs`
- `_fetch_verified_qa_stats_sync(agent_id, conn_str)` — tenant DB `verified_qa`
- `_fetch_corpus_stats_sync(agent_id, conn_str)` — tenant DB `documents` + `chunks`

**Blocking Condition (DEP-03)**
- `block` when: `red_team_summary.deployment_blocked == True` OR any eval metric < 0.70
- `DEP_BLOCK_ON_HIGH_RED_TEAM: bool = True` — when True, `high_count > 0` also triggers block
- Warning thresholds: `verified_qa_row_count < 50`, eval metric in [0.70, 0.85), medium_count > 2

**Data Model — Control DB Migration 0011**
- `checklist_runs` in control DB (not tenant DB)
- `id UUID PK DEFAULT gen_random_uuid()`, `agent_id UUID NOT NULL`, `status TEXT NOT NULL DEFAULT 'running'`
- `recommendation TEXT` — `'ship' | 'ship_with_warnings' | 'block'` | NULL
- `report JSONB`, `warnings JSONB NOT NULL DEFAULT '[]'`
- `warning_acknowledgments JSONB NOT NULL DEFAULT '{}'`
- `all_warnings_acknowledged BOOLEAN NOT NULL DEFAULT false`
- `approved_at TIMESTAMPTZ`, `approved_by TEXT`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Index: `checklist_runs_agent_id_idx` on `agent_id`
- `agents.is_deployed BOOLEAN NOT NULL DEFAULT false`

**Celery Task (run_deployment_checklist)**
- `runtime` queue, `acks_late=True`, idempotency: skip if `status='running'` row exists within 60 min
- No beat schedule entry — owner-triggered only
- Control DB used for checklist_runs (psycopg2 or SQLAlchemy ORM pattern TBD — ORM preferred since checklist_runs is control DB)

**FastAPI Routes (5 routes, IDOR-checked)**
- `POST /api/v1/agents/{agent_id}/checklist-runs` → 202
- `GET /api/v1/agents/{agent_id}/checklist-runs` → list (limit 10)
- `GET /api/v1/agents/{agent_id}/checklist-runs/{run_id}` → detail
- `POST /api/v1/agents/{agent_id}/checklist-runs/{run_id}/acknowledge` → JSONB update
- `POST /api/v1/agents/{agent_id}/approve-deployment` → flip is_deployed + return iframe_snippet

**Admin UI: Pre-Deploy Tab**
- Third tab: "Pre-Deploy" | "Embed Code" | "Customise Widget" (Pre-Deploy is first/default)
- Five states: no-run / running (spinner, poll 3s) / blocked / ship_with_warnings / ship / approved
- Clerk Bearer token on all API calls

**Demo Architecture**
- `scripts/demo_m8.sh` — local processes only, no Docker
- Guarded E2E: `DEP_E2E_ENABLED`

### Claude's Discretion
- Exact orchestrator system prompt wording
- Whether to display `verified_qa_stats` in the admin UI signal cards
- Warning category names (recommended: `"eval_quality"`, `"security"`, `"knowledge_depth"`, `"corpus_coverage"`)
- Whether to use `asyncio.to_thread` or direct sync psycopg2 for signal collection in Celery task (sync is fine)
- Whether `demo_m8.sh` Section 1 creates a new agent or reuses the Acme Consulting agent from M7

### Deferred Ideas (OUT OF SCOPE)
- Verified QA candidate review UI (M10)
- Weekly digest emails (M10)
- Retrieval strategy synthesis (M9)
- Automatic re-trigger when new red team / eval results arrive (post-v1)
- Deployment rollback route (post-v1)
- Latency stats from Langfuse (M10)
- Per-agent beat schedule for pre-deploy re-validation (post-v1)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEP-01 | Orchestrator agent (Claude Agent SDK, Sonnet) reads eval results, red team findings, and corpus coverage analysis | Agent SDK pattern: `red_team_service.py` runner functions + `asyncio.run(asyncio.wait_for(...))` bridge. Signal collection via sync psycopg2. |
| DEP-02 | Orchestrator writes structured deployment recommendation: `ship \| ship_with_warnings \| block` | Side-effect tool pattern (`submit_report` ToolUseBlock captured into `result_container`) produces `DeploymentReport` Pydantic model. |
| DEP-03 | `block` triggered by: critical eval failure or high/critical red team finding | Blocking logic in `run_deployment_checklist` task after signals collected. `DEP_BLOCK_ON_HIGH_RED_TEAM` config flag. |
| DEP-04 | Owner sees plain-language deployment report in admin UI with expandable technical detail | Pre-Deploy tab in `page.tsx` renders `report` JSONB from GET checklist-runs detail endpoint. |
| DEP-05 | Owner acknowledges each warning individually before `ship_with_warnings` proceeds; acknowledgments logged | POST /acknowledge route updates `warning_acknowledgments` JSONB; `all_warnings_acknowledged` recalculated. Approve route enforces this. |
| DEP-06 | On approval, iframe widget snippet is shown and the agent goes live | POST /approve-deployment flips `agents.is_deployed=true`; returns `iframe_snippet` from `_make_iframe_snippet(agent_id)`. |
| DEP-07 | A non-technical tester completes full journey unassisted: signup → ingest → deploy → widget live | `demo_m8.sh` with clear assertions at each step. |
| DEP-08 | Demo: recorded video of non-developer completing canonical happy path | `demo_m8.sh` is the script basis; video is a human checkpoint in the demo plan. |
</phase_requirements>

---

## Standard Stack

### Core (all pinned in pyproject.toml — verified)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| claude-agent-sdk | 0.1.81 | Orchestrator Sonnet agent loop | PINNED — M7 uses this exact version, tested working [VERIFIED: pyproject.toml] |
| anthropic | latest (transitively) | Direct API calls (signal collection judges if needed) | Already in project [VERIFIED: pyproject.toml] |
| psycopg2-binary | 2.9.12 | Synchronous tenant DB signal queries in Celery | Already used in run_red_team [VERIFIED: pyproject.toml] |
| SQLAlchemy (async) | via fastapi/alembic pin | Control DB checklist_runs read/write in FastAPI routes | Already used in all routes [VERIFIED: pyproject.toml] |
| pydantic | >=2.0,<3.0 | DeploymentReport, DeploymentWarning models | Already in project [VERIFIED: pyproject.toml] |
| fastapi | 0.136.1 | 5 new routes | Already in project [VERIFIED: pyproject.toml] |
| celery[redis] | 5.6.3 | run_deployment_checklist task | Already in project [VERIFIED: pyproject.toml] |
| structlog | 25.5.0 | Structured logging | Already in project [VERIFIED: pyproject.toml] |
| alembic | 1.18.4 | Control DB migration 0011 | Already in project [VERIFIED: pyproject.toml] |

**No new dependencies needed.** Phase 8 uses only what is already installed. [VERIFIED: codebase inspection]

### Frontend Stack

| Library | Version | Purpose |
|---------|---------|---------|
| Next.js | (existing) | Pre-Deploy tab in `apps/admin/app/agents/[id]/deploy/page.tsx` |
| @clerk/nextjs | (existing) | `useAuth()` + `getToken()` for Bearer token on API calls |
| React | (existing) | State machine: `checklistState`, `runId`, `warnings`, `acknowledged` |

**No new frontend dependencies needed.**

---

## Architecture Patterns

### System Architecture Diagram

```
Admin UI (Next.js)
  Pre-Deploy Tab
  |
  | POST /api/v1/agents/{id}/checklist-runs (202)
  v
FastAPI → run_deployment_checklist.apply_async(kwargs={"agent_id": str(id)}, queue="runtime")
  |
  | Celery runtime worker (solo pool)
  v
run_deployment_checklist task
  1. Fetch agent from control DB (SQLAlchemy sync)
  2. Decrypt conn_str (fernet_decrypt)
  3. Idempotency check: SELECT FROM checklist_runs (control DB psycopg2)
  4. INSERT checklist_runs (status='running') → returns run_id
  5. Collect signals synchronously (psycopg2 → tenant DB):
     _fetch_eval_summary_sync  ──┐
     _fetch_red_team_summary_sync │→ signals_dict (JSON)
     _fetch_verified_qa_stats_sync│
     _fetch_corpus_stats_sync  ──┘
  6. asyncio.run(asyncio.wait_for(
       run_orchestrator(signals_dict, result_container), timeout=120.0))
     |
     | Agent SDK Sonnet (claude-sonnet-4-6)
     | system_prompt: readiness orchestrator instructions
     | tools: [submit_report]
     | ToolUseBlock(name="submit_report") captured → result_container
     v
     DeploymentReport (recommendation, summary, warnings)
  7. UPDATE checklist_runs: status='complete', recommendation, report, warnings
  8. On exception: UPDATE checklist_runs status='failed'
  |
  | Admin UI polls GET /checklist-runs/{run_id} every 3s
  v
Approval gate (DEP-05, DEP-06):
  POST /acknowledge → update warning_acknowledgments JSONB
  POST /approve-deployment → validate → agents.is_deployed=true → return iframe_snippet
```

### Recommended Project Structure

```
apps/api/app/
├── services/
│   └── deployment_service.py      # Agent SDK orchestrator + signal collectors
├── worker/tasks/runtime/
│   └── deployment.py              # run_deployment_checklist Celery task
├── api/v1/
│   └── deployment.py              # 5 FastAPI routes
├── schemas/
│   └── deployment.py              # ChecklistRunResponse, ChecklistRunListResponse, etc.
├── models/
│   └── checklist_run.py           # ChecklistRun ORM model (control DB)
├── alembic/versions/
│   └── 0011_checklist_runs_is_deployed.py  # control DB migration
└── core/
    └── config.py                  # + DEP_BLOCK_ON_HIGH_RED_TEAM: bool = True

apps/admin/app/agents/[id]/deploy/
└── page.tsx                       # + Pre-Deploy tab (third tab, rendered first)

scripts/
└── demo_m8.sh

apps/api/tests/unit/
├── test_deployment_service.py     # service unit tests
└── test_deployment_task.py        # Celery task unit tests
apps/api/tests/integration/
└── test_deployment_e2e.py         # DEP_E2E_ENABLED guard
```

### Pattern 1: Side-Effect Tool (submit_report)

The orchestrator uses the exact same pattern as `run_prompt_injection_agent` in `red_team_service.py`. The agent calls `submit_report` as a tool; the runner captures the `ToolUseBlock` from `AssistantMessage.content` and writes to `result_container`. No tool result is sent back to the SDK loop.

```python
# Source: apps/api/app/services/red_team_service.py (verified working pattern)
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
        await client.query(f"Here are the quality signals:\n\n{signals_json}\n\nAssess deployment readiness and call submit_report.")
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "submit_report":
                        result_container["report"] = block.input
                        # Side-effect only — no tool result sent back


def run_orchestrator(signals_json: str, result_container: dict) -> None:
    """Bridge: called via asyncio.run(asyncio.wait_for(..., timeout=120.0)) in Celery task."""
    asyncio.run(
        asyncio.wait_for(
            _run_orchestrator_loop(signals_json, result_container),
            timeout=120.0,
        )
    )
```

### Pattern 2: Celery Runtime Task with Control DB + Tenant DB

`run_deployment_checklist` is structurally identical to `run_red_team` with one important difference: the checklist_runs row lives in the **control DB** (not tenant DB). Signal fetches go to tenant DB via psycopg2. The run row is written to control DB.

```python
# Source: apps/api/app/worker/tasks/runtime/red_team.py (verified pattern)
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.deployment.run_deployment_checklist",
)
def run_deployment_checklist(self, agent_id: str) -> dict:
    # Step 1: Fetch agent + decrypt conn_str (control DB, SQLAlchemy sync)
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            return {}
        conn_str = fernet_decrypt(agent.neon_connection_string)

    # Step 2: Idempotency check (control DB via psycopg2 or ORM)
    # SELECT FROM checklist_runs WHERE agent_id=X AND status='running'
    #   AND created_at > now() - interval '60 minutes'

    # Step 3: INSERT checklist_runs (status='running') in control DB
    # Step 4: Collect 4 signals synchronously (psycopg2 tenant DB)
    # Step 5: asyncio.run(asyncio.wait_for(run_orchestrator(...), timeout=120.0))
    # Step 6: UPDATE checklist_runs status='complete'
    # Step 7 (except): UPDATE checklist_runs status='failed'
```

**Critical difference from red_team.py:** The idempotency check and run row writes target the control DB (not tenant DB). Signal collection functions use psycopg2 against the tenant DB. The control DB operations can use either psycopg2 (via `CONTROL_DB_SYNC_URL`) or SQLAlchemy sync ORM (`get_sync_db()`). The ORM approach is preferred since ChecklistRun is a control DB model.

### Pattern 3: FastAPI IDOR Routes

```python
# Source: apps/api/app/api/v1/red_team.py (verified pattern)

@router.post("/agents/{agent_id}/checklist-runs", status_code=202)
async def trigger_checklist_run(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:            # IDOR prevention
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != "ready":
        raise HTTPException(status_code=400, detail="Agent must be ready")

    task = run_deployment_checklist.apply_async(
        kwargs={"agent_id": str(agent_id)},
        queue="runtime",
    )
    # INSERT checklist_runs row with status='queued' — or let task do it
    return {"checklist_run_id": task.id, "status": "queued"}
```

**Approve route pattern:**
```python
@router.post("/agents/{agent_id}/approve-deployment")
async def approve_deployment(
    agent_id: UUID,
    body: ApproveDeploymentRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # IDOR check (same pattern)
    # Fetch checklist_run by body.checklist_run_id
    # Validate: status='complete', recommendation != 'block'
    # If ship_with_warnings: check all_warnings_acknowledged
    # UPDATE agents.is_deployed = true
    # UPDATE checklist_runs.approved_at, approved_by (tenant Clerk user_id)
    return {"deployed": True, "agent_id": str(agent_id), "iframe_snippet": _make_iframe_snippet(str(agent_id))}
```

### Pattern 4: Admin UI Tab State Machine

The current `page.tsx` has `DeployTab = 'embed' | 'design'`. Phase 8 adds `'predeploy'` and makes it the default active tab.

```typescript
// Extend existing DeployTab type:
type DeployTab = 'predeploy' | 'embed' | 'design'

// ChecklistState machine (new to this file):
type ChecklistState =
  | { kind: 'idle' }
  | { kind: 'running'; runId: string }
  | { kind: 'complete'; run: ChecklistRun }
  | { kind: 'approved' }

// Poll pattern (same as existing job polling in other pages):
useEffect(() => {
  if (checklistState.kind !== 'running') return
  const interval = setInterval(async () => {
    const data = await fetchRun(checklistState.runId)
    if (data.status === 'complete' || data.status === 'failed') {
      setChecklistState({ kind: 'complete', run: data })
      clearInterval(interval)
    }
  }, 3000)
  return () => clearInterval(interval)
}, [checklistState])
```

**Tab order in nav:** Pre-Deploy (first, default) | Embed Code | Customise Widget

### Pattern 5: Control DB Migration 0011

```python
# Source: follows 0010_agent_strategy_resynthesis_flag.py exactly
# down_revision = "0010"
# Two-part migration: new table + ALTER TABLE

def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS checklist_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            recommendation TEXT,
            report JSONB,
            warnings JSONB NOT NULL DEFAULT '[]',
            warning_acknowledgments JSONB NOT NULL DEFAULT '{}',
            all_warnings_acknowledged BOOLEAN NOT NULL DEFAULT false,
            approved_at TIMESTAMPTZ,
            approved_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS checklist_runs_agent_id_idx ON checklist_runs (agent_id)")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_deployed BOOLEAN NOT NULL DEFAULT false")

def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_deployed")
    op.execute("DROP TABLE IF EXISTS checklist_runs")
```

**Key:** `IF NOT EXISTS` guards on all DDL statements — makes migration safe to re-run against pre-altered DBs (same pattern as migration 0006 in M7). [VERIFIED: `0010_agent_strategy_resynthesis_flag.py` uses same approach]

### Pattern 6: Signal Collection (Synchronous psycopg2)

```python
# Sync is fine in Celery tasks — asyncio.to_thread not needed
# (CONTEXT.md Claude's Discretion item confirms this)

def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
    """Query tenant DB eval_runs + eval_results for latest run."""
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            # Get latest eval_run
            cur.execute(
                "SELECT id, finished_at FROM eval_runs "
                "ORDER BY started_at DESC LIMIT 1"
            )
            run_row = cur.fetchone()
            if run_row is None:
                return {"last_run_at": None, "scenario_count": 0, "pass_rates": {}, "failing_scenarios": 0}
            # Get metric averages from eval_results for that run
            cur.execute(
                "SELECT metric_name, AVG(score) FROM eval_results "
                "WHERE run_id = %s GROUP BY metric_name",
                (str(run_row[0]),)
            )
            pass_rates = {row[0]: float(row[1]) for row in cur.fetchall()}
            # ... build return dict
    finally:
        conn.close()
```

### Anti-Patterns to Avoid

- **Putting checklist_runs in the tenant DB.** It is control-plane metadata (like `jobs`). Tenant DB holds domain data (chunks, eval results, red team findings). [VERIFIED: CONTEXT.md decision]
- **Passing conn_str in Celery task kwargs.** Use `agent_id` only; fetch and decrypt in task body. [VERIFIED: CLAUDE.md non-negotiable]
- **Using `loop.run_until_complete()` for asyncio bridge.** Broken in Python 3.12. Use `asyncio.run(asyncio.wait_for(...))`. [VERIFIED: run_red_team.py]
- **Sending a tool result back after submit_report.** The side-effect pattern captures the ToolUseBlock and exits — it does NOT send a tool result back to the SDK loop. [VERIFIED: red_team_service.py report_finding pattern]
- **Using Celery chord for sequential work.** `worker_pool=solo` means no chord. All signal collection is sequential in a single task. [VERIFIED: celery_app.py worker_pool=solo]
- **Forgetting asyncio.to_thread for FastAPI routes.** FastAPI routes ARE async. psycopg2 calls in routes must use `await asyncio.to_thread(...)` to avoid blocking the event loop. (Celery tasks are sync — no to_thread needed there.) [VERIFIED: red_team.py `_query_tenant_db_sync` + `asyncio.to_thread`]
- **Querying checklist_runs from tenant DB in FastAPI routes.** The checklist_runs table is in the control DB. Routes access it via SQLAlchemy async ORM (same as agents, tenants). No psycopg2 needed for checklist_runs in routes.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Agent SDK orchestrator loop | Custom HTTP calls to Anthropic | `ClaudeSDKClient` + `ClaudeAgentOptions` from `claude_agent_sdk` | Already in project, pinned, tested |
| Blocking condition logic | New framework | `if red_team_summary["deployment_blocked"] or any(v < 0.70 for v in pass_rates.values())` | Plain Python — no library needed |
| iframe snippet | Template engine | `_make_iframe_snippet(agent_id: str) -> str` — one-line function | Already decided in CONTEXT.md |
| IDOR auth pattern | New middleware | Copy pattern from `red_team.py` — `if agent.tenant_id != tenant.id: raise HTTPException(404)` | Proven in every M4-M7 route |
| psycopg2 sync wrapper | Custom connection pool | Direct psycopg2.connect + try/finally close | Already pattern in red_team.py `_query_tenant_db_sync` |
| 3-second polling UI | WebSocket / SSE | `setInterval(..., 3000)` + `clearInterval` on complete | Existing pattern in admin UI pages |

---

## Control DB vs Tenant DB — Authoritative Map

This is the most critical data-placement decision in Phase 8.

| Data | Lives In | Access Method |
|------|----------|---------------|
| `checklist_runs` table | Control DB | SQLAlchemy ORM (routes) / psycopg2 sync (Celery idempotency) |
| `agents.is_deployed` column | Control DB | SQLAlchemy ORM (routes + Celery `get_sync_db`) |
| `eval_runs`, `eval_results` | Tenant DB | psycopg2 sync in Celery task |
| `red_team_runs` | Tenant DB | psycopg2 sync in Celery task |
| `verified_qa` | Tenant DB | psycopg2 sync in Celery task |
| `documents`, `chunks` | Tenant DB | psycopg2 sync in Celery task |

**Why checklist_runs in control DB:** Deployment approval is a platform-level event. It involves setting `agents.is_deployed` (control DB) and recording who approved it. Keeping both in the same DB avoids cross-DB transactions. The analogous pattern is `jobs` (control DB) tracking Celery task status for tenant provisioning.

---

## Common Pitfalls

### Pitfall 1: Idempotency Window Too Short for Sonnet
**What goes wrong:** Sonnet is slower than Haiku (can take 30–60s per agent invocation). If the idempotency window is only 10 minutes (the eval window), a user who hits "Run checklist" twice quickly gets two simultaneous runs.
**Why it happens:** CONTEXT.md specifies 60-minute window. If implemented as 10 min (cargo-copied from eval task), double-runs occur.
**How to avoid:** Use `interval '60 minutes'` exactly as specified in CONTEXT.md. The `asyncio.wait_for(timeout=120.0)` in the bridge is the per-run timeout; 60 min idempotency window gives enough headroom.
**Warning signs:** Two checklist_runs rows in 'running' state for the same agent.

### Pitfall 2: Querying checklist_runs via psycopg2 instead of ORM in Routes
**What goes wrong:** Routes accidentally connect to the tenant DB via the decrypted conn_str to query checklist_runs — but checklist_runs is in the control DB.
**Why it happens:** Pattern confusion between the five signal query functions (which use tenant conn_str) and checklist_runs (which uses control DB).
**How to avoid:** In FastAPI routes, always use `db: AsyncSession = Depends(get_async_db)` for checklist_runs. In the Celery task, use `get_sync_db()` context manager or psycopg2 against `CONTROL_DB_SYNC_URL` (not the tenant conn_str) for idempotency check.
**Warning signs:** 404 errors on checklist_runs queries; rows not found in routes even though task inserted them.

### Pitfall 3: Missing `asyncio.to_thread` in FastAPI Routes for Tenant DB Queries
**What goes wrong:** FastAPI route handler is async. A synchronous psycopg2 call inside it blocks the entire event loop, causing other requests to hang.
**Why it happens:** Celery tasks are synchronous — psycopg2 calls are fine there. Routes are async — psycopg2 calls must be wrapped.
**How to avoid:** Only the signal collection functions (`_fetch_*_sync`) run inside the Celery task where no `asyncio.to_thread` is needed. If any route ever needs tenant DB data, use `await asyncio.to_thread(_query_tenant_db_sync, conn_str, sql, params)` — exact pattern from `red_team.py`.
**Warning signs:** API response times increase under load; event loop warnings in logs.

### Pitfall 4: Tab Order / Default Tab
**What goes wrong:** Planner adds Pre-Deploy as the third tab, leaving Embed Code as the default.
**Why it happens:** The existing code has `useState<DeployTab>('embed')` as the initial state.
**How to avoid:** Change initial state to `useState<DeployTab>('predeploy')` and add the Pre-Deploy tab button FIRST in the tablist DOM order.
**Warning signs:** User lands on deploy page and sees embed code instead of readiness check.

### Pitfall 5: Approval Before Checklist Complete
**What goes wrong:** POST /approve-deployment returns 422 if the checklist_run is still 'running', but a race condition between the poll and the approval can allow a stale UI state to trigger approval.
**Why it happens:** Frontend state might cache a 'complete' state from a previous run while a new run is in progress.
**How to avoid:** The server-side approval validation must re-fetch the checklist_run by `checklist_run_id` from the request body and check `status == 'complete'` before any mutation. This is the correct pattern even if the frontend shows "approved" — the database is the source of truth.
**Warning signs:** `agents.is_deployed` set to true despite `checklist_runs.status == 'running'`.

### Pitfall 6: Missing CLAUDE.md Demo Auth Pattern
**What goes wrong:** `demo_m8.sh` uses `X-API-Key` header but the admin routes use Clerk Bearer tokens.
**Why it happens:** M4-M7 demos used `X-API-Key`. M8 routes use Clerk Bearer (same as all `/api/v1/agents/*` routes added in M4.1).
**How to avoid:** Check existing `demo_m6.sh` and `demo_m7.sh` for which auth header they use. The red_team.py and evals.py routes use `get_current_tenant` which accepts `X-API-Key` (the legacy fallback). Verify whether the checklist routes should follow the same pattern or require Clerk Bearer. Given CONTEXT.md says "Clerk Bearer token on all API calls" for the admin UI, the demo script must use a Clerk-issued token or test with `X-API-Key` if `get_current_tenant` still supports it. [ASSUMED: dual-auth fallback still active from M4.1]

---

## Code Examples

### submit_report Tool Schema

```python
# Source: derived from _TOOL_REPORT_FINDING in red_team_service.py + CONTEXT.md DeploymentReport model
_TOOL_SUBMIT_REPORT = {
    "name": "submit_report",
    "description": "Submit the deployment readiness report with recommendation and warnings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": ["ship", "ship_with_warnings", "block"],
                "description": "Deployment recommendation based on quality signals.",
            },
            "summary": {
                "type": "string",
                "description": "2-3 plain-language sentences for the business owner. No jargon.",
            },
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "warning_id": {"type": "string"},
                        "category": {"type": "string", "enum": ["eval_quality", "security", "knowledge_depth", "corpus_coverage"]},
                        "message": {"type": "string"},
                        "severity_level": {"type": "string", "enum": ["info", "warning"]},
                    },
                    "required": ["warning_id", "category", "message", "severity_level"],
                },
            },
        },
        "required": ["recommendation", "summary", "warnings"],
    },
}
```

### Idempotency Check (Control DB — sync ORM or psycopg2 against CONTROL_DB_SYNC_URL)

```python
# Source: CONTEXT.md + pattern from red_team.py Step 2
# Option A — SQLAlchemy sync ORM (preferred since checklist_runs is a control DB model):
with get_sync_db() as db:
    existing = db.execute(
        select(ChecklistRun).where(
            ChecklistRun.agent_id == agent_id,
            ChecklistRun.status == "running",
            ChecklistRun.created_at > text("now() - interval '60 minutes'"),
        )
    ).scalar_one_or_none()
    if existing:
        log.info("deployment_checklist.idempotency_skip", agent_id=agent_id)
        return {"status": "already_running"}
```

### Warning Acknowledgment Logic

```python
# Route: POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge
# Body: {"warning_ids": ["verified_qa_low_count", "eval_quality_borderline"]}

async def acknowledge_warnings(
    agent_id: UUID,
    run_id: UUID,
    body: AcknowledgeRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # IDOR check
    # Fetch checklist_run
    # For each warning_id in body.warning_ids: add to warning_acknowledgments JSONB
    # Recalculate all_warnings_acknowledged:
    #   warnings_list = run.warnings  # list of DeploymentWarning dicts
    #   all_ids = {w["warning_id"] for w in warnings_list}
    #   acked_ids = set(run.warning_acknowledgments.keys())
    #   run.all_warnings_acknowledged = all_ids.issubset(acked_ids)
```

### iframe Snippet Generation

```python
# Source: CONTEXT.md §Specifics
def _make_iframe_snippet(agent_id: str) -> str:
    return (
        f'<script src="https://widget.veridian.app/widget.js" '
        f'data-agent="{agent_id}" async></script>'
    )
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Celery chord for parallel subtasks | Sequential calls in single task (`worker_pool=solo`) | M4 (Windows billiard bug) | Phase 8 must NOT use chord — all signal collection sequential |
| `loop.run_until_complete()` | `asyncio.run(asyncio.wait_for(...))` | M4 (Python 3.12) | Every asyncio bridge in Celery must use this form |
| Langfuse `start_span()` / `start_generation()` | `start_as_current_generation()` (v4) | M5 (langfuse v4 API) | Phase 8 does NOT use Langfuse (signals go to DB only) |
| pg_search / pgbm25 | Native tsvector + ts_rank_cd | March 2026 (Neon deprecation) | Not applicable to Phase 8 (no retrieval) |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `demo_m8.sh` can use `X-API-Key` header (dual-auth fallback from M4.1 still active on all `/api/v1/` routes) | Pitfall 6, Demo | Demo script fails to auth if Clerk Bearer is required but not easy to generate in bash |
| A2 | `ChecklistRun` ORM model in `app/models/checklist_run.py` is the right approach for control DB (vs raw psycopg2 for all operations) | Pattern 2 | Minor — either works, ORM is cleaner for routes |
| A3 | Existing tenant DB `eval_results` table has a `run_id` FK column joinable to `eval_runs.id` — schema from M6 | Signal collection | Signal query broken if column name differs |

**Claims A1 and A3 should be verified by the planner before assigning tasks that depend on them.**

---

## Open Questions

1. **Celery task control DB writes — ORM or psycopg2?**
   - What we know: `run_red_team` writes to tenant DB via psycopg2. `checklist_runs` is in the control DB.
   - What's unclear: Should `run_deployment_checklist` use `get_sync_db()` SQLAlchemy ORM for control DB writes, or use psycopg2 against `CONTROL_DB_SYNC_URL`?
   - Recommendation: Use `get_sync_db()` ORM for control DB operations (INSERT checklist_run, UPDATE status). ORM is already used in `run_red_team_beat` for `select(Agent)`. Reserve psycopg2 for tenant DB only.

2. **Dual-auth on checklist routes**
   - What we know: M4.1 added dual-auth (Clerk Bearer + X-API-Key fallback via `get_current_tenant`). CONTEXT.md says "Clerk Bearer token on all API calls" in the admin UI section.
   - What's unclear: Does `demo_m8.sh` need a real Clerk token or can it use X-API-Key?
   - Recommendation: Use `X-API-Key` in demo script (same as demo_m7.sh). The dual-auth fallback is in `get_current_tenant` and is non-negotiable for backward compat with demo scripts.

3. **Plan 08-01 through 08-07 already exist**
   - What we know: PLAN.md files for all 7 plans already exist in `.planning/phases/08-pre-deployment-checklist/`
   - What's unclear: Whether the plans were created before research completed and may need RESEARCH.md for supplemental detail
   - Recommendation: This RESEARCH.md supplements the existing plans. The planner should verify Plan consistency against this research, particularly the control DB vs tenant DB placement.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| claude-agent-sdk | deployment_service.py | Yes | 0.1.81 | None — PINNED |
| psycopg2-binary | signal collection functions | Yes | 2.9.12 | None |
| Redis | Celery broker | Yes (local) | N/A | None — required |
| PostgreSQL (local) | Control DB + Neon connection | Yes (local) | N/A | None |
| celery[redis] | run_deployment_checklist task | Yes | 5.6.3 | None |
| Next.js admin | Pre-Deploy tab | Yes (existing apps/admin) | N/A | None |

**No missing dependencies.** All runtime dependencies are installed and verified working from M7.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing, apps/api/pyproject.toml) |
| Config file | apps/api/pyproject.toml `[tool.pytest]` section |
| Quick run command | `cd apps/api && python -m pytest tests/unit/test_deployment_service.py tests/unit/test_deployment_task.py -x -q` |
| Full suite command | `cd apps/api && python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEP-01 | Orchestrator agent reads signals and calls submit_report | unit | `pytest tests/unit/test_deployment_service.py::TestRunOrchestrator -x` | Wave 0 |
| DEP-02 | `run_orchestrator` returns DeploymentReport with recommendation | unit | `pytest tests/unit/test_deployment_service.py::TestDeploymentReport -x` | Wave 0 |
| DEP-03 | Block logic fires when deployment_blocked=True or eval < 0.70 | unit | `pytest tests/unit/test_deployment_service.py::TestBlockingConditions -x` | Wave 0 |
| DEP-04 | GET /checklist-runs/{run_id} returns report with all signal sections | unit | `pytest tests/unit/test_deployment_routes.py::TestGetChecklistRun -x` | Wave 0 |
| DEP-05 | POST /acknowledge updates warning_acknowledgments; approve blocked until all acked | unit | `pytest tests/unit/test_deployment_routes.py::TestAcknowledge -x` | Wave 0 |
| DEP-06 | POST /approve-deployment sets is_deployed=true and returns iframe_snippet | unit | `pytest tests/unit/test_deployment_routes.py::TestApproveDeployment -x` | Wave 0 |
| DEP-07 | Full journey completes in demo_m8.sh with exit 0 | e2e (DEP_E2E_ENABLED) | `DEP_E2E_ENABLED=1 pytest tests/integration/test_deployment_e2e.py -x` | Wave 0 |
| DEP-08 | demo_m8.sh script exists and is executable | demo/manual | `bash scripts/demo_m8.sh --dry-run` (or verify existence) | Wave 7 |

### Sampling Rate
- **Per task commit:** `cd apps/api && python -m pytest tests/unit/test_deployment_service.py tests/unit/test_deployment_task.py -x -q`
- **Per wave merge:** `cd apps/api && python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_deployment_service.py` — covers DEP-01, DEP-02, DEP-03 (service unit tests with xfail stubs, de-xfailed in Plan 08-06)
- [ ] `tests/unit/test_deployment_task.py` — covers run_deployment_checklist idempotency, happy path, failure path (de-xfailed in Plan 08-06)
- [ ] `tests/unit/test_deployment_routes.py` — covers DEP-04, DEP-05, DEP-06 route behavior (de-xfailed in Plan 08-06)
- [ ] `tests/integration/test_deployment_e2e.py` — covers DEP-07, DEP_E2E_ENABLED guard

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Clerk Bearer + X-API-Key fallback via `get_current_tenant` (existing) |
| V3 Session Management | no | Stateless API, no sessions |
| V4 Access Control | yes | IDOR check: `agent.tenant_id == tenant.id` on every route (exact pattern from red_team.py) |
| V5 Input Validation | yes | Pydantic models (ChecklistRunResponse, AcknowledgeRequest, ApproveDeploymentRequest) |
| V6 Cryptography | yes | Fernet decrypt at runtime — never logged, never in task args (existing CTL-08 pattern) |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| IDOR — tenant A approves tenant B's agent | Spoofing / Elevation | `agent.tenant_id == tenant.id` check on every route (pattern from red_team.py) |
| Approve blocked agent | Elevation of Privilege | Server-side validation: `recommendation != 'block'` before any mutation |
| Acknowledge injection (warning_id manipulation) | Tampering | Validate `warning_id` values against the `warnings` list from the DB row, not the client payload |
| conn_str exposure via task args | Information Disclosure | Only `agent_id` in task kwargs; conn_str fetched + decrypted at task runtime |
| Prompt injection via signal data | Tampering | System prompt instructs agent to treat signals as data; signals are structured JSON (not free text from users) |

---

## Sources

### Primary (HIGH confidence — verified against codebase)
- `apps/api/app/services/red_team_service.py` — side-effect tool pattern (report_finding), Agent SDK runner structure, asyncio.run bridge
- `apps/api/app/worker/tasks/runtime/red_team.py` — Celery acks_late + idempotency pattern, psycopg2 tenant DB queries
- `apps/api/app/api/v1/red_team.py` — IDOR check pattern, asyncio.to_thread for psycopg2 in async routes, 202 trigger pattern
- `apps/admin/app/agents/[id]/deploy/page.tsx` — existing tab structure (embed + design), state patterns, useAuth Clerk
- `apps/api/alembic/versions/0010_agent_strategy_resynthesis_flag.py` — migration pattern (down_revision, IF NOT EXISTS)
- `apps/api/app/worker/celery_app.py` — include list, beat_schedule, worker_pool=solo
- `apps/api/app/core/config.py` — Settings pattern for new M8 config key
- `apps/api/app/models/agent.py` — ORM model pattern for is_deployed Mapped field addition
- `.planning/phases/08-pre-deployment-checklist/08-CONTEXT.md` — locked decisions, data models, route specs

### Secondary (MEDIUM confidence)
- `.planning/STATE.md` — confirmed M7 complete, all 6 plans done, patterns verified working
- `.planning/REQUIREMENTS.md` — DEP-01 through DEP-08 exact text

### Tertiary (LOW confidence)
- None — all claims verified against codebase or CONTEXT.md decisions

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified in pyproject.toml
- Architecture: HIGH — patterns verified in M7 code; no new patterns introduced
- Pitfalls: HIGH — derived from actual code analysis of M6/M7 implementation
- Validation: MEDIUM — test file names and commands are proposed; actual test names assigned during Plan creation

**Research date:** 2026-05-23
**Valid until:** 2026-08-23 (stable codebase — no external library changes affect this research)
