"""Every agent told to call a tool must actually be handed it.

BACKLOG `1.32`, hardened under `1.33`. Audit defect **D4** was "5 of 7 red-team
attackers were never given their tools, so they reported clean". It was fixed in
`red_team_service` and the identical defect was still live in
`deployment_service`, where `_TOOL_SUBMIT_REPORT` was passed to nothing and the
orchestrator could never emit the block the loop waited for.

Ticket #49 moved both agents off `claude_agent_sdk` and onto
`app.services.tool_loop.run_tool_loop`, so the SDK wiring those checks read is
gone. The DEFECT is not gone. `run_tool_loop` sends the tool list it is handed
and nothing else, `dispatch` refuses a name outside that list, and a caller that
names a tool in its prompt and passes no `tools=` reproduces D4 exactly: the
model is told to call something the loop will not offer, so it talks instead,
and the caller reads an empty report as a clean result. The checks below ask
that successor question.

`tools` is also the whole allowlist now. `run_tool_loop`'s docstring records why:
the SDK's `allowed_tools`, `mcp_servers`, `tools=[]` and `strict_mcp_config`
existed to fence in a default toolset the owned loop never has, so the tool list
and the capability grant became one object. A short `tools=` argument is
therefore a capability decision, which is why check three pins WHO may make one.

Why this scans the AST and not the source text
----------------------------------------------
**The first version of this module was green on the exact bug it was written
for.** An adversarial review stripped the tool wiring out of
`deployment_service`, left the comments in place, and observed **1 failed, 7
passed**. Three of its four checks were satisfied by the implementer's own prose:

- the "is this schema ever referenced again?" count was satisfied by a docstring
  sentence *stating* that the schema had only ever been referenced once;
- the tool-server marker survived on the `import` line and in that same
  docstring;
- the allowlist check was satisfied by a comment explaining how tool names were
  rewritten.

A substring scan over a file cannot distinguish code from a sentence about the
code, and these modules carry long explanatory comments. So every check below
walks `ast` and looks at call nodes and keyword arguments only. Docstrings and
comments are invisible to it by construction.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: The function every bounded agent conversation in this repo runs on.
LOOP = "run_tool_loop"

#: Modules permitted to run a tool loop anywhere under `app/`, as an exact set.
#:
#: Two names, measured by scanning the tree rather than by reading an import.
#: `deployment_service.py` runs the deployment Orchestrator and
#: `red_team_service.py` runs the red-team Attacker. Neither is the customer
#: agent, which runs its own loop in `app/services/agent_loop.py` because it
#: carries an SSE stream, an escalation ledger and a spend ceiling that an
#: attacker has no use for.
#:
#: A third entry is a third agent, and `tools=` is that agent's entire
#: capability grant. Adding a line here is a deliberate act with a reviewer
#: attached, which is what a set pin buys over a per-file scan: a new caller
#: fails this suite on the diff that introduces it, rather than passing every
#: check in the file because each one only ever looked at the two modules it
#: already knew about.
PINNED_TOOL_LOOP_CALLERS = {
    "app/services/deployment_service.py",
    "app/services/red_team_service.py",
}


def _tree(module: str) -> ast.Module:
    return ast.parse((APP.parent / module).read_text(encoding="utf-8"), filename=module)


def _calls(tree: ast.Module, func_name: str) -> list[ast.Call]:
    """Every Call node invoking `func_name`, by bare name or attribute."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if name == func_name:
            out.append(node)
    return out


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    """The named keyword argument of one call, or None when it carries none."""
    return next((kw for kw in call.keywords if kw.arg == name), None)


def _names_used_in_code(tree: ast.Module) -> list[str]:
    """Every identifier READ in executable code. Excludes its own assignment."""
    used = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.append(node.id)
    return used


@pytest.mark.parametrize("module", sorted(PINNED_TOOL_LOOP_CALLERS))
def test_each_pinned_caller_exists_and_runs_a_tool_loop(module: str):
    """Guards the guard: a wrong path or a moved call makes every scan vacuous.

    The two checks below parametrize over `PINNED_TOOL_LOOP_CALLERS` and read the
    calls they find. A module that no longer calls `run_tool_loop` gives them
    nothing to look at, and both then pass by finding no offence rather than by
    finding a tool list.
    """
    path = APP.parent / module
    assert path.is_file(), f"{path} not found; PINNED_TOOL_LOOP_CALLERS is stale"
    assert _calls(_tree(module), LOOP), (
        f"{module} no longer calls {LOOP}. Either the agent moved and this pin "
        "must move with it, or the agent was deleted and the line comes out. "
        "Leaving it here makes the checks below scan a module with nothing in it."
    )


@pytest.mark.parametrize("module", sorted(PINNED_TOOL_LOOP_CALLERS))
def test_every_tool_loop_call_is_given_its_tools(module: str):
    """A `run_tool_loop` call with no `tools=` is BACKLOG 1.32 exactly.

    `tools` has no default. Python raises `TypeError` on a call that omits it,
    so this check earns its place on the calls the interpreter never reaches:
    the error branch, the second vector, the retry path, the call added behind a
    flag nobody drives in a test. That is the shape D4 took. Five of the seven
    attackers were on paths no test ran, so five agents ran with no tools and
    five vectors reported clean.
    """
    for call in _calls(_tree(module), LOOP):
        assert _keyword(call, "tools") is not None, (
            f"{module} line {call.lineno} calls {LOOP} without `tools=`. The "
            "agent is told in its prompt to call a tool that the loop will not "
            "offer it, so it answers in prose, `dispatch` is never reached, and "
            "the caller reads a container that stayed empty as a clean result. "
            "BACKLOG 1.32 / audit D4."
        )


@pytest.mark.parametrize("module", sorted(PINNED_TOOL_LOOP_CALLERS))
def test_no_tool_loop_call_spells_its_tool_list_at_the_call_site(module: str):
    """The tool set arrives from a builder or a name, never as a literal.

    `tools=[]` is D4 in one line: the loop offers nothing and the agent reports
    clean. Any other literal is the slower version of the same defect. The tools
    a builder returns carry the handlers that write the report, send the probe
    and move money, and a list written at the call site is a second copy of that
    capability grant that no test of the builder can see. It drifts, and the
    agent an eval measures stops being the agent the task runs.

    Checked on the argument's syntax, because a literal is the one shape that
    cannot be pointed at any other way.
    """
    literals = (ast.List, ast.Tuple, ast.Set)
    for call in _calls(_tree(module), LOOP):
        keyword = _keyword(call, "tools")
        if keyword is None:
            continue  # test_every_tool_loop_call_is_given_its_tools owns that
        assert not isinstance(keyword.value, literals), (
            f"{module} line {call.lineno} writes its tool list out at the "
            f"{LOOP} call site as `tools={ast.unparse(keyword.value)}`. That "
            "list IS the agent's capability grant, so a second copy of it lives "
            "here now, out of reach of every test that drives the builder. An "
            "empty one hands the agent no tools at all and it reports clean. "
            "Pass the builder's result or a name bound to it."
        )


def test_only_pinned_modules_run_a_tool_loop():
    """Repo-wide, because a new agent is what this set exists to catch.

    Every other check here reads the modules the set already names, which proves
    each of those is wired correctly. A third module that calls `run_tool_loop`
    with no tools passes all of them, since none of them ever opens it.
    """
    found: dict[str, int] = {}
    for path in sorted(APP.rglob("*.py")):
        key = path.relative_to(APP.parent).as_posix()
        count = len(_calls(_tree(key), LOOP))
        if count:
            found[key] = count

    assert set(found) == PINNED_TOOL_LOOP_CALLERS, (
        "the set of modules running a tool loop changed.\n"
        f"  found:   {sorted(found)}\n"
        f"  allowed: {sorted(PINNED_TOOL_LOOP_CALLERS)}\n"
        "A new one is a new agent, and its `tools=` argument is the whole set of "
        "capabilities it holds. Add it here on purpose, so the checks above scan "
        "it too."
    )


@pytest.mark.parametrize("module", sorted(PINNED_TOOL_LOOP_CALLERS))
def test_every_module_scope_tool_schema_is_read_by_executable_code(module: str):
    """A `_TOOL_*` dict defined and never READ is a dead contract.

    Counted over `ast.Name` loads, not over source occurrences. A docstring
    mentioning the constant is not a use of it, and the previous version of this
    test was satisfied by exactly such a sentence.
    """
    tree = _tree(module)
    schemas = [
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and t.id.startswith("_TOOL_")
    ]
    assert schemas, f"{module} declares no _TOOL_* schema; update this test"

    loads = _names_used_in_code(tree)
    for schema in schemas:
        assert schema in loads, (
            f"{module}: {schema} is defined and never read by any executable "
            "statement. It is a tool schema no builder turns into a tool, so "
            "the prompt names the tool, the loop never offers it, and the caller "
            "waits for a block that cannot arrive. BACKLOG 1.32 / audit D4."
        )


def test_the_orchestrator_stops_on_a_name_its_tool_list_actually_carries():
    """BACKLOG 3.7's three-way agreement, on the owned loop.

    Under the SDK the three names were the registered tool, the `allowed_tools`
    entry and the `mcp__{server}__{tool}` spelling the SDK emitted, and 3.7
    records them drifting. Two of the three survive. `_run_orchestrator_loop`
    passes `stop_after={SUBMIT_REPORT_TOOL_NAME}`, and `build_report_tools` names
    the tool it registers. `_run_one_call` ends the loop by comparing the name
    the model called against `stop_after`, so a stop name no registered tool
    carries means the stop never fires. The orchestrator talks until
    `ORCHESTRATOR_MAX_TURNS`, spending a tenant's money on turns nobody reads.

    Read as a VALUE off the list the orchestrator is really handed, by BUILDING
    it. Comparing `SUBMIT_REPORT_TOOL_NAME` against `_TOOL_SUBMIT_REPORT["name"]`
    reads like the same check and proves nothing, because the dict is assigned
    from the constant and the two cannot disagree. That version was written here
    first and stayed GREEN under a rename of the constant. Its successor asks
    `build_report_tools` what it actually registered.
    """
    from app.services.deployment_service import (
        SUBMIT_REPORT_TOOL_NAME,
        build_report_tools,
    )

    registered = [definition.name for definition in build_report_tools({})]
    assert SUBMIT_REPORT_TOOL_NAME in registered, (
        f"the orchestrator loop stops on {SUBMIT_REPORT_TOOL_NAME!r} and the "
        f"tools it is handed are called {registered}. The stop can never fire, "
        "so the loop runs to its turn ceiling on every deployment"
    )


def test_the_orchestrator_loop_reads_the_stop_name_constant():
    """Registering the tool and then stopping on a literal reproduces 1.32.

    Read off the AST of the loop: the shared constant must appear as a value the
    function reads. A hand-written `stop_after=frozenset({"submit_report"})` is
    a second spelling of the tool's name, and the check above can no longer see
    the two halves drift apart.
    """
    tree = _tree("app/services/deployment_service.py")
    loop = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_run_orchestrator_loop"
        ),
        None,
    )
    assert loop is not None, "_run_orchestrator_loop not found"
    names = _names_used_in_code(ast.Module(body=loop.body, type_ignores=[]))
    assert "SUBMIT_REPORT_TOOL_NAME" in names, (
        "the orchestrator loop never reads SUBMIT_REPORT_TOOL_NAME, so whatever "
        "it stops on is a literal that no longer tracks the tool it is given"
    )
