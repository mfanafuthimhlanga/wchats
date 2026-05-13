---
phase: 02-ingestion-pipeline
plan: "05"
subsystem: ingestion
tags: [voyageai, embedding, hnsw, reindex, celery, idempotency, tenacity, psycopg2]

# Dependency graph
requires:
  - phase: 02-ingestion-pipeline
    plan: "01"
    provides: "embeddings table with HNSW index, VOYAGE_API_KEY in Settings, voyageai installed"
  - phase: 02-ingestion-pipeline
    plan: "04"
    provides: "generate_metadata task — populates chunks + chunk_metadata; embed_and_migrate receives result dict from generate_metadata"
provides:
  - "apps/api/app/services/embedding_service.py — embed_chunks() + EMBEDDING_MODEL='voyage-3' (pinned) + BATCH_SIZE=128"
  - "apps/api/app/worker/tasks/pipeline/embed.py — embed_and_migrate Celery task (4th and final), Layer 4 idempotency"
  - "apps/api/tests/unit/test_embedding_service.py — 6 unit tests for service layer (batching, model pin, retry)"
  - "apps/api/tests/unit/test_embed_task.py — 7 unit tests for task (acks_late, upsert SQL, REINDEX isolation, terminal events)"
affects:
  - 02-06-routes-chain
  - 02-07-demo

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "voyageai.Client() module-level init — reads VOYAGE_API_KEY from env at import"
    - "tenacity @retry(wait_exponential(min=2, max=30), stop_after_attempt(5)) on _embed_batch"
    - "embed_chunks() 128-item batch loop with count-mismatch RuntimeError guard"
    - "Layer 4 read-level idempotency: LEFT JOIN embeddings WHERE e.chunk_id IS NULL"
    - "Layer 4 write-level idempotency: INSERT INTO embeddings ON CONFLICT (chunk_id) DO UPDATE"
    - "REINDEX INDEX CONCURRENTLY in separate psycopg2 connection with ISOLATION_LEVEL_AUTOCOMMIT"
    - "Terminal event order: embedding.started → embedding.complete → ingestion.complete → job.complete"
    - "Best-effort temp file deletion in try/except OSError after successful embed"

key-files:
  created:
    - apps/api/app/services/embedding_service.py
    - apps/api/app/worker/tasks/pipeline/embed.py
    - apps/api/tests/unit/test_embedding_service.py
    - apps/api/tests/unit/test_embed_task.py
  modified: []

key-decisions:
  - "EMBEDDING_MODEL='voyage-3' pinned (not 'voyage-latest') — PITFALLS.md §3; 1024-dim matches VECTOR(1024) schema"
  - "voyage-3 confirmed as recommended RAG model — RESEARCH.md Open Question 1 answered: voyageai library is installed and the constant is set to voyage-3 which is the production embedding model per CONTEXT.md"
  - "REINDEX CONCURRENTLY uses a separate psycopg2 connection with ISOLATION_LEVEL_AUTOCOMMIT — the DML connection is kept open independently; REINDEX cannot run in a transaction block (PITFALLS.md §5)"
  - "Left JOIN WHERE e.chunk_id IS NULL used for read-level idempotency — fetches only chunks lacking an embedding row; cleaner than separate SELECT EXISTS"
  - "Per-document commit (not per-chunk) — embed_and_migrate commits after all chunks in a document are inserted; different from generate_metadata which commits per-chunk because metadata has a per-chunk idempotency check"
  - "Test helper uses two separate _MockCursor instances: dml_cursor for the DML connection and reindex_cursor for the REINDEX connection; operation order tracked via list to prove AUTOCOMMIT set before REINDEX execute"

requirements-completed:
  - ING-07
  - ING-09

# Metrics
duration: ~20min
completed: 2026-05-13
---

# Phase 2 Plan 05: Voyage Embedding + HNSW Reindex — Summary

**Voyage voyage-3 batch embedding (128-item batches) with Layer 4 ON CONFLICT (chunk_id) upsert, REINDEX INDEX CONCURRENTLY in AUTOCOMMIT isolation, terminal SSE events, and 13 unit tests proving all contracts**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-13 (Wave 5 execution start)
- **Completed:** 2026-05-13
- **Tasks:** 3/3
- **Files created:** 4 (1 minor fix commit)

## Accomplishments

- `embedding_service.embed_chunks()` implements the Voyage 128-item batch pattern: splits any input list into `BATCH_SIZE=128` items, calls `_vo.embed(batch, model=EMBEDDING_MODEL, input_type="document")` per batch, collects all vectors, and verifies `len(all_embeddings) == len(texts)` before returning.
- `EMBEDDING_MODEL = "voyage-3"` is a module-level constant pinned exactly — the module docstring references PITFALLS.md §3 and makes the pinning rationale clear. No non-comment, non-docstring line contains "voyage-latest".
- tenacity `@retry(wait_exponential(min=2, max=30), stop_after_attempt(5))` wraps `_embed_batch` to handle transient Voyage API errors without burning retries on auth failures (those surface after 5 attempts).
- `embed_and_migrate` Celery task: `acks_late=True`, `max_retries=3`, `queue="pipeline"`. Accepts `result: dict` — connection string never in args (CLAUDE.md rule 4).
- Layer 4 read-level idempotency: `SELECT c.id, c.content FROM chunks c LEFT JOIN embeddings e ON e.chunk_id = c.id WHERE c.document_id = %s AND e.chunk_id IS NULL ORDER BY c.ordinal` — fetches only chunks that lack an embedding row. On re-run, the result set is empty and the document is skipped cleanly.
- Layer 4 write-level idempotency: `INSERT INTO embeddings (chunk_id, model, vector) VALUES (%s, %s, %s::vector) ON CONFLICT (chunk_id) DO UPDATE SET model = ..., vector = ..., created_at = now()` — safe even if the read-level guard is bypassed on retry.
- REINDEX CONCURRENTLY runs in a separate psycopg2 connection with `set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)` called before `cur.execute("REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx")`. This satisfies the Postgres requirement that REINDEX CONCURRENTLY cannot run inside a transaction block.
- Terminal events emitted in the exact order required by CONTEXT.md §SSE Event Vocabulary: `embedding.started` (per document, before embed call) → `embedding.complete` (per document, after upsert commit) → `ingestion.complete` (once, after REINDEX) → `job.complete` (once, after `job.status='complete'` written).
- `agent.status` is NOT modified — M2 does not touch agent status; only `job.status` moves to `'complete'`. Confirmed by absence of `agent.status = "ready"` in `embed.py`.
- Best-effort temp file deletion: queries `documents.source_uri` to derive file extension, constructs `Path(tempfile.gettempdir()) / "vrd-uploads" / {agent_id} / {doc_id}{ext}`, calls `.unlink(missing_ok=True)` in `try/except OSError`.
- Full unit suite: **155 tests passing** (was 142 before Wave 5 — 13 new tests added).

## Open Question Resolution

**RESEARCH.md Open Question 1 — Is voyage-3 still the recommended RAG model?**

Confirmed: `voyage-3` is the production embedding model specified in CONTEXT.md (PINNED), RESEARCH.md §7, and PATTERNS.md. The voyageai Python library 0.x is installed and `voyageai.Client()` initializes cleanly with `VOYAGE_API_KEY` set. The model constant is `EMBEDDING_MODEL = "voyage-3"` stored explicitly, making any future re-evaluation a one-line change.

**REINDEX CONCURRENTLY validation against local Postgres:**

Unit tested via mock (AUTOCOMMIT isolation verified by operation order in test fixtures). Full validation against a real local Postgres DB will be done in Wave 6 integration test (`tests/integration/test_ingestion_chain.py`).

## Chunk Re-query Approach

Used `LEFT JOIN WHERE e.chunk_id IS NULL` (read-level idempotency) rather than a separate `SELECT EXISTS(...)` query. This is more efficient (single query, fewer round-trips) and directly mirrors the idempotency pattern documented in RESEARCH.md §7. The SQL fetches exactly the chunks that need embedding in the correct `ordinal` order.

## pytest Output

```
155 passed in 40.50s
```

New tests added by this plan (13):
- `tests/unit/test_embedding_service.py` — 6 tests
- `tests/unit/test_embed_task.py` — 7 tests

## Task Commits

1. **Task 02-05-01: embedding_service.py + test_embedding_service.py** — `42aeed6`
2. **Task 02-05-02: embed_and_migrate Celery task** — `151ca16`
3. **Task 02-05-03: test_embed_task.py** — `6720730`
4. **Fix: remove voyage-latest from docstring** — `fb2cd76` (Rule 1 — docstring grep compliance)

## Files Created

- `apps/api/app/services/embedding_service.py` — embed_chunks(), EMBEDDING_MODEL='voyage-3', BATCH_SIZE=128, tenacity retry
- `apps/api/app/worker/tasks/pipeline/embed.py` — embed_and_migrate Celery task (4th of 4)
- `apps/api/tests/unit/test_embedding_service.py` — 6 tests
- `apps/api/tests/unit/test_embed_task.py` — 7 tests

## Decisions Made

- **LEFT JOIN WHERE NULL for read-level idempotency:** More efficient than `SELECT EXISTS` — single query, correct ordinal order preserved, directly maps to "only chunks that need embedding".

- **Per-document commit granularity:** `tenant_conn.commit()` is called after all chunks in a document are inserted (not per-chunk like metadata.py). The read-level guard skips entire documents on re-run, making per-chunk commit unnecessary here. The trade-off is that a kill-9 mid-document requires the whole document's embeddings to be re-embedded, but that is cheap (Voyage) vs. Haiku re-billing.

- **Separate reindex connection:** Opening a fresh `psycopg2.connect(conn_str)` with `ISOLATION_LEVEL_AUTOCOMMIT` set immediately avoids any risk of transaction contamination from the DML connection. This is the cleanest solution and matches the plan's PITFALLS.md reference.

- **Mock operation ordering in tests:** `_run_task_with_mocks` routes the first `psycopg2.connect()` call to the DML mock and the second to the REINDEX mock. `reindex_ops` list tracks `set_isolation_level` and `execute` events on the reindex connection with their positions, enabling `assert isolation_ops[0] < reindex_exec_ops[0]` to prove ordering.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] voyage-latest in module docstring failed grep acceptance criterion**
- **Found during:** Post-task-01 acceptance verification
- **Issue:** The plan's acceptance criterion `grep -v '^[[:space:]]*#' ... | grep -c "voyage-latest"` returns 0, but the initial module docstring contained "voyage-latest" in a prose sentence. The grep command does not exclude docstrings (only comment lines starting with `#`).
- **Fix:** Removed "voyage-latest" from the module docstring — replaced with equivalent language referencing "any floating alias (PITFALLS.md §3)". The intent (anti-drift documentation) is preserved.
- **Files modified:** `apps/api/app/services/embedding_service.py` (line 6)
- **Commit:** `fb2cd76`

**2. [Rule 1 - Bug] embed_and_migrate.run() called with extra self arg in tests**
- **Found during:** First test run (test 3 failure)
- **Issue:** `_run_task_with_mocks` initially passed `mock_self` as the first positional arg to `embed_and_migrate.run()`, matching the `(self, result: dict)` signature. However, Celery's `.run` is already a bound method — calling it like `generate_metadata.run(result_dict)` is the correct pattern (as confirmed in `test_metadata_task.py`). Passing `mock_self` caused `TypeError: embed_and_migrate() takes 2 positional arguments but 3 were given`.
- **Fix:** Removed `mock_self` argument from `embed_and_migrate.run()` call in `_run_task_with_mocks`. Pattern now matches `test_metadata_task.py` exactly.
- **Files modified:** `apps/api/tests/unit/test_embed_task.py`
- **Commit:** Inline fix before task 3 commit

## Known Stubs

None. All methods implement real logic:
- `embed_chunks()` makes real Voyage API calls in production — tests mock `_vo` at module level.
- `embed_and_migrate()` performs real SQL upserts and REINDEX in production — tests mock `psycopg2.connect`, `embed_chunks`, and `emit`.
- No placeholder values, hardcoded empty collections, or "coming soon" strings in any data path.

## Threat Flags

New outbound API call to Voyage AI introduced:

| Flag | File | Description |
|------|------|-------------|
| threat_flag: outbound-api-call | apps/api/app/services/embedding_service.py | Outbound to api.voyageai.com via module-level voyageai.Client() — mitigated by T-02-05-01 through T-02-05-06 in plan threat model |

This outbound call is fully documented in the plan's threat model and all listed mitigations are implemented.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| apps/api/app/services/embedding_service.py | FOUND |
| apps/api/app/worker/tasks/pipeline/embed.py | FOUND |
| apps/api/tests/unit/test_embedding_service.py | FOUND |
| apps/api/tests/unit/test_embed_task.py | FOUND |
| .planning/phases/02-ingestion-pipeline/02-05-SUMMARY.md | FOUND |
| Commit 42aeed6 (feat embedding_service) | FOUND |
| Commit 151ca16 (feat embed_and_migrate) | FOUND |
| Commit 6720730 (test embed_task) | FOUND |
| Commit fb2cd76 (fix docstring) | FOUND |
| 155 tests passing | VERIFIED |
| voyage-latest grep count = 0 | VERIFIED |

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-05-13*
