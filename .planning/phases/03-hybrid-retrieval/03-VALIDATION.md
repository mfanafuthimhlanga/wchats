---
phase: 3
slug: hybrid-retrieval
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-16
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `apps/api/pytest.ini` (existing from M1/M2) |
| **Quick run command** | `cd apps/api && PYTHONPATH=. pytest tests/unit/retrieval/ -x -q` |
| **Full suite command** | `cd apps/api && PYTHONPATH=. pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds (unit only) / ~60 seconds (full) |

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Secure Behavior | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------------|-----------|-------------------|--------|
| 3-01-01 | 01 | 1 | RET-01, RET-02, RET-05 | agents.retrieval_strategy column exists; migration idempotent | unit | `pytest tests/unit/retrieval/test_migration.py -x -q` | ⬜ pending |
| 3-02-01 | 02 | 2 | RET-01 | vector search returns cosine scores; input_type="query" used | unit | `pytest tests/unit/retrieval/test_retrieval_service.py::test_vector_search -x -q` | ⬜ pending |
| 3-02-02 | 02 | 2 | RET-02 | BM25 uses tsvector only; pg_search never imported | unit | `pytest tests/unit/retrieval/test_retrieval_service.py::test_bm25_search -x -q` | ⬜ pending |
| 3-02-03 | 02 | 2 | RET-03 | RRF scores = 1/(60+rank_v) + 1/(60+rank_b); math verified | unit | `pytest tests/unit/retrieval/test_retrieval_service.py::test_rrf_fusion -x -q` | ⬜ pending |
| 3-02-04 | 02 | 2 | RET-04 | Voyage rerank called with model="rerank-2"; fallback fires on exception | unit | `pytest tests/unit/retrieval/test_retrieval_service.py::test_rerank -x -q` | ⬜ pending |
| 3-03-01 | 03 | 3 | RET-01–RET-06 | retrieve_and_rank: acks_late=True; no conn string in args; idempotency | unit | `pytest tests/unit/retrieval/test_retrieve_task.py -x -q` | ⬜ pending |
| 3-04-01 | 04 | 4 | RET-01–RET-06 | POST /agents/{id}/query → 202 + job_id; query.complete payload has trace | integration | `pytest tests/integration/test_query_route.py -x -q` | ⬜ pending |
| 3-05-01 | 05 | 5 | RET-06 | query.complete event contains trace with 4 candidate sets | integration | `pytest tests/integration/test_query_e2e.py -x -q` | ⬜ pending |
| 3-06-01 | 06 | 6 | RET-08 | Notebook runs, all cells execute, dataframes non-empty | manual | `jupyter nbconvert --to notebook --execute notebooks/demo_m3.ipynb` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `apps/api/tests/unit/retrieval/__init__.py` — empty init
- [ ] `apps/api/tests/unit/retrieval/test_retrieval_service.py` — stubs for RET-01 to RET-04
- [ ] `apps/api/tests/unit/retrieval/test_retrieve_task.py` — stubs for Celery task
- [ ] `apps/api/tests/integration/test_query_route.py` — stub for route test
- [ ] `apps/api/tests/conftest.py` — shared fixtures already exist from M1/M2

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Jupyter notebook shows meaningfully different candidate sets (vector vs BM25 vs hybrid) | RET-08 | Requires real M2 tenant DB with ingested data; cannot automate without E2E env | Run `notebooks/demo_m3.ipynb` against real tenant DB, visually verify 4 dataframes differ |
| BM25 and vector-only produce different top results on a test query | RET-08 (ROADMAP success criterion 2) | Requires real data; semantic divergence is not unit-testable | Use demo notebook, pick query where keyword match ≠ semantic match |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
