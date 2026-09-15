"""Eval routes for W Chats M6.

Queries tenant DB (eval_runs, eval_results, eval_scenarios) for eval run history
and per-scenario results. All routes require X-API-Key auth via get_current_tenant.
IDOR prevented by verifying agent.tenant_id == tenant.id.

Routes:
    GET  /agents/{agent_id}/eval-runs                   — list runs with aggregate scores (EVL-06)
    GET  /agents/{agent_id}/eval-runs/{run_id}/results  — per-scenario results (EVL-07)
    POST /agents/{agent_id}/eval-runs/trigger            — dispatch run_eval_suite manually (EVL-04)
    POST /agents/{agent_id}/eval-runs/{run_id}/rejudge   : rescore a finished run's stored samples (#274)

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
        gated verdict. Those last three are the run's own count and add up to
        `scored_scenario_count`, and nothing here re-reaches a verdict.
        `datasets.available` is false exactly when the run has no record.
        `datasets.unattributed` reports nulls, because the record does not carry
        that count and inventing a zero for it would claim a run had no
        unattributable rows when nobody asked.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_credential_kind, get_current_tenant
from app.core.database import get_async_db
from app.core.log_bounds import log_failure
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
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.eval import (
    GoldenDraftRequest,
    GoldenDraftResponse,
    GoldenScenariosRegisterRequest,
    GoldenScenariosRegisterResponse,
)
from app.services.eval_service import EVAL_DATASETS
from app.services.eval_service import GATED_METRIC_KEYS as EVAL_GATED_METRIC_KEYS
from app.services.eval_service import METRIC_KEYS as EVAL_METRIC_KEYS
from app.services.eval_service import REJUDGE_KIND_PREFIX as EVAL_REJUDGE_KIND_PREFIX
from app.services.scenario_service import (
    InvalidScenario,
    insert_authored_golden_scenario,
)
from app.worker.tasks.runtime.eval import run_eval_suite
from app.worker.tasks.runtime.golden_draft import draft_golden_scenarios
from app.worker.tasks.runtime.rejudge import rejudge_eval_run

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
#
# THE KIND FILTER IS LOAD-BEARING SINCE #274. Without it this listed every row in
# `eval_runs`, and a rejudge run is the newest row the moment one is dispatched:
# it carries no `result`, it measured two metrics and no agent turn, and the
# console's newest-first list would show it as the agent's current reading. Every
# other reader of a run was already kind-scoped (`_LATEST_RUN_SQL`, the deploy
# gate's since-readers, the digest); this one was not, and it is the one the
# owner looks at.
_LIST_EVAL_RUNS_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status,
        er.result,
        er.source_run_id
    FROM eval_runs er
    WHERE er.kind = %(kind)s
    ORDER BY er.started_at DESC
    LIMIT 50
"""

# The pre-0030 shape. A tenant behind 0030 has no `source_run_id`, and no rejudge
# has ever run on it, so the None appended to each row is the truthful reading.
_LIST_EVAL_RUNS_PRE_0030_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status,
        er.result
    FROM eval_runs er
    WHERE er.kind = %(kind)s
    ORDER BY er.started_at DESC
    LIMIT 50
"""

# The pre-0022 shape, used only after the queries above raise UndefinedColumn. A
# tenant DB without `eval_runs.result` holds no record for any run on it, so the
# same 50 runs come back and every one of them reports result "absent".
_LIST_EVAL_RUNS_PRE_0022_SQL = """
    SELECT
        er.id,
        er.started_at,
        er.finished_at,
        er.status
    FROM eval_runs er
    WHERE er.kind = %(kind)s
    ORDER BY er.started_at DESC
    LIMIT 50
"""

#: What `?kind=` accepts, and the `eval_runs.kind` prefix each one selects.
#:
#: A NAMED SET RATHER THAN A PASS-THROUGH. The value reaches a SQL parameter, so
#: a free-form string would be a filter nobody validated and a caller could list
#: another agent's runs by naming its kind. Two entries, both scoped to the agent
#: in the path.
EVAL_RUN_KINDS: tuple[str, ...] = ("eval", "rejudge")

_KIND_PREFIX = {"eval": "m6:", "rejudge": EVAL_REJUDGE_KIND_PREFIX}

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
            WHERE source IN ('generated', 'mined', 'authored') OR provenance IS NULL
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
        log_failure(
            log, "list_eval_runs.record_unreadable", exc, level="error",
            run_id=run_id,
            detail="the stored record breaks a rule; the run reads as unmeasured",
        )
        return None


def _eval_run_block(
    run_id, started_at, finished_at, status, record: EvalResult | None,
    source_run_id=None,
) -> dict:
    """One run as the console reads it. Every number comes off *record*.

    The run's own columns (id, the two timestamps, status, and since #274 the
    source run) come off the row because they are the row's; nothing else does.
    A run with no record reports null counts, unmeasured metrics and
    `result: "absent"`. Never a zero, and never a figure recovered from
    `eval_results` behind the record's back.

    `source_run_id` is null on every run of kind `eval`, which is every run that
    measured an agent. It names the run a rejudge rescored, and it is what lets
    a reader of the `?kind=rejudge` listing join a second opinion back to the
    measurement it is about.
    """
    metrics, metrics_dataset = _run_level_metrics(record)
    return {
        "id": str(run_id),
        "source_run_id": None if source_run_id is None else str(source_run_id),
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


#: The three SELECTs, widest first, with the log line naming what each rung
#: loses and how many Nones pad its rows out to the widest shape.
_LIST_RUNS_LADDER: tuple[tuple[str, str, int], ...] = (
    (_LIST_EVAL_RUNS_SQL, "", 0),
    (
        _LIST_EVAL_RUNS_PRE_0030_SQL,
        "list_eval_runs.source_run_id_column_absent",
        1,
    ),
    (_LIST_EVAL_RUNS_PRE_0022_SQL, "list_eval_runs.result_column_absent", 2),
)


async def _fetch_ledger(conn_str: str) -> tuple:
    """OPS-12's ORRERY counts, on the same tenant-DB round-trip pattern.

    In this route so the eval-runs response is the single place the admin UI
    reads eval provenance from. It is a claim about the scenario table rather
    than about any run, so no record holds it.
    """
    rows = await asyncio.to_thread(_query_tenant_db_sync, conn_str, _LEDGER_SQL, {})
    return rows[0] if rows else (0, 0, 0)


def _rendered_runs(rows: list[tuple]) -> list[dict]:
    """Each run as the console reads it. Every number comes off its record."""
    return [
        _eval_run_block(
            run_id,
            started_at,
            finished_at,
            status,
            _record_of(str(run_id), payload),
            source_run_id,
        )
        for run_id, started_at, finished_at, status, payload, source_run_id in rows
    ]


async def _fetch_eval_runs(conn_str: str, kind: str) -> list[tuple]:
    """This agent's 50 most recent runs of one kind, degrading by column.

    A tenant DB that predates migration 0030 has no `source_run_id` and one that
    predates 0022 has no `result`, and each absence raises UndefinedColumn before
    a row returns. Each narrower rung returns the same runs, and the Nones
    appended are the truthful reading: no run on that tenant recorded what it
    measured, and no rejudge has ever run on it.

    Args:
        kind: the full `eval_runs.kind`, already scoped to the agent by the
            caller. It reaches SQL as a parameter, never as interpolated text.

    Returns:
        Six-column rows: (id, started_at, finished_at, status, result,
        source_run_id).
    """
    for statement, event, padding in _LIST_RUNS_LADDER:
        try:
            rows = await asyncio.to_thread(
                _query_tenant_db_sync, conn_str, statement, {"kind": kind}
            )
        except psycopg2.errors.UndefinedColumn:
            continue
        if event:
            log.info(event)
        return [(*row, *([None] * padding)) for row in rows]
    raise psycopg2.errors.UndefinedColumn(
        "eval_runs is missing a column every rung of the read ladder needs"
    )


@router.get("/agents/{agent_id}/eval-runs")
async def list_eval_runs(
    agent_id: UUID,
    kind: str = "eval",
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return up to 50 eval runs of one kind for an agent, with their records.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id (IDOR prevention).
        Returns 404 for unknown agents or agents belonging to a different tenant.

    Args:
        kind: `eval` (the default) lists the runs that measured this agent, which
            is what the console and every polling caller want. `rejudge` lists
            the runs that rescored a finished run's stored answers (#274); each
            carries `source_run_id` and no agent turn of its own. Anything else
            is a 400, because the value reaches a SQL parameter and a
            pass-through would be a filter nobody validated.

    Response shape:
        {"eval_runs": [{id, source_run_id, started_at, finished_at, status,
                        result, scenario_count, valid_scenario_count,
                        scored_scenario_count, metrics, metrics_dataset,
                        aggregate_scores, datasets}],
         "kind": "eval" | "rejudge",
         "ledger": {...}}

    Every figure per run is the record's, read off `eval_runs.result`. See the
    module docstring for what each field says when the record is absent and why
    `metrics_dataset` can be null on a run that measured plenty.
    """
    if kind not in EVAL_RUN_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind must be one of {list(EVAL_RUN_KINDS)}",
        )
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
    rows = await _fetch_eval_runs(conn_str, f"{_KIND_PREFIX[kind]}{agent_id}")

    born_in_production_count, red_team_count, authored_count = await _fetch_ledger(conn_str)
    eval_runs = _rendered_runs(rows)

    log.info(
        "list_eval_runs.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        kind=kind,
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
        "kind": kind,
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


@router.post("/agents/{agent_id}/eval-runs/{run_id}/rejudge", status_code=202)
async def rejudge_eval_run_route(
    agent_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Rescore a finished run's stored answers with today's Judges (#274).

    No agent turn runs. The run's `eval_samples` rows hold the answers the agent
    gave, the two gated metrics are scored over those into a NEW run naming this
    one in `source_run_id`, and this run's rows are never written.

    Security. Agent ownership verified, 404 on a mismatch, matching every other
    route here. THE RUN ID IS NOT CHECKED and does not need to be: the only
    database the task opens is the one this agent's encrypted dsn names, so a run
    id belonging to another tenant finds no rows and the task reports
    `no_samples`. The agent check is the tenant boundary. No state guard either:
    `trigger_eval_run` refuses an agent that is not `ready` because it is about
    to drive that agent, and this drives nothing.

    Returns HTTP 202 with the dispatched task id. The run id the task writes is
    on the task's result and in `GET /eval-runs`.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    task = rejudge_eval_run.apply_async(
        kwargs={"agent_id": str(agent_id), "source_run_id": str(run_id)},
        queue="runtime",
    )

    log.info(
        "eval_rejudge.dispatched",
        agent_id=str(agent_id),
        source_run_id=str(run_id),
        task_id=task.id,
        tenant_id=str(tenant.id),
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "agent_id": str(agent_id),
        "source_run_id": str(run_id),
    }


# ---------------------------------------------------------------------------
# #56: golden registration, the one write on this router
# ---------------------------------------------------------------------------

_GOLDEN_EXISTING_SQL = (
    "SELECT question, COALESCE(turns, '[]'::jsonb) FROM eval_scenarios "
    "WHERE dataset = 'golden'"
)


def _golden_key(question: str, turns: list[dict] | None) -> tuple[str, str]:
    """What makes two golden pairs the same pair.

    THE QUESTION ALONE STOPPED BEING THE KEY AT #227. "How do I start the dev
    server?" is one golden pair per conversation that binds it, and keying on the
    text alone silently skipped the second and every one after it, leaving the
    owner with a golden set smaller than the file they registered and no row
    saying which pairs went missing.

    The turns are keyed on their JSON with sorted keys so two orderings of the
    same turn compare equal, and an unparseable stored value keys as its own raw
    text rather than collapsing into the empty conversation.
    """
    if isinstance(turns, str):
        try:
            turns = json.loads(turns)
        except (TypeError, ValueError):
            return question, turns
    return question, json.dumps(turns or [], sort_keys=True)


def _register_golden_sync(
    conn_str: str, pairs: list[tuple[str, str, list[dict], bool]], provenance: str
) -> tuple[int, list[str], int]:
    """Insert authored golden pairs in one transaction, skipping known pairs.

    A pair already in the golden set is skipped rather than duplicated, so one
    caller re-running the same file is idempotent. Sameness is `_golden_key`, the
    question AND the conversation it was asked in. No unique constraint backs the
    check, so concurrent registrations of one file can still race duplicates in;
    the surface is a single operator. Returns (registered, skipped_questions,
    golden_total), the total counting distinct pairs. Any failure rolls the whole
    batch back; a file half-registered would leave the golden floor unaccountable.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_GOLDEN_EXISTING_SQL)
            existing = {_golden_key(q, t) for q, t in cur.fetchall()}
        registered = 0
        skipped: list[str] = []
        for question, reference_answer, turns, ambiguous in pairs:
            key = _golden_key(question, turns)
            if key in existing:
                skipped.append(question)
                continue
            insert_authored_golden_scenario(
                conn,
                question=question,
                reference_answer=reference_answer,
                provenance=provenance,
                turns=turns,
                ambiguous=ambiguous,
            )
            existing.add(key)
            registered += 1
        conn.commit()
        return registered, skipped, len(existing)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _golden_refusal(exc: Exception) -> HTTPException:
    """422 for a pair the writer refused; 409 for a pre-0024 tenant DB (#64's
    class: nothing re-runs tenant migrations after provision)."""
    if isinstance(exc, InvalidScenario):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(
        status_code=409,
        detail=(
            "The tenant database predates migration 0024 and refuses "
            "authored rows. Re-run tenant migrations for this agent (#64)."
        ),
    )


@router.post(
    "/agents/{agent_id}/golden-scenarios",
    status_code=201,
    response_model=GoldenScenariosRegisterResponse,
)
async def register_golden_scenarios(
    agent_id: UUID,
    body: GoldenScenariosRegisterRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
    credential_kind: str = Depends(get_credential_kind),
) -> GoldenScenariosRegisterResponse:
    """Register owner-authored golden pairs for an agent (#56).

    The golden set is the tenant's own acceptance contract: `decide()` gates on
    it absolutely and refuses to ship under ten attempted pairs, so this is a
    mandatory Provisioning step before the first deploy.

    The rows land with `source='authored'`, `dataset='golden'` and a provenance
    tag derived from the authenticated caller. `label_trust_tier` stays NULL:
    this route authenticates an account (tenant key or Clerk session), not a
    person, so it does not assert which human wrote the text. No authored_by
    field is accepted for the same reason.

    Security:
        IDOR check on agent (404 on foreign or missing agent).
        Refusals map through _golden_refusal: 422 for an empty pair or an
        ambiguous pair whose reference is not a clarifying question (golden
        rows gate deploys, so one is never stored), 409 for a pre-0024 DB.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    conn_str = fernet_decrypt(agent.neon_connection_string)
    # The colon separates provenance fields, so the caller's segment loses its.
    source_tag = (body.source_file or "inline").replace(":", "_")
    provenance = f"authored:{credential_kind}:{source_tag}"

    try:
        registered, skipped, total = await asyncio.to_thread(
            _register_golden_sync,
            conn_str,
            [(p.question, p.reference_answer, [t.model_dump() for t in p.turns], p.ambiguous) for p in body.pairs],
            provenance,
        )
    except (InvalidScenario, psycopg2.errors.CheckViolation) as exc:
        raise _golden_refusal(exc) from exc

    log.info(
        "golden.registered",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        registered=registered,
        skipped=len(skipped),
        golden_total=total,
    )
    return GoldenScenariosRegisterResponse(
        registered=registered,
        skipped_duplicates=skipped,
        golden_total=total,
    )


# ---------------------------------------------------------------------------
# #203: golden drafts, a read of the corpus that writes no scenario row
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{agent_id}/golden-scenarios/drafts",
    status_code=202,
    response_model=GoldenDraftResponse,
)
async def draft_golden_scenarios_route(
    agent_id: UUID,
    body: GoldenDraftRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> GoldenDraftResponse:
    """Draft golden pairs from the agent's corpus for the owner to label (#203).

    Creates a `golden_draft` job and dispatches the runtime task with ids only.
    The drafts come back as `golden_draft.pair` events on the job; nothing is
    written to eval_scenarios. A kept draft is registered through the golden
    registration route above, which stays the single writer of golden rows.

    Security:
        IDOR check on agent (404 on foreign or missing agent). 404 when the
        agent has no tenant database yet.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    job = Job(tenant_id=tenant.id, agent_id=agent.id, kind="golden_draft", status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    draft_golden_scenarios.apply_async(
        kwargs={"job_id": str(job.id), "agent_id": str(agent_id), "n": body.n},
        queue="runtime",
    )
    log.info(
        "golden_draft.dispatched",
        agent_id=str(agent_id), tenant_id=str(tenant.id), job_id=str(job.id), n=body.n,
    )
    return GoldenDraftResponse(
        status="queued", job_id=str(job.id), agent_id=str(agent_id), n=body.n
    )
