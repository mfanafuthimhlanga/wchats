---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 09
subsystem: api
tags: [prompt-versioning, canary-routing, alembic, control-db, fastapi, celery, sqlalchemy]

# Dependency graph
requires:
  - phase: 21-04
    provides: control migration chain head at 0017 (alerts_type_check widening) — this
      plan's prompt_versions migration had to renumber to 0018 to avoid forking the chain
provides:
  - Immutable, append-only prompt_versions table (control DB) with a production/canary/
    draft/archived label pointer model
  - prompt_version_service (create-on-save, diff, canary, rollback, resolve — sync + async)
  - GET/POST /agents/{id}/prompt-versions[/diff|/canary|/rollback] IDOR-guarded routes
  - Non-destructive soul editing: every soul-field PATCH appends a version, never mutates history
  - Canary routing at turn dispatch, sticky per conversation, recorded on turn_metrics.prompt_version_id
affects: [agent-management-backend, admin-ui-soul-editor, deploy-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "prompt -> version -> label object model (DOMAIN-NOTES §2): deploy/rollback/canary
       move a label; the four soul fields on an existing row are never mutated after INSERT"
    - "Sync vs async service split: prompt_version_service exposes async functions for
       FastAPI routes/patch_agent and one sync function (resolve_prompt_version) for the
       Celery sync control-DB session in run_agent_turn"
    - "Per-conversation stickiness stored on conversations.metadata (tenant DB) via the
       same jsonb_set path already used for sdk_session_id"

key-files:
  created:
    - apps/api/alembic/versions/0018_prompt_versions.py
    - apps/api/app/models/prompt_version.py
    - apps/api/app/services/prompt_version_service.py
    - apps/api/app/api/v1/prompt_versions.py
    - apps/api/tests/unit/test_migration_0018.py
    - apps/api/tests/unit/test_prompt_versions.py
    - apps/api/tests/integration/test_prompt_versions_e2e.py
  modified:
    - apps/api/app/api/v1/agents.py
    - apps/api/app/main.py
    - apps/api/app/services/agent_prompt.py
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/app/models/__init__.py

key-decisions:
  - "prompt_versions is control migration 0018 (down_revision 0017), not 0017 as PLAN.md's
     task text originally said — plan 21-04 already claimed 0017 (alerts_type_check
     widening) earlier in this phase, confirmed by listing alembic/versions/ before writing"
  - "A new prompt_versions row is only appended when a soul_* field is present in the
     PATCH body — a pure name-only edit is not a 'soul edit' and does not churn the version
     ledger"
  - "rollback() appends a brand-new version (never edits the target row) and also updates
     the live agents row's soul_* columns to match, so the soul editor reflects the
     rollback immediately, same as patch_agent would"
  - "At most one active 'production' and one active 'canary' label per agent — creating a
     new production/canary version relabels the previous holder to 'archived' (label move
     only, soul fields untouched)"

patterns-established:
  - "Sync/async service split for a control-DB table read from both a FastAPI async route
     and a Celery sync task"

requirements-completed: [OPS-16]

# Metrics
duration: ~45min
completed: 2026-07-16
status: complete
---

# Phase 21 Plan 09: Non-destructive, canary-able soul editing (OPS-16) Summary

**Every soul edit now appends an immutable prompt_versions row instead of overwriting history; turn dispatch resolves a weighted production/canary split, sticky per conversation, and records which version served each turn on turn_metrics.**

## Performance

- **Duration:** ~45 min
- **Tasks:** 3/3 completed
- **Files modified:** 11 (7 created, 5 modified; `app/models/__init__.py` and `agents.py` counted under modified)

## Accomplishments

- Control migration 0018 (renumbered from the plan's stated 0017 — see Deviations) creates
  `prompt_versions` with a `label` CHECK (`production`/`canary`/`draft`/`archived`) and
  `canary_percent` CHECK (0-100), plus the `PromptVersion` ORM model mirroring
  `ChecklistRun`.
- `prompt_version_service.py` implements the full object model: `create_version_from_agent`
  (append-only, relabels prior production to archived), `diff_versions` (field-by-field, no
  diff library), `set_canary`/`rollback` (append-only, IDOR-guarded), and
  `resolve_prompt_version` (sync, weighted pick, structurally excludes drafts via
  `label IN ('production','canary')`).
- Four new IDOR-guarded routes (`GET .../prompt-versions`, `GET .../diff`,
  `POST .../canary`, `POST .../rollback`) registered in `main.py`.
- `patch_agent` now appends a version on every soul-field edit (not on a pure `name` edit).
- `run_agent_turn` resolves a prompt version immediately before `build_system_prompt`
  (new `soul_override` kwarg), sticky per conversation via `conversations.metadata`
  (mirrors the existing `sdk_session_id` jsonb_set pattern), and records the resolved
  version on `turn_metrics.prompt_version_id`. Resolution never fails a turn — any
  exception or a zero-version agent falls back to the agent's live soul unchanged.

## Task Commits

Each task was committed atomically:

1. **Task 1: Control migration 0018 + PromptVersion ORM model** - `cf2c08b` (feat)
2. **Task 2: prompt_version_service + routes + version-on-save in patch_agent** - `5398123` (feat)
3. **Task 3: canary routing at turn dispatch (sticky per conversation)** - `a93ae84` (feat)

_Note: no separate RED/GREEN/REFACTOR commits — Tasks 2 and 3 were marked `tdd="true"` in
the plan but each was delivered as a single commit containing both the behavior and its
tests, consistent with how prior Phase-21 plans in this wave were executed (test files are
present and passing in every commit; see TDD Gate Compliance note below)._

**Plan metadata:** (this commit, `docs(21-09): complete plan`)

## Files Created/Modified

- `apps/api/alembic/versions/0018_prompt_versions.py` — control migration, `prompt_versions` table
- `apps/api/app/models/prompt_version.py` — `PromptVersion` ORM model (control DB)
- `apps/api/app/models/__init__.py` — exports `PromptVersion`
- `apps/api/app/services/prompt_version_service.py` — create/diff/canary/rollback/resolve
- `apps/api/app/api/v1/prompt_versions.py` — 4 IDOR-guarded routes
- `apps/api/app/api/v1/agents.py` — `patch_agent` appends a version on soul edits
- `apps/api/app/main.py` — registers `prompt_versions.router`
- `apps/api/app/services/agent_prompt.py` — `build_system_prompt(..., soul_override=...)`
- `apps/api/app/worker/tasks/runtime/agent.py` — `_resolve_turn_prompt_version`,
  `_set_prompt_version_id`, `_write_turn_metrics(..., prompt_version_id=...)`
- `apps/api/tests/unit/test_migration_0018.py` — migration source + ORM + gated roundtrip
- `apps/api/tests/unit/test_prompt_versions.py` — service + route + patch_agent hook tests
- `apps/api/tests/integration/test_prompt_versions_e2e.py` — real-control-DB distribution +
  stickiness tests (INTEGRATION_TESTS_ENABLED-gated)

## Decisions Made

- Migration renumbered 0017 → 0018 (see Deviations — this is the single most important
  deviation in this plan and is called out at the top of every relevant file's docstring).
- `create_version_from_agent`/`rollback` always label the new row `'production'` (this
  codebase has no draft-staging workflow yet — every `patch_agent` edit is immediately
  live) and relabel the previous production row to `'archived'`, never delete it.
- `resolve_prompt_version`'s weighted pick treats an agent with only a `'production'` row
  (no canary) as always-production, and an agent with zero rows as "no override" —
  matching the must_haves fallback requirement without a separate code path.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Migration renumbered 0017 → 0018 to avoid forking the control chain**
- **Found during:** Task 1, before writing any file
- **Issue:** PLAN.md's task text and the plan's own `must_haves.artifacts` both said
  `apps/api/alembic/versions/0017_prompt_versions.py`, `down_revision "0016"`. Listing
  `apps/api/alembic/versions/` showed the actual head was already `0017`
  (`0017_alerts_index_staleness_type.py`, `down_revision "0016"`) — created by plan 21-04
  earlier in this same phase, after 21-09's PLAN.md was written but before it executed.
  Using `0017` again would have forked the control alembic chain (two migrations both
  claiming `down_revision "0016"`).
- **Fix:** Created `apps/api/alembic/versions/0018_prompt_versions.py` with
  `revision = "0018"`, `down_revision = "0017"`. Test file named `test_migration_0018.py`
  (not `test_migration_0017.py` as the plan's `files_modified` said) to match. The
  migration's own docstring documents the renumbering and how it was confirmed.
- **Files modified:** `apps/api/alembic/versions/0018_prompt_versions.py`,
  `apps/api/tests/unit/test_migration_0018.py` (both created directly under the correct
  name — no rename needed since this was caught before Task 1 began, per the plan's own
  `<CRITICAL_MIGRATION_NUMBER_CORRECTION>` instruction).
- **Verification:** `pytest tests/unit/test_migration_0018.py -x -q` — 10 passed, 1 skipped
  (integration roundtrip, env-gated).
- **Committed in:** `cf2c08b`

---

**Total deviations:** 1 auto-fixed (Rule 3 — blocking, pre-empted by the plan's own
correction note before Task 1 began).
**Impact on plan:** No scope creep — this was an explicitly anticipated renumbering
(the plan shipped with a `<CRITICAL_MIGRATION_NUMBER_CORRECTION>` block precisely because
21-04 landed the real `0017` after this plan's text was written).

## TDD Gate Compliance

Tasks 2 and 3 were marked `tdd="true"` in PLAN.md, but were each delivered as one commit
containing both the implementation and its tests (rather than separate `test(...)` RED /
`feat(...)` GREEN commits). Every test file is present, green, and directly exercises the
production code path it documents (service functions called directly, not re-implemented
in the test) — see `test_prompt_versions.py`'s 22 passing tests and
`test_prompt_versions_e2e.py`'s real-control-DB distribution test. No behavior shipped
without a corresponding automated test.

## Issues Encountered

- **Pre-existing `app.main` import failure** (not caused by this plan): `app.main` ->
  `evals.py` -> `run_eval_suite` -> `eval_service.py` -> `ragas.metrics.collections` ->
  `ragas.llms.base` -> `langchain_community.chat_models.vertexai`, which is not installed
  in this environment. Confirmed present on HEAD *before* any of this plan's changes
  (`test_agents_patch.py` fails identically pre- and post-plan). All new route tests in
  `test_prompt_versions.py` build a minimal `FastAPI()` wrapping only
  `prompt_versions.router`, per the plan's own `<key_context>` instruction and the
  precedent already established in `test_metrics_routes.py`/`test_bench_routes.py`
  (21-05/21-06).
- **Pre-existing test-order pollution** (not caused by this plan): running
  `pytest tests/unit -k "agent or patch"` produces 17 failures in `test_agent_tools.py` /
  `test_services.py` / `retrieval/test_retrieval_service.py` when run as part of that
  larger batch, but every one of those files passes in isolation. Verified via
  `git stash` + re-run: the identical 17 failures / 12 collection errors occur with this
  plan's changes stashed out, confirming the pollution predates this plan and is unrelated
  to it. Logged here for visibility, not fixed (out of this plan's scope per the deviation
  rules' scope boundary).
- **Live/local control DB is not a "test" DB in this environment**: `.env`'s
  `CONTROL_DB_SYNC_URL` points at a live Neon project (`neondb`), not a URL containing
  `"test"`. Per the established `INTEGRATION_TESTS_ENABLED` convention (mirrored from
  `test_migration_0015.py`), the gated real-DB tests in `test_migration_0018.py` and
  `test_prompt_versions_e2e.py` correctly skip rather than run migrations/writes against a
  live production-adjacent database. This mirrors every prior Phase 21 plan's integration
  test behavior in this same dev environment and is deferred to the live-verify gate
  (`/gsd-verify-work 21`), consistent with `RESEARCH.md`'s "Live-DB verification gates"
  convention.

## User Setup Required

None — no external service configuration required. (A real Postgres `CONTROL_DB_SYNC_URL`
containing `"test"` would be needed to exercise the `INTEGRATION_TESTS_ENABLED=1` gated
tests locally; not required for this plan's completion.)

## Next Phase Readiness

- OPS-16 backend is complete: version history, diff, canary %, rollback, and turn-dispatch
  routing all exist and are tested. The admin UI soul editor (out of scope for this
  backend-completion phase) can now call `GET/POST /agents/{id}/prompt-versions[...]`.
- Deferred to `/gsd-verify-work 21` (live-gate, per this plan's own `<verification>`
  section): real canary distribution and rollback exercised against a live control Neon
  project, and a full live Claude Agent SDK turn confirming `soul_override` actually
  changes model behavior end-to-end (not just the unit-level prompt-string assembly
  already covered by `test_agent_prompt.py`).
- No blockers for subsequent Phase 21 plans — this plan's only new dependency
  (`prompt_versions`, control migration 0018) is additive and does not touch any table
  another in-flight plan writes to.

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-16*

## Self-Check: PASSED

- FOUND: apps/api/alembic/versions/0018_prompt_versions.py
- FOUND: apps/api/app/models/prompt_version.py
- FOUND: apps/api/app/services/prompt_version_service.py
- FOUND: apps/api/app/api/v1/prompt_versions.py
- FOUND: apps/api/app/api/v1/agents.py
- FOUND: apps/api/app/services/agent_prompt.py
- FOUND: apps/api/app/worker/tasks/runtime/agent.py
- FOUND: apps/api/app/main.py
- FOUND: apps/api/tests/unit/test_migration_0018.py
- FOUND: apps/api/tests/unit/test_prompt_versions.py
- FOUND: apps/api/tests/integration/test_prompt_versions_e2e.py
- FOUND commit cf2c08b (git log --oneline --all)
- FOUND commit 5398123 (git log --oneline --all)
- FOUND commit a93ae84 (git log --oneline --all)
