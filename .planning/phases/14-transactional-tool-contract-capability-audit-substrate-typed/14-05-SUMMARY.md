---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "05"
subsystem: transactional-idempotency-schema
tags: [migration, alembic, orm, idempotency, tdd, cr-02, wr-02, txn-02]
status: complete

dependency_graph:
  requires:
    - 14-01 (capability_envelopes schema)
    - 14-02 (tool_calls_audit schema)
    - 14-03 (idempotency.py check/store helpers)
    - 14-04 (tool call audit symmetry)
  provides:
    - 0015 migration with status/args_hash/reserved_at columns
    - ToolIdempotencyKey ORM with reservation columns
    - test_migration_0015.py source + ORM + integration-gated roundtrip tests
  affects:
    - 14-06 (atomic reserve-before-execute engine — depends on status/reserved_at)
    - 14-07 (capability audit — uses updated ORM model)
    - 14-08 (dispatcher rewrite — depends on CR-02 substrate)

tech_stack:
  added: []
  patterns:
    - Alembic ADD COLUMN IF NOT EXISTS DDL style (matches 0014)
    - Alembic downgrade backfill NULL before re-asserting NOT NULL (T-14-05-02)
    - SQLAlchemy Mapped[T | None] for nullable typed columns
    - TDD RED→GREEN with importlib.util migration source assertions

key_files:
  created:
    - apps/api/alembic/versions/0015_idempotency_reservation.py
    - apps/api/tests/unit/test_migration_0015.py
  modified:
    - apps/api/app/models/tool_idempotency_key.py

decisions:
  - status DEFAULT 'completed' chosen (not 'completed' only after insert) so legacy store_idempotency rows and pre-existing rows are never seen as pending — fail-safe default
  - reserved_at NOT NULL DEFAULT now() so legacy rows have a timestamp without requiring a migration backfill
  - args_hash nullable so legacy rows without a hash stay valid
  - downgrade backfills NULL result to '{}'::jsonb before SET NOT NULL to satisfy T-14-05-02

metrics:
  duration_minutes: 5
  completed_date: 2026-06-29T18:44:21Z
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 1
---

# Phase 14 Plan 05: Idempotency Reservation Columns Summary

**One-liner:** Migration 0015 adds status/args_hash/reserved_at to tool_idempotency_keys and relaxes result to nullable, providing the DB substrate for the atomic reserve-before-execute idempotency guard (CR-02) and argument binding (WR-02).

## What Was Built

Migration `0015_idempotency_reservation.py` (chains from `0014`) adds three columns to `tool_idempotency_keys` and relaxes one constraint, enabling the reserve-before-execute pattern that plan 14-06 will implement.

### New Columns

| Column | Type | Nullable | Default | Purpose |
|--------|------|----------|---------|---------|
| `status` | TEXT | NOT NULL | `'completed'` | Reservation lifecycle: `'pending'` before adapter runs, `'completed'` after (CR-02) |
| `args_hash` | TEXT | NULL | — | sha256 hex of canonicalized tool arguments for WR-02 mismatch detection |
| `reserved_at` | TIMESTAMPTZ | NOT NULL | `now()` | INSERT timestamp; used by 14-06 stale-pending reclaim |

### Changed Column

| Column | Before | After |
|--------|--------|-------|
| `result` | JSONB NOT NULL | JSONB NULL |

### Threat Mitigations Applied

- **T-14-05-01**: `status DEFAULT 'completed'` ensures pre-existing / legacy-path rows are never seen as in-progress reservations (tamper prevention).
- **T-14-05-02**: Downgrade backfills `NULL` results to `'{}'::jsonb` before re-asserting `NOT NULL` so the downgrade path never fails on existing pending rows (DoS prevention).

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Failing migration-0015 + ORM tests (RED) | d619c27 | `tests/unit/test_migration_0015.py` (+288 lines) |
| 2 | Write migration 0015 + update ToolIdempotencyKey ORM (GREEN) | 5bb3b94 | `alembic/versions/0015_idempotency_reservation.py`, `app/models/tool_idempotency_key.py` |

## Test Results

```
35 passed, 2 skipped in 2.78s
```

- `tests/unit/test_migration_0015.py` — 16 tests (source assertions + ORM assertions); 1 integration-gated roundtrip skipped
- `tests/unit/test_migration_0014.py` — 11 tests pass (no regression); 1 integration-gated test skipped
- `tests/unit/test_tool_idempotency.py` — 8 tests pass (no regression)

## ORM Acceptance Criteria

```python
from app.models import ToolIdempotencyKey as T; c = T.__table__.c
assert c.result.nullable is True           # ✓
assert c.status.nullable is False          # ✓
assert 'completed' in str(c.status.server_default.arg).lower()  # ✓
assert 'args_hash' in c                    # ✓
assert 'reserved_at' in c                 # ✓
# prints: ok
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. This plan is schema + model only. No data-path stubs exist; the reserve/finalize engine (14-06) and dispatcher rewrite (14-08) will use these columns to implement the full CR-02 / WR-02 behaviour.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundaries introduced. Schema changes are on the control DB (existing boundary).

## Self-Check: PASSED

- `apps/api/alembic/versions/0015_idempotency_reservation.py` — FOUND
- `apps/api/app/models/tool_idempotency_key.py` — FOUND (modified)
- `apps/api/tests/unit/test_migration_0015.py` — FOUND
- Commit d619c27 — FOUND (test RED)
- Commit 5bb3b94 — FOUND (GREEN implementation)
