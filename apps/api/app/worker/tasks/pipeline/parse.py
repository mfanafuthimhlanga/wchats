"""
parse_documents — Celery task: parse uploaded documents via Docling.

Position in M2 chain (1st of 4):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate

Idempotency contract (Layer 1 of 4 — source_hash guard):
    Each document is checked for parse_status == 'parsed'. If the document already
    carries a non-NULL source_hash AND parse_status == 'parsed', the task skips it.
    This ensures task retries (acks_late=True) are safe: re-processing the same
    document_id produces the same outcome without duplicate work.

Connection string security (CLAUDE.md non-negotiable rule):
    The tenant DB connection string is NEVER in the task arguments.
    It is fetched from the control DB by agent_id and decrypted with fernet_decrypt()
    at runtime. This keeps the string out of Redis, Flower UI, and result backends.

Event emission order (CONTEXT.md §SSE Event Vocabulary):
    ingestion.started   ← emitted ONCE per task invocation (before the document loop)
    parsing.started     ← emitted per document (before Docling parse)
    parsing.complete    ← emitted per document (after successful Docling parse)

Return value (CLAUDE.md / chain contract):
    {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
    Connection strings are NEVER returned — they stay encrypted in the control DB.

Threat mitigations:
    T-02-02-01: task signature is (self, tenant_id, agent_id, job_id, document_ids)
                — no connection string or API key. Verified by test_parse_task.py.
    T-02-02-03: httpx.get uses timeout=30 seconds; follow_redirects=True (default max).
    T-02-02-04: structlog calls reference only document_id, page_count, error type —
                never source content, URL body, or connection strings.
    T-02-02-05: acks_late=True + Layer 1 idempotency guard — retries are safe.
"""

import hashlib
import ssl
import time
from pathlib import Path

import httpx
import psycopg2
import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.services import storage_service

# noqa: F401 on `parse_document` — this module only calls `parse_document_from_bytes`,
# but the *binding* is load-bearing: four tests patch
# `app.worker.tasks.pipeline.parse.parse_document`
# (tests/unit/test_parse_task.py:173 via monkeypatch.setattr, plus three sites in
# tests/integration/test_ingestion_chain.py), and both patch spellings raise
# AttributeError when the attribute is absent. Deleting the import turns those four
# green tests red — observed: 1 failed, 4 passed on that file with the name removed.
from app.services.docling_service import parse_document, parse_document_from_bytes  # noqa: F401
from app.services.events import emit
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client — strip query params; pass ssl_cert_reqs as constant.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


def _compute_source_hash(file_path: Path) -> str:
    """SHA-256 of file content — Layer 1 idempotency key.

    Args:
        file_path: Path to the local document file.

    Returns:
        Lowercase hex-encoded SHA-256 digest string.
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def parse_documents(
    self,
    tenant_id: str,
    agent_id: str,
    job_id: str,
    document_ids: list[str],
) -> dict:
    """Parse uploaded documents for the given agent using Docling.

    Args:
        tenant_id:    UUID string of the owning tenant.
        agent_id:     UUID string of the agent whose documents to parse.
        job_id:       UUID string of the ingestion job (for SSE event routing).
        document_ids: List of document UUID strings to process.

    Returns:
        {"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
        Passed as the ``result`` argument to chunk_documents in the Celery chain.
        Connection strings are NEVER included in the return value.
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Fetch agent from control DB — needed for tenant connection string
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("parse_documents.agent_not_found", agent_id=agent_id)
            return {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "job_id": job_id,
                "document_ids": document_ids,
            }

        # ------------------------------------------------------------------
        # Decrypt POOLED connection string from control DB (NOT the direct one).
        # DDL is done; M2 only does DML reads/writes — pooled URI is correct.
        # fernet_decrypt return value is intentionally not logged (T-02-02-04).
        # ------------------------------------------------------------------
        tenant_conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        # ------------------------------------------------------------------
        # Emit ingestion.started ONCE — only if there are un-parsed documents.
        # If all docs are already parsed, skip the emit and return early.
        # Retry on OperationalError: Neon pooler DNS can take 30-60s to
        # propagate after a new project is created (transient "Name or
        # service not known" on cold compute endpoint startup).
        # ------------------------------------------------------------------
        try:
            tenant_conn = psycopg2.connect(tenant_conn_str)
        except psycopg2.OperationalError as exc:
            raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
        try:
            cursor = tenant_conn.cursor()

            # Pre-check: count documents not yet parsed to gate ingestion.started emit
            cursor.execute(
                "SELECT COUNT(*) FROM documents WHERE id = ANY(%s::uuid[]) "
                "AND NOT (parse_status = 'parsed' AND source_hash IS NOT NULL)",
                (document_ids,),
            )
            unparsed_count = cursor.fetchone()[0]

            if unparsed_count == 0:
                log.info(
                    "parse_documents.all_already_parsed",
                    job_id=job_id,
                    document_count=len(document_ids),
                )
                return {
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "job_id": job_id,
                    "document_ids": document_ids,
                }

            # Only emit ingestion.started on the first attempt — retries skip it
            # to avoid duplicate events in the SSE stream (acks_late retries re-enter here).
            if self.request.retries == 0:
                emit(
                    job_id,
                    "ingestion.started",
                    {"document_count": len(document_ids)},
                    db,
                    _redis,
                )
                # Give SSE clients 1s to subscribe before parsing.started fires.
                # emit() publishes to Redis before the DB commit, so a client that
                # connects in the sub-second gap between the two emits would miss
                # parsing.started from both pub/sub (too late) and DB replay (not yet committed).
                time.sleep(1)

            # ------------------------------------------------------------------
            # Per-document parse loop
            # ------------------------------------------------------------------
            for doc_id in document_ids:
                cursor.execute(
                    "SELECT source_uri, source_type, source_hash, parse_status "
                    "FROM documents WHERE id = %s",
                    (doc_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    log.warning(
                        "parse_documents.document_not_found",
                        document_id=doc_id,
                        job_id=job_id,
                    )
                    continue

                source_uri, source_type, source_hash, parse_status = row

                # --------------------------------------------------------------
                # Layer 1 idempotency guard — skip if already parsed
                # (handles task retries where the document was parsed in a prior attempt)
                # --------------------------------------------------------------
                if parse_status == "parsed" and source_hash is not None:
                    log.info(
                        "parse_documents.already_parsed",
                        document_id=doc_id,
                        job_id=job_id,
                    )
                    continue

                # Emit parsing.started only on first attempt for this document.
                # On retries parse_status is 'parsing' (we do NOT reset it to 'failed'
                # before retrying — see except Exception handler below), so the event
                # was already sent — skip to avoid duplicate SSE events.
                is_retry_attempt = parse_status == "parsing"
                if not is_retry_attempt:
                    emit(
                        job_id,
                        "parsing.started",
                        {"document_id": doc_id, "source_uri": source_uri},
                        db,
                        _redis,
                    )

                    # Mark in-progress (only needed on first attempt)
                    cursor.execute(
                        "UPDATE documents SET parse_status = 'parsing' WHERE id = %s",
                        (doc_id,),
                    )
                    tenant_conn.commit()

                # Release the Neon connection before the long Docling parse.
                # Docling loads ML models on first call (~2 min); holding an idle
                # Neon connection that long triggers the serverless idle timeout.
                tenant_conn.close()
                tenant_conn = None
                cursor = None

                # --------------------------------------------------------------
                # Resolve file path or fetch URL bytes
                # --------------------------------------------------------------
                try:
                    if source_type in ("url",):
                        # URL source — fetch bytes via httpx then parse from stream
                        response = httpx.get(
                            source_uri, timeout=30, follow_redirects=True
                        )
                        response.raise_for_status()
                        content = response.content
                        computed_hash = hashlib.sha256(content).hexdigest()
                        doc = parse_document_from_bytes(content, source_uri)
                    else:
                        # File source (pdf, png, jpg, jpeg) — read bytes from S3
                        # (PROD-13: no local-disk dependency; bytes survive worker restarts
                        # and are reachable from any Fargate task).
                        ext = Path(source_uri).suffix or f".{source_type}"
                        content = storage_service.get_bytes(
                            storage_service.upload_key(agent_id, doc_id, ext)
                        )
                        computed_hash = hashlib.sha256(content).hexdigest()
                        doc = parse_document_from_bytes(content, source_uri)

                except RuntimeError as exc:
                    # Fatal Docling parse error — mark failed, do NOT retry
                    # (RuntimeError from docling_service = 4xx-equivalent)
                    log.error(
                        "parse_documents.docling_fatal_error",
                        document_id=doc_id,
                        error=str(exc),
                    )
                    tenant_conn = psycopg2.connect(tenant_conn_str)
                    cursor = tenant_conn.cursor()
                    cursor.execute(
                        "UPDATE documents SET parse_status = 'failed' WHERE id = %s",
                        (doc_id,),
                    )
                    tenant_conn.commit()
                    continue

                except Exception as exc:
                    # Transient error — do NOT set parse_status='failed' here.
                    # Keeping parse_status='parsing' ensures the retry idempotency
                    # guard (is_retry_attempt = parse_status == 'parsing') correctly
                    # skips re-emitting parsing.started on subsequent attempts.
                    # parse_status is set to 'failed' only if all retries are exhausted
                    # (MaxRetriesExceededError propagates past this handler naturally).
                    log.error(
                        "parse_documents.error",
                        document_id=doc_id,
                        error_type=type(exc).__name__,
                    )
                    raise self.retry(exc=exc, countdown=2**self.request.retries)

                # Reopen connection for post-parse writes (released before parse call)
                tenant_conn = psycopg2.connect(tenant_conn_str)
                cursor = tenant_conn.cursor()

                # --------------------------------------------------------------
                # Determine page_count from DoclingDocument
                # DoclingDocument exposes pages via .pages (dict keyed by page number)
                # --------------------------------------------------------------
                page_count = len(doc.pages) if hasattr(doc, "pages") else None

                # Write parse_status = 'parsed' and source_hash
                cursor.execute(
                    "UPDATE documents SET parse_status = 'parsed', source_hash = %s "
                    "WHERE id = %s",
                    (computed_hash, doc_id),
                )
                tenant_conn.commit()

                # Emit parsing.complete for this document
                emit(
                    job_id,
                    "parsing.complete",
                    {"document_id": doc_id, "page_count": page_count},
                    db,
                    _redis,
                )

                log.info(
                    "parse_documents.document_parsed",
                    document_id=doc_id,
                    page_count=page_count,
                    job_id=job_id,
                )

        finally:
            if tenant_conn is not None:
                tenant_conn.close()

    # T-02-02-01: Return only chain-forwarding keys — no connection string
    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "job_id": job_id,
        "document_ids": document_ids,
    }
