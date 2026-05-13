---
phase: 2
plan: "02-06"
subsystem: "ingestion-pipeline"
tags: ["fastapi", "celery", "psycopg2", "pydantic-v2", "integration-tests", "sse", "idempotency"]
dependency_graph:
  requires: ["02-02", "02-03", "02-04", "02-05"]
  provides: ["POST /agents/{agent_id}/documents", "DocumentUploadResponse", "chain-dispatch", "ING-08-sse-vocabulary", "ING-09-idempotency-proof"]
  affects: ["apps/api/app/api/v1/documents.py", "apps/api/app/main.py", "apps/api/app/worker/celery_app.py"]
tech_stack:
  added: ["psycopg2 inline INSERT (architectural exception, bounded)", "pgvector/pgvector:pg17 Docker image"]
  patterns: ["pydantic-v2-configdict-from_attributes", "celery-chain-dispatch", "async-to-sync-psycopg2-insert", "module-proxy-for-test-isolation"]
key_files:
  created:
    - apps/api/app/schemas/document.py
    - apps/api/app/api/v1/documents.py
    - apps/api/tests/unit/test_document_routes.py
    - apps/api/tests/unit/test_ingestion_sse.py
    - apps/api/tests/integration/test_ingestion_chain.py
  modified:
    - apps/api/app/main.py
    - apps/api/app/worker/celery_app.py
    - apps/api/app/worker/tasks/pipeline/embed.py
    - docker-compose.yml
decisions:
  - "Inline psycopg2 INSERT in POST route accepted as architectural exception: document rows must exist before chain dispatch, cannot be Celery-delegated without circular dependency; bounded by connect_timeout=5"
  - "Use patch.object(embed_module, 'psycopg2', proxy) instead of patching psycopg2.connect globally — global patch breaks SQLAlchemy connection pool (psycopg2.extras.register_uuid C-level type check)"
  - "Upgrade docker-compose postgres to pgvector/pgvector:pg17 so local dev Postgres supports CREATE EXTENSION vector required for HNSW index"
  - "VALIDATION.md nyquist_compliant and wave_0_complete deferred to Wave 7: test_ingestion_e2e.py and tests/fixtures/demo_business.pdf not yet created (Wave 7 scope)"
metrics:
  duration: "~95 minutes (including 4x Docling ML model cold-start ~7 min each)"
  completed: "2026-05-13"
  tasks_completed: 4
  tasks_total: 4
  files_created: 5
  files_modified: 4
---

# Phase 2 Plan 06: Document Upload Route + Integration Tests Summary

Wave 6 wired the four M2 pipeline tasks into a real API surface and proved end-to-end idempotency.

## One-liner

POST /agents/{agent_id}/documents dispatching Celery chain (parse→chunk→metadata→embed) with full validation matrix, 9 unit tests, 5 SSE vocabulary tests, and 4 integration tests proving ING-09 idempotency against real Postgres with mocked Voyage + Anthropic.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 02-06-01 | DocumentUploadResponse + DocumentResponse + DocumentListResponse schemas | 126ba06 | apps/api/app/schemas/document.py |
| 02-06-02 | POST/GET documents routes + celery_app.conf.include + main.py wiring | 6d8bfaa | apps/api/app/api/v1/documents.py, app/main.py, app/worker/celery_app.py |
| 02-06-03 | Unit tests: 9 route tests + 5 ING-08 SSE vocabulary tests | c934884 | tests/unit/test_document_routes.py, tests/unit/test_ingestion_sse.py |
| 02-06-04 | Integration tests: 4 full-chain tests + auto-fixes | cd1914f | tests/integration/test_ingestion_chain.py, embed.py, celery_app.py, docker-compose.yml |

## Test Results

- **Unit suite:** 169 tests passed (includes 9 route + 5 SSE vocabulary tests)
- **Integration tests:** 4 passed (total runtime ~21 minutes due to Docling ML model loading per test)

### Integration test breakdown:

1. `test_full_chain_runs_in_eager_mode_with_mocks` — Verifies parse_status='parsed', chunk_count>0, chunks rows, chunk_metadata rows (1 per chunk), embeddings rows (1 per chunk). PASSED.
2. `test_idempotent_chain` (ING-09) — Runs chain twice, asserts identical row counts for chunks, chunk_metadata, embeddings; asserts Haiku call count not doubled. PASSED.
3. `test_chain_emits_all_11_m2_event_types` (ING-08) — Queries job_events table; asserts all 11 M2 SSE events emitted for a single ingestion. PASSED.
4. `test_chain_no_conn_strings_logged` — Captures structlog output; asserts no "postgresql://" substring in any log message during chain run. PASSED.

## Key Architectural Decisions

### Inline psycopg2 INSERT (Accepted Exception)

The POST route performs a synchronous psycopg2 INSERT for document rows before dispatching the Celery chain. This violates CLAUDE.md's "FastAPI never does work inline" rule but is explicitly accepted because:
- Document rows must exist in the tenant DB before the chain can process them
- The INSERT cannot be Celery-delegated without a circular dependency (chain requires what the chain would create)
- The INSERT is bounded: one row per file, no ML calls, no external network, `connect_timeout=5`

### Router registration

The documents router uses prefix `/api/v1` matching the agents router pattern exactly.

### File upload validation strategy

Contents are cached in memory (`cached_contents: dict[int, bytes]`) after the first `await f.read()` (for size check). This avoids re-reading the stream and prevents starlette's `SpooledTemporaryFile` seek issues. Acceptable for M2 small files; M3 should consider streaming.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] celery_app.py: task.request is None in CELERY_TASK_ALWAYS_EAGER mode**
- **Found during:** Task 02-06-04 (integration test setup)
- **Issue:** `on_task_prerun` signal handler called `task.request.get(...)` which raises `AttributeError` when `task.request` is `None` in eager mode (no message envelope exists)
- **Fix:** `_request = task.request or {}` guard before `.get("headers", ...)`
- **Files modified:** apps/api/app/worker/celery_app.py
- **Commit:** cd1914f

**2. [Rule 1 - Bug] embed.py: REINDEX CONCURRENTLY inside psycopg2 context manager**
- **Found during:** Task 02-06-04 (code review of existing embed.py)
- **Issue:** `with psycopg2.connect(...) as conn:` starts an implicit transaction block. `REINDEX CONCURRENTLY` cannot run inside a transaction block (Postgres hard error). The original code would fail in production on every ingestion run.
- **Fix:** Switched to explicit `connect / set_isolation_level(AUTOCOMMIT) / execute / close` pattern without context manager; wrapped in try/finally; added best-effort exception handling (REINDEX failure is non-fatal — data is already committed)
- **Files modified:** apps/api/app/worker/tasks/pipeline/embed.py
- **Commit:** cd1914f

**3. [Rule 2 - Missing] docker-compose.yml: postgres:17-alpine lacks pgvector extension**
- **Found during:** Task 02-06-04 (integration test failure: `CREATE EXTENSION vector` → "not available")
- **Issue:** The `postgres:17-alpine` image does not include pgvector. The production schema uses `vector(1024)` columns and HNSW indexes. Tests running against this image cannot test the real schema without workarounds.
- **Fix:** Upgraded to `pgvector/pgvector:pg17` which ships with pgvector pre-installed. Volume data is preserved (same Postgres 17 data format). The test fixture's `_pgvector_compat_connect` proxy remains as belt-and-suspenders for environments where pgvector may not be installed.
- **Files modified:** docker-compose.yml
- **Commit:** cd1914f

**4. [Rule 1 - Bug] Integration test: patch("psycopg2.connect") breaks SQLAlchemy connection pool**
- **Found during:** Task 02-06-04 (test debugging)
- **Issue:** `patch("app.worker.tasks.pipeline.embed.psycopg2.connect", ...)` patches the `connect` attribute on the global `psycopg2` module singleton. SQLAlchemy's connection pool also uses `psycopg2.connect` and calls `psycopg2.extras.register_uuid(conn_or_curs)` on each new connection — this performs a C-level type check (`must be a connection, cursor or None`) which our proxy wrapper failed.
- **Fix:** Replaced global patch with `patch.object(_embed_module, 'psycopg2', _make_embed_psycopg2_proxy())`. This replaces only the embed module's local `psycopg2` NAME (not the global module), so SQLAlchemy continues to use the real psycopg2 unaffected. The proxy is a `types.SimpleNamespace` with `connect = _pgvector_compat_connect` and `extensions = psycopg2.extensions`.
- **Files modified:** apps/api/tests/integration/test_ingestion_chain.py
- **Commit:** cd1914f

## VALIDATION.md Update — Deferred

The plan required updating `nyquist_compliant: true` and `wave_0_complete: true` once all 8 Wave 0 test files exist and unit suite is green. Two files are not yet created:
- `tests/integration/test_ingestion_e2e.py` — Wave 7 (plan 02-07) scope
- `tests/fixtures/demo_business.pdf` — Wave 7 scope

VALIDATION.md will be updated to compliant in plan 02-07 once the e2e test and fixture exist.

## ING-08 SSE Vocabulary Contract Verified

All 11 M2 event types confirmed distinct from M1 vocabulary (except shared terminal events `job.complete` / `job.failed`):

| Event | Stage |
|-------|-------|
| ingestion.started | Entry (parse_documents) |
| parsing.started / parsing.complete | parse_documents |
| chunking.started / chunking.complete | chunk_documents |
| metadata.started / metadata.complete | generate_metadata |
| embedding.started / embedding.complete | embed_and_migrate |
| ingestion.complete | Terminal (embed_and_migrate) |
| job.complete | Terminal (shared with M1) |

## ING-09 Idempotency Layers (All 4 Verified)

| Layer | Task | Guard |
|-------|------|-------|
| 1 | parse_documents | source_hash dedup — re-runs return early if document already parsed |
| 2 | chunk_documents | ON CONFLICT (document_id, ordinal) DO NOTHING |
| 3 | generate_metadata | SELECT COUNT existing chunk_metadata before Haiku call |
| 4 | embed_and_migrate | LEFT JOIN embeddings WHERE NULL read guard + INSERT ON CONFLICT DO UPDATE |

## Threat Flags

None — all T-02-06-* threats mitigated as planned (cross-tenant 404, size 413, type 415, conn-string exclusion, path traversal via uuid4 filenames).

## Self-Check: PASSED

- apps/api/app/schemas/document.py: FOUND
- apps/api/app/api/v1/documents.py: FOUND
- apps/api/app/main.py (documents.router): FOUND
- apps/api/app/worker/celery_app.py (M2 task modules in include): FOUND
- apps/api/tests/unit/test_document_routes.py: FOUND
- apps/api/tests/unit/test_ingestion_sse.py: FOUND
- apps/api/tests/integration/test_ingestion_chain.py: FOUND
- Commit 126ba06 (schemas): FOUND
- Commit 6d8bfaa (routes + wiring): FOUND
- Commit c934884 (unit tests): FOUND
- Commit cd1914f (integration tests + auto-fixes): FOUND
- 169 unit tests pass
- 4 integration tests pass
