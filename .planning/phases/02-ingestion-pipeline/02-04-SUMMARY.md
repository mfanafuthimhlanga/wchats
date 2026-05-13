---
phase: 02-ingestion-pipeline
plan: "04"
subsystem: ingestion
tags: [anthropic, haiku, structured-output, entities, celery, idempotency, tenacity, pydantic]

# Dependency graph
requires:
  - phase: 02-ingestion-pipeline
    plan: "01"
    provides: "entities/chunk_entities tables (0002 migration), ANTHROPIC_API_KEY in Settings, anthropic==0.101.0 installed"
  - phase: 02-ingestion-pipeline
    plan: "03"
    provides: "chunk_documents task — populates chunks table; generate_metadata receives result dict from chunk_documents"
provides:
  - "app/services/metadata_service.py — enrich_chunk() + EntityExtraction + ChunkMetadataAndEntities Pydantic models"
  - "app/worker/tasks/pipeline/metadata.py — generate_metadata Celery task (3rd of 4), Layer 3 idempotency"
  - "tests/unit/test_metadata_service.py — 6 unit tests for service layer (models, retry, Haiku call shape)"
  - "tests/unit/test_metadata_task.py — 6 unit tests for task (acks_late, Layer 3 skip, entity UPSERT SQL, event sequence)"
affects:
  - 02-05-embedding
  - 02-06-routes-chain
  - 02-07-demo

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "client.messages.parse(output_format=ChunkMetadataAndEntities) — Pydantic structured output via anthropic 0.101.0"
    - "Single Haiku call for all four fields: summary + keywords + questions + entities"
    - "tenacity @retry(retry_if_exception_type((RateLimitError, APITimeoutError))) — selective retry on transient errors only"
    - "Layer 3 idempotency: SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = %s pre-check"
    - "ON CONFLICT (normalized, type) DO UPDATE — entity dedup across corpus"
    - "ON CONFLICT DO NOTHING — chunk_entities link dedup on retry"
    - "Per-chunk commit: partial-progress safety allows resume without re-billing Haiku"

key-files:
  created:
    - apps/api/app/services/metadata_service.py
    - apps/api/app/worker/tasks/pipeline/metadata.py
    - apps/api/tests/unit/test_metadata_service.py
    - apps/api/tests/unit/test_metadata_task.py
  modified: []

key-decisions:
  - "HAIKU_MODEL='claude-haiku-4-5' — exact model string used; verification via anthropic.Anthropic().models.list() deferred to pre-deploy (RESEARCH.md Open Question 2)"
  - "RateLimitError constructor confirmed usable in tests: requires httpx.Request+Response with status_code=429 and real headers dict. Test constructs this directly — no pytest.skip() needed."
  - "Pydantic ValidationError on invalid Haiku response aborts the chunk (not the document): the error propagates through enrich_chunk() -> generate_metadata exception handler -> self.retry(). On final retry exhaustion, the document is effectively skipped."
  - "Per-chunk commit chosen over per-document commit: allows partial progress preservation if worker is killed mid-document. Next retry skips already-committed chunks via Layer 3 SELECT COUNT(*) guard."
  - "test_metadata_task.py uses a custom _MockCursor class (not MagicMock) to capture SQL strings in a list and provide sequenced fetchone() values. This gives deterministic control over cursor behaviour across multiple execute+fetchone pairs in a single cursor scope."

requirements-completed:
  - ING-06

# Metrics
duration: ~25min
completed: 2026-05-13
---

# Phase 2 Plan 04: Haiku Metadata + Entity Extraction — Summary

**Single-call Haiku metadata enrichment (summary + keywords + questions + entities) via client.messages.parse(), generate_metadata Celery task with Layer 3 idempotency, entity dedup via ON CONFLICT (normalized, type), and 12 unit tests proving the contract**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-13 (Wave 4 execution start)
- **Completed:** 2026-05-13
- **Tasks:** 3/3
- **Files created:** 4 (0 modified)

## Accomplishments

- `metadata_service.enrich_chunk()` implements the single-call Haiku pattern: `client.messages.parse(model="claude-haiku-4-5", output_format=ChunkMetadataAndEntities)` returns a fully Pydantic-validated `ChunkMetadataAndEntities` object in one API call.
- `EntityExtraction` model enforces `type: Literal["product", "person", "place", "policy", "process"]` — arbitrary entity type injection is rejected at the Pydantic layer before any DB write.
- tenacity retry wraps `enrich_chunk` with `retry_if_exception_type((RateLimitError, APITimeoutError))` — auth errors and validation errors fail immediately, no retry budget burned.
- `generate_metadata` Celery task: `acks_late=True`, `max_retries=3`, `queue="pipeline"`. Accepts `result: dict` — connection string never in args (CLAUDE.md rule 4).
- Layer 3 idempotency: `SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = %s` pre-check. On task retry, previously enriched chunks are skipped — zero Haiku re-billing.
- Entity deduplication: `INSERT INTO entities ... ON CONFLICT (normalized, type) DO UPDATE SET name = EXCLUDED.name RETURNING id`. Same entity across multiple chunks → one `entities` row, N `chunk_entities` rows.
- SSE events emitted per document: `metadata.started` (before per-chunk loop) and `metadata.complete` (after all chunks).
- Full unit suite: **142 tests passing** (was 130 before Wave 4 — 12 new tests added).

## Confirmed HAIKU_MODEL String

`HAIKU_MODEL = "claude-haiku-4-5"` — as specified in CONTEXT.md. Pre-deploy verification via `anthropic.Anthropic().models.list()` is deferred (RESEARCH.md Open Question 2). The model string is assigned to a named constant; if it changes before deploy, only one line in `metadata_service.py` needs updating.

## RateLimitError Constructor

The `anthropic.RateLimitError` constructor requires a `response: httpx.Response` with `.request` set and `.headers` accessible. The test (`test_enrich_chunk_retries_on_rate_limit`) constructs a valid error using:

```python
real_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
real_response = httpx.Response(429, request=real_request, content=b'...')
rate_limit_err = anthropic.RateLimitError("rate limited", response=real_response, body=None)
```

No `pytest.skip()` was needed — clean test instantiation confirmed.

## Pydantic ValidationError Handling

An invalid Haiku response (wrong schema or invalid entity type) raises `pydantic.ValidationError` inside `enrich_chunk()`. This propagates through the per-chunk loop into the task's outer `except Exception as exc` handler, which calls `self.retry(exc=exc)`. On final retry exhaustion (`max_retries=3`), the task raises `MaxRetriesExceededError`. This means a consistently invalid response aborts the document (no partial state written for the failing chunk), but previously committed chunks within the same document are preserved due to per-chunk commit granularity.

## Task Commits

1. **Task 02-04-01: metadata_service.py + test_metadata_service.py** — `f0c5cc9`
2. **Task 02-04-02: generate_metadata Celery task** — `f10ae60`
3. **Task 02-04-03: test_metadata_task.py** — `288954e`

## Files Created

- `apps/api/app/services/metadata_service.py` — enrich_chunk(), EntityExtraction, ChunkMetadataAndEntities, HAIKU_MODEL constant, METADATA_SYSTEM_PROMPT
- `apps/api/app/worker/tasks/pipeline/metadata.py` — generate_metadata Celery task
- `apps/api/tests/unit/test_metadata_service.py` — 6 tests
- `apps/api/tests/unit/test_metadata_task.py` — 6 tests

## Decisions Made

- **Per-chunk commit granularity:** `tenant_conn.commit()` after each chunk (not per-document). On task retry, the Layer 3 SELECT guard skips already-committed chunks, allowing the task to resume from the first unprocessed chunk without re-billing Haiku.

- **_MockCursor class in tests:** The task tests use a custom `_MockCursor` class with a `fetchone_sequence` parameter instead of MagicMock. This allows predictable control over multiple consecutive `fetchone()` calls within a single cursor scope (e.g., COUNT check + entity RETURNING id). MagicMock's generic `.return_value` doesn't support sequenced returns cleanly for mixed SQL patterns.

- **METADATA_SYSTEM_PROMPT as module constant:** The exact prompt text matching PLAN.md specification is stored as `METADATA_SYSTEM_PROMPT` constant at module level. This makes it visible for testing and auditing without reading the enrich_chunk function body.

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Clarification: cursor context manager in tests

The `_MockCursor.__enter__`/`__exit__` methods are on the cursor instance itself (not on `mock_conn.cursor.return_value`). The task uses `with tenant_conn.cursor() as cur:` (cursor as context manager). The `_MockCursor` class implements `__enter__`/`__exit__` directly, and `_make_mock_tenant_conn` sets `mock_conn.cursor.return_value = mock_cursor` so `mock_conn.cursor()` returns the cursor which acts as its own context manager. This is consistent with how psycopg2 cursors work.

## pytest Output

```
142 passed in 40.69s
```

New tests added by this plan (12):
- `tests/unit/test_metadata_service.py` — 6 tests
- `tests/unit/test_metadata_task.py` — 6 tests

## Known Stubs

None. Both the service and task implement real logic. `enrich_chunk()` makes a real Anthropic API call in production — the test mocks patch `_anthropic` at the module level. No placeholder values in the data path.

## Threat Flags

No new network endpoints introduced. The metadata_service introduces an outbound call to `api.anthropic.com` — this is within the plan's documented threat model:

| Flag | File | Description |
|------|------|-------------|
| threat_flag: outbound-api-call | apps/api/app/services/metadata_service.py | Outbound to api.anthropic.com via module-level Anthropic client — mitigated by T-02-04-01 through T-02-04-06 in plan threat model |

This outbound call is fully documented in the plan's threat model and all listed mitigations are implemented.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| apps/api/app/services/metadata_service.py | FOUND |
| apps/api/app/worker/tasks/pipeline/metadata.py | FOUND |
| apps/api/tests/unit/test_metadata_service.py | FOUND |
| apps/api/tests/unit/test_metadata_task.py | FOUND |
| .planning/phases/02-ingestion-pipeline/02-04-SUMMARY.md | FOUND |
| Commit f0c5cc9 (feat metadata_service) | FOUND |
| Commit f10ae60 (feat generate_metadata) | FOUND |
| Commit 288954e (test metadata_task) | FOUND |

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-05-13*
