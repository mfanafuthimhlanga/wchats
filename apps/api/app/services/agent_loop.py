"""The customer turn: one assembly seam, one bounded tool loop, no framework.

WHY THIS MODULE EXISTS
    ADR 0008 took the Agent turn off `claude_agent_sdk` and put it on the plain
    OpenAI SDK. The harness's remaining value was `resume`, which stores session
    files on the container filesystem, and Railway replaces that filesystem on
    every deploy. Session state lives in the `conversations` and `messages`
    tables instead, so a turn resumes from the database on any container. What
    the harness did for free is written out here: the message list, the tool
    schemas, the dispatch, and the two ceilings.

THE SEAM, AND WHY IT IS ONE FUNCTION
    `build_agent_turn` assembles a turn exactly once, and both callers go through
    it. "Same agent" is not the same model id. It is the system prompt, the tool
    server that enforces the capability envelope, the tool list, the two ceilings
    and the model, together. Assemble any of those twice and the eval measures
    something adjacent to the product, which the measurement-layer audit records
    as this repo's recurring defect. `build_agent_options` held that line for the
    SDK path and this function carries it forward.

    `side_effects` is mandatory and has no default. A default is exactly how the
    eval path silently ends up live, and live means the agent can move a
    customer's money. A caller that does not say which mode it wants raises
    TypeError at the call site rather than discovering the question against a
    real tenant at 3am (BACKLOG 2.5).

    `ledger` is mandatory for the same reason one level up. A client that records
    nothing spends a tenant's money with no row to show for it, which is the
    failure #46 ended. During the turn the recorder only appends, into the turn's
    own list, where the budget guard reads it. `record_turn_calls` hands that list
    to the ledger once the turn is over and off the event loop, because writing a
    row opens a tenant connection and a sleeping tenant endpoint takes 8 to 20
    seconds to wake.

WHAT THE LOOP COUNTS, AND WHAT IT REFUSES TO INVENT
    The turn's cost is derived from `model_calls` rows at read time, against a
    versioned price book. The SDK's `total_cost_usd` went with the harness,
    because it priced calls from a book nobody here controls.

    Two ceilings bound a turn. `MAX_MODEL_CALLS_PER_TURN` bounds how many times
    the model is asked, and `max_budget_usd` stops a turn whose recorded spend
    has reached the ceiling. The budget guard is telemetry shaped. A price the
    book cannot read degrades it to a warning and the turn runs on, because a
    customer's turn may not die over a missing tariff row.

    A tool that raises, a tool nobody registered, and arguments that are not JSON
    all come back to the model as an error tool result. The model reads the text
    and can correct itself on the next call. None of the three ends the turn.

Rung: `app.services` imports `app.core`, `app.domain`, third-party packages and
its own siblings. It imports nothing from `app.worker`. It imports no provider
SDK either, because `app.core.model_client` is the only home for those and the
client arrives here already built and already hooked. `claude_agent_sdk` is absent
too. A tool is anything carrying `name`, `description`, `input_schema` and `handler`,
which is what lets the loop run tools the SDK defined without importing it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from app.core.config import settings
from app.core.model_client import ModelRoute, Recorder, make_async_client, route_for
from app.domain.model_call import ModelCall
from app.domain.pricing import UnknownPrice, cost_usd
from app.domain.tool_result import wire_text
from app.services.agent_prompt import build_system_prompt
from app.services.agent_tools import (
    SIDE_EFFECT_MODES,
    RetrievalStrategy,
    agent_tool_definitions,
    bind_tool_context,
    record_suppressed_side_effect,
    reset_side_effect_context,
)
from app.services.escalation import send_escalation_email
from app.services.events import emit

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# The bounds on one turn, and the keys one retrieve result is captured under.
# Same names and same values as the SDK path carried, because a second copy of
# either is the audit's D3 defect wearing new clothes. One call site kept its own
# copy of a column name and the deploy gate's eval query fails open to this day.
# ---------------------------------------------------------------------------

#: How much of a `retrieve` tool result is captured onto `tool_calls_log`.
#:
#: This is the AUDIT capture only. It bounds a value that reaches a jsonb column,
#: and it is what the judge falls back to when a payload cannot be decoded at all.
RETRIEVE_RESULT_CAPTURE_CHARS = 1800

#: Whether the tool marked this retrieve result an error. `retrieve_tool` returns
#: its DoS-guard refusal as ordinary text with is_error set, so the judge context
#: builder needs the flag to tell a refusal apart from a passage.
RETRIEVE_RESULT_IS_ERROR_KEY = "result_is_error"

#: The key on a `tool_calls_log` retrieve entry that carries the retrieved chunks
#: as ONE STRING PER CHUNK, untruncated. Beside it, `result` keeps the audit
#: capture, which is cut at RETRIEVE_RESULT_CAPTURE_CHARS.
#:
#: WHY BOTH. The eval scores Faithfulness, ContextPrecision and ContextRecall
#: against what this turn retrieved. Handing it `result` handed the judge a
#: string cut below ONE full chunk on any realistic corpus, as a SINGLE element,
#: which collapses ContextPrecision's ranking semantics to a coin flip over one
#: blob. Two ways for the capture format to dominate the score of the thing being
#: measured.
RETRIEVE_CHUNKS_KEY = "retrieved_chunks"

#: The key carrying the same chunks rendered FOR THE GROUNDING JUDGE: content
#: plus the provenance the agent saw (document_id, chunk_id, score).
#:
#: Separate from RETRIEVE_CHUNKS_KEY because the two readers need different
#: things and BACKLOG 5.18 is what happens when one serves both. Ragas scores
#: text against text, so provenance is noise to it. The Auditor is asked whether
#: a claim is supported, and a claim naming a document or a section cannot be
#: supported by a context that contains neither.
RETRIEVE_JUDGE_CHUNKS_KEY = "judge_chunks"

#: Companion to the key above. It reads 'chunks' when the tool handed over the
#: context it retrieved and 'unparsed' when it did not, and it is never absent.
#:
#: 'unparsed' NO LONGER ARRIVES ON ITS OWN. `retrieve_tool` attaches the
#: `_retrieved_context` ride-along on its one success path, and every other
#: producer of a retrieve wire sets `is_error`: a handler that raised, a tool
#: nobody registered, arguments that are not JSON, and the DoS-guard refusal. So
#: an entry this key marks 'unparsed' is an entry `RETRIEVE_RESULT_IS_ERROR_KEY`
#: marks errored as well.
#:
#: `_judge_retrieved_context`, `_published_context` and `_persisted_chunks` in
#: `app.worker.tasks.runtime.agent` all read the error flag first and skip, so
#: their unparsed branches never run and `counts["unparsed"]` reads 0 in
#: production. `run_eval_suite` reads this key without an error check, so its
#: `retrieve_unparsed` counts errored retrieves rather than undecodable ones.
#:
#: The state stays because a future producer of a ride-along-less SUCCESS would
#: otherwise be reported as a turn that retrieved nothing. An empty context makes
#: every claim unsupported, so that verdict would be about the reader rather than
#: about the answer.
RETRIEVE_CHUNKS_SOURCE_KEY = "retrieved_chunks_source"
RETRIEVE_CHUNKS_PARSED = "chunks"
RETRIEVE_CHUNKS_UNPARSED = "unparsed"

#: How many times one turn may ask the model.
#:
#: D-10 raised the SDK's `max_turns` from 3 to 6 on 2026-06-01. At 3 the agent was
#: cut off after the retrieve round trip and had no turn left to compose an
#: answer, so `response_text` came back empty. Six covers retrieve, synthesis and
#: a clarify or escalate follow-up while still bounding a DoS attempt
#: (T-04-03-06). This bounds model calls the same way, and it is the same number
#: because the shape it bounds has not changed.
MAX_MODEL_CALLS_PER_TURN = 6

#: The routing table's key for this turn, and the key a spend rollup groups by.
#: Named once because `_turn_client` and `route_for` both send it, and a second
#: copy of one value is the drift defect this block's own comment names.
AGENT_TURN_PURPOSE = "agent_turn"


def _judge_chunk_record(chunk: object) -> str:
    """Render ONE retrieved chunk for the grounding judge, metadata included.

    BACKLOG 5.18. The agent sees `chunk_id`, `document_id` and `score` alongside
    the text, and those are the fields `RetrievedChunk.to_json` emits, so those
    are the fields rendered here. RETRIEVE_CHUNKS_KEY keeps `content` alone,
    which is right for the eval, because Ragas scores text against text, and
    wrong for the Auditor. An answer that cites a document or a section name has
    no support in a context that contains neither. That is 5.16's failure mode a level
    down, the judge marking a claim unsupported because it was never shown the
    evidence.

    One capture, two renderings, one reader each.

    The rendering is a labelled header line then the content, rather than the raw
    dict the agent saw. Same information, without the syntax that
    RETRIEVE_CHUNKS_KEY's own docstring records as dominating the token budget.
    """
    if not isinstance(chunk, dict):
        return str(chunk)
    fields = [
        f"{label}: {chunk[key]}"
        for label, key in (
            ("source", "document_id"),
            ("chunk", "chunk_id"),
            ("score", "score"),
        )
        if chunk.get(key) not in (None, "")
    ]
    content = str(chunk.get("content", "") or "")
    return f"[{' | '.join(fields)}]\n{content}" if fields else content


@dataclass(frozen=True)
class AgentTurn:
    """Everything one turn needs, assembled once by the seam below.

    Args:
        client:          the factory-built async OpenAI client. Typed `Any`
                         because naming the class would import the provider SDK
                         into a module the import contract keeps it out of.
        route:           where this turn's calls go, and how hard the model
                         thinks. The loop sends the effort on every call.
        system_prompt:   what `build_system_prompt` assembled for this agent.
        tools:           the tools this turn may call, duck-typed on `name`,
                         `description`, `input_schema` and `handler`.
        max_model_calls: how many times the model may be asked.
        max_budget_usd:  the recorded spend at which the turn stops.
        calls:           the live `ModelCall` list the recorder appends to.
                         Mutable on purpose. The loop reads it for the budget
                         ceiling and the caller derives the turn's cost from it.
        ledger:          where those calls go once the turn is over.
                         `record_turn_calls` is what takes them there.
    """

    client: Any
    route: ModelRoute
    system_prompt: str
    tools: tuple[Any, ...]
    max_model_calls: int
    max_budget_usd: float
    calls: list[ModelCall]
    ledger: Recorder


def record_turn_calls(turn: AgentTurn) -> int:
    """Hand this turn's `ModelCall` rows to its ledger. Returns how many were written.

    Called AFTER the turn, off the event loop. The recorder that runs during the
    turn only appends, because `ledger_recorder` opens, commits and closes a
    tenant connection per row, and a tenant endpoint waking takes 8 to 20 seconds
    (CLAUDE.md). Six of those inside the customer's 90-second turn is the whole
    budget spent on telemetry.

    THE LIST IS TAKEN AND EMPTIED FIRST, then written, so a second call writes
    nothing and no row reaches the ledger twice. An index would leave the rows in
    place, where the next reader prices a turn that was already priced. The
    caller's cost derivation reads `turn.calls`, so it must run BEFORE this does.

    A ledger write that fails is logged and skipped, never raised. Telemetry may
    not fail a turn a customer was already served.
    """
    pending = list(turn.calls)
    del turn.calls[:]
    written = 0
    for call in pending:
        try:
            turn.ledger(call)
        except Exception as exc:
            log.error("agent_loop.ledger_row_not_written", error_type=type(exc).__name__, error=str(exc))
            continue
        written += 1
    return written


def _escalation_notifier(agent, conversation_id: str, side_effects: str):
    """The escalation edge, live or recorded.

    On the eval path the mail is recorded rather than sent. A scenario that drives
    the agent to escalate would otherwise page the owner about a customer who does
    not exist, and would do it nightly.

    A conditional expression rather than two `def`s, because a nested definition
    would hide a second assembly of this edge from the seam suite.
    """
    return (
        (lambda reason, context: send_escalation_email(agent, reason, context))
        if side_effects == "live"
        else (
            lambda reason, context: record_suppressed_side_effect(
                "escalation.notify",
                {
                    "agent_id": str(agent.id),
                    "conversation_id": str(conversation_id),
                    "reason": reason,
                    "context": context,
                },
            )
        )
    )


def _check_mode(side_effects: str) -> None:
    """Refuse a mode this seam cannot serve, before anything is built.

    `Literal` is a type-checker annotation and enforces nothing at run time, so
    an unrecognised value would compare unequal to "recorded" and be served as
    live. On the eval path that is a real refund against the tenant's provider.
    """
    if side_effects in SIDE_EFFECT_MODES:
        return
    raise ValueError(
        f"build_agent_turn: side_effects must be one of {SIDE_EFFECT_MODES}, "
        f"got {side_effects!r}. There is no third mode and no fallback. An "
        f"unrecognised value read as live is how an eval scenario issues a real "
        f"refund against the tenant's provider (BACKLOG 2.5)."
    )


def _turn_client(*, agent, job_id: str, calls: list[ModelCall]):
    """The client this turn's calls go through, built by the factory.

    The recorder only appends. It runs inside the async response hook, on the
    event loop the customer is waiting on, and `ledger_recorder` opens a tenant
    connection per row. The rows go to the ledger after the turn, through
    `record_turn_calls`.
    """
    return make_async_client(
        AGENT_TURN_PURPOSE,
        tenant_id=str(agent.tenant_id),
        recorder=calls.append,
        agent_id=str(agent.id),
        job_id=job_id,
    )


def build_agent_turn(
    *,
    agent,
    conn_str: str,
    conversation_id: str,
    job_id: str,
    side_effects: str,
    ledger: Recorder,
    verified_session_token: str = "",
    soul_override: dict | None = None,
) -> AgentTurn:
    """Assemble one turn of `agent`. THE SEAM, described in the module docstring.

    One side effect, and it is the point. `bind_tool_context` publishes the ContextVars every tool handler reads, and the last caller wins.

    Args:
        agent:                  Control-DB Agent row: id, tenant_id, name, retrieval_strategy, soul fields.
        conn_str:               Decrypted tenant DB connection string. Never logged, never a task arg (CTL-08).
        conversation_id:        Conversation UUID string.
        job_id:                 Celery job id (OPS-05/06 retrieval metrics).
        side_effects:           "live" or "recorded". Mandatory (BACKLOG 2.5).
        ledger:                 where each finished `ModelCall` goes once the turn is over, through `record_turn_calls`. Mandatory.
        verified_session_token: IDV-05 token, "" when there is none. NEVER logged (T-04-03-05).
        soul_override:          Prompt-version soul fields (OPS-16), or None.

    Raises:
        TypeError:       `side_effects` or `ledger` was not passed. Neither has a default.
        ValueError:      side_effects is neither "live" nor "recorded".
        ValidationError: pydantic refused `agent.retrieval_strategy`.
        UnknownPurpose:  PURPOSE_ROUTES has no row for AGENT_TURN_PURPOSE.
    """
    # FIRST, before anything that can raise. The prefork pool does not isolate
    # contextvars per task and the mode is sticky, so a previous turn's value is
    # in force on entry. Resetting here makes the safe default this function's.
    reset_side_effect_context()
    _check_mode(side_effects)
    bind_tool_context(
        conn_str=conn_str,
        agent_id=str(agent.id),
        agent_name=agent.name,
        strategy=RetrievalStrategy.model_validate(agent.retrieval_strategy or {}),
        conversation_id=str(conversation_id),
        notify_fn=_escalation_notifier(agent, conversation_id, side_effects),
        tenant_id=str(agent.tenant_id),
        verified_session_token=verified_session_token,
        job_id=job_id,
        side_effects=side_effects,
    )
    system_prompt = build_system_prompt(agent, soul_override=soul_override)
    calls: list[ModelCall] = []
    return AgentTurn(
        client=_turn_client(agent=agent, job_id=job_id, calls=calls),
        route=route_for(AGENT_TURN_PURPOSE),
        system_prompt=system_prompt,
        tools=agent_tool_definitions(),
        max_model_calls=MAX_MODEL_CALLS_PER_TURN,
        max_budget_usd=settings.AGENT_MAX_BUDGET_USD,
        calls=calls,
        ledger=ledger,
    )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class _TurnState:
    """What one turn accumulates across its model calls.

    `response_parts` is one entry per model call that produced text. They are
    joined with a newline at the end rather than concatenated as they arrive. A
    turn that says "let me look that up" and then "Fourteen days." otherwise
    reaches the customer, `_extract_citations`, the PII firewall and the Auditor
    as "let me look that upFourteen days."
    """

    response_parts: list = field(default_factory=list)
    tool_calls_log: list = field(default_factory=list)
    escalated: bool = False
    escalation_reason: str | None = None
    escalation_context: str | None = None
    num_turns: int = 0
    stop_reason: str | None = None


def _opening_messages(message: str, *, history: list, system_prompt: str) -> list[dict]:
    """The system prompt, the conversation so far, then what the customer just said."""
    return [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": message},
    ]


def _tools_wire(tools) -> list[dict]:
    """The tool list as the wire carries it. Built once and sent on every call."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _request_kwargs(turn: AgentTurn, messages: list, tools_wire: list) -> dict:
    """One request body. The effort field is absent when the route names none.

    Sending an explicit null is a different request from sending nothing, so a
    route without an effort sends no field at all and takes the provider default.
    """
    kwargs = {"model": turn.route.model, "messages": messages, "tools": tools_wire}
    if turn.route.reasoning_effort is not None:
        kwargs["reasoning_effort"] = turn.route.reasoning_effort
    return kwargs


def _over_budget(turn: AgentTurn) -> bool:
    """True when this turn's recorded spend has reached its ceiling.

    Derived from the `ModelCall` rows the recorder teed in, priced through the
    versioned book. A price the book cannot read degrades the guard to a warning
    and the turn runs on, because a customer's turn may not die over a missing
    tariff row.
    """
    total = Decimal(0)
    for call in turn.calls:
        try:
            total += cost_usd(call)[0]
        except UnknownPrice as exc:
            log.warning("agent_loop.budget_unpriced", error=str(exc))
            return False
    return float(total) >= turn.max_budget_usd


def _assistant_turn(message, tool_calls) -> dict:
    """Replay the model's own turn, built by hand so the wire shape is ours.

    A `model_dump` of the response object would carry whatever fields the SDK
    version happens to hold, and the next request would send them back.
    """
    return {
        "role": "assistant",
        "content": message.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ],
    }


def _error_wire(text: str) -> dict:
    """The wire shape a failed tool call returns. `wire_text` reads it like any other."""
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _tool_arguments(name: str, raw) -> tuple[dict, dict | None]:
    """The arguments the model sent, and the refusal to hand back when they do not read.

    A model writes this string, so a malformed one is ordinary traffic rather
    than a fault. It goes back as an error tool result the model can correct on
    its next call, and the turn runs on.
    """
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        log.warning("agent_loop.tool_arguments_unparsed", tool_name=name)
        return {}, _error_wire(f"Tool {name} received arguments that are not valid JSON.")
    if not isinstance(args, dict):
        log.warning("agent_loop.tool_arguments_not_an_object", tool_name=name)
        return {}, _error_wire(f"Tool {name} received arguments that are not a JSON object.")
    return args, None


async def _dispatch(tools, name: str, args: dict) -> dict:
    """Run the named tool's handler, or say why nothing ran.

    An unknown name, a raising handler and a handler that returns something other
    than a wire dict all come back as an error wire dict. The model reads the text
    and the turn continues. Raising here would end a customer's turn over one
    tool, and every reader downstream (`wire_text`, `_log_entry`) calls `.get` on
    what this returns.
    """
    tool = next((candidate for candidate in tools if candidate.name == name), None)
    if tool is None:
        log.warning("agent_loop.unknown_tool", tool_name=name)
        return _error_wire(f"Tool {name} is not one this agent has.")
    try:
        wire = await tool.handler(args)
    except Exception as exc:
        log.warning(
            "agent_loop.tool_failed", tool_name=name, error_type=type(exc).__name__
        )
        return _error_wire(f"Tool {name} failed with {type(exc).__name__}.")
    if isinstance(wire, dict):
        return wire
    log.warning(
        "agent_loop.tool_result_not_a_dict", tool_name=name, result_type=type(wire).__name__
    )
    return _error_wire(f"Tool {name} returned a {type(wire).__name__} rather than a result.")


def _note_escalation(state: _TurnState, name: str, args: dict) -> None:
    """Escalation is read off the tool the model called, never off its prose (T-04-03-03)."""
    if name != "escalate_to_human":
        return
    state.escalated = True
    state.escalation_reason = args.get("reason")
    state.escalation_context = args.get("context")


def _attach_retrieve_capture(entry: dict, wire: dict) -> None:
    """Write the retrieve captures onto one `tool_calls_log` entry.

    The ride-along `_retrieved_context` is the retrieval the tool performed,
    structurally. When it is absent the audit `result` string is the degraded
    capture and the source says `unparsed`.

    NOTHING IN THIS TREE PRODUCES `unparsed` WITHOUT `is_error` BESIDE IT.
    `retrieve_tool` attaches the ride-along on its one success path, and its
    DoS-guard refusal, a raising handler, an unknown tool and unreadable
    arguments all set the error flag instead. Every reader in
    `app.worker.tasks.runtime.agent` checks that flag first, so the branch below
    is a guard on a producer this tree does not have yet. It stays for the day
    one arrives, because a success carrying no ride-along would otherwise be
    reported as a turn that retrieved nothing.
    """
    entry[RETRIEVE_RESULT_IS_ERROR_KEY] = bool(wire.get("is_error"))
    context = wire.get("_retrieved_context")
    if not isinstance(context, dict):
        entry[RETRIEVE_CHUNKS_KEY] = []
        entry[RETRIEVE_JUDGE_CHUNKS_KEY] = []
        entry[RETRIEVE_CHUNKS_SOURCE_KEY] = RETRIEVE_CHUNKS_UNPARSED
        return
    chunks = context.get("chunks") or []
    entry[RETRIEVE_CHUNKS_KEY] = [
        str(chunk.get("content")) for chunk in chunks if chunk.get("content")
    ]
    entry[RETRIEVE_JUDGE_CHUNKS_KEY] = [
        rendered
        for rendered in (_judge_chunk_record(chunk) for chunk in chunks)
        if rendered
    ]
    entry[RETRIEVE_CHUNKS_SOURCE_KEY] = RETRIEVE_CHUNKS_PARSED


def _log_entry(name: str, args: dict, tool_use_id: str, wire: dict) -> dict:
    """One `tool_calls_log` row. A retrieve carries its captures as well."""
    entry = {
        "tool_name": name,
        "input": args,
        # Carried so a later reader can attach anything to the call that PRODUCED
        # a result, by id. "The most recent entry without a result" mis-attributes
        # the moment a reply carries two tool calls (BACKLOG 5.21).
        "tool_use_id": tool_use_id,
        "result": str(wire.get("content"))[:RETRIEVE_RESULT_CAPTURE_CHARS],
    }
    if name == "retrieve":
        _attach_retrieve_capture(entry, wire)
    return entry


async def _run_tool_call(call, *, messages, state, turn, job_id, db, redis) -> None:
    """One tool call: the two events, the tool message, and the audit entry.

    The `agent.tool_result` payload shape is a contract.
    `retrieval_eval.run_retrieval_faithfulness` selects job_events on
    `payload["tool_name"] == "retrieve"` and reads `payload["summary"]`.
    """
    name = call.function.name
    args, refusal = _tool_arguments(name, call.function.arguments)
    emit(job_id, "agent.tool_call", {"tool_name": name, "input": args}, db, redis)
    _note_escalation(state, name, args)
    wire = refusal if refusal is not None else await _dispatch(turn.tools, name, args)
    text = wire_text(wire)
    messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
    emit(job_id, "agent.tool_result", {"tool_name": name, "summary": text[:200]}, db, redis)
    state.tool_calls_log.append(_log_entry(name, args, call.id, wire))


def _turn_result(state: _TurnState) -> dict:
    """What one turn hands back to its caller."""
    return {
        "response_text": "\n".join(part for part in state.response_parts if part),
        "tool_calls_log": state.tool_calls_log,
        "escalated": state.escalated,
        "escalation_reason": state.escalation_reason,
        "escalation_context": state.escalation_context,
        "num_turns": state.num_turns,
        "stop_reason": state.stop_reason,
    }


async def run_agent_loop(message: str, *, history, turn: AgentTurn, job_id, db, redis) -> dict:
    """Run one customer turn to its end and collect what it produced.

    Args:
        message: what the customer just said. Never logged (T-04-03-05).
        history: the conversation so far, as `{"role", "content"}` dicts already
                 in order. The database is where it comes from, since ADR 0008
                 keeps session state in `conversations` and `messages`.
        turn:    what `build_agent_turn` assembled.
        job_id:  Celery job id, the SSE channel these events reach.
        db:      SQLAlchemy sync Session, for `emit`.
        redis:   sync Redis client, for `emit`.

    Returns:
        response_text, tool_calls_log, escalated, escalation_reason,
        escalation_context, num_turns and stop_reason. `stop_reason` is the
        provider's finish_reason when the model stopped on its own, and
        "budget_exceeded" or "max_model_calls" when a ceiling stopped it.
    """
    messages = _opening_messages(
        message, history=list(history), system_prompt=turn.system_prompt
    )
    tools_wire = _tools_wire(turn.tools)
    state = _TurnState()
    try:
        for index in range(turn.max_model_calls):
            # Every call after the first. A turn that has spent nothing yet cannot
            # be over its own ceiling, and checking there would refuse to serve.
            if index and _over_budget(turn):
                state.stop_reason = "budget_exceeded"
                break
            completion = await turn.client.chat.completions.create(
                **_request_kwargs(turn, messages, tools_wire)
            )
            state.num_turns += 1
            choice = completion.choices[0]
            if choice.message.content:
                state.response_parts.append(choice.message.content)
            tool_calls = getattr(choice.message, "tool_calls", None)
            if not tool_calls:
                state.stop_reason = choice.finish_reason
                break
            messages.append(_assistant_turn(choice.message, tool_calls))
            for call in tool_calls:
                await _run_tool_call(
                    call, messages=messages, state=state, turn=turn,
                    job_id=job_id, db=db, redis=redis,
                )
        else:
            # `for ... else` runs only when no `break` fired, so this is the call
            # ceiling and nothing else. The model was still asking for tools.
            state.stop_reason = "max_model_calls"
    finally:
        # The loop owns the client because an `AgentTurn` is single-use by
        # construction. Its `calls` list and the tool ContextVars are this turn's.
        # `asyncio.run` tears the event loop down the moment this returns, and a
        # live httpx transport per turn is a file-descriptor leak in a worker that
        # runs for weeks.
        await turn.client.close()
    return _turn_result(state)
