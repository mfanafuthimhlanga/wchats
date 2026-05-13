---
phase: 2
slug: ingestion-pipeline
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-13
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) + pytest-asyncio 1.3.0 (existing) |
| **Config file** | `apps/api/pyproject.toml` → `[tool.pytest.ini_options]` |
| **Quick run command** | `cd apps/api && pytest tests/unit/ -x -q` |
| **Full suite command** | `cd apps/api && pytest tests/unit/ tests/integration/ -x -q -m "not e2e"` |
| **Estimated runtime** | ~30 seconds (unit), ~90 seconds (unit + integration) |

---

## Sampling Rate

- **After every task commit:** Run `cd apps/api && pytest tests/unit/ -x -q`
- **After every plan wave:** Run `cd apps/api && pytest tests/unit/ tests/integration/ -x -q -m "not e2e"`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds (unit only)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | deps+migration | 1 | ING-05 | T-02-01 / — | deterministic UUID never exposes internal state | unit | `pytest tests/unit/test_chunk_id.py -x -q` | ❌ W0 | ⬜ pending |
| 02-02-01 | parse_documents | 2 | ING-02 | T-02-02 / — | parse skips duplicate documents (hash guard) | unit | `pytest tests/unit/test_parse_task.py -x -q` | ❌ W0 | ⬜ pending |
| 02-03-01 | chunk_documents | 3 | ING-03, ING-04 | T-02-03 / — | tables produce Markdown, not flattened text | unit | `pytest tests/unit/test_chunk_task.py -x -q` | ❌ W0 | ⬜ pending |
| 02-04-01 | generate_metadata | 4 | ING-06 | T-02-04 / — | metadata skips already-enriched chunks | unit | `pytest tests/unit/test_metadata_task.py -x -q` | ❌ W0 | ⬜ pending |
| 02-05-01 | embed_and_migrate | 5 | ING-07 | T-02-05 / — | upsert idempotency (no duplicate vectors) | unit | `pytest tests/unit/test_embed_task.py -x -q` | ❌ W0 | ⬜ pending |
| 02-06-01 | routes+chain | 6 | ING-01, ING-08, ING-09 | T-02-06 / — | agent ownership verified before dispatch | unit+integration | `pytest tests/unit/test_document_routes.py tests/integration/test_ingestion_chain.py -x -q -m "not e2e"` | ❌ W0 | ⬜ pending |
| 02-07-01 | demo | 7 | ING-10 | — / — | N/A | e2e | `INGESTION_E2E_ENABLED=1 pytest tests/integration/test_ingestion_e2e.py -x -m integration` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_chunk_id.py` — stubs for ING-05 (uuid5 determinism, ordinal uniqueness)
- [ ] `tests/unit/test_parse_task.py` — stubs for ING-02 (mocked Docling, idempotency guard)
- [ ] `tests/unit/test_chunk_task.py` — stubs for ING-03 (table path) + ING-04 (structure-aware)
- [ ] `tests/unit/test_metadata_task.py` — stubs for ING-06 (mocked Anthropic, idempotency skip)
- [ ] `tests/unit/test_embed_task.py` — stubs for ING-07 (mocked Voyage, upsert idempotency)
- [ ] `tests/unit/test_document_routes.py` — stubs for ING-01 + ING-08 (upload endpoint, SSE events)
- [ ] `tests/integration/test_ingestion_chain.py` — stub for ING-09 (full chain, no duplicates)
- [ ] `tests/integration/test_ingestion_e2e.py` — stub for ING-10 (real PDF, skip unless E2E enabled)
- [ ] `tests/fixtures/demo_business.pdf` — small real business PDF (< 500KB) with at least one table

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real PDF tables appear as Markdown rows (not prose) | ING-03 | Requires visual inspection of chunk content | Run `INGESTION_E2E_ENABLED=1 pytest tests/integration/test_ingestion_e2e.py -x -s` and inspect logged chunk text for the table fixture |
| SSE stream shows all 11 events in correct order | ING-08 | Visual SSE stream verification | Run `scripts/demo_m2.sh` and observe event sequence in terminal |
| HNSW index valid after reindex | ING-07 | Requires pgvector index inspection | Run `psql $TENANT_DB_URL -c "\d+ embeddings"` and verify `embeddings_vector_hnsw_idx` exists |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s (unit suite)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
