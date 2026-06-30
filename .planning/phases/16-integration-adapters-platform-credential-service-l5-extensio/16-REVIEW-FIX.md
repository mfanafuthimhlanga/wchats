---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
fixed_at: 2026-06-30T00:00:00Z
review_path: .planning/phases/16-integration-adapters-platform-credential-service-l5-extensio/16-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 16: Code Review Fix Report

**Fixed at:** 2026-06-30
**Source review:** `.planning/phases/16-integration-adapters-platform-credential-service-l5-extensio/16-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (CR-01, CR-02, CR-03, WR-01, WR-02, WR-06, WR-07)
- Fixed: 7
- Skipped: 0

**Test results:** 31 tests across 5 test files — all passed.

---

## Fixed Issues

### CR-01: Shopify `refundCreate` always issues a $0 refund

**Files modified:** `apps/api/app/services/transactional/adapters/shopify_adapter.py`, `apps/api/tests/unit/test_shopify_adapter.py`
**Commit:** `45a3a0a`
**Applied fix:** Replaced `refundLineItems: []` with a `transactions` array containing a single REFUND entry with `kind: "REFUND"`, `gateway: "shopify_payments"`, and `amount` set to the currency-major string (`refund_amount_cents / 100` formatted as `"35.00"`). Updated the docstring to document the amount-based refund path. Updated the test to assert `transactions` is present with the correct `kind` and `amount`, and `refundLineItems` is absent.

---

### CR-02: Stripe `update_subscription` adds a duplicate subscription item

**Files modified:** `apps/api/app/services/transactional/adapters/stripe_adapter.py`, `apps/api/tests/unit/test_stripe_adapter.py`
**Commit:** `24aa994`
**Applied fix:** Added a `client.v1.subscriptions.retrieve(subscription_id)` call before the update to obtain the existing item's `id`. The update is now `{"items": [{"id": existing_item_id, "price": args.new_plan}]}` which replaces the item instead of adding a second one. Also captured the `updated_sub` return value from `asyncio.to_thread(_sync)` and used `updated_sub.id` in the output (rather than the input arg) to confirm server-side state. Added `ValueError` if the subscription has no items. Updated the test to mock `subscriptions.retrieve`, assert it is called first, and assert `subscriptions.update` receives the `{"id": existing_item_id, ...}` form.

---

### CR-03: `KeyError` from missing `shop_url`/`site_url` escapes idempotency handler

**Files modified:** `apps/api/app/services/transactional/provider_adapter.py`, `apps/api/tests/unit/test_provider_adapter_dispatch.py`
**Commit:** `9b6ba5c`
**Applied fix:** Replaced bare `config.config_data["shop_url"]` and `config.config_data["site_url"]` indexing with `.get()` + explicit `ProviderNotConfiguredError` raise. The error messages include the missing key name and a provisioning hint. Added two new tests: `test_shopify_missing_shop_url_raises` and `test_woocommerce_missing_site_url_raises`, both asserting the error message matches the key name.

---

### WR-01: Stripe Refunds API — `"currency"` parameter causes 400 for charge-based refunds

**Files modified:** `apps/api/app/services/transactional/adapters/stripe_adapter.py`, `apps/api/tests/unit/test_stripe_adapter.py`
**Commit:** `7d733c6`
**Applied fix:** Removed `"currency": self._currency_code` from the `refunds.create` params dict. Added a comment explaining that Stripe derives the refund currency from the original charge. Updated `test_issue_refund_idempotency_key` to assert the params dict does NOT contain `currency`. Updated `test_currency_from_config_not_args` to assert `"currency" not in params_dict` (documenting that INT-07 is still enforced via schema — there is no currency field in `IssueRefundInput`).

---

### WR-02: Shopify session management — cross-tenant credential bleed under concurrent threads

**Files modified:** `apps/api/app/services/transactional/adapters/shopify_adapter.py`, `apps/api/tests/unit/test_shopify_adapter.py`
**Commit:** `400ea29`
**Applied fix:** Removed `_make_session()` and `_clear_session()` methods. All three `_sync()` closures (`issue_refund`, `place_order`, `cancel_order`) now use `with shopify.Session.temp(self._shop_url, self._api_version, token):` which is a thread-safe context manager scoped to the closure's thread context. The token is extracted inside each closure (T-16-01 preserved). Updated all four affected tests: replaced `ShopifyResource.activate_session.assert_called_once()` / `clear_session.assert_called_once()` with `Session.temp.assert_called_once()`. Updated `test_shop_url_from_constructor` to check `Session.temp.call_args` instead of `Session.call_args`.

---

### WR-06: Calendly `event_types` provisioning example shows list format; adapter expects dict

**Files modified:** `apps/api/scripts/provision_integration_credential.py`, `apps/api/app/services/transactional/adapters/calendly_adapter.py`, `apps/api/tests/unit/test_calendly_adapter.py`
**Commit:** `b8af848`
**Applied fix:**
1. Corrected the docstring example in `provision_integration_credential.py` from `["uuid-1", "uuid-2"]` to `{"consultation": "https://api.calendly.com/event_types/<UUID>", "demo": "..."}`.
2. Added a note in the docstring clarifying that `event_types` must be a dict, not a list.
3. Fixed the `--config-json` help text to show the correct dict format.
4. Added a `isinstance(self._event_types, dict)` check in `CalendlyAdapter.__init__` that raises `ValueError` immediately if a list is passed, rather than raising `AttributeError` later at call time.
5. Added `test_event_types_must_be_dict` test asserting `ValueError` is raised when a list is provided.

---

### WR-07: Stripe `place_order` integer division silently drops remainder cents

**Files modified:** `apps/api/app/services/transactional/adapters/stripe_adapter.py`, `apps/api/tests/unit/test_stripe_adapter.py`
**Commit:** `d86c612`
**Applied fix:** Replaced the `unit_amount = args.amount_cents // quantity` + `quantity=N` line-item pattern with a single line item: `unit_amount=args.amount_cents` / `quantity=1`. The product name is set to `f"{quantity}x {args.product_id}"` so customers see the bundle quantity. This ensures the billed total exactly equals `args.amount_cents` with no remainder loss. Updated `test_place_order_checkout_session` to assert `line_item["quantity"] == 1`, `line_item["price_data"]["unit_amount"] == 4000` (full total), and that the product name includes both product_id and quantity.

---

_Fixed: 2026-06-30_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
