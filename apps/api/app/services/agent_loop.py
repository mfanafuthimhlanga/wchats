"""The customer turn: one assembly seam, one bounded tool loop, no framework.

WHY THIS MODULE EXISTS
    ADR 0008 took the Agent turn off the Agent SDK harness and put it on the
    plain OpenAI SDK. The harness's remaining value was `resume`, which stores session
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

    The seam has one side effect, and it is the point. `bind_tool_context`
    publishes the ContextVars every tool handler reads, and the last caller wins.

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

THE PII FIREWALL IS INSIDE THE SEAM, WHERE NO CALLER CAN SKIP IT
    `_turn_result` scans the turn's finished text through
    `app.domain.pii_firewall.scan_response` and hands back the SERVED text. That
    module's docstring says the scan is unconditional, and until #50 it was not:
    the live Celery task ran it after `run_agent_loop` returned, and neither the
    eval task nor the red-team victim turn imported the module at all. A response
    the firewall would have deflected was therefore scored by Ragas verbatim and
    posted to a third-party judge API. The original text is not returned in any
    form, because a caller that can read it can serve it.

    THREE CALLERS RUN THIS LOOP, not two: `run_agent_turn`, the eval's
    `_run_one_eval_turn`, and `red_team_probe._build_transactional_probe_fn`. The
    third one now sees what a customer would have been served, which is what that
    probe's own docstring asks for — and it means a red-team attack that talks
    the agent into a leak is scored on the deflection the firewall substituted,
    not on the words the model wrote.

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
client arrives here already built and already hooked. The Agent SDK harness is
absent too, and #49 took its last import out of this tree. A tool is anything
carrying `name`, `description`, `input_schema` and `handler`, which is what let
this loop run the harness-declared tools without importing the harness.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from app.core.config import settings
from app.core.model_client import ModelRoute, Recorder, make_async_client, route_for
from app.domain.model_call import ModelCall
from app.domain.pii_firewall import detect_pii, scan_response
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
from app.services.tool_loop import (
    assistant_turn,
    dispatch,
    first_choice,
    tool_arguments,
    tools_wire,
)

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
#: `published_context` below, and `_judge_retrieved_context` and
#: `_persisted_chunks` in `app.worker.tasks.runtime.agent`, all read the error
#: flag first and skip, so their unparsed branches never run and
#: `counts["unparsed"]` reads 0 in
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


def _escalation_notifier(agent, conversation_id: str, side_effects: str, override=None):
    """The escalation edge: the caller's own, or the one this mode implies.

    On the eval path the mail is recorded rather than sent. A scenario that drives
    the agent to escalate would otherwise page the owner about a customer who does
    not exist, and would do it nightly.

    `override` HAS ONE CALLER, `red_team_probe._build_transactional_probe_fn`,
    and it is redundant on purpose. That probe runs on side_effects="recorded"
    since #90, so the branch below already returns the recording lambda for it
    and no mail can leave.

    It is kept because this is the one edge that pages a real human, and because
    of how the caller got here. The probe ran on side_effects="live" until #90,
    on the claim that recorded mode short-circuits the mutating skills and
    destroys the gate verdicts. That claim was false: recorded mode runs steps 1
    to 5 and every gate returns identical text. It went unchecked for a milestone
    and cost two BLOCK findings, so this edge does not rest on a claim about mode
    behaviour staying true. Every other caller passes nothing and takes the mode's
    notifier, which is the default a replacement could silently break.

    A conditional expression rather than two `def`s, because a nested definition
    would hide a second assembly of this edge from the seam suite.
    """
    if override is not None:
        return override
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


def _discard_client(client) -> None:
    """Close a client built for a turn that then failed to come into existence.

    `AsyncOpenAI.close` is a coroutine and this seam is synchronous, so the close
    runs on an event loop of its own. Both callers are sync Celery code, so there
    is no running loop for this one to collide with.

    Every failure here is swallowed. This runs while an exception is already on
    its way out of the seam, and a raise from the cleanup would replace the
    reason the turn failed with a footnote about a socket.
    """
    try:
        asyncio.run(client.close())
    except Exception as exc:
        log.warning(
            "agent_loop.unused_client_not_closed",
            error_type=type(exc).__name__,
            error=str(exc),
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
    notify_fn=None,
) -> AgentTurn:
    """Assemble one turn of `agent`. THE SEAM, described in the module docstring.

    Args:
        agent:                  Control-DB Agent row: id, tenant_id, name, retrieval_strategy, soul fields.
        conn_str:               Decrypted tenant DB connection string. Never logged, never a task arg (CTL-08).
        conversation_id:        Conversation UUID string.
        job_id:                 Celery job id (OPS-05/06 retrieval metrics).
        side_effects:           "live" or "recorded". Mandatory (BACKLOG 2.5).
        ledger:                 where each finished `ModelCall` goes once the turn is over, through `record_turn_calls`. Mandatory.
        verified_session_token: IDV-05 token, "" when there is none. NEVER logged (T-04-03-05).
        soul_override:          Prompt-version soul fields (OPS-16), or None.
        notify_fn:              What `escalate_to_human` calls, or None for the notifier this mode implies. One caller passes one; `_escalation_notifier` says which and why.

    Raises:
        TypeError:       `side_effects` or `ledger` was not passed. Neither has a default.
        ValueError:      side_effects is neither "live" nor "recorded".
        ValidationError: pydantic refused `agent.retrieval_strategy`.
        UnknownPurpose:  PURPOSE_ROUTES has no row for AGENT_TURN_PURPOSE.
    """
    # FIRST, before anything that can raise. `reset_side_effect_context` carries the reasoning.
    reset_side_effect_context()
    _check_mode(side_effects)
    bind_tool_context(
        conn_str=conn_str,
        agent_id=str(agent.id),
        agent_name=agent.name,
        strategy=RetrievalStrategy.model_validate(agent.retrieval_strategy or {}),
        conversation_id=str(conversation_id),
        notify_fn=_escalation_notifier(agent, conversation_id, side_effects, notify_fn),
        tenant_id=str(agent.tenant_id),
        verified_session_token=verified_session_token,
        job_id=job_id,
        side_effects=side_effects,
    )
    system_prompt = build_system_prompt(agent, soul_override=soul_override)
    calls: list[ModelCall] = []
    # Into a name, so `_discard_client` has something to close when `route_for` or `agent_tool_definitions` raises.
    client = _turn_client(agent=agent, job_id=job_id, calls=calls)
    try:
        return AgentTurn(
            client=client, route=route_for(AGENT_TURN_PURPOSE),
            system_prompt=system_prompt, tools=agent_tool_definitions(),
            max_model_calls=MAX_MODEL_CALLS_PER_TURN, max_budget_usd=settings.AGENT_MAX_BUDGET_USD,
            calls=calls, ledger=ledger,
        )
    except BaseException:
        _discard_client(client)
        raise


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


def _request_kwargs(turn: AgentTurn, messages: list, tool_schemas: list) -> dict:
    """One request body. The effort field is absent when the route names none.

    Sending an explicit null is a different request from sending nothing, so a
    route without an effort sends no field at all and takes the provider default.
    """
    kwargs = {"model": turn.route.model, "messages": messages, "tools": tool_schemas}
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


def _note_escalation(state: _TurnState, name: str, args: dict, wire: dict) -> None:
    """Escalation is read off the tool the model called, never off its prose (T-04-03-03).

    It is read off the tool's RESULT as well, and that half is what the earlier
    ordering lost. This used to run before dispatch, so arguments the model wrote
    as broken JSON, or a handler that raised on the tenant connection, still set
    `escalated` with a null reason. The turn then fired `agent.escalated` at the
    widget and wrote `turn_metrics.escalated`, while nothing marked the
    conversation and no mail left the building. An error wire is not an
    escalation.
    """
    if name != "escalate_to_human" or wire.get("is_error"):
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
    arguments all set the error flag instead. Every reader checks that flag
    first — `published_context` below, and `_judge_retrieved_context` and
    `_persisted_chunks` in `app.worker.tasks.runtime.agent` — so the branch below
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


def _log_entry(name: str, args: dict, tool_use_id: str, wire: dict, text: str) -> dict:
    """One `tool_calls_log` row. A retrieve carries its captures as well.

    ONLY A RETRIEVE CARRIES `result`, and that is a retention decision rather
    than a formatting one. `_persist_messages` writes this key into the tenant's
    `tool_calls.result` jsonb, so a `result` on every tool would keep
    `lookup_structured`'s customer rows and the six mutating skills' outputs at
    rest, up to RETRIEVE_RESULT_CAPTURE_CHARS per call, on a POPIA-sensitive
    platform. The SDK path stored `{}` there for every non-retrieve tool and
    nobody decided to change that. `tc.get("result", {})` is what the writer
    reads, so an absent key writes `{}` exactly as before.

    `text` is the tool result as the MODEL read it, which is what `wire_text`
    joins out of the content blocks. The capture used to be `str(wire["content"])`,
    a Python repr of the block list, and ADR 0008 records that seam dying with
    this ticket. This is its last producer.
    """
    entry = {
        "tool_name": name,
        "input": args,
        # Carried so a later reader can attach anything to the call that PRODUCED
        # a result, by id. "The most recent entry without a result" mis-attributes
        # the moment a reply carries two tool calls (BACKLOG 5.21).
        "tool_use_id": tool_use_id,
    }
    if name == "retrieve":
        entry["result"] = text[:RETRIEVE_RESULT_CAPTURE_CHARS]
        _attach_retrieve_capture(entry, wire)
    return entry


async def _run_tool_call(call, *, messages, state, turn, job_id, db, redis) -> None:
    """One tool call: the two events, the tool message, and the audit entry.

    The `agent.tool_result` payload shape is a contract.
    `retrieval_eval.run_retrieval_faithfulness` selects job_events on
    `payload["tool_name"] == "retrieve"` and reads `payload["summary"]`.
    """
    name = call.function.name
    args, refusal = tool_arguments(name, call.function.arguments)
    emit(job_id, "agent.tool_call", {"tool_name": name, "input": args}, db, redis)
    wire = refusal if refusal is not None else await dispatch(turn.tools, name, args)
    _note_escalation(state, name, args, wire)
    text = wire_text(wire)
    messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
    emit(job_id, "agent.tool_result", {"tool_name": name, "summary": text[:200]}, db, redis)
    state.tool_calls_log.append(_log_entry(name, args, call.id, wire, text))


def published_context(tool_calls_log: list[dict]) -> list[str]:
    """What the TENANT published, for the PII firewall's exemption (BACKLOG 7.29).

    The firewall stops the agent leaking a CUSTOMER's personal data. It is not
    meant to stop it repeating the BUSINESS's own published contact details, and
    it was doing exactly that: three of twenty E2E-6 responses came back as the
    deflection, because the best-matching chunk was the corpus's "Contact and
    Escalation" section and a correct answer quotes the address in it.

    A THIRD READING OF ONE CAPTURE, joining RETRIEVE_CHUNKS_KEY (what the eval
    scores) and RETRIEVE_JUDGE_CHUNKS_KEY (what the Auditor judges). It differs
    from `agent._judge_retrieved_context` in the two places that decide whether
    an exemption is safe, so it cannot borrow that function:

        content only   RETRIEVE_CHUNKS_KEY, not RETRIEVE_JUDGE_CHUNKS_KEY. The
                       judge needs provenance; an allowlist needs the smallest
                       surface that answers the question.
        unparsed -> nothing   RETIRED, and kept as a guard. `retrieve_tool`
                       attaches its ride-along on its one success path, so an
                       unparsed entry always carries is_error and the check above
                       has already skipped it. Fail closed on both routes.

    WHAT IS DELIBERATELY NOT IN HERE, because each would be a bypass:

        the framed payload   it echoes the retrieve QUERY, so a customer who
                             types their own address into the chat would see it
                             come back exempted
        the customer message and history   the same bypass, one step shorter
        the agent soul       `pii_firewall`'s stated property is that nothing in
                             the soul reaches its behaviour (T-18-SEC-02)
        errored retrieves    `retrieve_tool` returns its DoS-guard refusal as
                             ordinary text with is_error set, and a refusal is
                             not published material

    THE ONE THING THAT WOULD WIDEN THIS SILENTLY, checked 2026-08-18 and clear:
    `verified_qa`. Those rows are answers derived from CONVERSATIONS, not material
    the tenant published, so an address a customer typed could reach the allowlist
    through them. It cannot today, because `verified_qa_lookup` is called from
    `app.worker.tasks.runtime.retrieve`, not from `agent_tools.retrieve_tool`,
    which builds its chunks straight from reranked hybrid search. Promotion is
    also off by the owner's decision of 2026-08-08. **Routing the agent's retrieve
    through that cache, or adding the lookup to `agent_tools`, widens a security
    control without touching this file.** If that day comes, filter here on the
    chunk's provenance rather than trusting the tool name.

    Returns one string per retrieved chunk, in retrieval order. It sits beside
    the firewall rather than in the Celery task because #50 moved the scan into
    the seam, and `app.services` may not import `app.worker`.
    """
    published: list[str] = []
    for tc in tool_calls_log:
        if tc.get("tool_name") != "retrieve" or "result" not in tc:
            continue
        if tc.get(RETRIEVE_RESULT_IS_ERROR_KEY):
            continue
        if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
            continue
        published.extend(str(c) for c in (tc.get(RETRIEVE_CHUNKS_KEY) or []) if c)
    return published


def _turn_result(state: _TurnState) -> dict:
    """What one turn hands back to its caller, PII firewall already applied.

    THE FIREWALL RUNS HERE, and that is the whole of #50's first half. It ran in
    the live Celery task body, after `run_agent_loop` returned, so the eval task
    never called it and never imported it: a response the firewall would deflect
    was scored by Ragas verbatim and posted to a third-party judge API.
    `pii_firewall`'s docstring claims the scan is unconditional, and two of three
    callers simply not calling it is how that claim was false by construction.

    `response_text` is the SERVED text on both paths. The original is not
    returned in any form, because a caller that can read it can serve it, which
    is why the length below is a number rather than the text.

    The four `pii_` keys are the OBSERVATION, never the control. The substitution
    has already happened by the time a caller reads them, so a caller that
    ignores all four still serves the deflection. `pii_published_exemption` is
    computed here for the same reason the length is: it needs the text this
    function refuses to hand back.
    """
    text = "\n".join(part for part in state.response_parts if part)
    published = published_context(state.tool_calls_log)
    served, detector = scan_response(text, published_context=published)
    return {
        "response_text": served,
        "tool_calls_log": state.tool_calls_log,
        "escalated": state.escalated,
        "escalation_reason": state.escalation_reason,
        "escalation_context": state.escalation_context,
        "num_turns": state.num_turns,
        "stop_reason": state.stop_reason,
        "pii_detector": detector,
        "pii_published_chunks": len(published),
        "pii_original_length": len(text),
        "pii_published_exemption": (
            detector is None and bool(published) and detect_pii(text) is not None
        ),
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
        response_text, tool_calls_log, escalated, escalation_reason, escalation_context,
        num_turns and stop_reason. `stop_reason` is the provider's finish_reason when the
        model stopped on its own, "budget_exceeded" or "max_model_calls" when a ceiling
        stopped it, and "no_choices" when a reply carried nothing to read.
        `response_text` is what the PII firewall SERVES; `_turn_result` describes it and the four `pii_` keys.
    """
    state = _TurnState()
    try:
        # Inside the try, because the client is already built and the `finally` closes it. A tool missing an attribute raises out of `tools_wire`.
        messages = _opening_messages(
            message, history=list(history), system_prompt=turn.system_prompt
        )
        tool_schemas = tools_wire(turn.tools)
        for index in range(turn.max_model_calls):
            # Every call after the first. A turn that has spent nothing yet cannot be over its own ceiling, and checking there would refuse to serve.
            if index and _over_budget(turn):
                state.stop_reason = "budget_exceeded"
                break
            completion = await turn.client.chat.completions.create(
                **_request_kwargs(turn, messages, tool_schemas)
            )
            state.num_turns += 1
            choice = first_choice(completion)
            if choice is None:
                state.stop_reason = "no_choices"
                break
            if choice.message.content:
                state.response_parts.append(choice.message.content)
            tool_calls = getattr(choice.message, "tool_calls", None)
            if not tool_calls:
                state.stop_reason = choice.finish_reason
                break
            messages.append(assistant_turn(choice.message, tool_calls))
            for call in tool_calls:
                await _run_tool_call(
                    call, messages=messages, state=state, turn=turn, job_id=job_id, db=db, redis=redis
                )
        else:
            # `for ... else` runs only when no `break` fired, so this is the call ceiling and nothing else. The model was still asking for tools.
            state.stop_reason = "max_model_calls"
    finally:
        # The loop owns the client because an `AgentTurn` is single-use by construction. Its `calls` list and the
        # tool ContextVars are this turn's. `asyncio.run` tears the event loop down the moment this returns, and a
        # live httpx transport per turn is a file-descriptor leak in a worker that runs for weeks.
        await turn.client.close()
    return _turn_result(state)
