"""The two review routes (#290 step 3).

    GET  /api/v1/agents/{agent_id}/eval-runs/{run_id}/claims
    POST /api/v1/agents/{agent_id}/eval-runs/{run_id}/claims/review

The service is patched at the route's import, so these tests say what the route
does with what the service returns: the shape, the IDOR rule every eval route
keeps, and the status a refusal becomes (422 for an answer the run did not
flag or that the type refuses, 503 for a tenant database with nowhere to store
one). The service's own rules are in test_claim_review_service.py.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_current_tenant
from app.domain.claim_review import InvalidClaimReview
from app.main import app
from app.services.claim_review_service import (
    ClaimReviewsUnavailable,
    FlaggedClaim,
    FlaggedClaims,
    FlaggedScenario,
)
from tests.unit.test_eval_routes import (
    _make_fake_tenant,
    _make_mock_db_returning_agent,
    _make_mock_db_returning_none,
    _make_ready_agent,
)

HEADERS = {"X-API-Key": "vrd_live_test"}

FLAGGED = FlaggedClaims(scenarios=(
    FlaggedScenario("s1", "what does delivery cost?", "Delivery costs R30 ...", ("Delivery is R30.",), (
        FlaggedClaim(1, "Orders over R500 ship free."),
        FlaggedClaim(2, "The fee is R60 outside Tembisa.", review=True),
    )),
))


async def _call(method: str, path: str, tenant, db, **kwargs):
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_async_db] = lambda: db
    try:
        with patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await getattr(client, method)(path, headers=HEADERS, **kwargs)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def owned():
    tenant = _make_fake_tenant()
    agent = _make_ready_agent(tenant)
    return tenant, agent, _make_mock_db_returning_agent(agent)


def _answers(*items):
    return {"answers": [
        {"scenario_id": s, "position": p, "statement": st, "supported": sup} for s, p, st, sup in items
    ]}


class TestGetFlaggedClaims:
    async def test_lists_the_flags_grouped_by_answer_with_the_counts(self, owned):
        tenant, agent, db = owned
        run_id = uuid4()
        with patch("app.api.v1.evals.claim_review_service.read_flagged_claims", return_value=FLAGGED) as read:
            response = await _call("get", f"/api/v1/agents/{agent.id}/eval-runs/{run_id}/claims", tenant, db)
        assert response.status_code == 200
        body = response.json()
        assert (body["flagged"], body["answered"], body["reviews_available"], body["dropped_scenarios"]) == (2, 1, True, 0)
        [scenario] = body["scenarios"]
        assert set(scenario) == {"scenario_id", "question", "response", "retrieved_contexts", "claims"}
        assert [(c["position"], c["review"]) for c in scenario["claims"]] == [(1, None), (2, True)]
        read.assert_called_once_with(str(run_id), "postgresql://fake/db")

    async def test_a_tenant_with_nowhere_to_store_an_answer_is_told_so(self, owned):
        tenant, agent, db = owned
        result = FlaggedClaims(scenarios=FLAGGED.scenarios, reviews_available=False)
        with patch("app.api.v1.evals.claim_review_service.read_flagged_claims", return_value=result):
            response = await _call("get", f"/api/v1/agents/{agent.id}/eval-runs/{uuid4()}/claims", tenant, db)
        assert response.status_code == 200
        assert response.json()["reviews_available"] is False

    async def test_an_agent_of_another_tenant_is_404(self):
        tenant = _make_fake_tenant()
        other = _make_ready_agent(_make_fake_tenant())
        response = await _call("get", f"/api/v1/agents/{other.id}/eval-runs/{uuid4()}/claims", tenant, _make_mock_db_returning_agent(other))
        assert response.status_code == 404

    async def test_an_unknown_agent_is_404(self):
        response = await _call("get", f"/api/v1/agents/{uuid4()}/eval-runs/{uuid4()}/claims", _make_fake_tenant(), _make_mock_db_returning_none())
        assert response.status_code == 404

    async def test_without_an_api_key_the_route_is_refused(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/agents/{uuid4()}/eval-runs/{uuid4()}/claims")
        assert response.status_code in (401, 403)


class TestReviewFlaggedClaims:
    async def test_answers_reach_the_writer_as_records_about_this_run(self, owned):
        tenant, agent, db = owned
        run_id = uuid4()
        body = _answers(("s1", 1, "Orders over R500 ship free.", False), ("s1", 2, "The fee is R60 outside Tembisa.", True))
        with patch("app.api.v1.evals.claim_review_service.write_claim_reviews", return_value=2) as write:
            response = await _call("post", f"/api/v1/agents/{agent.id}/eval-runs/{run_id}/claims/review", tenant, db, json=body)
        assert response.status_code == 200
        assert response.json() == {"stored": 2}
        (called_run, answers, conn), _ = write.call_args
        assert called_run == str(run_id) and conn == "postgresql://fake/db"
        assert [(a.eval_run_id, a.scenario_id, a.position, a.supported) for a in answers] == [
            (str(run_id), "s1", 1, False), (str(run_id), "s1", 2, True),
        ]

    async def test_a_refused_answer_is_422_with_the_reason(self, owned):
        tenant, agent, db = owned
        with patch("app.api.v1.evals.claim_review_service.write_claim_reviews",
                   side_effect=InvalidClaimReview("the judge flagged no claim at ('s1', 9)")):
            response = await _call("post", f"/api/v1/agents/{agent.id}/eval-runs/{uuid4()}/claims/review", tenant, db,
                                   json=_answers(("s1", 9, "x", False)))
        assert response.status_code == 422
        assert "flagged no claim" in response.json()["detail"]

    async def test_a_whitespace_statement_is_422_not_500(self, owned):
        """The schema's min_length lets a space through; the type refuses it, and that is 422."""
        tenant, agent, db = owned
        with patch("app.api.v1.evals.claim_review_service.write_claim_reviews") as write:
            response = await _call("post", f"/api/v1/agents/{agent.id}/eval-runs/{uuid4()}/claims/review", tenant, db,
                                   json=_answers(("s1", 1, " ", False)))
        assert response.status_code == 422
        assert "statement" in response.json()["detail"]
        write.assert_not_called()

    async def test_a_tenant_with_nowhere_to_store_an_answer_is_503(self, owned):
        tenant, agent, db = owned
        with patch("app.api.v1.evals.claim_review_service.write_claim_reviews",
                   side_effect=ClaimReviewsUnavailable("the tenant database predates alembic_tenant 0032")):
            response = await _call("post", f"/api/v1/agents/{agent.id}/eval-runs/{uuid4()}/claims/review", tenant, db,
                                   json=_answers(("s1", 1, "x", False)))
        assert response.status_code == 503
        assert "0032" in response.json()["detail"]

    async def test_an_empty_sitting_is_refused_by_the_schema(self, owned):
        tenant, agent, db = owned
        with patch("app.api.v1.evals.claim_review_service.write_claim_reviews") as write:
            response = await _call("post", f"/api/v1/agents/{agent.id}/eval-runs/{uuid4()}/claims/review", tenant, db, json={"answers": []})
        assert response.status_code == 422
        write.assert_not_called()

    async def test_an_answer_that_is_not_yes_or_no_is_refused_by_the_schema(self, owned):
        tenant, agent, db = owned
        body = {"answers": [{"scenario_id": "s1", "position": 1, "statement": "x", "supported": "maybe"}]}
        response = await _call("post", f"/api/v1/agents/{agent.id}/eval-runs/{uuid4()}/claims/review", tenant, db, json=body)
        assert response.status_code == 422

    async def test_an_agent_of_another_tenant_is_404_before_any_write(self):
        tenant = _make_fake_tenant()
        other = _make_ready_agent(_make_fake_tenant())
        with patch("app.api.v1.evals.claim_review_service.write_claim_reviews") as write:
            response = await _call("post", f"/api/v1/agents/{other.id}/eval-runs/{uuid4()}/claims/review", tenant,
                                   _make_mock_db_returning_agent(other), json=_answers(("s1", 1, "x", False)))
        assert response.status_code == 404
        write.assert_not_called()
