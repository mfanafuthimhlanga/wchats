"""
Live Stripe test-mode refund proof — env-gated (INT-05, T-16-08).

Gates:
    STRIPE_TEST_MODE_ENABLED=1  (entire module skips when unset)
    STRIPE_TEST_API_KEY         Stripe test-mode Restricted Key (starts with rk_test or sk_test prefix)
    STRIPE_TEST_CHARGE_ID       A real test-mode charge ID (ch_...) to refund against

Purpose:
    Prove that StripeAdapter.issue_refund produces a REAL Stripe refund object
    through the typed tool path, and that calling issue_refund again with the
    SAME idempotency_key returns the SAME refund_id (Stripe native Idempotency-Key
    replay — T-16-08, TXN-02 defense-in-depth).

    This test is AUTHORED here and EXERCISED at the Task-3 human gate.
    By default (STRIPE_TEST_MODE_ENABLED unset) it collects and skips cleanly
    so the default CI suite stays green without live Stripe credentials.

Security invariants (T-16-01):
    - The Stripe test key is read from env only (no literal sk_/rk_ in this file).
    - The CredentialHandle repr is '<CredentialHandle:redacted>' — never the raw key.
    - Log output from the adapter includes only refund_id and status (see stripe_adapter.py).

How to run the live gate:
    1. Obtain a Stripe test-mode Restricted Key scoped to Refunds → Write.
    2. Create a test charge in Stripe Dashboard or via the Stripe CLI:
         stripe payment_intents create --amount=2000 --currency=usd --confirm --payment-method=pm_card_visa
       Note the charge ID (ch_...).
    3. Export env vars:
         export STRIPE_TEST_MODE_ENABLED=1
         export STRIPE_TEST_API_KEY="<your-stripe-test-restricted-key>"
         export STRIPE_TEST_CHARGE_ID="ch_..."
    4. Run:
         cd apps/api
         pytest tests/integration/test_stripe_live.py -m integration -x -q -s
    5. Grep the output for key material to confirm no leakage:
         pytest ... 2>&1 | grep -E "(sk_|rk_|test_)" | grep -v "STRIPE_TEST" | head
       Expect empty output (no raw key in logs).

Verification steps for the human gate (Task 3, Plan 16-07):
    - test_stripe_live_refund_and_idempotency_replay PASSES with a real refund_id.
    - Replay call returns the same refund_id (Stripe native Idempotency-Key confirmed).
    - grep on run logs confirms CredentialHandle repr is '<CredentialHandle:redacted>'.
    - grep on run logs shows no raw key fragment.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    # Annotation-only. The runtime imports stay inside the helper bodies below,
    # because this module is env-gated and must import cleanly on a machine with
    # no `stripe` package and no Stripe credentials. TYPE_CHECKING is never true
    # at runtime, so nothing here is imported when the gate is off.
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle

# ---------------------------------------------------------------------------
# Gate: entire module skips when STRIPE_TEST_MODE_ENABLED != "1"
# ---------------------------------------------------------------------------

_STRIPE_TEST_MODE_ENABLED = os.environ.get("STRIPE_TEST_MODE_ENABLED", "") == "1"
_STRIPE_TEST_API_KEY = os.environ.get("STRIPE_TEST_API_KEY", "")
_STRIPE_TEST_CHARGE_ID = os.environ.get("STRIPE_TEST_CHARGE_ID", "")

pytestmark = pytest.mark.skipif(
    not _STRIPE_TEST_MODE_ENABLED,
    reason=(
        "Skipping live Stripe test-mode refund gate — "
        "set STRIPE_TEST_MODE_ENABLED=1 (and STRIPE_TEST_API_KEY, STRIPE_TEST_CHARGE_ID) "
        "to run this test (INT-05, T-16-08 live gate, Plan 16-07 Task 3)."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credential_handle() -> "CredentialHandle":
    """Build a CredentialHandle from the test-mode key in env.

    T-16-01: the key is never passed as a literal — only read from env.
    The CredentialHandle wraps it; repr returns '<CredentialHandle:redacted>'.
    """
    from app.services.transactional.credential_service import CredentialHandle

    if not _STRIPE_TEST_API_KEY:
        pytest.skip("STRIPE_TEST_API_KEY env var is not set.")

    # Blob format matches what the provisioning script writes:
    # {"api_key": "<key>"} — the stripe_adapter json.loads this inside the closure.
    credential_blob = json.dumps({"api_key": _STRIPE_TEST_API_KEY})
    return CredentialHandle(_raw=credential_blob)


def _make_adapter(handle: "CredentialHandle") -> "StripeAdapter":
    """Instantiate StripeAdapter with test credentials and usd currency."""
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter

    return StripeAdapter(handle=handle, currency_code="usd")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_stripe_live_refund_and_idempotency_replay():
    """INT-05 live proof: issue_refund produces a real Stripe refund; replay returns same id.

    Assertions:
    1. IssueRefundOutput.status == "refunded" (live Stripe test-mode refund issued).
    2. IssueRefundOutput.refund_id starts with "re_" (real Stripe refund ID format).
    3. A second call with the SAME idempotency_key returns the SAME refund_id
       (Stripe native Idempotency-Key replay — T-16-08 / TXN-02).
    4. CredentialHandle.__repr__ is '<CredentialHandle:redacted>' at all times.
    """
    import asyncio

    from app.services.transactional.schemas import IssueRefundInput

    if not _STRIPE_TEST_CHARGE_ID:
        pytest.skip(
            "STRIPE_TEST_CHARGE_ID env var is not set. "
            "Create a test charge and export its ch_... ID."
        )

    handle = _make_credential_handle()
    adapter = _make_adapter(handle)

    # T-16-01: confirm the repr is redacted before any network call
    assert repr(handle) == "<CredentialHandle:redacted>", (
        f"CredentialHandle repr must be redacted; got {repr(handle)!r}"
    )

    # Use a unique idempotency_key per test run to avoid stale Stripe replay state
    idempotency_key = f"live-gate-refund-{uuid4()}"

    refund_args = IssueRefundInput(
        idempotency_key=idempotency_key,
        order_id=_STRIPE_TEST_CHARGE_ID,
        refund_amount_cents=50,  # refund 50 cents in test mode
        reason="test",
    )

    # --- First call: issue the refund ---
    result1 = asyncio.run(adapter.issue_refund(refund_args, agent_id="live-gate-agent"))

    assert result1.status == "refunded", (
        f"Expected status='refunded', got {result1.status!r}. "
        f"Full result: {result1}"
    )
    assert result1.refund_id.startswith("re_"), (
        f"Expected refund_id to start with 're_' (real Stripe ID); "
        f"got {result1.refund_id!r}"
    )

    first_refund_id = result1.refund_id
    print(f"\nFirst refund: refund_id={first_refund_id}, status={result1.status}")

    # --- Second call: same idempotency_key → Stripe must return the same refund_id ---
    result2 = asyncio.run(adapter.issue_refund(refund_args, agent_id="live-gate-agent"))

    assert result2.refund_id == first_refund_id, (
        f"T-16-08 idempotency replay FAILED: "
        f"first refund_id={first_refund_id!r}, "
        f"second refund_id={result2.refund_id!r}. "
        "Stripe's native Idempotency-Key should return the same refund object."
    )
    assert result2.status == "refunded", (
        f"Expected status='refunded' on replay, got {result2.status!r}"
    )

    print(
        f"Replay: refund_id={result2.refund_id} (matches first — T-16-08 VERIFIED)"
    )

    # --- T-16-01: repr still redacted after all network calls ---
    assert repr(handle) == "<CredentialHandle:redacted>", (
        "CredentialHandle repr changed after network call — CRITICAL"
    )


@pytest.mark.integration
def test_credential_handle_repr_is_always_redacted():
    """T-16-01: CredentialHandle.__repr__ never exposes the raw key value.

    This test runs regardless of STRIPE_TEST_CHARGE_ID — it only needs the key in env.
    The repr invariant is structural (no network call needed).
    """
    handle = _make_credential_handle()

    # __repr__
    assert repr(handle) == "<CredentialHandle:redacted>", (
        f"CredentialHandle repr must be redacted; got {repr(handle)!r}"
    )
    # __str__
    assert str(handle) == "<CredentialHandle:redacted>", (
        f"CredentialHandle str must be redacted; got {str(handle)!r}"
    )

    # format() — e.g. f"{handle}" or f"{handle!r}"
    assert f"{handle}" == "<CredentialHandle:redacted>", (
        f"f-string format must be redacted; got {f'{handle}'!r}"
    )
    assert f"{handle!r}" == "<CredentialHandle:redacted>", (
        f"f-string !r format must be redacted; got {f'{handle!r}'!r}"
    )

    # The raw value is accessible via .use() only (expected by the adapter)
    raw = handle.use()
    assert _STRIPE_TEST_API_KEY in raw, (
        "handle.use() should return the credential blob containing the test key"
    )
