"""The five ingestion hops, driven in sequence, on the seam they now share (ticket #43, AC3).

Every other test in this repo drives one hop with its own hand-built input. That
is what let the four-id dict be described four different ways in four docstrings
while nothing checked that hop N's output is something hop N+1 can read.

This file feeds each hop what the previous hop actually returned:

    parse_documents → chunk_documents → generate_metadata → embed_and_migrate
    → synthesize_retrieval_strategy

and reads two things off the run. First, the job survives every hop. The dict
that comes out the far end builds the IngestionJob that went into the first hop.
Second, the work still happens. The chunk rows, the metadata rows, the embedding
rows and the SSE events are the same ones each hop's own test asserts, and every
event in the run carries the one job_id.

The outside edges all answer from here. S3, Docling, the Haiku call, Voyage, the
Strategist and both databases are faked. What is real is the five task functions
and the seam between them.
"""

import base64
import os

# ---------------------------------------------------------------------------
# Environment setup, MUST run before any `from app` import (pydantic-settings)
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
)
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from contextlib import contextmanager  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import psycopg2  # noqa: E402
import pytest  # noqa: E402

from app.domain.chunk import Chunk  # noqa: E402
from app.domain.chunk_id import deterministic_chunk_id  # noqa: E402
from app.domain.ingestion_job import IngestionJob  # noqa: E402

DOCUMENT = "44444444-4444-4444-8444-444444444444"
JOB = IngestionJob(
    tenant_id="11111111-1111-4111-8111-111111111111",
    agent_id="22222222-2222-4222-8222-222222222222",
    job_id="33333333-3333-4333-8333-333333333333",
    document_ids=[DOCUMENT],
)
# The id chunk_documents will derive for the first chunk of that document, so the
# rows metadata and embed are fed are the rows chunk_documents wrote.
CHUNK_ID = deterministic_chunk_id(DOCUMENT, 0)


class _Cursor:
    """A psycopg2 cursor stand-in that records SQL and answers from two queues.

    Every hop that opens a tenant connection uses `with conn.cursor() as cur`
    except parse_documents, which holds a bare cursor across its Docling call, so
    this supports both. synthesize_retrieval_strategy reads its corpus signals
    through strategy_service instead, and the fixture fakes that whole.
    """

    def __init__(self, fetchone=(), fetchall=()):
        self.executed = []
        self._one = list(fetchone)
        self._all = list(fetchall)

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchone(self):
        return self._one.pop(0) if self._one else None

    def fetchall(self):
        return self._all.pop(0) if self._all else []

    def sql_naming(self, fragment):
        return [sql for sql, _ in self.executed if fragment in sql]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _connection(cursor):
    """A psycopg2 connection stand-in handing out one cursor."""
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _fake_psycopg2(connect):
    """A per-hop stand-in for the psycopg2 module.

    The four task modules that use psycopg2 hold the same module object, so
    patching `<task module>.psycopg2.connect` for one hop patches it for all four
    and the last hop patched wins. Each hop gets its own module stand-in instead,
    carrying the two other attributes the tasks read. parse catches
    psycopg2.OperationalError, and embed reads psycopg2.extensions for AUTOCOMMIT.
    """
    return SimpleNamespace(
        connect=connect,
        OperationalError=psycopg2.OperationalError,
        extensions=psycopg2.extensions,
    )


def _control_db(job_row):
    """A control DB session answering db.get(Agent, ...) and db.get(Job, ...)."""
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted-conn"
    db = MagicMock()
    db.get.side_effect = lambda model, _id: job_row if model.__name__ == "Job" else agent
    # finish_ingestion asks whether a job.complete row is already on the stream.
    # A MagicMock answers truthily to everything, which would read as "already
    # emitted" and the drive below would end with no terminal event at all.
    db.execute.return_value.first.return_value = None
    return db


@contextmanager
def _sync_db(db):
    yield db


class _Run:
    """One drive of the chain. What crossed each join, and what each hop wrote."""

    def __init__(self):
        self.joins = {}
        self.events = []
        self.cursors = {}

    def record_emit(self, job_id, event_type, payload, db, redis_client):
        """Stands in for emit() in every hop, so the run has one event list."""
        self.events.append((job_id, event_type))


@pytest.fixture
def run(monkeypatch):
    """Fake every outside edge of all six hops; leave the six task functions real."""
    state = _Run()
    job_row = MagicMock()
    job_row.status = "running"

    state.cursors["parse"] = _Cursor(
        # the un-parsed pre-check, then the document row
        fetchone=[(1,), (f"{DOCUMENT}.pdf", "pdf", None, "pending")]
    )
    state.cursors["chunk"] = _Cursor(fetchone=[(f"{DOCUMENT}.pdf", "pdf", "parsed", None)])
    state.cursors["metadata"] = _Cursor(
        fetchone=[(0,)],  # Layer 3: no chunk_metadata row yet
        fetchall=[[(CHUNK_ID, "the chunk content")]],
    )
    state.cursors["embed"] = _Cursor(fetchall=[[(CHUNK_ID, "the chunk content")]])
    state.cursors["reindex"] = _Cursor()

    document = MagicMock()
    document.pages = {1: MagicMock()}

    def _patch(hop, **extra):
        module = f"app.worker.tasks.pipeline.{hop}."
        monkeypatch.setattr(module + "get_sync_db", lambda: _sync_db(_control_db(job_row)))
        monkeypatch.setattr(module + "fernet_decrypt", lambda _: "fake-conn-str")
        monkeypatch.setattr(module + "emit", state.record_emit)
        for name, value in extra.items():
            monkeypatch.setattr(module + name, value)

    # parse: one tenant connection, reopened after the Docling call, plus S3.
    _patch(
        "parse",
        psycopg2=_fake_psycopg2(lambda _: _connection(state.cursors["parse"])),
        parse_document_from_bytes=lambda content, source_uri: document,
    )
    monkeypatch.setattr("app.services.storage_service.get_bytes", lambda key: b"%PDF-1.4 stub")
    # parse sleeps 1s after ingestion.started so an SSE client can subscribe. The
    # wait is real behaviour and irrelevant to the seam, so it costs nothing here.
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.time.sleep", lambda seconds: None)

    _patch(
        "chunk",
        psycopg2=_fake_psycopg2(lambda _: _connection(state.cursors["chunk"])),
        parse_document_from_bytes=lambda content, source_uri: document,
        chunk_document=lambda doc, doc_id: (
            Chunk(
                document_id=doc_id,
                ordinal=0,
                content="the chunk content",
                token_count=3,
                is_table=False,
            ),
        ),
    )

    from app.services.metadata_service import ChunkMetadataAndEntities

    _patch(
        "metadata",
        psycopg2=_fake_psycopg2(lambda _: _connection(state.cursors["metadata"])),
        enrich_chunks_batch=lambda contents, ledger: [
            ChunkMetadataAndEntities(
                summary="a summary", keywords=["k"], questions=["q?"], entities=[]
            )
            for _ in contents
        ],
    )

    embed_connections = iter(
        [state.cursors["embed"], state.cursors["reindex"], state.cursors["reindex"]]
    )
    _patch(
        "embed",
        psycopg2=_fake_psycopg2(lambda _: _connection(next(embed_connections))),
        embed_chunks=lambda texts: [[0.1] * 1024 for _ in texts],
    )

    # strategy opens no tenant cursor. It reads corpus signals through
    # strategy_service and calls the Strategist over the direct Anthropic API,
    # and both of those are the fakes below. run_strategist leaves the container
    # empty, so the task validates its way to the RetrievalStrategy defaults.
    _patch(
        "strategy",
        _fetch_corpus_signals_sync=lambda agent_id, conn_str: {
            "chunk_count": 1,
            "doc_count": 1,
            "avg_chunk_len": 300.0,
            "max_chunk_len": 600,
            "table_ratio": 0.0,
            "entity_count": 0,
            "doc_types": {"pdf": 1},
        },
        run_strategist=lambda signals_json, container, job, tenant_dsn: None,
    )

    # finish opens no tenant connection and decrypts nothing. It reads the job
    # row, writes the terminal state and emits the run's one terminal event.
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.finish.get_sync_db", lambda: _sync_db(_control_db(job_row))
    )
    monkeypatch.setattr("app.worker.tasks.pipeline.finish.emit", state.record_emit)

    state.job_row = job_row
    return state


def _drive(state):
    """Run the six hops, each on what the previous hop returned. Return the last dict."""
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.worker.tasks.pipeline.finish import finish_ingestion
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.parse import parse_documents
    from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy

    handed_on = parse_documents.run(JOB.tenant_id, JOB.agent_id, JOB.job_id, [DOCUMENT])

    for name, task in (
        ("parse to chunk", chunk_documents),
        ("chunk to metadata", generate_metadata),
        ("metadata to embed", embed_and_migrate),
        ("embed to strategy", synthesize_retrieval_strategy),
        ("strategy to finish", finish_ingestion),
    ):
        # What crossed this join is what the previous hop returned, never rebuilt here.
        state.joins[name] = handed_on
        handed_on = task.run(handed_on)

    return handed_on


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_the_job_survives_every_hop(run):
    """What comes out of the last hop builds the job that went into the first.

    parse takes the four ids as arguments at the head of the chain. Every hop
    after it reads its input with IngestionJob.from_dict, and all five write
    their output with to_dict, so a hop that dropped a document id, rebuilt the
    job from something else, or renamed a key on the wire breaks this equality.
    """
    handed_on = _drive(run)

    assert IngestionJob.from_dict(handed_on) == JOB
    assert handed_on == JOB.to_dict()


def test_every_join_carries_the_same_job(run):
    """At each of the five joins, the dict on the wire is the job.

    Read one join at a time, this is hop N's output being hop N+1's input, which
    is the thing no single hop's own tests can see.
    """
    _drive(run)

    assert {name: IngestionJob.from_dict(payload) for name, payload in run.joins.items()} == {
        "parse to chunk": JOB,
        "chunk to metadata": JOB,
        "metadata to embed": JOB,
        "embed to strategy": JOB,
        "strategy to finish": JOB,
    }


def test_the_cores_hand_the_type_along_without_the_wire(run):
    """The five cores below the head compose on the type itself, with no dict between them.

    The edges convert because Celery serialises JSON. Underneath, this is one
    function per hop taking an IngestionJob and returning one, and the tail of
    the chain is those five composed.
    """
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.worker.tasks.pipeline.finish import finish_ingestion
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy

    job = JOB
    for task in (
        chunk_documents,
        generate_metadata,
        embed_and_migrate,
        synthesize_retrieval_strategy,
        finish_ingestion,
    ):
        job = task.run.__wrapped__(task, job)
        assert isinstance(job, IngestionJob)

    assert job == JOB


def test_the_edge_logs_under_the_cores_own_module(monkeypatch):
    """job_in_job_out names its logger after the hop, never after chain_edge.

    An operator greps `app.worker.tasks.pipeline.chunk` for a stuck job, so a
    line filed under `app.worker.tasks.pipeline.chain_edge` is a line they never
    find. structlog's capture_logs replaces the whole processor chain and the
    captured entry carries no logger name, so this asks the decorator which name
    it built the logger with rather than reading one off a line.
    """
    import structlog

    from app.worker.tasks.pipeline import chain_edge

    original = structlog.get_logger
    asked = []

    def _record(name):
        asked.append(name)
        return original(name)

    monkeypatch.setattr(structlog, "get_logger", _record)

    def core(self, job):
        return job

    core.__module__ = "app.worker.tasks.pipeline.chunk"
    chain_edge.job_in_job_out(core)

    assert asked == ["app.worker.tasks.pipeline.chunk"]


# ---------------------------------------------------------------------------
# The work still happens
# ---------------------------------------------------------------------------


def test_each_hop_writes_the_rows_its_own_test_asserts(run):
    """Each hop persists what it persists, driven from one job.

    Read as one run this says something the per-hop tests cannot. The document
    id parse marked parsed is the one chunk wrote chunks for, and the chunk id
    those chunks carry is the one metadata and embed wrote rows against.
    """
    _drive(run)

    assert run.cursors["parse"].sql_naming("UPDATE documents SET parse_status = 'parsed'")
    assert run.cursors["chunk"].sql_naming("INSERT INTO chunks")
    assert run.cursors["metadata"].sql_naming("INSERT INTO chunk_metadata")
    assert run.cursors["embed"].sql_naming("INSERT INTO embeddings")
    assert run.cursors["reindex"].sql_naming("REINDEX INDEX CONCURRENTLY")

    chunk_insert = [
        params for sql, params in run.cursors["chunk"].executed if "INSERT INTO chunks" in sql
    ]
    assert chunk_insert[0][0] == str(CHUNK_ID), (
        "the chunk id written by chunk_documents is not the one metadata and embed "
        "then wrote rows against"
    )

    embed_insert = [
        params for sql, params in run.cursors["embed"].executed if "INSERT INTO embeddings" in sql
    ]
    assert embed_insert[0][0] == str(CHUNK_ID)


def test_the_run_emits_one_job_ids_worth_of_events_start_to_finish(run):
    """Every event of the run carries the job_id the head was given, and job.complete is last.

    An empty or mismatched job_id publishes to a channel nobody is subscribed
    to, which is silent. The ingest page simply never updates.

    `job.complete` ending the run is the whole of #168. embed emitted it while
    strategy still had to run, so a subscriber that closes on the terminal event
    closed a hop early and never saw the strategy step or its failure.
    """
    _drive(run)

    assert [job_id for job_id, _ in run.events] == [JOB.job_id] * len(run.events)

    event_types = [event_type for _, event_type in run.events]
    assert event_types[0] == "ingestion.started"
    assert event_types[-1] == "job.complete", (
        f"the run's terminal event is not last: {event_types}"
    )
    assert event_types.count("job.complete") == 1, (
        f"one task owns the terminal event: {event_types}"
    )
    for expected in (
        "parsing.complete",
        "chunking.complete",
        "metadata.complete",
        "embedding.complete",
        "ingestion.complete",
        "strategy.synthesized",
    ):
        assert expected in event_types, f"{expected} missing from {event_types}"


def test_the_job_row_is_marked_complete(run):
    """finish_ingestion closes the control DB job row, as it does on its own."""
    _drive(run)

    assert run.job_row.status == "complete"
