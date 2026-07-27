---
phase: 19-documentation-v1-1-verification
plan: 04
subsystem: testing
tags: [pytest, red-team-probe, adversarial, dispatcher, postgres, redis]

# Dependency graph
requires:
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: red_team_probe.py (red_team_mode(), invoke_probe_tool, ProbeToolResult.verdict_tag, CLEAN_TENANT_ENVELOPES, CLEAN_TENANT_SPEC) and the ephemeral-DB fixture pattern (test_red_team_rtx.py)
provides:
  - "apps/api/tests/integration/test_ver01_adversarial_harness.py — INTEGRATION_TESTS_ENABLED-gated VER-01 SC3 harness: a 104-entry ADVERSARIAL_MESSAGE_CORPUS spanning capability/identity/rate-constraint/Actor layers, run_adversarial_corpus (single red_team_mode() window driver), summarise_probe_run (attempted-vs-findings accounting with a provider_not_configured invalid-run guard)"
  - "apps/api/tests/unit/test_ver01_harness_probes.py — 12 DB-free unit tests proving corpus shape, summariser accounting, and an ordering (not co-occurrence) proof that the red-team window opens before the first probe and closes after the last"
affects: [19-05-live-gates, 19-UAT]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Deferred module-level substrate binding: invoke_probe_tool / red_team_mode / ProbeToolResult start as None module globals and are populated by a _load_probe_substrate() helper on first use, loading each name independently so a test that has already patched one or two of them never has the third clobbered back to a real value. Satisfies two competing constraints simultaneously: bare `python -c \"import ...\"` must succeed outside pytest (app.services.red_team_probe imports app.core.config, whose module body eagerly constructs Settings() and fails outside pytest — the exact 19-03 finding), while the unit companion still needs `tests.integration.test_ver01_adversarial_harness.invoke_probe_tool`/`red_team_mode` to exist as patchable module attributes (a direct `from X import Y` at module scope, which the plan's action text calls for, would satisfy patchability but break the bare-import requirement)."
    - "Layer-to-attack-class mapping grounded in the dispatcher's real Step 2.5-before-Step-4 enforcement order rather than the plan's illustrative wording: since the whole corpus runs under one unverified clean_tenant fixture (verified_session_token=\"\", matching CLEAN_TENANT_SPEC's own default), every issue_refund call is blocked at the identity layer regardless of amount and can never reach the rate/constraint layer in this run — so ceiling- and rate-limit-overage entries target place_order/cancel_order/update_subscription/book_slot/update_customer_record instead, none of which require identity verification on the clean tenant."

key-files:
  created:
    - apps/api/tests/integration/test_ver01_adversarial_harness.py
    - apps/api/tests/unit/test_ver01_harness_probes.py
  modified: []

key-decisions:
  - "Capability-layer probes routed through confirm_action referencing an unconfigured skill string (e.g. 'delete_account'), since all six real mutating skills are enabled=True on CLEAN_TENANT_ENVELOPES and there is no disabled-skill fixture to call directly — confirm_action's own capability check reads validated.skill (the referenced skill), never the literal 'confirm_action', so any unconfigured name still trips the same no_envelope_row fail-closed path."
  - "The five capability_denied/identity_required/rate_denied/actor_blocked/awaiting_approval-adjacent attack classes are mapped onto the shipped RTX_ATTACK_VECTORS + INJECTION_ATTACK_VECTORS vocabulary (confused_deputy, value_bound_evasion, identity_verification_bypass, conversation_injection, content_injection) with no sixth invented string — the capability-layer confirm_action probes are labelled confused_deputy (exploiting the confirmation subsystem's trust in an unauthorized referenced skill), the closest fit among the five shipped strings."
  - "unauthorized_mutations does not track WHICH enforcement layer denied an expected_denied=True entry, only that verdict_tag != 'succeeded' — this makes the corpus's pass/fail criterion invariant to call-ordering and shared-rate-window interactions between corpus entries targeting the same skill, so no entry needed to be hand-tuned against exact Redis window timing."

requirements-completed: [VER-01]

coverage:
  - id: D1
    description: "ADVERSARIAL_MESSAGE_CORPUS carries 104 entries, unique ids, exclusively known PROBE_SKILL_TOOLS skills, distinct idempotency_key per mutating entry, and no float anywhere in args (every monetary value is Python int cents)"
    requirement: "VER-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_ver01_harness_probes.py (5 corpus-shape tests)"
        status: pass
    human_judgment: false
  - id: D2
    description: "summarise_probe_run reports attempted independently of by_verdict/unauthorized_mutations, never merges repeated verdict tags, marks empty and provider_not_configured runs invalid, and defines unauthorized_mutations exactly once (succeeded + expected_denied)"
    requirement: "VER-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_ver01_harness_probes.py (6 summariser tests)"
        status: pass
    human_judgment: false
  - id: D3
    description: "Every invoke_probe_tool call in run_adversarial_corpus happens strictly inside one red_team_mode() window, entered exactly once before the first probe and exited after the last (ordering, not co-occurrence)"
    requirement: "VER-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_ver01_harness_probes.py::test_all_probes_inside_red_team_mode"
        status: pass
    human_judgment: false
  - id: D4
    description: "apps/api/tests/integration/test_ver01_adversarial_harness.py collects cleanly and skips (1 skipped, 0 errors) in this environment, and imports outside pytest without requiring a live DB"
    requirement: "VER-01"
    verification:
      - kind: unit
        ref: "pytest tests/integration/test_ver01_adversarial_harness.py -q (1 skipped)"
        status: pass
      - kind: other
        ref: "python -c \"import tests.integration.test_ver01_adversarial_harness as m; assert len(m.ADVERSARIAL_MESSAGE_CORPUS) >= 100\" (VER01-HARNESS-OK 104)"
        status: pass
    human_judgment: false
  - id: D5
    description: "A real run of at least 100 adversarial messages against a live migrated clean tenant produces zero unauthorized state mutations and zero provider_not_configured verdicts"
    requirement: "VER-01"
    verification: []
    human_judgment: true
    rationale: "No live local Postgres/Redis/ANTHROPIC_API_KEY guaranteed in this execution environment — this is the plan's own verification:backstop truth, deferred to the operator's live run in 19-05-PLAN.md and transcribed into 19-UAT.md, never silently passed."

# Metrics
duration: 30min
completed: 2026-07-28
status: complete
---

# Phase 19 Plan 04: VER-01 SC3 Adversarial Harness Summary

**104-entry adversarial corpus (capability/identity/rate-constraint/Actor layers) driven through the real transactional dispatcher via the shipped red_team_probe substrate, with an attempted-vs-findings summariser that structurally cannot report a short-circuited run as clean — the gated live run is deferred to the operator in plan 19-05.**

## Performance

- **Duration:** ~30 min
- **Completed:** 2026-07-27T23:06:18Z
- **Tasks:** 2/2
- **Files modified:** 2 (both new)

## Accomplishments

- `apps/api/tests/integration/test_ver01_adversarial_harness.py`: `INTEGRATION_TESTS_ENABLED`-gated VER-01 SC3 harness built entirely on the Phase-18 RTX substrate (`red_team_mode()`, `invoke_probe_tool`, `ProbeToolResult.verdict_tag`, `CLEAN_TENANT_ENVELOPES`/`CLEAN_TENANT_SPEC`) rather than a parallel one. `ADVERSARIAL_MESSAGE_CORPUS` (104 entries, all unique ids, int-cents-only args, distinct idempotency keys) spreads attacks across the capability layer (confirm_action referencing unconfigured skills, 15), identity layer (unverified issue_refund, 20), rate/constraint layer (place_order ceiling overage, 12, plus four 8-entry rate-limit chains against cancel_order/update_subscription/book_slot/update_customer_record, 32), the Actor layer (confused-deputy-shaped argument framings, 15), and conversation/content injection payloads embedded in free-text args (10) — plus a built-in minority of in-policy entries (the first 5 of each rate-limit chain, `expected_denied=False`) so the corpus is not entirely "expected to be denied." `run_adversarial_corpus` opens exactly one `red_team_mode()` window and drives every entry through `invoke_probe_tool` in order; `summarise_probe_run` reports `attempted` independently of `by_verdict`/`unauthorized_mutations`, marks a run `invalid` when `attempted < 100` or any `provider_not_configured` verdict appears, and defines an unauthorized mutation exactly once (`verdict_tag == "succeeded"` on an `expected_denied=True` entry).
- `apps/api/tests/unit/test_ver01_harness_probes.py`: 12 mocked-boundary tests (no Postgres/Redis/live Anthropic) importing the corpus/driver/summariser from the gated harness — proving corpus shape and int-cents discipline, the empty-run and `provider_not_configured` invalid-run guards, `attempted == sum(by_verdict.values())` accounting with repeated-tag counting, the single unauthorized-mutation definition, and (the load-bearing test) that `red_team_mode()` is entered exactly once, before the first `invoke_probe_tool` call and after none of them — an ordering proof, not a call-count proof.
- Resolved a genuine conflict between the plan's own acceptance criteria (bare `python -c "import tests.integration.test_ver01_adversarial_harness"` must succeed outside pytest) and its action text (module-level `from app.services.red_team_probe import ...` binding for patchability) via a deferred-loader pattern: `invoke_probe_tool`/`red_team_mode`/`ProbeToolResult` start as `None` module globals, each independently populated on first use by `_load_probe_substrate()` — patchable as real module attributes from the first line, but never triggering `app.core.config`'s eager `Settings()` validation at bare-import time.

## Task Commits

Each task was committed atomically:

1. **Task 1: Author the 100-message adversarial harness on the shipped probe substrate** - `40bbb50` (test)
2. **Task 2: Prove corpus shape, summariser accounting, and red-team-window discipline without live infrastructure** - `222ffcc` (test)

_No TDD RED/GREEN split — both tasks are pure test-file authorship with no production code under `apps/api/app/` touched; each commit's own test suite (skip-collection for Task 1, all-green for Task 2) was verified before committing._

## Files Created/Modified

- `apps/api/tests/integration/test_ver01_adversarial_harness.py` - VER-01 SC3 gated harness + `ADVERSARIAL_MESSAGE_CORPUS`/`run_adversarial_corpus`/`summarise_probe_run`
- `apps/api/tests/unit/test_ver01_harness_probes.py` - 12 DB-free unit tests proving corpus shape, accounting, and window-ordering discipline

## Decisions Made

- **Capability-layer attacks routed through `confirm_action` referencing an unconfigured skill string**, since `CLEAN_TENANT_ENVELOPES` enables all six real mutating skills and there is no disabled-skill fixture data to call directly. `confirm_action_tool`'s own capability check reads `validated.skill` (the referenced skill inside its args), never the literal `"confirm_action"`, so a skill name absent from `capability_envelopes` still trips `check_capability_access`'s fail-closed `no_envelope_row` path.
- **Rate/constraint-layer overage attacks target `place_order`/`cancel_order`/`update_subscription`/`book_slot`/`update_customer_record`, not `issue_refund`**, because the corpus runs under one unverified session for its whole duration and `_execute_transactional_tool`'s Step 2.5 (IDV) runs before Step 4 (rate/constraint) — an unverified `issue_refund` call is always blocked at the identity layer first, regardless of amount. `issue_refund` therefore carries the `identity_verification_bypass` attack class exclusively in this corpus; ceiling/rate-limit coverage moves to the five skills that don't require identity verification on the clean tenant.
- **`unauthorized_mutations` is invariant to which layer denies an `expected_denied=True` entry**, only requiring `verdict_tag != "succeeded"`. This let the rate-limit-chain entries (same skill, shared Redis window) and the ceiling-overage entries (same skill, shared window) be composed without hand-tuning exact call-ordering or timing — whichever layer catches a given call, the corpus's own pass/fail criterion still holds.
- **Attack-class vocabulary strictly limited to the shipped `RTX_ATTACK_VECTORS` + `INJECTION_ATTACK_VECTORS` five strings** (`confused_deputy`, `value_bound_evasion`, `identity_verification_bypass`, `conversation_injection`, `content_injection`) — no sixth invented label, even for the capability-layer probes that don't map perfectly onto any single existing vector (labelled `confused_deputy` as the closest fit: exploiting the confirmation subsystem's trust in an unauthorized referenced action).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Deferred (not module-level) binding of `invoke_probe_tool` / `red_team_mode` / `ProbeToolResult`, resolving a conflict in the plan's own text**
- **Found during:** Task 1, running the plan's own bare-import `<verify>` command (`python -c "import tests.integration.test_ver01_adversarial_harness as m; ..."`).
- **Issue:** The plan's `<action>` text specified `from app.services.red_team_probe import ...` at module level, matching the pattern `_control_db_redirected` uses for `get_sync_db`, "so a test patches `tests.integration.test_ver01_adversarial_harness.invoke_probe_tool`." But `red_team_probe.py` imports `app.core.config`, whose module body eagerly constructs `Settings()` — empirically confirmed to raise `pydantic_core.ValidationError` (missing `PLATFORM_CREDENTIAL_KEY`) outside pytest, before `tests/conftest.py`'s `os.environ.setdefault(...)` calls have run. This is the exact failure `19-03-SUMMARY.md` already recorded and fixed by moving to lazy imports — and the plan's own phase-specific constraints for this plan explicitly named it as a lesson to heed. A literal module-level import here would have broken the plan's own bare-`python -c` acceptance check.
- **Fix:** `invoke_probe_tool`, `red_team_mode`, and `ProbeToolResult` are declared as module-level globals initialised to `None`, populated independently on first use by a `_load_probe_substrate()` helper. Each name loads only if still `None`, so a name already patched by a test (a real `unittest.mock.patch` target, satisfying the plan's patchability requirement) is never clobbered back to the real function, while an unpatched name still loads for real — resolving both constraints simultaneously rather than picking one over the other.
- **Files modified:** `apps/api/tests/integration/test_ver01_adversarial_harness.py` (also documented in the module's own "Module-level import discipline" docstring section).
- **Verification:** `python -c "import tests.integration.test_ver01_adversarial_harness as m; n=len(m.ADVERSARIAL_MESSAGE_CORPUS); assert n>=100; ..."` completes in well under a second with no error (`VER01-HARNESS-OK 104`); `pytest tests/integration/test_ver01_adversarial_harness.py -q` reports 1 skipped, 0 errors; `test_all_probes_inside_red_team_mode` (Task 2) proves the patched names are actually intercepted at call time.
- **Committed in:** `40bbb50` (Task 1 commit — fixed before the first commit, then refined once more in the same commit's working tree after Task 2 exposed a second-order bug in the loader, see below).

**2. [Rule 1 - Bug] `_load_probe_substrate()`'s single-guard early return left `ProbeToolResult` unbound when only `invoke_probe_tool`/`red_team_mode` were patched**
- **Found during:** Task 2, first run of `test_all_probes_inside_red_team_mode` — `AttributeError: 'NoneType' object has no attribute 'from_dispatcher_response'`.
- **Issue:** The initial loader used a single guard (`if invoke_probe_tool is not None: return`). Since the unit companion patches only `invoke_probe_tool` and `red_team_mode` (never `ProbeToolResult`, a plain dataclass with no boundary to mock, per the plan's own patch-target list), the guard short-circuited before `ProbeToolResult` was ever loaded, leaving it `None` inside `run_adversarial_corpus`.
- **Fix:** Split the guard into three independent per-name checks (`if invoke_probe_tool is None: ...`, etc.) inside one shared import block, so a patched pair never blocks the third, unpatched name from loading for real.
- **Files modified:** `apps/api/tests/integration/test_ver01_adversarial_harness.py`.
- **Verification:** `pytest tests/unit/test_ver01_harness_probes.py -q` — 12 passed (was 11 passed, 1 failed before the fix); `pytest tests/integration/test_ver01_adversarial_harness.py -q` and the bare-import check re-verified still green after the edit.
- **Committed in:** `40bbb50` (Task 1 commit — the fix predates Task 1's commit; Task 2's tests, committed separately in `222ffcc`, are what surfaced it).

---

**Total deviations:** 2 auto-fixed (2 blocking/bug, both resolving conflicts internal to the plan's own text rather than deviating from its intent).
**Impact on plan:** Both fixes are necessary for the plan's own acceptance criteria to hold simultaneously (bare-import success AND patchability); the resulting design achieves everything the plan asked for, just not via the single literal mechanism ("direct module-level `from X import Y`") the action text named. No scope creep — no file outside the plan's two named artifacts was touched.

## Issues Encountered

- The plan's action text and its own acceptance criteria pulled in opposite directions on import strategy (module-level binding for patchability vs. bare-import safety) — the same category of internal-plan-text conflict `19-03-SUMMARY.md` found, but manifesting differently here because this plan's Task 2 explicitly depends on the harness module exposing patchable attributes, not just being importable. Re-reading both the acceptance criteria and the phase-specific constraints together (rather than the action text alone) surfaced the deferred-loader design as the resolution.

## User Setup Required

None - no external service configuration required. The gated harness needs real local Postgres, Redis, and (unless every corpus entry short-circuits before the Actor gate) a real `ANTHROPIC_API_KEY` to actually run (per its `INTEGRATION_TESTS_ENABLED=1` skip reason); that live run is deferred to `19-05-PLAN.md`'s operator gate, not a setup step for this plan.

## Next Phase Readiness

- Both new test files are committed, collect cleanly, and the full unit suite is green at 1134 passed / 8 skipped / 0 failed (1122 baseline from 19-03 + 12 new unit tests), strictly above the plan's required floor.
- `apps/api/pyproject.toml` is byte-identical to before this plan and no file under `apps/api/app/` was modified (both verified via `git diff --quiet` / `git status --short`) — no dependency added, no production path touched, matching the plan's hard scope fence.
- **Outstanding:** the gated `test_100_adversarial_messages_zero_unauthorized_mutations` itself has never been run against a real Postgres/Redis/Anthropic API in any environment (this one included) — its correctness rests on careful source-reading of the dispatcher's real enforcement order (confirmed by reading `tools.py`/`enforcement.py` directly, including the Step 2.5-before-Step-4 ordering that shapes this corpus's own layer-to-skill mapping) rather than an executed proof. `19-05-PLAN.md` Task 1 is where the operator runs it for real and records the `by_verdict` table and attempted count in `19-UAT.md`; this SUMMARY's `verification: backstop` coverage entry (D5) reflects that honestly rather than claiming it passed.
- **Scope fence honoured:** the file contains none of the three forbidden literals (`classify_severity`, the RTX-04 test name, `import app.main`), and does not claim or close RTX-04 — 18-11 remains its sole owner, per OD-5.

---
*Phase: 19-documentation-v1-1-verification*
*Completed: 2026-07-28*

## Self-Check: PASSED

- FOUND: apps/api/tests/integration/test_ver01_adversarial_harness.py
- FOUND: apps/api/tests/unit/test_ver01_harness_probes.py
- FOUND: .planning/phases/19-documentation-v1-1-verification/19-04-SUMMARY.md
- FOUND commit: 40bbb50
- FOUND commit: 222ffcc
