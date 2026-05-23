"""Ragas 0.4.x eval harness for Veridian M6.

Measures Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall per scenario.
Runs against a Neon branch connection string (never production — D-10).
Promotes passing scenarios to verified_qa (D-21/D-22/D-23).

Design notes:
- All Ragas imports use the 0.4.x path (ragas.metrics.collections) — CLAUDE.md constraint.
- Dataset field is 'reference' (renamed from the old 0.3.x name in 0.4.x).
- LLM wrapper uses InstructorLLM(instructor.from_anthropic(Anthropic())) — 0.4.x collections requirement.
- All DB writes use psycopg2 try/finally/close pattern matching retrieval_service.py.
- verified_qa promotion writes source='sandbox_test', promoted_by='system' (D-22).
- question_vector populated via Voyage embed at promotion time (D-23).
"""

from __future__ import annotations

import json
import uuid

import anthropic
import instructor
import psycopg2
import structlog

# ---------------------------------------------------------------------------
# Ragas 0.4.x imports — D-01 LOCKED: exact import path
# Do NOT use the 0.3.x ragas.metrics path — it has been removed.
# ---------------------------------------------------------------------------
from ragas.metrics.collections import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
)
from ragas import EvaluationDataset, evaluate
from ragas.llms import InstructorLLM

from app.core.config import settings
from app.services.embedding_service import _get_vo

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HAIKU_MODEL = "claude-haiku-4-5"

# Note: Ragas 0.4.x collections metrics require an InstructorLLM at construction time.
# Metrics are therefore instantiated inside run_ragas_eval(), not at module level.
# The four metrics used are: Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall (D-04)


# ---------------------------------------------------------------------------
# Task 1: Ragas evaluation harness
# ---------------------------------------------------------------------------

def run_ragas_eval(scenarios: list[dict], branch_conn_str: str) -> dict:  # noqa: ARG001
    """Run Ragas 0.4.x evaluation over a list of eval scenarios.

    Builds an EvaluationDataset from the scenarios, calls evaluate() with
    the four M6 metrics, and returns per-scenario scores plus per-metric means.

    D-02 LOCKED: Dataset field name is 'reference' (field was renamed in Ragas 0.4.x).
    The branch_conn_str parameter is accepted but not used by the Ragas harness
    itself — it is used by write_eval_results() and promote_to_verified_qa().

    Args:
        scenarios: List of scenario dicts. Each must contain:
            - question (str): The user question.
            - reference_answer (str): Ground-truth answer (D-02).
            - agent_response (str, optional): The agent's generated answer.
            - retrieved_contexts (list[str], optional): Retrieved chunk contents.
        branch_conn_str: Neon branch connection string for this eval run
            (passed through for reference; eval writes happen via branch).

    Returns:
        Dict with two keys:
            "scores": list[dict] — per-scenario scores, one dict per input row.
                Each dict: {scenario_id, faithfulness, answer_relevancy,
                            context_precision, context_recall}
            "means": dict — per-metric mean across all scored scenarios.
    """
    # Filter to only scenarios that have a reference_answer (required by Ragas)
    # D-02 LOCKED: field name is 'reference' (renamed in Ragas 0.4.x)
    samples = [
        {
            "user_input": s["question"],
            "response": s.get("agent_response", ""),
            "retrieved_contexts": s.get("retrieved_contexts", []),
            "reference": s["reference_answer"],   # D-02 LOCKED
        }
        for s in scenarios
        if s.get("reference_answer")
    ]

    # Keep only the scenarios that produced samples (same order)
    valid_scenarios = [s for s in scenarios if s.get("reference_answer")]

    if not samples:
        log.warning("run_ragas_eval.no_valid_scenarios")
        return {
            "scores": [],
            "means": {
                "faithfulness": None,
                "answer_relevancy": None,
                "context_precision": None,
                "context_recall": None,
            },
        }

    log.info("run_ragas_eval.start", scenario_count=len(samples))

    dataset = EvaluationDataset.from_list(samples)

    # Ragas 0.4.x requires InstructorLLM (InstructorBaseRagasLLM) for collections metrics.
    # Build the LLM wrapper at call time (not module level) — metrics are instantiated here.
    _anthropic_client = instructor.from_anthropic(anthropic.Anthropic())
    llm = InstructorLLM(client=_anthropic_client, model=HAIKU_MODEL, provider="anthropic")
    metrics = [
        Faithfulness(llm=llm),
        AnswerRelevancy(llm=llm),
        ContextPrecision(llm=llm),
        ContextRecall(llm=llm),
    ]

    results = evaluate(dataset=dataset, metrics=metrics, llm=llm)

    df = results.to_pandas()

    # Build per-scenario score dicts
    metric_columns = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]

    score_rows = []
    for idx, row in df.iterrows():
        scenario = valid_scenarios[idx] if idx < len(valid_scenarios) else {}
        score_row = {
            "scenario_id": str(scenario.get("id", str(uuid.uuid4()))),
        }
        for col in metric_columns:
            raw = row.get(col)
            score_row[col] = float(raw) if raw is not None and raw == raw else None  # NaN check
        score_rows.append(score_row)

    # Per-metric means
    means = {}
    for col in metric_columns:
        if col in df.columns:
            series = df[col].dropna()
            means[col] = float(series.mean()) if len(series) > 0 else None
        else:
            means[col] = None

    log.info(
        "run_ragas_eval.complete",
        scenario_count=len(samples),
        faithfulness_mean=means.get("faithfulness"),
        answer_relevancy_mean=means.get("answer_relevancy"),
    )

    return {"scores": score_rows, "means": means}


# ---------------------------------------------------------------------------
# Task 1 continued: write eval results to tenant DB
# ---------------------------------------------------------------------------

def write_eval_results(
    eval_run_id: str,
    scenario_scores: list[dict],
    branch_conn_str: str,
) -> None:
    """Insert per-scenario, per-metric rows into eval_results on the tenant DB branch.

    The eval_results table exists from migration 0001:
      id UUID, eval_run_id UUID, scenario_id TEXT, metric TEXT, score NUMERIC, detail JSONB

    One row is inserted per (scenario, metric) pair — four rows per scenario for
    Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall.

    Uses psycopg2 try/finally/close pattern matching retrieval_service.py (D-11).

    Args:
        eval_run_id: UUID string of the eval_runs row.
        scenario_scores: List of per-scenario score dicts from run_ragas_eval().
        branch_conn_str: Neon branch connection string (never production — D-10).
    """
    if not scenario_scores:
        log.info("write_eval_results.no_scores")
        return

    sql = """
        INSERT INTO eval_results (id, eval_run_id, scenario_id, metric, score, detail)
        VALUES (%(id)s::uuid, %(eval_run_id)s::uuid, %(scenario_id)s, %(metric)s, %(score)s, %(detail)s::jsonb)
    """

    metric_columns = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]

    conn = psycopg2.connect(branch_conn_str)
    try:
        with conn.cursor() as cur:
            for score in scenario_scores:
                for metric in metric_columns:
                    cur.execute(sql, {
                        "id": str(uuid.uuid4()),
                        "eval_run_id": eval_run_id,
                        "scenario_id": str(score["scenario_id"]),
                        "metric": metric,
                        "score": score.get(metric),
                        "detail": json.dumps(score),
                    })
        conn.commit()
    finally:
        conn.close()

    log.info(
        "write_eval_results.complete",
        eval_run_id=eval_run_id,
        rows_written=len(scenario_scores) * len(metric_columns),
    )


def update_eval_run_status(
    eval_run_id: str,
    status: str,
    finished_at: bool,
    branch_conn_str: str,
) -> None:
    """Update the status (and optionally finished_at) on an eval_runs row.

    The eval_runs table exists from migration 0001:
      id UUID, kind TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, status TEXT

    Args:
        eval_run_id: UUID string of the eval_runs row.
        status: New status value (e.g. 'running', 'complete', 'failed').
        finished_at: When True, sets finished_at = NOW().
        branch_conn_str: Neon branch connection string.
    """
    if finished_at:
        sql = """
            UPDATE eval_runs
            SET status = %(status)s, finished_at = NOW()
            WHERE id = %(id)s::uuid
        """
    else:
        sql = """
            UPDATE eval_runs
            SET status = %(status)s
            WHERE id = %(id)s::uuid
        """

    conn = psycopg2.connect(branch_conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {"status": status, "id": eval_run_id})
        conn.commit()
    finally:
        conn.close()

    log.info("update_eval_run_status.complete", eval_run_id=eval_run_id, status=status)


# ---------------------------------------------------------------------------
# Task 2: verified_qa promotion helper
# ---------------------------------------------------------------------------

def promote_to_verified_qa(
    scenarios: list[dict],
    scenario_scores: list[dict],
    branch_conn_str: str,
) -> int:
    """Promote passing scenarios to the verified_qa table on the tenant DB branch.

    Promotion criteria (D-21 LOCKED):
        faithfulness >= EVAL_FAITHFULNESS_THRESHOLD
        AND answer_relevancy >= EVAL_RELEVANCY_THRESHOLD
        (both default 0.90 from settings)

    Promoted rows written with (D-22 LOCKED):
        source = 'sandbox_test'
        promoted_by = 'system'

    question_vector populated via Voyage embed at promotion time (D-23 LOCKED).

    Uses psycopg2 try/finally/close pattern. ON CONFLICT DO NOTHING ensures
    idempotent promotion on Celery retry (acks_late + idempotency rule).

    Args:
        scenarios: Original scenario dicts (same list passed to run_ragas_eval).
        scenario_scores: Per-scenario score dicts from run_ragas_eval() "scores" list.
        branch_conn_str: Neon branch connection string (never stored — D-18).

    Returns:
        Count of rows successfully promoted.
    """
    # Build a lookup from scenario_id → scenario dict for O(1) access
    scenario_by_id = {str(s.get("id", "")): s for s in scenarios}

    sql = """
        INSERT INTO verified_qa (
            id, question, question_vector, answer, citations,
            source, faithfulness, relevance, promoted_at, promoted_by, use_count
        )
        VALUES (
            gen_random_uuid(),
            %(question)s,
            %(question_vector)s::vector,
            %(answer)s,
            %(citations)s::jsonb,
            'sandbox_test',
            %(faithfulness)s,
            %(relevance)s,
            NOW(),
            'system',
            0
        )
        ON CONFLICT DO NOTHING
    """

    promoted_count = 0
    conn = psycopg2.connect(branch_conn_str)
    try:
        with conn.cursor() as cur:
            for score in scenario_scores:
                faithfulness = score.get("faithfulness")
                answer_relevancy = score.get("answer_relevancy")

                # D-21 LOCKED: both thresholds must be met
                if (
                    faithfulness is None
                    or answer_relevancy is None
                    or faithfulness < settings.EVAL_FAITHFULNESS_THRESHOLD
                    or answer_relevancy < settings.EVAL_RELEVANCY_THRESHOLD
                ):
                    continue

                scenario = scenario_by_id.get(str(score["scenario_id"]))
                if scenario is None:
                    log.warning(
                        "promote_to_verified_qa.scenario_not_found",
                        scenario_id=score["scenario_id"],
                    )
                    continue

                question = scenario["question"]

                # D-23 LOCKED: Voyage embedding for question_vector
                question_vector = _get_vo().embed(
                    [question], model="voyage-3", input_type="query"
                ).embeddings[0]

                answer = scenario.get("agent_response", "")
                citations = scenario.get("citations", [])

                cur.execute(sql, {
                    "question": question,
                    # str(vector) then cast with ::vector — matching retrieval_service.py pattern
                    "question_vector": str(question_vector),
                    "answer": answer,
                    "citations": json.dumps(citations),
                    "faithfulness": faithfulness,
                    "relevance": answer_relevancy,
                })

                promoted_count += 1
                log.info(
                    "verified_qa.promoted",
                    scenario_id=score["scenario_id"],
                    faithfulness=faithfulness,
                    relevance=answer_relevancy,
                )

        conn.commit()
    finally:
        conn.close()

    log.info(
        "promote_to_verified_qa.complete",
        promoted=promoted_count,
        total_scored=len(scenario_scores),
    )
    return promoted_count


# ---------------------------------------------------------------------------
# Top-level orchestrator (used by 06-05 Celery task)
# ---------------------------------------------------------------------------

def run_eval_for_agent(
    eval_run_id: str,
    scenarios: list[dict],
    branch_conn_str: str,
) -> dict:
    """Run a full eval cycle for one agent: Ragas eval + DB writes + promotion.

    This is the function called by the run_eval_suite Celery task (06-05).
    The branch_conn_str is passed as a local variable within the task — it is
    NEVER stored in the DB or passed as a Celery argument (CLAUDE.md CTL-08).

    Sequence:
        1. update_eval_run_status → 'running'
        2. run_ragas_eval (four metrics against branch)
        3. write_eval_results (eval_results rows on branch)
        4. promote_to_verified_qa (verified_qa rows on branch)
        5. update_eval_run_status → 'complete'

    On exception: update_eval_run_status → 'failed', then re-raise.

    Args:
        eval_run_id: UUID string — the eval_runs row already created by caller.
        scenarios: List of scenario dicts from the eval_scenarios table.
        branch_conn_str: Neon branch connection string (never production).

    Returns:
        Dict: {
            "eval_run_id": str,
            "scenario_count": int,
            "means": dict,
            "promoted_count": int,
        }
    """
    log.info("run_eval_for_agent.start", eval_run_id=eval_run_id)
    update_eval_run_status(eval_run_id, "running", finished_at=False, branch_conn_str=branch_conn_str)

    try:
        result = run_ragas_eval(scenarios, branch_conn_str)
        scenario_scores = result["scores"]
        means = result["means"]

        write_eval_results(eval_run_id, scenario_scores, branch_conn_str)
        promoted_count = promote_to_verified_qa(scenarios, scenario_scores, branch_conn_str)

        update_eval_run_status(eval_run_id, "complete", finished_at=True, branch_conn_str=branch_conn_str)

        log.info(
            "run_eval_for_agent.complete",
            eval_run_id=eval_run_id,
            scenario_count=len(scenarios),
            promoted_count=promoted_count,
        )

        return {
            "eval_run_id": eval_run_id,
            "scenario_count": len(scenarios),
            "means": means,
            "promoted_count": promoted_count,
        }

    except Exception as exc:
        log.error(
            "run_eval_for_agent.failed",
            eval_run_id=eval_run_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        update_eval_run_status(eval_run_id, "failed", finished_at=True, branch_conn_str=branch_conn_str)
        raise
