"""Eval routes for W Chats M6.

Queries tenant DB (eval_runs, eval_results, eval_scenarios) for eval run history
and per-scenario results. All routes require X-API-Key auth via get_current_tenant.
IDOR prevented by verifying agent.tenant_id == tenant.id.

Routes:
    GET  /agents/{agent_id}/eval-runs                   — list runs with aggregate scores (EVL-06)
    GET  /agents/{agent_id}/eval-runs/{run_id}/results  — per-scenario results (EVL-07)
    POST /agents/{agent_id}/eval-runs/trigger            — dispatch run_eval_suite manually (EVL-04)
    GET  /agents/{agent_id}/eval-scenarios/unlabelled    — the labelling queue (D6 P2)
    POST /agents/{agent_id}/eval-scenarios/{scenario_id}/label — record one human label (D6 P2)

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

The labelling queue (D6 P2)
---------------------------
`eval_scenarios` rows written with `reference_answer = ''` — every mined
production failure, every owner-filed failing trace, every contained red-team
finding — are inert to the nightly selector by construction, because
`run_eval_suite` selects `WHERE reference_answer != ''`. That exclusion is
correct and this module does not touch it. What was missing is any path by
which a row LEAVES that state. These two routes are that path:

    GET  .../eval-scenarios/unlabelled          the queue, plus the counts
    POST .../eval-scenarios/{id}/label          one human-authored answer

A labelled row becomes eligible to the existing selector with NO change to the
selector: the write sets `reference_answer`, and `reference_answer != ''` is the
selector's only label predicate. `counts.eligible` is reported precisely so that
identity is readable rather than asserted.

THE AUTHOR IS DERIVED, NEVER SUBMITTED. `labelled_by` is computed from the
authenticated principal inside the handler and the request model forbids extra
fields, so a body naming an author is a 422 and not a field quietly ignored.
That is P1's settled decision, and it is the same argument as
`label_service`'s absent tier parameter one level up: a caller able to name the
human is a caller able to name any human.

THIS ORDERING IS NOT AN UNCERTAINTY ORDERING. See QUEUE_ORDERING below — the
judge-confidence signal is not joinable to a scenario, and the response says so
in the payload rather than leaving a reader to assume the queue is smarter than
it is.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
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
    HUMAN_LABEL_TIERS,
    LABEL_TIER_COLUMN,
    SCENARIO_SOURCE_TRUST_TIER,
    scenario_trust_tier,
    trust_tier_rank,
)
from app.services.eval_service import METRIC_KEYS as EVAL_METRIC_KEYS
from app.services.label_service import (
    HumanLabelRefused,
    LabelRejected,
    assert_human_context,
    record_human_label,
)
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


# ---------------------------------------------------------------------------
# D6 P2 — the labelling queue
# ---------------------------------------------------------------------------
# WHY THIS QUEUE IS NOT ORDERED BY JUDGE UNCERTAINTY, WHICH IS WHAT IT SHOULD
# BE ORDERED BY.
#
# The plan asks for uncertainty ordering, and it is right to: the rows the
# judges were least sure about are worth several times more per owner label than
# the newest rows. The signal exists — `validators.py` puts `confidence` on
# every `gatekeeper.complete` and `auditor.complete` payload. It is not joinable
# to a scenario, for three independent reasons, and this comment states them
# rather than substituting a proxy and calling it uncertainty:
#
#   1. DIFFERENT DATABASES. `emit()` is called with the session from
#      `get_sync_db()`, so `job_events` is a CONTROL-DB table, while
#      `eval_scenarios` lives in the tenant's own Neon project (one project per
#      tenant, so that eval branching works). There is no SQL join across them.
#
#   2. NO JOIN KEY, so application-side correlation does not rescue it either.
#      `scenario_service.store_scenarios` inserts
#      (id, source, question, reference_answer, retrieved_contexts,
#      scenario_category, created_at) — no job_id, no conversation_id, not even
#      the `origin_trace_id` column that 0011 added and that
#      `insert_provenance_scenario` populates for the promote and red-team
#      paths. And `mine_production_scenarios` selects `je.job_id` and
#      `je.payload->>'verdict'` only: it discards `payload->>'confidence'` at
#      the source, so the number is dropped before the row is even built.
#
#   3. THE ONE TENANT-SIDE CONFIDENCE COLUMN IS THE WRONG POPULATION.
#      `verified_qa_candidates.auditor_confidence` (migration 0004) is written
#      by `run_auditor` only when the verdict is `grounded` AND the confidence
#      clears the threshold — precisely the complement of the
#      fail/ungrounded/partial turns this queue is built out of. The confidence
#      attached to a FAILED judgement is never persisted tenant-side at all.
#
# Making it joinable is a schema change (a key carried onto the scenario row at
# mining time) plus a change to the miner, and it would still be retroactively
# empty for every row already mined. That is `BACKLOG 6.4`'s real cost and it is
# not P2's to spend.
#
# WHAT THE ORDERING ACTUALLY IS, and why each key earns its place:
#
#   origin trust tier, descending — a mined production failure, an owner-filed
#       failing trace and a contained red-team finding are all
#       `customer_negative`: a question a real customer asked that the agent got
#       wrong. A `generated` row with an empty answer is `model_generated`: an
#       artefact of a generation that came out without an answer. The first is
#       worth more of the owner's time than the second. The rank comes from
#       `eval_service`'s own tables, never restated here.
#   created_at, ASCENDING — oldest first, which is the opposite of recency, not
#       a dressed-up version of it. The oldest unlabelled row is the one that
#       has been unmeasurable the longest, and newest-first starves the tail of
#       the queue permanently.
#   id — the tiebreak that makes this a TOTAL order. Without it two rows sharing
#       a source and a created_at have no defined relative position, and
#       LIMIT/OFFSET pagination can then show one row twice and skip another.

# Reported verbatim on every queue response, so a console cannot mistake this
# for an uncertainty ranking and neither can a reader of the payload. Copied at
# each use site rather than handed out by reference, matching
# eval_service.VERIFIED_QA_PROMOTION_DECISION.
QUEUE_ORDERING: dict = {
    "by_uncertainty": False,
    "keys": ["origin_trust_tier DESC", "created_at ASC", "id ASC"],
    "reason": (
        "Judge confidence is emitted onto job_events, which is a control-DB "
        "table, while eval_scenarios lives in the tenant's own Neon project — "
        "no SQL join spans them. Application-side correlation has no key "
        "either: store_scenarios writes no job_id, conversation_id or "
        "origin_trace_id for a mined row, and mine_production_scenarios "
        "discards payload->>'confidence' at the point it reads the event. The "
        "one tenant-side confidence column, verified_qa_candidates."
        "auditor_confidence, is written only for grounded turns above "
        "threshold — the complement of the failed turns this queue is built "
        "from. So this ordering is origin trust tier first, then oldest first; "
        "it is not an uncertainty ordering and is not offered as a proxy for "
        "one. BACKLOG 6.4."
    ),
}


def _source_priority_order() -> list[str]:
    """Scenario sources, most-worth-labelling first.

    Derived from `eval_service`'s tier tables rather than restated. A source
    added to the schema without a tier resolves to 'unknown', ranks below
    'model_generated' by `trust_tier_rank`, and therefore sorts to the end of
    this list — and a source added to the schema without being added to
    `SCENARIO_SOURCE_TRUST_TIER` at all is absent from the list entirely, where
    `array_position` returns NULL and `NULLS LAST` sorts it last. Both
    directions fail in the same direction: an origin nobody has classified is
    never promoted to the front of the owner's queue.

    The secondary key is the source name, so the order is deterministic between
    two sources that share a tier rather than depending on dict iteration order.
    """
    return sorted(
        SCENARIO_SOURCE_TRUST_TIER,
        key=lambda source: (-trust_tier_rank(scenario_trust_tier(source)), source),
    )


# The nightly selector's label predicate, spelled once. `run_eval_suite` filters
# on exactly this text in all three of its scenario queries, and
# test_the_queue_is_the_exact_complement_of_the_eval_selector reads it back out
# of that task's source — so `unlabelled` here and "will never be scored" there
# cannot drift apart without a test going red. THIS MODULE DOES NOT CHANGE THE
# SELECTOR; the whole point of P2 is that a labelled row becomes eligible
# without the selector being touched.
SELECTOR_ELIGIBILITY_PREDICATE = "reference_answer != ''"

# The queue itself. `dataset` (0014) and the label columns (0016) are
# deliberately NOT selected: this route needs neither, and every column it does
# not name is a tenant-DB migration state it cannot break on. It requires 0011,
# which _LEDGER_SQL above already requires unconditionally.
_UNLABELLED_QUEUE_SQL = f"""
    SELECT
        id,
        source,
        question,
        scenario_category,
        retrieved_contexts,
        provenance,
        origin_trace_id,
        created_at
    FROM eval_scenarios
    WHERE NOT ({SELECTOR_ELIGIBILITY_PREDICATE})
    ORDER BY
        array_position(%(source_priority)s::text[], source) ASC NULLS LAST,
        created_at ASC,
        id ASC
    LIMIT %(limit)s OFFSET %(offset)s
"""

# The counts, in one round trip, every one of them a count out of `total`.
# `total` is not decoration: a rate must not be constructible from this
# response without its denominator being in the reader's hand at the same
# moment, which is the same house rule summarise_run_validity follows.
#
# `unlabelled` is the NEGATION of the selector's own predicate rather than a
# separately-written `= ''`, so `unlabelled + labelled == total` is an identity
# of the SQL and not a coincidence of two hand-written conditions agreeing.
# (`reference_answer` is NOT NULL since migration 0005, so neither FILTER can
# drop a row into a third bucket.)
_QUEUE_COUNTS_SQL = f"""
    SELECT
        COUNT(*)                                                        AS total,
        COUNT(*) FILTER (WHERE NOT ({SELECTOR_ELIGIBILITY_PREDICATE}))  AS unlabelled,
        COUNT(*) FILTER (WHERE {SELECTOR_ELIGIBILITY_PREDICATE})        AS labelled,
        COUNT(*) FILTER (
            WHERE {LABEL_TIER_COLUMN} = ANY(%(human_tiers)s::text[])
        )                                                               AS human_labelled
    FROM eval_scenarios
"""

# The same counts for a tenant DB that predates migration 0016. NOT a
# convenience: 0016 has never been applied to any database, so this is the path
# every tenant is on today. `human_labelled` is then reported as null with
# `label_provenance_available: false` beside it — "no way to tell" and "none"
# are different claims, and a metric over zero valid observations is unknown,
# never zero.
_QUEUE_COUNTS_PRE_0016_SQL = f"""
    SELECT
        COUNT(*)                                                        AS total,
        COUNT(*) FILTER (WHERE NOT ({SELECTOR_ELIGIBILITY_PREDICATE}))  AS unlabelled,
        COUNT(*) FILTER (WHERE {SELECTOR_ELIGIBILITY_PREDICATE})        AS labelled
    FROM eval_scenarios
"""


def _queue_counts_sync(conn_str: str) -> dict:
    """(total, unlabelled, labelled, eligible, human_labelled) for one tenant DB.

    Blocking psycopg2; called through asyncio.to_thread like every other tenant
    query in this module.

    `eligible` is `labelled`, and that identity IS the P2 claim rather than a
    redundancy: the nightly selector's only label-related predicate is
    SELECTOR_ELIGIBILITY_PREDICATE, so writing an answer is the whole of what
    makes a row eligible and the selector needs no change. Reporting both names
    lets a reader check that from the payload instead of taking it on trust.
    Eligible is not "will be scored tonight" — the exploratory half of the run
    is a sample of at most EXPLORATORY_SAMPLE_SIZE rows — it is "the selector
    will consider it".
    """
    try:
        rows = _query_tenant_db_sync(
            conn_str,
            _QUEUE_COUNTS_SQL,
            {"human_tiers": list(HUMAN_LABEL_TIERS)},
        )
        total, unlabelled, labelled, human_labelled = rows[0] if rows else (0, 0, 0, 0)
        label_provenance_available = True
    except psycopg2.errors.UndefinedColumn:
        rows = _query_tenant_db_sync(conn_str, _QUEUE_COUNTS_PRE_0016_SQL, {})
        total, unlabelled, labelled = rows[0] if rows else (0, 0, 0)
        human_labelled = None
        label_provenance_available = False

    labelled_count = int(labelled or 0)
    return {
        "total": int(total or 0),
        "unlabelled": int(unlabelled or 0),
        "labelled": labelled_count,
        "eligible": labelled_count,
        "human_labelled": (
            int(human_labelled or 0) if label_provenance_available else None
        ),
        "label_provenance_available": label_provenance_available,
    }


def _unlabelled_page_sync(conn_str: str, limit: int, offset: int) -> list[tuple]:
    """One page of the unlabelled queue, in the order QUEUE_ORDERING describes."""
    return _query_tenant_db_sync(
        conn_str,
        _UNLABELLED_QUEUE_SQL,
        {
            "source_priority": _source_priority_order(),
            "limit": limit,
            "offset": offset,
        },
    )


async def _resolve_agent_tenant_db(
    agent_id: UUID, db: AsyncSession, tenant: Tenant
) -> str:
    """The decrypted tenant connection string for *agent_id*, or an HTTPException.

    THE TENANT-ISOLATION MECHANISM FOR BOTH QUEUE ROUTES, factored out so the
    two cannot drift into two different checks. Byte-for-byte the sequence the
    three routes above already use, and it is structural rather than advisory:

      - the agent is fetched from the CONTROL db and 404s unless
        `agent.tenant_id == tenant.id`, so a foreign agent_id is
        indistinguishable from a nonexistent one and tenant enumeration gets
        nothing;
      - the only database a queue route ever opens is the one reached through
        THAT agent's own encrypted connection string. A scenario_id belonging to
        another tenant is not a row in this connection's eval_scenarios, so the
        label write matches nothing and the route 404s on the row count.

    404 rather than 403 on the ownership mismatch, matching the routes above:
    403 would confirm the agent exists.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # Decrypted at runtime — never stored, never logged (T-02-01)
    return fernet_decrypt(agent.neon_connection_string)


@router.get("/agents/{agent_id}/eval-scenarios/unlabelled")
async def list_unlabelled_scenarios(
    agent_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """One page of scenarios awaiting a human answer, plus the queue's counts.

    Security:
        Same ownership check as every other route here — see
        `_resolve_agent_tenant_db`.

    Response shape:
        {"scenarios": [...], "counts": {...}, "ordering": {...}, "page": {...}}

    `counts` carries `total` as the denominator of every other figure in it, and
    `human_labelled` is null (not 0) with `label_provenance_available: false`
    beside it on a tenant DB that predates migration 0016 — which is every
    tenant DB today, because 0016 has not been applied anywhere.

    `ordering` states in the payload that this is NOT an uncertainty ordering
    and why the signal is unavailable. See QUEUE_ORDERING.
    """
    conn_str = await _resolve_agent_tenant_db(agent_id, db, tenant)

    rows = await asyncio.to_thread(_unlabelled_page_sync, conn_str, limit, offset)
    counts = await asyncio.to_thread(_queue_counts_sync, conn_str)

    scenarios = []
    for (
        scenario_id,
        source,
        question,
        scenario_category,
        retrieved_contexts,
        provenance,
        origin_trace_id,
        created_at,
    ) in rows:
        scenarios.append(
            {
                "id": str(scenario_id),
                "source": source,
                # The tier the row's ORIGIN earns, which is NOT the tier a label
                # would carry — that distinction is the whole of D6 P1, and this
                # response keeps the two named apart on the wire too.
                "origin_trust_tier": scenario_trust_tier(source),
                "question": question or "",
                "scenario_category": scenario_category,
                # What the owner needs in order to write a grounded answer.
                # Empty for every mined row (mine_production_scenarios stores
                # []), which is itself worth seeing.
                "retrieved_contexts": retrieved_contexts or [],
                "provenance": provenance,
                "origin_trace_id": origin_trace_id,
                "created_at": created_at.isoformat() if created_at else None,
            }
        )

    log.info(
        "list_unlabelled_scenarios.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        returned=len(scenarios),
        unlabelled=counts["unlabelled"],
        total=counts["total"],
        label_provenance_available=counts["label_provenance_available"],
    )
    return {
        "scenarios": scenarios,
        "counts": counts,
        "ordering": dict(QUEUE_ORDERING),
        "page": {"limit": limit, "offset": offset, "returned": len(scenarios)},
    }


class ScenarioLabelRequest(BaseModel):
    """The entire body of a labelling request: the answer, and nothing else.

    `extra="forbid"` is load-bearing, not tidiness. It is the structural half of
    the decision P1 settled and left to P2 to enforce: the label's author is
    DERIVED from the authenticated principal and is never read from the request.
    With `extra` at its default an unrecognised `labelled_by` would be dropped
    silently, the request would succeed, and the caller would have every reason
    to believe it had named the author. Forbidding it makes that a 422 the
    caller can see.

    There is no tier field either, and there must never be one — same argument
    as `record_human_label`'s absent tier parameter, one level up the stack. The
    tier is what this route asserts, not what its caller asks for.
    """

    model_config = ConfigDict(extra="forbid")

    reference_answer: str = Field(
        min_length=1,
        description="The answer the authenticated human wrote for this question.",
    )


def _label_principal(tenant: Tenant) -> str:
    """The `labelled_by` value, derived from the authenticated principal.

    IT NAMES AN ACCOUNT, NOT A PERSON, AND THE PREFIX SAYS SO.
    `get_current_tenant` resolves to a `Tenant`, by either of two credential
    paths: a Clerk JWT, behind which there is one specific human, or an
    `X-API-Key`, which is a machine credential with no human behind it at all.
    The dependency does not report which path was taken, so reading
    `tenant.clerk_user_id` would attribute an API-key write to a Clerk user who
    may not have made it — a false claim about authorship stamped beside a human
    trust tier, which is the one place in the system where authorship claims are
    the entire point. Recording the account is the strongest claim this auth
    layer actually supports, so it is the claim made. Narrowing it to a person
    is a change to `app/api/deps.py` (a principal-aware dependency), not to this
    route.

    Matches `deployment.py`'s `run.approved_by = str(tenant.id)` in substance;
    the `tenant:` prefix is added because this value is stored next to a human
    trust tier, where a bare UUID would read as a user id.
    """
    return f"tenant:{tenant.id}"


def _record_label_sync(
    conn_str: str,
    scenario_id: str,
    reference_answer: str,
    labelled_by: str,
) -> dict:
    """Open a tenant connection, record one human label, commit, close.

    `record_human_label` neither commits nor closes — the caller owns the
    transaction — so this function is that owner, and the tenant connection
    string never leaves this frame.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        try:
            result = record_human_label(
                conn,
                scenario_id=scenario_id,
                reference_answer=reference_answer,
                labelled_by=labelled_by,
            )
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        return result
    finally:
        conn.close()


@router.post("/agents/{agent_id}/eval-scenarios/{scenario_id}/label")
async def label_eval_scenario(
    agent_id: UUID,
    scenario_id: UUID,
    body: ScenarioLabelRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Record one human-authored reference answer on one unlabelled scenario.

    Security:
        Ownership check per `_resolve_agent_tenant_db`. The write lands in the
        tenant's own database and nowhere else, so a `scenario_id` from another
        tenant matches no row and returns 404.

    The tier is not a parameter of this route and there is no field for it:
    `record_human_label` stamps `human_authored` and the caller cannot ask for
    anything else. The row's `source` is untouched — it still says where the
    QUESTION came from.

    Nothing here reaches a customer. `verified_qa` promotion is off by the
    owner's settled decision of 2026-08-08 and
    `eval_service.VERIFIED_QA_PROMOTION_DECISION` records the disablement with
    its reason on every run.

    Returns 200 with the recorded provenance and the queue's counts recomputed
    AFTER the write, so the labelled -> eligible transition is observable in the
    same response that caused it.
    """
    # The runtime context guard, before any tenant work and before a connection
    # could be opened. `record_human_label` re-asserts this as its own first
    # statement; running it here as well is what keeps P1's property — a refused
    # context never reaches the database — true across the thread hop, since
    # asyncio.to_thread copies this context into the worker thread but
    # _record_label_sync opens its connection before the writer can refuse.
    try:
        assert_human_context()
    except HumanLabelRefused as exc:
        log.error(
            "label_eval_scenario.refused_context",
            agent_id=str(agent_id),
            tenant_id=str(tenant.id),
            reason=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="A human trust tier cannot be recorded from this context.",
        )

    conn_str = await _resolve_agent_tenant_db(agent_id, db, tenant)

    try:
        result = await asyncio.to_thread(
            _record_label_sync,
            conn_str,
            str(scenario_id),
            body.reference_answer,
            _label_principal(tenant),
        )
    except LabelRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HumanLabelRefused as exc:
        log.error(
            "label_eval_scenario.refused_context",
            agent_id=str(agent_id),
            tenant_id=str(tenant.id),
            reason=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="A human trust tier cannot be recorded from this context.",
        )
    except psycopg2.errors.UndefinedColumn:
        # 0016 has not been applied to this tenant database, which is the state
        # every tenant is in today. A provisioning gap, not a bad request, and
        # the detail names the migration so the operator does not have to guess.
        log.error(
            "label_eval_scenario.label_columns_absent",
            agent_id=str(agent_id),
            tenant_id=str(tenant.id),
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "This tenant database has no label provenance columns — "
                "alembic_tenant migration 0016 has not been applied to it."
            ),
        )

    if result["rows_updated"] == 0:
        # No row with that id in THIS tenant's database. Same 404 as a foreign
        # agent_id, and for the same reason: the two must not be distinguishable.
        log.info(
            "label_eval_scenario.no_such_scenario",
            agent_id=str(agent_id),
            scenario_id=str(scenario_id),
            tenant_id=str(tenant.id),
        )
        raise HTTPException(status_code=404, detail="Scenario not found")

    counts = await asyncio.to_thread(_queue_counts_sync, conn_str)

    log.info(
        "label_eval_scenario.recorded",
        agent_id=str(agent_id),
        scenario_id=str(scenario_id),
        tenant_id=str(tenant.id),
        label_trust_tier=result["label_trust_tier"],
        labelled_by=result["labelled_by"],
        unlabelled=counts["unlabelled"],
        total=counts["total"],
        # The answer text is never logged — it is customer-domain content, and
        # this line's job is provenance.
    )
    return {
        "scenario_id": result["scenario_id"],
        "label_trust_tier": result["label_trust_tier"],
        "labelled_by": result["labelled_by"],
        "counts": counts,
    }
