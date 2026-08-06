"""Unit tests for Clerk JWT verification, dual-auth deps, webhook handler, and /me/provision.

Covers requirement IDs from 04-9-RESEARCH.md Validation Architecture:
    CLERK-01  verify_clerk_jwt() rejects expired token
    CLERK-02  verify_clerk_jwt() rejects invalid signature
    CLERK-03  get_current_tenant resolves via JWT path (mocked PyJWKClient + DB)
    CLERK-04  get_current_tenant falls back to X-API-Key
    CLERK-05  webhook user.created provisions tenant (idempotent INSERT)
    CLERK-06  webhook invalid signature returns 400
    CLERK-07  no credentials on protected route returns 401
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# CLERK-01: expired token rejected
# ---------------------------------------------------------------------------

def test_expired_token():
    """CLERK-01: verify_clerk_jwt raises InvalidTokenError for expired tokens."""
    import jwt as pyjwt

    from app.core.clerk_jwt import verify_clerk_jwt

    with patch("app.core.clerk_jwt._get_jwks_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.side_effect = pyjwt.InvalidTokenError("Token is expired")
        mock_get_client.return_value = mock_client

        with pytest.raises(pyjwt.InvalidTokenError):
            verify_clerk_jwt("fake.expired.token")


# ---------------------------------------------------------------------------
# CLERK-02: bad signature rejected
# ---------------------------------------------------------------------------

def test_bad_signature():
    """CLERK-02: verify_clerk_jwt raises InvalidTokenError for invalid signatures."""
    import jwt as pyjwt
    from jwt import PyJWKClientError

    from app.core.clerk_jwt import verify_clerk_jwt

    with patch("app.core.clerk_jwt._get_jwks_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.side_effect = PyJWKClientError("No matching key found")
        mock_get_client.return_value = mock_client

        with pytest.raises(pyjwt.InvalidTokenError):
            verify_clerk_jwt("fake.bad.signature")


# ---------------------------------------------------------------------------
# CLERK-03: get_current_tenant resolves via JWT path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_current_tenant_jwt_path():
    """CLERK-03: get_current_tenant returns Tenant when Clerk JWT is valid and tenant exists."""
    from app.core.database import get_async_db
    from app.main import app

    tenant_id = uuid4()
    mock_tenant = MagicMock()
    mock_tenant.id = tenant_id
    mock_tenant.clerk_user_id = "user_test123"
    mock_tenant.deleted_at = None

    # Build a mock DB session whose .execute().scalars().first() returns mock_tenant
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_tenant
    mock_db = AsyncMock()
    mock_db.add = MagicMock()  # add() is synchronous in async SQLAlchemy
    mock_db.execute.return_value = mock_result

    # refresh() must set .id so AgentCreateResponse Pydantic validation passes
    _agent_id, _job_id = uuid4(), uuid4()
    async def _mock_refresh(obj):
        from app.models.agent import Agent
        from app.models.job import Job
        if isinstance(obj, Agent):
            obj.id = _agent_id
        elif isinstance(obj, Job):
            obj.id = _job_id
    mock_db.refresh = AsyncMock(side_effect=_mock_refresh)

    async def override_db():
        yield mock_db

    with patch("app.api.deps.verify_clerk_jwt", return_value={"sub": "user_test123"}), \
         patch("app.api.v1.agents.provision_neon") as mock_pn:
        mock_pn.apply_async = MagicMock()
        app.dependency_overrides[get_async_db] = override_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/agents",
                    headers={"Authorization": "Bearer fake.clerk.token"},
                    json={"name": "Test Agent", "soul": {"voice": "", "do": [], "do_not": []}, "role": "support"},
                )
            assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        finally:
            app.dependency_overrides.pop(get_async_db, None)


# ---------------------------------------------------------------------------
# CLERK-04: get_current_tenant falls back to X-API-Key
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_current_tenant_apikey_fallback():
    """CLERK-04: get_current_tenant falls back to X-API-Key when no Bearer header."""
    from app.core.database import get_async_db
    from app.core.security import generate_api_key, hash_api_key, hmac_key_prefix
    from app.main import app

    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = hmac_key_prefix(raw_key)

    mock_tenant = MagicMock()
    mock_tenant.id = uuid4()
    mock_tenant.api_key_hash = key_hash
    mock_tenant.api_key_prefix = key_prefix
    mock_tenant.clerk_user_id = None
    mock_tenant.deleted_at = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_tenant

    mock_db = AsyncMock()
    mock_db.add = MagicMock()  # add() is synchronous in async SQLAlchemy
    mock_db.execute.return_value = mock_result

    _agent_id, _job_id = uuid4(), uuid4()
    async def _mock_refresh(obj):
        from app.models.agent import Agent
        from app.models.job import Job
        if isinstance(obj, Agent):
            obj.id = _agent_id
        elif isinstance(obj, Job):
            obj.id = _job_id
    mock_db.refresh = AsyncMock(side_effect=_mock_refresh)

    async def override_db():
        yield mock_db

    app.dependency_overrides[get_async_db] = override_db
    try:
        with patch("app.api.v1.agents.provision_neon") as mock_pn:
            mock_pn.apply_async = MagicMock()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/agents",
                    headers={"X-API-Key": raw_key},
                    json={"name": "Test Agent", "soul": {"voice": "", "do": [], "do_not": []}, "role": "support"},
                )
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}"
    finally:
        app.dependency_overrides.pop(get_async_db, None)


# ---------------------------------------------------------------------------
# CLERK-05: webhook user.created provisions tenant
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_webhook_user_created_provisions_tenant():
    """CLERK-05: valid user.created webhook creates a tenant row (idempotent INSERT)."""
    from app.core.database import get_async_db
    from app.main import app

    webhook_payload = {
        "type": "user.created",
        "data": {
            "id": "user_webhook_abc",
            "email_addresses": [{"email_address": "test@example.com"}],
            "first_name": "Test",
            "last_name": "User",
        },
    }

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    async def override_db():
        yield mock_db

    app.dependency_overrides[get_async_db] = override_db
    try:
        # Patch the entire Webhook class — constructor raises RuntimeError on empty secret
        mock_wh_instance = MagicMock()
        mock_wh_instance.verify.return_value = webhook_payload
        with patch("app.api.v1.webhooks.Webhook", return_value=mock_wh_instance):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/webhooks/clerk",
                    content=json.dumps(webhook_payload).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "svix-id": "msg_test",
                        "svix-timestamp": "1234567890",
                        "svix-signature": "v1,fake_sig",
                    },
                )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
        # Verify db.execute was called (INSERT INTO tenants)
        assert mock_db.execute.called
        assert mock_db.commit.called
    finally:
        app.dependency_overrides.pop(get_async_db, None)


# ---------------------------------------------------------------------------
# CLERK-06: webhook invalid signature returns 400
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_webhook_invalid_signature_returns_400():
    """CLERK-06: webhook with invalid Svix signature returns HTTP 400."""
    from svix.webhooks import WebhookVerificationError

    from app.main import app

    # Patch the entire Webhook class — constructor raises RuntimeError on empty secret
    mock_wh_instance = MagicMock()
    mock_wh_instance.verify.side_effect = WebhookVerificationError
    with patch("app.api.v1.webhooks.Webhook", return_value=mock_wh_instance):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/webhooks/clerk",
                content=b'{"type":"user.created"}',
                headers={
                    "Content-Type": "application/json",
                    "svix-id": "msg_test",
                    "svix-timestamp": "1234567890",
                    "svix-signature": "v1,bad_sig",
                },
            )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# CLERK-07: no credentials returns 401
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_credentials_returns_401():
    """CLERK-07: request with no Authorization and no X-API-Key returns 401."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # POST /agents requires get_current_tenant which requires auth
        resp = await client.post("/api/v1/agents", json={"name": "Test"})

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
