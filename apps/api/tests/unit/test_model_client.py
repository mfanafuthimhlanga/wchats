"""Tests for app.core.model_client, the ledger seam on the HTTP layer (ticket #46, issue #22).

WHAT THE HOOK HAS TO SURVIVE
    #47 wraps this client in `instructor.from_anthropic(...)`, and Ragas wraps that
    again. A recorder bolted onto a wrapper method disappears the moment someone
    wraps it once more, so the hook sits on the httpx client and every one of those
    layers passes its traffic through it. The tests below therefore drive the hook
    two ways. `attach_ledger_hook` runs against a bare `httpx.Client` that this
    module never constructed, and `make_client` runs through the real anthropic SDK.

WHAT AN ABSENT `model` FIELD BECOMES
    `unreported`, and served_model carries the requested alias. `reported` would
    credit the provider with a name it never sent, and `mapped_by_docs` would read
    the published mapping for a body that echoed nothing, so both invent a
    provenance. The tests below drive both providers through that case.

WHY A MOCK TRANSPORT AND NOT A MOCK CLIENT
    A stubbed `messages.create` proves the call site calls it. It proves nothing
    about the bytes a provider sends back, which is where `usage` and `model` live.
    `httpx.MockTransport` returns a canned response body at the layer the hook reads,
    so these tests fail if the hook stops parsing what a provider actually returns.

FAIL OPEN IS ASSERTED, NOT ASSUMED
    A recorder that raises must not fail the model call. The test that pins this
    raises from the recorder and then asserts the caller still got its response,
    and that one structured event names the purpose and the tenant.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from decimal import Decimal

import anthropic
import httpx
import openai
import pytest
import structlog
from pydantic import BaseModel

from app.core.config import settings
from app.core.model_client import (
    PURPOSE_ROUTES,
    CallContext,
    Credentials,
    LedgerContext,
    ModelRoute,
    UnknownPurpose,
    UnsupportedProvider,
    attach_async_ledger_hook,
    attach_ledger_hook,
    make_async_client,
    make_client,
    make_instructor_client,
    record_model_call,
    route_for,
    served_model_for,
)
from app.domain.model_call import ModelSource
from app.domain.pricing import cost_usd, cost_zar

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

TENANT = "11111111-1111-1111-1111-111111111111"
AGENT = "22222222-2222-2222-2222-222222222222"
JOB = "33333333-3333-3333-3333-333333333333"

# 08:30 CAT on a Tuesday, inside the 08:00-12:00 peak window the price book declares.
AT = datetime(2026, 8, 25, 6, 30, tzinfo=timezone.utc)


def _context(purpose: str = "retrieval_strategist") -> CallContext:
    return CallContext(purpose=purpose, tenant_id=TENANT, agent_id=AGENT, job_id=JOB)


def _body(model: str = "deepseek-v4-flash", **usage) -> dict:
    """A messages response shaped the way a provider sends one."""
    counts = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 300,
    }
    counts.update(usage)
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": counts,
    }


def _transport(body: dict, status: int = 200, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json=body, headers=headers or {"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


def _hooked_client(recorder, body: dict, **kwargs):
    """A bare httpx client this module did not construct, with the hook bolted on."""
    client = httpx.Client(transport=_transport(body, **kwargs))
    attach_ledger_hook(
        client, _context(), provider="deepseek", recorder=recorder, clock=lambda: AT
    )
    return client


def _post(client: httpx.Client, requested: str = "claude-sonnet-4-6") -> httpx.Response:
    return client.post(
        "https://provider.example/v1/messages",
        json={"model": requested, "max_tokens": 500, "messages": []},
    )


# ---------------------------------------------------------------------------
# The hook reads what the provider sent
# ---------------------------------------------------------------------------


class TestTheHookRecordsOneCall:
    def test_one_response_yields_one_call_with_the_provider_counts(self):
        recorded = []
        client = _hooked_client(recorded.append, _body())
        _post(client)

        assert len(recorded) == 1, f"expected one ledger row, got {len(recorded)}"
        call = recorded[0]
        assert call.input_tokens == 1000
        assert call.output_tokens == 500
        assert call.cache_read_tokens == 2000
        assert call.cache_creation_tokens == 300

    def test_the_call_carries_the_ids_the_factory_was_given(self):
        recorded = []
        _post(_hooked_client(recorded.append, _body()))

        call = recorded[0]
        assert call.tenant_id == TENANT
        assert call.agent_id == AGENT
        assert call.job_id == JOB
        assert call.purpose == "retrieval_strategist"
        assert call.provider == "deepseek"

    def test_the_requested_model_comes_off_the_request_body(self):
        """The alias the call site asked for, which the response may or may not echo."""
        recorded = []
        client = _hooked_client(recorded.append, _body())
        _post(client, requested="claude-haiku-4-5")

        assert recorded[0].requested_model == "claude-haiku-4-5"

    def test_at_is_utc_and_aware(self):
        recorded = []
        _post(_hooked_client(recorded.append, _body()))

        assert recorded[0].at == AT
        assert recorded[0].at.tzinfo is not None

    def test_the_caller_still_reads_the_body_after_the_hook_read_it(self):
        """The hook calls read(). A caller that then gets an empty body is a broken client."""
        response = _post(_hooked_client(lambda call: None, _body()))

        assert response.json()["usage"]["input_tokens"] == 1000


# ---------------------------------------------------------------------------
# Which model actually ran
# ---------------------------------------------------------------------------


class TestServedModel:
    def test_a_provider_model_in_the_body_is_reported(self):
        served, source = served_model_for(
            "deepseek", "claude-sonnet-4-6", "deepseek-v4-flash"
        )
        assert served == "deepseek-v4-flash"
        assert source is ModelSource.REPORTED

    def test_an_echoed_alias_falls_back_to_the_published_mapping(self):
        served, source = served_model_for(
            "deepseek", "claude-sonnet-4-6", "claude-sonnet-4-6"
        )
        assert served == "deepseek-v4-flash"
        assert source is ModelSource.MAPPED_BY_DOCS

    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("claude-haiku-4-5", "deepseek-v4-flash"),
            ("claude-haiku-4-5-20251001", "deepseek-v4-flash"),
            ("claude-sonnet-4-6", "deepseek-v4-flash"),
            ("claude-opus-4-1", "deepseek-v4-pro"),
        ],
    )
    def test_the_published_deepseek_mapping_covers_every_alias_this_repo_sends(
        self, alias, expected
    ):
        served, source = served_model_for("deepseek", alias, alias)
        assert served == expected
        assert source is ModelSource.MAPPED_BY_DOCS

    def test_an_anthropic_echo_is_reported_because_the_alias_is_the_model(self):
        served, source = served_model_for(
            "anthropic", "claude-haiku-4-5", "claude-haiku-4-5"
        )
        assert served == "claude-haiku-4-5"
        assert source is ModelSource.REPORTED

    def test_an_anthropic_body_that_named_no_model_is_unreported(self):
        """The provider echoed nothing, so `reported` would name a fact nobody stated."""
        served, source = served_model_for("anthropic", "claude-haiku-4-5", None)
        assert served == "claude-haiku-4-5"
        assert source is ModelSource.UNREPORTED

    def test_a_deepseek_body_that_named_no_model_is_unreported(self):
        """The mapping answers an echoed alias. An absent field echoes nothing."""
        served, source = served_model_for("deepseek", "claude-haiku-4-5", None)
        assert served == "claude-haiku-4-5"
        assert source is ModelSource.UNREPORTED

    def test_the_hook_puts_the_source_on_the_row(self):
        recorded = []
        client = _hooked_client(recorded.append, _body(model="claude-sonnet-4-6"))
        _post(client, requested="claude-sonnet-4-6")

        assert recorded[0].served_model == "deepseek-v4-flash"
        assert recorded[0].model_source is ModelSource.MAPPED_BY_DOCS


# ---------------------------------------------------------------------------
# What the hook refuses to record
# ---------------------------------------------------------------------------


class TestTheHookStaysQuiet:
    def test_an_error_response_records_nothing(self):
        recorded = []
        client = _hooked_client(recorded.append, {"error": {"type": "overloaded"}}, status=429)
        _post(client)

        assert recorded == [], "an HTTP 429 spent no tokens and must not bill a tenant"

    def test_a_body_without_usage_records_nothing(self):
        recorded = []
        client = _hooked_client(recorded.append, {"input_tokens": 4})
        _post(client)

        assert recorded == [], "a token-count endpoint reports no spend"

    def test_a_streamed_response_is_left_unread(self):
        """Reading an SSE body inside the hook would consume the caller's stream."""
        recorded = []
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"event: message_start\ndata: {}\n\n",
                    headers={"content-type": "text/event-stream"},
                )
            )
        )
        attach_ledger_hook(
            client, _context(), provider="deepseek", recorder=recorded.append, clock=lambda: AT
        )

        with client.stream("POST", "https://provider.example/v1/messages") as response:
            chunks = list(response.iter_bytes())

        assert recorded == []
        assert b"message_start" in b"".join(chunks)

    def test_a_streamed_response_logs_the_ledger_gap_it_leaves(self):
        """A stream spends tokens the ledger never sees, so the skip has to be findable."""
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"event: message_start\ndata: {}\n\n",
                    headers={"content-type": "text/event-stream"},
                )
            )
        )
        attach_ledger_hook(
            client, _context(), provider="deepseek", recorder=lambda call: None, clock=lambda: AT
        )

        with structlog.testing.capture_logs() as logs:
            with client.stream(
                "POST",
                "https://provider.example/v1/messages",
                json={"model": "claude-haiku-4-5", "stream": True, "messages": []},
            ) as response:
                list(response.iter_bytes())

        skipped = [entry for entry in logs if entry["event"] == "model_ledger.stream_skipped"]
        assert len(skipped) == 1, f"expected one event naming the skipped stream, got {logs}"
        assert skipped[0]["purpose"] == "retrieval_strategist"
        assert skipped[0]["requested_model"] == "claude-haiku-4-5"

    def test_an_unstreamed_response_logs_no_stream_skip(self):
        with structlog.testing.capture_logs() as logs:
            _post(_hooked_client(lambda call: None, _body()))

        assert [entry for entry in logs if entry["event"] == "model_ledger.stream_skipped"] == []


# ---------------------------------------------------------------------------
# Fail open
# ---------------------------------------------------------------------------


class TestRecordingFailureIsFailOpen:
    def test_a_recorder_that_raises_does_not_fail_the_model_call(self):
        def explode(call):
            raise RuntimeError("the ledger is down")

        response = _post(_hooked_client(explode, _body()))

        assert response.status_code == 200
        assert response.json()["usage"]["output_tokens"] == 500

    def test_the_failure_logs_one_event_naming_the_purpose_and_the_tenant(self):
        def explode(call):
            raise RuntimeError("the ledger is down")

        with structlog.testing.capture_logs() as logs:
            _post(_hooked_client(explode, _body()))

        failures = [entry for entry in logs if entry["event"] == "model_ledger.record_failed"]
        assert len(failures) == 1, f"expected one loud event, got {logs}"
        assert failures[0]["purpose"] == "retrieval_strategist"
        assert failures[0]["tenant_id"] == TENANT
        assert failures[0]["log_level"] == "error"

    def test_a_malformed_body_is_swallowed_the_same_way(self):
        recorded = []
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b"not json", headers={"content-type": "application/json"}
                )
            )
        )
        attach_ledger_hook(
            client, _context(), provider="deepseek", recorder=recorded.append, clock=lambda: AT
        )

        response = client.post("https://provider.example/v1/messages", json={"model": "x"})

        assert response.status_code == 200
        assert recorded == []


# ---------------------------------------------------------------------------
# record_model_call
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))


class _Connection:
    def __init__(self):
        self.cur = _Cursor()
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class TestRecordModelCall:
    def _call(self):
        recorded = []
        _post(_hooked_client(recorded.append, _body()))
        return recorded[0]

    def test_one_call_writes_one_row(self):
        conn = _Connection()
        record_model_call(self._call(), conn)

        assert len(conn.cur.statements) == 1
        sql, _ = conn.cur.statements[0]
        assert sql.upper().startswith("INSERT INTO MODEL_CALLS")

    def test_every_field_reaches_the_row(self):
        conn = _Connection()
        call = self._call()
        record_model_call(call, conn)

        _, params = conn.cur.statements[0]
        assert call.purpose in params
        assert call.provider in params
        assert call.served_model in params
        assert call.model_source.value in params
        assert call.input_tokens in params
        assert call.cache_creation_tokens in params
        assert call.at in params
        assert call.tenant_id in params

    def test_the_caller_owns_an_open_connection(self):
        """A connection handed in is not committed or closed by the ledger."""
        conn = _Connection()
        record_model_call(self._call(), conn)

        assert conn.commits == 0

    def test_no_connection_string_is_stored(self):
        conn = _Connection()
        record_model_call(self._call(), conn)

        _, params = conn.cur.statements[0]
        text = " ".join(str(p) for p in params)
        assert "postgres" not in text
        assert "://" not in text


# ---------------------------------------------------------------------------
# make_client, and the end to end proof
# ---------------------------------------------------------------------------


class TestMakeClient:
    def _client(self, recorder, body=None, purpose="retrieval_strategist"):
        return make_client(
            purpose,
            tenant_id=TENANT,
            agent_id=AGENT,
            job_id=JOB,
            recorder=recorder,
            provider="deepseek",
            credentials=Credentials(api_key="test-key", base_url="https://provider.example"),
            http_client=httpx.Client(transport=_transport(body or _body())),
            clock=lambda: AT,
        )

    def test_a_call_through_the_sdk_lands_exactly_one_ledger_row(self):
        recorded = []
        client = self._client(recorded.append)

        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": "hello"}],
        )

        assert len(recorded) == 1, f"expected one ledger row, got {len(recorded)}"

    def test_the_row_carries_the_tokens_the_response_reported(self):
        recorded = []
        self._client(recorded.append).messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": "hello"}],
        )

        call = recorded[0]
        assert (call.input_tokens, call.output_tokens) == (1000, 500)
        assert (call.cache_read_tokens, call.cache_creation_tokens) == (2000, 300)
        assert call.served_model == "deepseek-v4-flash"
        assert call.model_source is ModelSource.REPORTED
        assert call.requested_model == "claude-sonnet-4-6"

    def test_the_derived_money_matches_the_seeded_price_book(self):
        """The acceptance criterion for #46 slice B.

        Peak window, so 1000 fresh input at $0.44, 500 output at $1.32, 2000 cache
        reads at $0.088 and 300 cache writes at $0.44, all per million. That is
        440 + 660 + 176 + 132 = 1408 millionths of a dollar.
        """
        recorded = []
        self._client(recorded.append).messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": "hello"}],
        )

        usd, price_version = cost_usd(recorded[0])
        zar, fx_version = cost_zar(recorded[0])

        assert usd == Decimal("0.001408"), f"priced at {usd}"
        assert price_version == "2026-08-23.1"
        assert zar == Decimal("0.0225613696"), f"priced at {zar}"
        assert fx_version == "usd_zar-2026-08-24"

    def test_the_hook_rides_a_client_the_factory_did_not_construct(self):
        """#47 wraps this client in instructor. The hook has to survive that."""
        recorded = []
        supplied = httpx.Client(transport=_transport(_body()))
        client = make_client(
            "scenario_generation",
            tenant_id=TENANT,
            recorder=recorded.append,
            provider="deepseek",
            credentials=Credentials(api_key="test-key", base_url="https://provider.example"),
            http_client=supplied,
            clock=lambda: AT,
        )

        client.messages.create(
            model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": "x"}]
        )

        assert len(recorded) == 1
        assert recorded[0].purpose == "scenario_generation"
        assert recorded[0].agent_id is None, "a platform call names no agent"

    def test_the_request_the_sdk_sent_is_what_the_row_reports(self):
        """Proof the hook reads the wire and not the call site's arguments."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_body())

        recorded = []
        client = make_client(
            "scenario_generation",
            tenant_id=TENANT,
            recorder=recorded.append,
            provider="deepseek",
            credentials=Credentials(api_key="test-key", base_url="https://provider.example"),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: AT,
        )
        client.messages.create(
            model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": "x"}]
        )

        assert seen["model"] == "claude-haiku-4-5"
        assert recorded[0].requested_model == seen["model"]


# ---------------------------------------------------------------------------
# The OpenAI shape, beside the Anthropic one
# ---------------------------------------------------------------------------
# Field names read off the installed SDK, `openai 2.45.0`, in
# `.venv/Lib/site-packages/openai/types/completion_usage.py`. `CompletionUsage`
# declares `prompt_tokens`, `completion_tokens`, `total_tokens` and an optional
# `prompt_tokens_details`, whose `PromptTokensDetails` declares `cached_tokens`.
# `prompt_tokens` COUNTS the cached ones, so fresh input is the difference and
# adding the two counts would bill the cached tokens twice.

LUNA = "gpt-5.6-luna"


def _openai_body(model: str = LUNA, usage: dict | None = None) -> dict:
    """A chat-completion response shaped the way OpenAI sends one."""
    counts = {
        "prompt_tokens": 1200,
        "completion_tokens": 500,
        "total_tokens": 1700,
        "prompt_tokens_details": {"cached_tokens": 200},
    }
    if usage is not None:
        counts = usage
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": counts,
    }


def _openai_hooked(recorder, body: dict, **kwargs):
    client = httpx.Client(transport=_transport(body, **kwargs))
    attach_ledger_hook(
        client,
        _context("judge_faithfulness"),
        provider="openai",
        recorder=recorder,
        clock=lambda: AT,
    )
    return client


def _openai_post(client: httpx.Client, requested: str = LUNA) -> httpx.Response:
    return client.post(
        "https://api.openai.example/v1/chat/completions",
        json={"model": requested, "messages": []},
    )


class TestTheOpenAiShape:
    def test_fresh_input_is_prompt_tokens_less_the_cached_ones(self):
        """1200 prompt tokens of which 200 were cached leaves 1000 fresh."""
        recorded = []
        _openai_post(_openai_hooked(recorded.append, _openai_body()))

        assert recorded[0].input_tokens == 1000

    def test_completion_tokens_are_the_output(self):
        recorded = []
        _openai_post(_openai_hooked(recorded.append, _openai_body()))

        assert recorded[0].output_tokens == 500

    def test_cached_tokens_are_the_cache_read(self):
        recorded = []
        _openai_post(_openai_hooked(recorded.append, _openai_body()))

        assert recorded[0].cache_read_tokens == 200

    def test_cache_creation_is_zero_because_the_provider_bills_no_write(self):
        recorded = []
        _openai_post(_openai_hooked(recorded.append, _openai_body()))

        assert recorded[0].cache_creation_tokens == 0

    def test_a_body_with_no_prompt_tokens_details_reads_every_prompt_token_as_fresh(self):
        """The field is optional in the SDK, so its absence must not read as a negative."""
        recorded = []
        _openai_post(
            _openai_hooked(
                recorded.append,
                _openai_body(
                    usage={"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000}
                ),
            )
        )

        assert (recorded[0].input_tokens, recorded[0].cache_read_tokens) == (900, 0)

    def test_a_null_prompt_tokens_details_is_the_same_as_an_absent_one(self):
        recorded = []
        _openai_post(
            _openai_hooked(
                recorded.append,
                _openai_body(
                    usage={
                        "prompt_tokens": 900,
                        "completion_tokens": 100,
                        "total_tokens": 1000,
                        "prompt_tokens_details": None,
                    }
                ),
            )
        )

        assert (recorded[0].input_tokens, recorded[0].cache_read_tokens) == (900, 0)

    def test_more_cached_tokens_than_prompt_tokens_never_records_a_negative(self):
        """ModelCall refuses a negative count, so a nonsense body has to clamp at zero."""
        recorded = []
        _openai_post(
            _openai_hooked(
                recorded.append,
                _openai_body(
                    usage={
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "total_tokens": 110,
                        "prompt_tokens_details": {"cached_tokens": 400},
                    }
                ),
            )
        )

        assert recorded[0].input_tokens == 0

    def test_the_served_model_is_what_openai_named(self):
        recorded = []
        _openai_post(_openai_hooked(recorded.append, _openai_body()))

        assert recorded[0].served_model == LUNA
        assert recorded[0].model_source is ModelSource.REPORTED
        assert recorded[0].provider == "openai"

    def test_the_row_prices_against_the_flat_luna_entries(self):
        """1000 fresh at $0.20/M, 500 out at $1.20/M, 200 cache reads at $0.20/M.

        200 + 600 + 40 = 840 millionths of a dollar, and the peak window the
        DeepSeek book declares does not move any of them.
        """
        recorded = []
        _openai_post(_openai_hooked(recorded.append, _openai_body()))

        usd, price_version = cost_usd(recorded[0])
        assert usd == Decimal("0.00084"), f"priced at {usd}"
        assert price_version == "2026-08-23.1"

    def test_an_anthropic_body_still_records_the_anthropic_way(self):
        """Adding the second shape may not cost the first one."""
        recorded = []
        _post(_hooked_client(recorded.append, _body()))

        call = recorded[0]
        assert (call.input_tokens, call.output_tokens) == (1000, 500)
        assert (call.cache_read_tokens, call.cache_creation_tokens) == (2000, 300)


class TestABodyMatchingNeitherShape:
    NEITHER = {"model": LUNA, "usage": {"tokens_spent": 42}}

    def test_it_records_nothing(self):
        recorded = []
        _openai_post(_openai_hooked(recorded.append, self.NEITHER))

        assert recorded == [], "a usage block nobody can read must not be guessed at"

    def test_it_logs_the_gap_it_leaves(self):
        """The treatment a skipped stream gets: a hole somebody can count."""
        with structlog.testing.capture_logs() as logs:
            _openai_post(_openai_hooked(lambda call: None, self.NEITHER))

        skipped = [entry for entry in logs if entry["event"] == "model_ledger.shape_skipped"]
        assert len(skipped) == 1, f"expected one event naming the unreadable body, got {logs}"
        assert skipped[0]["purpose"] == "judge_faithfulness"
        assert skipped[0]["requested_model"] == LUNA
        assert skipped[0]["tenant_id"] == TENANT

    def test_a_body_with_no_usage_at_all_stays_silent(self):
        """A token-count endpoint reports no spend. That is not a gap in the ledger."""
        with structlog.testing.capture_logs() as logs:
            _openai_post(_openai_hooked(lambda call: None, {"input_tokens": 4}))

        assert [entry for entry in logs if entry["event"] == "model_ledger.shape_skipped"] == []

    def test_a_readable_body_logs_no_gap(self):
        with structlog.testing.capture_logs() as logs:
            _openai_post(_openai_hooked(lambda call: None, _openai_body()))

        assert [entry for entry in logs if entry["event"] == "model_ledger.shape_skipped"] == []


class TestTheAsyncHookFailsOpenToo:
    """The async twin of TestRecordingFailureIsFailOpen, on the client Ragas drives.

    The sync hook and the async hook each carry their own try/except around the
    same shared body, so a guard deleted from one is invisible to every test of
    the other. Deleting the async one broke nothing until this class existed.
    It builds the async OpenAI client through the factory, which is what every
    judge call in an eval run runs through.
    """

    def _client(self, recorder):
        return make_async_client(
            "judge_faithfulness",
            tenant_id=TENANT,
            recorder=recorder,
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.AsyncClient(transport=_transport(_openai_body())),
            clock=lambda: AT,
        )

    async def test_a_recorder_that_raises_does_not_fail_the_async_model_call(self):
        def explode(call):
            raise RuntimeError("the ledger is down")

        answer = await self._client(explode).chat.completions.create(
            model=LUNA, messages=[{"role": "user", "content": "grounded?"}]
        )

        assert answer.choices[0].message.content == "ok"
        assert answer.usage.completion_tokens == 500

    async def test_the_async_failure_logs_one_event_naming_the_purpose_and_the_tenant(self):
        def explode(call):
            raise RuntimeError("the ledger is down")

        with structlog.testing.capture_logs() as logs:
            await self._client(explode).chat.completions.create(
                model=LUNA, messages=[{"role": "user", "content": "grounded?"}]
            )

        failures = [entry for entry in logs if entry["event"] == "model_ledger.record_failed"]
        assert len(failures) == 1, f"expected one loud event, got {logs}"
        assert failures[0]["purpose"] == "judge_faithfulness"
        assert failures[0]["tenant_id"] == TENANT
        assert failures[0]["log_level"] == "error"

    async def test_a_malformed_async_body_is_swallowed_the_same_way(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b"not json", headers={"content-type": "application/json"}
                )
            )
        )
        attach_async_ledger_hook(
            client,
            _context("judge_faithfulness"),
            provider="openai",
            recorder=lambda call: None,
            clock=lambda: AT,
        )

        response = await client.post(
            "https://api.openai.example/v1/chat/completions", json={"model": LUNA}
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# The purpose routing table
# ---------------------------------------------------------------------------

#: Every purpose the direct-API half calls a model for, spelled out here rather
#: than read out of the table, so a row deleted from the table fails this file.
EVERY_PURPOSE = [
    "judge_faithfulness",
    "judge_answer_relevancy",
    "judge_context_precision",
    "judge_context_recall",
    "judge_retrieval_faithfulness",
    "scenario_generation",
    "metadata_enrichment",
    "actor_gate",
    "red_team_prompt",
    "red_team_probe",
    "red_team_severity",
    "query_expansion",
    "retrieval_strategist",
    "strategist",
    "gatekeeper",
    "auditor",
    # Ticket #48. The owned loop builds its client here like every other purpose,
    # so the Agent turn leaves a `model_calls` row on every iteration.
    "agent_turn",
    # Ticket #49. The deployment Orchestrator's prose turn ran on the Agent SDK
    # against a model no route named, so a checklist run could not report what
    # its own assessment cost. It bills like everything else now.
    "deployment_orchestrator",
    # Ticket #154. The judge the calibration harness correlates against the
    # owner's labels lives under `tests/`, which is why #153 stopped short of it
    # and it kept building its own Anthropic client.
    "calibration_judge",
]

#: The purposes decision #34 priced at effort `none`.
JUDGE_PURPOSES = [
    "judge_faithfulness",
    "judge_answer_relevancy",
    "judge_context_precision",
    "judge_context_recall",
    "judge_retrieval_faithfulness",
]

#: Every purpose runs at effort `none` since 2026-09-05. The judges and the
#: Agent turn were priced there by decision #34; the rest landed there because
#: the provider refuses a tool-bearing chat completion that sends any other
#: effort, or none at all. See `TestTheRawPathCarriesTheRouteEffort`.
EFFORT_NONE_PURPOSES = list(EVERY_PURPOSE)


class TestTheRoutingTable:
    @pytest.mark.parametrize("purpose", EVERY_PURPOSE)
    def test_every_direct_api_purpose_routes_to_luna_on_openai(self, purpose):
        route = route_for(purpose)

        assert route.provider == "openai"
        assert route.model == LUNA

    @pytest.mark.parametrize("purpose", JUDGE_PURPOSES)
    def test_a_judge_runs_at_effort_none(self, purpose):
        """The $0.62 per thousand floor holds only at effort none (decision #34)."""
        assert route_for(purpose).reasoning_effort == "none"

    @pytest.mark.parametrize("purpose", EFFORT_NONE_PURPOSES)
    def test_every_purpose_names_effort_none(self, purpose):
        """OBSERVED 2026-09-05 on staging: `400 Function tools with reasoning_effort
        are not supported for gpt-5.6-luna in /v1/chat/completions. To use function
        tools, use /v1/responses or set reasoning_effort to 'none'.` for a call
        that sent no effort field. A route naming no effort is a route the
        provider refuses the moment a tool is attached."""
        assert route_for(purpose).reasoning_effort == "none"

    def test_the_agent_turn_runs_at_effort_none_too(self):
        """Decision #34 prices the turn at $0.76 per thousand, and only at effort none."""
        assert route_for("agent_turn").reasoning_effort == "none"

    def test_the_agent_turn_reads_its_model_from_the_one_constant(self):
        """A second literal would attribute an eval score to a model that never ran."""
        from app.core.config import AGENT_TURN_MODEL

        route = route_for("agent_turn")
        assert route.model == AGENT_TURN_MODEL
        assert route.provider == "openai"

    def test_the_table_covers_exactly_these_purposes(self):
        assert sorted(PURPOSE_ROUTES) == sorted(EVERY_PURPOSE)

    def test_an_unknown_purpose_raises(self):
        with pytest.raises(UnknownPurpose, match="spellcheck"):
            route_for("spellcheck")

    def test_the_message_names_what_the_table_does_hold(self):
        with pytest.raises(UnknownPurpose, match="judge_faithfulness"):
            route_for("judge_faithfullness")

    def test_unknown_purpose_is_a_lookup_error(self):
        assert issubclass(UnknownPurpose, LookupError)

    def test_a_route_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            route_for("auditor").model = "gpt-5-mini"  # type: ignore[misc]

    def test_the_table_itself_refuses_a_write(self):
        """A frozen route still sits in a dict anyone can re-point.

        Rebinding a key here would send a purpose to a model nobody chose, and
        every reader downstream would report the new one as what ran.
        """
        with pytest.raises(TypeError):
            PURPOSE_ROUTES["judge_faithfulness"] = ModelRoute(  # type: ignore[index]
                provider="openai", model="gpt-5-mini"
            )

    def test_the_table_refuses_a_new_purpose_too(self):
        with pytest.raises(TypeError):
            PURPOSE_ROUTES["spellcheck"] = ModelRoute(  # type: ignore[index]
                provider="openai", model=LUNA
            )


# ---------------------------------------------------------------------------
# What the raw client path refuses, and what it carries
# ---------------------------------------------------------------------------


class TestTheRawPathChecksThePurpose:
    """`make_client` reads the routing table before it builds anything.

    Until it did, a purpose was a free-text string that reached the ledger
    unread. A typo billed a real tenant under a name no rollup groups.
    """

    def _ledger(self) -> LedgerContext:
        return LedgerContext(tenant_id=TENANT, recorder=lambda call: None)

    def test_a_mistyped_purpose_raises_before_a_client_is_built(self):
        with pytest.raises(UnknownPurpose, match="spellcheck"):
            make_client("spellcheck", tenant_id=TENANT, recorder=lambda call: None)

    def test_the_refusal_names_the_purposes_the_table_does_hold(self):
        with pytest.raises(UnknownPurpose, match="metadata_enrichment"):
            make_client("metadata_enrichement", tenant_id=TENANT, recorder=lambda call: None)

    def test_the_ledger_shortcut_is_checked_the_same_way(self):
        with pytest.raises(UnknownPurpose, match="spellcheck"):
            self._ledger().client("spellcheck")

    def test_a_direct_api_purpose_builds_on_the_raw_path(self):
        client = make_client(
            "actor_gate",
            tenant_id=TENANT,
            recorder=lambda call: None,
            provider="openai",
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=_transport(_openai_body())),
        )

        assert isinstance(client, openai.OpenAI)


class TestTheRawPathCarriesTheRouteEffort:
    """The route's effort reaches the wire on the raw seam, not only through instructor.

    OBSERVED 2026-09-05 on staging, worker-runtime, agent ee8087ed: every raw call
    that attached a tool (scenario generation, the red team's attacker and probe,
    the deployment Orchestrator, the validation trio) came back `400 Function
    tools with reasoning_effort are not supported for gpt-5.6-luna in
    /v1/chat/completions. To use function tools, use /v1/responses or set
    reasoning_effort to 'none'.` The raw client sent no effort field, because
    the OpenAI SDK has no default body parameter and `make_client` refused any
    route that named one. Reproduced from this box the same day: `none` with
    tools succeeds, no field with tools is refused.

    Nothing here touches a socket. The transport records the body the SDK built.
    """

    def _client(self, seen: dict, purpose="scenario_generation"):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_body())

        return make_client(
            purpose,
            tenant_id=TENANT,
            recorder=lambda call: None,
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: AT,
        )

    def _async_client(self, seen: dict, purpose="agent_turn"):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_body())

        return make_async_client(
            purpose,
            tenant_id=TENANT,
            recorder=lambda call: None,
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            clock=lambda: AT,
        )

    _TOOL = {
        "type": "function",
        "function": {"name": "f", "description": "noop", "parameters": {"type": "object"}},
    }
    _MESSAGES = [{"role": "user", "content": "hello"}]

    def test_a_tool_bearing_raw_call_goes_out_at_effort_none(self):
        """The exact request shape staging refused: tools attached, no effort named."""
        seen: dict = {}
        self._client(seen).chat.completions.create(
            model=LUNA, messages=self._MESSAGES, tools=[self._TOOL]
        )

        assert seen["tools"]
        assert seen["reasoning_effort"] == "none"

    def test_a_raw_call_with_no_tool_carries_it_too(self):
        seen: dict = {}
        self._client(seen, "query_expansion").chat.completions.create(
            model=LUNA, messages=self._MESSAGES
        )

        assert seen["reasoning_effort"] == "none"

    def test_parse_carries_it_the_same_way(self):
        """`metadata_service` reaches the wire through `chat.completions.parse`."""
        seen: dict = {}
        self._client(seen, "metadata_enrichment").chat.completions.parse(
            model=LUNA, messages=self._MESSAGES
        )

        assert seen["reasoning_effort"] == "none"

    def test_an_effort_named_at_the_call_site_wins(self):
        """The rule instructor applies: a default fills an absent kwarg only."""
        seen: dict = {}
        self._client(seen).chat.completions.create(
            model=LUNA, messages=self._MESSAGES, reasoning_effort="low"
        )

        assert seen["reasoning_effort"] == "low"

    def test_a_judge_purpose_builds_on_the_raw_path_and_carries_its_effort(self):
        """`tests/evals/judge.py` forces a tool over `create` under `calibration_judge`."""
        seen: dict = {}
        self._client(seen, "calibration_judge").chat.completions.create(
            model=LUNA, messages=self._MESSAGES, tools=[self._TOOL]
        )

        assert seen["reasoning_effort"] == "none"

    def test_the_ledger_shortcut_carries_it_too(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_body())

        ledger = LedgerContext(tenant_id=TENANT, recorder=lambda call: None)
        ledger.client(
            "auditor",
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ).chat.completions.create(model=LUNA, messages=self._MESSAGES, tools=[self._TOOL])

        assert seen["reasoning_effort"] == "none"

    async def test_the_async_factory_carries_it_for_the_owned_loop(self):
        """`deployment_service` and `red_team_service` build here and attach tools."""
        seen: dict = {}
        await self._async_client(seen, "deployment_orchestrator").chat.completions.create(
            model=LUNA, messages=self._MESSAGES, tools=[self._TOOL]
        )

        assert seen["reasoning_effort"] == "none"

    def test_a_route_naming_no_effort_leaves_the_client_bare(self):
        """Injected route, since the table no longer holds one. The default is the
        route's, never a literal of this module's own."""
        from app.core.model_client import _carry_route_effort

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_body())

        bare = _carry_route_effort(
            openai.OpenAI(
                api_key="test-key",
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            ),
            ModelRoute("openai", LUNA),
        )
        bare.chat.completions.create(model=LUNA, messages=self._MESSAGES)

        assert "reasoning_effort" not in seen

    def test_the_default_leaves_the_async_create_as_the_sdk_shipped_it(self):
        """OBSERVED 2026-09-05, openai 2.45.0: the SDK's own `AsyncCompletions.create`
        is not a coroutine function to `inspect` (it sits under `required_args`),
        and only `inspect.unwrap` reaches one. The wrap keeps both facts as they
        were, so anything reading either sees what it saw before."""
        import inspect

        bare = openai.AsyncOpenAI(api_key="test-key").chat.completions.create
        wrapped = self._async_client({}, "auditor").chat.completions.create

        assert inspect.iscoroutinefunction(wrapped) is inspect.iscoroutinefunction(bare)
        assert inspect.iscoroutinefunction(inspect.unwrap(wrapped))


class TestTheFactoryTakesItsProviderFromTheRoute:
    """Issue #88. The route decides which client gets built, and nothing else does.

    `make_client` used to read the provider off the credentials' base url, and
    an absent `OPENAI_BASE_URL` made `provider_for_base_url(None)` answer
    `anthropic`. Every raw purpose below therefore built an `anthropic.Anthropic`
    while its `PURPOSE_ROUTES` row said `openai / gpt-5.6-luna`, and
    `ANTHROPIC_API_KEY` has been empty since the credential was retired on
    2026-08-26.

    These tests name no provider, which is what an ordinary call site does. A
    green `route_for(purpose).provider == "openai"` is a claim about the table;
    these are claims about the object the factory hands back.
    """

    @pytest.mark.parametrize("purpose", EVERY_PURPOSE)
    def test_a_purpose_routed_to_luna_builds_an_openai_client(self, purpose):
        client = make_client(
            purpose,
            tenant_id=TENANT,
            recorder=lambda call: None,
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=_transport(_openai_body())),
        )

        assert isinstance(client, openai.OpenAI)

    def test_the_key_comes_from_the_routed_provider(self, monkeypatch):
        """The credentials follow the provider, so the order of those two lines matters.

        Resolving credentials first and the provider second reads the Anthropic
        key for an OpenAI route, which is the bug one line up from the one above.
        """
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "openai-key")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "anthropic-key")

        client = make_client(
            "gatekeeper",
            tenant_id=TENANT,
            recorder=lambda call: None,
            http_client=httpx.Client(transport=_transport(_openai_body())),
        )

        assert client.api_key == "openai-key"

    def test_the_row_the_hook_writes_names_the_routed_provider(self):
        """A row filed under `anthropic` prices against a book that never served it."""
        recorded = []
        make_client(
            "query_expansion",
            tenant_id=TENANT,
            recorder=recorded.append,
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=_transport(_openai_body())),
            clock=lambda: AT,
        ).chat.completions.create(model=LUNA, messages=[{"role": "user", "content": "x"}])

        assert len(recorded) == 1, f"expected one ledger row, got {len(recorded)}"
        assert recorded[0].provider == "openai"

    def test_an_explicit_provider_still_wins(self):
        """The argument survives as the seam a test drives another provider through."""
        client = make_client(
            "gatekeeper",
            tenant_id=TENANT,
            recorder=lambda call: None,
            provider="deepseek",
            credentials=Credentials(api_key="test-key", base_url="https://provider.example"),
            http_client=httpx.Client(transport=_transport(_body())),
        )

        assert isinstance(client, anthropic.Anthropic)


# ---------------------------------------------------------------------------
# make_client on OpenAI, and make_instructor_client
# ---------------------------------------------------------------------------


class _Verdict(BaseModel):
    passed: bool


def _tool_call_body() -> dict:
    """What OpenAI returns for a forced tool call, which is how instructor asks."""
    body = _openai_body()
    body["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "_Verdict", "arguments": json.dumps({"passed": True})},
            }
        ],
    }
    body["choices"][0]["finish_reason"] = "tool_calls"
    return body


class TestMakeClientOnOpenAi:
    """`scenario_generation` rather than a judge purpose. Every judge route names
    a reasoning effort, and a raw client sends no field for one, so the raw path
    refuses them (`TestTheRawPathChecksThePurpose`)."""

    def _client(self, recorder, body: dict):
        return make_client(
            "scenario_generation",
            tenant_id=TENANT,
            recorder=recorder,
            provider="openai",
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=_transport(body)),
            clock=lambda: AT,
        )

    def test_the_factory_returns_an_openai_client(self):
        assert isinstance(self._client(lambda call: None, _openai_body()), openai.OpenAI)

    def test_a_chat_completion_lands_exactly_one_ledger_row(self):
        recorded = []
        self._client(recorded.append, _openai_body()).chat.completions.create(
            model=LUNA, messages=[{"role": "user", "content": "hello"}]
        )

        assert len(recorded) == 1, f"expected one ledger row, got {len(recorded)}"
        assert recorded[0].purpose == "scenario_generation"
        assert recorded[0].input_tokens == 1000

    def test_an_empty_key_fails_at_construction_and_not_at_the_first_call(self):
        """OPENAI_API_KEY defaults empty until the cutover, so the failure has to be loud."""
        with pytest.raises(openai.OpenAIError):
            make_client(
                "scenario_generation",
                tenant_id=TENANT,
                recorder=lambda call: None,
                provider="openai",
                credentials=Credentials(api_key=""),
                http_client=httpx.Client(transport=_transport(_openai_body())),
            )


class TestWhatEachProviderSdkDoesWithAnEmptyKey:
    """The two SDKs disagree about when a missing key is an error, and it matters.

    FM-001 in the failure-mode log is "transplanted claim": a prose claim about
    one library, written where a sibling branch handles a different one. Its
    climb from rung 4 to rung 2 reads "a provider-contract test asserting what
    each SDK does with an empty key. Offline, no spend, so there is no excuse for
    it sitting at 4." This is that test, and nothing here touches a socket.

    The asymmetry is the finding. OpenAI refuses at construction, so a
    misconfigured worker dies at the factory with a message naming the key.
    Anthropic constructs happily and raises later while resolving auth, so the
    same misconfiguration reaches the call site and surfaces as whatever the
    caller's `except` decides to say about it.

    That is what makes issue #88 more than a tidiness problem. Twelve routed-to-
    Luna purposes build an Anthropic client, `ANTHROPIC_API_KEY` defaults to
    empty since the credential was retired on 2026-08-26, and the branch that
    fails quietly is the one they are on.
    """

    def test_openai_refuses_an_empty_key_at_construction(self):
        with pytest.raises(openai.OpenAIError):
            openai.OpenAI(api_key="")

    def test_the_openai_refusal_names_the_key(self):
        """A worker that dies at the factory should say which setting is missing."""
        with pytest.raises(openai.OpenAIError, match="api_key"):
            openai.OpenAI(api_key="")

    def test_anthropic_accepts_an_empty_key_at_construction(self):
        """Not a bug in either SDK. A recorded difference, so nobody assumes parity.

        If this ever starts raising, the comment on `resolve_credentials` and the
        reasoning on issue #88 both need re-reading rather than editing.
        """
        import anthropic

        assert anthropic.Anthropic(api_key="") is not None


class TestMakeInstructorClient:
    def _client(self, purpose, recorder, seen: dict | None = None, route=None):
        def handler(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.update(json.loads(request.content))
            return httpx.Response(200, json=_tool_call_body())

        return make_instructor_client(
            purpose,
            tenant_id=TENANT,
            recorder=recorder,
            credentials=Credentials(api_key="test-key"),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: AT,
            route=route,
        )

    def _ask(self, client):
        return client.chat.completions.create(
            response_model=_Verdict, messages=[{"role": "user", "content": "grounded?"}]
        )

    def test_the_wrapped_call_still_lands_one_ledger_row(self):
        """The whole point of hooking httpx. Ragas wraps instructor, instructor wraps this."""
        recorded = []
        self._ask(self._client("judge_faithfulness", recorded.append))

        assert len(recorded) == 1, f"expected one ledger row, got {len(recorded)}"
        assert recorded[0].purpose == "judge_faithfulness"
        assert recorded[0].served_model == LUNA

    def test_the_structured_answer_still_comes_back(self):
        assert self._ask(self._client("judge_faithfulness", lambda call: None)).passed is True

    def test_the_factory_puts_the_routed_model_on_the_wire(self):
        seen = {}
        self._ask(self._client("judge_faithfulness", lambda call: None, seen))

        assert seen["model"] == LUNA

    def test_the_factory_puts_the_judge_effort_on_the_wire(self):
        """`none` is a ReasoningEffort literal in openai 2.45.0, not a missing value."""
        seen = {}
        self._ask(self._client("judge_faithfulness", lambda call: None, seen))

        assert seen["reasoning_effort"] == "none"

    def test_a_direct_api_purpose_sends_effort_none_as_well(self):
        """The instructor seam reads the same table, so `_LUNA`'s effort reaches it too."""
        seen = {}
        self._ask(self._client("auditor", lambda call: None, seen))

        assert seen["reasoning_effort"] == "none"

    def test_an_unknown_purpose_raises_before_any_client_is_built(self):
        with pytest.raises(UnknownPurpose):
            self._client("spellcheck", lambda call: None)

    def test_a_route_naming_another_provider_is_refused(self):
        """Only the OpenAI wire format is wired here, so a silently wrong client is worse."""
        with pytest.raises(UnsupportedProvider, match="deepseek"):
            self._client(
                "judge_faithfulness",
                lambda call: None,
                route=ModelRoute(provider="deepseek", model="deepseek-v4-flash"),
            )

    # `is_async` picks the builder AND the kind of httpx client that builder can
    # hook, and only the first half was enforced. The pair below drives the two
    # mismatches. Neither raised before `_instructor_target`: the sync client
    # reached `AsyncOpenAI` as its transport and failed at the first await inside
    # the SDK, and the async one reached the sync SDK the same way.

    def test_an_async_build_refuses_a_sync_http_client(self):
        """A sync transport under AsyncOpenAI fails at the first await, far from here."""
        with pytest.raises(TypeError, match="passed an httpx.Client"):
            make_instructor_client(
                "judge_faithfulness",
                tenant_id=TENANT,
                recorder=lambda call: None,
                credentials=Credentials(api_key="test-key"),
                http_client=httpx.Client(),
                clock=lambda: AT,
                is_async=True,
            )

    def test_a_sync_build_refuses_an_async_http_client(self):
        """And the same mismatch the other way round, which the sync SDK cannot use."""
        with pytest.raises(TypeError, match="passed an httpx.AsyncClient"):
            make_instructor_client(
                "judge_faithfulness",
                tenant_id=TENANT,
                recorder=lambda call: None,
                credentials=Credentials(api_key="test-key"),
                http_client=httpx.AsyncClient(),
                clock=lambda: AT,
            )
