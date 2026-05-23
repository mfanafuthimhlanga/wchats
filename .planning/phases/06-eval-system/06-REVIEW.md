---
phase: "06"
status: findings
files_reviewed: 19
findings:
  critical: 2
  warning: 5
  info: 4
  total: 11
reviewed_at: 2026-05-23
---

# Phase 06 Code Review — Eval System

## Critical

### CR-001 (Critical) — SQL Injection in mine_production_scenarios INTERVAL clause

**File:** `apps/api/app/services/scenario_service.py:335`

**Issue:** The `lookback_hours` parameter is passed as a bind variable to the INTERVAL expression using `:hours`, but PostgreSQL does not accept a bind parameter inside an `INTERVAL` literal. The query `INTERVAL ':hours hours'` is treated as a string literal — the colon prefix and the word `hours` are part of the string, not a parameter substitution. The bind dict `{"hours": lookback_hours}` is silently ignored, so the query runs with the literal string `:hours hours` which PostgreSQL rejects or interprets as zero. More importantly, if the intent were to substitute it via f-string or string concatenation in a future refactor, this becomes an injection vector. The current form simply silently fails to apply the lookback window, meaning the query returns all events ever (or errors) rather than the intended 168-hour window.

**Fix:** Cast the parameter outside the string literal using PostgreSQL's `make_interval` or cast syntax:
```python
AND je.created_at > NOW() - make_interval(hours => :hours)
```
Or pass a precomputed interval string:
```python
AND je.created_at > NOW() - CAST(:hours_interval AS INTERVAL)
```
with `{"hours_interval": f"{lookback_hours} hours"}` as the param dict.

---

### CR-002 (Critical) — DataFrame index assumption breaks score-to-scenario alignment

**File:** `apps/api/app/services/eval_service.py:141-143`

**Issue:** After `results.to_pandas()` the code iterates `df.iterrows()` and uses `idx` (the DataFrame index) to look up `valid_scenarios[idx]`. Pandas `iterrows()` yields the DataFrame's row label as `idx`, not a sequential 0-based integer. If any Ragas metric returns `NaN` for a row and pandas drops or reorders rows (or if `evaluate()` internally reindexes), the integer index `idx` will be out of sync with the position in `valid_scenarios`. This produces silent mismatches: the wrong scenario's `id` is attached to the wrong score row, corrupting `write_eval_results` and `promote_to_verified_qa`. The `ON CONFLICT DO NOTHING` guard in the promotion SQL does not protect against this because each row has a freshly-generated `gen_random_uuid()` primary key.

**Fix:** Use positional enumeration instead of relying on the pandas label:
```python
for pos, (_, row) in enumerate(df.iterrows()):
    scenario = valid_scenarios[pos] if pos < len(valid_scenarios) else {}
```
Or, more robustly, use `df.reset_index(drop=True)` before iteration and use `.iloc[pos]` with `enumerate`.

---

## Warnings

### WR-001 (Warning) — Langfuse 3.x dependency contradicts CLAUDE.md v4 constraint

**File:** `apps/api/pyproject.toml:27`

**Issue:** The dependency is pinned to `langfuse==3.12.1`, but CLAUDE.md rule 6 states "Langfuse v4 API only — `start_span()`/`start_generation()` are gone." Installing v3 means M5 and M6 code that calls the v4 API will either receive deprecation warnings or outright `AttributeError` at runtime if v4 patterns were used anywhere in the existing codebase. The constraint in CLAUDE.md exists precisely because v3 and v4 have incompatible APIs.

**Fix:** Update the pinned version to `langfuse>=4.0.0,<5.0.0` (or the most recent stable 4.x release) and verify all Langfuse call sites in the codebase are using the v4 API.

---

### WR-002 (Warning) — eval_run_id insert uses production conn_str for status updates but branch_conn_str for results

**File:** `apps/api/app/worker/tasks/runtime/eval.py:283,305`

**Issue:** After the Neon branch is created (step 6), `write_eval_results` and `promote_to_verified_qa` are called with `branch_conn_str` (correct — they write to the branch). But `update_eval_run_status(run_id, "complete", ..., branch_conn_str=conn_str)` at line 283 and the failure-path call at line 305 both pass `conn_str` (the production connection) rather than `branch_conn_str`. This means `eval_runs.status` is updated on the production DB while results live on the branch — the eval run row on the branch stays in `status='running'` indefinitely. On the success path this is benign because the eval_run was inserted on `conn_str` (production), but it is architecturally inconsistent with the design intent that all eval state for a run lives on the branch (D-10).

Additionally, `run_eval_for_agent` in `eval_service.py` is called with `branch_conn_str` for its status update path and that function internally calls `update_eval_run_status` with `branch_conn_str`, creating a dual-path that is confusing and error-prone.

**Fix:** Clarify in `run_eval_suite` whether `eval_runs` rows live on production or on the branch. If on production (current insertion at line 235 uses `conn_str`), all `update_eval_run_status` calls in the task should consistently use `conn_str`. The `run_eval_for_agent` call should then pass `conn_str` as `branch_conn_str`, or `run_eval_for_agent`'s parameter should be renamed to avoid confusion.

---

### WR-003 (Warning) — ON CONFLICT DO NOTHING on verified_qa has no conflict target; silently ignores all constraint errors

**File:** `apps/api/app/services/eval_service.py:329`

**Issue:** `ON CONFLICT DO NOTHING` without a conflict target applies to every uniqueness or exclusion constraint on the table. The `verified_qa` table (from the migration) has only a primary key (`id UUID DEFAULT gen_random_uuid()`). Since `id` is generated fresh every call via `gen_random_uuid()` in the INSERT SQL itself, there can never be a primary key conflict. The ON CONFLICT clause therefore never fires and provides no idempotency protection on retry. If `acks_late=True` causes a retry after a partial commit, duplicate verified_qa rows will be inserted.

**Fix:** To make promotion truly idempotent, add a unique constraint on `(question)` or on a hash of `question` in the migration, and reference it in `ON CONFLICT (question) DO NOTHING`. Alternatively, add a deduplication check before insertion: `SELECT id FROM verified_qa WHERE question = %(question)s LIMIT 1` and skip if found.

---

### WR-004 (Warning) — `passed` flag in evals.py uses EVAL_FAITHFULNESS_THRESHOLD for all four metrics

**File:** `apps/api/app/api/v1/evals.py:241-246`

**Issue:** The `passed` flag in `get_eval_run_results` requires all four metrics (`faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`) to be >= `settings.EVAL_FAITHFULNESS_THRESHOLD` (0.90). However, the promotion gate in `eval_service.promote_to_verified_qa` only requires `faithfulness` and `answer_relevancy` to meet their respective thresholds (`EVAL_FAITHFULNESS_THRESHOLD` and `EVAL_RELEVANCY_THRESHOLD`). This means a scenario can be promoted to `verified_qa` (promotion gate: 2 metrics) but still shown as `FAIL` in the UI (display gate: 4 metrics), confusing the operator who sees "FAIL" next to a scenario that was actually promoted.

**Fix:** Either align the `passed` computation to use only the two promotion-gate metrics, or expose both a `promoted` boolean (2-metric gate) and a `fully_passed` boolean (4-metric gate) in the response. Use `settings.EVAL_RELEVANCY_THRESHOLD` for `answer_relevancy` rather than re-using `EVAL_FAITHFULNESS_THRESHOLD`.

---

### WR-005 (Warning) — Polling loop in eval page.tsx checks stale `evalRunsQuery.data` inside interval callback

**File:** `apps/admin/app/agents/[id]/eval/page.tsx:229-232`

**Issue:** Inside the `setInterval` callback, `evalRunsQuery.data?.eval_runs` is read from the closure. React Query's `invalidateQueries` triggers a refetch, but the closure-captured `evalRunsQuery.data` reference is stale — it reflects the data at the time the effect ran, not the data after the refetch completes. As a result, the `runs[0].status === 'complete'` check may never observe the updated status, and `isRunning` stays `true` even after the task finishes. The user sees a permanently spinning button.

**Fix:** Read the latest query data from `queryClient.getQueryData(['eval-runs', id])` inside the interval callback rather than from the stale closure, or add `evalRunsQuery.data` to the `useEffect` dependency array (and accept the cleanup/restart on each refetch) so the effect re-runs with fresh data.

---

## Info

### IN-001 (Info) — `run_eval_suite` step numbering skips Step 2 in comments

**File:** `apps/api/app/worker/tasks/runtime/eval.py:120,166`

**Issue:** The task comment headers label the idempotency guard as "Step 1" and the scenario fetch as "Step 3" — "Step 2" does not appear. The docstring lists the sequence as steps 1-6 with "decrypt conn_str" as step 2, but the actual code never labels this. This creates confusion when reading the task alongside the docstring.

**Fix:** Add a `# Step 2 — decrypt conn_str` comment at line 131 to match the docstring sequence, or renumber the comment headers to match actual code blocks.

---

### IN-002 (Info) — `update_eval_run_status` parameter `finished_at` typed as `bool` but named like a value

**File:** `apps/api/app/services/eval_service.py:238`

**Issue:** The parameter `finished_at: bool` is a flag, not a timestamp. The name implies a caller would pass a datetime. This is a readability issue — callers pass `finished_at=True` or `finished_at=False`, which reads awkwardly and obscures intent.

**Fix:** Rename the parameter to `set_finished_at: bool` or `mark_finished: bool` to make the boolean semantics explicit at call sites.

---

### IN-003 (Info) — `promote_to_verified_qa` ON CONFLICT in verified_qa INSERT is not idempotent for the `ON CONFLICT DO NOTHING` stated reason but comment says otherwise

**File:** `apps/api/app/services/eval_service.py:299`

**Issue:** The docstring says "ON CONFLICT DO NOTHING ensures idempotent promotion on Celery retry (acks_late + idempotency rule)" — this comment is inaccurate because the INSERT uses `gen_random_uuid()` as the primary key (no stable conflict target). See WR-003. The comment misleads future readers into believing retry safety is guaranteed when it is not.

**Fix:** Remove or correct the comment pending the fix described in WR-003.

---

### IN-004 (Info) — Demo script Section 1 calls `generate_eval_suite.apply_async` but treats all stderr as failure

**File:** `scripts/demo_m6.sh:112`

**Issue:** The failure detection pattern `grep -q "GENERATE_FAILED\|Error\|Traceback"` matches any line containing the word "Error" — including structlog INFO lines like `"INFO: Application startup complete"` that uvicorn emits to stderr when the worker imports the Celery app, or deprecation warnings containing "DeprecationError". This causes false-positive failure detection, printing a spurious warning in a clean run.

**Fix:** Change the failure detection to grep specifically for `"GENERATE_FAILED"` only (since that string is only emitted by the `|| echo "GENERATE_FAILED"` fallback), or capture stdout and stderr separately using `2>/dev/null` on the Python invocation within the section 1 block.
