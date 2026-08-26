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

## Discovered work, filed the same day

- #79 `messages` has no ordering tiebreaker. Four readers order by `created_at` alone.
- #80 the unit suite stalls on real Langfuse HTTP because the conftest sets a public key.
- #81 `retrieval_eval` scores an errored retrieve's refusal text as retrieved context.
- #82 `AGENT_MAX_BUDGET_USD` is roughly 650 times the measured turn cost, so the budget
  guard cannot fire. The six-call ceiling is the live bound.

## Known limits

- A live Luna turn is unproven here. `OPENAI_API_KEY` is empty and #41 carries it, so the
  first acceptance criterion rests on gate-level tests until that lands.
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
