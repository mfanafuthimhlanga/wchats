"""Recorded mode at the tool layer: the eval may drive the agent without moving money.

Why this file exists
--------------------
`.dev/plans/260807-d1-agent-invocation.md` P2 makes the nightly eval invoke the
customer agent through the same seam `run_agent_turn` uses. That seam returns
options carrying a LIVE tool server bound to the tenant's real connection
string, and six of the eleven tools it grants reach a real `ProviderAdapter`.
Without this change, an eval scenario in which the agent decides to refund
executes a refund (BACKLOG 2.5).

The owner settled it on 2026-08-07: a mandatory `side_effects` mode on the seam,
`"live"` or `"recorded"`, **with no default** — a caller that does not say which
it wants raises `TypeError`, because a default is exactly how the eval path
silently ends up live. The seam half of that contract is pinned in
`test_agent_options_seam.py`; this file pins the half below it, where the mode
actually takes effect.

The rejected alternative is what most of these tests protect. Handing the eval a
read-only `allowed_tools` subset would also have stopped the refund, and would
have made the sentence *"the agent should have refused to refund here"*
unfalsifiable: an agent that cannot attempt the wrong thing cannot be measured
on refusing it. So recorded mode changes nothing the agent can SEE or CHOOSE —
same eleven tools, same system prompt, same capability envelope, same IDV gate,
same Actor seam. It changes only the outer edge, where a call would otherwise
leave this process:

    notify_fn                  escalation mail            -> recorded
    write_retrieval_metrics    tenant retrieval_metrics   -> recorded
    ProviderAdapter            money and tenant state     -> recorded

Two properties the owner attached, each with its own test below:

  * **Unmissable, never a silent success.** A recorded `issue_refund` that
    returned a cheerful confirmation would teach the agent it worked and diverge
    the rest of the turn — the eval would then be scoring an agent reasoning
    from a false premise. The recorded return is `is_error` and says NOT
    EXECUTED in the text the transcript carries.
  * **The recording is eval signal, not debris.** That the agent CHOSE to call a
    mutating skill is capability-envelope adherence, one of the more valuable
    things an eval can observe. It is retrievable in-process through
    `get_recorded_side_effects()` and durable through the unchanged AUD-01
    `tool_calls_audit` write.

Scope, stated rather than implied: recorded mode suppresses the three side
effects named above and nothing else. `escalate_to_human` still marks the
conversation in the tenant DB, `reserve_idempotency` still claims its row, and
the Actor gate still costs an LLM call. Those are deliberate. The enforcement
order IS part of what the eval measures, and short-circuiting ahead of it would
make the recorded agent read "not executed" where production reads "access
denied" — a divergence in the one direction this whole phase exists to prevent.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fake SDK bootstrap — same shape and same reason as test_retrieval_metrics.py's:
# the real claude_agent_sdk binary is not present here. The `not in sys.modules`
# guard deliberately does not clobber an already-imported real SDK, so this
# module is collection-order independent.
# ---------------------------------------------------------------------------


def _make_passthrough_tool_decorator():
    def tool_decorator(name: str, description: str, input_schema: dict):
        def wrapper(fn):
            fn._tool_name = name
            fn._tool_description = description
            fn._tool_schema = input_schema
            return fn
        return wrapper
    return tool_decorator


def _make_fake_sdk():
    fake = types.ModuleType("claude_agent_sdk")
    fake.tool = _make_passthrough_tool_decorator()
    fake.create_sdk_mcp_server = MagicMock(return_value=MagicMock(name="mcp_server"))
    fake.ClaudeAgentOptions = MagicMock(name="ClaudeAgentOptions")
    fake.ClaudeSDKClient = MagicMock(name="ClaudeSDKClient")
    fake.AssistantMessage = MagicMock(name="AssistantMessage")
    fake.ResultMessage = MagicMock(name="ResultMessage")
    fake.TextBlock = MagicMock(name="TextBlock")
    fake.ToolUseBlock = MagicMock(name="ToolUseBlock")
    fake.ToolResultBlock = MagicMock(name="ToolResultBlock")
    fake.ClaudeSDKError = type("ClaudeSDKError", (Exception,), {})
    fake.CLINotFoundError = type("CLINotFoundError", (Exception,), {})
    fake.CLIConnectionError = type("CLIConnectionError", (Exception,), {})
    fake.ProcessError = type("ProcessError", (Exception,), {})
    fake.CLIJSONDecodeError = type("CLIJSONDecodeError", (Exception,), {})
    return fake


if "claude_agent_sdk" not in sys.modules:
    sys.modules["claude_agent_sdk"] = _make_fake_sdk()

import app.services.agent_tools as agent_tools  # noqa: E402

_T = "app.services.transactional.tools"
_TOOLS_PY = Path(agent_tools.__file__).resolve().parent / "transactional" / "tools.py"


@contextmanager
def _mode(mode: str):
    """Enter `mode` with a fresh recording sink, and LEAVE NOTHING BEHIND.

    ContextVars are process-wide within one pytest session — nothing resets them
    between tests. A leaked `"recorded"` would make every later transactional
    test stop reaching its adapter mock while still passing its own assertions,
    for entirely the wrong reason. Both tokens are therefore `.reset()` in
    `finally` rather than re-set to a guessed previous value.
    """
    token_mode = agent_tools._side_effects_var.set(mode)
    token_sink = agent_tools._recorded_side_effects_var.set([])
    try:
        yield
    finally:
        agent_tools._recorded_side_effects_var.reset(token_sink)
        agent_tools._side_effects_var.reset(token_mode)


def _valid_refund_args(idempotency_key: str = "idem-refund-001") -> dict:
    return {
        "idempotency_key": idempotency_key,
        "order_id": "ORD-9001",
        "refund_amount_cents": 4500,
        "reason": "Customer reported a damaged item",
    }


def _reservation(state: str, result: dict | None = None):
    from app.services.transactional.idempotency import Reservation

    return Reservation(state=state, result=result)


def _refund_adapter() -> MagicMock:
    from app.services.transactional.schemas import IssueRefundOutput

    adapter = MagicMock()
    adapter.issue_refund = AsyncMock(
        return_value=IssueRefundOutput(
            refund_id="RFND-stub",
            status="refunded",
            message="Refund of R45.00 issued [STUB]",
        )
    )
    return adapter


@contextmanager
def _dispatcher(*, adapter, audit, release, get_adapter):
    """Steps 1-5 all wired to PASS, so the only question left is step 6.

    Recorded mode's whole claim is that steps 1-5 are unchanged, so none of them
    is short-circuited here: capability access granted, reservation won, rate
    checks clear, Actor approves. What differs between the live and recorded
    assertions below is solely whether `get_adapter_for_skill` is reached.
    """
    patches = (
        patch(f"{_T}.check_capability_access",
              AsyncMock(return_value=({"enabled": True, "skill": "issue_refund"}, None))),
        patch(f"{_T}.reserve_idempotency", AsyncMock(return_value=_reservation("reserved"))),
        patch(f"{_T}.mark_reservation_in_flight", AsyncMock(return_value=None)),
        patch(f"{_T}.apply_rate_and_constraint_checks", AsyncMock(return_value=None)),
        patch(f"{_T}.finalize_idempotency", AsyncMock(return_value=None)),
        patch(f"{_T}.release_idempotency", release),
        patch(f"{_T}.compute_args_hash", MagicMock(return_value="fakehash")),
        patch(f"{_T}.call_actor_gate", AsyncMock(return_value=("approve", "within envelope"))),
        patch(f"{_T}.write_audit_row", audit),
        patch(f"{_T}.get_adapter_for_skill", get_adapter),
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield adapter


def _run_refund(args: dict | None = None):
    from app.services.transactional.tools import issue_refund_tool

    handler = getattr(issue_refund_tool, "handler", issue_refund_tool)
    return asyncio.run(handler(args if args is not None else _valid_refund_args()))


def _set_dispatcher_identity() -> None:
    """The ContextVars the dispatcher reads for identity, before asyncio.run().

    Set directly rather than through `build_tool_server`, matching
    test_transactional_tools.py — building a real MCP server is not what these
    tests are about, and `test_build_tool_server_publishes_the_mode_and_a_fresh_sink`
    below is what pins the production wiring of the same two variables.
    """
    agent_tools._agent_id_var.set("agent-recorded-0001")
    agent_tools._conversation_id_var.set("conv-recorded-0001")
    agent_tools._conn_str_var.set("postgresql://tenant-recorded")


# ===========================================================================
# build_tool_server — the production wiring of the mode
# ===========================================================================


def test_build_tool_server_publishes_the_mode_and_a_fresh_sink():
    """The seam's `side_effects` argument has to actually reach the tools.

    Everything else in this file sets the ContextVar by hand, which proves the
    tools honour it and proves nothing about how it gets there. This is the one
    test that drives the real factory, so a seam that accepted the parameter and
    then dropped it on the floor goes red here and nowhere else.

    The sink reset matters as much as the mode: `build_tool_server` runs once per
    turn, and a sink carried over from the previous turn would report another
    conversation's refund attempt as this eval scenario's.
    """
    with _mode("live"):
        agent_tools._recorded_side_effects_var.get().append({"kind": "stale", "detail": {}})
        agent_tools.build_tool_server(
            conn_str="postgresql://tenant",
            agent_id="agent-btsr",
            agent_name="Recorded Mode Agent",
            strategy=agent_tools.RetrievalStrategy.model_validate({}),
            conversation_id="conv-btsr",
            notify_fn=lambda reason, context: None,
            tenant_id="tenant-btsr",
            side_effects="recorded",
        )
        assert agent_tools.current_side_effect_mode() == "recorded"
        assert agent_tools.get_recorded_side_effects() == [], (
            "build_tool_server did not install a FRESH recording sink. The "
            "previous turn's suppressed side effects are still in it, so the "
            "eval would attribute one scenario's refund attempt to another."
        )


def test_build_tool_server_defaults_to_live():
    """Every pre-existing caller keeps the behaviour it had.

    `red_team.py` and `red_team_probe.py` build tool servers to probe the REAL
    dispatcher — `value_bound_evasion` and `identity_bypass` read genuine
    verdict tags off it, which is the best evaluation reasoning in the codebase
    per the measurement audit. Defaulting them to recorded would quietly turn
    those two live vectors into theatre. The mandatory-no-default rule belongs
    on the seam, one layer up, where the eval path is chosen.
    """
    with _mode("recorded"):
        agent_tools.build_tool_server(
            conn_str="postgresql://tenant",
            agent_id="agent-default",
            agent_name="Default Mode Agent",
            strategy=agent_tools.RetrievalStrategy.model_validate({}),
            conversation_id="conv-default",
            notify_fn=lambda reason, context: None,
            tenant_id="tenant-default",
        )
        assert agent_tools.current_side_effect_mode() == "live"


def test_build_tool_server_rejects_a_mode_it_does_not_implement():
    """A typo must not read as "not recorded, therefore live".

    `side_effects="dry_run"` is the plausible mistake — it is what the parameter
    would be called anywhere else — and a plain string comparison against
    `"recorded"` would treat it as live and move real money on the eval path.
    """
    with pytest.raises(ValueError, match="side_effects"):
        agent_tools.build_tool_server(
            conn_str="postgresql://tenant",
            agent_id="agent-bad-mode",
            agent_name="Bad Mode Agent",
            strategy=agent_tools.RetrievalStrategy.model_validate({}),
            conversation_id="conv-bad-mode",
            notify_fn=lambda reason, context: None,
            tenant_id="tenant-bad-mode",
            side_effects="dry_run",
        )


# ===========================================================================
# The transactional dispatcher — the half that can move money
# ===========================================================================


def test_the_refund_fixture_actually_reaches_the_dispatcher():
    """Every assert-not-called test in this file rests on this one.

    `issue_refund_tool` validates its args against `IssueRefundInput` and
    returns early on a ValidationError — before the capability check, before the
    reservation, and long before the adapter. So an out-of-date fixture makes
    every `assert_not_called` below pass for a reason that has nothing to do
    with recorded mode, and the file reports a money guard it does not have.
    That is not hypothetical: the first draft of this file said `amount_cents`
    where the schema says `refund_amount_cents`, and the money guard went green
    while the dispatcher was never entered at all. It was caught only by the
    live-mode partner test.

    Pinning the fixture against the schema directly means the day a field is
    renamed, THIS fails by name rather than four others failing silently.
    """
    from app.services.transactional.schemas import IssueRefundInput

    validated = IssueRefundInput(**_valid_refund_args())
    assert validated.refund_amount_cents == 4500


def test_recorded_mode_never_reaches_the_provider_adapter():
    """THE MONEY GUARD. This is the whole reason P2 could not start.

    `get_adapter_for_skill` is the function that fetches and decrypts the
    tenant's provider credential and returns a live Stripe/Shopify/Woo/Calendly
    adapter. Reaching it at all is the failure — not the adapter method call
    after it — because the credential fetch is itself a tenant-DB read of
    secrets that an eval has no business performing.

    The return value is asserted too, not out of thoroughness but because
    `assert_not_called` alone is satisfied by the dispatcher never running. See
    `test_the_refund_fixture_actually_reaches_the_dispatcher`.
    """
    _set_dispatcher_identity()
    get_adapter = AsyncMock(return_value=_refund_adapter())
    adapter = _refund_adapter()

    with _mode("recorded"), _dispatcher(
        adapter=adapter, audit=AsyncMock(), release=AsyncMock(), get_adapter=get_adapter
    ):
        result = _run_refund()

    get_adapter.assert_not_called()
    adapter.issue_refund.assert_not_called()
    assert "NOT EXECUTED" in result["content"][0]["text"], (
        "the adapter was not called AND the recorded branch did not run, so "
        "this guard proved nothing — the dispatcher was stopped somewhere "
        f"earlier. Got: {result!r}"
    )


def test_live_mode_still_reaches_the_provider_adapter():
    """The anti-tautology partner of the test above.

    A guard that is green because the adapter is never called in EITHER mode
    proves nothing — it would stay green if the recorded branch were deleted and
    something unrelated (a bad fixture, a raised ValidationError) were stopping
    the dispatcher earlier. This drives the identical fixture through the
    identical patches and asserts the adapter IS reached, so the pair of tests
    isolates the mode as the only difference.
    """
    _set_dispatcher_identity()
    adapter = _refund_adapter()
    get_adapter = AsyncMock(return_value=adapter)

    with _mode("live"), _dispatcher(
        adapter=adapter, audit=AsyncMock(), release=AsyncMock(), get_adapter=get_adapter
    ):
        result = _run_refund()

    get_adapter.assert_called_once()
    adapter.issue_refund.assert_called_once()
    assert "is_error" not in result


def test_the_recorded_refund_is_returned_as_an_unmissable_failure():
    """The owner's first attached requirement, in the transcript the eval reads.

    A recorded `issue_refund` that returned "Refund of R45.00 issued" would
    teach the agent the money moved. Everything the agent says for the rest of
    the turn is then reasoning from a false premise, and the eval scores a
    conversation that could not have happened in production. So the return is an
    error, and its text says so in words a human grading the transcript cannot
    mistake for success.
    """
    _set_dispatcher_identity()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
    ):
        result = _run_refund()

    assert result.get("is_error") is True, (
        "the recorded return is not an error. The agent reads a non-error tool "
        "result as 'it worked', which is precisely the silent success the owner "
        f"ruled out. Got: {result!r}"
    )
    text = result["content"][0]["text"]
    assert "NOT EXECUTED" in text, (
        "the recorded tool result does not say NOT EXECUTED. The transcript is "
        "what a human grades and what the next eval reads; it has to show this "
        f"call for what it was. Got: {text!r}"
    )
    assert "issue_refund" in text, (
        "the recorded tool result does not name the skill that was suppressed, "
        f"so a transcript with several tool calls is ambiguous. Got: {text!r}"
    )
    # None of the adapter's OUTPUT may appear. Checking for cheerful WORDS was
    # tried and rejected: the honest text contains "no money moved", so a naive
    # keyword ban flags the very sentence that makes it honest. What must be
    # absent is the adapter's own artefacts — an identifier the agent could
    # quote to a customer as proof, and the confirmation message itself.
    for artefact in ("RFND-", "Refund of R45.00", "[STUB]"):
        assert artefact not in text, (
            f"the recorded tool result carries the adapter's output ({artefact!r}), "
            f"which is impossible unless the adapter ran. Got: {text!r}"
        )
    assert "completed" not in text.split("Do not tell")[0], (
        "the recorded tool result claims completion before it disclaims it. "
        f"Got: {text!r}"
    )


def test_the_recorded_refund_attempt_is_retrievable():
    """The owner's second attached requirement: recording, not discarding.

    "The agent decided to issue a refund here" is capability-envelope adherence
    — the measurement audit's confusion matrix has an entire cell for it (FP,
    money moves wrongly, critical). An eval that suppressed the call and then
    forgot it had happened would throw away the most valuable observation of the
    scenario and score the turn only on the prose that followed.
    """
    _set_dispatcher_identity()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
    ):
        _run_refund()
        recorded = agent_tools.get_recorded_side_effects()

    assert len(recorded) == 1, (
        f"expected exactly one recorded side effect, got {recorded!r}"
    )
    entry = recorded[0]
    assert entry["kind"] == "transactional.adapter"
    detail = entry["detail"]
    assert detail["skill"] == "issue_refund"
    assert detail["arguments"]["refund_amount_cents"] == 4500, (
        "the recorded attempt does not carry the arguments the agent chose. "
        "Whether it tried to refund R45 or R45 000 is the difference between a "
        f"scenario passing and failing. Got: {detail!r}"
    )
    assert detail["actor_decision"] == "approve", (
        "the recorded attempt does not carry the Actor gate's verdict. Without "
        "it an eval cannot tell 'the agent tried and the gate stopped it' from "
        f"'the agent tried and the gate waved it through'. Got: {detail!r}"
    )


def test_recorded_mode_still_writes_its_audit_row():
    """AUD-01 symmetry survives the new branch, and P2 gets a durable copy.

    Every non-replay entry into a mutating tool writes exactly one
    `tool_calls_audit` row — capability denial, rate denial, actor block,
    adapter error and success all do. A recorded execution that wrote none would
    be the first hole in that contract, and would also drop the recording as
    soon as the worker process ended. `tool_calls_audit` is the control-DB
    decision log the measurement audit names as the ready-made supervised set
    for the Actor gate; a recorded row belongs in it, marked as recorded.
    """
    _set_dispatcher_identity()
    audit = AsyncMock()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=audit, release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
    ):
        _run_refund()

    audit.assert_called_once()
    kwargs = audit.call_args.kwargs
    assert kwargs["skill"] == "issue_refund"
    assert kwargs["result"] is None, (
        "the recorded audit row carries a result. No provider was called, so "
        "there is no result; writing one would make the row indistinguishable "
        "from a real execution when the Actor set is later labelled."
    )
    assert kwargs["error"] == "side_effects.recorded:not_executed", (
        "the recorded audit row is not marked as recorded "
        f"(error={kwargs['error']!r}). Unmarked, eval rows and production rows "
        "sit in the same table telling the same story, and the labelled set for "
        "the Actor gate is silently contaminated with actions that never ran."
    )
    assert kwargs["actor_decision"] == "approve"


def test_recorded_mode_releases_the_idempotency_reservation():
    """A suppressed call must not strand the key it claimed.

    `reserve_idempotency` ran and won (step 3). Every other path that then
    declines to execute — rate denial, actor block, require_human — releases the
    reservation so a later real attempt can re-enter. A recorded execution that
    kept it would leave the eval's idempotency keys claimed by an action that
    never happened, and `reserve_idempotency` would answer "unknown" to the next
    caller: fail-closed, but for a reason that is a lie.
    """
    _set_dispatcher_identity()
    release = AsyncMock()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=release,
        get_adapter=AsyncMock(return_value=_refund_adapter()),
    ):
        _run_refund()

    release.assert_called_once()


def test_the_shared_adapter_helper_stays_free_of_the_mode():
    """Where the branch lives is a design decision, so it is pinned.

    `_execute_adapter_and_audit` has two callers: the live-turn dispatcher and
    `confirmation_resolution.execute_approved_confirmation`, the human-approval
    resolver. The resolver runs hours later, in another task, driven by an
    administrator's click — it holds no per-turn context, and
    `test_confirmation_resolution.py::test_resolver_reads_no_dispatcher_contextvar`
    already forbids it from reading any. Putting the recorded-mode check inside
    the SHARED helper would smuggle exactly that ambient state into the resolver
    through the back door, where an approved refund's fate would depend on a
    ContextVar nobody in that call stack set.

    So the check lives in `_execute_transactional_tool` — the live-turn
    dispatcher, the only caller that has a turn — and this test says so in a way
    that fails if someone later moves it for tidiness.
    """
    tree = ast.parse(_TOOLS_PY.read_text(encoding="utf-8"))
    helper = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_execute_adapter_and_audit"
    )
    source = ast.unparse(helper)
    for symbol in ("_side_effects_var", "current_side_effect_mode", "recorded"):
        assert symbol not in source, (
            f"_execute_adapter_and_audit references {symbol!r}. It is shared "
            "with the human-approval resolver, which has no per-turn context "
            "and must not acquire one; the recorded-mode branch belongs in "
            "_execute_transactional_tool, its live-turn caller."
        )
    dispatcher = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_execute_transactional_tool"
    )
    assert "_side_effects_var" in ast.unparse(dispatcher), (
        "_execute_transactional_tool no longer reads the side-effect mode, so "
        "nothing stands between an eval scenario and a real refund."
    )
