"""
Unit tests for app.services.agent_prompt.build_system_prompt.

Test coverage:
  1. test_build_system_prompt_empty_soul       — CITATIONS present, AI-disclosure present, defaults
  2. test_build_system_prompt_populated_soul   — role/voice/do/donot values appear in output
  3. test_build_system_prompt_includes_few_shot — FEW_SHOT_SUFFIX substrings present
  4. test_build_system_prompt_does_not_leak_field_names — "soul_role" and "soul_voice" absent
  5. the length bound: a maximal agent's prompt fits SYSTEM_PROMPT_MAX_CHARS, and that
     number is tight enough to be a measurement rather than slack (#182)

Uses MagicMock(spec=Agent) to stub agent attributes — ORM model is not instantiated.
"""

from unittest.mock import MagicMock

from app.models.agent import Agent
from app.services.agent_prompt import (
    AGENT_NAME_MAX_CHARS,
    SOUL_LIST_ITEM_MAX_CHARS,
    SOUL_LIST_MAX_ITEMS,
    SOUL_ROLE_MAX_CHARS,
    SOUL_VOICE_MAX_CHARS,
    SYSTEM_PROMPT_MAX_CHARS,
    build_system_prompt,
)


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


# ---------------------------------------------------------------------------
# Test 5: the output length is bounded, and the bound is the schema's
# ---------------------------------------------------------------------------


def _maximal_agent() -> MagicMock:
    """The largest agent row `AgentSoulUpdate` and `AgentCreate` will accept.

    Every field at its cap, and the lists at both caps at once: the item count
    and the per-item length. This is the row a tenant can build from the admin
    soul editor with no code change and no deploy, so it is the row the bound
    has to cover.
    """
    return _make_agent(
        name="N" * AGENT_NAME_MAX_CHARS,
        soul_role="R" * SOUL_ROLE_MAX_CHARS,
        soul_voice="V" * SOUL_VOICE_MAX_CHARS,
        soul_do_list=["D" * SOUL_LIST_ITEM_MAX_CHARS] * SOUL_LIST_MAX_ITEMS,
        soul_donot_list=["X" * SOUL_LIST_ITEM_MAX_CHARS] * SOUL_LIST_MAX_ITEMS,
    )


def test_the_prompt_a_maximal_agent_builds_fits_the_declared_bound():
    """#182's first input. An unbounded prompt makes the turn budget underivable.

    This string is the first message of EVERY model call of a turn, and a turn
    sends up to six, so the prompt is paid for six times. Before the item-count
    cap the only limit was 200 characters per rule and no limit on rules, so a
    tenant set the per-call floor from the soul editor.
    """
    prompt = build_system_prompt(_maximal_agent())

    assert len(prompt) <= SYSTEM_PROMPT_MAX_CHARS, (
        f"a maximal agent builds a {len(prompt)}-character prompt against a "
        f"declared bound of {SYSTEM_PROMPT_MAX_CHARS}. The bound is the template "
        "plus the schema caps, so one of them moved without the other."
    )


def test_the_bound_is_tight_enough_to_mean_something():
    """A bound ten times the real prompt would pass the test above and say nothing.

    The maximal prompt has to reach nearly all of the declared bound, or the
    number is slack rather than a measurement and every ceiling derived from it
    inherits the slack. The only headroom the bound carries by construction is
    the newlines BETWEEN list items: `_LIST_ITEM_OVERHEAD_CHARS` counts one per
    item and a join writes one per gap, which is two characters over two lists.
    """
    prompt = build_system_prompt(_maximal_agent())

    assert len(prompt) >= SYSTEM_PROMPT_MAX_CHARS - 4, (
        f"the maximal prompt is {len(prompt)} characters against a bound of "
        f"{SYSTEM_PROMPT_MAX_CHARS}, so the bound carries "
        f"{SYSTEM_PROMPT_MAX_CHARS - len(prompt)} characters of slack it does "
        "not declare."
    )


def test_the_empty_soul_defaults_also_fit_the_bound():
    """The defaults substitute for empty fields, so they are inside the bound too."""
    assert len(build_system_prompt(_make_agent())) <= SYSTEM_PROMPT_MAX_CHARS


def test_a_soul_override_cannot_outgrow_the_bound_either():
    """OPS-16 routes a prompt_versions row through the same template.

    `create_version_from_agent` and `rollback_to_version` copy an agent row into
    `prompt_versions`, so a version is bounded by the schema that bounded the
    row. Driving the override path shows a canary turn gets the same template
    and therefore the same bound.
    """
    override = {
        "soul_role": "R" * SOUL_ROLE_MAX_CHARS,
        "soul_voice": "V" * SOUL_VOICE_MAX_CHARS,
        "soul_do_list": ["D" * SOUL_LIST_ITEM_MAX_CHARS] * SOUL_LIST_MAX_ITEMS,
        "soul_donot_list": ["X" * SOUL_LIST_ITEM_MAX_CHARS] * SOUL_LIST_MAX_ITEMS,
    }
    prompt = build_system_prompt(
        _make_agent(name="N" * AGENT_NAME_MAX_CHARS), soul_override=override
    )

    assert len(prompt) <= SYSTEM_PROMPT_MAX_CHARS
