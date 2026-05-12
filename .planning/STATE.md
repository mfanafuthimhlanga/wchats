# Project State

## Current Status

**Active Milestone:** M1 — Control Plane Skeleton
**Milestone Phase:** Phase 1 — Executing (8 plans, 7 waves) — Plan 01 complete
**Last updated:** 2026-05-12

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-12)

**Core value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.
**Current focus:** M1 Phase 1 — Executing Wave 2 (01-02: Security helpers, emit() helper, Celery app)
**Previous:** Wave 1 complete — 01-01 project skeleton, ORM models, Alembic migrations

## Milestone Progress

| Milestone | Name | Status | PRD |
|-----------|------|--------|-----|
| M1 | Control Plane Skeleton | ◐ In Progress (1/8 plans complete) | `prd-M1.md` ✓ |
| M2 | Ingestion Pipeline | ○ Pending | `prd-M2.md` (TBD) |
| M3 | Hybrid Retrieval | ○ Pending | `prd-M3.md` (TBD) |
| M4 | Reasoning Engine + Widget | ○ Pending | `prd-M4.md` (TBD) |
| M5 | Validation Chain | ○ Pending | `prd-M5.md` (TBD) |
| M6 | Eval System | ○ Pending | `prd-M6.md` (TBD) |
| M7 | Red Team | ○ Pending | `prd-M7.md` (TBD) |
| M8 | Pre-deployment Checklist | ○ Pending | `prd-M8.md` (TBD) |
| M9 | Retrieval Strategy Synthesis | ○ Pending | `prd-M9.md` (TBD) |
| M10 | Maintenance + Observability | ○ Pending | `prd-M10.md` (TBD) |

## Key Decisions

- redis==6.4.0 (not 7.4.0): celery[redis]==5.6.3 requires kombu 5.6.x which constrains redis<6.5; redis 7.4.0 is incompatible
- Tenant ORM attribute api_key_hash (DB column: api_key) prevents plaintext confusion in code
- target_metadata=None in alembic_tenant/env.py: no ORM models for tenant schema in M1; all DDL via raw SQL

## Performance Metrics

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 01 | 01 | ~45 min | 3 | 15 |

## Notes

- M1 PRD (`prd-M1.md`) is complete and ready for phase planning.
- M4 is the first hireable artifact — all scope decisions prioritize speed to M4.
- M6 and M7 are parallelizable — both depend only on M4, not on each other.
- Last session: 2026-05-12 — completed 01-01-PLAN.md (project skeleton, ORM models, Alembic migrations)
