# Project State

## Current Status

**Active Milestone:** M1 — Control Plane Skeleton
**Milestone Phase:** Phase 1 — Executing (8 plans, 7 waves) — Plan 03 complete
**Last updated:** 2026-05-12

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-12)

**Core value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.
**Current focus:** M1 Phase 1 — Executing Wave 4 (01-04: FastAPI routes — POST /tenants, POST /agents, GET /agents/{id}, GET /health)
**Previous:** Wave 3 complete — 01-03 Neon provisioning service, migration service, provision_neon + apply_migrations Celery tasks

## Milestone Progress

| Milestone | Name | Status | PRD |
|-----------|------|--------|-----|
| M1 | Control Plane Skeleton | ◐ In Progress (3/8 plans complete) | `prd-M1.md` ✓ |
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
- verify_api_key returns bool only (never raises VerifyMismatchError) — callers use if/else not try/except
- emit() copies payload before adding "at" timestamp — caller's dict is never mutated
- task_default_queue=runtime so unrouted tasks don't accidentally land in pipeline queue
- provision_neon writes agent.neon_project_id immediately after Neon API returns (idempotency save point for kill-9)
- apply_migrations uses neon_direct_connection_string (not pooled) — DDL requires non-pooled direct endpoint
- wait_for_neon_ready runs in apply_migrations before Alembic, not in provision_neon

## Performance Metrics

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 01 | 01 | ~45 min | 3 | 15 |
| 01 | 02 | ~35 min | 3 | 5 |
| 01 | 03 | ~7 min | 3 | 4 |

## Notes

- M1 PRD (`prd-M1.md`) is complete and ready for phase planning.
- M4 is the first hireable artifact — all scope decisions prioritize speed to M4.
- M6 and M7 are parallelizable — both depend only on M4, not on each other.
- Last session: 2026-05-12 — completed 01-03-PLAN.md (Neon service, migration service, provision_neon + apply_migrations Celery tasks)
