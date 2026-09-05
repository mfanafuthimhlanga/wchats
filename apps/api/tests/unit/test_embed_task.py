"""
Unit tests for embed_and_migrate Celery task — ING-07, ING-09.

Tests:
  1. test_embed_and_migrate_acks_late                           — acks_late=True, max_retries=3, default_retry_delay=5
  2. test_embed_and_migrate_signature: the typed core seam, no conn/api_key param
  2b. test_embed_and_migrate_takes_the_job_and_gives_the_same_job_back: IngestionJob in,
      IngestionJob out
  2c. test_a_result_dict_the_job_cannot_be_built_from_is_returned_unchanged
  3. test_embed_and_migrate_calls_voyage_via_service            — embed_chunks called with chunk texts
  4. test_embed_and_migrate_upserts_with_on_conflict_chunk_id   — SQL shape: INSERT INTO embeddings + ON CONFLICT (chunk_id) DO UPDATE
  5. test_embed_and_migrate_runs_reindex_concurrently           — REINDEX SQL + AUTOCOMMIT isolation set before execute
  6. test_embed_and_migrate_emits_its_own_step_events_and_nothing_terminal
     event order: embedding.started → embedding.complete → ingestion.complete,
     and job.complete is absent. finish_ingestion owns it (#168)
  7. test_embed_and_migrate_leaves_the_job_row_alone. The terminal row write
     moved to finish_ingestion with the event (#168)

Patch targets are symbols imported into app.worker.tasks.pipeline.embed, NOT
the original module paths (e.g. patch app.worker.tasks.pipeline.embed.fernet_decrypt,
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


def _make_mock_job():
    """Return a mock Job with mutable status and finished_at."""
    job = MagicMock()
    job.status = "running"
    job.finished_at = None
    job.error = None
    return job


class _MockCursor:
    """Mock psycopg2 cursor that records execute() SQL calls.

    Returns configurable fetchall() and fetchone() results.
    Supports 'with cursor:' context manager protocol.
    """

    def __init__(self, fetchall_result=None, fetchone_sequence=None):
        """
        Args:
            fetchall_result: value returned by fetchall() — default [].
            fetchone_sequence: list of values returned by consecutive fetchone() calls.
        """
        self.executed_sqls = []  # list of (sql_str, params) tuples
        self._fetchall_result = fetchall_result if fetchall_result is not None else []
        self._fetchone_seq = list(fetchone_sequence or [])
        self._fetchone_idx = 0
        # Track set_isolation_level calls on the parent connection
        self._isolation_level_set = None

    def execute(self, sql, params=None):
        self.executed_sqls.append((str(sql), params))

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        if self._fetchone_idx < len(self._fetchone_seq):
            val = self._fetchone_seq[self._fetchone_idx]
            self._fetchone_idx += 1
            return val
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_mock_tenant_conn(mock_cursor):
    """Return a mock psycopg2 connection that returns mock_cursor from .cursor()."""
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


def _build_standard_mocks(chunks=None):
    """Build the standard set of mocks used by most embed_and_migrate tests.

    Returns a tuple:
        (mock_db, mock_dml_conn, mock_reindex_conn, dml_cursor, reindex_cursor, mock_embed, mock_emit)

    chunks: list of (chunk_id, content) tuples for the DML fetchall result.
            Defaults to [("c1", "txt1"), ("c2", "txt2")].
    """
    if chunks is None:
        chunks = [("c1", "txt1"), ("c2", "txt2")]

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_job = _make_mock_job()
    mock_db.get.side_effect = lambda model, id_: mock_agent if "Agent" in str(model) else mock_job

    # DML cursor: fetchall returns chunks, fetchone for source_uri lookup
    dml_cursor = _MockCursor(
        fetchall_result=chunks,
        fetchone_sequence=[("s3://bucket/doc.pdf",)],  # source_uri for temp delete
    )
    mock_dml_conn = _make_mock_tenant_conn(dml_cursor)

    # Reindex cursor: separate connection for REINDEX CONCURRENTLY
    reindex_cursor = _MockCursor()
    mock_reindex_conn = _make_mock_tenant_conn(reindex_cursor)
    # Track isolation level set on reindex connection
    mock_reindex_conn.set_isolation_level = MagicMock()
    # Make reindex_conn work as context manager (with psycopg2.connect(...) as conn:)
    mock_reindex_conn.__enter__ = lambda s: s
    mock_reindex_conn.__exit__ = MagicMock(return_value=False)

    mock_embed = MagicMock(return_value=[[0.1] * 1024, [0.2] * 1024])
    mock_emit = MagicMock()

    return mock_db, mock_dml_conn, mock_reindex_conn, dml_cursor, reindex_cursor, mock_embed, mock_emit, mock_job


def _patch_task_seams(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit):
    """Point embed_and_migrate's outside edges at fakes; return the connect() record."""
    connect_calls = []

    def _psycopg2_connect(conn_str):
        if not connect_calls:
            # First call → DML connection
            connect_calls.append("dml")
            return mock_dml_conn
        else:
            # Second call → REINDEX connection
            connect_calls.append("reindex")
            return mock_reindex_conn

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.embed.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.embed.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.embed.psycopg2.connect",
        _psycopg2_connect,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.embed.embed_chunks",
        mock_embed,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.embed.emit",
        mock_emit,
    )

    return connect_calls


def _run_task_with_mocks(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit):
    """Patch all external dependencies and invoke embed_and_migrate.run()."""
    from app.worker.tasks.pipeline.embed import embed_and_migrate

    connect_calls = _patch_task_seams(
        monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit
    )

    embed_and_migrate.run(JOB.to_dict())

    return connect_calls


# ---------------------------------------------------------------------------
# Test 1: acks_late, max_retries, default_retry_delay
# ---------------------------------------------------------------------------


def test_embed_and_migrate_acks_late():
    """embed_and_migrate must have acks_late=True, max_retries=3, default_retry_delay=5."""
    from app.worker.tasks.pipeline.embed import embed_and_migrate

    assert embed_and_migrate.acks_late is True, "acks_late must be True (CLAUDE.md rule 5)"
    assert embed_and_migrate.max_retries == 3, (
        f"max_retries must be 3 but got {embed_and_migrate.max_retries}"
    )
    assert embed_and_migrate.default_retry_delay == 5, (
        f"default_retry_delay must be 5 but got {embed_and_migrate.default_retry_delay}"
    )


# ---------------------------------------------------------------------------
# Test 2: Signature — (self, result: dict) only; no conn or api_key param
# ---------------------------------------------------------------------------


def test_embed_and_migrate_signature():
    """The core seam is IngestionJob in, IngestionJob out (ticket #43).

    This enforces CLAUDE.md rule 4: connection strings NEVER in Celery task args.
    inspect.signature follows __wrapped__ past the Celery edge that converts the
    wire dict, so what it reports is the typed seam the task body works in.
    """
    from app.worker.tasks.pipeline.embed import embed_and_migrate

    sig = inspect.signature(embed_and_migrate.run)

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


def test_embed_and_migrate_takes_the_job_and_gives_the_same_job_back(monkeypatch):
    """The terminal hop returns the job it was handed, as the type rather than four keys."""
    from app.worker.tasks.pipeline.embed import embed_and_migrate

    mock_db, mock_dml_conn, mock_reindex_conn, _, _, mock_embed, mock_emit, _ = _build_standard_mocks()
    _patch_task_seams(
        monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit
    )

    handed_on = _core(embed_and_migrate)(embed_and_migrate, JOB)

    assert isinstance(handed_on, IngestionJob)
    assert handed_on == JOB


def test_a_result_dict_the_job_cannot_be_built_from_is_returned_unchanged(monkeypatch):
    """A dict missing an id is logged and handed straight back, with no work done.

    Defensive, and older than the type: a chain re-dispatched mid-flight can
    deliver a dict from a different revision of the pipeline. The `or` chain that
    used to do this here is gone; construction refuses the dict and the edge
    logs the same event.
    """
    from app.worker.tasks.pipeline.embed import embed_and_migrate

    opened = []

    @contextmanager
    def _record_open():
        opened.append("get_sync_db")
        yield MagicMock()

    monkeypatch.setattr("app.worker.tasks.pipeline.embed.get_sync_db", _record_open)

    payload = {"tenant_id": "t", "agent_id": "", "job_id": "j", "document_ids": ["d1"]}

    with capture_logs() as logs:
        output = embed_and_migrate.run(payload)

    assert output == payload
    assert opened == [], "the task reached the control DB with an unusable result dict"
    assert [entry["event"] for entry in logs] == ["embed_and_migrate.invalid_result_dict"]


# ---------------------------------------------------------------------------
# Test 3: embed_chunks is called via the service (not _vo directly)
# ---------------------------------------------------------------------------


def test_embed_and_migrate_calls_voyage_via_service(monkeypatch):
    """embed_and_migrate calls embed_chunks() with the fetched chunk texts.

    embed_chunks() is the correct abstraction — the task must NOT call _vo.embed
    directly. This ensures the 128-item batching and tenacity retry are always applied.
    """
    mock_db, mock_dml_conn, mock_reindex_conn, _, _, mock_embed, mock_emit, _ = _build_standard_mocks()

    _run_task_with_mocks(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit)

    assert mock_embed.call_count == 1, (
        f"Expected embed_chunks to be called once but got {mock_embed.call_count}"
    )
    actual_texts = mock_embed.call_args[0][0]
    assert actual_texts == ["txt1", "txt2"], (
        f"Expected embed_chunks called with ['txt1', 'txt2'] but got {actual_texts!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: ON CONFLICT (chunk_id) DO UPDATE SQL shape
# ---------------------------------------------------------------------------


def test_embed_and_migrate_upserts_with_on_conflict_chunk_id(monkeypatch):
    """embed_and_migrate executes INSERT INTO embeddings with ON CONFLICT (chunk_id) DO UPDATE.

    This is the Layer 4 write-level idempotency contract (T-02-05-04):
    re-running the task produces no duplicate embeddings rows.
    """
    mock_db, mock_dml_conn, mock_reindex_conn, dml_cursor, _, mock_embed, mock_emit, _ = _build_standard_mocks()

    _run_task_with_mocks(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit)

    all_sqls = [sql for sql, _ in dml_cursor.executed_sqls]

    upsert_sqls = [
        s for s in all_sqls
        if "INSERT INTO embeddings" in s and "ON CONFLICT (chunk_id) DO UPDATE" in s
    ]
    assert len(upsert_sqls) >= 1, (
        "Expected at least one SQL with 'INSERT INTO embeddings' AND "
        "'ON CONFLICT (chunk_id) DO UPDATE' but found none.\n"
        "All recorded SQLs:\n" + "\n".join(all_sqls)
    )


# ---------------------------------------------------------------------------
# Test 5: REINDEX CONCURRENTLY + ISOLATION_LEVEL_AUTOCOMMIT
# ---------------------------------------------------------------------------


def test_embed_and_migrate_runs_reindex_concurrently(monkeypatch):
    """embed_and_migrate runs REINDEX INDEX CONCURRENTLY in AUTOCOMMIT isolation.

    Two assertions are required:
    (a) The exact SQL string "REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx"
        is executed on the reindex connection.
    (b) set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT) was called
        on the reindex connection BEFORE the REINDEX execute().

    Assertion (b) proves the task does not accidentally run REINDEX inside a
    transaction block, which would raise "REINDEX CONCURRENTLY cannot run inside
    a transaction block" at runtime (PITFALLS.md §5).
    """
    import psycopg2.extensions

    mock_db, mock_dml_conn, mock_reindex_conn, _, reindex_cursor, mock_embed, mock_emit, _ = _build_standard_mocks()

    # Track operation order on the reindex connection
    reindex_ops = []

    def _track_isolation(level):
        reindex_ops.append(("set_isolation_level", level))

    mock_reindex_conn.set_isolation_level = _track_isolation

    original_execute = reindex_cursor.execute

    def _track_execute(sql, params=None):
        reindex_ops.append(("execute", str(sql)))
        original_execute(sql, params)

    reindex_cursor.execute = _track_execute

    _run_task_with_mocks(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit)

    # (a) Exact REINDEX SQL was executed
    reindex_sqls = [
        sql for op, sql in reindex_ops
        if op == "execute" and "REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx" in sql
    ]
    assert len(reindex_sqls) >= 1, (
        "Expected 'REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx' to be executed "
        "on the reindex connection, but it was not found.\n"
        f"Recorded reindex_ops: {reindex_ops}"
    )

    # (b) ISOLATION_LEVEL_AUTOCOMMIT was set BEFORE the REINDEX execute
    isolation_ops = [i for i, (op, _) in enumerate(reindex_ops) if op == "set_isolation_level"]
    reindex_exec_ops = [
        i for i, (op, sql) in enumerate(reindex_ops)
        if op == "execute" and "REINDEX INDEX CONCURRENTLY" in sql
    ]
    assert isolation_ops, (
        "set_isolation_level was never called on the reindex connection. "
        "REINDEX CONCURRENTLY must run in AUTOCOMMIT mode (PITFALLS.md §5)."
    )
    assert reindex_exec_ops, "REINDEX execute not found in reindex_ops"
    assert isolation_ops[0] < reindex_exec_ops[0], (
        f"set_isolation_level must be called BEFORE REINDEX execute. "
        f"Got isolation at op {isolation_ops[0]}, REINDEX at op {reindex_exec_ops[0]}. "
        f"Full ops: {reindex_ops}"
    )
    # Verify AUTOCOMMIT value was passed
    autocommit_ops = [
        level for op, level in reindex_ops
        if op == "set_isolation_level"
        and level == psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
    ]
    assert autocommit_ops, (
        "set_isolation_level was called but NOT with ISOLATION_LEVEL_AUTOCOMMIT. "
        f"Values passed: {[level for op, level in reindex_ops if op == 'set_isolation_level']}"
    )


# ---------------------------------------------------------------------------
# Test 6: step events only, in order, and no terminal event (#168)
# ---------------------------------------------------------------------------


def test_embed_and_migrate_emits_its_own_step_events_and_nothing_terminal(monkeypatch):
    """embedding.started, embedding.complete, ingestion.complete, in that order.

    `job.complete` used to be the fourth. This hop stopped being the chain's last
    when synthesize_retrieval_strategy joined, so a subscriber that closes on the
    terminal event closed with a hop still to run. finish_ingestion owns it now,
    and `tests/unit/test_ingestion_terminal_event.py` holds the whole rule.
    """
    mock_db, mock_dml_conn, mock_reindex_conn, _, _, mock_embed, _, _ = _build_standard_mocks()

    emitted_events = []

    def capture_emit(job_id, event_type, payload, db, redis_client):
        emitted_events.append((job_id, event_type))

    _run_task_with_mocks(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, capture_emit)

    event_types = [et for _, et in emitted_events]

    assert "job.complete" not in event_types, (
        f"embed_and_migrate may not end the run: {event_types}"
    )
    assert "job.failed" not in event_types, (
        f"embed_and_migrate may not end the run: {event_types}"
    )

    for evt in ("embedding.started", "embedding.complete", "ingestion.complete"):
        assert evt in event_types, (
            f"required step event '{evt}' not found in emitted events: {event_types}"
        )

    def _find_first(event_name):
        return next(i for i, e in enumerate(event_types) if e == event_name)

    pos_started = _find_first("embedding.started")
    pos_embed_complete = _find_first("embedding.complete")
    pos_ingestion_complete = _find_first("ingestion.complete")

    assert pos_started < pos_embed_complete < pos_ingestion_complete, (
        f"the three step events are out of order: {event_types}"
    )


# ---------------------------------------------------------------------------
# Test 7: the terminal row write moved out with the terminal event (#168)
# ---------------------------------------------------------------------------


def test_embed_and_migrate_leaves_the_job_row_alone(monkeypatch):
    """The row and the event are one record, so they moved together.

    A row saying 'complete' while two hops still had to run is the same defect as
    the event, and it is the half a reader of the jobs table sees. agent.status is
    not touched either; that stays M1-only behaviour.
    """
    mock_db, mock_dml_conn, mock_reindex_conn, _, _, mock_embed, mock_emit, mock_job = _build_standard_mocks()

    _run_task_with_mocks(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_embed, mock_emit)

    assert mock_job.status != "complete", (
        f"finish_ingestion writes the terminal row state, not this hop: {mock_job.status!r}"
    )
    assert mock_job.finished_at is None, (
        f"a run that has two hops to go has not finished: {mock_job.finished_at!r}"
    )
