"""Eval routes for W Chats M6.

Queries tenant DB (eval_runs, eval_results, eval_scenarios) for eval run history
and per-scenario results. All routes require X-API-Key auth via get_current_tenant.
IDOR prevented by verifying agent.tenant_id == tenant.id.

Routes:
    GET  /agents/{agent_id}/eval-runs                   — list runs with aggregate scores (EVL-06)
    GET  /agents/{agent_id}/eval-runs/{run_id}/results  — per-scenario results (EVL-07)
    POST /agents/{agent_id}/eval-runs/trigger            — dispatch run_eval_suite manually (EVL-04)

Architecture:
    - eval_runs and eval_results live in the TENANT DB (per-Neon-project), not the control DB.
    - Routes fetch agent from control DB (get_async_db) for IDOR check only.
    - Tenant DB queries go through psycopg2 with asyncio.to_thread() to avoid blocking the
      FastAPI event loop (D-30 pattern — same as validators.py).
    - POST /trigger dispatches Celery task and returns 202 immediately — eval is async.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.config import settings
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.worker.tasks.runtime.eval import run_eval_suite

router = APIRouter(tags=["evals"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helper — wraps blocking psycopg2 calls for asyncio.to_thread
# ---------------------------------------------------------------------------


def _query_tenant_db_sync(conn_str: str, sql: str, params: dict) -> list[tuple]:
    """Execute a SELECT against the tenant DB synchronously.

    Wraps psycopg2 in a try/finally to ensure the connection is always closed.
    Called inside asyncio.to_thread() to avoid blocking the FastAPI event loop.

    Args:
        conn_str: Decrypted tenant DB connection string (never logged — T-02-01).
        sql: SQL query with %(name)s placeholders.
        params: Dict of query parameters.

    Returns:
        List of row tuples from fetchall().
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Route 1: GET /agents/{agent_id}/eval-runs — list runs with aggregate scores
# ---------------------------------------------------------------------------

_LIST_EVAL_RUNS_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status,
        COUNT(DISTINCT res.scenario_id) AS scenario_count,
        AVG(CASE WHEN res.metric = 'faithfulness'      THEN res.score END) AS faithfulness,
        AVG(CASE WHEN res.metric = 'answer_relevancy'  THEN res.score END) AS answer_relevancy,
        AVG(CASE WHEN res.metric = 'context_precision' THEN res.score END) AS context_precision,
        AVG(CASE WHEN res.metric = 'context_recall'    THEN res.score END) AS context_recall
    FROM eval_runs er
    LEFT JOIN eval_results res ON res.eval_run_id = er.id
    GROUP BY er.id, er.started_at, er.finished_at, er.status
    ORDER BY er.started_at DESC
    LIMIT 50
"""

# OPS-12: ORRERY ledger — eval provenance (born-in-production vs authored counts).
# provenance IS NULL rows predate provenance tracking (migration 0011) and are
# always treated as authored, never as an error state (21-RESEARCH.md Runtime
# State Inventory / this plan's must_haves prohibitions).
_LEDGER_SQL = """
    SELECT
        COUNT(*) FILTER (WHERE source = 'production') AS born_in_production_count,
        COUNT(*) FILTER (WHERE source = 'red_team')    AS red_team_count,
        COUNT(*) FILTER (
            WHERE source IN ('generated', 'mined') OR provenance IS NULL
        ) AS authored_count
    FROM eval_scenarios
"""


@router.get("/agents/{agent_id}/eval-runs")
async def list_eval_runs(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return up to 50 eval runs for an agent with per-run aggregate metric scores.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id (IDOR prevention).
        Returns 404 for unknown agents or agents belonging to a different tenant.

    Response shape:
        {"eval_runs": [{id, started_at, finished_at, status, scenario_count, aggregate_scores}]}
    """
    # 1. Fetch agent from control DB (only metadata — not tenant DB)
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must have a tenant DB configured
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # 4. Decrypt connection string at runtime — never stored, never logged (T-02-01)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # 5. Query tenant DB in a thread pool to avoid blocking the event loop
    rows = await asyncio.to_thread(
        _query_tenant_db_sync, conn_str, _LIST_EVAL_RUNS_SQL, {}
    )

    # 5b. OPS-12: ORRERY ledger — same tenant-DB round-trip pattern (asyncio.to_thread
    # + _query_tenant_db_sync), computed in this same route so the eval-runs response
    # is the single place the admin UI reads eval provenance from.
    ledger_rows = await asyncio.to_thread(
        _query_tenant_db_sync, conn_str, _LEDGER_SQL, {}
    )
    born_in_production_count, red_team_count, authored_count = (
        ledger_rows[0] if ledger_rows else (0, 0, 0)
    )

    # 6. Build response matching the exact shape from RESEARCH.md §9
    eval_runs = []
    for row in rows:
        run_id, started_at, finished_at, status, scenario_count, faithfulness, answer_relevancy, context_precision, context_recall = row
        eval_runs.append(
            {
                "id": str(run_id),
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
                "status": status,
                "scenario_count": int(scenario_count) if scenario_count else 0,
                "aggregate_scores": {
                    "faithfulness": float(faithfulness) if faithfulness is not None else 0.0,
                    "answer_relevancy": float(answer_relevancy) if answer_relevancy is not None else 0.0,
                    "context_precision": float(context_precision) if context_precision is not None else 0.0,
                    "context_recall": float(context_recall) if context_recall is not None else 0.0,
                },
            }
        )

    log.info(
        "list_eval_runs.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        run_count=len(eval_runs),
        born_in_production_count=int(born_in_production_count or 0),
        authored_count=int(authored_count or 0),
    )
    return {
        "eval_runs": eval_runs,
        "ledger": {
            "born_in_production_count": int(born_in_production_count or 0),
            "red_team_count": int(red_team_count or 0),
            "authored_count": int(authored_count or 0),
        },
    }


# ---------------------------------------------------------------------------
# Route 2: GET /agents/{agent_id}/eval-runs/{run_id}/results — per-scenario results
# ---------------------------------------------------------------------------

_GET_RUN_RESULTS_SQL = """
    SELECT
        res.scenario_id,
        es.question,
        es.source,
        res.metric,
        res.score
    FROM eval_results res
    LEFT JOIN eval_scenarios es ON es.id::text = res.scenario_id
    WHERE res.eval_run_id = %(run_id)s
    ORDER BY res.scenario_id, res.metric
"""


@router.get("/agents/{agent_id}/eval-runs/{run_id}/results")
async def get_eval_run_results(
    agent_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return per-scenario metric scores for a specific eval run.

    Security:
        Same IDOR prevention as list_eval_runs — agent ownership verified.

    Response shape:
        {"results": [{scenario_id, question, source, scores, passed}]}

    passed = True when faithfulness >= EVAL_FAITHFULNESS_THRESHOLD AND answer_relevancy >= EVAL_RELEVANCY_THRESHOLD.
    Mirrors the 2-metric promotion gate used by promote_to_verified_qa (D-21).
    """
    # 1. Fetch agent and verify ownership (same as list_eval_runs)
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # 2. Decrypt connection string — never logged (T-02-01)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # 3. Query tenant DB in a thread pool
    rows = await asyncio.to_thread(
        _query_tenant_db_sync,
        conn_str,
        _GET_RUN_RESULTS_SQL,
        {"run_id": str(run_id)},
    )

    # 4. Group rows by scenario_id to build per-scenario score dicts
    #    Each row is: (scenario_id, question, source, metric, score)
    scenarios: dict[str, dict] = {}
    for scenario_id, question, source, metric, score in rows:
        sid = str(scenario_id)
        if sid not in scenarios:
            scenarios[sid] = {
                "scenario_id": sid,
                "question": question or "",
                "source": source or "generated",
                "scores": {
                    "faithfulness": 0.0,
                    "answer_relevancy": 0.0,
                    "context_precision": 0.0,
                    "context_recall": 0.0,
                },
            }
        if metric in scenarios[sid]["scores"] and score is not None:
            scenarios[sid]["scores"][metric] = float(score)

    # 5. Compute passed flag — mirrors the 2-metric promotion gate in promote_to_verified_qa (D-21)
    #    faithfulness >= EVAL_FAITHFULNESS_THRESHOLD AND answer_relevancy >= EVAL_RELEVANCY_THRESHOLD
    results = []
    for scen in scenarios.values():
        s = scen["scores"]
        passed = (
            s["faithfulness"] >= settings.EVAL_FAITHFULNESS_THRESHOLD
            and s["answer_relevancy"] >= settings.EVAL_RELEVANCY_THRESHOLD
        )
        results.append({**scen, "passed": passed})

    log.info(
        "get_eval_run_results.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        tenant_id=str(tenant.id),
        scenario_count=len(results),
    )
    return {"results": results}


# ---------------------------------------------------------------------------
# Route 3: POST /agents/{agent_id}/eval-runs/trigger — manual run dispatch
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/eval-runs/trigger", status_code=202)
async def trigger_eval_run(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Manually dispatch run_eval_suite for an agent and return 202 immediately.

    Used by the "Run Now" button on the eval dashboard (EVL-04 / D-30).

    Security:
        Agent ownership verified (IDOR prevention).
        Agent must be in 'ready' state — 400 otherwise.

    Celery:
        Dispatches run_eval_suite.apply_async(kwargs={"agent_id": str(agent_id)},
        queue="runtime"). Only agent_id is passed — no connection string in task
        args (CTL-08 / D-18 LOCKED).

    Returns HTTP 202 immediately. The frontend polls GET /eval-runs to detect
    completion.

    Response: {"status": "queued", "task_id": str, "agent_id": str}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — 404 on ownership mismatch (same as other routes)
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must be ready to run evals
    if agent.status != "ready":
        raise HTTPException(
            status_code=400,
            detail="Agent must be in ready state to run evals",
        )

    # 4. Dispatch Celery task — only agent_id, never conn_str (CTL-08)
    task = run_eval_suite.apply_async(
        kwargs={"agent_id": str(agent_id)},
        queue="runtime",
    )

    log.info(
        "eval_trigger.dispatched",
        agent_id=str(agent_id),
        task_id=task.id,
        tenant_id=str(tenant.id),
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "agent_id": str(agent_id),
    }
