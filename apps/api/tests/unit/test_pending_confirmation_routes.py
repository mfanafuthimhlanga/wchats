"""Route-level tests for app.api.v1.pending_confirmations (ACT-07, Phase 22 22-03).

Tests:
    TestResolveRoute
        test_approve_enqueues_exactly_once
        test_reject_never_enqueues
        test_second_resolve_returns_409_and_never_enqueues   — pinned node id
        test_expired_row_is_forced_to_expired_and_never_enqueues — pinned node id
        test_commit_precedes_enqueue                          — pinned node id
        test_confirm_action_row_resolves_without_enqueue      — pinned node id
        test_body_rejects_an_action_payload

    TestOwnership
        test_both_routes_404_on_foreign_agent                 — pinned node id, parametrised

    TestQueueList
        test_empty_queue_returns_empty_list                   — pinned node id
        test_row_with_null_arguments_and_no_deadline_serialises
        test_ordering_is_total_and_stable                     — pinned node id

    TestExecutionOutcome
        test_outcome_ignores_the_original_require_human_audit_row — pinned node id
        test_executed_when_audit_error_is_null
        test_not_executed_carries_the_raw_denial_string
        test_no_audit_row_yields_awaiting_execution
        test_missing_idempotency_key_skips_the_lookup

Mock strategy (mirrors test_capability_routes.py):
    - FastAPI dependency_overrides for get_current_tenant and get_async_db
    - ASGITransport(app=app) for request dispatch — real route-level status
      codes and response shapes, not a service-layer stand-in
    - AsyncMock db.get() for the IDOR agent fetch; AsyncMock db.execute()
      scripted per test on top of a shared MagicMock Result stand-in
      supporting BOTH .mappings().all() (the GET list, _execution_outcome_for)
      and .mappings().first() (the resolve claim)
    - The Celery dispatch is patched at its own module
      (app.worker.tasks.runtime.confirmations.resolve_approved_confirmation)
      — the exact name the route's local import resolves it under, not a
      name inside pending_confirmations itself, since the import is local
      to the function body
    - Several assertions read the ACTUAL SQL text sent to db.execute() (via
      mock_db.execute.call_args), not just the mocked return value. This is
      deliberate: a mocked DB boundary can't exercise a live claim/predicate,
      so the guard-removal demonstrations for the atomic claim (T-22-ACT-03)
      and the execution-outcome discriminator (T-22-ACT-11) mutate the real
      SQL string in the module and rely on these text assertions to go red
      — a status-code-only assertion would stay green regardless of the SQL
      the route actually sent.
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

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_current_tenant
from app.api.v1 import pending_confirmations as pc
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.transactional.schemas import SKILL_INPUT_MODELS

MUTATING_SKILL = "issue_refund"
assert MUTATING_SKILL in SKILL_INPUT_MODELS
NON_MUTATING_SKILL = "confirm_action"
assert NON_MUTATING_SKILL not in SKILL_INPUT_MODELS

_DISPATCH_TARGET = "app.worker.tasks.runtime.confirmations.resolve_approved_confirmation"


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


def _mock_result(rows: list[dict] | None = None, single: dict | None = None) -> MagicMock:
    """A scripted SQLAlchemy Result stand-in for both call shapes this
    module's routes use: .mappings().all() (GET list) and
    .mappings().first() (the resolve claim, and _execution_outcome_for)."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows or []
    result.mappings.return_value.first.return_value = single
    return result


def _claim_row(
    skill: str = MUTATING_SKILL,
    resolution: str = "approved",
    row_id: UUID | None = None,
    arguments: dict | None = None,
    expires_at: datetime | None = None,
) -> dict:
    """A row dict shaped exactly like the claim UPDATE's RETURNING columns.
    Always represents an already-claimed (i.e. resolved) row."""
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    return {
        "id": row_id or uuid4(),
        "skill": skill,
        "arguments": arguments if arguments is not None else {"idempotency_key": "key-1", "order_id": "o-1"},
        "requested_at": now - timedelta(hours=1),
        "expires_at": expires_at,
        "resolved_at": now,
        "resolution": resolution,
    }


def _make_db_for_agent(agent: MagicMock) -> AsyncMock:
    """An AsyncMock db whose .get() dispatches only the IDOR agent lookup.
    .execute() and .commit() are scripted per test on top of this."""
    mock_db = AsyncMock()

    async def _fake_get(model, pk):
        if model is Agent:
            return agent
        return None

    mock_db.get.side_effect = _fake_get
    mock_db.commit = AsyncMock()
    return mock_db


async def _get(agent_id: UUID, mock_db, tenant):
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_async_db] = lambda: mock_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(
                f"/api/v1/agents/{agent_id}/pending-confirmations",
                headers={"X-API-Key": "vrd_live_test"},
            )
    finally:
        app.dependency_overrides.clear()


async def _resolve(agent_id: UUID, confirmation_id, payload: dict, mock_db, tenant):
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_async_db] = lambda: mock_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                f"/api/v1/agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve",
                json=payload,
                headers={"X-API-Key": "vrd_live_test"},
            )
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TestResolveRoute
# ---------------------------------------------------------------------------


class TestResolveRoute:
    """Tests for POST /agents/{agent_id}/pending-confirmations/{id}/resolve."""

    async def test_approve_enqueues_exactly_once(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        row = _claim_row(skill=MUTATING_SKILL, resolution="approved", row_id=confirmation_id)
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))

        with patch(_DISPATCH_TARGET) as mock_task:
            response = await _resolve(agent_id, confirmation_id, {"resolution": "approved"}, mock_db, fake_tenant)

        assert response.status_code == 200
        assert response.json()["resolution"] == "approved"
        mock_task.delay.assert_called_once_with(str(confirmation_id))

    async def test_reject_never_enqueues(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        row = _claim_row(skill=MUTATING_SKILL, resolution="rejected", row_id=confirmation_id)
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))

        with patch(_DISPATCH_TARGET) as mock_task:
            response = await _resolve(agent_id, confirmation_id, {"resolution": "rejected"}, mock_db, fake_tenant)

        assert response.status_code == 200
        assert response.json()["resolution"] == "rejected"
        mock_task.delay.assert_not_called()

    async def test_second_resolve_returns_409_and_never_enqueues(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        # No row returned — simulates an already-resolved row: the UPDATE's
        # own WHERE resolved_at IS NULL clause matched nothing.
        mock_db.execute = AsyncMock(return_value=_mock_result(single=None))

        with patch(_DISPATCH_TARGET) as mock_task:
            response = await _resolve(agent_id, confirmation_id, {"resolution": "approved"}, mock_db, fake_tenant)

        assert response.status_code == 409
        mock_task.delay.assert_not_called()
        mock_db.commit.assert_not_awaited()

        # Guard-removal demonstration (b), T-22-ACT-03: the claim's entire
        # concurrency control is this literal WHERE-clause text. A mutation
        # dropping it from the real module's source turns this assertion red
        # even though the mocked return value above is unchanged.
        sent_query = str(mock_db.execute.call_args[0][0])
        assert "resolved_at IS NULL" in sent_query

    async def test_expired_row_is_forced_to_expired_and_never_enqueues(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        # The claim SQL's own CASE forces 'expired' server-side; the mock
        # returns exactly what that statement returns for an expired row,
        # regardless of what the caller requested below.
        row = _claim_row(skill=MUTATING_SKILL, resolution="expired", row_id=confirmation_id)
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))

        with patch(_DISPATCH_TARGET) as mock_task:
            response = await _resolve(agent_id, confirmation_id, {"resolution": "approved"}, mock_db, fake_tenant)

        assert response.status_code == 200
        assert response.json()["resolution"] == "expired"
        mock_task.delay.assert_not_called()

    async def test_commit_precedes_enqueue(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        row = _claim_row(skill=MUTATING_SKILL, resolution="approved", row_id=confirmation_id)
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))

        parent = MagicMock()
        parent.attach_mock(mock_db.commit, "commit")

        with patch(_DISPATCH_TARGET) as mock_task:
            parent.attach_mock(mock_task.delay, "delay")
            response = await _resolve(agent_id, confirmation_id, {"resolution": "approved"}, mock_db, fake_tenant)

        assert response.status_code == 200
        call_names = [c[0] for c in parent.mock_calls]
        assert "commit" in call_names
        assert "delay" in call_names
        # Behavioural ordering assertion — not a source-text scan. A refactor
        # that reorders the two calls without changing their literal text
        # would still turn this red.
        assert call_names.index("commit") < call_names.index("delay")

    async def test_confirm_action_row_resolves_without_enqueue(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        row = _claim_row(skill=NON_MUTATING_SKILL, resolution="approved", row_id=confirmation_id, arguments={})
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))

        with patch(_DISPATCH_TARGET) as mock_task:
            response = await _resolve(agent_id, confirmation_id, {"resolution": "approved"}, mock_db, fake_tenant)

        assert response.status_code == 200
        assert response.json()["resolution"] == "approved"
        mock_task.delay.assert_not_called()

    async def test_confirm_action_shaped_mutating_row_refused_with_422_never_enqueued(self):
        """CR-02: a row written by confirm_action_tool carries `skill` = the
        TARGET mutating skill (a key of SKILL_INPUT_MODELS — the OLD dispatch
        condition alone would enqueue it) but `arguments` = only
        `{"action_reference": ...}`, never the full argument set the
        resolver's re-validation requires. This exact row shape — the one
        confirm_action_tool actually produces (tools.py:949), NOT the
        unreachable skill="confirm_action" literal shape tested above — must
        be refused at approve-time with a 422 and never dispatched."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        row = _claim_row(
            skill=MUTATING_SKILL,
            resolution="approved",
            row_id=confirmation_id,
            arguments={"action_reference": "ref-1"},
        )
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))

        with (
            patch(_DISPATCH_TARGET) as mock_task,
            patch("app.api.v1.pending_confirmations.write_audit_row") as mock_audit,
        ):
            mock_audit.return_value = None
            response = await _resolve(agent_id, confirmation_id, {"resolution": "approved"}, mock_db, fake_tenant)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert MUTATING_SKILL in detail
        mock_task.delay.assert_not_called()
        # The claim itself still committed — only the dispatch was skipped.
        mock_db.commit.assert_awaited_once()
        mock_audit.assert_awaited_once()
        assert mock_audit.await_args.kwargs["error"] == "confirmation.incomplete_arguments"
        assert mock_audit.await_args.kwargs["arguments"] == {"action_reference": "ref-1"}

    async def test_body_rejects_an_action_payload(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        confirmation_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        mock_db.execute = AsyncMock(return_value=_mock_result(single=None))

        with patch(_DISPATCH_TARGET) as mock_task:
            response = await _resolve(
                agent_id,
                confirmation_id,
                {"resolution": "approved", "amount_cents": 1},
                mock_db,
                fake_tenant,
            )

        assert response.status_code == 422
        mock_db.execute.assert_not_called()
        mock_task.delay.assert_not_called()


# ---------------------------------------------------------------------------
# TestOwnership
# ---------------------------------------------------------------------------


class TestOwnership:
    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_both_routes_404_on_foreign_agent(self, method):
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(other_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        mock_db.execute = AsyncMock(return_value=_mock_result(single=None))

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                if method == "get":
                    response = await client.get(
                        f"/api/v1/agents/{agent_id}/pending-confirmations",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
                else:
                    confirmation_id = uuid4()
                    # A body that would resolve on its own merits — a
                    # handler checking the body before ownership would
                    # return 200/409, not 404, and fail this test.
                    response = await client.post(
                        f"/api/v1/agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve",
                        json={"resolution": "approved"},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent not found"
        mock_db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# TestQueueList
# ---------------------------------------------------------------------------


class TestQueueList:
    """Tests for GET /agents/{agent_id}/pending-confirmations."""

    async def test_empty_queue_returns_empty_list(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        mock_db.execute = AsyncMock(return_value=_mock_result(rows=[]))

        response = await _get(agent_id, mock_db, fake_tenant)

        assert response.status_code == 200
        assert response.json()["confirmations"] == []

    async def test_row_with_null_arguments_and_no_deadline_serialises(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_db_for_agent(mock_agent)
        row = {
            "id": uuid4(),
            "skill": MUTATING_SKILL,
            "arguments": None,
            "requested_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
            "expires_at": None,
            "resolved_at": None,
            "resolution": None,
        }
        mock_db.execute = AsyncMock(return_value=_mock_result(rows=[row]))

        response = await _get(agent_id, mock_db, fake_tenant)

        assert response.status_code == 200
        entry = response.json()["confirmations"][0]
        assert entry["arguments"] is None
        assert entry["expires_at"] is None
        assert entry["resolution"] is None
        assert entry["execution_outcome"] is None

    async def test_ordering_is_total_and_stable(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)

        # Two unresolved rows sharing everything but id — the id tiebreak is
        # what keeps their relative order identical across repeated calls.
        row_a = {
            "id": UUID(int=1),
            "skill": MUTATING_SKILL,
            "arguments": None,
            "requested_at": datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
            "expires_at": None,
            "resolved_at": None,
            "resolution": None,
        }
        row_b = {**row_a, "id": UUID(int=2)}
        scripted_rows = [row_a, row_b]

        last_query = None
        response_ids: list[list[str]] = []
        for _ in range(2):
            mock_db = _make_db_for_agent(mock_agent)
            mock_db.execute = AsyncMock(return_value=_mock_result(rows=scripted_rows))
            response = await _get(agent_id, mock_db, fake_tenant)
            assert response.status_code == 200
            response_ids.append([c["id"] for c in response.json()["confirmations"]])
            last_query = str(mock_db.execute.call_args[0][0])

        assert response_ids[0] == response_ids[1]
        assert response_ids[0] == [str(row_a["id"]), str(row_b["id"])]

        # The SQL text itself is the actual total-ordering guarantee (no
        # live DB is available in this environment to prove it end-to-end)
        # — both partitions carry the id tiebreak this queue's stability
        # depends on.
        assert "NULLS LAST" in last_query
        assert "id ASC" in last_query
        assert "resolved_at DESC" in last_query


# ---------------------------------------------------------------------------
# TestExecutionOutcome
# ---------------------------------------------------------------------------


class TestExecutionOutcome:
    """Drives _execution_outcome_for directly against a scripted session."""

    def _db_returning(self, row: dict | None) -> AsyncMock:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_mock_result(single=row))
        return mock_db

    async def test_outcome_ignores_the_original_require_human_audit_row(self):
        agent_id = uuid4()
        arguments = {"idempotency_key": "key-1"}
        # What a CORRECT query (carrying the actor-decision predicate)
        # selects: the resolver's own row, never the original require_human
        # row created at confirmation time, which shares agent_id, skill,
        # and arguments with this one.
        resolver_row = {"error": None, "created_at": datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)}
        mock_db = self._db_returning(resolver_row)

        outcome, error, executed_at = await pc._execution_outcome_for(mock_db, agent_id, MUTATING_SKILL, arguments)

        assert outcome == "executed"
        assert error is None
        assert executed_at == resolver_row["created_at"]

        # Guard-removal demonstration (c), T-22-ACT-11: the mechanical form
        # of the discriminator is the literal predicate in the SQL text
        # actually sent. Dropping it from the real module's source turns
        # this assertion red even though the mocked return value above is
        # unchanged — a status-only assertion could not catch that.
        sent_query = str(mock_db.execute.call_args[0][0])
        assert "actor_decision" in sent_query
        assert "approved_by_human" in sent_query

    async def test_executed_when_audit_error_is_null(self):
        agent_id = uuid4()
        row = {"error": None, "created_at": datetime(2026, 7, 28, tzinfo=timezone.utc)}
        mock_db = self._db_returning(row)

        outcome, error, executed_at = await pc._execution_outcome_for(
            mock_db, agent_id, MUTATING_SKILL, {"idempotency_key": "k"}
        )

        assert outcome == "executed"
        assert error is None
        assert executed_at == row["created_at"]

    async def test_not_executed_carries_the_raw_denial_string(self):
        agent_id = uuid4()
        row = {
            "error": "capability.denial:max_amount_cents",
            "created_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
        }
        mock_db = self._db_returning(row)

        outcome, error, executed_at = await pc._execution_outcome_for(
            mock_db, agent_id, MUTATING_SKILL, {"idempotency_key": "k"}
        )

        assert outcome == "not_executed"
        assert error == "capability.denial:max_amount_cents"
        assert executed_at == row["created_at"]

    async def test_no_audit_row_yields_awaiting_execution(self):
        agent_id = uuid4()
        mock_db = self._db_returning(None)

        outcome, error, executed_at = await pc._execution_outcome_for(
            mock_db, agent_id, MUTATING_SKILL, {"idempotency_key": "k"}
        )

        assert outcome is None
        assert error is None
        assert executed_at is None
        mock_db.execute.assert_awaited_once()

    async def test_missing_idempotency_key_skips_the_lookup(self):
        agent_id = uuid4()
        mock_db = self._db_returning({"error": None, "created_at": datetime.now(timezone.utc)})

        outcome, error, executed_at = await pc._execution_outcome_for(mock_db, agent_id, MUTATING_SKILL, {})

        assert outcome is None
        assert error is None
        assert executed_at is None
        mock_db.execute.assert_not_called()
