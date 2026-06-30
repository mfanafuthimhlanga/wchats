---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "06"
subsystem: transactional-dispatcher
tags: [INT-02, credential-resolution, provider-dispatch, tools-wiring, e2e]
dependency_graph:
  requires: [16-01, 16-02, 16-03, 16-04, 16-05]
  provides: [get_adapter_for_skill, tools-step-6-wired, dispatch-unit-tests, e2e-integration-test]
  affects: [transactional-dispatcher, provider-adapter, tools, idempotency-audit]
tech_stack:
  added: []
  patterns:
    - "get_adapter_for_skill async factory with HKDF decrypt + provider_type dispatch"
    - "Lazy imports inside async function to break circular import cycles"
    - "AsyncMock patching of async factory in existing dispatcher tests"
    - "env-gated pytest.mark.skipif for integration tests"
key_files:
  created:
    - apps/api/tests/unit/test_provider_adapter_dispatch.py
    - apps/api/tests/integration/test_integration_e2e.py
  modified:
    - apps/api/app/services/transactional/provider_adapter.py
    - apps/api/app/services/transactional/tools.py
    - apps/api/tests/unit/test_transactional_tools.py
decisions:
  - "Lazy imports (not module-top) for adapter classes and _tenant_id_var inside get_adapter_for_skill to avoid circular imports: adapters import ProviderAdapter from provider_adapter.py, and agent_tools imports tools.py. Module-top imports would cause AlreadyFinalized or ImportError at load time."
  - "credential_service symbols imported at module level in provider_adapter.py (no cycle) so test patches can target provider_adapter._fetch_credential_config reliably."
  - "test_transactional_tools.py updated (Rule 1 deviation): all get_adapter patches changed to get_adapter_for_skill with AsyncMock since the factory is now async — without this fix all 83 existing dispatcher tests would fail."
metrics:
  duration_seconds: 1097
  completed_date: "2026-06-30"
  tasks_completed: 3
  files_modified: 5
status: complete
---

# Phase 16 Plan 06: INT-02 Keystone Integration Summary

**One-liner:** `get_adapter_for_skill` wired to tools.py step 6 — per-tenant HKDF credential decrypt + provider_type dispatch into four real adapters, raw secret never crossing audit/agent/log boundaries.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | get_adapter_for_skill (decrypt + dispatch) + unit tests | ade82d1 | provider_adapter.py, test_provider_adapter_dispatch.py |
| 2 | tools.py step 6 wiring + error branch | ef0d161 | tools.py, test_transactional_tools.py |
| 3 | Env-gated e2e integration test | 0da77a0 | test_integration_e2e.py |

## What Was Built

### get_adapter_for_skill (provider_adapter.py)

Async factory that is the single INT-02 credential-resolution entry point:

1. Calls `_fetch_credential_config(conn_str, skill)` → returns a `_CredentialConfig` or None
2. Reads `tenant_id` from `_tenant_id_var` (lazy import; ContextVar set by build_tool_server)
3. Derives per-tenant Fernet via `_derive_tenant_fernet(HKDF(PLATFORM_CREDENTIAL_KEY, salt=tenant_id))`
4. Decrypts `config.credential_data` → `CredentialHandle(_raw=raw_cred)` (redacted repr)
5. Dispatches by `provider_type`:
   - `"stripe"` → `StripeAdapter(handle, currency_code)`
   - `"shopify"` → `ShopifyAdapter(handle, shop_url=config_data["shop_url"], currency_code)`
   - `"woocommerce"` → `WooCommerceAdapter(handle, site_url=config_data["site_url"], currency_code)`
   - `"calendly"` → `CalendlyAdapter(handle, config_data)`
   - unknown → `ProviderNotConfiguredError`

Raw credential string exists only in the `raw_cred` local variable within this stack frame and goes out of scope when the function returns.

### tools.py Step 6 (was: get_adapter(agent_id), now: await get_adapter_for_skill)

```python
# Step 6 — before (Phase 14):
adapter = get_adapter(agent_id)

# Step 6 — after (Phase 16):
try:
    adapter = await get_adapter_for_skill(skill, agent_id, conn_str)
except (ProviderNotConfiguredError, CredentialDecryptionError) as exc:
    await release_idempotency(agent_id, skill, validated.idempotency_key)
    await write_audit_row(..., error=f"provider.not_configured:{exc}")
    return {"content": [...], "is_error": True}
```

The 7-step enforcement order (capability check → reserve → rate → actor → **adapter** → audit → finalize) is preserved. Only step 6 acquisition changed.

### Tests

- `test_provider_adapter_dispatch.py`: 7 unit tests covering all 4 dispatch paths + None config + decrypt failure + unknown provider_type. Mocks `_fetch_credential_config` at `provider_adapter` module level.
- `test_transactional_tools.py`: Updated 83 existing tests to patch `get_adapter_for_skill` with `AsyncMock` (see Deviations).
- `test_integration_e2e.py`: 2 env-gated integration tests (skip by default; `INTEGRATION_TESTS_ENABLED=1` to run). Uses real Postgres for idempotency/capability tables; mocks adapter SDK calls.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_transactional_tools.py to patch get_adapter_for_skill instead of get_adapter**
- **Found during:** Task 2
- **Issue:** All 83 existing dispatcher tests patched `f"{_T}.get_adapter"`. After changing `tools.py` to import `get_adapter_for_skill` (not `get_adapter`), those patches would raise `AttributeError: <module> does not have attribute 'get_adapter'`. Every test that reached step 6 would also fail because `MagicMock()` is not awaitable but `get_adapter_for_skill` is now async.
- **Fix:** Replaced all `patch(f"{_T}.get_adapter", MagicMock(...))` with `patch(f"{_T}.get_adapter_for_skill", AsyncMock(...))`. Also updated the structural test `test_actor_gate_called_before_get_adapter_in_dispatcher` to look for `"get_adapter_for_skill("` instead of `"get_adapter("` in the tools.py source.
- **Files modified:** `apps/api/tests/unit/test_transactional_tools.py`
- **Commit:** ef0d161

**2. [Rule 2 - Correctness] Used lazy imports instead of module-top adapter imports**
- **Found during:** Task 1 (pre-implementation analysis)
- **Issue:** The plan action said "add module-top imports for StripeAdapter, ShopifyAdapter, WooCommerceAdapter, CalendlyAdapter". But each adapter imports `ProviderAdapter` from `provider_adapter.py`. A module-top import at `provider_adapter.py` line 18 would trigger `stripe_adapter.py` → `provider_adapter.py` import while `ProviderAdapter` class is not yet defined → `ImportError: cannot import name 'ProviderAdapter'`. Similarly, `_tenant_id_var` from `agent_tools.py` has a circular chain: `agent_tools → tools → provider_adapter`.
- **Fix:** Adapter classes (`StripeAdapter`, etc.) and `_tenant_id_var` are lazy-imported inside `get_adapter_for_skill` function body. `credential_service` symbols are safe at module level (no cycle) and are imported there for testability (patch target is `provider_adapter._fetch_credential_config`).
- **Files modified:** `apps/api/app/services/transactional/provider_adapter.py`
- **Commit:** ade82d1

## Threat Surface Scan

All threat mitigations from the plan's `<threat_model>` are implemented:

| Threat | Status |
|--------|--------|
| T-16-04 (confused deputy) | Actor gate (step 5) runs before get_adapter_for_skill (step 6) — ordering verified by structural test |
| T-16-01 (credential in audit/log) | CredentialHandle has redacted __repr__; raw_cred exists only in get_adapter_for_skill stack frame; write_audit_row receives typed result, not handle |
| T-16-02 (SSRF via provider URL) | shop_url/site_url extracted from config_data inside get_adapter_for_skill, never from tool args |
| T-16-09 (PII in audit.arguments) | Accepted (Phase 18 scope) |
| T-16-cfg (stuck reservation on unconfigured credential) | ProviderNotConfiguredError/CredentialDecryptionError trigger release_idempotency + audit row + is_error |

No new threat surface introduced — no new network endpoints, auth paths, or schema changes at trust boundaries.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| provider_adapter.py exists | FOUND |
| tools.py exists | FOUND |
| test_provider_adapter_dispatch.py exists | FOUND |
| test_integration_e2e.py exists | FOUND |
| 16-06-SUMMARY.md exists | FOUND |
| Commit ade82d1 (Task 1) | FOUND |
| Commit ef0d161 (Task 2) | FOUND |
| Commit 0da77a0 (Task 3) | FOUND |
| 94 unit+skip tests pass | PASSED (94 passed, 2 skipped) |
