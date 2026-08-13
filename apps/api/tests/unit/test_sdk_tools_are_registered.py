"""Every SDK agent that is told to call a tool must actually be given it.

BACKLOG `1.32`. Audit defect **D4** was "5 of 7 red-team attackers were never
given their tools, so they reported clean". It was found and fixed in
`red_team_service`. **The identical defect was still live in
`deployment_service`**, and E2E-4 — the first deployment checklist ever
executed — is what surfaced it:

    run_deployment_checklist.no_report
    run_deployment_checklist.failed  error='Orchestrator did not produce a report'

`_TOOL_SUBMIT_REPORT` was referenced *exactly once in the whole repository*: its
own definition. It was never passed to `ClaudeAgentOptions`, and the module
contained no `create_sdk_mcp_server`, no `mcp_servers`, no `allowed_tools`. The
orchestrator was instructed to "call submit_report" holding no such tool, so it
could never emit that block and **every checklist run failed, always**.

Why this is a module-level scan and not a test of one function
--------------------------------------------------------------
`1.14`'s lesson: when a row names one call site, pin the *shape*. A tool schema
defined at module scope and never wired is invisible to every test that drives
the loop with a fake SDK, because the fake accepts any options object. The
check below is therefore structural — it reads the source for the wiring — and
covers **both** SDK services, so the next module to grow an agent inherits the
guard instead of the bug.

`3.7` names the residual risk this closes: nothing pinned "the three-way name
agreement (`create_sdk_mcp_server` ↔ `mcp_servers` key ↔ `mcp__{name}__`
prefix)".
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[2] / "app" / "services"

#: Modules that construct a `ClaudeAgentOptions` for an agent expected to call
#: an in-process tool. Add a module here when it grows one.
SDK_TOOL_SERVICES = ["deployment_service.py", "red_team_service.py"]


def _source(name: str) -> str:
    return (SERVICES / name).read_text(encoding="utf-8")


def test_the_services_exist():
    """Guards the guard: a wrong path makes every scan below vacuous."""
    for name in SDK_TOOL_SERVICES:
        assert (SERVICES / name).is_file(), f"{SERVICES / name} not found"


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_every_module_scope_tool_schema_is_actually_registered(module: str):
    """A `_TOOL_*` dict defined and never referenced again is a dead contract.

    This is the exact signature of the bug: the schema exists, the prompt names
    the tool, and nothing ever hands it to the SDK.
    """
    source = _source(module)
    tree = ast.parse(source, filename=module)

    schema_names = [
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and re.fullmatch(r"_TOOL_[A-Z_]+", target.id)
    ]
    assert schema_names, f"{module} declares no _TOOL_* schema; update this test"

    for schema in schema_names:
        # Count references outside the assignment itself.
        uses = len(re.findall(rf"\b{re.escape(schema)}\b", source))
        assert uses > 1, (
            f"{module}: {schema} is defined and never used again — it is a tool "
            "schema the agent is never given. That is BACKLOG 1.32 / audit D4: "
            "the prompt tells the agent to call it, the SDK never offers it, and "
            "the loop waits forever for a block that cannot arrive."
        )


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_the_sdk_wiring_is_present(module: str):
    """`create_sdk_mcp_server` → `mcp_servers` → `allowed_tools`, all three."""
    source = _source(module)
    for marker in ("create_sdk_mcp_server", "mcp_servers=", "allowed_tools="):
        assert marker in source, (
            f"{module} constructs an SDK agent without `{marker}`. An agent with "
            "no registered tools cannot call one, and the failure is silent — "
            "the loop simply never sees the block it is waiting for."
        )


@pytest.mark.parametrize("module", SDK_TOOL_SERVICES)
def test_the_allowlist_uses_the_mcp_qualified_name(module: str):
    """The SDK rewrites tool names to `mcp__{server}__{tool}`.

    An allowlist naming the bare tool authorises a name the SDK never emits,
    which fails in exactly the same silent way.
    """
    source = _source(module)
    assert 'f"mcp__{' in source or "mcp__" in source, (
        f"{module} never spells the mcp__ prefix, so its allowlist cannot match "
        "the qualified names the SDK actually emits (BACKLOG 3.7)."
    )


def test_the_orchestrator_matches_the_qualified_block_name():
    """The specific regression: the loop must accept the prefixed name.

    Registering the tool but still matching the bare name reproduces the same
    "no report" outcome one layer later.
    """
    source = _source("deployment_service.py")
    assert "SUBMIT_REPORT_TOOL_NAME" in source
    loop_start = source.index("async def _run_orchestrator_loop")
    loop = source[loop_start : loop_start + 3000]
    assert "SUBMIT_REPORT_TOOL_NAME" in loop, (
        "the orchestrator loop does not compare against the mcp__-qualified "
        "name, so a correctly registered tool would still be ignored"
    )
