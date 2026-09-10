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

Every DB boundary here is a double: psycopg2.connect, the control-DB session and
eval_service's writers. So these tests prove which DSN each write opens and what
SQL it carries, and they prove nothing about a live database accepting that SQL.
That is integration territory, and it SKIPS, which is unobserved, never a pass.
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


def scenario_row(
    row_id: str,
    question: str,
    reference: str,
    *,
    source: str = "generated",
    contexts=(),
    dataset=None,
    turns=(),
    ambiguous: bool = False,
) -> tuple:
    """One `eval_scenarios` row at the WIDEST projection's width.

    Built here rather than typed out at each fixture, for two reasons. `_named`
    zips the projection's names onto the row strictly, so a double answering six
    of eight columns is a loud failure now rather than two absent keys; and a
    column added to `_SCENARIO_COLUMNS` is then one edit here instead of a dozen
    literals, which is what left `turns` and `ambiguous` covered by a single test
    when they arrived.

    Imported by test_label_downstream, which drives the same selector.
    """
    values = (
        row_id,
        source,
        question,
        reference,
        list(contexts),
        dataset,
        list(turns),
        ambiguous,
    )
    assert len(values) == len(mod._SCENARIO_COLUMNS), (
        f"the widest projection is {len(mod._SCENARIO_COLUMNS)} columns and this "
        f"builder makes {len(values)}; a new column has to arrive in both"
    )
    return values


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
        turns_column_missing=False,
    ):
        self.golden_rows = list(golden_rows)
        self.exploratory_rows = list(exploratory_rows)
        self.legacy_rows = list(legacy_rows)
        self.dataset_column_missing = dataset_column_missing
        self.turns_column_missing = turns_column_missing
        self.executed: list[str] = []
        self._last: list = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "FROM eval_scenarios" not in sql:
            self._last = []
            return
        # `turns_column_missing=True` stands in for a tenant DB that stopped at
        # 0027: only the widest rung of `_fetch_scenario_rows` names the column,
        # so the middle rung answers and the golden split survives (#227).
        #
        # The match is the PROJECTION, not the substring "turns": a bare `in sql`
        # would also fire on a comment, on `returns`, and on any later column
        # whose name contains it, which is a double branching on the text of the
        # thing under test rather than on the behaviour it stands for (FM-002).
        if self.turns_column_missing and "turns, ambiguous" in sql:
            raise psycopg2.errors.UndefinedColumn('column "turns" does not exist')
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
        scenario_row("g0000000-0000-0000-0000-000000000001", "GQ1", "GA1", dataset="golden"),
        scenario_row("g0000000-0000-0000-0000-000000000002", "GQ2", "GA2", dataset="golden"),
    ]
    exploratory_rows = [
        scenario_row("11111111-1111-1111-1111-111111111111", "Q1", "A1"),
        scenario_row("22222222-2222-2222-2222-222222222222", "Q2", "A2"),
    ]
    cursor = _Cursor(
        golden_rows=golden_rows,
        exploratory_rows=exploratory_rows,
        # FIVE columns, the width of the pre-0014 projection this list answers.
        legacy_rows=[
            ("11111111-1111-1111-1111-111111111111", "generated", "Q1", "A1", []),
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
                ("11111111-1111-1111-1111-111111111111", "generated", "Q1", "A1", []),
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

    def test_a_tenant_without_the_turns_column_keeps_its_golden_split(
        self, wired, monkeypatch
    ):
        """The middle rung of the ladder, and the reason it exists (#227).

        `turns` arrives in tenant 0028. A database that stopped at 0027 refuses
        the widest projection, and the first version of this fetch had only one
        fallback: the pre-0014 query, which has no `dataset` column. So a tenant
        one migration behind would have lost the golden set as well, and with it
        the paired per-item delta the whole split exists to produce, while the
        log said it predated 0014. It never held a multi-turn scenario, because
        the column that could carry one was not there, so the only honest cost of
        this rung is nothing.
        """
        conn = MagicMock()
        # SIX COLUMNS, because the 0014 rung is what answers here. A database
        # without `turns` cannot return it, and `_named` zips strictly, so a
        # double handing eight values to a six-name projection is a failure
        # rather than two keys nobody notices.
        narrow = len(mod._DATASET_PROJECTIONS[1][1])
        cursor = _Cursor(
            golden_rows=[row[:narrow] for row in wired["cursor"].golden_rows],
            exploratory_rows=[row[:narrow] for row in wired["cursor"].exploratory_rows],
            legacy_rows=[("d0000000-0000-0000-0000-00000000000d", "generated", "LQ", "LA", [])],
            turns_column_missing=True,
        )
        conn.cursor.return_value = cursor
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        result = _run()

        assert result["dataset_column_available"] is True, (
            "a tenant DB missing only `turns` was reported as one that cannot be "
            "asked about the golden set at all"
        )
        assert result["golden_set_present"] is True
        assert result["datasets"]["golden"]["attempted"] == 2
        assert result["attempted"] == 4, (
            f"the run scored {result['attempted']} rows, so it fell through to "
            "the pre-0014 single query rather than to the projection above it"
        )


# ---------------------------------------------------------------------------
# One row as the dict every later step reads (tenant 0028, #227)
# ---------------------------------------------------------------------------


class TestTheScenarioRowBecomesAScenario:
    """`_scenario_dict` reads the row BY NAME, off the projection that filled it.

    `_fetch_scenario_rows` keys each row with the column list of whichever rung
    the tenant database accepted, so a column that database could not offer is an
    absent key rather than a value in the wrong slot.
    """

    _ROW_0027 = dict(
        zip(
            mod._SCENARIO_COLUMNS[:6],
            scenario_row("11111111-1111-1111-1111-111111111111", "Q", "A", dataset="golden")[:6],
        )
    )
    _TURNS = [{"role": "user", "content": "I'm setting up Earth Elements locally."}]

    def _row(self, **overrides) -> dict:
        return {**self._ROW_0027, **overrides}

    def test_a_pre_0028_row_is_a_single_turn_scenario(self):
        scenario = mod._scenario_dict(self._ROW_0027)

        assert scenario["turns"] == []
        assert scenario["ambiguous"] is False
        assert scenario["dataset"] == "golden"

    def test_a_0028_row_carries_its_conversation_and_its_ambiguity(self):
        scenario = mod._scenario_dict(self._row(turns=self._TURNS, ambiguous=True))

        assert scenario["turns"] == self._TURNS
        assert scenario["ambiguous"] is True

    def test_the_conversation_is_bounded_at_the_read(self):
        """Once, here, so the run carries the history the model will be given.

        The turn hands this key to the model and `write_eval_samples` copies it
        onto the sample row. Bounding it later would leave the two disagreeing.
        """
        from app.worker.tasks.runtime.agent import TURN_HISTORY_MAX_ROW_CHARS

        overlong = [{"role": "user", "content": "x" * (TURN_HISTORY_MAX_ROW_CHARS + 9)}]
        scenario = mod._scenario_dict(self._row(turns=overlong))

        assert len(scenario["turns"][0]["content"]) == TURN_HISTORY_MAX_ROW_CHARS

    def test_a_turns_column_holding_something_else_is_dropped_not_sent(self):
        assert mod._scenario_dict(self._row(turns="a string"))["turns"] == []
        assert mod._scenario_dict(self._row(turns=None))["turns"] == []


class TestTheRewriteReachesTheJudgeAndTheSampleRow:
    """The wiring, not the parts (#227 PR 2).

    `annotate_resolved_questions` annotates in place and hands the same list back,
    and `run_eval_suite` folds it into the `write_eval_samples(...)` call so the
    rows the sheet records are the rows the Judge scores. Both halves had unit
    tests and the SEAM between them had none: replacing the argument with
    `[dict(s) for s in scored_scenarios]` disabled the whole feature, left the
    calibration sheet showing a rewrite the Judge never used, and 307 tests stayed
    green. Deleting the call outright was caught only by ruff noticing an unused
    import, which is an accident rather than a gate.
    """

    LEAD_IN = [{"role": "user", "content": "I'm setting up Earth Elements locally."}]
    REWRITE = "How do I start the dev server for Earth Elements?"

    def _run_with_a_follow_up(self, wired, monkeypatch):
        """One multi-turn scenario through the task, with the model call doubled.

        `resolve_question` is the seam, not `annotate_resolved_questions`: doubling
        the annotator would double the thing under test.
        """
        from app.services import question_resolution

        monkeypatch.setattr(
            question_resolution,
            "resolve_question",
            lambda question, turns, **kw: self.REWRITE,
        )
        conn = MagicMock()
        conn.cursor.return_value = _Cursor(
            golden_rows=[
                scenario_row("f0000000-0000-0000-0000-00000000000f", "how do I start it?",
                             "Run pnpm dev.", dataset="golden", turns=self.LEAD_IN),
                *wired["cursor"].golden_rows[:1],
            ],
            exploratory_rows=wired["cursor"].exploratory_rows,
        )
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)
        _run()
        [(args, _kwargs)] = wired["ragas"]
        return {row["id"]: row for row in args[0]}

    def test_the_rows_the_judge_scores_carry_the_rewrite(self, wired, monkeypatch):
        scored = self._run_with_a_follow_up(wired, monkeypatch)

        assert scored["f0000000-0000-0000-0000-00000000000f"]["resolved_question"] == (
            self.REWRITE
        ), (
            "the scenarios handed to run_ragas_eval carry no rewrite, so relevancy "
            "scored the raw follow-up and #227 is not fixed"
        )

    def test_a_single_turn_row_in_the_same_run_is_left_alone(self, wired, monkeypatch):
        """The rewrite reaches the row that needed it and no other."""
        scored = self._run_with_a_follow_up(wired, monkeypatch)

        others = [
            row for key, row in scored.items()
            if key != "f0000000-0000-0000-0000-00000000000f"
        ]
        assert others, "the run scored only the multi-turn row, so this proves nothing"
        assert all("resolved_question" not in row for row in others)


class TestTheRunRecordsWhichQuestionRelevancyScored:
    """The stamp, driven through the task rather than called directly (#233).

    `question_resolution_provenance` has its own unit tests and they cover the
    counting. Three things only the task can show, and each is a real way to
    build this wrong:

    - that `run_eval_suite` calls `update_eval_run_config` a SECOND time at all,
    - that it hands the deriver `scored_scenarios`, the list the annotator wrote
      the rewrites into, rather than `valid_scenarios`, which are the rows before
      the agent turn and carry no `resolved_question` at all,
    - that the patch names only its own key, so the observed `agent_invocation`
      object the first patch wrote survives the second one.

    The counts asserted here come out of a run whose rewrites were doubled one
    success and one failure, so a stamp derived from anything but those rows
    reports a different pair of numbers.
    """

    FOLLOW_UP = "f0000000-0000-0000-0000-00000000000f"
    UNRESOLVABLE = "f0000000-0000-0000-0000-00000000000e"
    LEAD_IN = [{"role": "user", "content": "I'm setting up Earth Elements locally."}]

    def _run_a_mixed_conversation(self, wired, monkeypatch, *, relevancy=0.9):
        """Two multi-turn rows, one rewritten and one that fell back, plus singles.

        `resolve_question` is doubled rather than the annotator, and the judge
        double scores the rows it was handed rather than a fixed id, so the
        record has something to be derived FROM.
        """
        from app.services import question_resolution

        monkeypatch.setattr(
            question_resolution,
            "resolve_question",
            lambda question, turns, **kw: (
                None if "deliver" in question else "How do I start the dev server?"
            ),
        )
        monkeypatch.setattr(
            mod,
            "run_ragas_eval",
            lambda scenarios, ledger: _ragas_return(
                [
                    {"scenario_id": s["id"], "answer_relevancy": relevancy,
                     "faithfulness": 0.8, "context_precision": 0.7, "context_recall": 0.6}
                    for s in scenarios
                ]
            ),
        )
        conn = MagicMock()
        conn.cursor.return_value = _Cursor(
            golden_rows=[
                scenario_row(self.FOLLOW_UP, "how do I start it?", "Run pnpm dev.",
                             dataset="golden", turns=self.LEAD_IN),
                scenario_row(self.UNRESOLVABLE, "and do you deliver?", "Yes.",
                             dataset="golden", turns=self.LEAD_IN),
            ],
            exploratory_rows=wired["cursor"].exploratory_rows,
        )
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)
        _run()
        return [patch for _run_id, patch, _conn in wired["config_patched"]]

    def test_the_run_config_carries_what_relevancy_was_measured_against(
        self, wired, monkeypatch
    ):
        patches = self._run_a_mixed_conversation(wired, monkeypatch)

        [counts] = [p["question_resolution"] for p in patches if "question_resolution" in p]
        assert counts == {
            "relevancy_scored": 4,
            "multi_turn": 2,
            "rewritten": 1,
            "raw_question_fallback": 1,
        }, (
            "the run does not record which question its gated relevancy column "
            "was scored against, so a collector cannot tell a rewrite from a "
            "fallback (#233)"
        )

    def test_the_stamp_does_not_disturb_the_invocation_provenance(
        self, wired, monkeypatch
    ):
        """Two patches, and the second names only its own key.

        Merging the two into one call would land the same config, so nothing
        clobbers today. What this holds is the shape that keeps it that way: the
        stamp patch carries `question_resolution` alone, so it can never be the
        write that replaces the observed `agent_invocation` object with a stale
        one. `||` is a shallow merge and that object is replaced whole.
        """
        patches = self._run_a_mixed_conversation(wired, monkeypatch)

        assert [sorted(p) for p in patches] == [
            ["agent_invocation", "agent_invoked", "dimensions_not_exercised",
             "scored_response_source"],
            ["question_resolution"],
        ]

    def test_a_row_the_judge_scored_no_relevancy_for_is_not_counted(
        self, wired, monkeypatch
    ):
        """A relevancy outage leaves the other three metrics and no denominator.

        The run still completes and still writes its rows; what it must not do is
        claim two rewrites were measured when relevancy measured nothing.
        """
        patches = self._run_a_mixed_conversation(wired, monkeypatch, relevancy=None)

        [counts] = [p["question_resolution"] for p in patches if "question_resolution" in p]
        assert counts["relevancy_scored"] == 0
        assert counts["rewritten"] == 0


class TestTheProjectionAndTheReadCannotDisagree:
    """The coupling that used to be a comment somebody had to obey.

    On main the SELECT list and the row-to-dict conversion sat adjacent inside
    `run_eval_suite`. Extracting them put 125 lines between the two, and while
    the row was read positionally, swapping two names in a projection put each
    scenario's reference answer to the agent as its question and scored the answer
    against the question text: audit defect D1, on every tenant, with 77 tests
    still green. These hold the structure that makes that unrepresentable.
    """

    def test_every_rung_is_a_prefix_of_one_column_order(self):
        for revision, columns in mod._DATASET_PROJECTIONS:
            assert columns == mod._SCENARIO_COLUMNS[: len(columns)], (
                f"the {revision} rung is not a prefix of _SCENARIO_COLUMNS, so it "
                "reorders columns rather than dropping trailing ones"
            )
        assert mod._PRE_0014_COLUMNS == mod._SCENARIO_COLUMNS[: len(mod._PRE_0014_COLUMNS)]

    def test_the_rungs_narrow_and_the_widest_is_the_whole_order(self):
        widths = [len(columns) for _rev, columns in mod._DATASET_PROJECTIONS]
        assert widths == sorted(widths, reverse=True), (
            f"the rungs are not widest first: {widths}"
        )
        assert mod._DATASET_PROJECTIONS[0][1] == mod._SCENARIO_COLUMNS
        assert len(mod._PRE_0014_COLUMNS) < min(widths)

    def test_the_read_names_every_column_the_widest_rung_asks_for(self):
        """A column selected and never read is a column paid for and ignored.

        THE KEYS IT ASKS THE ROW FOR, not the keys it returns. The first version
        of this test compared `_SCENARIO_COLUMNS` against the OUTPUT dict, which
        passes because seven of the eight names happen to survive into the output
        and the eighth was hardcoded as an exception. A read that asked the row for
        `turnz` while the projection selected `turns` left it green.
        """

        class _Recording(dict):
            def __init__(self, row):
                super().__init__(row)
                self.asked: set[str] = set()

            def __getitem__(self, key):
                self.asked.add(key)
                return super().__getitem__(key)

            def get(self, key, default=None):
                self.asked.add(key)
                return super().get(key, default)

        row = _Recording(
            dict(
                zip(
                    mod._SCENARIO_COLUMNS,
                    scenario_row("id-1", "Q", "A", contexts=["chunk"], dataset="golden"),
                    strict=True,
                )
            )
        )
        mod._scenario_dict(row)

        assert set(mod._SCENARIO_COLUMNS) <= row.asked, (
            f"selected but never read: {set(mod._SCENARIO_COLUMNS) - row.asked}"
        )
        assert not row.asked - set(mod._SCENARIO_COLUMNS), (
            "the read asks the row for a key no projection selects: "
            f"{row.asked - set(mod._SCENARIO_COLUMNS)}"
        )

    def test_a_real_eight_column_row_reaches_the_scored_set_with_its_turns(
        self, wired, monkeypatch
    ):
        """The widest projection, end to end through the task.

        Every other double in this module returns six-column rows, so the two
        columns 0028 adds were covered only by direct calls on `_scenario_dict`.
        This drives `run_eval_suite` over rows the shape PostgreSQL returns at
        0028 and follows one scenario's turns to the rows the scorer is handed.
        """
        lead_in = [{"role": "user", "content": "I'm setting up Earth Elements locally."}]
        eight = [
            scenario_row(
                f"a000000{n}-0000-0000-0000-00000000000{n}", f"Q{n}", f"A{n}",
                dataset="golden" if n < 2 else None,
                turns=lead_in if n == 0 else [],
                ambiguous=n == 3,
            )
            for n in range(4)
        ]
        conn = MagicMock()
        conn.cursor.return_value = _Cursor(
            golden_rows=eight[:2], exploratory_rows=eight[2:]
        )
        monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

        _run()

        [(args, _kwargs)] = wired["ragas"]
        scored = {row["id"]: row for row in args[0]}
        assert len(scored) == 4
        assert scored["a0000000-0000-0000-0000-000000000000"]["turns"] == lead_in, (
            "the scenario's conversation did not survive the fetch, the dict "
            "conversion and the invocation to the rows the scorer scores"
        )
        assert all(
            scored[key]["turns"] == [] for key in scored if not key.endswith("000000")
        )
        assert scored["a0000003-0000-0000-0000-000000000003"]["ambiguous"] is True

    def test_a_row_of_the_wrong_width_is_a_failure_not_two_absent_keys(self):
        """`_named` zips strictly, and the strictness is aimed at doubles.

        PostgreSQL cannot return a different number of columns from the ones the
        SELECT names, so a mismatch here is always a double that has not kept up
        with a widened projection. A lenient zip made that silent, and it is why
        `turns` and `ambiguous` reached the branch head covered by one test.
        """
        short = ("id-1", "generated", "Q", "A", [], "golden")
        with pytest.raises(ValueError):
            mod._named(mod._SCENARIO_COLUMNS, [short])
        with pytest.raises(ValueError):
            mod._named(mod._PRE_0014_COLUMNS, [short])

        assert mod._named(mod._SCENARIO_COLUMNS[:6], [short]) == [
            dict(zip(mod._SCENARIO_COLUMNS[:6], short, strict=True))
        ]

    def test_a_row_missing_a_column_every_rung_selects_is_loud(self):
        """Absence by bug and absence by revision must not look alike.

        The five columns every projection names are read with `[]`, so a row
        without one raises here rather than yielding a scenario with an empty
        question. `_scenario_dict` runs before the `eval_runs` row is inserted, so
        the task dies leaving no run recorded and the next beat is the retry.
        """
        row = dict(
            zip(
                mod._SCENARIO_COLUMNS,
                scenario_row("id-1", "Q", "A", dataset="golden"),
                strict=True,
            )
        )
        for required in ("id", "source", "question", "reference_answer", "retrieved_contexts"):
            with pytest.raises(KeyError):
                mod._scenario_dict({k: v for k, v in row.items() if k != required})

    def test_a_label_column_is_in_no_projection(self):
        """Moved here from test_label_downstream when the SELECT list left the SQL.

        That file asserts the label columns are in no WHERE clause. The SELECT list
        is now a tuple of names, so this is where a label column would appear.
        """
        for column in ("label_trust_tier", "labelled_by", "labelled_at"):
            assert column not in mod._SCENARIO_COLUMNS, (
                f"{column} is selected by the eval's scenario query"
            )


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
    """The nightly fan-out selects agents that are deployed AND ready (#134).

    Both halves cost money when they are missing. Dropping `is_deployed` fans the
    nightly eval out to every ready agent no customer can reach (#32). Dropping
    `status == 'ready'` keeps spending on an agent whose own chat route now answers
    409, because `is_deployed` has one writer, POST /approve-deployment, and nothing
    clears it when status moves off 'ready' afterwards.
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

    def test_the_selection_is_deployed_and_ready(self):
        """The WHERE clause is the behaviour here, so the compiled SQL is the pin.

        The column list names every Agent column, `status` included, so the full
        statement cannot tell these selections apart. Only the predicate can. It is
        rendered with literal binds because `status` alone would also pass for
        `status = 'pending'`, and a bare `is_deployed` would pass for `= false`.
        """
        _, mock_db, _ = self._fan_out()

        stmt = mock_db.execute.call_args.args[0]
        where = str(
            stmt.whereclause.compile(compile_kwargs={"literal_binds": True})
        ).lower()
        assert "is_deployed = true" in where, (
            f"the beat must select DEPLOYED agents, positively (#32): {where}"
        )
        assert "status = 'ready'" in where, (
            "a deployed agent whose status left 'ready' refuses its own customers "
            f"while the nightly eval keeps spending on it (#134): {where}"
        )
        assert " and " in where, (
            f"the two filters must both hold, not either one (#134): {where}"
        )


class TestTheEvalRunBound:
    """`eval_run_bound_s` is read by two modules and written down in neither.

    Its product sets the idempotency window a redelivered message is judged
    against, and in the deployment checklist it sets how long a silent chain may
    go before the guard stops trusting the clock. Both callers imported it and
    no test ever called it, so a wrong product would have been read as right in
    two places at once.
    """

    def test_it_is_the_invocation_ceiling_times_the_per_turn_bound(self):
        """P2 made a run invoke the customer agent once per scenario, so the
        worst case is every scenario the ceiling admits taking a whole turn."""
        from app.services.eval_service import AGENT_INVOCATION_MAX_CALLS_PER_RUN
        from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S

        assert (
            mod.eval_run_bound_s()
            == AGENT_INVOCATION_MAX_CALLS_PER_RUN * AGENT_TURN_TIMEOUT_S
        ), (
            "anything smaller under-reports how long one run can hold the "
            "runtime queue, and the checklist guard reaps live chains on it"
        )

    def test_a_longer_turn_budget_moves_the_bound(self):
        """ONE copy of the number, imported (audit D3). A literal would not move."""
        before = mod.eval_run_bound_s()

        with patch("app.worker.tasks.runtime.agent.AGENT_TURN_TIMEOUT_S", 180):
            after = mod.eval_run_bound_s()

        assert after == before * 2, (
            "doubling agent.py's per-turn bound doubles how long a run can "
            f"take, and this bound did not follow it: {before} then {after}"
        )

    def test_a_wider_invocation_ceiling_moves_the_bound(self):
        """The other term. More scenarios invoked is more wall clock spent."""
        before = mod.eval_run_bound_s()

        with patch.object(mod, "AGENT_INVOCATION_MAX_CALLS_PER_RUN", 120):
            after = mod.eval_run_bound_s()

        assert after == before * 2, (
            "the ceiling on invocations is one of the two factors, and raising "
            f"it left the bound where it was: {before} then {after}"
        )
