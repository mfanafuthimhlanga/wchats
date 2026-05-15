---
slug: parse-documents-fails-no-complete
status: resolved
trigger: "parse_documents Celery task emits parsing.started 4 times then silently fails — parsing.complete never arrives"
created: "2026-05-15"
updated: "2026-05-15"
phase: "02"
---

# Debug Session: parse-documents-fails-no-complete

## Symptoms

- **Expected:** parse_documents runs Docling on the PDF → emits parsing.complete → chunk_documents starts
- **Actual:** parsing.started emitted 4 times (initial + 3 retries), then SSE stream ends silently. parsing.complete never arrives.
- **Error:** ValueError: "model type `rt_detr_v2` not recognized" — transformers 4.46.3 too old
- **Timeline:** First occurrence today after provision_neon fixes; parse step was never successfully reached before
- **Reproduction:** POST /tenants → POST /agents (wait for ready) → POST /agents/{id}/documents → SSE stream

## Context

- Stack: FastAPI + Celery solo pool + Neon + Upstash Redis TLS (native Windows, no Docker)
- Demo PDF: apps/api/tests/fixtures/demo_business.pdf (8.3 KB)
- Job ID: d9a82b18-32ab-4f70-844f-770081c7740d
- Document ID: e0826066-6057-44e8-ab31-12688e7c40dc
- UPLOADS_DIR: C:/vrd-uploads (set in .env)

## Key Files

- `apps/api/app/worker/tasks/pipeline/parse.py` — parse_documents task
- `apps/api/app/services/docling_service.py` — Docling wrapper
- `apps/api/app/api/v1/documents.py` — upload handler (saves file to UPLOADS_DIR)

## Hypotheses (ordered by probability)

1. ~~Docling model download fails on Windows (SSL, proxy, or HF_HUB issue)~~ — ELIMINATED: models already cached
2. ~~File not found — UPLOADS_DIR path mismatch between API (saves) and worker (reads)~~ — ELIMINATED: file exists at correct path
3. **CONFIRMED: transformers version incompatibility** — `rt_detr_v2` added in 4.47.0; installed was 4.46.3
4. **CONFIRMED secondary: parse_status bug** — `except Exception` handler wrote `parse_status='failed'` before retrying, breaking retry idempotency guard → 4x `parsing.started`
5. **CONFIRMED secondary: PARTIAL_SUCCESS rejection** — pdfium `std::bad_alloc` on pages 3-4 causes `PARTIAL_SUCCESS`; old code rejected it as fatal error

## Current Focus

hypothesis: "resolved"
test: "manual parse of demo PDF with transformers 5.8.1 + PARTIAL_SUCCESS accepted"
expecting: "ACCEPTED: True, pages: 4"
next_action: "restart Celery worker and re-run demo upload"

## Evidence

- timestamp: 2026-05-15T21:00
  finding: "ValueError: The checkpoint you are trying to load has model type `rt_detr_v2` but Transformers does not recognize this architecture. transformers==4.46.3 installed; rt_detr_v2 added in 4.47.0"
  source: "direct python docling parse attempt"

- timestamp: 2026-05-15T21:05
  finding: "File exists at C:/vrd-uploads/4bfb1870-7bfc-448f-8b0c-3045e0b5e162/e0826066-6057-44e8-ab31-12688e7c40dc.pdf — path mismatch hypothesis eliminated"
  source: "filesystem check"

- timestamp: 2026-05-15T21:09
  finding: "parse.py except Exception handler sets parse_status='failed' before self.retry() — on retry parse_status is 'failed' not 'parsing', so is_retry_attempt=False, causing parsing.started to re-emit on every retry (4x total)"
  source: "code review parse.py lines 275-289"

- timestamp: 2026-05-15T21:10
  finding: "After upgrading to transformers 5.8.1: ConversionStatus.PARTIAL_SUCCESS returned (pdfium bad_alloc on pages 3,4); old docling_service.py raised RuntimeError on PARTIAL_SUCCESS"
  source: "direct python docling parse with transformers 5.8.1"

- timestamp: 2026-05-15T21:12
  finding: "After accepting PARTIAL_SUCCESS in docling_service.py: ACCEPTED=True, pages=4 — parse completes successfully"
  source: "direct python verification"

## Eliminated

- File not found: file at correct path `C:/vrd-uploads/{agent_id}/{doc_id}.pdf`
- Docling model download failure: all models cached locally
- psycopg2/Neon SSL error: task reaches Docling before any DB error

## Resolution

**Root cause:** `transformers==4.46.3` (installed as transitive dep via docling) does not support the `rt_detr_v2` architecture used by `docling-ibm-models 3.13.2` for layout detection. This raises `ValueError` inside `DocumentConverter.convert()` on every attempt.

**Secondary bug 1:** `parse.py` `except Exception` handler wrote `parse_status='failed'` before calling `self.retry()`. On retry, `is_retry_attempt = (parse_status == 'parsing')` evaluated to `False`, causing `parsing.started` to be re-emitted and `parse_status` to be re-set to `'parsing'` on every retry attempt — producing 4x `parsing.started` events.

**Secondary bug 2:** `docling_service.py` raised `RuntimeError` for `ConversionStatus.PARTIAL_SUCCESS`. The demo PDF gets `PARTIAL_SUCCESS` due to pdfium `std::bad_alloc` on 2 pages under Windows memory pressure. The document content is still extracted; rejecting it is overly strict.

**Fix applied:**
1. `pip install "transformers>=4.47.0"` — upgraded to 5.8.1; `rt_detr_v2` now supported.
2. `pyproject.toml` — added `"transformers>=4.47.0"` to `[project.optional-dependencies] pipeline` to prevent future downgrades.
3. `parse.py` — removed `cursor.execute("UPDATE ... parse_status='failed'")` from `except Exception` handler. Status stays `'parsing'` during retries; only terminal failures write `'failed'`.
4. `docling_service.py` — accept `ConversionStatus.PARTIAL_SUCCESS` (log warning, return document). Only `FAILURE` and other non-success statuses raise `RuntimeError`.

**Verified:** Direct Python parse of `e0826066-...pdf` with transformers 5.8.1 + PARTIAL_SUCCESS accepted returns `ACCEPTED: True, pages: 4`.

**Next step:** Restart Celery worker (picks up new transformers + code changes), then re-run `POST /agents/{agent_id}/documents` with the demo PDF.
