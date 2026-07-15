---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 04
subsystem: api
tags: [ragas, celery, postgres, psycopg2, retrieval, rag-health, alerts, alembic]

# Dependency graph
requires:
  - phase: 21-02
    provides: metrics.py router scaffold (GET /agents/{id}/metrics, IDOR + conn_str pattern) — left structured for this plan's addition
  - phase: 21-03
    provides: tenant migration 0010 (retrieval_metrics table, citation_coverage/faithfulness nullable columns), retrieval_metrics_service.read_retrieval_health
provides:
  - run_retrieval_faithfulness (runtime queue, sampled) — UPDATEs retrieval_metrics.citation_coverage/faithfulness
  - check_index_staleness + check_index_staleness_beat (pipeline queue) — stale-doc + embedding-drift scan, alert_service wiring
  - compute_index_staleness_summary (plain function, shared by the task and the route)
  - GET /agents/{id}/retrieval-health endpoint
  - control-DB migration 0017 (alerts.alert_type widened to include 'index_staleness')
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy in-function ragas/instructor/anthropic imports (never module top-level) so a module stays importable despite the pre-existing ragas->langchain_community.chat_models.vertexai ModuleNotFoundError in this environment"
    - "Post-Auditor gating: appending a task to the END of an existing celery_chain so it can honestly query a preceding chain step's committed verdict, instead of trying to know that verdict at the moment the whole chain is dispatched"
    - "Shared plain-function core (compute_index_staleness_summary) reused by both a Celery task and a FastAPI route via asyncio.to_thread — no cached/duplicated staleness table"
    - "Per-signal independent degrade-to-not_tracked: two scans in one summary function, either can fail without corrupting the other"

key-files:
  created:
    - apps/api/app/worker/tasks/runtime/retrieval_eval.py
    - apps/api/app/worker/tasks/pipeline/staleness.py
    - apps/api/alembic/versions/0017_alerts_index_staleness_type.py
    - apps/api/tests/unit/test_retrieval_faithfulness_task.py
    - apps/api/tests/unit/test_index_staleness.py
    - apps/api/tests/unit/test_retrieval_health_route.py
  modified:
    - apps/api/app/core/config.py
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/app/worker/celery_app.py
    - apps/api/app/api/v1/metrics.py
    - apps/api/tests/unit/test_agent_task.py

key-decisions:
  - "The sample-rate-OR-Auditor-flagged gate is evaluated INSIDE run_retrieval_faithfulness, not at agent.py's dispatch point. Auditor's verdict does not exist yet when agent.py assembles the celery_chain (Auditor is a preceding step in the SAME chain), so run_retrieval_faithfulness.si(agent_id, job_id) is appended as the chain's 4th/last step and queries job_events for the already-committed auditor.complete verdict once it runs. This is a sequencing decision, not a reinterpretation of DOMAIN-NOTES §2's sampling policy."
  - "citation_coverage is computed as len(citations_in_response) / len(retrieve_calls_with_a_result), capped at 1.0, None when nothing was retrieved. This is a coarse proxy (the schema does not persist per-chunk citation attribution) — documented in code, not silently assumed to be exact chunk-level coverage."
  - "The Ragas Faithfulness question input is sourced from the tenant-DB messages table's most recent role='user' row for the turn's conversation_id (matched by recency, not job_id — messages has no job_id column), because the user's question text is intentionally never persisted to job_events (T-04-03-05: message text must never be logged)."
  - "documents has no updated_at column (confirmed against alembic_tenant 0001/0002 — only created_at, set once at INSERT). compute_index_staleness_summary uses documents.created_at as the best-available 'source last touched' proxy for the staleness signal, documented explicitly rather than silently assumed."
  - "check_index_staleness reuses the EXISTING alert_service (_write_alert/_active_alert_exists/send_alert_email) instead of a new table, per the plan's explicit instruction. No public wrapper was added to alert_service.py (out of this plan's files_modified scope) — staleness.py calls the underscore-prefixed helpers directly."

requirements-completed: [OPS-07, OPS-08]

# Metrics
duration: ~2.5h
completed: 2026-07-16
status: complete
---

# Phase 21 Plan 04: Retrieval Faithfulness + Index Staleness (OPS-07/OPS-08) Summary

**Sampled Ragas 0.4.x faithfulness + citation-coverage task (lazy-imported to survive the pre-existing ragas/vertexai bug), a pipeline-queue index-staleness/embedding-drift scan wired to the existing alert_service, and the GET /agents/{id}/retrieval-health endpoint that reads both.**

## Performance

- **Duration:** ~2.5h
- **Completed:** 2026-07-16
- **Tasks:** 3/3 completed
- **Files modified:** 11 (6 created, 5 modified)

## What Was Built

### Task 1 — OPS-07: `run_retrieval_faithfulness` (runtime queue, sampled)

`apps/api/app/worker/tasks/runtime/retrieval_eval.py`:
- `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE: float = 0.1` added to `Settings` (config.py).
- `run_retrieval_faithfulness(self, agent_id, job_id)` — `acks_late=True`, `max_retries=2`, `queue="runtime"`. Args are `agent_id`/`job_id` only; `conn_str` decrypted at runtime from the control DB.
- Idempotency guard: `SELECT faithfulness FROM retrieval_metrics WHERE job_id = ...` — skips recompute if already non-NULL, skips entirely if no row exists (turn never called `retrieve`).
- Gating: `random.random() < settings.RETRIEVAL_FAITHFULNESS_SAMPLE_RATE` OR the turn's `auditor.complete` verdict is `ungrounded`/`partial` (queried from control-DB `job_events`).
- `citation_coverage` = cited spans / retrieve-calls-with-a-result, capped at 1.0, `None` when nothing was retrieved.
- `_compute_ragas_faithfulness` — all `ragas`/`instructor`/`anthropic` imports are **inside the function body**, never at module top-level, so `retrieval_eval.py` (and `celery_app.py`'s eager `include=[...]` import of it) never touches the broken `ragas.llms.base -> langchain_community.chat_models.vertexai` chain. Mirrors `eval_service.run_ragas_eval`'s `InstructorLLM`/`Faithfulness` call shape.
- `UPDATE retrieval_metrics SET citation_coverage = ..., faithfulness = ... WHERE job_id = ...`.
- `agent.py`: `run_retrieval_faithfulness.si(str(agent_id), job_id)` appended as the **4th/last step** of the existing post-turn `celery_chain(gatekeeper, auditor, strategist, ...)`.

### Task 2 — OPS-08: `check_index_staleness` (pipeline queue, scheduled)

`apps/api/app/worker/tasks/pipeline/staleness.py`:
- `compute_index_staleness_summary(conn_str)` — plain function (not a Celery task), reused by both the task and the route. Two independent scans:
  - **Stale documents:** a document is stale if it has a chunk with no embedding row, or `documents.created_at` is newer than the latest embedding's `created_at` among its chunks.
  - **Embedding-model drift:** any `embeddings.model` value differing from `bedrock_embedding_service.active_embedding_model()`.
  - Each scan degrades independently to `"not_tracked"` on its own query failure (e.g. a genuinely missing column on an older schema) — never fabricates.
- `check_index_staleness(self, agent_id)` — `acks_late=True`, `max_retries=2`, `queue="pipeline"` (Pitfall 6 — avoids contending with live agent-turn traffic under `worker_pool="solo"`). Raises an `index_staleness` alert via `alert_service._write_alert`/`_active_alert_exists` (deduped, no new table) when stale docs or drift are detected.
- `check_index_staleness_beat(self)` — fans out to `check_index_staleness` per `Agent.is_deployed` agent, mirroring `alert.py`'s `run_alert_check_beat` pattern exactly.
- `celery_app.py`: registered in `include=[...]` and `beat_schedule["index-staleness-daily"]` (05:00 UTC).

### Task 3 — GET `/agents/{id}/retrieval-health`

`apps/api/app/api/v1/metrics.py`:
- New route reusing the `evals.py`/`metrics.py` IDOR pattern (404, not 403, on cross-tenant or unknown agent).
- Combines `retrieval_metrics_service.read_retrieval_health` (21-03 stored-row aggregates) with a **live-computed** `compute_index_staleness_summary` call (no cached staleness table by design) into one response, both dispatched via `asyncio.to_thread`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — test regression] `test_agent_task.py::test_validators_dispatched` updated for the 4-task chain**
- **Found during:** Task 1, after wiring the new dispatch into `agent.py`'s `celery_chain(...)`.
- **Issue:** The existing test asserted exactly 3 chained tasks (gatekeeper/auditor/strategist); appending `run_retrieval_faithfulness` is a direct, intended consequence of this task.
- **Fix:** Updated the assertion to expect 4 tasks with an explanatory comment.
- **Files modified:** `apps/api/tests/unit/test_agent_task.py`
- **Commit:** `78e65de`

**2. [Rule 3 — blocking issue] Control-DB migration 0017 widening `alerts.alert_type` CHECK constraint**
- **Found during:** Task 2, implementing the plan's explicit instruction to "raise an alert via the existing alert_service."
- **Issue:** `alerts.alert_type` has a live `CHECK (alert_type IN ('eval_regression', 'red_team_critical'))` constraint (`0012_alerts_digest_runs.py`) — the same landmine class 21-RESEARCH.md's Pitfall 2 documents for `eval_scenarios.source`. Writing `alert_type='index_staleness'` without widening it first would raise `psycopg2.errors.CheckViolation` at INSERT time, silently swallowed by any broad `except Exception` around the insert.
- **Fix:** Added `apps/api/alembic/versions/0017_alerts_index_staleness_type.py`, using the same `DROP CONSTRAINT`/`ADD CONSTRAINT` convention Pitfall 2 establishes. Not in this plan's `files_modified` list.
- **Files modified:** `apps/api/alembic/versions/0017_alerts_index_staleness_type.py`
- **Commit:** `bc8784e`

### Design clarifications (not deviations from behavior, but from a literal reading of the plan text)

- The plan's `<behavior>` block for Task 1 reads as if the sample-rate-OR-auditor-flag gate is evaluated at `agent.py`'s dispatch point. That is not possible: Auditor is a *preceding* step in the same `celery_chain`, so its verdict does not exist yet when the chain is assembled. The gate is evaluated **inside** `run_retrieval_faithfulness` once it runs (guaranteed to be after Auditor commits its verdict, since chain steps execute sequentially) — matching the plan's `key_links` description ("sampled `.si()` dispatch in the post-turn celery_chain gated by sample rate OR auditor flag") more literally than the `<behavior>` prose. See `retrieval_eval.py`'s module docstring.
- `citation_coverage` is a proxy ratio (cited spans / retrieve calls with a result), not exact per-chunk attribution — the schema does not persist which retrieved chunk was cited. Documented in code and in `key-decisions` above.
- The Ragas `question` input is sourced from the tenant-DB `messages` table by conversation-recency (not `job_id` — no such column exists on `messages`), since `T-04-03-05` forbids ever persisting the question text to `job_events`.

## Live-Gated / Deferred Items

- **Real Ragas Faithfulness scoring against Anthropic.** `_compute_ragas_faithfulness` is fully implemented but never exercised against real `ragas`/`anthropic` in this environment — `import ragas` currently fails (`ragas.llms.base -> langchain_community.chat_models.vertexai` `ModuleNotFoundError`), confirmed pre-existing (identical to `eval_service.py`'s own top-level import, and to `test_metrics_routes.py`'s documented PRE-EXISTING INFRA NOTE). Unit tests stub `_compute_ragas_faithfulness` entirely. Real numbers are a live-verification-gate item per RESEARCH.md's Validation Architecture section — same precedent as Phases 13–16.
- **Migration 0017 has not been run against a live Neon control DB** in this session (no local Postgres connectivity available) — schema-correctness was verified by direct read against the exact constraint name from `0012_alerts_digest_runs.py`, not by executing `alembic upgrade head`.
- **`check_index_staleness_beat`'s 05:00 UTC cadence** was not verified against a running Celery beat process — verified by code inspection against the existing `alert-daily`/`eval-nightly` conventions only.

## Known Stubs

None — every field in the `GET /retrieval-health` response is computed from a stored row or a live scan; no hardcoded/mock values are returned.

## Threat Flags

None beyond what the plan's own `<threat_model>` already covers (T-21-04-01..04, all `mitigate`, all addressed as designed: sampled dispatch, pipeline-queue routing, agent_id/job_id-only task args, IDOR 404 pattern).

## Verification

```
cd apps/api && pytest tests/unit/test_retrieval_faithfulness_task.py tests/unit/test_index_staleness.py tests/unit/test_retrieval_health_route.py -q
# 28 passed
```

- `python -c "import inspect,app.worker.tasks.runtime.retrieval_eval as r; assert 'conn_str' not in inspect.signature(r.run_retrieval_faithfulness.run).parameters"` → exits 0
- `python -c "import inspect,app.worker.tasks.pipeline.staleness as s; assert 'conn_str' not in inspect.signature(s.check_index_staleness.run).parameters"` → exits 0
- `grep -n "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE" app/core/config.py` → present
- `grep -n 'queue="pipeline"' app/worker/tasks/pipeline/staleness.py` → present
- `grep -n "staleness" app/worker/celery_app.py` → present in both `include` and `beat_schedule`
- `grep -n "retrieval-health" app/api/v1/metrics.py` → present

**Full-suite regression check:** ran `pytest tests/unit -q` before and after this plan's changes (via `git stash`/`git stash pop`, excluding the 15 modules that fail to collect due to the pre-existing `ragas`/`vertexai` `ModuleNotFoundError`). The failure set is identical before and after this plan except: (a) `test_agent_task.py::test_validators_dispatched`, intentionally updated (see Deviations), and (b) this plan's own 3 new test files, which fail against the stashed (reverted) source and pass against the actual implementation. No new regressions introduced.

## Self-Check: PASSED

- FOUND: apps/api/app/worker/tasks/runtime/retrieval_eval.py
- FOUND: apps/api/app/worker/tasks/pipeline/staleness.py
- FOUND: apps/api/alembic/versions/0017_alerts_index_staleness_type.py
- FOUND: apps/api/tests/unit/test_retrieval_faithfulness_task.py
- FOUND: apps/api/tests/unit/test_index_staleness.py
- FOUND: apps/api/tests/unit/test_retrieval_health_route.py
- FOUND: commit 78e65de (feat(21-04): OPS-07 sampled Ragas faithfulness + citation-coverage task)
- FOUND: commit bc8784e (feat(21-04): OPS-08 check_index_staleness pipeline-queue scan + beat)
- FOUND: commit 298e1ea (feat(21-04): GET /agents/{id}/retrieval-health endpoint)
