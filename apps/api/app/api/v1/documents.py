"""
Document routes for Veridian M2 ingestion pipeline.

POST /agents/{agent_id}/documents — upload files and/or URLs; dispatches Celery chain
GET  /agents/{agent_id}/documents — list all documents for agent from tenant DB
GET  /agents/{agent_id}/documents/{document_id} — fetch a single document row

Security:
    - Requires X-API-Key on all routes (get_current_tenant dependency).
    - tenant_id sourced from authenticated Tenant (not request body) — T-04-04 pattern.
    - POST filters Agent by both Agent.id AND Agent.tenant_id — T-02-06-01.
    - File-type whitelist enforced before writing to disk — T-02-06-03.
    - File-size cap enforced before writing to disk — T-02-06-02.
    - Chain dispatch passes only IDs (tenant_id, agent_id, job_id, document_ids);
      connection strings NEVER in chain args — T-02-06-04, CLAUDE.md rule 4.
    - Temp files stored at gettempdir()/vrd-uploads/{agent_id}/{doc_id}{ext};
      original filename NEVER used as a path component — T-02-06-05.
    - File contents NEVER logged; only source_uri (user-provided metadata) — T-02-06-08.

ARCHITECTURAL EXCEPTION NOTE (CLAUDE.md: FastAPI never does work inline):
    The psycopg2 INSERT in the POST route is an accepted exception to the
    "FastAPI never does work inline" rule. Document row creation is a prerequisite
    for chain dispatch — the chain requires document IDs to exist in the tenant DB
    before it can process them. This insertion CANNOT be Celery-delegated without
    creating a circular dependency (Celery task that creates the row for which
    Celery was invoked). The INSERT is bounded: one row per uploaded file, no ML
    model calls, no external network calls.

    The psycopg2.connect() call MUST include connect_timeout=5 to bound
    blocking on the route's async event loop thread — T-02-06-09.
"""

import hashlib
import tempfile
import uuid
from pathlib import Path
from uuid import UUID

import psycopg2
import structlog
from celery import chain
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import get_contextvars

from app.api.deps import get_current_tenant
from app.core.config import settings
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.document import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.worker.tasks.pipeline.chunk import chunk_documents
from app.worker.tasks.pipeline.embed import embed_and_migrate
from app.worker.tasks.pipeline.metadata import generate_metadata
from app.worker.tasks.pipeline.parse import parse_documents

log = structlog.get_logger(__name__)

router = APIRouter(tags=["documents"])

# Whitelist of accepted file extensions (T-02-06-03)
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/documents
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{agent_id}/documents",
    status_code=202,
    response_model=DocumentUploadResponse,
)
async def upload_documents(
    agent_id: UUID,
    files: list[UploadFile] = File(default=[]),
    urls: list[str] = Form(default=[]),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> DocumentUploadResponse:
    """Accept multipart file uploads and/or URL strings; dispatch ingestion chain.

    Validates agent ownership, file types, file sizes, then:
      1. Saves files to local temp directory (bounded, uuid4 filename — T-02-06-05).
      2. Creates document rows in tenant DB via synchronous psycopg2 (accepted
         architectural exception — see module docstring).
      3. Creates a job row in the control DB.
      4. Dispatches the Celery chain: parse → chunk → metadata → embed.
      5. Returns 202 with job_id and events_url.

    Returns:
        DocumentUploadResponse (202 Accepted).
    """
    # ------------------------------------------------------------------
    # 1. Validate agent exists, belongs to tenant, and status == 'ready'
    # ------------------------------------------------------------------
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Agent is not ready (status={agent.status})",
        )

    # ------------------------------------------------------------------
    # 2. Validate at least one source provided
    # ------------------------------------------------------------------
    if not files and not urls:
        raise HTTPException(
            status_code=422,
            detail="At least one file or URL is required",
        )

    # ------------------------------------------------------------------
    # 3. Validate file types and sizes BEFORE writing to disk (T-02-06-02, T-02-06-03)
    #    Read entire file content into memory for size check;
    #    cache in list keyed by index to avoid double-awaiting the stream.
    # ------------------------------------------------------------------
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    cached_contents: list[bytes] = []

    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type: {ext}",
            )
        contents = await f.read()
        if len(contents) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File {f.filename} exceeds MAX_UPLOAD_SIZE_MB={settings.MAX_UPLOAD_SIZE_MB}",
            )
        cached_contents.append(contents)

    # ------------------------------------------------------------------
    # 4. Decrypt tenant DB connection string
    #    (ARCHITECTURAL EXCEPTION: needed for synchronous document INSERT)
    #    fernet_decrypt return value must NEVER be logged (T-02-06-08).
    # ------------------------------------------------------------------
    tenant_conn_str = fernet_decrypt(agent.neon_connection_string)

    # ------------------------------------------------------------------
    # 5. Save files to temp dir + INSERT document rows in tenant DB
    #    psycopg2.connect(connect_timeout=5) bounds blocking on the async
    #    event loop thread to 5 seconds (T-02-06-09).
    # ------------------------------------------------------------------
    upload_dir = Path("/vrd-uploads") / str(agent.id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    document_ids: list[str] = []

    tenant_conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    try:
        with tenant_conn.cursor() as cur:
            for idx, f in enumerate(files):
                doc_id = str(uuid.uuid4())
                ext = Path(f.filename or "").suffix.lower()
                local_path = upload_dir / f"{doc_id}{ext}"
                # Write using cached content from the validation pass
                local_path.write_bytes(cached_contents[idx])
                # source_uri stores the original filename for display/tracking;
                # it is NEVER used as a filesystem path (T-02-06-05).
                cur.execute(
                    """
                    INSERT INTO documents (id, source_type, source_uri, title)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (doc_id, ext.lstrip("."), f.filename, f.filename),
                )
                document_ids.append(doc_id)
                log.info(
                    "upload_documents.file_saved",
                    agent_id=str(agent.id),
                    document_id=doc_id,
                    source_uri=f.filename,
                )

            for url_str in urls:
                doc_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO documents (id, source_type, source_uri, title)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (doc_id, "url", url_str, url_str),
                )
                document_ids.append(doc_id)
                log.info(
                    "upload_documents.url_registered",
                    agent_id=str(agent.id),
                    document_id=doc_id,
                    source_uri=url_str,
                )

        tenant_conn.commit()
    finally:
        tenant_conn.close()

    # ------------------------------------------------------------------
    # 6. Create job row in control DB (mirrors agents.py lines 63–73)
    # ------------------------------------------------------------------
    job = Job(
        tenant_id=tenant.id,
        agent_id=agent.id,
        kind="ingest_documents",
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # ------------------------------------------------------------------
    # 7. Dispatch Celery chain (mirrors agents.py lines 76–84)
    #    Only IDs are passed; connection strings NEVER in task args (CLAUDE.md rule 4).
    # ------------------------------------------------------------------
    ctx = get_contextvars()
    chain(
        parse_documents.s(
            str(tenant.id), str(agent.id), str(job.id), document_ids
        ),
        chunk_documents.s(),
        generate_metadata.s(),
        embed_and_migrate.s(),
    ).apply_async(
        queue="pipeline",
        headers={"request_id": ctx.get("request_id", "")},
    )

    log.info(
        "upload_documents.chain_dispatched",
        tenant_id=str(tenant.id),
        agent_id=str(agent.id),
        job_id=str(job.id),
        document_ids=document_ids,
    )

    # ------------------------------------------------------------------
    # 8. Return 202 Accepted
    # ------------------------------------------------------------------
    return DocumentUploadResponse(
        job_id=job.id,
        document_ids=[UUID(d) for d in document_ids],
        status="pending",
        events_url=f"/jobs/{job.id}/events",
    )


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/documents
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> DocumentListResponse:
    """Return all documents for the specified agent (tenant-scoped).

    Validates agent ownership, then queries tenant DB via psycopg2.

    Returns:
        DocumentListResponse with up to 200 most-recently-created documents.
    """
    # Validate agent ownership (T-02-06-01 pattern)
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Decrypt connection string (never logged — T-02-06-08)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # Query tenant DB for documents
    tenant_conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_uri, source_type, title, parse_status,
                       chunk_count, created_at
                FROM documents
                ORDER BY created_at DESC
                LIMIT 200
                """
            )
            rows = cur.fetchall()
    finally:
        tenant_conn.close()

    documents = [
        DocumentResponse(
            id=row[0],
            source_uri=row[1],
            source_type=row[2],
            title=row[3],
            parse_status=row[4],
            chunk_count=row[5],
            created_at=row[6],
        )
        for row in rows
    ]

    return DocumentListResponse(documents=documents)


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/documents/{document_id}
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/documents/{document_id}",
    response_model=DocumentResponse,
)
async def get_document(
    agent_id: UUID,
    document_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> DocumentResponse:
    """Return a single document by ID (tenant-scoped).

    Returns:
        DocumentResponse.

    Raises:
        404 if agent not found or document not found.
    """
    # Validate agent ownership
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Decrypt connection string (never logged — T-02-06-08)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    tenant_conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_uri, source_type, title, parse_status,
                       chunk_count, created_at
                FROM documents
                WHERE id = %s
                """,
                (str(document_id),),
            )
            row = cur.fetchone()
    finally:
        tenant_conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentResponse(
        id=row[0],
        source_uri=row[1],
        source_type=row[2],
        title=row[3],
        parse_status=row[4],
        chunk_count=row[5],
        created_at=row[6],
    )
