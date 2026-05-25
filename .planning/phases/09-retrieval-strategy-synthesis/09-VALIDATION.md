---
phase: 9
slug: retrieval-strategy-synthesis
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-25
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | `apps/api/pyproject.toml` |
| **Quick run command** | `cd apps/api && python -m pytest tests/unit/test_strategy_service.py tests/unit/test_strategy_task.py -x -q` |
| **Full suite command** | `cd apps/api && python -m pytest tests/ -x -q --ignore=tests/e2e` |
| **Estimated runtime** | ~30 seconds (unit only) |

---

## Sampling Rate

- **After every task commit:** Run `cd apps/api && python -m pytest tests/unit/test_strategy_service.py tests/unit/test_strategy_task.py -x -q`
- **After every plan wave:** Run `cd apps/api && python -m pytest tests/ -x -q --ignore=tests/e2e`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 09-01-T1 | 01 | 1 | STR-02 | — | `run_strategist` does not log `conn_str`; asyncio bridge uses `asyncio.run(asyncio.wait_for(...))` | unit | `pytest tests/unit/test_strategy_service.py::test_run_strategist_calls_asyncio_run -x` | ❌ W0 | ⬜ pending |
| 09-01-T2 | 01 | 1 | STR-03 | — | `_expand_query` returns 3 queries; `rrf_fuse_with_expansion` calls `rrf_fuse` per variant | unit | `pytest tests/unit/test_strategy_service.py::test_expand_query_returns_three tests/unit/test_strategy_service.py::test_expansion_calls_rrf_fuse_per_variant -x` | ❌ W0 | ⬜ pending |
| 09-01-T3 | 01 | 1 | STR-01, STR-02 | — | Corpus signals dict has correct shape from psycopg2 mock; `model_validate` accepts string-typed LLM fields | unit | `pytest tests/unit/test_strategy_service.py::test_corpus_signals_shape tests/unit/test_strategy_service.py::test_strategy_validate_string_inputs -x` | ❌ W0 | ⬜ pending |
| 09-02-T1 | 02 | 2 | STR-01 | CTL-08 | `synthesize_retrieval_strategy` receives result dict; `conn_str` never in task args; writes non-empty strategy to DB | unit | `pytest tests/unit/test_strategy_task.py::test_strategy_written_to_db tests/unit/test_strategy_task.py::test_receives_embed_result_dict -x` | ❌ W0 | ⬜ pending |
| 09-02-T1 | 02 | 2 | STR-01 | — | Idempotency — skips if strategy already set; `strategy_resynthesis_flagged=True` bypasses guard | unit | `pytest tests/unit/test_strategy_task.py::test_idempotency_skip tests/unit/test_strategy_task.py::test_resynthesis_flag_bypasses_guard -x` | ❌ W0 | ⬜ pending |
| 09-02-T2 | 02 | 2 | STR-01 | — | `synthesize_retrieval_strategy.s()` is 5th link in `documents.py` chain | unit | `pytest tests/unit/test_strategy_task.py::test_receives_embed_result_dict -x` | ❌ W0 | ⬜ pending |
| 09-03-T1 | 03 | 3 | STR-01, STR-02 | — | All `test_strategy_service.py` stubs de-xfailed and passing | unit | `pytest tests/unit/test_strategy_service.py -x -q` | ❌ W0 | ⬜ pending |
| 09-03-T2 | 03 | 3 | STR-01 | — | All `test_strategy_task.py` stubs de-xfailed and passing | unit | `pytest tests/unit/test_strategy_task.py -x -q` | ❌ W0 | ⬜ pending |
| 09-04-T1 | 04 | 4 | STR-01, STR-02, STR-03 | — | `demo_m9.sh` exits 0; no Docker; two tenants show different strategies; eval comparison printed | manual | `bash scripts/demo_m9.sh` (autonomous: false — human checkpoint) | ❌ | ⬜ pending |
| 09-04-T2 | 04 | 4 | STR-01, STR-03 | — | E2E test passes when `STRATEGY_E2E_ENABLED=1` | e2e | `STRATEGY_E2E_ENABLED=1 pytest tests/e2e/test_strategy_e2e.py -x -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `apps/api/tests/unit/test_strategy_service.py` — xfail stubs for STR-01, STR-02 (corpus signals, asyncio bridge, string-typed field validation)
- [ ] `apps/api/tests/unit/test_strategy_task.py` — xfail stubs for STR-01 (task wiring, idempotency, chain arg format, resynthesis flag bypass)
- [ ] `apps/api/tests/e2e/test_strategy_e2e.py` — guarded E2E stub (`skipif(not STRATEGY_E2E_ENABLED)`) for STR-01–STR-03 end-to-end

Existing infrastructure covers pytest config, conftest.py fixtures, and test runner — no new test framework installs required.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Two tenants with different corpora produce different `retrieval_strategy` JSONB configs | STR-02 | Requires staging two separate tenant corpora via API + human inspection of diff | Run `bash scripts/demo_m9.sh`; visually confirm Section 3 output shows different values for at least `vector_k`, `bm25_k`, or `query_expansion` between Tenant A and Tenant B |
| Synthesized strategy outperforms `{}` default on Ragas metrics | STR-03 | Requires two complete `run_eval_suite` Celery runs against Neon branches; Ragas calls real LLM | Run `bash scripts/demo_m9.sh`; confirm Section 5 prints eval comparison table where synthesized strategy mean Ragas score > default strategy mean Ragas score |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
