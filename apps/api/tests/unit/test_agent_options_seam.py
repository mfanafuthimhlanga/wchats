"""The drift guard: an `AgentTurn` is assembled in exactly one place.

Why this file exists
--------------------
`.dev/plans/260807-d1-agent-invocation.md` settles D1 on approach (b): the eval
task invokes the customer agent through the SAME constructor `run_agent_turn`
uses, so that what the eval measures is what production serves. The named cost of
(b) is drift, the eval path and the chat path quietly diverging until the eval
measures something adjacent to the product, which the audit calls this repo's
recurring defect. The plan's answer is structural rather than advisory: ONE
constructor, and a test that fails if `run_agent_turn` builds a turn by any other
route.

That test is this file. It is the load-bearing artifact of P1, carried across the
cutover ADR 0008 describes. A comment reading "always use the seam" is not a
guard; this is.

WHAT MOVED, AND WHAT DID NOT. ADR 0008 took the turn off the Agent SDK harness.
`build_agent_options` became `build_agent_turn`, `ClaudeAgentOptions` became the
`AgentTurn` dataclass, and both now live in `app/services/agent_loop.py` beside
the loop they feed. The property this file pins did not move: one assembly, two
callers, no second definition of the agent anywhere. Ticket #49 then took the
last three callers of the SDK's own `ClaudeAgentOptions` off it, so
`MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS` is empty and
`test_only_allowlisted_modules_construct_claude_agent_options` now pins that
absence. Read that test's docstring for which gate is the primary one.

Three kinds of assertion, deliberately layered:

  * STATIC, a syntax-tree read of `agent_loop.py`, `agent.py` and `eval.py`. An
    inlined `AgentTurn(...)` inside `run_agent_turn` fails here even when it
    happens to produce byte-identical fields today, because "identical today" is
    precisely the state that drifts tomorrow. Import aliases are resolved before
    counting, so `from … import bind_tool_context as btc` does not hide a second
    binding of the tool context, and dynamic dispatch (`getattr(mod, "X")(…)`) is
    banned outright on the turn path rather than left as a hole.
  * IDENTITY, a sentinel driven through the live task. The turn object
    `run_agent_loop` actually receives must be the object the seam returned.
    Source that merely mentions the seam and then passes something else fails
    here.
  * VALUE, where the turn is driven with the REAL seam and every field of the
    `AgentTurn` the loop receives is compared against one built independently,
    after the turn, from a pristine copy of the same agent row. This is the half
    that does not care how the drift is spelled: an alias
    (`t = turn; t.max_model_calls = 3`), a module-level helper `_tighten(turn)`,
    a mutation of the `agent` row before the seam reads it, or a trimmed tool
    list all change a field value and all go red, without any guard having to
    recognise the spelling.

Why all three. An early version of this file had only STATIC + IDENTITY, and a
tier-2 probe walked straight through it: seven realistic drift edits left all
twelve guards green, because the in-place-mutation guard matched exactly one
spelling and the sentinel sees only the object it handed out, never the object's
contents. That is BACKLOG 3.3's defect class, a guard demonstrated only inside
the complement of its own blind spot. The VALUE half exists because the guard
must pin the property, not the spelling; the STATIC half stays because it catches
constructions on branches no test drives.

Known scope limits, stated rather than implied:

  * `notify_fn` is a closure built inside the seam, so two builds never produce
    equal values and it is excluded from the value comparison. Which of the two
    closures the mode selected is asserted directly instead, in
    `test_recorded_mode_records_the_escalation_instead_of_sending_it`.
  * The seam's turn carries a LIVE tool server bound to the tenant connection
    string, and `side_effects` decides what that means: "live" writes
    `retrieval_metrics`, sends escalation mail and dispatches the real
    transactional adapters; "recorded" records those three instead. BACKLOG 2.5,
    settled 2026-08-07. The tests here pin the seam's half of it — mandatory
    parameter, identical capability surface in both modes, mode threaded into the
    tool server. The tool layer's half is
    `tests/unit/test_recorded_side_effects.py`, and the loop's half is
    `tests/unit/test_agent_loop.py`.
"""

from __future__ import annotations

import ast
import copy
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.agent_loop_doubles import canned_turn_result

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_LOOP_PY = _APP_ROOT / "services" / "agent_loop.py"
_AGENT_PY = _APP_ROOT / "worker" / "tasks" / "runtime" / "agent.py"
_EVAL_PY = _APP_ROOT / "worker" / "tasks" / "runtime" / "eval.py"

#: The one callable both `run_agent_turn` and the eval task go through.
SEAM = "build_agent_turn"

#: Everything the seam must own, because each one determines how the agent
#: behaves rather than how the turn is recorded: the turn object itself, the tool
#: server (which is where the capability envelope is enforced), and the system
#: prompt. If any of these is assembled by the caller instead, the eval can be
#: handed a different agent than production serves without this suite noticing.
BEHAVIOUR_DETERMINING_CALLS = (
    "AgentTurn",
    "bind_tool_context",
    "build_system_prompt",
)

#: Modules permitted to construct an `AgentTurn` anywhere under `app/`.
#:
#: One name, and the list is short on purpose. `agent_loop.py` is the seam. The
#: names that matter are the two absent: `app/worker/tasks/runtime/agent.py` and
#: `app/worker/tasks/runtime/eval.py`. The whole value of approach (b) evaporates
#: the moment either builds its own turn. The eval would then score an agent no
#: customer is served, and the chat path would serve one no eval measures.
MODULES_ALLOWED_TO_CONSTRUCT_TURNS = {"app/services/agent_loop.py"}

#: Modules permitted to construct the SDK's own `ClaudeAgentOptions` anywhere
#: under `app/`. Empty, and #49 is what emptied it.
#:
#: ADR 0008 took the customer agent off that type. Three modules went on
#: constructing it afterwards: `deployment_service.py` for the deployment
#: Orchestrator, `red_team_service.py` for the red-team Attacker, and
#: `red_team_probe.py`, whose `_build_transactional_probe_fn` assembled the
#: VICTIM turn by hand with its own `_PROBE_MODEL` and its own `_ALLOWED_TOOLS`,
#: so RTX-01's confused-deputy findings described an agent with a different model
#: and a different tool list from the one production serves. All three now run on
#: `app.services.tool_loop`, and the victim turn goes through the seam.
#:
#: An empty allowlist asserts an ABSENCE, so read
#: `test_only_allowlisted_modules_construct_claude_agent_options` for which gate
#: is primary and what this one adds to it.
MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS: set[str] = set()

#: The exact capability surface the seam grants, in registration order. A value,
#: not a count: the audit's transactional table shows six of these move money or
#: state, so a silent addition is a capability grant and a silent removal is a
#: regression the eval would score as an agent that "chose not to".
EXPECTED_TOOL_NAMES = [
    "retrieve",
    "lookup_structured",
    "escalate_to_human",
    "clarify",
    "place_order",
    "cancel_order",
    "issue_refund",
    "update_subscription",
    "book_slot",
    "update_customer_record",
    "confirm_action",
]

#: The six of those that move money or tenant state through a real
#: ProviderAdapter. Named separately because the seam grants them to EVERY
#: caller, and the eval task is a caller: one eval scenario in which the agent
#: decides to refund would execute a refund. BACKLOG 2.5 is the settled decision.
MUTATING_SKILLS = {
    "place_order",
    "cancel_order",
    "issue_refund",
    "update_subscription",
    "book_slot",
    "update_customer_record",
}

#: Callables that may legitimately be handed the seam's turn object. Anything
#: else receiving it is a function that can mutate it out of sight of every
#: static guard here, the `_tighten_turn(turn)` shape.
#:
#: `record_turn_calls` is the second one. It takes the turn's `ModelCall` rows to
#: the ledger after the turn, off the event loop, and it is in `agent_loop.py`
#: beside the seam rather than in the task module.
_TURN_SINKS = {"run_agent_turn": {"run_agent_loop", "record_turn_calls"}}

#: Dynamic dispatch defeats every syntax-tree guard in this file by construction:
#: `getattr(agent_loop, "AgentTurn")(...)` is a Call whose func is itself a Call,
#: so no callee name exists to count. Rather than teach the counter to
#: constant-fold, the turn path is simply not allowed to use it.
_DYNAMIC_DISPATCH_CALLS = {
    "getattr",
    "eval",
    "exec",
    "globals",
    "vars",
    "locals",
    "import_module",
    "__import__",
}


# ---------------------------------------------------------------------------
# Static half. Read the three modules' source, import nothing
# ---------------------------------------------------------------------------


def _tree(path: Path) -> ast.Module:
    """One module's syntax tree. The only reader of app source in this file."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _aliases_in(tree: ast.Module) -> dict[str, str]:
    """Map each local import alias back to the name it actually binds.

    `from app.services.agent_tools import bind_tool_context as btc` makes `btc`
    a second spelling of the function that publishes the per-turn ContextVars.
    Counting the syntactic callee alone would score `btc(...)` as an unrelated
    function and let a second binding exist while every count stayed at 1.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in node.names:
                if name.asname:
                    aliases[name.asname] = name.name.split(".")[-1]
    return aliases


def _top_level_functions(path: Path) -> dict[str, ast.AST]:
    """Every module-scope `def` / `async def` in one file, by name.

    Neither module has nested function definitions (only lambdas), so attributing
    a call to the module-scope function it appears in is unambiguous. If that
    ever stops being true, `test_neither_turn_module_has_nested_function_
    definitions` below goes red rather than this helper silently mis-attributing.
    """
    return {
        node.name: node
        for node in _tree(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _callee_name(call: ast.Call, aliases: dict[str, str]) -> str | None:
    """The name being called, with import aliases resolved.

    `Foo(...)` -> "Foo"; `mod.Foo(...)` -> "Foo"; `btc(...)` -> "bind_tool_context"
    when `btc` is an alias. A call whose callee is itself an expression (a call,
    a subscript, a lambda) has no name — that returns None and is banned
    separately by `test_the_turn_path_uses_no_dynamic_dispatch`.
    """
    func = call.func
    if isinstance(func, ast.Name):
        raw = func.id
    elif isinstance(func, ast.Attribute):
        raw = func.attr
    else:
        return None
    return aliases.get(raw, raw)


def _called_names(node: ast.AST, aliases: dict[str, str]) -> Counter:
    """Count callee names appearing anywhere inside `node`, aliases resolved."""
    counts: Counter = Counter()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        name = _callee_name(sub, aliases)
        if name is not None:
            counts[name] += 1
    return counts


def _calls_in(path: Path, fn_name: str) -> Counter:
    """Callee counts inside one module-scope function of one file."""
    return _called_names(_top_level_functions(path)[fn_name], _aliases_in(_tree(path)))


def _root_name(node: ast.AST) -> str | None:
    """The base identifier of an attribute/subscript chain, or None.

    `a.b.c` -> "a"; `a["k"].b` -> "a"; `f().b` -> None.
    """
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _assign_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


def _turn_roots(fn: ast.AST, aliases: dict[str, str]) -> set[str]:
    """Every local name in `fn` that refers to the seam's turn object.

    Seeded two ways, from a parameter literally named `turn` (that is how
    `run_agent_loop` receives it) and from any name assigned the seam's return
    value, then closed transitively over `x = <known name>` so `t = turn` does not
    launder the object out of the guard's reach. That alias spelling is the exact
    edit that walked through an early version of this file with every test green.
    """
    roots: set[str] = set()

    args = getattr(fn, "args", None)
    if args is not None:
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg == "turn":
                roots.add(arg.arg)

    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call):
            if _callee_name(sub.value, aliases) == SEAM:
                roots |= {t.id for t in sub.targets if isinstance(t, ast.Name)}

    changed = True
    while changed:
        changed = False
        for sub in ast.walk(fn):
            value = getattr(sub, "value", None)
            if not isinstance(sub, (ast.Assign, ast.AnnAssign)):
                continue
            if not isinstance(value, ast.Name) or value.id not in roots:
                continue
            for target in _assign_targets(sub):
                if isinstance(target, ast.Name) and target.id not in roots:
                    roots.add(target.id)
                    changed = True
    return roots


def _turn_offences(fn: ast.AST, roots: set[str], sinks: set[str], aliases: dict[str, str]) -> list[str]:
    """Every way `fn` could change the turn object after the seam built it."""
    offences: list[str] = []
    for sub in ast.walk(fn):
        for target in _assign_targets(sub):
            if isinstance(target, (ast.Attribute, ast.Subscript)) and _root_name(target) in roots:
                offences.append(
                    f"line {sub.lineno}: rebinds {ast.unparse(target)}"
                )
        if not isinstance(sub, ast.Call):
            continue
        if isinstance(sub.func, (ast.Attribute, ast.Subscript)) and _root_name(sub.func) in roots:
            offences.append(
                f"line {sub.lineno}: calls {ast.unparse(sub.func)}(...) on the turn object"
            )
        handed_over = [a for a in sub.args if isinstance(a, ast.Name) and a.id in roots]
        handed_over += [
            kw.value
            for kw in sub.keywords
            if isinstance(kw.value, ast.Name) and kw.value.id in roots
        ]
        if handed_over and _callee_name(sub, aliases) not in sinks:
            offences.append(
                f"line {sub.lineno}: hands the turn object to "
                f"{_callee_name(sub, aliases)}(...)"
            )
    return offences


def test_the_seam_exists_and_is_module_scope():
    """Both callers import this name. A rename that is not also made here is a red
    test, not a silently re-inlined constructor."""
    functions = _top_level_functions(_LOOP_PY)
    assert SEAM in functions, (
        f"{SEAM} is not defined at module scope in {_LOOP_PY.name}. "
        "The chat task and the eval task both import it by this name; without it "
        "there is no seam and D1's fix measures an agent nobody serves."
    )


@pytest.mark.parametrize("path", [_LOOP_PY, _AGENT_PY], ids=["agent_loop", "agent"])
def test_neither_turn_module_has_nested_function_definitions(path):
    """Guards the attribution `_top_level_functions` relies on.

    A `def` nested inside `run_agent_turn` or inside the seam would let a
    constructor live in one of these files, execute on the chat path, and belong
    to no module-scope function this suite inspects. That is a hole in the static
    half, so it is closed here rather than assumed shut. Both files, because the
    seam and its caller now live in different modules and either one can hide a
    second assembly.
    """
    nested: list[str] = []
    for name, node in _top_level_functions(path).items():
        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested.append(f"{name} -> {sub.name}")
    assert nested == [], (
        f"{path.name} grew a nested function definition ({nested}). The static "
        "guards in this file attribute calls to the module-scope function "
        "containing them; a nested def can hide a second AgentTurn construction "
        "from that attribution. Either lift it to module scope or teach "
        "_called_names to walk it."
    )


@pytest.mark.parametrize("callee", BEHAVIOUR_DETERMINING_CALLS)
def test_run_agent_turn_constructs_nothing_behaviour_determining_itself(callee):
    """THE DRIFT GUARD. `run_agent_turn` must reach the agent's behaviour only
    through the seam."""
    functions = _top_level_functions(_AGENT_PY)
    assert "run_agent_turn" in functions, "run_agent_turn is no longer module-scope"
    found = _calls_in(_AGENT_PY, "run_agent_turn")[callee]
    assert found == 0, (
        f"run_agent_turn calls {callee}(...) directly ({found} call site(s)). "
        f"Every input that determines how the agent behaves must be assembled "
        f"inside {SEAM}, because the eval task calls only that. A constructor "
        f"here is a second agent definition that no eval will ever measure — "
        f"the exact drift the plan's approach (b) is structurally closing."
    )


@pytest.mark.parametrize("callee", BEHAVIOUR_DETERMINING_CALLS)
def test_the_seam_is_the_only_place_that_constructs_them(callee):
    """Exactly one call site each in agent_loop.py, and it is inside the seam.

    Zero-in-`run_agent_turn` alone would be satisfied by moving the constructor
    into some third helper that the eval does not call. This is the assertion
    that makes the seam singular rather than merely elsewhere. The two task
    modules are checked at zero in the same breath, because a construction in
    either of them is a second agent definition wherever it sits.
    """
    loop_total = _called_names(_tree(_LOOP_PY), _aliases_in(_tree(_LOOP_PY)))[callee]
    in_seam = _calls_in(_LOOP_PY, SEAM)[callee]
    assert in_seam == 1, (
        f"{SEAM} contains {in_seam} call(s) to {callee}(...); expected exactly 1."
    )
    assert loop_total == 1, (
        f"agent_loop.py contains {loop_total} call sites for {callee}(...); "
        f"expected exactly 1, inside {SEAM}. A second one is a second agent "
        "definition."
    )
    for path in (_AGENT_PY, _EVAL_PY):
        total = _called_names(_tree(path), _aliases_in(_tree(path)))[callee]
        assert total == 0, (
            f"{path.name} contains {total} call site(s) for {callee}(...). A task "
            f"module that assembles its own agent is the drift approach (b) "
            "exists to close."
        )


def test_import_aliases_are_resolved_before_counting():
    """Anti-tautology pin for the counter the two tests above depend on.

    `_called_names` used to key on the syntactic callee identifier, so
    `from … import bind_tool_context as btc` followed by `btc(...)` counted as
    an unrelated function named "btc" and a second binding of the tool context
    could exist with every count still reading 1. This asserts the alias
    resolution actually happens, on a synthetic module, so the property is
    observed rather than assumed from reading the helper.
    """
    tree = ast.parse(
        "from app.services.agent_tools import bind_tool_context as btc\n"
        "def f():\n"
        "    return btc(1), build_system_prompt(2)\n"
    )
    aliases = _aliases_in(tree)
    assert aliases.get("btc") == "bind_tool_context"
    counts = _called_names(tree, aliases)
    assert counts["bind_tool_context"] == 1, (
        "an aliased import of bind_tool_context was not counted against its real "
        f"name; counts={dict(counts)}"
    )
    assert counts["btc"] == 0


@pytest.mark.parametrize("fn_name", sorted(_TURN_SINKS))
def test_the_turn_path_never_mutates_or_launders_the_seam_s_turn(fn_name):
    """THE MUTATION GUARD, pinned to the object rather than to one spelling.

    Identity is necessary but not sufficient: `turn.max_model_calls = 3` after
    the seam call keeps every constructor count at 1 and still hands the sentinel
    test the seam's own object, while serving a customer a call ceiling the eval
    never scored against. An early version of this guard closed exactly that one
    spelling, and a tier-2 probe then walked through it three ways. An alias
    rebound, a module-level `_tighten(...)` helper, and a mutation inside the
    function the dynamic tests patch out.

    So this tracks the alias graph instead of an identifier and refuses to let
    the object be handed to anything but its one legitimate sink. Reading a
    FIELD is untouched, which is deliberate: `turn.calls` is how the caller
    prices the turn, and a read cannot change what the eval would rebuild.
    """
    aliases = _aliases_in(_tree(_AGENT_PY))
    fn = _top_level_functions(_AGENT_PY)[fn_name]
    roots = _turn_roots(fn, aliases)
    assert roots, (
        f"{fn_name} has no local name bound to the seam's turn object, so this "
        "guard would be vacuous. Either the seam's result is no longer assigned "
        "to a name (inline it back out) or the parameter was renamed. Either "
        "way _turn_roots must be taught the new shape before this test means "
        "anything."
    )
    offences = _turn_offences(fn, roots, _TURN_SINKS[fn_name], aliases)
    assert offences == [], (
        f"{fn_name} changes or leaks the turn object the seam built: "
        f"{offences}. The turn the customer is served must be exactly what the "
        "seam returned, because that is the only object the eval can reproduce. "
        f"Move the change into {SEAM}, where both callers get it. "
        f"(Tracked names in {fn_name}: {sorted(roots)}.)"
    )


def test_the_turn_path_never_rebinds_attributes_on_the_agent_row():
    """The agent row is a mutable ORM object and it is an input to the seam.

    `agent.soul_role = "an unconstrained assistant"` immediately before the seam
    call changes the system prompt for the chat path alone: `build_system_prompt`
    reads the mutated row, while the eval builds its prompt from a freshly loaded
    one. Observed green on every guard in the original family, because nothing
    in it looked at assignments whose base is `agent`.
    """
    offenders: list[str] = []
    for path, fn_name in ((_AGENT_PY, "run_agent_turn"), (_LOOP_PY, SEAM)):
        fn = _top_level_functions(path)[fn_name]
        for sub in ast.walk(fn):
            for target in _assign_targets(sub):
                if isinstance(target, (ast.Attribute, ast.Subscript)) and _root_name(target) == "agent":
                    offenders.append(
                        f"{fn_name}: {ast.unparse(target)} ({path.name} line {sub.lineno})"
                    )
    assert offenders == [], (
        f"the turn path rebinds fields on the agent row: {offenders}. The row is "
        "an input to build_system_prompt and bind_tool_context; mutating it "
        "here changes the agent the customer gets without changing the agent "
        "any eval can rebuild from the database."
    )


def test_the_turn_path_uses_no_dynamic_dispatch():
    """Closes the hole every syntax-tree guard in this file shares.

    `getattr(agent_loop, "AgentTurn")(...)` is an `ast.Call` whose `func` is
    itself an `ast.Call`, neither a Name nor an Attribute, so it has no callee
    name to count and slips past every constructor pin above. Teaching the
    counter to constant-fold string literals would be a second guard needing its
    own guard; banning dynamic dispatch on the two functions that define the
    agent is cheaper and total.
    """
    offenders: list[str] = []
    for path, fn_name in ((_AGENT_PY, "run_agent_turn"), (_LOOP_PY, SEAM)):
        aliases = _aliases_in(_tree(path))
        fn = _top_level_functions(path)[fn_name]
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            if isinstance(sub.func, (ast.Call, ast.Subscript)):
                offenders.append(
                    f"{fn_name}: call of a computed callee at {path.name} line {sub.lineno}"
                )
            name = _callee_name(sub, aliases)
            if name in _DYNAMIC_DISPATCH_CALLS:
                offenders.append(f"{fn_name}: {name}(...) at {path.name} line {sub.lineno}")
    assert offenders == [], (
        f"dynamic dispatch on the turn path: {offenders}. Every constructor "
        "guard in this file counts callee names from the syntax tree; a computed "
        "callee has no name to count, so this is the one construct that makes "
        "them all vacuous at once."
    )


def test_run_agent_turn_has_exactly_one_lexical_call_site_for_the_seam():
    """One *call site* in the source, a syntactic property, which is all a
    syntax-tree read can establish.

    The runtime property that actually matters (the seam runs exactly once per
    turn, so one tool server is built and one set of ContextVars is in force) is
    asserted by `mock_seam.assert_called_once()` in the dynamic tests below,
    along the first-turn and subsequent-turn paths. A single call site inside a
    loop or a retry wrapper satisfies this test and fails those; the pair is the
    claim, neither half alone.
    """
    calls = _calls_in(_AGENT_PY, "run_agent_turn")[SEAM]
    assert calls == 1, (
        f"run_agent_turn contains {calls} lexical call site(s) for {SEAM}(...); "
        "expected exactly 1."
    )


def test_the_eval_has_exactly_one_lexical_call_site_for_the_seam():
    """The eval's half of the same property, at its own one call site.

    The eval invokes the agent once per scenario through `_run_one_eval_turn`. A
    second call site there is a second tool server for one turn, and the second
    one wins the per-task ContextVars, which is how an eval scenario ends up
    served by an agent nobody assembled on purpose.
    """
    calls = _calls_in(_EVAL_PY, "_run_one_eval_turn")[SEAM]
    assert calls == 1, (
        f"_run_one_eval_turn contains {calls} lexical call site(s) for "
        f"{SEAM}(...); expected exactly 1."
    )


def test_only_allowlisted_modules_construct_agent_turns():
    """Repo-wide, because the drift the plan names is between two FILES.

    Every other static guard here parses one module at a time, which proves each
    file is self-consistent. If the eval constructs its own `AgentTurn`,
    `agent_loop.py` still contains exactly one of each name and every guard above
    stays green while the eval scores an agent no customer is served, which is the
    precise failure approach (b) was chosen to prevent.
    """
    found: dict[str, int] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = _tree(path)
        count = _called_names(tree, _aliases_in(tree))["AgentTurn"]
        if count:
            found[path.relative_to(_APP_ROOT.parent).as_posix()] = count

    assert set(found) == MODULES_ALLOWED_TO_CONSTRUCT_TURNS, (
        "the set of modules constructing AgentTurn changed.\n"
        f"  found:   {sorted(found)}\n"
        f"  allowed: {sorted(MODULES_ALLOWED_TO_CONSTRUCT_TURNS)}\n"
        "A new one is a new agent definition. The eval and the chat task must "
        f"both go through {SEAM} or they are not serving the same agent."
    )


def _construction_sites(callee: str) -> dict[str, int]:
    """Every module under `app/` that calls `callee`, and how many times."""
    found: dict[str, int] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = _tree(path)
        count = _called_names(tree, _aliases_in(tree))[callee]
        if count:
            found[path.relative_to(_APP_ROOT.parent).as_posix()] = count
    return found


def test_only_allowlisted_modules_construct_claude_agent_options():
    """The SDK type nothing under `app/` builds any more. The belt, not the braces.

    THE PRIMARY GATE IS THE IMPORT-LINTER CONTRACT, `the provider SDKs have one
    home` in `pyproject.toml`, which #49 extends by adding `claude_agent_sdk` to
    its `forbidden_modules`. That contract is stronger than anything here: it
    bans the IMPORT, so a module cannot reach the name at all, by any spelling,
    including one this file's counter has no case for. An empty allowlist alone
    would be a weak assertion, and it is not the argument.

    This test is the belt to those braces, and it stays for what the contract
    cannot say. A broken contract prints one edge, `app is not allowed to import
    claude_agent_sdk`. This prints the module and the count, and the failure
    message points at the seam, so the reader learns which file went back to the
    harness and why that is drift rather than a dependency question. The two also
    fail on different mistakes: the contract catches the import and misses a
    construction through a name already in scope, while this catches the
    construction and misses an import that builds nothing.

    Adding a module here is a deliberate act with a reviewer attached, and it now
    means arguing past the contract as well. Removing all three is what #49 did.
    """
    found = _construction_sites("ClaudeAgentOptions")

    for forbidden in (_LOOP_PY, _AGENT_PY, _EVAL_PY):
        key = forbidden.relative_to(_APP_ROOT.parent).as_posix()
        assert key not in found, (
            f"{key} constructs ClaudeAgentOptions ({found.get(key)} site(s)). The "
            f"customer turn is assembled by {SEAM} and run by the owned loop; an "
            "options object on this path is a second agent definition wearing the "
            "harness the ADR retired."
        )

    assert set(found) == MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS, (
        "the set of modules constructing ClaudeAgentOptions changed.\n"
        f"  found:   {sorted(found)}\n"
        f"  allowed: {sorted(MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS)}\n"
        "#49 took every caller off that type and onto app.services.tool_loop, so "
        "the allowed set is empty and a found one means the harness came back. "
        "Check the import-linter contract `the provider SDKs have one home` too: "
        "it bans the import outright, so this reading a construction while that "
        "contract stays KEPT means one of the two gates is no longer measuring."
    )


# ---------------------------------------------------------------------------
# Dynamic half — drive the real task
# ---------------------------------------------------------------------------

_CANNED_TURN_RESULT = canned_turn_result(
    "Returns are accepted within 14 days.\n\n"
    "CITATIONS:\n"
    "- Document: FAQ.pdf | Section: 1\n",
)

_CONN_STR = "postgresql://tenant"
_VERIFIED_TOKEN = "verified-session-token-for-seam"
_SOUL_OVERRIDE = {
    "soul_role": "the canary persona under test",
    "soul_voice": "clipped",
    "soul_do_list": ["quote the clause"],
    "soul_donot_list": ["never speculate"],
}
_PROMPT_VERSION_ID = "11111111-2222-3333-4444-555555555555"


class _SeamSentinel:
    """Not an AgentTurn and not a MagicMock.

    A MagicMock would compare equal to nothing in particular and would let an
    `is` check pass for the wrong reasons under `spec=`; a bare object() carries
    no name into the failure message. This carries both. It carries a `calls`
    list as well, because the caller prices the turn from that field and a
    sentinel without one would make every identity test a test of AttributeError.
    """

    def __init__(self) -> None:
        self.calls: list = []
        self.ledger = lambda call: None

    def __repr__(self) -> str:  # pragma: no cover - only read on failure
        return "<turn object returned by the seam>"


def _agent_spec() -> dict:
    """Declared field values for a test agent row.

    Every field the seam reads is set explicitly rather than left to MagicMock's
    auto-attributes, because the value comparison builds a SECOND agent from the
    same spec and compares the turns each produces. Auto-attributes carry
    id-based reprs and would differ between the two objects for reasons that
    have nothing to do with drift.
    """
    return {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "name": "Seam Test Agent",
        "retrieval_strategy": {},
        "neon_connection_string": b"encrypted-bytes",
        "soul_role": "a returns specialist",
        "soul_voice": "plain and unhurried",
        "soul_do_list": ["cite the policy clause"],
        "soul_donot_list": ["never invent a refund amount"],
    }


def _agent_from(spec: dict) -> MagicMock:
    agent = MagicMock()
    for field, value in spec.items():
        setattr(agent, field, value)
    return agent


def _make_agent() -> MagicMock:
    return _agent_from(_agent_spec())


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


def _tool_server_marker(**kwargs) -> str:
    """A deterministic stand-in for the tool server.

    Keyed on every input that decides what the tools can do and whose data they
    touch, so two calls with the same inputs produce equal markers and any
    divergence — a blanked `verified_session_token`, a different conn_str, a
    mutated retrieval strategy, a `side_effects` mode that reached the seam and
    then never reached the tools — produces unequal ones. `notify_fn` is
    excluded: it is a closure built inside the seam and would never compare equal
    across two calls; which of the two closures was chosen is asserted directly
    in `test_recorded_mode_records_the_escalation_instead_of_sending_it`.
    """
    return (
        "tool-server["
        f"conn_str={kwargs['conn_str']}|"
        f"agent_id={kwargs['agent_id']}|"
        f"agent_name={kwargs['agent_name']}|"
        f"tenant_id={kwargs['tenant_id']}|"
        f"conversation_id={kwargs['conversation_id']}|"
        f"strategy={kwargs['strategy']!r}|"
        f"verified_session_token={kwargs['verified_session_token']}|"
        f"job_id={kwargs['job_id']}|"
        f"side_effects={kwargs['side_effects']}]"
    )


def _system_prompt_marker(agent, soul_override=None) -> str:
    """A deterministic stand-in for the assembled system prompt.

    Reads the same soul fields `build_system_prompt` reads, so a mutation of the
    agent row before the seam runs, or a dropped `soul_override`, changes the
    value — which is the whole point of comparing values rather than identity.
    """
    return (
        "system-prompt["
        f"name={agent.name}|role={agent.soul_role}|voice={agent.soul_voice}|"
        f"do={agent.soul_do_list}|donot={agent.soul_donot_list}|"
        f"override={soul_override}]"
    )


def _client_marker(purpose, *, tenant_id, recorder, agent_id, job_id) -> str:
    """A deterministic stand-in for the factory-built provider client.

    The client carries the purpose route and the ledger context, so two builds
    that disagree about who is billed, or about which purpose the calls are
    attributed to, produce unequal markers. `recorder` is a closure and is
    excluded for the same reason `notify_fn` is.
    """
    return f"client[purpose={purpose}|tenant_id={tenant_id}|agent_id={agent_id}|job_id={job_id}]"


def _seam_collaborators():
    """Patch the seam's three outward edges with deterministic stand-ins."""
    return (
        patch("app.services.agent_loop.bind_tool_context", side_effect=_tool_server_marker),
        patch("app.services.agent_loop.build_system_prompt", side_effect=_system_prompt_marker),
        patch("app.services.agent_loop.make_async_client", side_effect=_client_marker),
    )


def test_the_turn_the_loop_receives_is_the_seam_s_own_object():
    """The seam is on the live chat path, not merely present in the file.

    `build_agent_turn` is patched to hand back a sentinel that no other code path
    can produce. If `run_agent_turn` builds its own turn by an aliased import,
    a copy, a mutation, or a second constructor the static guards above cannot
    see, then the object reaching `run_agent_loop` is not this sentinel and this test
    goes red.

    The message is asserted here too. It is the other half of what the loop is
    given, it is not part of the turn object, and prefixing it at the call site
    (`"SYSTEM OVERRIDE: " + message`) was observed to leave every other guard
    green while the chat path ran a prompt no eval would ever score.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    local_conv_id = "00000000-0000-0000-0000-0000000000aa"
    sentinel = _SeamSentinel()
    user_message = "What is the return policy?"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, _make_job()]

    captured: dict = {}

    async def fake_loop(*args, **kwargs):
        captured["message"] = args[0] if args else kwargs.get("message")
        captured.update(kwargs)
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam",
        ),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_turn",
            return_value=sentinel,
        ) as mock_seam,
        patch("app.worker.tasks.runtime.agent.run_agent_loop", side_effect=fake_loop),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message=user_message,
            conversation_id=None,
        )

    mock_seam.assert_called_once()
    assert "turn" in captured, (
        "run_agent_loop was never handed a `turn` argument, so run_agent_turn did "
        f"not drive the turn through {SEAM}. captured={sorted(captured)}"
    )
    assert captured["turn"] is sentinel, (
        "the turn object run_agent_loop received is not the one the seam "
        f"returned (got {captured['turn']!r}). run_agent_turn is constructing or "
        "substituting a turn on its own, so the eval and the chat path can no "
        "longer be the same agent."
    )
    assert captured.get("message") == user_message, (
        "the message handed to the loop is not the message the task was given "
        f"(got {captured.get('message')!r}). The question the agent is asked "
        "determines its answer as surely as the system prompt does, and it "
        "travels beside the turn object rather than inside it, so no turn guard "
        "can see it being rewritten."
    )


def test_the_turn_served_matches_an_independently_built_reference():
    """THE VALUE GUARD — the half that does not care how the drift is spelled.

    The turn runs with the REAL seam; the tool server, the system prompt and the
    provider client are replaced by deterministic stand-ins that record their own
    behaviour-determining inputs. After the turn, a second `AgentTurn` is built
    directly from a PRISTINE copy of the same agent spec and the same per-turn
    inputs, and every field is compared.

    What that closes, each of which was observed green against an identity-only
    version of this file:

      * `t = turn; t.max_model_calls = 3`      -> the ceiling differs
      * a module-level `_tighten_turn(turn)`   -> the field it set differs
      * a trimmed tool list                    -> the tool names differ, by
        assertion rather than by the sentinel happening to lack the attribute
      * `agent.soul_role = …` before the seam  -> system_prompt marker differs,
        because the reference is built from a copy taken before the turn
      * `soul_override=None`                   -> system_prompt marker differs
      * `verified_session_token=""`            -> tool-server marker differs
    """
    from app.core.config import settings
    from app.core.model_client import route_for
    from app.services.agent_loop import MAX_MODEL_CALLS_PER_TURN, build_agent_turn
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    spec = _agent_spec()
    pristine_spec = copy.deepcopy(spec)
    agent = _agent_from(spec)
    agent_id = str(agent.id)
    local_conv_id = "00000000-0000-0000-0000-0000000000bb"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, _make_job()]

    captured: dict = {}

    async def fake_loop(*args, **kwargs):
        captured.update(kwargs)
        return dict(_CANNED_TURN_RESULT)

    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with (
        tools_patch as served_tools,
        prompt_patch,
        client_patch,
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam-value",
        ),
        patch(
            "app.worker.tasks.runtime.agent._resolve_turn_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE), False),
        ),
        patch("app.worker.tasks.runtime.agent.run_agent_loop", side_effect=fake_loop),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
            verified_session_token=_VERIFIED_TOKEN,
        )
    served_tool_kwargs = dict(served_tools.call_args.kwargs)

    served = captured.get("turn")
    assert served is not None, (
        "the loop was not handed a turn object at all, so either the seam was "
        "bypassed or run_agent_loop was called with a different signature."
    )

    # Built AFTER the turn, from a pristine copy of the agent spec, so a
    # mutation of the row on the turn path cannot reach both sides.
    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with tools_patch as reference_tools, prompt_patch, client_patch:
        reference = build_agent_turn(
            agent=_agent_from(pristine_spec),
            conn_str=_CONN_STR,
            conversation_id=local_conv_id,
            job_id=job_id,
            side_effects="live",
            verified_session_token=_VERIFIED_TOKEN,
            soul_override=dict(_SOUL_OVERRIDE),
            ledger=[].append,
        )
    reference_tool_kwargs = dict(reference_tools.call_args.kwargs)

    # Named first, so a failure says which behaviour moved rather than only that
    # something did.
    assert served.route == reference.route == route_for("agent_turn")
    assert served.max_model_calls == reference.max_model_calls == MAX_MODEL_CALLS_PER_TURN
    assert served.max_budget_usd == reference.max_budget_usd == settings.AGENT_MAX_BUDGET_USD
    assert [t.name for t in served.tools] == [t.name for t in reference.tools] == EXPECTED_TOOL_NAMES
    assert served.calls == [], (
        "the turn was born carrying model calls. `calls` is the list the ledger "
        "recorder tees into and the budget guard reads; a non-empty one at birth "
        "prices this turn for somebody else's spend."
    )
    assert served.system_prompt == reference.system_prompt, (
        "the system prompt the customer was served is not the one an eval "
        "rebuilding from the same agent row would get.\n"
        f"  served:    {served.system_prompt}\n"
        f"  reference: {reference.system_prompt}\n"
        "Either the agent row was mutated on the turn path before the seam read "
        "it, or an input that selects the prompt (soul_override — the OPS-16 "
        "canary) was not threaded through."
    )
    assert served.client == reference.client, (
        "the provider client the customer was served was built from different "
        f"inputs than an eval would use.\n  served:    {served.client}\n"
        f"  reference: {reference.client}\n"
        "The client carries the purpose route and the ledger context, so this is "
        "a billing and attribution difference, not a cosmetic one."
    )

    # …then the tool-server inputs, so a capability difference nobody thought to
    # name still cannot drift.
    served_tool_kwargs.pop("notify_fn", None)
    reference_tool_kwargs.pop("notify_fn", None)
    assert served_tool_kwargs == reference_tool_kwargs, (
        "the tool server the customer was served was built from different inputs "
        f"than an eval would use.\n  served:    {served_tool_kwargs}\n"
        f"  reference: {reference_tool_kwargs}\n"
        "The tool server is where the capability envelope and the IDV-05 "
        "verified-session token take effect."
    )


def test_the_seam_receives_the_turn_s_own_inputs():
    """A seam called with the wrong arguments is drift with extra steps.

    All eight of the seam's arguments are checked, because the two that were
    originally skipped are the two with the largest behavioural reach and both
    were observed to be replaceable with constants while every guard stayed
    green:

      * `soul_override` IS the OPS-16 canary — it selects which system prompt
        the customer gets. Dropping it reverts every canary conversation to the
        agent's live soul_* columns while `turn_metrics.prompt_version_id` still
        stamps the canary id: a score attributed to a prompt version that never
        served the turn (BACKLOG 2.3's defect exactly).
      * `verified_session_token` is the IDV-05 identity posture. Blanking it
        makes every identity-gated skill refuse a verified customer.

    `ledger` is the one ADR 0008 added, and it is mandatory for the reason one
    level up: a turn whose calls are recorded nowhere spends a tenant's money
    with no row to show for it, which is the failure #46 ended.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    existing_conv_id = str(uuid.uuid4())

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, _make_job()]

    async def fake_loop(*args, **kwargs):
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={
                "id": existing_conv_id,
                "metadata": {"prompt_version_id": _PROMPT_VERSION_ID},
            },
        ),
        patch("app.worker.tasks.runtime.agent._read_turn_history", return_value=[]),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam-2",
        ),
        patch(
            "app.worker.tasks.runtime.agent._resolve_turn_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE), False),
        ),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_turn",
            return_value=_SeamSentinel(),
        ) as mock_seam,
        patch("app.worker.tasks.runtime.agent.run_agent_loop", side_effect=fake_loop),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="And what about exchanges?",
            conversation_id=existing_conv_id,
            verified_session_token=_VERIFIED_TOKEN,
        )

    mock_seam.assert_called_once()
    kwargs = mock_seam.call_args.kwargs
    assert kwargs.get("agent") is agent, (
        f"the seam was handed {kwargs.get('agent')!r}, not the agent row this "
        "turn resolved"
    )
    assert kwargs.get("conn_str") == _CONN_STR
    assert str(kwargs.get("conversation_id")) == existing_conv_id
    assert kwargs.get("job_id") == job_id
    assert callable(kwargs.get("ledger")), (
        "the seam was handed a ledger of "
        f"{kwargs.get('ledger')!r}. Every model call this turn makes is recorded "
        "through it; without one the turn spends the tenant's money and leaves "
        "no row."
    )
    assert kwargs.get("soul_override") == _SOUL_OVERRIDE, (
        "soul_override= must carry the resolved prompt version's soul fields "
        f"into the seam; got {kwargs.get('soul_override')!r}. It is the OPS-16 "
        "canary: dropped, the conversation is served the agent's live persona "
        "while turn_metrics still attributes the turn to the canary version."
    )
    assert kwargs.get("verified_session_token") == _VERIFIED_TOKEN, (
        "verified_session_token= must carry the turn's IDV-05 token into the "
        f"seam; got {kwargs.get('verified_session_token')!r}. Blanked, it reads "
        "as 'no verified session' and every identity-gated transactional skill "
        "refuses a customer who did verify."
    )
    assert kwargs.get("side_effects") == "live", (
        "the chat path did not ask for live side effects; it asked for "
        f"{kwargs.get('side_effects')!r}. This is the turn a customer is waiting "
        "on. Recorded here means their refund silently does not happen, their "
        "escalation never reaches the owner, and the ops room's retrieval "
        "metrics stop being written — none of which raises anything."
    )
    assert set(kwargs) == {
        "agent",
        "conn_str",
        "conversation_id",
        "job_id",
        "side_effects",
        "verified_session_token",
        "soul_override",
        "ledger",
    }, (
        f"the seam's call kwargs changed: {sorted(kwargs)}. Every argument is "
        "asserted above; a new one arriving unasserted is a new behaviour input "
        "with no test, which is how the two above went unchecked."
    )


def test_build_agent_turn_assembles_the_full_contract():
    """The seam tested directly, as the standalone callable the eval imports.

    Every other test here either reads the seam's source or patches it out, so
    nothing pinned what it actually returns under inputs `run_agent_turn` never
    supplies — an explicit `soul_override`, a non-empty `verified_session_token`,
    a conversation id the caller chose. The eval supplies exactly those.
    """
    from app.core.config import settings
    from app.core.model_client import route_for
    from app.services.agent_loop import (
        MAX_MODEL_CALLS_PER_TURN,
        AgentTurn,
        build_agent_turn,
    )

    agent = _make_agent()
    conv_id = "00000000-0000-0000-0000-0000000000cc"
    job_id = str(uuid.uuid4())

    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with tools_patch as mock_tools, prompt_patch as mock_prompt, client_patch:
        turn = build_agent_turn(
            agent=agent,
            conn_str=_CONN_STR,
            conversation_id=conv_id,
            job_id=job_id,
            side_effects="live",
            verified_session_token=_VERIFIED_TOKEN,
            soul_override=dict(_SOUL_OVERRIDE),
            ledger=[].append,
        )

    assert isinstance(turn, AgentTurn)
    assert turn.route == route_for("agent_turn"), (
        "the turn's route is not the one the purpose table gives `agent_turn`. "
        "Decision #34 priced this turn against that row; a route chosen here "
        "instead is a cost nobody measured."
    )
    assert turn.max_model_calls == MAX_MODEL_CALLS_PER_TURN == 6, (
        "max_model_calls is not 6. D-10 raised the SDK's max_turns from 3 "
        "because 3 cut the agent off after the retrieve round trip and left "
        "response_text empty; the eval would then score a 6-call agent against a "
        "3-call production one."
    )
    assert turn.max_budget_usd == settings.AGENT_MAX_BUDGET_USD
    assert turn.calls == []
    assert [t.name for t in turn.tools] == EXPECTED_TOOL_NAMES, (
        "the capability surface changed. Expected exactly these 11 tools, in "
        f"this order: {EXPECTED_TOOL_NAMES}. Six of them move money or state."
    )
    assert len(turn.tools) == 11
    assert MUTATING_SKILLS <= {t.name for t in turn.tools}, (
        "the seam no longer grants the mutating transactional skills. If that "
        "is a deliberate narrowing, good — but it changes what production can "
        "do, so update EXPECTED_TOOL_NAMES and say so."
    )

    mock_prompt.assert_called_once()
    assert mock_prompt.call_args.args[0] is agent
    assert mock_prompt.call_args.kwargs.get("soul_override") == _SOUL_OVERRIDE
    assert turn.system_prompt == _system_prompt_marker(agent, dict(_SOUL_OVERRIDE))

    mock_tools.assert_called_once()
    tool_kwargs = mock_tools.call_args.kwargs
    assert tool_kwargs["conn_str"] == _CONN_STR
    assert tool_kwargs["agent_id"] == str(agent.id)
    assert tool_kwargs["agent_name"] == agent.name
    assert tool_kwargs["tenant_id"] == str(agent.tenant_id)
    assert tool_kwargs["conversation_id"] == conv_id
    assert tool_kwargs["job_id"] == job_id
    assert tool_kwargs["verified_session_token"] == _VERIFIED_TOKEN
    assert callable(tool_kwargs["notify_fn"])


# ---------------------------------------------------------------------------
# The side-effect mode — the seam's half of BACKLOG 2.5
#
# Settled by the owner, 2026-08-07: a mandatory `side_effects` parameter,
# "live" or "recorded", NO DEFAULT. The tool layer's half of the same decision
# — that recorded mode actually stops the ProviderAdapter, the metrics write and
# the mail — lives in tests/unit/test_recorded_side_effects.py. Both halves are
# needed: a seam that takes the parameter and drops it is green on that file,
# and a tool layer that honours a mode nothing sets is green on this one.
# ---------------------------------------------------------------------------


def _build(**overrides):
    """Build a turn through the real seam with the collaborators stubbed out."""
    from app.services.agent_loop import build_agent_turn

    kwargs = {
        "agent": _make_agent(),
        "conn_str": _CONN_STR,
        "conversation_id": "00000000-0000-0000-0000-0000000000ff",
        "job_id": str(uuid.uuid4()),
        "verified_session_token": _VERIFIED_TOKEN,
        "soul_override": dict(_SOUL_OVERRIDE),
        "ledger": [].append,
    }
    kwargs.update(overrides)

    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with tools_patch as mock_tools, prompt_patch, client_patch:
        turn = build_agent_turn(**kwargs)
    return turn, mock_tools, kwargs["agent"]


def test_the_seam_refuses_to_build_without_a_side_effects_mode():
    """NO DEFAULT. This is the entire mechanism, and it is one word wide.

    Give `side_effects` a default of "live" and every guard in both files stays
    green while the eval quietly issues real refunds against a real
    tenant's provider, driven months from now by someone reading the signature
    and not the plan. A default is not a convenience here, it is the failure
    mode: the caller who most needs to think about this question is exactly the
    one who would never be asked it.

    TypeError from Python's own binding, rather than a runtime check, because it
    fires at the call site with the parameter's name in it.
    """
    from app.services.agent_loop import build_agent_turn

    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with pytest.raises(TypeError, match="side_effects"):
        with tools_patch, prompt_patch, client_patch:
            build_agent_turn(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="00000000-0000-0000-0000-00000000ffff",
                job_id=str(uuid.uuid4()),
                ledger=[].append,
            )


def test_the_seam_refuses_to_build_without_a_ledger():
    """The other mandatory argument, and the same argument one level up.

    A turn built without a ledger runs the model against a tenant's account and
    records nothing, so nothing can price it, bill it or notice it. That is the
    failure #46 ended, and a default of `None` would restore it silently at the
    one call site nobody re-reads.
    """
    from app.services.agent_loop import build_agent_turn

    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with pytest.raises(TypeError, match="ledger"):
        with tools_patch, prompt_patch, client_patch:
            build_agent_turn(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="00000000-0000-0000-0000-00000000fffd",
                job_id=str(uuid.uuid4()),
                side_effects="live",
            )


def test_the_seam_rejects_a_mode_it_does_not_implement():
    """`Literal` is a type annotation and stops nothing at run time.

    `side_effects="dry_run"` is the plausible mistake — it is what this
    parameter is called in most codebases — and a bare `== "recorded"` check
    would read it as live and move real money on the eval path. Fail loudly on
    the third value rather than silently on the safe-looking one.

    Both collaborators are patched out, and `match=` pins the seam's OWN message.
    Neither is fussiness. An early version of this test called the real tool
    layer, which has the same check one layer down, so it was green with the
    seam's `raise` deleted and its mutation proof said so. It was demonstrating
    the tool layer's guard while claiming to demonstrate the seam's. The seam's
    check is not redundant with that one. It fires BEFORE `bind_tool_context`
    publishes any per-task ContextVar or the system prompt is assembled, and it
    names `build_agent_turn`, which is where the caller made the mistake. A test
    still cannot prove a guard it never reaches.
    """
    from app.services.agent_loop import build_agent_turn

    tools_patch, prompt_patch, client_patch = _seam_collaborators()
    with tools_patch, prompt_patch, client_patch:
        with pytest.raises(ValueError, match="build_agent_turn: side_effects"):
            build_agent_turn(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="00000000-0000-0000-0000-00000000fffe",
                job_id=str(uuid.uuid4()),
                side_effects="dry_run",
                ledger=[].append,
            )


def test_recorded_mode_grants_exactly_the_same_capability_surface_as_live():
    """THE REJECTED-ALTERNATIVE PIN. The reason recorded mode exists at all.

    The other way to stop an eval refund was to hand the eval a read-only tool
    subset. The owner rejected it because it makes the sentence *"the agent
    should have refused to refund here"* unfalsifiable: the scenario could no
    longer FAIL, since the agent could not attempt the thing it was supposed to
    refuse. An agent that cannot do the wrong thing tells you nothing by not
    doing it.

    That rejection is only durable if something notices the day someone
    "hardens" recorded mode by trimming the tool list — which reads like an
    improvement, passes every money-related guard in the suite, and silently
    turns a whole class of eval scenario into a tautology. This is that
    something. Every behaviour-determining field is compared, not just the tool
    list, because the same reasoning applies to the two ceilings and the system
    prompt.
    """
    # ONE agent row and ONE job id across both builds. Everything that varies
    # between two calls has to be held equal, or the comparison below reports
    # fixture noise as a capability difference.
    shared = {"agent": _make_agent(), "job_id": str(uuid.uuid4())}
    live, live_tools, _ = _build(side_effects="live", **shared)
    recorded, recorded_tools, _ = _build(side_effects="recorded", **shared)

    live_names = [t.name for t in live.tools]
    assert [t.name for t in recorded.tools] == live_names == EXPECTED_TOOL_NAMES
    assert MUTATING_SKILLS <= set(live_names), (
        "recorded mode no longer grants the six mutating skills. That looks "
        "like safety and is the opposite: an eval agent that cannot attempt a "
        "refund cannot be scored on refusing one, so every capability-envelope "
        "scenario silently becomes unfalsifiable. Recorded mode's job is to "
        "make the attempt harmless, never to prevent it."
    )

    for field in ("route", "system_prompt", "max_model_calls", "max_budget_usd", "client"):
        assert getattr(live, field) == getattr(recorded, field), (
            f"recorded mode changed {field}, which the agent can see or choose "
            "against. Only the outer edge may differ between the two modes. The "
            "eval must measure the agent production serves, not a quieter one."
        )

    live_kwargs = dict(live_tools.call_args.kwargs)
    recorded_kwargs = dict(recorded_tools.call_args.kwargs)
    for kwargs in (live_kwargs, recorded_kwargs):
        # notify_fn is a closure and side_effects is the field the mode is
        # supposed to reach; everything else must match.
        kwargs.pop("notify_fn", None)
        kwargs.pop("side_effects", None)
    assert live_kwargs == recorded_kwargs, (
        "recorded mode built the tool server from different inputs than live "
        f"did.\n  live:     {live_kwargs}\n  recorded: {recorded_kwargs}"
    )


@pytest.mark.parametrize("mode", ["live", "recorded"])
def test_the_seam_threads_the_mode_into_the_tool_server(mode):
    """A parameter the seam accepts and then forgets is worse than none.

    It reads as settled in every review, satisfies the mandatory-parameter test
    above, and leaves the eval fully live. The tool layer is where the mode has
    its effect, so the only thing that matters is that it arrives there.
    """
    _, mock_tools, _ = _build(side_effects=mode)
    assert mock_tools.call_args.kwargs["side_effects"] == mode, (
        f"the seam was asked for side_effects={mode!r} and passed "
        f"{mock_tools.call_args.kwargs.get('side_effects')!r} to "
        "bind_tool_context. The mode has no effect anywhere else."
    )


def test_live_mode_sends_the_real_escalation_mail():
    """The anti-tautology partner of the recorded-escalation test below.

    Asserting only that recorded mode does not send mail would stay green if the
    seam never wired escalation mail at all. This drives the notify_fn the seam
    actually handed the tool server and asserts the owner still gets paged.
    """
    _, mock_tools, agent = _build(side_effects="live")
    notify_fn = mock_tools.call_args.kwargs["notify_fn"]

    with (
        patch("app.services.agent_loop.send_escalation_email") as mock_mail,
        patch("app.services.agent_loop.record_suppressed_side_effect") as mock_record,
    ):
        notify_fn("customer asked for a human", "three failed lookups")

    mock_mail.assert_called_once_with(
        agent, "customer asked for a human", "three failed lookups"
    )
    mock_record.assert_not_called()


def test_recorded_mode_records_the_escalation_instead_of_sending_it():
    """The escalation edge, which the value comparison structurally cannot see.

    `notify_fn` is a closure, so two builds never produce equal values and it is
    excluded from `test_the_turn_served_matches_an_independently_built_reference`
    by design. That leaves it as the one behaviour-determining input in the seam
    with no coverage, and an eval that escalates would page a real owner about a
    customer who does not exist, nightly, for as long as the scenario stays in
    the golden set.
    """
    _, mock_tools, agent = _build(side_effects="recorded")
    notify_fn = mock_tools.call_args.kwargs["notify_fn"]

    with (
        patch("app.services.agent_loop.send_escalation_email") as mock_mail,
        patch("app.services.agent_loop.record_suppressed_side_effect") as mock_record,
    ):
        notify_fn("customer asked for a human", "three failed lookups")

    mock_mail.assert_not_called()
    mock_record.assert_called_once()
    kind, detail = mock_record.call_args.args
    assert kind == "escalation.notify"
    assert detail["reason"] == "customer asked for a human"
    assert detail["context"] == "three failed lookups"
    assert detail["agent_id"] == str(agent.id), (
        "the recorded escalation does not name the agent that escalated, so an "
        "eval reading it back cannot attribute the escalation to a scenario."
    )


def test_the_canary_choice_is_not_committed_when_the_seam_fails():
    """RESOLVE BEFORE, COMMIT AFTER — the settled answer to BACKLOG 2.6.

    P1 moved `_resolve_turn_prompt_version` ahead of the seam, because the soul
    fields it resolves are an input to the system prompt the seam builds. That
    move was correct and stays. But the helper also CALLED
    `_set_prompt_version_id`, which commits to `conversations.metadata` — so the
    write moved forward with the read, and a turn that then died inside the seam
    left the conversation permanently sticky to a prompt version that never
    served it. Before P1 the Celery retry re-rolled. A canary whose denominator
    counts conversations it never spoke in is a canary reporting on a population
    it does not have.

    Settled by the owner on 2026-08-07: the resolution stays where P1 put it,
    the WRITE moves back behind a successful `build_agent_turn`. The conversation
    becomes sticky only once there is an agent to be sticky to.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    local_conv_id = "00000000-0000-0000-0000-0000000000dd"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, _make_job()]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch(
            "app.worker.tasks.runtime.agent.resolve_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE)),
        ),
        patch("app.worker.tasks.runtime.agent._set_prompt_version_id") as mock_commit,
        patch(
            "app.worker.tasks.runtime.agent.build_agent_turn",
            side_effect=RuntimeError("tool server unavailable"),
        ),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        with pytest.raises(RuntimeError, match="tool server unavailable"):
            run_agent_turn.run(
                job_id=job_id,
                agent_id=agent_id,
                message="What is the return policy?",
                conversation_id=None,
            )

        assert mock_commit.call_count == 0, (
            "the canary choice was committed even though the seam failed "
            f"(call_count={mock_commit.call_count}). The conversation is now "
            "sticky to a prompt version that never served a turn, and the Celery "
            "retry can no longer re-roll it. Move the _set_prompt_version_id "
            "call back behind a successful build_agent_turn. BACKLOG 2.6, "
            "settled 2026-08-07."
        )


def test_the_canary_choice_is_committed_once_the_turn_exists():
    """The other half of "resolve before, commit after", and the half that keeps
    the fix from being a deletion.

    Not committing at all would pass the test above and silently end OPS-16
    canary stickiness: every turn of a conversation would re-roll, the persona
    would flip mid-conversation (A-CANARY's whole prohibition), and
    `turn_metrics.prompt_version_id` would attribute consecutive turns of one
    conversation to different versions. So the write must still happen on the
    success path, exactly once, with the same arguments as before — and strictly
    AFTER the seam returned, which is the ordering the pair of tests pins.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    local_conv_id = "00000000-0000-0000-0000-0000000000ee"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, _make_job()]

    order: list[str] = []

    async def fake_loop(*args, **kwargs):
        order.append("loop")
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-canary",
        ),
        patch(
            "app.worker.tasks.runtime.agent.resolve_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE)),
        ),
        patch(
            "app.worker.tasks.runtime.agent._set_prompt_version_id",
            side_effect=lambda *a, **k: order.append("commit"),
        ) as mock_commit,
        patch(
            "app.worker.tasks.runtime.agent.build_agent_turn",
            side_effect=lambda **kwargs: (order.append("seam"), _SeamSentinel())[1],
        ),
        patch("app.worker.tasks.runtime.agent.run_agent_loop", side_effect=fake_loop),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    assert mock_commit.call_count == 1, (
        "the canary choice was never committed on a SUCCESSFUL turn "
        f"(call_count={mock_commit.call_count}). Deleting the write passes the "
        "failure-path test above and quietly ends per-conversation stickiness: "
        "every turn re-rolls, the persona flips mid-conversation, and "
        "consecutive turns of one conversation are attributed to different "
        "prompt versions."
    )
    assert mock_commit.call_args.args[1:] == (local_conv_id, _PROMPT_VERSION_ID)
    assert order.index("commit") > order.index("seam"), (
        f"the commit did not follow the seam call (order={order}). Committing "
        "first is exactly the P1 behaviour BACKLOG 2.6 settled against — a turn "
        "that dies in the seam would again leave the conversation sticky to a "
        "version that never served it."
    )


def test_a_failed_canary_commit_never_fails_the_turn():
    """T-21-09-05's tenant-DB half, which had no test anywhere.

    Moving the canary WRITE behind the seam put it on the turn's critical path
    for the first time, and it writes to the TENANT database — a Neon cold start
    or a dropped connection is the ordinary case, not the exotic one. Unwrapped,
    it would fail a customer turn whose answer had already been produced. The
    `try/except` in `run_agent_turn` is what stops that, and deleting it left
    the whole suite green: the pre-existing test that covered a tenant-DB
    failure was rewritten during P1b to cover a CONTROL-DB failure instead, so
    this half lost its only coverage and the replacement's docstring asserted it
    in prose.

    What must survive the failure: the turn runs, the task returns, and the
    failure is logged rather than swallowed silently.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    local_conv_id = "00000000-0000-0000-0000-0000000000ff"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, _make_job()]

    ran: list[str] = []

    async def fake_loop(*args, **kwargs):
        ran.append("loop")
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-canary-fail",
        ),
        patch(
            "app.worker.tasks.runtime.agent.resolve_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE)),
        ),
        patch(
            "app.worker.tasks.runtime.agent._set_prompt_version_id",
            side_effect=RuntimeError("simulated tenant DB outage"),
        ) as mock_commit,
        patch(
            "app.worker.tasks.runtime.agent.build_agent_turn",
            side_effect=lambda **kwargs: _SeamSentinel(),
        ),
        patch("app.worker.tasks.runtime.agent.run_agent_loop", side_effect=fake_loop),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", MagicMock()),
        patch("app.worker.tasks.runtime.agent.log.warning") as mock_warn,
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    mock_commit.assert_called_once()
    assert ran == ["loop"], (
        "the turn never ran: a tenant-DB failure on the canary write killed a "
        "turn the customer is waiting on. The write is bookkeeping about which "
        "prompt version served the turn; losing it costs stickiness, and the "
        "next turn of this conversation re-rolls. Losing the turn costs the "
        "answer."
    )
    assert isinstance(result, dict)
    warned = [c.args[0] for c in mock_warn.call_args_list if c.args]
    assert "run_agent_turn.prompt_version_persist_failed" in warned, (
        "the canary-write failure was swallowed without a log line. A silent "
        "except is how a conversation stops being sticky and nobody finds out "
        f"until the canary's denominator is wrong. Warnings logged: {warned}"
    )


@pytest.mark.parametrize("failing_step", ["strategy", "tool_context"])
def test_a_failed_seam_leaves_the_side_effect_mode_at_the_safe_default(failing_step):
    """The mode is process-context sticky and nothing resets it between tasks.

    Celery's prefork pool does not isolate contextvars per task. Once an eval
    task sets the mode to "recorded" it stays set in that worker's context until
    something calls `bind_tool_context` again, and the entire safety argument
    for the `"live"` default rested on the untested claim that every path that
    reaches the tools does. `build_agent_turn` raises above `bind_tool_context`
    in three places (its own validation, the `RetrievalStrategy` parse, and
    `bind_tool_context` itself), so a stale "recorded" could survive into
    whatever ran next in that context: a customer turn that silently stops
    refunding, with no error anywhere, found by a customer rather than by us.

    Two changes close it, and this test drives both. `build_agent_turn` resets to
    the safe default FIRST, before anything that can throw; and
    `bind_tool_context` publishes the mode LAST, after every step that can raise.
    `strategy` dies before `bind_tool_context` and `tool_context` dies inside it,
    on opposite sides of where the mode used to be published, so neither change
    alone makes both cases pass.
    """
    import app.services.agent_tools as agent_tools
    from app.services.agent_loop import build_agent_turn

    token = agent_tools._side_effects_var.set("recorded")
    try:
        # The expected exception is matched by MESSAGE, not by `Exception`. A
        # bare `pytest.raises(Exception)` swallows a NameError from a missing
        # import and the seam is never called at all — which is exactly how the
        # first draft of this test passed the reset assertion for no reason.
        if failing_step == "strategy":
            failure = patch(
                "app.services.agent_loop.RetrievalStrategy.model_validate",
                side_effect=ValueError("malformed retrieval_strategy"),
            )
            expected: tuple = (ValueError, "malformed retrieval_strategy")
        else:
            # The last step inside `bind_tool_context` before it publishes the
            # mode. Anything that raises there has to leave "live" behind.
            failure = patch(
                "app.services.agent_tools.log",
                new=MagicMock(
                    debug=MagicMock(side_effect=RuntimeError("tool context not bound"))
                ),
            )
            expected = (RuntimeError, "tool context not bound")

        with failure, pytest.raises(expected[0], match=expected[1]):
            build_agent_turn(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="conv-stale-mode",
                job_id="job-stale-mode",
                side_effects="recorded",
                ledger=[].append,
            )

        assert agent_tools.current_side_effect_mode() == "live", (
            f"a seam call that died at {failing_step!r} left the previous "
            "turn's 'recorded' in force. The next thing to run in this Celery "
            "worker context is a customer turn whose refunds silently stop "
            "happening — a failure that produces no error and is found by the "
            "customer."
        )
    finally:
        agent_tools._side_effects_var.reset(token)
