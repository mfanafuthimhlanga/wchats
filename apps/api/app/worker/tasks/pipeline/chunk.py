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
    The tenant DB connection string is NEVER in the task arguments. The result dict from
    parse_documents carries only {tenant_id, agent_id, job_id, document_ids}. The
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
    {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
    Connection strings are NEVER returned.

Threat mitigations:
    T-02-03-01: Every chunk text passes through sanitize_chunk_text() inside
                chunking_service.chunk_document() before any INSERT.
    T-02-03-02: structlog calls reference document_id, chunk_count only — never chunk content
                or connection strings.
    T-02-03-03: uuid5 IDs + ON CONFLICT DO UPDATE — retry-safe upsert; no chunk-row leak.
    T-02-03-04: task signature is (self, result: dict) — no connection string or API key.
                Verified by test_chunk_documents_signature_takes_only_result_dict.
    T-02-03-06: httpx.get(timeout=30) for URL re-fetch — same DoS mitigation as parse_documents.
"""

import ssl
import structlog
from pathlib import Path

import httpx
import psycopg2
import redis as redis_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.chunking_service import chunk_document
from app.services.docling_service import parse_document, parse_document_from_bytes
from app.services.events import emit
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client — strip query params; pass ssl_cert_reqs as constant.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


def _resolve_local_path(agent_id: str, doc_id: str, source_uri: str) -> Path:
    """Mirror of parse_documents path-resolution. Returns the local temp file path.

    Constructs the same path that parse_documents used when saving the uploaded file,
    so that chunk_documents can re-read the file for Docling re-parsing.

    Args:
        agent_id:   String agent UUID (used as directory name under vrd-uploads).
        doc_id:     String document UUID (used as filename stem).
        source_uri: Original source URI — used to extract the file extension.

    Returns:
        Path: absolute path at /vrd-uploads/{agent_id}/{doc_id}{ext} (Docker volume mount)
    """
    ext = Path(source_uri).suffix or ".bin"
    return Path("/vrd-uploads") / agent_id / f"{doc_id}{ext}"


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def chunk_documents(self, result: dict) -> dict:
    """Chunk parsed documents using the two-path strategy (text + table Markdown).

    Receives the result dict from parse_documents, re-parses each document via Docling,
    splits into chunks via chunking_service.chunk_document(), and UPSERTs each chunk row
    into the tenant chunks table using ON CONFLICT (id) DO UPDATE (Layer 2 idempotency).

    Args:
        result: Return value from parse_documents —
                {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}.
                Connection strings are NEVER in this dict (CLAUDE.md non-negotiable rule).

    Returns:
        {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
        Forwarded unchanged to generate_metadata in the Celery chain.
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
            "chunk_documents.invalid_result_dict",
            keys=list(result.keys()),
        )
        # Return result unchanged — defensive; chain may have been re-dispatched mid-flight
        return result

    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Fetch agent from control DB — needed for tenant connection string
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("chunk_documents.agent_not_found", agent_id=agent_id)
            return {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "job_id": job_id,
                "document_ids": document_ids,
            }

        # ------------------------------------------------------------------
        # Decrypt POOLED connection string — DML only, pooled URI is correct
        # (T-02-03-02: conn_str never logged)
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(agent.neon_connection_string)

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
                    if source_type in ("pdf", "png", "jpg", "jpeg"):
                        local_path = _resolve_local_path(agent_id, doc_id, source_uri)
                        doc = parse_document(local_path)
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
                    raise self.retry(exc=exc, countdown=2**self.request.retries)

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
                            INSERT INTO chunks (id, document_id, ordinal, content, token_count)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE
                                SET content     = EXCLUDED.content,
                                    token_count = EXCLUDED.token_count,
                                    ordinal     = EXCLUDED.ordinal
                            """,
                            (
                                chunk["id"],
                                doc_id,
                                chunk["ordinal"],
                                chunk["text"],
                                chunk["token_count"],
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
            raise self.retry(exc=exc, countdown=2**self.request.retries)
        finally:
            if tenant_conn is not None:
                tenant_conn.close()

    # T-02-03-04: Return only chain-forwarding keys — no connection string
    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "job_id": job_id,
        "document_ids": document_ids,
    }
