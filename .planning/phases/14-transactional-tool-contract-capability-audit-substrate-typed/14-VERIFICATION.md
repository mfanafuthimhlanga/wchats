---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
verified: 2026-06-29T20:45:00Z
status: human_needed
score: 3/4 must-haves verified
behavior_unverified: 1
overrides_applied: 0
post_verification_fix:
  - "AUD-01 args_mismatch gap (the sole blocker below) RESOLVED in commit a18dd35: write_audit_row(error='idempotency.args_mismatch') added on the args_mismatch path + regression test test_args_mismatch_writes_exactly_one_audit_row. Status moved gaps_found → human_needed; only the live-DB integration items remain."
re_verification:
  previous_status: human_needed
  previous_score: 3/4
  gaps_closed:
    - "CR-01: capability_snapshot JSON-serializable — _json_safe() coercion in check_capability_access (commit 61b45a0 + plan 14-07)"
    - "CR-02: non-atomic idempotency guard — reserve_idempotency atomic INSERT...ON CONFLICT DO NOTHING RETURNING (plan 14-06 + 14-08 dispatcher rewrite)"
    - "WR-01: rate INCR on replays — replay now short-circuits BEFORE apply_rate_and_constraint_checks (plan 14-08)"
    - "WR-02: args mismatch silent stale replay — compute_args_hash + reserve_idempotency args_mismatch branch (plan 14-06 + 14-08)"
    - "WR-03: blocking event loop — asyncio.to_thread in enforcement.py, audit.py, idempotency.py (plan 14-07; confirm_action_tool DB write remains unaddressed — see Warning)"
    - "WR-04: Redis TLS CERT_NONE — CERT_REQUIRED by default; REDIS_TLS_INSECURE flag (plan 14-07)"
    - "WR-05: unbounded confirm_action — check_capability_access gate added to confirm_action_tool (plan 14-08)"
    - "IN-01: INCR/EXPIRE race — pipelined atomic INCR+EXPIRE (plan 14-07)"
    - "IN-02: falsy-zero amount — explicit None-check for amount_cents (plan 14-07)"
    - "IN-03: empty agent_id — guard at top of dispatcher and confirm_action_tool (plan 14-08)"
  gaps_remaining: []
  regressions_resolved:
    - "AUD-01 args_mismatch: the 14-08 dispatcher rewrite added an args_mismatch branch that returned is_error without write_audit_row. RESOLVED in commit a18dd35 — write_audit_row(error='idempotency.args_mismatch') now runs before the return, with regression test test_args_mismatch_writes_exactly_one_audit_row. Re-confirmed: 124 dispatcher/enforcement/idempotency unit tests pass."
gaps:
  - truth: "Every mutating tool call writes a complete tool_calls_audit row on EVERY path: success, adapter-error, capability-denial, args_mismatch, actor-block (AUD-01)"
    status: resolved
    resolution: "Fixed in commit a18dd35 — write_audit_row(error='idempotency.args_mismatch') added on the args_mismatch path + regression test. (in_progress remains intentionally un-audited: concurrent-duplicate no-op; the reserved winner audits the real execution.)"
    reason: "Was: the args_mismatch branch returned is_error without calling write_audit_row. Now writes one audit row, matching capability.denial / actor_block. All required paths (capability denial, rate denial, actor block, adapter error, success, args_mismatch) write an audit row."
    artifacts:
      - path: "apps/api/app/services/transactional/tools.py"
        issue: "Lines 210-224: reservation.state == 'args_mismatch' returns early with is_error; write_audit_row is NOT called. The docstring at the top of the file lists 'Replays and in_progress do NOT write audit rows' but omits args_mismatch — the plan's AUD-01 symmetry table also omits it, making this a scope gap that became a regression relative to REQUIREMENTS.md AUD-01 ('100% of mutating calls')."
    missing:
      - "Add write_audit_row call on the args_mismatch path (before the return at line ~223), using: error='idempotency.args_mismatch', result=None, capability_snapshot=snapshot (already available). Update the docstring AUD-01 symmetry table and plan 14-08 SUMMARY to include args_mismatch."
      - "Add a unit test asserting write_audit_row is called when reserve_idempotency returns args_mismatch (mirrors the existing TestCapabilityDenial test pattern)."
behavior_unverified_items:
  - truth: "A side-effecting tool replayed with the same idempotency key returns the original result and does not re-execute (durable control-DB guard, atomic reserve-before-execute, survives Redis restart / acks_late retries)"
    test: "Run cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/integration/test_idempotency_concurrency.py tests/integration/test_transactional_idempotency_e2e.py -q against a live PostgreSQL control DB"
    expected: "test_concurrent_same_key_exactly_one_winner: two concurrent workers hit the same key; exactly 1 reserved, 1 in_progress/replay; single DB row; third reservation after finalize is replay with result. test_args_mismatch_returns_error_not_stale_replay: same key + different args → args_mismatch, not stale replay. E2e: replay returns cached result; adapter called exactly once across two calls with the same key."
    why_human: "Unit tests mock get_sync_db and verify the SQL string contains ON CONFLICT RETURNING. The actual PostgreSQL UNIQUE constraint enforcement and the concurrent winner/loser race (threading.Barrier) require a live control DB. The migration 0015 status/reserved_at columns and the reserve/finalize/release SQL are correct per code inspection, but exactly-once-under-concurrency is a DB-level invariant that cannot be proven by grep alone."
human_verification:
  - test: "Run INTEGRATION_TESTS_ENABLED=1 pytest tests/unit/test_migration_0014.py tests/unit/test_migration_0015.py -q against a running control DB"
    expected: "Migration 0014 creates capability_envelopes, tool_calls_audit, pending_confirmations, tool_idempotency_keys with correct columns and UNIQUE constraints. Migration 0015 adds status (DEFAULT 'completed'), args_hash (nullable), reserved_at (NOT NULL DEFAULT now()) to tool_idempotency_keys and relaxes result to nullable. Downgrade backfills NULL results before re-asserting NOT NULL (T-14-05-02). Upgrade-downgrade-upgrade roundtrip succeeds."
    why_human: "Both migration roundtrip tests are gated behind INTEGRATION_TESTS_ENABLED=1 and require a live control DB. Cannot be verified in the local-only unit environment."
  - test: "Run INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_idempotency_concurrency.py -q against a running control DB"
    expected: "Two concurrent threads hitting the same (agent_id, skill, idempotency_key) produce exactly one reserved and one in_progress/replay outcome; exactly one DB row exists; third reservation after finalize returns replay with result. release_allows_re_reservation: reserve → release → re-reserve wins. args_mismatch test: same key + different business args → args_mismatch state, result is None."
    why_human: "Test uses ThreadPoolExecutor + threading.Barrier to simulate concurrent duplicate delivery. Proves exactly-once execution is DB-enforced, not just code-asserted. Requires a live control DB with the 0015 schema applied."
  - test: "Run INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_transactional_idempotency_e2e.py -q against a running control DB"
    expected: "Two place_order calls with the same idempotency_key: second call returns the cached result without calling the adapter; tool_calls_audit has exactly one row; tool_idempotency_keys has exactly one completed row. Confirms reserve-before-execute → audit → finalize pipeline end-to-end."
    why_human: "E2e test requires a real PostgreSQL control DB with migrations 0014 and 0015 applied and a live Celery-reachable capability_envelopes row."
---

# Phase 14: Transactional Tool Contract + Capability/Audit Substrate — Re-Verification Report

**Phase Goal:** Establish the authorization substrate every transactional action rides on — six typed transactional tools tagged `mutating:true` with idempotency-key handling, the per-skill capability-envelope table + enforcement middleware, and the audit/confirmation tables — so no action can execute without a typed contract, a capability check, and an audit row.
**Verified:** 2026-06-29T20:45:00Z
**Status:** human_needed _(was gaps_found; the sole blocker — AUD-01 args_mismatch audit row — was resolved in commit a18dd35 + regression test, post-re-verification)_
**Re-verification:** Yes — gap-closure plans 14-05..08 executed; verifying against current code.

---

## Re-Verification Context

Plans 14-05..08 closed all ten REVIEW blockers and warnings from the initial code review. The base plans (14-01..04) remain intact. The gap-closure summary claims are documented in 14-05..08-SUMMARY.md.

**What changed since initial verification (status: human_needed, score 3/4):**

| Plan | Closed |
|------|--------|
| 14-05 | Migration 0015: status/args_hash/reserved_at columns on tool_idempotency_keys; result → nullable |
| 14-06 | Atomic reserve/finalize/release engine in idempotency.py; compute_args_hash; Reservation dataclass; 120s crash-lease; asyncio.to_thread offload (WR-03) |
| 14-07 | check_capability_access (side-effect-free) + apply_rate_and_constraint_checks (side-effecting rate INCR) split; Redis TLS CERT_REQUIRED by default (WR-04); pipelined INCR/EXPIRE (IN-01); falsy-zero amount fix (IN-02); asyncio.to_thread offload for enforcement.py and audit.py (WR-03) |
| 14-08 | Dispatcher (_execute_transactional_tool) rewritten to: IN-03 guard → auth (check_capability_access) → reserve_idempotency (replay/args_mismatch/in_progress/reserved) → rate (apply_rate_and_constraint_checks, winners only) → actor seam → adapter → finalize/audit; confirm_action capability gate (WR-05); IN-03 guard for confirm_action |

---

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | The six transactional tools + `confirm_action` exist as typed Pydantic functions; no string-blob/SQL/URL inputs anywhere in the set | VERIFIED | `schemas.py` unchanged: 6 mutating Input/Output + ConfirmActionInput/Output; all fields typed scalars; idempotency_key on all 6 mutating inputs; ConfirmActionInput has no idempotency_key; `tools.py` 7 handlers intact; agent_tools.py and agent.py retain all 11 tools; no regression |
| 2 | A side-effecting tool replayed with the same idempotency key returns the original result and does not re-execute (durable control-DB guard, survives Redis restart / acks_late retries) | PRESENT_BEHAVIOR_UNVERIFIED | Dispatcher order verified (code): step 3 reserve_idempotency returns Reservation("replay") before step 4 rate checks; adapter not called on replay; finalize writes result after success. Atomic INSERT...ON CONFLICT DO NOTHING RETURNING confirmed in reserve_idempotency (idempotency.py line 174-188); UNIQUE constraint in both migration 0014 (original) and ORM. asyncio.to_thread offload present on all idempotency functions. DB-level exactly-once requires live Postgres — integration tests written but INTEGRATION_TESTS_ENABLED-gated (3 skipped). |
| 3 | A disabled / over-limit / constraint-violating skill call is rejected and logged as `capability.denial` (fail-closed) | VERIFIED | check_capability_access: no_envelope_row → ({}, "no_envelope_row"), disabled → (snapshot, "disabled"); both log capability.denial. apply_rate_and_constraint_checks: rate_limit → pipeline INCR > max_calls, logs capability.denial; max_amount_cents → explicit None-check (IN-02 fixed). Dispatcher step 2 runs check_capability_access on EVERY call including replays (verified via tools.py step ordering). confirm_action_tool gated behind check_capability_access (WR-05 closed). |
| 4 | Every mutating tool call writes a complete `tool_calls_audit` row on EVERY path: success, adapter-error, capability-denial, args_mismatch, actor-block (AUD-01) | FAILED | Paths with audit rows: capability denial (line 168), rate denial (line 251), actor block (line 282), adapter error (line 322), success (line 349) — all write write_audit_row. MISSING: the args_mismatch branch (lines 210-224) returns is_error WITHOUT calling write_audit_row. snapshot is available at that point (capability check passed at step 2). The 14-08 AUD-01 symmetry table in the SUMMARY omits args_mismatch, making it a scope gap that violates REQUIREMENTS.md AUD-01 "100% of mutating calls." CR-01 fix confirmed: _json_safe() coercion at enforcement.py line 250 converts datetime/UUID before snapshot enters the audit column — no serialization crash. |

**Score:** 2/4 truths directly verified (1 present, behavior-unverified; 1 failed)

---

### Gap Closed vs. Previous Verification: Enforcement Order

The previous report noted that `actor_seam.py` docstring showed a stale enforcement order. That docstring remains stale — the body at line 8 still reads `capability check → [call_actor_gate] → idempotency check → execute(adapter) → audit` which is the pre-refinement order. The actual tools.py dispatcher implements the correct Plan-08 order. This is documentation-only, no security impact.

The previous verification's SC4 was marked VERIFIED. The 14-08 rewrite added an `args_mismatch` path that did not exist before. That new path does not write an audit row — this is a regression relative to AUD-01 introduced by the rewrite.

---

### Dispatcher Enforcement Order (Verified Against Current tools.py)

```
EVERY call (steps 1-3, including replays):
  1. IN-03 agent_id guard    → precondition error if empty
  2. check_capability_access → auth-only, no Redis; fail-closed
  3. reserve_idempotency     → atomic INSERT ON CONFLICT DO NOTHING RETURNING
      "replay"       → return stored result  (no rate INCR, no adapter, no audit) ✓ WR-01
      "args_mismatch"→ return is_error        (no rate INCR, no adapter, NO AUDIT) ✗ AUD-01 GAP
      "in_progress"  → return is_error        (no rate INCR, no adapter, no audit — coordination)
      "reserved"     → proceed as winner

WINNER path (steps 4-7):
  4. apply_rate_and_constraint_checks → Redis INCR+EXPIRE pipeline; denial → release + audit
  5. call_actor_gate                  → block → release + audit
  6. adapter execute                  → error → release + audit
  7. write_audit_row (success) + finalize_idempotency + return
```

---

### New Artifacts (Gap-Closure Plans 14-05..08)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/api/alembic/versions/0015_idempotency_reservation.py` | ADD COLUMN status/args_hash/reserved_at; DROP NOT NULL on result | VERIFIED | revision="0015", down_revision="0014"; ADD COLUMN IF NOT EXISTS for all 3; result DROP NOT NULL; downgrade backfills NULL → '{}' before SET NOT NULL (T-14-05-02) |
| `apps/api/app/models/tool_idempotency_key.py` | Updated ORM with reservation columns | VERIFIED | result: Mapped[dict \| None] nullable=True; status NOT NULL DEFAULT 'completed'; args_hash nullable; reserved_at NOT NULL DEFAULT now(); UniqueConstraint retained |
| `apps/api/app/services/transactional/idempotency.py` | Reservation, compute_args_hash, reserve/finalize/release functions | VERIFIED | Reservation frozen dataclass; compute_args_hash excludes idempotency_key; reserve_idempotency atomic INSERT...ON CONFLICT DO NOTHING RETURNING; finalize UPDATE status='completed' scoped to status='pending'; release DELETE scoped to status='pending'; all functions use asyncio.to_thread; no Redis import |
| `apps/api/app/services/transactional/enforcement.py` | check_capability_access (side-effect-free) + apply_rate_and_constraint_checks (side-effecting) | VERIFIED | _json_safe() coerces datetime/UUID for JSONB (CR-01); check_capability_access: no Redis, only no_envelope_row + disabled checks; apply_rate_and_constraint_checks: pipeline INCR+EXPIRE (IN-01), explicit None-check (IN-02); CERT_REQUIRED by default + REDIS_TLS_INSECURE flag (WR-04); asyncio.to_thread on both DB and Redis calls (WR-03) |
| `apps/api/app/services/transactional/audit.py` | asyncio.to_thread offload | VERIFIED | _sync_write() lambda wrapped in await asyncio.to_thread(_sync_write) (WR-03); TypeError guard on non-dict snapshot retained |
| `apps/api/app/services/transactional/tools.py` | Rewritten dispatcher (14-08 order) + IN-03 + WR-05 confirm_action gate | VERIFIED (with AUD-01 gap noted) | Dispatcher imports check_capability_access + apply_rate_and_constraint_checks + reserve/finalize/release; step ordering confirmed; confirm_action_tool calls check_capability_access before DB write (WR-05); IN-03 guard at top of both dispatcher and confirm_action_tool; **args_mismatch at lines 210-224 does NOT write audit row** |
| `apps/api/tests/unit/test_transactional_tools.py` | 66 tests covering new dispatcher paths | VERIFIED | 66 test functions present; covers replay (TestIdempotencyReplay, 4 tests confirming rate_mock not called, adapter not called, audit not written), args_mismatch (TestArgsMismatch), in_progress, WR-01, WR-02, WR-05, IN-03, AUD-01 symmetry for the paths it covers |
| `apps/api/tests/unit/test_transactional_offload.py` | 3 tests for asyncio.to_thread coverage | VERIFIED | write_audit_row, check_capability_access, apply_rate_and_constraint_checks each assert to_thread is invoked |
| `apps/api/tests/integration/test_idempotency_concurrency.py` | 3 integration tests (INTEGRATION_TESTS_ENABLED-gated) | VERIFIED (gated) | 3 test functions present; threading.Barrier concurrent-winner test; release-allows-re-reservation; args_mismatch-returns-error-not-stale-replay; all 3 gated by INTEGRATION_TESTS_ENABLED=1 |
| `apps/api/tests/integration/test_transactional_idempotency_e2e.py` | 2 e2e integration tests (INTEGRATION_TESTS_ENABLED-gated) | VERIFIED (gated) | 2 test functions present; gated by INTEGRATION_TESTS_ENABLED=1 |

---

### Pre-Existing Artifacts (Carried Forward as VERIFIED)

All artifacts from the initial verification remain intact and unregressed. Full table in initial 14-VERIFICATION.md. Quick regression check:

- `schemas.py`: 14 models, all typed, idempotency_key on 6 mutating inputs, ConfirmActionInput no key — no change
- `registry.py`: 6 mutating=True + confirm_action mutating=False — no change
- `provider_adapter.py`: 6 abstract + 6 stub methods — no change
- `migration 0014`: 4 tables, UNIQUE constraints — no change
- `models/` (4 ORM files + `__init__.py`): no regressions
- `agent_tools.py`: 4 original tools + 7 transactional tools in build_tool_server (11 total) — no change
- `agent.py allowed_tools`: 4 original + 7 new mcp__customer-tools__* entries (11 total) — no change

---

### Key Link Verification (New Links from Gap-Closure)

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `tools.py` dispatcher step 2 | `enforcement.check_capability_access` | Direct import + await | WIRED | Lines 69-72 import; line 165 call on every path |
| `tools.py` dispatcher step 3 | `idempotency.reserve_idempotency` | Direct import + await | WIRED | Lines 73-78 import; line 196 call |
| `tools.py` dispatcher step 4 | `enforcement.apply_rate_and_constraint_checks` | Direct import + await | WIRED | Lines 69-72 import; line 247 call (winners only, after reserve) |
| `tools.py` dispatcher step 7 | `idempotency.finalize_idempotency` | Direct import + await | WIRED | Line 362 call (success path only) |
| `tools.py` denial/error paths | `idempotency.release_idempotency` | Direct import + await | WIRED | Lines 250, 281, 321 (rate denial, actor block, adapter error) |
| `tools.py` replay → return | `reserve_idempotency` | Reservation.state == "replay" | WIRED | Line 208: return reservation.result before rate checks |
| `confirm_action_tool` | `enforcement.check_capability_access` | Import + await | WIRED | Line 594: _snapshot, denial = await check_capability_access(agent_id, validated.skill) |
| `enforcement._json_safe()` | audit JSONB column | Applied at snapshot construction | WIRED | Line 250: {k: _json_safe(v) for k, v in dict(row).items()} — datetime/UUID coerced |

---

### Behavioral Spot-Checks

Unit test suite state per orchestrator: 210 passed / 2 skipped (local environment).

| Behavior | Evidence | Status |
|----------|----------|--------|
| replay short-circuits BEFORE rate INCR | test_transactional_tools.py TestIdempotencyReplay::test_replay_does_not_call_rate_checks — rate_mock.assert_not_called() | PASS (unit) |
| replay returns stored result | TestIdempotencyReplay::test_replay_returns_stored_result | PASS (unit) |
| replay does not call adapter | TestIdempotencyReplay::test_replay_does_not_call_adapter | PASS (unit) |
| replay does not write audit row | TestIdempotencyReplay::test_replay_does_not_write_audit_row | PASS (unit) |
| capability denial writes audit row | TestCapabilityDenial | PASS (unit) |
| args_mismatch returns is_error | TestArgsMismatch | PASS (unit) |
| args_mismatch does NOT write audit row | (no test) | NOT COVERED — audit row absence is the gap |
| check_capability_access has no Redis call | TestEnforcementSplit in test_capability_enforcement.py | PASS (unit) |
| Redis pipeline INCR+EXPIRE atomic | TestEnforcementSplit: pipe.execute asserted; direct incr/expire NOT asserted | PASS (unit) |
| asyncio.to_thread for audit/enforcement/idempotency | test_transactional_offload.py (3 tests) | PASS (unit) |
| exactly-once concurrent redelivery | test_idempotency_concurrency.py | SKIPPED (INTEGRATION_TESTS_ENABLED=1) |

---

### Prohibition Checks (Carried Forward)

All 13 prohibitions from Plans 01-08 remain CLEAR — no regressions found:

- capability_envelopes.enabled MUST NOT default to true: CLEAR
- Tables MUST NOT be created on per-tenant Neon: CLEAR
- tool_idempotency_keys MUST NOT omit UNIQUE constraint: CLEAR
- No free-form blob fields: CLEAR
- mutating MUST be literal definition-time flag: CLEAR
- call_actor_gate MUST NOT contain Phase-15 Haiku logic: CLEAR
- No A2A/ACP server or network surface: CLEAR
- No fail-open path: CLEAR
- Idempotency keys MUST NOT use Redis: CLEAR (no Redis import in idempotency.py — grep confirms zero matches)
- Actor seam MUST NOT be bypassable for a mutating tool: CLEAR — every fresh execution (reserved state) calls call_actor_gate at step 5
- Replay MUST NOT call provider adapter or write duplicate audit row: CLEAR — replay returns at step 3 before step 4 (rate), step 5 (actor), and step 6 (adapter)
- confirm_action MUST NOT call provider adapter or require idempotency key: CLEAR — confirm_action_tool writes PendingConfirmation row only; ConfirmActionInput has no idempotency_key
- Existing escalate_to_human/retrieve/lookup_structured/clarify MUST NOT be removed: CLEAR

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TXN-01 | 14-02, 14-04 | Six typed Pydantic tools + confirm_action; no blob inputs | SATISFIED | 14 schemas; all typed scalars; spot-checks pass; no regression |
| TXN-02 | 14-01, 14-06, 14-08 | Idempotency key replay from control-DB UNIQUE table | PARTIAL | Code: atomic reserve-before-execute, finalize, release, args_hash all present; ON CONFLICT RETURNING confirmed. DB-level exactly-once requires live Postgres (integration gate). |
| TXN-03 | 14-02 | mutating: true/false at definition time | SATISFIED | TOOL_REGISTRY literal flags; no change |
| TXN-04 | 14-04 | confirm_action added; escalate_to_human retained | SATISFIED | Both present in build_tool_server and allowed_tools; no regression |
| TXN-05 | 14-02 | A2A-compatible shape without A2A surface | SATISFIED | No change |
| CAP-01 | 14-01 | capability_envelopes control-DB table with UNIQUE(agent_id, skill) | SATISFIED | No change to migration 0014 |
| CAP-02 | 14-03, 14-07 | Enforcement middleware rejects disabled/over-limit/constraint-violating skills | SATISFIED | check_capability_access + apply_rate_and_constraint_checks; capability.denial logged on all denial paths; fail-closed |
| AUD-01 | 14-01, 14-03, 14-04, 14-08 | tool_calls_audit writes 100% of mutating calls | PARTIAL | 5 of 6 required paths write audit rows (capability denial, rate denial, actor block, adapter error, success). args_mismatch path (lines 210-224 of tools.py) does NOT write a row — violates "100% of mutating calls." |
| AUD-02 | 14-01, 14-04, 14-08 | pending_confirmations table; confirm_action writes rows | SATISFIED | WR-05 closed: confirm_action now gated behind check_capability_access; row written only on pass |

---

### Anti-Patterns Found

| File | Location | Pattern | Severity | Impact |
|------|----------|---------|----------|--------|
| `tools.py` | Lines 210-224 | `args_mismatch` returns is_error without `write_audit_row` | BLOCKER | AUD-01 gap: a capability-authenticated mutation attempt with a reused key is not recorded in tool_calls_audit. The snapshot is available (capability passed at step 2); the row can be written with error="idempotency.args_mismatch". |
| `tools.py` | Line 627-629 | `confirm_action_tool` uses `get_sync_db()` directly (blocking sync DB call, no asyncio.to_thread) | WARNING | WR-03 partial resolution: enforcement.py, audit.py, and idempotency.py were all fixed to use asyncio.to_thread; confirm_action_tool's DB write was not included in the 14-07 offload scope. Stalls the event loop during the pending_confirmations insert. |
| `actor_seam.py` | Line 8 | Stale docstring: "capability check → [call_actor_gate] → idempotency check" — shows pre-refinement order; actual order is capability → idempotency → actor | WARNING (pre-existing) | Documentation only. Code is correct. Carried forward from initial verification. |

No TBD / FIXME / XXX markers found in any phase-14 or gap-closure file.

---

### Human Verification Required

#### 1. Migration 0014 + 0015 DB Roundtrip (Integration Gate)

**Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/unit/test_migration_0014.py tests/unit/test_migration_0015.py -q`
**Expected:** Migration 0014 creates all 4 tables with correct columns and UNIQUE constraints; migration 0015 adds status (DEFAULT 'completed'), args_hash (nullable), reserved_at (NOT NULL DEFAULT now()) and relaxes result to nullable; downgrade backfills NULL results to `'{}'::jsonb` before re-asserting NOT NULL (T-14-05-02); upgrade-downgrade-upgrade roundtrip succeeds for both migrations.
**Why human:** Both integration roundtrip tests require a live control DB. 2 tests gated behind INTEGRATION_TESTS_ENABLED=1 (one per migration).

#### 2. Idempotency Concurrency Proof (Integration Gate)

**Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/integration/test_idempotency_concurrency.py -q`
**Expected:** Two concurrent threads with a threading.Barrier produce exactly 1 reserved + 1 in_progress/replay outcome; single DB row for the key; third reservation after finalize returns replay with result. release_allows_re_reservation test passes. args_mismatch test: same key + different business args → args_mismatch state, result=None.
**Why human:** Proves exactly-once-under-concurrent-redelivery is DB-enforced. Requires live PostgreSQL with migration 0015 applied. Cannot be verified by unit tests which mock get_sync_db.

#### 3. Idempotency E2e and UNIQUE Constraint (Integration Gate)

**Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 python -m pytest tests/integration/test_transactional_idempotency_e2e.py -q`
**Expected:** Sequential replay test: two place_order calls with the same idempotency_key — second call returns the cached result; tool_calls_audit has exactly one row; tool_idempotency_keys has exactly one completed row.
**Why human:** End-to-end wire of capability envelope (must exist in DB), reserve_idempotency, adapter, finalize, and audit in a real PostgreSQL environment. The CR-01 JSON-safe snapshot fix is also exercised here (live TIMESTAMPTZ from the real DB).

---

## Gaps Summary

One BLOCKER gap was introduced by the 14-08 dispatcher rewrite: the `args_mismatch` branch (tools.py lines 210-224) returns `is_error` without writing a `tool_calls_audit` row. This violates REQUIREMENTS.md AUD-01 ("100% of mutating calls") and the explicit SC4 requirement for the re-verification. The gap is mechanical to fix — the `snapshot` dict is available at that point (capability check passed at step 2 and returned it), and calling `write_audit_row(..., error="idempotency.args_mismatch")` before the early return closes it. A matching unit test (asserting `audit_mock.assert_called_once()` when `reserve_idempotency` returns `args_mismatch`) is needed alongside.

All ten code-review findings from 14-REVIEW.md are otherwise resolved. The remaining human-verification items are live-DB integration gates (migration roundtrip, concurrency proof, e2e replay) — these are not code defects, they are correctness anchors that require PostgreSQL.

The `confirm_action_tool` blocking DB call (WR-03 partial) is a pre-existing warning-level issue not in the gap-closure scope; it should be added to the Phase-18 pre-deployment checklist or addressed in a follow-up sub-plan.

---

_Verified: 2026-06-29T20:45:00Z_
_Verifier: Claude (gsd-verifier) — re-verification after gap-closure plans 14-05..08_
