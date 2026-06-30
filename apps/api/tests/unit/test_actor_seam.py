"""
Unit tests for the Phase-15 Actor seam (call_actor_gate).

Coverage:
  ACT-01 — Haiku forced-tool-use returns approve | block | require_human + rationale
  ACT-03 — skip short-circuit: low-value action (max_amount_cents < threshold AND
            requires_confirmation=False) returns approve WITHOUT calling the Anthropic client
  ACT-06 — Langfuse v4 start_as_current_generation + create_score + flush called on Haiku call

Mock strategy:
  - Patch ANTHROPIC_CLIENT.messages.create at module boundary
    (app.services.actor_seam.ANTHROPIC_CLIENT) with a MagicMock whose .content
    is a list containing a fake tool_use block
  - Patch app.services.actor_seam._langfuse to test Langfuse logging branch
  - Patch app.services.actor_seam._fetch_history (AsyncMock) to control history fetch
  - asyncio.run() drives the async call_actor_gate; no real event loop setup needed

Named skip tests (-k skip_threshold selects both):
  test_skip_threshold_returns_approve — ACT-03 below-threshold
  test_skip_threshold_does_not_skip_when_at_or_above — ACT-03 at-threshold boundary
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODULE = "app.services.actor_seam"

_SKILL = "place_order"
_ARGUMENTS = {"product_id": "SKU-001", "quantity": 1, "amount_cents": 250}
_CONV_ID = "conv-test-0001"
_AGENT_ID = "agent-test-0001"

# Snapshot helpers
_SNAP_REQUIRES_CONFIRM = {
    "enabled": True,
    "requires_confirmation": True,
    "constraints": {"max_amount_cents": 400},
}

_SNAP_LOW_VALUE = {
    "enabled": True,
    "requires_confirmation": False,
    "constraints": {"max_amount_cents": 400},  # 400 < 500 threshold → skip
}

_SNAP_AT_THRESHOLD = {
    "enabled": True,
    "requires_confirmation": False,
    "constraints": {"max_amount_cents": 500},  # 500 == 500 threshold → no skip (not strictly below)
}

_SNAP_ABOVE_THRESHOLD = {
    "enabled": True,
    "requires_confirmation": False,
    "constraints": {"max_amount_cents": 600},  # 600 > 500 → no skip
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_use_block(verdict: str, rationale: str = "Test rationale.") -> MagicMock:
    """Create a fake tool_use content block mimicking Anthropic API response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_verdict"
    block.input = {"verdict": verdict, "rationale": rationale}
    return block


def _make_api_response(*blocks) -> MagicMock:
    """Create a fake anthropic messages.create response with the given content blocks."""
    response = MagicMock()
    response.content = list(blocks)
    return response


def _run_gate(
    snapshot: dict,
    *,
    api_mock=None,
    history_mock=None,
    langfuse_mock=None,
    conn_str: str = "",
) -> tuple[str, str]:
    """Drive call_actor_gate through asyncio.run with all external deps mocked."""
    from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

    ctx_managers = []
    if api_mock is not None:
        ctx_managers.append(
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock)
        )
    if history_mock is not None:
        ctx_managers.append(
            patch(f"{_MODULE}._fetch_history", history_mock)
        )
    if langfuse_mock is not None:
        ctx_managers.append(
            patch(f"{_MODULE}._langfuse", langfuse_mock)
        )

    def _run():
        return call_actor_gate(
            _SKILL, _ARGUMENTS, snapshot, _CONV_ID, _AGENT_ID, conn_str
        )

    # Apply patches via nested context managers
    if not ctx_managers:
        return asyncio.run(_run())

    # Python 3.10+ contextlib.ExitStack-style manual stack
    result = None

    def _apply(managers, idx):
        nonlocal result
        if idx >= len(managers):
            result = asyncio.run(_run())
            return
        with managers[idx]:
            _apply(managers, idx + 1)

    _apply(ctx_managers, 0)
    return result


# ---------------------------------------------------------------------------
# ACT-03: Skip threshold tests
# ---------------------------------------------------------------------------


class TestSkipThreshold:
    """ACT-03: skip short-circuit when requires_confirmation=False AND max < threshold."""

    def test_skip_threshold_returns_approve(self):
        """Low-value skill: requires_confirmation=False, max_amount_cents=400 < 500 → approve,
        and ANTHROPIC_CLIENT.messages.create is NOT called."""
        api_mock = MagicMock()  # MagicMock, NOT AsyncMock — messages.create is synchronous

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
        ):
            decision, rationale = asyncio.run(
                call_actor_gate(_SKILL, _ARGUMENTS, _SNAP_LOW_VALUE, _CONV_ID, _AGENT_ID, "")
            )

        assert decision == "approve", f"Expected approve, got {decision!r}"
        assert "skip" in rationale, f"Rationale should mention skip, got {rationale!r}"
        api_mock.assert_not_called()  # No Haiku spend on low-value actions

    def test_skip_threshold_does_not_skip_when_at_or_above(self):
        """Boundary: max_amount_cents == 500 (== threshold) must NOT skip — Haiku IS called."""
        approve_block = _make_tool_use_block("approve", "Aligned with intent.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, _ = asyncio.run(
                call_actor_gate(_SKILL, _ARGUMENTS, _SNAP_AT_THRESHOLD, _CONV_ID, _AGENT_ID, "")
            )

        assert decision == "approve"
        api_mock.assert_called_once()  # Haiku was called

    def test_skip_does_not_apply_when_requires_confirmation_true(self):
        """requires_confirmation=True + low max_amount_cents → Haiku IS still called."""
        approve_block = _make_tool_use_block("approve", "Aligned with intent.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, _ = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == "approve"
        api_mock.assert_called_once()  # Haiku was called regardless of low amount


# ---------------------------------------------------------------------------
# ACT-01: Verdict parsing (approve, block, require_human)
# ---------------------------------------------------------------------------


class TestVerdictParsing:
    """ACT-01: forced-tool-use Haiku call returns the parsed verdict and rationale."""

    @pytest.mark.parametrize("verdict_str,expected_rationale", [
        ("approve", "Action aligns with stated intent."),
        ("block", "Proposed action does not match conversation context."),
        ("require_human", "Uncertain alignment — escalating for human review."),
    ])
    def test_haiku_verdict_parsed_correctly(self, verdict_str: str, expected_rationale: str):
        """Mocked tool_use block with any verdict → call_actor_gate returns that verdict."""
        block = _make_tool_use_block(verdict_str, expected_rationale)
        api_mock = MagicMock(return_value=_make_api_response(block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, rationale = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == verdict_str
        assert rationale == expected_rationale
        api_mock.assert_called_once()

    def test_haiku_approve_verdict(self):
        """Explicit test name for approve verdict (test discovery compatibility)."""
        block = _make_tool_use_block("approve", "Aligned.")
        api_mock = MagicMock(return_value=_make_api_response(block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, rationale = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == "approve"
        assert rationale == "Aligned."

    def test_haiku_block_verdict(self):
        """Explicit test name for block verdict."""
        block = _make_tool_use_block("block", "Misaligned action.")
        api_mock = MagicMock(return_value=_make_api_response(block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, rationale = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == "block"
        assert "Misaligned" in rationale

    def test_haiku_require_human_verdict(self):
        """Explicit test name for require_human verdict."""
        block = _make_tool_use_block("require_human", "Uncertain — escalating.")
        api_mock = MagicMock(return_value=_make_api_response(block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, rationale = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == "require_human"
        assert "escalating" in rationale


# ---------------------------------------------------------------------------
# History fallback tests
# ---------------------------------------------------------------------------


class TestHistoryFallback:
    """Conversation history fetch failure must not prevent the Actor from running."""

    def test_history_fetch_failure_falls_back(self):
        """When _fetch_history raises, Haiku is still called with NO CONVERSATION HISTORY
        AVAILABLE in the prompt; no exception escapes call_actor_gate."""
        approve_block = _make_tool_use_block("approve", "OK.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))
        history_mock = AsyncMock(side_effect=Exception("DB connection refused"))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", history_mock),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, _ = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, "conn://..."
                )
            )

        # Gate still returns a verdict
        assert decision in ("approve", "block", "require_human")
        api_mock.assert_called_once()

        # Verify the NO HISTORY sentinel was passed in the user message
        call_args = api_mock.call_args
        user_content = call_args.kwargs.get("messages", [{}])[0].get("content", "")
        assert "NO CONVERSATION HISTORY AVAILABLE" in user_content

    def test_empty_conn_str_skips_fetch_and_uses_sentinel(self):
        """Empty conn_str means _fetch_history returns [] without DB call, and
        NO CONVERSATION HISTORY AVAILABLE is used as the history string."""
        approve_block = _make_tool_use_block("approve", "OK.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._langfuse", None),
        ):
            # conn_str="" — _fetch_history is NOT patched; the real function runs
            # but returns [] because conn_str is empty (no psycopg2 call)
            decision, _ = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision in ("approve", "block", "require_human")
        call_args = api_mock.call_args
        user_content = call_args.kwargs.get("messages", [{}])[0].get("content", "")
        assert "NO CONVERSATION HISTORY AVAILABLE" in user_content


# ---------------------------------------------------------------------------
# ACT-06: Langfuse logging tests
# ---------------------------------------------------------------------------


class TestLangfuseLogging:
    """ACT-06: Langfuse v4 start_as_current_generation + create_score logged; NO per-call flush."""

    def test_langfuse_logged_on_haiku_call(self):
        """On an approve verdict, start_as_current_generation and create_score are each
        called exactly once; flush is NOT called on the request path (ACT-06 — the Actor
        is synchronous pre-mutation, so a per-call flush would add a Langfuse network
        round-trip to every mutating call; the SDK background-flushes instead)."""
        approve_block = _make_tool_use_block("approve", "Aligned with intent.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))

        # Mock Langfuse instance
        langfuse_mock = MagicMock()
        # start_as_current_generation is a context manager
        gen_ctx = MagicMock()
        gen_ctx.__enter__ = MagicMock(return_value=gen_ctx)
        gen_ctx.__exit__ = MagicMock(return_value=False)
        langfuse_mock.start_as_current_generation.return_value = gen_ctx

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", langfuse_mock),
        ):
            asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        # Langfuse v4 pattern: start_as_current_generation called exactly once
        langfuse_mock.start_as_current_generation.assert_called_once()
        call_kwargs = langfuse_mock.start_as_current_generation.call_args.kwargs
        assert call_kwargs.get("name") == "actor-gate"
        assert "verdict" in call_kwargs.get("output", {})

        # create_score called once
        langfuse_mock.create_score.assert_called_once()
        score_kwargs = langfuse_mock.create_score.call_args.kwargs
        assert score_kwargs.get("name") == "actor_decision"
        assert score_kwargs.get("data_type") == "CATEGORICAL"
        assert score_kwargs.get("trace_id") == _CONV_ID

        # ACT-06: flush() must NOT be called on the request path — the Actor runs
        # synchronously pre-mutation; a per-call flush adds a Langfuse network round-trip
        # to every mutating call. The SDK's background flusher + atexit deliver the data.
        langfuse_mock.flush.assert_not_called()

    def test_langfuse_failure_does_not_block_gate(self):
        """If Langfuse raises during logging, the gate still returns the correct verdict."""
        approve_block = _make_tool_use_block("approve", "OK.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))

        langfuse_mock = MagicMock()
        langfuse_mock.start_as_current_generation.side_effect = RuntimeError("Langfuse down")

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", langfuse_mock),
        ):
            # Must NOT raise
            decision, rationale = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == "approve"
        assert rationale == "OK."

    def test_langfuse_none_does_not_crash(self):
        """When _langfuse is None (keys not set), the gate still works."""
        approve_block = _make_tool_use_block("approve", "OK.")
        api_mock = MagicMock(return_value=_make_api_response(approve_block))

        from app.services.actor_seam import call_actor_gate  # noqa: PLC0415

        with (
            patch(f"{_MODULE}.ANTHROPIC_CLIENT.messages.create", api_mock),
            patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
            patch(f"{_MODULE}._langfuse", None),
        ):
            decision, _ = asyncio.run(
                call_actor_gate(
                    _SKILL, _ARGUMENTS, _SNAP_REQUIRES_CONFIRM, _CONV_ID, _AGENT_ID, ""
                )
            )

        assert decision == "approve"
