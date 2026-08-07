"""The drift guard: ClaudeAgentOptions is constructed in exactly one place.

Why this file exists
--------------------
`.dev/plans/260807-d1-agent-invocation.md` settles D1 on approach (b): the eval
task will invoke the customer agent through the SAME options constructor
`run_agent_turn` uses, so that what the eval measures is what production serves.
The named cost of (b) is drift — the eval path and the chat path quietly
diverging until the eval measures something adjacent to the product, which the
audit calls this repo's recurring defect. The plan's answer is structural rather
than advisory: ONE constructor, and a test that fails if `run_agent_turn` builds
options by any other route.

That test is this file. It is the load-bearing artifact of P1, not hygiene. A
comment reading "always use the seam" is not a guard; this is.

Two kinds of assertion, deliberately paired:

  * STATIC — an AST read of `agent.py`'s own source. An inlined
    `ClaudeAgentOptions(...)` inside `run_agent_turn` fails here even when it
    happens to produce byte-identical kwargs today, because "identical today"
    is precisely the state that drifts tomorrow.
  * DYNAMIC — a sentinel driven through the live task. The options object
    `_run_sdk_turn` actually receives must be the object the seam returned.
    Source that merely mentions the seam and then passes something else fails
    here.

Neither half subsumes the other. The static half cannot see an aliased import
(`from claude_agent_sdk import ClaudeAgentOptions as _Opts`) — the dynamic half
catches that, because an aliased constructor still does not yield the sentinel.
The dynamic half cannot see a second construction whose result is discarded or
used on a branch this test does not drive — the static half catches that.
"""

from __future__ import annotations

import ast
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AGENT_PY = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "worker"
    / "tasks"
    / "runtime"
    / "agent.py"
)

#: The one callable both `run_agent_turn` and (from P2) the eval task go through.
SEAM = "build_agent_options"

#: Everything the seam must own, because each one determines how the agent
#: behaves rather than how the turn is recorded: the options object itself, the
#: MCP tool server (which is where the capability envelope is enforced), and the
#: system prompt. If any of these is assembled by the caller instead, the eval
#: can be handed a different agent than production serves without this suite
#: noticing.
BEHAVIOUR_DETERMINING_CALLS = (
    "ClaudeAgentOptions",
    "build_tool_server",
    "build_system_prompt",
)


# ---------------------------------------------------------------------------
# Static half — read agent.py's source, import nothing
# ---------------------------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(_AGENT_PY.read_text(encoding="utf-8"))


def _top_level_functions() -> dict[str, ast.AST]:
    """Every module-scope `def` / `async def` in agent.py, by name.

    agent.py has no nested function definitions (only lambdas), so attributing a
    call to the module-scope function it appears in is unambiguous. If that ever
    stops being true, `test_agent_py_has_no_nested_function_definitions` below
    goes red rather than this helper silently mis-attributing.
    """
    return {
        node.name: node
        for node in _module_tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(node: ast.AST) -> Counter:
    """Count callee names appearing anywhere inside `node`.

    `Foo(...)` counts as "Foo"; `mod.Foo(...)` counts as "Foo" too, so moving the
    constructor behind a module alias does not evade the count.
    """
    counts: Counter = Counter()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            counts[func.id] += 1
        elif isinstance(func, ast.Attribute):
            counts[func.attr] += 1
    return counts


def test_the_seam_exists_and_is_module_scope():
    """P2 imports this name. A rename that is not also made here is a red test,
    not a silently re-inlined constructor."""
    functions = _top_level_functions()
    assert SEAM in functions, (
        f"{SEAM} is not defined at module scope in {_AGENT_PY.name}. "
        "The eval task imports it by this name; without it there is no seam and "
        "D1's fix measures an agent nobody serves."
    )


def test_agent_py_has_no_nested_function_definitions():
    """Guards the attribution `_top_level_functions` relies on.

    A `def` nested inside `run_agent_turn` would let a constructor live in
    agent.py, execute on the chat path, and belong to no module-scope function
    this file inspects. That is a hole in the static half, so it is closed here
    rather than assumed shut.
    """
    nested: list[str] = []
    for name, node in _top_level_functions().items():
        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested.append(f"{name} -> {sub.name}")
    assert nested == [], (
        "agent.py grew a nested function definition "
        f"({nested}). The static guards in this file attribute calls to the "
        "module-scope function containing them; a nested def can hide a second "
        "ClaudeAgentOptions construction from that attribution. Either lift it "
        "to module scope or teach _called_names to walk it."
    )


@pytest.mark.parametrize("callee", BEHAVIOUR_DETERMINING_CALLS)
def test_run_agent_turn_constructs_nothing_behaviour_determining_itself(callee):
    """THE DRIFT GUARD. `run_agent_turn` must reach the agent's behaviour only
    through the seam."""
    functions = _top_level_functions()
    assert "run_agent_turn" in functions, "run_agent_turn is no longer module-scope"
    found = _called_names(functions["run_agent_turn"])[callee]
    assert found == 0, (
        f"run_agent_turn calls {callee}(...) directly ({found} call site(s)). "
        f"Every input that determines how the agent behaves must be assembled "
        f"inside {SEAM}, because the eval task calls only that. A constructor "
        f"here is a second agent definition that no eval will ever measure — "
        f"the exact drift the plan's approach (b) is structurally closing."
    )


@pytest.mark.parametrize("callee", BEHAVIOUR_DETERMINING_CALLS)
def test_the_seam_is_the_only_place_that_constructs_them(callee):
    """Exactly one call site each, module-wide, and it is inside the seam.

    Zero-in-`run_agent_turn` alone would be satisfied by moving the constructor
    into some third helper that the eval does not call. This is the assertion
    that makes the seam singular rather than merely elsewhere.
    """
    module_total = _called_names(_module_tree())[callee]
    in_seam = _called_names(_top_level_functions()[SEAM])[callee]
    assert in_seam == 1, (
        f"{SEAM} contains {in_seam} call(s) to {callee}(...); expected exactly 1."
    )
    assert module_total == 1, (
        f"agent.py contains {module_total} call sites for {callee}(...); expected "
        f"exactly 1, inside {SEAM}. A second one is a second agent definition."
    )


def test_run_agent_turn_never_mutates_the_options_it_was_given():
    """Identity is necessary but not sufficient.

    `options.max_turns = 3` placed after the seam call keeps every other
    assertion in this file green — no new constructor for the AST guards to
    find, and the sentinel test still sees the seam's own object, because it IS
    the seam's own object. It would nonetheless serve a customer a turn ceiling
    the eval never scored against. That is the one drift the sentinel cannot
    see, so it is closed here instead of assumed away.
    """
    fn = _top_level_functions()["run_agent_turn"]
    offenders: list[str] = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
            targets = [sub.target]
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "options"
            ):
                offenders.append(f"options.{target.attr} (agent.py line {sub.lineno})")
    assert offenders == [], (
        f"run_agent_turn rebinds attributes on the options object: {offenders}. "
        "The options the customer is served must be exactly what the seam "
        "returned, because that is the only object the eval can reproduce. "
        "Move the change into build_agent_options, where both callers get it."
    )


def test_run_agent_turn_calls_the_seam_exactly_once():
    """One turn, one options object. Two calls would mean two tool servers were
    built and the ContextVars of one silently overwrote the other's."""
    calls = _called_names(_top_level_functions()["run_agent_turn"])[SEAM]
    assert calls == 1, (
        f"run_agent_turn calls {SEAM}(...) {calls} times; expected exactly 1."
    )


# ---------------------------------------------------------------------------
# Dynamic half — drive the real task, prove the seam's object is the one used
# ---------------------------------------------------------------------------

_CANNED_TURN_RESULT = {
    "response_text": (
        "Returns are accepted within 14 days.\n\n"
        "CITATIONS:\n"
        "- Document: FAQ.pdf | Section: 1\n"
    ),
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-seam-001",
    "total_cost_usd": 0.001,
    "num_turns": 2,
    "stop_reason": "end_turn",
}


class _SeamSentinel:
    """Not a ClaudeAgentOptions and not a MagicMock.

    A MagicMock would compare equal to nothing in particular and would let an
    `is` check pass for the wrong reasons under `spec=`; a bare object() carries
    no name into the failure message. This carries both.
    """

    def __repr__(self) -> str:  # pragma: no cover - only read on failure
        return "<options object returned by the seam>"


def _make_agent() -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.tenant_id = uuid.uuid4()
    agent.name = "Seam Test Agent"
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"
    return agent


def _make_job() -> MagicMock:
    job = MagicMock()
    job.status = "running"
    job.finished_at = None
    return job


def _db_ctx(db: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_the_options_the_sdk_turn_receives_are_the_seam_s_own_object():
    """The seam is on the live chat path, not merely present in the file.

    `build_agent_options` is patched to hand back a sentinel that no other code
    path can produce. If `run_agent_turn` builds its own options — by an aliased
    import, a copy, a mutation, or a second constructor the AST guards above
    cannot see — the object reaching `_run_sdk_turn` is not this sentinel and
    this test goes red.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    local_conv_id = "00000000-0000-0000-0000-0000000000aa"
    sentinel = _SeamSentinel()

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, _make_job()]

    captured: dict = {}

    async def fake_sdk_turn(**kwargs):
        captured.update(kwargs)
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam",
        ),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_options",
            return_value=sentinel,
        ) as mock_seam,
        patch("app.worker.tasks.runtime.agent._run_sdk_turn", side_effect=fake_sdk_turn),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    mock_seam.assert_called_once()
    assert "options" in captured, (
        "_run_sdk_turn was never handed an `options` argument — run_agent_turn "
        f"did not drive the turn through {SEAM}. captured={sorted(captured)}"
    )
    assert captured["options"] is sentinel, (
        "the options object _run_sdk_turn received is not the one the seam "
        f"returned (got {captured['options']!r}). run_agent_turn is constructing "
        "or substituting options on its own, so the eval and the chat path can "
        "no longer be the same agent."
    )


def test_the_seam_receives_the_turn_s_own_inputs():
    """A seam called with the wrong arguments is drift with extra steps.

    The four checked here are the ones that vary per turn and would each be a
    silent wrong-agent if mis-threaded: the agent row, the tenant connection
    string, this conversation's id, and the SDK session to resume. `resume` in
    particular is the one that turns a follow-up into a fresh conversation.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    existing_conv_id = str(uuid.uuid4())
    stored_session = "stored-sdk-session-for-seam"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, _make_job()]

    async def fake_sdk_turn(**kwargs):
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={
                "id": existing_conv_id,
                "metadata": {"sdk_session_id": stored_session},
            },
        ),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam-2",
        ),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_options",
            return_value=_SeamSentinel(),
        ) as mock_seam,
        patch("app.worker.tasks.runtime.agent._run_sdk_turn", side_effect=fake_sdk_turn),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="And what about exchanges?",
            conversation_id=existing_conv_id,
        )

    mock_seam.assert_called_once()
    kwargs = mock_seam.call_args.kwargs
    assert kwargs.get("agent") is agent, (
        f"the seam was handed {kwargs.get('agent')!r}, not the agent row this "
        "turn resolved"
    )
    assert kwargs.get("conn_str") == "postgresql://tenant"
    assert str(kwargs.get("conversation_id")) == existing_conv_id
    assert kwargs.get("resume") == stored_session, (
        "resume= must carry the conversation's stored sdk_session_id into the "
        f"seam; got {kwargs.get('resume')!r}. Without it every follow-up turn "
        "starts a new SDK session and the agent loses the conversation."
    )
    assert kwargs.get("job_id") == job_id
