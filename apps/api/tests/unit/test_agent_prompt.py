"""
Unit tests for app.services.agent_prompt.build_system_prompt.

Test coverage:
  1. test_build_system_prompt_empty_soul       — CITATIONS present, AI-disclosure present, defaults
  2. test_build_system_prompt_populated_soul   — role/voice/do/donot values appear in output
  3. test_build_system_prompt_includes_few_shot — FEW_SHOT_SUFFIX substrings present
  4. test_build_system_prompt_does_not_leak_field_names — "soul_role" and "soul_voice" absent

Uses MagicMock(spec=Agent) to stub agent attributes — ORM model is not instantiated.
"""

from unittest.mock import MagicMock

import pytest

from app.models.agent import Agent
from app.services.agent_prompt import build_system_prompt


def _make_agent(
    name: str = "Acme Bot",
    soul_role=None,
    soul_voice=None,
    soul_do_list=None,
    soul_donot_list=None,
) -> MagicMock:
    """Build a MagicMock that satisfies the Agent spec with controlled soul fields."""
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.soul_role = soul_role
    agent.soul_voice = soul_voice
    agent.soul_do_list = soul_do_list if soul_do_list is not None else []
    agent.soul_donot_list = soul_donot_list if soul_donot_list is not None else []
    return agent


# ---------------------------------------------------------------------------
# Test 1: Empty soul — defaults + mandatory blocks present
# ---------------------------------------------------------------------------


def test_build_system_prompt_empty_soul():
    """When all soul fields are None/empty, defaults are injected and mandatory blocks present."""
    agent = _make_agent()
    prompt = build_system_prompt(agent)

    # CITATIONS literal block
    assert "CITATIONS:" in prompt
    assert "Document:" in prompt
    assert "Section:" in prompt

    # AI-disclosure (California SB-1001)
    assert "AI assistant" in prompt

    # Default role and voice applied
    assert "customer service representative" in prompt
    assert "helpful, professional, and concise" in prompt

    # Default do/donot placeholders
    assert "Answer questions accurately based on retrieved content" in prompt
    assert "Make up information not present in retrieved content" in prompt


# ---------------------------------------------------------------------------
# Test 2: Populated soul — agent values appear in output
# ---------------------------------------------------------------------------


def test_build_system_prompt_populated_soul():
    """When soul fields are populated, those values appear verbatim in the prompt."""
    agent = _make_agent(
        name="TechCorp Support",
        soul_role="senior technical support engineer",
        soul_voice="direct, empathetic, and solution-focused",
        soul_do_list=["Escalate P1 issues immediately", "Verify software version first"],
        soul_donot_list=["Promise SLAs not in the contract", "Reveal internal ticket IDs"],
    )
    prompt = build_system_prompt(agent)

    # Agent name present
    assert "TechCorp Support" in prompt

    # Role and voice
    assert "senior technical support engineer" in prompt
    assert "direct, empathetic, and solution-focused" in prompt

    # Do-list items
    assert "Escalate P1 issues immediately" in prompt
    assert "Verify software version first" in prompt

    # Do-not-list items
    assert "Promise SLAs not in the contract" in prompt
    assert "Reveal internal ticket IDs" in prompt


# ---------------------------------------------------------------------------
# Test 3: FEW_SHOT_SUFFIX substrings present
# ---------------------------------------------------------------------------


def test_build_system_prompt_includes_few_shot():
    """FEW_SHOT_SUFFIX content is present in the generated prompt."""
    agent = _make_agent()
    prompt = build_system_prompt(agent)

    # From AI-SPEC.md §4b.3 FEW_SHOT_SUFFIX
    assert "Example of a correct response with citation" in prompt
    assert "Example of correct escalation" in prompt


# ---------------------------------------------------------------------------
# Test 4: No field-name leakage
# ---------------------------------------------------------------------------


def test_build_system_prompt_does_not_leak_field_names():
    """Literal ORM field names must not appear in the assembled prompt."""
    agent = _make_agent(
        soul_role="retail associate",
        soul_voice="warm and friendly",
    )
    prompt = build_system_prompt(agent)

    # Raw field names must not appear (would indicate template variable leakage)
    assert "soul_role" not in prompt
    assert "soul_voice" not in prompt
