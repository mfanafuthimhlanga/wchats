# 260826, the owned Agent loop on Luna (#48)

Closes #48. ADR `docs/adr/0008-one-provider-owned-loop.md`, committed before the code per
spec #37. Branch `feat/agent-loop`, PR #78.

The customer Agent turn leaves `claude_agent_sdk` for an owned bounded tool loop over the
OpenAI SDK, serving `gpt-5.6-luna`. DeepSeek stops serving the customer turn.

## The shape

Two public functions in a new module `app/services/agent_loop.py`. The chat task and the
eval driver both go through both.

- `build_agent_turn(...)` succeeds `build_agent_options`. It returns a frozen `AgentTurn`
  holding the factory client, the `ModelRoute`, the system prompt, the eleven tool
  definitions, the two ceilings, the live `calls` list and the `ledger`.
- `run_agent_loop(message, *, history, turn, job_id, db, redis)` succeeds `_run_sdk_turn`.

`app/worker/tasks/runtime/agent.py` loses 818 lines. Gone with the harness: the seam, the
stream reader, four framed-text parsers, `ast.literal_eval`, `_set_sdk_session_id`, and
the SDK's `total_cost_usd`.

## Decisions, and why

- **The route carries the effort.** `agent_turn` joins `PURPOSE_ROUTES` at
  `reasoning_effort="none"`, which is the figure decision #34 priced. The loop builds
  through `make_async_client`, which runs no purpose check, so `_request_kwargs` puts the
  route's effort on every body. A call site never names it.
- **The ledger only appends during the turn.** `ledger_recorder` opens, commits and closes
  a tenant connection per row, and a suspended Neon endpoint takes 8 to 20 seconds to wake.
  Six of those inside a 90 second turn is the entire budget spent on telemetry. So the
  recorder appends to `turn.calls`, and `record_turn_calls(turn)` writes the rows after the
  turn, in the `finally` that already closes the pooled connection. It covers the served
  path, the timeout path and the retry path, and each attempt writes only its own rows.
  The cost derivation reads `turn.calls` and therefore runs before that call.
- **A turn with no ledger row costs `None`, never zero.** `sum([])` is 0, and a stored zero
  claims the turn was free. The hook records nothing for an unreadable usage shape, a
  streamed body, a 4xx or 5xx, or a hook exception that fails open, so the empty case is
  real and reachable.
- **The retrieve payload travels structurally.** `retrieve_tool` frames
  `json.dumps(chunks)` for the model to read and rides `_retrieved_context` alongside the
  wire dict. The loop takes its capture keys from the ride-along, so nothing parses
  model-facing text and the `literal_eval` seam #44 left standing dies here.
- **`bind_tool_context` splits from `build_tool_server`.** The loop needs the ContextVar
  publication, not an MCP server. Before the split the seam called `build_tool_server` and
  discarded its return, so every customer turn built an SDK server nothing read.
  `build_tool_server` now constructs the server first and binds second, which means a
  half-built server can no longer leave a published side-effect mode behind.
- **History is DB-backed, capped at 40 messages, `role IN ('user','assistant')`.** A turn's
  user and assistant rows share `transaction_timestamp()`, so `created_at` alone orders
  them by query plan. The `CASE role WHEN 'assistant' THEN 0 ELSE 1 END` tiebreak is what
  keeps the question ahead of its answer. Issue #79 carries the durable fix.
- **The eval turn bills under the run id.** Synthesised per-scenario ids named no job, so a
  run could not total its own agent spend and eval traffic was indistinguishable from
  customer traffic under `purpose='agent_turn'`.

## Deviations from the plan

- `build_agent_turn` lost its `client` parameter. An injected client skips the ledger hook,
  which defeats the one seam whose job is to be un-bypassable. Tests patch
  `make_async_client` instead.
- `_run_one_eval_turn` grew a `finally`, which costs one CCN in lizard and would have
  breached an exact pin, so the drive moved to `_drive_eval_turn`. `run_agent_turn` paid
  for its own `finally` by extracting `_retry_countdown`.
- Four lizard pins shrank and three entries were deleted. No pin was raised.

## Evidence

- `scripts/gates.py full`, exit 0, `3050 passed, 13 skipped`.
- Guards proven by mutation, each observed red then restored and observed green: the
  eval's `side_effects` literal, a second `AgentTurn` construction inside `run_agent_turn`,
  the history tiebreak, the turn cost rebound to a literal, an empty `calls` list priced at
  zero, an `UnknownPrice` priced at zero, the ledger written during the turn, an unchecked
  non-dict tool result, a no-op ledger on the chat path, and the errored retrieve's SSE
  emit.
- The history tiebreak fixture inserts the user row first. Inserting the assistant row
  first passes with or without the `CASE`, which makes that arrangement a tautology.

## What the adversary pass found, and what it cost

Three findings blocked. Two of them were guards the earlier mutation proofs had already
certified, which is the part worth remembering.

- **The history tiebreak was pinned by string-matching the SQL.** The fake cursor decided
  how to sort by looking for the `CASE` expression anywhere in the statement, so leaving
  that text in a `--` comment while deleting it from the `ORDER BY` kept six tests green
  on a query that had lost its tiebreak. The earlier proof deleted the clause outright and
  went red, which looked like evidence and was not. The fix is a guard that strips comments
  and reads only the segment after the final `ORDER BY`, plus
  `tests/integration/test_turn_history_order.py`, which writes both rows of a turn in one
  transaction against `wchats_tenant_probe` and reads them back through the real function.
  It carries a control test that runs the tiebreak-less statement and pins the wrong answer,
  so it cannot pass by accident.
- **Nothing pinned that the ledger is ever written.** Deleting `record_turn_calls` from both
  `finally` blocks left 104 tests passing, because the task suite handed the seam a turn
  whose `calls` list was always empty. Both call sites now drive a real `ModelCall` through
  to `record_model_call`, on the served path and on a path that raises.
- **Every tool result was being persisted where the SDK path stored `{}`.** `_log_entry` set
  `result` for all eleven tools and `_persist_messages` writes that key into the tenant's
  `tool_calls.result`, so `lookup_structured` customer rows and the six mutating skills'
  outputs were being retained at rest up to 1800 chars per call. Nobody decided that.
  `result` is now set only for `retrieve`, and it carries the joined text rather than a
  Python repr of the content blocks, which retires the last producer of the repr ADR 0008
  says died here.

Smaller ones fixed in the same round: the client leaked when the opening messages or the
tools wire raised, an empty `choices` list killed the turn with an IndexError, a malformed
`escalate_to_human` argument reported an escalation that never happened, an empty assistant
row replayed into the next turn's context, and the rewritten seam suite had stopped policing
`ClaudeAgentOptions` construction across the repo while three modules still construct it.

Two fixture directions were wrong in briefs written for that work, both the same way. A
history test that inserts the assistant row first passes with or without the tiebreak,
because the reader reverses a `DESC` scan. The rule that catches it is to make the fixture's
heap order disagree with the wanted order, then check that the tiebreak-less query really
does return the wrong answer.

## Discovered work, filed the same day

- #79 `messages` has no ordering tiebreaker. Four readers order by `created_at` alone.
- #80 the unit suite stalls on real Langfuse HTTP because the conftest sets a public key.
- #81 `retrieval_eval` scores an errored retrieve's refusal text as retrieved context.
- #82 `AGENT_MAX_BUDGET_USD` is roughly 650 times the measured turn cost, so the budget
  guard cannot fire. The six-call ceiling is the live bound.
- #83 provider response bodies and libpq DSN fragments reach the public SSE stream as
  `str(exc)`, measured against the installed SDK.
- #84 the faithfulness context proxy changed shape at the cutover, so scores computed
  either side of it do not compare.
- #85 an `acks_late` redelivery mid-turn runs a second full turn. The idempotency read is
  sequential-only.
- #86 `emit` commits to the control database twice per tool call, on the turn's event loop.
  The tenant ledger moved off that loop in this ticket and the control writes did not.

## Validated against the live model, 2026-08-27

The owner supplied an OpenAI key and revoked the Anthropic and DeepSeek credentials. Four
probes ran against `gpt-5.6-luna` for $0.001895 in total. Every assumption the loop makes
about the provider is now measured rather than assumed.

The model contract. `reasoning_effort: "none"` is accepted alongside `tools`, and the API
names the supported set as none, low, medium, high and xhigh. `finish_reason` reads
`tool_calls` for a tool reply and `stop` for an answer. `function.arguments` arrives as a
string of JSON, which is what `json.loads` in `_tool_arguments` expects. The body reports
`gpt-5.6-luna` itself, so `model_source` is `reported` and the price book prices every
call. Nothing comes back that the loop would have to echo into the next request. A 400
leaves no ledger row, which is the hook correctly skipping an error body.

The loop. A grounded question ran a two-call tool round trip, captured two chunks and two
judge chunks with source `chunks`, and stored the joined text rather than a repr. A
follow-up turn answered from DB-shaped history with no container state. An angry customer
produced `escalated=True` off the tool the model called, with the handler actually invoked.
`max_model_calls=1` stopped a turn with `stop_reason='max_model_calls'`. Seven `ModelCall`
rows were written into the probe database by `record_turn_calls` and then removed.

The seam. Driven through `build_agent_turn`, so the prompt was the one `build_system_prompt`
writes. Luna emitted the citation block in the exact shape `CITATIONS_REGEX` demands and
`_extract_citations` returned two structured citations. The PII firewall then allowed the
business's support email through because it appeared in the retrieved published context,
and `detect_pii` on the same text reports `email`, so the BACKLOG 7.29 exemption is what
let it pass rather than an absent detector.

The ceilings. A budget of $0.00002 produced `stop_reason='budget_exceeded'` after one call
that cost $0.000114, which measures the one-call overshoot the guard's shape implies.
Luna emits parallel tool calls, `retrieve` and `lookup_structured` in a single reply, each
carrying its own `tool_use_id`, so the id-matched attribution BACKLOG 5.21 introduced is
exercised by real traffic rather than only by fixtures. The non-retrieve call carried no
`result` key, so nothing of its output would reach `tool_calls.result`.

## Known limits

- A full `run_agent_turn` has not run. It needs the control database, and `CONTROL_DB_URL`
  points at live Neon production, so the probes drove `run_agent_loop` and the seam
  directly instead. Retrieval was stubbed because the probe database's `chunks` table
  carries no embedding column.
- The root `.env` the owner keeps the OpenAI key in is shadowed by `apps/api/.env`, which
  is the first `.env` above `app/core`. The probes injected the key into the process
  environment, which outranks the env file. A worker cannot serve a live turn until one of
  the two files is made canonical, and `ADMIN_KEY` differs between them. #41 carries it.
- The `unparsed` retrieve state is now reachable only alongside `is_error`, because
  `retrieve_tool` attaches the ride-along on its one success path. The three readers in
  `agent.py` check the error flag first and skip, so their counter reads zero.
  `run_eval_suite` does not check it, so its `retrieve_unparsed` still counts errored
  retrieves. The branches stay as a guard against a future producer.
- `_over_budget` prices against an exact `served_model` match. A dated snapshot id would
  raise `UnknownPrice` on every call and degrade the guard to off, logging
  `agent_loop.budget_unpriced`. #82 carries it.
- A test file's fake `claude_agent_sdk` used to win on import order rather than on whether
  the real package was installed. `agent_tool_definitions()` then handed the loop objects
  with no `.name`, and `test_agent_loop.py` passed only because it sorts ahead of
  `test_agent_tools.py`. The four guards now test installation.
