"""
Unit tests for app.worker.tasks.runtime.eval.run_eval_suite (measurement-layer P1).

The task had no unit coverage at all, which is how audit defect D2 survived: the
task wrote `eval_results`, the terminal `eval_runs` status and the verified_qa
promotion to a Neon branch it then deleted in `finally`, so a successful run was
indistinguishable from a hung one and `eval_results` never existed on production
for evals.py's LEFT JOIN to find.

These tests assert on WHICH connection string each write opens. That is the only
observable difference between the correct and the broken version — both write
the same SQL, to the same table names, and both "succeed". A test that mocked
eval_service wholesale and asserted "write_eval_results was called" would have
passed against the defect.

No live PostgreSQL exists on this machine, so every DB boundary is a double:
psycopg2.connect, the control-DB session, the Neon branch API and eval_service's
writers. Nothing here proves a live database accepts the SQL — that is
integration territory and it SKIPS, which is unobserved, never a pass.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.worker.tasks.runtime import eval as mod

PRODUCTION = "postgresql://production/tenant"
BRANCH = "postgresql://neon-branch/tenant"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _Cursor:
    """Cursor double serving the task's two raw psycopg2 reads in order:
    the idempotency check (no running run) and the scenario fetch."""

    def __init__(self, scenario_rows):
        self.scenario_rows = scenario_rows
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return None  # no recent 'running' eval run -> no idempotent skip

    def fetchall(self):
        return self.scenario_rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_sync_db_context(mock_db):
    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


@pytest.fixture
def wired(monkeypatch):
    """run_eval_suite with every boundary doubled and every call recorded.

    Returns a dict of recorders; individual tests re-patch one collaborator to
    make it fail and then assert on what still happened.
    """
    agent = MagicMock()
    agent.neon_project_id = "neon-project-1"
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: PRODUCTION)

    scenario_rows = [
        ("11111111-1111-1111-1111-111111111111", "generated", "Q1", "A1", []),
    ]
    cursor = _Cursor(scenario_rows)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    # Mining is best-effort and irrelevant here.
    monkeypatch.setattr(mod, "mine_production_scenarios", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "store_scenarios", lambda *a, **kw: None)

    rec: dict = {
        "config_built": [],
        "inserted": [],
        "ragas": [],
        "results": [],
        "status": [],
        "deleted": [],
    }

    monkeypatch.setattr(
        mod,
        "build_eval_run_config",
        lambda agent_id, conn_str: (
            rec["config_built"].append(conn_str)
            or {"prompt_version_id": "pv-1", "config": {"model_id": "m"}}
        ),
    )
    monkeypatch.setattr(
        mod,
        "insert_eval_run",
        lambda run_id, kind, pv, config, conn_str: (
            rec["inserted"].append((kind, pv, config, conn_str)) or True
        ),
    )
    monkeypatch.setattr(
        mod,
        "create_branch",
        lambda project_id, name: ("branch-1", BRANCH),
    )
    monkeypatch.setattr(mod, "wait_for_neon_ready", lambda conn_str: None)
    monkeypatch.setattr(
        mod,
        "run_ragas_eval",
        lambda scenarios, branch_conn_str: (
            rec["ragas"].append(branch_conn_str)
            or {"scores": [{"scenario_id": "s1"}], "means": {"faithfulness": 0.9}}
        ),
    )
    monkeypatch.setattr(
        mod,
        "write_eval_results",
        lambda run_id, scores, conn_str: rec["results"].append(conn_str),
    )
    monkeypatch.setattr(
        mod,
        "update_eval_run_status",
        lambda run_id, status, finished_at, conn_str: rec["status"].append(
            (status, conn_str)
        ),
    )
    monkeypatch.setattr(
        mod,
        "delete_branch",
        lambda project_id, branch_id: rec["deleted"].append((project_id, branch_id)),
    )

    return rec


def _run(agent_id="agent-1", retries=0):
    """Invoke the task body with an explicit retry count.

    Celery's `self.retry()` outside a worker re-raises the original exception
    rather than scheduling anything, so the failure-path tests below run at
    retries == max_retries: the task takes its `return {}` exhaustion branch and
    the assertions can be about what the `finally` did rather than about which
    exception escaped. `test_retry_path_still_deletes_the_branch` covers
    retries=0, where the exception does escape.
    """
    mod.run_eval_suite.push_request(retries=retries)
    try:
        return mod.run_eval_suite.run(agent_id)
    finally:
        mod.run_eval_suite.pop_request()


EXHAUSTED = mod.run_eval_suite.max_retries


# ---------------------------------------------------------------------------
# D2 — the persistence split
# ---------------------------------------------------------------------------


class TestPersistenceSplit:

    def test_results_go_to_production_and_scoring_goes_to_the_branch(self, wired):
        result = _run()

        assert wired["ragas"] == [BRANCH], "scoring must run against the branch (D-10)"
        assert wired["results"] == [PRODUCTION], (
            "eval_results were written to the Neon branch this task deletes in "
            "`finally` — that is audit defect D2"
        )
        assert result["run_id"]

    def test_terminal_status_lands_on_production(self, wired):
        _run()

        assert ("complete", PRODUCTION) in wired["status"], (
            "a run must reach a terminal state on PRODUCTION or it never "
            "happened — a branch-only 'complete' leaves production at 'running'"
        )
        assert all(conn == PRODUCTION for _, conn in wired["status"]), (
            f"a status write targeted a non-production connection: {wired['status']}"
        )

    def test_eval_run_row_is_inserted_on_production_with_its_config(self, wired):
        result = _run()

        assert len(wired["inserted"]) == 1
        kind, prompt_version_id, config, conn_str = wired["inserted"][0]
        assert kind == "m6:agent-1", "kind is the per-agent idempotency key"
        assert prompt_version_id == "pv-1"
        assert config == {"model_id": "m"}
        assert conn_str == PRODUCTION
        assert result["config_recorded"] is True

    def test_config_is_collected_against_production_not_the_branch(self, wired):
        """The corpus figure must describe the live corpus, and the branch does
        not exist yet at collection time."""
        _run()
        assert wired["config_built"] == [PRODUCTION]

    def test_unattributed_run_is_reported_as_unattributed(self, wired, monkeypatch):
        """A tenant DB behind migration 0013 still runs, and says so."""
        monkeypatch.setattr(
            mod, "insert_eval_run", lambda *a, **kw: False
        )
        result = _run()
        assert result["config_recorded"] is False


# ---------------------------------------------------------------------------
# D-10 — the branch is deleted on every path
# ---------------------------------------------------------------------------


class TestBranchDeletion:

    def test_branch_is_deleted_on_success(self, wired):
        _run()
        assert wired["deleted"] == [("neon-project-1", "branch-1")]

    def test_branch_is_deleted_when_scoring_raises(self, wired, monkeypatch):
        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ragas exploded")),
        )
        assert _run(retries=EXHAUSTED) == {}
        assert wired["deleted"] == [("neon-project-1", "branch-1")], (
            "the Neon branch leaked on the failure path — D-10 requires deletion "
            "on every path, and a leaked branch is a live copy of tenant data"
        )
        assert ("failed", PRODUCTION) in wired["status"]

    def test_retry_path_still_deletes_the_branch(self, wired, monkeypatch):
        """retries < max_retries: `self.retry` propagates out of the task and the
        `finally` is the only thing standing between that and a leaked branch."""
        from celery.exceptions import Retry

        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ragas exploded")),
        )
        with pytest.raises((Retry, RuntimeError)):
            _run(retries=0)

        assert wired["deleted"] == [("neon-project-1", "branch-1")]

    def test_branch_is_deleted_when_the_production_result_write_raises(
        self, wired, monkeypatch
    ):
        """The new production write is inside the try, so it must not be able to
        strand a branch."""
        def _boom(run_id, scores, conn_str):
            raise RuntimeError("production unreachable")

        monkeypatch.setattr(mod, "write_eval_results", _boom)
        assert _run(retries=EXHAUSTED) == {}
        assert wired["deleted"] == [("neon-project-1", "branch-1")]

    def test_branch_is_deleted_even_if_marking_failed_also_raises(
        self, wired, monkeypatch
    ):
        """Two failures at once — scoring AND the terminal status write. The
        branch must still go, and the task must still return rather than dying
        on the second exception."""
        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ragas exploded")),
        )

        def _status_boom(*args, **kwargs):
            raise RuntimeError("production unreachable")

        monkeypatch.setattr(mod, "update_eval_run_status", _status_boom)

        result = _run(retries=EXHAUSTED)

        assert wired["deleted"] == [("neon-project-1", "branch-1")]
        assert result == {}, (
            "a failure in the terminal-status write must not escape the except "
            "block — it would skip the retry/exhaustion branch entirely"
        )

    def test_branch_creation_failure_marks_failed_on_production(
        self, wired, monkeypatch
    ):
        """The one failure mode that fires before the branch exists — it was
        already writing to production, and must keep doing so."""
        monkeypatch.setattr(
            mod,
            "create_branch",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("neon down")),
        )
        result = _run(retries=EXHAUSTED)

        assert ("failed", PRODUCTION) in wired["status"]
        assert wired["deleted"] == [], "no branch was created, so none is deleted"
        assert result == {}


# ---------------------------------------------------------------------------
# D5 / the trust hierarchy — promotion is not reachable from this task
# ---------------------------------------------------------------------------


class TestPromotionIsUnreachableFromTheTask:

    def test_task_never_promotes(self, wired):
        result = _run()
        assert result["promoted"] == 0
        assert result["promotion_disabled_reason"]

    def test_module_does_not_import_or_call_promote_to_verified_qa(self):
        """Absence pin. The promotion call site is what turns a repaired
        write-back into a path that serves an operator-flagged failure to real
        customers via retrieval_service.verified_qa_lookup, so its absence from
        this module is a property worth pinning rather than assuming."""
        source = inspect.getsource(mod)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        # The docstring names it in prose; only executable references matter.
        body = code.split('"""', 2)[-1]
        assert "promote_to_verified_qa(" not in body, (
            "run_eval_suite calls promote_to_verified_qa — with results now "
            "durable, that path can reach the customer-serving verified_qa cache"
        )

    def test_no_write_targets_the_branch_except_scoring(self, wired):
        """Every recorded connection string, in one assertion."""
        _run()

        branch_writes = [c for c in wired["results"] if c == BRANCH]
        branch_status = [s for s in wired["status"] if s[1] == BRANCH]
        branch_inserts = [i for i in wired["inserted"] if i[3] == BRANCH]

        assert branch_writes == []
        assert branch_status == []
        assert branch_inserts == []


# ---------------------------------------------------------------------------
# Pre-existing task contracts that must survive the rewiring
# ---------------------------------------------------------------------------


class TestTaskContract:

    def test_acks_late_and_queue(self):
        assert mod.run_eval_suite.acks_late is True
        assert mod.run_eval_suite.max_retries == 2
        assert 'queue="runtime"' in inspect.getsource(mod)

    def test_signature_takes_no_conn_str(self):
        """CTL-08: tasks receive agent_id and decrypt at runtime."""
        params = set(inspect.signature(mod.run_eval_suite.run).parameters)
        assert "conn_str" not in params
        assert "branch_conn_str" not in params
        assert "agent_id" in params

    def test_idempotent_skip_when_a_run_is_already_going(self, wired, monkeypatch):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = ("existing-run-id",)
        conn.cursor.return_value = cursor
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        assert _run() == {"status": "already_running"}
        assert wired["inserted"] == []
        assert wired["deleted"] == []

    def test_no_scenarios_returns_before_creating_a_branch(self, wired, monkeypatch):
        conn = MagicMock()
        cursor = _Cursor([])
        conn.cursor.return_value = cursor
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        assert _run() == {"status": "no_scenarios"}
        assert wired["deleted"] == [], "no branch should have been created"
