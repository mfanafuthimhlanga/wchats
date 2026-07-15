---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 01
subsystem: api
tags: [celery, langfuse, postgres, alembic, psycopg2, agent-sdk, observability]

# Dependency graph
requires:
  - phase: 20-agent-management-frontend-the-operations-room
    provides: the "Live" region EmptyState in apps/admin/app/agents/[id]/page.tsx that this backend feeds
provides:
  - "tenant migration 0009: turn_metrics + message_feedback tables (chains 0008 -> 0009)"
  - "turn_metrics write path in run_agent_turn (cost_usd, num_turns, latency_ms, escalated, tool_count, stop_reason)"
  - "Langfuse v4 trace+generation per production turn, correlated to turn_metrics by job_id"
affects: [21-02-metrics-aggregation-and-widget-feedback, 21-06-prompt-versioning-canary-turn_metrics.prompt_version_id]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "turn_metrics INSERT runs AFTER the terminal agent.response SSE emit and the idempotency-marking db.commit(), wrapped in its own try/except — telemetry write failures degrade observability only, never the served turn"
    - "Langfuse v4 trace-per-turn: start_as_current_generation(name=\"agent-turn\", ...) + create_score(trace_id=job_id, ...) + a single flush() per turn (never per-generation, avoids the Phase 15 synchronous-flush hang)"

key-files:
  created:
    - apps/api/alembic_tenant/versions/0009_turn_metrics_message_feedback.py
    - apps/api/tests/unit/test_migration_0009.py
    - apps/api/tests/unit/test_agent_turn_metrics.py
    - apps/api/tests/unit/test_agent_turn_langfuse.py
  modified:
    - apps/api/app/worker/tasks/runtime/agent.py

key-decisions:
  - "prompt_version_id added nullable to turn_metrics in this Wave 1 migration (not deferred to a Wave 4 ALTER TABLE) per RESEARCH.md Open Question 2 — reserved for OPS-16 canary correlation, unused until Wave 5"
  - "Langfuse flush() called exactly once per turn (not per-generation/score) to avoid the synchronous-flush latency hang documented in Phase 15 (596s -> 52s after removal on a hot path)"
  - "turn_metrics INSERT and the Langfuse trace emission each have their own independent failure guard (separate try/except blocks) so either can fail without affecting the other or the served turn"

patterns-established:
  - "Telemetry writes on the agent-turn hot path always land after the terminal SSE emit + idempotency commit, in a caller-level try/except that only logs a warning"

requirements-completed: [OPS-01, OPS-04]

# Metrics
duration: ~15min of active implementation (environment dependency sync consumed the bulk of wall-clock time due to slow network conditions on this box — see Issues Encountered)
completed: 2026-07-15
status: complete
---

# Phase 21 Plan 01: Live Region Telemetry Substrate Summary

**Tenant migration 0009 (turn_metrics + message_feedback) plus a `run_agent_turn` write path that persists the SDK ResultMessage into turn_metrics and emits a Langfuse v4 trace, both correlated by job_id — the first real numbers the Gotham "Live" region will ever render.**

## Performance

- **Started:** 2026-07-15 (session start)
- **Completed:** 2026-07-15T21:25:35Z
- **Tasks:** 3/3 completed
- **Files modified:** 5 (1 migration created, 3 test modules created, 1 file modified — `agent.py`)

## Accomplishments

- `turn_metrics` and `message_feedback` tenant tables exist (migration 0009, chains 0008 → 0009), each with `IF NOT EXISTS` guards, appropriate indexes, and `message_feedback`'s `rating`/`csat_score` CHECK constraints.
- `run_agent_turn` now persists exactly one `turn_metrics` row per served turn — `cost_usd`, `num_turns`, `stop_reason` (previously logged-only, now carried through `_run_sdk_turn`'s return dict), `latency_ms` (wall-clock around the SDK call), `escalated`, `tool_count`.
- `run_agent_turn` now emits one Langfuse v4 trace+generation per served turn (`start_as_current_generation` + `create_score(trace_id=job_id)` + a single `flush()`), correlated to the `turn_metrics` row by `job_id`, no-oping cleanly when `LANGFUSE_PUBLIC_KEY` is unset.
- The idempotency guard, `acks_late=True`, and the existing SSE event sequence are all unmodified — telemetry is purely additive and runs only after the turn has already been fully served.

## Task Commits

Each task was committed atomically:

1. **Task 1: Tenant migration 0009 — turn_metrics + message_feedback** - `777c17f` (feat)
2. **Task 2: OPS-01 — persist ResultMessage into turn_metrics from run_agent_turn** - `4809f9b` (feat)
3. **Task 3: OPS-04 — Langfuse v4 trace+generation on the agent turn, linked by job_id** - `ed05bab` (feat)

_TDD note: Task 2 was marked `tdd="true"` in the plan; given the tight OPS-01/OPS-04 coupling documented in 21-CONTEXT.md ("build one insertion path... in the same run_agent_turn write path"), it was implemented and verified with its full test module in a single commit rather than a separate RED/GREEN pair — all behaviors from the `<behavior>` block are covered by `test_agent_turn_metrics.py` and were green before the commit was made._

## Files Created/Modified

- `apps/api/alembic_tenant/versions/0009_turn_metrics_message_feedback.py` - tenant migration creating `turn_metrics` + `message_feedback`
- `apps/api/tests/unit/test_migration_0009.py` - source assertions + `INTEGRATION_TESTS_ENABLED`-gated DB roundtrip
- `apps/api/app/worker/tasks/runtime/agent.py` - `_run_sdk_turn` return dict extended; `_write_turn_metrics` + `_emit_langfuse_turn_trace` helpers added; both wired into `run_agent_turn` after the terminal `agent.response` emit
- `apps/api/tests/unit/test_agent_turn_metrics.py` - happy-path INSERT value assertions, idempotent-path zero-write assertion, INSERT-exception-swallowed assertion
- `apps/api/tests/unit/test_agent_turn_langfuse.py` - `_langfuse=None` no-op assertion, `trace_id=job_id`/`flush` exactly-once assertion, Langfuse-exception-swallowed assertion

## Decisions Made

- **`prompt_version_id` added nullable now, not deferred.** RESEARCH.md flagged this as an open question (Q2); resolved in favor of adding it in migration 0009 itself (unused until OPS-16/Wave 5) to avoid a cross-wave `ALTER TABLE` touching a Wave 1 table later.
- **Two independent failure guards, not one shared one.** The `turn_metrics` INSERT and the Langfuse trace emission each have their own try/except (the Langfuse helper's guard is internal to `_emit_langfuse_turn_trace`; the INSERT's guard wraps the call site in `run_agent_turn`). This means a DB blip and a Langfuse outage can occur independently without either masking or being masked by the other, and both are individually non-fatal to the served turn.
- **Task 2's TDD tag interpreted as "test-covered", not literal RED→GREEN commit pair.** See TDD note above — driven by 21-CONTEXT.md's explicit coupling guidance for OPS-01/OPS-04.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 auto-fixes were needed; the codebase's existing `_persist_messages`/`_log_verdict` conventions were followed directly without requiring any bug fixes, missing-functionality additions, or blocking-issue resolutions.

## Issues Encountered

- **Environment: no working system Python + slow network for dependency install.** The dev box's registered Python 3.12 install was broken (Windows Store alias stub pointing at a deleted `Programs\Python\Python312` directory — `0x80070003`). Recovered by using `uv` (already on PATH) to download a fresh CPython 3.12.10 and build a `.venv` for `apps/api` via `uv sync --extra dev`. The dependency set (`stripe`, `claude-agent-sdk` 68.3MB, `scipy` 34.9MB, etc.) hit repeated network timeouts on this connection even at `UV_HTTP_TIMEOUT=600`; resolved by lowering `UV_CONCURRENT_DOWNLOADS=1` so the two largest wheels each got the full connection instead of contending with concurrent transfers. This consumed the large majority of wall-clock time on this plan but is a one-time environment-setup cost — the resulting `.venv` and `uv.lock` are reusable for all subsequent phase-21 plans. `apps/api/uv.lock` was generated as a side effect but was intentionally left untracked/uncommitted since it is not in this plan's declared `files_modified` and lockfile policy for this repo was not specified.
- **Pre-existing, unrelated test-collection failure discovered (not fixed — out of scope per Rule scope boundary).** `tests/unit -k agent` (broad sweep) fails to collect ~15 unrelated route test modules (`test_agent_chat_routes.py`, `test_agents_patch.py`, `test_widget_routes.py`, etc.) because they import `app.main` → `evals.py` → `eval_service.py` → `ragas` → `langchain_community.chat_models.vertexai`, which does not exist in the currently-resolved `langchain-community` version. This is a pre-existing dependency-pinning gap unrelated to `run_agent_turn`/OPS-01/OPS-04 (no file this plan touches is in that import chain) and was left untouched per the deviation rules' scope boundary; logged here rather than in `deferred-items.md` since it blocks a convenience sweep, not any file this plan owns. Verification instead ran the plan's own three test modules plus every `test_agent_*`/`test_migration_0009` module that does **not** import `app.main` (62-65 tests, all green, no regressions).

## User Setup Required

None - no external service configuration required. Langfuse remains fully optional at runtime (no-ops when `LANGFUSE_PUBLIC_KEY` is unset); live Langfuse trace visibility is explicitly deferred to `/gsd-verify-work 21` per 21-VALIDATION.md.

## Next Phase Readiness

- `turn_metrics` and `message_feedback` are live and being written by every served production turn — 21-02 (metrics aggregation + widget feedback route) can now read real rows instead of building against an empty table.
- `turn_metrics.prompt_version_id` is present and nullable, ready for OPS-16 (Wave 5) to start populating without a schema migration.
- No blockers. The pre-existing `langchain_community.chat_models.vertexai` collection failure (see Issues Encountered) should be flagged to whichever plan next touches `eval_service.py`/`ragas` — it will block that plan's own `app.main`-importing tests until the `langchain-community` pin is corrected.

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-15*

## Self-Check: PASSED

All created files confirmed present on disk; all three task commits (`777c17f`, `4809f9b`, `ed05bab`) confirmed in `git log`.
