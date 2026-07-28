"""Route-level tests for app.api.v1.capability_envelopes (CAP-03, Phase 18 18-08).

Tests:
    TestListCapabilityEnvelopes
        test_list_returns_an_entry_for_every_platform_skill
        test_list_entries_carry_platform_default_and_mutating
        test_list_foreign_agent_returns_404

    TestPatchCapabilityEnvelope
        test_patch_accepts_tighten_returns_200
        test_patch_rejects_loosen_returns_422              — pinned node id
        test_patch_rejects_each_loosening_field             — parametrised, 5 fields
            (CAP-05: the `enabled` case moved out of this parametrize — it is
            no longer a loosening direction. See the four new tests below.)
        test_patch_rejects_actor_mode_off_for_mutating_skill
        test_patch_rejects_out_of_domain_actor_mode_at_the_schema
        test_patch_rejects_unknown_field
        test_patch_unknown_skill_returns_404
        test_patch_foreign_agent_returns_404                — pinned node id
        test_patch_first_write_compared_against_platform_default
        test_empty_patch_body_is_a_noop_200
        test_patch_enables_a_disabled_skill_for_every_mutating_skill  — CAP-05
        test_patch_enable_does_not_change_any_other_field             — CAP-05
        test_patch_first_write_enable_creates_row_at_platform_defaults — CAP-05
        test_enable_plus_illegal_other_field_still_rejected            — CAP-05

    test_platform_defaults_still_ship_every_skill_disabled  — module-scope, CAP-05

Mock strategy (mirrors test_deployment_routes.py):
    - FastAPI dependency_overrides for get_current_tenant and get_async_db
    - ASGITransport(app=app) for request dispatch — real route-level status
      codes and response shapes, not a service-layer stand-in (18-VALIDATION.md)
    - AsyncMock db.get() for the IDOR agent fetch; AsyncMock db.execute()
      scripted per-route (.scalars().all() for the GET list, .scalar_one_or_none()
      for the PATCH single-row lookup)
    - Every test builds expectations from PLATFORM_CAPABILITY_DEFAULTS rather
      than hard-coding a ceiling literal, so a change to the platform
      defaults cannot silently invalidate these tests
    - dependency_overrides.clear() in finally blocks to avoid test pollution
"""

from __future__ import annotations

import os
import base64

# Safety: ensure required env vars are present even if conftest is not loaded
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("PLATFORM_CREDENTIAL_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.capability_envelope import CapabilityEnvelope
from app.models.tenant import Tenant
from app.services.capability_service import PLATFORM_CAPABILITY_DEFAULTS

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> MagicMock:
    """Return a mock Tenant with a stable id."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: MagicMock, agent_id: UUID | None = None) -> MagicMock:
    """Return a mock Agent in 'ready' state belonging to the given tenant."""
    agent = MagicMock(spec=Agent)
    agent.id = agent_id or uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    return agent


def _make_envelope_row(skill: str, agent_id: UUID | None = None, **overrides) -> MagicMock:
    """Return a MagicMock(spec=CapabilityEnvelope) with a TIGHTENED baseline.

    Defaults are derived from PLATFORM_CAPABILITY_DEFAULTS[skill] so a change
    to the platform defaults cannot silently invalidate these tests: rate_limit
    matches the platform ceiling exactly (same is never a loosen), and
    max_amount_cents (when the skill has one) is pinned strictly below every
    platform ceiling (5000, vs. the lowest platform ceiling of 50000) so it is
    always a valid "current" state to tighten further from.
    """
    default = PLATFORM_CAPABILITY_DEFAULTS[skill]
    default_constraints = default.get("constraints") or {}
    if "max_amount_cents" in default_constraints:
        tightened_constraints = {"max_amount_cents": 5000}
    else:
        tightened_constraints = {}

    row = MagicMock(spec=CapabilityEnvelope)
    row.agent_id = agent_id or uuid4()
    row.skill = skill
    row.enabled = overrides.get("enabled", True)
    row.rate_limit = overrides.get("rate_limit", default["rate_limit"])
    row.constraints = overrides.get("constraints", tightened_constraints)
    row.requires_confirmation = overrides.get("requires_confirmation", False)
    row.requires_identity_verification = overrides.get("requires_identity_verification", False)
    row.actor_mode = overrides.get("actor_mode", "always-on")
    row.updated_at = overrides.get("updated_at", datetime(2026, 1, 1, tzinfo=timezone.utc))
    return row


def _make_mock_db(
    agent: MagicMock,
    envelope_rows: list[MagicMock] | None = None,
    patch_row: MagicMock | None = None,
) -> AsyncMock:
    """Return an async DB mock.

    db.get() dispatches to `agent` for the IDOR lookup. db.execute() returns
    one scripted result object supporting BOTH access shapes the two routes
    need: .scalars().all() for the GET list (returns envelope_rows) and
    .scalar_one_or_none() for the PATCH single-row lookup (returns patch_row).
    Each test only exercises one route, so scripting both on the same result
    is safe.
    """
    mock_db = AsyncMock()

    async def _fake_get(model, pk):
        if model is Agent:
            return agent
        return None

    mock_db.get.side_effect = _fake_get

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = envelope_rows or []
    mock_result.scalar_one_or_none.return_value = patch_row
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    return mock_db


# ---------------------------------------------------------------------------
# TestListCapabilityEnvelopes
# ---------------------------------------------------------------------------


class TestListCapabilityEnvelopes:
    """Tests for GET /agents/{agent_id}/capability-envelopes."""

    async def test_list_returns_an_entry_for_every_platform_skill(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)

        stored_skills = list(PLATFORM_CAPABILITY_DEFAULTS.keys())[:2]
        stored_rows = [
            _make_envelope_row(stored_skills[0], agent_id=agent_id, enabled=True),
            _make_envelope_row(stored_skills[1], agent_id=agent_id, enabled=True),
        ]
        mock_db = _make_mock_db(mock_agent, envelope_rows=stored_rows)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/capability-envelopes",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        envelopes = response.json()["envelopes"]
        assert len(envelopes) == len(PLATFORM_CAPABILITY_DEFAULTS)

        by_skill = {e["skill"]: e for e in envelopes}
        for skill in stored_skills:
            assert by_skill[skill]["enabled"] is True
            assert by_skill[skill]["constraints"] == {"max_amount_cents": 5000}

        no_row_skills = set(PLATFORM_CAPABILITY_DEFAULTS.keys()) - set(stored_skills)
        for skill in no_row_skills:
            assert by_skill[skill]["enabled"] is False
            assert by_skill[skill]["updated_at"] is None

    async def test_list_entries_carry_platform_default_and_mutating(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, envelope_rows=[])

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/capability-envelopes",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        envelopes = response.json()["envelopes"]
        by_skill = {e["skill"]: e for e in envelopes}

        for skill, entry in by_skill.items():
            assert entry["platform_default"], f"{skill} missing platform_default"
            assert isinstance(entry["mutating"], bool)

        assert by_skill["confirm_action"]["mutating"] is False

    async def test_list_foreign_agent_returns_404(self):
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(other_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, envelope_rows=[])

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/capability-envelopes",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent not found"


# ---------------------------------------------------------------------------
# TestPatchCapabilityEnvelope
# ---------------------------------------------------------------------------


class TestPatchCapabilityEnvelope:
    """Tests for PATCH /agents/{agent_id}/capability-envelopes/{skill}."""

    async def _patch(self, agent_id, skill, payload, mock_db, tenant):
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await client.patch(
                    f"/api/v1/agents/{agent_id}/capability-envelopes/{skill}",
                    json=payload,
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

    async def test_patch_accepts_tighten_returns_200(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row(
            "issue_refund",
            agent_id=agent_id,
            constraints={"max_amount_cents": 5000},
            rate_limit="5/hour",
            requires_identity_verification=False,
        )
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(
            agent_id,
            "issue_refund",
            {"constraints": {"max_amount_cents": 2000}, "requires_identity_verification": True},
            mock_db,
            fake_tenant,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["constraints"] == {"max_amount_cents": 2000}
        assert body["requires_identity_verification"] is True
        mock_db.commit.assert_awaited()

    async def test_patch_rejects_loosen_returns_422(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id, constraints={"max_amount_cents": 5000})
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(
            agent_id,
            "issue_refund",
            {"constraints": {"max_amount_cents": 20000}},
            mock_db,
            fake_tenant,
        )

        assert response.status_code == 422
        assert "loosen_max_amount_cents" in response.json()["detail"]
        # Load-bearing: a route that wrote the row and then raised would
        # still return 422 and a status-code-only assertion would pass.
        mock_db.commit.assert_not_awaited()

    @pytest.mark.parametrize(
        "current_overrides,payload",
        [
            ({"rate_limit": "5/hour"}, {"rate_limit": "10/hour"}),
            ({"constraints": {"max_amount_cents": 5000}}, {"constraints": {"max_amount_cents": 20000}}),
            ({"requires_confirmation": True}, {"requires_confirmation": False}),
            (
                {"requires_identity_verification": True},
                {"requires_identity_verification": False},
            ),
            ({"actor_mode": "always-on"}, {"actor_mode": "sample_at_rate_10"}),
        ],
        ids=[
            "rate_limit",
            "max_amount_cents",
            "requires_confirmation",
            "requires_identity_verification",
            "actor_mode",
        ],
    )
    async def test_patch_rejects_each_loosening_field(self, current_overrides, payload):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id, **current_overrides)
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, "issue_refund", payload, mock_db, fake_tenant)

        assert response.status_code == 422
        mock_db.commit.assert_not_awaited()

    async def test_patch_rejects_actor_mode_off_for_mutating_skill(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, "issue_refund", {"actor_mode": "off"}, mock_db, fake_tenant)

        assert response.status_code == 422
        assert "actor_mode_off_requires_non_mutating" in response.json()["detail"]
        mock_db.commit.assert_not_awaited()

    async def test_patch_rejects_out_of_domain_actor_mode_at_the_schema(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, "issue_refund", {"actor_mode": "sampled"}, mock_db, fake_tenant)

        assert response.status_code == 422
        mock_db.commit.assert_not_awaited()

    async def test_patch_rejects_unknown_field(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, "issue_refund", {"max_amount": 1}, mock_db, fake_tenant)

        assert response.status_code == 422
        mock_db.commit.assert_not_awaited()

    async def test_patch_unknown_skill_returns_404(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, patch_row=None)

        response = await self._patch(
            agent_id, "not_a_real_skill", {"enabled": False}, mock_db, fake_tenant
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Skill not found"
        mock_db.commit.assert_not_awaited()

    async def test_patch_foreign_agent_returns_404(self):
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(other_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id, enabled=False)
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        # A LOOSENING body — a handler that checked the body before ownership
        # would return 422 and fail this test.
        response = await self._patch(agent_id, "issue_refund", {"enabled": True}, mock_db, fake_tenant)

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent not found"
        mock_db.commit.assert_not_awaited()

    async def test_patch_first_write_compared_against_platform_default(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        platform_ceiling = PLATFORM_CAPABILITY_DEFAULTS["issue_refund"]["constraints"]["max_amount_cents"]

        # No stored row.
        mock_db_above = _make_mock_db(mock_agent, patch_row=None)
        response_above = await self._patch(
            agent_id,
            "issue_refund",
            {"constraints": {"max_amount_cents": platform_ceiling + 10_000}},
            mock_db_above,
            fake_tenant,
        )
        assert response_above.status_code == 422
        mock_db_above.commit.assert_not_awaited()

        mock_db_below = _make_mock_db(mock_agent, patch_row=None)
        response_below = await self._patch(
            agent_id,
            "issue_refund",
            {"constraints": {"max_amount_cents": platform_ceiling - 10_000}},
            mock_db_below,
            fake_tenant,
        )
        assert response_below.status_code == 200
        mock_db_below.commit.assert_awaited()

    async def test_empty_patch_body_is_a_noop_200(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row("issue_refund", agent_id=agent_id, constraints={"max_amount_cents": 5000})
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, "issue_refund", {}, mock_db, fake_tenant)

        assert response.status_code == 200
        body = response.json()
        assert body["constraints"] == {"max_amount_cents": 5000}
        assert body["rate_limit"] == row.rate_limit
        mock_db.commit.assert_not_awaited()

    # -----------------------------------------------------------------------
    # CAP-05: enabled is now an owner-controlled authorization toggle, not a
    # tightness dimension. These four tests are the route-level proof that
    # the enable transition is reachable, and — the phase's central claim —
    # that enabling a skill loosens nothing else on the row.
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "skill",
        [s for s, d in PLATFORM_CAPABILITY_DEFAULTS.items() if d.get("mutating")],
    )
    async def test_patch_enables_a_disabled_skill_for_every_mutating_skill(self, skill):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row(skill, agent_id=agent_id, enabled=False)
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, skill, {"enabled": True}, mock_db, fake_tenant)

        assert response.status_code == 200
        assert response.json()["enabled"] is True
        mock_db.commit.assert_awaited()

    async def test_patch_enable_does_not_change_any_other_field(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        row = _make_envelope_row(
            "issue_refund",
            agent_id=agent_id,
            enabled=False,
            rate_limit="1/day",
            constraints={"max_amount_cents": 100},
            requires_confirmation=True,
            requires_identity_verification=True,
            actor_mode="always-on",
        )
        captured = {
            "rate_limit": row.rate_limit,
            "constraints": dict(row.constraints),
            "requires_confirmation": row.requires_confirmation,
            "requires_identity_verification": row.requires_identity_verification,
            "actor_mode": row.actor_mode,
        }
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(agent_id, "issue_refund", {"enabled": True}, mock_db, fake_tenant)

        assert response.status_code == 200
        assert row.enabled is True
        assert row.rate_limit == captured["rate_limit"]
        assert row.constraints == captured["constraints"]
        assert row.requires_confirmation == captured["requires_confirmation"]
        assert row.requires_identity_verification == captured["requires_identity_verification"]
        assert row.actor_mode == captured["actor_mode"]

    async def test_patch_first_write_enable_creates_row_at_platform_defaults(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent, patch_row=None)
        default = PLATFORM_CAPABILITY_DEFAULTS["issue_refund"]

        response = await self._patch(agent_id, "issue_refund", {"enabled": True}, mock_db, fake_tenant)

        assert response.status_code == 200
        mock_db.add.assert_called_once()
        added_row = mock_db.add.call_args[0][0]
        assert added_row.rate_limit == default["rate_limit"]
        assert added_row.constraints == default["constraints"]
        assert added_row.requires_confirmation == default["requires_confirmation"]
        assert added_row.requires_identity_verification == default["requires_identity_verification"]
        assert added_row.actor_mode == default["actor_mode"]

    async def test_enable_plus_illegal_other_field_still_rejected(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        platform_ceiling = PLATFORM_CAPABILITY_DEFAULTS["issue_refund"]["constraints"]["max_amount_cents"]
        row = _make_envelope_row(
            "issue_refund",
            agent_id=agent_id,
            enabled=False,
            constraints={"max_amount_cents": platform_ceiling},
        )
        mock_db = _make_mock_db(mock_agent, patch_row=row)

        response = await self._patch(
            agent_id,
            "issue_refund",
            {"enabled": True, "constraints": {"max_amount_cents": platform_ceiling + 1}},
            mock_db,
            fake_tenant,
        )

        assert response.status_code == 422
        assert "loosen_max_amount_cents" in response.json()["detail"]
        mock_db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Module-scope: fail-closed platform posture (CAP-05)
# ---------------------------------------------------------------------------


def test_platform_defaults_still_ship_every_skill_disabled():
    """CAP-05 changes what an owner may reach, never what a newly-provisioned
    agent starts with. Every platform default must still ship enabled=False."""
    assert all(entry.get("enabled") is False for entry in PLATFORM_CAPABILITY_DEFAULTS.values())
