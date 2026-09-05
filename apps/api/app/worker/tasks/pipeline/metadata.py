"""
generate_metadata — Celery task: metadata + entity extraction per chunk.

Position in M2 chain (3rd of 4):
    parse_documents → chunk_documents → generate_metadata → embed_and_migrate

Layout:
    generate_metadata    the task, one tenant connection, the document loop, the
                         wholly-failed rule below
    _enrich_document     one document: fetch, Layer 3 pre-check, enrich, emit
    _pending_chunks      Layer 3 idempotency pre-check
    _enrich_pending      the batch loop, one ChunkMetadata per enriched chunk
    _persist_enrichment  one chunk's writes, committed
    _refuse_a_run_that_enriched_nothing
                         the wholly-failed rule itself, called once per run
    _fail_the_job        the failure mechanism shared with the other pipeline tasks
    MetadataEnrichmentFailed
                         what the wholly-failed rule raises, so Celery records
                         FAILURE and the chain stops

THE WHOLLY-FAILED RULE (issue #23):
    A run that processed chunks and produced ZERO ChunkMetadata does not report
    succeeded. It marks the job failed with a reason naming chunks_seen and
    chunks_enriched, emits job.failed, and raises so Celery records FAILURE and
    the chain stops instead of embedding chunks that have no metadata.

    Observed 2026-08-22: batch_extraction_failed on all three documents,
    chunks_enriched=0, job status succeeded. The failure was invisible to the
    SSE stream, to the job row, and to the embed step that ran next.

    chunks_seen counts the chunks a run took responsibility for enriching, the
    pending set, after the Layer 3 pre-check. Zero of them is not a failure: an
    empty document, and a document whose chunks are all already enriched, both
    succeed. Partial enrichment also succeeds, with the counts recorded as
    before; a re-run re-attempts the chunks that missed.

    No retry. Every batch already exhausted the tenacity retries inside
    enrich_chunks_batch, and a total failure is a configuration wall (a missing
    or rejected ANTHROPIC_API_KEY, for instance) that a fourth attempt only bills.

Layer 3 idempotency (SELECT COUNT(*) pre-check):
    Before calling the model for a chunk, the task checks whether a chunk_metadata
    row already exists for that chunk_id. If it does, the chunk is skipped — no
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
    The tenant DB connection string is NEVER in the task arguments. The IngestionJob
    from chunk_documents carries the four ids and has no field for anything else.
    The connection string is fetched from the control DB by agent_id and decrypted
    with fernet_decrypt() at runtime.

Cost design (batched model calls):
    Metadata extraction is the pipeline's slowest and most billed step.
    enrich_chunks_batch() packs BATCH_SIZE (10) chunks into a single call,
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
    that DID succeed). When EVERY batch takes that path the run enriched nothing,
    which is the wholly-failed rule above.

Event emission order (CONTEXT.md §SSE Event Vocabulary):
    metadata.started  ← emitted per document (before the batch loop)
    metadata.progress ← emitted after each batch ({processed, total})
    metadata.complete ← emitted per document (after all chunks processed)
    job.failed        ← terminal, only on the wholly-failed path

Return value (chain contract):
    The same IngestionJob it received. job_in_job_out (chain_edge.py) builds the job
    from the wire dict at the task's edge and sends the returned job back out as one,
    so Celery still carries the four keys as JSON and the body works in the type.
    A dict the job cannot be built from is logged as generate_metadata.invalid_result_dict
    and returned unchanged, which is what the `or` chain here used to do.
    Connection strings are NEVER returned.

Threat mitigations (T-02-04):
    T-02-04-01: Task signature is (self, job: IngestionJob); the provider key is
                resolved from Settings by the client factory, never a task arg.
    T-02-04-02: The response is validated by Pydantic via
                client.chat.completions.parse(); malformed responses raise
                ValidationError and the chunk is skipped.
                Literal entity types prevent arbitrary type injection.
    T-02-04-03: Layer 3 idempotency guard (SELECT COUNT(*) FROM chunk_metadata)
                prevents re-billing on retry.
    T-02-04-06: structlog calls log chunk_id and document_id ONLY — never chunk
                content or response bodies.
"""

from datetime import datetime, timezone

import psycopg2
import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.model_client import LedgerContext, ledger_recorder
from app.core.redis_tls import redis_ssl_kwargs
from app.core.security import fernet_decrypt, require_ciphertext
from app.domain.chunk_metadata import ChunkMetadata
from app.domain.ingestion_job import IngestionJob
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.metadata_service import BATCH_SIZE, enrich_chunks_batch
from app.worker.celery_app import celery_app
from app.worker.tasks.pipeline.chain_edge import job_in_job_out

log = structlog.get_logger(__name__)

# Module-level sync Redis client. Strip the query string, then redis_ssl_kwargs decides TLS.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = redis_ssl_kwargs(_url_clean)
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


class MetadataEnrichmentFailed(RuntimeError):
    """Every chunk the run took responsibility for failed enrichment (issue #23).

    Raised after the job has been marked failed, so Celery records the task as
    FAILURE and the chain stops rather than forwarding a success dict to
    embed_and_migrate.
    """


def _persist_enrichment(tenant_conn, enrichment: ChunkMetadata) -> None:
    """Write one chunk's metadata and entities, then commit.

    Entity dedup: ON CONFLICT (normalized, type) DO UPDATE SET name so the
    human-readable name reflects the most recent occurrence, while the canonical
    normalized form stays the dedup key (T-02-04-02). The chunk_entities link
    uses ON CONFLICT DO NOTHING so a retry cannot duplicate a pair.

    The commit is per-chunk, which is what Layer 3 retry granularity rests on:
    a worker killed mid-batch leaves already-written chunks skippable.
    """
    with tenant_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chunk_metadata (chunk_id, summary, keywords, questions)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (chunk_id) DO UPDATE
                SET summary   = EXCLUDED.summary,
                    keywords  = EXCLUDED.keywords,
                    questions = EXCLUDED.questions
            """,
            (
                enrichment.chunk_id,
                enrichment.summary,
                enrichment.keywords,
                enrichment.questions,
            ),
        )
        for entity in enrichment.entities:
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
                (enrichment.chunk_id, entity_id),
            )
    tenant_conn.commit()


def _pending_chunks(tenant_conn, chunk_rows) -> list[tuple]:
    """The (chunk_id, content) pairs that have no chunk_metadata row yet.

    Layer 3 idempotency: an already-enriched chunk costs no model call and no
    re-billing (T-02-04-03). Stays on the main thread because psycopg2 is not
    thread-safe.
    """
    pending: list[tuple] = []
    for chunk_id, content in chunk_rows:
        with tenant_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = %s",
                (chunk_id,),
            )
            if cur.fetchone()[0] > 0:
                log.info("generate_metadata.already_enriched", chunk_id=str(chunk_id))
                continue
        pending.append((chunk_id, content))
    return pending


def _enrich_pending(
    tenant_conn, db, job_id: str, pending: list[tuple], ledger: LedgerContext
) -> int:
    """Enrich and persist the pending chunks; return how many records landed.

    One model call per BATCH_SIZE chunks. The model returns per-chunk results in
    submission order, and this function zips them back to chunk_ids by index,
    never by id. The model never sees an id. A ChunkMetadata exists only for a
    chunk that came back, so the return value counts real records, not attempts.

    `ledger` carries the job's three ids down to the enrichment call, so each
    batch's spend lands on a row naming the job that spent it.
    """
    enriched = 0
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start : batch_start + BATCH_SIZE]
        try:
            results = enrich_chunks_batch([content for _, content in batch], ledger)
        except Exception as exc:
            # One bad batch does not abort the document. Every batch taking this
            # path is what the wholly-failed rule catches.
            log.warning(
                "generate_metadata.batch_extraction_failed",
                batch_start=batch_start,
                batch_size=len(batch),
                error=str(exc),
            )
            continue

        for (chunk_id, _content), meta in zip(batch, results):
            _persist_enrichment(
                tenant_conn,
                ChunkMetadata(
                    chunk_id=chunk_id,
                    summary=meta.summary,
                    keywords=meta.keywords,
                    questions=meta.questions,
                    entities=meta.entities,
                ),
            )
            enriched += 1

        emit(job_id, "metadata.progress", {"processed": enriched, "total": len(pending)}, db, _redis)
    return enriched


def _enrich_document(
    tenant_conn, db, job_id: str, doc_id: str, ledger: LedgerContext
) -> tuple[int, int]:
    """Enrich one document's chunks; return (chunks_seen, chunks_enriched).

    chunks_seen is the pending count, the chunks this run took responsibility
    for, after Layer 3 has skipped the ones already enriched. The caller sums
    both numbers across documents and reads the wholly-failed rule off them.
    """
    with tenant_conn.cursor() as cur:
        cur.execute(
            "SELECT id, content FROM chunks WHERE document_id = %s ORDER BY ordinal",
            (doc_id,),
        )
        chunk_rows = cur.fetchall()

    emit(job_id, "metadata.started", {"document_id": doc_id, "chunk_count": len(chunk_rows)}, db, _redis)

    pending = _pending_chunks(tenant_conn, chunk_rows)
    enriched = _enrich_pending(tenant_conn, db, job_id, pending, ledger)

    emit(job_id, "metadata.complete", {"document_id": doc_id}, db, _redis)
    log.info("generate_metadata.complete", document_id=doc_id, chunks_enriched=enriched)
    return len(pending), enriched


def _refuse_a_run_that_enriched_nothing(db, job_id: str, seen: int, enriched: int) -> None:
    """Issue #23's rule, in one place: chunks processed, none enriched, no success.

    Returns quietly when the run took on no chunks at all (an empty document, or
    every chunk already enriched), and when at least one ChunkMetadata landed.
    Otherwise it fails the job and raises, so Celery records FAILURE and
    embed_and_migrate never runs over chunks with no metadata.
    """
    if seen == 0 or enriched > 0:
        return
    reason = f"generate_metadata enriched nothing: chunks_seen={seen}, chunks_enriched={enriched}"
    log.error("generate_metadata.nothing_enriched", chunks_seen=seen, chunks_enriched=enriched)
    _fail_the_job(db, job_id, reason)
    raise MetadataEnrichmentFailed(reason)


def _fail_the_job(db, job_id: str, reason: str) -> None:
    """Mark the job failed and emit the terminal job.failed event.

    The same mechanism provision, migrations and embed use: the job row carries
    the reason, and job.failed is what the SSE stream and the admin ingest page
    treat as terminal.
    """
    # job_row, not job: `job` is the IngestionJob the chain carries, and this is
    # the control DB row. Same two names embed.py uses.
    job_row = db.get(Job, job_id)
    if job_row is not None:
        job_row.status = "failed"
        job_row.error = reason
        job_row.finished_at = datetime.now(timezone.utc)
        db.commit()
    emit(job_id, "job.failed", {"error": reason}, db, _redis)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
@job_in_job_out
def generate_metadata(self, job: IngestionJob) -> IngestionJob:
    """Enrich every chunk that has no metadata yet; link entities via chunk_entities.

    Args:
        job: The IngestionJob chunk_documents forwarded. Connection strings are
             NEVER on it; the type has no field for one (T-02-04-01).

    Returns:
        The same job, forwarded to embed_and_migrate. job_in_job_out converts
        both ends, so the broker still sees the four keys as JSON.

    Raises:
        MetadataEnrichmentFailed: chunks were processed and none were enriched.
    """
    agent_id = job.agent_id
    job_id = job.job_id
    document_ids = job.document_ids

    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("generate_metadata.agent_not_found", agent_id=agent_id)
            return job

        # POOLED connection string, DML only. Never logged (T-02-04-01).
        conn_str = fernet_decrypt(
            require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string")
        )
        tenant_conn = psycopg2.connect(conn_str)
        # The three ids the chain already carries, plus where the spend is
        # written. The dsn reaches the recorder here and travels no further:
        # LedgerContext has no field that could hold one (T-02-04-01).
        ledger = LedgerContext(
            tenant_id=job.tenant_id,
            agent_id=job.agent_id,
            job_id=job.job_id,
            recorder=ledger_recorder(conn_str),
        )
        chunks_seen = 0
        chunks_enriched = 0
        try:
            for doc_id in document_ids:
                seen, enriched = _enrich_document(tenant_conn, db, job_id, doc_id, ledger)
                chunks_seen += seen
                chunks_enriched += enriched
        except Exception as exc:
            try:
                tenant_conn.rollback()
            except Exception:
                pass
            log.error("generate_metadata.unexpected_error", error_type=type(exc).__name__, error=str(exc))
            raise self.retry(exc=exc, countdown=2**self.request.retries)
        finally:
            tenant_conn.close()

        _refuse_a_run_that_enriched_nothing(db, job_id, chunks_seen, chunks_enriched)

    return job
