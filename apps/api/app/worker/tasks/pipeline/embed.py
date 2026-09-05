"""
embed_and_migrate — Celery task: Voyage embedding + HNSW upsert + REINDEX.

Position in the ingestion chain (4th of 6):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate
    → synthesize_retrieval_strategy → finish_ingestion

After it succeeds the tenant DB is ready for retrieval. It does NOT end the run:
`job.complete` and the terminal `jobs` row belong to finish_ingestion (#168).

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

Important: agent.status is NOT modified by the ingestion chain. This is different
from migrations.py, which sets agent.status='ready'; that is an M1-only behaviour
and the ingestion chain assumes the agent is already 'ready'.

Connection string security (CLAUDE.md non-negotiable rule):
    The tenant DB connection string is NEVER in the task arguments. The IngestionJob
    from generate_metadata carries the four ids and has no field for anything else.
    The connection string is fetched from the control DB by agent_id and decrypted
    with fernet_decrypt() at runtime.

Return value (chain contract):
    The same IngestionJob it received. job_in_job_out (chain_edge.py) builds the job
    from the wire dict at the task's edge and sends the returned job back out as one,
    so Celery still carries the four keys as JSON and the body works in the type.
    A dict the job cannot be built from is logged as embed_and_migrate.invalid_result_dict
    and returned unchanged, which is what the `or` chain here used to do.

    Two names that used to be one: `job` is the IngestionJob the chain carries, and
    `job_row` is the control DB Job row, read here only to confirm the run exists.

Threat mitigations (T-02-05):
    T-02-05-01: Task signature is (self, job: IngestionJob); VOYAGE_API_KEY read from
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


import psycopg2
import psycopg2.extensions
import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.redis_tls import redis_ssl_kwargs
from app.core.security import fernet_decrypt, require_ciphertext
from app.domain.ingestion_job import IngestionJob
from app.models.agent import Agent
from app.models.job import Job
from app.services.embedding_service import EMBEDDING_MODEL, embed_chunks
from app.services.events import emit
from app.services.job_failure import retry_or_fail_the_job
from app.worker.celery_app import celery_app
from app.worker.tasks.pipeline.chain_edge import job_in_job_out

log = structlog.get_logger(__name__)

# Module-level sync Redis client. Strip the query string, then redis_ssl_kwargs decides TLS.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = redis_ssl_kwargs(_url_clean)
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
@job_in_job_out
def embed_and_migrate(self, job: IngestionJob) -> IngestionJob:
    """Embed chunks, upsert into the tenant embeddings table, REINDEX HNSW.

    This is the fourth of the six tasks in the ingestion chain. It:
    1. Fetches only unembedded chunks per document (LEFT JOIN WHERE NULL — Layer 4 read guard).
    2. Calls embed_chunks() to batch-embed with Voyage AI (128 items/request, voyage-3).
    3. Upserts each vector via INSERT ... ON CONFLICT (chunk_id) DO UPDATE.
    4. Runs REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx in AUTOCOMMIT mode.
    5. Emits ingestion.complete, its own step event. The run's terminal event
       belongs to finish_ingestion, two hops later (#168).

    Args:
        job: The IngestionJob generate_metadata forwarded. Connection strings are
             NEVER on it; the type has no field for one (CLAUDE.md rule 1).

    Returns:
        The same job, handed on to synthesize_retrieval_strategy.
        job_in_job_out converts both ends, so the broker still sees a dict.
    """
    agent_id = job.agent_id
    job_id = job.job_id
    document_ids = job.document_ids

    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Fetch agent from control DB — needed for tenant connection string
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("embed_and_migrate.agent_not_found", agent_id=agent_id)
            return job

        # ------------------------------------------------------------------
        # Fetch job from control DB. A run with no row is a run to abandon
        # ------------------------------------------------------------------
        job_row = db.get(Job, job_id)
        if job_row is None:
            log.info("embed_and_migrate.job_not_found", job_id=job_id)
            return job

        # ------------------------------------------------------------------
        # Decrypt POOLED connection string — DML only, pooled URI is correct
        # (T-02-05-01: conn_str never logged)
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

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
                log_failure(log, "embed_and_migrate.reindex_skipped", reindex_exc, reason=type(reindex_exc).__name__)
            finally:
                reindex_conn.close()

            log.info(
                "embed_and_migrate.reindex_complete",
                total_chunks_embedded=total_chunks_embedded,
            )

            # ------------------------------------------------------------------
            # This hop's own step event, and nothing terminal (#168).
            # `job.complete` and the `jobs` row belong to finish_ingestion, the
            # hop appended for the purpose. This task stopped being last when
            # synthesize_retrieval_strategy joined the chain, and a subscriber
            # that closes on job.complete was closing one hop early.
            # ------------------------------------------------------------------
            emit(
                job_id,
                "ingestion.complete",
                {"job_id": job_id, "total_chunks": total_chunks_embedded},
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

            log_failure(
                log, "embed_and_migrate.unexpected_error", exc, level="error",
                total_chunks_embedded=total_chunks_embedded,
            )

            # On final retry exhaustion, mark job failed and RE-RAISE so Celery
            # records the task as FAILURE. This branch once swallowed the
            # exception and fell through to `return {...success dict...}`,
            # producing a phantom success: the embed step would "succeed" with
            # 0 chunks embedded (e.g. VOYAGE_API_KEY AuthenticationError exhausting
            # tenacity retries → RetryError). The job must be marked failed, not
            # silently completed. The shape embed spelled here is now the shape
            # every pipeline task shares (#63).
            retry_or_fail_the_job(self, exc, job_id, db, _redis, 2**self.request.retries)

        finally:
            tenant_conn.close()

    return job
