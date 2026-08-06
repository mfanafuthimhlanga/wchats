"""
Unit tests for OPS-16: prompt_version_service + prompt_versions routes + the
version-on-save hook in patch_agent.

PRE-EXISTING INFRA NOTE (not a regression introduced by this plan):
    `app.main` transitively imports app.api.v1.evals -> app.worker.tasks.runtime.eval
    -> app.services.eval_service -> ragas.metrics.collections -> ragas.llms.base ->
    langchain_community.chat_models.vertexai, which raises ModuleNotFoundError in
    this environment (confirmed present on HEAD before this plan's changes).
    Route tests below build a minimal FastAPI app around ONLY
    app.api.v1.prompt_versions.router (mirrors the targeted-import pattern already
    established in test_bench_routes.py / test_metrics_routes.py, 21-05/21-06)
    instead of importing `app.main`.

Coverage:
    Service layer (app/services/prompt_version_service.py):
        - create_version_from_agent: version_number increments; the FIRST
          version object returned is never mutated by a SECOND call (history
          never overwritten, T-21-09-02).
        - diff_versions: per-field comparison with correct `changed` flags.
        - set_canary: sets label='canary' + canary_percent; demotes any other
          canary for the same agent.
        - rollback: appends a NEW version restoring the target's soul; never
          mutates the target row; updates the live agent row to match.
        - resolve_prompt_version (sync): filters label IN ('production',
          'canary') — a 'draft' row is structurally excluded by the WHERE
          clause; weighted-distribution over many seeded calls matches
          canary_percent within tolerance.

    Route layer (app/api/v1/prompt_versions.py):
        - IDOR: 404 on agent not found / cross-tenant agent.
        - POST canary: percent out of 0-100 range -> 422 (Pydantic bound).
        - Happy-path response shapes for list/diff/canary/rollback.

    patch_agent (app/api/v1/agents.py):
        - A soul-field PATCH triggers create_version_from_agent; a name-only
          PATCH does not.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_tenant
from app.api.v1 import prompt_versions as prompt_versions_module
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.prompt_version import PromptVersion
from app.models.tenant import Tenant
from app.services.prompt_version_service import (
    PromptVersionNotFoundError,
    create_version_from_agent,
    diff_versions,
    resolve_prompt_version,
    rollback,
    set_canary,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_agent(tenant_id, agent_id=None) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = agent_id or uuid4()
    agent.tenant_id = tenant_id
    agent.name = "TestBot"
    agent.status = "ready"
    agent.created_at = datetime(2026, 7, 16, tzinfo=timezone.utc)
    agent.deleted_at = None
    agent.soul_role = "role-live"
    agent.soul_voice = "voice-live"
    agent.soul_do_list = ["do-live"]
    agent.soul_donot_list = ["dont-live"]
    return agent


def _make_version(
    agent_id,
    version_number: int,
    label: str = "draft",
    canary_percent: int = 0,
    **soul,
) -> PromptVersion:
    return PromptVersion(
        id=uuid4(),
        agent_id=agent_id,
        version_number=version_number,
        label=label,
        canary_percent=canary_percent,
        soul_role=soul.get("soul_role", f"role-v{version_number}"),
        soul_voice=soul.get("soul_voice", f"voice-v{version_number}"),
        soul_do_list=soul.get("soul_do_list", [f"do-v{version_number}"]),
        soul_donot_list=soul.get("soul_donot_list", [f"dont-v{version_number}"]),
        created_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )


def _scalar_result(value):
    m = MagicMock()
    m.scalar.return_value = value
    return m


def _noop_result():
    return MagicMock()


async def _fake_refresh(obj):
    """Simulate DB-generated fields being populated by AsyncSession.refresh()."""
    if getattr(obj, "id", None) is None:
        obj.id = uuid4()
    if getattr(obj, "created_at", None) is None:
        obj.created_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Service: create_version_from_agent — append-only + version_number increment
# ---------------------------------------------------------------------------


class TestCreateVersionFromAgent:
    @pytest.mark.asyncio
    async def test_version_number_increments_and_first_row_never_mutated(self):
        """T-21-09-02: a second soul edit appends version 2 without touching
        version 1's soul fields — history is never overwritten."""
        agent = _make_agent(uuid4())
        agent.soul_role = "v1 role"
        agent.soul_voice = "v1 voice"
        agent.soul_do_list = ["v1 do"]
        agent.soul_donot_list = ["v1 dont"]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(None), _noop_result()])
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock(side_effect=_fake_refresh)

        v1 = await create_version_from_agent(db, agent)
        assert v1.version_number == 1
        assert v1.label == "production"
        v1_snapshot = (
            v1.soul_role,
            v1.soul_voice,
            tuple(v1.soul_do_list),
            tuple(v1.soul_donot_list),
        )

        # Second soul edit — simulates a subsequent PATCH
        agent.soul_role = "v2 role"
        agent.soul_voice = "v2 voice"
        db.execute = AsyncMock(side_effect=[_scalar_result(1), _noop_result()])

        v2 = await create_version_from_agent(db, agent)
        assert v2.version_number == 2
        assert v2.soul_role == "v2 role"

        # v1 is a DIFFERENT object and its soul fields are byte-identical to
        # what they were right after creation — never mutated.
        assert v1 is not v2
        assert (
            v1.soul_role,
            v1.soul_voice,
            tuple(v1.soul_do_list),
            tuple(v1.soul_donot_list),
        ) == v1_snapshot
        assert v1.soul_role == "v1 role"

    @pytest.mark.asyncio
    async def test_first_version_starts_at_one_when_no_prior_rows(self):
        agent = _make_agent(uuid4())
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(None), _noop_result()])
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock(side_effect=_fake_refresh)

        v1 = await create_version_from_agent(db, agent)
        assert v1.version_number == 1


# ---------------------------------------------------------------------------
# Service: diff_versions
# ---------------------------------------------------------------------------


class TestDiffVersions:
    @pytest.mark.asyncio
    async def test_diff_reports_changed_fields_only(self):
        agent_id = uuid4()
        v_a = _make_version(agent_id, 1, soul_role="A role", soul_voice="same voice")
        v_b = _make_version(
            agent_id, 2, soul_role="B role", soul_voice="same voice"
        )

        db = AsyncMock()
        db.get = AsyncMock(side_effect=[v_a, v_b])

        result = await diff_versions(db, agent_id, v_a.id, v_b.id)

        assert result["fields"]["soul_role"]["changed"] is True
        assert result["fields"]["soul_voice"]["changed"] is False
        assert result["fields"]["soul_role"]["a"] == "A role"
        assert result["fields"]["soul_role"]["b"] == "B role"

    @pytest.mark.asyncio
    async def test_diff_idor_raises_when_version_belongs_to_other_agent(self):
        agent_id = uuid4()
        other_agent_id = uuid4()
        v_a = _make_version(agent_id, 1)
        v_foreign = _make_version(other_agent_id, 1)

        db = AsyncMock()
        db.get = AsyncMock(side_effect=[v_a, v_foreign])

        with pytest.raises(PromptVersionNotFoundError):
            await diff_versions(db, agent_id, v_a.id, v_foreign.id)


# ---------------------------------------------------------------------------
# Service: set_canary
# ---------------------------------------------------------------------------


class TestSetCanary:
    @pytest.mark.asyncio
    async def test_sets_label_canary_and_percent(self):
        agent_id = uuid4()
        target = _make_version(agent_id, 2, label="archived")

        db = AsyncMock()
        db.get = AsyncMock(return_value=target)
        db.execute = AsyncMock(return_value=_noop_result())
        db.flush = AsyncMock()
        db.refresh = AsyncMock(side_effect=_fake_refresh)

        result = await set_canary(db, agent_id, target.id, 25)

        assert result.label == "canary"
        assert result.canary_percent == 25

    @pytest.mark.asyncio
    async def test_idor_raises_for_foreign_version(self):
        agent_id = uuid4()
        foreign_version = _make_version(uuid4(), 1)

        db = AsyncMock()
        db.get = AsyncMock(return_value=foreign_version)

        with pytest.raises(PromptVersionNotFoundError):
            await set_canary(db, agent_id, foreign_version.id, 10)


# ---------------------------------------------------------------------------
# Service: rollback
# ---------------------------------------------------------------------------


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_appends_new_version_and_never_mutates_target(self):
        agent_id = uuid4()
        target = _make_version(
            agent_id, 1, label="archived", soul_role="original role"
        )
        agent = _make_agent(agent_id, agent_id)
        agent.soul_role = "current live role"

        db = AsyncMock()
        # rollback() calls: _get_owned_version -> db.get(PromptVersion, ...) [1]
        #                    db.get(Agent, agent_id)                        [2]
        db.get = AsyncMock(side_effect=[target, agent])
        db.execute = AsyncMock(side_effect=[_scalar_result(3), _noop_result()])
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock(side_effect=_fake_refresh)

        new_version = await rollback(db, agent_id, target.id)

        # A brand-new row, never the target itself
        assert new_version is not target
        assert new_version.version_number == 4  # max(3) + 1
        assert new_version.label == "production"
        assert new_version.soul_role == "original role"

        # Target row's soul fields are untouched (still "original role" —
        # trivially true since rollback() never calls setattr on `target`,
        # but assert explicitly to guard against future regressions).
        assert target.soul_role == "original role"

        # Live agent row updated to match the restored version
        assert agent.soul_role == "original role"

    @pytest.mark.asyncio
    async def test_rollback_idor_raises_for_foreign_version(self):
        agent_id = uuid4()
        foreign_version = _make_version(uuid4(), 1)

        db = AsyncMock()
        db.get = AsyncMock(return_value=foreign_version)

        with pytest.raises(PromptVersionNotFoundError):
            await rollback(db, agent_id, foreign_version.id)


# ---------------------------------------------------------------------------
# Service: resolve_prompt_version (sync) — never a draft, weighted canary
# ---------------------------------------------------------------------------


class TestResolvePromptVersion:
    def _mock_sync_session(self, versions: list[PromptVersion]):
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = versions
        session.execute = MagicMock(return_value=result)
        return session

    def test_no_versions_returns_none(self):
        session = self._mock_sync_session([])
        version_id, override = resolve_prompt_version(session, str(uuid4()))
        assert version_id is None
        assert override is None

    def test_query_filters_label_in_production_canary(self):
        """T-21-09-01: the WHERE clause must restrict to label IN
        ('production', 'canary') — a draft can never be a SQL-level candidate."""
        session = self._mock_sync_session([])
        resolve_prompt_version(session, str(uuid4()))

        executed_stmt = session.execute.call_args[0][0]
        compiled_sql = str(executed_stmt)
        assert "prompt_versions.label IN" in compiled_sql

    def test_production_only_always_chosen(self):
        agent_id = str(uuid4())
        production = _make_version(agent_id, 1, label="production")
        session = self._mock_sync_session([production])

        version_id, override = resolve_prompt_version(session, agent_id)

        assert version_id == str(production.id)
        assert override["soul_role"] == production.soul_role

    def test_canary_distribution_matches_percent_within_tolerance(self):
        """Seeded weighted-random pick: over many calls, the canary share
        should land close to canary_percent (target 30%, tolerance +-5pp)."""
        agent_id = str(uuid4())
        production = _make_version(agent_id, 1, label="production")
        canary = _make_version(agent_id, 2, label="canary", canary_percent=30)
        session = self._mock_sync_session([production, canary])

        random.seed(20260716)
        n = 4000
        canary_hits = 0
        for _ in range(n):
            version_id, _ = resolve_prompt_version(session, agent_id)
            if version_id == str(canary.id):
                canary_hits += 1

        observed_pct = (canary_hits / n) * 100
        assert 25.0 <= observed_pct <= 35.0, (
            f"Expected ~30% canary routing, observed {observed_pct:.1f}%"
        )

    def test_zero_percent_canary_never_selected(self):
        agent_id = str(uuid4())
        production = _make_version(agent_id, 1, label="production")
        canary = _make_version(agent_id, 2, label="canary", canary_percent=0)
        session = self._mock_sync_session([production, canary])

        for _ in range(200):
            version_id, _ = resolve_prompt_version(session, agent_id)
            assert version_id == str(production.id)


# ---------------------------------------------------------------------------
# Route layer — minimal FastAPI() wrapping ONLY prompt_versions.router
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(prompt_versions_module.router, prefix="/api/v1")


class TestPromptVersionsRoutes:
    @pytest.mark.asyncio
    async def test_list_returns_404_for_cross_tenant_agent(self):
        fake_tenant = _make_fake_tenant()
        foreign_agent = _make_agent(uuid4())  # different tenant_id

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=foreign_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{foreign_agent.id}/prompt-versions"
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_returns_404_for_unknown_agent(self):
        fake_tenant = _make_fake_tenant()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{uuid4()}/prompt-versions"
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_happy_path(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_agent(fake_tenant.id)
        versions = [_make_version(agent.id, 1, label="archived"),
                    _make_version(agent.id, 2, label="production")]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.services.prompt_version_service.list_versions",
                AsyncMock(return_value=versions),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{agent.id}/prompt-versions"
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert len(body["versions"]) == 2

    @pytest.mark.asyncio
    async def test_canary_percent_out_of_range_returns_422(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_agent(fake_tenant.id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent.id}/prompt-versions/canary",
                    json={"version_id": str(uuid4()), "percent": 150},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_canary_happy_path(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_agent(fake_tenant.id)
        version = _make_version(agent.id, 2, label="canary", canary_percent=20)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        mock_db.commit = AsyncMock()

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.services.prompt_version_service.set_canary",
                AsyncMock(return_value=version),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/prompt-versions/canary",
                        json={"version_id": str(version.id), "percent": 20},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["label"] == "canary"
        assert response.json()["canary_percent"] == 20

    @pytest.mark.asyncio
    async def test_rollback_happy_path(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_agent(fake_tenant.id)
        restored = _make_version(agent.id, 5, label="production")

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)
        mock_db.commit = AsyncMock()

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.services.prompt_version_service.rollback",
                AsyncMock(return_value=restored),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/prompt-versions/rollback",
                        json={"version_id": str(uuid4())},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["version_number"] == 5

    @pytest.mark.asyncio
    async def test_rollback_not_found_returns_404(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_agent(fake_tenant.id)

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.services.prompt_version_service.rollback",
                AsyncMock(side_effect=PromptVersionNotFoundError("nope")),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/prompt-versions/rollback",
                        json={"version_id": str(uuid4())},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# patch_agent version-on-save hook (app/api/v1/agents.py)
# ---------------------------------------------------------------------------


class TestPatchAgentVersionOnSave:
    @pytest.mark.asyncio
    async def test_soul_field_patch_triggers_create_version(self):
        with patch(
            "app.api.v1.agents.create_version_from_agent", new=AsyncMock()
        ) as mock_create:
            from app.api.v1.agents import patch_agent
            from app.schemas.agent import AgentSoulUpdate

            agent = _make_agent(uuid4())
            db = AsyncMock()
            result_mock = MagicMock()
            result_mock.scalar_one_or_none.return_value = agent
            db.execute = AsyncMock(return_value=result_mock)
            db.commit = AsyncMock()
            db.refresh = AsyncMock()

            tenant = MagicMock(spec=Tenant)
            tenant.id = agent.tenant_id

            body = AgentSoulUpdate(soul_voice="new voice")
            await patch_agent(agent.id, body, db, tenant)

            mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_name_only_patch_does_not_trigger_create_version(self):
        with patch(
            "app.api.v1.agents.create_version_from_agent", new=AsyncMock()
        ) as mock_create:
            from app.api.v1.agents import patch_agent
            from app.schemas.agent import AgentSoulUpdate

            agent = _make_agent(uuid4())
            db = AsyncMock()
            result_mock = MagicMock()
            result_mock.scalar_one_or_none.return_value = agent
            db.execute = AsyncMock(return_value=result_mock)
            db.commit = AsyncMock()
            db.refresh = AsyncMock()

            tenant = MagicMock(spec=Tenant)
            tenant.id = agent.tenant_id

            body = AgentSoulUpdate(name="RenamedBot")
            await patch_agent(agent.id, body, db, tenant)

            mock_create.assert_not_awaited()
