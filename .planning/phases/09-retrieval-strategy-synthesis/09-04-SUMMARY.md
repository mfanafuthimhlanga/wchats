# Phase 09-04 Summary — Demo Script + Guarded E2E Test

**Wave:** 4 (depends on Wave 3 — strategy service, pipeline task, query expansion)
**Completed:** 2026-05-25
**Status:** Tasks 1 and 2 complete. Human checkpoint required.

---

## What Was Built

### Task 1: `scripts/demo_m9.sh`

Two-tenant strategy diff + eval comparison demo script. Modelled exactly on `scripts/demo_m8.sh`.

**Sections:**
1. **Prerequisites** — `redis-cli ping` + `curl /health` checks (verbatim from demo_m8.sh)
2. **Two-tenant provisioning** — Creates Tenant A (dense technical PDF) and Tenant B (FAQ plain-text) via `POST /api/v1/agents` with distinct soul/name values. Polls each for `status=ready` (24 × 5s = 120s).
3. **Ingest trigger** — `POST /api/v1/agents/{id}/documents` (multipart). Fixture paths documented in comments; graceful degradation if fixtures absent. Pipeline chain auto-runs `synthesize_retrieval_strategy` after `embed_and_migrate`.
4. **Strategy polling + side-by-side print (STR-02)** — Polls `GET /api/v1/agents/{id}` extracting `retrieval_strategy` via `python -c "..."` until non-empty `{}` (40 × 3s = 120s). Prints both configs side-by-side.
5. **Eval comparison (STR-03)** — Triggers `POST /eval-runs/trigger` (202), polls to completion (60 × 3s). PATCHes strategy to `{}`, triggers second run, polls to completion. Extracts `faithfulness` from `aggregate_scores` for both runs.
6. **Assertions** — Three `[PASS]`/`[FAIL]` lines, exits 0 only if all pass:
   - STR-02: `Tenant A vector_k != Tenant B vector_k`
   - STR-02: `Tenant B query_expansion=true`
   - STR-03: `synthesized faithfulness >= default faithfulness`

**Key compliance:**
- `set -euo pipefail` exactly once
- `python` not `python3` (Windows convention)
- No Docker — header documents 4 local processes
- API_KEY/ADMIN_KEY never echoed (only placeholder text in usage lines)
- Uses `eval-runs/trigger` (correct verified route, not bare `/eval-runs`)

### Task 2: `apps/api/tests/e2e/test_strategy_e2e.py`

Guarded E2E test covering STR-01/STR-02/STR-03. Skipped in CI unless `STRATEGY_E2E_ENABLED=1`.

**Guard pattern** (copied exactly from `test_retrieval_e2e.py`):
```python
STRATEGY_E2E_ENABLED = os.environ.get("STRATEGY_E2E_ENABLED", "0") == "1"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not STRATEGY_E2E_ENABLED, ...)]
```

**Tests:**
- `test_str01_strategy_is_auto_generated` — polls `GET /agents/{id}` up to 60s, asserts `retrieval_strategy` is a non-empty dict
- `test_str02_strategy_fields_in_bounds` — asserts all 6 `RetrievalStrategy` keys present; `vector_k` in [10,50], `final_k` in [3,10], `query_expansion` is bool, `metadata_filters` is list
- `test_str03_eval_run_returns_faithfulness` — triggers eval (202), polls to completion (3 min), asserts `faithfulness` is numeric in [0.0, 1.0] (tolerant — no hard threshold)

**No mocking** — real services only. Uses `httpx` (same as `test_retrieval_e2e.py`).

---

## Verification Outputs

### Task 1 — demo_m9.sh syntax check

```
SYNTAX OK
1
```
(`bash -n scripts/demo_m9.sh` exits 0; `grep -c "set -euo pipefail"` = 1)

```
eval-runs/trigger: 2 matches
retrieval_strategy: 8 matches
docker (case-insensitive): 1 match (only in "no Docker" comment — correct)
python3: 0 matches
```

### Task 2 — pytest skip check (without STRATEGY_E2E_ENABLED)

```
sss   [100%]
3 skipped (STRATEGY_E2E_ENABLED=1 required for real strategist E2E test)
```

All 3 tests skipped cleanly — no errors, no failures.

---

## Commit SHAs

| Task | SHA | Message |
|------|-----|---------|
| Task 1 | `ed92804` | `feat(09-04): add demo_m9.sh — two-tenant strategy diff + eval comparison` |
| Task 2 | `cd3d9d7` | `feat(09-04): add test_strategy_e2e.py — guarded STR-01/STR-02/STR-03 E2E test` |

---

## Human Checkpoint

**Gate:** ✅ APPROVED (2026-05-25) — Two distinct strategies generated; Agent B had `query_expansion=true`; script exited 0.

~~Blocking. The following must be verified manually before Wave 4 is considered complete.~~

### What to do

1. Start local services (no Docker):
   ```
   redis-server
   cd apps/api && uvicorn app.main:app --reload
   cd apps/api && celery -A app.worker.celery_app worker --queues pipeline,runtime
   ```
2. Supply corpus fixtures or URLs:
   - Tenant A: a dense technical PDF (2000+ chunks, long prose) → `scripts/fixtures/m9_tenant_a_technical.pdf`
   - Tenant B: a FAQ plain-text corpus (short answers) → `scripts/fixtures/m9_tenant_b_faq.txt`
   - OR edit Section 3 of `demo_m9.sh` to pass URLs via `--data-urlencode`
3. Run the demo:
   ```
   ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m9.sh
   ```
4. Confirm the output shows:
   - Two DIFFERENT `retrieval_strategy` JSON blocks (different `vector_k`/`bm25_k`)
   - `query_expansion=true` in Tenant B's strategy (FAQ corpus)
   - `[PASS] STR-03` — synthesized faithfulness >= default faithfulness
   - Script exits 0

5. (Optional) Run guarded E2E:
   ```
   STRATEGY_E2E_ENABLED=1 \
   STRATEGY_E2E_AGENT_ID=<uuid> \
   STRATEGY_E2E_API_KEY=<key> \
   python -m pytest apps/api/tests/e2e/test_strategy_e2e.py -v
   ```

### Resume signal

Type `approved` if the two strategies differ meaningfully and the synthesized eval is not worse than default. Otherwise describe what looked wrong (identical configs, expansion not enabled for FAQ, eval regression).

---

## Notes

- The `eval-runs/trigger` POST returns `{"status": "queued", "task_id": ..., "agent_id": ...}` — not a `run_id`. The demo polls `GET /eval-runs` and matches by position in the returned list (most recent first).
- Fixture files are not committed — the human checkpoint operator supplies them (or uses URL-based ingest).
- `STR-03` assertion uses `>=` (not `>`) to handle the case where both runs score equally (e.g., on an empty or minimal corpus during demo setup).
