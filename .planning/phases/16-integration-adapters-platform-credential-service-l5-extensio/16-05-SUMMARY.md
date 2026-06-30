---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "05"
subsystem: provider-adapters
status: complete
tags: [woocommerce, calendly, int-04, int-06, httpx, oauth1, async, adapters, tdd]
completed_date: "2026-06-30"
duration: "~9 minutes"
tasks_completed: 2
files_modified: 4
requirements: [INT-04, INT-06]

dependency_graph:
  requires: [16-01, 16-02]
  provides: [WooCommerceAdapter, CalendlyAdapter]
  affects: [16-06-provider-dispatch, 16-07-deploy-runbook]

tech_stack:
  added: []
  patterns:
    - httpx sync client + _WooOAuth1Auth(httpx.Auth) bridging requests-oauthlib OAuth1 to httpx
    - asyncio.to_thread wrapping sync httpx.Client (Pitfall 3 prevention)
    - native async httpx.AsyncClient (CalendlyAdapter — no asyncio.to_thread)
    - config_data["event_types"] mapping pattern for provider-agnostic tool schemas (Open Q2)
    - HTTPS-only guard via ValueError in WooCommerceAdapter.__init__ (T-16-woo-http)

key_files:
  created:
    - apps/api/app/services/transactional/adapters/woocommerce_adapter.py
    - apps/api/app/services/transactional/adapters/calendly_adapter.py
    - apps/api/tests/unit/test_woocommerce_adapter.py
    - apps/api/tests/unit/test_calendly_adapter.py

decisions:
  - "[16-05] WooCommerceAdapter uses httpx sync client + _WooOAuth1Auth(httpx.Auth) bridging requests-oauthlib OAuth1 HMAC-SHA256 signing — overrides plan text referencing the rejected WooCommerce PyPI package (16-02 gate)"
  - "[16-05] CalendlyAdapter resolves service_type → event_type URI from config_data['event_types'] (Open Question 2 closed) — keeps BookSlotInput.service_type provider-agnostic"
  - "[16-05] Calendly paid-plan 403 is surfaced via raise_for_status(), not swallowed — deploy-time prerequisite: paid Calendly plan required"

metrics:
  duration: "~9 minutes"
  completed_date: "2026-06-30"
  tasks: 2
  tests_added: 8
  files_created: 4
---

# Phase 16 Plan 05: WooCommerceAdapter + CalendlyAdapter Summary

WooCommerceAdapter (INT-04) and CalendlyAdapter (INT-06) implemented; 8/8 tests pass. WooCommerce uses httpx + requests-oauthlib OAuth1 HMAC-SHA256 (the approved 16-02 fallback). Calendly uses native async httpx with Bearer PAT and resolves service_type to event_type URI from per-tenant config_data (Open Question 2 closed).

## What Was Built

### Task 1: WooCommerceAdapter (INT-04)

`apps/api/app/services/transactional/adapters/woocommerce_adapter.py` — `WooCommerceAdapter(ProviderAdapter)` with:

| Method | Endpoint | Notes |
|--------|----------|-------|
| `issue_refund` | `POST /wp-json/wc/v3/orders/{id}/refunds` | amount in currency-major string (cents ÷ 100) |
| `place_order` | `POST /wp-json/wc/v3/orders` | line_items + billing.email |
| `cancel_order` | `PUT /wp-json/wc/v3/orders/{id}` | status=cancelled |
| `update_subscription` | — | NotImplementedError |
| `book_slot` | — | NotImplementedError |
| `update_customer_record` | — | NotImplementedError |

Key implementation details:
- `_WooOAuth1Auth(httpx.Auth)` bridges `requests_oauthlib.OAuth1(HMAC-SHA256)` to httpx via a `requests.PreparedRequest` signing vehicle — cleanly satisfies both "httpx" and "requests-oauthlib" constraints
- All sync `httpx.Client` calls wrapped in `asyncio.to_thread` (Pitfall 3 prevention)
- `__init__` raises `ValueError` if `site_url` is not `https://` (T-16-woo-http, Pitfall 5)
- `consumer_key`/`consumer_secret` extracted from `CredentialHandle` only inside `_WooOAuth1Auth` — never logged

5 tests pass: `test_issue_refund`, `test_place_order`, `test_cancel_order`, `test_http_url_rejected`, `test_unsupported_methods_raise`.

### Task 2: CalendlyAdapter (INT-06)

`apps/api/app/services/transactional/adapters/calendly_adapter.py` — `CalendlyAdapter(ProviderAdapter)` with:

| Method | Endpoint | Notes |
|--------|----------|-------|
| `book_slot` | `POST https://api.calendly.com/invitees` | Bearer PAT; event_type URI from config_data |
| `place_order` | — | NotImplementedError |
| `cancel_order` | — | NotImplementedError |
| `issue_refund` | — | NotImplementedError |
| `update_subscription` | — | NotImplementedError |
| `update_customer_record` | — | NotImplementedError |

Key implementation details:
- `CALENDLY_API_BASE = "https://api.calendly.com"` — fixed module constant (T-16-02: no URL from args)
- `__init__(handle, config_data=None)` stores `self._event_types = (config_data or {}).get("event_types", {})` — Open Question 2 closed
- `book_slot` resolves `args.service_type` via `self._event_types.get(service_type)` → raises `ValueError` with available types if missing
- PAT extracted inside `book_slot` via `handle.use()` — placed only in `Authorization: Bearer` header
- `response.raise_for_status()` propagates 403 (Calendly paid-plan requirement, Pitfall 7)
- Native async `httpx.AsyncClient` — no `asyncio.to_thread`

3 tests pass (via `respx` mock): `test_book_slot`, `test_unknown_service_type_raises`, `test_unsupported_methods_raise`.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1: WooCommerceAdapter (INT-04) | `feb38be` | feat(16-05): WooCommerceAdapter (INT-04) — httpx + OAuth1 HMAC-SHA256 over HTTPS |
| Task 2: CalendlyAdapter (INT-06) | `7423624` | feat(16-05): CalendlyAdapter (INT-06) — async httpx Bearer PAT + config_data event_type mapping |

## Deviations from Plan

### Plan-vs-Reality: WooCommerce Package Override (CRITICAL — Upstream Decision)

**Found during:** Task 1 (setup)

**Issue:** The 16-05 PLAN.md text still references `from woocommerce import API as WooCommerceAPI` and the PATTERNS.md references the `WooCommerce` PyPI package import. However, the upstream decision from the 16-02 checkpoint (user REJECTED the WooCommerce PyPI package, stale since 2021) is authoritative and overrides the plan text.

**Fix applied:** WooCommerceAdapter uses `httpx` + `requests_oauthlib.OAuth1(HMAC-SHA256)` throughout. Implemented `_WooOAuth1Auth(httpx.Auth)` class to bridge `requests-oauthlib`'s `OAuth1` (designed for `requests` library) to `httpx` via a `requests.PreparedRequest` signing vehicle. This satisfies both the "httpx" and "requests-oauthlib" constraints from the 16-02 SUMMARY.

**Authority:** Upstream executor decision (16-02 supply-chain gate, user decision, CLAUDE.md: "follow RESEARCH").

### test_http_url_rejected regex fix (Rule 1 — Bug)

**Found during:** Task 1 GREEN phase

**Issue:** Test used `match="https://"` but the ValueError message says "HTTPS" (uppercase). The regex `//` has no special meaning but `"https://"` was not a substring of the error message.

**Fix:** Changed test to `match="HTTPS"` to match the actual error message pattern.

## Security Invariants Verified

| Threat | Control | Status |
|--------|---------|--------|
| T-16-01 (credential logging) | `log.info` calls verified to contain no consumer_key, consumer_secret, or PAT fields | PASS |
| T-16-02 (SSRF via URL from args) | No URL field in any typed Input schema; site_url from constructor only; CALENDLY_API_BASE is a fixed constant | PASS |
| T-16-woo-http (WooCommerce HTTP downgrade) | `__init__` raises ValueError if not `site_url.startswith("https://")` | PASS |
| T-16-cal-paid (Calendly 403 on free plan) | `response.raise_for_status()` propagates 403 — not swallowed | PASS (documented caveat) |

## Calendly Paid-Plan Caveat (Deploy-Time Prerequisite)

`POST /invitees` requires a **paid Calendly plan** (Pitfall 7, RESEARCH.md). Free Calendly accounts return 403 Forbidden even with a valid PAT. The adapter surfaces this as a `httpx.HTTPStatusError` via `raise_for_status()`, which the dispatcher audits.

**Deploy-time action required:** Confirm the tenant has a paid Calendly plan before enabling `book_slot`. The deploy runbook (16-07) must document:
1. Verify paid Calendly plan via Calendly dashboard
2. Fallback if programmatic booking unavailable: `GET /event_types/{uuid}/scheduling_url` returns a scheduling URL that can be sent to the customer

## Known Stubs

None — all implemented methods have real API calls (mocked in tests). `NotImplementedError` stubs are intentional per the Phase 16 Provider→Tool Mapping (unsupported combinations fast-fail at the dispatcher).

## Threat Surface Scan

Two new outbound HTTPS connections introduced:
- `WooCommerceAdapter` → tenant's WooCommerce site (HTTPS only; site_url from config, never args)
- `CalendlyAdapter` → `https://api.calendly.com` (fixed constant, never from args)

Both are documented in the plan's `<threat_model>` (T-16-01, T-16-02, T-16-woo-http, T-16-cal-paid). No new threat surface beyond what the plan's threat register already covers.

## Self-Check

- [x] `apps/api/app/services/transactional/adapters/woocommerce_adapter.py` exists (min_lines: 60 — actual: 280)
- [x] `apps/api/app/services/transactional/adapters/calendly_adapter.py` exists (min_lines: 40 — actual: 210)
- [x] `apps/api/tests/unit/test_woocommerce_adapter.py` exists
- [x] `apps/api/tests/unit/test_calendly_adapter.py` exists
- [x] Commit `feb38be` exists (WooCommerceAdapter)
- [x] Commit `7423624` exists (CalendlyAdapter)
- [x] `pytest tests/unit/test_woocommerce_adapter.py tests/unit/test_calendly_adapter.py -x -q` → 8 passed
- [x] No `import woocommerce` in `woocommerce_adapter.py`
- [x] `CALENDLY_API_BASE` is a module-level constant in `calendly_adapter.py`
- [x] No credentials in `log.info` calls (verified via grep)
- [x] HTTPS guard present in `WooCommerceAdapter.__init__`

## Self-Check: PASSED
