---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "02"
subsystem: provider-sdk-dependencies
status: complete
tags: [dependencies, supply-chain, stripe, shopify, woocommerce-fallback, oauth1]
completed_date: "2026-06-30"
duration: "~16 minutes"
tasks_completed: 1
files_modified: 1
requirements: [INT-03, INT-04, INT-05]

dependency_graph:
  requires: [16-01]
  provides: [stripe==15.3.0, ShopifyAPI==12.7.0, requests-oauthlib>=2.0]
  affects: [16-03-stripe-adapter, 16-04-shopify-adapter, 16-05-woocommerce-adapter]

tech_stack:
  added:
    - stripe==15.3.0 (official Stripe SDK, thread-safe StripeClient pattern)
    - ShopifyAPI==12.7.0 (official Shopify SDK)
    - requests-oauthlib>=2.0 (WooCommerce OAuth1-signing fallback)
  patterns:
    - Supply-chain gate: human-verify checkpoint T-16-SC cleared before install
    - WooCommerce fallback: httpx + requests-oauthlib OAuth1 (WooCommerce PyPI pkg rejected)

key_files:
  modified:
    - apps/api/pyproject.toml

decisions:
  - "[16-02] WooCommerce PyPI package REJECTED (5-year stale, last released 2021). Plan 16-05 WooCommerce adapter uses httpx + requests-oauthlib OAuth1 signing instead"
  - "[16-02] stripe==15.3.0 pinned (exact) — enables thread-safe StripeClient(api_key) multi-tenant pattern (ADR-0002 compliant)"
  - "[16-02] ShopifyAPI==12.7.0 pinned (exact) — official Shopify org SDK, last release Nov 2024"
  - "[16-02] requests-oauthlib>=2.0 (range pin) — OAuth1 signing support for WooCommerce httpx fallback adapter in 16-05"
---

# Phase 16 Plan 02: Provider SDK Dependencies Summary

Provider SDK supply-chain gate cleared and dependencies pinned: `stripe==15.3.0`, `ShopifyAPI==12.7.0`, `requests-oauthlib>=2.0`. WooCommerce PyPI package rejected (5-year stale); Plan 16-05 will use httpx + OAuth1 fallback.

## What Was Built

Updated `apps/api/pyproject.toml` `[project].dependencies` with three provider SDK entries following explicit human approval at the T-16-SC supply-chain legitimacy checkpoint:

| Package | Version | Status | Notes |
|---------|---------|--------|-------|
| `stripe` | `==15.3.0` | APPROVED | Official Stripe SDK; `StripeClient(api_key)` pattern for thread-safe multi-tenant use |
| `ShopifyAPI` | `==12.7.0` | APPROVED | Official Shopify SDK (github.com/Shopify/shopify_python_api), Nov 2024 release |
| `WooCommerce` | `3.0.0` | REJECTED | 5-year stale (last released 2021); not installed |
| `requests-oauthlib` | `>=2.0` | ADDED (fallback) | OAuth1 signing for httpx-based WooCommerce adapter in Plan 16-05 |

## WooCommerce Package Decision (CRITICAL for Plan 16-05)

**Decision: REJECTED the `WooCommerce` PyPI package. Plan 16-05 must use the httpx + requests-oauthlib OAuth1 fallback.**

The user rejected the `WooCommerce==3.0.0` PyPI package due to its 5-year staleness (last released 2021). The WooCommerce REST API adapter in Plan 16-05 will instead use:
- `httpx` (already a core dependency at `0.28.1`) for HTTP requests
- `requests-oauthlib>=2.0` (added in this plan) for OAuth1 HMAC-SHA256 signature signing

Plan 16-05 WooCommerce adapter imports accordingly:
```python
from requests_oauthlib import OAuth1
import httpx
```

## Verification Results

**Import smoke (Task 2 acceptance criteria — PASS):**
```
python -c "import stripe, shopify; print('sdk import ok', stripe.VERSION)"
# Output: sdk import ok 15.3.0

python -c "import requests_oauthlib; print('oauth1 ok')"
# Output: oauth1 ok
```

**Test collection (no import errors — PASS):**
- `734 tests collected` in 136.99s with no collection errors
- Pre-existing Bedrock/AWS credential failure in `veridian` project tests is out-of-scope (not in wchats codebase; not caused by this plan's changes)

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 2: Pin SDKs + install + smoke | `79fb1ac` | chore(16-02): pin stripe==15.3.0, ShopifyAPI==12.7.0, requests-oauthlib>=2.0 |

## Deviations from Plan

### Supply-Chain Gate Outcome (Expected Deviation — Documented)

**Task 1 checkpoint resolution:**
- **stripe==15.3.0** — APPROVED
- **ShopifyAPI==12.7.0** — APPROVED
- **WooCommerce==3.0.0** — REJECTED (stale); fallback `requests-oauthlib>=2.0` added instead

**Plan spec deviation in Task 2 smoke test:**
- Plan spec: `python -c "import woocommerce; print('woo ok')"` (WooCommerce-path)
- Actual (per user decision): `python -c "import requests_oauthlib; print('oauth1 ok')"` (fallback-path)
- This substitution exactly matches the plan's stated fallback: "depending on the WooCommerce decision, either ... `python -c 'import woocommerce; print(\"woo ok\")'` or `python -c 'import requests_oauthlib; print(\"oauth1 ok\")'`"

No auto-fix deviations (Rules 1-3) were triggered. The plan executed cleanly with one expected human-gate-driven deviation.

## Known Stubs

None. This plan only modifies dependency declarations — no Python symbols, no UI, no stubs.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. `pyproject.toml` dependency additions were gated behind the supply-chain human checkpoint T-16-SC; all three approved packages are official first-party SDKs from their respective platform vendors.

## Self-Check

- [x] `apps/api/pyproject.toml` exists and contains `stripe==15.3.0`, `ShopifyAPI==12.7.0`, `requests-oauthlib>=2.0`
- [x] Commit `79fb1ac` exists on `main`
- [x] Import smoke returned `sdk import ok 15.3.0` and `oauth1 ok`
- [x] 734 tests collected without import errors

## Self-Check: PASSED
