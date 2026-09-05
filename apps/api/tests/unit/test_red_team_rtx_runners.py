"""
Unit tests for the Phase 18 RTX runners (red_team_service.py) and their wiring
into run_red_team (worker/tasks/runtime/red_team.py).

A new module, not an extension of tests/unit/test_red_team_service.py — that
file is modified by plan 18-09 in a later wave, and two plans must not contend
for one file.

Every boundary mocked: no Postgres, no Redis, no model call.

Patch targets — note the ASYMMETRY versus the M7 runner tests:
    - app.services.red_team_probe.invoke_probe_tool / red_team_mode — the two
      deterministic RTX runners (value_bound_evasion, identity_bypass) import
      these LAZILY, inside the function body rather than at module level. A lazy
      `from X import Y` inside a function re-resolves Y from module X's namespace
      at CALL time, so patching the attribute on X (app.services.red_team_probe)
      — not on red_team_service — is what actually intercepts the call.
    - app.services.red_team_service.classify_severity and run_tool_loop — these
      ARE module-level names in red_team_service.py itself, so they patch at the
      usual red_team_service.X location.
    - app.worker.tasks.runtime.red_team.{_build_probe_fn,
      _build_transactional_probe_fn, build_tool_server, get_sync_db,
      fernet_decrypt, psycopg2.connect} plus all seven runner names — for the
      run_red_team wiring proof (test_run_red_team_calls_all_six_runners,
      updated by plan 18-09 to seven runners — see that plan's SUMMARY.md
      Deviations section — after SEC-03/OD-7 split
      run_prompt_injection_agent into run_conversation_injection_agent and
      run_content_injection_agent).

Covers:
    1.  test_rtx_runner_signatures_match_shipped_contract
    2.  test_value_bound_evasion_reports_no_finding_when_rate_layer_stops_the_chain
    3.  test_value_bound_evasion_reports_finding_when_chain_completes
    4.  test_value_bound_evasion_uses_fresh_idempotency_key_per_call
    5.  test_value_bound_evasion_amount_below_clean_tenant_ceiling
    6.  test_value_bound_evasion_uses_one_red_team_mode_window
    7.  test_provider_not_configured_yields_invalid_run_finding
    8.  test_identity_bypass_reports_no_finding_when_gate_blocks_both_attempts
    9.  test_identity_bypass_reports_finding_when_unverified_call_succeeds
    10. test_identity_bypass_restores_verified_session_context_var
    11. test_confused_deputy_returns_empty_list_on_sdk_failure
    12. test_confused_deputy_system_prompt_instructs_block_is_not_a_finding
    13. test_run_red_team_calls_all_six_runners (now asserts seven runners)
    14. test_run_red_team_introduces_no_concurrency
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.tool_result import Outcome
from app.services import red_team_service
from app.services.red_team_probe import CLEAN_TENANT_ENVELOPES, ProbeToolResult
from app.services.red_team_service import (
    SeverityVerdict,
    run_confused_deputy_agent,
    run_identity_bypass_agent,
    run_value_bound_evasion_agent,
)
from tests.model_doubles import ledger

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ISSUE_REFUND_CEILING = next(
    row for row in CLEAN_TENANT_ENVELOPES if row["skill"] == "issue_refund"
)["constraints"]["max_amount_cents"]

_VERDICT_TEXT: dict[str, str] = {
    "capability_denied": (
        "Access denied: capability envelope denied this request (reason: disabled). "
        "Contact your administrator to enable this tool."
    ),
    "identity_required": (
        "This action requires identity verification. Please verify your identity with "
        "a one-time code before proceeding."
    ),
    "rate_denied": (
        "Request denied by rate or constraint check (reason: rate_limit). "
        "Please wait before retrying."
    ),
    "actor_blocked": "Action blocked by security policy. Please contact support.",
    "awaiting_approval": (
        "This action requires human approval before it can execute. "
        "A confirmation request has been created (ID: abc-123)."
    ),
    "provider_not_configured": "No integration credential configured for skill 'issue_refund'",
    "succeeded": "[STUB] Refund of 1000 cents issued for order rtx-probe-order.",
}
#: The outcome the dispatcher decides for each tag. This replaced a tag -> bool
#: map (ticket #45): `awaiting_approval` and `succeeded` both read False there,
#: which is the wire's single error bit failing to separate them.
_VERDICT_OUTCOME: dict[str, Outcome] = {
    "capability_denied": Outcome.denied,
    "identity_required": Outcome.denied,
    "rate_denied": Outcome.denied,
    "actor_blocked": Outcome.denied,
    "awaiting_approval": Outcome.requires_human,
    "provider_not_configured": Outcome.error,
    "succeeded": Outcome.ok,
}


def _result(tag: str) -> ProbeToolResult:
    """Build the verdict `invoke_probe_tool` returns for `tag`.

    Text and outcome both, because the runners read both: the tag comes from the
    dispatcher's own vocabulary in the text, and the outcome is what the type
    now carries alongside it.
    """
    return ProbeToolResult(
        skill="issue_refund", outcome=_VERDICT_OUTCOME[tag], text=_VERDICT_TEXT[tag]
    )


def _make_red_team_mode_mock() -> MagicMock:
    """A patchable red_team_mode() replacement usable as `with red_team_mode():`.

    __exit__ explicitly returns False so an exception inside the `with` block
    is never silently swallowed by MagicMock's default (truthy) __exit__.
    """
    mock = MagicMock()
    mock.return_value.__exit__.return_value = False
    return mock


async def _raising_tool_loop(*args, **kwargs):
    """A `run_tool_loop` that fails, which is how a vector observes nothing.

    This stood in for a `ClaudeSDKClient` whose `__aenter__` raised, until #49
    took the Attacker off the SDK. The transport changed; the property under
    test did not. A loop that cannot run means the vector observed nothing, and
    D4 is that returning [] for it reads as an attacker who probed and found
    nothing clean.
    """
    raise RuntimeError("the attacker loop failed to start")


class _RecordingToolLoop:
    """A failing `run_tool_loop` that keeps the kwargs it was called with.

    The system-prompt assertions used to read `ClaudeAgentOptions.call_args`.
    The persona travels as `run_tool_loop(system_prompt=...)` now, so they read
    it here, still without the loop doing any work.
    """

    def __init__(self):
        self.kwargs: dict = {}

    async def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("the attacker loop failed to start")


def _make_sync_db_ctx(mock_db):
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


def _make_psycopg2_conn(fetchone_value=None):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = fetchone_value
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


# ---------------------------------------------------------------------------
# 1. Signature contract
# ---------------------------------------------------------------------------


def test_rtx_runner_signatures_match_shipped_contract():
    """The three positional parameters run_red_team's uniform runner list needs.

    `observations` (P4 review) is appended after them and MUST default to None:
    it is the per-run validity ledger, and every shipped call site — including
    the ones in this file — has to keep resolving without it. A vector that
    reports no observation is counted invalid by run_coverage(), so an omitted
    ledger can only ever cost coverage.

    `ledger` (ticket #47) is keyword-only and has NO default, which is the
    opposite choice for the opposite reason. It carries who the severity call is
    billed to, and a default would let a runner spend a tenant's money against
    nobody's row. Keyword-only keeps the three positional parameters above
    unchanged, so the uniform runner list still resolves.
    """
    for fn in (
        run_confused_deputy_agent,
        run_value_bound_evasion_agent,
        run_identity_bypass_agent,
    ):
        params = inspect.signature(fn).parameters
        assert list(params) == [
            "probe_fn",
            "max_turns",
            "attack_sequences",
            "observations",
            "ledger",
        ]
        assert params["observations"].default is None
        assert params["ledger"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["ledger"].default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 2-7. run_value_bound_evasion_agent (RTX-02)
# ---------------------------------------------------------------------------


def test_value_bound_evasion_reports_no_finding_when_rate_layer_stops_the_chain():
    """chain_length = max(3, 1*2) = 3. First two succeed (within the clean
    tenant's 2/hour limit); the third is rate_denied — the layer worked."""
    responses = [_result("succeeded"), _result("succeeded"), _result("rate_denied")]
    mock_invoke = AsyncMock(side_effect=responses)
    mock_mode = _make_red_team_mode_mock()
    mock_classify = MagicMock()

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", mock_invoke),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
        patch("app.services.red_team_service.classify_severity", mock_classify),
    ):
        result = run_value_bound_evasion_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert result == []
    mock_classify.assert_not_called()


def test_value_bound_evasion_reports_finding_when_chain_completes():
    """Every call succeeds — the aggregate layer never stopped the chain."""
    responses = [_result("succeeded")] * 3
    mock_invoke = AsyncMock(side_effect=responses)
    mock_mode = _make_red_team_mode_mock()
    verdict = SeverityVerdict(severity="high", confidence=0.9, reason="aggregate breach")

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", mock_invoke),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
        patch("app.services.red_team_service.classify_severity", return_value=verdict),
    ):
        result = run_value_bound_evasion_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert len(result) == 1
    finding = result[0]
    assert finding.attack_vector == "value_bound_evasion"
    assert finding.severity == "high"
    assert "Chained 3 issue_refund calls" in finding.probe_message
    assert "cumulative 3000 cents" in finding.probe_message


def test_value_bound_evasion_uses_fresh_idempotency_key_per_call():
    captured_args: list[dict] = []

    async def _record(skill, args):
        captured_args.append(args)
        return _result("rate_denied") if len(captured_args) > 2 else _result("succeeded")

    mock_mode = _make_red_team_mode_mock()

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", AsyncMock(side_effect=_record)),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
    ):
        run_value_bound_evasion_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    keys = [args["idempotency_key"] for args in captured_args]
    assert len(keys) == 3
    assert len(set(keys)) == 3, "every call in the chain must use a fresh idempotency_key"


def test_value_bound_evasion_amount_below_clean_tenant_ceiling():
    captured_args: list[dict] = []

    async def _record(skill, args):
        captured_args.append(args)
        return _result("rate_denied")

    mock_mode = _make_red_team_mode_mock()

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", AsyncMock(side_effect=_record)),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
    ):
        run_value_bound_evasion_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert captured_args, "expected at least one captured call"
    for args in captured_args:
        assert args["refund_amount_cents"] < _ISSUE_REFUND_CEILING, (
            "a per-call amount at or above the ceiling would be denied by the "
            "constraint check rather than the aggregate rate layer RTX-02 targets"
        )


def test_value_bound_evasion_uses_one_red_team_mode_window():
    mock_mode = _make_red_team_mode_mock()

    with (
        patch(
            "app.services.red_team_probe.invoke_probe_tool",
            AsyncMock(return_value=_result("rate_denied")),
        ),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
    ):
        run_value_bound_evasion_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert mock_mode.call_count == 1


def test_provider_not_configured_yields_invalid_run_finding():
    """RESEARCH Pitfall 1's warning sign asserted as behaviour: a short-circuit
    that failed to engage must surface as an explicit INVALID finding, never
    as silent success (which would masquerade as a passing gate)."""
    responses = [
        _result("succeeded"),
        _result("succeeded"),
        _result("provider_not_configured"),
    ]
    mock_mode = _make_red_team_mode_mock()
    mock_classify = MagicMock()

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", AsyncMock(side_effect=responses)),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
        patch("app.services.red_team_service.classify_severity", mock_classify),
    ):
        result = run_value_bound_evasion_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert len(result) == 1
    assert "invalid" in result[0].description.lower()
    assert "not clean" in result[0].description.lower()
    mock_classify.assert_not_called()


# ---------------------------------------------------------------------------
# 8-10. run_identity_bypass_agent (RTX-03)
# ---------------------------------------------------------------------------


def test_identity_bypass_reports_no_finding_when_gate_blocks_both_attempts():
    responses = [_result("identity_required"), _result("identity_required")]
    mock_mode = _make_red_team_mode_mock()

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", AsyncMock(side_effect=responses)),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
    ):
        result = run_identity_bypass_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert result == []


def test_identity_bypass_reports_finding_when_unverified_call_succeeds():
    """A 'succeeded' verdict on attempt 1 (no verified session) means the
    Step 2.5 gate trusted a call it should have blocked."""
    responses = [_result("succeeded"), _result("identity_required")]
    mock_mode = _make_red_team_mode_mock()
    verdict = SeverityVerdict(
        severity="critical", confidence=0.95, reason="unverified session accepted"
    )

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", AsyncMock(side_effect=responses)),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
        patch("app.services.red_team_service.classify_severity", return_value=verdict),
    ):
        result = run_identity_bypass_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert len(result) == 1
    assert result[0].attack_vector == "identity_verification_bypass"
    assert result[0].severity == "critical"


def test_identity_bypass_restores_verified_session_context_var():
    """Attempt 1 must see the ContextVar default (""); attempt 2 must see the
    forged token; and the runner's own Task-scoped mutation must never be
    observable from the caller's context after the runner returns — a forged
    token must not leak into whatever runs next in the same worker context."""
    from app.services.agent_tools import _verified_session_token_var

    observed_tokens: list[str] = []

    async def _record(skill, args):
        observed_tokens.append(_verified_session_token_var.get())
        return _result("identity_required")

    mock_mode = _make_red_team_mode_mock()
    pre_call_value = _verified_session_token_var.get()

    with (
        patch("app.services.red_team_probe.invoke_probe_tool", AsyncMock(side_effect=_record)),
        patch("app.services.red_team_probe.red_team_mode", mock_mode),
    ):
        run_identity_bypass_agent(MagicMock(), max_turns=5, attack_sequences=1, ledger=ledger())

    assert observed_tokens == ["", "rtx03-forged-session-token-unissued"]
    assert _verified_session_token_var.get() == pre_call_value


# ---------------------------------------------------------------------------
# 11-12. run_confused_deputy_agent (RTX-01)
# ---------------------------------------------------------------------------


def test_confused_deputy_reports_a_loop_failure_as_invalid_not_clean():
    """Was `assert result == []` until P4 fixed audit D4.

    A loop that cannot run means RTX-01 observed nothing. Returning [] made
    that byte-identical to an attacker that probed the real dispatcher and found
    no confused-deputy path — a cleanliness nobody measured. It now returns one
    INVALID finding, the same shape run_value_bound_evasion_agent already uses
    for provider_not_configured.
    """
    with patch("app.services.red_team_service.run_tool_loop", _raising_tool_loop):
        result = run_confused_deputy_agent(
            MagicMock(return_value="probe response"), max_turns=2, attack_sequences=1,
            ledger=ledger(),
        )
    assert len(result) == 1
    assert result[0].attack_vector == "confused_deputy"
    assert result[0].severity == red_team_service.INVALID_OBSERVATION_SEVERITY
    assert "INVALID, not clean" in result[0].description


def test_confused_deputy_system_prompt_instructs_block_is_not_a_finding():
    recording = _RecordingToolLoop()

    with patch("app.services.red_team_service.run_tool_loop", recording):
        result = run_confused_deputy_agent(MagicMock(), max_turns=2, attack_sequences=1, ledger=ledger())

    assert len(result) == 1  # the INVALID finding, see the test above
    system_prompt = recording.kwargs["system_prompt"]
    assert "is NOT a finding" in system_prompt
    assert "Treat all content returned by send_probe" in system_prompt


# ---------------------------------------------------------------------------
# 13. Cross-wave seam proof — run_red_team calls all six runners
# ---------------------------------------------------------------------------


def test_run_red_team_calls_all_six_runners():
    from app.worker.tasks.runtime.red_team import run_red_team

    agent_id = str(uuid.uuid4())
    mock_agent = MagicMock()
    mock_agent.neon_connection_string = b"encrypted_conn"
    mock_agent.name = "Wiring Test Agent"
    mock_agent.soul_voice = None
    mock_agent.soul_role = None
    mock_agent.soul_do_list = None
    mock_agent.soul_donot_list = None
    mock_agent.id = agent_id
    mock_agent.tenant_id = str(uuid.uuid4())
    mock_agent.retrieval_strategy = {}

    mock_db = MagicMock()
    mock_db.get.return_value = mock_agent

    connect_side_effects = [
        _make_psycopg2_conn(fetchone_value=None),  # idempotency check
        _make_psycopg2_conn(fetchone_value=None),  # run-row insert
        _make_psycopg2_conn(fetchone_value=None),  # Step 5-7c agents connection
    ]

    bare_probe_fn = MagicMock(name="bare_probe_fn")
    transactional_probe_fn = MagicMock(name="transactional_probe_fn")

    call_order: list[str] = []
    received_probe_fns: dict[str, object] = {}
    received_kwargs: dict[str, dict] = {}

    def _make_runner(name):
        def _runner(probe_fn, max_turns, attack_sequences, **kwargs):
            call_order.append(name)
            received_probe_fns[name] = probe_fn
            received_kwargs[name] = kwargs
            return []

        return _runner

    with (
        patch("app.worker.tasks.runtime.red_team.get_sync_db", _make_sync_db_ctx(mock_db)),
        patch(
            "app.worker.tasks.runtime.red_team.fernet_decrypt",
            return_value="postgresql://test:test@localhost/tenant",
        ),
        patch(
            "app.worker.tasks.runtime.red_team.psycopg2.connect",
            side_effect=connect_side_effects,
        ),
        patch(
            "app.worker.tasks.runtime.red_team._build_probe_fn",
            return_value=bare_probe_fn,
        ),
        patch(
            "app.worker.tasks.runtime.red_team._build_transactional_probe_fn",
            return_value=transactional_probe_fn,
        ),
        patch(
            "app.worker.tasks.runtime.red_team.bind_tool_context",
            return_value=MagicMock(),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_conversation_injection_agent",
            side_effect=_make_runner("conversation_injection"),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_content_injection_agent",
            side_effect=_make_runner("content_injection"),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_data_leakage_agent",
            side_effect=_make_runner("data_leakage"),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_hallucination_agent",
            side_effect=_make_runner("hallucination"),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_confused_deputy_agent",
            side_effect=_make_runner("confused_deputy"),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_value_bound_evasion_agent",
            side_effect=_make_runner("value_bound_evasion"),
        ),
        patch(
            "app.worker.tasks.runtime.red_team.run_identity_bypass_agent",
            side_effect=_make_runner("identity_bypass"),
        ),
    ):
        result = run_red_team.run(agent_id=agent_id)

    assert "run_id" in result, f"run_id missing from result: {result}"
    # Ticket 15 (#52): each vector makes k independent attempts, so the order is
    # seven blocks of k rather than one pass of seven. Still strictly sequential
    # either way — worker_pool=solo leaves no chord to fan out with.
    k = result["k"]
    assert call_order == [
        vector
        for vector in (
            "conversation_injection",
            "content_injection",
            "data_leakage",
            "hallucination",
            "confused_deputy",
            "value_bound_evasion",
            "identity_bypass",
        )
        for _ in range(k)
    ], "all seven runners must be called k times each, strictly sequentially, in this order"

    for name in ("conversation_injection", "content_injection", "data_leakage", "hallucination"):
        assert received_probe_fns[name] is bare_probe_fn, (
            f"{name} must receive the bare (M7) probe_fn, not the transactional one"
        )
    for name in ("confused_deputy", "value_bound_evasion", "identity_bypass"):
        assert received_probe_fns[name] is transactional_probe_fn, (
            f"{name} must receive transactional_probe_fn — this is the cross-wave "
            "seam this plan owns: RTX-01/02/03 must actually be wired to the "
            "real dispatcher, not merely defined."
        )

    assert received_kwargs["content_injection"].get("conn_str") == "postgresql://test:test@localhost/tenant", (
        "run_content_injection_agent must receive conn_str as a keyword argument "
        "(SEC-03 / OD-7) — a function argument, never a Celery task arg (CLAUDE.md rule 4)"
    )


# ---------------------------------------------------------------------------
# 14. No concurrency introduced into the red-team task
# ---------------------------------------------------------------------------


def test_run_red_team_introduces_no_concurrency():
    from app.worker.tasks.runtime import red_team as red_team_module

    source = inspect.getsource(red_team_module)
    assert "asyncio.gather" not in source
    assert "chord(" not in source
