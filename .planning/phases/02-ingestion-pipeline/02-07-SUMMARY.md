---
phase: "02"
plan: "07"
subsystem: "ingestion-pipeline"
tags: ["e2e", "demo", "ingestion", "sse", "celery", "pdf"]
dependency_graph:
  requires: ["02-06"]
  provides: ["demo-script", "e2e-test", "demo-pdf-fixture"]
  affects: ["M2-milestone-gate"]
tech_stack:
  added: []
  patterns:
    - "E2E demo script mirrors demo_m1.sh structure (set -euo pipefail, SSE stream, jq parsing)"
    - "E2E test gated by INGESTION_E2E_ENABLED=1 env var — no accidental API spend"
    - "E2E tests provision tenant+agent via live HTTP API (not SQLAlchemy fixtures)"
    - "Tenant DB inspection via fernet_decrypt + psycopg2 (connection string never echoed)"
key_files:
  created:
    - scripts/demo_m2.sh
    - apps/api/tests/integration/test_ingestion_e2e.py
  modified: []
decisions:
  - "E2E tests provision via HTTP API (not extending test_agent_and_job fixture) — test_agent_and_job creates status=pending agents with no neon_connection_string; E2E needs a ready agent with real tenant DB, so provisioning via the live API is the cleanest approach"
  - "Idempotency test re-dispatches the Celery chain tasks directly (parse_documents → embed_and_migrate) with a second job_id rather than re-uploading the file — this tests the 4-layer idempotency at the task layer without creating a second document row"
  - "demo_m2.sh inspect step uses Python inline script to decrypt neon_connection_string via fernet_decrypt — never echoed to terminal (T-02-07-03)"
metrics:
  duration: "~20 minutes"
  completed: "2026-05-13T19:14:32Z"
  tasks_completed: 2
  files_created: 2
---

# Phase 02 Plan 07: E2E Demo Script + E2E Test Summary

**One-liner:** Bash demo script streaming 11 M2 SSE events and Python E2E test gated by INGESTION_E2E_ENABLED=1 proving real-PDF ingestion end-to-end with idempotency assertion.

## What Was Built

### Task 02-07-01 (pre-completed by orchestrator)

`apps/api/tests/fixtures/demo_business.pdf` — Acme Coffee Roasters Customer Service Handbook (8.3 KB). Owner-authored, no licensing concerns. Contains a wholesale pricing table (visible pipe characters when extracted as Markdown — ING-03 visual proof candidate).

### Task 02-07-02 — scripts/demo_m2.sh

End-to-end demo script mirroring `scripts/demo_m1.sh` structure:

- `set -euo pipefail` throughout
- Provisions tenant + agent via `POST /tenants` + `POST /agents`, polls `GET /agents/{id}` until `status='ready'` (or uses pre-set `AGENT_ID` + `API_KEY`)
- POSTs `demo_business.pdf` to `POST /api/v1/agents/{id}/documents` using `curl -F files=@$PDF_PATH`
- Streams SSE events from the returned `events_url`; tracks all 11 M2 event types in `EXPECTED_EVENTS` array
- Exits 1 if any of the 11 events is missing; exits 0 on success
- Inspects tenant DB via Python inline script: COUNT(*) from chunks, chunk_metadata, embeddings, entities; heuristic table check via `SELECT content FROM chunks WHERE content LIKE '%|%'`
- Connection string decrypted in-process via `fernet_decrypt` — never printed to terminal

All 11 expected events verified present in `EXPECTED_EVENTS` array:
`ingestion.started`, `parsing.started`, `parsing.complete`, `chunking.started`, `chunking.complete`, `metadata.started`, `metadata.complete`, `embedding.started`, `embedding.complete`, `ingestion.complete`, `job.complete`

### Task 02-07-03 — apps/api/tests/integration/test_ingestion_e2e.py

Two E2E tests gated by `INGESTION_E2E_ENABLED=1`:

**`test_real_pdf_ingestion_end_to_end`**
- Provisions tenant + agent via live HTTP API
- Uploads `demo_business.pdf` to `POST /api/v1/agents/{id}/documents`
- Polls control DB `jobs` table until `status='complete'` (max 300s)
- Inspects tenant DB via `fernet_decrypt` + psycopg2: asserts chunk_count > 5, metadata_count == chunk_count, embedding_count == chunk_count, vector_dims == 1024, entity_count >= 1, and at least one chunk LIKE `%|%` (ING-03 Markdown table proof)

**`test_real_pdf_idempotent_rerun`**
- Provisions a fresh tenant + agent, uploads PDF once (run 1), captures chunk_count_before and embedding_count_before
- Re-dispatches the Celery chain directly (`parse_documents → chunk_documents → generate_metadata → embed_and_migrate`) with a second job_id for the same document_id
- Waits for run 2 to complete; asserts `chunk_count_before == chunk_count_after` AND `embedding_count_before == embedding_count_after`

## Verification Results

| Check | Result |
|-------|--------|
| `test -x scripts/demo_m2.sh` | PASS — executable |
| `set -euo pipefail` in script | PASS |
| All 11 M2 event types in EXPECTED_EVENTS | PASS |
| SELECT FROM chunks, chunk_metadata, embeddings, entities | PASS |
| Table chunk heuristic (LIKE '%\|%') | PASS |
| `pytest tests/integration/test_ingestion_e2e.py --collect-only -q` | PASS — 2 tests collected |
| `pytest tests/integration/test_ingestion_e2e.py -x -q` (no E2E flag) | PASS — 2 skipped, exit 0 |
| `pytest tests/unit/ -x -q` | PASS — 169 passed |

## Deviations from Plan

### Auto-fix: E2E fixture strategy changed (Rule 2 — correctness)

**Found during:** Task 02-07-03

**Issue:** The plan specified using `test_agent_and_job` fixture from conftest.py. That fixture creates an agent with `status='pending'` and no `neon_connection_string`. The E2E tests need a `ready` agent with a real (encrypted) tenant DB connection string — the fixture as-written cannot satisfy this without either: (a) major extension to the M1 fixture that would couple it to the M2 schema lifecycle, or (b) the xfail path documented in the plan.

**Fix:** E2E tests provision a fresh tenant + agent via the live HTTP API (identical to `demo_m2.sh` steps 1–2). This is cleaner than extending the M1 fixture because: (1) it tests the full real-API path including agent readiness, (2) it is self-contained, and (3) it avoids coupling M1 integration test infrastructure to M2 schema migration state.

**Files modified:** `apps/api/tests/integration/test_ingestion_e2e.py`

**No xfail needed** — the tests collect and skip cleanly; when `INGESTION_E2E_ENABLED=1` is set, they exercise the correct full path.

## Live Demo Run Status

Task 02-07-04 (human-verify: visual SSE stream check) is the orchestrator's responsibility. The demo script (`scripts/demo_m2.sh`) and E2E test are created and ready. The following has NOT been run yet (requires live docker-compose + real API keys):
- `bash scripts/demo_m2.sh` against live stack
- `INGESTION_E2E_ENABLED=1 pytest tests/integration/test_ingestion_e2e.py`

## ING-03 Visual Proof

The `demo_business.pdf` is an Acme Coffee Roasters handbook that contains a wholesale pricing table. When Docling parses this PDF and the table path in `chunking_service.py` converts it via `table.export_to_markdown()`, the resulting chunk will contain `|` characters (Markdown table rows). Both `demo_m2.sh` step 5 and `test_real_pdf_ingestion_end_to_end` assert this heuristic.

## Known Stubs

None — all assertions are concrete and data-driven.

## Threat Flags

None — no new network endpoints, auth paths, or file access patterns introduced beyond what was planned in the 02-07 threat model.

## Self-Check: PASSED

- `scripts/demo_m2.sh` exists: confirmed (created at commit fbf804e)
- `apps/api/tests/integration/test_ingestion_e2e.py` exists: confirmed (created at commit 9cd0ed8)
- `apps/api/tests/fixtures/demo_business.pdf` exists: confirmed (8.3 KB, pre-existing)
- Both commits in git log: fbf804e, 9cd0ed8
- 169 unit tests pass
- E2E tests collect (2) and skip cleanly (exit 0) without INGESTION_E2E_ENABLED
