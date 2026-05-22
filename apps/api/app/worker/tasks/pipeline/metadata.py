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

Concurrency design (Haiku fan-out):
    Haiku metadata extraction is the pipeline's slowest step (~8-10s per chunk).
    Serialized, 50 chunks take ~7.7 minutes. enrich_chunk() is a synchronous
    httpx/anthropic call with no shared mutable state, so it is safe to run in
    threads. A ThreadPoolExecutor with MAX_CONCURRENT_HAIKU=5 workers fans the
    Haiku calls out concurrently, cutting 50 chunks from ~7.7 min to ~90s while
    staying well under Anthropic rate limits.

    DB writes are NOT parallelized. psycopg2 connections and cursors are not
    thread-safe — sharing one across threads corrupts the protocol state. Only
    the network-bound enrich_chunk() runs in worker threads; every DB write
    (chunk_metadata UPSERT, entities UPSERT, chunk_entities link) and the
    per-chunk commit happen on the main thread as as_completed() yields results.
    This preserves the per-chunk commit granularity that backs Layer 3 retry
    safety.

    Partial-failure tolerance: a single chunk's enrich_chunk() raising (e.g. a
    fatal ValidationError after retries) is logged and skipped — it does not
    abort the whole document. The chunk simply has no metadata row, and a future
    re-run will re-attempt it (Layer 3 skips only chunks that DID succeed).

Event emission order (CONTEXT.md §SSE Event Vocabulary):
    metadata.started  ← emitted per document (before per-chunk loop)
    metadata.progress ← emitted every 5 processed chunks ({processed, total})
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
import structlog
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import redis as redis_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.events import emit
from app.services.metadata_service import enrich_chunk
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Max concurrent Haiku calls. enrich_chunk() is network-bound (httpx/anthropic);
# 5 threads fans out the slow API calls while staying well under Anthropic rate
# limits. DB writes remain serial on the main thread (psycopg2 is not thread-safe).
MAX_CONCURRENT_HAIKU = 5

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
    once per unprocessed chunk, UPSERTs chunk_metadata, UPSERTs entities by
    (normalized, type), and links chunk_entities.

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
                # Pass 2 (parallel Haiku, serial DB): fan out enrich_chunk()
                # across MAX_CONCURRENT_HAIKU threads. As each future completes,
                # write its result to the DB on THIS (main) thread and commit.
                #
                # Only the network-bound Haiku call is parallelized. All DB
                # access stays on the main thread because the single psycopg2
                # connection/cursor is not thread-safe (T-02-04-06: content
                # never logged; only chunk_id/document_id).
                # ----------------------------------------------------------
                processed = 0
                if pending:
                    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_HAIKU) as executor:
                        # enrich_chunk(content) — same Haiku call as before
                        futures = {
                            executor.submit(enrich_chunk, content): (chunk_id, content)
                            for chunk_id, content in pending
                        }
                        for future in as_completed(futures):
                            chunk_id, _content = futures[future]
                            try:
                                meta = future.result()
                            except Exception as enrich_exc:
                                # Partial-failure tolerance: one chunk's failure
                                # (e.g. fatal ValidationError after retries) must
                                # NOT abort the whole document. Log and skip — a
                                # future re-run re-attempts this chunk (Layer 3
                                # only skips chunks that succeeded).
                                log.warning(
                                    "generate_metadata.metadata_extraction_failed",
                                    chunk_id=str(chunk_id),
                                    error=str(enrich_exc),
                                )
                                continue

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
                            # (Layer 3 design). If the worker is killed after commit,
                            # the SELECT COUNT(*) guard on the next retry skips this chunk.
                            tenant_conn.commit()

                            # Per-chunk progress: emit every 5 processed chunks so the
                            # admin UI shows movement during long documents.
                            processed += 1
                            if processed % 5 == 0:
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
