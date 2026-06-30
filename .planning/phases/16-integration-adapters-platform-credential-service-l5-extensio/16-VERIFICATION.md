---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
verified: 2026-07-01T00:00:00Z
status: human_needed
score: 2/3 must-haves verified
behavior_unverified: 1
overrides_applied: 0
behavior_unverified_items:
  - truth: "Each of Shopify / WooCommerce / Stripe / Calendly performs its real action behind the typed tool contract"
    test: "Run the live Stripe gate: export STRIPE_TEST_MODE_ENABLED=1 STRIPE_TEST_API_KEY=<rk_test_...> STRIPE_TEST_CHARGE_ID=<ch_...> and execute pytest tests/integration/test_stripe_live.py -m integration -x -q -s from apps/api/"
    expected: "test_stripe_live_refund_and_idempotency_replay passes with a real re_... refund_id; a second call with the same idempotency_key returns the identical re_... id; no sk_/rk_ key prefix appears in stdout or logs"
    why_human: "Real HTTP state transition to Stripe requires live test-mode credentials (STRIPE_TEST_MODE_ENABLED + STRIPE_TEST_API_KEY + STRIPE_TEST_CHARGE_ID) not present in the local dev environment. Deferred per 16-UAT.md consistent with Phase 13/14/15 live-gate deferral pattern."
human_verification:
  - test: "Live Stripe test-mode refund + idempotency replay gate (INT-05, T-16-08)"
    expected: "StripeAdapter.issue_refund issues a real test-mode refund (re_... id returned). A second call with the same idempotency_key returns the same refund_id (Stripe native Idempotency-Key replay). No raw key material (sk_/rk_ prefix) in stdout or logs."
    why_human: "Requires live Stripe test-mode credentials that were not available during autonomous execution. The test file tests/integration/test_stripe_live.py is authored, correct, and skips cleanly without env vars. See 16-UAT.md for full runbook."
---

# Phase 16: Integration Adapters + Platform Credential Service Verification Report

**Phase Goal:** Wire the transactional tools to real providers via adapters backed by encrypted, agent-invisible credentials resolved through a platform credential service — so an agent can take real actions without any code path ever seeing a raw credential.
**Verified:** 2026-07-01T00:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `integration_credentials` is Fernet-encrypted and never read by agent code; the credential service returns only short-lived in-memory handles | VERIFIED | `0007_integration_credentials.py` creates BYTEA column; `_derive_tenant_fernet` implements HKDF(SHA-256, salt=tenant_id); `CredentialHandle.__repr__` returns `<CredentialHandle:redacted>`; `get_adapter_for_skill` is the only decrypt path, called only from `tools.py` step 6, not from any FastAPI route; tests `test_hkdf_per_tenant_isolation` and `test_handle_repr_redacted` both pass |
| 2 | Each of Shopify / WooCommerce / Stripe / Calendly performs its real action behind the typed tool contract | PRESENT_BEHAVIOR_UNVERIFIED | Code is present and wired: ShopifyAdapter (GraphQL mutations), WooCommerceAdapter (httpx + OAuth1), StripeAdapter (StripeClient), CalendlyAdapter (async httpx); 35 unit tests pass; Stripe live API state transition (real HTTP round-trip with live credentials) not exercised — deferred per 16-UAT.md |
| 3 | Single-currency per tenant is enforced at deploy time | VERIFIED | `provision_integration_credential.py` implements `_check_single_currency` which queries existing rows and calls `sys.exit(1)` on any currency conflict; currency is sourced from `config.currency_code` in all four adapters, never from tool args; `StripeAdapter.__init__` lowercases currency_code; unit tests verify no currency field in tool-arg schemas |

**Score:** 2/3 truths verified (1 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/api/alembic_tenant/versions/0007_integration_credentials.py` | Tenant-DB migration creating `integration_credentials` table with BYTEA `credential_data` | VERIFIED | Exists; `CREATE TABLE IF NOT EXISTS integration_credentials` with `credential_data BYTEA NOT NULL`; `IF NOT EXISTS` guards for idempotent reruns; correct `down_revision = "0006"` |
| `apps/api/app/services/transactional/credential_service.py` | `CredentialHandle`, `_derive_tenant_fernet` (HKDF), `_fetch_credential_config`, `ProviderNotConfiguredError`, `CredentialDecryptionError` | VERIFIED | All symbols present; `_derive_tenant_fernet` creates fresh HKDF per call (AlreadyFinalized avoidance); `CredentialHandle.__repr__/__str__` returns literal `<CredentialHandle:redacted>` |
| `apps/api/app/services/transactional/provider_adapter.py` | `get_adapter_for_skill` dispatch function | VERIFIED | Exists; fetches credential, decrypts with HKDF-derived Fernet, wraps in CredentialHandle, dispatches by provider_type to concrete adapter |
| `apps/api/app/services/transactional/tools.py` | Step-6 adapter dispatch via `get_adapter_for_skill` | VERIFIED | `get_adapter_for_skill` called at line 392 after actor gate; wrapped in try/except that catches `ProviderNotConfiguredError` and `CredentialDecryptionError` |
| `apps/api/app/services/transactional/adapters/shopify_adapter.py` | `ShopifyAdapter` — Admin GraphQL place/cancel/refund | VERIFIED | Implements `orderCreate`, `orderCancel`, `refundCreate` mutations; uses `shopify.Session.temp()` (thread-safe); CR-01 (zero-refund bug) fixed — uses transactions array; CR-02-equivalent thread-safety fix applied |
| `apps/api/app/services/transactional/adapters/woocommerce_adapter.py` | `WooCommerceAdapter` — httpx + OAuth1, NO WooCommerce PyPI package | VERIFIED | Uses `httpx.Client` + `requests_oauthlib.OAuth1`; `WooCommerce` PyPI package absent from pyproject.toml; HTTPS enforced (ValueError on http:// URLs); OAuth1 bridged via `_WooOAuth1Auth(httpx.Auth)` |
| `apps/api/app/services/transactional/adapters/stripe_adapter.py` | `StripeAdapter` — Refunds API, Subscriptions API, Checkout Sessions | VERIFIED | `StripeClient` constructed inside `_sync()` closures (no module-level key); CR-02 fixed — `subscriptions.retrieve` before update, existing item id used; WR-07 fixed — single line item with full `unit_amount`; WR-01 fixed — currency not passed to charge-based refund |
| `apps/api/app/services/transactional/adapters/calendly_adapter.py` | `CalendlyAdapter` — async httpx `book_slot`, service_type→event_type URI mapping | VERIFIED | Uses native async httpx (no asyncio.to_thread); resolves `service_type` from `config_data["event_types"]` dict; raises ValueError if event_types is a list (WR-06 fix); paid-plan caveat documented |
| `apps/api/scripts/provision_integration_credential.py` | Deploy-time provisioning script with INT-07 single-currency guard | VERIFIED | `_check_single_currency` queries existing rows, aborts on conflict; credential read from file/stdin NEVER argv; reuses `_derive_tenant_fernet` for round-trip consistency; dry-run mode available |
| `apps/api/tests/unit/test_credential_service.py` | Unit tests for HKDF isolation, CredentialHandle redaction, schema cleanness | VERIFIED | 4 tests; `test_hkdf_per_tenant_isolation`, `test_handle_repr_redacted`, `test_no_credential_in_tool_schema`, `test_fetch_credential_config_none_when_missing` — all pass |
| `apps/api/tests/unit/test_provider_adapter_dispatch.py` | Unit tests for `get_adapter_for_skill` dispatch | VERIFIED | 9 tests; all four provider types + error paths (unconfigured skill, decrypt failure, unknown provider, missing shop_url/site_url) — all pass |
| `apps/api/tests/integration/test_stripe_live.py` | Env-gated live Stripe test | VERIFIED SKIPPED (awaiting human gate) | File exists and authored correctly; skips cleanly without env vars (2 skipped, 0 failed); see 16-UAT.md for runbook |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `tools.py` | `provider_adapter.get_adapter_for_skill` | Import at line 86; called at step 6 of `_execute_transactional_tool` | WIRED | `from app.services.transactional.provider_adapter import get_adapter_for_skill`; called as `adapter = await get_adapter_for_skill(skill, agent_id, conn_str)` |
| `provider_adapter.get_adapter_for_skill` | `credential_service._fetch_credential_config` | Direct call at line 245 | WIRED | `config = await _fetch_credential_config(conn_str, skill)`; returns None if skill not configured |
| `provider_adapter.get_adapter_for_skill` | `credential_service._derive_tenant_fernet` | Called with `settings.PLATFORM_CREDENTIAL_KEY` at line 258 | WIRED | `master_key_bytes = base64.urlsafe_b64decode(settings.PLATFORM_CREDENTIAL_KEY)` + `fernet = _derive_tenant_fernet(master_key_bytes, tenant_id)` |
| `worker/tasks/runtime/agent.py` | `agent_tools.build_tool_server` | `tenant_id=str(agent.tenant_id)` at line 533 | WIRED | `build_tool_server(..., tenant_id=str(agent.tenant_id))` sets `_tenant_id_var` ContextVar used by `get_adapter_for_skill` |
| `agent_tools.build_tool_server` | `_tenant_id_var.set(tenant_id)` | Line 606 | WIRED | `_tenant_id_var.set(tenant_id)` — ContextVar populated before the tool server is activated |
| `provision_integration_credential.py` | `credential_service._derive_tenant_fernet` | Direct import and call for round-trip consistency | WIRED | `from app.services.transactional.credential_service import _derive_tenant_fernet`; same HKDF derivation as runtime |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| HKDF per-tenant isolation: two tenant IDs produce different keys; cross-tenant decrypt raises `InvalidToken` | `pytest tests/unit/test_credential_service.py::test_hkdf_per_tenant_isolation -v` | PASSED | PASS |
| CredentialHandle repr redaction: `__repr__`/`__str__`/f-string always return `<CredentialHandle:redacted>` | `pytest tests/unit/test_credential_service.py::test_handle_repr_redacted -v` | PASSED | PASS |
| Provider adapter dispatch: all four provider types return correct concrete adapter | `pytest tests/unit/test_provider_adapter_dispatch.py -q` | 9 passed | PASS |
| Adapter unit tests (Shopify, WooCommerce, Stripe, Calendly) | `pytest tests/unit/test_stripe_adapter.py tests/unit/test_shopify_adapter.py tests/unit/test_woocommerce_adapter.py tests/unit/test_calendly_adapter.py -q` | 22 passed | PASS |
| Full Phase 16 test suite collection | `pytest tests/unit/test_credential_service.py tests/unit/test_stripe_adapter.py tests/unit/test_shopify_adapter.py tests/unit/test_woocommerce_adapter.py tests/unit/test_calendly_adapter.py tests/unit/test_provider_adapter_dispatch.py --collect-only -q` | 35 collected | PASS |
| Live Stripe test-mode refund gate | `STRIPE_TEST_MODE_ENABLED=1 pytest tests/integration/test_stripe_live.py -m integration` | DEFERRED — skips cleanly without env vars | SKIP (awaiting human gate) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| INT-01 | 16-01-PLAN.md | `integration_credentials` tenant-DB table — BYTEA, HKDF key derivation, never exposed to agent code | SATISFIED | `0007_integration_credentials.py`; `_derive_tenant_fernet`; REQUIREMENTS.md checkbox is stale `[ ]` — implementation exists and passes tests |
| INT-02 | 16-01-PLAN.md, 16-06-PLAN.md | Credential service resolves to short-lived in-memory handle; no agent SQL path | SATISFIED | `CredentialHandle`; `get_adapter_for_skill` only called from `tools.py` step 6; no FastAPI route imports it |
| INT-03 | 16-04-PLAN.md | Shopify adapter (place/cancel/refund) via Admin GraphQL | SATISFIED | `ShopifyAdapter` with `orderCreate`, `orderCancel`, `refundCreate`; 5 unit tests pass |
| INT-04 | 16-05-PLAN.md | WooCommerce adapter | SATISFIED | `WooCommerceAdapter` with httpx + OAuth1 (WooCommerce PyPI package rejected); 5 unit tests pass |
| INT-05 | 16-03-PLAN.md | Stripe adapter (issue refund, update subscription) | SATISFIED (live gate pending) | `StripeAdapter` with Refunds API + Subscriptions API; 3 unit tests pass; live test-mode gate deferred |
| INT-06 | 16-05-PLAN.md | Calendly adapter (book slot) | SATISFIED | `CalendlyAdapter` with async httpx; service_type→event_type URI mapping from config; REQUIREMENTS.md checkbox is stale `[ ]` — implementation exists and passes tests |
| INT-07 | 16-07-PLAN.md | Single-currency per tenant, enforced at deploy time | SATISFIED | `_check_single_currency` in `provision_integration_credential.py`; currency from `config.currency_code` in all adapters, never from tool args |

**Note:** REQUIREMENTS.md traceability table still shows `INT-01..07 | Phase 16 | Pending` and INT-01/INT-06 checkboxes are `[ ]`. This is stale documentation — the implementations are fully present, tested, and verified. A post-phase REQUIREMENTS.md update to mark all seven INT requirements as complete is recommended.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None found | — | No TBD/FIXME/XXX markers in any phase-16 transactional service files | — | — |

The two occurrences of "placeholder" in `stripe_adapter.py` (lines 17 and 62) refer to static safety URLs (`_CHECKOUT_SUCCESS_URL`, `_CHECKOUT_CANCEL_URL`) that are intentionally fixed strings for SSRF prevention (T-16-02 security invariant) — not incomplete code.

### Security Prohibition Verification

**Prohibition: The decrypted raw credential MUST NOT appear in any structlog call, exception message, ContextVar, Celery task arg, or JSON serialization.**

| Check | Finding | Status |
|-------|---------|--------|
| `CredentialHandle.__repr__/__str__` | Returns literal `<CredentialHandle:redacted>` — test passes | CONFIRMED |
| structlog calls in adapters | All four adapter log statements explicitly avoid logging credential fields; only `refund_id`, `order_id`, `agent_id`, `status` are logged | CONFIRMED |
| ContextVar transport | `_tenant_id_var` carries tenant_id (not the credential); credential is derived on-the-fly in `get_adapter_for_skill` | CONFIRMED |
| Celery task args | `conn_str` travels via `_conn_str_var` ContextVar (set by `build_tool_server`), not task args; credential resolved at tool-call time only | CONFIRMED |
| Audit rows | `write_audit_row` receives `arguments=raw_args` (tool input schema has no credential fields — `test_no_credential_in_tool_schema` passes) and `error=str(exc)` (CredentialDecryptionError messages contain only provider_type, not the raw credential) | CONFIRMED |
| `get_adapter_for_skill` import scope | Only imported from `apps/api/app/services/transactional/tools.py` line 86; no FastAPI route or SDK hook imports it | CONFIRMED |

**Prohibition: `_derive_tenant_fernet` MUST NOT reuse `settings.NEON_ENCRYPTION_KEY` (would collapse all tenants to same key).**

`credential_service.py` does not import `settings` at all — the master key bytes are received as a parameter from `provider_adapter.py` which correctly reads `settings.PLATFORM_CREDENTIAL_KEY`. The NEON_ENCRYPTION_KEY is nowhere referenced in the transactional service files.

**Prohibition: No provider MCP server or vendor agent-toolkit (ADR-0002).**

Grep for `mcp_server`, `agent_toolkit`, `shopify_mcp`, `stripe_mcp`, `woocommerce_mcp` in `apps/api/app/services/transactional/` returned zero matches. The `WooCommerce` PyPI package is absent from `pyproject.toml`; `requests-oauthlib>=2.0` is the approved alternative.

### Human Verification Required

#### 1. Live Stripe test-mode refund + idempotency replay gate (INT-05, T-16-08)

**Test:** Run the env-gated live Stripe integration test from `apps/api/`:

```
export STRIPE_TEST_MODE_ENABLED=1
export STRIPE_TEST_API_KEY="<rk_test_... or sk_test_... key>"
export STRIPE_TEST_CHARGE_ID="<ch_... charge ID from a real test PaymentIntent>"
pytest tests/integration/test_stripe_live.py -m integration -x -q -s
```

Then check for credential leakage:

```
pytest tests/integration/test_stripe_live.py -m integration -s 2>&1 \
  | grep -E "(sk_|rk_)" | grep -v "STRIPE_TEST"
# Expected: empty (no raw key in stdout)
```

**Expected:** `test_stripe_live_refund_and_idempotency_replay` passes — a real `re_...` refund_id is returned; a second call with the same idempotency_key returns the identical `re_...` id. `test_credential_handle_repr_is_always_redacted` passes. No `sk_`/`rk_` prefix appears in captured output.

**Why human:** Requires real Stripe test-mode credentials (Restricted Key scoped to Refunds Write + a pre-created test charge). These credentials were not available in the local dev environment during autonomous execution. The test file is authored, correct, and skips cleanly without the env vars.

**Precedent:** This follows the same live-gate deferral pattern used in Phase 13 (AWS live gate), Phase 14 (live-DB gate), and Phase 15 (ACT-06 latency gate). The deferred item is recorded in `16-UAT.md`.

---

### Summary

The phase goal is achieved at the code level. All security invariants are in place and tested: Fernet-encrypted BYTEA credential storage, per-tenant HKDF key derivation, `CredentialHandle` repr redaction, `get_adapter_for_skill` as the single decrypt/dispatch path callable only from `tools.py` step 6, four concrete provider adapters (Shopify/GraphQL, WooCommerce/httpx+OAuth1, Stripe/StripeClient, Calendly/async httpx), and the deploy-time single-currency guard.

The three review blockers (CR-01 zero-refund, CR-02 duplicate subscription item, CR-03 missing-key escape) were all fixed with 7 `fix(16):` commits and 35 unit tests pass.

The one outstanding item is the **live Stripe test-mode gate**: the `StripeAdapter.issue_refund` live round-trip with real credentials has not been exercised. The test infrastructure (`test_stripe_live.py`) is authored and correct. This is a human-only gate requiring Stripe test-mode credentials, consistent with prior-phase live-gate deferrals.

**Documentation gap (WARNING, not blocking):** REQUIREMENTS.md checkboxes for INT-01 and INT-06 remain `[ ]` (unchecked) and the traceability table still shows `INT-01..07 | Phase 16 | Pending`. The implementations are fully present and tested. A documentation update to mark all seven INT requirements as complete is recommended.

---

_Verified: 2026-07-01_
_Verifier: Claude (gsd-verifier)_
