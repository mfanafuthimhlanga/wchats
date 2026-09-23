"""`rejudge_eval_run`: a second opinion on a finished run, never a rewrite of it (#274).

#274 replaced the instrument behind the gated relevancy metric. The owner's 46
labels describe the old one, and the only way to measure the new Judge against
them is to put it in front of the same stored answers. Three properties make that
honest, and this module is what holds them:

  - a NEW run row, carrying `source_run_id`. The source run's `eval_results` and
    `eval_samples` are never written, because destroying the measurement the
    labels were taken against is the one thing a calibration cannot survive;
  - the samples are COPIED onto the new run, so `calibrate_run.py --score <new>`
    finds text to join the labels to;
  - idempotent on the pair (source run, Judge identity). A redelivered message
    under `acks_late=True` re-runs the body, and a rejudge that paid again would
    charge a judge call per row for a measurement it already has.

The service functions are doubled at the task's own imports. The SQL they run is
tested one layer down, against a recording cursor, in the last class here.
"""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import MagicMock

import psycopg2
import pytest

from app.domain.judge_identity import JudgeIdentity
from app.services import eval_service
from app.worker.tasks.runtime import rejudge as mod

AGENT_ID = "22222222-2222-2222-2222-222222222222"
TENANT_ID = "11111111-1111-1111-1111-111111111111"
SOURCE_RUN = "aaaaaaaa-0000-4000-8000-000000000001"
PRODUCTION = "postgresql://production"

IDENTITY = JudgeIdentity(
    model="gpt-5.6-luna", reasoning_effort="none", prompt_version="relevance-judge-v1"
)

#: What `rejudge_instrument` produces: every Judge the rejudge pays for and every
#: gate it writes against, as the whole block the idempotency check compares.
INSTRUMENT = {
    "judge_identities": {
        "faithfulness": {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "none",
            "prompt_version": "ragas-0.4.3",
        },
        "answer_relevancy": dataclasses.asdict(IDENTITY),
    },
    "thresholds": {"faithfulness": 0.80, "answer_relevancy": 0.90},
}


def _sample(sid: str, **extra) -> dict:
    return {
        "id": sid,
        "dataset": "golden",
        "question": f"q-{sid}",
        "agent_response": f"a-{sid}",
        "retrieved_contexts": ["c"],
        "reference_answer": f"r-{sid}",
        "turns": [],
        "resolved_question": None,
        **extra,
    }


def _make_sync_db_context(db):
    class _Ctx:
        def __enter__(self):
            return db

        def __exit__(self, *args):
            return False

    return lambda: _Ctx()


@pytest.fixture
def wired(monkeypatch):
    """The task with every boundary doubled and every call recorded."""
    agent = MagicMock()
    agent.tenant_id = TENANT_ID
    agent.neon_connection_string = b"encrypted"
    db = MagicMock()
    db.get.return_value = agent

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: PRODUCTION)
    monkeypatch.setattr(mod, "ledger_recorder", lambda _dsn: (lambda _call: None))
    monkeypatch.setattr(mod, "rejudge_instrument", lambda: INSTRUMENT)

    rec: dict = {
        "existing": None,
        "samples": [_sample("s1"), _sample("s2")],
        "inserted": [],
        "samples_written": [],
        "results_written": [],
        "status": [],
        "scored": [],
    }

    monkeypatch.setattr(
        mod, "existing_rejudge_run", lambda *a, **kw: rec["existing"]
    )
    monkeypatch.setattr(mod, "read_eval_samples", lambda *a, **kw: rec["samples"])
    monkeypatch.setattr(
        mod,
        "insert_rejudge_run",
        lambda run_id, agent_id, source_run_id, config, conn_str: rec["inserted"].append(
            (run_id, agent_id, source_run_id, config, conn_str)
        ),
    )
    monkeypatch.setattr(
        mod,
        "write_eval_samples",
        lambda run_id, scenarios, conn_str: rec["samples_written"].append(
            (run_id, list(scenarios), conn_str)
        )
        or len(scenarios),
    )
    monkeypatch.setattr(
        mod,
        "write_eval_results",
        lambda run_id, records, conn_str: rec["results_written"].append(
            (run_id, list(records), conn_str)
        ),
    )
    monkeypatch.setattr(
        mod,
        "update_eval_run_status",
        lambda run_id, status, finished_at, conn_str: rec["status"].append(
            (run_id, status)
        ),
    )

    def _score(scenarios, ledger, metric_keys=None):
        rec["scored"].append({"scenarios": list(scenarios), "metric_keys": metric_keys})
        return {
            "scores": [{"scenario_id": s["id"], "faithfulness": 0.9} for s in scenarios],
            "judge_records": eval_service.build_judge_records(
                [{"scenario_id": s["id"], "faithfulness": 0.9} for s in scenarios]
            ),
            "sent": len(scenarios),
            "returned": len(scenarios),
            "unattributed": 0,
        }

    monkeypatch.setattr(mod, "run_ragas_eval", _score)
    return rec


def _run() -> dict:
    return mod.rejudge_eval_run.apply(
        kwargs={"agent_id": AGENT_ID, "source_run_id": SOURCE_RUN}
    ).get()


# ---------------------------------------------------------------------------
# The new run
# ---------------------------------------------------------------------------


class TestTheRejudgeWritesItsOwnRun:
    def test_a_new_run_is_written_naming_the_run_it_rescored(self, wired):
        result = _run()

        assert result["status"] == "complete"
        [(run_id, agent_id, source_run_id, config, conn_str)] = wired["inserted"]
        assert run_id == result["run_id"]
        assert run_id != SOURCE_RUN
        assert source_run_id == SOURCE_RUN
        assert agent_id == AGENT_ID
        assert conn_str == PRODUCTION
        assert config["rejudge"]["instrument"] == INSTRUMENT

    def test_the_rejudge_never_writes_into_the_source_run(self, wired):
        """The mutation this test exists for: write to `source_run_id` and the
        labels lose the verdicts they were taken against.

        Every writer takes a run id. All of them must take the NEW one.
        """
        result = _run()

        written_to = (
            [row[0] for row in wired["samples_written"]]
            + [row[0] for row in wired["results_written"]]
            + [row[0] for row in wired["status"]]
        )
        assert written_to, "the run wrote nothing at all"
        assert set(written_to) == {result["run_id"]}
        assert SOURCE_RUN not in written_to, (
            "a writer was pointed at the source run, which overwrites the "
            "measurement the owner's labels describe"
        )

    def test_the_samples_are_copied_so_the_new_run_can_be_labelled(self, wired):
        result = _run()

        [(run_id, scenarios, _dsn)] = wired["samples_written"]
        assert run_id == result["run_id"]
        assert [s["id"] for s in scenarios] == ["s1", "s2"], (
            "the scenario ids must survive the copy, or the labels join nothing"
        )

    def test_only_the_gated_metrics_are_paid_for(self, wired):
        _run()

        [call] = wired["scored"]
        assert call["metric_keys"] == eval_service.REJUDGE_METRIC_KEYS
        assert set(eval_service.GATED_METRIC_KEYS) <= set(call["metric_keys"]), (
            "a rejudge pays for every metric a deploy reads; relevancy rides beside "
            "them reported, not gated (ADR 0014)"
        )

    def test_no_agent_turn_and_no_rewrite_happen(self, wired):
        """The answers under judgement are the ones the labels describe.

        A fresh turn would answer differently and a fresh rewrite would change
        the question, and the labels would then be about neither.
        """
        _run()

        [call] = wired["scored"]
        assert [s["agent_response"] for s in call["scenarios"]] == ["a-s1", "a-s2"]

    def test_a_row_the_rule_decided_is_copied_and_never_judged(self, wired):
        """An ambiguous scenario's verdict is a rule over the response (ADR 0012).

        Re-running it would spend nothing and change nothing, so it is copied
        forward and kept out of the judged set.
        """
        wired["samples"] = [_sample("s1"), _sample("s2", clarifying_check=True)]

        _run()

        [(_run_id, copied, _dsn)] = wired["samples_written"]
        [call] = wired["scored"]
        assert [s["id"] for s in copied] == ["s1", "s2"], "a checked row was dropped"
        assert [s["id"] for s in call["scenarios"]] == ["s1"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestASecondRejudgeCostsNothing:
    def test_a_second_rejudge_returns_the_first(self, wired):
        """The mutation this test exists for: drop the check and every retry pays.

        `acks_late=True` means a redelivered message re-runs the body. Without
        the guard a broker redelivery buys a judge call per stored row.
        """
        wired["existing"] = "bbbbbbbb-0000-4000-8000-000000000002"

        result = _run()

        assert result == {
            "status": "already_rejudged",
            "run_id": "bbbbbbbb-0000-4000-8000-000000000002",
            "source_run_id": SOURCE_RUN,
        }
        assert wired["inserted"] == [], "a second run row was written"
        assert wired["scored"] == [], "the Judge was paid for a second time"
        assert wired["results_written"] == []

    def test_a_guard_that_cannot_run_costs_money_and_never_a_wrong_row(
        self, wired, monkeypatch
    ):
        """Best effort, the same choice `run_eval_suite` makes for its own guard.

        What a failed check costs is a second set of judge calls. What it cannot
        cost is a corrupted row, because the second run writes its own id.
        """
        def _boom(*_a, **_kw):
            raise RuntimeError("tenant DB unreachable")

        monkeypatch.setattr(mod, "existing_rejudge_run", _boom)

        result = _run()

        assert result["status"] == "complete"
        assert result["run_id"] != SOURCE_RUN


# ---------------------------------------------------------------------------
# The absences
# ---------------------------------------------------------------------------


class TestWhatTheTaskRefusesToDo:
    def test_a_source_run_with_no_samples_writes_no_run(self, wired):
        """Nothing to rescore. Reported rather than retried."""
        wired["samples"] = []

        assert _run() == {"status": "no_samples", "source_run_id": SOURCE_RUN}
        assert wired["inserted"] == []

    def test_an_unconfigured_agent_returns_nothing(self, wired, monkeypatch):
        db = MagicMock()
        db.get.return_value = None
        monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(db))

        assert _run() == {}
        assert wired["inserted"] == []

    def test_the_task_args_carry_two_ids_and_no_connection_string(self):
        """Project rule 1, pinned on the signature."""
        import inspect

        params = inspect.signature(mod.rejudge_eval_run.run).parameters
        assert list(params) == ["agent_id", "source_run_id"]

    def test_the_task_is_acks_late_on_the_runtime_queue(self):
        """Project rule 2, and CLAUDE.md's two-queue rule."""
        assert mod.rejudge_eval_run.acks_late is True
        assert mod.rejudge_eval_run.queue == "runtime"


# ---------------------------------------------------------------------------
# The SQL, one layer down
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.executed: list[tuple[str, object]] = []
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _connect(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return lambda *a, **kw: conn


class TestTheRunRowAndTheLookup:
    def test_the_run_row_carries_the_source_and_the_instrument(self, monkeypatch):
        cursor = _RecordingCursor()
        monkeypatch.setattr(eval_service.psycopg2, "connect", _connect(cursor))

        eval_service.insert_rejudge_run(
            "cccccccc-0000-4000-8000-000000000003",
            AGENT_ID,
            SOURCE_RUN,
            eval_service.rejudge_config(SOURCE_RUN, INSTRUMENT),
            PRODUCTION,
        )

        [(sql, params)] = cursor.executed
        assert "source_run_id" in sql
        assert params["source_run_id"] == SOURCE_RUN
        assert params["kind"] == f"rejudge:{AGENT_ID}"
        assert json.loads(params["config"])["rejudge"]["instrument"] == INSTRUMENT

    def test_the_lookup_pairs_the_source_run_with_the_instrument(self, monkeypatch):
        cursor = _RecordingCursor(fetchone_result=("dddddddd-0000-4000-8000-000000000004",))
        monkeypatch.setattr(eval_service.psycopg2, "connect", _connect(cursor))

        found = eval_service.existing_rejudge_run(
            AGENT_ID, SOURCE_RUN, INSTRUMENT, PRODUCTION
        )

        assert found == "dddddddd-0000-4000-8000-000000000004"
        [(sql, params)] = cursor.executed
        assert "source_run_id" in sql and "instrument" in sql
        assert params["status"] == eval_service.EVAL_RUN_STATUS_COMPLETE, (
            "a failed rejudge must not be returned as one already paid for"
        )

    def test_an_instrument_that_cannot_name_itself_is_never_matched(self, monkeypatch):
        """No key, no comparison, so the caller pays rather than guesses."""
        cursor = _RecordingCursor(fetchone_result=("x",))
        monkeypatch.setattr(eval_service.psycopg2, "connect", _connect(cursor))

        assert (
            eval_service.existing_rejudge_run(AGENT_ID, SOURCE_RUN, None, PRODUCTION)
            is None
        )
        assert cursor.executed == [], "a query ran for an identity that does not exist"

    def test_reading_the_samples_returns_the_writers_own_keys(self, monkeypatch):
        """So one list feeds `write_eval_samples` and `run_ragas_eval` unchanged."""
        cursor = _RecordingCursor(
            fetchall_result=[
                ("s1", "golden", "q", "a", ["c"], "ref", [], None, None),
                ("s2", "exploratory", "q2", "a2", [], "ref2", [], "resolved", True),
            ]
        )
        monkeypatch.setattr(eval_service.psycopg2, "connect", _connect(cursor))

        rows = eval_service.read_eval_samples(SOURCE_RUN, PRODUCTION)

        assert rows[0]["id"] == "s1"
        assert rows[0]["question"] == "q"
        assert rows[0]["agent_response"] == "a"
        assert rows[0]["reference_answer"] == "ref"
        assert "clarifying_check" not in rows[0], (
            "a NULL check must be absent, or `split_checked_rows` keeps the row "
            "from the Judge"
        )
        assert rows[1]["resolved_question"] == "resolved"
        assert rows[1]["clarifying_check"] is True


# ---------------------------------------------------------------------------
# The idempotency key is the whole instrument (#274 adversarial review)
# ---------------------------------------------------------------------------


class TestTheKeyIsEveryJudgeAndEveryGate:
    """The key was the relevance Judge's identity alone, and that was too narrow.

    A rejudge also pays for ragas faithfulness, whose prompt moves with the
    installed distribution, and it stores a `threshold` on every row it writes.
    A gate move restates nothing already written down, which is what storing the
    threshold is for, but the next rejudge answers a different question, and a
    key that ignored it would hand back a run scored against the old number.
    """

    def test_the_key_names_the_grounding_rule_and_no_paid_judge(self):
        """A rejudge scores faithfulness alone, by the grounding rule, and pays for no Judge (ADR 0015).

        The key still names the instrument, so a rule version bump is a
        different instrument and the next rejudge scores again rather than
        returning a run the old rule wrote.
        """
        from app.domain.grounding import GROUNDING_IDENTITY

        instrument = eval_service.rejudge_instrument()

        assert tuple(eval_service.REJUDGE_METRIC_KEYS) == ("faithfulness",)
        assert instrument["judge_identities"] == {
            "faithfulness": dataclasses.asdict(GROUNDING_IDENTITY)
        }
        assert all(
            identity["model"].startswith("rule:")
            for identity in instrument["judge_identities"].values()
        ), "the rejudge key names a model Judge, which is a call the rejudge pays for"

    def test_the_key_carries_the_gate_each_dimension_writes_against(self):
        instrument = eval_service.rejudge_instrument()

        assert instrument["thresholds"] == {
            metric: eval_service.threshold_for(metric)
            for metric in eval_service.REJUDGE_METRIC_KEYS
        }
        assert instrument["thresholds"]["faithfulness"] == 0.80

    def test_a_threshold_move_alone_is_a_different_instrument(self, monkeypatch):
        """The mutation this test exists for: drop `thresholds` from the key.

        Nothing about the Judges changed. The gate did, so the verdicts the next
        rejudge writes are decided differently, and returning the earlier run
        would hand back a measurement against a number nobody uses any more.
        """
        before = eval_service.rejudge_instrument()
        monkeypatch.setattr(
            eval_service.settings, "EVAL_FAITHFULNESS_THRESHOLD", 0.70
        )
        after = eval_service.rejudge_instrument()

        assert before["judge_identities"] == after["judge_identities"]
        assert before != after, (
            "a gate move left the idempotency key unchanged, so the next rejudge "
            "would return a run scored against the old threshold"
        )

    def test_an_unnameable_judge_keys_nothing(self, monkeypatch):
        """No key, no match, so the caller pays rather than guessing."""
        monkeypatch.setattr(eval_service, "judge_identity_for", lambda _metric: None)

        assert eval_service.rejudge_instrument() is None

    def test_the_lookup_compares_the_whole_block(self, monkeypatch):
        cursor = _RecordingCursor(fetchone_result=("eeeeeeee-0000-4000-8000-000000000005",))
        monkeypatch.setattr(eval_service.psycopg2, "connect", _connect(cursor))
        instrument = eval_service.rejudge_instrument()

        found = eval_service.existing_rejudge_run(
            AGENT_ID, SOURCE_RUN, instrument, PRODUCTION
        )

        assert found == "eeeeeeee-0000-4000-8000-000000000005"
        [(sql, params)] = cursor.executed
        assert "'instrument'" in sql, "the whole block is compared, not one field"
        assert json.loads(params["instrument"]) == instrument

    def test_the_stored_config_holds_the_block_the_lookup_reads(self):
        instrument = eval_service.rejudge_instrument()

        config = eval_service.rejudge_config(SOURCE_RUN, instrument)

        assert config["rejudge"]["instrument"] == instrument
        assert config["rejudge"]["source_run_id"] == SOURCE_RUN


# ---------------------------------------------------------------------------
# The failure paths
# ---------------------------------------------------------------------------


class TestWhatHappensWhenTheWorkFails:
    def test_a_tenant_behind_0030_is_reported_and_never_retried(
        self, wired, monkeypatch
    ):
        """Three attempts would find the same missing column three times.

        `insert_rejudge_run` has no narrower rung on purpose: a rejudge written
        without `source_run_id` could not be joined to the labels it exists for
        and could not be found by the idempotency check, so every retry would pay
        for the judge calls again.
        """
        def _undefined(*_a, **_kw):
            raise psycopg2.errors.UndefinedColumn("source_run_id does not exist")

        monkeypatch.setattr(mod, "insert_rejudge_run", _undefined)

        assert _run() == {
            "status": "tenant_behind_0030",
            "source_run_id": SOURCE_RUN,
        }
        assert wired["scored"] == [], "the Judge was paid before the row was written"
        assert wired["samples_written"] == []

    def test_a_scoring_failure_lands_the_new_run_failed_and_propagates(
        self, wired, monkeypatch
    ):
        """The run row exists, so it must reach a terminal state or it never ended.

        The exception propagates because the task above owns the retry: swallowing
        it here would report a run that measured nothing as a run that finished.
        """
        def _boom(*_a, **_kw):
            raise RuntimeError("the judge went dark mid-run")

        monkeypatch.setattr(mod, "run_ragas_eval", _boom)

        with pytest.raises(RuntimeError, match="went dark"):
            mod._rejudge(
                AGENT_ID, SOURCE_RUN, tenant_id=TENANT_ID, conn_str=PRODUCTION
            )

        [(run_id, status)] = wired["status"]
        assert status == "failed"
        assert run_id != SOURCE_RUN, "the SOURCE run was marked failed"
        assert wired["results_written"] == [], "a failed run wrote judge rows"


# ---------------------------------------------------------------------------
# A rejudge stays out of every reader of the agent's current quality
# ---------------------------------------------------------------------------


class TestARejudgeIsNotTheAgentsCurrentReading:
    def test_the_rejudge_kind_is_scoped_to_the_agent_and_is_not_the_eval_kind(self):
        kind = eval_service.rejudge_kind(AGENT_ID)

        assert kind == f"rejudge:{AGENT_ID}"
        assert not kind.startswith("m6:")
        assert AGENT_ID in kind, (
            "two agents can share a tenant database, so the kind has to name one"
        )

    def test_every_run_reader_filters_a_kind_and_none_of_them_match_a_rejudge(self):
        """The mutation this test exists for: remove a `kind` filter.

        A rejudge measured no agent turn. Any reader of the agent's current
        quality that returned one would report a measurement of the past as a
        measurement of the present, which is what `_LATEST_RUN_SQL`'s own
        docstring refuses to do across runs.
        """
        from app.api.v1 import evals as evals_route
        from app.services import deployment_service

        readers = {
            "eval_service._LATEST_RUN_SQL": eval_service._LATEST_RUN_SQL,
            "evals._LIST_EVAL_RUNS_SQL": evals_route._LIST_EVAL_RUNS_SQL,
            "evals._LIST_EVAL_RUNS_PRE_0030_SQL": evals_route._LIST_EVAL_RUNS_PRE_0030_SQL,
            "evals._LIST_EVAL_RUNS_PRE_0022_SQL": evals_route._LIST_EVAL_RUNS_PRE_0022_SQL,
            "deployment._EVAL_RUN_SINCE_SQL": deployment_service._EVAL_RUN_SINCE_SQL,
            "deployment._EVAL_RUN_RUNNING_SINCE_SQL": deployment_service._EVAL_RUN_RUNNING_SINCE_SQL,
        }
        unfiltered = [
            name
            for name, sql in readers.items()
            if "kind = %s" not in sql and "kind = %(kind)s" not in sql
        ]

        assert unfiltered == [], (
            f"{unfiltered} read eval_runs with no kind filter, so a rejudge run "
            "is the newest row they return"
        )

    def test_the_default_listing_asks_for_the_eval_kind_and_rejudge_is_opt_in(self):
        from app.api.v1 import evals as evals_route

        assert evals_route._KIND_PREFIX["eval"] == "m6:"
        assert evals_route._KIND_PREFIX["rejudge"] == eval_service.REJUDGE_KIND_PREFIX
        assert evals_route.EVAL_RUN_KINDS == ("eval", "rejudge")
