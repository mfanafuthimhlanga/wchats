"""M7 xfail stubs for red_team_service unit tests.

These stubs are de-xfailed in Plan 07-05 once red_team_service.py is implemented.

Mock strategy (for 07-05):
    All Anthropic and Agent SDK calls patched at module boundary.
    Haiku judge calls mocked via unittest.mock.patch.
    Agent SDK run() patched to return deterministic probe responses.
"""

import os
import base64

# Safety: ensure required env vars are present even if conftest is not loaded
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

import pytest


@pytest.mark.xfail(reason="07-05: red_team_service not yet implemented", strict=True)
def test_classify_severity_critical():
    """Haiku judge returns critical severity for successful prompt injection."""
    assert False, "stub — de-xfail in 07-05"


@pytest.mark.xfail(reason="07-05: red_team_service not yet implemented", strict=True)
def test_prompt_injection_agent_finds_vulnerability():
    """PromptInjectionAgent returns at least one RedTeamFinding when probe_fn is vulnerable."""
    assert False, "stub — de-xfail in 07-05"
