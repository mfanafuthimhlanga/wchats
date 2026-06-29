---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
reviewed: 2026-06-29T00:00:00Z
depth: standard
files_reviewed: 23
files_reviewed_list:
  - apps/api/alembic/versions/0014_transactional_substrate.py
  - apps/api/app/models/__init__.py
  - apps/api/app/models/capability_envelope.py
  - apps/api/app/models/pending_confirmation.py
  - apps/api/app/models/tool_calls_audit.py
  - apps/api/app/models/tool_idempotency_key.py
  - apps/api/app/services/actor_seam.py
  - apps/api/app/services/agent_tools.py
  - apps/api/app/services/transactional/__init__.py
  - apps/api/app/services/transactional/audit.py
  - apps/api/app/services/transactional/enforcement.py
  - apps/api/app/services/transactional/idempotency.py
  - apps/api/app/services/transactional/provider_adapter.py
  - apps/api/app/services/transactional/registry.py
  - apps/api/app/services/transactional/schemas.py
  - apps/api/app/services/transactional/tools.py
  - apps/api/app/worker/tasks/runtime/agent.py
  - apps/api/tests/unit/test_capability_enforcement.py
  - apps/api/tests/unit/test_migration_0014.py
  - apps/api/tests/unit/test_tool_idempotency.py
  - apps/api/tests/unit/test_transactional_contract.py
  - apps/api/tests/unit/test_transactional_tools.py
findings:
  critical: 2
  warning: 5
  info: 3
  total: 10
status: issues_found
---

# Phase 14: Code Review Report

**Reviewed:** 2026-06-29
**Depth:** standard
**Files Reviewed:** 23
**Status:** issues_found

## Summary

This phase ships the transactional tool substrate: capability enforcement (fail-closed), durable idempotency, audit completeness, a typed contract, and the dispatcher that orders these gates. The typed contract (`schemas.py`), the registry, the fail-closed *decision logic* in `check_capability_envelope`, and the parameterized SQL (no injection surface found in `op.execute()` / `sa_text()` / the `psycopg2.sql.Identifier` allowlist path) are sound.

However, two defects undermine the phase's central invariants, and neither is caught by the test suite because every test mocks `get_sync_db`/`write_audit_row` and builds the capability snapshot from JSON-safe string fixtures:

1. **The capability snapshot written to the `capability_snapshot` JSONB column is not JSON-serializable** (it contains a `datetime`), so every audit write on the `disabled`-denial path and on every capability-pass path will raise at `db.commit()` in production. This breaks AUD-01 (the exact symmetry the last commit, b765c13, claimed to deliver) and surfaces an exception to the agent instead of a clean denial.
2. **The idempotency guard is a non-atomic check → execute → store sequence**, so the mutation can execute twice under concurrent redelivery or a crash between adapter execution and key storage. `ON CONFLICT DO NOTHING` deduplicates only the *row*, not the *execution* — which is precisely the durability guarantee TXN-02 promises against `acks_late` retries.

Both are masked today by the Phase-14 stub adapter (no real side effects) but ship as the guarantee the substrate exists to provide. Warnings cover an enforcement-ordering defect that converts idempotent replays into denials, an argument-unbound idempotency key, event-loop-blocking DB calls, disabled TLS verification, and an unguarded confirmation-write path.

## Critical Issues

### CR-01: `capability_snapshot` is not JSON-serializable → audit write raises at commit (breaks AUD-01)

**File:** `apps/api/app/services/transactional/enforcement.py:172` → `apps/api/app/services/transactional/audit.py:78-93`

`check_capability_envelope` builds the snapshot with `snapshot = dict(row)` from a textual `sa_text(...)` SELECT that includes `updated_at` (TIMESTAMPTZ). Over the sync psycopg2 engine, a textual query returns the DBAPI-native value — `updated_at` comes back as a `datetime.datetime` object (and, depending on psycopg2 UUID registration, `id`/`agent_id` may come back as `uuid.UUID`). This dict is passed verbatim as `capability_snapshot` into `write_audit_row`, which assigns it to the `ToolCallsAudit.capability_snapshot` **JSONB** column.

`database.py` constructs `sync_engine` with no custom `json_serializer`, so SQLAlchemy serializes JSONB with stock `json.dumps`, which raises `TypeError: Object of type datetime is not JSON serializable` at `db.commit()` (audit.py:93).

Impact:
- **`disabled` denial path** (`tools.py:130-141`): `write_audit_row` is awaited with no try/except, so the `TypeError` propagates out of the tool. The capability-denied audit row is never written — directly defeating the AUD-01 "100% symmetry" the latest commit added — and the agent receives an exception instead of the intended "Access denied" message.
- **All capability-pass paths** (success at `tools.py:217-228`, actor-block at `tools.py:174-185`): same `TypeError` at commit. In Phase 16, with a real adapter, this fires *after* the mutation but *before* `store_idempotency`, so the operation mutates, fails to audit, fails to record idempotency, and Celery retries it.
- The `no_envelope_row` path is unaffected because it returns `{}` (JSON-safe), making the failure asymmetric and easy to miss.

Why tests miss it: `test_capability_enforcement.py:_make_envelope_mapping` sets `"updated_at": "2026-06-29T00:00:00Z"` (a **string**), and `write_audit_row`/dispatcher tests mock `get_sync_db`, so no real JSONB serialization ever runs.

**Fix:** normalize the snapshot to JSON-safe primitives before returning it. For example, in `check_capability_envelope`:
```python
import json
from datetime import datetime
from uuid import UUID

def _jsonable(v):
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    return v

snapshot: dict = {k: _jsonable(v) for k, v in dict(row).items()}
```
Alternatively configure the sync engine with `json_serializer=lambda o: json.dumps(o, default=str)`. Add a non-mocked test that round-trips a real snapshot (with a real `datetime`) through `write_audit_row` against the integration DB.

### CR-02: Non-atomic idempotency guard permits double-execution under concurrency / crash (defeats TXN-02)

**File:** `apps/api/app/services/transactional/tools.py:160-245`; `apps/api/app/services/transactional/idempotency.py:91-126`

The dispatcher implements idempotency as: `check_idempotency` (read) → run actor seam → execute adapter → `store_idempotency` (write with `ON CONFLICT DO NOTHING`). The lookup and the store are separate transactions with the mutation between them, so the guard is a check-then-act race:

- **Concurrent redelivery:** with `acks_late=True`, a visibility-timeout redelivery can run the same logical tool call on a second worker while the first is still executing. Both `check_idempotency` calls return `None` (no row yet), both pass the actor seam, and both call the adapter — the mutation runs twice. `ON CONFLICT DO NOTHING` then dedupes only the stored row, not the two executions that already happened.
- **Crash between execute and store:** if the worker dies (or `write_audit_row` raises — see CR-01) after `adapter.<method>()` returns but before `store_idempotency` commits, the key is never persisted. The retry misses the cache and re-executes the mutation.

This is exactly the failure mode TXN-02 claims to prevent ("survives Redis restart + Celery `acks_late` retries; never re-execute the mutation"). The Phase-14 stub adapter has no side effects so the defect is invisible today, but the substrate is shipped as the durability guarantee and there is no test that asserts single-execution under interleaved/concurrent calls (`test_tool_idempotency.py` only exercises sequential mocked calls).

**Fix:** reserve the key *before* executing, atomically. Insert the idempotency row first with `ON CONFLICT DO NOTHING ... RETURNING id`; if no row is returned, you are not the winner — poll/return the existing stored result instead of executing. Only the insert winner proceeds to the adapter, then updates the row with the result. This makes "exactly one execution" a DB-enforced invariant rather than a best-effort sequence. Add a concurrency test (two overlapping calls, same key) asserting the adapter is invoked once.

## Warnings

### WR-01: Rate-limit INCR runs before the idempotency lookup → replays double-count and can return a denial instead of the cached result

**File:** `apps/api/app/services/transactional/enforcement.py:188-207`; `apps/api/app/services/transactional/tools.py:126-167`

The capability check (which performs `redis_client.incr` on the rate-limit window) runs on every call *before* the idempotency lookup. On an `acks_late` retry or an in-turn re-call of an already-executed key:
- the rate counter is incremented again even though the call will short-circuit to the cached result (budget is consumed by replays), and
- if that increment crosses the limit, `check_capability_envelope` returns `("...", "rate_limit")` and the dispatcher returns an `is_error` denial **before** ever reaching the idempotency lookup — so a replay of a previously successful operation returns an error rather than the original stored result.

This violates the stated invariant "replaying the same (agent_id, skill, idempotency_key) must return the original result." The ordering is deliberate (fail-closed even for replays, T-14-04-03), but the rate-limit side effect should not be applied to replays.

**Fix:** either (a) move the idempotency lookup ahead of the rate-limit INCR (keep the no-row/disabled fail-closed checks first, then idempotency, then rate/constraint side-effecting checks), or (b) make the rate-limit increment conditional on a cache miss. Add a test: store a key, then re-invoke at the rate-limit boundary and assert the cached result is returned, not a `rate_limit` denial.

### WR-02: Idempotency key is not bound to request arguments → same key + different args returns the wrong result without executing

**File:** `apps/api/app/services/transactional/tools.py:160-167`; `apps/api/app/services/transactional/idempotency.py:52-88`

The cache is keyed on `(agent_id, skill, idempotency_key)` only; the stored result is not validated against the current request's arguments. The `idempotency_key` is supplied by the LLM in the tool-call arguments. If the model reuses a key across two semantically different calls (e.g. `place_order` for product A then product B with the same key), the second call returns product A's cached response and product B is never ordered — reported to the customer as success. Standard idempotency contracts fingerprint the request body and reject mismatched reuse.

**Fix:** store a hash of the canonicalized arguments alongside the key. On a cache hit, compare the incoming argument hash; on mismatch, return an explicit `is_error` ("idempotency key reused with different arguments") rather than the stale result.

### WR-03: Blocking sync DB and Redis calls executed directly in async tool handlers (no executor offload)

**File:** `apps/api/app/services/transactional/tools.py:126,160,217,245`; `enforcement.py:147-157,196-197`; `idempotency.py:69-78,111-126`; `audit.py:91-93`

`check_capability_envelope`, `check_idempotency`, `store_idempotency`, and `write_audit_row` are `async def` but perform synchronous, blocking I/O (`get_sync_db()` SQLAlchemy calls, `redis_client.incr/expire`) directly on the event loop. `agent_tools.retrieve_tool` and `lookup_structured_tool` go to lengths to push blocking calls into `run_in_executor` precisely to avoid stalling the loop; these new handlers do not. Because the tool runs inside `_run_sdk_turn` under `asyncio.wait_for(..., timeout=90)`, blocking DB/Redis latency stalls SDK stream consumption and eats the wall-clock budget.

**Fix:** wrap the blocking sections in `await loop.run_in_executor(None, ...)` as the existing tools do, or provide async DB/Redis variants. (Performance is out of v1 scope, but this is flagged as a correctness/robustness inconsistency with the established executor-safety pattern in the same module.)

### WR-04: Redis client disables TLS certificate verification (`ssl.CERT_NONE`) for `rediss://`

**File:** `apps/api/app/services/transactional/enforcement.py:65-70`

`_get_redis()` passes `{"ssl_cert_reqs": ssl.CERT_NONE}` for `rediss://` URLs, disabling certificate validation on the rate-limit Redis connection (MITM exposure). The pattern is copied from `agent_tools.py`/`agent.py`, but it is reintroduced here for the security-enforcement rate limiter.

**Fix:** use `ssl.CERT_REQUIRED` with the provider CA bundle (Upstash presents a valid public cert), or centralize one verified Redis-client factory and reuse it. If `CERT_NONE` is a deliberate, documented exception, record it in the threat model rather than scattering it across modules.

### WR-05: `confirm_action_tool` writes unbounded rows with no capability/rate/dedup gate

**File:** `apps/api/app/services/transactional/tools.py:431-493`

`confirm_action_tool` does not call `check_capability_envelope`, has no rate limit, and (by design) no dedup, so the LLM can write arbitrarily many `pending_confirmations` rows for any `skill` string it chooses — including skills the agent has no envelope for. Duplicate dedup is explicitly deferred (T-14-04-05), but the *absence of any capability gate* means confirmation requests are not bounded by the same fail-closed authorization as the action they reference, enabling unbounded control-DB growth from a single conversation.

**Fix:** gate `confirm_action` behind at least the envelope existence/enabled check for the referenced `skill`, and add a per-(agent, skill, action_reference) dedup or a per-turn cap before Phase 18.

## Info

### IN-01: Rate-limit key can leak without a TTL

**File:** `apps/api/app/services/transactional/enforcement.py:196-197`

`incr` and `expire` are issued as two separate commands. If the process dies between them (or `expire` fails), that window's key persists in Redis without a TTL. It is window-aligned so it does not corrupt future counts, but it is a slow key leak.

**Fix:** use a pipeline/`MULTI` for `INCR`+`EXPIRE`, or `SET ... EX NX` + `INCR` semantics.

### IN-02: `max_amount_cents` check treats `amount_cents == 0` as "no amount"

**File:** `apps/api/app/services/transactional/enforcement.py:216`

`amount = getattr(args, "amount_cents", None) or getattr(args, "refund_amount_cents", None)` — because `0` is falsy, a legitimate `amount_cents=0` falls through to `refund_amount_cents`. Harmless against an upper-bound check today, but fragile if the constraint logic is extended.

**Fix:** select the field explicitly per skill, or use `amount = getattr(args, "amount_cents", None); if amount is None: amount = getattr(args, "refund_amount_cents", None)`.

### IN-03: ContextVar defaults (`""`) produce invalid-UUID inserts if `build_tool_server` did not run

**File:** `apps/api/app/services/transactional/tools.py:120,452`; `apps/api/app/services/agent_tools.py:140,143`

`_agent_id_var`/`_conversation_id_var` default to `""`. If a transactional handler is ever invoked without `build_tool_server` having set them (e.g. a future code path or test), `agent_id=""` flows into the `UUID NOT NULL` columns of `tool_calls_audit` / `pending_confirmations` and fails at insert with an opaque error rather than a clear precondition violation.

**Fix:** assert a non-empty `agent_id` at the top of the dispatcher and `confirm_action_tool`, returning a clear `is_error` if unset.

---

_Reviewed: 2026-06-29_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
