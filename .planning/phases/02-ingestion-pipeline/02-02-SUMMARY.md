---
phase: 02-ingestion-pipeline
plan: "02"
subsystem: ingestion
tags: [docling, celery, parse, idempotency, sse, source_hash]

# Dependency graph
requires:
  - phase: 02-ingestion-pipeline
    plan: "01"
    provides: "0002 migration, chunk_id/sanitize utils, M2 deps installed, Settings extended"
provides:
  - "app/services/docling_service.py — module-level DocumentConverter + parse_document + parse_document_from_bytes"
  - "app/worker/tasks/pipeline/parse.py — parse_documents Celery task, Layer 1 source_hash idempotency, SSE events"
  - "tests/unit/test_docling_service.py — 3 unit tests (failure, success, DocumentStream)"
  - "tests/unit/test_parse_task.py — 5 unit tests (acks_late, sig, idempotency, chain dict, emit order)"
affects:
  - 02-03-chunking
  - 02-04-metadata-entities
  - 02-05-embedding
  - 02-06-routes-chain

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Module-level DocumentConverter() singleton — amortises ~10-15s ML model load across task calls"
    - "Layer 1 idempotency: pre-check COUNT(*) WHERE NOT parsed; per-doc parse_status == 'parsed' guard"
    - "RuntimeError from docling_service = fatal (no retry); generic Exception = transient (retry with backoff)"
    - "URL source via httpx.get(timeout=30) → parse_document_from_bytes; file source via tempfile path"
    - "tempfile.gettempdir() / vrd-uploads / agent_id / {doc_id}{ext} — platform-portable per CONTEXT.md"
    - "error_message list joined into RuntimeError message for testable assertion (deviation from initial plan)"

key-files:
  created:
    - apps/api/app/services/docling_service.py
    - apps/api/app/worker/tasks/pipeline/parse.py
    - apps/api/tests/unit/test_docling_service.py
    - apps/api/tests/unit/test_parse_task.py
  modified: []

key-decisions:
  - "RuntimeError message includes error_message strings (joined with ';') not repr(result.errors) — enables testable error content assertion"
  - "tempfile.gettempdir() resolves to C:/Users/Bantu/AppData/Local/Temp on Windows dev — valid directory, tests pass"
  - "URL-source page_count: None (not 0) — DoclingDocument.pages may not be populated for streamed content; None is the honest default"
  - "parse_documents.run() called without self_mock in tests — Celery bind=True .run is a bound method; self is already the task instance"

requirements-completed:
  - ING-01
  - ING-02

# Metrics
duration: ~80min
completed: 2026-05-13
---

# Phase 2 Plan 02: Docling Document Parse Task — Summary

**Module-level DocumentConverter singleton (one-per-worker-process), parse_documents Celery task with Layer 1 source_hash idempotency guard, and 8 unit tests proving the contract**

## Performance

- **Duration:** ~80 min
- **Started:** 2026-05-13 (Wave 2 execution start)
- **Completed:** 2026-05-13
- **Tasks:** 3/3
- **Files created:** 4 (0 modified)

## Accomplishments

- `docling_service.py` — thin service wrapper with module-level `_converter = DocumentConverter()` (loads DocLayNet + TableFormer ML models ~1-2GB RAM; amortises 10-15s load across task calls). Two pure functions: `parse_document(file_path)` for local files, `parse_document_from_bytes(content, filename)` for URL-fetched bytes. Both use `ConversionStatus.SUCCESS` guard.
- `parse_documents` Celery task on `queue="pipeline"`, `acks_late=True`, `max_retries=3` — first of 4 tasks in the M2 chain.
- Layer 1 idempotency: pre-check COUNT of unparsed documents before emitting `ingestion.started`; per-document `parse_status == 'parsed'` guard skips already-processed documents.
- Tenant DB connection fetched via `fernet_decrypt(agent.neon_connection_string)` — never in task args (CLAUDE.md non-negotiable rule enforced and tested).
- Three SSE events emitted in order: `ingestion.started` → `parsing.started` → `parsing.complete`.
- URL source path via `httpx.get(timeout=30, follow_redirects=True)` → `parse_document_from_bytes`.
- Fatal `RuntimeError` from Docling (bad PDF) marks `parse_status='failed'` and continues to next doc (no retry). Generic exceptions trigger `self.retry` with exponential backoff.
- 8 new unit tests added; full unit suite: 118 passed (was 110 before Wave 2).

## Task Commits

1. **Task 02-02-01: Build docling_service.py wrapper around DocumentConverter** — `848a40f`
2. **Task 02-02-02: Build parse_documents Celery task with source_hash idempotency guard and SSE emits** — `44dd596`
3. **Task 02-02-03: Write unit tests for parse_documents** — `78a7fbe`

## DocumentConverter Module-Level Init Observation

`_converter = DocumentConverter()` at module import loads DocLayNet + TableFormer ML models. On the Windows dev environment (Python 3.12, docling 2.93.0), model loading completes at first test run within the first test invocation — the 3 docling_service tests ran in ~66s total (most of which is the first-test model load). Subsequent tests in the same process reuse the module-level singleton (0 reload cost). Production workers will pay the load cost once at worker startup, then amortise across all task invocations.

## `tempfile.gettempdir()` on Windows Dev

`tempfile.gettempdir()` returns `C:\Users\Bantu\AppData\Local\Temp` — a valid, writable directory. The constructed path `gettempdir() / "vrd-uploads" / agent_id / {doc_id}{ext}` resolves correctly. Tests use `monkeypatch.setattr("app.worker.tasks.pipeline.parse.tempfile.gettempdir", ...)` to redirect to `tmp_path` fixture, confirming platform portability.

## URL-Source `page_count` Decision

URL-sourced documents parsed via `parse_document_from_bytes` return `None` for `page_count` when `hasattr(doc, 'pages')` is False (DoclingDocument may not populate `.pages` for streamed content without layout detection). `None` is the correct default — it is honest about the uncertainty. Future waves (chunking) read chunk counts from the `chunks` table directly; `page_count` here is informational for SSE consumers only.

## Decisions Made

- **RuntimeError message includes error_message strings** (not `repr(result.errors)`): the plan test spec required `assert "bad pdf" in str(exc_info.value)`, which meant the error text had to flow into the exception message. The initial service implementation used `str(result.errors)` (MagicMock repr), which failed the assertion. Fixed by collecting `err.error_message` into a list and joining with `;`. This is a Rule 1 auto-fix — the service contract was not meeting the plan's test expectations.

- **`parse_documents.run` call convention**: Celery `bind=True` makes `.run` a bound method where `self` is already the task instance. Tests call `parse_documents.run("t1", "a1", "j1", ["d1"])` directly without a `self_mock` argument. Retry counter (`self.request.retries`) is not needed for tests that don't exercise the retry path (idempotency and happy-path tests). This is a discovery correction, not a deviation from the plan's contract.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] RuntimeError message must contain error_message text**
- **Found during:** Task 02-02-01 (test run)
- **Issue:** `raise RuntimeError(f"Docling conversion failed: {result.errors}")` included `repr(result.errors)` (a MagicMock repr), not the `err.error_message` strings. Test assertion `assert "bad pdf" in str(exc_info.value)` failed.
- **Fix:** Collect `err.error_message` values into a list, join with `"; "`, include in RuntimeError message for both `parse_document` and `parse_document_from_bytes`.
- **Files modified:** `apps/api/app/services/docling_service.py`
- **Commit:** `848a40f`

**2. [Rule 1 - Bug] Test call convention for Celery bind=True tasks**
- **Found during:** Task 02-02-03 (test run — test 2, 3, 4, 5)
- **Issue:** Plan spec described calling `parse_documents.run(MagicMock(retries=0), tenant_id=..., ...)` — this fails because (a) `.run` is a bound method (no explicit self), (b) keyword args conflict with positional task args on `bind=True`.
- **Fix:** Call `parse_documents.run("t1", "a1", "j1", ["d1"])` directly. Retry-context patching (`patch.object(parse_documents, "request")`) fails because `request` is a property with no setter on Celery task proxy objects. The early-return (idempotency) and happy-path tests don't exercise `self.request.retries`, so no patching is needed.
- **Files modified:** `apps/api/tests/unit/test_parse_task.py`
- **Commit:** `78a7fbe`

**3. [Rule 1 - Bug] File path in tests must include "vrd-uploads" subdirectory**
- **Found during:** Task 02-02-03 (test 4, 5)
- **Issue:** Tests created fake PDF at `tmp_path / "a1" / "d1.pdf"` but task constructs path as `gettempdir() / "vrd-uploads" / agent_id / f"{doc_id}{ext}"`. `FileNotFoundError` raised.
- **Fix:** Create fake file at `tmp_path / "vrd-uploads" / "a1" / "d1.pdf"` in test fixtures.
- **Files modified:** `apps/api/tests/unit/test_parse_task.py`
- **Commit:** `78a7fbe`

## pytest Output

```
118 passed in 35.09s
```

New tests added by this plan (8):
- `tests/unit/test_docling_service.py` — 3 tests
- `tests/unit/test_parse_task.py` — 5 tests

## Known Stubs

None. This plan creates infrastructure tasks with no UI rendering or data sources — no stubs applicable.

## Threat Flags

No new network endpoints introduced beyond what is documented in the plan's `<threat_model>`.

- T-02-02-01 (Information Disclosure — conn string in task args): Mitigated. `test_parse_documents_no_conn_string_in_signature` verifies no conn/password params.
- T-02-02-03 (DoS — URL fetch): Mitigated. `httpx.get(timeout=30, follow_redirects=True)` enforced.
- T-02-02-04 (Information Disclosure — logs): Mitigated. `tenant_conn_str` held in local variable, never logged. Structlog calls reference `document_id`, `page_count`, `error_type` only.
- T-02-02-05 (Repudiation — retries): Mitigated. `acks_late=True` set; Layer 1 guard makes retries safe.

## Next Phase Readiness

- Wave 3 (02-03: chunking) can proceed — `parse_documents` returns `{"tenant_id", "agent_id", "job_id", "document_ids"}` ready for `chunk_documents.s()`.
- `chunk_documents` receives the result dict and re-fetches tenant DB connection via same pattern.
- `docling_service._converter` is available for `chunk_document()` to call `doc.tables` and `HybridChunker.chunk(doc)`.

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-05-13*
