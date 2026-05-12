"""
Unit tests for app.schemas.agent — Pydantic v2 schema validation.

Tests:
    - AgentCreate with valid payload succeeds
    - AgentCreate with invalid role raises ValidationError
    - AgentCreate without name raises ValidationError
    - SoulSchema requires do and do_not fields
"""

import os

import pytest
from pydantic import ValidationError

# env vars are set in conftest.py (loaded before this file);
# the setdefault calls in individual test files use setdefault so conftest wins.
from app.schemas.agent import AgentCreate, AgentCreateResponse, AgentResponse, SoulSchema


# ---------------------------------------------------------------------------
# SoulSchema
# ---------------------------------------------------------------------------


class TestSoulSchema:
    def test_soul_valid(self):
        soul = SoulSchema(
            voice="Friendly and professional",
            do=["greet users", "answer FAQs"],
            do_not=["share personal data"],
        )
        assert soul.voice == "Friendly and professional"
        assert soul.do == ["greet users", "answer FAQs"]
        assert soul.do_not == ["share personal data"]

    def test_soul_empty_lists_allowed(self):
        """do and do_not can be empty lists."""
        soul = SoulSchema(voice="neutral", do=[], do_not=[])
        assert soul.do == []
        assert soul.do_not == []

    def test_soul_missing_voice_raises(self):
        with pytest.raises(ValidationError):
            SoulSchema(do=["help users"], do_not=[])

    def test_soul_missing_do_raises(self):
        with pytest.raises(ValidationError):
            SoulSchema(voice="v", do_not=[])

    def test_soul_missing_do_not_raises(self):
        with pytest.raises(ValidationError):
            SoulSchema(voice="v", do=["help"])


# ---------------------------------------------------------------------------
# AgentCreate
# ---------------------------------------------------------------------------


def _valid_soul() -> SoulSchema:
    return SoulSchema(voice="Helpful", do=["answer questions"], do_not=["be rude"])


class TestAgentCreate:
    def test_valid_support_role(self):
        agent = AgentCreate(name="SupportBot", soul=_valid_soul(), role="support")
        assert agent.name == "SupportBot"
        assert agent.role == "support"

    def test_valid_sales_role(self):
        agent = AgentCreate(name="SalesBot", soul=_valid_soul(), role="sales")
        assert agent.role == "sales"

    def test_valid_helpdesk_role(self):
        agent = AgentCreate(name="HelpBot", soul=_valid_soul(), role="helpdesk")
        assert agent.role == "helpdesk"

    def test_invalid_role_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            AgentCreate(name="Bot", soul=_valid_soul(), role="intern")
        # Pydantic v2 raises ValidationError with literal constraint info
        assert "intern" in str(exc_info.value) or "validation error" in str(exc_info.value).lower()

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            AgentCreate(soul=_valid_soul(), role="support")

    def test_missing_soul_raises(self):
        with pytest.raises(ValidationError):
            AgentCreate(name="Bot", role="support")

    def test_missing_role_raises(self):
        with pytest.raises(ValidationError):
            AgentCreate(name="Bot", soul=_valid_soul())

    def test_soul_is_nested(self):
        """AgentCreate.soul must be a SoulSchema instance, not a plain dict."""
        agent = AgentCreate(
            name="Bot",
            soul={"voice": "calm", "do": [], "do_not": []},
            role="support",
        )
        assert isinstance(agent.soul, SoulSchema)

    def test_model_dump_contains_soul_dict(self):
        """soul serialises to dict correctly."""
        agent = AgentCreate(name="Bot", soul=_valid_soul(), role="support")
        d = agent.model_dump()
        assert "soul" in d
        assert isinstance(d["soul"], dict)
        assert "voice" in d["soul"]


# ---------------------------------------------------------------------------
# AgentCreateResponse
# ---------------------------------------------------------------------------


class TestAgentCreateResponse:
    def test_valid_response(self):
        from uuid import uuid4
        resp = AgentCreateResponse(
            agent_id=uuid4(),
            job_id=uuid4(),
            status="pending",
            events_url="/jobs/123/events",
        )
        assert resp.status == "pending"
        assert resp.events_url.startswith("/jobs/")
