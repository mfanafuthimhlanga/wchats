"""
Unit tests for the eval routes in apps/api/app/api/v1/evals.py.

Tests:
    GET  /api/v1/agents/{agent_id}/eval-runs
    GET  /api/v1/agents/{agent_id}/eval-runs/{run_id}/results
    POST /api/v1/agents/{agent_id}/eval-runs/trigger

Coverage:
    - Happy-path response shapes (RESEARCH.md §9)
    - list_eval_runs reads the record and computes nothing (#51 slice 3)
    - Unmeasured vs measured-zero: no reading is 'unknown', never 0.0
    - IDOR prevention: 404 on agent not found, 404 on cross-tenant access
    - POST trigger: 202 on ready agent, 400 on non-ready agent, 404 on unknown agent
    - POST trigger: only agent_id dispatched to Celery (CTL-08)
    - GET routes: asyncio.to_thread + psycopg2 path mocked correctly
    - Auth: 401/403 when X-API-Key header is missing (no dependency overrides)

The fixtures build every record through `EvalResult`, so a payload these tests
drive the route with is a payload `run_eval_suite` could have written. A
hand-typed dict would let this module assert on a shape the writer cannot
produce.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_current_tenant
from app.domain.eval_result import (
    DatasetOutcome,
    EvalResult,
    Invocation,
    InvocationStatus,
    Measurement,
)
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.eval_service import METRIC_KEYS

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    """Agent in 'ready' state with a fake encrypted neon_connection_string."""
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    """Async DB mock that returns *agent* on db.get(Agent, agent_id)."""
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    return mock_session


def _make_mock_db_returning_none() -> AsyncMock:
    """Async DB mock that returns None on db.get() — simulates missing agent."""
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)
    return mock_session


def _outcome(attempted: int, valid: int, scored: int, *values: float | None) -> DatasetOutcome:
    """One dataset's three counts and its four metrics, in METRIC_KEYS order.

    A None value is a metric that came back with no number: measured False over
    zero observations, which is what `Measurement` refuses to let read as a
    score. Anything else was measured over `scored` rows.
    """
    return DatasetOutcome(
        attempted=attempted,
        valid=valid,
        scored=scored,
        metrics={
            metric: Measurement(
                value=value,
                observations=scored if value is not None else 0,
                measured=value is not None,
            )
            for metric, value in zip(METRIC_KEYS, values)
        },
    )


#: A dataset this run reported nothing for: no rows attempted, four metrics
#: unmeasured. The ordinary shape of `golden` on a tenant that has designated no
#: golden rows.
_NO_ROWS = (0, 0, 0, None, None, None, None)


def _record_payload(
    run_id: str,
    golden: DatasetOutcome,
    exploratory: DatasetOutcome,
) -> dict:
    """`eval_runs.result` exactly as `write_eval_result` stores it.

    Built through `EvalResult` rather than typed as a dict, so a payload this
    module drives the route with is one the run could actually have written and
    a rule the record enforces cannot be side-stepped here.
    """
    valid = golden.valid + exploratory.valid
    scored = golden.scored + exploratory.scored
    return EvalResult(
        run_id=run_id,
        agent_id=str(uuid4()),
        invocation=Invocation(
            status=InvocationStatus.MEASURED,
            valid=valid,
            attempted=valid,
            responded=scored,
            scorable=scored,
            failed=0,
            empty=valid - scored,
        ),
        datasets={"golden": golden, "exploratory": exploratory},
        requested_model="gpt-5.6-luna",
    ).payload


def _fake_eval_runs_rows() -> list[tuple]:
    """Two fake eval_run rows: one carrying a record, one that never wrote one.

    Column order matches _LIST_EVAL_RUNS_SQL: id, started_at, finished_at,
    status, result. The first run scored 20 exploratory rows and designated no
    golden ones, which is the ordinary tenant. The second died before the write
    and has no numbers at all.
    """
    measured_id = str(uuid4())
    return [
        (
            measured_id,
            datetime(2026, 5, 23, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 23, 2, 4, 0, tzinfo=timezone.utc),
            "complete",
            _record_payload(
                measured_id,
                golden=_outcome(*_NO_ROWS),
                exploratory=_outcome(20, 20, 20, 0.87, 0.91, 0.83, 0.79),
            ),
        ),
        (
            str(uuid4()),
            datetime(2026, 5, 22, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 22, 2, 1, 0, tzinfo=timezone.utc),
            "failed",
            None,  # the run failed before it recorded anything
        ),
    ]


def _judge_outage_run_rows() -> list[tuple]:
    """A run that COMPLETED and measured nothing.

    The failing input behind the tri-state work: the judge LLM is down for the
    duration, Ragas returns NaN for all four metrics on all 30 scenarios,
    run_ragas_eval emits None, and the run is marked 'complete' on production.
    The record says 30 attempted, 30 valid, 0 scored, four metrics unmeasured.
    Rendered as 0.00 it sits directly under yesterday's 0.94 and reads as a
    total quality collapse.
    """
    run_id = str(uuid4())
    return [
        (
            run_id,
            datetime(2026, 5, 24, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 24, 2, 6, 0, tzinfo=timezone.utc),
            "complete",
            _record_payload(
                run_id,
                golden=_outcome(*_NO_ROWS),
                exploratory=_outcome(30, 30, 0, None, None, None, None),
            ),
        ),
    ]


def _both_datasets_scored_rows() -> list[tuple]:
    """A run whose golden AND exploratory halves both scored rows.

    Golden 0.93 against exploratory 0.71 is the pair the split exists to keep
    apart: any single number over both moves whenever the exploratory DRAW
    moves, so a redraw becomes indistinguishable from a regression.
    """
    run_id = str(uuid4())
    return [
        (
            run_id,
            datetime(2026, 5, 25, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 25, 2, 5, 0, tzinfo=timezone.utc),
            "complete",
            _record_payload(
                run_id,
                golden=_outcome(10, 10, 10, 0.93, 0.90, 0.88, 0.86),
                exploratory=_outcome(20, 20, 18, 0.71, 0.70, 0.69, 0.68),
            ),
        ),
    ]


def _fake_ledger_rows() -> list[tuple]:
    """ORRERY ledger row (OPS-12): (born_in_production, red_team, authored).

    list_eval_runs makes TWO asyncio.to_thread calls — the eval-runs query and
    then _LEDGER_SQL — so tests must patch to_thread with side_effect, not
    return_value, or the ledger unpack at evals.py receives eval-run rows.
    """
    return [(3, 2, 15)]


def _fake_eval_results_rows(run_id: str, scenario_id: str) -> list[tuple]:
    """Rows for a single scenario with all four metrics."""
    return [
        (scenario_id, "What is your return policy?", "generated", "faithfulness", 0.95),
        (scenario_id, "What is your return policy?", "generated", "answer_relevancy", 0.88),
        (scenario_id, "What is your return policy?", "generated", "context_precision", 0.90),
        (scenario_id, "What is your return policy?", "generated", "context_recall", 0.85),
    ]


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/eval-runs — list eval runs
# ---------------------------------------------------------------------------


class TestListEvalRuns:
    """Tests for GET /api/v1/agents/{agent_id}/eval-runs."""

    async def test_returns_200_with_eval_runs_shape(self):
        """Happy path: returns 200 with eval_runs list and aggregate_scores dicts."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        fake_rows = _fake_eval_runs_rows()

        try:
            with (
                patch(
                    "app.api.v1.evals.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.evals.asyncio.to_thread",
                    # Two round trips: the run list with its records, then the
                    # ORRERY ledger. The per-dataset breakdown used to cost a
                    # third and now comes out of the record.
                    new=AsyncMock(side_effect=[fake_rows, _fake_ledger_rows()]),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "eval_runs" in body
        assert len(body["eval_runs"]) == 2

        first = body["eval_runs"][0]
        assert "id" in first
        assert "started_at" in first
        assert "finished_at" in first
        assert "status" in first
        assert "scenario_count" in first
        assert "aggregate_scores" in first
        scores = first["aggregate_scores"]
        assert "faithfulness" in scores
        assert "answer_relevancy" in scores
        assert "context_precision" in scores
        assert "context_recall" in scores
        assert first["status"] == "complete"
        assert first["result"] == "present"
        assert first["scenario_count"] == 20
        assert abs(scores["faithfulness"] - 0.87) < 0.001

        # OPS-12: the ORRERY ledger travels with the eval-runs response
        assert body["ledger"] == {
            "born_in_production_count": 3,
            "red_team_count": 2,
            "authored_count": 15,
        }

    async def _get_runs(
        self,
        rows: list[tuple],
        pre_0022: bool = False,
        query_error: Exception | None = None,
    ) -> dict:
        """Drive GET /eval-runs over *rows* and return the parsed body.

        pre_0022 stands in for a tenant DB that predates migration 0022: the
        wide SELECT raises UndefinedColumn, the narrow one returns the same runs
        with their record column stripped, and every run has to report result
        "absent" rather than a number recovered from somewhere else.

        query_error is anything else the runs query can raise. It must surface.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        if query_error is not None:
            calls: list = [query_error]
        elif pre_0022:
            calls = [
                psycopg2.errors.UndefinedColumn("column er.result does not exist"),
                [row[:4] for row in rows],
                _fake_ledger_rows(),
            ]
        else:
            calls = [rows, _fake_ledger_rows()]

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch(
                    "app.api.v1.evals.asyncio.to_thread",
                    new=AsyncMock(side_effect=calls),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        return response.json()

    async def test_unmeasured_metric_is_not_reported_as_a_score(self):
        """A NULL average is 'we have no observation', never 'zero'.

        This is the whole owner-facing consequence: an unmeasured run rendered
        as 0.00 beside yesterday's 0.94 reads as a total quality collapse, and
        the rational response to a collapse is to roll back a healthy agent.
        """
        body = await self._get_runs(_judge_outage_run_rows())
        run = body["eval_runs"][0]

        assert run["status"] == "complete"
        for metric in METRIC_KEYS:
            assert run["metrics"][metric] == {
                "value": None,
                "measured": False,
                "observations": 0,
            }, f"{metric} was never measured and the response claims a value"

    async def test_a_measured_zero_is_distinguishable_from_an_unmeasured_one(self):
        """The two states that used to be identical on the wire."""
        run_id = str(uuid4())
        row = (
            run_id,
            datetime(2026, 5, 24, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 24, 2, 6, 0, tzinfo=timezone.utc),
            "complete",
            _record_payload(
                run_id,
                golden=_outcome(*_NO_ROWS),
                # Faithfulness genuinely averaged 0.0 over 30 rows; relevancy
                # came back with nothing at all.
                exploratory=_outcome(30, 30, 30, 0.0, None, None, None),
            ),
        )

        body = await self._get_runs([row])
        run = body["eval_runs"][0]

        assert run["metrics"]["faithfulness"] == {
            "value": 0.0,
            "measured": True,
            "observations": 30,
        }
        assert run["metrics"]["answer_relevancy"]["measured"] is False
        assert (
            run["metrics"]["faithfulness"] != run["metrics"]["answer_relevancy"]
        ), "a measured 0.0 and an unmeasured metric are the same on the wire"

    async def test_every_run_carries_all_three_of_its_counts(self):
        """attempted, valid and scored are three claims, and #26 is what two look like.

        The route reported COUNT(DISTINCT scenario_id) over `eval_results` as
        `scenario_count`, the rows a judge SCORED, while the task that
        produced them reported the rows the selector FETCHED. Both numbers were
        right about their own question and the response said which one it was
        answering nowhere, so the console read 18 where the task read 20.
        """
        body = await self._get_runs(_judge_outage_run_rows())
        run = body["eval_runs"][0]

        assert run["scenario_count"] == 30, "attempted"
        assert run["valid_scenario_count"] == 30, "the denominator"
        assert run["scored_scenario_count"] == 0, "what actually scored"

    async def test_numeric_projection_is_retained_for_the_shipped_console(self):
        """aggregate_scores stays numeric on purpose.

        apps/admin types these `number` and calls .toFixed(2) on them
        (agents/[id]/eval/page.tsx:291), so emitting null here would throw on
        the eval page. The honest reading is `metrics`; this key is a
        compatibility projection and the test says so rather than letting a
        future reader mistake it for a measurement.
        """
        body = await self._get_runs(_judge_outage_run_rows())
        run = body["eval_runs"][0]

        assert run["aggregate_scores"]["faithfulness"] == 0.0
        assert run["metrics"]["faithfulness"]["measured"] is False

    # -----------------------------------------------------------------
    # The record is the source, and the only source (#51 slice 3)
    # -----------------------------------------------------------------

    async def test_the_routes_number_is_whatever_the_record_says(self):
        """Change a number in the stored record and the response follows it.

        The first proof that this route reads `eval_runs.result`. Nothing else
        moves between the two calls: the same run, the same two round trips, one
        edited payload. Twenty scored at 0.87 becomes seven scored at 0.42
        because the record says so.
        """
        rows = _fake_eval_runs_rows()
        before = (await self._get_runs(rows))["eval_runs"][0]

        assert before["scored_scenario_count"] == 20
        assert before["metrics"]["faithfulness"]["value"] == 0.87

        edited = (
            *rows[0][:4],
            _record_payload(
                rows[0][0],
                golden=_outcome(*_NO_ROWS),
                exploratory=_outcome(20, 20, 7, 0.42, 0.91, 0.83, 0.79),
            ),
        )
        after = (await self._get_runs([edited]))["eval_runs"][0]

        assert after["scored_scenario_count"] == 7
        assert after["metrics"]["faithfulness"]["value"] == 0.42
        assert after["metrics"]["faithfulness"]["observations"] == 7, (
            "the observation count travels with the number, so a reader can see "
            "that a mean came off seven rows"
        )

    async def test_the_numbers_survive_every_result_row_being_deleted(self):
        """The second proof: `eval_results` is empty and the numbers do not move.

        The tenant DB is faked at `_query_tenant_db_sync` rather than at
        `to_thread`, so this test can answer per statement. Every query that
        names `eval_results` gets an empty result set, which is the table with
        the run's twenty rows deleted out of it. The response still reports 20
        attempted at 0.87, because the route never asks that table anything.

        Restore the COUNT/AVG join and this goes red twice over: the runs query
        would name `eval_results`, and the empty result set it gets back would
        take the whole response to zero runs.
        """
        rows = _fake_eval_runs_rows()
        executed: list[str] = []

        def _fake_query(conn_str, sql, params):
            executed.append(sql)
            if "eval_results" in sql:
                return []
            if "born_in_production_count" in sql:
                return _fake_ledger_rows()
            return rows

        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(
            ready_agent
        )
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals._query_tenant_db_sync", side_effect=_fake_query),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        run = response.json()["eval_runs"][0]
        assert run["scenario_count"] == 20
        assert run["scored_scenario_count"] == 20
        assert run["metrics"]["faithfulness"]["value"] == 0.87
        assert not any("eval_results" in sql for sql in executed), (
            "this route ran a query over eval_results; every number it reports "
            "is supposed to come off the record the run wrote"
        )

    async def test_a_run_without_a_record_reports_nulls_and_says_why(self):
        """No record means no numbers. Never a zero, never a recovered figure.

        A zero here is indistinguishable from a run that attempted nothing, and
        the owner's rational response to a run that scored 0 of 0 is not the
        response to a run whose record never landed.
        """
        body = await self._get_runs(_fake_eval_runs_rows())
        run = body["eval_runs"][1]

        assert run["result"] == "absent"
        assert run["scenario_count"] is None
        assert run["valid_scenario_count"] is None
        assert run["scored_scenario_count"] is None
        assert run["metrics_dataset"] is None
        for metric in METRIC_KEYS:
            assert run["metrics"][metric]["measured"] is False
        assert run["datasets"]["available"] is False
        assert run["datasets"]["golden"]["scenario_count"] is None
        # The run's own columns are the row's and survive intact.
        assert run["status"] == "failed"
        assert run["started_at"] == "2026-05-22T02:00:00+00:00"

    async def test_a_record_that_breaks_a_rule_reads_as_absent(self):
        """A stored payload is validated on the way out, not trusted on sight.

        `scored` above `valid` is a shape `DatasetOutcome` refuses to build, so
        a row holding it was written by something that is not this build's
        writer. Rendering its numbers would put a figure on the console that no
        rule in this system vouches for.
        """
        rows = _fake_eval_runs_rows()
        payload = rows[0][4]
        payload["datasets"]["exploratory"]["scored"] = 99

        body = await self._get_runs([(*rows[0][:4], payload)])
        run = body["eval_runs"][0]

        assert run["result"] == "absent"
        assert run["scenario_count"] is None
        assert run["scored_scenario_count"] is None

    async def test_the_list_query_aggregates_nothing(self):
        """A source-level pin on the shape of the query itself.

        Every test in this module mocks psycopg2 out, so none of them executes
        the SQL. What can be asserted is that it contains no arithmetic at all:
        the moment a COUNT, an AVG or a CASE reappears here, this route is
        deriving a figure the run already derived, and the two are free to
        disagree the way they did in #26.
        """
        from app.api.v1.evals import _LIST_EVAL_RUNS_PRE_0022_SQL, _LIST_EVAL_RUNS_SQL

        for sql in (_LIST_EVAL_RUNS_SQL, _LIST_EVAL_RUNS_PRE_0022_SQL):
            upper = sql.upper()
            for arithmetic in ("COUNT(", "AVG(", "CASE ", "SUM(", "JOIN"):
                assert arithmetic not in upper, (
                    f"{arithmetic.strip()} is back in the list query; the record "
                    "is supposed to be the only place these numbers are computed"
                )
        assert "er.result" in _LIST_EVAL_RUNS_SQL

    # -----------------------------------------------------------------
    # P2, the golden/exploratory split. Two measurements, never one number.
    # -----------------------------------------------------------------

    async def test_golden_and_exploratory_are_reported_separately(self):
        """The two datasets travel as two blocks with their own denominators."""
        body = await self._get_runs(_both_datasets_scored_rows())
        datasets = body["eval_runs"][0]["datasets"]

        assert datasets["available"] is True
        assert datasets["golden"]["scenario_count"] == 10
        assert datasets["golden"]["valid_scenario_count"] == 10
        assert datasets["golden"]["scored_scenario_count"] == 10
        assert datasets["exploratory"]["scenario_count"] == 20
        assert datasets["exploratory"]["scored_scenario_count"] == 18
        assert datasets["golden"]["metrics"]["faithfulness"] == {
            "value": 0.93,
            "measured": True,
            "observations": 10,
        }
        assert datasets["exploratory"]["metrics"]["faithfulness"] == {
            "value": 0.71,
            "measured": True,
            "observations": 18,
        }

    async def test_the_two_dataset_scores_are_never_merged_into_one(self):
        """A golden mean and an exploratory mean are different measurements.

        The failure this forbids is silent: with golden 0.93 and exploratory
        0.71, any single number over both moves whenever the exploratory DRAW
        moves, so a redraw is indistinguishable from a regression — precisely
        the property the fixed golden set exists to provide.
        """
        body = await self._get_runs(_both_datasets_scored_rows())
        run = body["eval_runs"][0]
        datasets = run["datasets"]

        golden = datasets["golden"]["metrics"]["faithfulness"]["value"]
        exploratory = datasets["exploratory"]["metrics"]["faithfulness"]["value"]
        assert golden != exploratory

        combined = (golden + exploratory) / 2
        for name in ("golden", "exploratory"):
            for metric in datasets[name]["metrics"].values():
                assert metric["value"] != combined, (
                    "a merged golden+exploratory mean has appeared in the "
                    "per-dataset block"
                )
        for metric in run["metrics"].values():
            assert metric["value"] != combined, (
                "a merged golden+exploratory mean has appeared at run level"
            )

    async def test_a_run_that_scored_both_halves_reports_no_run_level_number(self):
        """Two measurements, so there is no run-level one and the route says so.

        The record holds no pooled mean by construction. When both datasets
        scored, the honest run-level answer is that the question has two
        answers and they are under `datasets`. A weighted average here would be
        the route computing a number nobody measured.
        """
        body = await self._get_runs(_both_datasets_scored_rows())
        run = body["eval_runs"][0]

        assert run["metrics_dataset"] is None
        for metric in METRIC_KEYS:
            assert run["metrics"][metric]["measured"] is False
        # The counts are sums, not means, so they stay exact and stay reported.
        assert run["scenario_count"] == 30
        assert run["scored_scenario_count"] == 28

    async def test_a_single_scoring_dataset_names_itself_at_run_level(self):
        """One dataset scored, so there is nothing to pool and its numbers are the run's."""
        body = await self._get_runs(_fake_eval_runs_rows())
        run = body["eval_runs"][0]

        assert run["metrics_dataset"] == "exploratory"
        assert run["metrics"]["faithfulness"]["value"] == 0.87
        assert (
            run["metrics"]["faithfulness"]
            == run["datasets"]["exploratory"]["metrics"]["faithfulness"]
        ), "the run-level reading is the dataset's, copied, not recomputed"

    async def test_a_run_with_no_golden_rows_reports_zero_not_absence(self):
        """An empty golden bucket is present and zeroed, never a missing key.

        A missing key has to be interpreted, and the two available readings,
        'this run covered no golden rows' and 'this response cannot say', are
        exactly the pair the `available` flag exists to keep apart.
        """
        body = await self._get_runs(_fake_eval_runs_rows())
        datasets = body["eval_runs"][0]["datasets"]

        assert datasets["available"] is True
        assert datasets["golden"]["scenario_count"] == 0
        assert all(
            metric == {"value": None, "measured": False, "observations": 0}
            for metric in datasets["golden"]["metrics"].values()
        ), "an uncovered golden set must be unmeasured, never zero-scored"

    async def test_the_unattributed_bucket_reports_nothing_rather_than_zero(self):
        """The record does not carry this count, so the route does not claim one.

        `summarise_run_validity` counts result rows whose scenario no longer
        exists and keeps them out of both datasets, and `EvalResult` stores no
        such field. This route used to recount them in its own SQL. A zero here
        would assert that a run had no unattributable rows, which is a claim
        nothing in the response is entitled to make.
        """
        body = await self._get_runs(_both_datasets_scored_rows())
        unattributed = body["eval_runs"][0]["datasets"]["unattributed"]

        assert unattributed == {"scenario_count": None, "scored_scenario_count": None}
        assert "metrics" not in unattributed, (
            "a mean over rows whose scenario is unknown is a mean over an "
            "unknown denominator and must not be constructible from this response"
        )

    async def test_pre_0022_tenant_says_it_cannot_tell_rather_than_reporting_none(self):
        """UndefinedColumn on `eval_runs.result` degrades, and says it degraded.

        A tenant one migration behind holds no record for any run on it. Every
        run there reports result 'absent' with null counts, never a zero and
        never a figure recovered from `eval_results` behind the record's back,
        which is exactly what this route used to do for every run on every
        tenant.
        """
        body = await self._get_runs(_fake_eval_runs_rows(), pre_0022=True)
        run = body["eval_runs"][0]

        assert run["result"] == "absent"
        assert run["datasets"]["available"] is False
        assert run["scenario_count"] is None
        assert run["scored_scenario_count"] is None
        # The run's own columns still arrive, from the narrow query.
        assert run["status"] == "complete"
        assert run["finished_at"] == "2026-05-23T02:04:00+00:00"

    async def test_a_genuine_query_failure_is_not_swallowed_as_a_missing_column(self):
        """Only UndefinedColumn degrades. Anything else must surface.

        The narrow except is the point: catching Exception here would turn a
        connection failure into a confident "this tenant predates 0022", which
        is a fabricated explanation for an outage.
        """
        with pytest.raises(psycopg2.OperationalError):
            await self._get_runs(
                _fake_eval_runs_rows(),
                query_error=psycopg2.OperationalError("connection refused"),
            )

    async def test_returns_404_when_agent_not_found(self):
        """404 when agent doesn't exist in control DB."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_returning_none()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{uuid4()}/eval-runs",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_404_on_cross_tenant_idor(self):
        """404 on IDOR attempt — agent exists but belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        # Agent belongs to other_tenant, not fake_tenant
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{foreign_agent.id}/eval-runs",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        # Must return 404 — not 403 — to prevent tenant enumeration
        assert response.status_code == 404

    async def test_requires_api_key(self):
        """401/403 when X-API-Key header is missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/agents/{uuid4()}/eval-runs")

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/eval-runs/{run_id}/results — per-scenario results
# ---------------------------------------------------------------------------


class TestGetEvalRunResults:
    """Tests for GET /api/v1/agents/{agent_id}/eval-runs/{run_id}/results."""

    async def test_returns_200_with_results_shape(self):
        """Happy path: returns 200 with results list grouped by scenario."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        fake_rows = _fake_eval_results_rows(str(run_id), scenario_id)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=fake_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "results" in body
        assert len(body["results"]) == 1

        result = body["results"][0]
        assert result["scenario_id"] == scenario_id
        assert result["question"] == "What is your return policy?"
        assert result["source"] == "generated"
        assert "scores" in result
        assert "passed" in result
        scores = result["scores"]
        assert abs(scores["faithfulness"] - 0.95) < 0.001
        assert abs(scores["answer_relevancy"] - 0.88) < 0.001

    async def test_passed_flag_true_when_all_scores_above_threshold(self):
        """passed=True when all four scores >= EVAL_FAITHFULNESS_THRESHOLD (0.90)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        # All scores >= 0.90
        passing_rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.95),
            (scenario_id, "Q?", "generated", "answer_relevancy", 0.92),
            (scenario_id, "Q?", "generated", "context_precision", 0.91),
            (scenario_id, "Q?", "generated", "context_recall", 0.90),
        ]

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=passing_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert body["results"][0]["passed"] is True

    async def test_passed_flag_false_when_gated_score_below_threshold(self):
        """passed=False when a GATED metric falls below its threshold.

        D-21 LOCKED gates on exactly two metrics, faithfulness AND
        answer_relevancy.
        Here answer_relevancy = 0.79 < EVAL_RELEVANCY_THRESHOLD (0.90).
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        # answer_relevancy = 0.79 — below threshold, and it IS part of the gate
        failing_rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.95),
            (scenario_id, "Q?", "generated", "answer_relevancy", 0.79),
            (scenario_id, "Q?", "generated", "context_precision", 0.91),
            (scenario_id, "Q?", "generated", "context_recall", 0.93),
        ]

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=failing_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert body["results"][0]["passed"] is False

    async def test_passed_flag_ignores_ungated_metrics_below_threshold(self):
        """passed stays True when only NON-gated metrics are below threshold.

        Pins the D-21 LOCKED contract: context_precision and context_recall are
        reported but deliberately NOT part of the promotion gate. A prior version
        of this suite asserted a 4-metric "any score" rule, which contradicts
        D-21 — it never ran because the module was uncollectable while the ragas
        import was broken.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        # Both gated metrics pass; both ungated metrics are well below threshold.
        rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.95),
            (scenario_id, "Q?", "generated", "answer_relevancy", 0.92),
            (scenario_id, "Q?", "generated", "context_precision", 0.41),
            (scenario_id, "Q?", "generated", "context_recall", 0.39),
        ]

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert body["results"][0]["passed"] is True

    async def _get_results(self, rows: list[tuple]) -> dict:
        """Drive GET /eval-runs/{run_id}/results over *rows*."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{uuid4()}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        return response.json()

    async def test_unmeasured_scenario_is_neither_passed_nor_failed(self):
        """A judge outage NULLs every score. Those scenarios did not fail.

        write_eval_results writes the NULLs faithfully; rendering them as
        passed=false reports 30 failing scenarios for a run that measured
        nothing, which is the reading that gets a healthy agent rolled back.
        """
        scenario_id = str(uuid4())
        rows = [
            (scenario_id, "Q?", "generated", "faithfulness", None),
            (scenario_id, "Q?", "generated", "answer_relevancy", None),
            (scenario_id, "Q?", "generated", "context_precision", None),
            (scenario_id, "Q?", "generated", "context_recall", None),
        ]

        body = await self._get_results(rows)
        result = body["results"][0]

        assert result["passed"] is None, (
            "an unmeasured scenario was rendered as a failing one"
        )
        assert result["metrics"]["faithfulness"] == {"score": None, "measured": False}

    async def test_a_gated_metric_that_is_missing_makes_the_verdict_unknown(self):
        """Half a measurement is not a verdict.

        faithfulness scored 0.99 and answer_relevancy produced nothing, so the
        two-metric gate (D-21) cannot be evaluated. Reporting passed=false here
        would attribute a failure to a metric that was never observed.
        """
        scenario_id = str(uuid4())
        rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.99),
            (scenario_id, "Q?", "generated", "answer_relevancy", None),
        ]

        body = await self._get_results(rows)
        result = body["results"][0]

        assert result["passed"] is None
        assert result["metrics"]["faithfulness"]["measured"] is True
        assert result["metrics"]["answer_relevancy"]["measured"] is False

    async def test_a_measured_zero_still_fails(self):
        """The tri-state must not turn a real zero into 'unknown' — that would
        fail OPEN on the one reading that should stop a deploy."""
        scenario_id = str(uuid4())
        rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.0),
            (scenario_id, "Q?", "generated", "answer_relevancy", 0.0),
        ]

        body = await self._get_results(rows)
        result = body["results"][0]

        assert result["passed"] is False
        assert result["metrics"]["faithfulness"] == {"score": 0.0, "measured": True}

    async def test_returns_empty_results_when_no_rows(self):
        """Empty results list when no eval_results rows exist for the run."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=[])),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{uuid4()}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []

    async def test_returns_404_on_cross_tenant_idor(self):
        """404 on IDOR: agent belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{foreign_agent.id}/eval-runs/{uuid4()}/results",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_requires_api_key(self):
        """401/403 when X-API-Key header is missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/agents/{uuid4()}/eval-runs/{uuid4()}/results"
            )

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/eval-runs/trigger — manual dispatch
# ---------------------------------------------------------------------------


class TestTriggerEvalRun:
    """Tests for POST /api/v1/agents/{agent_id}/eval-runs/trigger."""

    async def test_returns_202_with_queued_status_and_task_id(self):
        """Happy path: 202 with status='queued', task_id, agent_id."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        fake_task_id = str(uuid4())
        mock_async_result = MagicMock()
        mock_async_result.id = fake_task_id

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.run_eval_suite.apply_async",
                return_value=mock_async_result,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/trigger",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["task_id"] == fake_task_id
        assert body["agent_id"] == str(ready_agent.id)

    async def test_dispatches_celery_with_agent_id_only_not_conn_str(self):
        """CTL-08: Celery task must receive only agent_id, never conn_str."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        mock_async_result = MagicMock()
        mock_async_result.id = str(uuid4())

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.run_eval_suite.apply_async",
                return_value=mock_async_result,
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await client.post(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/trigger",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args
        # Must use kwargs= with agent_id only, and queue="runtime"
        assert call_kwargs.kwargs["queue"] == "runtime"
        task_kwargs = call_kwargs.kwargs["kwargs"]
        assert "agent_id" in task_kwargs
        assert task_kwargs["agent_id"] == str(ready_agent.id)
        # Must NOT include conn_str or neon_connection_string
        assert "conn_str" not in task_kwargs
        assert "neon_connection_string" not in task_kwargs

    async def test_returns_400_when_agent_not_ready(self):
        """400 when agent.status != 'ready' (e.g. still building)."""
        fake_tenant = _make_fake_tenant()
        building_agent = _make_ready_agent(fake_tenant)
        building_agent.status = "building"
        mock_db = _make_mock_db_returning_agent(building_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{building_agent.id}/eval-runs/trigger",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "ready" in response.json()["detail"].lower()

    async def test_returns_404_when_agent_not_found(self):
        """404 when agent doesn't exist in control DB."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_returning_none()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/eval-runs/trigger",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_404_on_cross_tenant_idor(self):
        """404 on IDOR: agent exists but belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{foreign_agent.id}/eval-runs/trigger",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_requires_api_key(self):
        """401/403 when X-API-Key header is missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/agents/{uuid4()}/eval-runs/trigger",
            )

        assert response.status_code in (401, 403)
