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

Three kinds of assertion, deliberately layered:

  * STATIC — an AST read of `agent.py`'s own source. An inlined
    `ClaudeAgentOptions(...)` inside `run_agent_turn` fails here even when it
    happens to produce byte-identical kwargs today, because "identical today"
    is precisely the state that drifts tomorrow. Import aliases are resolved
    before counting, so `from … import build_tool_server as bts` does not hide
    a second tool server, and dynamic dispatch (`getattr(mod, "X")(…)`) is
    banned outright on the turn path rather than left as a hole.
  * IDENTITY — a sentinel driven through the live task. The options object
    `_run_sdk_turn` actually receives must be the object the seam returned.
    Source that merely mentions the seam and then passes something else fails
    here.
  * VALUE — the turn is driven with the REAL seam, and every kwarg the options
    object `_run_sdk_turn` receives was built from is compared against an
    options object built independently, after the turn, from a pristine copy of
    the same agent row. This is the half that does not
    care how the drift is spelled: an alias (`opts = options; opts.max_turns =
    3`), a module-level helper (`_tighten(options)`), a mutation of the `agent`
    row before the seam reads it, or an `allowed_tools.append(...)` all change a
    field value and all go red, without any guard having to recognise the
    spelling.

Why all three. The first version of this file had only STATIC + IDENTITY, and a
tier-2 probe walked straight through it: seven realistic drift edits left all
twelve guards green, because the in-place-mutation guard matched exactly one
spelling (`options.<attr> = …`, base literally named `options`) and the sentinel
sees only the object it handed out, never the object's contents. That is
BACKLOG 3.3's defect class — a guard demonstrated only inside the complement of
its own blind spot. The VALUE half exists because the guard must pin the
property, not the spelling; the STATIC half stays because it catches
constructions on branches no test drives, and reaches into `_run_sdk_turn`,
which the dynamic halves patch out.

Known scope limits, stated rather than implied:

  * `notify_fn` is a closure built inside the seam, so two builds never produce
    equal values and it is excluded from the value comparison. Which of the two
    closures the mode selected is asserted directly instead, in
    `test_recorded_mode_records_the_escalation_instead_of_sending_it`.
  * The seam's options carry a LIVE tool server bound to the tenant connection
    string, and from P1b `side_effects` decides what that means: "live" writes
    `retrieval_metrics`, sends escalation mail and dispatches the real
    transactional adapters; "recorded" records those three instead. BACKLOG 2.5,
    settled 2026-08-07. The tests here pin the seam's half of it — mandatory
    parameter, identical capability surface in both modes, mode threaded into
    the tool server. The tool layer's half is
    `tests/unit/test_recorded_side_effects.py`.
"""

from __future__ import annotations

import ast
import copy
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_AGENT_PY = _APP_ROOT / "worker" / "tasks" / "runtime" / "agent.py"

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

#: Modules permitted to construct ClaudeAgentOptions anywhere under `app/`.
#: `agent.py` is the seam. `deployment_service.py` and `red_team_service.py`
#: build orchestrators and ADVERSARIES, not the customer agent, and are
#: grandfathered deliberately.
#:
#: `red_team_probe.py` is NOT that, and the earlier wording here ("adversaries
#: and tooling, not the customer agent") was wrong about it. Its
#: `_build_transactional_probe_fn` builds the VICTIM turn — the customer agent —
#: by hand, with its own `_PROBE_MODEL` and its own `_ALLOWED_TOOLS`. So RTX-01's
#: confused-deputy findings are about an agent with a different model and a
#: different tool list from the one production serves and the eval will measure.
#: Routing it through the seam is BACKLOG 2.9; until then this entry is a known
#: divergence, recorded rather than implied.
#:
#: The name that matters is the one absent: `app/worker/tasks/runtime/eval.py`.
#: P2 makes the eval invoke the agent, and the whole value of approach (b)
#: evaporates the moment the eval builds its own options — the eval would then
#: score an agent no customer is served. That is not caught by anything scoped
#: to agent.py, so it is caught here.
MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS = {
    "app/worker/tasks/runtime/agent.py",
    "app/services/deployment_service.py",
    "app/services/red_team_probe.py",
    "app/services/red_team_service.py",
}

#: The exact capability surface the seam grants. A value, not a count: the
#: audit's transactional table shows six of these move money or state, so a
#: silent addition is a capability grant and a silent removal is a regression
#: the eval would score as an agent that "chose not to".
EXPECTED_ALLOWED_TOOLS = [
    "mcp__customer-tools__retrieve",
    "mcp__customer-tools__lookup_structured",
    "mcp__customer-tools__escalate_to_human",
    "mcp__customer-tools__clarify",
    "mcp__customer-tools__place_order",
    "mcp__customer-tools__cancel_order",
    "mcp__customer-tools__issue_refund",
    "mcp__customer-tools__update_subscription",
    "mcp__customer-tools__book_slot",
    "mcp__customer-tools__update_customer_record",
    "mcp__customer-tools__confirm_action",
]

#: The six of those that move money or tenant state through a real
#: ProviderAdapter. Named separately because the seam grants them to EVERY
#: caller, and from P2 the eval task is a caller: one eval scenario in which the
#: agent decides to refund executes a refund. BACKLOG 2.5 is the open decision.
MUTATING_SKILLS = {
    "mcp__customer-tools__place_order",
    "mcp__customer-tools__cancel_order",
    "mcp__customer-tools__issue_refund",
    "mcp__customer-tools__update_subscription",
    "mcp__customer-tools__book_slot",
    "mcp__customer-tools__update_customer_record",
}

#: Callables that may legitimately be handed the seam's options object. Anything
#: else receiving it is a function that can mutate it out of sight of every
#: static guard here — the `_tighten_options(options)` shape.
_OPTIONS_SINKS = {
    "run_agent_turn": {"_run_sdk_turn"},
    "_run_sdk_turn": {"ClaudeSDKClient"},
}

#: Dynamic dispatch defeats every AST guard in this file by construction:
#: `getattr(claude_agent_sdk, "ClaudeAgentOptions")(...)` is a Call whose func is
#: itself a Call, so no callee name exists to count. Rather than teach the
#: counter to constant-fold, the turn path is simply not allowed to use it.
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
# Static half — read agent.py's source, import nothing
# ---------------------------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(_AGENT_PY.read_text(encoding="utf-8"))


def _aliases_in(tree: ast.Module) -> dict[str, str]:
    """Map each local import alias back to the name it actually binds.

    `from app.services.agent_tools import build_tool_server as bts` makes `bts`
    a second spelling of the tool-server constructor. Counting the syntactic
    callee alone would score `bts(...)` as an unrelated function and let a
    second tool server exist while every count stayed at 1.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in node.names:
                if name.asname:
                    aliases[name.asname] = name.name.split(".")[-1]
    return aliases


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


def _callee_name(call: ast.Call, aliases: dict[str, str]) -> str | None:
    """The name being called, with import aliases resolved.

    `Foo(...)` -> "Foo"; `mod.Foo(...)` -> "Foo"; `bts(...)` -> "build_tool_server"
    when `bts` is an alias. A call whose callee is itself an expression (a call,
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


def _called_names(node: ast.AST, aliases: dict[str, str] | None = None) -> Counter:
    """Count callee names appearing anywhere inside `node`, aliases resolved."""
    if aliases is None:
        aliases = _aliases_in(_module_tree())
    counts: Counter = Counter()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        name = _callee_name(sub, aliases)
        if name is not None:
            counts[name] += 1
    return counts


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


def _options_roots(fn: ast.AST, aliases: dict[str, str]) -> set[str]:
    """Every local name in `fn` that refers to the seam's options object.

    Seeded two ways — a parameter literally named `options` (that is how
    `_run_sdk_turn` receives it) and any name assigned the seam's return value —
    then closed transitively over `x = <known name>` so `opts = options` does
    not launder the object out of the guard's reach. That alias spelling is the
    exact edit that walked through the first version of this file with all
    twelve tests green.
    """
    roots: set[str] = set()

    args = getattr(fn, "args", None)
    if args is not None:
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg == "options":
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


def _options_offences(fn: ast.AST, roots: set[str], sinks: set[str], aliases: dict[str, str]) -> list[str]:
    """Every way `fn` could change the options object after the seam built it."""
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
                f"line {sub.lineno}: calls {ast.unparse(sub.func)}(...) on the options object"
            )
        handed_over = [a for a in sub.args if isinstance(a, ast.Name) and a.id in roots]
        handed_over += [
            kw.value
            for kw in sub.keywords
            if isinstance(kw.value, ast.Name) and kw.value.id in roots
        ]
        if handed_over and _callee_name(sub, aliases) not in sinks:
            offences.append(
                f"line {sub.lineno}: hands the options object to "
                f"{_callee_name(sub, aliases)}(...)"
            )
    return offences


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


def test_import_aliases_are_resolved_before_counting():
    """Anti-tautology pin for the counter the two tests above depend on.

    `_called_names` used to key on the syntactic callee identifier, so
    `from … import build_tool_server as bts` followed by `bts(...)` counted as
    an unrelated function named "bts" and a second tool server could exist with
    every count still reading 1. This asserts the alias resolution actually
    happens, on a synthetic module, so the property is observed rather than
    assumed from reading the helper.
    """
    tree = ast.parse(
        "from app.services.agent_tools import build_tool_server as bts\n"
        "def f():\n"
        "    return bts(1), build_system_prompt(2)\n"
    )
    aliases = _aliases_in(tree)
    assert aliases.get("bts") == "build_tool_server"
    counts = _called_names(tree, aliases)
    assert counts["build_tool_server"] == 1, (
        "an aliased import of build_tool_server was not counted against its real "
        f"name; counts={dict(counts)}"
    )
    assert counts["bts"] == 0


@pytest.mark.parametrize("fn_name", sorted(_OPTIONS_SINKS))
def test_the_turn_path_never_mutates_or_launders_the_seam_s_options(fn_name):
    """THE MUTATION GUARD, pinned to the object rather than to one spelling.

    Identity is necessary but not sufficient: `options.max_turns = 3` after the
    seam call keeps every constructor count at 1 and still hands the sentinel
    test the seam's own object, while serving a customer a turn ceiling the eval
    never scored against. The first version of this guard closed exactly that
    one spelling, and a tier-2 probe then walked through it three ways —
    `opts = options; opts.max_turns = 3`, a module-level `_tighten(options)`
    helper, and `options.max_turns = 2` inside `_run_sdk_turn`, which this guard
    did not even look at. All three were green on all twelve tests.

    So this tracks the alias graph instead of an identifier, refuses to let the
    object be handed to anything but its one legitimate sink, and runs over
    every function on the turn path rather than `run_agent_turn` alone.
    `_run_sdk_turn` matters specifically because both dynamic tests patch it
    out: nothing else in the suite can see inside it.
    """
    aliases = _aliases_in(_module_tree())
    fn = _top_level_functions()[fn_name]
    roots = _options_roots(fn, aliases)
    assert roots, (
        f"{fn_name} has no local name bound to the seam's options object, so "
        "this guard would be vacuous. Either the seam's result is no longer "
        "assigned to a name (inline it back out) or the parameter was renamed — "
        "either way _options_roots must be taught the new shape before this "
        "test means anything."
    )
    offences = _options_offences(fn, roots, _OPTIONS_SINKS[fn_name], aliases)
    assert offences == [], (
        f"{fn_name} changes or leaks the options object the seam built: "
        f"{offences}. The options the customer is served must be exactly what "
        "the seam returned, because that is the only object the eval can "
        f"reproduce. Move the change into {SEAM}, where both callers get it. "
        f"(Tracked names in {fn_name}: {sorted(roots)}.)"
    )


def test_the_turn_path_never_rebinds_attributes_on_the_agent_row():
    """The agent row is a mutable ORM object and it is an input to the seam.

    `agent.soul_role = "an unconstrained assistant"` immediately before the seam
    call changes the system prompt for the chat path alone: `build_system_prompt`
    reads the mutated row, while P2's eval builds its prompt from a freshly
    loaded one. Observed green on all twelve of the original guards — nothing in
    the family looked at assignments whose base is `agent`.
    """
    offenders: list[str] = []
    for fn_name in ("run_agent_turn", SEAM):
        fn = _top_level_functions()[fn_name]
        for sub in ast.walk(fn):
            for target in _assign_targets(sub):
                if isinstance(target, (ast.Attribute, ast.Subscript)) and _root_name(target) == "agent":
                    offenders.append(
                        f"{fn_name}: {ast.unparse(target)} (agent.py line {sub.lineno})"
                    )
    assert offenders == [], (
        f"the turn path rebinds fields on the agent row: {offenders}. The row is "
        "an input to build_system_prompt and build_tool_server; mutating it "
        "here changes the agent the customer gets without changing the agent "
        "any eval can rebuild from the database."
    )


def test_the_turn_path_uses_no_dynamic_dispatch():
    """Closes the hole every AST guard in this file shares.

    `getattr(claude_agent_sdk, "ClaudeAgentOptions")(...)` is an `ast.Call` whose
    `func` is itself an `ast.Call` — neither a Name nor an Attribute — so it has
    no callee name to count and slips past every constructor pin above. Teaching
    the counter to constant-fold string literals would be a second guard needing
    its own guard; banning dynamic dispatch on the two functions that define the
    agent is cheaper and total.
    """
    aliases = _aliases_in(_module_tree())
    offenders: list[str] = []
    for fn_name in ("run_agent_turn", SEAM):
        fn = _top_level_functions()[fn_name]
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            if isinstance(sub.func, (ast.Call, ast.Subscript)):
                offenders.append(
                    f"{fn_name}: call of a computed callee at agent.py line {sub.lineno}"
                )
            name = _callee_name(sub, aliases)
            if name in _DYNAMIC_DISPATCH_CALLS:
                offenders.append(f"{fn_name}: {name}(...) at agent.py line {sub.lineno}")
    assert offenders == [], (
        f"dynamic dispatch on the turn path: {offenders}. Every constructor "
        "guard in this file counts callee names from the AST; a computed callee "
        "has no name to count, so this is the one construct that makes them all "
        "vacuous at once."
    )


def test_run_agent_turn_has_exactly_one_lexical_call_site_for_the_seam():
    """One *call site* in the source — a syntactic property, which is all an AST
    read can establish.

    The runtime property that actually matters (the seam runs exactly once per
    turn, so one tool server is built and one set of ContextVars is in force) is
    asserted by `mock_seam.assert_called_once()` in the dynamic tests below,
    along the first-turn and subsequent-turn paths. A single call site inside a
    loop or a retry wrapper satisfies this test and fails those; the pair is the
    claim, neither half alone.
    """
    calls = _called_names(_top_level_functions()["run_agent_turn"])[SEAM]
    assert calls == 1, (
        f"run_agent_turn contains {calls} lexical call site(s) for {SEAM}(...); "
        "expected exactly 1."
    )


def test_only_allowlisted_modules_construct_claude_agent_options():
    """Repo-wide, because the drift the plan names is between two FILES.

    Every other guard here parses `agent.py` alone, which proves the chat path
    is self-consistent and says nothing about the eval path. If P2 constructs
    its own `ClaudeAgentOptions` in `eval.py`, `agent.py` still contains exactly
    one of each name and every guard above stays green while the eval scores an
    agent no customer is served — the precise failure approach (b) was chosen to
    prevent.

    Six construction sites already exist under `app/`, so a blanket "only the
    seam" pin is not available: the other three modules build red-team attackers
    and the deployment orchestrator, which are adversaries and tooling, not the
    customer agent. Hence an explicit allowlist. Adding a module to it is a
    deliberate act with a reviewer attached.
    """
    found: dict[str, int] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count = _called_names(tree, _aliases_in(tree))["ClaudeAgentOptions"]
        if count:
            found[path.relative_to(_APP_ROOT.parent).as_posix()] = count

    eval_module = "app/worker/tasks/runtime/eval.py"
    assert eval_module not in found, (
        f"{eval_module} constructs ClaudeAgentOptions ({found.get(eval_module)} "
        f"site(s)). The eval must go through {SEAM} in agent.py or it is scoring "
        "an agent that nobody is served — D1 with an extra step. This is the "
        "assertion that makes approach (b) structural rather than intended."
    )
    assert set(found) == MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS, (
        "the set of modules constructing ClaudeAgentOptions changed.\n"
        f"  found:   {sorted(found)}\n"
        f"  allowed: {sorted(MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS)}\n"
        "A new one is a new agent definition. If it is genuinely an adversary "
        "or a tool rather than the customer agent, add it here on purpose."
    )


# ---------------------------------------------------------------------------
# Dynamic half — drive the real task
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
    """Not a ClaudeAgentOptions and not a MagicMock.

    A MagicMock would compare equal to nothing in particular and would let an
    `is` check pass for the wrong reasons under `spec=`; a bare object() carries
    no name into the failure message. This carries both.

    Deliberately attribute-poor, and deliberately NOT relied upon for that: an
    `options.allowed_tools.append(...)` drift used to be caught only because
    this class raises AttributeError, i.e. the task crashed rather than any
    assertion detecting anything. The value comparison in
    `test_the_options_served_match_an_independently_built_reference` is what
    actually checks the capability surface; this class only proves identity.
    """

    def __repr__(self) -> str:  # pragma: no cover - only read on failure
        return "<options object returned by the seam>"


class _RecordingOptions:
    """Stand-in for ClaudeAgentOptions that records exactly the kwargs it got.

    Why not the real dataclass. `tests/unit/test_agent_task.py` installs a FAKE
    `claude_agent_sdk` module into `sys.modules` at import time and never
    removes it, so whether `ClaudeAgentOptions` is the SDK dataclass or a
    MagicMock depends on which test module imported first — observed directly:
    this file's value comparison passes alone and raises
    `TypeError: isinstance() arg 2 must be a type` when run after
    `test_agent_task.py`. A guard whose meaning depends on collection order is
    not a guard.

    It is also the sharper instrument. Comparing the kwargs the seam PASSED
    answers "did the seam assemble the same agent twice"; comparing the
    dataclass's fields would also drag in defaults the seam never set. And the
    attributes stay writable, so a post-construction mutation
    (`options.max_turns = 3`, `options.allowed_tools.append(...)`) is visible in
    the snapshot exactly as it would be on the real object.
    """

    def __init__(self, **kwargs):
        self._recorded_fields = sorted(kwargs)
        for name, value in kwargs.items():
            setattr(self, name, value)

    def snapshot(self) -> dict:
        """Values read at compare time, not at construction time — that is what
        makes a later mutation show up."""
        return {name: getattr(self, name) for name in self._recorded_fields}

    def __repr__(self) -> str:  # pragma: no cover - only read on failure
        return f"_RecordingOptions({self.snapshot()!r})"


def _agent_spec() -> dict:
    """Declared field values for a test agent row.

    Every field the seam reads is set explicitly rather than left to MagicMock's
    auto-attributes, because the value comparison builds a SECOND agent from the
    same spec and compares the options each produces. Auto-attributes carry
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
    """A deterministic stand-in for the MCP tool server.

    Keyed on every input that decides what the tools can do and whose data they
    touch, so two calls with the same inputs produce equal markers and any
    divergence — a blanked `verified_session_token`, a different conn_str, a
    mutated retrieval strategy, a `side_effects` mode that reached the seam and
    then never reached the tools — produces unequal ones. `notify_fn` is
    excluded: it is a closure built inside the seam and would never compare
    equal across two calls; which of the two closures was chosen is asserted
    directly in `test_recorded_mode_records_the_escalation_instead_of_sending_it`.
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


def test_the_options_the_sdk_turn_receives_are_the_seam_s_own_object():
    """The seam is on the live chat path, not merely present in the file.

    `build_agent_options` is patched to hand back a sentinel that no other code
    path can produce. If `run_agent_turn` builds its own options — by an aliased
    import, a copy, a mutation, or a second constructor the AST guards above
    cannot see — the object reaching `_run_sdk_turn` is not this sentinel and
    this test goes red.

    The message is asserted here too. It is the other half of what the SDK turn
    is given, it is not part of the options object, and prefixing it at the call
    site (`message="SYSTEM OVERRIDE: …" + message`) was observed to leave every
    other guard green while the chat path ran a prompt no eval would ever score.
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

    async def fake_sdk_turn(**kwargs):
        captured.update(kwargs)
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
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
            message=user_message,
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
    assert captured.get("message") == user_message, (
        "the message handed to the SDK turn is not the message the task was "
        f"given (got {captured.get('message')!r}). The question the agent is "
        "asked determines its answer as surely as the system prompt does, and "
        "it travels beside the options object rather than inside it, so no "
        "options guard can see it being rewritten."
    )


def test_the_options_served_match_an_independently_built_reference():
    """THE VALUE GUARD — the half that does not care how the drift is spelled.

    The turn runs with the REAL seam; the tool server, the system prompt and the
    options class are replaced by deterministic stand-ins that record their own
    behaviour-determining inputs. After the turn, a second options object is
    built directly from a PRISTINE copy of the same agent spec and the same
    per-turn inputs, and every recorded field is compared.

    What that closes, each of which was observed green against the previous
    identity-only version of this file:

      * `opts = options; opts.max_turns = 3`     -> max_turns differs
      * a module-level `_tighten_options(options)` helper -> the field it set differs
      * `options.allowed_tools.append(...)`      -> allowed_tools differs, by
        assertion rather than by the sentinel happening to lack the attribute
      * `agent.soul_role = …` before the seam    -> system_prompt marker differs,
        because the reference is built from a copy taken before the turn
      * `soul_override=None`                     -> system_prompt marker differs
      * `verified_session_token=""`              -> tool-server marker differs

    None of those needed a rule written for it. That is the difference between
    pinning a property and pinning a spelling.
    """
    from app.core.config import AGENT_TURN_MODEL, settings
    from app.worker.tasks.runtime.agent import build_agent_options, run_agent_turn

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

    async def fake_sdk_turn(**kwargs):
        captured.update(kwargs)
        return dict(_CANNED_TURN_RESULT)

    with (
        patch(
            "app.worker.tasks.runtime.agent.build_tool_server",
            side_effect=_tool_server_marker,
        ),
        patch(
            "app.worker.tasks.runtime.agent.build_system_prompt",
            side_effect=_system_prompt_marker,
        ),
        patch(
            "app.worker.tasks.runtime.agent.ClaudeAgentOptions",
            side_effect=_RecordingOptions,
        ),
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam-value",
        ),
        patch(
            "app.worker.tasks.runtime.agent._resolve_turn_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE), False),
        ),
        patch("app.worker.tasks.runtime.agent._run_sdk_turn", side_effect=fake_sdk_turn),
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

    served = captured.get("options")
    assert isinstance(served, _RecordingOptions), (
        "the SDK turn was not handed the options object the seam constructed "
        f"(got {served!r}). Either the seam was bypassed or the options were "
        "built by a constructor this test did not patch — an aliased import, or "
        "a second class entirely."
    )

    # Built AFTER the turn, from a pristine copy of the agent spec, so a
    # mutation of the row on the turn path cannot reach both sides.
    with (
        patch(
            "app.worker.tasks.runtime.agent.build_tool_server",
            side_effect=_tool_server_marker,
        ),
        patch(
            "app.worker.tasks.runtime.agent.build_system_prompt",
            side_effect=_system_prompt_marker,
        ),
        patch(
            "app.worker.tasks.runtime.agent.ClaudeAgentOptions",
            side_effect=_RecordingOptions,
        ),
    ):
        reference = build_agent_options(
            agent=_agent_from(pristine_spec),
            conn_str=_CONN_STR,
            conversation_id=local_conv_id,
            job_id=job_id,
            side_effects="live",
            verified_session_token=_VERIFIED_TOKEN,
            soul_override=dict(_SOUL_OVERRIDE),
            resume=None,
        )

    # Named first, so a failure says which behaviour moved rather than only that
    # something did.
    assert served.model == AGENT_TURN_MODEL == reference.model
    assert served.max_turns == reference.max_turns == 6
    assert served.max_budget_usd == reference.max_budget_usd == settings.AGENT_MAX_BUDGET_USD
    assert served.allowed_tools == reference.allowed_tools == EXPECTED_ALLOWED_TOOLS
    assert served.system_prompt == reference.system_prompt, (
        "the system prompt the customer was served is not the one an eval "
        "rebuilding from the same agent row would get.\n"
        f"  served:    {served.system_prompt}\n"
        f"  reference: {reference.system_prompt}\n"
        "Either the agent row was mutated on the turn path before the seam read "
        "it, or an input that selects the prompt (soul_override — the OPS-16 "
        "canary) was not threaded through."
    )
    assert served.mcp_servers == reference.mcp_servers, (
        "the tool server the customer was served was built from different "
        "inputs than an eval would use.\n"
        f"  served:    {served.mcp_servers}\n"
        f"  reference: {reference.mcp_servers}\n"
        "The tool server is where the capability envelope and the IDV-05 "
        "verified-session token take effect, so this is a capability difference, "
        "not a cosmetic one."
    )

    # …then the sweep, so a field nobody thought to name still cannot drift.
    served_snapshot = served.snapshot()
    reference_snapshot = reference.snapshot()
    assert set(served_snapshot) == set(reference_snapshot), (
        "the two builds passed different SETS of kwargs to the options "
        f"constructor.\n  served:    {sorted(served_snapshot)}\n"
        f"  reference: {sorted(reference_snapshot)}"
    )
    mismatches = [
        f"{name}: served={served_snapshot[name]!r} reference={reference_snapshot[name]!r}"
        for name in sorted(served_snapshot)
        if repr(served_snapshot[name]) != repr(reference_snapshot[name])
    ]
    assert mismatches == [], (
        "the options object the SDK turn received differs from one built "
        f"independently from the same inputs: {mismatches}. Something on the "
        "turn path changed the agent after the seam defined it, so the eval and "
        "the customer are no longer served the same agent."
    )


def test_the_seam_receives_the_turn_s_own_inputs():
    """A seam called with the wrong arguments is drift with extra steps.

    All seven of the seam's parameters are checked, because the two that were
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

    `resume` remains the one that turns a follow-up into a fresh conversation.
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
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={
                "id": existing_conv_id,
                "metadata": {
                    "sdk_session_id": stored_session,
                    "prompt_version_id": _PROMPT_VERSION_ID,
                },
            },
        ),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="assistant-msg-id-seam-2",
        ),
        patch(
            "app.worker.tasks.runtime.agent._resolve_turn_prompt_version",
            return_value=(_PROMPT_VERSION_ID, dict(_SOUL_OVERRIDE), False),
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
    assert kwargs.get("resume") == stored_session, (
        "resume= must carry the conversation's stored sdk_session_id into the "
        f"seam; got {kwargs.get('resume')!r}. Without it every follow-up turn "
        "starts a new SDK session and the agent loses the conversation."
    )
    assert kwargs.get("job_id") == job_id
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
        "resume",
    }, (
        f"the seam's call kwargs changed: {sorted(kwargs)}. Every parameter is "
        "asserted above; a new one arriving unasserted is a new behaviour input "
        "with no test, which is how the two above went unchecked."
    )


def test_build_agent_options_assembles_the_full_contract():
    """The seam tested directly, as the standalone callable P2 imports.

    Every other test here either reads the seam's source or patches it out, so
    nothing pinned what it actually returns under inputs `run_agent_turn` never
    supplies — an explicit `soul_override`, a non-empty `verified_session_token`,
    a conversation id the caller chose. The eval supplies exactly those.
    """
    from app.core.config import AGENT_TURN_MODEL, settings
    from app.worker.tasks.runtime.agent import build_agent_options

    agent = _make_agent()
    conv_id = "00000000-0000-0000-0000-0000000000cc"
    job_id = str(uuid.uuid4())

    with (
        patch(
            "app.worker.tasks.runtime.agent.build_tool_server",
            side_effect=_tool_server_marker,
        ) as mock_tools,
        patch(
            "app.worker.tasks.runtime.agent.build_system_prompt",
            side_effect=_system_prompt_marker,
        ) as mock_prompt,
        patch(
            "app.worker.tasks.runtime.agent.ClaudeAgentOptions",
            side_effect=_RecordingOptions,
        ),
    ):
        options = build_agent_options(
            agent=agent,
            conn_str=_CONN_STR,
            conversation_id=conv_id,
            job_id=job_id,
            side_effects="live",
            verified_session_token=_VERIFIED_TOKEN,
            soul_override=dict(_SOUL_OVERRIDE),
            resume="resume-me",
        )

    assert isinstance(options, _RecordingOptions), (
        "build_agent_options did not construct its options through the "
        "ClaudeAgentOptions name in its own module namespace."
    )
    assert options.model == AGENT_TURN_MODEL
    assert options.max_turns == 6, (
        "max_turns is not 6. D-10 raised it from 3 because 3 cut the agent off "
        "after the retrieve round-trip and left response_text empty; the eval "
        "would then score a 6-turn agent against a 3-turn production one."
    )
    assert options.max_budget_usd == settings.AGENT_MAX_BUDGET_USD
    assert options.resume == "resume-me"
    assert options.allowed_tools == EXPECTED_ALLOWED_TOOLS, (
        "the capability surface changed. Expected exactly these 11 tools, in "
        f"this order: {EXPECTED_ALLOWED_TOOLS}. Six of them move money or state."
    )
    assert len(options.allowed_tools) == 11
    assert MUTATING_SKILLS <= set(options.allowed_tools), (
        "the seam no longer grants the mutating transactional skills. If that "
        "is a deliberate narrowing, good — but it changes what production can "
        "do, so update EXPECTED_ALLOWED_TOOLS and say so."
    )
    assert set(options.mcp_servers) == {"customer-tools"}, (
        "the MCP server key must stay 'customer-tools' — it is one third of a "
        "three-way name agreement with the mcp__customer-tools__ prefixes in "
        "allowed_tools and the server the SDK resolves."
    )

    mock_prompt.assert_called_once()
    assert mock_prompt.call_args.args[0] is agent
    assert mock_prompt.call_args.kwargs.get("soul_override") == _SOUL_OVERRIDE

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
    """Build options through the real seam with the collaborators stubbed out."""
    from app.worker.tasks.runtime.agent import build_agent_options

    kwargs = {
        "agent": _make_agent(),
        "conn_str": _CONN_STR,
        "conversation_id": "00000000-0000-0000-0000-0000000000ff",
        "job_id": str(uuid.uuid4()),
        "verified_session_token": _VERIFIED_TOKEN,
        "soul_override": dict(_SOUL_OVERRIDE),
        "resume": None,
    }
    kwargs.update(overrides)

    with (
        patch(
            "app.worker.tasks.runtime.agent.build_tool_server",
            side_effect=_tool_server_marker,
        ) as mock_tools,
        patch(
            "app.worker.tasks.runtime.agent.build_system_prompt",
            side_effect=_system_prompt_marker,
        ),
        patch(
            "app.worker.tasks.runtime.agent.ClaudeAgentOptions",
            side_effect=_RecordingOptions,
        ),
    ):
        options = build_agent_options(**kwargs)
    return options, mock_tools, kwargs["agent"]


def test_the_seam_refuses_to_build_without_a_side_effects_mode():
    """NO DEFAULT. This is the entire mechanism, and it is one word wide.

    Give `side_effects` a default of "live" and every guard in both files stays
    green while P2's eval — written months from now by someone reading the
    signature, not the plan — quietly issues real refunds against a real
    tenant's provider. A default is not a convenience here, it is the failure
    mode: the caller who most needs to think about this question is exactly the
    one who would never be asked it.

    TypeError from Python's own binding, rather than a runtime check, because it
    fires at the call site with the parameter's name in it.
    """
    from app.worker.tasks.runtime.agent import build_agent_options

    with pytest.raises(TypeError, match="side_effects"):
        with (
            patch("app.worker.tasks.runtime.agent.build_tool_server",
                  side_effect=_tool_server_marker),
            patch("app.worker.tasks.runtime.agent.build_system_prompt",
                  side_effect=_system_prompt_marker),
            patch("app.worker.tasks.runtime.agent.ClaudeAgentOptions",
                  side_effect=_RecordingOptions),
        ):
            build_agent_options(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="00000000-0000-0000-0000-00000000ffff",
                job_id=str(uuid.uuid4()),
            )


def test_the_seam_rejects_a_mode_it_does_not_implement():
    """`Literal` is a type annotation and stops nothing at run time.

    `side_effects="dry_run"` is the plausible mistake — it is what this
    parameter is called in most codebases — and a bare `== "recorded"` check
    would read it as live and move real money on the eval path. Fail loudly on
    the third value rather than silently on the safe-looking one.

    Both collaborators are patched out, and `match=` pins the seam's OWN message.
    Neither is fussiness. The first version of this test called the real
    `build_tool_server`, which has the same check one layer down — so it was
    green with the seam's `raise` deleted, and its mutation proof said so:

        M2 seam drops the unknown-mode ValueError
          RED:   1 passed in 10.57s

    It was demonstrating the tool layer's guard while claiming to demonstrate
    the seam's. The seam's check is not redundant with that one — it fires
    BEFORE `build_tool_server` sets any per-task ContextVar or the system prompt
    is assembled, and it names `build_agent_options`, which is where the caller
    made the mistake — but a test cannot prove a guard it never reaches.
    """
    from app.worker.tasks.runtime.agent import build_agent_options

    with (
        patch("app.worker.tasks.runtime.agent.build_tool_server",
              side_effect=_tool_server_marker),
        patch("app.worker.tasks.runtime.agent.build_system_prompt",
              side_effect=_system_prompt_marker),
        patch("app.worker.tasks.runtime.agent.ClaudeAgentOptions",
              side_effect=_RecordingOptions),
    ):
        with pytest.raises(ValueError, match="build_agent_options: side_effects"):
            build_agent_options(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="00000000-0000-0000-0000-00000000fffe",
                job_id=str(uuid.uuid4()),
                side_effects="dry_run",
            )


def test_recorded_mode_grants_exactly_the_same_capability_surface_as_live():
    """THE REJECTED-ALTERNATIVE PIN. The reason recorded mode exists at all.

    The other way to stop an eval refund was to hand the eval a read-only
    `allowed_tools` subset. The owner rejected it because it makes the sentence
    *"the agent should have refused to refund here"* unfalsifiable: the scenario
    could no longer FAIL, since the agent could not attempt the thing it was
    supposed to refuse. An agent that cannot do the wrong thing tells you
    nothing by not doing it.

    That rejection is only durable if something notices the day someone
    "hardens" recorded mode by trimming the tool list — which reads like an
    improvement, passes every money-related guard in the suite, and silently
    turns a whole class of eval scenario into a tautology. This is that
    something. Every behaviour-determining kwarg is compared, not just
    allowed_tools, because the same reasoning applies to max_turns, the budget
    ceiling and the system prompt.
    """
    live, _, _ = _build(side_effects="live")
    recorded, _, _ = _build(side_effects="recorded")

    assert recorded.allowed_tools == live.allowed_tools == EXPECTED_ALLOWED_TOOLS
    assert MUTATING_SKILLS <= set(recorded.allowed_tools), (
        "recorded mode no longer grants the six mutating skills. That looks "
        "like safety and is the opposite: an eval agent that cannot attempt a "
        "refund cannot be scored on refusing one, so every capability-envelope "
        "scenario silently becomes unfalsifiable. Recorded mode's job is to "
        "make the attempt harmless, never to prevent it."
    )

    live_snapshot = live.snapshot()
    recorded_snapshot = recorded.snapshot()
    assert set(live_snapshot) == set(recorded_snapshot)
    differences = [
        name
        for name in sorted(live_snapshot)
        # mcp_servers legitimately differs: it is the one field the mode is
        # supposed to reach, and _tool_server_marker keys on it.
        if name != "mcp_servers" and repr(live_snapshot[name]) != repr(recorded_snapshot[name])
    ]
    assert differences == [], (
        f"recorded mode changed what the agent sees or can choose: {differences}. "
        "Only the outer edge may differ between the two modes — the eval must "
        "measure the agent production serves, not a quieter one."
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
        "build_tool_server. The mode has no effect anywhere else."
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
        patch("app.worker.tasks.runtime.agent.send_escalation_email") as mock_mail,
        patch("app.worker.tasks.runtime.agent.record_suppressed_side_effect") as mock_record,
    ):
        notify_fn("customer asked for a human", "three failed lookups")

    mock_mail.assert_called_once_with(
        agent, "customer asked for a human", "three failed lookups"
    )
    mock_record.assert_not_called()


def test_recorded_mode_records_the_escalation_instead_of_sending_it():
    """The escalation edge, which the value comparison structurally cannot see.

    `notify_fn` is a closure, so two builds never produce equal values and it is
    excluded from `test_the_options_served_match_an_independently_built_reference`'s
    sweep by design. That leaves it as the one behaviour-determining input in
    the seam with no coverage — and an eval that escalates would page a real
    owner about a customer who does not exist, nightly, for as long as the
    scenario stays in the golden set.
    """
    _, mock_tools, agent = _build(side_effects="recorded")
    notify_fn = mock_tools.call_args.kwargs["notify_fn"]

    with (
        patch("app.worker.tasks.runtime.agent.send_escalation_email") as mock_mail,
        patch("app.worker.tasks.runtime.agent.record_suppressed_side_effect") as mock_record,
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


def test_the_canary_choice_is_not_committed_when_the_options_build_fails():
    """RESOLVE BEFORE, COMMIT AFTER — the settled answer to BACKLOG 2.6.

    This test replaces `test_the_canary_choice_is_committed_before_the_options_can_fail`,
    which pinned P1's behaviour and was written to be inverted the moment the
    question was settled. The history it records:

    P1 moved `_resolve_turn_prompt_version` ahead of the seam, because the soul
    fields it resolves are an input to the system prompt the seam builds. That
    move was correct and stays. But the helper also CALLED
    `_set_prompt_version_id`, which commits to `conversations.metadata` — so the
    write moved forward with the read, and a turn that then died in
    `RetrievalStrategy.model_validate` or `build_tool_server` left the
    conversation permanently sticky to a prompt version that never served it.
    Before P1 the Celery retry re-rolled. A canary whose denominator counts
    conversations it never spoke in is a canary reporting on a population it
    does not have.

    Settled by the owner on 2026-08-07: the resolution stays where P1 put it,
    the WRITE moves back behind a successful `build_agent_options`. The
    conversation becomes sticky only once there is an agent to be sticky to.
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
            "app.worker.tasks.runtime.agent.build_agent_options",
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
            "the canary choice was committed even though the options build "
            f"failed (call_count={mock_commit.call_count}). The conversation is "
            "now sticky to a prompt version that never served a turn, and the "
            "Celery retry can no longer re-roll it. Move the "
            "_set_prompt_version_id call back behind a successful "
            "build_agent_options — BACKLOG 2.6, settled 2026-08-07."
        )


def test_the_canary_choice_is_committed_once_the_options_exist():
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

    async def fake_sdk_turn(**kwargs):
        order.append("sdk_turn")
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
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
            "app.worker.tasks.runtime.agent.build_agent_options",
            side_effect=lambda **kwargs: (order.append("seam"), _SeamSentinel())[1],
        ),
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
        "that dies in options-building would again leave the conversation "
        "sticky to a version that never served it."
    )


def test_a_failed_canary_commit_never_fails_the_turn():
    """T-21-09-05's tenant-DB half, which had no test anywhere.

    Moving the canary WRITE behind the seam put it on the turn's critical path
    for the first time, and it writes to the TENANT database — a Neon cold start
    or a dropped connection is the ordinary case, not the exotic one. Unwrapped,
    it would fail a customer turn whose answer had already been produced. The
    `try/except` in `run_agent_turn` is what stops that, and deleting it left
    the whole suite green: the pre-existing test that covered a tenant-DB
    failure (`..._never_raises_on_bad_db`) was rewritten during P1b to cover a
    CONTROL-DB failure instead, so this half lost its only coverage and the
    replacement's docstring asserted it in prose.

    What must survive the failure: the SDK turn runs, the turn returns, and the
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

    async def fake_sdk_turn(**kwargs):
        ran.append("sdk_turn")
        return dict(_CANNED_TURN_RESULT)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=_CONN_STR),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
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
            "app.worker.tasks.runtime.agent.build_agent_options",
            side_effect=lambda **kwargs: _SeamSentinel(),
        ),
        patch("app.worker.tasks.runtime.agent._run_sdk_turn", side_effect=fake_sdk_turn),
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
    assert ran == ["sdk_turn"], (
        "the SDK turn never ran: a tenant-DB failure on the canary write killed "
        "a turn the customer is waiting on. The write is bookkeeping about "
        "which prompt version served the turn; losing it costs stickiness, and "
        "the next turn of this conversation re-rolls. Losing the turn costs the "
        "answer."
    )
    assert isinstance(result, dict)
    warned = [c.args[0] for c in mock_warn.call_args_list if c.args]
    assert "run_agent_turn.prompt_version_persist_failed" in warned, (
        "the canary-write failure was swallowed without a log line. A silent "
        "except is how a conversation stops being sticky and nobody finds out "
        f"until the canary's denominator is wrong. Warnings logged: {warned}"
    )


@pytest.mark.parametrize("failing_step", ["strategy", "tool_server"])
def test_a_failed_seam_leaves_the_side_effect_mode_at_the_safe_default(failing_step):
    """The mode is process-context sticky and nothing resets it between tasks.

    Celery's prefork pool does not isolate contextvars per task. Once an eval
    task sets the mode to "recorded" it stays set in that worker's context until
    something calls `build_tool_server` again — and the entire safety argument
    for the `"live"` default rested on the untested claim that every path that
    reaches the tools does. `build_agent_options` raises above
    `build_tool_server` in three places (its own validation, the
    `RetrievalStrategy` parse, and `build_tool_server` itself), so a stale
    "recorded" could survive into whatever ran next in that context: a customer
    turn that silently stops refunding, with no error anywhere, found by a
    customer rather than by us.

    Two changes close it, and this test drives both. `build_agent_options`
    resets to the safe default FIRST, before anything that can throw; and
    `build_tool_server` publishes the mode LAST, after `create_sdk_mcp_server`,
    rather than before it. `strategy` dies before `build_tool_server` and
    `tool_server` dies inside it — on opposite sides of where the mode used to
    be published, so neither change alone makes both cases pass.
    """
    import app.services.agent_tools as agent_tools
    from app.worker.tasks.runtime.agent import build_agent_options

    token = agent_tools._side_effects_var.set("recorded")
    try:
        # The expected exception is matched by MESSAGE, not by `Exception`. A
        # bare `pytest.raises(Exception)` swallows a NameError from a missing
        # import and the seam is never called at all — which is exactly how the
        # first draft of this test passed the reset assertion for no reason.
        if failing_step == "strategy":
            failure = patch(
                "app.worker.tasks.runtime.agent.RetrievalStrategy.model_validate",
                side_effect=ValueError("malformed retrieval_strategy"),
            )
            expected: tuple = (ValueError, "malformed retrieval_strategy")
        else:
            failure = patch(
                "app.services.agent_tools.create_sdk_mcp_server",
                side_effect=RuntimeError("mcp server could not be built"),
            )
            expected = (RuntimeError, "mcp server could not be built")

        with failure, pytest.raises(expected[0], match=expected[1]):
            build_agent_options(
                agent=_make_agent(),
                conn_str=_CONN_STR,
                conversation_id="conv-stale-mode",
                job_id="job-stale-mode",
                side_effects="recorded",
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
