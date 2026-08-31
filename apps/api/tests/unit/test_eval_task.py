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
here proves a live database accepts the SQL. That is integration territory, and
it SKIPS, which is unobserved, never a pass.
"""

from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import psycopg2
import pytest

from app.services.eval_service import build_judge_records
from app.worker.tasks.runtime import eval as mod

# The canned Judge outputs and the embedding stand-in are borrowed rather than
# copied, because a second set of canned verdicts would let this module and the
# eval_service tests disagree about what a Judge returns.
from tests.unit.test_eval_service import _CANNED_JUDGE_OUTPUTS, _FakeRagasEmbedding

PRODUCTION = "postgresql://production/tenant"
#: The tenant every ledger row this module produces is billed to. A real UUID,
#: because `ModelCall` and the ledger columns take UUID strings.
TENANT_ID = "11111111-1111-1111-1111-111111111111"


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


def _ragas_return(scores: list[dict], **extra) -> dict:
    """What `run_ragas_eval` returns, with the judge records derived the real way.

    `build_judge_records` is the shipped function, not a copy. A double that
    invented its own pairing of scenarios to metrics would let `write_eval_results`
    be exercised against rows the scorer never produces, and the pin that a
    metric the judge did not score STILL gets a row would then be a pin on the
    double.
    """
    return {
        "scores": scores,
        "judge_records": build_judge_records(scores),
        **extra,
    }


@pytest.fixture
def wired(monkeypatch):
    """run_eval_suite with every boundary doubled and every call recorded.

    Returns a dict of recorders; individual tests re-patch one collaborator to
    make it fail and then assert on what still happened.
    """
    agent = MagicMock()
    agent.tenant_id = TENANT_ID
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
        # The EvalResult the task built and the connection it wrote it on (#51),
        # plus the ledger rows the cost is read from. The ledger is a list a test
        # can fill: empty is the honest default here, because no test in this
        # module bills a real call and a cost over no rows is unknown, not zero.
        "record": [],
        "ledger": [],
    }

    # D1/P2: the agent invocation is doubled here so these tests keep testing
    # what they were written to test, which is which connection string each
    # write opens, rather than accidentally exercising a live SDK
    # turn against a MagicMock agent row. The scenarios that come back carry an
    # `agent_response` that is deliberately NOT the reference answer, so any
    # test in this module that starts scoring self-answers fails loudly.
    # tests/unit/test_eval_agent_invocation.py drives the real helper.
    def _fake_invoke(*, agent_id, conn_str, run_id, scenarios, prompt_version_id):
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
        scores = [{"scenario_id": "s1"}]
        # The records the real function derives from those scores, through
        # the real deriver. A double inventing its own would let the writer
        # be tested against a pairing the scorer never produces.
        return {
            "scores": scores,
            "judge_records": build_judge_records(scores),
        }

    monkeypatch.setattr(mod, "run_ragas_eval", _fake_ragas)
    monkeypatch.setattr(mod, "read_run_ledger", lambda run_id, conn_str: rec["ledger"])
    monkeypatch.setattr(
        mod,
        "write_eval_result",
        lambda run_id, result, conn_str: (
            rec["record"].append((run_id, result, conn_str)) or True
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
            "eval_results were not written to production, which is audit "
            "defect D2"
        )
        assert len(wired["ragas"]) == 1
        args, kwargs = wired["ragas"][0]
        assert len(args) == 2 and kwargs == {}, (
            "scoring was handed something besides the scenarios and the "
            "ledger. The argument it used to be given and never read was a "
            "connection string"
        )
        scenarios, led = args
        assert isinstance(scenarios, list)
        assert not [
            value for value in vars(led).values() if isinstance(value, str)
            and value.startswith("postgres")
        ], f"a connection string reached scoring on the ledger: {led!r}"
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
            lambda scenarios, ledger: _ragas_return([
                {
                    "scenario_id": "g0000000-0000-0000-0000-000000000001",
                    "faithfulness": 0.9,
                    "answer_relevancy": 0.9,
                    "context_precision": None,
                    "context_recall": None,
                }
            ]),
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
            lambda scenarios, ledger: _ragas_return([
                {
                    "scenario_id": s["id"],
                    "faithfulness": None,
                    "answer_relevancy": None,
                    "context_precision": None,
                    "context_recall": None,
                }
                for s in scenarios
            ]),
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


# ---------------------------------------------------------------------------
# The judge calls a run pays for (ticket #47)
# ---------------------------------------------------------------------------


def _luna_judge_transport(seen: list[str]) -> httpx.MockTransport:
    """Canned Luna chat-completion bodies, one per structured judge request.

    Instructor names the response model as the tool it forces, so the handler
    reads that name off the request and answers with the canned output for it.
    One fixed shape would fail four of the five schemas ragas asks for.
    `_CANNED_JUDGE_OUTPUTS` is reused from the eval_service tests rather than
    copied, so the two modules can only ever agree about what a Judge returns.

    Every body carries `model` and a `usage` block in OpenAI's shape, which is
    what makes the response hook write a real `model_calls` row instead of
    logging a gap.
    """
    by_name = {cls.__name__: make for cls, make in _CANNED_JUDGE_OUTPUTS.items()}

    def _handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["tools"][0]["function"]["name"]
        seen.append(name)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": "gpt-5.6-luna",
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": by_name[name]().model_dump_json(),
                            },
                        }],
                    },
                }],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 10},
                },
            },
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(_handler)


#: Which purpose each ragas response model belongs to. Instructor names the
#: response model as the tool it forces, so this is what pairs one request with
#: the dimension that made it. Faithfulness asks twice under one purpose, first
#: for the statements and then for a verdict on each.
_PURPOSE_BY_TOOL = {
    "StatementGeneratorOutput": "judge_faithfulness",
    "NLIStatementOutput": "judge_faithfulness",
    "AnswerRelevanceOutput": "judge_answer_relevancy",
    "ContextPrecisionOutput": "judge_context_precision",
    "ContextRecallOutput": "judge_context_recall",
}

#: 4 scenarios x (2 faithfulness + 3 answer_relevancy + 1 context_precision + 1 context_recall) = 28.
EXPECTED_JUDGE_CALLS = 28


class TestJudgeCallsReachTheLedger:
    """A whole run's judge calls, counted where the bill is read.

    Everything between the task and the wire is real: the scorer, the four ragas
    metrics, instructor, the OpenAI SDK, the response hook and
    `record_model_call`'s own INSERT. Only the two network hops are canned, and
    the database is the double every test in this module writes through. A test
    that patched the recorder would prove the recorder was called and nothing
    about whether a row exists.
    """

    def test_a_full_run_bills_every_judge_call_to_the_ledger(
        self, wired, monkeypatch
    ):
        """One row per judge request, on the four dimensions, for this run."""
        from app.domain.model_call import ModelSource
        from app.services import eval_service
        from app.services.eval_service import JUDGE_PURPOSES

        rows: list = []
        seen: list[str] = []
        real_recorder = mod.ledger_recorder

        def _recording_recorder(conn_str):
            """The production recorder, with every row read on its way past."""
            write = real_recorder(conn_str)

            def record(call):
                rows.append((conn_str, call))
                write(call)

            return record

        monkeypatch.setattr(mod, "ledger_recorder", _recording_recorder)
        # `wired` doubles the scorer, because every other test in this module is
        # about which connection string a write opens. This one is about the
        # calls scoring makes, so the real scorer goes back.
        monkeypatch.setattr(mod, "run_ragas_eval", eval_service.run_ragas_eval)
        monkeypatch.setattr(
            eval_service, "_VoyageRagasEmbedding", _FakeRagasEmbedding
        )

        transport = _luna_judge_transport(seen)

        class _Pinned(httpx.AsyncClient):
            """A client the OpenAI SDK still recognises, answering canned bytes.

            A lambda fails here, because the SDK isinstance-checks the client
            it is handed, so the stand-in has to be a real subclass.
            """

            def __init__(self, **kwargs):
                super().__init__(transport=transport, **kwargs)

        with patch("httpx.AsyncClient", _Pinned):
            result = _run()

        assert len(seen) == EXPECTED_JUDGE_CALLS, (
            f"the run made {len(seen)} judge requests where {EXPECTED_JUDGE_CALLS} "
            "is the arithmetic above. A drop is a dimension that stopped asking, "
            "a rise is a bill nobody planned, and both are invisible to a count "
            "compared against itself"
        )
        assert len(rows) == len(seen), (
            f"{len(seen)} judge requests left {len(rows)} ledger rows, so this "
            "tenant's judge spend is under-reported by the difference"
        )
        assert sorted(JUDGE_PURPOSES) == sorted(set(_PURPOSE_BY_TOOL.values())), (
            "a purpose was added to eval_service without a tool to pair it with, "
            "so the pairing below would never see it"
        )
        # Pairing, not set equality. Every purpose being present says nothing
        # about whether a context_recall request was billed to context_recall,
        # and a rollup built on a mislabelled row prices the wrong dimension.
        mispaired = [
            (tool, call.purpose)
            for tool, (_dsn, call) in zip(seen, rows, strict=True)
            if call.purpose != _PURPOSE_BY_TOOL[tool]
        ]
        assert mispaired == [], (
            f"{len(mispaired)} requests were billed to another dimension: {mispaired[:4]}"
        )
        assert {call.served_model for _dsn, call in rows} == {"gpt-5.6-luna"}
        assert {call.model_source for _dsn, call in rows} == {ModelSource.REPORTED}, (
            "the served model was not read off the body the provider sent"
        )
        assert {call.job_id for _dsn, call in rows} == {result["run_id"]}
        assert {call.tenant_id for _dsn, call in rows} == {TENANT_ID}
        assert {call.agent_id for _dsn, call in rows} == {"agent-1"}, (
            "a judge call billed to no agent cannot be charged back to the "
            "agent whose eval bought it"
        )
        assert {dsn for dsn, _call in rows} == {PRODUCTION}
        inserts = [
            sql for sql in wired["cursor"].executed if "INSERT INTO model_calls" in sql
        ]
        assert len(inserts) == EXPECTED_JUDGE_CALLS, (
            f"{len(inserts)} of {EXPECTED_JUDGE_CALLS} rows reached the database "
            "the recorder was bound to, and a recorder that writes some of them "
            "reads as a working ledger"
        )


# ---------------------------------------------------------------------------
# #51. The run's numbers are one record, and the return dict is that record
# ---------------------------------------------------------------------------


#: Scores the fixture's four scenarios, golden high and exploratory low, so a
#: builder that pooled the two datasets would produce a number neither half has.
#: The golden mean is 0.8 and the exploratory mean 0.2; their pooled mean is 0.5,
#: which no assertion below would accept.
_GOLDEN_IDS = (
    "g0000000-0000-0000-0000-000000000001",
    "g0000000-0000-0000-0000-000000000002",
)
_EXPLORATORY_IDS = (
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
)


def _scored(scenario_id: str, value: float) -> dict:
    return {
        "scenario_id": scenario_id,
        "faithfulness": value,
        "answer_relevancy": value,
        "context_precision": value,
        "context_recall": value,
    }


@pytest.fixture
def scored(wired, monkeypatch):
    """`wired`, with Ragas returning real per-scenario numbers for all four rows."""
    scores = [_scored(sid, 0.8) for sid in _GOLDEN_IDS]
    scores += [_scored(sid, 0.2) for sid in _EXPLORATORY_IDS]

    def _fake_ragas(*args, **kwargs):
        wired["ragas"].append((args, kwargs))
        return {
            "scores": scores,
            "judge_records": build_judge_records(scores),
            "sent": 4, "returned": 4, "unattributed": 0,
        }

    monkeypatch.setattr(mod, "run_ragas_eval", _fake_ragas)
    return wired


class TestTheRunWritesItsRecord:
    """The task builds one EvalResult and stores it. #51 slice 1."""

    def test_the_record_lands_on_production(self, scored):
        _run()

        assert len(scored["record"]) == 1, (
            f"the run wrote {len(scored['record'])} record(s); a completed run "
            "writes exactly one"
        )
        run_id, _result, conn_str = scored["record"][0]
        assert conn_str == PRODUCTION, (
            "the record is an observation about a run and belongs on production"
        )
        assert run_id

    def test_the_record_is_about_the_run_that_was_inserted(self, scored):
        returned = _run()
        run_id, result, _ = scored["record"][0]
        assert run_id == returned["run_id"] == result.run_id

    def test_the_per_dataset_numbers_are_the_summarisers_own(self, scored):
        """0.8 golden and 0.2 exploratory, never a pooled 0.5."""
        _run()
        _, result, _ = scored["record"][0]

        golden = result.datasets["golden"]
        exploratory = result.datasets["exploratory"]
        assert golden.metrics["faithfulness"].value == pytest.approx(0.8)
        assert exploratory.metrics["faithfulness"].value == pytest.approx(0.2)
        assert golden.metrics["faithfulness"].observations == 2
        assert (golden.attempted, golden.valid, golden.scored) == (2, 2, 2)

    def test_the_record_reports_both_datasets(self, scored):
        """A dropped dataset is a run reporting half of what it measured."""
        _run()
        _, result, _ = scored["record"][0]
        assert set(result.datasets) == {"golden", "exploratory"}

    def test_the_return_dict_numbers_are_the_records_numbers(self, scored):
        returned = _run()
        _, result, _ = scored["record"][0]

        assert returned["datasets"] == result.payload["datasets"]
        assert returned["attempted"] == result.attempted
        assert returned["valid"] == result.valid
        assert returned["scored"] == result.scored
        assert returned["invocation"] == result.invocation.payload
        assert returned["cost"] == result.cost.payload
        assert returned["served_model"] == result.served_model

    def test_the_returned_scenario_count_is_the_records_attempted(self, scored):
        """One key, one meaning (#51 F5).

        The task returned `len(valid_scenarios)` under this name while the
        console route returned `record.attempted` under it, so the same key on
        the same run answered two questions and neither said which.
        """
        returned = _run()
        _, result, _ = scored["record"][0]

        assert returned["scenario_count"] == result.attempted
        assert returned["scenario_count"] == returned["attempted"]

    def test_the_return_dict_is_the_payload_plus_the_run_keys(self, scored):
        """Every key of the record survives, and the run keys sit beside them."""
        returned = _run()
        _, result, _ = scored["record"][0]

        assert set(result.payload) <= set(returned)
        assert set(returned) - set(result.payload) == {
            "scenario_count",
            "dataset_column_available",
            "golden_set_present",
            "promoted",
            "config_recorded",
            "promotion_enabled",
            "promotion_disabled_reason",
            "agent_invoked",
            "agent_invocation",
            "invocation_recorded",
            "result_recorded",
        }

    def test_the_context_proxy_version_is_stamped(self, scored):
        """#84. Scores computed over different context proxies do not compare."""
        from app.domain.eval_result import CONTEXT_PROXY_VERSION

        returned = _run()
        _, result, _ = scored["record"][0]
        assert result.context_proxy_version == CONTEXT_PROXY_VERSION
        assert returned["context_proxy_version"] == CONTEXT_PROXY_VERSION

    def test_the_invocation_counters_are_the_observations(self, scored):
        returned = _run()
        _, result, _ = scored["record"][0]
        observation = returned["agent_invocation"]

        assert result.invocation.status.value == observation["status"]
        for name in ("valid", "attempted", "responded", "scorable", "failed", "empty"):
            assert getattr(result.invocation, name) == observation[name], name

    def test_a_run_with_no_ledger_rows_reports_an_unknown_cost(self, scored):
        """The ledger hook fails open, so no rows is unknown and never free."""
        returned = _run()
        assert returned["cost"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "usd": None,
            "zar": None,
            "measured": False,
        }

    def _turn_row(self, **overrides):
        from app.domain.model_call import ModelCall

        fields = {
            "purpose": "agent_turn",
            "provider": "openai",
            "requested_model": "gpt-5.6-luna",
            "served_model": "gpt-5.6-luna",
            "model_source": "reported",
            "input_tokens": 400,
            "output_tokens": 90,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "at": datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
            "tenant_id": TENANT_ID,
            "job_id": "the run this ledger belongs to",
        }
        fields.update(overrides)
        return ModelCall(**fields)

    def test_a_priced_run_reports_what_it_spent(self, scored):
        scored["ledger"].append(self._turn_row())
        returned = _run()

        assert returned["cost"]["measured"] is True
        assert returned["cost"]["input_tokens"] == 400
        assert returned["cost"]["output_tokens"] == 90
        assert returned["cost"]["usd"] > 0
        assert returned["cost"]["zar"] > returned["cost"]["usd"]

    def test_a_served_model_the_book_refuses_keeps_the_tokens_and_loses_the_money(
        self, scored
    ):
        """The provider named a snapshot nobody priced. The tokens are still a fact."""
        scored["ledger"].append(self._turn_row(served_model="gpt-5.6-luna-2026-08"))
        returned = _run()

        assert returned["cost"]["measured"] is True
        assert returned["cost"]["input_tokens"] == 400
        assert returned["cost"]["usd"] is None
        assert returned["served_model"] == "gpt-5.6-luna-2026-08", (
            "the served model is read off the run's own agent_turn rows, not "
            "assumed from the model the routing table asked for"
        )

    def test_a_below_floor_run_records_an_unknown_invocation_and_no_scores(
        self, wired, monkeypatch
    ):
        """The record has to be able to say a run measured too little.

        The fail-closed branch writes no eval_results, so a run below the floor
        would otherwise leave nothing on the row at all and read exactly like a
        run that never happened.
        """
        def _thin_invoke(*, agent_id, conn_str, run_id, scenarios, prompt_version_id):
            summary = mod.summarise_agent_invocation(
                [
                    {
                        "scenario_id": s["id"],
                        "responded": False,
                        "scorable": False,
                        "error": "TimeoutError",
                        "retrieve_calls": 0,
                        "retrieve_at_cap": False,
                        "retrieve_unparsed": 0,
                        "retrieved_chunks": 0,
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
            )
            return [], summary

        monkeypatch.setattr(mod, "_invoke_agent_for_scenarios", _thin_invoke)
        returned = _run()
        _, result, _ = wired["record"][0]

        assert result.invocation.status.value == "unknown"
        assert result.invocation.failed == 4 and result.invocation.responded == 0
        assert wired["results"] == [], "a run below the floor wrote scores"
        assert all(
            m["measured"] is False
            for dataset in returned["datasets"].values()
            for m in dataset["metrics"].values()
        ), "a run that scored nothing reported a measured metric"

    def test_a_record_that_cannot_be_stored_is_reported_rather_than_hidden(
        self, wired, monkeypatch
    ):
        """A pre-0022 tenant still scores; the run says its record did not land."""
        monkeypatch.setattr(mod, "write_eval_result", lambda *a, **kw: False)
        assert _run()["result_recorded"] is False


# ---------------------------------------------------------------------------
# #25. A failed turn reaches the record with its class AND what happened
# ---------------------------------------------------------------------------

#: An exception whose own text is unmistakable, so an assertion can look for it
#: in the stored record and expect not to find it.
SENTINEL = "SENTINEL-customer-said-my-card-is-4111111111111111"


def _invocation_record(scenario_id: str, **overrides) -> dict:
    record = {
        "scenario_id": scenario_id,
        "responded": True,
        "scorable": True,
        "error": None,
        "error_message": None,
        "retrieve_calls": 1,
        "retrieve_at_cap": False,
        "retrieve_unparsed": 0,
        "retrieved_chunks": 1,
        "side_effects": [],
    }
    record.update(overrides)
    return record


@pytest.fixture
def one_turn_timed_out(scored, monkeypatch):
    """`scored`, with the first scenario's turn raising at the per-turn bound.

    The summary comes from the real `summarise_agent_invocation`, so the fixture
    cannot hand the task a shape production does not produce.
    """
    timeout_message = f"agent turn exceeded {mod._agent_turn_timeout_s()}s"

    def _fake_invoke(*, agent_id, conn_str, run_id, scenarios, prompt_version_id):
        failed, answered = scenarios[0], scenarios[1:]
        rows = [
            {
                **s,
                "agent_response": f"AGENT SAID: {s['question']}",
                "retrieved_contexts": [f"CTX for {s['id']}"],
            }
            for s in answered
        ]
        records = [
            _invocation_record(
                failed["id"],
                responded=False,
                scorable=False,
                error="TimeoutError",
                error_message=timeout_message,
                retrieve_calls=0,
                retrieved_chunks=0,
            )
        ] + [_invocation_record(s["id"]) for s in answered]
        summary = mod.summarise_agent_invocation(
            records,
            valid=len(scenarios),
            ceiling_skipped=0,
            ceiling_skipped_golden=0,
            per_turn_timeout_s=mod._agent_turn_timeout_s(),
            audit_capture_char_cap=1800,
            retrieved_context_chunk_char_cap=2000,
        )
        return rows, summary

    monkeypatch.setattr(mod, "_invoke_agent_for_scenarios", _fake_invoke)
    scored["timeout_message"] = timeout_message
    return scored


class TestATimeoutReachesTheRecordWithItsMessage:
    """#25. `run_eval_suite.scenario_invocation_failed ... error= error_type=
    TimeoutError`, twice, in eval run 29754ceb.

    `str(TimeoutError())` is the empty string. The type survived and the budget
    did not, so the row said a turn raised and nothing about what ran out.
    """

    def test_the_record_names_the_row_that_timed_out(self, one_turn_timed_out):
        _run()
        _, result, _ = one_turn_timed_out["record"][0]

        assert result.invocation.failed == 1
        assert len(result.failures) == 1
        failure = result.failures[0]
        assert failure.error_type == "TimeoutError"
        assert failure.scenario_id == _GOLDEN_IDS[0], (
            f"the failure names {failure.scenario_id!r}, not the row that failed"
        )

    def test_the_message_names_the_budget_the_turn_exceeded(self, one_turn_timed_out):
        """The one fact the exception's class cannot carry."""
        _run()
        _, result, _ = one_turn_timed_out["record"][0]

        assert result.failures[0].message == one_turn_timed_out["timeout_message"]
        assert str(mod._agent_turn_timeout_s()) in result.failures[0].message, (
            "the message carries no budget, so the record says a turn timed out "
            "and not what it timed out against"
        )

    def test_the_failure_survives_into_what_the_task_returns(self, one_turn_timed_out):
        returned = _run()
        _, result, _ = one_turn_timed_out["record"][0]

        assert returned["failures"] == result.payload["failures"]
        assert returned["failures"][0]["error_type"] == "TimeoutError"

    def test_the_exceptions_own_text_is_absent_from_the_stored_record(
        self, one_turn_timed_out, monkeypatch
    ):
        """THE ANTI-TAUTOLOGY HALF.

        Asserting the message equals a fixed phrase passes just as well when a
        build appends `str(exc)` to it. This drives the whole task with an
        invoker whose `error_message` IS the exception's text, and looks for
        that text in the serialised record. `eval_runs.result` is jsonb the
        owner reads back, so a raw exception string landing there is #96's
        class one table over.
        """
        import json

        _run()
        _, honest, _ = one_turn_timed_out["record"][0]
        assert SENTINEL not in json.dumps(honest.payload)

        # And the pin is worth something only if the sentinel CAN reach the row.
        one_turn_timed_out["record"].clear()
        real_summarise = mod.summarise_agent_invocation

        def _leaking_summarise(records, **kwargs):
            for record in records:
                if record.get("error"):
                    record["error_message"] = SENTINEL
            return real_summarise(records, **kwargs)

        monkeypatch.setattr(mod, "summarise_agent_invocation", _leaking_summarise)
        _run()
        _, leaked, _ = one_turn_timed_out["record"][0]
        assert SENTINEL in json.dumps(leaked.payload), (
            "the record dropped the message entirely, so the assertion above "
            "would pass for a build that stores no message at all"
        )


class TestRunEvalSuiteBeat:
    """The nightly fan-out selects DEPLOYED agents only (#32, decision #6.5).

    Schedules arm per agent at deploy: is_deployed has one writer,
    POST /approve-deployment. The beat selected status='ready' until #32, so
    the first beat worker would have evaluated every ready agent nightly,
    deployed or not, spending eval money on agents no customer can reach.
    """

    def _fan_out(self):
        from contextlib import contextmanager

        mock_agent = MagicMock()
        mock_agent.id = "11111111-1111-1111-1111-111111111111"
        mock_db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_agent]
        mock_db.execute.return_value.scalars.return_value = mock_scalars

        @contextmanager
        def _fake_get_sync_db():
            yield mock_db

        with patch.object(mod, "get_sync_db", _fake_get_sync_db), patch.object(
            mod.run_eval_suite, "apply_async"
        ) as apply_async:
            result = mod.run_eval_suite_beat.run()
        return result, mock_db, apply_async

    def test_one_dispatch_per_selected_agent_with_agent_id_only(self):
        result, _, apply_async = self._fan_out()

        assert result == {"dispatched": 1}
        assert apply_async.call_count == 1
        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {
            "agent_id": "11111111-1111-1111-1111-111111111111"
        }, "agent_id only crosses the task boundary (CTL-08)"

    def test_the_selection_is_deployed_only_never_ready(self):
        """The WHERE clause is the behaviour here, so the compiled SQL is the
        pin. Control: remove the filter and 'is_deployed' leaves the SQL."""
        _, mock_db, _ = self._fan_out()

        stmt = mock_db.execute.call_args.args[0]
        # The WHERE clause alone: the column list names every Agent column,
        # `status` included, so the full statement cannot distinguish the
        # selections this test exists to tell apart.
        where = str(stmt.whereclause).lower()
        assert "is_deployed" in where, (
            f"the beat must select deployed agents only (#32): {where}"
        )
        assert "status" not in where, (
            "the pre-#32 selection (status='ready') is back, which fans the "
            f"nightly eval out to undeployed agents: {where}"
        )
