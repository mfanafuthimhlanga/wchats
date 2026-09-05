"""AGENT_MAX_BUDGET_USD, driven both ways against the real loop (#182, #82).

WHAT THE TWO FAILED ATTEMPTS HAD IN COMMON. PR #173 set this constant twice and
reverted both. 0.0038 was five times ADR 0008's $0.00076, which prices one model
CALL and not a turn. 0.04 was twice the worst turn the first measurement could
build, and a second measurement found turns it still cut. Both passed a test
suite that measured a turn SMALLER than the configuration allows, and neither
suite could tell the difference: shrink the fixture and every assertion stayed
green.

So this file asserts in both directions at once.

    the worst permitted turn reaches its answer   the ceiling may not cut a turn
                                                  MAX_MODEL_CALLS_PER_TURN says
                                                  is servable
    that fixture really is the worst turn         a lower bound on what the
                                                  fixture itself cost, so
                                                  shrinking the payload, dropping
                                                  the history or emptying the tool
                                                  schemas turns this file red
    a runaway is stopped                          a ceiling that cannot fire is a
                                                  note, not a gate (#82)

THE SHAPE OF THE WORST PERMITTED TURN, every term a named constant:

    MAX_MODEL_CALLS_PER_TURN            6 calls, every one re-sending everything
    _RETRIEVE_CALLS_PER_TURN_MAX        8 retrieves, FRONT-LOADED into call 1 so
                                        all of them ride on calls 2 to 6
    MAX_CHUNKS x CHUNK_CONTENT_CHAR_LIMIT   5 x 2000 characters per retrieve
    TURN_HISTORY_MAX_MESSAGES           40 rows, on every call
    TURN_HISTORY_MAX_ROW_CHARS          4000 characters each
    SYSTEM_PROMPT_MAX_CHARS             the soul at both list caps, on every call
    the eleven real tool schemas        on every call

FRONT-LOADING IS THE WORST CASE, not a trick. The retrieve cap is per TURN, so a
turn carries at most eight retrieves' worth of context however it spreads them.
Asking for all eight on the first call maximises how many later calls re-send
them. #182 measured the same shape and recorded it as "8, front-loaded".

CJK IS THE DENSEST CONTENT, and the ceiling is derived at it. `tests.token_meter`
measures 1.37 characters per token against English prose's 5.67, so a ceiling
derived on prose is roughly four times too low for a Chinese tenant and cuts
their turns off mid-answer. The cost of deriving at the densest is the other
direction: for an English tenant this ceiling sits about four times above their
own worst turn, so the guard is loose for them. ONE global constant cannot be
tight for both, and the honest choice is the one that never cuts a real answer.

WHAT IS ASSUMED RATHER THAN BOUNDED. The loop sends no `max_tokens`, so the
OUTPUT side of a turn is bounded only by whatever the provider defaults to. This
derivation prices output at `TURN_HISTORY_MAX_ROW_CHARS` characters of the
densest content per call, which is what the product already treats as a maximal
assistant message, since that is the cap `_read_turn_history` applies to one. It
is an assumption, not a bound, and it is the one term here that a provider
default could exceed.

WHAT IS A STAND-IN. `tests.token_meter` prices with `o200k_base` over the real
request body. The provider's `usage.prompt_tokens` also carries its chat template
and per-message framing, which are not public, so every figure below is a lower
bound on the input side.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.core.model_client import route_for
from app.domain.model_call import ModelCall, ModelSource
from app.domain.pricing import cost_usd
from app.services.agent_loop import MAX_MODEL_CALLS_PER_TURN, AgentTurn, run_agent_loop
from app.services.agent_prompt import (
    AGENT_NAME_MAX_CHARS,
    SOUL_LIST_ITEM_MAX_CHARS,
    SOUL_LIST_MAX_ITEMS,
    SOUL_ROLE_MAX_CHARS,
    SOUL_VOICE_MAX_CHARS,
    build_system_prompt,
)
from app.services.agent_tools import (
    _RETRIEVE_CALLS_PER_TURN_MAX,
    CHUNK_CONTENT_CHAR_LIMIT,
    MAX_CHUNKS,
    _frame_retrieved_context,
    agent_tool_definitions,
)
from tests.token_meter import TokenBilledClient, count_tokens, sample

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
JOB = "33333333-3333-3333-3333-333333333333"

# 08:30 CAT on a Tuesday. Luna is priced flat, so the instant only has to be aware.
AT = datetime(2026, 8, 25, 6, 30, tzinfo=timezone.utc)

#: The densest content the product can carry, per `tests.token_meter`.
DENSEST = "CJK"

#: Spend through the call the guard actually compares against, for the worst turn
#: the configured limits permit. MEASURED, not chosen: `_over_budget` runs at the
#: TOP of a call against rows recorded through the PREVIOUS one, so what decides
#: whether call six happens is the total after call five.
#:
#: Re-record it by driving `TestTheCeilingAgainstTheWorstPermittedTurn` and
#: reading the failure message, which prints the figure it measured.
WORST_TURN_SPEND_THROUGH_GUARD_USD = 0.200019

#: Whole-turn spend for the same shape, all six calls. Larger than the figure
#: above by exactly the last call, which is the "plus one full call" #82 recorded.
WORST_TURN_SPEND_USD = 0.244260

#: The eleven shipped tool schemas measure 2,334 tokens through `tools_wire`, and
#: they ride on every model call. The floor is under that so a schema edit does
#: not red this file, and far above the ~30 tokens an empty-schema double costs.
TOOL_SCHEMA_TOKENS_FLOOR = 2000


# ---------------------------------------------------------------------------
# The fixture: one turn at every configured maximum at once
# ---------------------------------------------------------------------------


def _luna_call(input_tokens: int, output_tokens: int) -> ModelCall:
    """One ledger row, the shape the recorder tees in live."""
    return ModelCall(
        purpose="agent_turn",
        provider="openai",
        requested_model="gpt-5.6-luna",
        served_model="gpt-5.6-luna",
        model_source=ModelSource.REPORTED,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=AT,
        tenant_id=TENANT,
    )


def _tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _completion(content=None, tool_calls=(), finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=list(tool_calls) or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _retrieve_wire(chunk_chars: int = CHUNK_CONTENT_CHAR_LIMIT) -> dict:
    """One retrieve result, built exactly the way `retrieve_tool` builds it.

    The same keys `RetrievedChunk.to_json` emits, the same `json.dumps` with
    `ensure_ascii=False`, and the same SEC-02 frame around it. Written here
    rather than mocked, because the token count of the framing and the key
    names is part of what every later call re-sends.
    """
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "document_id": f"doc-{index}",
            "content": sample(DENSEST, chunk_chars),
            "score": 0.8123,
            "rank": index,
        }
        for index in range(MAX_CHUNKS)
    ]
    framed = _frame_retrieved_context(json.dumps(chunks, ensure_ascii=False))
    return {"content": [{"type": "text", "text": framed}]}


def _maximal_agent() -> MagicMock:
    """An agent row at every soul cap at once, in the densest content."""
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.tenant_id = uuid.uuid4()
    agent.name = sample(DENSEST, AGENT_NAME_MAX_CHARS)
    agent.soul_role = sample(DENSEST, SOUL_ROLE_MAX_CHARS)
    agent.soul_voice = sample(DENSEST, SOUL_VOICE_MAX_CHARS)
    agent.soul_do_list = [sample(DENSEST, SOUL_LIST_ITEM_MAX_CHARS)] * SOUL_LIST_MAX_ITEMS
    agent.soul_donot_list = [sample(DENSEST, SOUL_LIST_ITEM_MAX_CHARS)] * SOUL_LIST_MAX_ITEMS
    return agent


def _real_tool_schemas(handler) -> tuple:
    """The eleven shipped tools, with one handler behind all of them.

    THE REAL SCHEMAS, not a double carrying `{"type": "object", "properties": {}}`.
    Roughly two thousand tokens of tool schema ride on EVERY model call of a turn,
    and a double prices that at nothing, which puts a derived ceiling well under
    the product's own floor.
    """
    return tuple(
        SimpleNamespace(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            handler=handler,
        )
        for tool in agent_tool_definitions()
    )


class _WorstTurn:
    """What one driven turn produced, with its arithmetic already done."""

    def __init__(self, out, client, calls):
        self.out = out
        self.client = client
        self.spend = [cost_usd(call)[0] for call in calls]

    @property
    def total_usd(self) -> float:
        return float(sum(self.spend, Decimal(0)))

    @property
    def through_guard_usd(self) -> float:
        """Spend the guard reads before the LAST call it allowed."""
        return float(sum(self.spend[:-1], Decimal(0)))

    def __str__(self) -> str:
        return (
            f"stop_reason={self.out['stop_reason']!r} "
            f"num_turns={self.out['num_turns']} "
            f"answer_chars={len(self.out['response_text'])} "
            f"input_tokens_per_call={self.client.input_tokens} "
            f"proxy_would_have_billed={self.client.proxy_input_tokens} "
            f"whole_turn=${self.total_usd:.6f} "
            f"through_call_{max(len(self.spend) - 1, 0)}=${self.through_guard_usd:.6f}"
        )


async def _drive(
    *,
    budget: float,
    history_rows: int,
    history_row_chars: int,
    retrieves: int,
    chunk_chars: int = CHUNK_CONTENT_CHAR_LIMIT,
) -> _WorstTurn:
    """One real `run_agent_loop`, billed per call at `o200k_base`.

    The model asks for `retrieves` retrieves on its FIRST call, then one cheap
    tool per call until the last, which answers. That is the shape that keeps the
    retrieved context on the greatest number of later calls.
    """
    from app.worker.tasks.runtime.agent import TURN_HISTORY_MAX_ROW_CHARS

    wire = _retrieve_wire(chunk_chars)
    cheap = {"content": [{"type": "text", "text": "noted"}]}

    async def handler(args):
        return wire if "query" in args else cheap

    answer = sample(DENSEST, TURN_HISTORY_MAX_ROW_CHARS)
    replies = [
        _completion(
            content=answer,
            tool_calls=[
                _tool_call(f"retrieve-{index}", "retrieve", '{"query": "return window"}')
                for index in range(retrieves)
            ],
            finish_reason="tool_calls",
        )
    ]
    replies += [
        _completion(
            content=answer,
            tool_calls=[_tool_call(f"clarify-{index}", "clarify", '{"question": "which order?"}')],
            finish_reason="tool_calls",
        )
        for index in range(MAX_MODEL_CALLS_PER_TURN - 2)
    ]
    replies.append(_completion(content=answer, finish_reason="stop"))

    calls: list[ModelCall] = []
    client = TokenBilledClient(
        calls, *replies, record=_luna_call, output_tokens=count_tokens(answer)
    )
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": sample(DENSEST, history_row_chars),
        }
        for index in range(history_rows)
    ]
    turn = AgentTurn(
        client=client,
        route=route_for("agent_turn"),
        system_prompt=build_system_prompt(_maximal_agent()),
        tools=_real_tool_schemas(handler),
        max_model_calls=MAX_MODEL_CALLS_PER_TURN,
        max_budget_usd=budget,
        calls=calls,
        ledger=lambda call: None,
    )

    async def _capture(job_id, event_type, payload, db, redis):
        return None

    with patch("app.services.agent_loop.emit_async", new=_capture):
        out = await run_agent_loop(
            sample(DENSEST, 2000),
            history=history,
            turn=turn,
            job_id=JOB,
            db=MagicMock(),
            redis=MagicMock(),
        )
    return _WorstTurn(out, client, calls)


async def _worst_permitted_turn(budget: float) -> _WorstTurn:
    """Every configured maximum at once."""
    from app.worker.tasks.runtime.agent import (
        TURN_HISTORY_MAX_MESSAGES,
        TURN_HISTORY_MAX_ROW_CHARS,
    )

    return await _drive(
        budget=budget,
        history_rows=TURN_HISTORY_MAX_MESSAGES,
        history_row_chars=TURN_HISTORY_MAX_ROW_CHARS,
        retrieves=_RETRIEVE_CALLS_PER_TURN_MAX,
    )


# ---------------------------------------------------------------------------
# The ceiling, in both directions
# ---------------------------------------------------------------------------


class TestTheCeilingAgainstTheWorstPermittedTurn:
    """The shipped ceiling may not cut a turn the configuration calls servable."""

    async def test_the_worst_permitted_turn_reaches_its_answer(self):
        """The direction both reverted numbers failed.

        `MAX_MODEL_CALLS_PER_TURN` declares six calls servable, so the budget
        guard may not stop the turn before them. An exhausted turn joins
        `response_text` to "" and `_persist_messages` writes an empty assistant
        row, which is the row `_read_turn_history` then has to filter out of the
        next turn's context.
        """
        turn = await _worst_permitted_turn(settings.AGENT_MAX_BUDGET_USD)

        assert turn.out["stop_reason"] != "budget_exceeded", (
            f"the shipped ceiling ${settings.AGENT_MAX_BUDGET_USD} cut the worst "
            f"turn the configured limits permit. {turn}"
        )
        assert turn.out["num_turns"] == MAX_MODEL_CALLS_PER_TURN
        assert turn.out["response_text"], (
            f"the turn ran to its end and served nothing. {turn}"
        )

    async def test_that_fixture_really_is_the_worst_turn(self):
        """The half neither reverted attempt had, and why they could not tell.

        Without a lower bound on what the fixture ITSELF cost, the test above is
        satisfied by any turn at all: halve the history, empty the tool schemas,
        drop seven of the eight retrieves, and it still passes while measuring
        something the product never runs. This pins the measurement the ceiling
        was derived from, so shrinking the fixture is red.
        """
        turn = await _worst_permitted_turn(settings.AGENT_MAX_BUDGET_USD)

        assert turn.through_guard_usd >= WORST_TURN_SPEND_THROUGH_GUARD_USD * 0.98, (
            f"this fixture spent ${turn.through_guard_usd:.6f} through the call "
            f"the guard reads, against the ${WORST_TURN_SPEND_THROUGH_GUARD_USD:.6f} "
            f"AGENT_MAX_BUDGET_USD was derived from. Something in the turn got "
            f"smaller, so the ceiling above it is no longer measured. {turn}"
        )
        assert turn.total_usd >= WORST_TURN_SPEND_USD * 0.98, (
            f"the whole turn spent ${turn.total_usd:.6f} against a recorded "
            f"${WORST_TURN_SPEND_USD:.6f}. {turn}"
        )

    async def test_the_eleven_real_tool_schemas_ride_on_every_call(self):
        """The one term the lower bound above is too coarse to police.

        `tools_wire` of the eleven shipped tools is 2,334 tokens, and it is
        re-sent on every model call. That is about 1% of this turn, so swapping
        the real schemas for a double carrying `{"type": "object", "properties": {}}`
        moves the total by less than the tolerance on
        `test_that_fixture_really_is_the_worst_turn` and leaves it green. It was
        the largest term in the FIRST measurement that set this constant, so it
        gets its own floor rather than being folded into a total.
        """
        turn = await _worst_permitted_turn(settings.AGENT_MAX_BUDGET_USD)

        assert len(turn.client.requests) == MAX_MODEL_CALLS_PER_TURN
        for index, request in enumerate(turn.client.requests):
            schema_tokens = count_tokens(json.dumps(request["tools"], ensure_ascii=False))
            assert schema_tokens >= TOOL_SCHEMA_TOKENS_FLOOR, (
                f"call {index + 1} carried {schema_tokens} tokens of tool schema "
                f"against a floor of {TOOL_SCHEMA_TOKENS_FLOOR}. A double with an "
                "empty input_schema prices this at nothing and puts the derived "
                "ceiling under the product's own floor."
            )

    async def test_the_ceiling_leaves_the_headroom_the_field_comment_claims(self):
        """The constant is a multiple of the measurement, and this is the multiple."""
        turn = await _worst_permitted_turn(settings.AGENT_MAX_BUDGET_USD)

        headroom = settings.AGENT_MAX_BUDGET_USD / turn.through_guard_usd
        assert headroom >= 1.5, (
            f"AGENT_MAX_BUDGET_USD is ${settings.AGENT_MAX_BUDGET_USD}, only "
            f"{headroom:.2f} times the ${turn.through_guard_usd:.6f} the worst "
            "permitted turn spends through the call the guard reads. Under 1.5 a "
            "provider that counts its own framing puts an ordinary turn over."
        )

    async def test_a_turn_beyond_every_configured_limit_is_stopped(self):
        """The other direction. A ceiling that cannot fire is a note, not a gate (#82).

        Ten times `CHUNK_CONTENT_CHAR_LIMIT` per chunk is a context no configured
        limit can produce, so this is the runaway T-04-03-06 names and not an
        expensive ordinary turn.
        """
        turn = await _drive(
            budget=settings.AGENT_MAX_BUDGET_USD,
            history_rows=40,
            history_row_chars=4000,
            retrieves=_RETRIEVE_CALLS_PER_TURN_MAX,
            chunk_chars=CHUNK_CONTENT_CHAR_LIMIT * 10,
        )

        assert turn.out["stop_reason"] == "budget_exceeded", (
            f"a context ten times the configured per-chunk limit ran to its end "
            f"under the shipped ceiling. {turn}"
        )
        assert turn.out["num_turns"] < MAX_MODEL_CALLS_PER_TURN

    async def test_the_first_call_is_never_blocked_however_large_the_ceiling_is(self):
        """A turn that has spent nothing yet cannot be over its own ceiling.

        The runaway above is stopped mid-turn rather than refused, which is the
        behaviour a customer feels: the guard reads spend recorded through the
        PREVIOUS call, so the effective ceiling is the constant plus one full
        call and the first call always happens.
        """
        turn = await _drive(
            budget=0.0,
            history_rows=2,
            history_row_chars=100,
            retrieves=1,
        )

        assert turn.out["num_turns"] == 1
        assert turn.out["stop_reason"] == "budget_exceeded"
