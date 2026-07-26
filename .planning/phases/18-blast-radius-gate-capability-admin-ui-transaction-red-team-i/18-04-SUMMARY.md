---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 04
subsystem: api
tags: [capability-envelope, tighten-only, sha256, blast-radius, pure-service]

# Dependency graph
requires:
  - phase: 18-01
    provides: "capability_envelopes.actor_mode column + ck_capability_envelopes_actor_mode CHECK; checklist_runs.envelope_hash/envelope_acknowledged_at columns"
provides:
  - "capability_service.PLATFORM_CAPABILITY_DEFAULTS — 7-entry platform-default dict (six mutating skills + confirm_action), fail-closed"
  - "capability_service.HASHED_ENVELOPE_FIELDS + canonical_envelope_payload/canonical_envelope_hash — BLR-02's single canonical envelope-hash implementation"
  - "capability_service.validate_tighten_only — CAP-03's server-side, below-the-route tighten-only comparator"
  - "capability_service.envelope_drift — CAP-04's missing-acknowledgement-is-drift predicate"
  - "capability_service.parse_actor_mode / ACTOR_MODE_RE — actor_mode tightness ordinal, byte-matched to the DB CHECK domain"
affects: [18-07, 18-08, 18-10, 18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure fail-closed comparator returning a reason string (never raising), mirroring enforcement.check_capability_access's early-return-on-denial shape — the route layer converts the string into a 422"
    - "Canonical hash via sorted-by-key JSON projection + sort_keys + whitespace-free separators, so row order and DB-managed columns (id/agent_id/updated_at) never affect the hash"

key-files:
  created:
    - apps/api/app/services/capability_service.py
    - apps/api/tests/unit/test_capability_service.py
  modified: []

key-decisions:
  - "PLATFORM_CAPABILITY_DEFAULTS and HASHED_ENVELOPE_FIELDS live in capability_service.py, not config.py — OD-3 (Settings is a flat scalar surface; no env override wanted for a nested per-skill dict)"
  - "validate_tighten_only takes (current, proposed, platform_defaults) with no FastAPI/DB/auth objects — enforcement lives below the route so a direct API call is rejected identically to a UI-originated one (T-18-CAP-02)"
  - "actor_mode='off' is rejected unconditionally on any skill whose platform default is mutating, independent of the ordinal tightness comparison — off is not a valid state at any tightness level for a mutating skill (PRD S4.5)"
  - "A present 'constraints' key with an absent or None max_amount_cents is read identically to an explicit null — both are loosen_max_amount_removed against a non-None current"
  - "This module ships with zero callers by design (cross-wave seam ownership) — 18-07 wires canonical_envelope_hash/envelope_drift, 18-08 wires validate_tighten_only"

patterns-established:
  - "Pattern: tighten-only field comparator — each of the six comparable fields is its own early-return branch, only fields present in the proposed dict are examined (partial-PATCH semantics), and a missing key is never read as a loosen"

requirements-completed: [CAP-03, CAP-04, BLR-02]

coverage:
  - id: D1
    description: "A loosening change on any of the six comparable fields (enabled, rate_limit, max_amount_cents, requires_confirmation, requires_identity_verification, actor_mode) is rejected with a distinct reason string; the tightening direction on each is accepted; a mixed tighten+loosen payload surfaces the loosening field's reason; a no-op proposed dict returns None"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_service.py::test_validate_tighten_only"
        status: pass
    human_judgment: false
  - id: D2
    description: "Tighten-only enforcement lives below the route: validate_tighten_only's signature carries no FastAPI/DB-session/auth objects, its source raises no HTTPException and names no status_code, and the module imports no fastapi symbol"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_service.py::test_tighten_only_enforced_below_route"
        status: pass
    human_judgment: false
  - id: D3
    description: "envelope_drift treats a missing/empty acknowledged hash or a missing live hash as drift; identical non-empty hashes are not drift"
    requirement: "CAP-04"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_service.py::test_envelope_drift_flag"
        status: pass
    human_judgment: false
  - id: D4
    description: "canonical_envelope_hash is order-independent, stable across a no-op re-save (id/agent_id/updated_at excluded), sensitive to every one of the 7 hashed fields (including a nested constraints key), deterministic on an empty row list, tolerant of a row missing actor_mode, and its payload contains no whitespace"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_service.py::TestCanonicalEnvelopeHash (6 tests, incl. test_hash_changes_on_each_semantic_field parametrised over all 7 HASHED_ENVELOPE_FIELDS)"
        status: pass
    human_judgment: false

# Metrics
duration: ~25min
completed: 2026-07-26
status: complete
---

# Phase 18 Plan 04: Capability Service — Tighten-Only Comparator, Envelope Hash, Drift Predicate Summary

**One pure, synchronous module (`capability_service.py`) implementing CAP-03's per-field tighten-only comparator, BLR-02's single canonical envelope-hash function, and CAP-04's missing-acknowledgement-is-drift predicate — deliberately caller-free, with its own 15-test unit suite covering every comparable field's loosen/tighten direction, hash stability under a no-op re-save, and drift semantics.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-26T~21:25Z
- **Completed:** 2026-07-26T21:48:02Z
- **Tasks:** 2
- **Files modified:** 2 (both new)

## Accomplishments
- `PLATFORM_CAPABILITY_DEFAULTS`: 7 entries (six mutating skills + `confirm_action`), every entry carrying all seven semantic fields plus `mutating`; every entry fail-closed (`enabled=False`, `actor_mode="always-on"`); `confirm_action` fully specified concretely (`mutating=False`, `constraints={}`, `rate_limit="20/hour"`)
- `HASHED_ENVELOPE_FIELDS` (7 fields, fixed order) + `canonical_envelope_payload`/`canonical_envelope_hash`: sorted-by-`skill`, whitespace-free, sha256 hex digest — the single canonical hash implementation both the checklist task (18-07) and the approve route (18-07) will call, so they cannot disagree
- `ACTOR_MODE_RE` (byte-identical to `ck_capability_envelopes_actor_mode`'s regex term) + `parse_actor_mode`: `(tier, rate)` tightness-ordinal pair, raising `ValueError` on any out-of-domain value
- `validate_tighten_only`: per-field fail-closed comparator covering all six comparable fields, each its own early-return branch, only fields present in `proposed` examined, every rejection logged via `capability.tighten_only_rejected` before returning
- `envelope_drift`: `True` whenever the acknowledged hash is missing/empty or the live hash is missing or the two differ
- `tests/unit/test_capability_service.py`: 15 tests — the three fixed T-18-CAP-01/02/03 node ids plus a 6-test `TestCanonicalEnvelopeHash` class for BLR-02, all pure-function, 2.25s total

## Task Commits

Each task was committed atomically:

1. **Task 1: capability_service.py — platform defaults, canonical hash, tighten-only comparator, drift predicate** - `c9aeb58` (feat)
2. **Task 2: test_capability_service.py — per-field tighten-only, hash stability, drift** - `38459a7` (test)

_No plan-metadata commit yet — this SUMMARY.md and STATE.md updates are committed separately per the final_commit step._

## Files Created/Modified
- `apps/api/app/services/capability_service.py` - pure service module: `PLATFORM_CAPABILITY_DEFAULTS`, `HASHED_ENVELOPE_FIELDS`, `ACTOR_MODE_RE`, `parse_actor_mode`, `canonical_envelope_payload`, `canonical_envelope_hash`, `validate_tighten_only`, `envelope_drift`
- `apps/api/tests/unit/test_capability_service.py` - 15 tests: `test_validate_tighten_only`, `test_tighten_only_enforced_below_route`, `test_envelope_drift_flag`, `TestCanonicalEnvelopeHash` (6 methods)

## Decisions Made
- `PLATFORM_CAPABILITY_DEFAULTS` and `HASHED_ENVELOPE_FIELDS` live in `capability_service.py` per OD-3/OD-2, not `config.py` — plan-specified, no deviation.
- `validate_tighten_only`'s `enabled` branch reads the platform default per-skill (`platform_defaults.get(skill, {})`), so a `None`/unknown-skill lookup defaults to `{}` and fails closed (`loosen_enabled`) rather than raising `KeyError` — same fail-closed convention as `enforcement.check_capability_access`, not a deviation from the plan's specified behavior (every platform default has `enabled=False`, so this path is only reachable for a genuinely unknown skill name).
- `actor_mode`'s "off requires non-mutating" check runs unconditionally before the ordinal-tightness comparison, so it fires even in the (currently unreachable, since the DB default is `always-on`) case where `current` itself is already `"off"` on a mutating skill — matches the plan's explicit "off is not a valid state at any tightness level, not merely one the owner may not reach right now."
- Followed the plan's exact field list, per-field rules, docstring content (OD-2/OD-3/OD-3b), and test-name contract — no deviation from `18-04-PLAN.md`'s acceptance criteria.

## Deviations from Plan

None — plan executed exactly as written. One self-correction during authoring, not logged as a Rule-N deviation because it never reached a commit in a failing form: my first draft of `test_tighten_only_enforced_below_route` asserted the literal string `"fastapi"` was absent anywhere in the module source, which false-failed on the module's own docstring prose ("no FastAPI, no AsyncSession..."). Reworded the assertion to check for `"import fastapi"`/`"from fastapi"` specifically (matching the plan's actual acceptance criterion, "no `fastapi` import") before running the suite the first time, and separately removed the literal string `"AsyncSession"` from a test docstring after the plan's own acceptance-criteria grep (`grep -n 'psycopg2\|redis\|httpx\|AsyncSession'`) caught it in a documentation comment, not code.

## Issues Encountered
None. Standalone module verification required a scratch script (in the session scratchpad, not committed) supplying the same `os.environ.setdefault(...)` preamble `tests/conftest.py` already provides globally for pytest — `python -c` invocations outside pytest don't get that preamble automatically, since `Settings()` requires `PLATFORM_CREDENTIAL_KEY` and other env vars with no defaults. This did not affect the actual test suite, which resolves the preamble via `conftest.py` as designed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `capability_service.py` is caller-free by design (explicit cross-wave seam ownership per the plan's objective and this plan's own module docstring) — plan 18-07 must wire `canonical_envelope_hash`/`envelope_drift` (checklist-time hash persistence, the approve-time 422, drift on the checklist read) and plan 18-08 must wire `validate_tighten_only` (the PATCH route). This is not incomplete work; it is the explicit correction of the Phase 21 `promote_trace_to_scenario` failure pattern recorded in `.planning/.continue-here.md`.
- Full unit suite: 1029 passed / 8 skipped / 0 failed (baseline before this plan was 1014 passed / 8 skipped / 0 failed — net +15 passing, 0 skips added, 0 failures).
- `apps/api/pyproject.toml` unchanged (`git diff --exit-code` exits 0) — no new dependency.
- No blockers for the next plan in the wave sequence.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-26*

## Self-Check: PASSED

- FOUND: apps/api/app/services/capability_service.py
- FOUND: apps/api/tests/unit/test_capability_service.py
- FOUND: .planning/phases/18-blast-radius-gate-capability-admin-ui-transaction-red-team-i/18-04-SUMMARY.md
- FOUND: commit c9aeb58
- FOUND: commit 38459a7
- FOUND: commit c239183
