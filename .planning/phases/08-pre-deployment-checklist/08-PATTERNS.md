# Phase 8: Pre-deployment Checklist + Human Validation — Pattern Map

**Mapped:** 2026-05-23
**Files analyzed:** 14 (9 new, 5 modified)
**Analogs found:** 14 / 14

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/api/app/services/deployment_service.py` | service | request-response (Agent SDK) | `apps/api/app/services/red_team_service.py` | exact |
| `apps/api/app/worker/tasks/runtime/deployment.py` | task | CRUD + event-driven | `apps/api/app/worker/tasks/runtime/red_team.py` | exact |
| `apps/api/app/api/v1/deployment.py` | controller | request-response | `apps/api/app/api/v1/red_team.py` | exact |
| `apps/api/app/api/v1/schemas/deployment.py` | schema/model | transform | `apps/api/app/schemas/red_team.py` | exact (wrong path — real location is `apps/api/app/schemas/`) |
| `apps/api/alembic/versions/0011_checklist_runs_is_deployed.py` | migration | batch | `apps/api/alembic/versions/0010_agent_strategy_resynthesis_flag.py` | exact |
| `scripts/demo_m8.sh` | utility | request-response | `scripts/demo_m7.sh` | exact |
| `apps/api/tests/unit/test_deployment_service.py` | test | transform | `apps/api/tests/unit/test_red_team_service.py` | exact |
| `apps/api/tests/unit/test_deployment_routes.py` | test | request-response | `apps/api/tests/unit/test_red_team_task.py` | role-match |
| `apps/api/tests/integration/test_deployment_e2e.py` | test | request-response | `apps/api/tests/integration/test_red_team_e2e.py` | exact |
| `apps/api/app/core/config.py` (modify) | config | — | `apps/api/app/core/config.py` (existing) | self |
| `apps/api/app/models/agent.py` (modify) | model | — | `apps/api/app/models/agent.py` (existing) | self |
| `apps/api/app/worker/celery_app.py` (modify) | config | — | `apps/api/app/worker/celery_app.py` (existing) | self |
| `apps/api/app/main.py` (modify) | config | — | `apps/api/app/main.py` (existing) | self |
| `apps/admin/app/agents/[id]/deploy/page.tsx` (modify) | component | event-driven | `apps/admin/app/agents/[id]/deploy/page.tsx` (existing) | self |

---

## Pattern Assignments

### `apps/api/app/services/deployment_service.py` (service, Agent SDK orchestrator)

**Analog:** `apps/api/app/services/red_team_service.py`

**Imports pattern** (lines 1-31):
```python
"""
M8 Deployment service: pre-deployment readiness orchestrator (Claude Agent SDK Sonnet).

Architecture notes:
- Signals are collected synchronously (psycopg2) BEFORE calling the Agent SDK.
- Agent calls submit_report as a side-effect tool; runner captures ToolUseBlock
  and writes to result_container. No tool result sent back (same as report_finding pattern).
- claude-agent-sdk==0.1.81 PINNED — do not upgrade without testing.
- asyncio.run(asyncio.wait_for(..., timeout=120.0)) bridge in Celery task.
"""

import asyncio
import json
import structlog
from typing import Literal

import psycopg2
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

**Pydantic models pattern** (analogous to lines 38-57 of red_team_service.py):
```python
class DeploymentWarning(BaseModel):
    warning_id: str        # unique slug, e.g. "verified_qa_low_count"
    category: str          # "eval_quality" | "security" | "knowledge_depth" | "corpus_coverage"
    message: str           # plain-language text for the owner
    severity_level: str    # "info" | "warning"


class DeploymentReport(BaseModel):
    recommendation: Literal["ship", "ship_with_warnings", "block"]
    summary: str           # 2-3 plain-language sentences for the owner
    warnings: list[DeploymentWarning]
    eval_summary: dict
    red_team_summary: dict
    verified_qa_stats: dict
    corpus_stats: dict
```

**Tool schema pattern** (analogous to lines 168-206 of red_team_service.py — `_TOOL_REPORT_FINDING`):
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
            "summary": {"type": "string"},
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

**Core Agent SDK runner pattern** (analogous to lines 248-304 of red_team_service.py — `run_prompt_injection_agent`):

The key difference from red_team_service.py: there is no `send_probe` two-way tool. Only `submit_report` exists, and it is a pure side-effect — the runner exits on first `ToolUseBlock(name="submit_report")`.

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
                        # Side-effect only — no tool result sent back (same as report_finding)
                        return


def run_orchestrator(signals_json: str, result_container: dict) -> None:
    """Bridge: called via asyncio.run(asyncio.wait_for(..., timeout=120.0)) in Celery task."""
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

**Signal collection pattern** (sync psycopg2, analogous to `_query_tenant_db_sync` in red_team.py lines 40-60):
```python
def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, finished_at FROM eval_runs ORDER BY started_at DESC LIMIT 1"
            )
            run_row = cur.fetchone()
            if run_row is None:
                return {"last_run_at": None, "scenario_count": 0, "pass_rates": {}, "failing_scenarios": 0}
            cur.execute(
                "SELECT metric_name, AVG(score) FROM eval_results "
                "WHERE run_id = %s GROUP BY metric_name",
                (str(run_row[0]),),
            )
            pass_rates = {row[0]: float(row[1]) for row in cur.fetchall()}
            return {
                "last_run_at": run_row[1].isoformat() if run_row[1] else None,
                "scenario_count": len(pass_rates),
                "pass_rates": pass_rates,
                "failing_scenarios": sum(1 for v in pass_rates.values() if v < 0.70),
            }
    finally:
        conn.close()
```

**iframe snippet helper** (no analog — one-liner prescribed by CONTEXT.md):
```python
def _make_iframe_snippet(agent_id: str) -> str:
    return (
        f'<script src="https://widget.veridian.app/widget.js" '
        f'data-agent="{agent_id}" async></script>'
    )
```

---

### `apps/api/app/worker/tasks/runtime/deployment.py` (task, runtime queue)

**Analog:** `apps/api/app/worker/tasks/runtime/red_team.py`

**Imports pattern** (lines 25-46 of red_team.py):
```python
from __future__ import annotations

import asyncio
import json
import uuid

import psycopg2
import structlog
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun  # new ORM model for M8
from app.services.deployment_service import run_orchestrator
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)
```

**Task decorator + signature pattern** (lines 179-187 of red_team.py):
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.deployment.run_deployment_checklist",
)
def run_deployment_checklist(self, agent_id: str) -> dict:
```

**Step 1 — fetch agent + decrypt** (lines 218-227 of red_team.py):
```python
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error("run_deployment_checklist.agent_not_found", agent_id=agent_id)
            return {}
        conn_str = fernet_decrypt(agent.neon_connection_string)
```

**Step 2 — idempotency guard** (analogous to lines 234-255 of red_team.py, BUT targets control DB via ORM, not tenant DB via psycopg2; 60-minute window not 30):
```python
    # Idempotency: skip if 'running' row exists within 60 minutes (control DB)
    with get_sync_db() as db:
        existing = db.execute(
            select(ChecklistRun).where(
                ChecklistRun.agent_id == agent_id,
                ChecklistRun.status == "running",
                ChecklistRun.created_at > text("now() - interval '60 minutes'"),
            )
        ).scalar_one_or_none()
        if existing:
            log.info("run_deployment_checklist.idempotency_skip", agent_id=agent_id)
            return {"status": "already_running"}
```

**Step 3 — INSERT run row** (analogous to lines 266-291 of red_team.py, BUT control DB via ORM):
```python
    # Insert checklist_runs row in control DB via ORM
    with get_sync_db() as db:
        run = ChecklistRun(agent_id=agent_id, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = str(run.id)
```

**Step 5 — asyncio.run bridge** (lines 296-303 of red_team.py — exact pattern, just different function):
```python
    result_container: dict = {}
    try:
        asyncio.run(
            asyncio.wait_for(
                run_orchestrator(json.dumps(signals), result_container),
                timeout=120.0,
            )
        )
    except Exception as exc:
        log.error("run_deployment_checklist.orchestrator_failed", agent_id=agent_id, error=str(exc))
        # fall through to status='failed' update
```

**Steps 6 & 7 — UPDATE complete/failed** (lines 342-418 of red_team.py, adapted for ORM + control DB):
```python
        with get_sync_db() as db:
            run_obj = db.get(ChecklistRun, run_id)
            run_obj.status = "complete"
            run_obj.recommendation = report.recommendation
            run_obj.report = report.model_dump()
            run_obj.warnings = [w.model_dump() for w in report.warnings]
            db.commit()

    except Exception as exc:
        with get_sync_db() as db:
            run_obj = db.get(ChecklistRun, run_id)
            if run_obj:
                run_obj.status = "failed"
                db.commit()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        return {}
```

**Critical difference from red_team.py:** The idempotency check AND run row writes target the **control DB** via `get_sync_db()` ORM (not psycopg2 against tenant conn_str). The four `_fetch_*_sync` signal functions use psycopg2 against the tenant `conn_str`. There is no separate psycopg2 connect call to the control DB.

---

### `apps/api/app/api/v1/deployment.py` (controller, 5 routes)

**Analog:** `apps/api/app/api/v1/red_team.py`

**Imports pattern** (lines 1-31 of red_team.py):
```python
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.models.tenant import Tenant
from app.worker.tasks.runtime.deployment import run_deployment_checklist

router = APIRouter(tags=["deployment"])
log = structlog.get_logger(__name__)
```

**IDOR check pattern** (lines 92-105 of red_team.py — copy verbatim, apply to all 5 routes):
```python
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:        # IDOR prevention
        raise HTTPException(status_code=404, detail="Agent not found")
```

**POST trigger route pattern (202)** (lines 220-270 of red_team.py):
```python
@router.post("/agents/{agent_id}/checklist-runs", status_code=202)
async def trigger_checklist_run(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # 1. IDOR check (copy exactly from red_team.py lines 243-255)
    # 2. Agent status guard
    if agent.status != "ready":
        raise HTTPException(status_code=400, detail="Agent must be in ready state")
    # 3. Dispatch — only agent_id, never conn_str (CTL-08)
    task = run_deployment_checklist.apply_async(
        kwargs={"agent_id": str(agent_id)},
        queue="runtime",
    )
    log.info("checklist_trigger.dispatched", agent_id=str(agent_id), task_id=task.id)
    return {"checklist_run_id": task.id, "status": "queued"}
```

**GET list route pattern** (lines 76-135 of red_team.py, adapted for control DB ORM):
```python
@router.get("/agents/{agent_id}/checklist-runs")
async def list_checklist_runs(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # IDOR check
    # Query control DB (SQLAlchemy async ORM — NOT psycopg2, checklist_runs is control DB)
    result = await db.execute(
        select(ChecklistRun)
        .where(ChecklistRun.agent_id == agent_id)
        .order_by(ChecklistRun.created_at.desc())
        .limit(10)
    )
    runs = result.scalars().all()
    return {"runs": [_run_to_dict(r) for r in runs]}
```

**POST acknowledge route pattern** (new mutation; use same IDOR + ORM pattern):
```python
@router.post("/agents/{agent_id}/checklist-runs/{run_id}/acknowledge")
async def acknowledge_warnings(
    agent_id: UUID,
    run_id: UUID,
    body: AcknowledgeRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # IDOR check
    # Fetch run from control DB
    run = await db.get(ChecklistRun, run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")
    # Update warning_acknowledgments JSONB
    acks = dict(run.warning_acknowledgments or {})
    now_iso = datetime.now(timezone.utc).isoformat()
    for wid in body.warning_ids:
        acks[wid] = now_iso
    run.warning_acknowledgments = acks
    # Recalculate all_warnings_acknowledged
    all_ids = {w["warning_id"] for w in (run.warnings or [])}
    run.all_warnings_acknowledged = all_ids.issubset(set(acks.keys()))
    await db.commit()
    return {"all_warnings_acknowledged": run.all_warnings_acknowledged}
```

**POST approve-deployment route pattern** (new mutation; approval validation from CONTEXT.md):
```python
@router.post("/agents/{agent_id}/approve-deployment")
async def approve_deployment(
    agent_id: UUID,
    body: ApproveDeploymentRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    # IDOR check on agent
    # Fetch checklist_run by body.checklist_run_id
    run = await db.get(ChecklistRun, body.checklist_run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")
    # Approval validation (CONTEXT.md §Approval Validation)
    if run.status != "complete":
        raise HTTPException(status_code=422, detail="Checklist is still running")
    if run.recommendation == "block":
        raise HTTPException(status_code=422, detail="Cannot approve a blocked deployment — resolve critical issues first")
    if run.recommendation == "ship_with_warnings" and not run.all_warnings_acknowledged:
        raise HTTPException(status_code=422, detail="Acknowledge all warnings before approving")
    # Flip is_deployed + stamp approved_at/approved_by
    agent.is_deployed = True
    run.approved_at = datetime.now(timezone.utc)
    run.approved_by = tenant.clerk_user_id  # or however Tenant exposes it
    await db.commit()
    from app.services.deployment_service import _make_iframe_snippet
    return {
        "deployed": True,
        "agent_id": str(agent_id),
        "iframe_snippet": _make_iframe_snippet(str(agent_id)),
    }
```

**Note on no psycopg2 in routes:** Unlike red_team.py which uses `asyncio.to_thread(_query_tenant_db_sync, ...)` because it reads the tenant DB, deployment routes only query `checklist_runs` and `agents` — both in the control DB via SQLAlchemy async ORM. No psycopg2 needed in these routes.

---

### `apps/api/app/schemas/deployment.py` (schema — note: NOT `api/v1/schemas/`, real path is `app/schemas/`)

**Analog:** `apps/api/app/schemas/red_team.py`

**CRITICAL PATH CORRECTION:** The CONTEXT.md lists the file as `apps/api/app/api/v1/schemas/deployment.py` but that directory does not exist. All existing schemas live in `apps/api/app/schemas/`. The file must be created at `apps/api/app/schemas/deployment.py`.

**Full schema pattern** (copy structure from `apps/api/app/schemas/red_team.py` lines 1-34):
```python
"""Pydantic schemas for M8 deployment API routes — request/response models for checklist-runs endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class ChecklistRunResponse(BaseModel):
    id: str
    agent_id: str
    status: str                         # "running" | "complete" | "failed"
    recommendation: str | None          # "ship" | "ship_with_warnings" | "block" | None
    report: dict[str, Any] | None       # full DeploymentReport JSONB
    warnings: list[dict[str, Any]]      # list of DeploymentWarning dicts
    warning_acknowledgments: dict[str, Any]
    all_warnings_acknowledged: bool
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime


class ChecklistRunListResponse(BaseModel):
    runs: list[ChecklistRunResponse]


class ChecklistRunTriggerResponse(BaseModel):
    checklist_run_id: str   # Celery task ID (correlator for polling)
    status: str             # "queued"


class AcknowledgeRequest(BaseModel):
    warning_ids: list[str]  # list of warning_id slugs to acknowledge


class AcknowledgeResponse(BaseModel):
    all_warnings_acknowledged: bool


class ApproveDeploymentRequest(BaseModel):
    checklist_run_id: str   # UUID of the checklist_run to approve


class ApproveDeploymentResponse(BaseModel):
    deployed: bool
    agent_id: str
    iframe_snippet: str
```

---

### `apps/api/alembic/versions/0011_checklist_runs_is_deployed.py` (migration)

**Analog:** `apps/api/alembic/versions/0010_agent_strategy_resynthesis_flag.py`

**Full file pattern** (lines 1-34 of 0010, adapted):
```python
"""Add checklist_runs table and agents.is_deployed column for M8 pre-deployment checklist.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    op.execute(
        "CREATE INDEX IF NOT EXISTS checklist_runs_agent_id_idx ON checklist_runs (agent_id)"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_deployed BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_deployed")
    op.execute("DROP TABLE IF EXISTS checklist_runs")
```

**Key patterns to copy:**
- `IF NOT EXISTS` on all DDL (line 28 of 0010 uses `ALTER TABLE agents ADD COLUMN` without IF NOT EXISTS — 0011 must add it for safe re-runs)
- Import only `from alembic import op` — no SQLAlchemy imports needed for raw DDL
- `down_revision = "0010"` (exact string match to previous revision)

---

### `scripts/demo_m8.sh` (utility script)

**Analog:** `scripts/demo_m7.sh`

**Header + config pattern** (lines 1-55 of demo_m7.sh):
```bash
#!/usr/bin/env bash
# demo_m8.sh — Veridian M8 Pre-deployment Checklist demo script
#
# Prerequisites (ALL local — no Docker):
#   1. Redis:    redis-server (running on localhost:6379)
#   2. Postgres: local install (running on localhost:5432)
#   3. API:      uvicorn app.main:app --reload       (from apps/api/)
#   4. Worker:   celery -A app.worker.celery_app worker --queues runtime  (from apps/api/)
#
# Required env vars:
#   ADMIN_KEY   — X-Admin-Key header value for POST /api/v1/agents
#   API_KEY     — X-API-Key for tenant auth
#
# Usage:
#   ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m8.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:-}"
API_KEY="${API_KEY:-}"
```

**Prerequisite check pattern** (lines 64-82 of demo_m7.sh — copy verbatim):
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

**Poll-until-complete pattern** (lines 165-195 of demo_m7.sh — adapt for `status` field not Celery state; poll `GET /checklist-runs/{run_id}` every 3s not 15s):
```bash
# M8 polls the checklist_run status directly (not Celery AsyncResult)
# because status is tracked in control DB, not Celery result backend.
MAX_POLLS=60   # 3s * 60 = 3 min max
POLL_COUNT=0
STATUS="running"
while [[ $POLL_COUNT -lt $MAX_POLLS ]]; do
    STATUS=$(curl -sf --max-time 5 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID/checklist-runs/$RUN_ID" \
        2>/dev/null | python -c "import sys,json; print(json.load(sys.stdin).get('run', {}).get('status', 'unknown'))" 2>/dev/null || echo "unknown")
    echo "  [Poll $((POLL_COUNT+1))/$MAX_POLLS] status: $STATUS"
    if [[ "$STATUS" == "complete" ]] || [[ "$STATUS" == "failed" ]]; then
        break
    fi
    POLL_COUNT=$((POLL_COUNT+1))
    sleep 3
done
```

**Assertion pattern** (lines 208-274 of demo_m7.sh — adapt for recommendation and is_deployed):
```bash
# Final assertions
if [[ "$RECOMMENDATION" == "ship" ]] || [[ "$RECOMMENDATION" == "ship_with_warnings" ]]; then
    echo "[PASS] recommendation=$RECOMMENDATION — agent can proceed to approval"
elif [[ "$RECOMMENDATION" == "block" ]]; then
    echo "[WARN] recommendation=block — resolve issues before approval"
fi
# After approve:
IS_DEPLOYED=$(curl -sf -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/agents/$AGENT_ID" \
    | python -c "import sys,json; print(json.load(sys.stdin).get('is_deployed', False))" 2>/dev/null || echo "false")
if [[ "$IS_DEPLOYED" == "True" ]] || [[ "$IS_DEPLOYED" == "true" ]]; then
    echo "[PASS] agents.is_deployed=true confirmed"
else
    echo "[WARN] agents.is_deployed not true — check approval route"
fi
```

---

### `apps/api/tests/unit/test_deployment_service.py` (unit test)

**Analog:** `apps/api/tests/unit/test_red_team_service.py`

**Env-var bootstrap + imports pattern** (lines 29-54 of test_red_team_service.py — copy verbatim):
```python
import os
import base64

os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

import pytest
from unittest.mock import MagicMock, patch

from app.services.deployment_service import (
    DeploymentWarning,
    DeploymentReport,
    run_orchestrator,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
)
```

**Mock patch pattern** (lines 82-88 of test_red_team_service.py — adapt for run_orchestrator):
```python
# Patch asyncio.run at module boundary:
with patch("app.services.deployment_service.asyncio.run", return_value=None) as mock_run:
    # ...
# Patch psycopg2.connect for signal collection functions:
with patch("app.services.deployment_service.psycopg2.connect", return_value=mock_conn):
    result = _fetch_eval_summary_sync("test-agent-id", "postgresql://test/tenant")
```

**Test class structure** (analogous to classes TestClassifySeverity, TestPromptInjectionAgent etc.):
```python
class TestRunOrchestrator:         # DEP-01, DEP-02 — asyncio.run called, result_container populated
class TestDeploymentReport:        # DEP-02 — DeploymentReport model construction
class TestBlockingConditions:      # DEP-03 — block/warn/ship logic
class TestSignalCollectionFunctions:  # _fetch_*_sync return correct dict shapes
```

---

### `apps/api/tests/unit/test_deployment_routes.py` (unit test)

**Analog:** `apps/api/tests/unit/test_red_team_task.py` (task mock patterns)

**Helper builders pattern** (lines 43-64 of test_red_team_task.py — adapt for async ORM):
```python
# For route tests, use FastAPI TestClient with dependency overrides:
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_tenant
from app.core.database import get_async_db

def _override_tenant():
    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return mock_tenant

def _override_db(mock_db):
    async def _get_db():
        yield mock_db
    return _get_db
```

**Test class structure** (per RESEARCH.md validation map):
```python
class TestGetChecklistRun:      # DEP-04 — GET returns report with all signal sections
class TestAcknowledge:          # DEP-05 — POST acknowledge updates JSONB, approve blocked until all acked
class TestApproveDeployment:    # DEP-06 — POST approve sets is_deployed=true, returns iframe_snippet
```

---

### `apps/api/tests/integration/test_deployment_e2e.py` (integration test)

**Analog:** `apps/api/tests/integration/test_red_team_e2e.py`

**Guard pattern** (lines 30-33 of test_red_team_e2e.py — copy exactly, change env var name):
```python
@pytest.mark.skipif(
    not os.environ.get("DEP_E2E_ENABLED"),
    reason="DEP_E2E_ENABLED not set — skipping live deployment checklist E2E test",
)
def test_deployment_checklist_completes():
```

**Poll loop pattern** (lines 62-82 of test_red_team_e2e.py — adapt: poll `GET /checklist-runs` for `status == 'complete'`, 3s intervals, 300s deadline):
```python
    deadline = time.time() + 300
    run_id = None
    while time.time() < deadline:
        poll_resp = requests.get(
            f"{BASE_URL}/api/v1/agents/{AGENT_ID}/checklist-runs",
            headers=headers, timeout=30,
        )
        runs = poll_resp.json().get("runs", [])
        for run in runs:
            if run.get("status") == "complete":
                run_id = run["id"]
                break
        if run_id:
            break
        time.sleep(3)
```

**Assertion pattern** (lines 97-109 of test_red_team_e2e.py — adapt for checklist schema):
```python
    assert run_detail["status"] == "complete"
    assert run_detail["recommendation"] in ["ship", "ship_with_warnings", "block"]
    assert isinstance(run_detail["warnings"], list)
    assert isinstance(run_detail["all_warnings_acknowledged"], bool)
```

---

## Modifications to Existing Files

### `apps/api/app/core/config.py` — add `DEP_BLOCK_ON_HIGH_RED_TEAM`

**Pattern:** Add after the M7 red team configuration block (line 97 of config.py):
```python
    # M7: Red team configuration
    RED_TEAM_MAX_TURNS: int = 5
    RED_TEAM_ATTACK_SEQUENCES: int = 3

    # M8: Deployment checklist configuration
    DEP_BLOCK_ON_HIGH_RED_TEAM: bool = True  # when True, high_count > 0 triggers block
```

The `bool = True` default requires no env var override in existing deployments. The `Settings` class uses `pydantic_settings.BaseSettings` with `extra="ignore"`, so no other changes needed.

---

### `apps/api/app/models/agent.py` — add `is_deployed: Mapped[bool]`

**Pattern:** Add after `strategy_resynthesis_flagged` field (line 55 of agent.py), following same `Mapped[bool]` pattern:
```python
    # M5: validation chain — persistent Auditor ungrounded failures trigger resynthesis
    strategy_resynthesis_flagged: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    # M8: deployment gate — set to True on POST /approve-deployment (DEP-06)
    is_deployed: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
```

No import changes needed — `Mapped`, `mapped_column`, `text` are already imported at lines 15-16.

---

### `apps/api/app/worker/celery_app.py` — add deployment task to `include`

**Pattern:** Append after the M7 entry (line 85 of celery_app.py), following the same comment style:
```python
        # M7: red team tasks (runtime queue)
        "app.worker.tasks.runtime.red_team",
        # M8: deployment checklist task (runtime queue)
        "app.worker.tasks.runtime.deployment",
```

No `beat_schedule` entry needed — deployment checklist is owner-triggered only (CONTEXT.md confirmed).

---

### `apps/api/app/main.py` — register deployment router

**Pattern:** Add to the import line and `include_router` block (lines 144, 158 of main.py):
```python
# Line 144 — add deployment to the import:
from app.api.v1 import agents, documents, health, jobs, tenants, query, agent_chat, widget, webhooks, evals, red_team, deployment  # noqa: E402

# After line 158 — add include_router (same pattern as red_team):
app.include_router(red_team.router, prefix="/api/v1")
app.include_router(deployment.router, prefix="/api/v1")
```

---

### `apps/admin/app/agents/[id]/deploy/page.tsx` — add Pre-Deploy tab

**Pattern:** Extend existing file. Key changes required:

**1. Extend DeployTab type** (line 10 of page.tsx — change from `'embed' | 'design'`):
```typescript
type DeployTab = 'predeploy' | 'embed' | 'design'
```

**2. Add ChecklistState machine** (new type, add after line 11):
```typescript
type ChecklistState =
  | { kind: 'idle' }
  | { kind: 'running'; runId: string }
  | { kind: 'complete'; run: ChecklistRun }
  | { kind: 'approved' }

interface ChecklistRun {
  id: string
  status: string
  recommendation: string | null
  report: Record<string, unknown> | null
  warnings: Array<{ warning_id: string; category: string; message: string; severity_level: string }>
  warning_acknowledgments: Record<string, string>
  all_warnings_acknowledged: boolean
}
```

**3. Change initial tab state** (line 187 of page.tsx — change `'embed'` to `'predeploy'`):
```typescript
// BEFORE:
const [activeTab, setActiveTab] = useState<DeployTab>('embed')
// AFTER:
const [activeTab, setActiveTab] = useState<DeployTab>('predeploy')
```

**4. Add checklist state** (after line 189):
```typescript
const [checklistState, setChecklistState] = useState<ChecklistState>({ kind: 'idle' })
const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
```

**5. Poll effect pattern** (analogous to existing `useEffect` at line 197 — setInterval + clearInterval):
```typescript
useEffect(() => {
  if (checklistState.kind !== 'running') return
  const interval = setInterval(async () => {
    try {
      const token = await getToken()
      if (!token) return
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/checklist-runs/${checklistState.runId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) return
      const data = await r.json()
      const run: ChecklistRun = data.run
      if (run.status === 'complete' || run.status === 'failed') {
        setChecklistState({ kind: 'complete', run })
        clearInterval(interval)
      }
    } catch { /* ignore poll errors */ }
  }, 3000)
  return () => clearInterval(interval)
}, [checklistState, id, apiBase, getToken])
```

**6. Pre-Deploy tab button** (add FIRST in the tablist div at line 342 — before existing `tab-embed` button):
```typescript
<button
  role="tab"
  id="tab-predeploy"
  aria-selected={activeTab === 'predeploy'}
  aria-controls="panel-predeploy"
  onClick={() => setActiveTab('predeploy')}
  style={{
    padding: '10px 20px',
    border: 'none',
    borderBottom: `2px solid ${activeTab === 'predeploy' ? 'var(--accent)' : 'transparent'}`,
    background: 'none',
    color: activeTab === 'predeploy' ? 'var(--accent)' : 'var(--text-3)',
    fontWeight: activeTab === 'predeploy' ? 600 : 400,
    fontSize: '14px',
    cursor: activeTab === 'predeploy' ? 'default' : 'pointer',
    fontFamily: 'var(--font-sans)',
  }}
>
  Pre-Deploy
</button>
```

**7. Auth call pattern** (existing `getToken()` pattern at lines 200-207 — copy exactly for all API calls in the pre-deploy tab):
```typescript
const token = await getToken()
if (!token) { /* handle */ return }
const r = await fetch(`${apiBase}/api/v1/agents/${id}/...`, {
  headers: { Authorization: `Bearer ${token}` },
})
```

---

## New ORM Model Required

### `apps/api/app/models/checklist_run.py` (new — no analog in CONTEXT.md file list)

**Analog:** `apps/api/app/models/job.py` (control DB model with JSONB fields)

The CONTEXT.md file list omits `checklist_run.py` but the task file and route file both import `ChecklistRun`. It must be created.

```python
"""ChecklistRun ORM model — control DB.

Table: checklist_runs
Created by migration 0011.
"""

from datetime import datetime
from uuid import UUID
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChecklistRun(Base):
    __tablename__ = "checklist_runs"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'running'")
    )
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    warning_acknowledgments: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    all_warnings_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("checklist_runs_agent_id_idx", "agent_id"),
    )
```

---

## Shared Patterns

### Authentication / Tenant Resolution
**Source:** `apps/api/app/api/v1/red_team.py` lines 76-105
**Apply to:** All 5 deployment route handlers
```python
agent = await db.get(Agent, agent_id)
if agent is None:
    raise HTTPException(status_code=404, detail="Agent not found")
if agent.tenant_id != tenant.id:
    raise HTTPException(status_code=404, detail="Agent not found")
```

### Connection String Handling
**Source:** `apps/api/app/worker/tasks/runtime/red_team.py` lines 218-227
**Apply to:** `run_deployment_checklist` task
```python
# NEVER log conn_str — CTL-08
conn_str = fernet_decrypt(agent.neon_connection_string)
```

### asyncio.run Bridge
**Source:** `apps/api/app/services/red_team_service.py` lines 296-303
**Apply to:** `run_deployment_checklist` Celery task (NOT in service functions; bridge is in the task)
```python
asyncio.run(
    asyncio.wait_for(
        _run_orchestrator_loop(signals_json, result_container),
        timeout=120.0,  # 120s for Sonnet (vs 60s for Haiku in probe_fn)
    )
)
```

### Sync ORM Context Manager (Celery tasks)
**Source:** `apps/api/app/worker/tasks/runtime/red_team.py` lines 157-158
**Apply to:** All control DB operations in `run_deployment_checklist`
```python
with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
```

### Structlog Logging Convention
**Source:** `apps/api/app/worker/tasks/runtime/red_team.py` lines 373-381
**Apply to:** All new Python files
```python
log.info("run_deployment_checklist.complete", agent_id=agent_id, run_id=run_id, recommendation=recommendation)
log.error("run_deployment_checklist.failed", agent_id=agent_id, error=str(exc))
log.warning("run_deployment_checklist.idempotency_skip", agent_id=agent_id)
```

### `__future__` annotations import
**Source:** `apps/api/app/worker/tasks/runtime/red_team.py` line 25; `apps/api/app/api/v1/red_team.py` line 13
**Apply to:** All new Python files
```python
from __future__ import annotations
```

---

## No Analog Found

No files fall into this category. All 14 files (including 5 modifications) have direct analogs in the existing codebase. Every pattern required by Phase 8 was already built in M6/M7 and exists as verified-working code.

---

## Path Correction Summary

| CONTEXT.md Path | Actual Correct Path | Reason |
|---|---|---|
| `apps/api/app/api/v1/schemas/deployment.py` | `apps/api/app/schemas/deployment.py` | `app/api/v1/schemas/` directory does not exist; all schemas live in `app/schemas/` |

The `apps/api/app/schemas/` directory contains all existing schema files: `red_team.py`, `agent.py`, `tenant.py`, `job.py`, etc. The executor must create `deployment.py` there, not in `api/v1/schemas/`.

---

## Metadata

**Analog search scope:** `apps/api/app/services/`, `apps/api/app/worker/tasks/runtime/`, `apps/api/app/api/v1/`, `apps/api/app/schemas/`, `apps/api/app/models/`, `apps/api/app/core/`, `apps/api/alembic/versions/`, `apps/api/tests/unit/`, `apps/api/tests/integration/`, `apps/admin/app/agents/[id]/deploy/`, `scripts/`
**Files scanned:** 16
**Pattern extraction date:** 2026-05-23
