# 260827, the Attacker and the Orchestrator on the owned loop (#49)

Closes #49. ADR `docs/adr/0008-one-provider-owned-loop.md`, which named this ticket as the
one that finishes what #48 started. Branch `feat/sdk-removal`, PR #87, stacked on
`feat/agent-loop` (#48, PR #78) until that merges.

`claude_agent_sdk` leaves the process. The red-team Attacker, the deployment Orchestrator
and the red-team victim turn move onto the same owned loop the customer turn has run since
#48.

## What was actually broken

Not just a dependency. `red_team_service.py` and `deployment_service.py` both ran on
`SONNET_MODEL = "claude-sonnet-4-6"`, and commit `82d5db9` retired the Anthropic credential
on 2026-08-26. The Attacker and the Orchestrator could not make a model call at all. #49 is
what brings them back, on Luna through `PURPOSE_ROUTES`.

`deployment_orchestrator` had no route row before this. A checklist run could not report
what its own assessment cost.

## The shape

Two new modules, one deletion.

- `app/domain/tool_def.py`. `ToolDefinition` and a `tool` decorator, succeeding
  `claude_agent_sdk.tool` and `SdkMcpTool`. The SDK's decorator was a six-line constructor
  over a dataclass whose four fields are the four `_tools_wire` reads.
- `app/services/tool_loop.py`. The provider-shaped half of a bounded loop: `tools_wire`,
  `dispatch`, `tool_arguments`, `assistant_turn`, `first_choice`, `error_wire`, plus
  `run_tool_loop` for the two callers that are not a customer turn. `agent_loop` imports
  all six rather than owning them.
- `agent_tools.build_tool_server` deleted. Callers reach `bind_tool_context`.

## Decisions, and why

- **The split is by what the loops share, not by what looks reusable.** The SSE emit, the
  escalation ledger, the retrieval capture and the spend ceiling stay in `agent_loop`. The
  Attacker has no customer on a stream and no tenant row to write, so a loop general enough
  for both would carry branches for cases it never serves.
- **`build_tool_server` is gone rather than rewritten.** Its body was build the MCP server,
  then bind the ContextVars, and its docstring existed to justify that ordering: a server
  that raised must leave no side-effect mode behind for the next task in the worker's
  context. With no server there is no step between entry and the bind, so the hazard it
  guarded went with it.
- **The owned decorator refuses two things the SDK accepted.** A schema that is not a JSON
  Schema dict, because `tools_wire` sends it to the provider verbatim as
  `function.parameters` and the SDK's shorthand there surfaces as a model calling the tool
  wrongly rather than as an error. And a handler that is not `async def`, because
  `dispatch` awaits it inside a `try` that turns every exception into an error wire dict,
  so a sync handler would ship permanently broken and silent. Both now fail at import.
- **Four SDK controls have no counterpart and need none.** `tools=[]` removed the CLI's
  built-in Bash/Read/Edit set from a red-team agent on the worker's filesystem,
  `strict_mcp_config=True` refused a project `.mcp.json`, `allowed_tools` named the
  approved subset and `permission_mode="dontAsk"` denied the rest. `run_tool_loop` has no
  built-ins, no config file and no permission model: the `tools` argument is the entire set
  and `dispatch` refuses any name outside it. The allowlist and the tool list became the
  same object.
- **The MCP name prefix dies with the server.** `mcp__{server}__{tool}` rewriting is what
  forced `probe_tool_basename`, and what forced the Orchestrator to accept two spellings of
  `submit_report`. Names are bare now, and both mechanisms are gone.
- **`stop_after` claims the handler RAN, not that the name was called.** The implementing
  agent found two of the four ways that claim can be false, and the adversarial pass found
  the other two. See "The adversarial pass" below.
- **`_run_sdk_attacker` is now `_run_attacker`.** There is no SDK, and a name saying
  otherwise teaches the next reader something false.
- **`ANTHROPIC_BASE_URL` retired.** The SDK's CLI reads it to pick a wire format and this
  repo set it to `api.deepseek.com/anthropic`. ADR 0008 retired DeepSeek and #49 removed the
  SDK, so its only remaining effect would be redirecting an Anthropic call to a provider no
  route names, from an environment nobody reads.

## Two defects fixed in passing, both found by implementing agents

- **`str(TimeoutError())` is the empty string**, which is falsy, so every `if loop_error` in
  the attacker runner read a timed-out attack sequence as a completed one and the truncation
  went unreported. `_loop_failure` returns the type name when the message is empty.
- **The extraction nearly diverged from the path it unifies.** The first draft of
  `run_tool_loop` sent `json.dumps(payload)` as the tool message while
  `agent_loop._run_tool_call` sent `wire_text(payload)`. Both read fine alone. Together the
  Attacker would reason about the MCP envelope while the customer agent reads the text
  inside it. Caught before the first commit by diffing the new call site against the
  original. Logged as FM-008 and gated by `TestTheTwoLoopsAgree`.

## What this found that is NOT in scope

**Issue #88.** `make_client` never reads the route's provider. It derives one from the
credentials' base url, and with `ANTHROPIC_BASE_URL` unset that answers `anthropic`, so all
twelve raw direct-API purposes build an `anthropic.Anthropic` client while their route row
says `openai / gpt-5.6-luna`. Measured, twelve for twelve. The judge half is unaffected:
`make_instructor_client` passes the route and refuses another provider.

ADR 0008 says Luna serves every model call, the route table says so, and the clients do not.
Filed rather than widening this branch.

## Evidence

- Import contracts: 3 kept, 0 broken, with `claude_agent_sdk` in `forbidden_modules`.
  Mutation `from claude_agent_sdk import tool` in `validation_service.py` turned it BROKEN
  naming the edge and its line. Restored, KEPT again.
- Every mutation proof the implementing agents ran is recorded in their commit messages,
  each observed red and restored green.

## Failure-mode log

FM-008 opened (the extraction diverges from the path it unifies), gated at rung 2. FM-005
climbed to rung 1: the fake SDK modules are deleted, because there is nothing left to fake.

## The adversarial pass, and what it changed

Two reviewers, run against green gates, different lenses, neither told to be conservative.
Both found the same defect first.

Fixed on the branch:

- **`stop_after` stopped on a handler that raised.** `ran` was computed from the tool's
  existence before dispatch, so a `submit_report` whose handler threw returned
  `stop_reason="stop_after"` with an empty container, which is verbatim what the guard's own
  docstring says it prevents. `dispatch` split into `dispatch_outcome`. Logged as FM-010: the
  guard read a proxy for the property rather than the property.
- **`reset_side_effect_context` cleared one sink of two**, so a turn that failed to build left
  the previous turn's typed verdicts readable, refund text included.
- **Three stale ceilings in `test_gates.py::PINNED_LIZARD`** let `LIZARD_BASELINE` be raised
  back with no meta-guard signal. One reviewer prescribed deleting all three; that would have
  broken the guard, because one key is still live in the baseline. Two deleted, one ratcheted.
- The shadow-copy test covered four of the six names the branch moved.
- Five stale names and claims.

Filed as #90 and #91, then fixed here, and the fix is smaller than the issues said because one
of my own claims was wrong.

Both issues argued AGAINST flipping the probe to recorded mode, on the strength of a sentence in
`red_team_probe.py`: *"recorded mode short-circuits the six mutating skills, and the live gate
verdicts are the entire finding."* False. `_execute_transactional_tool`'s step list says
**"Steps 1-5 all ran; only the money did not move"**, and every gate branch under `recorded`
returns a `ToolResult` with identical text.

So recorded mode closes both issues at once: the `require_human` row is not queued, retrieval
metrics are recorded rather than written, idempotency keys get the `recorded:` namespace, and
every audit row carries `RECORDED_NOT_EXECUTED`. The only real work was making the approve path
legible, since `verdict_tag` reported `succeeded` for it by falling off the end of the needle
list. `GATES_PASSED_DETAIL` names that case and the probe derives a `would_have_executed` tag
from the constant rather than copying its words.

Two defects the move itself created, both closed: `confirm_action` under recorded mode tagged an
escalation to a human approver as `succeeded`, and `bind_tool_context` never resets
`_side_effects_var`, which broke 26 tests in another module by file order once the probe bound
something other than the default.

#91 stays open for its ledger-attribution clause. The victim turn must keep the `agent_turn`
route, because `red_team_probe` names no reasoning effort while `agent_turn` names `none`, and
sending no field is a different request. What is missing is the `job_id`, and that is a signature
change through the runner template.

**Two failure modes, both mine.** FM-009: this ticket's headline finding was that the Attacker
and Orchestrator were DEAD on the credential retired the day before, and #49 revives them. The
same revocation had also stopped the victim turn reaching the dispatcher. The revival was reasoned
about in the direction that was good news and not the other. FM-001 recurrence: I read the false
sentence, repeated it into two issues and three agent briefs, and never ran it. It sat in a list
headed "each is load-bearing" beside three true claims, and two adversarial reviewers briefed to
check claims against evidence read past it too.

## What is not proven

- **RTX-01 has never executed.** The victim turn was rewritten and the only test that drives it
  against a real model is gated behind `INTEGRATION_TESTS_ENABLED` and spends money. The
  correctness win this ticket claims as its headline rests on a path no live run has touched.
- **Neither BLOCK was observed.** Both are traced hop by hop through source and the source's own
  comments. Nothing has watched a `pending_confirmations` row appear from a probe. One
  integration test against `wchats_tenant_probe` would settle both.
- The gates now run with `claude_agent_sdk` uninstalled: 3153 passed, 13 skipped. Before
  `uv sync` every green gate on this branch ran with the removed package still importable.
