"""
Unit tests for the red-team read routes in apps/api/app/api/v1/red_team.py.

Tests:
    GET /api/v1/agents/{agent_id}/red-team-runs
    GET /api/v1/agents/{agent_id}/red-team-runs/{run_id}

Why this module exists (P2 review). The plan requires that every red-team run
report (attempted, valid, findings) and that the denominator be wired "through
the API responses that the ops room already reads". P2 computed the coverage and
left it in a structlog line and the Celery return dict, so these two routes —
the only red-team read surface the console has — still answered `{findings: [],
max_severity: null, deployment_blocked: false}` for a run in which four of seven
attackers could not probe at all (audit D4). That is byte-identical to a
genuinely clean seven-vector run, which is .dev/retro.md Family B's standing
rule broken on the security half: "unknown" and "pass" must never render the
same on screen.

Mock strategy mirrors test_eval_routes.py — dependency_overrides for the tenant
and the control-DB session, asyncio.to_thread patched at the route module so no
psycopg2 connection is opened.
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

pytestmark = pytest.mark.asyncio


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
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    return mock_session


_PARTIAL_COVERAGE = {
    "vectors_attempted": 7,
    "vectors_valid": 3,
    "invalid_vectors": [
        "conversation_injection",
        "data_leakage",
        "hallucination",
        "confused_deputy",
    ],
    "invalid_reason": "the conversational attacker loops cannot probe",
    "complete": False,
}


def _run_row(coverage=None, with_coverage_column=True) -> tuple:
    """One red_team_runs row in the route's SELECT order.

    with_coverage_column=False is the pre-0015 projection: eight columns, no
    coverage at all, which is what a tenant provisioned before migration 0015
    returns.
    """
    row = (
        str(uuid4()),
        "m7:agent",
        "complete",
        datetime(2026, 5, 23, 3, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 23, 3, 12, 0, tzinfo=timezone.utc),
        [],            # findings — a CLEAN run, or one nobody could run
        None,          # max_severity
        False,         # deployment_blocked
    )
    return row if not with_coverage_column else (*row, coverage)


async def _get_runs(rows, error=None) -> dict:
    """Drive GET /red-team-runs over *rows* and return the parsed body."""
    fake_tenant = _make_fake_tenant()
    agent = _make_ready_agent(fake_tenant)
    mock_db = _make_mock_db_returning_agent(agent)

    app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
    app.dependency_overrides[get_async_db] = lambda: mock_db

    side_effect = [error, rows] if error is not None else [rows]
    try:
        with (
            patch(
                "app.api.v1.red_team.fernet_decrypt",
                return_value="postgresql://fake/tenantdb",
            ),
            patch(
                "app.api.v1.red_team.asyncio.to_thread",
                new=AsyncMock(side_effect=side_effect),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent.id}/red-team-runs",
                    headers={"X-API-Key": "vrd_live_test"},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    return response.json()


class TestRedTeamRunCoverageIsReadable:
    """A run's denominator must reach the surface a human reads."""

    async def test_a_partial_coverage_run_is_not_rendered_as_clean(self):
        """The filed failure: run the red team, get zero findings, open the ops
        room. Without coverage the response is indistinguishable from a clean
        seven-vector run — and four of the seven never sent a single probe."""
        body = await _get_runs([_run_row(_PARTIAL_COVERAGE)])
        run = body["runs"][0]

        assert run["findings"] == []
        assert run["coverage_recorded"] is True
        assert run["coverage"]["vectors_attempted"] == 7
        assert run["coverage"]["vectors_valid"] == 3
        assert run["coverage"]["complete"] is False
        assert run["coverage"]["invalid_vectors"], (
            "a partial run must name the vectors that could not probe — "
            "'clean over an unnamed subset' is not a result anyone can act on"
        )

    async def test_a_clean_run_and_a_silent_run_differ_on_the_wire(self):
        """The property, stated as a comparison rather than as two fields.

        Both runs have `findings: []`. Only the coverage separates them, so the
        two payloads must not be equal anywhere a reader could stop reading.
        """
        full = dict(_PARTIAL_COVERAGE, vectors_valid=7, invalid_vectors=[], complete=True)

        silent = (await _get_runs([_run_row(_PARTIAL_COVERAGE)]))["runs"][0]
        clean = (await _get_runs([_run_row(full)]))["runs"][0]

        assert silent["findings"] == clean["findings"] == []
        assert silent["max_severity"] == clean["max_severity"]
        assert silent["deployment_blocked"] == clean["deployment_blocked"]
        assert silent["coverage"] != clean["coverage"], (
            "'nothing succeeded' and 'nothing could try' render identically"
        )

    async def test_a_run_that_recorded_nothing_says_so_rather_than_claiming_full(self):
        """NULL coverage is 'this run did not say', never 'this run covered
        everything'. Every run written before migration 0015 is in this state,
        and substituting the current build's numbers would re-describe history
        the moment P4 wires the four silent attackers."""
        body = await _get_runs([_run_row(None)])
        run = body["runs"][0]

        assert run["coverage"] is None
        assert run["coverage_recorded"] is False

    async def test_a_pre_0015_tenant_degrades_instead_of_failing(self):
        """UndefinedColumn on `coverage` falls back to the pre-0015 projection.

        A tenant one migration behind must keep reading its red-team history;
        it simply cannot see coverage, and says so through the same two keys.
        """
        body = await _get_runs(
            [_run_row(with_coverage_column=False)],
            error=psycopg2.errors.UndefinedColumn("column coverage does not exist"),
        )
        run = body["runs"][0]

        assert run["coverage"] is None
        assert run["coverage_recorded"] is False
        assert run["status"] == "complete", (
            "the rest of the run must survive a degraded coverage read"
        )

    async def test_a_non_undefined_column_failure_is_not_swallowed(self):
        """Only the missing column degrades. A genuine read failure that
        arrived as a successful degraded response would be the same fail-open
        shape audit D3 had on the eval side."""
        fake_tenant = _make_fake_tenant()
        agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.red_team.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.red_team.asyncio.to_thread",
                    new=AsyncMock(
                        side_effect=psycopg2.OperationalError("connection refused")
                    ),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    with pytest.raises(psycopg2.OperationalError):
                        await client.get(
                            f"/api/v1/agents/{agent.id}/red-team-runs",
                            headers={"X-API-Key": "vrd_live_test"},
                        )
        finally:
            app.dependency_overrides.clear()
