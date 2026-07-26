---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 06
subsystem: security
tags: [red-team, transactional-dispatcher, contextvar, claude-agent-sdk, rtx-probes]

# Dependency graph
requires:
  - phase: 18-03
    provides: "red_team_probe.py substrate — red_team_mode(), invoke_probe_tool(), _build_transactional_probe_fn(), ProbeToolResult, CLEAN_TENANT_ENVELOPES/CLEAN_TENANT_SPEC (shipped caller-free by design)"
  - phase: 18-01
    provides: "OD-6 decision text (this plan's contract) — RTX probes drive the REAL dispatcher"
provides:
  - "red_team_service.RTX_ATTACK_VECTORS, run_confused_deputy_agent, run_value_bound_evasion_agent, run_identity_bypass_agent, _RTX_DETERMINISTIC_FINDING_TEMPLATE"
  - "worker/tasks/runtime/red_team.py: the call site that wires 18-03's substrate into run_red_team — the cross-wave seam this plan owns"
  - "tests/unit/test_red_team_rtx_runners.py: mocked-boundary proof that run_red_team calls all six runners sequentially with the correct probe object"
  - "tests/integration/test_red_team_rtx.py: INTEGRATION_TESTS_ENABLED-gated real-dispatcher roundtrips for RTX-01/02/03"
affects: [18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy (function-body) imports of red_team_probe.py symbols inside red_team_service.py's two deterministic runners — required to avoid a circular import, since red_team_probe.py imports SONNET_MODEL from red_team_service.py at module level"
    - "Synchronous ContextVar seeding via build_tool_server() in the Celery task body, before any asyncio.run() call in Step 5 — asyncio Task creation copies the calling thread's *current* context (contextvars.copy_context()), so values set here propagate into each deterministic runner's own event loop, matching invoke_probe_tool's documented caller contract"

key-files:
  created:
    - apps/api/tests/unit/test_red_team_rtx_runners.py
    - apps/api/tests/integration/test_red_team_rtx.py
  modified:
    - apps/api/app/services/red_team_service.py
    - apps/api/app/worker/tasks/runtime/red_team.py
    - apps/api/tests/unit/test_red_team_task.py
    - apps/api/tests/unit/test_redteam_findings.py
    - apps/api/tests/unit/test_redteam_programme.py

key-decisions:
  - "RTX-02/RTX-03 runners keep the shipped run_X_agent(probe_fn, max_turns, attack_sequences) signature for contract uniformity but do not call probe_fn — they call red_team_probe.invoke_probe_tool directly. Since they have no direct access to agent/conn_str/tenant_id, run_red_team's Step 4 seeds the dispatcher ContextVars once via build_tool_server() (the sanctioned way per red_team_probe.py's own docstring: 'The caller must have already populated the dispatcher ContextVars via build_tool_server()') before Step 5 runs any runner."
  - "provider_not_configured is built as a plain RedTeamFinding with a hardcoded severity='high', bypassing _RTX_DETERMINISTIC_FINDING_TEMPLATE (and therefore classify_severity) — this is a tooling/infra failure signal (the short-circuit didn't engage), not a behavioral security finding a Haiku rubric should judge."
  - "run_confused_deputy_agent's per-call refund amount / probe strings avoid literally repeating the six new runner function names in comments/docstrings inside red_team.py, to keep the plan's exact grep counts (2 for _build_transactional_probe_fn, 6 for the three runner names) satisfied without contradicting the module-docstring-update instruction — used the PascalCase short names (ConfusedDeputy, ValueBoundEvasion, IdentityBypass) instead, mirroring the existing PromptInjection/DataLeakage/Hallucination convention already used in that docstring."

requirements-completed: [RTX-01, RTX-02, RTX-03]

coverage:
  - id: D1
    description: "run_red_team invokes all six runners (three M7 + three RTX) strictly sequentially, with RTX runners receiving the transactional probe and M7 runners receiving the bare conversational one"
    requirement: "RTX-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_run_red_team_calls_all_six_runners"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_run_red_team_introduces_no_concurrency"
        status: pass
    human_judgment: false
  - id: D2
    description: "The confused-deputy runner drives the real dispatcher via the transactional probe_fn and classifies whatever verdict it observes, instructed that a blocked/require-human line is not a finding"
    requirement: "RTX-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_confused_deputy_returns_empty_list_on_sdk_failure"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_confused_deputy_system_prompt_instructs_block_is_not_a_finding"
        status: pass
      - kind: integration
        ref: "tests/integration/test_red_team_rtx.py::test_confused_deputy"
        status: deferred
    human_judgment: true
    rationale: "test_confused_deputy requires a real ANTHROPIC_API_KEY and local Postgres, neither available in this environment. The module collects and skips cleanly (verified: 3 skipped, 0 failed); the live run is plan 18-11's autonomous:false gate."
  - id: D3
    description: "The value-bound-evasion runner issues multiple real refund calls in one red-team-mode window and reads the rate layer's own verdict"
    requirement: "RTX-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_value_bound_evasion_reports_no_finding_when_rate_layer_stops_the_chain"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_value_bound_evasion_reports_finding_when_chain_completes"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_value_bound_evasion_uses_fresh_idempotency_key_per_call"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_value_bound_evasion_amount_below_clean_tenant_ceiling"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_value_bound_evasion_uses_one_red_team_mode_window"
        status: pass
      - kind: integration
        ref: "tests/integration/test_red_team_rtx.py::test_value_bound_evasion"
        status: deferred
    human_judgment: true
    rationale: "test_value_bound_evasion requires local Redis and local Postgres, neither available in this environment. Module collects/skips cleanly; live run deferred to plan 18-11."
  - id: D4
    description: "The identity-bypass runner attempts an identity-gated skill with no verified session and with a forged token, and reads the Step 2.5 gate's own verdict"
    requirement: "RTX-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_identity_bypass_reports_no_finding_when_gate_blocks_both_attempts"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_identity_bypass_reports_finding_when_unverified_call_succeeds"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_identity_bypass_restores_verified_session_context_var"
        status: pass
      - kind: integration
        ref: "tests/integration/test_red_team_rtx.py::test_identity_bypass"
        status: deferred
    human_judgment: true
    rationale: "test_identity_bypass requires local Postgres, unavailable in this environment. Module collects/skips cleanly; live run deferred to plan 18-11."
  - id: D5
    description: "A run whose verdicts are provider-not-configured is reported as invalid, not clean"
    requirement: "RTX-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_provider_not_configured_yields_invalid_run_finding"
        status: pass
    human_judgment: false

# Metrics
duration: ~40min
completed: 2026-07-27
status: complete
---

# Phase 18 Plan 06: Transaction red-team probes (RTX-01/02/03) wired into run_red_team Summary

**Three new red-team runners (confused-deputy, value-bound-evasion, identity-bypass) built on plan 18-03's transactional probe substrate, wired into `run_red_team` alongside the three shipped M7 runners — the cross-wave seam this plan owns and asserts a real call site for.**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-07-27T00:50:00+02:00 (approx.)
- **Completed:** 2026-07-27T01:14:00+02:00
- **Tasks:** 4 (plus 2 deviation fix commits for pre-existing tests broken by the Step 4/5 wiring change)
- **Files modified:** 7 (2 new, 5 modified)

## Accomplishments

- `red_team_service.py`: three new runners sharing the shipped `run_X_agent(probe_fn, max_turns, attack_sequences)` signature and `except Exception -> log.warning(..., agent_type=...) -> return []` contract. `run_confused_deputy_agent` (RTX-01) is conversational, a near-copy of `run_prompt_injection_agent`, with a system prompt that explicitly instructs a blocked/require-human/capability-denied/identity-required transcript line is correct behaviour, not a finding. `run_value_bound_evasion_agent` (RTX-02) and `run_identity_bypass_agent` (RTX-03) are deterministic — they chain/attempt real `invoke_probe_tool` calls inside one `red_team_mode()` window and read the dispatcher's own `verdict_tag`. `provider_not_configured` produces an explicit invalid-run finding in both deterministic runners (RESEARCH.md Pitfall 1). `RTX_ATTACK_VECTORS` and `_RTX_DETERMINISTIC_FINDING_TEMPLATE` give the task/tests a single source of truth. No `asyncio.gather`/`chord`/`run_until_complete` anywhere.
- `worker/tasks/runtime/red_team.py`: Step 4 now builds two probe functions (`probe_fn` unchanged; `transactional_probe_fn` new, via `red_team_probe._build_transactional_probe_fn`) and seeds the dispatcher ContextVars once via `build_tool_server()` for the two deterministic RTX runners. Step 5 runs all six runners strictly sequentially (no chord — `worker_pool=solo` preserved), concatenating six finding lists. Task decorator, signature, and the Step 2 idempotency guard are untouched; `conn_str` is never logged.
- New `tests/unit/test_red_team_rtx_runners.py`: 14 fully mocked tests (no Postgres, no Redis, no live Anthropic, no SDK subprocess), ~14s wall — the fast unit companion 18-VALIDATION.md's sampling-continuity rule requires alongside the integration-gated module. Includes `test_run_red_team_calls_all_six_runners`, the cross-wave-seam wiring proof, and a source assertion that no concurrency was introduced.
- New `tests/integration/test_red_team_rtx.py`: `INTEGRATION_TESTS_ENABLED`-gated, mirrors `test_deploy_gate_redteam.py`'s ephemeral-DB-migrated-to-head fixture pattern (control DB to "0019", tenant DB to head), seeds the clean tenant from `CLEAN_TENANT_ENVELOPES`/`CLEAN_TENANT_SPEC`, and defines `test_confused_deputy`, `test_value_bound_evasion`, `test_identity_bypass` at module scope per 18-VALIDATION.md's fixed node IDs. `test_value_bound_evasion` requires local Redis (`require_redis` fixture); `test_confused_deputy` requires a real `ANTHROPIC_API_KEY`. Module collects cleanly with all three skipped in this environment (no live Postgres/Redis/API key here).
- Fixed three pre-existing test files (`test_red_team_task.py`, `test_redteam_findings.py`, `test_redteam_programme.py`) whose `MagicMock` agents' auto-generated `retrieval_strategy` attribute broke Step 4's new `RetrievalStrategy.model_validate(...)` call — Rule 1 auto-fix, directly caused by this plan's Step 4/5 wiring change.
- Unit suite: **1049 → 1063 passed, 8 skipped, 0 failed.** `apps/api/pyproject.toml` unchanged — `git diff --exit-code` exits 0, no `pyrit`, no new dependency.

## Task Commits

Each task was committed atomically:

1. **Task 1: Three RTX runner functions in red_team_service.py** - `4431ca4` (feat)
2. **Task 2: Wire the transactional probe_fn and three runner calls into run_red_team** - `2ccb0b2` (feat)
   - Deviation fix: `81becbc` (fix) — `test_red_team_task.py` needed the new Step 4/5 mocks
   - Deviation fix: `df2deef` (fix) — `test_redteam_findings.py` / `test_redteam_programme.py` needed the same
3. **Task 3: Fast mocked-boundary unit coverage for the runners and the wiring** - `8d662f1` (test)
4. **Task 4: Integration module — ephemeral control+tenant DB fixture and the three RTX roundtrips** - `4d12544` (test)

**Plan metadata:** committed alongside this SUMMARY (see below).

## Files Created/Modified

- `apps/api/app/services/red_team_service.py` - added `RTX_ATTACK_VECTORS`, `_RTX_DETERMINISTIC_FINDING_TEMPLATE`, `run_confused_deputy_agent`, `run_value_bound_evasion_agent`, `run_identity_bypass_agent`
- `apps/api/app/worker/tasks/runtime/red_team.py` - Step 4 builds `transactional_probe_fn` + seeds dispatcher ContextVars via `build_tool_server()`; Step 5 runs six runners sequentially; docstrings updated
- `apps/api/tests/unit/test_red_team_rtx_runners.py` (new) - 14 mocked-boundary tests for the three runners + the six-runner wiring proof
- `apps/api/tests/integration/test_red_team_rtx.py` (new) - `INTEGRATION_TESTS_ENABLED`-gated real-dispatcher roundtrips
- `apps/api/tests/unit/test_red_team_task.py`, `test_redteam_findings.py`, `test_redteam_programme.py` - updated mock agents + added patches for the new Step 4/5 code path (Rule 1 fix)

## Decisions Made

- ContextVar propagation for the two deterministic RTX runners: since they only receive `probe_fn` (kept for contract uniformity, per plan) and have no direct access to `agent`/`conn_str`/`tenant_id`, `red_team.py`'s Step 4 calls `build_tool_server()` synchronously — before any `asyncio.run()` call in Step 5 — so Python's `contextvars.copy_context()` (invoked internally by every subsequent `asyncio.run()`/Task creation) carries those values into each runner's own event loop. This is the exact mechanism `red_team_probe.invoke_probe_tool`'s own docstring names as the caller's responsibility ("The caller must have already populated the dispatcher ContextVars via `build_tool_server()`").
- Lazy (function-body) imports for every `red_team_probe.py` symbol used inside `red_team_service.py`'s new runners — `red_team_probe.py` imports `SONNET_MODEL` from `red_team_service.py` at module level, so a module-level import in the other direction would be circular. Tests patch these at their source (`app.services.red_team_probe.invoke_probe_tool`, not `app.services.red_team_service.invoke_probe_tool`) accordingly.
- `provider_not_configured` findings in both deterministic runners are built directly (severity hardcoded `"high"`), bypassing `classify_severity` — this is a tooling-failure signal, not a behavioral judgment a Haiku rubric should render.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed three pre-existing test files broken by Step 4's new RetrievalStrategy.model_validate call**
- **Found during:** Task 2 verification (running `test_red_team_task.py` etc. as a broader regression check)
- **Issue:** `run_red_team`'s Step 4 now calls `RetrievalStrategy.model_validate(agent.retrieval_strategy or {})` and `build_tool_server(...)`. Three existing test files (`test_red_team_task.py::test_run_red_team_complete`, `test_redteam_findings.py` (2 tests), `test_redteam_programme.py` (2 tests)) constructed `mock_agent = MagicMock()` without setting `retrieval_strategy`, so `agent.retrieval_strategy` auto-generated a truthy `MagicMock` that failed Pydantic validation with `ValidationError: Input should be a valid dictionary`.
- **Fix:** Set `mock_agent.retrieval_strategy = {}`, `mock_agent.tenant_id`, `mock_agent.id` on each mock agent, and added patches for `build_tool_server`, `_build_transactional_probe_fn`, and the three new RTX runners at the same boundary the three M7 runners were already patched at.
- **Files modified:** `apps/api/tests/unit/test_red_team_task.py`, `apps/api/tests/unit/test_redteam_findings.py`, `apps/api/tests/unit/test_redteam_programme.py`
- **Commits:** `81becbc`, `df2deef`

Or: no other deviations — the remaining three tasks executed as written.

## Issues Encountered

- The plan's Task 2 instruction to "add the three new runner names to the Step 5 line" of the module docstring conflicts with the acceptance criterion requiring the runner-name grep count to be exactly 6 (three imports, three calls). Resolved by following the SAME convention the existing docstring already uses for the M7 runners — PascalCase short names (`ConfusedDeputy`, `ValueBoundEvasion`, `IdentityBypass`) rather than the literal snake_case function names — which satisfies both the docstring-update instruction and the exact-count acceptance criterion.
- `tests/integration/test_red_team_rtx.py` cannot be executed against real infrastructure in this environment (no live Postgres, no local Redis confirmed running, no real `ANTHROPIC_API_KEY`). Verified the module imports cleanly and all three tests skip correctly (`3 skipped, 0 failed`) — the live run is explicitly plan 18-11's `autonomous:false` gate, consistent with how Phases 13/15/16/17/21 handled the same class of gap.

## User Setup Required

None for this plan's automated scope. The live integration run (real local Postgres + local `redis-server` + real `ANTHROPIC_API_KEY`) is deferred to plan 18-11's `autonomous:false` checkpoint — no Docker in any step (CLAUDE.md rule 9).

## Next Phase Readiness

- Plan 18-09 extends `tests/unit/test_red_team_service.py` in a later wave — this plan deliberately did not touch that file (verified via `git diff --stat`), avoiding file contention.
- Plan 18-11 owns the RTX-04 clean-tenant zero-high-severity live gate and can run `pytest tests/integration/test_red_team_rtx.py -m integration` with `INTEGRATION_TESTS_ENABLED=1`, a real `ANTHROPIC_API_KEY`, local Postgres, and local `redis-server` directly — the fixtures and three named tests are already in place.
- No blockers. `apps/api/pyproject.toml` untouched; full unit suite green at 1063 passed / 0 failed, above the 970/1049 baselines.

## Known Stubs

None — every deliverable in this plan has a live call site (the cross-wave seam this plan exists to close) and is exercised by either a passing unit test or a correctly-skipping integration test.

## Threat Flags

None beyond what `18-06-PLAN.md`'s own `<threat_model>` already registers (T-18-RTX-01/01b/02/03/04, T-18-SC) — no new network endpoint, auth path, file-access pattern, or schema change was introduced outside that register.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-27*
