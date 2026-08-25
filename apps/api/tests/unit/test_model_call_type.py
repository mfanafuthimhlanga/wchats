"""Unit tests for app.domain.model_call.ModelCall, the ledger record (ticket #46, issue #22).

Twelve model call sites read neither `usage` nor `model` off a response, so the
first Harness run could not be priced at all. This type is what each call site
yields once the factory lands: the tokens one call spent, the model the provider
actually served, and the instant it happened.

WHAT THESE TESTS PIN
    Money is derived from these fields at read time, so a field that arrives
    wrong is every report over that row being wrong for as long as the row
    lives. Construction refuses the three shapes that poison a rollup quietly.

        a naive `at`      names no instant, so it cannot be placed in a CAT
                          pricing window, and it borrows whatever offset the
                          reader happened to assume
        a negative count  subtracts spend from a tenant's day
        an empty tenant   is a row nobody is billed for

    `at` is normalised to UTC rather than merely accepted, because pricing reads
    the window off a conversion to CAT and that conversion needs a known offset
    to start from.

WHY model_source EXISTS
    DeepSeek answers a `claude-haiku` request with its own model, and some
    responses echo the alias back instead of naming what ran. `reported` marks a
    served model the response named. `mapped_by_docs` marks one the published
    mapping supplied. A report that mixes the two without saying so cannot be
    audited, and `turn_metrics.cost_usd` priced DeepSeek calls against
    Anthropic's book for exactly that reason.
"""

import base64
import dataclasses
import os
from datetime import datetime, timedelta, timezone

# Env setup before any `from app` import, matching tests/unit/test_chunk_type.py.
# app.domain.model_call imports the standard library only, so Settings never
# loads here, but the block keeps the file runnable in isolation.
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

import pytest  # noqa: E402

from app.domain.model_call import InvalidModelCall, ModelCall, ModelSource  # noqa: E402

TENANT = "11111111-1111-4111-8111-111111111111"
AGENT = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"

SAST = timezone(timedelta(hours=2))
AT = datetime(2026, 8, 25, 9, 30, tzinfo=timezone.utc)

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)

REQUIRED_TEXT_FIELDS = (
    "purpose",
    "provider",
    "requested_model",
    "served_model",
    "tenant_id",
)


def _call(**overrides) -> ModelCall:
    fields = {
        "purpose": "judge",
        "provider": "deepseek",
        "requested_model": "claude-haiku-4-5",
        "served_model": "deepseek-v4-flash",
        "model_source": ModelSource.REPORTED,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "at": AT,
        "tenant_id": TENANT,
        "agent_id": AGENT,
        "job_id": JOB,
    }
    fields.update(overrides)
    return ModelCall(**fields)


# ---------------------------------------------------------------------------
# The field set
# ---------------------------------------------------------------------------


def test_the_field_set_is_the_thirteen_the_resolution_named():
    """The record #22 decided, in the order it was written there."""
    names = [f.name for f in dataclasses.fields(ModelCall)]
    assert names == [
        "purpose",
        "provider",
        "requested_model",
        "served_model",
        "model_source",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "at",
        "tenant_id",
        "agent_id",
        "job_id",
    ]


def test_the_record_is_frozen():
    """A ledger row a reader can edit is a ledger nobody can audit."""
    call = _call()
    with pytest.raises(dataclasses.FrozenInstanceError):
        call.input_tokens = 99


def test_no_field_could_hold_a_connection_string():
    """Project rule 1. A call carries ids, never credentials."""
    names = {f.name for f in dataclasses.fields(ModelCall)}
    assert not {n for n in names if "dsn" in n or "url" in n or "conn" in n}


# ---------------------------------------------------------------------------
# model_source, the two ways a served model arrives
# ---------------------------------------------------------------------------


def test_model_source_has_exactly_the_two_decided_members():
    assert [m.value for m in ModelSource] == ["reported", "mapped_by_docs"]


def test_a_source_string_is_coerced_to_the_enum():
    """A row read back off the wire arrives as text, and the text is the same fact."""
    assert _call(model_source="mapped_by_docs").model_source is ModelSource.MAPPED_BY_DOCS


def test_an_unknown_source_is_refused():
    with pytest.raises(InvalidModelCall):
        _call(model_source="guessed")


def test_a_source_of_none_is_refused():
    with pytest.raises(InvalidModelCall):
        _call(model_source=None)


# ---------------------------------------------------------------------------
# `at`, the instant the price window is read from
# ---------------------------------------------------------------------------


def test_a_naive_timestamp_is_refused():
    """No offset means no CAT hour, so no price window and no honest report."""
    with pytest.raises(InvalidModelCall):
        _call(at=datetime(2026, 8, 25, 9, 30))


def test_an_aware_timestamp_in_another_zone_keeps_the_same_instant():
    """09:30 UTC and 11:30 SAST are one instant, and the record holds UTC."""
    call = _call(at=datetime(2026, 8, 25, 11, 30, tzinfo=SAST))
    assert call.at == AT
    assert call.at.tzinfo == timezone.utc
    assert call.at.hour == 9


def test_a_string_is_not_a_timestamp():
    with pytest.raises(InvalidModelCall):
        _call(at="2026-08-25T09:30:00Z")


# ---------------------------------------------------------------------------
# The four token counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", TOKEN_FIELDS)
def test_a_negative_token_count_is_refused(field):
    """Negative spend on one row is a tenant's day quietly discounted."""
    with pytest.raises(InvalidModelCall):
        _call(**{field: -1})


@pytest.mark.parametrize("field", TOKEN_FIELDS)
def test_zero_tokens_is_a_whole_count(field):
    """A call that read no cache spent nothing on cache, which is a fact."""
    assert getattr(_call(**{field: 0}), field) == 0


@pytest.mark.parametrize("field", TOKEN_FIELDS)
def test_a_token_count_that_is_not_an_int_is_refused(field):
    with pytest.raises(InvalidModelCall):
        _call(**{field: "1200"})


@pytest.mark.parametrize("field", TOKEN_FIELDS)
def test_a_boolean_is_not_a_token_count(field):
    """True is an int in Python and would price as one token."""
    with pytest.raises(InvalidModelCall):
        _call(**{field: True})


# ---------------------------------------------------------------------------
# The ids: tenant always, agent and job only when there is one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", REQUIRED_TEXT_FIELDS)
def test_a_required_field_refuses_the_empty_string(field):
    with pytest.raises(InvalidModelCall):
        _call(**{field: ""})


@pytest.mark.parametrize("field", REQUIRED_TEXT_FIELDS)
def test_a_required_field_refuses_none(field):
    with pytest.raises(InvalidModelCall):
        _call(**{field: None})


def test_a_platform_call_has_no_agent_and_no_job():
    """A rollup and a health probe are real calls that belong to no job."""
    call = _call(agent_id=None, job_id=None)
    assert call.agent_id is None
    assert call.job_id is None


def test_agent_and_job_default_to_none():
    call = ModelCall(
        purpose="rollup",
        provider="deepseek",
        requested_model="claude-haiku-4-5",
        served_model="deepseek-v4-flash",
        model_source=ModelSource.REPORTED,
        input_tokens=10,
        output_tokens=2,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=AT,
        tenant_id=TENANT,
    )
    assert (call.agent_id, call.job_id) == (None, None)


@pytest.mark.parametrize("field", ["agent_id", "job_id"])
def test_an_optional_id_refuses_the_empty_string(field):
    """None says there is no job. An empty string says there is one and hides it."""
    with pytest.raises(InvalidModelCall):
        _call(**{field: ""})


def test_the_error_names_the_field_that_was_wrong():
    with pytest.raises(InvalidModelCall, match="input_tokens"):
        _call(input_tokens=-5)


def test_invalid_model_call_is_a_value_error():
    """Callers already catching ValueError keep catching it, as InvalidJobDict does."""
    assert issubclass(InvalidModelCall, ValueError)
