---
phase: 19-documentation-v1-1-verification
plan: 02
subsystem: docs
tags: [markdown, capability-envelope, actor-seam, tighten-only, red-team-fixtures]

# Dependency graph
requires:
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: "capability_service.py (PLATFORM_CAPABILITY_DEFAULTS, validate_tighten_only), the shipped 18-10 deploy page copy, red_team_probe.py's CLEAN_TENANT_ENVELOPES shape"
  - phase: 15-actor-validator-l3-plus-four-node-validation-chain
    provides: "actor_seam.py's call_actor_gate and the ACTOR_SKIP_MAX_AMOUNT_CENTS skip short-circuit"
provides:
  - "docs/guides/owner-capability-guide.md — DOC-03, plain-language owner narration of the six capability controls, tighten-only, and blast radius"
  - "apps/api/tests/unit/test_ver01_demo_tenant.py — VER01_DEMO_TENANT_ENVELOPES / VER01_DEMO_TENANT_SPEC plus the Actor skip-boundary proof, closing half of VER-01's precondition"
affects: [19-05, future-phases-touching-capability-service-or-actor-seam]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Demo/fixture tenant postures expressed as executable module-level constants (VER01_DEMO_TENANT_ENVELOPES/SPEC), mirroring red_team_probe.py's CLEAN_TENANT_ENVELOPES/SPEC shape rather than prose"
    - "A tighten-only reachability proof calls the shipped validate_tighten_only directly instead of reimplementing its per-field comparison logic"

key-files:
  created:
    - docs/guides/owner-capability-guide.md
    - apps/api/tests/unit/test_ver01_demo_tenant.py
  modified: []

key-decisions:
  - "test_demo_envelopes_are_reachable_under_tighten_only calls the real validate_tighten_only() over the 5 comparable fields the plan names (rate_limit, constraints.max_amount_cents, the two boolean gates, actor_mode) — deliberately excluding 'enabled', because the source disagrees with the plan's general framing (see Deviations)."
  - "All quoted owner-guide copy strings verified against apps/admin/app/agents/[id]/deploy/page.tsx by line number before writing, not paraphrased from 19-PATTERNS.md's summary of them."
  - "Skip-boundary tests read settings.ACTOR_SKIP_MAX_AMOUNT_CENTS at test time rather than hard-coding 500, per the plan's own instruction, so a future config change fails the suite loudly."

requirements-completed: [DOC-03, VER-01]

coverage:
  - id: D1
    description: "docs/guides/owner-capability-guide.md narrates the six capability controls, tighten-only boundary behaviour, platform defaults, and blast-radius reporting in owner-facing plain language, quoting the shipped 18-10 deploy page verbatim"
    requirement: "DOC-03"
    verification:
      - kind: other
        ref: "grep -F anchor loop over 22 literals/values + docker-command negative gate + frontmatter/length checks in docs/guides/owner-capability-guide.md (task <verify>, automated, exits DOC-03-ANCHORS-OK)"
        status: pass
    human_judgment: false
  - id: D2
    description: "apps/api/tests/unit/test_ver01_demo_tenant.py defines VER01_DEMO_TENANT_ENVELOPES/SPEC and proves the Actor skip short-circuit engages for the demo issue_refund envelope (499 cents) and not for its place_order envelope (20000 cents), pinning the strict-inequality boundary against settings.ACTOR_SKIP_MAX_AMOUNT_CENTS on both sides"
    requirement: "VER-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_ver01_demo_tenant.py — 8/8 tests pass (pytest tests/unit/test_ver01_demo_tenant.py -q)"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit — full suite 1111 passed, 8 skipped, 0 failed (baseline 1103 + 8 new)"
        status: pass
    human_judgment: false

# Metrics
duration: ~25min
completed: 2026-07-28
status: complete
---

# Phase 19 Plan 02: Owner capability guide + VER-01 demo tenant Summary

**Owner-facing capability-configuration guide quoting the shipped 18-10 admin UI verbatim, plus the VER-01 demo tenant locked as executable data with the Actor skip's strict 499/500 boundary pinned by test on both sides.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-07-28T00:23:58+02:00
- **Tasks:** 2
- **Files modified:** 2 (both new)

## Accomplishments

- `docs/guides/owner-capability-guide.md` (256 lines): plain-language narration for a non-technical business owner of the seven capability envelopes (six mutating skills + `confirm_action`), the six controls (Enabled, Rate limit, Ceiling, Requires confirmation, Requires identity verification, Actor review mode), the tighten-only rule and its exact boundary behaviour (exactly-current accepted as no-op, one cent over refused with the stored row untouched, one cent under accepted), the shipped platform-default table, blast-radius reporting (configured ceiling vs. observed maximum as two separate lines, never merged), and the envelope-drift re-acknowledgement flow — quoting six shipped UI copy strings verbatim from `apps/admin/app/agents/[id]/deploy/page.tsx` rather than paraphrasing them. Explicitly prohibits presenting a loosened control (lower Actor review, disabled identity verification) as a remedy for friction.
- `apps/api/tests/unit/test_ver01_demo_tenant.py` (338 lines, 8 tests): defines `VER01_DEMO_TENANT_ENVELOPES` (issue_refund at 499 cents — one cent below `ACTOR_SKIP_MAX_AMOUNT_CENTS`'s default of 500 — and place_order at 20 000 cents, both enabled; the remaining four mutating skills copied straight from `PLATFORM_CAPABILITY_DEFAULTS`) and `VER01_DEMO_TENANT_SPEC` (the environmental preconditions `19-05-PLAN.md`'s human UAT run depends on). Eight tests prove: the skip engages for the demo refund envelope with zero Anthropic calls; the skip does NOT engage at exactly the threshold (Haiku is called); the skip DOES engage one cent below; the skip does NOT engage when `requires_confirmation=True` even sub-threshold (both AND-terms must hold); the skip does NOT engage when `max_amount_cents` is absent from `constraints`; the demo `place_order` envelope does NOT engage the skip (making the accepted `require_human` residual gap, T-19-04, a tested fact); `499 < settings.ACTOR_SKIP_MAX_AMOUNT_CENTS` read from settings rather than hard-coded; and every demo envelope's five non-`enabled` comparable fields pass the real `validate_tighten_only` unchanged.

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the owner capability-configuration guide (DOC-03)** - `cf16cc3` (docs)
2. **Task 2: Lock and prove the VER-01 demo tenant configuration** - `b398761` (test)

_Note: Task 2 is `tdd="true"` in the plan, but the behaviour under test (`call_actor_gate`'s skip short-circuit) already ships in `actor_seam.py` from Phase 15 — this task locks and proves already-shipped behaviour as demo-tenant fixture data, not new production behaviour. There is no production implementation to add (the plan itself forbids touching `apps/api/app/`), so the RED→GREEN→REFACTOR split does not apply in its usual sense; see "TDD Gate Compliance" below._

## Files Created/Modified

- `docs/guides/owner-capability-guide.md` - DOC-03: owner-facing capability-configuration guide (256 lines)
- `apps/api/tests/unit/test_ver01_demo_tenant.py` - VER-01: demo tenant fixture data + Actor skip-boundary proof (338 lines, 8 tests)

## Decisions Made

- Owner guide follows `docs/runbooks/integration-credentials.md`'s house structure (Audience/Phase/Scope header, `---`-separated sections, bold inline call-outs, no YAML frontmatter) per `19-PATTERNS.md`, with copy sourced from `apps/admin/app/agents/[id]/deploy/page.tsx` at the exact line numbers `19-02-PLAN.md`'s `<read_first>` cited.
- `VER01_DEMO_TENANT_ENVELOPES`/`SPEC` mirror `red_team_probe.py`'s `CLEAN_TENANT_ENVELOPES`/`CLEAN_TENANT_SPEC` shape exactly, but the demo tenant is not imported from that module — it is its own named fixture, kept separate because it is a different tenant with a different purpose (VER-01's live gate, not RTX-04's clean-tenant red-team baseline).
- The tighten-only reachability test calls the shipped `validate_tighten_only` directly rather than reimplementing its per-field comparison logic in the test — this catches any future change to the comparator's rules automatically, rather than the test silently drifting from the real function.

## Deviations from Plan

### Source-vs-plan discrepancy (recorded per CLAUDE.md's "source wins" directive — not an auto-fix, a documented correction)

**1. `enabled=True` is NOT reachable via the shipped tighten-only PATCH route, contrary to `19-02-PLAN.md`'s general framing**

- **Found during:** Task 2, while designing `test_demo_envelopes_are_reachable_under_tighten_only`.
- **Plan's claim:** "Every demo envelope is at least as strict as its `PLATFORM_CAPABILITY_DEFAULTS` counterpart on the comparable fields, so the demo posture is reachable through the shipped tighten-only PATCH route rather than requiring a hand-edited row" (plan `<behavior>` block).
- **What the source actually does:** `validate_tighten_only` (`apps/api/app/services/capability_service.py`) rejects every `enabled: False -> True` transition unless the skill's own `PLATFORM_CAPABILITY_DEFAULTS` entry already ships `enabled=True`. Every one of the seven `PLATFORM_CAPABILITY_DEFAULTS` entries ships `enabled=False` (verified directly in the source), so this branch is **unconditionally unreachable** for every skill, every time — confirmed independently by `apps/api/tests/unit/test_capability_routes.py::test_patch_rejects_each_loosening_field`'s own `({"enabled": False}, {"enabled": True})` parametrize case, and by the function's own docstring: "in practice re-enabling a disabled skill is not reachable through this route — a chosen consequence, not a surprise."
- **Resolution:** `test_demo_envelopes_are_reachable_under_tighten_only` proves reachability only for the five comparable fields the plan's own `<action>` block explicitly names (`rate_limit`, `constraints.max_amount_cents`, the two boolean gates, `actor_mode`) — the plan's own field list already omits `enabled`, consistent with this finding. `VER01_DEMO_TENANT_SPEC["tighten_only_reachability_note"]` and the module docstring both record that `enabled=True` must be seeded directly (a fixture/DB row), the same way `CLEAN_TENANT_ENVELOPES` already is — not silently claimed reachable, and not silently dropped from the test.
- **Files affected:** `apps/api/tests/unit/test_ver01_demo_tenant.py` only (comment + spec field); no production file touched.
- **Committed in:** `b398761` (Task 2 commit).

---

**Total deviations:** 1 documented source-vs-plan discrepancy (no code fix required — the plan's own test-field list already matched the source; only the plan's prose framing overstated reachability).
**Impact on plan:** None on the shipped artifacts — both `<verify>` blocks pass exactly as specified. The discrepancy is a correction to the plan's narrative claim, not to any deliverable.

## TDD Gate Compliance

Task 2 carries `tdd="true"` but tests already-shipped Phase-15 behaviour (`call_actor_gate`'s skip short-circuit) as demo-tenant fixture data, with the plan itself forbidding any change under `apps/api/app/`. There is no RED phase in the conventional sense (no implementation is missing to make a test fail against), so a single `test(...)` commit was made rather than a `test` → `feat` → `refactor` sequence. All 8 named tests pass on the first run, which is expected and correct here — the behaviour under test has existed since Phase 15's `15-01-SUMMARY.md`, and this task's job is to lock it as tested fixture data, not to build new behaviour.

## Issues Encountered

None. Both `<verify>` blocks (anchor grep for Task 1, pytest for Task 2 plus the full suite) passed on first run after two markdown line-wrap fixes to the owner guide (blockquote text that wrapped across two source lines broke the `grep -F` anchor match for two of the six quoted strings — fixed by joining each quoted sentence onto one line; this is a formatting fix to the file being verified, not a deviation from the plan's content).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

Both `<verify>` blocks pass: the DOC-03 anchor gate exits `DOC-03-ANCHORS-OK`, and `pytest tests/unit/test_ver01_demo_tenant.py -q` reports 8 passed. The full unit suite is green at 1111 passed / 8 skipped / 0 failed (1103 baseline + 8 new, 0 regressions), and both `apps/api/pyproject.toml` and every file under `apps/api/app/` are untouched by this plan. `docs/guides/owner-capability-guide.md` and the two VER01 demo-tenant constants are now available for `19-05-PLAN.md`'s human UAT run (VER-01 SC2) to reference directly rather than re-deriving the demo posture from prose.

---
*Phase: 19-documentation-v1-1-verification*
*Completed: 2026-07-28*

## Self-Check: PASSED

- FOUND: docs/guides/owner-capability-guide.md
- FOUND: apps/api/tests/unit/test_ver01_demo_tenant.py
- FOUND: .planning/phases/19-documentation-v1-1-verification/19-02-SUMMARY.md
- FOUND commit: cf16cc3
- FOUND commit: b398761
- FOUND commit: 8938518
