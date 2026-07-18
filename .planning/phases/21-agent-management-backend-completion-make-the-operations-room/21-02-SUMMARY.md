---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 02
subsystem: api
tags: [fastapi, psycopg2, postgres, redis, jwt, observability]

# Dependency graph
requires:
  - phase: 21-agent-management-backend-completion-make-the-operations-room
    plan: 01
    provides: "tenant migration 0009 (turn_metrics + message_feedback tables) and the turn_metrics write path in run_agent_turn that this plan reads and writes into"
provides:
  - "metrics_service.compute_agent_metrics — containment/deflection/escalation/CSAT/thumbs/p95/cost KPI aggregation from stored rows, with NOT_TRACKED sentinels on zero-row windows"
  - "GET /api/v1/agents/{id}/metrics — IDOR-guarded route exposing those KPIs, structured for the 21-04 GET /retrieval-health addition"
  - "POST /widget/agents/{id}/feedback — JWT-authed, rate-limited thumbs/CSAT capture into message_feedback"
affects: [21-04-retrieval-health-router-extension]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "metrics_service exposes a sync core (compute_agent_metrics) the route wraps in asyncio.to_thread — same D-30 pattern as evals.py/_query_tenant_db_sync"
    - "Honest-empty-state sentinel (NOT_TRACKED = \"not_tracked\") returned per-metric, not all-or-nothing, whenever that metric's own underlying row count is zero — sample_size is the one field that is a literal count and is never sentinel'd"
    - "Widget feedback route uses its own Redis rate-limit bucket key (rate:feedback:{agent_id}:{bucket}) distinct from /chat's (rate:{agent_id}:{bucket}) so feedback and chat traffic cannot starve each other on the same 60/min ceiling"

key-files:
  created:
    - apps/api/app/services/metrics_service.py
    - apps/api/app/api/v1/metrics.py
    - apps/api/tests/unit/test_metrics_routes.py
    - apps/api/tests/unit/test_widget_feedback.py
  modified:
    - apps/api/app/api/v1/widget.py
    - apps/api/app/schemas/widget.py
    - apps/api/app/main.py

key-decisions:
  - "deflection is computed identically to containment (1 - escalation_rate), not as an independently-tracked number, because this schema (migration 0009 + 0003 conversations) has no signal for human handoff distinct from the escalate_to_human tool-use flag already captured as turn_metrics.escalated (confirmed via app/services/escalation.py — escalation is only ever raised from that ToolUseBlock). Fabricating a second, differently-computed deflection number without a real distinguishing signal would itself violate the honest-empty-state discipline the metric is supposed to uphold."
  - "sample_size (total turn_metrics rows in the window) is never sentinel'd, unlike every ratio/average/percentile metric — a literal row count of 0 is an honest fact (\"zero turns happened\"), not a fabricated statistic, so NOT_TRACKED does not apply to it."
  - "Widget feedback rate limit uses a dedicated bucket key (rate:feedback:...) rather than reusing /chat's (rate:{agent_id}:{bucket}) key, so a burst of feedback submissions cannot exhaust the budget a customer needs for actual chat turns, and vice versa."
  - "Feedback route returns 204 No Content (not 200 with a body) — mirrors the existing POST /widget/{id}/identity/request precedent in the same file, and message_feedback INSERT has no client-relevant response payload to return."

patterns-established:
  - "Any future tenant-DB aggregation route (e.g. 21-04's retrieval-health) should follow metrics_service's shape: a pure dict-building helper (_build_metrics_dict) separated from the psycopg2 I/O (compute_agent_metrics), so the computation logic is unit-testable without mocking a DB cursor."

requirements-completed: [OPS-02, OPS-03]

# Metrics
duration: ~50min of active implementation
completed: 2026-07-16
status: complete
---

# Phase 21 Plan 02: Live Region Metrics + Widget Feedback Summary

**metrics_service.compute_agent_metrics aggregates turn_metrics + message_feedback into containment/deflection/escalation/CSAT/thumbs/p95-latency/cost-per-session with honest NOT_TRACKED sentinels on empty windows, exposed via IDOR-guarded GET /agents/{id}/metrics; a new JWT-authed, rate-limited POST /widget/agents/{id}/feedback route captures thumbs +/- and optional CSAT into message_feedback — the second half of the "Live" region's real numbers.**

## Performance

- **Started:** 2026-07-15 (session continuation from 21-01)
- **Completed:** 2026-07-16T00:25:24+02:00
- **Tasks:** 3/3 completed
- **Files modified:** 7 (4 created, 3 modified)

## Accomplishments

- `metrics_service.compute_agent_metrics(conn_str, window_days)` runs two aggregate queries (turn_metrics with a per-conversation escalation CTE; message_feedback) and returns containment, deflection, escalation_rate, csat_avg, thumbs_down_rate, p95_latency_ms (`percentile_cont(0.95) WITHIN GROUP`), cost_per_session, sample_size, window_days.
- Every ratio/average/percentile field is gated independently on its own underlying row count — a window with turns but no feedback returns real containment/escalation/p95/cost numbers alongside `NOT_TRACKED` for csat_avg/thumbs_down_rate, never a blanket fabricated 0.0 across the board.
- `GET /agents/{agent_id}/metrics` (optional `window_days` query param, default 7, bounded 1-90) copies the `evals.py` IDOR + conn_str + `asyncio.to_thread` pattern verbatim and is registered in `main.py`.
- `POST /widget/agents/{agent_id}/feedback` requires the same Bearer widget JWT as `/widget/{id}/chat` (never unauthenticated like `/config`), applies its own 60/min-per-agent_id Redis INCR rate limit bucket, validates `rating: Literal['up','down']` and `csat_score: int | None` bounded 1-5 via Pydantic (422 on violation, DB CHECK constraints as defense-in-depth), and inserts one `message_feedback` row.

## Task Commits

Each task was committed atomically:

1. **Task 1: metrics_service — aggregation over turn_metrics/message_feedback/conversations** - `3dbe181` (feat)
2. **Task 2: GET /agents/{id}/metrics route (IDOR-guarded) + main.py registration** - `82141f4` (feat)
3. **Task 3: OPS-02 — widget POST /widget/agents/{id}/feedback (JWT + rate-limited)** - `64e6e19` (feat)

_TDD note: Task 1 was marked `tdd="true"`. `_build_metrics_dict` (the pure computation core) was written test-first against known aggregate tuples — including the explicit zero-row-window assertion required by the acceptance criteria — before `compute_agent_metrics`'s psycopg2 wiring was added; all tests were green before the commit, consistent with 21-01's "test-covered, not literal RED→GREEN commit pair" interpretation given the plan's tight single-file task shape._

## Files Created/Modified

- `apps/api/app/services/metrics_service.py` - `compute_agent_metrics` + `_build_metrics_dict`; two SQL queries (turn_metrics aggregate w/ per-conversation escalation CTE, message_feedback aggregate); `NOT_TRACKED` sentinel
- `apps/api/app/api/v1/metrics.py` - `GET /agents/{agent_id}/metrics` route (IDOR + conn_str + `asyncio.to_thread`)
- `apps/api/app/main.py` - `metrics` router import + `include_router(metrics.router, prefix="/api/v1")`
- `apps/api/app/api/v1/widget.py` - `_insert_message_feedback_sync` helper; `POST /widget/agents/{agent_id}/feedback` route; `OPTIONS /widget/agents/{agent_id}/feedback` preflight handler; module docstring route list updated
- `apps/api/app/schemas/widget.py` - `WidgetFeedbackRequest` schema (`message_id`, `conversation_id`, `rating: Literal['up','down']`, `csat_score: int | None` bounded 1-5)
- `apps/api/tests/unit/test_metrics_routes.py` - service tests (`_build_metrics_dict` known-aggregate + zero-row + partial-data cases, `compute_agent_metrics` mocked-cursor wiring) + route tests (IDOR 404, agent-not-found 404, missing-conn-str 404, happy-path shape, `window_days` forwarding)
- `apps/api/tests/unit/test_widget_feedback.py` - happy path (with/without csat_score) + auth failures (missing header, invalid JWT, agent_id mismatch) + input validation (422 rating enum, 422 csat bounds) + rate limit 429 + agent-lookup 404s + source assertion for `INSERT INTO message_feedback`

## Decisions Made

- **Deflection mirrors containment, not a fabricated independent number.** See `key-decisions` above — this is a data-model honesty call, not an oversight: the schema genuinely has no distinct human-handoff signal, and DOMAIN-NOTES §6's honest-empty-state discipline applies equally to "don't compute two numbers as if they measure different things when they don't" as it does to "don't fabricate a row."
- **`sample_size` is never sentinel'd.** It is a literal count, not a derived ratio — reporting `0` when zero turns occurred is itself the honest answer.
- **Widget feedback rate limit uses its own Redis bucket key**, isolated from `/chat`'s, so the two endpoints' traffic budgets never interfere with each other.
- **204 No Content for the feedback route**, matching the existing `POST /widget/{id}/identity/request` precedent already in the same file rather than inventing a new 200-with-ack shape.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 auto-fixes were needed. The one design judgment call (deflection = containment, given the schema's actual signal set) is documented above as a decision, not a deviation, since the plan's own `<action>` text for deflection ("share of conversations with no escalation and no human handoff") is honored literally — there is simply no data source in this schema that distinguishes "human handoff" from "escalation" as separate events, so the two computations collapse to the same value rather than one silently being invented.

## Issues Encountered

- **Pre-existing, unrelated test-collection failure confirmed present (not fixed — out of scope per Rule scope boundary and explicit plan instruction).** `pytest tests/unit/test_widget_routes.py` (which does `from app.main import app`) fails to collect with `ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'`, traced through `app.main` → `app.api.v1.evals` → `app.worker.tasks.runtime.eval` → `app.services.eval_service` → `ragas.metrics.collections` → `ragas.llms.base`. Reproduced independently before writing any code in this plan to confirm it is pre-existing (matches 21-01-SUMMARY.md's and 21-05-SUMMARY.md's own findings). Per the plan's explicit `key_context`, this was NOT fixed. Both new test files (`test_metrics_routes.py`, `test_widget_feedback.py`) instead build a minimal `FastAPI()` around only the relevant router module (`app.api.v1.metrics.router` / `app.api.v1.widget.router`), following the exact pattern already established in `test_bench_routes.py` (21-05), so they run cleanly without touching the broken import chain. Confirmed `app.api.v1.widget` and `app.api.v1.metrics` import standalone without error (both verified interactively before writing tests).

## User Setup Required

None — no external service configuration required. Both new routes operate purely against the tenant DB (already migrated in 21-01) and Redis (already required by every existing widget route).

## Next Phase Readiness

- `GET /agents/{id}/metrics` and `POST /widget/agents/{id}/feedback` are live; the admin "Live" region and widget thumbs UI (Phase 20 frontend) can now be wired to real endpoints instead of the EmptyState placeholder.
- `app/api/v1/metrics.py` is deliberately left as a single small router file (one route) so 21-04's `GET /agents/{id}/retrieval-health` addition is a same-file extension, not a new router registration.
- The pre-existing `langchain_community.chat_models.vertexai` collection failure remains unresolved and will continue to block any test module that imports `app.main` until a plan that owns `eval_service.py`/`ragas` corrects the `langchain-community` pin — flagged again here per 21-01/21-05's own next-phase-readiness notes.

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-16*

## Self-Check: PASSED

All created/modified files confirmed present on disk; all three task commits (`3dbe181`, `82141f4`, `64e6e19`) confirmed in `git log`.
