---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
verified: 2026-06-29T19:10:00Z
status: human_needed
score: 3/4 must-haves verified
behavior_unverified: 1
overrides_applied: 0
behavior_unverified_items:
  - truth: "A side-effecting tool replayed with the same idempotency key returns the original result and does not re-execute (durable control-DB guard, survives Redis restart / acks_late retries)"
    test: "Issue two place_order calls with the same idempotency_key against a running PostgreSQL instance (INTEGRATION_TESTS_ENABLED=1)"
    expected: "The second call returns the stored result without calling the adapter and without writing a second tool_calls_audit row; the tool_idempotency_keys table contains exactly one row for the key; the UNIQUE constraint prevents double-insert"
    why_human: "Unit tests use mocked DB sessions (MagicMock). The ON CONFLICT DO NOTHING SQL string is verified, and the UNIQUE constraint DDL is in migration 0014. But the actual PostgreSQL constraint enforcement — the correctness anchor for acks_late retries after Redis restart — requires a live control-DB. The test_migration_db_roundtrip in test_migration_0014.py exercises the table but is guarded by INTEGRATION_TESTS_ENABLED=1 (skipped locally). No integration test for idempotency store/check against real Postgres exists yet."
human_verification:
  - test: "Run test_migration_0014.py::test_migration_db_roundtrip with INTEGRATION_TESTS_ENABLED=1"
    expected: "Migration 0014 applies cleanly against real PostgreSQL; all 4 tables exist with the correct columns and UNIQUE constraints (uq_capability_envelopes_agent_skill, uq_tool_idempotency_keys); downgrade drops all 4 tables; upgrade-downgrade-upgrade roundtrip succeeds"
    why_human: "Test is gated behind INTEGRATION_TESTS_ENABLED=1 and requires a live control DB. Cannot be verified in the local-only unit environment."
  - test: "Issue two place_order calls with identical idempotency_key against a live PostgreSQL control DB"
    expected: "Second call returns the cached result (adapter NOT called again, no second audit row in tool_calls_audit, one row in tool_idempotency_keys). Confirms the UNIQUE(agent_id, skill, idempotency_key) constraint enforces ON CONFLICT DO NOTHING correctness end-to-end."
    why_human: "Durable idempotency durability (survives Redis restart, acks_late retries) is a DB-level guarantee. Unit tests mock the DB session and verify the SQL string contains ON CONFLICT; the actual constraint enforcement requires a live Postgres."
---

# Phase 14: Transactional Tool Contract + Capability/Audit Substrate Verification Report

**Phase Goal:** Establish the authorization substrate every transactional action rides on — six typed transactional tools tagged `mutating:true` with idempotency-key handling, the per-skill capability-envelope table + enforcement middleware, and the audit/confirmation tables — so no action can execute without a typed contract, a capability check, and an audit row.
**Verified:** 2026-06-29T19:10:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | The six transactional tools + `confirm_action` exist as typed Pydantic functions; no string-blob/SQL/URL inputs anywhere in the set | VERIFIED | `schemas.py`: 6 mutating Input models + 6 Output models + ConfirmActionInput/Output; all fields are typed scalars; `idempotency_key` present on all 6 mutating inputs; `ConfirmActionInput` correctly has no `idempotency_key`; behavioral spot-check: PlaceOrderInput rejects missing idempotency_key and negative amount_cents |
| 2 | A side-effecting tool replayed with the same idempotency key returns the original result and does not re-execute (durable control-DB guard, survives Redis restart / acks_late retries) | PRESENT_BEHAVIOR_UNVERIFIED | Dispatcher logic verified: cap check runs first, idempotency hit short-circuits before actor seam (behavioral spot-check passed); ON CONFLICT SQL present in `idempotency.py`; UNIQUE constraint in migration DDL; no Redis import in idempotency.py. DB-level UNIQUE enforcement not exercised — unit tests use mocked sessions, not real PostgreSQL. See Human Verification. |
| 3 | A disabled / over-limit / constraint-violating skill call is rejected and logged as `capability.denial` (fail-closed) | VERIFIED | `enforcement.py`: fail-closed (no row → no_envelope_row denial; enabled=false → disabled denial; rate-limit via Redis INCR; max_amount_cents constraint); `structlog.warning("capability.denial")` emitted on all 4 denial paths (5 occurrences in enforcement.py); behavioral spot-check: capability denial returns is_error |
| 4 | Every mutating tool call writes a complete `tool_calls_audit` row (AUD-01) — including capability-denied calls (the AUD-01 symmetry fix) | VERIFIED | `tools.py` dispatcher writes audit row on capability denial (error=`"capability.denial:{reason}"`), actor block (error=`"actor_block"`), adapter success, and adapter error; behavioral spot-check: capability denial wrote audit row with `error.startswith("capability.denial:")` confirmed; commit `b765c13` added the symmetry fix |

**Score:** 3/4 truths directly verified (1 present + behavior-unverified)

### Dispatcher Enforcement Order (Phase-14 Documented Refinement)

The audit notes required confirmation that: (a) capability check runs first on every call, and (b) `call_actor_gate` gates 100% of non-replay executions.

Verified against `tools.py` `_execute_transactional_tool`:

```
EVERY call:     capability check (step 1)
                idempotency lookup (step 2)
  HIT (replay): → return cached (NO actor seam, NO adapter, NO audit row)
  MISS (fresh): → actor seam (step 3) → adapter execute (step 4) → audit (step 5) → store idempotency (step 6)
```

Behavioral spot-check confirmed:
- Replay: capability=1 call, idempotency=1 call, actor gate=0 calls
- Fresh: capability=1, idempotency=1, actor gate=1, audit=1, store=1

The actor_seam.py docstring still describes the pre-refinement order (`capability check → [call_actor_gate] → idempotency check`). The docstring is stale. The actual code order matches the Plan-04 documented optimization. **This is a documentation-only inconsistency — the code is correct.**

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/api/alembic/versions/0014_transactional_substrate.py` | Migration creating 4 control-DB tables | VERIFIED | revision="0014", down_revision="0013"; IF NOT EXISTS guards; capability_envelopes enabled DEFAULT false; UNIQUE(agent_id, skill); UNIQUE(agent_id, skill, idempotency_key) |
| `apps/api/app/models/capability_envelope.py` | CapabilityEnvelope ORM | VERIFIED | UniqueConstraint("agent_id","skill","uq_capability_envelopes_agent_skill"); server_default=text("false") |
| `apps/api/app/models/tool_calls_audit.py` | ToolCallsAudit ORM | VERIFIED | actor_decision/actor_rationale server_default text("''"); all columns present |
| `apps/api/app/models/pending_confirmation.py` | PendingConfirmation ORM | VERIFIED | expires_at nullable (plan spec override); Index on agent_id |
| `apps/api/app/models/tool_idempotency_key.py` | ToolIdempotencyKey ORM | VERIFIED | UniqueConstraint("agent_id","skill","idempotency_key","uq_tool_idempotency_keys") |
| `apps/api/app/models/__init__.py` | All 4 models registered | VERIFIED | All 4 in imports + `__all__`; `python -c "from app.models import CapabilityEnvelope, ToolCallsAudit, PendingConfirmation, ToolIdempotencyKey; print('ok')"` → ok |
| `apps/api/app/services/transactional/schemas.py` | 14 Pydantic v2 models | VERIFIED | 6 mutating Input + 6 Output + ConfirmActionInput/Output; idempotency_key on all 6 mutating inputs; no open dicts; amount_cents/refund_amount_cents Annotated[int, ge=0] |
| `apps/api/app/services/transactional/registry.py` | TransactionalToolDef + TOOL_REGISTRY | VERIFIED | 6 mutating=True + confirm_action mutating=False; all idempotency_required=True for mutating; A2A metadata; TOOL_REGISTRY alias |
| `apps/api/app/services/transactional/provider_adapter.py` | ProviderAdapter ABC + StubProviderAdapter | VERIFIED | 6 abstract async methods + 6 stub implementations returning [STUB]-labelled outputs; 12 async def total |
| `apps/api/app/services/actor_seam.py` | call_actor_gate pass-through stub | VERIFIED | Returns ("approve", "") unconditionally; no Haiku logic |
| `apps/api/app/services/transactional/enforcement.py` | check_capability_envelope (fail-closed) | VERIFIED | 5 occurrences of capability.denial; fail-closed on no row and disabled; Redis INCR rate limit; max_amount_cents constraint; snapshot plain dict |
| `apps/api/app/services/transactional/idempotency.py` | check/store_idempotency (control-DB) | VERIFIED | ON CONFLICT in SQL (4 occurrences); no Redis import; documented per-tool vs turn-level distinction |
| `apps/api/app/services/transactional/audit.py` | write_audit_row | VERIFIED | Called on both success and error paths; actor_decision/actor_rationale empty strings in Phase 14; TypeError on non-dict capability_snapshot |
| `apps/api/app/services/transactional/tools.py` | 6 mutating handlers + confirm_action + dispatcher | VERIFIED | _execute_transactional_tool dispatcher encodes order once; AUD-01 symmetry on capability denial; call_actor_gate before adapter on fresh exec; confirm_action writes pending_confirmations row |
| `apps/api/app/services/agent_tools.py` | build_tool_server extended to 11 tools | VERIFIED | 7 new tools appended; 4 original tools retained |
| `apps/api/app/worker/tasks/runtime/agent.py` | allowed_tools extended | VERIFIED | 7 new mcp__customer-tools__* entries; escalate_to_human retained (6 references) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `transactional/tools.py` dispatcher | `enforcement.check_capability_envelope` | Direct import + await in step 1 | WIRED | `grep -c check_capability_envelope tools.py` = 3 (import + 2 call sites) |
| `transactional/tools.py` dispatcher | `idempotency.check_idempotency` / `store_idempotency` | Direct import + await in steps 2 + 6 | WIRED | check before actor seam; store after success |
| `transactional/tools.py` dispatcher | `actor_seam.call_actor_gate` | Direct import + await in step 3 | WIRED | `grep -c call_actor_gate tools.py` = 4 (import + 3 call sites) |
| `transactional/tools.py` dispatcher | `provider_adapter.get_adapter` | Direct import + await in step 4 | WIRED | adapter_method dispatched via getattr |
| `transactional/tools.py` dispatcher | `audit.write_audit_row` | Direct import + await in steps 1, 3, 5 | WIRED | Called on capability denial, actor block, and always after execute |
| `agent_tools.build_tool_server` | `transactional/tools.py` 7 handlers | Lazy import inside build_tool_server body | WIRED | Avoids circular import; all 7 appended to create_sdk_mcp_server tools=[] |
| `agent.py` allowed_tools | 7 mcp__customer-tools__* names | List literal in ClaudeAgentOptions | WIRED | All 7 new names present; original 4 retained |
| `capability_envelopes` table | `enforcement.check_capability_envelope` | SELECT WHERE agent_id=:a AND skill=:s | WIRED | Fail-closed: no row or enabled=false → denial |
| `tool_idempotency_keys` table | `idempotency.store_idempotency` | INSERT...ON CONFLICT DO NOTHING | WIRED | UNIQUE(agent_id, skill, idempotency_key) enforces durable guard |
| `tool_calls_audit` table | `audit.write_audit_row` | ORM db.add(ToolCallsAudit(...)) | WIRED | Written on all paths including capability denial |
| `pending_confirmations` table | `confirm_action_tool` | ORM db.add(PendingConfirmation(...)) | WIRED | client-generated UUID; 24-hr TTL; no provider adapter |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| PlaceOrderInput rejects missing idempotency_key | `python -c "PlaceOrderInput(product_id=...) raises ValidationError"` | ValidationError raised | PASS |
| IssueRefundInput rejects negative refund_amount_cents | `python -c "IssueRefundInput(refund_amount_cents=-1) raises ValidationError"` | ValidationError raised | PASS |
| call_actor_gate returns ("approve","") pass-through | `asyncio.run(call_actor_gate(...))` | `('approve', '')` | PASS |
| TOOL_REGISTRY: 6 mutating + 1 non-mutating | `python -c "from...registry import TOOL_REGISTRY..."` | 6 mutating=True, confirm_action mutating=False | PASS |
| Dispatcher: capability check runs first on replay | Inline test with mocked cap_pass + idem_hit | cap_pass=1 call, gate=0 calls | PASS |
| Dispatcher: actor seam called on fresh execution | Inline test with mocked cap_pass + idem_miss + gate | gate=1 call | PASS |
| AUD-01 symmetry: capability denial writes audit row | Inline test with cap_deny mock | audit_mock called; error="capability.denial:disabled" | PASS |
| Dispatcher: enforcement order step sequence | Inline test fresh exec | capability=1, idempotency=1, actor=1, audit=1, store=1 | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TXN-01 | 14-02, 14-04 | Six typed Pydantic tools + confirm_action; no string-blob/SQL/URL inputs | SATISFIED | 14 schemas in schemas.py; all fields typed scalars; spot-checks pass |
| TXN-02 | 14-01, 14-03, 14-04 | Idempotency key replay from control-DB UNIQUE table | PARTIAL | SQL + DDL verified; UNIQUE constraint against live PostgreSQL not exercised in unit tests (see Human Verification) |
| TXN-03 | 14-02 | mutating: true/false at definition time | SATISFIED | TOOL_REGISTRY has literal definition-time flags for all 7 skills |
| TXN-04 | 14-04 | confirm_action added; escalate_to_human retained | SATISFIED | confirm_action in build_tool_server and allowed_tools; escalate_to_human in both (6 refs in agent.py) |
| TXN-05 | 14-02 | A2A-compatible shape without A2A surface | SATISFIED | TransactionalToolDef + to_a2a_skill() helper; no network; no A2A server |
| CAP-01 | 14-01 | capability_envelopes control-DB table with UNIQUE(agent_id, skill) | SATISFIED | Migration 0014 creates table with named UNIQUE constraint; enabled DEFAULT false |
| CAP-02 | 14-03 | Enforcement middleware rejects disabled/over-limit/constraint-violating skills | SATISFIED | check_capability_envelope fail-closed; capability.denial logged on all denial paths |
| AUD-01 | 14-01, 14-03, 14-04 | tool_calls_audit writes 100% of mutating calls including capability-denied calls | SATISFIED | AUD-01 symmetry fix in commit b765c13; verified by spot-check and test TestCapabilityDenial |
| AUD-02 | 14-01, 14-04 | pending_confirmations table; confirm_action writes rows | SATISFIED | Table in migration 0014; confirm_action_tool writes PendingConfirmation row with 24-hr TTL |

### Prohibition Checks

All prohibitions from Plans 01–04 verified:

| Prohibition | Status |
|-------------|--------|
| capability_envelopes.enabled MUST NOT default to true | CLEAR — DEFAULT false in DDL and ORM server_default=text("false") |
| Tables MUST NOT be created on per-tenant Neon | CLEAR — down_revision="0013" (control DB); no per-tenant migrations |
| tool_idempotency_keys MUST NOT omit UNIQUE(agent_id, skill, idempotency_key) | CLEAR — CONSTRAINT uq_tool_idempotency_keys in DDL and ORM UniqueConstraint |
| No tool input field may be a free-form blob, SQL, URL, or arbitrary JSON dict | CLEAR — all fields are typed scalars or enums |
| mutating MUST be a literal definition-time flag, never runtime-inferred | CLEAR — literal bool values in TOOL_REGISTRY |
| call_actor_gate MUST NOT contain Phase-15 Haiku logic | CLEAR — returns ("approve","") unconditionally |
| No A2A/ACP server, client, or network surface | CLEAR — metadata only; to_a2a_skill() is a dict helper with no network |
| No fail-open path (missing/disabled envelope → denial, never pass) | CLEAR — enforcement.py check confirms |
| Idempotency keys MUST NOT be read from or written to Redis | CLEAR — no Redis import in idempotency.py; control-DB table only |
| Actor seam MUST NOT be bypassable for a mutating tool | CLEAR — every fresh execution calls call_actor_gate (step 3 in dispatcher); behavioral spot-check confirms |
| A replay MUST NOT call the provider adapter or write a duplicate audit row | CLEAR — idempotency hit returns before actor seam and adapter; no audit row on replay (spot-check confirms gate=0, audit written=0 on replay) |
| confirm_action MUST NOT call a provider adapter or require idempotency key | CLEAR — confirm_action_tool writes PendingConfirmation row only; ConfirmActionInput has no idempotency_key |
| Existing escalate_to_human/retrieve/lookup_structured/clarify tools MUST NOT be removed | CLEAR — all 4 present in build_tool_server and allowed_tools |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `actor_seam.py` | 22–25 | Docstring shows stale enforcement order: `capability check → [call_actor_gate] → idempotency check` | WARNING | Documentation inconsistency only. Actual code in tools.py implements the correct Plan-04 order (idempotency lookup before actor seam). No security impact — capability check still runs first on every call; actor seam still gates 100% of fresh executions. |

No TBD / FIXME / XXX / TODO / HACK / PLACEHOLDER markers found in any phase-14 file.

### Human Verification Required

#### 1. Migration 0014 DB Roundtrip (Integration Gate)

**Test:** Run `cd apps/api && INTEGRATION_TESTS_ENABLED=1 pytest tests/unit/test_migration_0014.py -x -q`
**Expected:** All 12 tests pass (11 unit + 1 DB roundtrip); migration creates all 4 tables with correct columns and UNIQUE constraints; downgrade drops all 4 tables; upgrade-downgrade-upgrade roundtrip succeeds against real PostgreSQL
**Why human:** Test gated behind `INTEGRATION_TESTS_ENABLED=1` — requires a live control DB. Cannot be verified in the local-only environment.

#### 2. Idempotency UNIQUE Constraint End-to-End (Integration Gate)

**Test:** Issue two `place_order` calls with the same `idempotency_key` against a running system with a real PostgreSQL control DB; or run `INTEGRATION_TESTS_ENABLED=1 pytest tests/unit/test_tool_idempotency.py` if an integration fixture is added
**Expected:** Second call returns the cached result (adapter NOT called again); tool_calls_audit has exactly one row for the idempotency key; tool_idempotency_keys has exactly one row; no exception on the second store call (ON CONFLICT DO NOTHING)
**Why human:** Unit tests mock the DB session and verify the ON CONFLICT SQL string is present. The actual PostgreSQL UNIQUE constraint enforcement — which guarantees durability under acks_late retries and Redis restart scenarios — requires a live control DB. This is the correctness anchor for TXN-02.

---

## Gaps Summary

No BLOCKER gaps found. All four success criteria have substantive implementation in the codebase. The single human verification item (TXN-02 live-DB durability test) is an integration gate, not a code defect — the ON CONFLICT SQL is correct, the UNIQUE constraint DDL is correct, and the dispatcher logic is verified by behavioral spot-check.

The actor_seam.py stale docstring is a documentation inconsistency with no security impact — the code is correct.

---

_Verified: 2026-06-29T19:10:00Z_
_Verifier: Claude (gsd-verifier)_
