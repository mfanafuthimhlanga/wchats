---
status: testing
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
source: [14-VERIFICATION.md]
started: 2026-06-29T19:20:00Z
updated: 2026-06-29T21:05:00Z
---

## Current Test

number: 1
name: Migration 0014 + 0015 roundtrip against live PostgreSQL
expected: |
  Migration 0014 creates capability_envelopes, tool_calls_audit,
  pending_confirmations, tool_idempotency_keys with correct columns + UNIQUE
  constraints. Migration 0015 adds status (DEFAULT 'completed'), args_hash
  (nullable), reserved_at (NOT NULL DEFAULT now()) to tool_idempotency_keys and
  relaxes result to nullable; downgrade backfills NULL results before re-asserting
  NOT NULL. Upgrade-downgrade-upgrade roundtrip succeeds.
awaiting: user response

## Tests

### 1. Migration 0014 + 0015 roundtrip against live PostgreSQL
expected: All 4 tables + both UNIQUE constraints exist; 0015 reservation columns added; downgrade backfills then re-asserts NOT NULL; up-down-up roundtrip succeeds.
how: `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/unit/test_migration_0014.py tests/unit/test_migration_0015.py -q`
result: [pending]

### 2. Idempotency exactly-once under concurrency (CR-02 DB-enforced)
expected: Two concurrent workers hitting the same (agent_id, skill, idempotency_key) → exactly one `reserved` winner, the other `in_progress`/`replay`; exactly one DB row; a third reservation after finalize returns `replay` with the stored result; reserve→release→re-reserve wins; same key + different args → `args_mismatch` (result None).
how: `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/integration/test_idempotency_concurrency.py -q`
note: This is the live-DB proof that the reserve-before-execute fix (CR-02) prevents double-execution — the invariant unit mocks cannot establish.
result: [pending]

### 3. End-to-end replay through the dispatcher
expected: Two place_order calls with the same idempotency_key — the second returns the cached result WITHOUT calling the adapter; tool_calls_audit has exactly one row for the executed call; tool_idempotency_keys has exactly one completed row. Confirms reserve → execute → audit → finalize end-to-end (closes the original UAT idempotency item).
how: `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/integration/test_transactional_idempotency_e2e.py -q`
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
