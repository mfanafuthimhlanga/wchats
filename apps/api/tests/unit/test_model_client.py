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

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import structlog

from app.core.model_client import (
    CallContext,
    Credentials,
    attach_ledger_hook,
    make_client,
    provider_for_base_url,
    record_model_call,
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


class TestProviderForBaseUrl:
    def test_no_base_url_is_anthropic(self):
        assert provider_for_base_url(None) == "anthropic"

    def test_a_deepseek_endpoint_is_deepseek(self):
        assert provider_for_base_url("https://api.deepseek.com/anthropic") == "deepseek"

    def test_an_unknown_host_is_recorded_as_itself(self):
        """The price book then raises rather than pricing a call from a guess."""
        assert provider_for_base_url("https://gateway.internal:8443/v1") == (
            "gateway.internal"
        )


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
            "judge",
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
        assert recorded[0].purpose == "judge"
        assert recorded[0].agent_id is None, "a platform call names no agent"

    def test_the_request_the_sdk_sent_is_what_the_row_reports(self):
        """Proof the hook reads the wire and not the call site's arguments."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_body())

        recorded = []
        client = make_client(
            "judge",
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
