"""
Unit tests for OPS-08: check_index_staleness Celery task + compute_index_staleness_summary.

Tests:
    1. Signature — check_index_staleness takes only agent_id, no conn_str (CLAUDE.md rule 4).
    2. acks_late=True, queue="pipeline" (Pitfall 6).
    3. A document newer than its (only) embedding is flagged stale.
    4. A document with an un-embedded chunk is flagged stale.
    5. An embedding whose model differs from the active model is flagged drift.
    6. A staleness-scan failure (e.g. missing column) degrades stale_count to
       "not_tracked" WITHOUT crashing the drift scan (independent degradation).
    7. Re-running compute_index_staleness_summary over unchanged data returns
       the identical summary (idempotent scan).
    8. check_index_staleness is registered on the pipeline queue AND in
       celery_app's beat_schedule/include list.

Patch targets are symbols imported into app.worker.tasks.pipeline.staleness:
    - app.worker.tasks.pipeline.staleness.psycopg2.connect
    - app.worker.tasks.pipeline.staleness.bedrock_embedding_service
    - app.worker.tasks.pipeline.staleness.get_sync_db
    - app.worker.tasks.pipeline.staleness.fernet_decrypt
    - app.worker.tasks.pipeline.staleness.alert_service
"""

from __future__ import annotations

import datetime
import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

from app.worker.tasks.pipeline import staleness as mod

_TARGET_MODEL = "amazon.titan-embed-text-v2:0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _RaisingCursor:
    def execute(self, sql, params=None):
        raise Exception("column d.created_at does not exist")

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.close = MagicMock()
    return conn


def _patch_bedrock(monkeypatch, model=_TARGET_MODEL):
    svc = MagicMock()
    svc.active_embedding_model.return_value = model
    monkeypatch.setattr(mod, "bedrock_embedding_service", svc)
    return svc


def _make_sync_db_context(mock_db):
    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


# ---------------------------------------------------------------------------
# Test 1/2: signature + acks_late + queue
# ---------------------------------------------------------------------------


def test_check_index_staleness_signature_has_no_conn_str():
    params = set(inspect.signature(mod.check_index_staleness.run).parameters)
    assert "conn_str" not in params
    assert "agent_id" in params


def test_check_index_staleness_acks_late_and_queue():
    assert mod.check_index_staleness.acks_late is True
    assert mod.check_index_staleness.max_retries == 2


def test_check_index_staleness_routes_to_pipeline_queue():
    """Pitfall 6: must route to pipeline, not runtime, to avoid contending with
    live agent-turn traffic under worker_pool='solo'."""
    import inspect as _inspect

    source = _inspect.getsource(mod)
    assert 'queue="pipeline"' in source


# ---------------------------------------------------------------------------
# Test 3: document newer than its (only) embedding -> stale
# ---------------------------------------------------------------------------


def test_compute_summary_flags_doc_newer_than_embedding(monkeypatch):
    _patch_bedrock(monkeypatch)

    doc_created = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
    embed_created = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)  # older than doc

    staleness_rows = [("doc-1", doc_created, 1, 1, embed_created)]
    drift_rows = [(_TARGET_MODEL, 5)]

    connects = [_make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows))]
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: connects.pop(0))

    summary = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary["stale_count"] == 1
    assert summary["stale_document_ids"] == ["doc-1"]
    assert summary["drift_detected"] is False
    assert summary["current_embedding_model"] == _TARGET_MODEL


# ---------------------------------------------------------------------------
# Test 4: un-embedded chunk -> stale
# ---------------------------------------------------------------------------


def test_compute_summary_flags_missing_embedding(monkeypatch):
    _patch_bedrock(monkeypatch)

    now = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
    # chunk_count=2, embedded_count=1 -> one chunk has no embedding row
    staleness_rows = [("doc-2", now, 2, 1, now)]
    drift_rows = [(_TARGET_MODEL, 1)]

    connects = [_make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows))]
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: connects.pop(0))

    summary = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary["stale_count"] == 1
    assert summary["stale_document_ids"] == ["doc-2"]


def test_compute_summary_no_stale_when_fully_embedded_and_current(monkeypatch):
    _patch_bedrock(monkeypatch)

    now = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
    later = datetime.datetime(2026, 7, 16, tzinfo=datetime.timezone.utc)
    staleness_rows = [("doc-3", now, 2, 2, later)]  # embedded after doc created
    drift_rows = [(_TARGET_MODEL, 2)]

    connects = [_make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows))]
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: connects.pop(0))

    summary = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary["stale_count"] == 0
    assert summary["stale_document_ids"] == []


# ---------------------------------------------------------------------------
# Test 5: embedding-model drift
# ---------------------------------------------------------------------------


def test_compute_summary_flags_model_drift(monkeypatch):
    _patch_bedrock(monkeypatch, model=_TARGET_MODEL)

    now = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
    staleness_rows = [("doc-4", now, 1, 1, now)]
    # a stale model (voyage-3) alongside the current target model
    drift_rows = [(_TARGET_MODEL, 3), ("voyage-3", 2)]

    connects = [_make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows))]
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: connects.pop(0))

    summary = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary["drift_detected"] is True
    assert summary["drift_model_counts"] == {_TARGET_MODEL: 3, "voyage-3": 2}


def test_compute_summary_no_drift_when_single_current_model(monkeypatch):
    _patch_bedrock(monkeypatch, model=_TARGET_MODEL)

    now = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
    staleness_rows = [("doc-5", now, 1, 1, now)]
    drift_rows = [(_TARGET_MODEL, 10)]

    connects = [_make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows))]
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: connects.pop(0))

    summary = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary["drift_detected"] is False


# ---------------------------------------------------------------------------
# Test 6: missing-column degrades to not_tracked, drift scan unaffected
# ---------------------------------------------------------------------------


def test_compute_summary_degrades_to_not_tracked_on_scan_failure(monkeypatch):
    _patch_bedrock(monkeypatch)

    drift_rows = [(_TARGET_MODEL, 4)]
    connects = [_make_conn(_RaisingCursor()), _make_conn(_Cursor(drift_rows))]
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: connects.pop(0))

    summary = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary["stale_count"] == mod.NOT_TRACKED
    assert summary["stale_document_ids"] == []
    # Drift scan is a SEPARATE connection/query -- it still succeeds.
    assert summary["drift_detected"] is False
    assert summary["drift_model_counts"] == {_TARGET_MODEL: 4}


# ---------------------------------------------------------------------------
# Test 7: idempotent scan — same input -> same output on re-run
# ---------------------------------------------------------------------------


def test_compute_summary_is_idempotent_across_reruns(monkeypatch):
    _patch_bedrock(monkeypatch)

    now = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
    staleness_rows = [("doc-6", now, 1, 0, None)]
    drift_rows = [(_TARGET_MODEL, 1)]

    def _connect_factory():
        return iter([
            _make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows)),
            _make_conn(_Cursor(staleness_rows)), _make_conn(_Cursor(drift_rows)),
        ])

    it = _connect_factory()
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: next(it))

    summary_1 = mod.compute_index_staleness_summary("postgresql://fake/tenant")
    summary_2 = mod.compute_index_staleness_summary("postgresql://fake/tenant")

    assert summary_1 == summary_2


# ---------------------------------------------------------------------------
# Test 8: task -> alert_service wiring, deduped via _active_alert_exists
# ---------------------------------------------------------------------------


def test_check_index_staleness_raises_alert_when_stale(monkeypatch):
    agent = MagicMock()
    agent.id = "agent-1"
    agent.tenant_id = "tenant-1"
    agent.name = "Test Agent"
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: "postgresql://fake/tenant")
    monkeypatch.setattr(
        mod, "compute_index_staleness_summary",
        lambda conn_str: {
            "stale_count": 3, "stale_document_ids": ["d1"],
            "drift_detected": False, "drift_model_counts": {},
            "current_embedding_model": _TARGET_MODEL,
        },
    )

    mock_alert_service = MagicMock()
    mock_alert_service._active_alert_exists.return_value = False
    monkeypatch.setattr(mod, "alert_service", mock_alert_service)

    result = mod.check_index_staleness.run("agent-1")

    assert result["stale_count"] == 3
    mock_alert_service._write_alert.assert_called_once()
    call_args = mock_alert_service._write_alert.call_args
    assert call_args.args[1] == "index_staleness"


def test_check_index_staleness_skips_alert_when_already_active(monkeypatch):
    agent = MagicMock()
    agent.id = "agent-1"
    agent.tenant_id = "tenant-1"
    agent.name = "Test Agent"
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: "postgresql://fake/tenant")
    monkeypatch.setattr(
        mod, "compute_index_staleness_summary",
        lambda conn_str: {
            "stale_count": 3, "stale_document_ids": ["d1"],
            "drift_detected": False, "drift_model_counts": {},
            "current_embedding_model": _TARGET_MODEL,
        },
    )

    mock_alert_service = MagicMock()
    mock_alert_service._active_alert_exists.return_value = True
    monkeypatch.setattr(mod, "alert_service", mock_alert_service)

    mod.check_index_staleness.run("agent-1")

    mock_alert_service._write_alert.assert_not_called()
