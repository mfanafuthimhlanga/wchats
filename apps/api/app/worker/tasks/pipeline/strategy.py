"""
synthesize_retrieval_strategy — Celery task: corpus-signal-driven retrieval strategy synthesis.

Position in M3 chain (5th of 5):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate
    → synthesize_retrieval_strategy

This task runs after embedding is complete. It:
1. Fetches corpus signals from the tenant DB (psycopg2).
2. Calls the direct Anthropic API (run_strategist) to select optimized retrieval parameters.
3. Validates the result via RetrievalStrategy.model_validate (Pydantic).
4. Writes agent.retrieval_strategy to the control DB.
5. Emits 'strategy.synthesized' SSE event.
6. Returns the same IngestionJob it received (chain pass-through).

Chain contract:
    job_in_job_out (chain_edge.py) builds the job from the wire dict at the
    task's edge and sends the returned job back out as one, so Celery still
    carries the four keys as JSON and the body works in the type. This hop read
    the dict with its own `result.get("agent_id")` guard until 2026-08-24, which
    is one more place a renamed key would have turned into a silent no-op.

Idempotency:
    - Skips synthesis if agent.retrieval_strategy is already set AND
      agent.strategy_resynthesis_flagged is False.
    - On re-run after strategy_resynthesis_flagged=True, synthesis runs again
      and clears the flag.

Connection string security (CLAUDE.md non-negotiable rule):
    - conn_str is NEVER in task args — fetched from control DB and decrypted
      via fernet_decrypt() at runtime.
    - conn_str is NEVER logged.

acks_late=True + idempotency: both are always required (CLAUDE.md rule 5).
"""

from __future__ import annotations

import json
import ssl

import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.domain.ingestion_job import IngestionJob
from app.models.agent import Agent
from app.services.events import emit
from app.services.retrieval_service import RetrievalStrategy
from app.services.strategy_service import _fetch_corpus_signals_sync, run_strategist
from app.worker.celery_app import celery_app
from app.worker.tasks.pipeline.chain_edge import job_in_job_out

log = structlog.get_logger(__name__)

# Module-level sync Redis client — strip query params; pass ssl_cert_reqs as constant.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=10,
    queue="pipeline",
)
@job_in_job_out
def synthesize_retrieval_strategy(self, job: IngestionJob) -> IngestionJob:
    """Synthesize an optimized retrieval strategy from corpus signals.

    This is the fifth task in the M3 ingestion chain. It:
    1. Reads agent_id and job_id off the job embed_and_migrate returned.
    2. Checks idempotency — skips if strategy already set and resynthesis not flagged.
    3. Fetches corpus signals from the tenant DB via psycopg2.
    4. Calls the Strategist's direct-API turn, 60s timeout. Never the Agent SDK.
    5. Validates the output via RetrievalStrategy; falls back to defaults on failure.
    6. Writes agent.retrieval_strategy and clears strategy_resynthesis_flagged.
    7. Emits 'strategy.synthesized' SSE event.

    Args:
        job: The IngestionJob the chain carries. Connection strings are NEVER on
             it (CLAUDE.md non-negotiable rule); the type has no field for one.
             Construction has already refused an empty agent_id or job_id, which
             is the guard this task used to spell for itself.

    Returns:
        The same job, unchanged. job_in_job_out converts both ends, so the broker
        still sees the four keys it has always seen.
    """
    # ------------------------------------------------------------------
    # Step 1. Read the two ids this task uses
    # ------------------------------------------------------------------
    # tenant_id and document_ids are deliberately not read here. This task returns
    # the job it was handed, so the chain contract is preserved by pass-through
    # rather than by re-assembling it. Binding them was dead code (F841).
    agent_id = job.agent_id
    job_id = job.job_id

    # ------------------------------------------------------------------
    # Step 2 — Idempotency guard + conn_str fetch in ONE get_sync_db() block
    # conn_str is intentionally not logged — CLAUDE.md non-negotiable rule.
    # ------------------------------------------------------------------
    try:
        with get_sync_db() as db:
            agent = db.get(Agent, agent_id)
            if agent is None:
                log.error(
                    "synthesize_retrieval_strategy.agent_not_found",
                    agent_id=agent_id,
                )
                return job
            if (
                agent.retrieval_strategy
                and agent.retrieval_strategy != {}
                and not agent.strategy_resynthesis_flagged
            ):
                log.info(
                    "synthesize_retrieval_strategy.idempotent_skip",
                    agent_id=agent_id,
                )
                return job
            if not agent.neon_connection_string:
                log.error(
                    "synthesize_retrieval_strategy.no_conn_str",
                    agent_id=agent_id,
                )
                return job
            conn_str = fernet_decrypt(agent.neon_connection_string)
    except Exception as exc:
        log.error(
            "synthesize_retrieval_strategy.db_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # ------------------------------------------------------------------
    # Step 3 — Collect corpus signals from tenant DB (psycopg2 sync)
    # ------------------------------------------------------------------
    try:
        signals = _fetch_corpus_signals_sync(agent_id, conn_str)
    except Exception as exc:
        log.error(
            "synthesize_retrieval_strategy.corpus_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    signals_json = json.dumps(signals)

    # ------------------------------------------------------------------
    # Step 4 — Call direct Anthropic API (run_strategist — synchronous)
    # Strategist failures are not retried — fall through to validation
    # with empty result_container so defaults are applied.
    # ------------------------------------------------------------------
    result_container: dict = {}
    try:
        run_strategist(signals_json, result_container, job, conn_str)
    except Exception as exc:
        log.error(
            "synthesize_retrieval_strategy.strategist_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        # Fall through to validation with empty result_container — defaults applied.

    # ------------------------------------------------------------------
    # Step 5 — Validate strategy output; fall back to defaults on failure
    # ------------------------------------------------------------------
    raw = result_container.get("strategy", {})
    try:
        strategy = RetrievalStrategy.model_validate(raw)
    except Exception as val_exc:
        log.warning(
            "synthesize_retrieval_strategy.validation_failed",
            agent_id=agent_id,
            error=str(val_exc),
        )
        strategy = RetrievalStrategy()

    # ------------------------------------------------------------------
    # Step 6 — Write strategy to control DB in a NEW get_sync_db() block
    # (do NOT reuse the idempotency session — session boundary is intentional)
    # ------------------------------------------------------------------
    try:
        with get_sync_db() as db:
            agent = db.get(Agent, agent_id)
            if agent is not None:
                agent.retrieval_strategy = strategy.model_dump()
                agent.strategy_resynthesis_flagged = False
                db.commit()
            # job_id is never empty. IngestionJob refuses to exist without one.
            emit(job_id, "strategy.synthesized", {"agent_id": agent_id}, db, _redis)
    except Exception as exc:
        log.error(
            "synthesize_retrieval_strategy.db_write_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # ------------------------------------------------------------------
    # Step 7 — Log completion and return chain pass-through
    # ------------------------------------------------------------------
    log.info("synthesize_retrieval_strategy.complete", agent_id=agent_id)

    return job
