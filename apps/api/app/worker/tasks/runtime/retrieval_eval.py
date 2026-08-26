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

The Judge names itself (ticket #47, AC3):
    A faithfulness score nobody can attribute cannot be calibrated against, so
    the row carries the model, the reasoning effort and the prompt version that
    produced it, in `retrieval_metrics.judge_identity` (tenant migration 0020).
    `eval_service` does the same for its four offline metrics, in
    `eval_results.detail`. Each lands beside its own verdict, so a calibration
    figure reads one place per verdict and joins nothing.

Security (CLAUDE.md rule 4): task args are (agent_id, job_id) ONLY. conn_str
is decrypted at runtime from the control DB, never in task args/logs.

Ragas import:
    `import ragas` pulls langchain, datasets and pandas — seconds of import
    time. To keep THIS module cheap at Celery worker startup (celery_app.py's
    `include=[...]` list imports every task module eagerly), the ragas imports
    here are LAZY, confined inside `_build_instructor_llm()` /
    `_build_faithfulness_metrics()`, never at module top-level. The provider SDKs
    are no longer imported here at all: `app.core.model_client` owns them, and
    the worker already pays for that module through `celery_app`.

Ragas 0.4.x scoring shape (7.18 — this task returned faithfulness=None on the
first live turn ever sampled):
    `ragas.metrics.collections.Faithfulness` descends from SimpleBaseMetric,
    NOT from `ragas.metrics.base.Metric`, so `ragas.evaluate()` rejects it at
    evaluation.py:133 with "All metrics must be initialised metric objects" —
    a message about the class hierarchy, not about instantiation. Collections
    metrics are scored directly: `await metric.ascore(...) -> MetricResult`,
    which is the API CLAUDE.md rule 4 names. The LLM must wrap an ASYNC
    client: collections metrics only ever call `llm.agenerate()`, and
    InstructorLLM.agenerate raises TypeError on a sync one. `make_async_client`
    is the factory's answer to that.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random

import psycopg2
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.model_client import (
    OPENAI_PROVIDER,
    LedgerContext,
    ledger_recorder,
    route_for,
)
from app.core.security import fernet_decrypt, require_ciphertext
from app.domain.judge_identity import JUDGE_PROMPT_VERSION, JudgeIdentity
from app.models.agent import Agent
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

#: The routing-table key this task's judge calls bill under.
JUDGE_PURPOSE = "judge_retrieval_faithfulness"


def judge_identity() -> JudgeIdentity | None:
    """Which Judge scored this turn, at the grain calibration compares on.

    The fifth Judge. `eval_service.judge_identity_for` answers the same question
    for the four offline metrics, and this answers it for the one that scores
    live traffic. Both read the model and the effort off `PURPOSE_ROUTES`, the
    table the request itself was built from, so neither record can name a Judge
    the run did not use.

    Returns None when the route names no reasoning effort. Decision #34 priced
    the Judge floor at effort `none` and this route carries it today; a route
    that dropped it would leave the identity a field short, and a key with a hole
    in it groups two different Judges together. An absent identity says the Judge
    is unknown, which is what it would be.
    """
    route = route_for(JUDGE_PURPOSE)
    if route.reasoning_effort is None:
        log.error(
            "judge_identity.no_reasoning_effort",
            purpose=JUDGE_PURPOSE,
            model=route.model,
            detail=(
                "the route names no effort, so the Judge cannot be identified "
                "and its verdicts cannot be calibrated against"
            ),
        )
        return None
    return JudgeIdentity(
        model=route.model,
        reasoning_effort=route.reasoning_effort,
        prompt_version=JUDGE_PROMPT_VERSION,
    )


def _turn_ledger(tenant_id: str, agent_id: str, job_id: str, conn_str: str) -> LedgerContext:
    """Who this sampled turn's judge call is billed to, and where its row goes."""
    return LedgerContext(
        tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        recorder=ledger_recorder(conn_str),
    )


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


def _identity_row(faithfulness: float | None) -> dict | None:
    """The Judge that produced this verdict, in the shape the row stores, or None.

    None where there is no verdict to attribute, and None again where the route
    could not name a complete Judge. Both are unknown, and unknown is what the
    column then holds.
    """
    if faithfulness is None:
        return None
    identity = judge_identity()
    return dataclasses.asdict(identity) if identity else None


def _update_retrieval_metrics(
    conn_str: str,
    job_id: str,
    citation_coverage: float | None,
    faithfulness: float | None,
    identity: dict | None,
) -> None:
    """Write this turn's two signals, and the Judge that produced the second one.

    `identity` belongs to `faithfulness` and to nothing else on the row.
    citation_coverage is arithmetic this task does itself, so a row carrying only
    that one gets NULL here rather than the name of a Judge that did no work
    (tenant migration 0020). It is named `identity` rather than `judge_identity`
    so it cannot be read as the module function of that name.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE retrieval_metrics SET citation_coverage = %s, faithfulness = %s,"
                " judge_identity = %s::jsonb WHERE job_id = %s",
                (
                    citation_coverage,
                    faithfulness,
                    json.dumps(identity) if identity else None,
                    job_id,
                ),
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
    emit time, see agent_loop.py's run_agent_loop) is the best available retrieved-
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


def _build_instructor_llm(purpose: str, ledger: LedgerContext):
    """The InstructorLLM this task's Faithfulness metric scores through.

    The client is async. Collections metrics await `llm.agenerate(...)`
    exclusively, and `InstructorLLM.agenerate` raises
    TypeError("Cannot use agenerate() with a synchronous client") for any client
    whose `chat.completions.create` is not a coroutine function
    (`ragas/llms/base.py`, `_check_client_async`). It carries the ledger hook, so
    a sampled live turn's judge call is counted like every other call.

    `thinking={"type": "disabled"}` is gone with the provider that needed it. It
    cleared a DeepSeek 400 on the forced tool_choice instructor puts on every
    structured call; OpenAI has no such parameter, and ragas splats every extra
    kwarg straight into `client.chat.completions.create()`
    (`ragas/llms/base.py:1109`), so leaving it in would put an unknown field on
    the wire.

    Args:
        purpose: the routing-table key this judge call bills under. Passed in
            rather than read off the module constant, so this builder has the
            same shape as eval_service's and one test drives both.
        ledger: the ids this judge call is billed to and where its row goes.
    """
    from ragas.llms import InstructorLLM

    return InstructorLLM(
        client=ledger.instructor_client(purpose, is_async=True),
        model=route_for(purpose).model,
        provider=OPENAI_PROVIDER,
        # BACKLOG 8.2a. The **kwargs seam: merged into `model_args`
        # (ragas/llms/base.py:772) and splatted into the client call by agenerate
        # (:1109). Ragas metrics ARE judges, so they get the same temperature as
        # every other verdict in the system.
        #
        # CORRECTED 2026-08-18 by adversarial review: this site was NOT sampling
        # at the provider default before 8.2a. ragas 0.4.3's InstructorModelArgs
        # defaults to `temperature=0.01, top_p=0.1` whenever `model_args is None`,
        # which is how this is constructed, and 0.01 was measured on the wire. So
        # the change here is 0.01 -> 0, not "unset -> 0".
        #
        # STILL OPEN and deliberately not changed here: ragas also sends
        # `top_p: 0.1` alongside, and setting temperature and top_p together is
        # against both providers' guidance. BACKLOG 8.10.
        temperature=0,
    )


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
    question: str, response_text: str, contexts: list[str], ledger: LedgerContext
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
        llm = _build_instructor_llm(JUDGE_PURPOSE, ledger)
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
            log.error("run_retrieval_faithfulness.agent_not_found", job_id=job_id, agent_id=agent_id)
            return {}
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))
        tenant_id = str(agent.tenant_id)  # read while the session is open

        # Idempotency guard (T-21-04-01 adjacent): skip the recompute if already
        # scored, and skip entirely if no retrieval_metrics row exists.
        already_scored, has_row = _check_existing_score(conn_str, job_id)
        if not has_row:
            log.info("run_retrieval_faithfulness.no_retrieval_metrics_row", job_id=job_id)
            return {"status": "no_retrieval_metrics_row"}
        if already_scored:
            log.info("run_retrieval_faithfulness.already_scored", job_id=job_id)
            return {"status": "already_scored"}

        # ------------------------------------------------------------------
        # Gating (T-21-04-01: DoS/cost mitigation) is sampled OR 100% of the
        # Auditor-flagged ungrounded and partial turns. The module docstring says
        # why it lives here, post-Auditor, rather than at dispatch.
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

    # citation_coverage is a coarse proxy. The schema does not persist per-chunk
    # citation attribution, so this measures how often a retrieve call led to a
    # cited claim (cited spans over retrieve calls with a result), not exact
    # chunk-level coverage. None rather than 0.0 when nothing was retrieved,
    # which is the honest empty state (DOMAIN-NOTES §6).
    if retrieve_contexts:
        citation_coverage = min(1.0, len(citations_list) / len(retrieve_contexts))
    else:
        citation_coverage = None

    question = _fetch_last_user_message(conn_str, conversation_id) if conversation_id else None
    faithfulness = _compute_ragas_faithfulness(
        question=question or "", response_text=response_text, contexts=retrieve_contexts,
        ledger=_turn_ledger(tenant_id, agent_id, job_id, conn_str))

    if citation_coverage is None and faithfulness is None:
        log.info("run_retrieval_faithfulness.no_signal", job_id=job_id)
        return {"status": "no_signal"}

    identity_row = _identity_row(faithfulness)

    try:
        _update_retrieval_metrics(conn_str, job_id, citation_coverage, faithfulness, identity_row)
    except Exception as exc:  # noqa: BLE001 — never fail/retry an already-served turn
        log.warning("run_retrieval_faithfulness.update_failed", job_id=job_id, error=str(exc))
        return {}

    log.info(
        "run_retrieval_faithfulness.complete",
        job_id=job_id,
        citation_coverage=citation_coverage,
        faithfulness=faithfulness,
        judge_identity=identity_row,
    )
    return {
        "status": "scored",
        "citation_coverage": citation_coverage,
        "faithfulness": faithfulness,
        "judge_identity": identity_row,
    }
