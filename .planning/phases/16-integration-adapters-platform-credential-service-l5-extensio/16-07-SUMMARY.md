---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "07"
subsystem: integration-credentials-provisioning
tags: [integration, credentials, stripe, provisioning, single-currency, INT-07, INT-05]
dependency_graph:
  requires: [16-01, 16-03]
  provides: [deploy-time-credential-provisioning, operator-runbook, live-stripe-test]
  affects: [integration_credentials table, tenant DB encryption]
tech_stack:
  added: []
  patterns:
    - HKDF per-tenant key derivation (reuse of _derive_tenant_fernet from Plan 16-01)
    - argparse CLI provisioning script
    - Fernet BYTEA encryption via credential_service round-trip
    - env-gated pytest.mark.integration live test
key_files:
  created:
    - apps/api/scripts/provision_integration_credential.py
    - docs/runbooks/integration-credentials.md
    - apps/api/tests/integration/test_stripe_live.py
  modified: []
decisions:
  - "Open Question 3 resolved: deploy-time script + operator runbook, NOT new admin API endpoints (Phase 18 owns self-serve UI)"
  - "INT-07 single-currency guard implemented at provisioning time (script aborts on conflicting currency_code)"
  - "Credential input via --credential-file or --credential-stdin only (never argv — prevents shell history exposure)"
  - "T-16-03: runbook mandates Stripe Restricted Key scoped to only the enabled skills' permissions"
  - "T-16-08 live gate: deferred — live Stripe test creds not available to autonomous wave; mirrors Phase 13/15 pattern"
metrics:
  duration: "573s (~9m)"
  completed_date: "2026-06-30"
  tasks_completed: 2
  tasks_total: 3
  files_created: 3
  files_modified: 0
status: complete
---

# Phase 16 Plan 07: Deploy-Time Provisioning + Live Stripe Gate Summary

**One-liner:** Deploy-time CLI provisions Fernet-encrypted integration credentials with INT-07 single-currency enforcement; env-gated Stripe test-mode refund proof authored and deferred to the live gate (mirroring Phase 13/15).

---

## What Was Built

### Task 1 — Provisioning Script + Single-Currency Guard + Operator Runbook (COMPLETE)

**`apps/api/scripts/provision_integration_credential.py`**

Deploy-time CLI that writes an encrypted `integration_credentials` row into a tenant DB.

Key properties:
- Reuses `_derive_tenant_fernet` from `credential_service.py` (identical HKDF derivation) so provisioned ciphertext decrypts correctly at runtime via `get_adapter_for_skill`.
- **INT-07 single-currency guard:** before INSERT, queries all existing rows; aborts (exit 1) with a clear message if any existing row has a different `currency_code`. A tenant has exactly one currency.
- Credential read from `--credential-file` (file path) or `--credential-stdin` only — never from argv, preventing shell history exposure (T-16-01).
- Prints only non-secret confirmation: row ID, provider_type, currency_code, enabled_skills, config_data. Never prints raw credential or encrypted bytes.
- `--dry-run` flag: validates inputs and INT-07 guard without writing to the DB.
- Skills validated against provider's supported set (Stripe: issue_refund/update_subscription/place_order; Shopify/WooCommerce: place_order/cancel_order/issue_refund; Calendly: book_slot).
- `--help` exits 0 and lists all documented flags.

**`docs/runbooks/integration-credentials.md`**

Operator runbook covering:
- Per-provider provisioning instructions (Stripe, Shopify, WooCommerce, Calendly).
- **Stripe Restricted API Key scope→skill mapping** (T-16-03): one Restricted Key per tenant scoped to only the permissions matching `enabled_skills`.
- **INT-07 single-currency confirm step** and M8 pre-deploy checklist item.
- **Calendly paid-plan requirement** and `GET /event_types/{uuid}/scheduling_url` fallback when programmatic booking is unavailable (Pitfall 7).
- Explicit statement: self-serve credential admin UI is Phase 18, not Phase 16 (Open Question 3 resolved).
- Security checklist for operators (grep for key material, file mode 600, key type check).

Commit: `c6ee0d2` — `feat(16-07): provisioning script + operator runbook (INT-07 single-currency, deploy-time provisioning)`

### Task 2 — Env-Gated Live Stripe Test (COMPLETE, authored — exercised at gate)

**`apps/api/tests/integration/test_stripe_live.py`**

Gated on `STRIPE_TEST_MODE_ENABLED=1`. Skips cleanly without the env var (2 skipped, 0 failed — default CI stays green).

Tests:
1. `test_stripe_live_refund_and_idempotency_replay` — issues a real Stripe test-mode refund via `StripeAdapter.issue_refund`, asserts `status == "refunded"` and `refund_id` starts with `re_`; then calls again with the same `idempotency_key` and asserts the same `refund_id` is returned (T-16-08 / TXN-02 Stripe native Idempotency-Key replay).
2. `test_credential_handle_repr_is_always_redacted` — structural proof that `CredentialHandle.__repr__`, `__str__`, and f-string format all return `<CredentialHandle:redacted>` (T-16-01 invariant).

Security checks: no `sk_/rk_` key prefix literals in the file; key read from `STRIPE_TEST_API_KEY` env only.

Commit: `64622ff` — `feat(16-07): env-gated live Stripe test-mode refund proof (INT-05, T-16-08)`

### Task 3 — Live Gate (DEFERRED — see checkpoint below)

The live Stripe test-mode gate requires real Stripe test-mode credentials which are not available to the autonomous executor. This mirrors the Phase 13/14/15 live-gate deferral pattern. The autonomous artifacts (Tasks 1 and 2) are complete and correct.

---

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as written for the autonomous tasks.

### Threat Flag: Live Gate Deferred (expected per plan)

The plan's `autonomous: false` frontmatter and Task 3's `type="checkpoint:human-verify"` explicitly model this deferral. The live proof (INT-05) cannot be auto-executed without real Stripe test-mode credentials (`sk_test_...` or `rk_test_...`). This is consistent with the checkpoint_behavior instruction in the execution context.

---

## Threat Surface Scan

No new network endpoints added. No new auth paths. The provisioning script is a local CLI tool; it does not expose any network surface. The live test file adds a test-only path that is gated by env var.

Threat register entries closed by autonomous artifacts (T-16-01, T-16-05, T-16-cur):

| Threat | Mitigation Applied |
|---|---|
| T-16-01: key in stdout/logs | Script prints only non-secret metadata; test reads key from env only (no literal) |
| T-16-05: PLATFORM_CREDENTIAL_KEY handling | Read from env only; never written to DB or printed |
| T-16-cur: multi-currency drift | Provisioning script aborts on conflicting currency_code (INT-07) |
| T-16-03: over-scoped Stripe key | Runbook documents per-tenant Restricted Key scope→skill mapping |
| T-16-08: Stripe refund replay | Test asserts same idempotency_key → same refund_id (Stripe native Idempotency-Key) |

---

## Known Stubs

None — the provisioning script writes real encrypted rows; the test exercises the real StripeAdapter code path (when enabled). No placeholder data that flows to UI rendering.

---

## Live Gate Disposition

**Status: DEFERRED — awaiting human action (Task 3, checkpoint:human-verify)**

The live Stripe test-mode refund gate requires the operator to:

1. Obtain a Stripe test-mode Restricted Key scoped to `Refunds → Write`.
2. Create a test charge (e.g. via Stripe CLI or Dashboard) and note the `ch_...` ID.
3. Export env vars:
   ```
   export STRIPE_TEST_MODE_ENABLED=1
   export STRIPE_TEST_API_KEY="<rk_test or sk_test key>"
   export STRIPE_TEST_CHARGE_ID="<ch_... charge ID>"
   ```
4. Run: `cd apps/api && pytest tests/integration/test_stripe_live.py -m integration -x -q -s`
5. Verify the run shows a real `re_...` refund_id and the idempotency replay returns the same ID.
6. Grep logs to confirm no raw key appears: `2>&1 | grep -E "(sk_|rk_)" | grep -v "STRIPE_TEST"` — expect empty.

Reply `approved: live Stripe refund + replay verified, no credential leak` to close this gate.

---

## Self-Check: PASSED

- `apps/api/scripts/provision_integration_credential.py` exists and `--help` exits 0.
- `docs/runbooks/integration-credentials.md` exists and covers all required sections.
- `apps/api/tests/integration/test_stripe_live.py` exists; 2 tests collect; both skip without `STRIPE_TEST_MODE_ENABLED`.
- Commits c6ee0d2 and 64622ff exist in git log.
- No `sk_/rk_` key literals in test file.
- Script imports `_derive_tenant_fernet` from `credential_service`.
- INT-07 guard present and aborts on conflicting currency.
