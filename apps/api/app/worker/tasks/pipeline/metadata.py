"""
generate_metadata — Celery task: Haiku metadata + entity extraction per chunk.

Position in M2 chain (3rd of 4):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate

Layer 3 idempotency (SELECT COUNT(*) pre-check):
    Before calling Haiku for a chunk, the task checks whether a chunk_metadata row
    already exists for that chunk_id. If it does, the chunk is skipped — no Haiku
    call, no re-billing. This is Layer 3 of the 4-layer idempotency contract.

    Design rationale: chunk_metadata is populated per-chunk. On Celery retry (e.g.
    worker kill mid-document), previously processed chunks are cheaply skipped via
    this SELECT guard while the task picks up from the first unprocessed chunk.

    Idempotency commit granularity: tenant_conn.commit() is called per-chunk (not
    per-document) so that partial progress is preserved on retry.

Entity deduplication design:
    Entities are deduplicated via UPSERT on UNIQUE(normalized, type) on the entities
    table. Same entity (same normalized form + type) appearing across multiple chunks
    results in a single entities row and N chunk_entities rows.

    The chunk_entities link uses INSERT ... ON CONFLICT DO NOTHING to prevent
    duplicate (chunk_id, entity_id) pairs from being created on retry.

Connection string security (CLAUDE.md non-negotiable rule):
    The tenant DB connection string is NEVER in the task arguments. The result dict
    from chunk_documents carries only {tenant_id, agent_id, job_id, document_ids}.
    The connection string is fetched from the control DB by agent_id and decrypted
    with fernet_decrypt() at runtime.

Cost design (batched Haiku calls):
    Haiku metadata extraction is the pipeline's slowest and most billed step.
    enrich_chunks_batch() packs BATCH_SIZE (10) chunks into a single Haiku call,
    cutting 50 chunks from 50 calls (~$0.08/doc) to 5 calls (~$0.008/doc) — a
    ~10x cost reduction. Chunks are numbered by position in the batch and the
    model returns per-chunk results in submission order; the task zips results
    back to chunk_ids by index, NOT by ID.

    DB writes stay serial on the main thread. psycopg2 connections and cursors
    are not thread-safe, and the per-chunk commit granularity backs Layer 3
    retry safety: each chunk's metadata + entities are written and committed
    individually so a worker kill mid-batch leaves already-written chunks
    skippable by the SELECT COUNT(*) guard on retry.

    Partial-failure tolerance: a single batch's enrich_chunks_batch() raising
    (e.g. a fatal ValidationError or a size mismatch) is logged and the batch is
    skipped — it does not abort the whole document. Those chunks simply have no
    metadata row, and a future re-run re-attempts them (Layer 3 skips only chunks
    that DID succeed).

Event emission order (CONTEXT.md §SSE Event Vocabulary):
    metadata.started  ← emitted per document (before the batch loop)
    metadata.progress ← emitted after each batch ({processed, total})
    metadata.complete ← emitted per document (after all chunks processed)

Return value (chain contract):
    {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
    Connection strings are NEVER returned.

Threat mitigations (T-02-04):
    T-02-04-01: Task signature is (self, result: dict); ANTHROPIC_API_KEY read from
                env at module init by anthropic.Anthropic() in metadata_service.
    T-02-04-02: Haiku response validated by Pydantic via client.messages.parse();
                malformed responses raise ValidationError and the chunk is skipped.
                Literal entity types prevent arbitrary type injection.
    T-02-04-03: Layer 3 idempotency guard (SELECT COUNT(*) FROM chunk_metadata)
                prevents re-billing on retry.
    T-02-04-06: structlog calls log chunk_id and document_id ONLY — never chunk
                content or Haiku response bodies.
"""

import ssl

import psycopg2
import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.events import emit
from app.services.metadata_service import BATCH_SIZE, enrich_chunks_batch
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
def generate_metadata(self, result: dict) -> dict:
    """Enrich chunks with Haiku metadata + entities; link via chunk_entities.

    Receives the result dict from chunk_documents, iterates all chunks per document,
    applies Layer 3 idempotency (skip chunks already in chunk_metadata), calls Haiku
    once per batch of BATCH_SIZE unprocessed chunks, UPSERTs chunk_metadata, UPSERTs
    entities by (normalized, type), and links chunk_entities.

    Args:
        result: Return value from chunk_documents —
                {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}.
                Connection strings are NEVER in this dict (CLAUDE.md non-negotiable rule).

    Returns:
        {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
        Forwarded unchanged to embed_and_migrate in the Celery chain.
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
            "generate_metadata.invalid_result_dict",
            keys=list(result.keys()),
        )
        return result

    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Fetch agent from control DB — needed for tenant connection string
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("generate_metadata.agent_not_found", agent_id=agent_id)
            return {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "job_id": job_id,
                "document_ids": document_ids,
            }

        # ------------------------------------------------------------------
        # Decrypt POOLED connection string — DML only, pooled URI is correct
        # (T-02-04-01: conn_str never logged)
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(agent.neon_connection_string)

        # ------------------------------------------------------------------
        # Open tenant DB connection for DML writes
        # ------------------------------------------------------------------
        tenant_conn = psycopg2.connect(conn_str)
        try:
            for doc_id in document_ids:
                # ----------------------------------------------------------
                # Fetch all chunks for this document in ordinal order
                # ----------------------------------------------------------
                with tenant_conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, content FROM chunks WHERE document_id = %s ORDER BY ordinal",
                        (doc_id,),
                    )
                    chunk_rows = cur.fetchall()

                chunk_count = len(chunk_rows)

                # Emit metadata.started for this document
                emit(
                    job_id,
                    "metadata.started",
                    {"document_id": doc_id, "chunk_count": chunk_count},
                    db,
                    _redis,
                )

                # ----------------------------------------------------------
                # Pass 1 (serial, main thread): Layer 3 idempotency pre-check.
                # SELECT COUNT(*) FROM chunk_metadata for each chunk to decide
                # which chunks need a Haiku call. Already-enriched chunks are
                # skipped here (no Haiku call, no re-billing — T-02-04-03).
                # This must stay on the main thread: psycopg2 is not thread-safe.
                # ----------------------------------------------------------
                pending: list[tuple] = []  # (chunk_id, content) needing enrichment
                for chunk_id, content in chunk_rows:
                    with tenant_conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = %s",
                            (chunk_id,),
                        )
                        if cur.fetchone()[0] > 0:
                            log.info(
                                "generate_metadata.already_enriched",
                                chunk_id=str(chunk_id),
                            )
                            continue
                    pending.append((chunk_id, content))

                # ----------------------------------------------------------
                # Pass 2: batch Haiku calls (BATCH_SIZE chunks per call) then
                # serial DB writes. Each batch is one Haiku call returning
                # per-chunk results in submission order; results are zipped back
                # to chunk_ids by index (NOT by ID — the model has no IDs).
                #
                # All DB access stays on the main thread because the single
                # psycopg2 connection/cursor is not thread-safe (T-02-04-06:
                # content never logged; only chunk_id/document_id). Per-chunk
                # commit preserves Layer 3 retry granularity.
                # ----------------------------------------------------------
                processed = 0
                for batch_start in range(0, len(pending), BATCH_SIZE):
                    batch = pending[batch_start : batch_start + BATCH_SIZE]
                    batch_texts = [content for _, content in batch]

                    try:
                        results = enrich_chunks_batch(batch_texts)
                    except Exception as exc:
                        # Partial-failure tolerance: one batch's failure (fatal
                        # ValidationError after retries, size mismatch, etc.) must
                        # NOT abort the whole document. Log and skip — a future
                        # re-run re-attempts these chunks (Layer 3 only skips
                        # chunks that succeeded).
                        log.warning(
                            "generate_metadata.batch_extraction_failed",
                            batch_start=batch_start,
                            batch_size=len(batch),
                            error=str(exc),
                        )
                        continue  # skip this batch, don't abort the document

                    for (chunk_id, _content), meta in zip(batch, results):
                        with tenant_conn.cursor() as cur:
                            # ------------------------------------------
                            # UPSERT chunk_metadata (ON CONFLICT (chunk_id) DO UPDATE)
                            # ------------------------------------------
                            cur.execute(
                                """
                                INSERT INTO chunk_metadata (chunk_id, summary, keywords, questions)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (chunk_id) DO UPDATE
                                    SET summary   = EXCLUDED.summary,
                                        keywords  = EXCLUDED.keywords,
                                        questions = EXCLUDED.questions
                                """,
                                (chunk_id, meta.summary, meta.keywords, meta.questions),
                            )

                            # ------------------------------------------
                            # UPSERT entities + link chunk_entities
                            #
                            # Entity dedup: ON CONFLICT (normalized, type) DO UPDATE
                            # SET name so the human-readable name reflects the most
                            # recent occurrence; the canonical normalized form is the
                            # dedup key (T-02-04-02).
                            #
                            # chunk_entities link: ON CONFLICT DO NOTHING prevents
                            # duplicate (chunk_id, entity_id) pairs on retry.
                            # ------------------------------------------
                            for entity in meta.entities:
                                cur.execute(
                                    """
                                    INSERT INTO entities (name, type, normalized)
                                    VALUES (%s, %s, %s)
                                    ON CONFLICT (normalized, type) DO UPDATE
                                        SET name = EXCLUDED.name
                                    RETURNING id
                                    """,
                                    (entity.name, entity.type, entity.normalized),
                                )
                                entity_id = cur.fetchone()[0]
                                cur.execute(
                                    """
                                    INSERT INTO chunk_entities (chunk_id, entity_id)
                                    VALUES (%s, %s)
                                    ON CONFLICT DO NOTHING
                                    """,
                                    (chunk_id, entity_id),
                                )

                        # Commit per-chunk: partial-progress safety on retry
                        # (Layer 3 design). If the worker is killed mid-batch,
                        # the SELECT COUNT(*) guard on the next retry skips
                        # already-committed chunks.
                        tenant_conn.commit()
                        processed += 1

                    # Emit progress after each batch (each batch IS ~BATCH_SIZE chunks).
                    emit(
                        job_id,
                        "metadata.progress",
                        {"processed": processed, "total": len(pending)},
                        db,
                        _redis,
                    )

                # Emit metadata.complete for this document
                emit(
                    job_id,
                    "metadata.complete",
                    {"document_id": doc_id},
                    db,
                    _redis,
                )

                log.info(
                    "generate_metadata.complete",
                    document_id=doc_id,
                    chunks_enriched=processed,
                )

        except Exception as exc:
            # Best-effort rollback on unexpected error
            try:
                tenant_conn.rollback()
            except Exception:
                pass
            log.error(
                "generate_metadata.unexpected_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise self.retry(exc=exc, countdown=2**self.request.retries)
        finally:
            tenant_conn.close()

    # T-02-04-01: Return only chain-forwarding keys — no connection string
    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "job_id": job_id,
        "document_ids": document_ids,
    }
