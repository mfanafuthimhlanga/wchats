---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "06"
subsystem: transactional-idempotency-engine
tags: [idempotency, atomic, reservation, concurrency, cr-02, wr-02, wr-03, txn-02, tdd]
status: complete

dependency_graph:
  requires:
    - 14-05 (migration 0015 — status/args_hash/reserved_at columns on tool_idempotency_keys)
  provides:
    - atomic reserve-before-execute engine (reserve_idempotency/finalize/release/compute_args_hash)
    - Reservation dataclass with state/result
    - _RESERVATION_LEASE_SECONDS stale-reclaim window
    - 16 unit tests (TestReservationEngine) covering all reservation paths
    - integration concurrency proof (3 tests, INTEGRATION_TESTS_ENABLED gated)
  affects:
    - 14-08 (dispatcher rewrite — consumes new reserve/finalize/release API)

tech_stack:
  added: []
  patterns:
    - INSERT ... ON CONFLICT (agent_id, skill, idempotency_key) DO NOTHING RETURNING id
    - asyncio.to_thread(_inner) offload for blocking get_sync_db calls (WR-03)
    - frozen dataclass for typed Reservation result
    - TDD RED→GREEN: 16 ImportError failures in RED; 24/24 pass in GREEN
    - INTEGRATION_TESTS_ENABLED gate + finally-block teardown (T-07-01 pattern)

key_files:
  modified:
    - apps/api/app/services/transactional/idempotency.py
    - apps/api/tests/unit/test_tool_idempotency.py
  created:
    - apps/api/tests/integration/test_idempotency_concurrency.py

decisions:
  - "DB decides the winner: single INSERT...ON CONFLICT...RETURNING — no app-level check-then-act race (CR-02)"
  - "args_hash excludes idempotency_key so hash binds to the logical request, not the key string (WR-02)"
  - "NULL stored args_hash treated as legacy row — skip mismatch check, treat as replay for backward compatibility"
  - "asyncio.to_thread used (not loop.run_in_executor) — simpler, same semantics, Python 3.9+ (WR-03)"
  - "check_idempotency/store_idempotency retained unchanged as facades — tools.py not touched until 14-08"
  - "_RESERVATION_LEASE_SECONDS = 120 (2 min) — stale pending rows reclaimable after crash without deadlocking"
  - "Reservation is a frozen dataclass — immutable, typed, printable in logs"

metrics:
  duration_minutes: 14
  completed_date: "2026-06-29T19:38:30Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 2
---

# Phase 14 Plan 06: Atomic Reserve/Finalize/Release Engine Summary

Atomic DB-enforced reserve-before-execute engine that makes "exactly one execution per key" an invariant under `acks_late` concurrent redelivery and crash-between-execute-and-store (CR-02 BLOCKER fix), with argument fingerprinting to detect key reuse with different business args (WR-02), and executor offload for all blocking DB calls (WR-03).

## What Was Built

### `compute_args_hash(args: dict) -> str`

sha256 hex of `json.dumps(filtered, sort_keys=True, separators=(",",":"), default=str)` where `filtered` drops the `idempotency_key` entry.  Excluding `idempotency_key` binds the hash to the *logical request*: the same business args with different key strings hash equal (correct replay), while the same key with different product/amount/etc. hashes differently (WR-02 mismatch detection).

### `Reservation` (frozen dataclass)

```python
@dataclass(frozen=True)
class Reservation:
    state: Literal["reserved", "replay", "in_progress", "args_mismatch"]
    result: dict | None = None
```

### `reserve_idempotency(agent_id, skill, key, args_hash) -> Reservation`

Single atomic INSERT:
```sql
INSERT INTO tool_idempotency_keys
  (agent_id, skill, idempotency_key, args_hash, status, reserved_at, result)
VALUES (:a, :s, :k, :h, 'pending', now(), NULL)
ON CONFLICT (agent_id, skill, idempotency_key) DO NOTHING
RETURNING id
```

Decision tree on conflict:
- `RETURNING` yields row → winner → `Reservation("reserved")`, commit
- `RETURNING` yields nothing → SELECT existing row:
  - `args_hash` differs (and not NULL) → `Reservation("args_mismatch")` (WR-02)
  - `status='completed'` → `Reservation("replay", result=stored)`
  - `status='pending'`, `reserved_at < now()-120s` → attempt reclaim UPDATE:
    - UPDATE returns row → `Reservation("reserved")`, commit
    - UPDATE returns nothing → `Reservation("in_progress")`
  - `status='pending'`, recent → `Reservation("in_progress")`

Legacy rows with `args_hash=NULL` skip the mismatch check and replay as before.

### `finalize_idempotency(agent_id, skill, key, result) -> None`

```sql
UPDATE tool_idempotency_keys
SET result = :r::jsonb, status = 'completed'
WHERE agent_id=:a AND skill=:s AND idempotency_key=:k AND status='pending'
```

Safe to call idempotently: if the row is already completed (or missing), the UPDATE matches no rows (no-op).

### `release_idempotency(agent_id, skill, key) -> None`

```sql
DELETE FROM tool_idempotency_keys
WHERE agent_id=:a AND skill=:s AND idempotency_key=:k AND status='pending'
```

Scoped to `status='pending'` — never deletes a completed row. Used on denial, actor block, adapter error, graceful exception paths so the key can be retried.

### Executor Offload (WR-03)

Every function wraps its `get_sync_db` block in an `_inner()` helper called via `asyncio.to_thread(_inner)`, keeping synchronous Postgres calls off the event loop. Pattern mirrors `loop.run_in_executor` in `agent_tools.py`.

### Facades Retained

`check_idempotency` and `store_idempotency` are kept **unchanged in behavior** (same SQL, same signatures). They also receive `asyncio.to_thread` offload but their externally-observable contract is identical, so `tools.py` and all existing tests continue to work without modification.

## Tests

### Unit (24/24 pass)

- 8 original (`TestCheckIdempotency`, `TestStoreIdempotency`, `TestNoRedisUsage`) — all still pass
- 16 new (`TestReservationEngine`):
  - `compute_args_hash`: stability, `idempotency_key` exclusion, business-arg sensitivity, hex type/length
  - `reserve_idempotency`: winner commits; replay carries stored result; in_progress on recent pending; stale reclaim wins and lost-reclaim returns in_progress; args_mismatch; NULL stored hash treated as legacy replay
  - `finalize_idempotency`: issues UPDATE with 'completed' keyword, commits
  - `release_idempotency`: issues DELETE scoped to 'pending', commits
  - `Reservation`: state/result attributes; `_RESERVATION_LEASE_SECONDS` is positive int

### Integration (INTEGRATION_TESTS_ENABLED gated, 3 skipped)

`tests/integration/test_idempotency_concurrency.py`:
- `test_concurrent_same_key_exactly_one_winner`: two `ThreadPoolExecutor` workers with `threading.Barrier` hit the same key; exactly 1 `reserved`, 1 `in_progress`/`replay`; single DB row; third reservation after finalize is `replay` with result
- `test_release_allows_re_reservation`: reserve → release → re-reserve wins; row count 0 after release
- `test_args_mismatch_returns_error_not_stale_replay`: same key, different business args → `args_mismatch`; result is None

**Human check:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/integration/test_idempotency_concurrency.py -q` proves exactly-once under real-Postgres concurrency.

## Deviations from Plan

None — plan executed exactly as written.

## Threat Coverage

| Threat | Mitigation |
|--------|-----------|
| T-14-06-01: double-execution under concurrent redelivery | atomic INSERT...ON CONFLICT...RETURNING; loser gets in_progress/replay, never executes |
| T-14-06-02: key reused with different args returns wrong result | args_hash bound at reserve; mismatch surfaces `args_mismatch` error, not stale replay |
| T-14-06-03: orphaned pending row deadlocks a key after crash | reserved_at lease (120s) + reclaim UPDATE...RETURNING |
| T-14-06-04: idempotency keys leaking to Redis | control-DB only; no redis import; TestNoRedisUsage retained |

## Self-Check

All commits:
- `67278e2` test(14-06): failing tests for reservation engine RED phase
- `0660820` feat(14-06): atomic reserve/finalize/release engine with executor offload

## Self-Check: PASSED

- [x] `apps/api/app/services/transactional/idempotency.py` — exists, contains reserve/finalize/release/compute_args_hash/Reservation/_RESERVATION_LEASE_SECONDS
- [x] `apps/api/tests/unit/test_tool_idempotency.py` — 24/24 pass including TestReservationEngine
- [x] `apps/api/tests/integration/test_idempotency_concurrency.py` — 3 skipped (gate correct)
- [x] `check_idempotency`/`store_idempotency` facades retained; `tools.py` not modified; 30/30 `test_transactional_tools.py` pass
- [x] No `import redis` / `from redis` in idempotency.py — TestNoRedisUsage still passes
- [x] commit `67278e2` exists (RED); commit `0660820` exists (GREEN)
