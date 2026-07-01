---
status: deferred
phase: 16-integration-adapters-platform-credential-service-l5-extensio
source: [16-07-SUMMARY.md]
started: 2026-06-30T20:26:00Z
updated: 2026-07-01T00:00:00Z
---

## Current Test

number: 1
name: Live Stripe test-mode refund + idempotency replay gate (INT-05, T-16-08)
expected: |
  `StripeAdapter.issue_refund` issues a real test-mode refund via the Stripe API and
  returns status=="refunded" with a refund_id starting with "re_". A second call with
  the same idempotency_key returns the same refund_id (Stripe native Idempotency-Key
  replay — T-16-08 / TXN-02). No raw key material (sk_/rk_ prefix) appears in stdout
  or logs.
awaiting: production-like infra — operator deferred 2026-07-01 (accepted deferral, not a failure)

## Tests

### 1. Live Stripe test-mode refund + idempotency replay (INT-05, T-16-08)

**Background:** The autonomous executor deferred this gate because real Stripe test-mode
credentials were not available during the execution wave. The test file
`apps/api/tests/integration/test_stripe_live.py` is authored and correct; it skips
cleanly without the env vars (2 skipped, 0 failed — CI stays green). This test must
be run manually by the operator before Phase 16 is considered fully live-verified.

expected: |
  1. `test_stripe_live_refund_and_idempotency_replay` passes: real `re_...` refund_id
     returned; same idempotency_key on replay returns the identical `re_...` id.
  2. `test_credential_handle_repr_is_always_redacted` passes: __repr__, __str__, and
     f-string format all return `<CredentialHandle:redacted>` (T-16-01 invariant).
  3. No `sk_/rk_` key prefix appears in captured stdout or the run log.

how: |
  Prerequisites:
  1. Obtain a Stripe test-mode Restricted Key scoped to `Refunds → Write`
     (Dashboard → Developers → Restricted keys, or use a full sk_test_... key for initial
     testing).
  2. Create a test PaymentIntent/Charge (e.g. via Stripe CLI):
       stripe trigger payment_intent.succeeded
     Note the resulting `ch_...` or `pi_...` charge ID from the Stripe Dashboard.
  3. Export env vars:
       export STRIPE_TEST_MODE_ENABLED=1
       export STRIPE_TEST_API_KEY="<rk_test_... or sk_test_... key>"
       export STRIPE_TEST_CHARGE_ID="<ch_... charge ID>"
  4. Run:
       cd apps/api
       pytest tests/integration/test_stripe_live.py -m integration -x -q -s
  5. Verify output shows a real `re_...` refund_id and the replay call returns the same ID.
  6. Grep for credential leak:
       pytest tests/integration/test_stripe_live.py -m integration -s 2>&1 \
         | grep -E "(sk_|rk_)" | grep -v "STRIPE_TEST"
     Expect: empty output (no raw key in stdout).

result: [deferred — operator accepted deferral to production-like infra on 2026-07-01; run the runbook above on prod to close INT-05 / T-16-08. Adapter code is complete + unit-tested; the live provider round-trip is the only unproven piece.]

## Summary

total: 1
passed: 0
issues: 0
pending: 0
skipped: 1
blocked: 0
deferred: 1

## Gaps

- T-16-08 (Stripe native Idempotency-Key replay) and INT-05 (live provider action) cannot
  be proven by unit tests alone — the live gate is required to close these two items.
- This mirrors the Phase 13 AWS live-gate deferral (plans 13-08..11), Phase 14 live-DB
  deferral (14-UAT.md items 1-3), and Phase 15 ACT-06 latency deferral (15-03-SUMMARY.md).
  All deferred items are in UAT files so `/gsd-verify-work` surfaces them.
