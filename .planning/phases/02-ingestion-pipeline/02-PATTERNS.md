# Phase 2: M2 Ingestion Pipeline — Pattern Map

**Mapped:** 2026-05-13
**Files analyzed:** 20 (new/modified files across 7 waves)
**Analogs found:** 18 / 20

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `alembic_tenant/versions/0002_documents_ingestion_columns.py` | migration | batch | `alembic_tenant/versions/0001_tenant_v1_schema.py` | exact |
| `app/utils/chunk_id.py` | utility | transform | `app/core/security.py` (hash/crypto utils) | role-match |
| `app/utils/sanitize.py` | utility | transform | `app/core/security.py` | role-match |
| `app/services/docling_service.py` | service | file-I/O | `app/services/events.py` (service wrapper) | role-match |
| `app/services/chunking_service.py` | service | transform | `app/services/events.py` | role-match |
| `app/services/metadata_service.py` | service | request-response | `app/services/events.py` | role-match |
| `app/services/embedding_service.py` | service | batch | `app/services/events.py` | role-match |
| `app/worker/tasks/pipeline/parse.py` | Celery task | file-I/O | `app/worker/tasks/pipeline/provision.py` | exact |
| `app/worker/tasks/pipeline/chunk.py` | Celery task | transform | `app/worker/tasks/pipeline/provision.py` | exact |
| `app/worker/tasks/pipeline/metadata.py` | Celery task | request-response | `app/worker/tasks/pipeline/migrations.py` | exact |
| `app/worker/tasks/pipeline/embed.py` | Celery task | CRUD | `app/worker/tasks/pipeline/migrations.py` | exact |
| `app/api/v1/documents.py` | route/controller | request-response | `app/api/v1/agents.py` | exact |
| `app/schemas/document.py` | schema | — | `app/schemas/agent.py` | exact |
| `scripts/demo_m2.sh` | config/script | — | `scripts/demo_m1.sh` | exact |
| `tests/unit/test_chunk_id.py` | test | — | `tests/unit/test_task_args.py` | role-match |
| `tests/unit/test_parse_task.py` | test | — | `tests/unit/test_emit.py` | role-match |
| `tests/unit/test_chunk_task.py` | test | — | `tests/unit/test_emit.py` | role-match |
| `tests/unit/test_metadata_task.py` | test | — | `tests/unit/test_emit.py` | role-match |
| `tests/unit/test_embed_task.py` | test | — | `tests/unit/test_emit.py` | role-match |
| `tests/unit/test_document_routes.py` | test | — | `tests/unit/test_routes.py` | role-match |
| `tests/integration/test_ingestion_chain.py` | test | — | `tests/integration/test_chain.py` | exact |
| `tests/integration/test_ingestion_e2e.py` | test | — | `tests/integration/test_chain.py` | role-match |

---

## Pattern Assignments

### `alembic_tenant/versions/0002_documents_ingestion_columns.py` (migration, batch)

**Analog:** `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py`

**Imports pattern** (lines 1–34):
```python
"""Tenant DB v2 ingestion columns — adds source_hash, parse_status, chunk_count to
documents, and creates entities + chunk_entities tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
```

**Core ADD COLUMN pattern** (modeled on 0001 op.execute style):
```python
def upgrade() -> None:
    # --- documents: add ingestion columns ---
    op.execute("ALTER TABLE documents ADD COLUMN source_hash TEXT")
    op.execute("ALTER TABLE documents ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'pending'")
    op.execute("ALTER TABLE documents ADD COLUMN chunk_count INT")

    # --- entities table ---
    op.execute("""
        CREATE TABLE entities (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name       TEXT NOT NULL,
            type       TEXT NOT NULL,
            normalized TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (normalized, type)
        )
    """)

    # --- chunk_entities join table ---
    op.execute("""
        CREATE TABLE chunk_entities (
            chunk_id  UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, entity_id)
        )
    """)
```

**Downgrade pattern** (reverse dependency order, mirrors 0001 lines 192–204):
```python
def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunk_entities")
    op.execute("DROP TABLE IF EXISTS entities")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS chunk_count")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS parse_status")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS source_hash")
```

---

### `app/utils/chunk_id.py` (utility, transform)

**Analog:** No direct file analog — stdlib-only utility. Pattern drawn from RESEARCH.md §5.

**Core pattern:**
```python
import uuid

# Fixed namespace — never changes across Veridian deployments
CHUNK_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL


def deterministic_chunk_id(document_id: str, ordinal: int) -> uuid.UUID:
    """Return a stable uuid5 derived from document_id + ordinal.

    Same document_id + ordinal always produces the same chunk_id.
    Re-ingesting an unchanged document produces the same chunk IDs → safe upsert.
    uuid4() is intentionally NOT used — it breaks idempotency on task retry.
    """
    name = f"{document_id}:{ordinal}"
    return uuid.uuid5(CHUNK_UUID_NAMESPACE, name)
```

**No external imports.** stdlib only. Module has no `settings` dependency.

---

### `app/utils/sanitize.py` (utility, transform)

**Analog:** No direct file analog. Pattern drawn from CONTEXT.md §Security + RESEARCH.md §Common Pitfalls §11.

**Core pattern:**
```python
import re

# Injection patterns to strip from chunk text before DB write (PITFALLS.md §11)
_INJECTION_PATTERNS = re.compile(
    r"(System:|Human:|Assistant:|\[INST\]|\[/INST\]|<!--.*?-->|Ignore previous)",
    re.IGNORECASE | re.DOTALL,
)


def sanitize_chunk_text(text: str) -> str:
    """Strip prompt-injection patterns from chunk content before storage.

    Prevents indirect prompt injection via uploaded documents (PITFALLS.md §11).
    Returns the cleaned string; does not mutate the input.
    """
    return _INJECTION_PATTERNS.sub("", text).strip()
```

**No external imports.** stdlib only.

---

### `app/services/docling_service.py` (service, file-I/O)

**Analog:** `apps/api/app/services/events.py` — service wrapper with no class, plain functions.

**Module-level init pattern** (mirrors `provision.py` lines 61–62 — `_redis = redis_lib.from_url(...)`):
```python
import structlog
from pathlib import Path
from io import BytesIO

from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import DocumentStream, ConversionStatus

log = structlog.get_logger(__name__)

# Module-level init — DocumentConverter loads DocLayNet + TableFormer ML models
# (~1-2GB RAM). Must be initialized ONCE per worker process, not inside the task.
# Mirrors the _redis module-level pattern from provision.py.
_converter = DocumentConverter()
```

**Core function pattern:**
```python
def parse_document(file_path: Path) -> object:
    """Convert a document file to DoclingDocument.

    Args:
        file_path: Path to the document file (PDF, image).

    Returns:
        DoclingDocument on success.

    Raises:
        RuntimeError: If Docling conversion fails (wraps ConversionStatus errors).
    """
    result = _converter.convert(str(file_path), raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        for error in result.errors:
            log.error("docling.conversion_error", message=error.error_message)
        raise RuntimeError(f"Docling conversion failed: {result.errors}")
    return result.document


def parse_document_from_bytes(content: bytes, filename: str) -> object:
    """Convert document bytes (URL-fetched content) to DoclingDocument."""
    stream = DocumentStream(name=filename, stream=BytesIO(content))
    result = _converter.convert(stream, raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        raise RuntimeError(f"Docling conversion failed: {result.errors}")
    return result.document
```

---

### `app/services/chunking_service.py` (service, transform)

**Analog:** `apps/api/app/services/events.py` — plain functions, no class.

**Core two-path pattern** (from RESEARCH.md §4 + CONTEXT.md §Chunking Strategy):
```python
import structlog
from docling.chunking import HybridChunker
from docling_core.types.doc import TableItem

from app.utils.chunk_id import deterministic_chunk_id
from app.utils.sanitize import sanitize_chunk_text

log = structlog.get_logger(__name__)


def chunk_document(doc, document_id: str) -> list[dict]:
    """Produce an ordered list of chunk dicts from a DoclingDocument.

    Two-path strategy:
      - Text path: HybridChunker on doc, skip chunks containing TableItem.
      - Table path: one chunk per table, serialized as Markdown.
    Tables are NEVER fed to HybridChunker (PITFALLS.md §2).

    Returns:
        list of dicts with keys: id, text, ordinal, is_table, token_count
    """
    chunker = HybridChunker(max_tokens=512, merge_peers=True)
    chunks = []
    ordinal = 0

    # Text path
    for chunk in chunker.chunk(doc):
        has_table = any(
            isinstance(item, TableItem)
            for item in getattr(chunk.meta, "doc_items", [])
        )
        if has_table:
            continue
        text = sanitize_chunk_text(chunker.contextualize(chunk))
        if not text:
            continue
        chunk_id = deterministic_chunk_id(document_id, ordinal)
        chunks.append({
            "id": str(chunk_id),
            "text": text,
            "ordinal": ordinal,
            "is_table": False,
            "token_count": len(text.split()),  # approximate; replace with tokenizer if needed
        })
        ordinal += 1

    # Table path
    for table in doc.tables:
        md = table.export_to_markdown(doc=doc)
        if not md.strip():
            continue
        text = sanitize_chunk_text(md)
        chunk_id = deterministic_chunk_id(document_id, ordinal)
        chunks.append({
            "id": str(chunk_id),
            "text": text,
            "ordinal": ordinal,
            "is_table": True,
            "token_count": len(text.split()),
        })
        ordinal += 1

    return chunks
```

---

### `app/services/metadata_service.py` (service, request-response)

**Analog:** `apps/api/app/services/events.py` — plain functions. External SDK call pattern from RESEARCH.md §6.

**Module-level init + structured output pattern:**
```python
import structlog
import anthropic
from pydantic import BaseModel
from typing import Literal
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

log = structlog.get_logger(__name__)

# Module-level client — reads ANTHROPIC_API_KEY from env (settings)
_anthropic = anthropic.Anthropic()


class EntityExtraction(BaseModel):
    name: str
    type: Literal["product", "person", "place", "policy", "process"]
    normalized: str


class ChunkMetadataAndEntities(BaseModel):
    summary: str
    keywords: list[str]
    questions: list[str]
    entities: list[EntityExtraction]


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APITimeoutError)),
)
def enrich_chunk(text: str) -> ChunkMetadataAndEntities:
    """Call Claude Haiku to extract metadata + entities for a single chunk.

    Uses client.messages.parse() with Pydantic output_format for validated
    structured output. Single call returns summary, keywords, questions, AND entities.
    """
    result = _anthropic.messages.parse(
        model="claude-haiku-4-5",
        system=(
            "You are a metadata extractor for a RAG system. "
            "For the given text chunk produce: "
            "a 1-2 sentence summary, 5-10 keywords (noun phrases, no stop words), "
            "3-5 hypothetical questions a user might ask that this chunk answers, "
            "and all named entities (product, person, place, policy, process)."
        ),
        messages=[{"role": "user", "content": text}],
        max_tokens=512,
        output_format=ChunkMetadataAndEntities,
    )
    return result.parsed_output
```

---

### `app/services/embedding_service.py` (service, batch)

**Analog:** `apps/api/app/services/events.py` — plain functions. Batch pattern from RESEARCH.md §7.

**Module-level client + batch pattern:**
```python
import structlog
import voyageai
from tenacity import retry, wait_exponential, stop_after_attempt

log = structlog.get_logger(__name__)

BATCH_SIZE = 128
EMBEDDING_MODEL = "voyage-3"  # pinned — do NOT use voyage-latest (PITFALLS.md §3)

# Module-level client — reads VOYAGE_API_KEY from env (settings)
_vo = voyageai.Client()


@retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(5))
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed one batch of up to 128 texts. Retried on transient errors."""
    return _vo.embed(texts, model=EMBEDDING_MODEL, input_type="document").embeddings


def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed all texts in batches of BATCH_SIZE (128).

    Returns:
        list of 1024-dimensional float vectors, same order as input texts.
    """
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        all_embeddings.extend(_embed_batch(batch))
    return all_embeddings
```

---

### `app/worker/tasks/pipeline/parse.py` (Celery task, file-I/O)

**Analog:** `apps/api/app/worker/tasks/pipeline/provision.py` — EXACT pattern.

**Imports pattern** (mirrors provision.py lines 42–56):
```python
import structlog
from datetime import datetime, timezone

import redis as redis_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.docling_service import parse_document, parse_document_from_bytes
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level Redis client (mirrors provision.py line 61)
_redis = redis_lib.from_url(settings.REDIS_URL)
```

**Task decorator pattern** (mirrors provision.py lines 64–70):
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def parse_documents(self, tenant_id: str, agent_id: str, job_id: str, document_ids: list[str]) -> dict:
```

**Idempotency guard pattern** (mirrors provision.py lines 89–98):
```python
    # Idempotency guard: skip document if already parsed
    if document.parse_status == "parsed":
        log.info("parse_documents.already_parsed", document_id=str(doc_id))
        continue  # skip this document in the loop
```

**Emit pattern** (mirrors provision.py lines 131–136):
```python
    emit(job_id, "ingestion.started", {"document_count": len(document_ids)}, db, _redis)
    emit(job_id, "parsing.started", {"document_id": str(doc_id)}, db, _redis)
    # ... parse ...
    emit(job_id, "parsing.complete", {"document_id": str(doc_id), "page_count": page_count}, db, _redis)
```

**Return value pattern** (mirrors provision.py line 221 — no connection strings):
```python
    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "job_id": job_id,
        "document_ids": document_ids,
    }
```

**Error pattern** (mirrors provision.py lines 141–174 — 4xx fatal, 5xx retriable):
```python
    except Exception as exc:
        log.error("parse_documents.error", document_id=str(doc_id), error=str(exc))
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
```

**Tenant DB connection pattern** (mirrors migrations.py lines 120–124):
```python
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        conn_str = fernet_decrypt(agent.neon_connection_string)
    import psycopg2
    conn = psycopg2.connect(conn_str)
```

---

### `app/worker/tasks/pipeline/chunk.py` (Celery task, transform)

**Analog:** `apps/api/app/worker/tasks/pipeline/provision.py`

**Task signature** (chained — receives result dict from parse_documents, mirrors migrations.py lines 77–86):
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def chunk_documents(self, result: dict) -> dict:
    """Chunk parsed documents using two-path strategy (text + table).

    Args:
        result: Return value from parse_documents —
                {"tenant_id", "agent_id", "job_id", "document_ids"}.
                Connection strings NEVER in this dict (CLAUDE.md rule).
    """
    tenant_id = result["tenant_id"]
    agent_id  = result["agent_id"]
    job_id    = result["job_id"]
    document_ids = result["document_ids"]
```

**DB upsert pattern** (from RESEARCH.md §7 — ON CONFLICT DO UPDATE):
```python
    cursor.execute("""
        INSERT INTO chunks (id, document_id, ordinal, content, token_count)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE
            SET content     = EXCLUDED.content,
                token_count = EXCLUDED.token_count,
                ordinal     = EXCLUDED.ordinal
    """, (chunk["id"], doc_id, chunk["ordinal"], chunk["text"], chunk["token_count"]))
```

---

### `app/worker/tasks/pipeline/metadata.py` (Celery task, request-response)

**Analog:** `apps/api/app/worker/tasks/pipeline/migrations.py`

**Task signature and idempotency check** (mirrors migrations.py idempotency guard lines 99–105):
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def generate_metadata(self, result: dict) -> dict:
    ...
    # Idempotency: skip chunks that already have metadata (Layer 3)
    cursor.execute("SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = %s", (chunk_id,))
    if cursor.fetchone()[0] > 0:
        log.info("generate_metadata.already_enriched", chunk_id=chunk_id)
        continue
```

**Chunk_metadata upsert** (from RESEARCH.md §7):
```python
    cursor.execute("""
        INSERT INTO chunk_metadata (chunk_id, summary, keywords, questions)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chunk_id) DO UPDATE
            SET summary   = EXCLUDED.summary,
                keywords  = EXCLUDED.keywords,
                questions = EXCLUDED.questions
    """, (chunk_id, meta.summary, meta.keywords, meta.questions))
```

**Entity upsert** (from CONTEXT.md §Entity Extraction — upsert on UNIQUE(normalized, type)):
```python
    for entity in meta.entities:
        cursor.execute("""
            INSERT INTO entities (name, type, normalized)
            VALUES (%s, %s, %s)
            ON CONFLICT (normalized, type) DO UPDATE
                SET name = EXCLUDED.name
            RETURNING id
        """, (entity.name, entity.type, entity.normalized))
        entity_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO chunk_entities (chunk_id, entity_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (chunk_id, entity_id))
```

---

### `app/worker/tasks/pipeline/embed.py` (Celery task, CRUD)

**Analog:** `apps/api/app/worker/tasks/pipeline/migrations.py`

**Task signature:**
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def embed_and_migrate(self, result: dict) -> dict:
```

**Embedding upsert pattern** (from RESEARCH.md §7 — ON CONFLICT on chunk_id PK):
```python
    cursor.execute("""
        INSERT INTO embeddings (chunk_id, model, vector)
        VALUES (%s, %s, %s::vector)
        ON CONFLICT (chunk_id) DO UPDATE
            SET model      = EXCLUDED.model,
                vector     = EXCLUDED.vector,
                created_at = now()
    """, (str(chunk_id), "voyage-3", str(embedding)))
```

**HNSW reindex** (from RESEARCH.md §7 + PITFALLS.md §5 — after all inserts):
```python
    # Post-insert HNSW reindex (PITFALLS.md §5 — prevents HNSW bloat)
    cursor.execute("REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx")
    conn.commit()
```

**Terminal events** (mirrors migrations.py lines 186–200 — emit complete then job.complete):
```python
    emit(job_id, "ingestion.complete", {"job_id": job_id, "total_chunks": total_chunks}, db, _redis)
    emit(job_id, "job.complete", {"job_id": job_id}, db, _redis)
    # Update job.status = "complete" (mirrors migrations.py lines 173–176)
    job.status = "complete"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
```

---

### `app/api/v1/documents.py` (route/controller, request-response)

**Analog:** `apps/api/app/api/v1/agents.py` — EXACT pattern.

**Imports pattern** (mirrors agents.py lines 20–36):
```python
from uuid import UUID

from celery import chain
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import get_contextvars

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.document import DocumentResponse, DocumentUploadResponse
from app.worker.tasks.pipeline.parse import parse_documents
from app.worker.tasks.pipeline.chunk import chunk_documents
from app.worker.tasks.pipeline.metadata import generate_metadata
from app.worker.tasks.pipeline.embed import embed_and_migrate

router = APIRouter(tags=["documents"])
```

**POST route pattern** (mirrors agents.py lines 40–91 — create rows, dispatch chain, return 202):
```python
@router.post("/agents/{agent_id}/documents", status_code=202, response_model=DocumentUploadResponse)
async def upload_documents(
    agent_id: UUID,
    files: list[UploadFile] = File(default=[]),
    urls: list[str] = Form(default=[]),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> DocumentUploadResponse:
    # 1. Validate agent belongs to tenant (mirrors agents.py GET filter pattern)
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
        raise HTTPException(status_code=409, detail=f"Agent is not ready (status={agent.status})")

    # 2. Validate at least one source
    if not files and not urls:
        raise HTTPException(status_code=422, detail="At least one file or URL is required")

    # 3. Create document rows + job row + dispatch chain (mirrors agents.py lines 53–84)
    ...
    job = Job(tenant_id=tenant.id, agent_id=agent.id, kind="ingest_documents", status="pending")
    db.add(job)
    await db.commit()

    ctx = get_contextvars()
    chain(
        parse_documents.s(str(tenant.id), str(agent.id), str(job.id), document_ids),
        chunk_documents.s(),
        generate_metadata.s(),
        embed_and_migrate.s(),
    ).apply_async(queue="pipeline", headers={"request_id": ctx.get("request_id", "")})

    return DocumentUploadResponse(
        job_id=job.id,
        document_ids=[UUID(d) for d in document_ids],
        status="pending",
        events_url=f"/jobs/{job.id}/events",
    )
```

**GET route pattern** (mirrors agents.py lines 94–116):
```python
@router.get("/agents/{agent_id}/documents", response_model=...)
async def list_documents(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    # Validate agent belongs to tenant
    # Query tenant DB for documents via agent's neon connection
    ...
```

---

### `app/schemas/document.py` (schema)

**Analog:** `apps/api/app/schemas/agent.py` — EXACT pattern.

**Full file pattern** (mirrors agent.py structure lines 1–47):
```python
"""
Pydantic v2 schemas for document endpoints.

DocumentUploadResponse — response body for POST /agents/{id}/documents (202 Accepted)
DocumentResponse       — response body for GET /agents/{id}/documents
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentUploadResponse(BaseModel):
    job_id: UUID
    document_ids: list[UUID]
    status: str
    events_url: str


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_uri: str
    source_type: str
    title: str | None
    parse_status: str
    chunk_count: int | None
    created_at: datetime
```

---

### `scripts/demo_m2.sh` (script)

**Analog:** `scripts/demo_m1.sh` — EXACT pattern.

**Shell header pattern** (mirrors demo_m1.sh lines 1–19):
```bash
#!/usr/bin/env bash
# demo_m2.sh — Veridian M2 ingestion pipeline demo
#
# Prerequisites:
#   - docker-compose services running (docker compose up -d)
#   - ADMIN_KEY env var set
#   - jq installed
#   - tests/fixtures/demo_business.pdf exists
#
# Usage:
#   ADMIN_KEY=vrd_admin_... bash scripts/demo_m2.sh

set -euo pipefail
API="${API_BASE:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:?ADMIN_KEY env var required}"
PDF_PATH="${PDF_PATH:-apps/api/tests/fixtures/demo_business.pdf}"
```

**SSE streaming pattern** (mirrors demo_m1.sh lines 70–80):
```bash
while IFS= read -r line; do
  if [[ "$line" == event:* ]]; then
    EVENT_TYPE="${line#event: }"
    EVENTS_SEEN+=("$EVENT_TYPE")
    echo "  event: $EVENT_TYPE"
  fi
  if [[ "${EVENTS_SEEN[*]:-}" == *"job.complete"* ]] || \
     [[ "${EVENTS_SEEN[*]:-}" == *"job.failed"* ]]; then
    break
  fi
done < <(timeout 300 curl -N -s -H "X-API-Key: $API_KEY" "$API$EVENTS_URL" 2>&1 || true)
```

**Upload via multipart** (new for M2 — uses -F flag):
```bash
UPLOAD_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/agents/$AGENT_ID/documents" \
  -H "X-API-Key: $API_KEY" \
  -F "files=@$PDF_PATH")
```

**Verification** (mirrors demo_m1.sh lines 103–106 — inspect DB directly):
```bash
docker compose exec db psql -U veridian -d veridian_control \
  -c "SELECT COUNT(*) AS chunk_count FROM chunks;" 2>/dev/null || true
```

---

### `tests/unit/test_chunk_id.py` (test, utility)

**Analog:** `apps/api/tests/unit/test_task_args.py` — pure unit test, no fixtures needed.

**Test class pattern** (mirrors test_task_args.py class structure):
```python
"""Unit tests for app.utils.chunk_id.deterministic_chunk_id — ING-05."""

import uuid
import pytest
from app.utils.chunk_id import deterministic_chunk_id, CHUNK_UUID_NAMESPACE


class TestDeterministicChunkId:
    def test_same_inputs_produce_same_id(self):
        id1 = deterministic_chunk_id("doc-uuid-123", 0)
        id2 = deterministic_chunk_id("doc-uuid-123", 0)
        assert id1 == id2

    def test_different_ordinals_produce_different_ids(self):
        id0 = deterministic_chunk_id("doc-uuid-123", 0)
        id1 = deterministic_chunk_id("doc-uuid-123", 1)
        assert id0 != id1

    def test_different_document_ids_produce_different_ids(self):
        id_a = deterministic_chunk_id("doc-aaa", 0)
        id_b = deterministic_chunk_id("doc-bbb", 0)
        assert id_a != id_b

    def test_returns_uuid_instance(self):
        result = deterministic_chunk_id("doc-uuid-123", 5)
        assert isinstance(result, uuid.UUID)

    def test_uses_correct_namespace(self):
        """uuid5 must use CHUNK_UUID_NAMESPACE (uuid.NAMESPACE_URL)."""
        expected = uuid.uuid5(CHUNK_UUID_NAMESPACE, "doc-uuid-123:0")
        assert deterministic_chunk_id("doc-uuid-123", 0) == expected
```

---

### `tests/unit/test_parse_task.py` (test, Celery task)

**Analog:** `apps/api/tests/unit/test_emit.py` — env vars at top, MagicMock pattern.

**File header + env pattern** (mirrors test_emit.py lines 1–31):
```python
"""Unit tests for parse_documents Celery task — ING-02."""

import os
import base64

os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage")
```

**Mock Docling pattern** (module-level patch, from RESEARCH.md §11):
```python
from unittest.mock import MagicMock, patch

@patch("app.services.docling_service._converter")
def test_parse_documents_idempotency(mock_converter):
    """parse_documents skips if document.parse_status == 'parsed'."""
    # Arrange: mock doc already parsed
    # Assert: _converter.convert NOT called
    mock_converter.convert.assert_not_called()
```

**acks_late assertion pattern** (mirrors test_task_args.py lines 89–93):
```python
from app.worker.tasks.pipeline.parse import parse_documents

def test_parse_documents_acks_late():
    assert parse_documents.acks_late is True
```

---

### `tests/unit/test_chunk_task.py` (test, Celery task)

**Analog:** `apps/api/tests/unit/test_emit.py`

**Table path test pattern:**
```python
def test_table_chunk_produces_markdown():
    """TableItem goes through Markdown path, not HybridChunker — ING-03."""
    # Build minimal DoclingDocument mock with one table
    mock_table = MagicMock()
    mock_table.export_to_markdown.return_value = "| Col1 | Col2 |\n|---|---|\n| A | B |"
    mock_doc = MagicMock()
    mock_doc.tables = [mock_table]
    mock_doc.pictures = []

    with patch("app.services.chunking_service.HybridChunker") as mock_chunker_cls:
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = []  # no text chunks
        mock_chunker_cls.return_value = mock_chunker

        from app.services.chunking_service import chunk_document
        chunks = chunk_document(mock_doc, "doc-uuid-abc")

    assert len(chunks) == 1
    assert chunks[0]["is_table"] is True
    assert "|" in chunks[0]["text"]  # Markdown table markers present
```

---

### `tests/unit/test_metadata_task.py` (test, Celery task)

**Analog:** `apps/api/tests/unit/test_emit.py`

**Mocked Anthropic pattern:**
```python
from unittest.mock import MagicMock, patch

@patch("app.services.metadata_service._anthropic")
def test_enrich_chunk_returns_structured_output(mock_client):
    """enrich_chunk returns ChunkMetadataAndEntities — ING-06."""
    mock_result = MagicMock()
    mock_result.parsed_output.summary = "A product summary."
    mock_result.parsed_output.keywords = ["product", "catalog"]
    mock_result.parsed_output.questions = ["What products are available?"]
    mock_result.parsed_output.entities = []
    mock_client.messages.parse.return_value = mock_result

    from app.services.metadata_service import enrich_chunk
    result = enrich_chunk("Some chunk text.")
    assert result.summary == "A product summary."
```

**Idempotency skip test:**
```python
def test_generate_metadata_skips_existing(mock_db_cursor):
    """generate_metadata skips Haiku call if chunk_metadata row already exists."""
    mock_db_cursor.fetchone.return_value = (1,)  # count > 0
    # Assert: _anthropic.messages.parse NOT called
```

---

### `tests/unit/test_embed_task.py` (test, Celery task)

**Analog:** `apps/api/tests/unit/test_emit.py`

**Mocked Voyage pattern:**
```python
@patch("app.services.embedding_service._vo")
def test_embed_chunks_batches_correctly(mock_vo):
    """embed_chunks splits into batches of 128 — ING-07."""
    mock_vo.embed.return_value = MagicMock(embeddings=[[0.1] * 1024] * 128)
    texts = [f"chunk {i}" for i in range(256)]

    from app.services.embedding_service import embed_chunks
    result = embed_chunks(texts)

    assert mock_vo.embed.call_count == 2  # 256 / 128 = 2 batches
    assert len(result) == 256
    assert len(result[0]) == 1024  # voyage-3 dimension
```

---

### `tests/unit/test_document_routes.py` (test, route)

**Analog:** `apps/api/tests/unit/test_routes.py` (FastAPI TestClient pattern).

**Route test pattern** (async TestClient, mirrors existing route tests):
```python
"""Unit tests for POST/GET /agents/{id}/documents — ING-01."""

import pytest
from httpx import AsyncClient

from app.main import app  # FastAPI app instance


@pytest.mark.anyio
async def test_upload_documents_returns_202(mock_async_redis, sample_agent_id):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            f"/agents/{sample_agent_id}/documents",
            headers={"X-API-Key": "test_key"},
            files={"files": ("test.pdf", b"%PDF-1.4 mock", "application/pdf")},
        )
    assert response.status_code == 202
    assert "job_id" in response.json()
    assert "events_url" in response.json()
```

---

### `tests/integration/test_ingestion_chain.py` (test, integration)

**Analog:** `apps/api/tests/integration/test_chain.py` — EXACT pattern.

**Integration test structure** (mirrors test_chain.py lines 1–28):
```python
"""Integration tests: Full ingestion chain — parse → chunk → metadata → embed.

- Real local Postgres DB
- Mocked Voyage + Anthropic via unittest.mock
- CELERY_TASK_ALWAYS_EAGER=True (unit-level), or real worker subprocess
- Idempotency: run chain twice, assert no duplicate rows
"""
import pytest
pytestmark = pytest.mark.integration
```

**Idempotency test pattern** (from RESEARCH.md §11):
```python
@pytest.mark.integration
def test_idempotent_chain(db_session, test_agent_and_job):
    """Running the ingestion chain twice produces no duplicate rows — ING-09."""
    # Run chain once
    # Assert: chunk count = N
    # Run chain again with same document
    # Assert: chunk count still = N (no duplicates)
    # Assert: embeddings count = N (no duplicates via ON CONFLICT)
```

---

### `tests/integration/test_ingestion_e2e.py` (test, e2e)

**Analog:** `apps/api/tests/e2e/test_neon_e2e.py` — skipif guard pattern.

**E2E skip guard pattern** (from RESEARCH.md §11):
```python
import os
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("INGESTION_E2E_ENABLED"),
    reason="Set INGESTION_E2E_ENABLED=1 to run e2e ingestion tests against real APIs",
)
def test_real_pdf_ingestion(db_session, test_agent_and_job):
    """ING-10: Upload real business PDF, verify chunks, chunk_metadata, and entities tables."""
    # Uses tests/fixtures/demo_business.pdf
    # Asserts: chunks > 5 rows, embeddings dimension 1024, entities table populated
    # Asserts: re-run produces same chunk count (idempotency)
```

---

## Shared Patterns

### Celery Task Decorator (ALL four pipeline tasks)

**Source:** `apps/api/app/worker/tasks/pipeline/provision.py` lines 64–70

Apply to: `parse.py`, `chunk.py`, `metadata.py`, `embed.py`

```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
```

CLAUDE.md rule: `acks_late=True` AND idempotency are BOTH always required. Neither is optional.

---

### Module-Level Redis Client (ALL four pipeline tasks)

**Source:** `apps/api/app/worker/tasks/pipeline/provision.py` lines 59–61

Apply to: `parse.py`, `chunk.py`, `metadata.py`, `embed.py`

```python
import redis as redis_lib
from app.core.config import settings

_redis = redis_lib.from_url(settings.REDIS_URL)
```

Each task module declares its own `_redis` (one per worker process). Do not share across modules.

---

### Tenant DB Connection Fetch (ALL four pipeline tasks)

**Source:** `apps/api/app/worker/tasks/pipeline/migrations.py` lines 120–124

Apply to: `parse.py`, `chunk.py`, `metadata.py`, `embed.py`

```python
from app.core.security import fernet_decrypt
from app.core.database import get_sync_db

with get_sync_db() as db:
    agent = db.get(Agent, agent_id)
    conn_str = fernet_decrypt(agent.neon_connection_string)

import psycopg2
conn = psycopg2.connect(conn_str)
```

Connection strings NEVER passed in task args (CLAUDE.md non-negotiable rule). Always fetched and decrypted at runtime.

---

### emit() Call Pattern (ALL four pipeline tasks)

**Source:** `apps/api/app/services/events.py` full file

Apply to: `parse.py`, `chunk.py`, `metadata.py`, `embed.py`

```python
from app.services.events import emit

emit(job_id, "parsing.started", {"document_id": str(doc_id)}, db, _redis)
```

`db` is the control DB sync session. `_redis` is the module-level Redis client. `job_id` is a UUID from the task args (not a connection string). Payload dict is copied inside emit() — safe to pass mutable dicts.

---

### structlog Logging (ALL new modules)

**Source:** `apps/api/app/worker/tasks/pipeline/provision.py` line 56

Apply to: all tasks, all services

```python
import structlog
log = structlog.get_logger(__name__)

log.info("parse_documents.complete", document_id=str(doc_id), chunk_count=len(chunks))
log.error("parse_documents.error", document_id=str(doc_id), error=str(exc))
```

Key names follow `module_name.event_name` convention. Connection strings and API keys NEVER passed to any log call.

---

### Settings Extension Pattern

**Source:** `apps/api/app/core/config.py` lines 12–47

Apply to: `app/core/config.py` (modification — add three new fields)

```python
# Add after existing CORS_ORIGINS field:
ANTHROPIC_API_KEY: str
VOYAGE_API_KEY: str
MAX_UPLOAD_SIZE_MB: int = 50
```

`__repr__` suppression already in place — new secret fields are automatically protected.

---

### Test Environment Setup (ALL unit tests)

**Source:** `apps/api/tests/conftest.py` lines 29–51

Apply to: all new unit test files that import app modules

```python
import os, base64
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")
# NEW for M2:
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")
```

CRITICAL: env vars must be set BEFORE any `from app` or `import app` statement.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `app/utils/chunk_id.py` | utility | transform | No existing utility module in codebase; stdlib-only, no analog needed |
| `app/utils/sanitize.py` | utility | transform | No existing sanitization utility; stdlib-only, pattern from PITFALLS.md §11 |

Both files are simple stdlib-only modules (<30 lines). Planner should use RESEARCH.md patterns directly.

---

## Metadata

**Analog search scope:** `apps/api/app/`, `apps/api/tests/`, `apps/api/alembic_tenant/`, `scripts/`
**Files scanned:** 16 source files read directly
**Pattern extraction date:** 2026-05-13
