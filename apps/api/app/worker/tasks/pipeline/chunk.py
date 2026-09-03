"""
chunk_documents — Celery task: chunk parsed documents via HybridChunker + table Markdown path.

Position in M2 chain (2nd of 4):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate

Layer 2 idempotency (ON CONFLICT DO UPDATE):
    Chunk IDs are derived via uuid5(NAMESPACE_URL, f"{document_id}:{ordinal}") — stable
    across reruns for the same document. The INSERT uses ON CONFLICT (id) DO UPDATE so
    that a retry after worker kill overwrites with the same content rather than creating
    duplicate rows. This is Layer 2 of the 4-layer idempotency contract.

Connection string security (CLAUDE.md non-negotiable rule):
    The tenant DB connection string is NEVER in the task arguments. The IngestionJob
    from parse_documents carries the four ids and has no field for anything else. The
    connection string is fetched from the control DB by agent_id and decrypted with
    fernet_decrypt() at runtime — never passed through Celery broker.

Re-parse decision:
    DoclingDocument is not serialised between parse_documents and chunk_documents.
    Re-parsing from the local temp file (or fetching the URL again) is cheaper than
    round-tripping a 100MB+ DoclingDocument object through Redis. Accepted variance:
    non-deterministic at the millisecond level, but HybridChunker is deterministic given
    the same DoclingDocument input. (T-02-03-05 — accepted risk.)

Event emission order (CONTEXT.md §SSE Event Vocabulary):
    chunking.started  ← emitted per document (before chunking)
    chunking.complete ← emitted per document (after successful chunk write)

Return value (chain contract):
    The same IngestionJob it received. job_in_job_out (chain_edge.py) builds the job
    from the wire dict at the task's edge and sends the returned job back out as one,
    so Celery still carries the four keys as JSON and the body works in the type.
    A dict the job cannot be built from is logged as chunk_documents.invalid_result_dict
    and returned unchanged, which is what the `or` chain here used to do.
    Connection strings are NEVER returned.

Threat mitigations:
    T-02-03-01: Every chunk text passes through sanitize_chunk_text() inside
                chunking_service.chunk_document() before any INSERT.
    T-02-03-02: structlog calls reference document_id, chunk_count only — never chunk content
                or connection strings.
    T-02-03-03: uuid5 IDs + ON CONFLICT DO UPDATE — retry-safe upsert; no chunk-row leak.
    T-02-03-04: task signature is (self, job: IngestionJob), carrying no connection
                string and no API key. Verified by
                test_chunk_documents_signature_carries_no_connection_string.
    T-02-03-06: httpx.get(timeout=30) for URL re-fetch — same DoS mitigation as parse_documents.
"""

import ssl
from pathlib import Path

import httpx
import psycopg2
import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt, require_ciphertext
from app.domain.docling_service import parse_document_from_bytes
from app.domain.ingestion_job import IngestionJob
from app.models.agent import Agent
from app.services import storage_service
from app.services.chunking_service import chunk_document
from app.services.events import emit
from app.services.job_failure import retry_or_fail_the_job
from app.worker.celery_app import celery_app
from app.worker.tasks.pipeline.chain_edge import job_in_job_out

log = structlog.get_logger(__name__)

# Module-level sync Redis client — strip query params; pass ssl_cert_reqs as constant.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


# _resolve_local_path() was deleted on 2026-08-13 (BACKLOG 1.26) rather than
# left unused. Its docstring called it "Mirror of parse_documents
# path-resolution" — and it was, until PROD-13 moved parse_documents to S3 and
# left this behind pointing at /vrd-uploads, a path nothing writes. A dead
# helper that names a plausible contract is how the next reader re-adopts it,
# so it is gone, and tests/unit/test_ingestion_reads_from_s3.py pins its
# absence at the level that matters: no pipeline task may reference
# settings.UPLOADS_DIR.


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
@job_in_job_out
def chunk_documents(self, job: IngestionJob) -> IngestionJob:
    """Chunk parsed documents using the two-path strategy (text + table Markdown).

    Receives the job parse_documents built, re-parses each document via Docling,
    splits into chunks via chunking_service.chunk_document(), and UPSERTs each chunk row
    into the tenant chunks table using ON CONFLICT (id) DO UPDATE (Layer 2 idempotency).

    Args:
        job: The IngestionJob the chain carries. Connection strings are NEVER on it
             (CLAUDE.md non-negotiable rule); the type has no field for one.

    Returns:
        The same job, forwarded to generate_metadata in the Celery chain.
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
            log.error("chunk_documents.agent_not_found", agent_id=agent_id)
            return job

        # ------------------------------------------------------------------
        # Decrypt POOLED connection string — DML only, pooled URI is correct
        # (T-02-03-02: conn_str never logged)
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        # ------------------------------------------------------------------
        # Open tenant DB connection for DML writes
        # ------------------------------------------------------------------
        tenant_conn = psycopg2.connect(conn_str)
        try:
            for doc_id in document_ids:
                # --------------------------------------------------------------
                # Query document row from tenant DB
                # --------------------------------------------------------------
                with tenant_conn.cursor() as cur:
                    cur.execute(
                        "SELECT source_uri, source_type, parse_status, chunk_count "
                        "FROM documents WHERE id = %s",
                        (doc_id,),
                    )
                    row = cur.fetchone()

                if row is None:
                    log.warning(
                        "chunk_documents.document_not_found",
                        document_id=doc_id,
                        job_id=job_id,
                    )
                    continue

                source_uri, source_type, parse_status, chunk_count = row

                if parse_status != "parsed":
                    log.warning(
                        "chunk_documents.document_not_parsed",
                        document_id=doc_id,
                        parse_status=parse_status,
                        job_id=job_id,
                    )
                    continue

                # Layer 2 idempotency observation:
                # Even if chunk_count is already set (prior successful run), we still
                # re-write all chunks — deterministic IDs make rewrites cheap and consistent.
                # The ON CONFLICT DO UPDATE ensures no duplicate rows are created.

                # Emit chunking.started for this document
                emit(
                    job_id,
                    "chunking.started",
                    {"document_id": doc_id, "document_count": len(document_ids)},
                    db,
                    _redis,
                )

                # Release the Neon connection before the long Docling re-parse.
                # Re-parsing loads ML models on first call (~2 min) and runs
                # inference (~15 min on CPU); holding an idle Neon serverless
                # connection that long triggers the serverless idle timeout.
                tenant_conn.close()
                tenant_conn = None

                # --------------------------------------------------------------
                # Re-parse document via Docling
                # DoclingDocument is not serialised between tasks (T-02-03-05 accepted).
                # Re-parsing is cheaper than round-tripping a 100MB+ object via Redis.
                # --------------------------------------------------------------
                try:
                    if source_type in ("pdf", "png", "jpg", "jpeg", "md"):
                        # BACKLOG 1.26. This read local disk until 2026-08-13,
                        # via a _resolve_local_path() that mirrored a contract
                        # parse_documents abandoned at PROD-13. Nothing in app/
                        # writes any file to disk any more, so EVERY file-source
                        # document failed here with FileNotFoundError and
                        # retried to exhaustion — in every environment, not just
                        # locally. Only URL sources (the else branch) ever
                        # completed. Read the bytes from S3, exactly as
                        # parse_documents does.
                        #
                        # .lower() matches the WRITER (documents.py:191), which
                        # is the authority on the key. See 1.27: parse.py omits
                        # it, so an uppercase extension 404s there.
                        ext = (Path(source_uri).suffix or f".{source_type}").lower()
                        content = storage_service.get_bytes(
                            storage_service.upload_key(agent_id, doc_id, ext)
                        )
                        doc = parse_document_from_bytes(content, source_uri)
                    else:
                        # URL source — re-fetch bytes and parse
                        resp = httpx.get(source_uri, timeout=30, follow_redirects=True)
                        resp.raise_for_status()
                        doc = parse_document_from_bytes(resp.content, source_uri)

                except RuntimeError as exc:
                    # Fatal Docling error — log and skip this document
                    log.error(
                        "chunk_documents.docling_fatal_error",
                        document_id=doc_id,
                        error=str(exc),
                    )
                    # Reopen connection to continue with next document
                    tenant_conn = psycopg2.connect(conn_str)
                    continue

                except Exception as exc:
                    # Transient error — retry with exponential backoff
                    log.error(
                        "chunk_documents.error",
                        document_id=doc_id,
                        error_type=type(exc).__name__,
                    )
                    retry_or_fail_the_job(self, exc, job_id, db, _redis, 2**self.request.retries)

                # Reopen connection for post-parse writes (released before re-parse)
                tenant_conn = psycopg2.connect(conn_str)

                # --------------------------------------------------------------
                # Chunk the document using two-path strategy
                # (sanitize_chunk_text() is called inside chunk_document())
                # --------------------------------------------------------------
                chunks = chunk_document(doc, doc_id)

                # --------------------------------------------------------------
                # UPSERT chunks into tenant DB (Layer 2 idempotency)
                # ON CONFLICT (id) DO UPDATE — uuid5 IDs are stable across reruns.
                # Also update documents.chunk_count after the per-document loop.
                # --------------------------------------------------------------
                with tenant_conn.cursor() as cur:
                    for chunk in chunks:
                        cur.execute(
                            """
                            INSERT INTO chunks (id, document_id, ordinal, content, token_count, is_table)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE
                                SET content     = EXCLUDED.content,
                                    token_count = EXCLUDED.token_count,
                                    ordinal     = EXCLUDED.ordinal,
                                    is_table    = EXCLUDED.is_table
                            """,
                            # Column order above. chunk.id is the uuid5 the Chunk
                            # constructor derived, rendered as text for psycopg2.
                            (
                                str(chunk.id), doc_id, chunk.ordinal,
                                chunk.content, chunk.token_count, chunk.is_table,
                            ),
                        )

                    cur.execute(
                        "UPDATE documents SET chunk_count = %s WHERE id = %s",
                        (len(chunks), doc_id),
                    )
                    tenant_conn.commit()

                # Emit chunking.complete for this document
                emit(
                    job_id,
                    "chunking.complete",
                    {"document_id": doc_id, "chunk_count": len(chunks)},
                    db,
                    _redis,
                )

                log.info(
                    "chunk_documents.complete",
                    document_id=doc_id,
                    chunk_count=len(chunks),
                )

        except Exception as exc:
            # Best-effort rollback on unexpected error
            try:
                if tenant_conn is not None:
                    tenant_conn.rollback()
            except Exception:
                pass
            log.error(
                "chunk_documents.unexpected_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            retry_or_fail_the_job(self, exc, job_id, db, _redis, 2**self.request.retries)
        finally:
            if tenant_conn is not None:
                tenant_conn.close()

    # T-02-03-04: the job's four ids and nothing else, never a connection string
    return job
