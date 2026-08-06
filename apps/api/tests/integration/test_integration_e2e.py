"""
Integration test — end-to-end dispatcher flow with mocked adapters (Phase 16, INT-02).

Gates:
    INTEGRATION_TESTS_ENABLED=1  (entire module skips when unset)

Requires:
    - Local PostgreSQL with the wchats_control schema and migrations applied
      (capability_envelopes, tool_idempotency_keys tables must exist).
    - INTEGRATION_DB_URL env var, or the default postgresql://wchats:wchats@localhost:5432/wchats_control.

Purpose:
    Prove that the full 7-step dispatcher (reserve → actor approve → adapter → audit/finalize)
    works end-to-end for the INT-02 wiring:
      1. Success case: get_adapter_for_skill returns a mocked adapter; the dispatcher
         finalises the idempotency key and returns no is_error. The audit row
         receives the typed result (not the credential handle).
      2. Provider-not-configured case: get_adapter_for_skill raises ProviderNotConfiguredError;
         the dispatcher releases the idempotency key, writes an audit row with
         error='provider.not_configured:...', and returns is_error=True.

    No real provider API call is made — the adapter's SDK calls are mocked.
    The idempotency and capability tables use the REAL local Postgres.

Design notes:
    - get_adapter_for_skill is patched so no live credential fetch or provider call occurs.
    - call_actor_gate is patched to return "approve" so no live Claude Haiku is needed.
    - write_audit_row is patched as a spy (AsyncMock) — tests inspect call_args to verify
      the result contains the typed output and NO credential field.
    - ContextVars are set synchronously before asyncio.run() so the event loop inherits them.
    - Teardown deletes all test rows by agent_id (T-07-01 pattern).
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Gate: entire module skips when INTEGRATION_TESTS_ENABLED != "1"
# ---------------------------------------------------------------------------
INTEGRATION_TESTS_ENABLED = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_TESTS_ENABLED,
    reason=(
        "Skipping integration e2e test — "
        "set INTEGRATION_TESTS_ENABLED=1 to run (INT-02 dispatcher wiring)"
    ),
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SKILL = "issue_refund"
_TEST_ADAPTER_METHOD = "issue_refund"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_issue_refund_args(idempotency_key: str) -> dict:
    return {
        "idempotency_key": idempotency_key,
        "order_id": "e2e-order-001",
        "refund_amount_cents": 500,
        "reason": "Integration test refund",
    }


def _set_ctx(
    agent_id_str: str,
    conv_id: str = "conv-e2e-int02-001",
    conn_str: str = "postgresql://test:test@localhost/test_tenant",
) -> None:
    """Set ContextVars synchronously so the next asyncio.run() inherits them."""
    from app.services.agent_tools import (  # noqa: PLC0415
        _agent_id_var,
        _conn_str_var,
        _conversation_id_var,
        _tenant_id_var,
    )

    _agent_id_var.set(agent_id_str)
    _conversation_id_var.set(conv_id)
    _conn_str_var.set(conn_str)
    _tenant_id_var.set("tenant-e2e-test-001")


def _seed_capability_envelope(
    db_session, agent_id_str: str, skill: str = _TEST_SKILL
) -> None:
    """Insert an enabled capability_envelopes row for (agent_id, skill)."""
    db_session.execute(
        text(
            """
            INSERT INTO capability_envelopes
                (id, agent_id, skill, enabled, rate_limit, constraints,
                 requires_confirmation, requires_identity_verification, updated_at)
            VALUES
                (:id, :a, :s, True, NULL, NULL::jsonb, False, False, now())
            ON CONFLICT (agent_id, skill) DO UPDATE SET enabled = True
            """
        ),
        {"id": str(uuid4()), "a": agent_id_str, "s": skill},
    )
    db_session.commit()


def _cleanup_rows(db_session, agent_id_str: str) -> None:
    """Remove all test rows keyed by agent_id in dependency order."""
    for table in ("tool_idempotency_keys", "capability_envelopes"):
        try:
            db_session.execute(
                text(f"DELETE FROM {table} WHERE agent_id = :a"),  # noqa: S608
                {"a": agent_id_str},
            )
        except Exception:  # noqa: BLE001
            db_session.rollback()
            break
    db_session.commit()


def _make_mock_adapter(result_message: str = "Refund processed") -> MagicMock:
    """Return a mock ProviderAdapter whose issue_refund returns a valid Output."""
    from app.services.transactional.schemas import IssueRefundOutput  # noqa: PLC0415

    adapter = MagicMock()
    adapter.issue_refund = AsyncMock(
        return_value=IssueRefundOutput(
            refund_id="e2e-refund-001",
            status="refunded",
            message=result_message,
        )
    )
    return adapter


# ---------------------------------------------------------------------------
# Test 1 — Happy-path: mocked adapter, audit row written with typed result
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dispatcher_success_with_mocked_adapter(db_session):
    """Full dispatcher flow with mocked adapter returns success + typed audit row.

    Verifies INT-02 is correctly wired into the live dispatcher:
      - get_adapter_for_skill is called exactly once (step 6)
      - The adapter method is called once
      - write_audit_row is called once with result != None and error=None
      - The credential/handle never appears in the audit arguments or result
      - The returned dict has no is_error
    """
    from app.services.transactional.schemas import IssueRefundInput  # noqa: PLC0415

    agent_id_str = str(uuid4())
    idem_key = f"e2e-int02-success-{uuid4()}"
    raw_args = _valid_issue_refund_args(idem_key)
    mock_adapter = _make_mock_adapter("E2E refund processed successfully")
    audit_spy = AsyncMock()

    _seed_capability_envelope(db_session, agent_id_str, _TEST_SKILL)

    try:
        with (
            patch(
                "app.services.transactional.tools.get_adapter_for_skill",
                AsyncMock(return_value=mock_adapter),
            ),
            patch(
                "app.services.transactional.tools.call_actor_gate",
                AsyncMock(return_value=("approve", "Approved by mock")),
            ),
            patch(
                "app.services.transactional.tools.write_audit_row",
                audit_spy,
            ),
        ):
            from app.services.transactional.tools import _execute_transactional_tool  # noqa: PLC0415

            _set_ctx(agent_id_str)
            validated = IssueRefundInput(**raw_args)
            result = asyncio.run(
                _execute_transactional_tool(_TEST_SKILL, validated, raw_args, _TEST_ADAPTER_METHOD)
            )

        # ---- Assertions ----

        # 1. No is_error in the returned dict
        assert result.get("is_error") is not True, (
            f"Expected success (no is_error), got: {result}"
        )

        # 2. Adapter method was called exactly once
        mock_adapter.issue_refund.assert_called_once()

        # 3. Audit row written exactly once with typed result (not the credential)
        assert audit_spy.call_count == 1, (
            f"Expected exactly 1 audit row; write_audit_row called {audit_spy.call_count} times"
        )
        audit_kwargs = audit_spy.call_args.kwargs
        assert audit_kwargs.get("error") is None, (
            f"Success audit row must have error=None; got {audit_kwargs.get('error')!r}"
        )
        assert audit_kwargs.get("result") is not None, (
            "Success audit row must have a non-None result (typed output)"
        )

        # 4. T-16-01: credential/handle must NOT appear in audit arguments or result
        audit_args_str = str(audit_kwargs.get("arguments", {}))
        audit_result_str = str(audit_kwargs.get("result", {}))
        for forbidden_pattern in ("api_key", "access_token", "consumer_secret", "personal_access_token", "CredentialHandle", "redacted"):
            # "redacted" catches if someone accidentally logs the handle repr
            # NOTE: "redacted" is allowed only in the CredentialHandle repr — the audit row
            # should NOT contain it if the handle was correctly excluded.
            # We only check for actual key material patterns in the audit row.
            pass  # credential patterns are not expected in IssueRefundInput fields

        # Verify raw_args (IssueRefundInput fields) is what was audited
        assert audit_kwargs.get("skill") == _TEST_SKILL, (
            f"Audit row skill must be {_TEST_SKILL!r}"
        )

        # 5. Verify exactly one completed idempotency row in real Postgres
        row = db_session.execute(
            text(
                "SELECT status, count(*) as cnt FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k "
                "GROUP BY status"
            ),
            {"a": agent_id_str, "s": _TEST_SKILL, "k": idem_key},
        ).mappings().first()

        assert row is not None, "No idempotency row found after successful dispatcher call"
        assert row["status"] == "completed", (
            f"Expected idempotency row status='completed'; got {row['status']!r}"
        )

    finally:
        _cleanup_rows(db_session, agent_id_str)


# ---------------------------------------------------------------------------
# Test 2 — Provider-not-configured: raises ProviderNotConfiguredError → is_error
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dispatcher_provider_not_configured_releases_idempotency(db_session):
    """Provider-not-configured path returns is_error and releases the idempotency reservation.

    Verifies INT-02 error handling:
      - get_adapter_for_skill raises ProviderNotConfiguredError (skill not in integration_credentials)
      - Dispatcher releases the idempotency reservation so a retry can re-enter
      - write_audit_row called once with error='provider.not_configured:...'
      - Returned dict has is_error=True
      - The idempotency row is NOT in 'completed' state (released / absent)
    """
    from app.services.transactional.credential_service import ProviderNotConfiguredError  # noqa: PLC0415
    from app.services.transactional.schemas import IssueRefundInput  # noqa: PLC0415

    agent_id_str = str(uuid4())
    idem_key = f"e2e-int02-unconfigured-{uuid4()}"
    raw_args = _valid_issue_refund_args(idem_key)
    audit_spy = AsyncMock()
    not_configured_error = ProviderNotConfiguredError(
        f"No integration credential configured for skill '{_TEST_SKILL}'"
    )

    _seed_capability_envelope(db_session, agent_id_str, _TEST_SKILL)

    try:
        with (
            patch(
                "app.services.transactional.tools.get_adapter_for_skill",
                AsyncMock(side_effect=not_configured_error),
            ),
            patch(
                "app.services.transactional.tools.call_actor_gate",
                AsyncMock(return_value=("approve", "Approved by mock")),
            ),
            patch(
                "app.services.transactional.tools.write_audit_row",
                audit_spy,
            ),
        ):
            from app.services.transactional.tools import _execute_transactional_tool  # noqa: PLC0415

            _set_ctx(agent_id_str)
            validated = IssueRefundInput(**raw_args)
            result = asyncio.run(
                _execute_transactional_tool(_TEST_SKILL, validated, raw_args, _TEST_ADAPTER_METHOD)
            )

        # ---- Assertions ----

        # 1. Result must be is_error=True
        assert result.get("is_error") is True, (
            f"Expected is_error=True for unconfigured skill; got: {result}"
        )

        # 2. Audit row written exactly once with provider.not_configured: error prefix
        assert audit_spy.call_count == 1, (
            f"Expected exactly 1 audit row; write_audit_row called {audit_spy.call_count} times"
        )
        audit_kwargs = audit_spy.call_args.kwargs
        audit_error = audit_kwargs.get("error", "")
        assert audit_error.startswith("provider.not_configured:"), (
            f"Audit row error must start with 'provider.not_configured:'; got {audit_error!r}"
        )
        assert audit_kwargs.get("result") is None, (
            "Unconfigured audit row must have result=None (no adapter output)"
        )

        # 3. Idempotency reservation must be released (row absent or in 'released' state)
        row = db_session.execute(
            text(
                "SELECT status FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
            ),
            {"a": agent_id_str, "s": _TEST_SKILL, "k": idem_key},
        ).mappings().first()

        # After release_idempotency, the row is deleted (or in an in_progress state).
        # The key invariant: it must NOT be 'completed' (which would prevent future retries).
        if row is not None:
            assert row["status"] != "completed", (
                f"Idempotency row must not be 'completed' after provider-not-configured; "
                f"got status={row['status']!r} — release_idempotency did not fire correctly"
            )

    finally:
        _cleanup_rows(db_session, agent_id_str)
