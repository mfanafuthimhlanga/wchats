"""
retrieve_and_rank — Celery task: Hybrid retrieval + rerank + SSE events.

Position in M3 runtime flow:
    API endpoint → dispatch retrieve_and_rank (runtime queue) → SSE stream back to client

This task executes the full hybrid retrieval pipeline for a single query:
  1. Embed the query with Voyage voyage-3 (input_type="query")
  2. Run the RRF CTE (vector HNSW + BM25 tsvector → fused by RRF score)
  3. Rerank the fused candidates with Voyage rerank-2 (Cohere fallback)
  4. Build a retrieval trace with truncated candidate content (max 200 chars)
  5. Emit 5 SSE events and mark the job complete

Idempotency mechanism:
    READ guard on job_events: if a "query.complete" row already exists for this
    job_id, the task returns immediately with {} — safe to retry without
    duplicate events or double-billing.

Security constraints (CLAUDE.md non-negotiable rules):
    - Task args: (job_id, agent_id, query) only — NO conn_str, NO API keys.
    - conn_str fetched via fernet_decrypt(agent.neon_connection_string) at runtime.
    - Query text is NEVER logged; only job_id, agent_id, and counts are logged.

SSE event sequence:
    query.started    ← task begins, agent confirmed present
    query.embedding  ← query vector produced (voyage-3)
    query.searching  ← RRF CTE executed, fused candidates counted
    query.reranking  ← Voyage/Cohere rerank complete
    query.complete   ← final payload with results + trace written to job_events

Queue: runtime (CLAUDE.md non-negotiable: both Celery queues always present)
"""

from datetime import datetime, timezone

import redis as redis_lib
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.redis_tls import redis_ssl_kwargs
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.retrieval_service import (
    RetrievalStrategy,
    build_trace,
    embed_query,
    rerank,
    rrf_fuse,
    verified_qa_lookup,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client. Strip the query string, then redis_ssl_kwargs decides TLS.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = redis_ssl_kwargs(_url_clean)
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=2,
    queue="runtime",
    name="retrieve_and_rank",
)
def retrieve_and_rank(self, job_id: str, agent_id: str, query: str) -> dict:
    """Execute hybrid retrieval pipeline for a single query and emit SSE events.

    Idempotent: returns {} immediately if a "query.complete" event row already
    exists for this job_id (duplicate delivery / retry safety).

    Args:
        job_id:   UUID string of the runtime query job.
        agent_id: UUID string of the agent whose retrieval_strategy config is used.
        query:    Raw user query string. NEVER logged — security constraint.

    Returns:
        {} always. Never returns sensitive data (conn_str, query text, API keys).
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Idempotency guard — exit immediately if query.complete already exists
        # for this job_id. Prevents duplicate events on Celery retry or
        # at-least-once redelivery from Redis.
        # ------------------------------------------------------------------
        existing = db.execute(
            sa_text(
                "SELECT 1 FROM job_events"
                " WHERE job_id = :jid AND event_type = 'query.complete' LIMIT 1"
            ),
            {"jid": job_id},
        ).fetchone()
        if existing:
            log.info("retrieve_and_rank.idempotent_skip", job_id=job_id)
            return {}

        # ------------------------------------------------------------------
        # Fetch agent from control DB — required for retrieval_strategy JSONB
        # and the encrypted neon_connection_string.
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "retrieve_and_rank.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}

        # ------------------------------------------------------------------
        # Fetch job from control DB — required to update status on completion.
        # ------------------------------------------------------------------
        job = db.get(Job, job_id)
        if job is None:
            log.error("retrieve_and_rank.job_not_found", job_id=job_id)
            return {}

        # ------------------------------------------------------------------
        # Parse retrieval strategy from JSONB — all fields optional with
        # defaults in RetrievalStrategy. Unknown keys silently ignored.
        # ------------------------------------------------------------------
        strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})

        # ------------------------------------------------------------------
        # Decrypt connection string at runtime — NEVER in task args.
        # (T-02-05-01 / CLAUDE.md non-negotiable rule: conn_str at runtime only)
        # conn_str is intentionally not logged.
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        try:
            # --------------------------------------------------------------
            # EVENT 1: query.started — confirm task is executing for this agent
            # --------------------------------------------------------------
            emit(job_id, "query.started", {"agent_id": agent_id}, db, _redis)

            # --------------------------------------------------------------
            # Embed query with Voyage voyage-3 (input_type="query")
            # --------------------------------------------------------------
            query_vector = embed_query(query)

            # EVENT 2: query.embedding — vector produced
            emit(job_id, "query.embedding", {"model": "voyage-3"}, db, _redis)

            # --------------------------------------------------------------
            # D-24: verified_qa cache lookup BEFORE hybrid search.
            # If cosine similarity >= VERIFIED_QA_HIT_THRESHOLD (0.93), return
            # the cached answer immediately and skip vector + BM25 entirely.
            # --------------------------------------------------------------
            cache_hit = verified_qa_lookup(
                conn_str=conn_str,
                query_vector=query_vector,
                threshold=settings.VERIFIED_QA_HIT_THRESHOLD,
            )
            if cache_hit is not None:
                log.info(
                    "retrieve_and_rank.cache_hit",
                    job_id=job_id,
                    similarity=cache_hit["similarity"],
                )
                payload = {
                    "results": [{"content": cache_hit["answer"], "citations": cache_hit["citations"]}],
                    "trace": {
                        "cache_hit": True,
                        "similarity": cache_hit["similarity"],
                        "source": "verified_qa_cache",
                    },
                }
                emit(job_id, "query.complete", payload, db, _redis)
                job.status = "complete"
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
                log.info(
                    "retrieve_and_rank.complete",
                    job_id=job_id,
                    agent_id=agent_id,
                    reranked_count=1,
                )
                return {}

            # D-27: cache miss — fall through to hybrid search (existing code unchanged)

            # --------------------------------------------------------------
            # RRF fusion: one CTE, a RetrievedContext under each of three fields
            # --------------------------------------------------------------
            rrf_result = rrf_fuse(conn_str, query_vector, query, strategy)
            fused = rrf_result.fused
            vector_cands = rrf_result.vector_candidates
            bm25_cands = rrf_result.bm25_candidates

            log.info(
                "retrieve_and_rank.searching_complete",
                job_id=job_id,
                fused_count=len(fused.chunks),
                vector_count=len(vector_cands.chunks),
                bm25_count=len(bm25_cands.chunks),
            )

            # EVENT 3: query.searching — RRF results ready
            emit(job_id, "query.searching", {"fused_count": len(fused.chunks)}, db, _redis)

            # --------------------------------------------------------------
            # Rerank — Voyage rerank-2 primary, Cohere rerank-english-v3.0 fallback
            # --------------------------------------------------------------
            reranked = rerank(query, fused, strategy)

            log.info(
                "retrieve_and_rank.reranking_complete",
                job_id=job_id,
                reranked_count=len(reranked.chunks),
            )

            # EVENT 4: query.reranking — reranked candidate count
            emit(
                job_id,
                "query.reranking",
                {"reranked_count": len(reranked.chunks)},
                db,
                _redis,
            )

            # --------------------------------------------------------------
            # Build retrieval trace (truncated to 200 chars per candidate)
            # --------------------------------------------------------------
            trace = build_trace(vector_cands, bm25_cands, fused, reranked, max_content=200)

            payload = {
                "query": query,
                # emit() writes this to job_events as JSON, so the wire form.
                "results": reranked.to_json()["chunks"],
                "trace": trace,
                "strategy_used": strategy.model_dump(),
            }

            # EVENT 5: query.complete — final payload persisted to job_events
            emit(job_id, "query.complete", payload, db, _redis)

            # Mark job complete
            job.status = "complete"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()

            log.info(
                "retrieve_and_rank.complete",
                job_id=job_id,
                agent_id=agent_id,
                reranked_count=len(reranked.chunks),
            )

        except Exception as exc:
            log.error(
                "retrieve_and_rank.failed",
                job_id=job_id,
                agent_id=agent_id,
                error=str(exc),
            )

            # On final retry exhaustion, mark job failed and emit failure event
            if self.request.retries >= self.max_retries:
                try:
                    with get_sync_db() as db2:
                        job2 = db2.get(Job, job_id)
                        if job2:
                            job2.status = "failed"
                            job2.finished_at = datetime.now(timezone.utc)
                            db2.commit()
                            emit(
                                job_id,
                                "query.failed",
                                {"error": str(exc)},
                                db2,
                                _redis,
                            )
                except Exception:
                    pass
            else:
                # Rate-limit errors need at least 30s; other errors use exponential backoff.
                is_rate_limit = any(k in str(exc).lower() for k in ("rate limit", "rate_limit", "429", "rpm", "tpm"))
                countdown = 30 if is_rate_limit else 2 ** self.request.retries
                raise self.retry(exc=exc, countdown=countdown)

    return {}
