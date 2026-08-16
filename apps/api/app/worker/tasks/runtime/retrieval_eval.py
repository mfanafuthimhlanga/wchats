"""
run_retrieval_faithfulness — Celery task (runtime queue): sampled Ragas 0.4.x
faithfulness + per-turn citation coverage (OPS-07).

Position in the post-turn chain (agent.py):
    celery_chain(run_gatekeeper.si(...), run_auditor.si(...), run_strategist.si(...),
                 run_retrieval_faithfulness.si(str(agent_id), job_id)).apply_async(queue="runtime")

Why the gating (sample rate OR Auditor-flagged) lives INSIDE this task, not at
dispatch time in agent.py:
    The Auditor's verdict (grounded/ungrounded/partial) is only known once
    run_auditor has executed and committed its `auditor.complete` job_events
    row. Since run_auditor is the PRECEDING step in the same celery_chain,
    that verdict does not exist yet at the moment agent.py assembles and
    dispatches the chain. Appending `run_retrieval_faithfulness.si(...)` as
    the chain's last step guarantees it runs strictly after Auditor commits
    its verdict, so THIS task can honestly query "was this turn flagged?" and
    apply DOMAIN-NOTES §2's "sample 1-10% + 100% of guardrail-flagged" rule.
    This is a sequencing decision documented as a deviation in 21-04-SUMMARY.md,
    not a re-interpretation of the sampling policy itself.

Idempotency (CLAUDE.md rule 5): re-running this task for the same job_id is a
no-op once retrieval_metrics.faithfulness is non-NULL for that row.

Security (CLAUDE.md rule 4): task args are (agent_id, job_id) ONLY. conn_str
is decrypted at runtime from the control DB, never in task args/logs.

Ragas import:
    `import ragas` pulls langchain, datasets and pandas — seconds of import
    time. To keep THIS module cheap at Celery worker startup (celery_app.py's
    `include=[...]` list imports every task module eagerly), all
    ragas/instructor/anthropic imports here are LAZY, confined inside
    `_build_instructor_llm()` / `_build_faithfulness_metrics()`, never at
    module top-level.

Ragas 0.4.x scoring shape (7.18 — this task returned faithfulness=None on the
first live turn ever sampled):
    `ragas.metrics.collections.Faithfulness` descends from SimpleBaseMetric,
    NOT from `ragas.metrics.base.Metric`, so `ragas.evaluate()` rejects it at
    evaluation.py:133 with "All metrics must be initialised metric objects" —
    a message about the class hierarchy, not about instantiation. Collections
    metrics are scored directly: `await metric.ascore(...) -> MetricResult`,
    which is the API CLAUDE.md rule 4 names. The LLM must wrap an ASYNC
    Anthropic client: collections metrics only ever call `llm.agenerate()`,
    and InstructorLLM.agenerate raises TypeError on a sync client.
"""

from __future__ import annotations

import asyncio
import random

import psycopg2
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Tenant-DB helpers (psycopg2 connect/try/finally/close idiom, per convention)
# ---------------------------------------------------------------------------


def _check_existing_score(conn_str: str, job_id: str) -> tuple[bool, bool]:
    """Return (already_scored, has_row) for the retrieval_metrics row at job_id.

    has_row is False when no retrieval_metrics row exists yet for this job
    (e.g. the turn never called the retrieve tool) — nothing to score.
    already_scored is True when faithfulness is already non-NULL (idempotent skip).
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT faithfulness FROM retrieval_metrics WHERE job_id = %s LIMIT 1",
                (job_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return False, False
    return row[0] is not None, True


def _fetch_last_user_message(conn_str: str, conversation_id: str) -> str | None:
    """Best-effort question proxy for the Ragas call.

    The user's question text is intentionally NEVER persisted to control-DB
    job_events (T-04-03-05 — message text must never be logged). The tenant-DB
    `messages` table IS the durable transcript (written by _persist_messages
    in agent.py), so the most recent role='user' row for this conversation is
    used as the question for this turn. This is a best-effort correlation by
    recency, not by job_id (messages has no job_id column) — acceptable for a
    sampled, non-blocking analytics task; documented as a known limitation.
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM messages WHERE conversation_id = %s"
                    " AND role = 'user' ORDER BY created_at DESC LIMIT 1",
                    (conversation_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — best-effort, never fails the task
        log.warning("run_retrieval_faithfulness.question_fetch_failed", error=str(exc))
        return None
    return row[0] if row else None


def _update_retrieval_metrics(
    conn_str: str, job_id: str, citation_coverage: float | None, faithfulness: float | None
) -> None:
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE retrieval_metrics SET citation_coverage = %s, faithfulness = %s"
                " WHERE job_id = %s",
                (citation_coverage, faithfulness, job_id),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Control-DB helpers (job_events — same tier as validators.py's queries)
# ---------------------------------------------------------------------------


def _is_auditor_flagged(db, job_id: str) -> bool:
    """True if this job's Auditor verdict was 'ungrounded' or 'partial'.

    Queried from control-DB job_events, which by the time this task runs
    (last step of the post-turn chain) already has auditor.complete committed
    if the Auditor step succeeded. If the Auditor step failed/exhausted
    retries (no auditor.complete row), this returns False — the sample-rate
    gate is the only signal in that case, which is the safe default (never
    force-run the expensive Ragas call on missing data).
    """
    flagged_row = db.execute(
        sa_text(
            "SELECT 1 FROM job_events WHERE job_id = :jid"
            " AND event_type = 'auditor.complete'"
            " AND payload->>'verdict' IN ('ungrounded', 'partial') LIMIT 1"
        ),
        {"jid": job_id},
    ).fetchone()
    return flagged_row is not None


def _fetch_turn_context(db, job_id: str) -> tuple[str, list, str | None, list[str]] | None:
    """Return (response_text, citations_list, conversation_id, retrieve_contexts) or None.

    retrieve_contexts is built from `agent.tool_result` job_events whose
    tool_name is 'retrieve' — the summary field (truncated to 200 chars at
    emit time, see agent.py's _run_sdk_turn) is the best available retrieved-
    context proxy from data that is actually persisted; full chunk content is
    never round-tripped into job_events by design.
    """
    response_row = db.execute(
        sa_text(
            "SELECT payload FROM job_events WHERE job_id = :jid"
            " AND event_type = 'agent.response' LIMIT 1"
        ),
        {"jid": job_id},
    ).fetchone()
    if response_row is None or response_row[0] is None:
        return None

    response_payload = response_row[0]
    response_text = response_payload.get("text", "") or ""
    citations_list = response_payload.get("citations") or []
    conversation_id = response_payload.get("conversation_id")

    tool_result_rows = db.execute(
        sa_text(
            "SELECT payload FROM job_events WHERE job_id = :jid"
            " AND event_type = 'agent.tool_result'"
        ),
        {"jid": job_id},
    ).fetchall()
    retrieve_contexts = [
        r[0].get("summary", "")
        for r in tool_result_rows
        if r[0] and r[0].get("tool_name") == "retrieve"
    ]

    return response_text, citations_list, conversation_id, retrieve_contexts


# ---------------------------------------------------------------------------
# Ragas 0.4.x faithfulness — LAZY import (see module docstring)
# ---------------------------------------------------------------------------


def _build_instructor_llm():
    """Build the InstructorLLM the collections metrics score through.

    The client is `anthropic.AsyncAnthropic`, not `anthropic.Anthropic`.
    Collections metrics await `llm.agenerate(...)` exclusively, and
    InstructorLLM.agenerate raises
    TypeError("Cannot use agenerate() with a synchronous client") whenever its
    client is sync — which is what `instructor.from_anthropic(Anthropic())`
    produces (InstructorLLM.is_async is False for it).
    """
    import anthropic
    import instructor
    from ragas.llms import InstructorLLM

    client = instructor.from_anthropic(anthropic.AsyncAnthropic())
    return InstructorLLM(client=client, model=HAIKU_MODEL, provider="anthropic")


def _build_faithfulness_metrics(llm) -> list:
    """The metric list this task scores through: constructed INSTANCES.

    Unlike eval_service's offline harness, no ground-truth `reference` is
    available for live traffic, so only the reference-free Faithfulness metric
    is computed — it checks response claims against retrieved_contexts, not
    against a ground-truth answer.
    """
    from ragas.metrics.collections import Faithfulness

    return [Faithfulness(llm=llm)]


def _compute_ragas_faithfulness(
    question: str, response_text: str, contexts: list[str]
) -> float | None:
    """Compute a single-turn Ragas 0.4.x Faithfulness score.

    Scores through `metric.ascore(...) -> MetricResult` rather than
    `ragas.evaluate()`; see the module docstring for why evaluate() cannot take
    a collections metric.

    Never raises: any failure (import, API, parsing) is caught, logged, and
    returns None — a faithfulness-scoring failure must never fail or retry
    the sampled analytics task's other work (citation_coverage still writes).
    """
    if not contexts or not response_text or not question:
        return None

    try:
        llm = _build_instructor_llm()
        metrics = _build_faithfulness_metrics(llm)
    except Exception as exc:  # noqa: BLE001 — import or client construction
        log.warning("run_retrieval_faithfulness.ragas_import_failed", error=str(exc))
        return None

    try:
        result = asyncio.run(
            metrics[0].ascore(
                user_input=question,
                response=response_text,
                retrieved_contexts=contexts,
            )
        )
        raw = result.value
        return float(raw) if raw is not None and raw == raw else None  # raw == raw: NaN check
    except Exception as exc:  # noqa: BLE001 — never fail the task on a Ragas/API error
        log.warning("run_retrieval_faithfulness.ragas_call_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="app.worker.tasks.runtime.retrieval_eval.run_retrieval_faithfulness",
)
def run_retrieval_faithfulness(self, agent_id: str, job_id: str) -> dict:  # noqa: ARG001
    """Sampled Ragas faithfulness + citation-coverage UPDATE (OPS-07).

    Args:
        agent_id: UUID string. conn_str is decrypted at runtime from the
                  control DB — NEVER an argument (CLAUDE.md rule 4).
        job_id:   UUID string of the runtime chat job this scores.

    Returns:
        {"status": "already_scored" | "no_retrieval_metrics_row" |
                    "skipped_not_sampled" | "no_agent_response_event" |
                    "no_signal" | "scored", ...}
        {} on agent-not-found or an unrecoverable UPDATE failure.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "run_retrieval_faithfulness.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        # ------------------------------------------------------------------
        # Idempotency guard (T-21-04-01 adjacent): skip recompute if already
        # scored, and skip entirely if no retrieval_metrics row exists.
        # ------------------------------------------------------------------
        already_scored, has_row = _check_existing_score(conn_str, job_id)
        if not has_row:
            log.info("run_retrieval_faithfulness.no_retrieval_metrics_row", job_id=job_id)
            return {"status": "no_retrieval_metrics_row"}
        if already_scored:
            log.info("run_retrieval_faithfulness.already_scored", job_id=job_id)
            return {"status": "already_scored"}

        # ------------------------------------------------------------------
        # Gating (T-21-04-01: DoS/cost mitigation) — sampled OR 100% of
        # Auditor-flagged ungrounded/partial turns. See module docstring for
        # why this check lives here (post-Auditor) rather than at dispatch.
        # ------------------------------------------------------------------
        sampled = random.random() < settings.RETRIEVAL_FAITHFULNESS_SAMPLE_RATE
        auditor_flagged = False if sampled else _is_auditor_flagged(db, job_id)
        if not (sampled or auditor_flagged):
            log.debug("run_retrieval_faithfulness.skipped_not_sampled", job_id=job_id)
            return {"status": "skipped_not_sampled"}

        turn_context = _fetch_turn_context(db, job_id)
        if turn_context is None:
            log.warning("run_retrieval_faithfulness.no_agent_response_event", job_id=job_id)
            return {"status": "no_agent_response_event"}

    response_text, citations_list, conversation_id, retrieve_contexts = turn_context

    # citation_coverage: a coarse proxy — the schema does not persist
    # per-chunk citation attribution, so this measures "how often a retrieve
    # call led to a cited claim" (cited spans / retrieve calls with a result),
    # not exact chunk-level coverage. None (not 0.0) when nothing was
    # retrieved — honest-empty-state discipline (DOMAIN-NOTES §6).
    if retrieve_contexts:
        citation_coverage = min(1.0, len(citations_list) / len(retrieve_contexts))
    else:
        citation_coverage = None

    question = _fetch_last_user_message(conn_str, conversation_id) if conversation_id else None
    faithfulness = _compute_ragas_faithfulness(
        question=question or "", response_text=response_text, contexts=retrieve_contexts
    )

    if citation_coverage is None and faithfulness is None:
        log.info("run_retrieval_faithfulness.no_signal", job_id=job_id)
        return {"status": "no_signal"}

    try:
        _update_retrieval_metrics(conn_str, job_id, citation_coverage, faithfulness)
    except Exception as exc:  # noqa: BLE001 — never fail/retry an already-served turn
        log.warning("run_retrieval_faithfulness.update_failed", job_id=job_id, error=str(exc))
        return {}

    log.info(
        "run_retrieval_faithfulness.complete",
        job_id=job_id,
        citation_coverage=citation_coverage,
        faithfulness=faithfulness,
    )
    return {
        "status": "scored",
        "citation_coverage": citation_coverage,
        "faithfulness": faithfulness,
    }
