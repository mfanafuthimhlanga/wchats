"""
Unit tests for app.worker.tasks.runtime.eval.run_eval_suite (measurement-layer P1).

The task had no unit coverage at all, which is how audit defect D2 survived: the
task wrote `eval_results`, the terminal `eval_runs` status and the verified_qa
promotion somewhere other than production, so a successful run was
indistinguishable from a hung one and `eval_results` never existed on production
for evals.py's LEFT JOIN to find.

These tests assert on WHICH connection string each write opens. That is the only
observable difference between the correct and the broken version — both write
the same SQL, to the same table names, and both "succeed". A test that mocked
eval_service wholesale and asserted "write_eval_results was called" would have
passed against the defect.

No live PostgreSQL exists on this machine, so every DB boundary is a double:
psycopg2.connect, the control-DB session and eval_service's writers. Nothing
here proves a live database accepts the SQL — that is integration territory and
it SKIPS, which is unobserved, never a pass.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

import psycopg2
import pytest

from app.worker.tasks.runtime import eval as mod

PRODUCTION = "postgresql://production/tenant"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _Cursor:
    """Cursor double serving the task's raw psycopg2 reads.

    Three statements now, not two: the idempotency check, then the GOLDEN
    selector and the EXPLORATORY selector (P2 — the golden rows run in full
    every night, the exploratory ones rotate). The double dispatches on the SQL
    text rather than on call order so a test can assert which query produced
    which rows, and so reordering the two selectors cannot silently swap the
    datasets under a passing test.

    `dataset_column_missing=True` makes the two dataset-aware selectors raise
    UndefinedColumn, standing in for a tenant DB that predates migration 0014.
    """

    def __init__(
        self,
        golden_rows=(),
        exploratory_rows=(),
        legacy_rows=(),
        dataset_column_missing=False,
    ):
        self.golden_rows = list(golden_rows)
        self.exploratory_rows = list(exploratory_rows)
        self.legacy_rows = list(legacy_rows)
        self.dataset_column_missing = dataset_column_missing
        self.executed: list[str] = []
        self._last: list = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "FROM eval_scenarios" not in sql:
            self._last = []
            return
        if "dataset" in sql:
            if self.dataset_column_missing:
                raise psycopg2.errors.UndefinedColumn(
                    'column "dataset" does not exist'
                )
            self._last = (
                self.golden_rows
                if "dataset = %(golden)s" in sql
                else self.exploratory_rows
            )
        else:
            self._last = self.legacy_rows

    def fetchone(self):
        return None  # no recent 'running' eval run -> no idempotent skip

    def fetchall(self):
        return self._last

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

    # Two golden rows and two exploratory rows, so every test in this module
    # exercises the two-query selector rather than only the sampled half.
    #
    # FOUR, NOT TWO, since the P2 review: eval_service.MIN_SCORED_OBSERVATIONS is
    # the MIN_PAIRS-analogue absolute floor under a measurement, and a two-row run
    # is BELOW it — correctly, because two observations do not certify a deploy.
    # A fixture that stays under the floor would make every test in this module a
    # test of the fail-closed branch, which is not what any of them are about.
    golden_rows = [
        ("g0000000-0000-0000-0000-000000000001", "generated", "GQ1", "GA1", [], "golden"),
        ("g0000000-0000-0000-0000-000000000002", "generated", "GQ2", "GA2", [], "golden"),
    ]
    exploratory_rows = [
        ("11111111-1111-1111-1111-111111111111", "generated", "Q1", "A1", [], None),
        ("22222222-2222-2222-2222-222222222222", "generated", "Q2", "A2", [], None),
    ]
    cursor = _Cursor(
        golden_rows=golden_rows,
        exploratory_rows=exploratory_rows,
        legacy_rows=[
            ("11111111-1111-1111-1111-111111111111", "generated", "Q1", "A1", [], None),
        ],
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    # Mining is best-effort and irrelevant here.
    monkeypatch.setattr(mod, "mine_production_scenarios", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "store_scenarios", lambda *a, **kw: None)

    rec: dict = {
        "config_built": [],
        "composition": [],
        "cursor": cursor,
        "inserted": [],
        "invoked": [],
        "config_patched": [],
        "ragas": [],
        "results": [],
        "status": [],
    }

    # D1/P2: the agent invocation is doubled here so these tests keep testing
    # what they were written to test — which connection string each write opens
    # — rather than accidentally exercising a live SDK
    # turn against a MagicMock agent row. The scenarios that come back carry an
    # `agent_response` that is deliberately NOT the reference answer, so any
    # test in this module that starts scoring self-answers fails loudly.
    # tests/unit/test_eval_agent_invocation.py drives the real helper.
    def _fake_invoke(*, agent_id, conn_str, scenarios, prompt_version_id):
        rec["invoked"].append(
            {
                "agent_id": agent_id,
                "conn_str": conn_str,
                "scenario_ids": [s["id"] for s in scenarios],
                "prompt_version_id": prompt_version_id,
            }
        )
        rows = [
            {
                **s,
                "agent_response": f"AGENT SAID: {s['question']}",
                "retrieved_contexts": [f"CTX for {s['id']}"],
            }
            for s in scenarios
        ]
        # Built by the real summariser so the fixture can never hand the task a
        # shape the production summariser does not produce.
        summary = mod.summarise_agent_invocation(
            [
                {
                    "scenario_id": s["id"],
                    "responded": True,
                    "scorable": True,
                    "error": None,
                    "retrieve_calls": 1,
                    "retrieve_at_cap": False,
                    "retrieve_unparsed": 0,
                    "retrieved_chunks": 1,
                    "side_effects": [],
                }
                for s in scenarios
            ],
            valid=len(scenarios),
            ceiling_skipped=0,
            ceiling_skipped_golden=0,
            per_turn_timeout_s=90,
            audit_capture_char_cap=1800,
            retrieved_context_chunk_char_cap=2000,
            pii_firewall_applied=False,
        )
        return rows, summary

    monkeypatch.setattr(mod, "_invoke_agent_for_scenarios", _fake_invoke)
    monkeypatch.setattr(
        mod,
        "update_eval_run_config",
        lambda run_id, patch, conn_str: (
            rec["config_patched"].append((run_id, patch, conn_str)) or True
        ),
    )

    monkeypatch.setattr(
        mod,
        "build_eval_run_config",
        lambda agent_id, conn_str, dataset=None: (
            rec["config_built"].append(conn_str)
            or rec["composition"].append(dataset)
            or {
                "prompt_version_id": "pv-1",
                "config": {"model_id": "m", "dataset": dataset},
            }
        ),
    )
    monkeypatch.setattr(
        mod,
        "insert_eval_run",
        lambda run_id, kind, pv, config, conn_str: (
            rec["inserted"].append((kind, pv, config, conn_str)) or True
        ),
    )
    def _fake_ragas(*args, **kwargs):
        # Recorded as (args, kwargs) rather than as a named connection string:
        # the property under test is that scoring is handed NO connection at
        # all, and that cannot be expressed by a signature that names one.
        rec["ragas"].append((args, kwargs))
        return {"scores": [{"scenario_id": "s1"}], "means": {"faithfulness": 0.9}}

    monkeypatch.setattr(mod, "run_ragas_eval", _fake_ragas)
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
    return rec


def _run(agent_id="agent-1", retries=0):
    """Invoke the task body with an explicit retry count.

    Celery's `self.retry()` outside a worker re-raises the original exception
    rather than scheduling anything, so the failure-path tests below run at
    retries == max_retries: the task takes its `return {}` exhaustion branch and
    the assertions can be about what the task recorded rather than about which
    exception escaped.
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

    def test_results_go_to_production_and_scoring_opens_nothing(self, wired):
        result = _run()

        assert wired["results"] == [PRODUCTION], (
            "eval_results were not written to production — that is audit "
            "defect D2"
        )
        assert len(wired["ragas"]) == 1
        args, kwargs = wired["ragas"][0]
        assert len(args) == 1 and kwargs == {}, (
            "scoring was handed something besides the scenarios — the only "
            "thing it ever needed, and the argument it used to be given and "
            "never read was a connection string"
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
        assert config["model_id"] == "m"
        assert conn_str == PRODUCTION
        assert result["config_recorded"] is True

        # P2: the run records WHICH rows it covered. A golden score that moved
        # and a golden SET that moved are indistinguishable after the fact
        # unless the composition was stamped on the run.
        composition = config["dataset"]
        assert composition["golden"]["attempted"] == 2
        assert composition["exploratory"]["attempted"] == 2
        assert composition["golden_set_present"] is True
        assert composition["dataset_column_available"] is True

    def test_config_is_collected_against_production(self, wired):
        """The corpus figure must describe the live corpus."""
        _run()
        assert wired["config_built"] == [PRODUCTION]

    def test_unattributed_run_is_reported_as_unattributed(self, wired, monkeypatch):
        """A tenant DB behind migration 0013 still runs, and says so."""
        monkeypatch.setattr(
            mod, "insert_eval_run", lambda *a, **kw: False
        )
        result = _run()
        assert result["config_recorded"] is False




class TestRetryAfterTheInvocation:

    def test_a_failure_after_the_invocation_does_not_re_buy_sixty_sdk_turns(
        self, wired, monkeypatch
    ):
        """A judge outage must not re-run the agent (D1/P2 review).

        `max_retries=2` meant a raise anywhere after the invocation re-entered
        the task body, drew a fresh run_id and put every scenario to the agent
        again — up to three times the ceiling the run stamps on itself as
        `max_wall_clock_s`, which no field on the run expressed. Losing one
        night's scores is cheaper by orders of magnitude, and tonight's beat
        repeats tomorrow.

        retries=0, so a retrying path WOULD raise Retry here. It must not.
        """
        from celery.exceptions import Retry

        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("judge outage")),
        )

        try:
            result = _run(retries=0)
        except Retry:  # pragma: no cover - the assertion below is the message
            pytest.fail(
                "the task retried after the agent had already been invoked, so "
                "one judge outage buys a second full set of live SDK turns"
            )

        assert result == {}
        assert ("failed", PRODUCTION) in wired["status"], (
            "the run must still reach a terminal state on production — not "
            "retrying is not the same as not finishing"
        )
        assert len(wired["invoked"]) == 1, (
            f"the agent was invoked {len(wired['invoked'])} times for one dispatch"
        )


# ---------------------------------------------------------------------------
# D5 / the trust hierarchy — promotion is not reachable from this task
# ---------------------------------------------------------------------------


class TestPromotionIsUnreachableFromTheTask:

    def test_task_never_promotes(self, wired):
        result = _run()
        assert result["promoted"] == 0
        assert result["promotion_disabled_reason"]


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

    def test_no_scenarios_returns_early(self, wired, monkeypatch):
        conn = MagicMock()
        cursor = _Cursor()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        result = _run()
        assert result["status"] == "no_scenarios"
        # The denominators travel even here: a run that scored nothing must say
        # so with numbers, not with absent keys a caller has to interpret.
        assert result["attempted"] == 0
        assert result["valid"] == 0
        assert result["scored"] == 0

    def test_an_empty_run_is_still_recorded_terminally(self, wired, monkeypatch):
        """A run that covered nothing still happened (P2 review).

        This path used to write nothing at all, so production held no eval_runs
        row and the deploy gate reported EVAL_SIGNAL_NO_RUNS — the same signal
        as an agent nobody has ever tried to evaluate. Two consequences: the
        owner is told "quality has never been measured" when the truth is "this
        tenant has no scenarios", and run_deployment_checklist's day-1 remedy
        re-fires on every readiness check because the state it keys off never
        changes. A completed run that scored nothing still blocks — honestly —
        and converges.
        """
        conn = MagicMock()
        conn.cursor.return_value = _Cursor()
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        result = _run()

        assert result["run_recorded"] is True
        assert result["run_id"]
        assert len(wired["inserted"]) == 1, (
            "the empty run left no eval_runs row, so nothing on production "
            "explains why this agent's deploy is blocked"
        )
        kind, _pv, config, conn_str = wired["inserted"][0]
        assert kind == "m6:agent-1"
        assert conn_str == PRODUCTION
        assert config["dataset"]["attempted"] == 0, (
            "the run must record that it covered nothing, not omit the claim"
        )
        assert ("complete", PRODUCTION) in wired["status"], (
            "an empty run left at 'running' is indistinguishable from a hung one"
        )

    def test_a_failure_to_record_the_empty_run_does_not_raise(
        self, wired, monkeypatch
    ):
        """Best-effort. Nothing to evaluate must not become a retry storm."""
        conn = MagicMock()
        conn.cursor.return_value = _Cursor()
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)
        monkeypatch.setattr(
            mod,
            "insert_eval_run",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("production down")),
        )

        result = _run()

        assert result["status"] == "no_scenarios"
        assert result["run_recorded"] is False
        assert result["run_id"] is None


# ---------------------------------------------------------------------------
# P2 — the golden set is held FIXED, and the rest rotates
# ---------------------------------------------------------------------------


class TestGoldenSetIsHeldFixed:
    """The selector's whole purpose is that one half of it does not move.

    `ORDER BY RANDOM() LIMIT 30` drew a different sample every night, so
    run-to-run variance was dominated by the draw: a five-point regression at
    n=30 is invisible inside sampling noise unpaired, and obvious paired on the
    same items. These tests assert the two halves are selected by DIFFERENT
    queries with different bounds, because "we ran the golden rows" is only true
    if none of them can be sampled away.
    """

    def _scenario_queries(self, rec) -> list[str]:
        return [sql for sql in rec["cursor"].executed if "FROM eval_scenarios" in sql]

    def test_golden_rows_are_selected_unsampled_and_exploratory_ones_are_not(
        self, wired
    ):
        _run()
        golden_sql, exploratory_sql = self._scenario_queries(wired)[:2]

        assert "dataset = %(golden)s" in golden_sql
        assert "LIMIT" not in golden_sql.upper(), (
            "the golden set must run in FULL — a LIMIT on it means some golden "
            "rows are sampled away and the paired comparison is broken"
        )
        assert "ORDER BY RANDOM()" not in golden_sql.upper(), (
            "a randomly ordered golden set is a sampled golden set as soon as "
            "anyone adds a bound to it"
        )

        assert "dataset IS NULL OR dataset <> %(golden)s" in exploratory_sql
        assert "ORDER BY RANDOM()" in exploratory_sql
        assert "LIMIT %(limit)s" in exploratory_sql

    def test_both_halves_keep_the_empty_label_exclusion(self, wired):
        """An unlabelled row stays inert to the selector on BOTH paths.

        bench.promote_trace_to_scenario stores a filed failing trace with an
        empty reference_answer precisely so it cannot be scored against a label
        it does not have. A new query that forgot the filter would reintroduce
        that by the back door on one half only, which is the version nobody
        notices.
        """
        _run()
        queries = self._scenario_queries(wired)
        assert len(queries) >= 2
        for sql in queries:
            assert "reference_answer != ''" in sql

    def test_the_same_golden_rows_appear_in_every_run(self, wired):
        """Repeated invocations cover the identical golden set.

        The property the paired comparison needs, asserted over repetition
        rather than over one call: the golden half of the composition is the
        same on run 1, 2 and 3, which is exactly what the old
        `ORDER BY RANDOM() LIMIT 30` could not promise.
        """
        goldens = []
        for _ in range(3):
            _run()
            goldens.append(wired["composition"][-1]["golden"])

        assert goldens[0]["attempted"] == 2
        assert goldens == [goldens[0]] * 3, (
            f"the golden set moved between runs: {goldens}"
        )

    def test_the_run_reports_the_two_datasets_separately(self, wired):
        result = _run()

        assert set(result["datasets"]) == {"golden", "exploratory"}
        assert result["datasets"]["golden"]["valid"] == 2
        assert result["datasets"]["exploratory"]["valid"] == 2
        assert result["golden_set_present"] is True

    def test_a_tenant_without_the_dataset_column_degrades_and_says_so(
        self, wired, monkeypatch
    ):
        """Pre-0014 tenants keep evaluating; they just have no fixed set.

        'This tenant designated no golden rows' and 'this tenant cannot be
        asked' are different claims. Collapsing them would report a tenant one
        migration behind as a tenant that had curated nothing.
        """
        conn = MagicMock()
        cursor = _Cursor(
            legacy_rows=[
                (
                    "11111111-1111-1111-1111-111111111111",
                    "generated",
                    "Q1",
                    "A1",
                    [],
                    None,
                ),
            ],
            dataset_column_missing=True,
        )
        conn.cursor.return_value = cursor
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        result = _run()

        assert result["dataset_column_available"] is False
        assert result["golden_set_present"] is False
        assert result["attempted"] == 1
        assert result["datasets"]["exploratory"]["attempted"] == 1
        assert result["datasets"]["golden"]["attempted"] == 0


# ---------------------------------------------------------------------------
# P2 — validity denominators
# ---------------------------------------------------------------------------


class TestValidityDenominators:
    """(attempted, valid, scored) are three different claims.

    The rule is .dev/retro.md Family B's, and the failure it prevents is a rate
    reported over a denominator nobody stated: a run that fetched 2 rows and
    scored 1 is half-measured, and all three numbers are needed to see that.
    """

    def test_the_run_reports_all_three_counts(self, wired):
        result = _run()

        assert result["attempted"] == 4, "four rows were fetched"
        assert result["valid"] == 4, "all four carried a label"
        assert result["scored"] == 0, (
            "the doubled scorer returns a score row for a scenario_id that is "
            "not in the fetched set, so nothing is attributable"
        )

    def test_scored_is_below_valid_when_ragas_returns_fewer_rows(
        self, wired, monkeypatch
    ):
        """Ragas returning fewer rows than it was given must be visible.

        A judge outage or a parse failure drops rows silently. Reporting only
        the submitted count would then claim a measurement of two over an
        observation of one.
        """
        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda scenarios: {
                "scores": [
                    {
                        "scenario_id": "g0000000-0000-0000-0000-000000000001",
                        "faithfulness": 0.9,
                        "answer_relevancy": 0.9,
                        "context_precision": None,
                        "context_recall": None,
                    }
                ],
                "means": {},
            },
        )

        result = _run()

        assert result["valid"] == 4
        assert result["scored"] == 1, "one of the four valid rows produced a score"
        assert result["datasets"]["golden"]["scored"] == 1
        assert result["datasets"]["exploratory"]["scored"] == 0

    def test_zero_valid_scenarios_reports_unknown_never_a_pass_rate(
        self, wired, monkeypatch
    ):
        """A metric over zero observations is 'unknown', never 'pass'.

        The failing input: every judge call returns NaN, run_ragas_eval emits
        None for all four metrics, and the run completes. Rendered as 0.0 that
        reads as a total quality collapse; omitted, it reads as fine. Both are
        wrong, and `measured: False` with `observations: 0` is the only honest
        third answer.
        """
        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda scenarios: {
                "scores": [
                    {
                        "scenario_id": s["id"],
                        "faithfulness": None,
                        "answer_relevancy": None,
                        "context_precision": None,
                        "context_recall": None,
                    }
                    for s in scenarios
                ],
                "means": {},
            },
        )

        result = _run()

        assert result["valid"] == 4
        assert result["scored"] == 0
        for name in ("golden", "exploratory"):
            for metric in result["datasets"][name]["metrics"].values():
                assert metric == {
                    "value": None,
                    "measured": False,
                    "observations": 0,
                }, f"{name} reported a value for a metric nothing observed"
