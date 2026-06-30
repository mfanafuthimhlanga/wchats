---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "03"
subsystem: transactional-adapter-stripe
tags: [stripe, adapter, refund, subscription, checkout, idempotency, int-05, int-07, tdd]
dependency_graph:
  requires: [16-01, 16-02]
  provides: [StripeAdapter, stripe-refund, stripe-subscription, stripe-checkout]
  affects: [tools.py dispatcher, provider_adapter.py factory]
tech_stack:
  added:
    - stripe==15.3.0 (StripeClient v15, v1 namespace)
    - asyncio.to_thread (stdlib, sync SDK offload)
  patterns:
    - TDD RED/GREEN per task
    - CredentialHandle in-memory pattern (Plan 16-01)
    - asyncio.to_thread inner-closure pattern (from actor_seam.py)
    - options dict for Stripe Idempotency-Key
key_files:
  created:
    - apps/api/app/services/transactional/adapters/stripe_adapter.py
    - apps/api/tests/unit/test_stripe_adapter.py
  modified: []
decisions:
  - "idempotency_key forwarded via the RequestOptions options dict (second positional arg to all v1 methods), not as a keyword arg — matches the actual stripe 15.3.0 method signature (params, options)"
  - "StripeClient constructed with json.loads(handle.use())['api_key'] — not handle.use() directly (credential JSON blob, not raw key)"
  - "All 3 method types (refund, subscription, checkout) use the same inner-_sync()-closure + asyncio.to_thread pattern for consistency"
  - "unit_amount derived as amount_cents // quantity; quantity floored to 1 to guard against zero-div"
  - "Static placeholder success_url/cancel_url — no user-controlled URL in Checkout Session (T-16-02)"
metrics:
  duration: "8 minutes"
  completed: "2026-06-30"
  tasks_total: 2
  tasks_completed: 2
  files_created: 2
  files_modified: 0
status: complete
---

# Phase 16 Plan 03: StripeAdapter Summary

**One-liner:** Real Stripe refunds, subscription updates, and Checkout-Session orders via stripe 15.3.0 StripeClient behind the typed tool contract — idempotency keys forwarded natively, currency enforced from config.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for issue_refund + update_subscription | f4eeac4 | tests/unit/test_stripe_adapter.py |
| 1 (GREEN) | StripeAdapter implementation + all tests pass | 954d113 | adapters/stripe_adapter.py, test_stripe_adapter.py |

Both tasks were executed in a single TDD cycle (RED commit → GREEN commit) because the test file covered both Task 1 and Task 2 behaviors, and the implementation addressed all methods atomically.

## What Was Built

### `apps/api/app/services/transactional/adapters/stripe_adapter.py`

`StripeAdapter(ProviderAdapter)` subclass with:

- **`issue_refund`** — `client.v1.refunds.create(params, {"idempotency_key": ...})` where currency is `self._currency_code` (never from args, INT-07). Returns `IssueRefundOutput(refund_id=..., status="refunded")`.

- **`update_subscription`** — `client.v1.subscriptions.update(subscription_id, {"items": [{"price": new_plan}]}, {"idempotency_key": ...})`. Returns `UpdateSubscriptionOutput(status="updated")`.

- **`place_order`** — `client.v1.checkout.sessions.create({"mode": "payment", "line_items": [...], "success_url": ..., "cancel_url": ...}, {"idempotency_key": ...})`. Static placeholder URLs (T-16-02). No raw card fields. Returns `PlaceOrderOutput(status="pending_confirmation")`.

- **`cancel_order`, `book_slot`, `update_customer_record`** — raise `NotImplementedError("... not supported by StripeAdapter")`.

### `apps/api/tests/unit/test_stripe_adapter.py`

7 unit tests with `stripe.StripeClient` patched (no network calls):

| Test | Verifies |
|------|----------|
| `test_issue_refund_idempotency_key` | idempotency_key forwarded via options dict; StripeClient constructed with extracted api_key |
| `test_currency_from_config_not_args` | currency == `self._currency_code` (INT-07), tested with ZAR |
| `test_update_subscription` | subscriptions.update called with subscription_id + new plan items |
| `test_sync_offloaded` | issue_refund and update_subscription are async coroutines |
| `test_place_order_checkout_session` | mode="payment", currency from config, idempotency_key in options, no card fields |
| `test_unsupported_methods_raise` | cancel_order/book_slot/update_customer_record raise NotImplementedError("StripeAdapter") |
| `test_no_module_level_stripe_api_key` | source file contains zero occurrences of `stripe.api_key` |

## Key Design Decisions

1. **idempotency_key via options dict, not keyword arg.** The actual stripe 15.3.0 `v1.refunds.create(params, options)` signature takes a `RequestOptions` TypedDict as the second positional arg. Passing `{"idempotency_key": key}` as the options dict correctly maps to the `Idempotency-Key` HTTP header. The RESEARCH.md pseudo-code used `idempotency_key=args.idempotency_key` as a simplified illustration — the actual call uses the options dict.

2. **`json.loads(self._handle.use())["api_key"]`.** The CredentialHandle wraps the full credential JSON blob (`{"api_key": "rk_live_..."}`). StripeClient requires the raw key string, so the adapter extracts it inside the closure.

3. **StripeClient inside `_sync()` closure.** Constructed fresh per call, scoped to the synchronous thread. Prevents cross-tenant key bleed (Pitfall 2): no module-level `stripe.api_key`, no cached client at class level.

4. **unit_amount = amount_cents // quantity.** Stripe Checkout requires per-unit price in cents. Derived by integer division; quantity floored to 1 guards division-by-zero.

5. **Static placeholder URLs.** `success_url` and `cancel_url` are static constants (T-16-02). User-controlled URL fields in Checkout Session params would introduce SSRF risk; Plan 16-07 will wire real redirect URLs at deploy time.

## Acceptance Criteria Verification

| Criterion | Status |
|-----------|--------|
| `issue_refund` forwards `idempotency_key` as Stripe options dict | PASS (test_issue_refund_idempotency_key) |
| currency from `currency_code` config, not args (INT-07) | PASS (test_currency_from_config_not_args) |
| StripeClient inside `asyncio.to_thread` closure | PASS (test_sync_offloaded) |
| `update_subscription` returns status="updated" | PASS (test_update_subscription) |
| `place_order` uses mode="payment", no card fields | PASS (test_place_order_checkout_session) |
| `cancel_order`/`book_slot`/`update_customer_record` raise NotImplementedError | PASS (test_unsupported_methods_raise) |
| Zero occurrences of `stripe.api_key` in source | PASS (test_no_module_level_stripe_api_key, confirmed by grep) |
| min_lines >= 70 | PASS (279 lines) |
| pytest tests/unit/test_stripe_adapter.py green | PASS (7/7) |

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as specified with one refinement:

**[Deviation: Clarification] idempotency_key passed as options dict, not keyword arg**
- **Found during:** Task 1 GREEN (implementation)
- **Issue:** The RESEARCH.md and PATTERNS.md code examples show `idempotency_key=args.idempotency_key` as a keyword argument, but the actual stripe 15.3.0 method signature is `create(params, options)`. The keyword `idempotency_key` does not match the parameter name `options`.
- **Fix:** Passed `{"idempotency_key": args.idempotency_key}` as the second positional arg (the `RequestOptions` TypedDict), which correctly maps to the `Idempotency-Key` HTTP header.
- **Confirmed correct:** `stripe.RequestOptions.__annotations__` shows `idempotency_key` as a valid field.
- **Impact:** None — behavior is identical, implementation is more precise.

## Threat Surface Scan

No new threat surface introduced beyond what is already in the plan's `<threat_model>`. The implementation:
- Uses only fixed Stripe API endpoints (no user-controlled URLs in checkout session params)
- Never logs the api_key or CredentialHandle raw value
- Does not introduce new network endpoints or auth paths
- All structlog calls log only `refund_id`, `subscription_id`, `order_id`, `status`, `agent_id`

## Known Stubs

None — all StripeAdapter methods either fully implement the operation or raise `NotImplementedError` with an explicit message. No silent stubs or placeholder return values.

## Self-Check: PASSED

- [x] `apps/api/app/services/transactional/adapters/stripe_adapter.py` exists (279 lines)
- [x] `apps/api/tests/unit/test_stripe_adapter.py` exists (379 lines)
- [x] Commit f4eeac4 exists (test RED)
- [x] Commit 954d113 exists (feat GREEN)
- [x] `pytest tests/unit/test_stripe_adapter.py` → 7 passed
- [x] `grep stripe.api_key stripe_adapter.py` → CLEAN
