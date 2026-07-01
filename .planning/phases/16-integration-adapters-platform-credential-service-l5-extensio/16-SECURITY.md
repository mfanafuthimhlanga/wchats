---
phase: 16
slug: integration-adapters-platform-credential-service-l5
status: verified
threats_open: 0
asvs_level: 1
created: 2026-07-01
---

# Phase 16 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Phase scope: Integration adapters (Stripe, Shopify, WooCommerce, Calendly) + platform
> credential service (HKDF per-tenant Fernet), supply-chain gate, dispatcher enforcement,
> provisioning script, and operator runbook.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Celery worker → provider SDK | Encrypted credential decrypted in-memory; CredentialHandle passed to adapter constructor | Raw API key / access token (never logged, never serialised) |
| Provisioning script → tenant DB | HKDF-derived Fernet encrypts credential before INSERT | Encrypted credential blob (BYTEA); master key from env only |
| Adapter sync closure → provider API | Credential extracted from CredentialHandle inside asyncio.to_thread closure | API key placed in request header / SDK constructor; never leaves the closure |
| Dispatcher → audit log | Tool call arguments and typed result written to tool_calls_audit | PII in arguments accepted risk (T-16-09); no credential fields |
| Dependency install → runtime | Provider SDKs gated behind human supply-chain checkpoint | Pinned exact versions; WooCommerce PyPI rejected → httpx + OAuth1 fallback |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-16-01 | Information Disclosure | CredentialHandle in structlog / traceback / provisioning script | mitigate | `CredentialHandle.__repr__`/`__str__` return `<CredentialHandle:redacted>`; all adapters extract raw value only inside sync closures; structlog calls log only IDs/status; provisioning script prints only non-secret metadata | closed |
| T-16-02 | Tampering (SSRF) | Provider URL from tool args | mitigate | `shop_url`/`site_url` sourced from `config_data` constructor param only (never from typed schemas); Stripe `success_url`/`cancel_url` are static module constants; Calendly base URL is a fixed module constant (`CALENDLY_API_BASE`) | closed |
| T-16-03 | Elevation of Privilege | Over-scoped Stripe Restricted Key | transfer | Adapter assumes correctly-scoped key provisioned at deploy time; `docs/runbooks/integration-credentials.md` documents per-tenant Restricted Key scope → skill mapping; verification gate in 16-07 optionally confirms out-of-scope key fails closed | closed |
| T-16-04 | Elevation of Privilege | Confused-deputy provider call | mitigate | Capability envelope check (step 2) and Actor gate (step 5) both run BEFORE `get_adapter_for_skill` (step 6) in `_execute_transactional_tool`; step ordering is locked | closed |
| T-16-05 | Information Disclosure | PLATFORM_CREDENTIAL_KEY master key | mitigate | `Settings.PLATFORM_CREDENTIAL_KEY` has no default (fails fast if unset); `Settings.__repr__`/`__str__` return only `LOG_LEVEL`; provisioning script reads from `os.environ.get("PLATFORM_CREDENTIAL_KEY")` only; never written to DB or printed | closed |
| T-16-06 | Information Disclosure | Raw credential in Celery task args | mitigate | `_tenant_id_var` ContextVar (not a task arg) carries the HKDF salt; credential is fetched and decrypted inside `get_adapter_for_skill` at call time; `build_tool_server` injects `tenant_id=str(agent.tenant_id)` via ContextVar | closed |
| T-16-07 | Information Disclosure | CredentialHandle serialised to JSON / Redis | mitigate | `CredentialHandle` is a `frozen=True` dataclass with no `__json__`, no `model_dump`, no `asdict`; never stored in any ContextVar, Redis key, or audit row | closed |
| T-16-08 | Replay | Stripe idempotency key reuse | mitigate | `args.idempotency_key` forwarded to Stripe's native `Idempotency-Key` option in `issue_refund` (line 126), `update_subscription` (line 188), and `place_order` (line 259) of `stripe_adapter.py`; Stripe returns original response on replay (defense-in-depth atop W Chats idempotency engine); live round-trip verification operator-accepted per 16-UAT.md | closed |
| T-16-09 | Information Disclosure | PII (customer_email) in tool_calls_audit.arguments | accept | Accepted risk — same posture as Phase 14; no new PII fields introduced; L4 PII output-firewall deferred to Phase 18 | closed |
| T-16-10 | Tampering | Per-tenant key isolation defeated by shared HKDF instance | mitigate | `_derive_tenant_fernet` constructs a fresh `HKDF()` instance per call with `salt=tenant_id.encode("utf-8")`; instance-level caching forbidden by docstring; `test_hkdf_per_tenant_isolation` proves cross-tenant decrypt raises `InvalidToken` | closed |
| T-16-SC | Tampering | Supply-chain: pip install of SUS-flagged provider SDKs | mitigate | Human-verify checkpoint at 16-02 gate confirmed each pin against official PyPI project page before install; `stripe==15.3.0` and `ShopifyAPI==12.7.0` approved; `WooCommerce==3.0.0` rejected (5-year stale, last released 2021) — replaced with `httpx` + `requests-oauthlib>=2.0` OAuth1 fallback (both already-audited deps) | closed |
| T-16-cal | Denial of Service | Calendly paid-plan 403 on free plan | accept | Accepted risk — adapter calls `response.raise_for_status()` so 403 propagates to dispatcher and is audited as an error; deploy-time runbook (`docs/runbooks/integration-credentials.md`) documents paid-plan requirement and `GET /event_types/{uuid}/scheduling_url` fallback | closed |
| T-16-cfg | Denial of Service | Unconfigured / undecryptable credential | mitigate | `ProviderNotConfiguredError` and `CredentialDecryptionError` both caught in `_execute_transactional_tool` step 6; both paths call `release_idempotency`, write an audit row, and return `is_error=True`; no silent failure or stuck reservation | closed |
| T-16-cur | Tampering | Currency override via tool args | mitigate | `self._currency_code` sourced from `integration_credentials.currency_code` at adapter construction time; never read from tool args in any adapter method; provisioning script enforces single-currency-per-tenant (INT-07 guard aborts on conflicting `currency_code`) | closed |
| T-16-dep | Tampering | Deprecated Shopify REST API surface | mitigate | All Shopify mutations use `shopify.GraphQL().execute(mutation, ...)` exclusively; no `/admin/api/...` REST path present in `shopify_adapter.py`; `T-16-dep` invariant documented in module docstring | closed |
| T-16-woo | Tampering | WooCommerce over HTTP (auth downgrade) | mitigate | `WooCommerceAdapter.__init__` raises `ValueError` if `site_url` does not start with `"https://"`; HTTPS-only guard enforced at construction time before any network call | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party / operator)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-16-01 | T-16-09 | PII (customer_email and similar fields) may appear in `tool_calls_audit.arguments` JSONB column. Same posture accepted in Phase 14. No new PII fields introduced in Phase 16. Mitigated at Phase 18 by the L4 PII output-firewall. | Phase 16 plan decision (16-06-PLAN.md threat_model) | 2026-07-01 |
| AR-16-02 | T-16-cal | Calendly POST `/invitees` endpoint requires a paid Calendly plan. Free-plan tenants receive 403 Forbidden. The adapter surfaces 403 via `raise_for_status()`; the deploy-time runbook documents the requirement and a `scheduling_url` fallback. Risk accepted as a deploy-time prerequisite gate, not a code defect. | Phase 16 plan decision (16-05-PLAN.md threat_model, T-16-cal-paid) | 2026-07-01 |
| AR-16-03 | T-16-08 | Stripe idempotency key replay proof (live test-mode round-trip) is deferred to production gate. The code-level mitigation (forwarding `args.idempotency_key` to Stripe's `Idempotency-Key` header) is present and verified. Live end-to-end verification was operator-accepted as a post-deploy gate per 16-UAT.md (same pattern as Phase 13/15). | Phase 16 operator decision (16-07-SUMMARY.md, 16-UAT.md) | 2026-07-01 |

---

## Security Review Findings

The following code-review findings (16-REVIEW.md / 16-REVIEW-FIX.md) were security-relevant and verified as resolved before this audit:

| Finding | Security Impact | Resolution | Status |
|---------|----------------|------------|--------|
| WR-02: Shopify `activate_session()` cross-tenant bleed | Concurrent Celery workers could share a Shopify session, leaking `access_token` across tenants | All three `_sync()` closures in `shopify_adapter.py` now use `shopify.Session.temp(shop_url, version, token)` thread-safe context manager; token extracted inside each closure | Verified closed (`shopify_adapter.py:179,259,331`) |
| CR-03: `KeyError` on missing `shop_url`/`site_url` | Bare `KeyError` escaped the `ProviderNotConfiguredError` catch in `tools.py`, leaving an idempotency reservation stuck | Replaced `config_data["shop_url"]` / `config_data["site_url"]` with `.get()` + explicit `ProviderNotConfiguredError` in `provider_adapter.py` | Verified closed (`provider_adapter.py:284-293, 301-310`) |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-07-01 | 16 | 16 | 0 | gsd-security-auditor (claude-sonnet-4-6) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log (T-16-09, T-16-cal, T-16-08 live gate)
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-07-01
