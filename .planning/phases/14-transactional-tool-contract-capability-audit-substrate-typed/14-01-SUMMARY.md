---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "01"
subsystem: transactional-substrate
tags: [migration, orm, capability-envelopes, audit, idempotency, postgresql]
dependencies:
  requires: [0013_alert_tenant_id]
  provides: [capability_envelopes, tool_calls_audit, pending_confirmations, tool_idempotency_keys]
  affects: [apps/api/alembic, apps/api/app/models]
tech_stack:
  added: []
  patterns:
    - Alembic op.execute() raw SQL with IF NOT EXISTS guards (safe re-run)
    - SQLAlchemy 2.x Mapped/mapped_column declarative ORM style
    - UniqueConstraint at table_args level with canonical named constraints
    - server_default=text("false") for fail-closed boolean columns
key_files:
  created:
    - apps/api/alembic/versions/0014_transactional_substrate.py
    - apps/api/app/models/capability_envelope.py
    - apps/api/app/models/tool_calls_audit.py
    - apps/api/app/models/pending_confirmation.py
    - apps/api/app/models/tool_idempotency_key.py
    - apps/api/tests/unit/test_migration_0014.py
  modified:
    - apps/api/app/models/__init__.py
decisions:
  - "[14-01] Migration uses op.execute() raw SQL (same as 0013) with IF NOT EXISTS guards for idempotent re-runs"
  - "[14-01] arguments/capability_snapshot are nullable in tool_calls_audit (plan spec overrides PRD NOT NULL) — capture may fail before schema validation"
  - "[14-01] expires_at is nullable in pending_confirmations — NULL means no deadline configured (plan spec overrides PRD NOT NULL)"
  - "[14-01] actor_decision/actor_rationale are NOT NULL DEFAULT '' (Phase 14 writes empty string; Phase 15 fills the body)"
  - "[14-01] No ORM FK relationships declared (agent_id is a plain UUID column) — avoids cross-table teardown complexity in tests"
  - "[14-01] DB roundtrip test guarded by INTEGRATION_TESTS_ENABLED=1 — skipped in unit mode; source-level assertions cover correctness in CI"
metrics:
  duration: "~9 min"
  completed_date: "2026-06-29"
  tasks_completed: 2
  files_changed: 7
status: complete
---

# Phase 14 Plan 01: Transactional Substrate — Summary

Control-DB Alembic migration 0014 creating four authorization/audit tables, plus four SQLAlchemy ORM models with fail-closed defaults and durable UNIQUE constraints.

## What Was Built

### Migration 0014 (`0014_transactional_substrate.py`)

Single Alembic migration (revision=`"0014"`, down_revision=`"0013"`) creating four control-DB tables:

| Table | Requirement | Key Constraint |
|-------|-------------|----------------|
| `capability_envelopes` | CAP-01 | `UNIQUE(agent_id, skill)` named `uq_capability_envelopes_agent_skill`; `enabled DEFAULT false` (fail-closed) |
| `tool_calls_audit` | AUD-01 | Index on `(agent_id, skill)`; `actor_decision/rationale NOT NULL DEFAULT ''` |
| `pending_confirmations` | AUD-02 | Index on `agent_id` |
| `tool_idempotency_keys` | TXN-02 | `UNIQUE(agent_id, skill, idempotency_key)` named `uq_tool_idempotency_keys` — durable double-execute guard |

Migration uses `op.execute()` raw SQL with `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` guards (consistent with migration 0013, safe for re-runs). `downgrade()` drops all four tables in reverse order.

### ORM Models

Four SQLAlchemy 2.x declarative models mirroring `agent.py` style:

- `CapabilityEnvelope` — `__tablename__ = "capability_envelopes"`, `UniqueConstraint("agent_id", "skill", name="uq_capability_envelopes_agent_skill")`, `enabled server_default text("false")`
- `ToolCallsAudit` — `__tablename__ = "tool_calls_audit"`, `actor_decision`/`actor_rationale` `server_default text("''")`
- `PendingConfirmation` — `__tablename__ = "pending_confirmations"`
- `ToolIdempotencyKey` — `__tablename__ = "tool_idempotency_keys"`, `UniqueConstraint("agent_id", "skill", "idempotency_key", name="uq_tool_idempotency_keys")`

All four registered in `app/models/__init__.py` (`import` + `__all__`) so `Base.metadata` includes the tables for Alembic autogenerate and ORM operations.

### Tests (`test_migration_0014.py`)

11 unit tests, 1 integration test (skipped unless `INTEGRATION_TESTS_ENABLED=1`):
- 6 migration source assertions (file exists, revision, down_revision, constraint names, table names, fail-closed default)
- 5 ORM model assertions (imports, Base.metadata registration, both UNIQUE constraints, fail-closed ORM default)
- 1 DB roundtrip (guarded — verifies tables+constraints in a real DB after migration)

## Deviations from Plan

None — plan executed exactly as written. Minor PRD→plan spec deltas are documented in decisions (nullable arguments/capability_snapshot in tool_calls_audit; nullable expires_at in pending_confirmations — plan spec governs over PRD for execution).

## Threat Flag Mitigations

All four STRIDE mitigations from the plan's threat_model were implemented:

| Threat ID | Mitigation Status |
|-----------|------------------|
| T-14-01-01 (enabled fail-open) | `capability_envelopes.enabled DEFAULT false` in migration SQL and `server_default=text("false")` in ORM |
| T-14-01-02 (idempotency UNIQUE) | `CONSTRAINT uq_tool_idempotency_keys UNIQUE (agent_id, skill, idempotency_key)` in migration + ORM |
| T-14-01-03 (cross-tenant audit) | `agent_id` on every table; no tenant PII columns; no cross-tenant FK |
| T-14-01-04 (wrong migration lineage) | `down_revision = "0013"` pinned; source assertion test verifies it |
| T-14-01-SC (supply chain) | No new packages — alembic/sqlalchemy/psycopg2 already pinned |

## Known Stubs

None — this plan ships schema and ORM structure only. No data flow, no runtime logic, no UI stubs.

## Self-Check

### Files Exist

- `apps/api/alembic/versions/0014_transactional_substrate.py` — FOUND
- `apps/api/app/models/capability_envelope.py` — FOUND
- `apps/api/app/models/tool_calls_audit.py` — FOUND
- `apps/api/app/models/pending_confirmation.py` — FOUND
- `apps/api/app/models/tool_idempotency_key.py` — FOUND
- `apps/api/tests/unit/test_migration_0014.py` — FOUND
- `apps/api/app/models/__init__.py` — MODIFIED, FOUND

### Commits Exist

- `56f8d41` — feat(14-01): Alembic migration 0014 — 4 transactional substrate tables
- `a91afed` — feat(14-01): four ORM models + app.models registration

### Test Results

`cd apps/api && pytest tests/unit/test_migration_0014.py -x -q` → **11 passed, 1 skipped** (DB roundtrip skipped, INTEGRATION_TESTS_ENABLED not set)

## Self-Check: PASSED
