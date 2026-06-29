---
phase: 14-transactional-tool-contract-capability-audit-substrate-typed
plan: "02"
subsystem: transactional-tool-contract
tags: [pydantic, typed-schemas, tool-registry, provider-adapter, actor-seam, a2a-compat, tdd]
status: complete

dependency_graph:
  requires:
    - 14-01  # migration 0014 + ORM models for control-DB substrate
  provides:
    - transactional/schemas.py     # 14 Pydantic models consumed by Plan 04 tool handlers
    - transactional/registry.py    # TOOL_REGISTRY consumed by Plan 03 enforcement
    - transactional/provider_adapter.py  # StubProviderAdapter consumed by Plan 04 tool stubs
    - actor_seam.py                # call_actor_gate seam consumed by Plan 03 + Phase 15
  affects:
    - 14-03  # enforcement/audit/idempotency helpers import TOOL_REGISTRY
    - 14-04  # tool handlers import schemas + registry + provider_adapter + actor_seam

tech_stack:
  added:
    - pydantic v2 Annotated + Field(ge=...) for integer blast-radius fields
  patterns:
    - TDD: RED commit (8165c78) → 3x GREEN commits (3a6ec8a, 7cf3539, 9d2e260)
    - ABC + StubProviderAdapter pattern for offline-testable Phase-14 contract
    - Module-level singleton (_STUB_ADAPTER) + factory (get_adapter) pattern
    - Literal definition-time flags on TransactionalToolDef (never runtime-inferred)

key_files:
  created:
    - apps/api/app/services/transactional/__init__.py
    - apps/api/app/services/transactional/schemas.py
    - apps/api/app/services/transactional/registry.py
    - apps/api/app/services/transactional/provider_adapter.py
    - apps/api/app/services/actor_seam.py
    - apps/api/tests/unit/test_transactional_contract.py
  modified: []

decisions:
  - "[14-02] All 14 Pydantic model imports are at module level in the test file — creating all 3 impl modules was required before any tests could be collected; TDD RED covered all tasks in one commit, GREEN committed per-task"
  - "[14-02] confirm_action tagged mutating=False — it writes a pending_confirmations row but does not call a provider; the UNIQUE constraint on pending_confirmations (not idempotency_keys) prevents duplicate rows"
  - "[14-02] actor_seam.py lives in services/ not transactional/ — it is imported by Phase 15 independently of the full transactional stack"
  - "[14-02] to_a2a_skill() falls back gracefully when sdk_tool is None — Phase-14 tests run without SDK dependency"
  - "[14-02] book_slot.requires_identity_verification=False — lower-risk scheduling action; confirmed per research Cluster 2 table"

metrics:
  duration: ~7 min
  completed: "2026-06-29"
  tasks_completed: 3
  files_created: 6
  tests_added: 50
  tests_passing: 50
---

# Phase 14 Plan 02: Typed Tool Contract (Schemas + Registry + Provider Adapter + Actor Seam) Summary

**One-liner:** 14 Pydantic v2 typed schemas (6 Input + 6 Output + ConfirmAction pair), definition-time TransactionalToolDef registry with A2A metadata and mutating flags, offline StubProviderAdapter behind ProviderAdapter ABC, and pass-through call_actor_gate seam — zero DB or SDK dependency.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| RED | Failing tests for all 3 tasks | 8165c78 | `test_transactional_contract.py`, `transactional/__init__.py` |
| 1 GREEN | Pydantic schemas (14 models) | 3a6ec8a | `transactional/schemas.py` |
| 2 GREEN | TransactionalToolDef registry | 7cf3539 | `transactional/registry.py` |
| 3 GREEN | ProviderAdapter + StubProviderAdapter + actor_seam | 9d2e260 | `provider_adapter.py`, `actor_seam.py` |

## What Was Built

### schemas.py — 14 Pydantic v2 models

6 mutating Input models (`PlaceOrderInput`, `CancelOrderInput`, `IssueRefundInput`, `UpdateSubscriptionInput`, `BookSlotInput`, `UpdateCustomerRecordInput`), each with `idempotency_key: Annotated[str, Field(...)]` as a required field, plus fully typed scalar fields. `PlaceOrderInput.amount_cents` and `IssueRefundInput.refund_amount_cents` are `Annotated[int, Field(ge=0)]` for the Plan-03 max_amount_cents blast-radius constraint. 6 matching Output models. `ConfirmActionInput`/`ConfirmActionOutput` pair (non-mutating — no idempotency_key). All 14 models use `Field(description=...)` on every field so `model_json_schema()` is self-describing.

### registry.py — TransactionalToolDef + TOOL_METADATA

`TransactionalToolDef` dataclass with: `skill_name`, `mutating`, `idempotency_required`, `requires_identity_verification`, `a2a_input_modes`, `a2a_output_modes`, `examples`, `sdk_tool=None`. `TOOL_METADATA` dict with 7 entries (6 mutating + confirm_action). `TOOL_REGISTRY` is the same-object alias. All flags are literal definition-time values (T-14-02-02). `to_a2a_skill()` produces the A2A v1.2 Agent Card dict — no network. `requires_identity_verification=False` for `book_slot`; `True` for the other 5 mutating tools.

### provider_adapter.py — ProviderAdapter ABC + StubProviderAdapter

`ProviderAdapter(ABC)` with 6 abstract async methods, each typed `(args: <Input>, agent_id: str) -> <Output>`. `StubProviderAdapter` implements all 6 returning `[STUB]`-labelled outputs (`f"stub-{uuid4()}"` ids, no network, no real side effects — T-14-02-03). Module-level `_STUB_ADAPTER` singleton and `get_adapter(agent_id=None)` factory.

### actor_seam.py — call_actor_gate pass-through stub

`async def call_actor_gate(skill, arguments, capability_snapshot, conversation_id, agent_id) -> tuple[str, str]` returning `("approve", "")` unconditionally. Docstring documents Phase 15 fills the body and that decision ∈ {approve, block, require_human}. Positioned inside tool handlers after capability check, before idempotency check (not SDK hooks — LANDMINE 1 per RESEARCH.md).

## Test Coverage

50 tests passing in `test_transactional_contract.py`:
- Missing `idempotency_key` → `ValidationError` for all 6 mutating inputs
- Wrong type / negative `amount_cents` or `refund_amount_cents` → `ValidationError`
- Valid payloads instantiate without error
- `model_json_schema()` has `type:object` and `properties` for all 14 models
- `TOOL_REGISTRY` has all 7 skills; all 6 mutating skills have `mutating=True`
- `confirm_action` has `mutating=False`, `idempotency_required=False`
- All tools have `a2a_input_modes`, `a2a_output_modes` present
- `requires_identity_verification` correct per Cluster-2 table
- `StubProviderAdapter` all 6 methods return `[STUB]`-labelled output
- `get_adapter()` returns `ProviderAdapter` / `StubProviderAdapter` instance
- `call_actor_gate` returns `("approve", "")` for any inputs

## Verification

```
cd apps/api && pytest tests/unit/test_transactional_contract.py -x -q
→ 50 passed in 0.65s

grep -c 'idempotency_key' apps/api/app/services/transactional/schemas.py
→ 10 (>= 6 required)

grep -c 'mutating' apps/api/app/services/transactional/registry.py
→ 21 (>= 7 required)

grep -c 'async def' apps/api/app/services/transactional/provider_adapter.py
→ 12 (6 abstract + 6 stub, == required)

TOOL_REGISTRY["place_order"].mutating → True
TOOL_REGISTRY["confirm_action"].mutating → False
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocker] Module-level imports require all 3 task modules to exist simultaneously**
- **Found during:** Task 1 GREEN — test collection failed with ImportError on registry import
- **Issue:** The test file has all 3 tasks' imports at module level; Python attempts all imports at collection time. Task 1 alone was untestable until Task 2+3 modules existed.
- **Fix:** All 3 implementation files created before first test run; per-task feat commits still applied (RED covered all tasks in one commit; GREEN applied per-task).
- **Files modified:** No extra files — the 3 feat commits were done in order after creating all implementations.

None other — plan executed as written.

## Known Stubs

The following are intentional stubs per the plan's explicit Phase-14 scope:

| Stub | File | Reason |
|------|------|--------|
| `call_actor_gate` returns `("approve", "")` | `actor_seam.py` | Phase-15 fills the Haiku call body |
| `StubProviderAdapter.*` returns `[STUB]` outputs | `provider_adapter.py` | Phase-16 implements real adapters |
| `TransactionalToolDef.sdk_tool = None` | `registry.py` | Plan-04 attaches the @tool-decorated instance |

These stubs are intentional (plan-documented) and do not prevent this plan's goal from being achieved. The goal is the pure-contract layer — all schemas, flags, and the offline adapter are complete.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries introduced by this plan. The schemas.py, registry.py, provider_adapter.py, and actor_seam.py modules are pure in-memory Python with no DB or network connections.

Mitigations per threat register:
- T-14-02-01 (Tampering — coerced args): Pydantic schemas reject blobs/SQL/URLs/open-dicts; amount fields are `int ge=0`. VERIFIED.
- T-14-02-02 (Elevation of Privilege — runtime-inferred mutating flag): All flags are literal in TOOL_REGISTRY. VERIFIED.
- T-14-02-03 (Tampering — real side effects in Phase 14): StubProviderAdapter has no network calls. VERIFIED.
- T-14-02-SC (Supply chain): No new packages added.

## Self-Check: PASSED

Files created:
- `apps/api/app/services/transactional/__init__.py` FOUND
- `apps/api/app/services/transactional/schemas.py` FOUND
- `apps/api/app/services/transactional/registry.py` FOUND
- `apps/api/app/services/transactional/provider_adapter.py` FOUND
- `apps/api/app/services/actor_seam.py` FOUND
- `apps/api/tests/unit/test_transactional_contract.py` FOUND

Commits:
- 8165c78 (RED test) FOUND
- 3a6ec8a (schemas GREEN) FOUND
- 7cf3539 (registry GREEN) FOUND
- 9d2e260 (provider_adapter + actor_seam GREEN) FOUND
