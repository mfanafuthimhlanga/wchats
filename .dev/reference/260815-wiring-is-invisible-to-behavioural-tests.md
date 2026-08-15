# Wiring is invisible to behavioural tests

**Three times in one week, in three unrelated modules, a correct piece of code was not connected to
anything and every test that exercised it stayed green.** This note is here so the fourth one is
caught by a habit rather than by an outage.

## The three

| Where | What was defined | What was missing | What the suite said |
|---|---|---|---|
| `1.32` `deployment_service.py` | `_TOOL_SUBMIT_REPORT`, a correct tool schema | `create_sdk_mcp_server` / `mcp_servers` / `allowed_tools`, all absent | 2,206 green. The deployment checklist had **never** succeeded and could not |
| `1.33` B4 `test_sdk_tools_are_registered.py` | a check counting references to detect "defined and never used" | it counted references in **prose**; the implementer's own docstring was the second one | green on the very defect it was written for |
| `5.16` `agent.py` | `_judge_retrieved_context`, correct and fully tested | the dispatch site still built the context inline | **8 of 10 tests green with the whole defect restored** |

## Why the suite cannot see it

A behavioural test reaches the unit under test **by calling it**. That call is the thing production
was missing. The test supplies the wiring it is meant to be checking, so it passes for a reason that
has nothing to do with whether production is wired.

This is the same structure as retro Family I ("a mock is a claim about a boundary nobody was required
to evidence") one level up: **the call itself is the unevidenced claim.**

Coverage does not help. `_judge_retrieved_context` was at 100% line coverage while dead.

## The habit

**For anything defined at module scope that production must call, pin the call site, not the
behaviour.** Two assertions, both cheap:

1. The symbol is referenced somewhere other than its own definition.
2. The call site has the shape it must have (no cap reintroduced, no argument order swapped, the
   registered name matching the dispatched name).

**Walk the AST. Never scan the source as text.** `1.33` B4 is the record of what happens otherwise:
a check about code that a sentence of documentation satisfied, so the module got *weaker* the more it
was documented. `ast.parse` makes comments and docstrings invisible by construction.

Worked example, `tests/unit/test_judge_sees_agent_context.py`:

```python
tree = ast.parse(Path(agent_module.__file__).read_text(encoding="utf-8"))
def_lines = range(definition.lineno, definition.end_lineno + 1)
call_sites = [
    n for n in ast.walk(tree)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    and n.func.id == "_judge_retrieved_context" and n.lineno not in def_lines
]
assert call_sites
```

Excluding the definition's own line range is what makes it a wiring check rather than a
does-this-symbol-exist check.

## How to know your own module has this hole

Restore the original defect and run the module. **If the behavioural tests stay green, they were
never the guard.** That is the only reliable detector, and it takes one mutation.

Existing modules worth this treatment, in the order they would hurt: `red_team_service` (D4 was
found here first, and `1.32` proves the fix did not generalise), `transactional/tools.py` (the
capability envelope rides entirely on `mcp__customer-tools__*` names agreeing across three places),
`eval.py`.

## Prior art in this repo

- `.dev/reference/260815-the-never-executed-class.md` — the six Phase A defects and why the tests
  could not see them.
- `.dev/reference/260815-adversary-phase-a.md` — eleven mutations, five vacuous guards.
- `.dev/reference/260815-judge-context-mutation-proofs.md` — the M1 proof this note is built on.
