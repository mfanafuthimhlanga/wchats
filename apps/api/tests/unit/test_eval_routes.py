"""
Unit tests for the eval routes in apps/api/app/api/v1/evals.py.

Tests:
    GET  /api/v1/agents/{agent_id}/eval-runs
    GET  /api/v1/agents/{agent_id}/eval-runs/{run_id}/results
    POST /api/v1/agents/{agent_id}/eval-runs/trigger

Coverage:
    - Happy-path response shapes (RESEARCH.md §9)
    - Unmeasured vs measured-zero: a NULL AVG is 'unknown', never 0.0
    - IDOR prevention: 404 on agent not found, 404 on cross-tenant access
    - POST trigger: 202 on ready agent, 400 on non-ready agent, 404 on unknown agent
    - POST trigger: only agent_id dispatched to Celery (CTL-08)
    - GET routes: asyncio.to_thread + psycopg2 path mocked correctly
    - Auth: 401/403 when X-API-Key header is missing (no dependency overrides)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant

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


def _fake_eval_runs_rows() -> list[tuple]:
    """Two fake eval_run rows: one measured, one that measured nothing.

    Column order matches _LIST_EVAL_RUNS_SQL: id, started_at, finished_at,
    status, scenario_count (attempted), scored_scenario_count (valid), then the
    four metric averages. AVG is NULL when every input was NULL.
    """
    return [
        (
            str(uuid4()),                               # id
            datetime(2026, 5, 23, 2, 0, 0, tzinfo=timezone.utc),   # started_at
            datetime(2026, 5, 23, 2, 4, 0, tzinfo=timezone.utc),   # finished_at
            "complete",                                  # status
            20,                                         # scenario_count
            20,                                         # scored_scenario_count
            0.87,                                       # faithfulness
            0.91,                                       # answer_relevancy
            0.83,                                       # context_precision
            0.79,                                       # context_recall
        ),
        (
            str(uuid4()),
            datetime(2026, 5, 22, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 22, 2, 1, 0, tzinfo=timezone.utc),
            "failed",
            0,
            0,
            None,  # NULL scores on failed run
            None,
            None,
            None,
        ),
    ]


def _judge_outage_run_rows() -> list[tuple]:
    """A run that COMPLETED and measured nothing.

    The failing input behind the tri-state work: the judge LLM is down for the
    duration, Ragas returns NaN for all four metrics on all 30 scenarios,
    run_ragas_eval emits None, write_eval_results writes 120 rows with score
    NULL, and the run is marked 'complete' on production. Rendered as 0.00 it
    sits directly under yesterday's 0.94 and reads as a total quality collapse.
    """
    return [
        (
            str(uuid4()),
            datetime(2026, 5, 24, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 24, 2, 6, 0, tzinfo=timezone.utc),
            "complete",
            30,    # attempted
            0,     # valid — nothing scored
            None,
            None,
            None,
            None,
        ),
    ]


def _fake_dataset_rows(run_id: str | None = None) -> list[tuple]:
    """Per-run, per-dataset aggregate rows (P2 golden/exploratory split).

    Column order matches _LIST_EVAL_RUN_DATASETS_SQL: eval_run_id, dataset,
    scenario_count, scored_scenario_count, then the four metric averages.
    Empty by default — most list-route tests are about the run-level shape and
    a tenant with no designated golden rows is the ordinary case.
    """
    if run_id is None:
        return []
    return [
        (run_id, "golden", 10, 10, 0.93, 0.90, 0.88, 0.86),
        (run_id, "exploratory", 20, 18, 0.71, 0.70, 0.69, 0.68),
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
                    # Three round trips now: the run list, the per-dataset
                    # breakdown, then the ORRERY ledger.
                    new=AsyncMock(
                        side_effect=[fake_rows, _fake_dataset_rows(), _fake_ledger_rows()]
                    ),
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
        dataset_rows: list[tuple] | None = None,
        dataset_error: Exception | None = None,
    ) -> dict:
        """Drive GET /eval-runs over *rows* and return the parsed body.

        dataset_error stands in for a tenant DB that predates migration 0014:
        the per-dataset query raises UndefinedColumn and the route has to say
        so rather than report an empty golden bucket, which would assert
        "this run covered no golden rows" about a question it could not ask.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        second = dataset_error if dataset_error is not None else (dataset_rows or [])
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch(
                    "app.api.v1.evals.asyncio.to_thread",
                    new=AsyncMock(side_effect=[rows, second, _fake_ledger_rows()]),
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
        for metric in ("faithfulness", "answer_relevancy", "context_precision",
                       "context_recall"):
            assert run["metrics"][metric] == {"value": None, "measured": False}, (
                f"{metric} was never measured and the response claims a value"
            )

    async def test_a_measured_zero_is_distinguishable_from_an_unmeasured_one(self):
        """The two states that used to be identical on the wire."""
        measured_zero = list(_judge_outage_run_rows()[0])
        measured_zero[5] = 30            # scored_scenario_count — all valid
        measured_zero[6] = 0.0           # faithfulness genuinely averaged 0.0

        body = await self._get_runs([measured_zero])
        run = body["eval_runs"][0]

        assert run["metrics"]["faithfulness"] == {"value": 0.0, "measured": True}
        assert run["metrics"]["answer_relevancy"]["measured"] is False
        assert (
            run["metrics"]["faithfulness"] != run["metrics"]["answer_relevancy"]
        ), "a measured 0.0 and an unmeasured metric are the same on the wire"

    async def test_every_run_carries_its_validity_denominator(self):
        """A rate without its denominator must not be constructible."""
        body = await self._get_runs(_judge_outage_run_rows())
        run = body["eval_runs"][0]

        assert run["scenario_count"] == 30, "attempted"
        assert run["scored_scenario_count"] == 0, "valid"

    async def test_numeric_projection_is_retained_for_the_shipped_console(self):
        """aggregate_scores stays numeric on purpose.

        apps/admin types these `number` and calls .toFixed(2) on them
        (agents/[id]/eval/page.tsx:291); every run that predates the
        persistence fix has no eval_results rows at all, so emitting null here
        would throw on the eval page for every tenant. The honest reading is
        `metrics`; this key is a compatibility projection and the test says so
        rather than letting a future reader mistake it for a measurement.
        """
        body = await self._get_runs(_judge_outage_run_rows())
        run = body["eval_runs"][0]

        assert run["aggregate_scores"]["faithfulness"] == 0.0
        assert run["metrics"]["faithfulness"]["measured"] is False

    # -----------------------------------------------------------------
    # P2 — the golden/exploratory split. Two measurements, never one number.
    # -----------------------------------------------------------------

    async def test_golden_and_exploratory_are_reported_separately(self):
        """The two datasets travel as two blocks with their own denominators."""
        rows = _fake_eval_runs_rows()
        run_id = rows[0][0]

        body = await self._get_runs(rows, _fake_dataset_rows(run_id))
        run = body["eval_runs"][0]

        assert run["datasets"]["available"] is True
        assert run["datasets"]["golden"]["scenario_count"] == 10
        assert run["datasets"]["golden"]["scored_scenario_count"] == 10
        assert run["datasets"]["exploratory"]["scenario_count"] == 20
        assert run["datasets"]["exploratory"]["scored_scenario_count"] == 18
        assert run["datasets"]["golden"]["metrics"]["faithfulness"] == {
            "value": 0.93,
            "measured": True,
        }
        assert run["datasets"]["exploratory"]["metrics"]["faithfulness"] == {
            "value": 0.71,
            "measured": True,
        }

    async def test_the_two_dataset_scores_are_never_merged_into_one(self):
        """A golden mean and an exploratory mean are different measurements.

        The failure this forbids is silent: with golden 0.93 and exploratory
        0.71, any single number over both moves whenever the exploratory DRAW
        moves, so a redraw is indistinguishable from a regression — precisely
        the property the fixed golden set exists to provide.
        """
        rows = _fake_eval_runs_rows()
        run_id = rows[0][0]

        body = await self._get_runs(rows, _fake_dataset_rows(run_id))
        datasets = body["eval_runs"][0]["datasets"]

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

    async def test_a_run_with_no_golden_rows_reports_zero_not_absence(self):
        """An empty golden bucket is present and zeroed, never a missing key.

        A missing key has to be interpreted, and the two available readings —
        'this run covered no golden rows' and 'this response cannot say' — are
        exactly the pair the `available` flag exists to keep apart.
        """
        rows = _fake_eval_runs_rows()
        run_id = rows[0][0]

        body = await self._get_runs(
            rows, [(run_id, "exploratory", 20, 18, 0.71, 0.70, 0.69, 0.68)]
        )
        datasets = body["eval_runs"][0]["datasets"]

        assert datasets["available"] is True
        assert datasets["golden"]["scenario_count"] == 0
        assert datasets["golden"]["scored_scenario_count"] == 0
        assert all(
            metric == {"value": None, "measured": False}
            for metric in datasets["golden"]["metrics"].values()
        ), "an uncovered golden set must be unmeasured, never zero-scored"

    async def test_an_unattributable_row_joins_neither_dataset(self):
        """One run, one denominator (P2 review).

        A result row whose scenario no longer exists — a deleted scenario, or
        the synthetic id older builds minted when a scenario carried none — used
        to land in the EXPLORATORY bucket here while
        eval_service.summarise_run_validity dropped it from both. The same run
        then had two different denominators depending on who was reading, and
        the exploratory MEAN silently included a score nobody could attribute.
        Both readers now refuse to attribute it and both report it.
        """
        rows = _fake_eval_runs_rows()
        run_id = rows[0][0]

        body = await self._get_runs(
            rows,
            [
                (run_id, "golden", 10, 10, 0.93, 0.90, 0.88, 0.86),
                (run_id, "exploratory", 20, 18, 0.71, 0.70, 0.69, 0.68),
                (run_id, "unattributed", 1, 1, 0.99, 0.99, 0.99, 0.99),
            ],
        )
        datasets = body["eval_runs"][0]["datasets"]

        assert datasets["unattributed"] == {
            "scenario_count": 1,
            "scored_scenario_count": 1,
        }
        assert "metrics" not in datasets["unattributed"], (
            "a mean over rows whose scenario is unknown is a mean over an "
            "unknown denominator and must not be constructible from this "
            "response"
        )
        assert datasets["exploratory"]["scenario_count"] == 20, (
            "the unattributable row was absorbed into the exploratory count"
        )
        assert datasets["exploratory"]["metrics"]["faithfulness"]["value"] == 0.71, (
            "the unattributable row's 0.99 reached the exploratory mean"
        )

    async def test_the_query_itself_routes_an_orphan_row_to_its_own_bucket(self):
        """A source-level pin, and the honest maximum available here.

        Every test in this module mocks psycopg2 out, so none of them executes
        the CASE that decides the bucket — the rows arrive pre-bucketed. There
        is no local PostgreSQL on this machine, so the query cannot be run
        against a database at all (the `-m integration` harnesses skip, and a
        skip is unobserved, never a pass). Asserting on the SQL text is
        therefore the only evidence that exists that the orphan arm is present,
        and it is stated as such rather than dressed up as behavioural coverage.

        The GROUP BY half matters as much as the SELECT half: Postgres rejects a
        SELECT expression that is absent from GROUP BY, so the two drifting
        apart is a 500 on the ops room's first request rather than a wrong
        number.
        """
        from app.api.v1.evals import _LIST_EVAL_RUN_DATASETS_SQL

        assert _LIST_EVAL_RUN_DATASETS_SQL.count("es.id IS NULL") == 2, (
            "the orphan arm must appear in both the SELECT and the GROUP BY — "
            "without it a row whose scenario no longer exists is bucketed as "
            "exploratory and its score enters the exploratory mean"
        )
        select_half, _, group_half = _LIST_EVAL_RUN_DATASETS_SQL.partition("GROUP BY")
        for half in (select_half, group_half):
            orphan_arm = half.index("es.id IS NULL")
            golden_arm = half.index("es.dataset = %(golden)s")
            assert orphan_arm < golden_arm, (
                "the orphan arm must be tested FIRST — es.dataset is NULL for an "
                "orphan row too, so a later arm never sees it"
            )

    async def test_the_unattributed_bucket_is_present_and_zeroed_by_default(self):
        """Present-and-zero, never absent: an absent key has to be interpreted,
        and 'this run had none' is a claim worth making explicitly."""
        rows = _fake_eval_runs_rows()
        body = await self._get_runs(rows, _fake_dataset_rows(rows[0][0]))

        assert body["eval_runs"][0]["datasets"]["unattributed"] == {
            "scenario_count": 0,
            "scored_scenario_count": 0,
        }

    async def test_pre_0014_tenant_says_it_cannot_tell_rather_than_reporting_none(self):
        """UndefinedColumn on the dataset query degrades, and says it degraded.

        'This tenant designated no golden rows' and 'this tenant has no dataset
        column' produce the same empty bucket, and only `available` separates
        them. Without it, a tenant one migration behind would look like a tenant
        that had deliberately curated nothing.
        """
        rows = _fake_eval_runs_rows()
        body = await self._get_runs(
            rows,
            dataset_error=psycopg2.errors.UndefinedColumn(
                "column es.dataset does not exist"
            ),
        )
        run = body["eval_runs"][0]

        assert run["datasets"]["available"] is False
        assert run["datasets"]["golden"]["scenario_count"] == 0
        # The run-level counts come from the first query, which never touches
        # eval_scenarios — a degraded breakdown must not cost the run its
        # denominators.
        assert run["scenario_count"] == 20
        assert run["scored_scenario_count"] == 20

    async def test_a_genuine_query_failure_is_not_swallowed_as_a_missing_column(self):
        """Only UndefinedColumn degrades. Anything else must surface.

        The narrow except is the point: catching Exception here would turn a
        connection failure into a confident "this tenant predates 0014", which
        is a fabricated explanation for an outage.
        """
        rows = _fake_eval_runs_rows()
        with pytest.raises(psycopg2.OperationalError):
            await self._get_runs(
                rows,
                dataset_error=psycopg2.OperationalError("connection refused"),
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

        D-21 LOCKED gates on exactly two metrics — faithfulness AND
        answer_relevancy (see promote_to_verified_qa in eval_service.py).
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
