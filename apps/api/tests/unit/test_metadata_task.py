"""
Unit tests for generate_metadata Celery task — ING-06.

Tests:
  1. test_generate_metadata_acks_late                                    — acks_late=True, max_retries=3
  2. test_generate_metadata_signature: the typed core seam, no conn/api_key param
  2b. test_generate_metadata_takes_the_job_and_gives_the_same_job_back: IngestionJob in,
      IngestionJob out
  2c. test_a_result_dict_the_job_cannot_be_built_from_is_returned_unchanged
  3. test_layer_3_idempotency_skips_haiku_when_metadata_exists           — Layer 3 fires; enrich_chunks_batch NOT called
  4. test_generate_metadata_calls_enrich_when_no_existing_metadata        — enrich_chunks_batch called once with ["the content"]
  5. test_generate_metadata_upserts_entities_with_on_conflict_normalized_type — entity UPSERT SQL shape
  6. test_generate_metadata_emits_event_sequence                          — metadata.started then metadata.complete
  7. test_wholly_failed_enrichment_fails_the_job                          (issue #23, 0 enriched is not a success)
  8. test_partial_enrichment_still_succeeds_and_reports_its_counts        (1 of 11 enriched still completes)
  9. test_a_document_with_no_chunks_still_succeeds                        (nothing to enrich is not a failure)
 10. test_enriched_chunks_reach_persistence_as_chunk_metadata             (typed records at the persistence seam)

Patch targets are symbols imported into app.worker.tasks.pipeline.metadata, NOT
the original module paths (e.g. patch app.worker.tasks.pipeline.metadata.fernet_decrypt,
not app.core.security.fernet_decrypt).
"""

import base64
import os

# ---------------------------------------------------------------------------
# Environment setup — MUST run before any `from app` import (pydantic-settings)
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
)
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault(
    "CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb"
)
os.environ.setdefault(
    "CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb"
)
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

import inspect
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

from structlog.testing import capture_logs

from app.domain.ingestion_job import IngestionJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JOB = IngestionJob(tenant_id="t", agent_id="a", job_id="j", document_ids=["d1"])


def _core(task):
    """The task's core, the half that takes an IngestionJob and returns one.

    The Celery task is the edge. It takes the wire dict, builds the job, and
    sends the returned job back out as a dict. functools.wraps puts the core on
    the edge as __wrapped__, so a test can hold the typed seam directly.
    """
    return task.run.__wrapped__


def _make_sync_db_context(mock_db):
    """Return a context manager factory that yields mock_db."""
    @contextmanager
    def _ctx():
        yield mock_db
    return _ctx


def _make_mock_agent():
    """Return a mock Agent with a usable neon_connection_string."""
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted-conn"
    return agent


class _MockCursor:
    """Mock psycopg2 cursor that records execute() SQL calls.

    Returns configurable fetchone() results in sequence. Supports
    'with cursor:' context manager protocol.
    """

    def __init__(self, fetchone_sequence=None):
        """
        Args:
            fetchone_sequence: list of values returned by consecutive fetchone() calls.
                               When exhausted, returns None.
        """
        self.executed_sqls = []  # (sql_str, params) tuples
        self._fetchone_seq = list(fetchone_sequence or [])
        self._fetchone_idx = 0
        self.fetchall_result = []  # override for fetchall()

    def execute(self, sql, params=None):
        self.executed_sqls.append((str(sql), params))

    def fetchone(self):
        if self._fetchone_idx < len(self._fetchone_seq):
            val = self._fetchone_seq[self._fetchone_idx]
            self._fetchone_idx += 1
            return val
        return None

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_mock_tenant_conn(mock_cursor):
    """Return a mock psycopg2 connection that returns mock_cursor from .cursor()."""
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    # cursor() as context manager (with tenant_conn.cursor() as cur:)
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


# ---------------------------------------------------------------------------
# Test 1: acks_late and max_retries
# ---------------------------------------------------------------------------


def test_generate_metadata_acks_late():
    """generate_metadata must have acks_late=True and max_retries=3."""
    from app.worker.tasks.pipeline.metadata import generate_metadata

    assert generate_metadata.acks_late is True
    assert generate_metadata.max_retries == 3


# ---------------------------------------------------------------------------
# Test 2: Signature accepts only (self, result: dict); no connection/api_key param
# ---------------------------------------------------------------------------


def test_generate_metadata_signature():
    """The core seam is IngestionJob in, IngestionJob out, and no conn or api_key (#43).

    inspect.signature follows __wrapped__ past the Celery edge that converts the
    wire dict, so what it reports is the typed seam the task body works in.
    """
    from app.worker.tasks.pipeline.metadata import generate_metadata

    sig = inspect.signature(generate_metadata.run)

    assert list(sig.parameters) == ["job"], (
        f"Expected the core seam ['job'] but got {list(sig.parameters)}"
    )
    assert sig.parameters["job"].annotation is IngestionJob
    assert sig.return_annotation is IngestionJob

    sig_str = str(sig).lower()
    assert "conn" not in sig_str, (
        f"Signature contains 'conn' — connection string must not be in task args: {sig}"
    )
    assert "api_key" not in sig_str, (
        f"Signature contains 'api_key' — API keys must not be in task args: {sig}"
    )


def test_generate_metadata_takes_the_job_and_gives_the_same_job_back(monkeypatch):
    """The hop forwards the job it was handed, as the type rather than four keys."""
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_cursor = _MockCursor(fetchone_sequence=[(1,)])
    mock_cursor.fetchall_result = [("c1", "content")]
    mock_db = _make_mock_db(_make_mock_agent(), MagicMock())
    _patch_task_seams(
        monkeypatch,
        mock_db,
        _make_mock_tenant_conn(mock_cursor),
        MagicMock(),
        _capture_emit([]),
    )

    handed_on = _core(generate_metadata)(generate_metadata, JOB)

    assert isinstance(handed_on, IngestionJob)
    assert handed_on == JOB


def test_a_result_dict_the_job_cannot_be_built_from_is_returned_unchanged(monkeypatch):
    """A dict missing an id is logged and handed straight back, with no work done.

    Defensive, and older than the type: a chain re-dispatched mid-flight can
    deliver a dict from a different revision of the pipeline. The `or` chain that
    used to do this here is gone; construction refuses the dict and the edge
    logs the same event.
    """
    from app.worker.tasks.pipeline.metadata import generate_metadata

    opened = []

    @contextmanager
    def _record_open():
        opened.append("get_sync_db")
        yield MagicMock()

    monkeypatch.setattr("app.worker.tasks.pipeline.metadata.get_sync_db", _record_open)

    payload = {"tenant_id": "t", "agent_id": "a", "job_id": "j"}

    with capture_logs() as logs:
        output = generate_metadata.run(payload)

    assert output == payload
    assert opened == [], "the task reached the control DB with an unusable result dict"
    assert [entry["event"] for entry in logs] == ["generate_metadata.invalid_result_dict"]


# ---------------------------------------------------------------------------
# Test 3: Layer 3 idempotency — skip Haiku call when chunk_metadata row exists
# ---------------------------------------------------------------------------


def test_layer_3_idempotency_skips_haiku_when_metadata_exists(monkeypatch):
    """generate_metadata skips enrich_chunks_batch when SELECT COUNT(*) FROM chunk_metadata > 0.

    This is the Layer 3 idempotency contract: previously enriched chunks must not
    trigger additional Haiku API calls on task retry (prevents re-billing). When all
    chunks are already enriched, `pending` is empty so no batch call is made.
    """
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor sequence:
    #   fetchall() → [("c1", "content")]   (SELECT id, content FROM chunks)
    #   fetchone() → (1,)                  (SELECT COUNT(*) FROM chunk_metadata)
    mock_cursor = _MockCursor(fetchone_sequence=[(1,)])
    mock_cursor.fetchall_result = [("c1", "content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    mock_enrich = MagicMock()
    mock_emit = MagicMock()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunks_batch",
        mock_enrich,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        mock_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    # Layer 3 fired: enrich_chunks_batch must NOT be called
    assert mock_enrich.call_count == 0, (
        f"Layer 3 idempotency failed: enrich_chunks_batch was called {mock_enrich.call_count} "
        "time(s) but should have been skipped (chunk_metadata row exists)"
    )


# ---------------------------------------------------------------------------
# Test 4: enrich_chunk IS called when no existing chunk_metadata row
# ---------------------------------------------------------------------------


def test_generate_metadata_calls_enrich_when_no_existing_metadata(monkeypatch):
    """generate_metadata calls enrich_chunks_batch once with the chunk content list when no metadata exists."""
    from app.services.metadata_service import ChunkMetadataAndEntities
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor sequence:
    #   fetchall() → [("c1", "the content")]
    #   fetchone() for COUNT(*) → (0,)        (no metadata row)
    mock_cursor = _MockCursor(fetchone_sequence=[(0,)])
    mock_cursor.fetchall_result = [("c1", "the content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    mock_meta = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[]
    )
    # Batched call returns a LIST of per-chunk results matching the batch size.
    mock_enrich = MagicMock(return_value=[mock_meta])
    mock_emit = MagicMock()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunks_batch",
        mock_enrich,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        mock_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    assert mock_enrich.call_count == 1, (
        f"Expected enrich_chunks_batch to be called once but got {mock_enrich.call_count}"
    )
    assert mock_enrich.call_args[0][0] == ["the content"], (
        f"Expected enrich_chunks_batch called with ['the content'] but got "
        f"{mock_enrich.call_args[0][0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: Entity UPSERT SQL shape — ON CONFLICT (normalized, type) + chunk_entities
# ---------------------------------------------------------------------------


def test_generate_metadata_upserts_entities_with_on_conflict_normalized_type(monkeypatch):
    """generate_metadata executes entity UPSERT with ON CONFLICT (normalized, type) DO UPDATE
    and chunk_entities INSERT with ON CONFLICT DO NOTHING.

    This proves the entity deduplication contract (T-02-04-02):
    same entity across chunks → one entities row, N chunk_entities rows.
    """
    from app.services.metadata_service import ChunkMetadataAndEntities, EntityExtraction
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor sequence:
    #   fetchall() → [("c1", "content with Acme")]
    #   fetchone() for COUNT(*) → (0,)       (no metadata row — proceed)
    #   fetchone() for RETURNING id → ("ent-uuid-1",)   (entity UPSERT returns id)
    mock_cursor = _MockCursor(fetchone_sequence=[(0,), ("ent-uuid-1",)])
    mock_cursor.fetchall_result = [("c1", "content with Acme")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    entity = EntityExtraction(name="Acme Corp", type="product", normalized="acme corp")
    mock_meta = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[entity]
    )
    # Batched call returns a LIST of per-chunk results matching the batch size.
    mock_enrich = MagicMock(return_value=[mock_meta])
    mock_emit = MagicMock()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunks_batch",
        mock_enrich,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        mock_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    all_sqls = [sql for sql, _ in mock_cursor.executed_sqls]

    # Entity UPSERT: must contain INSERT INTO entities + ON CONFLICT (normalized, type)
    entity_upsert_sqls = [
        s for s in all_sqls
        if "INSERT INTO entities" in s and "ON CONFLICT (normalized, type)" in s
    ]
    assert len(entity_upsert_sqls) >= 1, (
        "Expected at least one SQL with 'INSERT INTO entities' AND "
        "'ON CONFLICT (normalized, type)' but found none.\n"
        "All recorded SQLs:\n" + "\n".join(all_sqls)
    )

    # chunk_entities INSERT: must contain INSERT INTO chunk_entities + ON CONFLICT DO NOTHING
    ce_sqls = [
        s for s in all_sqls
        if "INSERT INTO chunk_entities" in s and "ON CONFLICT DO NOTHING" in s
    ]
    assert len(ce_sqls) >= 1, (
        "Expected at least one SQL with 'INSERT INTO chunk_entities' AND "
        "'ON CONFLICT DO NOTHING' but found none.\n"
        "All recorded SQLs:\n" + "\n".join(all_sqls)
    )


# ---------------------------------------------------------------------------
# Test 6: Event sequence — metadata.started then metadata.complete
# ---------------------------------------------------------------------------


def test_generate_metadata_emits_event_sequence(monkeypatch):
    """generate_metadata emits metadata.started then metadata.complete (in that order)."""
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor: COUNT(*) → (1,) so we skip enrich (not testing entity path here)
    mock_cursor = _MockCursor(fetchone_sequence=[(1,)])
    mock_cursor.fetchall_result = [("c1", "content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    emitted_events = []

    def capture_emit(job_id, event_type, payload, db, redis_client):
        emitted_events.append((job_id, event_type))

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunks_batch",
        MagicMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        capture_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    event_types = [et for _, et in emitted_events]
    assert event_types == ["metadata.started", "metadata.complete"], (
        f"Expected event sequence ['metadata.started', 'metadata.complete'] "
        f"but got {event_types}"
    )

    # Verify job_id is passed correctly to all emit calls
    for job_id_arg, _ in emitted_events:
        assert job_id_arg == "j", (
            f"Expected job_id='j' but got {job_id_arg!r}"
        )


# ---------------------------------------------------------------------------
# Enrichment visibility (ticket #42 slice 2, issue #23)
#
# The task used to read the enrichment straight off the model's parsed response
# and pass it to an INSERT, so nothing in it held the idea "this chunk was
# enriched". Tests 7-10 read the counts that idea makes countable, and test 10
# reads the ChunkMetadata records themselves at the persistence seam.
# ---------------------------------------------------------------------------

CHUNK_ONE = uuid.UUID("94a95541-fb48-5918-9a19-2a9e3932b380")


def _make_mock_db(mock_agent, mock_job):
    """Mock Session answering db.get(Agent, ...) and db.get(Job, ...) separately.

    The task looks up both models on one session, and the wholly-failed path
    writes to the Job, so the two cannot be the same mock.
    """
    mock_db = MagicMock()
    mock_db.get.side_effect = (
        lambda model, _id: mock_job if model.__name__ == "Job" else mock_agent
    )
    return mock_db


def _capture_emit(events):
    """emit() stand-in appending (event_type, payload) to the given list."""

    def _emit(job_id, event_type, payload, db, redis_client):
        events.append((event_type, payload))

    return _emit


def _patch_task_seams(monkeypatch, mock_db, mock_conn, enrich, emit_fn):
    """Point the task's four outside edges at fakes: control DB, tenant DB, Haiku, events.

    Two emit seams, not one. The task module holds its own imported `emit`, and
    app/services/job_failure.py reaches the same function through the events
    module, so the terminal job.failed arrives on the second seam (#63). Both are
    pointed at the one capture, so the event lists read as one stream.
    """
    import app.services.events as events_module

    module = "app.worker.tasks.pipeline.metadata."
    monkeypatch.setattr(module + "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(module + "fernet_decrypt", lambda _: "fake-conn")
    monkeypatch.setattr(module + "psycopg2.connect", lambda _: mock_conn)
    monkeypatch.setattr(module + "enrich_chunks_batch", enrich)
    monkeypatch.setattr(module + "emit", emit_fn)
    monkeypatch.setattr(events_module, "emit", emit_fn)


def _run_task(document_ids=("d1",)):
    """Run the task and report how it ended, as ("returned", dict) or ("raised", exc).

    A run that enriched nothing stops the chain by raising, so the way it ended
    is part of what these tests read, not just the value handed back.
    """
    from app.worker.tasks.pipeline.metadata import generate_metadata

    payload = {
        "tenant_id": "t",
        "agent_id": "a",
        "job_id": "j",
        "document_ids": list(document_ids),
    }
    try:
        return "returned", generate_metadata.run(payload)
    except Exception as exc:
        return "raised", exc


# ---------------------------------------------------------------------------
# Test 7: chunks processed, none enriched -> the job is failed, not succeeded
# ---------------------------------------------------------------------------


def test_wholly_failed_enrichment_fails_the_job(monkeypatch):
    """Issue #23: every batch failed, so no metadata exists, so the job is not a success.

    Observed 2026-08-22: batch_extraction_failed on all three documents,
    chunks_enriched=0, and the job still reported succeeded, so the failure was
    invisible to everyone downstream: the SSE stream, the job row, and the
    embed step that ran next over chunks with no metadata.

    The counts travel with the failure. "Enriched nothing" and "had nothing to
    enrich" are different outcomes and only the numbers separate them.
    """
    mock_agent = _make_mock_agent()
    mock_job = MagicMock()
    mock_db = _make_mock_db(mock_agent, mock_job)

    mock_cursor = _MockCursor(fetchone_sequence=[(0,)])
    mock_cursor.fetchall_result = [(CHUNK_ONE, "the content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    events = []
    _patch_task_seams(
        monkeypatch,
        mock_db,
        mock_conn,
        MagicMock(side_effect=RuntimeError("model refused the batch")),
        _capture_emit(events),
    )

    outcome, value = _run_task()

    assert outcome == "raised", (
        "The task reported success after enriching 0 of 1 chunk. A run that "
        "produced no metadata must not forward the chain."
    )
    reason = str(value)
    assert "chunks_seen=1" in reason, f"reason does not name chunks_seen: {reason!r}"
    assert "chunks_enriched=0" in reason, f"reason does not name chunks_enriched: {reason!r}"

    assert mock_job.status == "failed", f"job status is {mock_job.status!r}"
    assert "chunks_seen=1" in str(mock_job.error), f"job.error is {mock_job.error!r}"

    failed = [payload for event_type, payload in events if event_type == "job.failed"]
    assert len(failed) == 1, (
        "Expected one job.failed event, the terminal event both the SSE stream and "
        f"the admin ingest page watch for, but got event types {[e for e, _ in events]}"
    )
    assert "chunks_enriched=0" in str(failed[0].get("error")), (
        f"job.failed payload does not carry the counts: {failed[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: some chunks enriched -> today's behaviour, with today's counts
# ---------------------------------------------------------------------------


def test_partial_enrichment_still_succeeds_and_reports_its_counts(monkeypatch):
    """One batch of 10 fails, the 11th chunk enriches, and the job says 1 of 11.

    Partial-failure tolerance is deliberate. A re-run re-attempts the missing
    chunks, and Layer 3 skips the ones that landed. Only the wholly-failed
    outcome changed in issue #23.
    """
    from app.services.metadata_service import ChunkMetadataAndEntities

    mock_agent = _make_mock_agent()
    mock_job = MagicMock()
    mock_db = _make_mock_db(mock_agent, mock_job)

    chunk_rows = [(uuid.uuid5(uuid.NAMESPACE_DNS, f"c{n}"), f"content {n}") for n in range(11)]
    mock_cursor = _MockCursor(fetchone_sequence=[(0,)] * 11)
    mock_cursor.fetchall_result = chunk_rows
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    enriched = ChunkMetadataAndEntities(summary="s", keywords=["k"], questions=["q?"], entities=[])
    mock_enrich = MagicMock(side_effect=[RuntimeError("first batch refused"), [enriched]])

    events = []
    _patch_task_seams(monkeypatch, mock_db, mock_conn, mock_enrich, _capture_emit(events))

    outcome, value = _run_task()

    assert outcome == "returned", f"Partial enrichment must not fail the job: {value!r}"
    assert value["document_ids"] == ["d1"], f"chain dict not forwarded: {value!r}"
    assert [payload for event_type, payload in events if event_type == "job.failed"] == []

    progress = [payload for event_type, payload in events if event_type == "metadata.progress"]
    assert progress == [{"processed": 1, "total": 11}], (
        f"Expected one progress event reading 1 of 11 but got {progress!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: no chunks to enrich -> success, because nothing failed
# ---------------------------------------------------------------------------


def test_a_document_with_no_chunks_still_succeeds(monkeypatch):
    """An empty document enriches nothing and fails nothing.

    The wholly-failed rule counts the chunks a run took responsibility for. Zero
    of them is not a failure, which is what separates this from test 7.
    """
    mock_agent = _make_mock_agent()
    mock_job = MagicMock()
    mock_db = _make_mock_db(mock_agent, mock_job)

    mock_cursor = _MockCursor()
    mock_cursor.fetchall_result = []
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    mock_enrich = MagicMock()
    events = []
    _patch_task_seams(monkeypatch, mock_db, mock_conn, mock_enrich, _capture_emit(events))

    outcome, value = _run_task()

    assert outcome == "returned", f"An empty document must not fail the job: {value!r}"
    assert mock_enrich.call_count == 0
    assert [event_type for event_type, _ in events] == ["metadata.started", "metadata.complete"]


# ---------------------------------------------------------------------------
# Test 10: what reaches persistence is a ChunkMetadata carrying the chunk's id
# ---------------------------------------------------------------------------


def test_enriched_chunks_reach_persistence_as_chunk_metadata(monkeypatch):
    """The persistence seam receives one ChunkMetadata per enriched chunk, fields intact.

    The model returns per-chunk results in submission order and the task zips
    them back to chunk_ids by index, never by id. The model never sees an id.
    Reading the records at the seam is what proves the zip lined up: a summary
    landing on the wrong chunk_id is a mispairing this assertion catches and a
    call count cannot.
    """
    from app.domain.chunk_metadata import ChunkMetadata
    from app.services.metadata_service import ChunkMetadataAndEntities, EntityExtraction

    mock_agent = _make_mock_agent()
    mock_job = MagicMock()
    mock_db = _make_mock_db(mock_agent, mock_job)

    chunk_two = uuid.uuid5(uuid.NAMESPACE_DNS, "second")
    mock_cursor = _MockCursor(fetchone_sequence=[(0,), (0,)])
    mock_cursor.fetchall_result = [(CHUNK_ONE, "first content"), (chunk_two, "second content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    acme = EntityExtraction(name="Acme Corp", type="product", normalized="acme corp")
    first = ChunkMetadataAndEntities(
        summary="first summary", keywords=["one"], questions=["first?"], entities=[acme]
    )
    second = ChunkMetadataAndEntities(
        summary="second summary", keywords=["two"], questions=["second?"], entities=[]
    )

    persisted = []
    _patch_task_seams(
        monkeypatch,
        mock_db,
        mock_conn,
        MagicMock(return_value=[first, second]),
        _capture_emit([]),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata._persist_enrichment",
        lambda conn, enrichment: persisted.append(enrichment),
    )

    outcome, value = _run_task()

    assert outcome == "returned", f"{value!r}"
    assert persisted == [
        ChunkMetadata(
            chunk_id=CHUNK_ONE,
            summary="first summary",
            keywords=["one"],
            questions=["first?"],
            entities=[acme],
        ),
        ChunkMetadata(
            chunk_id=chunk_two,
            summary="second summary",
            keywords=["two"],
            questions=["second?"],
            entities=[],
        ),
    ], f"persisted records: {persisted!r}"
