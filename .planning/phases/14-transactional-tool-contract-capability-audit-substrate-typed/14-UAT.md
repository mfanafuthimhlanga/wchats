---
status: testing
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
source: [14-VERIFICATION.md]
started: 2026-06-29T19:20:00Z
updated: 2026-06-29T19:20:00Z
---

## Current Test

number: 1
name: Migration 0014 DB roundtrip against live PostgreSQL
expected: |
  Migration 0014 applies cleanly against real PostgreSQL; all 4 tables exist
  (capability_envelopes, tool_calls_audit, pending_confirmations,
  tool_idempotency_keys) with correct columns and UNIQUE constraints
  (uq_capability_envelopes_agent_skill, uq_tool_idempotency_keys); downgrade
  drops all 4 tables; upgrade-downgrade-upgrade roundtrip succeeds.
awaiting: user response

## Tests

### 1. Migration 0014 DB roundtrip against live PostgreSQL
expected: Migration 0014 applies cleanly against real PostgreSQL; all 4 tables exist with correct columns and UNIQUE constraints (uq_capability_envelopes_agent_skill, uq_tool_idempotency_keys); downgrade drops all 4 tables; upgrade-downgrade-upgrade roundtrip succeeds.
how: `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/unit/test_migration_0014.py -q` against a running control DB.
result: [pending]

### 2. Idempotency UNIQUE constraint end-to-end (durable replay)
expected: Two place_order calls with an identical idempotency_key against a live PostgreSQL control DB — the second call returns the cached result (adapter NOT called again, NO second row in tool_calls_audit), and tool_idempotency_keys contains exactly one row for the key. Confirms ON CONFLICT DO NOTHING + the UNIQUE(agent_id, skill, idempotency_key) constraint enforce durable replay end-to-end (survives Redis restart / acks_late retries).
note: The CR-01 audit-write crash (non-JSON-safe capability_snapshot) was fixed in commit 61b45a0 — without that fix this test would have raised TypeError at the audit commit before reaching the idempotency assertion. See also code-review finding CR-02 (concurrent/crash-window double-execution) in 14-REVIEW.md — this UAT validates the sequential replay path; the concurrency hardening is a separate decision.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
