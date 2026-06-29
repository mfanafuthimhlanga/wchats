"""
embed_and_migrate — Celery task: Voyage embedding + HNSW upsert + REINDEX.

Position in M2 chain (4th and final of 4):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate

This is the terminal task in the ingestion chain. After it succeeds, the
job status is set to 'complete' and the tenant DB is ready for M3 retrieval.

Layer 4 idempotency mechanism:
    Two-level idempotency protects against task retry:
    1. READ-LEVEL: SELECT chunks LEFT JOIN embeddings WHERE e.chunk_id IS NULL
       fetches ONLY chunks that do not yet have an embedding row. On re-run,
       already-embedded chunks produce an empty result set for that document.
    2. WRITE-LEVEL: INSERT INTO embeddings ... ON CONFLICT (chunk_id) DO UPDATE
       ensures that even if a re-run somehow arrives at the INSERT, the upsert
       is safe — no duplicate rows, just an overwrite of the same vector.

REINDEX CONCURRENTLY isolation requirement (PITFALLS.md §5):
    REINDEX CONCURRENTLY cannot run inside a transaction block. The statement
    must be issued on a connection with ISOLATION_LEVEL_AUTOCOMMIT set BEFORE
    the cursor.execute() call. A separate psycopg2 connection is opened solely
    for this statement to avoid inadvertently affecting the DML connection's
    transaction state.

Terminal event order (CONTEXT.md §SSE Event Vocabulary):
    embedding.started  ← emitted per document (before embed call)
    embedding.complete ← emitted per document (after upsert commit)
    ingestion.complete ← emitted once (after all documents processed + REINDEX)
    job.complete       ← emitted once (after job.status = 'complete' is written)

Important: agent.status is NOT modified in M2. Only job.status moves to
'complete'. This is different from migrations.py which sets agent.status='ready'
— that is an M1-only behaviour. M2 assumes agent is already 'ready'.

Connection string security (CLAUDE.md non-negotiable rule):
    The tenant DB connection string is NEVER in the task arguments. The result dict
    from generate_metadata carries only {tenant_id, agent_id, job_id, document_ids}.
    The connection string is fetched from the control DB by agent_id and decrypted
    with fernet_decrypt() at runtime.

Threat mitigations (T-02-05):
    T-02-05-01: Task signature is (self, result: dict); VOYAGE_API_KEY read from
                env at module init by voyageai.Client() in embedding_service.
    T-02-05-02: EMBEDDING_MODEL = "voyage-3" is a pinned module constant; prevents
                drift. embeddings.model column stores the model string for auditability.
    T-02-05-03: tenacity wait_exponential(min=2, max=30) + stop_after_attempt(5) in
                embedding_service._embed_batch caps retry cost.
    T-02-05-04: ON CONFLICT (chunk_id) DO UPDATE + LEFT JOIN WHERE NULL read guard —
                re-runs produce zero duplicate rows.
    T-02-05-06: log calls reference chunk counts and document_ids ONLY — never chunk
                content, never vector values, never Voyage response body.
"""

import ssl
import structlog
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extensions
import redis as redis_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.embedding_service import embed_chunks, EMBEDDING_MODEL
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client — strip query params; pass ssl_cert_reqs as constant.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def embed_and_migrate(self, result: dict) -> dict:
    """Embed chunks, upsert into tenant embeddings table, REINDEX HNSW, emit terminal events.

    This is the fourth and final task in the M2 ingestion chain. It:
    1. Fetches only unembedded chunks per document (LEFT JOIN WHERE NULL — Layer 4 read guard).
    2. Calls embed_chunks() to batch-embed with Voyage AI (128 items/request, voyage-3).
    3. Upserts each vector via INSERT ... ON CONFLICT (chunk_id) DO UPDATE.
    4. Runs REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx in AUTOCOMMIT mode.
    5. Emits terminal SSE events and sets job.status = 'complete'.

    Args:
        result: Return value from generate_metadata —
                {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}.
                Connection strings are NEVER in this dict (CLAUDE.md non-negotiable rule).

    Returns:
        {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
        Chain-forwarding format (no connection strings).
    """
    # ------------------------------------------------------------------
    # Extract result dict keys — defensive validation
    # ------------------------------------------------------------------
    tenant_id = result.get("tenant_id")
    agent_id = result.get("agent_id")
    job_id = result.get("job_id")
    document_ids = result.get("document_ids")

    if not all([tenant_id, agent_id, job_id, document_ids is not None]):
        log.error(
            "embed_and_migrate.invalid_result_dict",
            keys=list(result.keys()),
        )
        return result

    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Fetch agent from control DB — needed for tenant connection string
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("embed_and_migrate.agent_not_found", agent_id=agent_id)
            return {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "job_id": job_id,
                "document_ids": document_ids,
            }

        # ------------------------------------------------------------------
        # Fetch job from control DB — idempotent exit if already complete
        # ------------------------------------------------------------------
        job = db.get(Job, job_id)
        if job is None:
            log.info("embed_and_migrate.job_not_found", job_id=job_id)
            return {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "job_id": job_id,
                "document_ids": document_ids,
            }

        # ------------------------------------------------------------------
        # Decrypt POOLED connection string — DML only, pooled URI is correct
        # (T-02-05-01: conn_str never logged)
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(agent.neon_connection_string)

        # ------------------------------------------------------------------
        # Open tenant DB connection for DML writes
        # ------------------------------------------------------------------
        tenant_conn = psycopg2.connect(conn_str)
        total_chunks_embedded = 0

        try:
            for doc_id in document_ids:
                # ----------------------------------------------------------
                # Layer 4 read-level idempotency:
                # Fetch only chunks that do NOT yet have an embedding row.
                # On re-run, already-embedded chunks are skipped entirely.
                # ----------------------------------------------------------
                with tenant_conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT c.id, c.content
                        FROM chunks c
                        LEFT JOIN embeddings e ON e.chunk_id = c.id
                        WHERE c.document_id = %s
                          AND e.chunk_id IS NULL
                        ORDER BY c.ordinal
                        """,
                        (doc_id,),
                    )
                    rows = cur.fetchall()

                chunk_count = len(rows)

                if chunk_count == 0:
                    log.info(
                        "embed_and_migrate.no_pending_chunks",
                        document_id=doc_id,
                    )
                    # Emit events even on re-run to maintain SSE consistency
                    emit(
                        job_id,
                        "embedding.started",
                        {"document_id": doc_id, "chunk_count": 0},
                        db,
                        _redis,
                    )
                    emit(
                        job_id,
                        "embedding.complete",
                        {"document_id": doc_id},
                        db,
                        _redis,
                    )
                    continue

                # Emit embedding.started for this document
                emit(
                    job_id,
                    "embedding.started",
                    {"document_id": doc_id, "chunk_count": chunk_count},
                    db,
                    _redis,
                )

                chunk_ids = [row[0] for row in rows]
                texts = [row[1] for row in rows]

                # Call embedding service (batches at 128, tenacity retry)
                embeddings = embed_chunks(texts)

                # ----------------------------------------------------------
                # Layer 4 write-level idempotency:
                # INSERT ... ON CONFLICT (chunk_id) DO UPDATE
                # Safe to re-run — no duplicate rows, just vector overwrite.
                # ----------------------------------------------------------
                with tenant_conn.cursor() as cur:
                    for chunk_id, vec in zip(chunk_ids, embeddings, strict=True):
                        cur.execute(
                            """
                            INSERT INTO embeddings (chunk_id, model, vector)
                            VALUES (%s, %s, %s::vector)
                            ON CONFLICT (chunk_id) DO UPDATE
                                SET model      = EXCLUDED.model,
                                    vector     = EXCLUDED.vector,
                                    created_at = now()
                            """,
                            (str(chunk_id), EMBEDDING_MODEL, str(vec)),
                        )
                    tenant_conn.commit()

                # Emit embedding.complete for this document
                emit(
                    job_id,
                    "embedding.complete",
                    {"document_id": doc_id},
                    db,
                    _redis,
                )

                total_chunks_embedded += chunk_count
                # Note: S3 source bytes are intentionally NOT deleted here.
                # Retaining the upload object in S3 allows safe idempotent
                # re-ingestion (e.g., backfill re-embed in 13-04) without
                # needing to re-upload.  Documents deleted from the DB have
                # their S3 object cleaned up by delete_document in documents.py.
                # (Landmine 4 fix: the former hardcoded local-path cleanup block
                # used a literal constant and is removed — it was a no-op once
                # S3 replaced local disk for uploads per PROD-12, PROD-13.)

            # ------------------------------------------------------------------
            # REINDEX CONCURRENTLY — must run outside any transaction block.
            # (PITFALLS.md §5: REINDEX CONCURRENTLY cannot run inside a
            #  transaction block — "REINDEX CONCURRENTLY cannot run inside a
            #  transaction block" is a Postgres hard error)
            #
            # A separate connection with ISOLATION_LEVEL_AUTOCOMMIT is opened
            # solely for this statement to avoid affecting the DML connection.
            # ------------------------------------------------------------------
            # IMPORTANT: Do NOT use `with psycopg2.connect(...) as conn:` here.
            # When a psycopg2 Connection is used as a context manager it implicitly
            # starts a transaction context. set_isolation_level() cannot switch to
            # AUTOCOMMIT while inside an active transaction block — Postgres raises
            # "REINDEX CONCURRENTLY cannot run inside a transaction block".
            # Instead: create the connection without context manager, set autocommit
            # BEFORE any statement, then close explicitly in a finally block.
            reindex_conn = psycopg2.connect(conn_str)
            try:
                reindex_conn.set_isolation_level(
                    psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
                )
                with reindex_conn.cursor() as cur:
                    cur.execute("REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx")
            except Exception as reindex_exc:
                # Best-effort REINDEX: log but do NOT fail the chain.
                # REINDEX is a performance optimization (prevents HNSW bloat);
                # the data is already committed. A failed REINDEX never loses data.
                # Common reasons for failure:
                #   - Index does not exist yet (first ingestion before HNSW created)
                #   - pgvector extension not installed (test environments without pgvector)
                #   - Transient Postgres error (retries will attempt REINDEX again)
                log.warning(
                    "embed_and_migrate.reindex_skipped",
                    reason=type(reindex_exc).__name__,
                    error=str(reindex_exc),
                )
            finally:
                reindex_conn.close()

            log.info(
                "embed_and_migrate.reindex_complete",
                total_chunks_embedded=total_chunks_embedded,
            )

            # ------------------------------------------------------------------
            # Emit terminal events and mark job complete
            # (mirrors migrations.py lines 186-200)
            # ------------------------------------------------------------------
            emit(
                job_id,
                "ingestion.complete",
                {"job_id": job_id, "total_chunks": total_chunks_embedded},
                db,
                _redis,
            )

            # Update job row — agent.status is NOT touched in M2 (M1-only behaviour)
            job.status = "complete"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()

            emit(
                job_id,
                "job.complete",
                {"job_id": job_id},
                db,
                _redis,
            )

            log.info(
                "embed_and_migrate.complete",
                job_id=job_id,
                total_chunks_embedded=total_chunks_embedded,
            )

        except Exception as exc:
            # Best-effort rollback on unexpected error
            try:
                tenant_conn.rollback()
            except Exception:
                pass

            log.error(
                "embed_and_migrate.unexpected_error",
                error_type=type(exc).__name__,
                error=str(exc),
                total_chunks_embedded=total_chunks_embedded,
            )

            # On final retry exhaustion, mark job failed and RE-RAISE so Celery
            # records the task as FAILURE. Previously this branch swallowed the
            # exception and fell through to `return {...success dict...}`,
            # producing a phantom success: the embed step would "succeed" with
            # 0 chunks embedded (e.g. VOYAGE_API_KEY AuthenticationError exhausting
            # tenacity retries → RetryError). The job must be marked failed, not
            # silently completed.
            if self.request.retries >= self.max_retries:
                try:
                    job.status = "failed"
                    job.error = str(exc)
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    emit(job_id, "job.failed", {"error": str(exc)}, db, _redis)
                except Exception:
                    pass
                # Re-raise the original error: Celery marks the task FAILURE and
                # the chain stops here rather than forwarding a success dict.
                raise
            else:
                raise self.retry(exc=exc, countdown=2**self.request.retries)

        finally:
            tenant_conn.close()

    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "job_id": job_id,
        "document_ids": document_ids,
    }
