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

Unmeasured is not zero
----------------------
`run_ragas_eval` emits None for a metric whose judge call produced no valid
observation (a NaN — a judge outage, a parse failure), `write_eval_results`
writes that faithfully as NULL, and the AVG below is NULL when a run has no
valid observation for a metric at all. Both routes used to render that NULL as
0.0, which put a fabricated total-quality-collapse directly beside yesterday's
0.94 and made an unmeasured run indistinguishable from a measured catastrophe.
The owner's rational response to a 0.00 is to roll back a healthy agent.

So every metric now travels with the fact of its own measurement:

    aggregate_scores / scores  — NUMERIC COMPATIBILITY PROJECTION. Unmeasured
        still reads 0.0 here, and it is a lie, retained for exactly one reason:
        apps/admin types these fields `number` and calls `.toFixed(2)` on them
        (agents/[id]/eval/page.tsx:291) and plots them (`:184`), so a null would
        throw on the eval page for every tenant — every run that predates this
        phase has no eval_results rows at all. Making that surface render an
        absent measurement is a frontend change and this branch is apps/api
        only. DO NOT read these for quality; read `metrics`.
    metrics                    — the honest one: per metric, {value, measured}.
        value is null exactly when measured is false.
    scenario_count / scored_scenario_count — attempted, and the VALID
        denominator. A pass rate without its denominator must not be
        constructible from this response.
    passed                     — tri-state. None when a gated metric was not
        measured: unknown is neither a pass nor a fail. A client that cannot
        read null degrades to "not passed", which fails closed.
    datasets                   — the same run split into its golden half (fixed,
        run in full every night, comparable across runs) and its exploratory
        half (rotating). They are different measurements: averaging them
        destroys the paired per-item comparison the golden set exists to make,
        so they are aggregated separately and never summed here.
        `datasets.available` is false when the tenant DB predates migration
        0014, which is a different claim from "no golden rows were covered".
        `datasets.unattributed` counts result rows whose scenario no longer
        exists; they carry no metrics and belong to neither dataset, which is
        the same rule eval_service.summarise_run_validity applies to the same
        rows. The two used to disagree about them, giving one run two
        denominators.
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
from app.services.eval_service import (
    DATASET_EXPLORATORY,
    DATASET_GOLDEN,
    EVAL_DATASETS,
)
from app.services.eval_service import METRIC_KEYS as EVAL_METRIC_KEYS
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

# AVG ignores NULLs and is itself NULL when every input was NULL, which is
# precisely the "no valid observation" signal — it is not fabricated here and
# must not be fabricated in the response either. scored_scenario_count is the
# VALID denominator beside the attempted count: a scenario is scored when it
# produced at least one non-NULL score.
_LIST_EVAL_RUNS_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status,
        COUNT(DISTINCT res.scenario_id) AS scenario_count,
        COUNT(DISTINCT res.scenario_id) FILTER (
            WHERE res.score IS NOT NULL
        ) AS scored_scenario_count,
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

# The four M6 metrics, in the order the UI channels read them (D-04). Imported
# from eval_service rather than restated: audit D3 was one call site's copy of a
# column name drifting from the schema's, and four metric names duplicated
# across the writer, the scorer and this reader is the same shape of defect
# waiting to happen.
METRIC_KEYS = EVAL_METRIC_KEYS

# The two metrics the promotion gate is defined over (D-21 LOCKED). Kept
# separate from METRIC_KEYS because `passed` is a claim about these two only.
GATED_METRIC_KEYS = ("faithfulness", "answer_relevancy")

# P2 — the golden/exploratory split, per run. A golden score and an exploratory
# score are DIFFERENT MEASUREMENTS: the golden rows are fixed and run in full
# every night, so consecutive runs are a paired per-item comparison, while the
# exploratory sample rotates and its mean moves whenever the draw moves.
# Averaging them destroys the paired comparison the golden set exists for, so
# they are aggregated separately here and never combined into one number.
#
# The CASE mirrors eval_service.dataset_of() exactly — only the literal
# 'golden' is golden, and NULL (every row predating migration 0014), '' and any
# unrecognised value are exploratory, because membership of a curated set has to
# be asserted rather than inherited. A COALESCE would leave an unexpected value
# to open a third bucket the reader is not expecting.
#
# ONE RULE FOR AN UNATTRIBUTABLE ROW (P2 review). A result row whose scenario no
# longer exists — a deleted scenario, or the synthetic id older builds of
# run_ragas_eval minted when a scenario carried none — used to land in the
# EXPLORATORY bucket here while eval_service.summarise_run_validity dropped it
# from both. Both comments argued their case as the honest one and they
# disagreed, so the same run had two denominators differing by exactly those
# rows: the Celery return excluded them, this response counted them and let
# their scores into the exploratory means. The rule is now the same in both
# places and stated in both: an unattributable row is attributed to NEITHER
# dataset and is counted separately, because "we scored something and cannot say
# what it was about" is a third fact, not an exploratory measurement. The
# `es.id IS NULL` arm is what the LEFT JOIN produces for exactly those rows.
#
# Bounded to the same 50 runs the list query returns — this is one extra round
# trip on a route that already makes two, not a full-table aggregate.
_LIST_EVAL_RUN_DATASETS_SQL = """
    SELECT
        res.eval_run_id,
        CASE
            WHEN es.id IS NULL THEN %(unattributed)s
            WHEN es.dataset = %(golden)s THEN %(golden)s
            ELSE %(exploratory)s
        END AS dataset,
        COUNT(DISTINCT res.scenario_id) AS scenario_count,
        COUNT(DISTINCT res.scenario_id) FILTER (
            WHERE res.score IS NOT NULL
        ) AS scored_scenario_count,
        AVG(CASE WHEN res.metric = 'faithfulness'      THEN res.score END) AS faithfulness,
        AVG(CASE WHEN res.metric = 'answer_relevancy'  THEN res.score END) AS answer_relevancy,
        AVG(CASE WHEN res.metric = 'context_precision' THEN res.score END) AS context_precision,
        AVG(CASE WHEN res.metric = 'context_recall'    THEN res.score END) AS context_recall
    FROM eval_results res
    LEFT JOIN eval_scenarios es ON es.id::text = res.scenario_id
    WHERE res.eval_run_id IN (
        SELECT id FROM eval_runs ORDER BY started_at DESC LIMIT 50
    )
    GROUP BY res.eval_run_id,
             CASE
                 WHEN es.id IS NULL THEN %(unattributed)s
                 WHEN es.dataset = %(golden)s THEN %(golden)s
                 ELSE %(exploratory)s
             END
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


# The third bucket. It is NOT an eval_service dataset and deliberately not in
# EVAL_DATASETS: it is the count of result rows this run cannot attribute to any
# scenario, reported so they do not vanish and kept out of both datasets so they
# cannot be averaged into a measurement. Same rule, same name, as
# summarise_run_validity's `unattributed`.
DATASET_UNATTRIBUTED = "unattributed"


def _dataset_block(per_dataset: dict[str, dict], available: bool) -> dict:
    """Per-dataset aggregates for one run, with every dataset key always present.

    A dataset with no rows in this run reports zero counts and four unmeasured
    metrics rather than being omitted. An absent key would have to be
    interpreted, and the two available interpretations — "this run covered no
    golden rows" and "this response does not carry that information" — are
    exactly the pair `available` exists to separate.

    `unattributed` carries counts only, with no metrics block: a mean over rows
    whose scenario is unknown is a mean over an unknown denominator, and the
    point of separating them is that they must not be readable as a score.
    """
    unattributed = per_dataset.get(
        DATASET_UNATTRIBUTED, {"scenario_count": 0, "scored_scenario_count": 0}
    )
    return {
        "available": available,
        **{
            name: per_dataset.get(
                name,
                {
                    "scenario_count": 0,
                    "scored_scenario_count": 0,
                    "metrics": {
                        metric: {"value": None, "measured": False}
                        for metric in METRIC_KEYS
                    },
                },
            )
            for name in EVAL_DATASETS
        },
        DATASET_UNATTRIBUTED: {
            "scenario_count": unattributed["scenario_count"],
            "scored_scenario_count": unattributed["scored_scenario_count"],
        },
    }


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
        {"eval_runs": [{id, started_at, finished_at, status, scenario_count,
                        scored_scenario_count, aggregate_scores, metrics,
                        datasets}]}

    See the module docstring: `metrics` carries {value, measured} per metric and
    is the one to read; `aggregate_scores` is the numeric projection the shipped
    console still needs, in which an unmeasured metric reads 0.0. `datasets`
    splits the same run into its golden and exploratory halves, which are
    different measurements and must not be averaged together.
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

    # 5a. P2: the golden/exploratory breakdown, one extra round trip in the same
    # pattern. A tenant DB that predates migration 0014 has no `dataset` column,
    # and that is a DIFFERENT claim from "this tenant designated no golden rows"
    # — `dataset_breakdown_available: false` says which one happened rather than
    # letting an empty golden bucket assert the second.
    dataset_rows: list[tuple] = []
    dataset_breakdown_available = True
    try:
        dataset_rows = await asyncio.to_thread(
            _query_tenant_db_sync,
            conn_str,
            _LIST_EVAL_RUN_DATASETS_SQL,
            {
                "golden": DATASET_GOLDEN,
                "exploratory": DATASET_EXPLORATORY,
                "unattributed": DATASET_UNATTRIBUTED,
            },
        )
    except psycopg2.errors.UndefinedColumn:
        dataset_breakdown_available = False
        log.info(
            "list_eval_runs.dataset_column_absent",
            agent_id=str(agent_id),
            tenant_id=str(tenant.id),
        )

    # run_id -> dataset -> aggregates
    by_run: dict[str, dict[str, dict]] = {}
    for row in dataset_rows:
        run_key, dataset_name, scenario_count, scored_count, *metric_averages = row
        by_run.setdefault(str(run_key), {})[dataset_name] = {
            "scenario_count": int(scenario_count or 0),
            "scored_scenario_count": int(scored_count or 0),
            "metrics": {
                metric: {
                    "value": float(mean) if mean is not None else None,
                    "measured": mean is not None,
                }
                for metric, mean in zip(METRIC_KEYS, metric_averages)
            },
        }

    # 5b. OPS-12: ORRERY ledger — same tenant-DB round-trip pattern (asyncio.to_thread
    # + _query_tenant_db_sync), computed in this same route so the eval-runs response
    # is the single place the admin UI reads eval provenance from.
    ledger_rows = await asyncio.to_thread(
        _query_tenant_db_sync, conn_str, _LEDGER_SQL, {}
    )
    born_in_production_count, red_team_count, authored_count = (
        ledger_rows[0] if ledger_rows else (0, 0, 0)
    )

    # 6. Build response matching the exact shape from RESEARCH.md §9, plus the
    #    measurement record each metric now travels with.
    eval_runs = []
    for row in rows:
        (
            run_id,
            started_at,
            finished_at,
            status,
            scenario_count,
            scored_scenario_count,
            *metric_averages,
        ) = row
        means = dict(zip(METRIC_KEYS, metric_averages))
        eval_runs.append(
            {
                "id": str(run_id),
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
                "status": status,
                # attempted, and the valid denominator beside it
                "scenario_count": int(scenario_count) if scenario_count else 0,
                "scored_scenario_count": (
                    int(scored_scenario_count) if scored_scenario_count else 0
                ),
                # The honest reading. value is null exactly when measured is false.
                "metrics": {
                    metric: {
                        "value": float(mean) if mean is not None else None,
                        "measured": mean is not None,
                    }
                    for metric, mean in means.items()
                },
                # Numeric compatibility projection — see the module docstring.
                # An unmeasured metric reads 0.0 here and that is not a score.
                "aggregate_scores": {
                    metric: float(mean) if mean is not None else 0.0
                    for metric, mean in means.items()
                },
                # The two measurements, kept apart. Never add them together:
                # the golden set is fixed and paired across runs, the
                # exploratory sample rotates, and one mean over both moves
                # whenever the draw moves while looking like a quality change.
                "datasets": _dataset_block(
                    by_run.get(str(run_id), {}), dataset_breakdown_available
                ),
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
        {"results": [{scenario_id, question, source, scores, metrics, passed}]}

    passed = True when faithfulness >= EVAL_FAITHFULNESS_THRESHOLD AND
    answer_relevancy >= EVAL_RELEVANCY_THRESHOLD — the 2-metric promotion gate
    used by promote_to_verified_qa (D-21) — False when a measured score misses
    it, and None when either gated metric was never measured. That third state
    is the point: a judge outage NULLs every score, and rendering those rows as
    passed=false reports a total quality collapse for a run that measured
    nothing. eval_service._meets_score_thresholds refuses a None the same way.
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

    # 4. Group rows by scenario_id. Each row is:
    #    (scenario_id, question, source, metric, score) — score may be NULL,
    #    which means the judge produced no valid observation for that metric.
    #    A metric with no row at all is equally unmeasured, so both start None.
    scenarios: dict[str, dict] = {}
    for scenario_id, question, source, metric, score in rows:
        sid = str(scenario_id)
        if sid not in scenarios:
            scenarios[sid] = {
                "scenario_id": sid,
                "question": question or "",
                "source": source or "generated",
                "measured": {key: None for key in METRIC_KEYS},
            }
        if metric in scenarios[sid]["measured"] and score is not None:
            scenarios[sid]["measured"][metric] = float(score)

    # 5. Compute the passed flag over the two GATED metrics (D-21). Unknown is
    #    neither a pass nor a fail: if either gated metric was not measured the
    #    verdict is None, because "we did not measure it" must never be
    #    rendered as "it failed" — that is what turns a judge outage into an
    #    apparent quality collapse and an owner-initiated rollback.
    results = []
    for scen in scenarios.values():
        measured = scen.pop("measured")
        thresholds = {
            "faithfulness": settings.EVAL_FAITHFULNESS_THRESHOLD,
            "answer_relevancy": settings.EVAL_RELEVANCY_THRESHOLD,
        }
        if any(measured[key] is None for key in GATED_METRIC_KEYS):
            passed = None
        else:
            passed = all(
                measured[key] >= thresholds[key] for key in GATED_METRIC_KEYS
            )
        results.append(
            {
                **scen,
                # The honest reading — see the module docstring.
                "metrics": {
                    key: {"score": measured[key], "measured": measured[key] is not None}
                    for key in METRIC_KEYS
                },
                # Numeric compatibility projection: unmeasured reads 0.0.
                "scores": {
                    key: measured[key] if measured[key] is not None else 0.0
                    for key in METRIC_KEYS
                },
                "passed": passed,
            }
        )

    log.info(
        "get_eval_run_results.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        tenant_id=str(tenant.id),
        scenario_count=len(results),
        # The denominator, in the log too: a run whose scenarios are all
        # unmeasured is a run that measured nothing, and it should be visible
        # here without anyone opening the response body.
        unmeasured_scenario_count=sum(1 for r in results if r["passed"] is None),
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
