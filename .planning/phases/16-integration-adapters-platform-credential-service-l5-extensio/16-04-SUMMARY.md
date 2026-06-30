---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "04"
subsystem: transactional-adapters
tags: [shopify, graphql, tdd, int-03, credential-handle, session-per-call]
requires: [16-01, 16-02]
provides: [ShopifyAdapter]
affects: [transactional-dispatcher, provider-adapter-contract]
tech_stack:
  added:
    - ShopifyAPI==12.7.0 (shopify.Session, shopify.GraphQL, ShopifyResource) — Admin GraphQL adapter
    - shopify.GraphQL().execute(mutation, variables=...) — GraphQL-only pattern (no REST)
  patterns:
    - session-per-call: activate_session inside asyncio.to_thread, clear_session in finally
    - asyncio.to_thread for all sync Shopify SDK calls (Pitfall 3 avoidance)
    - json.loads(handle.use())["access_token"] — raw token extracted only inside sync closure
key_files:
  created:
    - apps/api/app/services/transactional/adapters/shopify_adapter.py (376 lines)
    - apps/api/tests/unit/test_shopify_adapter.py (253 lines)
  modified: []
decisions:
  - "API version pinned to 2025-04 (_API_VERSION constant) — Pitfall 6 prevention"
  - "orderCancel uses reason=OTHER + staffNote for human-readable reason (Shopify enum constraint)"
  - "shop_url is a constructor param only — never from args (T-16-02 SSRF prevention)"
  - "access_token extracted via json.loads(handle.use())['access_token'] inside sync closure only"
metrics:
  duration_seconds: 912
  completed_date: "2026-06-30"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
status: complete
---

# Phase 16 Plan 04: ShopifyAdapter (INT-03) Summary

ShopifyAdapter (INT-03) implementing place/cancel order and issue refund via Shopify Admin GraphQL mutations (`orderCreate`, `orderCancel`, `refundCreate`) behind the locked typed tool contract, with a per-call session and credential-config `shop_url`.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | ShopifyAdapter tests — issue_refund + place_order + shop_url invariant | ed367e5 | tests/unit/test_shopify_adapter.py |
| 1 (GREEN) | ShopifyAdapter scaffold — issue_refund + place_order via GraphQL | 642f5ed | adapters/shopify_adapter.py |
| 2 (tests included in RED) | cancel_order + unsupported stubs — RED already committed with Task 1 | ed367e5 | (included above) |
| 2 (GREEN) | cancel_order (orderCancel) + NotImplementedError stubs | 642f5ed | (included above) |

## What Was Built

### ShopifyAdapter (376 lines, `apps/api/app/services/transactional/adapters/shopify_adapter.py`)

Concrete `ProviderAdapter` subclass for Shopify Admin GraphQL:

**Methods implemented:**
- `issue_refund(args, agent_id)` — `refundCreate` mutation with `orderId`, `currency` (from config, INT-07), and `note`
- `place_order(args, agent_id)` — `orderCreate` mutation with line items (`variantId` + `quantity`), email, and shippingAddress
- `cancel_order(args, agent_id)` — `orderCancel` mutation with `orderId`, `reason=OTHER`, and `staffNote` from args.reason

**Methods raising NotImplementedError (fast-fail stubs):**
- `update_subscription` — "use StripeAdapter for subscription management"
- `book_slot` — "use CalendlyAdapter"
- `update_customer_record` — Phase 16 deferred across all providers

**Session-per-call pattern:**
- `_make_session()` — `json.loads(handle.use())["access_token"]` → `shopify.Session(shop_url, api_version, token)` → `shopify.ShopifyResource.activate_session(session)`
- `_clear_session()` — `shopify.ShopifyResource.clear_session()` (always called in `finally` block)
- `_API_VERSION = "2025-04"` — pinned constant (Pitfall 6 prevention)

**Thread safety:** Every Shopify SDK call runs inside `asyncio.to_thread(_sync)`. The sync closure activates a session, executes the GraphQL mutation, and clears the session in a `finally` block — preventing cross-tenant bleed in a multi-tenant Celery worker.

### Tests (6 tests, `apps/api/tests/unit/test_shopify_adapter.py`)

All tests mock `shopify` module — zero network calls:

| Test | What it verifies |
|------|-----------------|
| `test_issue_refund_calls_refund_create` | Mutation contains "refundCreate", variables include orderId + currency; session activated + cleared |
| `test_place_order_calls_order_create` | Mutation contains "orderCreate", variables include product_id + quantity; status in {placed, pending_confirmation} |
| `test_shop_url_from_constructor` | `shopify.Session` called with constructor shop_url; token is bare access_token (not JSON blob) |
| `test_cancel_order_calls_order_cancel` | Mutation contains "orderCancel", variables include orderId; status in {cancelled, pending_cancellation} |
| `test_unsupported_methods_raise` | update_subscription/book_slot/update_customer_record raise NotImplementedError matching "ShopifyAdapter" |
| `test_no_args_shop_url_in_source` | Static analysis: "args.shop_url" absent from source (T-16-02 guard) |

## Deviations from Plan

None — plan executed exactly as written.

All must_haves verified:
- `ShopifyAdapter.issue_refund` executes `refundCreate` mutation ✓
- `ShopifyAdapter.place_order` executes `orderCreate` mutation ✓
- `ShopifyAdapter.cancel_order` executes `orderCancel` mutation ✓
- Every Shopify SDK call runs inside `asyncio.to_thread` with session activated then cleared ✓
- `shop_url` comes from credential config (constructor), never from tool arguments ✓

## Security Invariants Verified

| Threat | Mitigation | Status |
|--------|-----------|--------|
| T-16-01: access_token in logs | Token extracted via `json.loads(handle.use())["access_token"]` inside sync closure only; structlog logs order_id/agent_id only | Closed |
| T-16-02: SSRF via shop_url from args | `shop_url` is a constructor param from `integration_credentials.config_data`; no URL field in typed schemas; static test confirms no `args.shop_url` | Closed |
| T-16-04: confused-deputy provider call | Enforced upstream by capability envelope + Actor gate (dispatcher steps 2 & 5) before `get_adapter_for_skill` | Pre-existing |
| T-16-dep: deprecated REST API | GraphQL mutations only; REST forbidden; confirmed by grep (no `/admin/api/...` URL in source) | Closed |

## Known Stubs

None that block the plan's goal. The following methods are intentional stubs per the Phase 16 Provider→Tool Mapping:
- `update_subscription` — Shopify does not have a subscription management API in scope
- `book_slot` — Calendly concern
- `update_customer_record` — Deferred across all providers in Phase 16 per INT requirements table

These stubs are the correct implementation per the plan, not deferred work.

## Self-Check

### Files created:
- `apps/api/app/services/transactional/adapters/shopify_adapter.py` ✓ (376 lines, min_lines 70 — exceeded)
- `apps/api/tests/unit/test_shopify_adapter.py` ✓

### Commits verified:
- `ed367e5` — test(16-04): RED phase tests
- `642f5ed` — feat(16-04): GREEN implementation

### Test result: 6/6 PASSED

```
tests/unit/test_shopify_adapter.py ......    6 passed in 1.13s
```

## Self-Check: PASSED
