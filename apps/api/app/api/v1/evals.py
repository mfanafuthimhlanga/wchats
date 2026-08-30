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

These routes compute nothing (#51 slice 3)
------------------------------------------
Both routes used to derive a run's figures a second time, in SQL, over
`eval_results`. `list_eval_runs` ran COUNT(DISTINCT scenario_id) and four AVGs;
a companion query re-split the same rows into golden and exploratory; and
`get_eval_run_results` re-reached each scenario's verdict by comparing scores to
today's settings. The run itself had already computed every one of those figures
and written them down, so the console and the task were two arithmetics over one
run, free to disagree.

#26 is what that disagreement looked like from the console: the route reported
18 scenarios while the task reported 20 attempted. Neither was wrong about its
own question. COUNT(DISTINCT scenario_id) over `eval_results` counts the rows a
judge SCORED; the task counted the rows the selector FETCHED; and nothing in the
response said which question its number answered.

Now every number here is lifted from one place:

    eval_runs.result: the EvalResult the run wrote at the end of
        `run_eval_suite` (migration 0022, `eval_service.write_eval_result`).
        `scenario_count` is its `attempted`, `valid_scenario_count` its `valid`,
        `scored_scenario_count` its `scored`, and every metric is a stored
        `Measurement` copied through verbatim.
    eval_results rows: read as stored, never aggregated. Each row carries the
        `binary_verdict` the judge reached and the `threshold` it was reached
        against (migration 0023), so a later change to the gate cannot restate a
        verdict already written down.

What the response says when a number does not exist:

    result: "present" or "absent". Absent means the run has no record, which
        covers a tenant DB predating migration 0022, a run that died before the
        write, and a stored payload that broke a construction rule on the way
        out. All three report null counts and unmeasured metrics. Zero is never
        used for any of them.
    metrics / metrics_dataset: per metric, {value, measured, observations},
        and the name of the dataset they were lifted from. THE RECORD HOLDS NO
        RUN-LEVEL MEAN, on purpose: a golden mean and an exploratory mean answer
        different questions and one number over both moves whenever the
        exploratory draw moves. So a run-level reading exists only when exactly
        one dataset scored anything. When both did, `metrics_dataset` is null,
        the four metrics read unmeasured, and the numbers are under `datasets`,
        where the record keeps them apart.
    aggregate_scores / scores: NUMERIC COMPATIBILITY PROJECTION. Unmeasured
        reads 0.0 here, and it is a lie, retained for exactly one reason:
        apps/admin types these fields `number` and calls `.toFixed(2)` on them
        (agents/[id]/eval/page.tsx:291) and plots them (`:184`), so a null would
        throw on the eval page. DO NOT read these for quality; read `metrics`.
    passed: tri-state, and no longer computed here. It is the conjunction of
        the `binary_verdict` values stored on the scenario's gated rows. None
        when any of them is NULL, because unknown is neither a pass nor a fail.
        A client that cannot read null degrades to "not passed", which fails
        closed.
    datasets: the record's per-dataset outcomes. The golden half is fixed, runs
        in full every night and is comparable across runs; the exploratory half
        rotates. Each carries its own three counts, its own four measurements,
        and how its scored scenarios ended: passed, failed, or undecided by a
        gated verdict. Those three are the run's own count and add up to
        `scored_scenario_count`; nothing here re-reaches a verdict. `datasets.available` is false exactly when the run has no
        record. `datasets.unattributed` reports nulls, because the record does
        not carry that count and inventing a zero for it would claim a run had
        no unattributable rows when nobody asked.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.domain.eval_result import (
    DatasetOutcome,
    EvalResult,
    InvalidEvalResult,
    metrics_of,
    run_level_metrics,
    unmeasured_metrics,
)
from app.domain.judge_record import scenario_verdict
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.eval_service import EVAL_DATASETS
from app.services.eval_service import GATED_METRIC_KEYS as EVAL_GATED_METRIC_KEYS
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

# The run's own columns and the record it wrote. No aggregate, no join: the
# arithmetic that used to sit here is the arithmetic `run_eval_suite` had already
# done, and #26 is what running it twice looked like from the console.
_LIST_EVAL_RUNS_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status,
        er.result
    FROM eval_runs er
    ORDER BY er.started_at DESC
    LIMIT 50
"""

# The pre-0022 shape, used only after the query above raises UndefinedColumn. A
# tenant DB without `eval_runs.result` holds no record for any run on it, so the
# same 50 runs come back and every one of them reports result "absent".
_LIST_EVAL_RUNS_PRE_0022_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status
    FROM eval_runs er
    ORDER BY er.started_at DESC
    LIMIT 50
"""

# The four M6 metrics, in the order the UI channels read them (D-04). Imported
# from eval_service rather than restated: audit D3 was one call site's copy of a
# column name drifting from the schema's, and four metric names duplicated
# across the writer, the scorer and this reader is the same shape of defect
# waiting to happen.
METRIC_KEYS = EVAL_METRIC_KEYS

# The two metrics the promotion gate is defined over (D-21 LOCKED). Imported
# beside `threshold_for`, which returns a number for exactly these two, rather
# than restated: `deployment_service` counts verdicts over the same pair, and a
# console that gates on faithfulness while a deploy gate reads three metrics is
# the same shape of defect as audit D3's copied column name.
GATED_METRIC_KEYS = EVAL_GATED_METRIC_KEYS


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
# EVAL_DATASETS: it is the count of result rows a run cannot attribute to any
# scenario, kept out of both datasets so they cannot be averaged into a
# measurement. Same name as summarise_run_validity's `unattributed`, which is
# where the count is still computed. `EvalResult` does not carry it, so this
# route reports nulls rather than a zero nobody counted.
DATASET_UNATTRIBUTED = "unattributed"

#: What the run reports when it has a record, and when it has none.
RESULT_PRESENT = "present"
RESULT_ABSENT = "absent"

#: A dataset the record does not report. Counts are null, not zero: "this run
#: covered no golden rows" and "this response cannot say" are different claims
#: and a zero asserts the first about a question nobody asked.
_UNREPORTED_DATASET = {
    "scenario_count": None,
    "valid_scenario_count": None,
    "scored_scenario_count": None,
    "scenarios_passed": None,
    "scenarios_failed": None,
    "scenarios_unmeasured": None,
}


def _rendered(metrics: dict) -> dict:
    """Four Measurements as JSON, {value, measured, observations} each.

    The observation count travels with every number, so a reader can see that a
    0.91 came off four rows. `app.domain.eval_result` decides which Measurement
    belongs to which key; this function only turns it into a response body.
    """
    return {metric: reading.payload for metric, reading in metrics.items()}


def _unmeasured_metrics() -> dict:
    """Four metrics, none of them read."""
    return _rendered(unmeasured_metrics())


def _metrics_of(outcome: DatasetOutcome) -> dict:
    """One dataset's four metrics, copied out of the record unchanged."""
    return _rendered(metrics_of(outcome))


def _dataset_block(record: EvalResult | None) -> dict:
    """The record's per-dataset outcomes, with every dataset key always present.

    `available` is true exactly when the run has a record. A missing key would
    have to be interpreted, and the two available readings, "this run covered
    no golden rows" and "this response does not carry that information", are
    exactly the pair the flag exists to separate.

    `unattributed` carries no numbers. `summarise_run_validity` counts those
    rows and `EvalResult` stores no such field, so this route has nothing to
    read; computing one here from `eval_results` is the second derivation the
    slice exists to remove.
    """
    outcomes = record.datasets if record is not None else {}
    return {
        "available": record is not None,
        **{
            name: (
                {
                    "scenario_count": outcomes[name].attempted,
                    "valid_scenario_count": outcomes[name].valid,
                    "scored_scenario_count": outcomes[name].scored,
                    # How the scored scenarios ended, as the run counted them.
                    # The three add up to scored_scenario_count.
                    "scenarios_passed": outcomes[name].scenarios_passed,
                    "scenarios_failed": outcomes[name].scenarios_failed,
                    "scenarios_unmeasured": outcomes[name].scenarios_unmeasured,
                    "metrics": _metrics_of(outcomes[name]),
                }
                if name in outcomes
                else {**_UNREPORTED_DATASET, "metrics": _unmeasured_metrics()}
            )
            for name in EVAL_DATASETS
        },
        DATASET_UNATTRIBUTED: {
            "scenario_count": None,
            "scored_scenario_count": None,
        },
    }


def _run_level_metrics(record: EvalResult | None) -> tuple[dict, str | None]:
    """The run's four metrics as JSON, and the dataset they were lifted from.

    `app.domain.eval_result.run_level_metrics` is the rule and carries the
    reasoning: a run-level reading exists only when exactly one dataset scored a
    row, because there is no pooled mean to fall back on. The deploy gate asks
    the same function, so the console and the gate cannot name different
    datasets.
    """
    metrics, dataset = run_level_metrics(record)
    return _rendered(metrics), dataset


def _record_of(run_id: str, payload) -> EvalResult | None:
    """One run's stored record, or None when it has none that can be read.

    `EvalResult.from_payload` decides whether the payload is readable and this
    logs which run lost its numbers. The response then reports that ONE run as
    recordless: an unreadable row costs its own run and no other, which is what
    keeps a console listing fifty runs from going dark over one of them.
    """
    if payload is None:
        return None
    try:
        return EvalResult.from_payload(payload)
    except InvalidEvalResult as exc:
        log.error(
            "list_eval_runs.record_unreadable",
            run_id=run_id,
            error=str(exc),
            detail="the stored record breaks a rule; the run reads as unmeasured",
        )
        return None


def _eval_run_block(
    run_id, started_at, finished_at, status, record: EvalResult | None
) -> dict:
    """One run as the console reads it. Every number comes off *record*.

    The run's own columns (id, the two timestamps, status) come off the row
    because they are the row's; nothing else does. A run with no record reports
    null counts, unmeasured metrics and `result: "absent"`. Never a zero, and
    never a figure recovered from `eval_results` behind the record's back.
    """
    metrics, metrics_dataset = _run_level_metrics(record)
    return {
        "id": str(run_id),
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "status": status,
        "result": RESULT_ABSENT if record is None else RESULT_PRESENT,
        # attempted, the valid denominator, and what actually scored. #26 was
        # these three collapsed into two by a COUNT that answered a different
        # question from the one the task had already answered.
        "scenario_count": None if record is None else record.attempted,
        "valid_scenario_count": None if record is None else record.valid,
        "scored_scenario_count": None if record is None else record.scored,
        # The honest reading. value is null exactly when measured is false.
        "metrics": metrics,
        "metrics_dataset": metrics_dataset,
        # Numeric compatibility projection, see the module docstring.
        # An unmeasured metric reads 0.0 here and that is not a score.
        "aggregate_scores": {
            metric: reading["value"] if reading["measured"] else 0.0
            for metric, reading in metrics.items()
        },
        # The two measurements, kept apart. Never add them together: the golden
        # set is fixed and paired across runs, the exploratory sample rotates,
        # and one mean over both moves whenever the draw moves while looking
        # like a quality change.
        "datasets": _dataset_block(record),
    }


async def _fetch_eval_runs(conn_str: str) -> list[tuple]:
    """The 50 most recent runs with their records, degrading to a pre-0022 tenant.

    A tenant DB that predates migration 0022 has no `eval_runs.result` column at
    all, so the wide SELECT raises UndefinedColumn before it returns a row. The
    narrow one returns the same runs, and the None appended to each is the
    truthful reading: no run on that tenant recorded what it measured.

    Returns:
        Five-column rows: (id, started_at, finished_at, status, result).
    """
    try:
        return await asyncio.to_thread(
            _query_tenant_db_sync, conn_str, _LIST_EVAL_RUNS_SQL, {}
        )
    except psycopg2.errors.UndefinedColumn:
        log.info("list_eval_runs.result_column_absent")
        rows = await asyncio.to_thread(
            _query_tenant_db_sync, conn_str, _LIST_EVAL_RUNS_PRE_0022_SQL, {}
        )
        return [(*row, None) for row in rows]


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
        {"eval_runs": [{id, started_at, finished_at, status, result,
                        scenario_count, valid_scenario_count,
                        scored_scenario_count, metrics, metrics_dataset,
                        aggregate_scores, datasets}],
         "ledger": {...}}

    Every figure per run is the record's, read off `eval_runs.result`. See the
    module docstring for what each field says when the record is absent and why
    `metrics_dataset` can be null on a run that measured plenty.
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

    # 5. Query tenant DB in a thread pool to avoid blocking the event loop.
    #    One round trip for the runs and their records; the golden/exploratory
    #    breakdown used to cost a second one and now comes out of the record.
    rows = await _fetch_eval_runs(conn_str)

    # 5b. OPS-12: ORRERY ledger, on the same tenant-DB round-trip pattern, in
    # this same route so the eval-runs response is the single place the admin UI
    # reads eval provenance from. It is a claim about the scenario table rather
    # than about any run, so no record holds it.
    ledger_rows = await asyncio.to_thread(
        _query_tenant_db_sync, conn_str, _LEDGER_SQL, {}
    )
    born_in_production_count, red_team_count, authored_count = (
        ledger_rows[0] if ledger_rows else (0, 0, 0)
    )

    # 6. Render each run from its record.
    eval_runs = [
        _eval_run_block(
            run_id,
            started_at,
            finished_at,
            status,
            _record_of(str(run_id), payload),
        )
        for run_id, started_at, finished_at, status, payload in rows
    ]

    log.info(
        "list_eval_runs.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        run_count=len(eval_runs),
        # A run with no record has no numbers at all, and that is visible here
        # without anyone opening the response body.
        recordless_run_count=sum(
            1 for run in eval_runs if run["result"] == RESULT_ABSENT
        ),
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

# The judge rows as stored. `binary_verdict` is the decision the judge reached at
# scoring time and `threshold` is the gate it was reached against (migration
# 0023), both stamped by `write_eval_results`. Reading them instead of comparing
# `score` to today's settings is what stops a later change to the gate restating
# a verdict already written down.
_GET_RUN_RESULTS_SQL = """
    SELECT
        res.scenario_id,
        es.question,
        es.source,
        res.metric,
        res.score,
        res.binary_verdict,
        res.threshold
    FROM eval_results res
    LEFT JOIN eval_scenarios es ON es.id::text = res.scenario_id
    WHERE res.eval_run_id = %(run_id)s
    ORDER BY res.scenario_id, res.metric
"""

# The pre-0023 shape, used only after the query above raises UndefinedColumn. A
# row written before the verdict had a column of its own carries no verdict, so
# every scenario on such a run reads `passed: null`, which is unknown and what a
# run whose decisions were never recorded actually is.
_GET_RUN_RESULTS_PRE_0023_SQL = """
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

#: A metric with no row on this run, or a row from before 0023. No score, no
#: verdict, no gate.
_NO_JUDGE_ROW = {"score": None, "measured": False, "verdict": None, "threshold": None}


def _judge_reading(score, verdict, threshold) -> dict:
    """One stored judge row, rendered without deciding anything.

    `verdict` is None for an ungated metric (`threshold_for` gives
    `context_precision` and `context_recall` no gate, so their rows carry none)
    and None for a gated metric the judge produced no score for. Both mean the
    same thing to a reader of `passed`: there is no decision here.
    """
    return {
        "score": float(score) if score is not None else None,
        "measured": score is not None,
        "verdict": None if verdict is None else bool(verdict),
        "threshold": float(threshold) if threshold is not None else None,
    }


async def _fetch_run_results(conn_str: str, run_id: str) -> list[tuple]:
    """One run's judge rows, degrading to a tenant DB that predates 0023.

    Returns:
        Seven-column rows: (scenario_id, question, source, metric, score,
        binary_verdict, threshold).
    """
    params = {"run_id": run_id}
    try:
        return await asyncio.to_thread(
            _query_tenant_db_sync, conn_str, _GET_RUN_RESULTS_SQL, params
        )
    except psycopg2.errors.UndefinedColumn:
        log.info("get_eval_run_results.verdict_columns_absent", run_id=run_id)
        rows = await asyncio.to_thread(
            _query_tenant_db_sync, conn_str, _GET_RUN_RESULTS_PRE_0023_SQL, params
        )
        return [(*row, None, None) for row in rows]


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

    passed is the conjunction of the `binary_verdict` values stored on the
    scenario's two gated rows (D-21 LOCKED). The route reaches no verdict of its
    own: it used to re-compare every score to whatever the thresholds were at
    request time, so a deployment that moved the gate silently restated the
    verdicts of every run already scored against the old one.

    None when either gated verdict is NULL, which covers a judge outage, a
    metric with no row, and a run written before migration 0023 gave the verdict
    a column. That third state is the point: rendering an absent decision as
    passed=false reports a total quality collapse for a run that decided
    nothing.

    There is no run-level verdict here and this route never reported one. The
    function that computes one is ticket 17's `decide()`.
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
    rows = await _fetch_run_results(conn_str, str(run_id))

    # 4. Group rows by scenario_id, each row read exactly as it was written:
    #    (scenario_id, question, source, metric, score, binary_verdict,
    #    threshold). A metric with no row at all reads the same as a row the
    #    judge scored nothing for, which is what both of them are. No reading.
    scenarios: dict[str, dict] = {}
    for scenario_id, question, source, metric, score, verdict, threshold in rows:
        sid = str(scenario_id)
        if sid not in scenarios:
            scenarios[sid] = {
                "scenario_id": sid,
                "question": question or "",
                "source": source or "generated",
                "metrics": {key: dict(_NO_JUDGE_ROW) for key in METRIC_KEYS},
            }
        if metric in scenarios[sid]["metrics"]:
            scenarios[sid]["metrics"][metric] = _judge_reading(
                score, verdict, threshold
            )

    # 5. `scenario_verdict` is the conjunction of the stored verdicts over the
    #    two GATED metrics (D-21), and it is the rule the run counted its own
    #    scenarios by, so this screen and the deploy gate describe one scenario
    #    one way. A NULL verdict on either makes it None rather than False,
    #    because "nobody decided" rendered as "it failed" turns a judge outage
    #    into an apparent collapse and an owner-initiated rollback.
    results = [
        {
            **scen,
            # Numeric compatibility projection: unmeasured reads 0.0.
            "scores": {
                key: reading["score"] if reading["measured"] else 0.0
                for key, reading in scen["metrics"].items()
            },
            "passed": scenario_verdict(
                [scen["metrics"][k]["verdict"] for k in GATED_METRIC_KEYS]
            ),
        }
        for scen in scenarios.values()
    ]

    log.info(
        "get_eval_run_results.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        tenant_id=str(tenant.id),
        scenario_count=len(results),
        # The denominator, in the log too: a run whose scenarios all carry no
        # verdict is a run that decided nothing, and it should be visible here
        # without anyone opening the response body.
        unverdicted_scenario_count=sum(1 for r in results if r["passed"] is None),
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
