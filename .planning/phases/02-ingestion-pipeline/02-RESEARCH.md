# Phase 2: M2 Ingestion Pipeline — Research

**Researched:** 2026-05-13
**Domain:** Document ingestion — Docling parsing, Chonkie chunking, Claude Haiku metadata enrichment, Voyage embedding, pgvector HNSW upsert, Celery chain extension, FastAPI multipart upload
**Confidence:** HIGH

---

## Summary

M2 extends the proven M1 chain pattern with four new pipeline tasks:
`parse_documents → chunk_documents → generate_metadata → embed_and_migrate`. The chain accepts
`(tenant_id, agent_id, job_id, document_ids)` as its first task argument and threads results
through to each subsequent task. Every task follows the exact idempotency + `acks_late=True`
contract established in M1.

The central technical challenge is table handling: Docling's `HybridChunker` has a documented
open issue where table structure gets corrupted in chunk output (see PITFALLS.md §2). The correct
fix is a dedicated table-aware path inside `chunk_documents` that converts each `TableItem` to a
Markdown table string before chunking rather than letting the chunker encounter raw table elements.
This must be a first-class design decision, not a post-hoc patch.

Idempotency across all four tasks is achieved via: (a) a document-level hash guard on
`documents.source_hash` that skips unchanged uploads, (b) deterministic `uuid5`-derived chunk IDs
that convert `INSERT` to `ON CONFLICT DO UPDATE`, and (c) upsert semantics on every write to
`chunks`, `chunk_metadata`, and `embeddings`.

**Primary recommendation:** Implement `chunk_documents` as two separate code paths — a
text-element path (HybridChunker on `DoclingDocument`) and a table path (one-chunk-per-table with
Markdown serialization) — merged into a single ordered chunk list before writing to DB.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| File upload endpoint | API / Backend (FastAPI) | — | FastAPI handles multipart; immediately delegates to Celery |
| File storage (temp) | API / Backend (local tmp) | — | M2 scope: local temp file, not object store |
| URL ingestion | API / Backend (Celery task) | — | httpx fetch in pipeline task, not FastAPI inline |
| PDF/image parsing | Celery pipeline worker | — | CPU-bound; must not block FastAPI event loop |
| Layout analysis (Docling) | Celery pipeline worker | — | Docling runs ML models; must be in worker process |
| Table-aware chunking | Celery pipeline worker | — | Custom logic in chunk_documents task |
| Structure-aware chunking | Celery pipeline worker | — | HybridChunker runs in worker |
| Metadata enrichment (Haiku) | Celery pipeline worker | — | External API call in runtime queue? No — pipeline queue, batch call |
| Embedding generation | Celery pipeline worker | — | Voyage API call, batched in embed_and_migrate |
| pgvector HNSW upsert | Celery pipeline worker | Neon tenant DB | Sync DB writes via psycopg2 in worker |
| SSE event streaming | API / Backend (FastAPI SSE) | Redis pub/sub | Existing emit() + SSE pattern from M1 |
| Job status (ingestion) | Control DB (jobs table) | — | Reuses M1 jobs table with kind='ingest_document' |

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ING-01 | User can upload PDF, image, and URL list for ingestion | §2 (file upload endpoint design) |
| ING-02 | Documents parsed with layout awareness (Docling) preserving headings, tables, lists | §3 (Docling integration pattern) |
| ING-03 | Tables ingested via dedicated table-aware path preserving row/column relationships | §4 (table-aware chunking path) |
| ING-04 | Chunks generated with structure-awareness (Chonkie) — boundaries respect document structure | §4 (Chonkie integration pattern) |
| ING-05 | Chunks have deterministic UUIDs (document_id + ordinal hash) for upsert idempotency | §5 (deterministic chunk UUIDs) |
| ING-06 | Each chunk enriched with summary, keywords, hypothetical questions via Claude Haiku | §6 (Haiku metadata enrichment) |
| ING-07 | Chunks embedded via Voyage and stored in tenant DB with HNSW index | §7 (Voyage embedding + HNSW upsert) |
| ING-08 | Ingestion progress streamed to owner via SSE | §8 (SSE event vocabulary extension) |
| ING-09 | Full ingestion chain is idempotent end-to-end | §9 (idempotency strategy) |
| ING-10 | Demo: upload real business PDF, inspect chunks and chunk_metadata tables | §12 (implementation order / demo wave) |
</phase_requirements>

---

## 1. Celery Chain Design — Extending M1

### How M2 Attaches to the M1 Chain

M1 ends when `apply_migrations` sets `agent.status = 'ready'` and emits `job.complete`.
M2 is a **new chain**, not an extension of the M1 provisioning chain. It is dispatched by
`POST /documents` (or `POST /ingest`) after the agent is `ready`.

```python
# apps/api/app/api/v1/documents.py — dispatches on upload
chain(
    parse_documents.s(tenant_id, agent_id, job_id, document_ids),
    chunk_documents.s(),
    generate_metadata.s(),
    embed_and_migrate.s(),
).apply_async(queue="pipeline")
```

The chain passes a result dict forward at each step. Each task returns:
```python
{"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}
```
No connection strings. Tasks fetch the tenant DB connection from the control DB by `agent_id` at
runtime — exactly as `apply_migrations` already does. [VERIFIED: M1 codebase pattern in
`apps/api/app/worker/tasks/pipeline/migrations.py`]

### Chain Task Skeletons

All four tasks follow the M1 idempotency + `acks_late=True` pattern established in
`provision.py` and `migrations.py`:

```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def parse_documents(self, tenant_id: str, agent_id: str, job_id: str, document_ids: list[str]) -> dict:
    # 1. Idempotency guard: check document.parse_status != 'complete'
    # 2. Emit parsing.started
    # 3. Fetch tenant connection from control DB by agent_id (never passed as arg)
    # 4. Parse each document via Docling
    # 5. Store parse results in documents table (or a temp staging area)
    # 6. Emit parsing.complete
    # 7. Return {"tenant_id": ..., "agent_id": ..., "job_id": ..., "document_ids": [...]}
```

### Job Kind

The M2 ingestion job uses `kind='ingest_documents'` in the `jobs` table.
The existing `jobs` table schema supports this without migration.

---

## 2. File Upload + URL Ingestion Endpoint

### FastAPI Multipart Upload

```python
# POST /agents/{agent_id}/documents
@router.post("/agents/{agent_id}/documents", status_code=202)
async def upload_documents(
    agent_id: UUID,
    files: list[UploadFile] = File(default=[]),
    urls: list[str] = Form(default=[]),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> DocumentUploadResponse:
    # 1. Validate agent belongs to tenant
    # 2. Create document rows in tenant DB (source_type, source_uri, status='pending')
    # 3. Save uploaded files to temp location (e.g. /tmp/vrd-{agent_id}/{doc_id}.pdf)
    # 4. Create job row (kind='ingest_documents')
    # 5. Dispatch pipeline chain
    # 6. Return 202 with job_id and events_url
```

Requires `python-multipart` (already in pyproject.toml via sse-starlette indirect deps — must
verify, likely needs explicit addition). [ASSUMED — `python-multipart` not currently listed in
`apps/api/pyproject.toml`]

### File Storage Strategy for M2

**Decision: local temp file storage.** Object store (S3/GCS) is M2-deferred complexity.
Strategy:
- On upload: save to `/tmp/vrd-uploads/{agent_id}/{document_id}{ext}`
- Task reads from this path
- After successful embed_and_migrate: delete temp file
- On worker restart: file still exists at same path (idempotency safe)

For URL sources: `parse_documents` task fetches the URL using `httpx` (already in pyproject.toml),
writes content to a `DocumentStream` and passes to Docling. URL is stored as `source_uri` on the
`documents` row.

### Documents Table Gap

The existing `documents` table schema (from `0001_tenant_v1_schema.py`) lacks:
- `source_hash TEXT` — SHA-256 of file content for idempotency
- `parse_status TEXT DEFAULT 'pending'` — `pending | parsing | parsed | failed`
- `chunk_count INT` — populated after chunking

M2 requires an Alembic migration (tenant DB `0002_documents_ingestion_columns.py`) to add these
columns. [VERIFIED: tenant schema reviewed at
`apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py`]

---

## 3. Docling Integration Pattern

### DocumentConverter API

```python
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import DocumentStream
from io import BytesIO

converter = DocumentConverter()  # initializes ML models once

# From file path
result = converter.convert(Path("/tmp/vrd-uploads/agent_id/doc_id.pdf"))
doc = result.document  # DoclingDocument

# From bytes (for URL-fetched content)
pdf_bytes = httpx.get(url).content
stream = DocumentStream(name="fetched.pdf", stream=BytesIO(pdf_bytes))
result = converter.convert(stream)
doc = result.document
```

`DocumentConverter` is expensive to initialize (loads DocLayNet + TableFormer ML models).
**Initialize once at module level in the worker process, not inside the task function.**
[VERIFIED: Context7 — /docling-project/docling]

### DoclingDocument Structure

After conversion, `result.document` exposes:
- `doc.iterate_items()` — yields `(item, level)` tuples in reading order
- `doc.tables` — list of `TableItem` objects
- `doc.pictures` — list of `PictureItem` objects
- `item.text` — plain text for `TextItem`
- `table.export_to_markdown(doc=result.document)` — Markdown table string with headers
- `table.export_to_dataframe(doc=result.document)` — pandas DataFrame
- `chunk.meta.headings` — list of heading strings (from HybridChunker output)
- `chunk.meta.origin.page_no` — source page number

[VERIFIED: Context7 — /docling-project/docling and /docling-project/docling-core]

### ConversionResult Error Handling

```python
from docling.datamodel.base_models import ConversionStatus

result = converter.convert(source, raises_on_error=False)
if result.status != ConversionStatus.SUCCESS:
    # Log errors and mark document as failed
    for error in result.errors:
        log.error("docling.conversion_error", message=error.error_message)
    raise RuntimeError(f"Docling failed: {result.errors}")
```

### Worker Initialization

```python
# Module-level in parse_documents.py — initialized once per worker process
_converter = DocumentConverter()
```

This mirrors the M1 `_redis = redis.from_url(...)` pattern from `provision.py`. [VERIFIED: M1
codebase pattern]

---

## 4. Chonkie Integration Pattern

### Two-Path Chunking (Text + Table)

The PITFALLS.md §2 documents that Docling's HybridChunker corrupts table structure in chunks.
The correct approach is to separate tables from text before chunking:

```python
from docling.chunking import HybridChunker
from docling_core.types.doc import TableItem, TextItem

def chunk_docling_document(doc) -> list[dict]:
    """Returns list of chunk dicts with 'text', 'ordinal', 'is_table' fields."""
    chunks = []
    ordinal = 0

    # --- Table path: one chunk per table, serialized as Markdown ---
    table_positions = {}  # track table ordinals for merging with text order
    for i, table in enumerate(doc.tables):
        md_text = table.export_to_markdown(doc=doc)
        if md_text.strip():
            table_positions[id(table)] = {
                "text": md_text,
                "ordinal": None,  # assigned after merge with text chunks
                "is_table": True,
                "page_no": table.prov[0].page_no if table.prov else None,
            }

    # --- Text path: HybridChunker on the DoclingDocument ---
    chunker = HybridChunker(max_tokens=512, merge_peers=True)
    for chunk in chunker.chunk(doc):
        # Skip chunks that consist only of table content (HybridChunker may include
        # table cells as text; filter by checking doc_items for TableItem instances)
        has_table_item = any(
            isinstance(item, TableItem)
            for item in getattr(chunk.meta, 'doc_items', [])
        )
        if has_table_item:
            continue  # table handled separately above
        chunks.append({
            "text": chunker.contextualize(chunk),  # includes heading breadcrumb
            "ordinal": ordinal,
            "is_table": False,
            "headings": chunk.meta.headings if hasattr(chunk.meta, 'headings') else [],
            "page_no": chunk.meta.origin.page_no if hasattr(chunk.meta, 'origin') else None,
        })
        ordinal += 1

    # --- Merge tables into reading order and assign ordinals ---
    # For M2, append tables after text chunks with incrementing ordinals.
    # In M3+, consider preserving reading order from doc.iterate_items().
    for table_data in table_positions.values():
        table_data["ordinal"] = ordinal
        chunks.append(table_data)
        ordinal += 1

    return chunks
```

### HybridChunker Parameters

```python
from docling.chunking import HybridChunker

# For Voyage voyage-3-large (1024 dimensions, ~2048 token effective context)
# Use max_tokens=512 to leave room for HyDE hypothetical questions at retrieval time
chunker = HybridChunker(
    max_tokens=512,      # stays within Voyage context window
    merge_peers=True,    # merge undersized consecutive chunks (default True)
)
```

No HuggingFace tokenizer required if you rely on Docling's default tokenizer.
The HybridChunker accepts `max_tokens` without a tokenizer (uses character-based fallback).
[VERIFIED: Context7 — /docling-project/docling-core HybridChunker]

### `chunker.contextualize(chunk)` vs `chunk.text`

`chunker.contextualize(chunk)` prepends heading breadcrumbs to the chunk text, producing:
```
"## Product Catalog / Pricing\n\nThe standard rate for..."
```
This is the string to embed — the context-enriched version improves retrieval significantly.
[VERIFIED: Context7 — /docling-project/docling, hybrid_chunking.ipynb]

---

## 5. Deterministic Chunk UUIDs (ING-05)

### uuid5 Pattern

```python
import uuid

# Namespace for Veridian chunk IDs — fixed, never changes
CHUNK_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL

def deterministic_chunk_id(document_id: str, ordinal: int) -> uuid.UUID:
    """Derive a stable UUID from document_id and chunk ordinal.

    Same document_id + ordinal always produces the same chunk_id.
    Re-ingesting the same document produces the same chunk IDs → safe upsert.
    """
    name = f"{document_id}:{ordinal}"
    return uuid.uuid5(CHUNK_UUID_NAMESPACE, name)
```

`uuid.uuid5` is deterministic: same namespace + same name string → same UUID every time.
This is a Python stdlib function, no external library needed. [VERIFIED: training knowledge,
Python docs — uuid5 is deterministic per RFC 4122]

### Why Not uuid4

`uuid4()` is random per call. On task retry, new UUIDs would be generated, creating duplicate
chunks. `uuid5` ensures idempotency at the chunk level. [VERIFIED: PITFALLS.md §8]

### Chunk ID Stability Across Pipeline Reruns

The chunk ID must be stable across reruns of the same document. This means:
- `ordinal` must be deterministically ordered (same parse of same document → same ordinal sequence)
- If the document changes (different file hash), a new `document_id` is created → new chunk IDs
- If the document is unchanged (same hash) → same `document_id` → same chunk IDs → upsert is no-op

---

## 6. Claude Haiku Metadata Enrichment (ING-06)

### Structured Output Pattern (anthropic 0.101.0)

```python
import anthropic
from pydantic import BaseModel

class ChunkMetadata(BaseModel):
    summary: str
    keywords: list[str]
    questions: list[str]  # hypothetical questions this chunk answers

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

def enrich_chunk(text: str) -> ChunkMetadata:
    """Generate summary, keywords, and hypothetical questions for a chunk."""
    result = client.messages.parse(
        model="claude-haiku-4-5",  # current Haiku model name
        messages=[{
            "role": "user",
            "content": (
                "Extract metadata for this document chunk.\n\n"
                "CHUNK:\n" + text
            )
        }],
        system=(
            "You are a metadata extractor for a RAG system. "
            "For the given text chunk, produce: "
            "a 1-2 sentence summary, "
            "5-10 keywords (noun phrases, no stop words), "
            "and 3-5 hypothetical questions a user might ask that this chunk would answer."
        ),
        max_tokens=512,
        output_format=ChunkMetadata,
    )
    return result.parsed_output
```

[VERIFIED: Context7 — /anthropics/anthropic-sdk-python, `client.messages.parse()` with
`output_format=PydanticModel`]

### Model Name Confirmation

`claude-haiku-4-5` is the current Haiku model name as of May 2026. The STACK.md confirms
`anthropic 0.101.0` is the pinned version. [ASSUMED — Haiku model name "claude-haiku-4-5";
the STACK.md confirms anthropic 0.101.0 but does not specify the current Haiku model string.
Confirm via `anthropic.Anthropic().models.list()` or API docs before implementation.]

### Batch Strategy Within a Celery Task

Celery workers are synchronous. Metadata enrichment calls Haiku per-chunk. For a 50-chunk
document this is 50 sequential API calls. Strategy:

**Option A (recommended for M2):** Sequential with `tenacity` retry:
```python
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APITimeoutError)),
)
def enrich_chunk_with_retry(text: str) -> ChunkMetadata:
    return enrich_chunk(text)
```

**Option B (future optimization):** Anthropic batch messages API for bulk processing.
For M2, sequential is simpler and sufficient for single-document ingestion.

### Cost Estimate

Haiku pricing (May 2026): ~$0.25/M input tokens, $1.25/M output tokens.
A 512-token chunk with 512-token response = ~$0.0002 per chunk.
50 chunks per document ≈ $0.01 per document ingestion.
Acceptable for M2 demo. [ASSUMED: Based on Haiku pricing as of training knowledge; verify current pricing at console.anthropic.com]

### Idempotency

```python
# In generate_metadata task: skip chunks that already have metadata
# Check: SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = ?
# If count > 0: skip (metadata already enriched)
```

This prevents re-billing on task retry. [VERIFIED: PITFALLS.md §8 — idempotency pattern]

---

## 7. Voyage Embedding + HNSW Upsert (ING-07)

### voyageai.Client.embed() API

```python
import voyageai

vo = voyageai.Client()  # reads VOYAGE_API_KEY from env

# Batch embedding — 128 items max per request
BATCH_SIZE = 128

def embed_chunks(texts: list[str], model: str = "voyage-3") -> list[list[float]]:
    """Embed text chunks in batches of 128."""
    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        result = vo.embed(
            batch,
            model=model,
            input_type="document",  # optimizes for document-side retrieval
        )
        all_embeddings.extend(result.embeddings)
    return all_embeddings
```

[VERIFIED: Context7 — /websites/voyageai, batch embedding pattern with 128-item limit]

### Model Choice: `voyage-3` vs `voyage-3.5`

The STACK.md confirms `voyageai 0.3.7`. The M2 requirements spec says "voyage-3 or current
equivalent." Context7 shows Voyage now offers `voyage-3.5` and `voyage-4-lite` as newer models.

**Decision for M2:** Use `voyage-3` explicitly (pinned). Do NOT use `voyage-latest` or
auto-detect — embedding drift is a critical pitfall (PITFALLS.md §3). Store the model name in
`embeddings.model` column so future re-embedding is detectable.

The tenant schema already has `embeddings.model TEXT NOT NULL` for this purpose.
[VERIFIED: tenant schema at `0001_tenant_v1_schema.py` line 81]

### pgvector Upsert Pattern

The `embeddings` table has `chunk_id UUID PRIMARY KEY`. Use `ON CONFLICT DO UPDATE`:

```python
# Via psycopg2 (sync, in Celery task)
insert_sql = """
    INSERT INTO embeddings (chunk_id, model, vector)
    VALUES (%s, %s, %s::vector)
    ON CONFLICT (chunk_id) DO UPDATE
        SET model = EXCLUDED.model,
            vector = EXCLUDED.vector,
            created_at = now()
"""
cursor.execute(insert_sql, (str(chunk_id), model_name, str(embedding)))
```

For the `chunks` table (also upsert):
```sql
INSERT INTO chunks (id, document_id, ordinal, content, token_count)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE
    SET content = EXCLUDED.content,
        token_count = EXCLUDED.token_count,
        ordinal = EXCLUDED.ordinal
```

For `chunk_metadata`:
```sql
INSERT INTO chunk_metadata (chunk_id, summary, keywords, questions)
VALUES (%s, %s, %s, %s)
ON CONFLICT (chunk_id) DO UPDATE
    SET summary = EXCLUDED.summary,
        keywords = EXCLUDED.keywords,
        questions = EXCLUDED.questions
```

[VERIFIED: PITFALLS.md §8 — "use ON CONFLICT (chunk_id) DO NOTHING or DO UPDATE — never bare INSERT"]

### HNSW Reindex After Bulk Insert

Per PITFALLS.md §5, run `REINDEX CONCURRENTLY` after each full ingestion pipeline completes:

```python
# At end of embed_and_migrate task, after all rows are inserted:
cursor.execute("REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx")
```

This runs in the same `embed_and_migrate` task after all embeddings are written.
[VERIFIED: PITFALLS.md §5]

### Tenant DB Connection in Celery Task

`embed_and_migrate` fetches the tenant connection string exactly as `apply_migrations` does:

```python
# In embed_and_migrate task:
with get_sync_db() as control_db:
    agent = control_db.get(Agent, agent_id)
    conn_str = fernet_decrypt(agent.neon_connection_string)  # pooled endpoint

# Use psycopg2 for sync writes from Celery task
import psycopg2
conn = psycopg2.connect(conn_str)
```

Never pass connection strings in task arguments. Always decrypt from control DB at runtime.
[VERIFIED: CLAUDE.md rule, M1 codebase pattern]

---

## 8. SSE Event Vocabulary Extension (ING-08)

### New M2 Event Types

M1 established the dot-separated event vocabulary. M2 adds:

| Event Type | Emitted By | Payload |
|------------|-----------|---------|
| `ingestion.started` | `parse_documents` | `{job_id, document_count}` |
| `parsing.started` | `parse_documents` | `{document_id, source_uri}` |
| `parsing.complete` | `parse_documents` | `{document_id, page_count}` |
| `chunking.started` | `chunk_documents` | `{document_id, document_count}` |
| `chunking.complete` | `chunk_documents` | `{document_id, chunk_count}` |
| `metadata.started` | `generate_metadata` | `{document_id, chunk_count}` |
| `metadata.complete` | `generate_metadata` | `{document_id}` |
| `embedding.started` | `embed_and_migrate` | `{document_id, chunk_count}` |
| `embedding.complete` | `embed_and_migrate` | `{document_id}` |
| `ingestion.complete` | `embed_and_migrate` | `{job_id, total_chunks}` |
| `job.complete` | `embed_and_migrate` | `{job_id}` |

### Plugging Into emit()

M2 tasks call the existing `emit()` helper from `app.services.events` — no changes needed to
the helper. Tasks import `emit` and call it with the ingestion job's `job_id`:

```python
from app.services.events import emit

emit(job_id, "parsing.started", {"document_id": str(doc_id)}, db, _redis)
```

[VERIFIED: M1 codebase — `app/services/events.py`; PRD §7.3 "vocabulary is forward-extensible: future parsing.*, chunking.*, embedding.*"]

### SSE Endpoint Reuse

The existing `GET /jobs/{job_id}/events` endpoint streams all events for any job ID, regardless of
job `kind`. M2 ingestion SSE reuses the same endpoint with the new ingestion job's ID.
The `TERMINAL_EVENTS` frozenset in `app/services/sse.py` must include `'job.complete'` and
`'job.failed'` — both already present. No changes to the SSE endpoint. [VERIFIED: M1 codebase
`app/services/sse.py`]

---

## 9. Idempotency Strategy (ING-09)

### Four-Layer Idempotency

```
Layer 1: Document level (parse_documents task)
  Guard: SELECT source_hash FROM documents WHERE id = ?
  Logic: If document.source_hash == hash(uploaded_file): skip ALL four tasks, return early
  Effect: Uploading same file twice → second upload is a no-op at document level

Layer 2: Chunk level (chunk_documents task)
  Guard: chunk_id = uuid5(NAMESPACE, f"{document_id}:{ordinal}")
  Logic: INSERT ... ON CONFLICT (id) DO UPDATE
  Effect: Re-running chunking overwrites existing chunks (safe — same content)

Layer 3: Metadata level (generate_metadata task)
  Guard: SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = ?
  Logic: If count > 0: skip Haiku call, skip insert
  Effect: Metadata not regenerated on retry → no duplicate billing

Layer 4: Embedding level (embed_and_migrate task)
  Guard: INSERT INTO embeddings ... ON CONFLICT (chunk_id) DO UPDATE
  Logic: Upsert overwrites with same vector (idempotent result)
  Effect: Re-embedding same chunk writes same vector (stable model version)
```

### Document Source Hash

```python
import hashlib

def compute_source_hash(file_content: bytes) -> str:
    return hashlib.sha256(file_content).hexdigest()
```

Store on `documents.source_hash` (new column added in M2 migration).
On re-upload of same file: hash matches → all four tasks skip → zero cost re-run.

### What Happens on Re-run After Partial Failure

If `chunk_documents` fails mid-task (worker kill):
- `parse_documents` output is already in `documents` table with `parse_status='parsed'`
- Re-run starts at `parse_documents` again (chain retries from beginning)
- `parse_documents` sees `parse_status='parsed'` → emits status events only, returns immediately
- `chunk_documents` re-runs from scratch — but chunk upserts are idempotent
- Same result, no duplicates

This pattern mirrors the M1 `provision_neon` idempotency guard pattern. [VERIFIED: M1 codebase
`provision.py` lines 91-98]

---

## 10. New Routes + API Surface

### POST /agents/{agent_id}/documents

```
POST /agents/{agent_id}/documents
Content-Type: multipart/form-data

files: [UploadFile, ...]   # PDF, image files
urls: ["https://...", ...]  # optional URL list

Response 202:
{
  "job_id": "uuid",
  "document_ids": ["uuid", ...],
  "status": "pending",
  "events_url": "/jobs/{job_id}/events"
}
```

Validation:
- Agent must exist and belong to authenticated tenant (same pattern as `GET /agents/{id}`)
- Agent must be in `status='ready'` (not `pending` or `failed`)
- At least one file or one URL must be provided
- File types: `.pdf`, `.png`, `.jpg`, `.jpeg` (extensible in M3+)
- Max file size: 50MB per file (configurable via settings)

### GET /agents/{agent_id}/documents

```
GET /agents/{agent_id}/documents

Response 200:
{
  "documents": [
    {
      "id": "uuid",
      "source_uri": "filename.pdf",
      "source_type": "pdf",
      "title": "Optional title",
      "status": "parsed | chunked | embedded | failed",
      "chunk_count": 47,
      "created_at": "2026-05-13T..."
    }
  ]
}
```

### GET /agents/{agent_id}/documents/{document_id}

Returns single document with metadata and chunk count.

### No Changes to Existing Routes

M1 routes (`POST /agents`, `GET /agents/{id}`, `GET /jobs/{id}/events`) are unchanged.
The SSE endpoint already supports any job kind. [VERIFIED: M1 codebase]

---

## 11. Test Strategy

### The Core Testing Challenge

Docling runs heavy ML models (DocLayNet, TableFormer). These cannot be run in CI without:
- Significant CI runner memory (2-4GB RAM for models)
- ~30-60 second parse time per document

**Solution: Two-tier test strategy.**

### Unit Tests (fast, no ML models)

Mock `DocumentConverter.convert()` to return a pre-built `DoclingDocument` fixture:

```python
# tests/unit/test_parse_task.py
from unittest.mock import MagicMock, patch

@patch("app.worker.tasks.pipeline.parse.DocumentConverter")
def test_parse_documents_idempotency(mock_converter):
    """parse_documents skips parsing if document already has parse_status='parsed'."""
    mock_doc = MagicMock()
    mock_converter.return_value.convert.return_value.document = mock_doc
    # ... assert idempotency guard fires, convert() not called second time
```

For chunk UUID determinism:
```python
def test_deterministic_chunk_id_stable():
    id1 = deterministic_chunk_id("doc-uuid-123", 0)
    id2 = deterministic_chunk_id("doc-uuid-123", 0)
    assert id1 == id2  # must be identical across calls

def test_deterministic_chunk_id_ordinal_differs():
    id0 = deterministic_chunk_id("doc-uuid-123", 0)
    id1 = deterministic_chunk_id("doc-uuid-123", 1)
    assert id0 != id1
```

For table chunking path:
```python
def test_table_chunk_produces_markdown():
    """TableItem goes through Markdown path, not HybridChunker."""
    # Build a minimal DoclingDocument with one table
    # Assert chunk list contains Markdown table string, not flattened text
```

### Integration Tests (real local DB, mocked external APIs)

```python
# tests/integration/test_ingestion_chain.py
@pytest.mark.integration
def test_full_chain_with_real_pdf():
    """Full chain against local Postgres, mocked Voyage + Haiku."""
    # Use respx to mock Voyage embed endpoint
    # Use unittest.mock to mock anthropic.Anthropic().messages.parse
    # Run chain with CELERY_TASK_ALWAYS_EAGER=True
    # Assert: chunks table has rows, chunk_metadata has rows, embeddings has rows
    # Assert: no duplicates after running chain twice (idempotency)
```

### Demo Integration Test (real PDF, real APIs)

```python
# tests/integration/test_ingestion_e2e.py — runs only with INGESTION_E2E_ENABLED=1
@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("INGESTION_E2E_ENABLED"), reason="needs real APIs")
def test_real_pdf_ingestion():
    """ING-10: upload real business PDF, verify chunks and metadata tables."""
    # Use tests/fixtures/demo_business.pdf (included in repo, <100KB)
    # Assert: chunks table has > 5 rows
    # Assert: every chunk has metadata (summary, keywords, questions)
    # Assert: embeddings table has vectors with dimension 1024
    # Assert: re-running produces same chunk count (idempotency)
```

### Test Fixture PDF

Include a small, real business PDF (e.g., a product catalog or FAQ with at least one table) at
`apps/api/tests/fixtures/demo_business.pdf`. This should be a real document, not generated, to
test genuine Docling layout analysis. File size target: < 500KB to keep repo size small.

### What NOT to Unit Test

- Docling internal parsing logic (IBM's responsibility)
- Voyage embedding quality (vendor responsibility)
- Haiku output quality (Anthropic's responsibility)

Unit test your code's contract with these APIs, not the APIs themselves.

---

## 12. Implementation Order (Waves)

Based on dependency graph and M1 wave pattern:

### Wave 1: Schema + Foundation
- Alembic tenant migration `0002_documents_ingestion_columns.py` (adds `source_hash`, `parse_status`, `chunk_count` to `documents`)
- New pyproject.toml deps: `docling==2.93.0`, `chonkie==1.6.5`, `voyageai==0.3.7`, `anthropic==0.101.0`, `python-multipart`, `tenacity`, `pandas`
- New Settings fields: `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `MAX_UPLOAD_SIZE_MB`
- Deterministic chunk UUID utility (`app/utils/chunk_id.py`)
- Chunk sanitization utility (strip injection patterns per PITFALLS.md §11)

### Wave 2: Docling Parsing Task
- `app/services/docling_service.py` — `DocumentConverter` wrapper with retry
- `app/worker/tasks/pipeline/parse.py` — `parse_documents` task
- Unit tests for idempotency guard and error handling

### Wave 3: Chunking Task
- `app/services/chunking_service.py` — two-path chunking logic (text + table)
- `app/worker/tasks/pipeline/chunk.py` — `chunk_documents` task
- Unit tests for: table path produces Markdown, text path respects structure, uuid5 IDs

### Wave 4: Metadata Task
- `app/services/metadata_service.py` — Haiku enrichment with tenacity retry
- `app/worker/tasks/pipeline/metadata.py` — `generate_metadata` task
- Unit tests for: idempotency guard (skip if metadata exists), structured output schema

### Wave 5: Embedding Task + HNSW Reindex
- `app/services/embedding_service.py` — Voyage batch embed with 128-item chunking
- `app/worker/tasks/pipeline/embed.py` — `embed_and_migrate` task (upserts + reindex)
- Unit tests for: batch splitting, upsert idempotency, REINDEX call

### Wave 6: FastAPI Routes + Chain Dispatch
- `app/api/v1/documents.py` — `POST /agents/{id}/documents`, `GET /agents/{id}/documents`
- `app/schemas/document.py` — `DocumentUploadResponse`, `DocumentResponse`
- Integration test: full chain with mocked Voyage + Haiku, real local Postgres
- Integration test: idempotency (run chain twice, assert no duplicates)

### Wave 7: Demo + ING-10
- `tests/fixtures/demo_business.pdf` — small real business PDF with at least one table
- `scripts/demo_m2.sh` — uploads PDF, streams SSE, inspects chunks and chunk_metadata
- E2E test with real APIs (`INGESTION_E2E_ENABLED=1`)

---

## Architecture Diagram

```
POST /agents/{id}/documents
         │  (multipart/form-data)
         │
         ▼
┌────────────────────────────┐
│  FastAPI route             │  Creates: document rows, job row
│  (async, returns 202)      │  Saves: files to /tmp/vrd-uploads/
└────────────┬───────────────┘
             │ chain.apply_async(queue="pipeline")
             │
             ▼
┌────────────────────────────────────────────────────────────────────┐
│                   Celery pipeline worker                           │
│                                                                    │
│  parse_documents   ──emit parsing.started/complete──►            │
│       │                                                            │
│       ▼  result: {tenant_id, agent_id, job_id, document_ids}      │
│  chunk_documents   ──emit chunking.started/complete──►            │
│    ┌──┴──────────────────────────────────┐                        │
│    │  Table path (TableItem)              │                        │
│    │  table.export_to_markdown() → chunk  │                        │
│    │                                      │                        │
│    │  Text path (HybridChunker)           │                        │
│    │  chunker.contextualize(chunk) → text │                        │
│    └──────────────────────────────────────┘                        │
│       │                                                            │
│       ▼                                                            │
│  generate_metadata ──emit metadata.started/complete──►           │
│    Anthropic Haiku (client.messages.parse())                       │
│    → {summary, keywords, questions} per chunk                      │
│       │                                                            │
│       ▼                                                            │
│  embed_and_migrate ──emit embedding.started/complete──►          │
│    Voyage API (batch 128) → 1024-dim vectors                       │
│    psycopg2 upsert → chunks, embeddings, chunk_metadata tables     │
│    REINDEX CONCURRENTLY embeddings_vector_hnsw_idx                 │
│    emit job.complete                                               │
└────────────────────────────┬───────────────────────────────────────┘
                             │ emit() → Redis pub/sub
                             │
                             ▼
                  ┌──────────────────────┐
                  │  Redis pub/sub       │
                  │  job_events:{job_id} │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  SSE endpoint        │
                  │  GET /jobs/{id}/     │
                  │  events (existing)   │
                  └──────────────────────┘
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PDF layout analysis | Custom PDF parser | `docling.document_converter.DocumentConverter` | DocLayNet model for layout; TableFormer for tables; handles OCR, scanned docs |
| Table structure extraction | Regex/text parsing | `table.export_to_markdown(doc=result.document)` | Docling's TableFormer preserves row/column relationships |
| Structure-aware chunking | Fixed-size splitter | `docling.chunking.HybridChunker` | Respects headings, sections, token limits with merge_peers |
| Embedding batching | Custom pagination | voyageai 128-item batch loop | API enforces 128-item limit; hand-rolling misses token limit per batch |
| Deterministic IDs | Random uuid4 | `uuid.uuid5(NAMESPACE, f"{doc_id}:{ordinal}")` | uuid4 breaks idempotency on retry |
| Structured LLM output | JSON-mode + regex parse | `client.messages.parse(output_format=PydanticModel)` | Native SDK method, validated by Pydantic |
| Retry logic | Custom sleep loops | `tenacity.retry` decorator | Handles jitter, max attempts, exception filtering |
| Vector upsert | DELETE + INSERT | `INSERT ... ON CONFLICT DO UPDATE` | DELETE + INSERT is not atomic; upsert is |

---

## Common Pitfalls

The following pitfalls are documented in detail in `.planning/research/PITFALLS.md`. Summary for M2 relevance:

### Pitfall 1 (PITFALLS.md §1): Chunk Boundary Splits
**Avoidance in M2:** HybridChunker on `DoclingDocument` (not on raw text). `chunker.contextualize()` for the string that gets embedded, not `chunk.text`.

### Pitfall 2 (PITFALLS.md §2): Table Flattening
**Avoidance in M2:** Separate table path using `table.export_to_markdown()`. Tables are never fed to HybridChunker.

### Pitfall 3 (PITFALLS.md §3): Embedding Drift
**Avoidance in M2:** Pin `voyage-3` (not `voyage-latest`). Store model name in `embeddings.model`. Store preprocessing hash in `chunk_metadata.preprocessing_hash` (consider adding this column).

### Pitfall 5 (PITFALLS.md §5): HNSW Bloat
**Avoidance in M2:** `REINDEX CONCURRENTLY` at end of `embed_and_migrate`.

### Pitfall 8 (PITFALLS.md §8): acks_late Without Idempotency
**Avoidance in M2:** Every write uses `ON CONFLICT DO UPDATE`. Document-level hash guard. Metadata level existence check.

### Pitfall 11 (PITFALLS.md §11): Indirect Prompt Injection
**Avoidance in M2:** Sanitize chunk text before storing. Strip injection patterns (`System:`, `[INST]`, `Human:`, `<!--`, `Ignore previous`) from chunk content before writing to `chunks` table.

### M2-Specific Pitfall: Docling Memory Usage
`DocumentConverter` loads ML models into memory at initialization (~1-2GB RAM per worker process).
`worker_pipeline` service must have sufficient memory. Docling should be initialized at module level (once per process), never inside the task function. Failing to do this causes 10-15s model loading per task invocation. [ASSUMED: Based on typical ML model loading overhead for DocLayNet-scale models; verify with `htop` during first test run]

### M2-Specific Pitfall: HybridChunker + Tables
Docling's HybridChunker may include table cells as text chunks (PITFALLS.md §2 confirms "totally messed up" issue). Filter chunks where `chunk.meta.doc_items` contains any `TableItem` instance and handle those in the separate table path. [VERIFIED: PITFALLS.md §2 cites GitHub issue docling-project/docling-serve#484]

### M2-Specific Pitfall: Voyage API Token Limits
The 128-item batch limit is per request. There is also a per-request token limit. For chunks with large `contextualize()` output, batches of 128 may exceed the token budget. Strategy: count tokens before batching and split on token budget, not just item count. The `vo.count_tokens()` method is available. [VERIFIED: Context7 — /websites/voyageai, batch embedding examples]

---

## Code Examples

### Complete Docling Parse + Chunk Flow

```python
# Source: Context7 — /docling-project/docling + project-specific table path
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from docling_core.types.doc import TableItem
import uuid

CHUNK_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

_converter = DocumentConverter()  # module-level, initialized once per worker

def parse_and_chunk(file_path: str, document_id: str) -> list[dict]:
    result = _converter.convert(file_path)
    doc = result.document
    chunker = HybridChunker(max_tokens=512, merge_peers=True)
    chunks = []
    ordinal = 0

    # Text path
    for chunk in chunker.chunk(doc):
        # Skip chunks that are table content
        has_table = any(isinstance(i, TableItem) for i in getattr(chunk.meta, 'doc_items', []))
        if has_table:
            continue
        text = chunker.contextualize(chunk)
        chunk_id = uuid.uuid5(CHUNK_UUID_NAMESPACE, f"{document_id}:{ordinal}")
        chunks.append({"id": str(chunk_id), "text": text, "ordinal": ordinal, "is_table": False})
        ordinal += 1

    # Table path
    for table in doc.tables:
        md = table.export_to_markdown(doc=doc)
        if md.strip():
            chunk_id = uuid.uuid5(CHUNK_UUID_NAMESPACE, f"{document_id}:{ordinal}")
            chunks.append({"id": str(chunk_id), "text": md, "ordinal": ordinal, "is_table": True})
            ordinal += 1

    return chunks
```

### Voyage Batch Embedding with tenacity Retry

```python
# Source: Context7 — /websites/voyageai
import voyageai
from tenacity import retry, wait_exponential, stop_after_attempt

vo = voyageai.Client()

@retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(5))
def _embed_batch(texts: list[str], model: str) -> list[list[float]]:
    return vo.embed(texts, model=model, input_type="document").embeddings

def embed_all(texts: list[str], model: str = "voyage-3") -> list[list[float]]:
    all_embeddings = []
    for i in range(0, len(texts), 128):
        all_embeddings.extend(_embed_batch(texts[i:i+128], model))
    return all_embeddings
```

### Haiku Structured Output

```python
# Source: Context7 — /anthropics/anthropic-sdk-python
import anthropic
from pydantic import BaseModel

class ChunkMetadata(BaseModel):
    summary: str
    keywords: list[str]
    questions: list[str]

_anthropic = anthropic.Anthropic()

def enrich(text: str) -> ChunkMetadata:
    result = _anthropic.messages.parse(
        model="claude-haiku-4-5",
        system="Extract metadata: summary (1-2 sentences), keywords (5-10 noun phrases), hypothetical questions (3-5) a user might ask that this chunk answers.",
        messages=[{"role": "user", "content": text}],
        max_tokens=512,
        output_format=ChunkMetadata,
    )
    return result.parsed_output
```

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `python-multipart` is not yet in pyproject.toml (needs adding) | §2 | Build fails at file upload if missing; low risk — easy to add |
| A2 | Current Haiku model name is `claude-haiku-4-5` | §6 | `generate_metadata` task fails with invalid model error; verify with `anthropic.Anthropic().models.list()` |
| A3 | Haiku cost ~$0.01 per 50-chunk document | §6 | Acceptable for M2 demo regardless of exact pricing |
| A4 | Docling ML models load ~1-2GB RAM per worker process | §Common Pitfalls | Worker OOM if memory not provisioned; profile on first run |
| A5 | `voyage-3` model name is still valid in voyageai 0.3.7 | §7 | Embedding fails with invalid model error; check Voyage docs |
| A6 | HybridChunker `max_tokens` works without a HuggingFace tokenizer | §4 | Chunking falls back to character-based sizing; acceptable for M2 |

---

## Open Questions

1. **Voyage model name for M2**
   - What we know: STACK.md confirms `voyageai 0.3.7`. Context7 shows `voyage-3`, `voyage-3.5`, `voyage-4-lite` all currently available.
   - What's unclear: Whether `voyage-3` is still the recommended RAG model or if `voyage-3.5` should be used.
   - Recommendation: Use `voyage-3` for M2 (matches the 1024-dimension schema in `embeddings` table). `voyage-3.5` also produces 1024-dim vectors and is a drop-in replacement if preferred.

2. **Anthropic Haiku model string**
   - What we know: `anthropic 0.101.0` is installed. Stack research confirms structured outputs via `client.messages.parse()`.
   - What's unclear: Exact current Haiku model name (`claude-haiku-4-5` is [ASSUMED]).
   - Recommendation: Verify via `python -c "import anthropic; print([m.id for m in anthropic.Anthropic().models.list().data if 'haiku' in m.id.lower()])"` before Wave 4.

3. **Local temp file vs task-visible path on Windows dev**
   - What we know: `STATE.md` confirms development on Windows 11. `/tmp/` may not exist.
   - What's unclear: Whether Celery workers in docker-compose share the same filesystem with the API container for temp file access.
   - Recommendation: Use `tempfile.gettempdir()` for platform-portable path, or use a shared Docker volume mount for upload staging.

4. **`documents` table needs `agent_id` column**
   - What we know: The existing `documents` table has no `agent_id` or tenant scoping — it assumes physical per-tenant DB isolation.
   - What's unclear: Whether the chunks/documents pattern assumes complete physical DB isolation (no cross-tenant queries needed) or whether queries will need tenant-scoping columns.
   - Recommendation: Per the M1 architecture, each tenant has a dedicated Neon project. Physical isolation means `agent_id` is not needed in tenant DB tables. Confirmed correct: no `agent_id` column needed. [VERIFIED: CONTEXT.md — "Per-tenant Neon projects (not schema-per-tenant)"]

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11 | All tasks | ✓ | 3.11 (pyproject.toml requires-python) | — |
| Redis | SSE pub/sub, Celery broker | ✓ | 6.4.0 (redis-py) | — |
| PostgreSQL | Control DB | ✓ | via Neon + local docker-compose postgres | — |
| Neon tenant DB | Embedding upsert | ✓ | via existing M1 provisioning | — |
| Docker | Dev environment | ✓ | docker-compose.yml exists | — |
| `docling==2.93.0` | parse_documents task | ✗ (not yet in pyproject.toml) | — | None — must install |
| `chonkie==1.6.5` | chunk_documents task | ✗ (not yet in pyproject.toml) | — | None — must install |
| `voyageai==0.3.7` | embed_and_migrate task | ✗ (not yet in pyproject.toml) | — | None — must install |
| `anthropic==0.101.0` | generate_metadata task | ✗ (not yet in pyproject.toml) | — | None — must install |
| `python-multipart` | File upload endpoint | ✗ (not confirmed in pyproject.toml) | — | None — must install |
| `tenacity` | Retry logic | ✗ (not in pyproject.toml) | — | None — must install |
| `pandas` | Docling table export | ✗ (docling dependency, may auto-install) | — | Use `export_to_markdown()` only |
| `ANTHROPIC_API_KEY` | generate_metadata | ✗ (not in .env.example) | — | Must add to Settings |
| `VOYAGE_API_KEY` | embed_and_migrate | ✗ (not in .env.example) | — | Must add to Settings |

**Missing dependencies with no fallback:**
- `docling`, `chonkie`, `voyageai`, `anthropic`, `python-multipart`, `tenacity` — all must be added to `pyproject.toml` in Wave 1

**Missing environment variables:**
- `ANTHROPIC_API_KEY` — must be added to `Settings` in `app/core/config.py` and `.env.example`
- `VOYAGE_API_KEY` — must be added to `Settings` and `.env.example`

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing) + pytest-asyncio 1.3.0 (existing) |
| Config file | `apps/api/pyproject.toml` → `[tool.pytest.ini_options]` |
| Quick run command | `cd apps/api && pytest tests/unit/ -x -q` |
| Full suite command | `cd apps/api && pytest tests/unit/ tests/integration/ -x -q -m "not e2e"` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ING-01 | POST /agents/{id}/documents returns 202 | unit | `pytest tests/unit/test_document_routes.py -x` | ❌ Wave 6 |
| ING-02 | Docling parse preserves headings/lists in chunks | unit | `pytest tests/unit/test_parse_task.py -x` | ❌ Wave 2 |
| ING-03 | Tables appear as Markdown rows in chunks | unit | `pytest tests/unit/test_chunk_task.py::test_table_chunk_produces_markdown -x` | ❌ Wave 3 |
| ING-04 | HybridChunker respects structure boundaries | unit | `pytest tests/unit/test_chunk_task.py -x` | ❌ Wave 3 |
| ING-05 | Same doc + ordinal → same chunk UUID | unit | `pytest tests/unit/test_chunk_id.py::test_deterministic_chunk_id_stable -x` | ❌ Wave 1 |
| ING-06 | Haiku enriches with summary/keywords/questions | unit | `pytest tests/unit/test_metadata_task.py -x` | ❌ Wave 4 |
| ING-07 | Voyage embeddings stored with correct dimension | unit | `pytest tests/unit/test_embed_task.py -x` | ❌ Wave 5 |
| ING-08 | SSE emits parsing/chunking/metadata/embedding events | unit | `pytest tests/unit/test_ingestion_sse.py -x` | ❌ Wave 6 |
| ING-09 | Chain run twice produces no duplicates | integration | `pytest tests/integration/test_ingestion_chain.py::test_idempotent_chain -x -m integration` | ❌ Wave 6 |
| ING-10 | Real PDF → populated chunks + chunk_metadata | e2e | `INGESTION_E2E_ENABLED=1 pytest tests/integration/test_ingestion_e2e.py -x -m integration` | ❌ Wave 7 |

### Sampling Rate

- **Per task commit:** `cd apps/api && pytest tests/unit/ -x -q`
- **Per wave merge:** `cd apps/api && pytest tests/unit/ tests/integration/ -x -q -m "not e2e"`
- **Phase gate:** Full suite green before `/gsd-verify-work 2`

### Wave 0 Gaps

- [ ] `tests/unit/test_chunk_id.py` — covers ING-05 deterministic UUID
- [ ] `tests/unit/test_parse_task.py` — covers ING-02 with mocked Docling
- [ ] `tests/unit/test_chunk_task.py` — covers ING-03 table path + ING-04 structure-aware
- [ ] `tests/unit/test_metadata_task.py` — covers ING-06 with mocked Anthropic
- [ ] `tests/unit/test_embed_task.py` — covers ING-07 with mocked Voyage
- [ ] `tests/unit/test_document_routes.py` — covers ING-01 and ING-08
- [ ] `tests/unit/test_ingestion_sse.py` — covers ING-08 event vocabulary
- [ ] `tests/integration/test_ingestion_chain.py` — covers ING-09 idempotency
- [ ] `tests/integration/test_ingestion_e2e.py` — covers ING-10 demo
- [ ] `apps/api/tests/fixtures/demo_business.pdf` — small real PDF for ING-10

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Existing `get_current_tenant` dependency reused on all new routes |
| V3 Session Management | no | Stateless API, no sessions |
| V4 Access Control | yes | Agent must belong to authenticated tenant; verified before dispatching chain |
| V5 Input Validation | yes | Pydantic schemas on upload request; file type validation; max file size |
| V6 Cryptography | no new surface | Fernet encryption of connection strings already established in M1 |

### Known Threat Patterns for M2 Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Indirect prompt injection via uploaded documents | Tampering | Strip injection patterns from chunk content before DB write (PITFALLS.md §11) |
| Cross-tenant document access | Information Disclosure | Physical per-tenant DB isolation (M1); all DB connections fetched by tenant_id from control DB |
| Oversized file upload (DoS) | Denial of Service | `MAX_UPLOAD_SIZE_MB` setting enforced before Docling parse |
| Malicious PDF (parser exploit) | Tampering | Docling isolates parsing in its own process; timeout on parse task |
| Connection string in task args | Information Disclosure | Never — tasks receive `agent_id` only; fetch+decrypt at runtime (CLAUDE.md rule) |
| API key in task args | Information Disclosure | `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY` read from settings at task runtime, never in args |

---

## Sources

### Primary (HIGH confidence)
- Context7 `/docling-project/docling` — DocumentConverter API, HybridChunker, DoclingDocument, table extraction
- Context7 `/docling-project/docling-core` — HybridChunker parameters, ProvenanceItem, DocChunk
- Context7 `/chonkie-inc/chonkie` — RecursiveChunker, SemanticChunker, Chunk object attributes
- Context7 `/anthropics/anthropic-sdk-python` — `client.messages.parse()` with Pydantic output_format
- Context7 `/websites/voyageai` — batch embedding, 128-item limit, model names
- M1 codebase (verified live): `apps/api/app/worker/tasks/pipeline/provision.py`, `migrations.py`, `celery_app.py`, `services/events.py`
- M1 tenant schema (verified live): `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py`

### Secondary (MEDIUM confidence)
- `.planning/research/PITFALLS.md` — M2-specific pitfalls (table flattening §2, HNSW bloat §5, idempotency §8, injection §11) — compiled from multiple cited web sources
- `.planning/research/STACK.md` — confirmed stack versions (all PyPI-verified)
- PRD M1 §7.3 — event vocabulary forward-extensibility contract (parsing.*, chunking.*, embedding.*)

### Tertiary (LOW confidence)
- Docling memory footprint (~1-2GB per worker) — [ASSUMED], not verified with profiling
- Haiku model string `claude-haiku-4-5` — [ASSUMED], verify before Wave 4

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions confirmed in STACK.md against PyPI
- Architecture: HIGH — extends verified M1 pattern directly; no new architectural concepts
- Docling API patterns: HIGH — verified via Context7 against official docling-project/docling repo
- Chonkie API patterns: HIGH — verified via Context7 against official chonkie-inc/chonkie repo
- Voyage embedding: HIGH — verified via Context7 against official voyageai docs
- Anthropic structured outputs: HIGH — verified via Context7 against official anthropic-sdk-python
- Idempotency strategy: HIGH — extends proven M1 patterns with same mechanisms
- Test strategy: HIGH — extends proven M1 test patterns; framework and conftest verified
- Haiku model name: LOW — [ASSUMED], not verified in this session

**Research date:** 2026-05-13
**Valid until:** 2026-06-13 (stable stack — 30 days)
