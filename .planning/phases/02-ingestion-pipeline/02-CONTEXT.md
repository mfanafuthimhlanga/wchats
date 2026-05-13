# Phase 2: M2 Ingestion Pipeline — Context

**Gathered:** 2026-05-13
**Status:** Ready for planning
**Source:** PRD Express Path (prd.md §M2 + prd-M1.md §5.1 + RESEARCH.md)

<domain>
## Phase Boundary

M2 is a strict extension of the M1 chain: new Celery tasks added, new event types emitted, writes to tables that already exist or are added via Alembic migration. The architectural contract (FastAPI never does inline work, acks_late=True + idempotency on every task, connection strings never in args) is inherited from M1 and never relaxed.

Four new pipeline tasks chained from a new `POST /agents/{agent_id}/documents` endpoint:

```
parse_documents → chunk_documents → generate_metadata → embed_and_migrate
```

Phase delivers:
- Document upload (PDF, image, URL list) via multipart/form-data
- Layout-aware parsing via Docling (DocumentConverter — module-level init, one per worker process)
- Structure-aware chunking via HybridChunker with dedicated table-aware path (tables → Markdown, never fed to HybridChunker)
- Metadata **and entity extraction** via Claude Haiku — single structured output call returns summary, keywords, questions, AND entities
- Voyage `voyage-3` embedding, batched (128 items max per request)
- pgvector HNSW upsert to tenant DB + `REINDEX CONCURRENTLY` post-insert
- SSE progress streaming (reuses M1 emit() + existing GET /jobs/{id}/events endpoint)
- 4-layer end-to-end idempotency
- Demo: upload real business PDF, inspect `chunks`, `chunk_metadata`, AND `entities` tables

</domain>

<decisions>
## Implementation Decisions

### Schema Baseline (from prd-M1.md §5.1 — what M2 migrates FROM)

M1 created these tenant DB tables (complete, exact):
- `documents(id, source_type, source_uri, title, metadata JSONB, created_at)` — NO source_hash, parse_status, chunk_count yet
- `chunks(id, document_id, ordinal, content, token_count, created_at)` + GIN index on content
- `embeddings(chunk_id PK, model, vector VECTOR(1024), created_at)` + HNSW index
- `chunk_metadata(chunk_id PK, summary TEXT, keywords TEXT[], questions TEXT[], created_at)` — NO entities field
- `conversations, messages, tool_calls, eval_runs, eval_results, red_team_runs` — empty, schema unchanged in M2

NOT yet created (M2 must add): `entities`, `chunk_entities`

### M2 Migration — `0002_documents_ingestion_columns.py`

Adds to `documents`:
- `source_hash TEXT` — SHA-256 of file content for document-level idempotency guard
- `parse_status TEXT DEFAULT 'pending'` — values: `pending | parsing | parsed | failed`
- `chunk_count INT` — populated after chunking

Creates new tables:
```sql
CREATE TABLE entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,   -- 'product' | 'person' | 'place' | 'policy' | 'process'
    normalized  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (normalized, type)
);

CREATE TABLE chunk_entities (
    chunk_id    UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (chunk_id, entity_id)
);
```

### Entity Extraction (NEW from prd.md v2 — LOCKED, not deferred)

- **Decision:** Entity extraction runs in M2 alongside metadata. Not deferred to later milestone.
- Runs in the **same Claude Haiku API call** as summary/keywords/questions — one structured output.
- Entity types (5): `product`, `person`, `place`, `policy`, `process`
- Entity deduplication: upsert on `UNIQUE(normalized, type)` — same entity across multiple chunks → one `entities` row, N `chunk_entities` rows
- Rationale from prd.md: "cheap (same API call), unlocks metadata-filtered retrieval in M3, substrate for M10 Conversation-Insights Engine"

```python
class EntityExtraction(BaseModel):
    name: str           # raw form as it appears in text
    type: Literal['product', 'person', 'place', 'policy', 'process']
    normalized: str     # canonical lowercase form for dedup

class ChunkMetadataAndEntities(BaseModel):
    summary: str
    keywords: list[str]
    questions: list[str]   # hypothetical questions this chunk answers
    entities: list[EntityExtraction]
```

Haiku call uses `client.messages.parse(output_format=ChunkMetadataAndEntities)`.

### Wave Structure (7 waves — from RESEARCH.md §12 + entity addition)

| Wave | Content |
|------|---------|
| 1 | `0002_documents_ingestion_columns.py` migration (documents columns + entities + chunk_entities tables). New deps: docling, chonkie, voyageai, anthropic, python-multipart, tenacity. New Settings: ANTHROPIC_API_KEY, VOYAGE_API_KEY, MAX_UPLOAD_SIZE_MB. chunk_id utility + chunk sanitization utility. |
| 2 | `docling_service.py` + `parse_documents` Celery task. Unit tests (mocked Docling, idempotency guard). |
| 3 | `chunking_service.py` (two-path: text + table) + `chunk_documents` task. Unit tests (table Markdown path, uuid5 IDs). |
| 4 | `metadata_service.py` (Haiku + entities) + `generate_metadata` task. Unit tests (mocked Anthropic, idempotency skip, entity dedup). |
| 5 | `embedding_service.py` (Voyage batch 128) + `embed_and_migrate` task (upserts + REINDEX CONCURRENTLY). Unit tests (batch splitting, upsert idempotency). |
| 6 | `app/api/v1/documents.py` (POST + GET routes) + `app/schemas/document.py`. Integration test: full chain mocked Voyage + Haiku, real local Postgres. Idempotency test (run chain twice, no duplicates). |
| 7 | `tests/fixtures/demo_business.pdf` + `scripts/demo_m2.sh`. E2E test (INGESTION_E2E_ENABLED=1). |

### Celery Chain + Task Contract (from RESEARCH.md §1 + prd-M1.md §7)

- New chain dispatched from `POST /agents/{id}/documents`, NOT attached to M1 provisioning chain
- Job kind: `'ingest_documents'`
- Result passed between tasks: `{"tenant_id": str, "agent_id": str, "job_id": str, "document_ids": list[str]}`
- Connection strings: NEVER in task args — always fetch+decrypt from control DB by `agent_id`
- Every task: `acks_late=True`, `max_retries=3`, `default_retry_delay=5`, `queue="pipeline"`
- Idempotency pattern (4 layers): document hash guard → chunk uuid5 upsert → metadata existence check → embedding upsert ON CONFLICT

### Chunking Strategy (from RESEARCH.md §4)

- Two-path: HybridChunker (text items) + `table.export_to_markdown()` (table items)
- `HybridChunker(max_tokens=512, merge_peers=True)`
- Embed string: `chunker.contextualize(chunk)` (NOT `chunk.text`) — includes heading breadcrumb
- Deterministic chunk IDs: `uuid5(uuid.NAMESPACE_URL, f"{document_id}:{ordinal}")`
- Tables are NEVER fed to HybridChunker (PITFALLS.md §2 — "totally messed up")
- DocumentConverter initialized at module level (once per worker process — ~1-2GB RAM)

### Embedding (from RESEARCH.md §7)

- Model: `voyage-3` (PINNED — no voyage-latest, embedding drift is PITFALLS.md §3)
- Batch size: 128 items; also respect per-batch token budget via `vo.count_tokens()`
- Upsert: `INSERT INTO embeddings ... ON CONFLICT (chunk_id) DO UPDATE`
- Chunks + chunk_metadata + chunk_entities: all upsert semantics
- Post-insert: `REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx` (PITFALLS.md §5)
- Tenant DB: psycopg2 sync, connection fetched from control DB

### API Surface (from RESEARCH.md §10)

```
POST /agents/{agent_id}/documents   → 202 {job_id, document_ids, status, events_url}
GET  /agents/{agent_id}/documents   → 200 {documents: [...]}
GET  /agents/{agent_id}/documents/{document_id}  → 200 single document
```

Validation: agent exists + belongs to tenant + status='ready'. File types: pdf, png, jpg, jpeg. Max size: MAX_UPLOAD_SIZE_MB setting.

SSE: reuses existing `GET /jobs/{job_id}/events` — no changes to SSE endpoint needed.

### SSE Event Vocabulary (from RESEARCH.md §8)

11 new event types: `ingestion.started`, `parsing.started`, `parsing.complete`, `chunking.started`, `chunking.complete`, `metadata.started`, `metadata.complete`, `embedding.started`, `embedding.complete`, `ingestion.complete`, `job.complete`

### File Storage for M2

- Local temp: `tempfile.gettempdir()` / `vrd-uploads/{agent_id}/{document_id}{ext}` — platform-portable
- Shared Docker volume between `api` and `worker_pipeline` services
- Delete temp file after successful `embed_and_migrate`

### Security (from RESEARCH.md §Security Domain)

- Chunk sanitization before DB write: strip injection patterns (PITFALLS.md §11)
- Agent ownership verified before dispatching chain (same pattern as existing GET /agents/{id})
- `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY` read from Settings at task runtime — never in task args
- `MAX_UPLOAD_SIZE_MB` enforced before Docling parse

### Testing Strategy (from prd-M1.md §9 pattern + RESEARCH.md §11)

- Unit: mocked Docling (module-level patch), mocked Anthropic, mocked Voyage — fast, no ML models
- Integration: real local Postgres, mocked external APIs via respx/unittest.mock, CELERY_TASK_ALWAYS_EAGER=True
- E2E: INGESTION_E2E_ENABLED=1 guard, real PDF fixture (<500KB), real APIs
- conftest.py: sets env vars before app import (existing M1 pattern)

### Deferred to Later Milestones

- `verified_qa` table — M6
- `conversation_insights` table — M10
- Anthropic batch messages API for metadata — future optimization (sequential + tenacity sufficient for M2)
- Voyage `voyage-3.5` — not for M2; re-embedding requires schema migration
- Object store (S3/GCS) for document storage — M2 local temp only
- Structured data path (CSV) — open question deferred per prd.md §12

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### PRDs
- `prd.md` — Full product PRD; §M2 section for entity extraction + Layer 3 schema + Layer 4 (verified_qa context)
- `prd-M1.md` — M1 PRD; §5.1 for exact tenant DB schema baseline; §7.3 for emit() pattern; §14 for "M2 as strict extension" contract

### Planning
- `.planning/ROADMAP.md` — M2 milestone definition, success criteria (ING-01–ING-10)
- `.planning/REQUIREMENTS.md` — ING-01 through ING-10 (entity extraction is expansion of ING-06)
- `.planning/phases/02-ingestion-pipeline/02-RESEARCH.md` — Full technical research (all API patterns, pitfalls, wave structure)
- `.planning/phases/02-ingestion-pipeline/02-VALIDATION.md` — Nyquist test map, Wave 0 requirements, per-task verification commands

### M1 Codebase (pattern source — read before implementing)
- `apps/api/app/worker/tasks/pipeline/provision.py` — idempotency guard, module-level init, acks_late=True
- `apps/api/app/worker/tasks/pipeline/migrations.py` — tenant DB connection pattern via control DB
- `apps/api/app/services/events.py` — emit() helper
- `apps/api/app/services/sse.py` — TERMINAL_EVENTS frozenset
- `apps/api/app/core/config.py` — Settings pattern (add ANTHROPIC_API_KEY, VOYAGE_API_KEY, MAX_UPLOAD_SIZE_MB)
- `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py` — exact current tenant schema (DO NOT duplicate any table created here)
- `apps/api/app/api/v1/agents.py` — existing route pattern; new documents.py router follows same shape
- `apps/api/app/worker/celery_app.py` — Celery app config, queue definitions

</canonical_refs>

<specifics>
## Specific Ideas

### ING-06 Scope Expansion

REQUIREMENTS.md ING-06 reads: "Each chunk enriched with: summary, keywords list, and hypothetical questions list (via Claude API, Haiku)"

prd.md v2 expands this to also include entity extraction in the same call. Entity extraction should be treated as mandatory ING-06 scope for M2 planning. The Haiku model string `claude-haiku-4-5` is assumed — verify with `anthropic.Anthropic().models.list()` before Wave 4 implementation.

### `demo_business.pdf` Requirements

Real business PDF (not generated), <500KB, must contain at least one table. Good options: product catalog, FAQ with table, policy document with structured data. The table must appear as Markdown rows in the `chunks` table — not fragmented prose. This is the ING-03 manual verification.

</specifics>

<deferred>
## Deferred Ideas

- `verified_qa` table creation — M6
- `conversation_insights` table — M10
- Anthropic batch messages API (Option B for metadata) — future cost optimization
- Voyage `voyage-3.5` — embedding drift risk; stick with `voyage-3` for M2
- Object store (S3/GCS) — local temp for M2 only
- CSV/structured data ingestion path — open question from prd.md §12, deferred
- Multi-language entity types — English only

</deferred>

---

*Phase: 02-ingestion-pipeline*
*Context gathered: 2026-05-13 via PRD Express Path (prd.md + prd-M1.md + RESEARCH.md)*
