"""
reembed_corpus — Celery task: one-time per-tenant re-embed / backfill to Bedrock Titan v2.

PROD-06 Wave 1: Realigns the existing Voyage-embedded corpus onto Amazon Bedrock
Titan Text Embeddings v2 so query and document vectors share one embedding space.

Without this backfill, the 13-02 Bedrock query embedder computes cosine similarity
against Voyage-embedded document vectors — meaningless results, silently wrong
retrieval (Pitfall 3 in RESEARCH.md). The backfill upserts Bedrock vectors over the
same `embeddings.vector VECTOR(1024)` rows (Titan v2 @1024 matches; no schema change).

Idempotency (T-13-04-02):
    Model-id filter: SELECT ... WHERE embeddings.model IS DISTINCT FROM target_model.
    Only chunks whose embedding was produced by a different model (or have no
    embedding row at all — NULL IS DISTINCT FROM any value is TRUE) are re-embedded.
    After a full migration, a re-run queries zero rows and returns immediately.
    ON CONFLICT (chunk_id) DO UPDATE provides write-level safety even on retry.

Resumability:
    Each batch is committed before the next batch is fetched. A mid-run kill
    leaves committed batches migrated. On resume, the model-id filter skips them —
    the task picks up exactly where the last successful commit left off.

Tenant isolation (T-13-04-01):
    Receives agent_id only. Decrypts connection strings from the control DB at
    runtime. Never iterates other agents or tenants. Callers dispatch one task
    per tenant.

Retry exhaustion (#63):
    The seven job-scoped pipeline tasks route their last failed attempt through
    app/services/job_failure.py, which writes the jobs row and emits job.failed.
    This task is not job-scoped — it takes an agent_id and nothing else, so there
    is no jobs row to mark and no job_events channel to publish on. A bare
    re-raise is everything its caller can observe, and that is why it has none.

REINDEX endpoint requirement (Pitfall 7, T-13-04-04):
    REINDEX CONCURRENTLY cannot run inside a transaction block and behaves
    incorrectly through PgBouncer transaction mode (the pooled endpoint).
    A separate connection from neon_direct_connection_string with
    ISOLATION_LEVEL_AUTOCOMMIT is used solely for the REINDEX statement.
    Best-effort: failure is logged but never re-raised.

Connection string security (CLAUDE.md rule 4):
    neon_connection_string and neon_direct_connection_string are decrypted at
    runtime from the control DB. They are NEVER in task arguments and NEVER
    written to logs (T-13-04-03).

Queue: pipeline (declared on the decorator; pipeline.* routing rule in celery_app.py
already routes this module to the pipeline queue).
"""

import psycopg2
import psycopg2.extensions
import structlog

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.services import bedrock_embedding_service
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Batch size: small enough that each commit completes quickly (resumability),
# large enough to amortise Bedrock per-call overhead.
_BATCH_SIZE = 50


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=10,
    queue="pipeline",
    name="reembed_corpus",
)
def reembed_corpus(self, agent_id: str) -> dict:
    """Re-embed a single tenant's chunks onto the active Bedrock embedding model.

    Selects all chunks whose embeddings.model IS DISTINCT FROM the active Bedrock
    model id (includes chunks with no embedding row — NULL IS DISTINCT FROM target
    is TRUE), re-embeds them in batches using
    bedrock_embedding_service.embed_texts(texts, "document"), and upserts each via
    INSERT ... ON CONFLICT (chunk_id) DO UPDATE SET model=..., vector=..., created_at=now().

    Each batch is committed before fetching the next, making the task resumable:
    a kill mid-run leaves committed batches migrated; the model-id filter skips them
    on the next run.

    After all batches, runs REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx on
    a separate connection from neon_direct_connection_string with
    ISOLATION_LEVEL_AUTOCOMMIT (never the pooled endpoint — Pitfall 7). REINDEX is
    best-effort: failure is logged but never re-raised.

    Args:
        agent_id: UUID string identifying the agent whose corpus to backfill.
                  Connection strings are NEVER in task args (CLAUDE.md rule 4).

    Returns:
        {"agent_id": str, "total_reembedded": int, "model": str}
    """
    target_model = bedrock_embedding_service.active_embedding_model()
    total_reembedded = 0

    # ------------------------------------------------------------------
    # Fetch agent and decrypt both connection strings from the control DB.
    # The SQLAlchemy session is closed immediately after this read — we hold
    # it only for the duration of the Agent lookup.
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("reembed_corpus.agent_not_found", agent_id=agent_id)
            return {"agent_id": agent_id, "total_reembedded": 0, "model": target_model}

        # conn_str and direct_conn_str are NEVER logged (T-13-04-03)
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))           # pooled  → DML
        direct_conn_str = fernet_decrypt(require_ciphertext(agent.neon_direct_connection_string, "agents.neon_direct_connection_string"))  # direct → REINDEX

    # ------------------------------------------------------------------
    # Open ONE pooled connection for all DML (SELECT batches + upserts).
    # Pooled endpoint is correct for transaction-mode DML.
    # ------------------------------------------------------------------
    tenant_conn = psycopg2.connect(conn_str)

    try:
        while True:
            # Model-id filter: fetches only chunks whose embedding is NOT yet
            # produced by target_model. NULL IS DISTINCT FROM target_model → TRUE
            # so chunks with no embedding row are included.
            # ORDER BY c.id is stable; committed batches drop out of the result set
            # naturally (their embeddings.model now equals target_model), so there is
            # no offset-drift: the next SELECT always starts from unmigrated rows.
            with tenant_conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, c.content
                    FROM chunks c
                    LEFT JOIN embeddings e ON e.chunk_id = c.id
                    WHERE e.model IS DISTINCT FROM %s
                    ORDER BY c.id
                    LIMIT %s
                    """,
                    (target_model, _BATCH_SIZE),
                )
                rows = cur.fetchall()

            if not rows:
                # Idempotent exit: all chunks already carry target_model embeddings.
                log.info(
                    "reembed_corpus.no_pending_chunks",
                    agent_id=agent_id,
                    total_reembedded=total_reembedded,
                )
                break

            chunk_ids = [row[0] for row in rows]
            texts = [row[1] for row in rows]

            # Re-embed via Bedrock Titan v2 using the "document" input type.
            # embed_texts raises RuntimeError if dim != 1024 (dim guard in 13-02).
            vectors = bedrock_embedding_service.embed_texts(texts, "document")

            # Write-level idempotency: ON CONFLICT (chunk_id) DO UPDATE.
            # Safe to re-run — no duplicate rows, just model + vector overwrite.
            with tenant_conn.cursor() as cur:
                for chunk_id, vec in zip(chunk_ids, vectors, strict=True):
                    cur.execute(
                        """
                        INSERT INTO embeddings (chunk_id, model, vector)
                        VALUES (%s, %s, %s::vector)
                        ON CONFLICT (chunk_id) DO UPDATE
                            SET model      = EXCLUDED.model,
                                vector     = EXCLUDED.vector,
                                created_at = now()
                        """,
                        (str(chunk_id), target_model, str(vec)),
                    )
                tenant_conn.commit()

            total_reembedded += len(rows)
            log.info(
                "reembed_corpus.batch_committed",
                agent_id=agent_id,
                batch_size=len(rows),
                total_reembedded=total_reembedded,
            )

    except Exception as exc:
        # Best-effort rollback — psycopg2 may raise if the connection is broken.
        try:
            tenant_conn.rollback()
        except Exception:
            pass

        log.error(
            "reembed_corpus.unexpected_error",
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=str(exc),
            total_reembedded=total_reembedded,
            # conn_str is NEVER logged (T-13-04-03)
        )

        if self.request.retries >= self.max_retries:
            raise
        else:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    finally:
        tenant_conn.close()

    # ------------------------------------------------------------------
    # REINDEX on the DIRECT endpoint in AUTOCOMMIT isolation.
    # MUST NOT use the pooled endpoint (PgBouncer transaction mode breaks
    # REINDEX CONCURRENTLY autocommit — Pitfall 7, T-13-04-04).
    # Best-effort: a REINDEX failure never loses committed vectors.
    # ------------------------------------------------------------------
    reindex_conn = psycopg2.connect(direct_conn_str)
    try:
        reindex_conn.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
        )
        with reindex_conn.cursor() as cur:
            cur.execute("REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx")
        log.info(
            "reembed_corpus.reindex_complete",
            agent_id=agent_id,
            total_reembedded=total_reembedded,
        )
    except Exception as reindex_exc:
        # Log and swallow — the data is already committed.
        log.warning(
            "reembed.reindex_skipped",
            agent_id=agent_id,
            reason=type(reindex_exc).__name__,
            error=str(reindex_exc),
        )
    finally:
        reindex_conn.close()

    log.info(
        "reembed_corpus.complete",
        agent_id=agent_id,
        total_reembedded=total_reembedded,
        model=target_model,
    )

    return {
        "agent_id": agent_id,
        "total_reembedded": total_reembedded,
        "model": target_model,
    }
