"""Every SDK agent that is told to call a tool must actually be given it.

BACKLOG `1.32`, hardened under `1.33`. Audit defect **D4** was "5 of 7 red-team
attackers were never given their tools, so they reported clean". It was fixed in
`red_team_service` and the identical defect was still live in
`deployment_service`, where `_TOOL_SUBMIT_REPORT` was passed to nothing and the
orchestrator could never emit the block the loop waited for.

Why this scans the AST and not the source text
----------------------------------------------
**The first version of this module was green on the exact bug it was written
for.** An adversarial review stripped `create_sdk_mcp_server`, `mcp_servers=`,
`allowed_tools=`, `tools=[]` and `strict_mcp_config` from `deployment_service`,
left the comments in place, and observed **1 failed, 7 passed**. Three of its
four checks were satisfied by the implementer's own prose:

- the "is this schema ever referenced again?" count was satisfied by a docstring
  sentence *stating* that the schema had only ever been referenced once;
- the `create_sdk_mcp_server` marker survived on the `import` line and in that
  same docstring;
- the `mcp__` allowlist check was satisfied by a comment explaining the
  `mcp__{server}__{tool}` rewrite.

A substring scan over a file cannot distinguish code from a sentence about the
code, and these modules carry long explanatory comments. So every check below
walks `ast` and looks at call nodes and keyword arguments only. Docstrings and
comments are invisible to it by construction.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[2] / "app" / "services"

#: Modules that construct a `ClaudeAgentOptions` for an agent expected to call
#: an in-process tool. Add a module here when it grows one.
SDK_TOOL_SERVICES = ["deployment_service.py", "red_team_service.py"]


def _tree(module: str) -> ast.Module:
    return ast.parse((SERVICES / module).read_text(encoding="utf-8"), filename=module)


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


def _kwargs_of(tree: ast.Module, func_name: str) -> set[str]:
    """Keyword-argument names passed to any call of `func_name`."""
    return {
        kw.arg
        for call in _calls(tree, func_name)
        for kw in call.keywords
        if kw.arg is not None
    }


def _names_used_in_code(tree: ast.Module) -> list[str]:
    """Every identifier READ in executable code. Excludes its own assignment."""
    used = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.append(node.id)
    return used


def test_the_services_exist():
    """Guards the guard: a wrong path makes every scan below vacuous."""
    for name in SDK_TOOL_SERVICES:
        assert (SERVICES / name).is_file(), f"{SERVICES / name} not found"


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_every_module_scope_tool_schema_is_read_by_executable_code(module: str):
    """A `_TOOL_*` dict defined and never READ is a dead contract.

    Counted over `ast.Name` loads, not over source occurrences — a docstring
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
            "statement. It is a tool schema the agent is never given — the "
            "prompt names the tool, the SDK never offers it, and the loop waits "
            "forever for a block that cannot arrive. BACKLOG 1.32 / audit D4."
        )


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_an_mcp_server_is_actually_constructed(module: str):
    """`create_sdk_mcp_server` must be CALLED, not merely imported or described."""
    calls = _calls(_tree(module), "create_sdk_mcp_server")
    assert calls, (
        f"{module} never calls create_sdk_mcp_server. An import line and a "
        "comment about MCP wiring both satisfy a text search and neither "
        "registers a tool."
    )


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_the_options_receive_the_server_and_the_allowlist(module: str):
    """`ClaudeAgentOptions(...)` must carry the wiring keywords."""
    kwargs = _kwargs_of(_tree(module), "ClaudeAgentOptions")
    assert kwargs, f"{module} never constructs ClaudeAgentOptions"
    for required in ("mcp_servers", "allowed_tools"):
        assert required in kwargs, (
            f"{module} builds ClaudeAgentOptions without `{required}`. An agent "
            f"with no registered tools cannot call one, and it fails SILENTLY — "
            f"the loop simply never sees the block it waits for. got: {sorted(kwargs)}"
        )


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_the_agent_does_not_inherit_the_cli_builtins(module: str):
    """`tools=[]` removes Bash/Read/Edit on the worker's own filesystem.

    Not tidiness: `red_team_service` documents that an attacker persona is the
    worst process in this codebase to hand a shell to, and the orchestrator runs
    on the same worker.
    """
    kwargs = _kwargs_of(_tree(module), "ClaudeAgentOptions")
    for required in ("tools", "strict_mcp_config"):
        assert required in kwargs, (
            f"{module} builds ClaudeAgentOptions without `{required}`, so the "
            f"agent inherits the CLI's built-in toolset. got: {sorted(kwargs)}"
        )


def test_the_orchestrator_allowlist_is_the_mcp_qualified_name():
    """The SDK rewrites in-process tools to `mcp__{server}__{tool}`.

    Checked as a VALUE, computed from the module's own constants, not as a
    substring of the file — a comment explaining the rewrite is not a
    correctly-spelled allowlist entry.
    """
    from app.services.deployment_service import (
        DEPLOYMENT_MCP_SERVER_NAME,
        SUBMIT_REPORT_TOOL_NAME,
        _TOOL_SUBMIT_REPORT,
    )

    expected = f"mcp__{DEPLOYMENT_MCP_SERVER_NAME}__{_TOOL_SUBMIT_REPORT['name']}"
    assert SUBMIT_REPORT_TOOL_NAME == expected, (
        f"the allowlisted name {SUBMIT_REPORT_TOOL_NAME!r} is not the name the "
        f"SDK will emit ({expected!r}); the allowlist authorises a tool that "
        "never appears (BACKLOG 3.7's three-way agreement)"
    )


def test_the_orchestrator_loop_matches_the_qualified_name():
    """Registering the tool but matching the bare name reproduces 1.32.

    Read off the AST of the loop: the qualified constant must appear as a
    compared value inside `_run_orchestrator_loop`.
    """
    tree = _tree("deployment_service.py")
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
        "the orchestrator loop never reads SUBMIT_REPORT_TOOL_NAME, so it is "
        "comparing against the bare tool name the SDK does not emit"
    )
