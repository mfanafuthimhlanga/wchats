"""One task owns an ingestion run's terminal event, and it is the last one (#168).

Two orderings were wrong, and both put `complete` on the stream in front of a
failure:

  * `embed_and_migrate` emitted `job.complete` while
    `synthesize_retrieval_strategy` still had to run. A subscriber that closes on
    `job.complete` (which is what `app.services.sse.TERMINAL_EVENTS` tells it to)
    stopped reading before the strategy step reported anything, its failure
    included.
  * `generate_metadata` emitted `metadata.complete` for a document even when
    every batch in it failed, so a run that then ended in `job.failed` had said
    `complete` for the document that killed it.

The tests below drive the sequence rather than reading the source. Each one holds
the emitted event names in the order they were emitted, which is the only thing a
subscriber sees.
"""

import base64
import os

# ---------------------------------------------------------------------------
# Environment setup. MUST run before any `from app` import (pydantic-settings)
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
)
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")

from contextlib import contextmanager  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

WIRE = {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}


def _sync_db(mock_db):
    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


class _Emitted:
    """The event stream a subscriber would see, in order."""

    def __init__(self):
        self.events = []

    def __call__(self, job_id, event_type, payload, db, redis_client):
        self.events.append(event_type)

    @property
    def terminal(self):
        return [e for e in self.events if e in ("job.complete", "job.failed")]


# ---------------------------------------------------------------------------
# The chain's shape: the owner is last, and it is the only owner
# ---------------------------------------------------------------------------


def test_the_last_hop_of_the_ingestion_chain_is_the_terminal_owner():
    """A hop appended after finish_ingestion would put job.complete back mid-chain."""
    from app.api.v1.documents import INGESTION_CHAIN
    from app.worker.tasks.pipeline.finish import finish_ingestion

    assert INGESTION_CHAIN[-1] is finish_ingestion
    assert [hop for hop in INGESTION_CHAIN].count(finish_ingestion) == 1


def test_the_chain_still_starts_at_parse():
    """The head takes the four upload arguments; every later hop takes the job."""
    from app.api.v1.documents import INGESTION_CHAIN
    from app.worker.tasks.pipeline.parse import parse_documents

    assert INGESTION_CHAIN[0] is parse_documents


# ---------------------------------------------------------------------------
# embed_and_migrate: a step event, never a terminal one
# ---------------------------------------------------------------------------


def test_embed_and_migrate_emits_no_terminal_event(monkeypatch):
    """It ran two hops before the end and said the run was over."""
    from app.worker.tasks.pipeline import embed as embed_module

    mock_db = MagicMock()
    mock_db.get.return_value = MagicMock(neon_connection_string=b"encrypted")

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = lambda self, *args: False
    tenant_conn = MagicMock()
    tenant_conn.cursor.return_value = cursor

    emitted = _Emitted()
    monkeypatch.setattr(embed_module, "get_sync_db", _sync_db(mock_db))
    monkeypatch.setattr(embed_module, "fernet_decrypt", lambda _: "fake-conn")
    monkeypatch.setattr(embed_module, "require_ciphertext", lambda value, _: value)
    monkeypatch.setattr(embed_module.psycopg2, "connect", lambda *a, **k: tenant_conn)
    monkeypatch.setattr(embed_module, "emit", emitted)

    embed_module.embed_and_migrate.run(WIRE)

    assert emitted.terminal == [], (
        f"embed_and_migrate is not the last hop and may not end the run: {emitted.events}"
    )
    assert "ingestion.complete" in emitted.events, (
        f"its own step event still has to be emitted: {emitted.events}"
    )


# ---------------------------------------------------------------------------
# finish_ingestion: writes the row, emits once, and reads the row first
# ---------------------------------------------------------------------------


def _finish_db(status, already_emitted):
    """A control-DB session whose job row has `status` and whose stream may hold job.complete."""
    job_row = MagicMock()
    job_row.status = status
    db = MagicMock()
    db.get.return_value = job_row
    db.execute.return_value.first.return_value = ("row-id",) if already_emitted else None
    return db, job_row


def test_finish_ingestion_writes_the_row_and_emits_the_terminal_event(monkeypatch):
    from app.worker.tasks.pipeline import finish as finish_module

    db, job_row = _finish_db("running", already_emitted=False)
    emitted = _Emitted()
    monkeypatch.setattr(finish_module, "get_sync_db", _sync_db(db))
    monkeypatch.setattr(finish_module, "emit", emitted)

    finish_module.finish_ingestion.run(WIRE)

    assert emitted.events == ["job.complete"]
    assert job_row.status == "complete"
    assert job_row.finished_at is not None
    db.commit.assert_called_once()


def test_a_redelivery_does_not_put_a_second_terminal_event_on_the_stream(monkeypatch):
    """acks_late re-runs this after a lost ack. The stream row is the condition."""
    from app.worker.tasks.pipeline import finish as finish_module

    db, _ = _finish_db("complete", already_emitted=True)
    emitted = _Emitted()
    monkeypatch.setattr(finish_module, "get_sync_db", _sync_db(db))
    monkeypatch.setattr(finish_module, "emit", emitted)

    finish_module.finish_ingestion.run(WIRE)

    assert emitted.events == []


def test_a_row_written_complete_before_a_lost_emit_still_gets_its_event(monkeypatch):
    """The row commits first, so `status == complete` is not evidence the event landed."""
    from app.worker.tasks.pipeline import finish as finish_module

    db, _ = _finish_db("complete", already_emitted=False)
    emitted = _Emitted()
    monkeypatch.setattr(finish_module, "get_sync_db", _sync_db(db))
    monkeypatch.setattr(finish_module, "emit", emitted)

    finish_module.finish_ingestion.run(WIRE)

    assert emitted.events == ["job.complete"]


def test_a_failed_run_is_never_reported_complete(monkeypatch):
    """A hop that failed already emitted job.failed. Two terminal events is worse than none."""
    from app.worker.tasks.pipeline import finish as finish_module

    db, _ = _finish_db("failed", already_emitted=False)
    emitted = _Emitted()
    monkeypatch.setattr(finish_module, "get_sync_db", _sync_db(db))
    monkeypatch.setattr(finish_module, "emit", emitted)

    finish_module.finish_ingestion.run(WIRE)

    assert emitted.events == []


def test_a_missing_job_row_emits_nothing(monkeypatch):
    from app.worker.tasks.pipeline import finish as finish_module

    db = MagicMock()
    db.get.return_value = None
    emitted = _Emitted()
    monkeypatch.setattr(finish_module, "get_sync_db", _sync_db(db))
    monkeypatch.setattr(finish_module, "emit", emitted)

    finish_module.finish_ingestion.run(WIRE)

    assert emitted.events == []


# ---------------------------------------------------------------------------
# generate_metadata: a document whose every batch failed did not complete
# ---------------------------------------------------------------------------


class _MetadataCursor:
    """One chunk pending, and a COUNT(*) of 0 so the batch loop runs."""

    def __init__(self):
        self._fetchone = [(0,)]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return [("c1", "the content")]

    def fetchone(self):
        return self._fetchone[0] if self._fetchone else (0,)


def _run_metadata(monkeypatch, enrich):
    from app.worker.tasks.pipeline import metadata as metadata_module

    mock_db = MagicMock()
    mock_db.get.return_value = MagicMock(neon_connection_string=b"encrypted")
    tenant_conn = MagicMock()
    tenant_conn.cursor.return_value = _MetadataCursor()

    emitted = _Emitted()
    monkeypatch.setattr(metadata_module, "get_sync_db", _sync_db(mock_db))
    monkeypatch.setattr(metadata_module, "fernet_decrypt", lambda _: "fake-conn")
    monkeypatch.setattr(metadata_module, "require_ciphertext", lambda value, _: value)
    monkeypatch.setattr(metadata_module.psycopg2, "connect", lambda *a, **k: tenant_conn)
    monkeypatch.setattr(metadata_module, "ledger_recorder", lambda _: MagicMock())
    monkeypatch.setattr(metadata_module, "enrich_chunks_batch", enrich)
    monkeypatch.setattr(metadata_module, "emit", emitted)
    monkeypatch.setattr(
        metadata_module, "fail_the_job", lambda job_id, reason, db, redis: emitted("j", "job.failed", {}, db, redis)
    )
    return metadata_module, emitted


def test_a_document_whose_every_batch_failed_is_not_reported_complete(monkeypatch):
    """The wrong sequence was metadata.complete then job.failed for the same document."""
    metadata_module, emitted = _run_metadata(
        monkeypatch, MagicMock(side_effect=RuntimeError("the model refused"))
    )

    with pytest.raises(metadata_module.MetadataEnrichmentFailed):
        metadata_module.generate_metadata.run(WIRE)

    assert "metadata.complete" not in emitted.events, (
        f"nothing about this document completed: {emitted.events}"
    )
    assert emitted.events == ["metadata.started", "metadata.failed", "job.failed"], emitted.events


def test_a_document_that_enriched_something_is_still_reported_complete(monkeypatch):
    """A partial document produced metadata. Only the wholly failed one is a failure."""
    metadata_module, emitted = _run_metadata(
        monkeypatch,
        MagicMock(
            return_value=[
                MagicMock(summary="s", keywords=[], questions=[], entities=[])
            ]
        ),
    )

    metadata_module.generate_metadata.run(WIRE)

    assert emitted.events[-1] == "metadata.complete"
    assert "metadata.failed" not in emitted.events
    assert "job.failed" not in emitted.events
