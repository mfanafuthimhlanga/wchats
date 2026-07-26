---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, control-db, pydantic-settings, blast-radius, capability-envelope]

# Dependency graph
requires:
  - phase: 21
    provides: control migration chain through 0018 (prompt_versions), ChecklistRun/CapabilityEnvelope/Tenant ORM models
provides:
  - Control migration 0019 (single control head, chains from 0018)
  - checklist_runs.envelope_hash / envelope_acknowledged_at columns (BLR-02 substrate)
  - capability_envelopes.actor_mode column + ck_capability_envelopes_actor_mode CHECK (CAP-03/CAP-04 substrate)
  - tenants.blast_radius_warn_single_cents / blast_radius_warn_hourly_cents columns (BLR-01 substrate)
  - Settings.BLAST_RADIUS_WARN_SINGLE_CENTS / _HOURLY_CENTS / _OBSERVED_WINDOW_DAYS platform defaults
affects: [18-04, 18-05, 18-07, 18-08, 18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Control-DB migration: raw op.execute() with ADD COLUMN IF NOT EXISTS, CHECK constraints added inside a DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$; block for idempotent re-run"
    - "NULL-means-platform-default: nullable per-tenant threshold columns fall back to a Settings field when NULL, mirroring tenants.daily_budget_usd from migration 0008"

key-files:
  created:
    - apps/api/alembic/versions/0019_blast_radius_capability_v2.py
    - apps/api/tests/unit/test_migration_0019.py
  modified:
    - apps/api/app/models/capability_envelope.py
    - apps/api/app/models/checklist_run.py
    - apps/api/app/models/tenant.py
    - apps/api/app/core/config.py

key-decisions:
  - "actor_mode defaults to 'always-on' (NOT NULL DEFAULT), the strictest mode — an unset row never silently means 'no Actor review' (T-18-CAP-01)"
  - "envelope_hash / envelope_acknowledged_at are nullable on checklist_runs — historical runs predate the hash; NULL must be read as drift, never as a match (18-07's contract)"
  - "blast_radius_warn_single_cents / blast_radius_warn_hourly_cents are nullable on tenants — NULL means 'use the platform default in settings', mirroring tenants.daily_budget_usd"
  - "actor_mode domain constraint wrapped in a DO block catching duplicate_object since Postgres has no ADD CONSTRAINT IF NOT EXISTS"

patterns-established:
  - "Pattern: idempotent control migration via raw op.execute() ADD COLUMN IF NOT EXISTS + DO-block-guarded CHECK constraint, following 0018_prompt_versions.py / 0016_pending_confirmations_dedup_index.py house convention"

requirements-completed: [BLR-02, CAP-03, CAP-04]

coverage:
  - id: D1
    description: "Control migration 0019 chains from 0018 and is the single control head, with all five columns and the actor_mode CHECK declared via idempotent guards"
    requirement: "BLR-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_migration_down_revision_chains_from_0018"
        status: pass
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_migration_source_adds_all_five_columns"
        status: pass
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_migration_source_guards_actor_mode_constraint"
        status: pass
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_downgrade_scoped_to_0019_additions"
        status: pass
    human_judgment: false
  - id: D2
    description: "capability_envelopes.actor_mode, checklist_runs.envelope_hash/envelope_acknowledged_at, tenants.blast_radius_warn_single_cents/blast_radius_warn_hourly_cents reachable as typed ORM attributes"
    requirement: "CAP-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_orm_capability_envelope_has_actor_mode"
        status: pass
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_orm_capability_envelope_actor_mode_server_default_always_on"
        status: pass
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_orm_checklist_run_has_envelope_columns"
        status: pass
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_orm_tenant_has_blast_radius_threshold_columns"
        status: pass
    human_judgment: false
  - id: D3
    description: "Three blast-radius platform-default settings readable from Settings with zero environment configuration"
    requirement: "CAP-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_migration_0019.py::test_settings_expose_blast_radius_defaults"
        status: pass
    human_judgment: false
  - id: D4
    description: "Live-DB upgrade/downgrade/re-upgrade roundtrip against a real control DB"
    verification:
      - kind: integration
        ref: "tests/unit/test_migration_0019.py::test_migration_0019_db_roundtrip (INTEGRATION_TESTS_ENABLED-gated, skipped by default)"
        status: unknown
    human_judgment: true
    rationale: "No live Neon control DB currently holds any v1.2 migration — deferred to plan 18-11's autonomous:false live gate, matching the Phase 13/15/16/17 precedent."

# Metrics
duration: ~20min
completed: 2026-07-26
status: complete
---

# Phase 18 Plan 01: Schema Foundation Summary

**Control migration 0019 (single head, chains from 0018) plus three ORM model extensions and a Settings block — the schema every later Phase 18 plan reads for envelope-hash acknowledgement, per-skill Actor mode, and tenant blast-radius warning thresholds.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-26T~21:52Z
- **Completed:** 2026-07-26T22:12:41+02:00
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- `0019_blast_radius_capability_v2.py`: five `ADD COLUMN IF NOT EXISTS` statements plus a `DO $$ ... EXCEPTION WHEN duplicate_object` guarded `ck_capability_envelopes_actor_mode` CHECK constraint; downgrade scoped strictly to what 0019 added
- `CapabilityEnvelope.actor_mode`, `ChecklistRun.envelope_hash` / `envelope_acknowledged_at`, `Tenant.blast_radius_warn_single_cents` / `blast_radius_warn_hourly_cents` added as typed `mapped_column` attributes
- `Settings.BLAST_RADIUS_WARN_SINGLE_CENTS` (50000), `_HOURLY_CENTS` (200000), `_OBSERVED_WINDOW_DAYS` (7) — readable with zero environment configuration
- `tests/unit/test_migration_0019.py` — 13 tests (12 unit + 1 `INTEGRATION_TESTS_ENABLED`-gated roundtrip): revision chain, all five columns, guarded CHECK, three legal `actor_mode` shapes, scoped downgrade, ORM mirror, settings defaults

## Task Commits

Each task was committed atomically:

1. **Task 1: Control migration 0019 + three ORM model extensions** - `70c7c0a` (feat)
2. **Task 2: Blast-radius platform-default settings + migration 0019 test module** - `ac56741` (feat)

_No plan-metadata commit yet — this SUMMARY.md and STATE.md updates are committed separately per the final_commit step._

## Files Created/Modified
- `apps/api/alembic/versions/0019_blast_radius_capability_v2.py` - control migration adding the five Phase 18 columns + `actor_mode` CHECK constraint
- `apps/api/app/models/capability_envelope.py` - `actor_mode` typed column + docstring note on envelope-hash input scope
- `apps/api/app/models/checklist_run.py` - `envelope_hash` / `envelope_acknowledged_at` typed columns
- `apps/api/app/models/tenant.py` - `Integer` import + `blast_radius_warn_single_cents` / `blast_radius_warn_hourly_cents` typed columns
- `apps/api/app/core/config.py` - three blast-radius platform-default settings fields
- `apps/api/tests/unit/test_migration_0019.py` - new test module (source/ORM/settings assertions + gated roundtrip)

## Decisions Made
- `actor_mode` server default is `'always-on'` (the strictest mode), not any looser default — an unset row must never silently mean "no Actor review" (T-18-CAP-01). This is CLAUDE.md-consistent Rule-2-shaped correctness, not an addition beyond plan scope: the plan itself specified this default.
- Both `tenants` threshold columns are nullable with NULL meaning "fall back to `settings`" rather than defaulting to a fixed cents value — this keeps a single source of truth for the platform default (`Settings`) instead of duplicating it into every tenant row at creation time.
- Followed plan as specified for all column shapes, constraint text, and test structure — no deviation from the `18-01-PLAN.md` acceptance criteria.

## Deviations from Plan

None - plan executed exactly as written. One self-correction during authoring: an early draft of the migration's `downgrade()` comment named the literal strings `approved_by` and `daily_budget_usd` to explain scope (in prose), which would have failed the acceptance criterion "`downgrade()` body ... does not contain the strings `approved_by` or `daily_budget_usd`" — reworded the comment to describe the same scope constraint without using those literal identifiers, before committing. Not logged as a Rule-N deviation because it never left the working tree in the failing form; caught during self-verification of the plan's own acceptance criteria, not a runtime bug or missing functionality.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Schema foundation is in place for 18-04 (CAP-03 tighten-only PATCH route + `capability_service.py`), 18-05 (BLR-01 blast-radius collector), 18-07 (BLR-02 envelope-hash compute/compare + `approve-deployment` 422), and 18-08 (admin UI wiring).
- Full unit suite: 982 passed / 8 skipped / 0 failed (baseline was 970 passed / 7 skipped / 0 failed — net +12 passing, +1 skip from the new gated roundtrip test).
- Live-DB roundtrip verification remains deferred to plan 18-11 (`autonomous: false`) per `18-01-PLAN.md`'s own stated constraint — no live Neon control DB currently holds any v1.2 migration.
- No blockers for the next plan in the wave sequence.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-26*

## Self-Check: PASSED

- FOUND: apps/api/alembic/versions/0019_blast_radius_capability_v2.py
- FOUND: apps/api/tests/unit/test_migration_0019.py
- FOUND: .planning/phases/18-blast-radius-gate-capability-admin-ui-transaction-red-team-i/18-01-SUMMARY.md
- FOUND: commit 70c7c0a
- FOUND: commit ac56741
