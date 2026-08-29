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

#: T-16-01/T-16-06 canary. A real adapter is built as
#: `StripeAdapter(handle=CredentialHandle(_raw=<decrypted secret>), ...)` in
#: provider_adapter.get_adapter_for_skill and holds that handle for its whole
#: lifetime. The mock adapter below carries one too, holding this string, so
#: "the raw credential never reaches the audit row or the agent" is something
#: this test can observe rather than assume.
#:
#: Deliberately not a plausible-looking key: it must be greppable and it must
#: never match anything the dispatcher legitimately writes.
_CREDENTIAL_CANARY = "T16-01-CANARY-4f3b9a1c-raw-provider-secret-must-never-be-audited"


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
    """Set ContextVars synchronously so the next asyncio.run() inherits them.

    `reset_side_effect_context()` is called first, and it is not decoration.
    `_side_effects_var` defaults to "live" but is **process-context sticky** —
    agent_tools' own docstring says so, and nothing resets it between tests. A
    single earlier caller in this interpreter that did
    `_side_effects_var.set("recorded")` without resetting
    (tests/unit/test_eval_agent_invocation.py:1039 does exactly that) would send
    both tests below down the step-5.5 recorded branch: the adapter is never
    called, `write_audit_row` gets `result=None` and the RECORDED_NOT_EXECUTED
    marker, and every assertion here fails for a reason that has nothing to do
    with INT-02. These two tests are *about* the live adapter path, so the mode
    they need is stated rather than inherited.
    """
    from app.services.agent_tools import (  # noqa: PLC0415
        _agent_id_var,
        _conn_str_var,
        _conversation_id_var,
        _tenant_id_var,
        reset_side_effect_context,
    )

    reset_side_effect_context()  # pin side_effects="live", empty sink
    _agent_id_var.set(agent_id_str)
    _conversation_id_var.set(conv_id)
    _conn_str_var.set(conn_str)
    _tenant_id_var.set("tenant-e2e-test-001")


def _seed_capability_envelope(
    db_session, agent_id_str: str, skill: str = _TEST_SKILL
) -> None:
    """Insert an enabled capability_envelopes row for (agent_id, skill).

    `constraints` is seeded as an empty JSONB object, NOT NULL. Migration 0014
    declares the column `JSONB NOT NULL DEFAULT '{}'::jsonb`, so the literal
    `NULL::jsonb` this fixture used to bind raised
    `NotNullViolation: null value in column "constraints"` on the very first
    statement of both tests — before the dispatcher was ever entered. It went
    unnoticed because this module has never run against a live Postgres.

    `{}` is also the value the dispatcher's step 4 expects: it reads
    `snapshot.get("constraints") or {}` and looks up `max_amount_cents`, so an
    empty object means "no amount ceiling", which is what these two tests want
    (they are about INT-02 adapter wiring, not about constraint enforcement).

    `rate_limit` stays NULL deliberately: `_parse_rate_limit(None)` returns None
    and step 4 then never touches Redis, so this module needs Postgres only.

    actor_mode is left to its schema default ('always-on', migration 0019) —
    call_actor_gate is patched in both tests, so the mode never selects a code
    path here, and pinning it would be a claim this module does not test.
    """
    db_session.execute(
        text(
            """
            INSERT INTO capability_envelopes
                (id, agent_id, skill, enabled, rate_limit, constraints,
                 requires_confirmation, requires_identity_verification, updated_at)
            VALUES
                (:id, :a, :s, True, NULL, '{}'::jsonb, False, False, now())
            ON CONFLICT (agent_id, skill) DO UPDATE
                SET enabled = True, constraints = '{}'::jsonb, rate_limit = NULL
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
    """Return a mock ProviderAdapter whose issue_refund returns a valid Output.

    The adapter carries a real `CredentialHandle` holding `_CREDENTIAL_CANARY`,
    mirroring how `get_adapter_for_skill` constructs every concrete adapter
    (`Adapter(handle=CredentialHandle(_raw=...))`). Without it the T-16-01
    assertion below would have no secret to look for.
    """
    from app.domain.transactional_schemas import IssueRefundOutput  # noqa: PLC0415
    from app.services.transactional.credential_service import CredentialHandle  # noqa: PLC0415

    adapter = MagicMock()
    handle = CredentialHandle(_raw=_CREDENTIAL_CANARY)
    # Both spellings: concrete adapters differ on whether the attribute is
    # public, and a leak would come from something reflecting over the adapter,
    # not from a specific attribute name.
    adapter.handle = handle
    adapter._handle = handle
    adapter.issue_refund = AsyncMock(
        return_value=IssueRefundOutput(
            refund_id="e2e-refund-001",
            status="refunded",
            message=result_message,
        )
    )
    return adapter


def _walk(value, path: str = "<root>"):
    """Depth-first walk over a nested payload, yielding (path, node) for every node."""
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}[{key!r}]")
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def _credential_leaks(payload, label: str) -> list[str]:
    """Return a description of every credential-shaped node found in *payload*.

    Two distinct leak shapes, because they fail differently:

      * the raw string — someone put `handle.use()`, or a config dict built from
        it, into `arguments` / `result` / `capability_snapshot`. This is the
        T-16-06 shape and it lands in the JSONB column verbatim.
      * a `CredentialHandle` instance — someone passed the handle (or the whole
        adapter) through. `CredentialHandle.__repr__` is redacted, so a
        string-only scan would call this clean; today it would blow up on
        `json.dumps` in the real `write_audit_row`, but the redaction is one
        `__repr__` edit away from turning a crash into a silent leak.
    """
    from app.services.transactional.credential_service import CredentialHandle  # noqa: PLC0415

    found: list[str] = []
    for path, node in _walk(payload, label):
        if isinstance(node, CredentialHandle):
            found.append(f"{path}: CredentialHandle instance")
        elif isinstance(node, str) and _CREDENTIAL_CANARY in node:
            found.append(f"{path}: raw credential string")
    return found


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
    from app.domain.transactional_schemas import IssueRefundInput  # noqa: PLC0415

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

        # 4. T-16-01 / T-16-06 — the credential never reaches the audit row or
        #    the agent. This replaces the dead loop that used to sit here (two
        #    strings built, six patterns iterated, body `pass`): it asserted
        #    nothing, so coverage of this threat in this file was zero.
        #
        #    Scope, stated plainly: `get_adapter_for_skill` is mocked, so this
        #    does NOT exercise credential *resolution* (fetch → Fernet decrypt →
        #    CredentialHandle) — that is provider_adapter's own contract. What
        #    it does pin is the dispatcher half of T-16-01: given an adapter
        #    that holds a live secret, everything `_execute_adapter_and_audit`
        #    hands to `write_audit_row`, and everything it returns to the agent,
        #    is derived from the adapter's *typed output*, never from the
        #    adapter object. That is the leak path a future edit would open.
        #
        #    4a. POSITIVE CONTROL, first. "assert no leak was found" is worth
        #    exactly what the finder is worth, and a scanner that never matches
        #    reports every payload clean — which is the dead loop this block
        #    replaces, in its purest form. So the scanner is required to find
        #    both leak shapes in a payload that really carries them, in this
        #    run, against this canary and this adapter's real handle, before
        #    its silence downstream is allowed to mean anything.
        control_hits = _credential_leaks(
            {"cfg": {"api_key": _CREDENTIAL_CANARY}, "adapter_handle": mock_adapter.handle},
            "positive_control",
        )
        assert len(control_hits) == 2, (
            "The credential scanner failed its own positive control, so the "
            "'no leak' assertions below prove nothing. Expected one raw-string "
            f"hit and one CredentialHandle hit; got {control_hits}"
        )

        leaks = _credential_leaks(
            {"args": audit_spy.call_args.args, "kwargs": dict(audit_kwargs)},
            "write_audit_row",
        )
        assert not leaks, (
            "T-16-01/T-16-06 violated — the provider credential reached the audit "
            f"row: {leaks}"
        )

        returned_leaks = _credential_leaks(result, "tool_response")
        assert not returned_leaks, (
            "T-16-01 violated — the provider credential reached the agent-visible "
            f"tool response: {returned_leaks}"
        )

        # The positive half of the same claim: the audited result is EXACTLY the
        # adapter's typed output. `not leaks` alone would still pass if the
        # dispatcher audited some other adapter-derived object that happened to
        # carry no secret today; this pins the shape as well as the absence.
        expected_result = mock_adapter.issue_refund.return_value.model_dump()
        assert audit_kwargs.get("result") == expected_result, (
            "Audit row result must be the adapter's typed output verbatim; got "
            f"{audit_kwargs.get('result')!r}, expected {expected_result!r}"
        )

        # Verify raw_args (IssueRefundInput fields) is what was audited
        assert audit_kwargs.get("skill") == _TEST_SKILL, (
            f"Audit row skill must be {_TEST_SKILL!r}"
        )
        assert audit_kwargs.get("arguments") == raw_args, (
            "Audit row must carry the caller's raw arguments unchanged"
        )
        # Step 5's verdict has to survive the handoff into step 7. This is the
        # one place the Actor seam and the audit row meet on the success path.
        assert audit_kwargs.get("actor_decision") == "approve", (
            "Audit row must carry the Actor verdict from step 5; got "
            f"{audit_kwargs.get('actor_decision')!r}"
        )

        # 5. Verify exactly one completed idempotency row in real Postgres.
        #    Fetched with .all(), not the previous "count(*) ... GROUP BY status"
        #    + .first(): that query computed a `cnt` column nothing ever read, so
        #    the comment's "exactly one" was never actually checked. The UNIQUE
        #    (agent_id, skill, idempotency_key) constraint makes >1 impossible,
        #    which is precisely why asserting it is cheap and why a 0 or a 2 here
        #    would mean something has gone badly wrong upstream.
        rows = db_session.execute(
            text(
                "SELECT status, result FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
            ),
            {"a": agent_id_str, "s": _TEST_SKILL, "k": idem_key},
        ).mappings().all()

        assert len(rows) == 1, (
            f"Expected exactly 1 idempotency row after a successful dispatcher "
            f"call; found {len(rows)}"
        )
        assert rows[0]["status"] == "completed", (
            f"Expected idempotency row status='completed'; got {rows[0]['status']!r}"
        )

        # 6. T-16-01 on the DURABLE surface. write_audit_row is a spy here, so
        #    tool_calls_audit is never written and the scan above only ever saw
        #    an in-memory kwargs dict. finalize_idempotency is NOT mocked: this
        #    JSONB column is the only thing this test actually commits to disk,
        #    and it holds the full tool_response the agent was handed. A
        #    credential that reached it would outlive the process, which is what
        #    makes it the strictest observation available here.
        persisted_leaks = _credential_leaks(
            rows[0]["result"], "tool_idempotency_keys.result"
        )
        assert not persisted_leaks, (
            "T-16-01 violated — the provider credential was PERSISTED in "
            f"tool_idempotency_keys.result: {persisted_leaks}"
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
    from app.domain.transactional_schemas import IssueRefundInput  # noqa: PLC0415
    from app.services.transactional.credential_service import ProviderNotConfiguredError  # noqa: PLC0415

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
