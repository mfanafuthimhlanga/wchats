---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 03
subsystem: security
tags: [red-team, transactional-dispatcher, contextvar, claude-agent-sdk, stub-adapter]

# Dependency graph
requires:
  - phase: 18-01
    provides: "OD-6 decision text (this plan's contract), migration 0019 actor_mode domain"
  - phase: 14-16
    provides: "the real transactional dispatcher (_execute_transactional_tool), StubProviderAdapter, provider_adapter.get_adapter_for_skill"
provides:
  - "provider_adapter._red_team_mode_var + _set_red_team_mode/_reset_red_team_mode — module-private short-circuit to StubProviderAdapter, off by default, no config/env surface"
  - "red_team_probe.red_team_mode() — the only sanctioned setter of the short-circuit"
  - "red_team_probe.invoke_probe_tool(skill, args) — deterministic dispatcher-invocation surface returning the dispatcher's own response dict"
  - "red_team_probe._build_transactional_probe_fn(agent, conn_str, tenant_id) — conversational victim-turn probe_fn matching the run_X_agent(probe_fn, ...) contract, appends a machine-readable tool-verdict transcript"
  - "red_team_probe.ProbeToolResult.verdict_tag — 7-way dispatcher-vocabulary classifier including provider_not_configured (a failed short-circuit surfaces as an invalid run, not a clean one)"
  - "red_team_probe.CLEAN_TENANT_ENVELOPES / CLEAN_TENANT_SPEC — RTX-04's clean tenant as executable data"
affects: [18-06, 18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ContextVar-gated short-circuit at the top of a dispatcher-adjacent resolver function, mirroring the existing _conn_str_var/_agent_id_var ContextVar convention already used by the transactional dispatcher"
    - "Machine-readable verdict-tag classification of dispatcher response text (dispatcher's own vocabulary, not free-text matching) so probe assertions are deterministic"

key-files:
  created:
    - apps/api/app/services/red_team_probe.py
    - apps/api/tests/unit/test_red_team_probe.py
  modified:
    - apps/api/app/services/transactional/provider_adapter.py

key-decisions:
  - "Red-team-mode short-circuit placed at the very top of get_adapter_for_skill, before _fetch_credential_config — a clean tenant has zero integration_credentials rows, so credential resolution would raise ProviderNotConfiguredError and abort the call before any capability/IDV/rate/Actor verdict is observed (RESEARCH.md Pitfall 1)"
  - "_build_transactional_probe_fn returns \"\" immediately on any exception inside the victim-turn try/except (not a partial transcript) — matches the shipped _build_probe_fn's exact failure-return contract, verified by test_probe_fn_returns_empty_string_on_victim_failure"
  - "red_team_mode() window scoped tightly around the ClaudeSDKClient async-with block only, not around tool_server/options construction — those never touch the dispatcher"
  - "invoke_probe_tool does NOT open its own red_team_mode() window — the caller owns it so a multi-call sequence (RTX-02's chained refunds) stays inside one window"
  - "Task 3's env preamble: test_capability_enforcement.py (named by the plan as the source) carries no os.environ.setdefault(...) block of its own — those vars are already set by tests/conftest.py, auto-loaded for every test under tests/unit/. No preamble was duplicated; relies on the same conftest.py mechanism."

requirements-completed: [RTX-01, RTX-02, RTX-03]

coverage:
  - id: D1
    description: "Red-team mode is off by default, has no config/env surface, and is reachable only from red_team_probe.red_team_mode()"
    requirement: "RTX-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_red_team_mode_off_by_default"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_red_team_mode_context_manager_sets_and_resets"
        status: pass
    human_judgment: false
  - id: D2
    description: "get_adapter_for_skill returns the stub singleton before any credential fetch inside red-team mode, and still resolves credentials normally outside it"
    requirement: "RTX-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_get_adapter_for_skill_short_circuits_to_stub_in_red_team_mode"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_get_adapter_for_skill_still_resolves_credentials_outside_red_team_mode"
        status: pass
    human_judgment: false
  - id: D3
    description: "invoke_probe_tool gives RTX-02/RTX-03 the dispatcher's own verdict as a deterministic, tagged observable"
    requirement: "RTX-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_invoke_probe_tool_returns_dispatcher_response_verbatim"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_probe_tool_result_verdict_tags"
        status: pass
    human_judgment: false
  - id: D4
    description: "_build_transactional_probe_fn returns a one-argument callable matching the run_X_agent runner contract, drives the real tool server via build_tool_server, and fails safe (returns \"\") on a victim-turn error — the actual dispatcher-driving integration path is deferred to plan 18-06's INTEGRATION_TESTS_ENABLED-gated runners"
    requirement: "RTX-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_probe_fn_signature_matches_runner_contract"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_probe_fn_returns_empty_string_on_victim_failure"
        status: pass
    human_judgment: true
    rationale: "This deliverable is caller-free by design (plan 18-06 wires it into the runners and drives it against the real dispatcher via INTEGRATION_TESTS_ENABLED tests). The mocked-boundary tests here prove the contract and failure resilience but cannot prove a live tool call traverses the real dispatcher end-to-end — that proof belongs to 18-06/18-11."
  - id: D5
    description: "The clean tenant exists as executable data (CLEAN_TENANT_ENVELOPES/CLEAN_TENANT_SPEC) with exactly one IDV-gated skill and a bounded configured blast radius, satisfying RTX-04's structural precondition"
    requirement: "RTX-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_clean_tenant_envelopes_are_well_formed"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_probe.py::test_clean_tenant_spec_declares_zero_credentials"
        status: pass
    human_judgment: false

# Metrics
duration: ~25min
completed: 2026-07-26
status: complete
---

# Phase 18 Plan 03: Transactional red-team probe substrate (OD-6) Summary

**A red-team probe path that drives the real `_execute_transactional_tool` dispatcher via a `StubProviderAdapter` short-circuit — the substrate that makes RTX-01/02/03 meaningful, since the shipped `_build_probe_fn` never attaches tools and therefore never reaches L1-L3.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-26T22:50:00+02:00 (approx.)
- **Completed:** 2026-07-26T23:20:00+02:00
- **Tasks:** 3
- **Files modified:** 3 (1 modified, 2 new)

## Accomplishments

- `provider_adapter.py`: module-private `_red_team_mode_var` ContextVar (default `False`) plus `_set_red_team_mode`/`_reset_red_team_mode`, and a short-circuit at the very top of `get_adapter_for_skill` that returns the `_STUB_ADAPTER` singleton before `_fetch_credential_config` is ever called — proven by `assert_not_called()` in the unit test, not just by inspection.
- New `red_team_probe.py`: `red_team_mode()` (the only sanctioned setter), `invoke_probe_tool()` (deterministic dispatcher-invocation surface for RTX-02/RTX-03), `_build_transactional_probe_fn()` (conversational victim-turn surface for RTX-01, matching the existing `run_X_agent(probe_fn, ...)` runner contract exactly), `ProbeToolResult.verdict_tag` (7-way classifier over the dispatcher's own response vocabulary — `capability_denied`, `identity_required`, `rate_denied`, `actor_blocked`, `awaiting_approval`, `provider_not_configured`, `succeeded`), and `CLEAN_TENANT_ENVELOPES`/`CLEAN_TENANT_SPEC` (RTX-04's clean tenant expressed as executable data).
- New `test_red_team_probe.py`: 18 test cases (12 named + 7 parametrised verdict-tag cases counted individually — 12 unique test functions), every boundary mocked (no Postgres, no Redis, no live Anthropic call, no SDK subprocess). Full module runs in ~20s wall (import-time dominated; each individual test is sub-10ms).
- Unit suite: **996 → 1014 passed, 8 skipped, 0 failed.** `apps/api/pyproject.toml` unchanged — `git diff --exit-code` exits 0, no `pyrit`, no new dependency.

## Task Commits

Each task was committed atomically:

1. **Task 1: Red-team-mode short-circuit in get_adapter_for_skill** - `ed4bcef` (feat)
2. **Task 2: red_team_probe.py — probe surfaces + executable clean-tenant posture** - `9a7849c` (feat)
3. **Task 3: Mocked-boundary unit coverage for the probe substrate** - `ab6b35a` (test)

**Plan metadata:** committed alongside this SUMMARY (see below).

## Files Created/Modified

- `apps/api/app/services/transactional/provider_adapter.py` - added `_red_team_mode_var` + setter/resetter + the short-circuit and its docstring rationale in `get_adapter_for_skill`
- `apps/api/app/services/red_team_probe.py` (new) - the two no-analog gaps named by `18-PATTERNS.md`: `_build_transactional_probe_fn` and the red-team-mode window management, plus the deterministic `invoke_probe_tool` surface and the clean-tenant fixture data
- `apps/api/tests/unit/test_red_team_probe.py` (new) - mocked-boundary companion to plan 18-06's `INTEGRATION_TESTS_ENABLED`-gated integration tests

## Decisions Made

- Short-circuit placed before `_fetch_credential_config`, not after — the only placement that prevents a clean (zero-credential) tenant from silently aborting at `provider.not_configured` before any real security verdict fires (RESEARCH.md Pitfall 1). See `key-decisions` in frontmatter for the full list, including the `_build_transactional_probe_fn` "return `\"\"` on any failure" contract and the `test_capability_enforcement.py` env-preamble non-finding.

## Deviations from Plan

None — plan executed exactly as written, with one clarification: the plan's read_first instruction to "copy the `os.environ.setdefault(...)` preamble from `tests/unit/test_capability_enforcement.py`" describes a preamble that file does not actually contain (that file, like most test modules in this suite, relies on `tests/conftest.py`'s module-level `os.environ.setdefault` block, which pytest auto-loads for every test under `tests/unit/`). `test_red_team_probe.py` relies on the same `conftest.py` mechanism rather than duplicating a preamble that doesn't exist in the named source file. This is a correction to the plan's factual claim about that file's contents, not a scope change — no environment variable behavior differs from what the plan intended.

## Issues Encountered

- The literal string `run_until_complete` initially appeared inside a docstring comment (explaining what NOT to use) in `red_team_probe.py`, which would have failed the acceptance-criteria grep `grep -n 'run_until_complete' ... returns nothing` even though no such call exists in the code. Reworded the docstring to describe the same constraint without using the literal substring. No behavior change — caught before commit, not tracked as a Rule 1-3 deviation since nothing was ever broken at runtime.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- This plan is **caller-free by design**. Nothing in the codebase calls `red_team_probe.py` yet. Plan 18-06 is the later plan that wires this substrate into the red-team runners (`red_team.py` / `red_team_service.py`) and whose acceptance criteria assert the call site exists — this SUMMARY explicitly does not treat that as incomplete work.
- Plan 18-06 can `from app.services.red_team_probe import invoke_probe_tool, _build_transactional_probe_fn, red_team_mode, CLEAN_TENANT_ENVELOPES, CLEAN_TENANT_SPEC` directly — all five symbols are stable and unit-tested.
- Plan 18-11's live RTX-04 gate can rely on `CLEAN_TENANT_SPEC["integration_credentials_rows"] == 0` and `CLEAN_TENANT_SPEC["max_acceptable_severity"] == "medium"` as the machine-readable gate definition when provisioning the clean-tenant fixture.
- No blockers. `apps/api/pyproject.toml` untouched; full unit suite green at 1014 passed / 0 failed, above the 970/996 baselines.

## Known Stubs

None — this plan's deliverable is intentionally a substrate with no caller (see "Next Phase Readiness"); this is documented as by-design, not a stub requiring resolution.

## Threat Flags

None beyond what `18-03-PLAN.md`'s own `<threat_model>` already registers (T-18-RTX-01/01b/02/03/04, T-18-SC) — no new network endpoint, auth path, file-access pattern, or schema change was introduced outside that register.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-26*
