"""
Unit tests for POST /agents/{agent_id}/golden-scenarios and the golden writer (#56).

Writer (scenario_service.insert_authored_golden_scenario):
    1. refuses an empty question or reference answer (InvalidScenario)
    2. writes source='authored' and dataset='golden' with provenance verbatim

Batch worker (evals._register_golden_sync):
    3. skips questions already in the golden set and reports the total

Route:
    4. 404 when the agent is missing or foreign
    5. 404 when the agent has no tenant DB
    6. 422 when the writer refuses a pair
    7. 201 with counts; provenance derives from the credential kind and
       source_file, never from a request field
    8. 401 when no credential is presented
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_credential_kind, get_current_tenant
from app.api.v1.evals import _register_golden_sync
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.scenario_service import (
    InvalidScenario,
    insert_authored_golden_scenario,
)


def _make_fake_conn():
    """A psycopg2-shaped connection whose cursor records every execute."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.deleted_at = None
    return tenant


def _make_agent(tenant: Tenant, conn_string: bytes | None = b"encrypted") -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.deleted_at = None
    agent.neon_connection_string = conn_string
    return agent


async def _post_pairs(agent_id, body: dict):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            f"/api/v1/agents/{agent_id}/golden-scenarios",
            headers={"X-API-Key": "vrd_live_test"},
            json=body,
        )


# ---------------------------------------------------------------------------
# 1-2. The writer
# ---------------------------------------------------------------------------


class TestGoldenWriter:
    def test_refuses_empty_question(self):
        conn, cursor = _make_fake_conn()
        with pytest.raises(InvalidScenario):
            insert_authored_golden_scenario(
                conn, question="   ", reference_answer="a", provenance="p"
            )
        cursor.execute.assert_not_called()

    def test_refuses_empty_reference_answer(self):
        conn, cursor = _make_fake_conn()
        with pytest.raises(InvalidScenario):
            insert_authored_golden_scenario(
                conn, question="q", reference_answer="\n\t ", provenance="p"
            )
        cursor.execute.assert_not_called()

    def test_writes_authored_source_and_golden_dataset(self):
        conn, cursor = _make_fake_conn()
        scenario_id = insert_authored_golden_scenario(
            conn,
            question="What are your hours?",
            reference_answer="Nine to five.",
            provenance="authored:api_key:golden.md",
        )
        assert scenario_id
        sql, params = cursor.execute.call_args[0]
        assert "'authored'" in sql
        assert params == (
            scenario_id,
            "What are your hours?",
            "Nine to five.",
            "authored:api_key:golden.md",
            "golden",
        )
        # the caller owns the transaction: the writer must not commit
        conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# 3. The batch worker
# ---------------------------------------------------------------------------


class TestRegisterGoldenSync:
    def test_skips_known_questions_and_counts_the_total(self):
        conn, cursor = _make_fake_conn()
        cursor.fetchall.return_value = [("known question",)]
        with patch("app.api.v1.evals.psycopg2") as fake_psycopg2:
            fake_psycopg2.connect.return_value = conn
            registered, skipped, total = _register_golden_sync(
                "postgresql://x",
                [("known question", "a"), ("new question", "b")],
                "authored:api_key:inline",
            )
        assert registered == 1
        assert skipped == ["known question"]
        assert total == 2
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_rolls_back_the_whole_batch_on_failure(self):
        conn, cursor = _make_fake_conn()
        cursor.fetchall.return_value = []
        with patch("app.api.v1.evals.psycopg2") as fake_psycopg2:
            fake_psycopg2.connect.return_value = conn
            with pytest.raises(InvalidScenario):
                _register_golden_sync(
                    "postgresql://x",
                    [("good question", "a"), ("empty answer", "  ")],
                    "authored:api_key:inline",
                )
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# 4-8. The route
# ---------------------------------------------------------------------------


class TestGoldenRoute:
    async def test_missing_or_foreign_agent_is_404(self):
        tenant = _make_fake_tenant()
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            response = await _post_pairs(
                uuid4(), {"pairs": [{"question": "q", "reference_answer": "a"}]}
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 404

    async def test_agent_without_tenant_db_is_404(self):
        tenant = _make_fake_tenant()
        agent = _make_agent(tenant, conn_string=None)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            response = await _post_pairs(
                agent.id, {"pairs": [{"question": "q", "reference_answer": "a"}]}
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 404

    async def test_refused_pair_is_422(self):
        tenant = _make_fake_tenant()
        agent = _make_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_credential_kind] = lambda: "api_key"
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://x"),
                patch(
                    "app.api.v1.evals._register_golden_sync",
                    side_effect=InvalidScenario("empty pair"),
                ),
            ):
                response = await _post_pairs(
                    agent.id,
                    {"pairs": [{"question": "q", "reference_answer": "  "}]},
                )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 422

    async def test_201_with_counts_and_derived_provenance(self):
        tenant = _make_fake_tenant()
        agent = _make_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_credential_kind] = lambda: "api_key"
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://x"),
                patch(
                    "app.api.v1.evals._register_golden_sync",
                    return_value=(2, ["dup"], 3),
                ) as sync_mock,
            ):
                response = await _post_pairs(
                    agent.id,
                    {
                        "pairs": [
                            {"question": "q1", "reference_answer": "a1"},
                            {"question": "q2", "reference_answer": "a2"},
                        ],
                        "source_file": "golden.md",
                        # not a field; must be ignored, never stored
                        "authored_by": "someone else",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        assert response.json() == {
            "registered": 2,
            "skipped_duplicates": ["dup"],
            "golden_total": 3,
        }
        provenance = sync_mock.call_args[0][2]
        assert provenance == "authored:api_key:golden.md"
        assert "someone else" not in provenance

    async def test_no_credential_is_401(self):
        mock_db = AsyncMock()
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/golden-scenarios",
                    json={"pairs": [{"question": "q", "reference_answer": "a"}]},
                )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 9-10. Review edges (tier-1, findings 3 and 17)
# ---------------------------------------------------------------------------


class TestGoldenRouteReviewEdges:
    async def test_pre_0024_tenant_db_is_409(self):
        """A tenant DB provisioned before 0024 still carries the v2 CHECK;
        the refusal names the migration and the tracked class (#64)."""
        tenant = _make_fake_tenant()
        agent = _make_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_credential_kind] = lambda: "api_key"
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://x"),
                patch(
                    "app.api.v1.evals._register_golden_sync",
                    side_effect=psycopg2.errors.CheckViolation("v2 refused"),
                ),
            ):
                response = await _post_pairs(
                    agent.id,
                    {"pairs": [{"question": "q", "reference_answer": "a"}]},
                )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 409
        assert "0024" in response.json()["detail"]

    async def test_source_file_colons_cannot_forge_the_credential_field(self):
        """The colon separates provenance fields, so the caller's segment may
        not carry one: 'x:clerk_jwt:y' must not read as a credential claim."""
        tenant = _make_fake_tenant()
        agent = _make_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_credential_kind] = lambda: "api_key"
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://x"),
                patch(
                    "app.api.v1.evals._register_golden_sync",
                    return_value=(1, [], 1),
                ) as sync_mock,
            ):
                response = await _post_pairs(
                    agent.id,
                    {
                        "pairs": [{"question": "q", "reference_answer": "a"}],
                        "source_file": "x:clerk_jwt:y",
                    },
                )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 201
        provenance = sync_mock.call_args[0][2]
        assert provenance == "authored:api_key:x_clerk_jwt_y"
        assert provenance.split(":")[1] == "api_key"
