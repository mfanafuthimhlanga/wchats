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
    from app.domain.transactional_schemas import IssueRefundOutput

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
def _dispatcher(*, adapter, audit, release, get_adapter, **overrides):
    """Steps 1-5 all wired to PASS by default, so the only question left is step 6.

    Recorded mode's whole claim is that steps 1-5 are unchanged, so none of them
    is short-circuited here: capability access granted, reservation won, rate
    checks clear, Actor approves. What differs between the live and recorded
    assertions below is solely whether `get_adapter_for_skill` is reached.

    `**overrides` replaces any of those defaults by name. It exists because the
    all-pass wiring demonstrates the APPROVE path and nothing else, and the two
    outcomes it excluded — `require_human` and `replay` — turned out to be the
    two that returned before the recorded branch and reached real, durable
    effects anyway. A fixture that can only express one outcome is a fixture
    that hides the others.
    """
    defaults: dict = {
        "check_capability_access": AsyncMock(return_value=({"enabled": True}, None)),
        "reserve_idempotency": AsyncMock(return_value=_reservation("reserved")),
        "mark_reservation_in_flight": AsyncMock(return_value=None),
        "apply_rate_and_constraint_checks": AsyncMock(return_value=None),
        "finalize_idempotency": AsyncMock(return_value=None),
        "release_idempotency": release,
        "compute_args_hash": MagicMock(return_value="fakehash"),
        "call_actor_gate": AsyncMock(return_value=("approve", "within envelope")),
        "write_audit_row": audit,
        "get_adapter_for_skill": get_adapter,
    }
    defaults.update(overrides)
    with ExitStack() as stack:
        for name, replacement in defaults.items():
            stack.enter_context(patch(f"{_T}.{name}", replacement))
        yield adapter


def _run_refund(args: dict | None = None):
    from app.services.transactional.tools import issue_refund_tool

    handler = getattr(issue_refund_tool, "handler", issue_refund_tool)
    return asyncio.run(handler(args if args is not None else _valid_refund_args()))


# ---------------------------------------------------------------------------
# All six mutating skills. issue_refund is the one the money guard was written
# for; the other five have the same dispatcher, the same adapter and the same
# capacity to move money or tenant state, and were demonstrated nowhere.
# ---------------------------------------------------------------------------

MUTATING_SKILL_ARGS: dict[str, dict] = {
    "place_order": {
        "idempotency_key": "idem-po-001",
        "product_id": "SKU-1",
        "quantity": 2,
        "customer_email": "c@example.com",
        "shipping_address": "1 Main Rd, Johannesburg",
        "amount_cents": 12000,
    },
    "cancel_order": {
        "idempotency_key": "idem-co-001",
        "order_id": "ORD-9001",
        "reason": "Customer changed their mind",
    },
    "issue_refund": {
        "idempotency_key": "idem-ir-001",
        "order_id": "ORD-9001",
        "refund_amount_cents": 4500,
        "reason": "Customer reported a damaged item",
    },
    "update_subscription": {
        "idempotency_key": "idem-us-001",
        "subscription_id": "SUB-1",
        "new_plan": "pro",
        "effective_date": "2026-09-01",
    },
    "book_slot": {
        "idempotency_key": "idem-bs-001",
        "service_type": "consultation",
        "preferred_date": "2026-09-01",
        "preferred_time": "09:30",
        "customer_name": "Thandi Mokoena",
    },
    "update_customer_record": {
        "idempotency_key": "idem-ucr-001",
        "field_name": "email",
        "new_value": "new@example.com",
    },
}


def _handler_for(skill: str):
    import app.services.transactional.tools as tools_mod

    tool_obj = getattr(tools_mod, f"{skill}_tool")
    return getattr(tool_obj, "handler", tool_obj)


def _run_skill(skill: str, args: dict | None = None):
    handler = _handler_for(skill)
    return asyncio.run(handler(dict(args if args is not None else MUTATING_SKILL_ARGS[skill])))


def _generic_adapter(skill: str) -> MagicMock:
    """An adapter whose method returns something `.model_dump()`-able.

    Deliberately NOT the real Output model for each of the six: the point of the
    parametrized sweep is the dispatcher branch, and a per-skill Output fixture
    would be five more chances to write a fixture that never reaches the
    dispatcher — the failure `test_the_refund_fixture_actually_reaches_the_dispatcher`
    exists to catch. The refund tests keep the real model, where the artefact
    assertions need it.
    """
    result_obj = MagicMock()
    result_obj.model_dump = MagicMock(return_value={"message": f"{skill} done [STUB]"})
    adapter = MagicMock()
    setattr(adapter, skill, AsyncMock(return_value=result_obj))
    return adapter


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
    from app.domain.transactional_schemas import IssueRefundInput

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
    # The property the owner attached is about the FIRST thing the agent reads,
    # not about the paragraph as a whole. The previous form of this assertion
    # (`"completed" not in text.split("Do not tell")[0]`) was satisfied by the
    # construction of the message it was written against: the only occurrence of
    # "completed" sat in the trailing disclaimer, so the split always removed it
    # and the assertion could not fail for the reason it named. Asserting on the
    # first sentence can.
    first_sentence = text.split(".")[0]
    assert first_sentence.startswith("NOT EXECUTED"), (
        "the recorded tool result does not OPEN with NOT EXECUTED. The first "
        "sentence is what the agent conditions the rest of its turn on; a "
        "confirmation followed by a disclaimer is still a confirmation. "
        f"Got first sentence: {first_sentence!r}"
    )


def test_the_recorded_result_does_not_disclose_the_evaluation_frame():
    """The eval must measure the agent production serves, not one that knows.

    The first version of this text told the agent, in its own context, "this
    agent is running in evaluation mode (side_effects='recorded')" and then
    instructed it not to tell the customer the action completed. From that token
    on, every sentence the agent produced was produced by an evaluation-AWARE
    agent — and Faithfulness and AnswerRelevancy score exactly those sentences.
    A conversation that structurally could not occur in production is the drift
    class approach (b) and the seam exist to close, so scoring one is the same
    defect as D1 wearing a different hat.

    The marker did not disappear; it moved to the two places whose readers
    actually need it. `tool_calls_audit.error` carries it for the human grader
    and the labelled Actor set, and `get_recorded_side_effects()` carries the
    full attempt for P2 — both asserted elsewhere in this file. What the MODEL
    sees is a failed tool call, which is a thing production produces.
    """
    _set_dispatcher_identity()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
    ):
        text = _run_refund()["content"][0]["text"]

    lowered = text.lower()
    for tell in ("evaluation", "eval mode", "side_effects", "recorded", "measurement", "test mode"):
        assert tell not in lowered, (
            f"the recorded tool result puts {tell!r} in the model's context. "
            "Every token the agent produces afterwards is conditioned on "
            "knowing it is under evaluation, and those are the tokens the eval "
            f"scores. Got: {text!r}"
        )
    assert "do not tell the customer" not in lowered, (
        "the recorded tool result instructs the agent about what to tell the "
        "customer. Production emits no such instruction, so the turn the eval "
        f"scores is not a turn production could produce. Got: {text!r}"
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


# ===========================================================================
# The paths that return BEFORE step 5.5 — where the branch could not see them
#
# The money guard above sits at step 5.5, after the Actor approves. Two arms
# return earlier and both reached durable, real effects: the step-5
# `require_human` verdict wrote a `pending_confirmations` row the owner's
# approval queue dispatches into a live ProviderAdapter, and the step-3
# idempotency `replay` returned a stored REAL provider result. Neither was
# demonstrated by the all-pass fixture, because the all-pass fixture cannot
# express them.
# ===========================================================================


def _db_session():
    """(get_sync_db replacement, session mock). `.first()` defaults to None.

    A bare MagicMock would make the require_human branch's pre-insert dedup
    lookup return a truthy row, which the branch reads as "a duplicate already
    exists" and skips the insert — so a test asserting "no row was written"
    would pass without recorded mode doing anything at all.
    """
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield session

    return _ctx, session


def test_recorded_require_human_queues_no_pending_confirmation():
    """THE SECOND MONEY GUARD, and the one step 5.5 structurally could not be.

    A `require_human` verdict writes a `pending_confirmations` row carrying the
    agent's full chosen arguments. That row is not inert. It is not filtered by
    `_is_confirm_action_shaped` (it holds `idempotency_key`, never
    `action_reference`), it appears in `GET /agents/{id}/pending-confirmations`
    with nothing marking it as an eval's, and approving it dispatches
    `resolve_confirmation_task` -> `execute_approved_confirmation` ->
    `_execute_adapter_and_audit` -> `get_adapter_for_skill` -> a real
    Stripe/Shopify/Woo/Calendly call.

    So a nightly eval scenario that provokes a large refund silently queued a
    real refund for the owner to approve — the fast path to the adapter was
    closed and the slow one was not.
    """
    _set_dispatcher_identity()
    db_ctx, session = _db_session()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        call_actor_gate=AsyncMock(return_value=("require_human", "above the owner's ceiling")),
        get_sync_db=db_ctx,
    ):
        result = _run_refund()

    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert result.get("is_error") is True, (
        "the recorded require_human return is not an error. Production returns "
        "a cheerful non-error naming a confirmation ID; recorded mode created "
        "no confirmation, so returning that text would tell the agent an "
        f"approval is coming that nobody will ever see. Got: {result!r}"
    )
    assert "NOT EXECUTED" in result["content"][0]["text"]


def test_live_require_human_still_queues_the_pending_confirmation():
    """The anti-tautology partner. Without it, deleting the require_human arm
    entirely would pass the guard above.

    The owner's approval queue is a shipped product surface: an agent that hits
    the Actor's require_human verdict on a real customer turn MUST leave a row
    for a human to act on, or the customer is told an approval is coming and
    none is.
    """
    _set_dispatcher_identity()
    db_ctx, session = _db_session()

    with _mode("live"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        call_actor_gate=AsyncMock(return_value=("require_human", "above the owner's ceiling")),
        get_sync_db=db_ctx,
    ):
        result = _run_refund()

    session.add.assert_called_once()
    session.commit.assert_called_once()
    assert "is_error" not in result


def test_recorded_require_human_records_the_attempt_and_marks_the_audit_row():
    """The verdict is the eval signal; not writing the row must not lose it.

    "The agent tried to refund R45 and the Actor gate escalated it" is a cell of
    the measurement audit's confusion matrix. The pending row was never what
    carried that observation — the Actor verdict is — so suppressing the row
    costs nothing provided the verdict still lands in both places P2 reads.
    """
    _set_dispatcher_identity()
    audit = AsyncMock()
    db_ctx, _session = _db_session()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=audit, release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        call_actor_gate=AsyncMock(return_value=("require_human", "above the owner's ceiling")),
        get_sync_db=db_ctx,
    ):
        _run_refund()
        recorded = agent_tools.get_recorded_side_effects()

    assert len(recorded) == 1, f"expected one recorded attempt, got {recorded!r}"
    detail = recorded[0]["detail"]
    assert detail["reason"] == "actor_require_human"
    assert detail["actor_decision"] == "require_human"
    assert detail["arguments"]["refund_amount_cents"] == 4500

    from app.services.transactional.tools import RECORDED_NOT_EXECUTED

    error = audit.call_args.kwargs["error"]
    assert error.startswith(RECORDED_NOT_EXECUTED), (
        "the recorded require_human audit row is unmarked, so it is "
        "byte-identical to a production require_human row. Every consumer that "
        "labels the Actor gate from tool_calls_audit would then train on "
        f"decisions that never happened. Got: {error!r}"
    )
    assert "actor_require_human" in error, (
        "the marked audit row lost the reason it was written. The marker says "
        "'an eval did this'; the reason says WHICH cell of the confusion matrix "
        f"it belongs in, and both are needed. Got: {error!r}"
    )


def test_recorded_mode_never_hands_the_agent_a_stored_replay_result():
    """A recorded turn may not be handed a genuine success.

    `idempotency_key` is MODEL-supplied on every mutating Input model, and models
    produce deterministic keys ("refund-ORD-9001"). An eval scenario mined from a
    production conversation can therefore hit the exact key a real completed call
    used, and step 3's replay arm returns that call's stored REAL result — the
    agent reads "Refund of R45.00 issued" and the whole rest of the turn reasons
    from money having moved. That is the silent success the owner ruled out,
    arriving through a door in FRONT of step 5.5 rather than behind it.
    """
    _set_dispatcher_identity()
    stored = {"content": [{"type": "text", "text": "Refund of R45.00 issued [STUB] RFND-real"}]}

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        reserve_idempotency=AsyncMock(return_value=_reservation("replay", stored)),
    ):
        result = _run_refund()
        recorded = agent_tools.get_recorded_side_effects()

    text = result["content"][0]["text"]
    for artefact in ("RFND-real", "Refund of R45.00", "[STUB]"):
        assert artefact not in text, (
            f"the recorded replay handed the agent the stored REAL provider "
            f"result ({artefact!r}). Got: {text!r}"
        )
    assert result.get("is_error") is True
    assert "NOT EXECUTED" in text
    assert [e["detail"]["reason"] for e in recorded] == ["idempotency.replay"], (
        f"the declined replay was not recorded for P2. Got: {recorded!r}"
    )


def test_live_mode_still_returns_the_stored_replay_result():
    """The anti-tautology partner: WR-01 replay semantics are unchanged for
    customers. A retry of a completed refund must still return the original
    result rather than refunding twice."""
    _set_dispatcher_identity()
    stored = {"content": [{"type": "text", "text": "Refund of R45.00 issued [STUB]"}]}

    with _mode("live"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        reserve_idempotency=AsyncMock(return_value=_reservation("replay", stored)),
    ):
        result = _run_refund()

    assert result is stored


def test_the_recorded_keyspace_is_separate_from_productions():
    """The eval reserves its own idempotency keys, not the tenant's.

    The collision runs both ways and only one of them is the replay above. An
    eval that reserves "refund-ORD-9001" first makes a real customer's later
    call with that key read as a replay or a stranded reservation — an outage
    caused by a measurement. A recorded execution never finalizes, so nothing is
    ever stored under a "recorded:" key and a recorded replay becomes
    unreachable rather than merely guarded.
    """
    _set_dispatcher_identity()
    reserve = AsyncMock(return_value=_reservation("reserved"))

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        reserve_idempotency=reserve,
    ):
        _run_refund(_valid_refund_args("idem-collide"))
    recorded_key = reserve.call_args.args[2]

    reserve_live = AsyncMock(return_value=_reservation("reserved"))
    with _mode("live"), _dispatcher(
        adapter=_refund_adapter(), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        reserve_idempotency=reserve_live,
    ):
        _run_refund(_valid_refund_args("idem-collide"))
    live_key = reserve_live.call_args.args[2]

    assert live_key == "idem-collide", (
        "the LIVE idempotency key changed. Customers' replay protection is "
        f"keyed on the value the model supplied. Got: {live_key!r}"
    )
    assert recorded_key != live_key, (
        "recorded mode reserves the same idempotency key production does, so "
        "an eval and a real customer share one keyspace in the control DB. "
        f"Got: {recorded_key!r} for both."
    )
    assert recorded_key.startswith("recorded:")


# ===========================================================================
# The refused column — every non-executing outcome, recorded and marked
# ===========================================================================

_DECLINE_CASES = [
    (
        "capability_denial",
        {"check_capability_access": AsyncMock(return_value=({"enabled": False}, "disabled"))},
        "capability.denial:disabled",
        True,
    ),
    (
        "rate_denial",
        {"apply_rate_and_constraint_checks": AsyncMock(return_value="rate_limit")},
        "capability.denial:rate_limit",
        True,
    ),
    (
        "actor_block",
        {"call_actor_gate": AsyncMock(return_value=("block", "policy violation"))},
        "actor_block",
        True,
    ),
    (
        "args_mismatch",
        {"reserve_idempotency": AsyncMock(return_value=_reservation("args_mismatch"))},
        "idempotency.args_mismatch",
        True,
    ),
    (
        "stranded_reservation",
        {"reserve_idempotency": AsyncMock(return_value=_reservation("unknown"))},
        "idempotency.stranded_reservation",
        True,
    ),
    (
        "in_progress",
        {"reserve_idempotency": AsyncMock(return_value=_reservation("in_progress"))},
        "idempotency.in_progress",
        False,   # AUD-01 writes no audit row for a concurrent duplicate, in either mode
    ),
]


@pytest.mark.parametrize(
    "name,overrides,reason,writes_audit",
    _DECLINE_CASES,
    ids=[c[0] for c in _DECLINE_CASES],
)
def test_every_declined_outcome_is_recorded_and_marked(name, overrides, reason, writes_audit):
    """The *refused* column of the confusion matrix, which recorded nothing.

    `record_suppressed_side_effect` had exactly three call sites and not one of
    them was on a denial, block or IDV-refusal path — so
    `get_recorded_side_effects()` systematically omitted every attempt the
    envelope stopped, and P2 could not tell "the agent never tried" from "the
    agent tried and was refused". Those two are scored oppositely: one is
    correct restraint, the other is a capability-envelope save.

    Falling back to `tool_calls_audit` did not rescue it either. Only step 5.5's
    row carried the recorded marker, so a recorded `actor_block` row was
    byte-identical to a production one — the exact contamination
    RECORDED_NOT_EXECUTED's own comment says it exists to prevent.
    """
    from app.services.transactional.tools import RECORDED_NOT_EXECUTED

    _set_dispatcher_identity()
    audit = AsyncMock()

    with _mode("recorded"), _dispatcher(
        adapter=_refund_adapter(), audit=audit, release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        **overrides,
    ):
        _run_refund()
        recorded = agent_tools.get_recorded_side_effects()

    assert [e["detail"]["reason"] for e in recorded] == [reason], (
        f"the {name} outcome recorded {recorded!r} rather than one entry "
        f"reasoned {reason!r}. An eval reading this turn back sees a turn in "
        "which the agent attempted nothing."
    )
    assert recorded[0]["kind"] == "transactional.declined", (
        "a declined attempt is recorded under the same kind as a suppressed "
        "execution, so P2 cannot separate the two columns of the matrix. "
        f"Got: {recorded[0]['kind']!r}"
    )
    assert recorded[0]["detail"]["arguments"]["refund_amount_cents"] == 4500

    if writes_audit:
        error = audit.call_args.kwargs["error"]
        assert error == f"{RECORDED_NOT_EXECUTED}|{reason}", (
            f"the recorded {name} audit row reads {error!r}. Unmarked it is "
            "byte-identical to the production row for the same outcome, and "
            "the labelled Actor set silently absorbs decisions that never ran."
        )
    else:
        audit.assert_not_called()


@pytest.mark.parametrize(
    "name,overrides,reason,writes_audit",
    _DECLINE_CASES,
    ids=[c[0] for c in _DECLINE_CASES],
)
def test_live_mode_leaves_every_declined_audit_row_unmarked(name, overrides, reason, writes_audit):
    """The anti-tautology partner of the sweep above, and a regression pin.

    A marker applied unconditionally would corrupt the production decision log
    in the opposite direction: every real `actor_block` would read as an eval's
    and the Actor set would have nothing left in it. The live rows must be byte
    for byte what they were before recorded mode existed.
    """
    _set_dispatcher_identity()
    audit = AsyncMock()

    with _mode("live"), _dispatcher(
        adapter=_refund_adapter(), audit=audit, release=AsyncMock(),
        get_adapter=AsyncMock(return_value=_refund_adapter()),
        **overrides,
    ):
        _run_refund()
        recorded = agent_tools.get_recorded_side_effects()

    assert recorded == [], (
        f"live mode recorded {recorded!r}. The sink is the eval's; a customer's "
        "turn filling it means the mode is not what gates the recording."
    )
    if writes_audit:
        assert audit.call_args.kwargs["error"] == reason


# ===========================================================================
# All six mutating skills, not just the one the guard was written for
# ===========================================================================


def test_every_mutating_skill_in_the_registry_has_a_fixture():
    """The sweep below is only a sweep if it covers the registry.

    Parametrizing over a hand-written dict silently stops covering a skill the
    day a seventh mutating one is added — the tests keep passing and the new
    skill is the one cell nobody looked at, which is the shape of the defect
    this whole review round is about.
    """
    from app.services.transactional.registry import TOOL_REGISTRY

    registry_mutating = {
        name for name, entry in TOOL_REGISTRY.items() if getattr(entry, "mutating", False)
    }
    assert registry_mutating == set(MUTATING_SKILL_ARGS), (
        "MUTATING_SKILL_ARGS no longer matches the registry's mutating skills.\n"
        f"  registry: {sorted(registry_mutating)}\n"
        f"  fixtures: {sorted(MUTATING_SKILL_ARGS)}\n"
        "A skill with no fixture is a skill with no recorded-mode money guard."
    )


@pytest.mark.parametrize("skill", sorted(MUTATING_SKILL_ARGS))
def test_every_skill_fixture_actually_validates(skill):
    """Each fixture is pinned against its schema, by name.

    `test_the_refund_fixture_actually_reaches_the_dispatcher` exists because the
    first draft of the refund args said `amount_cents` where the schema says
    `refund_amount_cents`, the tool returned a ValidationError before the
    dispatcher was entered, and the money guard went green having proved
    nothing. Writing five more fixtures is five more chances to do it — and
    `place_order` did, omitting `amount_cents`, caught here.
    """
    from app.domain.transactional_schemas import (
        BookSlotInput,
        CancelOrderInput,
        IssueRefundInput,
        PlaceOrderInput,
        UpdateCustomerRecordInput,
        UpdateSubscriptionInput,
    )

    models = {
        "place_order": PlaceOrderInput,
        "cancel_order": CancelOrderInput,
        "issue_refund": IssueRefundInput,
        "update_subscription": UpdateSubscriptionInput,
        "book_slot": BookSlotInput,
        "update_customer_record": UpdateCustomerRecordInput,
    }
    validated = models[skill](**MUTATING_SKILL_ARGS[skill])
    assert validated.idempotency_key == MUTATING_SKILL_ARGS[skill]["idempotency_key"]


@pytest.mark.parametrize("skill", sorted(MUTATING_SKILL_ARGS))
def test_recorded_mode_reaches_no_adapter_for_any_mutating_skill(skill):
    """The guard was demonstrated at ONE point of a two-dimensional space.

    Six skills x seven dispatcher outcomes, and `issue_refund` at the all-pass
    outcome was the only cell with a test. `book_slot` has a different schema
    and no `refund_amount_cents`; `update_customer_record` writes tenant PII.
    A future edit that gave any of them its own early return would move
    money-adjacent state with every guard in this file green.
    """
    _set_dispatcher_identity()
    get_adapter = AsyncMock(return_value=_generic_adapter(skill))

    with _mode("recorded"), _dispatcher(
        adapter=_generic_adapter(skill), audit=AsyncMock(), release=AsyncMock(),
        get_adapter=get_adapter,
    ):
        result = _run_skill(skill)
        recorded = agent_tools.get_recorded_side_effects()

    get_adapter.assert_not_called()
    assert "NOT EXECUTED" in result["content"][0]["text"], (
        f"{skill}: the adapter was not reached AND the recorded branch did not "
        f"run, so this guard proved nothing about {skill}. Got: {result!r}"
    )
    assert [e["detail"]["skill"] for e in recorded] == [skill]


@pytest.mark.parametrize("skill", sorted(MUTATING_SKILL_ARGS))
def test_live_mode_reaches_the_adapter_for_any_mutating_skill(skill):
    """The per-skill anti-tautology partner.

    Without it, a fixture whose args fail `model_validate` returns is_error
    before the dispatcher is entered and the guard above passes for a reason
    that has nothing to do with recorded mode. That is not hypothetical — it is
    exactly how the first draft of the refund fixture went green.
    """
    _set_dispatcher_identity()
    adapter = _generic_adapter(skill)
    get_adapter = AsyncMock(return_value=adapter)

    with _mode("live"), _dispatcher(
        adapter=adapter, audit=AsyncMock(), release=AsyncMock(), get_adapter=get_adapter,
    ):
        result = _run_skill(skill)

    get_adapter.assert_called_once()
    getattr(adapter, skill).assert_called_once()
    assert "is_error" not in result, f"{skill}: {result!r}"


# ===========================================================================
# confirm_action — in allowed_tools, not routed through the dispatcher
# ===========================================================================


def _run_confirm_action(skill: str = "issue_refund", reference: str = "idem-refund-001"):
    from app.services.transactional.tools import confirm_action_tool

    handler = getattr(confirm_action_tool, "handler", confirm_action_tool)
    return asyncio.run(handler({"skill": skill, "action_reference": reference}))


def test_recorded_confirm_action_writes_no_row_and_records_the_attempt():
    """`confirm_action` never touches `_execute_transactional_tool`, so step 5.5
    never saw it — and it is granted in BOTH modes on purpose, because an eval
    agent that cannot request approval cannot be scored on choosing to.

    Left ungated it wrote a durable row into the owner's triage queue on every
    eval scenario in which the agent decided to ask, nightly. Less dangerous
    than the require_human row — `_is_confirm_action_shaped` DOES filter this
    shape, so approving one never reaches an adapter — but it is queue pollution
    the owner would have to triage, and nothing recorded that the agent made the
    choice, so it was lost eval signal as well.
    """
    _set_dispatcher_identity()
    db_ctx, session = _db_session()

    with _mode("recorded"), ExitStack() as stack:
        stack.enter_context(patch(
            f"{_T}.check_capability_access",
            AsyncMock(return_value=({"enabled": True}, None)),
        ))
        stack.enter_context(patch(f"{_T}.get_sync_db", db_ctx))
        result = _run_confirm_action()
        recorded = agent_tools.get_recorded_side_effects()

    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert result.get("is_error") is True
    assert len(recorded) == 1, f"expected the attempt to be recorded, got {recorded!r}"
    assert recorded[0]["kind"] == "transactional.confirm_action"
    assert recorded[0]["detail"]["skill"] == "issue_refund"
    assert recorded[0]["detail"]["action_reference"] == "idem-refund-001"


def test_live_confirm_action_still_writes_its_row():
    """The anti-tautology partner: the owner's approval queue is a real product
    surface, and a customer turn that requests approval must still leave a row
    in it."""
    _set_dispatcher_identity()
    db_ctx, session = _db_session()

    with _mode("live"), ExitStack() as stack:
        stack.enter_context(patch(
            f"{_T}.check_capability_access",
            AsyncMock(return_value=({"enabled": True}, None)),
        ))
        stack.enter_context(patch(f"{_T}.get_sync_db", db_ctx))
        result = _run_confirm_action()

    session.add.assert_called_once()
    session.commit.assert_called_once()
    assert "is_error" not in result


# ===========================================================================
# escalate_to_human — the mail is one edge of it, the tenant UPDATE is the other
# ===========================================================================


def _run_escalation():
    handler = getattr(
        agent_tools.escalate_to_human_tool, "handler", agent_tools.escalate_to_human_tool
    )
    return asyncio.run(handler({"reason": "Customer frustrated", "context": "Order delayed"}))


@contextmanager
def _escalation_context(rowcount: int = 1):
    """Tool-level identity plus a psycopg2 whose UPDATE matched `rowcount` rows."""
    notify_fn = MagicMock()
    agent_tools._conversation_id_var.set("conv-escalate-0001")
    agent_tools._agent_id_var.set("agent-escalate-0001")
    agent_tools._conn_str_var.set("postgresql://TENANT-REAL-DB")
    agent_tools._notify_fn_var.set(notify_fn)

    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.rowcount = rowcount
    conn.cursor.return_value = cursor

    connect = MagicMock(return_value=conn)
    with patch("psycopg2.connect", connect):
        yield connect, conn, notify_fn


def test_recorded_mode_does_not_mark_the_tenant_conversation_escalated():
    """Recorded mode suppressed the escalation MAIL and nothing else.

    `escalate_to_human_tool` still opened a psycopg2 connection to the TENANT's
    real database and committed `UPDATE conversations ... SET metadata
    escalated=true` before notify_fn was ever consulted. P2's scenarios are
    mined from real conversations, so the conversation_id the seam is handed is
    exactly the kind that exists — and an eval scenario that escalates would
    mark a real customer's conversation escalated, changing what the owner's
    inbox and every escalation dashboard show.

    The guard that was supposed to cover this called the seam's notify_fn
    closure directly and never entered the tool, so it could not see the UPDATE
    at all. This one drives the tool.
    """
    with _mode("recorded"), _escalation_context() as (connect, conn, notify_fn):
        result = _run_escalation()
        recorded = agent_tools.get_recorded_side_effects()

    connect.assert_not_called()
    conn.commit.assert_not_called()
    assert [e["kind"] for e in recorded] == ["conversation.escalated_marker"], (
        f"the suppressed conversations UPDATE was not recorded. Got: {recorded!r}"
    )
    assert recorded[0]["detail"]["conversation_id"] == "conv-escalate-0001"
    # The customer-facing half is unchanged: escalation TEXT is not a side effect.
    assert notify_fn.call_count == 1, (
        "the recorded escalation notification did not fire. With the UPDATE "
        "suppressed there is no rowcount to be zero, so the recording must no "
        "longer depend on it — that dependency is what made the escalation "
        "guard green against an edge P2 would never reach."
    )
    assert "flagged this conversation" in result["content"][0]["text"]


def test_live_mode_still_marks_the_tenant_conversation_escalated():
    """The anti-tautology partner. A customer's escalation must still be durable:
    the owner's inbox, the escalation dashboard and the `escalated` flag on the
    conversations list all read that column."""
    with _mode("live"), _escalation_context() as (connect, conn, notify_fn):
        _run_escalation()
        recorded = agent_tools.get_recorded_side_effects()

    connect.assert_called_once()
    conn.cursor.return_value.execute.assert_called_once()
    conn.commit.assert_called_once()
    assert recorded == []
    assert notify_fn.call_count == 1


def test_the_already_escalated_return_carries_content():
    """Every other tool in agent_tools returns a "content" list; this one
    returned a bare `{"already_escalated": True}`.

    The SDK hands the agent whatever text it finds in `content`, so on this path
    the agent's next turn reasoned over nothing at all. It is reachable in
    production on any second escalation of a conversation — and BACKLOG 2.7
    showed it becoming the NORMAL outcome on the eval path, where the UPDATE
    matches zero rows. Recorded mode no longer runs the UPDATE, so the eval
    cannot reach it; production still can.
    """
    with _mode("live"), _escalation_context(rowcount=0) as (_connect, _conn, notify_fn):
        result = _run_escalation()

    assert result.get("already_escalated") is True
    assert "content" in result, (
        f"the already-escalated return has no content key: {result!r}"
    )
    assert isinstance(result["content"], list) and result["content"][0]["type"] == "text"
    assert "already flagged" in result["content"][0]["text"]
    assert notify_fn.call_count == 0, (
        "the duplicate escalation fired notify_fn again — F3's idempotency "
        "guard is what stops the owner being paged twice for one conversation."
    )


# ===========================================================================
# Shared state the eval must not consume: the Redis rate counter
# ===========================================================================


def test_the_rate_counter_is_namespaced_by_mode():
    """Step 4 runs live in recorded mode BY DESIGN — and INCRs a shared counter.

    Keyed only on (agent, skill, window), an overnight eval with six
    refund-shaped scenarios exhausts an envelope that allows five refunds an
    hour, and the next REAL customer refund inside that window comes back
    "Request denied by rate or constraint check (reason: rate_limit)". Silent
    from the eval's side; indistinguishable from an ordinary envelope denial
    from the customer's side.

    Namespacing rather than suppressing: the eval still measures the ceiling, on
    its own counter. Suppressing the INCR would make "the agent kept refunding
    past its limit" unfalsifiable — the same mistake as handing the eval a
    read-only tool subset.
    """
    import app.services.transactional.enforcement as enforcement

    keys: list[str] = []

    class _Pipe:
        def incr(self, key):
            keys.append(key)

        def expire(self, key, ttl):
            pass

        def execute(self):
            return [1, True]

    client = MagicMock()
    client.pipeline = MagicMock(return_value=_Pipe())
    snapshot = {"enabled": True, "rate_limit": "5/hour", "constraints": {}}

    with patch.object(enforcement, "_get_redis", MagicMock(return_value=client)):
        with _mode("live"):
            asyncio.run(enforcement.apply_rate_and_constraint_checks(
                "agent-rate-001", "issue_refund", snapshot, MagicMock(spec=[]),
            ))
        with _mode("recorded"):
            asyncio.run(enforcement.apply_rate_and_constraint_checks(
                "agent-rate-001", "issue_refund", snapshot, MagicMock(spec=[]),
            ))

    assert len(keys) == 2, f"expected one INCR per mode, got {keys!r}"
    live_key, recorded_key = keys
    assert live_key == recorded_key.replace("recorded:", ""), (
        f"the two keys differ by more than the mode namespace: {keys!r}"
    )
    assert live_key != recorded_key, (
        "the eval INCRs the tenant's PRODUCTION per-skill rate counter, so an "
        "overnight run consumes budget a real customer needs the next morning. "
        f"Both modes used {live_key!r}."
    )
    assert recorded_key.startswith("ratelimit:recorded:")
