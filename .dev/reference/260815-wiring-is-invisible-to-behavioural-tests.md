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

## The habit, corrected by an adversary pass the same day

The first version of this note said: pin the call site with an AST walk, asserting the symbol is
referenced outside its own definition and that the call site has the right shape. **That is not
enough, and the counter-example is this note's own worked example.**

An adversarial review reintroduced the `5.16` defect **five ways that all stayed green** against
exactly those two AST assertions:

| Reintroduction | Why the AST guard missed it |
|---|---|
| truncate into a differently named variable, then dump that | a different `Assign` node; the guard inspected the first one |
| move the truncation into a helper | no slice and no literal at the call site |
| `list(islice(contexts, JUDGE_CTX_CAP))` | `islice` is a `Call`, not an `ast.Slice`; the cap is a name |
| a second assignment to the same name on the next line | the walk returns the first match and stops |
| rebuild the old value while still calling the helper | the "is it called" check passed |

**A structural guard bans one spelling, and the author picks the spelling.** It is better than a text
scan and it is not a wiring check.

### What actually works: guard the argument the consumer receives

Extract a seam whose return value or call arguments a test can read, then assert on the value handed
over. For `5.16`, `_dispatch_validation_chain` builds the judge context and dispatches the chain; the
test patches `celery_chain` and `run_auditor` and reads `run_auditor.si.call_args`:

```python
with patch.object(agent_module, "celery_chain") as chain, \
     patch.object(agent_module, "run_auditor") as auditor:
    returned = agent_module._dispatch_validation_chain(...)
assert json.loads(auditor.si.call_args.args[4]) == what_the_agent_saw
assert returned == auditor.si.call_args.args[4]   # reported == dispatched
```

All five reintroductions fail against this, because all five change the value. The second assertion
matters on its own: without it, a change could return the honest value and dispatch a truncated one.

**Where an AST walk is still right** is the narrower question of existence: "this symbol is
registered", "no module references `settings.UPLOADS_DIR`", "these two names agree". Use it for
absence pins and registration checks (`1.32`, `1.26`), never as the only guard on a value.

**And when you do use one, walk the AST rather than scanning text.** `1.33` B4 is the record of a
check about code that a docstring sentence satisfied, so the module got *weaker* the more it was
documented. `ast.parse` makes comments invisible by construction.

## The test that tells you which kind you have

**Restore the original defect and run the module.** If the behavioural tests stay green, they were
never the guard. If the structural tests also stay green under a second spelling of the same defect,
they were a spelling check. Both took one mutation each to discover, and both were discovered by
someone other than the author.

## Where to look next

Modules worth this treatment, in the order they would hurt: `red_team_service` (D4 was found here
first, and `1.32` proves the fix did not generalise), `transactional/tools.py` (the capability
envelope rides entirely on `mcp__customer-tools__*` names agreeing across three places), `eval.py`.

For each, ask the seam question rather than the coverage question: **is there any test that observes
the value the next stage receives?** For `retrieved_context_json` the answer was no — it was built
inside a 400-line task body, and `validators.py`'s tests supplied their own string, so both ends were
tested and nothing joined them.

## Prior art in this repo

- `.dev/reference/260815-the-never-executed-class.md` — the six Phase A defects and why the tests
  could not see them.
- `.dev/reference/260815-adversary-phase-a.md` — eleven mutations, five vacuous guards.
- `.dev/reference/260815-judge-context-mutation-proofs.md` — the M1 proof this note is built on.
