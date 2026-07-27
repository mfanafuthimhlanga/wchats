---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 08
subsystem: api
tags: [capability-envelope, tighten-only, fastapi, idor, pydantic]

# Dependency graph
requires:
  - phase: 18-04
    provides: "capability_service.validate_tighten_only, PLATFORM_CAPABILITY_DEFAULTS, ACTOR_MODE_RE — shipped deliberately caller-free"
  - phase: 18-01
    provides: "capability_envelopes.actor_mode column + ck_capability_envelopes_actor_mode CHECK"
provides:
  - "GET /api/v1/agents/{agent_id}/capability-envelopes — stable 7-entry read (platform_default + mutating attached, synthesised for skills with no stored row)"
  - "PATCH /api/v1/agents/{agent_id}/capability-envelopes/{skill} — the CAP-03 tighten-only write gate, the only caller of validate_tighten_only"
  - "app/schemas/capability.py — CapabilityEnvelopeUpdate/Response/ListResponse"
affects: [18-10, 18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-wave seam closure: this plan is the sole call site for 18-04's validate_tighten_only, mirroring how 18-07 closed the canonical_envelope_hash/envelope_drift seam — each later plan owns its own wiring and its own acceptance criterion proving the call site exists"
    - "Comparator-before-mutation ordering: validate_tighten_only is called before any db.add/attribute-assignment/db.commit in the PATCH handler; a non-None reason returns a 422 with the transaction untouched"

key-files:
  created:
    - apps/api/app/schemas/capability.py
    - apps/api/app/api/v1/capability_envelopes.py
    - apps/api/tests/unit/test_capability_routes.py
  modified:
    - apps/api/app/main.py

key-decisions:
  - "Tighten-only direction validation lives entirely in capability_service (OD-3), never in the Pydantic schema — CapabilityEnvelopeUpdate's field_validators check shape only (rate_limit parses, max_amount_cents is a non-negative int or null, actor_mode is in-domain) since a bare validator cannot see the current DB row tightness is relative to"
  - "GET returns exactly len(PLATFORM_CAPABILITY_DEFAULTS) entries (7) always — a skill with no stored row is synthesised from the platform default with enabled=False and updated_at=None, giving the UI's zero-envelope-rows empty state a definite server contract"
  - "PATCH on an absent row treats the platform default as the comparison baseline ('current'), not an empty dict — enforces 'never loosen beyond platform defaults' on first write, not only on update"
  - "An empty proposed body (model_dump(exclude_unset=True) == {}) is a 200 no-op that skips validate_tighten_only entirely and never touches updated_at or db.commit — a no-op PATCH is not an error"
  - "CapabilityEnvelopeUpdate uses extra='forbid' — a typo'd or unknown field name is a 422, not a silently dropped no-op, on an authorization surface"
  - "platform_default dict copied (not referenced) when building the 'current' comparison dict for a first write, to guard against any future in-place mutation of the shared PLATFORM_CAPABILITY_DEFAULTS constant"

patterns-established:
  - "Pattern: route-level 404-not-403 IDOR guard (_get_owned_agent) copied verbatim from prompt_versions.py, identical detail string on both the missing-agent and foreign-agent branches"

requirements-completed: [CAP-03]

coverage:
  - id: D1
    description: "An owner can read every per-skill capability envelope for their agent (7 stable entries), with the platform default and mutating flag shown alongside the current value"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestListCapabilityEnvelopes::test_list_returns_an_entry_for_every_platform_skill"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestListCapabilityEnvelopes::test_list_entries_carry_platform_default_and_mutating"
        status: pass
    human_judgment: false
  - id: D2
    description: "A PATCH that loosens any field on any of the six comparable fields is rejected with HTTP 422 and the rejection reason, and no row is written; a PATCH that tightens is written and the response reflects the stored row; an empty PATCH body is a 200 no-op"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_rejects_loosen_returns_422"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_rejects_each_loosening_field (6 cases)"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_accepts_tighten_returns_200"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_empty_patch_body_is_a_noop_200"
        status: pass
    human_judgment: false
  - id: D3
    description: "A PATCH or GET for an agent belonging to another tenant returns 404, not 403 and not 422; ownership is checked before the body is examined"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_foreign_agent_returns_404"
        status: pass
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestListCapabilityEnvelopes::test_list_foreign_agent_returns_404"
        status: pass
    human_judgment: false
  - id: D4
    description: "An agent with no envelope row for a skill can have one created only at or tighter than the platform default; the tighten-only decision is made by the service, calling validate_tighten_only before any ORM mutation"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_first_write_compared_against_platform_default"
        status: pass
      - kind: other
        ref: "grep -c 'validate_tighten_only' apps/api/app/api/v1/capability_envelopes.py (>=2: import + call) and line-order inspection (comparator precedes every db.add/attribute-assignment/db.commit)"
        status: pass
    human_judgment: false

# Metrics
duration: ~15min
completed: 2026-07-27
status: complete
---

# Phase 18 Plan 08: Capability-Envelope Read + Tighten-Only PATCH Routes Summary

**New `GET`/`PATCH /api/v1/agents/{agent_id}/capability-envelopes` route file wiring plan 18-04's `validate_tighten_only` as the server-side write gate — the comparator now has its first and only caller, closing the cross-wave seam by the same pattern 18-07 used for the envelope hash.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-27T~01:53Z
- **Completed:** 2026-07-27T02:08:42Z
- **Tasks:** 3
- **Files modified:** 4 (3 new, 1 extended)

## Accomplishments
- `app/schemas/capability.py`: `CapabilityEnvelopeUpdate` (all-optional PATCH body, `extra="forbid"`, shape-only `field_validator`s reusing `_parse_rate_limit` and `ACTOR_MODE_RE` — direction validation deliberately absent, per OD-3), `CapabilityEnvelopeResponse` (carries `platform_default` + `mutating`), `CapabilityEnvelopeListResponse`
- `app/api/v1/capability_envelopes.py`: `_get_owned_agent` IDOR guard copied verbatim from `prompt_versions.py` (404 on both branches, identical detail string); `GET` returns a stable 7-entry list (one per `PLATFORM_CAPABILITY_DEFAULTS` key) synthesising a `enabled=False`/`updated_at=None` entry for any skill with no stored row; `PATCH` is the write gate — unknown skill 404, first-write baseline is the platform default (not an empty dict), empty body is a 200 no-op, `validate_tighten_only` runs before any `db.add`/attribute-assignment/`db.commit` and converts a non-`None` reason into a 422 with the transaction untouched
- Registered `capability_envelopes.router` in `app/main.py` immediately after `prompt_versions.router`
- `tests/unit/test_capability_routes.py`: 18 route-level tests through `ASGITransport(app=app)` — every loosening direction on all six comparable fields proven rejected with no `db.commit` awaited (the load-bearing assertion, since a route that wrote then raised would still return 422), the tightening direction proven to write, a first write proven compared against the platform ceiling in both directions, and the foreign-agent 404 test deliberately sends a loosening body so a handler checking the body before ownership would fail it

## Task Commits

Each task was committed atomically:

1. **Task 1: Pydantic schemas for the capability-envelope read and partial update** - `5decd3d` (feat)
2. **Task 2: capability_envelopes route file + registration in main.py** - `d926ad1` (feat)
3. **Task 3: Route-level tests for the read, the tighten path, every loosen rejection, and IDOR** - `61a9e4d` (test)

## Files Created/Modified
- `apps/api/app/schemas/capability.py` - `CapabilityEnvelopeUpdate`, `CapabilityEnvelopeResponse`, `CapabilityEnvelopeListResponse`
- `apps/api/app/api/v1/capability_envelopes.py` - `_get_owned_agent`, `_envelope_to_dict`, `list_capability_envelopes`, `patch_capability_envelope`
- `apps/api/app/main.py` - added `capability_envelopes` to the v1 import line and its `include_router` registration
- `apps/api/tests/unit/test_capability_routes.py` - `TestListCapabilityEnvelopes` (3 tests), `TestPatchCapabilityEnvelope` (10 test methods, 15 cases including the 6-case parametrised loosening test)

## Decisions Made
- Followed the plan's exact task order, field list, per-field rules, and test-name contract — no deviation from `18-08-PLAN.md`'s acceptance criteria.
- Deep-copied nested dict values (`constraints`) when building the "current" comparison state from `PLATFORM_CAPABILITY_DEFAULTS` for a first write, so no code path can accidentally mutate the shared platform-defaults constant through a returned reference. Not called for by the plan text explicitly but consistent with the plan's own "never a second implementation" framing for the platform-defaults source of truth — recorded here as a defensive addition, not a deviation from any stated behavior.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None. `db.execute(...)` in the mock DB test helper needed to support two different SQLAlchemy result access shapes (`.scalars().all()` for the GET list query, `.scalar_one_or_none()` for the PATCH single-row lookup) on the same mocked result object — resolved by scripting both on one `MagicMock` result and letting each test only exercise the access path its route actually uses, following `test_deployment_routes.py`'s existing `_make_mock_db` convention rather than inventing a new one.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `validate_tighten_only` now has exactly one caller (this plan), asserted structurally by the acceptance-criteria greps and by every rejection/acceptance test in `test_capability_routes.py` — the cross-wave seam 18-04 shipped caller-free is closed.
- Plan 18-10 (admin UI) can now call `GET`/`PATCH /api/v1/agents/{agent_id}/capability-envelopes` directly — the response shape (`platform_default` + `mutating` on every entry) was built specifically so the UI never needs a second request or a duplicated TypeScript copy of the platform-defaults table.
- No route exists for `pending_confirmations` (OQ-1, deliberately out of scope) — `grep -n 'pending_confirmations\|pending-confirmations' apps/api/app/api/v1/capability_envelopes.py` returns nothing.
- Full unit suite: 1092 passed / 8 skipped / 0 failed (baseline before this plan was 1074 passed / 8 skipped / 0 failed — net +18 passing, 0 skips added, 0 failures).
- `apps/api/pyproject.toml` unchanged (`git diff --exit-code` exits 0) — no new dependency.
- No blockers for the next plan in the wave sequence.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-27*

## Self-Check: PASSED

- FOUND: apps/api/app/schemas/capability.py
- FOUND: apps/api/app/api/v1/capability_envelopes.py
- FOUND: apps/api/tests/unit/test_capability_routes.py
- FOUND: commit 5decd3d
- FOUND: commit d926ad1
- FOUND: commit 61a9e4d
