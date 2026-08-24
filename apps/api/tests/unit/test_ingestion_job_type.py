"""Unit tests for app.domain.ingestion_job.IngestionJob (ticket #43, issue #7).

The four ids the ingestion chain passes from hop to hop were a bare dict, and
each of chunk, metadata and embed re-spelled the same four-clause guard over it
before trusting the values. Three copies of one rule drift, and a dict says
nothing about which keys it is supposed to carry.

The type carries the rule once. Construction rejects a falsy id and a missing
document_ids, so a job that exists is a job whose four ids are present, and no
task needs to ask again.

Celery serialises JSON, so the wire stays a dict. to_dict and from_dict are that
conversion, and the round trip is exact: the tuple the type holds goes out as a
list and comes back as the same tuple.
"""

import base64
import dataclasses
import os

# Env setup before any `from app` import, matching tests/unit/test_chunk_type.py.
# app.domain.ingestion_job imports the standard library only, so Settings never
# loads here, but the block keeps the file runnable in isolation.
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

import pytest  # noqa: E402

from app.domain.ingestion_job import IngestionJob, InvalidJobDict  # noqa: E402

TENANT = "11111111-1111-4111-8111-111111111111"
AGENT = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"
DOC = "44444444-4444-4444-8444-444444444444"


def _job(**overrides) -> IngestionJob:
    fields = {
        "tenant_id": TENANT,
        "agent_id": AGENT,
        "job_id": JOB,
        "document_ids": [DOC],
    }
    fields.update(overrides)
    return IngestionJob(**fields)


# ---------------------------------------------------------------------------
# The field set
# ---------------------------------------------------------------------------


def test_the_field_set_is_the_four_ids_the_chain_forwards():
    """The four keys the tasks passed as a dict, in the order they were written."""
    names = [f.name for f in dataclasses.fields(IngestionJob)]
    assert names == ["tenant_id", "agent_id", "job_id", "document_ids"]


def test_no_connection_string_field_exists():
    """Project rule 1: a task receives ids and fetches the connection string itself.

    The dict this type replaces was described in four task docstrings as the
    thing that carries no connection string. Here that is a property of the type
    rather than a promise repeated at four return statements.
    """
    names = {f.name for f in dataclasses.fields(IngestionJob)}
    for banned in ("conn_str", "connection_string", "dsn", "neon_connection_string"):
        assert banned not in names


# ---------------------------------------------------------------------------
# Construction rejects what the three or-chains rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["tenant_id", "agent_id", "job_id"])
@pytest.mark.parametrize("value", ["", None])
def test_a_falsy_id_is_refused(field, value):
    """Empty string and None both fail, which is what `not tenant_id` caught."""
    with pytest.raises(ValueError) as exc:
        _job(**{field: value})
    assert field in str(exc.value)


def test_document_ids_of_none_is_refused():
    """`document_ids is None` was the fourth clause of the same guard."""
    with pytest.raises(ValueError) as exc:
        _job(document_ids=None)
    assert "document_ids" in str(exc.value)


def test_document_ids_is_required():
    """No default. A job that names no document list is not a job."""
    with pytest.raises(TypeError):
        IngestionJob(tenant_id=TENANT, agent_id=AGENT, job_id=JOB)


def test_an_empty_document_list_is_a_whole_job():
    """Empty is not missing. A run with nothing to do still has a job id."""
    job = _job(document_ids=[])
    assert job.document_ids == ()


# ---------------------------------------------------------------------------
# document_ids is held as a tuple
# ---------------------------------------------------------------------------


def test_a_list_of_document_ids_is_held_as_a_tuple():
    """The wire hands over a list; a frozen record holds something immutable."""
    job = _job(document_ids=[DOC, TENANT])
    assert job.document_ids == (DOC, TENANT)
    assert isinstance(job.document_ids, tuple)


def test_mutating_the_list_that_was_passed_in_does_not_reach_the_job():
    """The tuple is a copy, so a caller's later append cannot change the job."""
    document_ids = [DOC]
    job = _job(document_ids=document_ids)
    document_ids.append(AGENT)
    assert job.document_ids == (DOC,)


# ---------------------------------------------------------------------------
# Frozen and comparable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("tenant_id", AGENT),
        ("agent_id", TENANT),
        ("job_id", TENANT),
        ("document_ids", (AGENT,)),
    ],
)
def test_every_field_refuses_assignment(attribute, value):
    job = _job()
    before = getattr(job, attribute)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(job, attribute, value)
    assert getattr(job, attribute) == before


def test_same_values_compare_equal():
    assert _job() == _job()


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("tenant_id", AGENT),
        ("agent_id", TENANT),
        ("job_id", TENANT),
        ("document_ids", [AGENT]),
    ],
)
def test_a_difference_in_any_field_compares_unequal(attribute, value):
    assert _job() != _job(**{attribute: value})


# ---------------------------------------------------------------------------
# The wire form
# ---------------------------------------------------------------------------


def test_to_dict_is_the_four_keys_the_chain_has_always_sent():
    """Byte for byte what the tasks returned before, so the broker sees no change."""
    assert _job().to_dict() == {
        "tenant_id": TENANT,
        "agent_id": AGENT,
        "job_id": JOB,
        "document_ids": [DOC],
    }


def test_to_dict_writes_document_ids_as_a_list():
    """Celery serialises JSON. A list is what JSON round-trips; a tuple is not."""
    assert isinstance(_job().to_dict()["document_ids"], list)


def test_from_dict_reads_back_exactly_what_to_dict_wrote():
    job = _job(document_ids=[DOC, AGENT])
    assert IngestionJob.from_dict(job.to_dict()) == job


def test_from_dict_reads_back_a_job_with_no_documents():
    job = _job(document_ids=[])
    assert IngestionJob.from_dict(job.to_dict()) == job


def test_from_dict_ignores_keys_it_does_not_know():
    """An older hop that added a key does not break the hop that reads it.

    The chain has been re-dispatched mid-flight before, and a dict from a
    different revision of the pipeline may carry more than these four keys.
    """
    payload = _job().to_dict()
    payload["chunk_count"] = 12
    payload["retries"] = 0
    assert IngestionJob.from_dict(payload) == _job()


@pytest.mark.parametrize("key", ["tenant_id", "agent_id", "job_id", "document_ids"])
def test_from_dict_refuses_a_dict_that_is_missing_a_key(key):
    """A missing key is the case each task logged as invalid_result_dict."""
    payload = _job().to_dict()
    del payload[key]
    with pytest.raises(ValueError) as exc:
        IngestionJob.from_dict(payload)
    assert key in str(exc.value)


@pytest.mark.parametrize("key", ["tenant_id", "agent_id", "job_id"])
def test_from_dict_refuses_a_dict_whose_id_is_empty(key):
    payload = _job().to_dict()
    payload[key] = ""
    with pytest.raises(ValueError):
        IngestionJob.from_dict(payload)


# ---------------------------------------------------------------------------
# What the chain edge may swallow, and what it may not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [42, "abc", 3.5, {"a": 1}])
def test_document_ids_of_the_wrong_shape_is_a_type_error(value):
    """A document_ids that is not a list or a tuple fails loudly, every time.

    `tuple(42)` raised TypeError and the chain edge caught it beside the missing
    key case, so an upstream shape bug logged one line and reported SUCCESS.
    `tuple("abc")` was worse and raised nothing at all, turning one string into
    three document ids that name no document.

    TypeError sits outside InvalidJobDict, and InvalidJobDict is the only
    exception the edge swallows.
    """
    payload = _job().to_dict()
    payload["document_ids"] = value
    with pytest.raises(TypeError) as exc:
        IngestionJob.from_dict(payload)
    assert "document_ids" in str(exc.value)


@pytest.mark.parametrize("key", ["tenant_id", "agent_id", "job_id", "document_ids"])
def test_a_missing_key_raises_invalid_job_dict(key):
    """The one failure the edge is allowed to swallow.

    A chain re-dispatched mid-flight can deliver a dict from a different revision
    of the pipeline. That dict passes through; a wrong-shaped one does not.
    """
    payload = _job().to_dict()
    del payload[key]
    with pytest.raises(InvalidJobDict):
        IngestionJob.from_dict(payload)


@pytest.mark.parametrize("key", ["tenant_id", "agent_id", "job_id"])
def test_an_empty_id_raises_invalid_job_dict(key):
    payload = _job().to_dict()
    payload[key] = ""
    with pytest.raises(InvalidJobDict):
        IngestionJob.from_dict(payload)


def test_document_ids_of_none_raises_invalid_job_dict():
    """None is the fourth clause of the guard the three tasks used to spell."""
    with pytest.raises(InvalidJobDict):
        _job(document_ids=None)


def test_invalid_job_dict_is_a_value_error():
    """Every caller that already catches ValueError keeps catching this one."""
    assert issubclass(InvalidJobDict, ValueError)
