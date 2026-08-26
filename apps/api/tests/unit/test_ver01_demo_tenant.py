"""
VER-01 demo tenant — locks the demo tenant's capability-envelope posture as
executable, tested data and proves the single non-obvious fact the whole demo
rests on: the Actor's low-value skip short-circuit engages for the demo
tenant's issue_refund envelope and does NOT engage for its place_order
envelope.

Two module-level constants, mirroring red_team_probe.py's CLEAN_TENANT_ENVELOPES
/ CLEAN_TENANT_SPEC shape (18-03), but never importing from that module — the
demo tenant is its own named fixture, not the clean red-team tenant.

Every boundary mocked: no Postgres, no Redis, no live Anthropic API call, no
SDK subprocess. No file under apps/api/app/ is touched by this module — the
demo tenant is verification data, not production surface.

Mock strategy (mirrors tests/unit/test_actor_seam.py exactly):
    - Patch the client factory (app.core.model_client.make_client, through
      model_doubles.factory) so the gate is handed a double whose
      messages.create returns a fake tool_use block.
    - Patch app.services.actor_seam._fetch_history (AsyncMock) so no real
      psycopg2 connection is attempted.
    - Patch app.services.actor_seam._langfuse to None so no Langfuse call is
      attempted either.
    - asyncio.run() drives the async call_actor_gate; no real event loop
      setup needed.

Skip-path tests assert api_mock.assert_not_called() — that is what
distinguishes "the skip engaged" from "the model happened to approve".
Non-skip-path tests assert api_mock.call_count >= 1 and deliberately do NOT
assert the verdict itself; the mock's return shape is not the subject under
test on those paths.

Discrepancy vs 19-02-PLAN.md recorded here per CLAUDE.md's "source wins"
directive (see also VER01_DEMO_TENANT_SPEC["tighten_only_reachability_note"]
and 19-02-SUMMARY.md § Deviations): validate_tighten_only
(capability_service.py) rejects every enabled:False -> True transition unless
the skill's own PLATFORM_CAPABILITY_DEFAULTS entry already has enabled=True —
and every platform default ships enabled=False (capability_service.py's own
docstring: "in practice re-enabling a disabled skill is not reachable through
this route — a chosen consequence, not a surprise"; confirmed independently by
test_capability_routes.py::test_patch_rejects_each_loosening_field's
`({"enabled": False}, {"enabled": True})` case). So the two enabled=True rows
in VER01_DEMO_TENANT_ENVELOPES below are NOT reachable via a tighten-only
PATCH starting from a fresh envelope — "enabled" must be seeded directly (a
fixture/DB-seeded row), the same way CLEAN_TENANT_ENVELOPES already is.
test_demo_envelopes_are_reachable_under_tighten_only therefore proves
reachability only for the five OTHER comparable fields (rate_limit,
constraints.max_amount_cents, the two boolean gates, actor_mode) — exactly
the field list 19-02-PLAN.md's own <action> block names, which does not
include "enabled".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.services.actor_seam import call_actor_gate
from app.services.capability_service import (
    PLATFORM_CAPABILITY_DEFAULTS,
    validate_tighten_only,
)
from tests.model_doubles import factory, ledger

_MODULE = "app.services.actor_seam"

_CONV_ID = "ver01-demo-conv-0001"
_AGENT_ID = "ver01-demo-agent-0001"

# ---------------------------------------------------------------------------
# (a) VER01_DEMO_TENANT_ENVELOPES — the demo tenant's capability posture,
# executable data rather than prose (mirrors CLEAN_TENANT_ENVELOPES's shape).
# ---------------------------------------------------------------------------


def _platform_default_row(skill: str) -> dict[str, Any]:
    """A demo-tenant row that is a straight copy of the skill's shipped
    platform default (used for the four skills the demo leaves disabled)."""
    d = PLATFORM_CAPABILITY_DEFAULTS[skill]
    return {
        "skill": skill,
        "enabled": d["enabled"],
        "rate_limit": d["rate_limit"],
        "constraints": dict(d["constraints"]),
        "requires_confirmation": d["requires_confirmation"],
        "requires_identity_verification": d["requires_identity_verification"],
        "actor_mode": d["actor_mode"],
    }


VER01_DEMO_TENANT_ENVELOPES: list[dict[str, Any]] = [
    {
        "skill": "issue_refund",
        "enabled": True,
        "rate_limit": "5/hour",
        # 499, not 500 — the whole point. settings.ACTOR_SKIP_MAX_AMOUNT_CENTS
        # defaults to 500 (config.py) and actor_seam.py's Step A skip
        # condition is `max_env < settings.ACTOR_SKIP_MAX_AMOUNT_CENTS`
        # (strictly less-than). 499 is therefore the LARGEST ceiling at which
        # the Actor's low-value skip engages and the refund leg completes
        # deterministically with no model call. Any larger value re-opens the
        # require_human dead end described in 19-01-PLAN.md § OD-2.
        "constraints": {"max_amount_cents": 499},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "actor_mode": "always-on",
    },
    {
        "skill": "place_order",
        "enabled": True,
        "rate_limit": "5/hour",
        # This one deliberately does NOT engage the skip (20 000 cents is
        # nowhere near the 500-cent threshold) — the demo's place_order leg
        # reaches the live Actor and can return require_human with no
        # resolution route. That is the accepted residual gap, T-19-04
        # (19-01-PLAN.md § OD-2), made an observable, tested fact by
        # test_demo_place_order_envelope_does_not_engage_skip below rather
        # than an assumption.
        "constraints": {"max_amount_cents": 20000},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "actor_mode": "always-on",
    },
    _platform_default_row("cancel_order"),
    _platform_default_row("update_subscription"),
    _platform_default_row("book_slot"),
    _platform_default_row("update_customer_record"),
]

_ISSUE_REFUND_ENVELOPE = next(
    row for row in VER01_DEMO_TENANT_ENVELOPES if row["skill"] == "issue_refund"
)
_PLACE_ORDER_ENVELOPE = next(
    row for row in VER01_DEMO_TENANT_ENVELOPES if row["skill"] == "place_order"
)

# ---------------------------------------------------------------------------
# (b) VER01_DEMO_TENANT_SPEC — the environmental preconditions the human UAT
# run in 19-05-PLAN.md depends on (mirrors CLEAN_TENANT_SPEC's shape).
# ---------------------------------------------------------------------------

VER01_DEMO_TENANT_SPEC: dict[str, Any] = {
    # Exactly two skills are enabled for this demo tenant.
    "enabled_skills": ["issue_refund", "place_order"],
    # The refund leg is Actor-skip-deterministic: no live model call, no
    # network variance, same verdict every run.
    "actor_skip_deterministic_skills": ["issue_refund"],
    # The place_order leg reaches the live Actor and can legitimately return
    # require_human with no resolution route in this codebase today — the
    # accepted disposition of T-19-04 (19-01-PLAN.md § OD-2), not a bug.
    "actor_live_no_resolution_skills": ["place_order"],
    # A live Shopify test credential is required to exercise the order leg
    # end to end (VER-01's live gate, 19-05-PLAN.md).
    "requires_live_credential": {"place_order": "shopify"},
    # No container runtime anywhere in this demo's precondition set (CLAUDE.md
    # rule 9) — local Postgres, local Redis, `uvicorn`,
    # `celery -A app.worker.celery_app worker`.
    "local_process_only": True,
    # Discrepancy note (see module docstring for the full derivation): the two
    # enabled=True rows above cannot be reached from a fresh envelope via the
    # shipped tighten-only PATCH route — validate_tighten_only rejects every
    # enabled:False -> True transition because every PLATFORM_CAPABILITY_DEFAULTS
    # entry ships enabled=False. They must be seeded directly (fixture/DB row),
    # same as CLEAN_TENANT_ENVELOPES already is. The five OTHER comparable
    # fields (rate_limit, constraints.max_amount_cents, the two boolean gates,
    # actor_mode) ARE reachable via tighten-only from the platform default —
    # proven by test_demo_envelopes_are_reachable_under_tighten_only.
    "tighten_only_reachability_note": (
        "enabled=True is seeded, not PATCHed; the other five comparable "
        "fields are PATCH-reachable from the platform default."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_use_block(verdict: str = "approve", rationale: str = "Test rationale.") -> MagicMock:
    """Create a fake tool_use content block mimicking an Anthropic API response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_verdict"
    block.input = {"verdict": verdict, "rationale": rationale}
    return block


def _make_api_response(*blocks: MagicMock) -> MagicMock:
    """Create a fake anthropic messages.create response with the given content blocks."""
    response = MagicMock()
    response.content = list(blocks)
    return response


def _run_gate(skill: str, snapshot: dict, api_mock: MagicMock) -> tuple[str, str]:
    """Drive call_actor_gate through asyncio.run with every external dependency mocked."""
    with (
        factory(SimpleNamespace(messages=SimpleNamespace(create=api_mock))),
        patch(f"{_MODULE}._fetch_history", AsyncMock(return_value=[])),
        patch(f"{_MODULE}._langfuse", None),
    ):
        return asyncio.run(
            call_actor_gate(
                skill,
                {"order_id": "ver01-demo-order-1", "amount_cents": 499},
                snapshot,
                _CONV_ID,
                _AGENT_ID,
                "",
                ledger=ledger(),
            )
        )


# ---------------------------------------------------------------------------
# Actor skip boundary — proven on both sides of the strict inequality.
# ---------------------------------------------------------------------------


def test_actor_skip_engages_for_demo_refund_envelope():
    """The demo tenant's issue_refund envelope (499 cents, requires_confirmation
    False) engages the skip: exactly ("approve", "skip:low_value_below_threshold"),
    with the Anthropic client never invoked."""
    api_mock = MagicMock()

    decision, rationale = _run_gate("issue_refund", _ISSUE_REFUND_ENVELOPE, api_mock)

    assert (decision, rationale) == ("approve", "skip:low_value_below_threshold")
    api_mock.assert_not_called()


def test_actor_skip_does_not_engage_at_exactly_the_threshold():
    """max_amount_cents == settings.ACTOR_SKIP_MAX_AMOUNT_CENTS (exactly) must NOT
    skip — the comparison in actor_seam.py is strictly less-than."""
    snapshot = {
        "enabled": True,
        "requires_confirmation": False,
        "constraints": {"max_amount_cents": settings.ACTOR_SKIP_MAX_AMOUNT_CENTS},
    }
    api_mock = MagicMock(return_value=_make_api_response(_make_tool_use_block("approve")))

    _run_gate("issue_refund", snapshot, api_mock)

    assert api_mock.call_count >= 1


def test_actor_skip_engages_one_cent_below_the_threshold():
    """settings.ACTOR_SKIP_MAX_AMOUNT_CENTS - 1 engages the skip — the other side
    of the same strict-inequality boundary as the test above."""
    snapshot = {
        "enabled": True,
        "requires_confirmation": False,
        "constraints": {"max_amount_cents": settings.ACTOR_SKIP_MAX_AMOUNT_CENTS - 1},
    }
    api_mock = MagicMock()

    decision, rationale = _run_gate("issue_refund", snapshot, api_mock)

    assert (decision, rationale) == ("approve", "skip:low_value_below_threshold")
    api_mock.assert_not_called()


def test_actor_skip_does_not_engage_when_requires_confirmation_true():
    """Both AND-terms of the skip condition must hold simultaneously: a
    sub-threshold ceiling with requires_confirmation=True still reaches Haiku."""
    snapshot = {
        "enabled": True,
        "requires_confirmation": True,
        "constraints": {"max_amount_cents": settings.ACTOR_SKIP_MAX_AMOUNT_CENTS - 1},
    }
    api_mock = MagicMock(return_value=_make_api_response(_make_tool_use_block("approve")))

    _run_gate("issue_refund", snapshot, api_mock)

    assert api_mock.call_count >= 1


def test_actor_skip_does_not_engage_when_max_amount_cents_absent():
    """constraints carrying no max_amount_cents key resolves to None, so an
    unbounded skill always reaches the live Actor."""
    snapshot = {
        "enabled": True,
        "requires_confirmation": False,
        "constraints": {},
    }
    api_mock = MagicMock(return_value=_make_api_response(_make_tool_use_block("approve")))

    _run_gate("issue_refund", snapshot, api_mock)

    assert api_mock.call_count >= 1


def test_demo_place_order_envelope_does_not_engage_skip():
    """The demo tenant's place_order envelope does NOT engage the skip — making
    the accepted require_human residual gap (T-19-04) a tested fact rather than
    a claim. The verdict itself is not asserted; only that Haiku was reached."""
    api_mock = MagicMock(return_value=_make_api_response(_make_tool_use_block("approve")))

    _run_gate("place_order", _PLACE_ORDER_ENVELOPE, api_mock)

    assert api_mock.call_count >= 1


def test_demo_refund_ceiling_is_below_the_configured_skip_threshold():
    """The 499 literal in VER01_DEMO_TENANT_ENVELOPES stays a literal, but this
    assertion is read against settings.ACTOR_SKIP_MAX_AMOUNT_CENTS rather than a
    second hard-coded 500 — so lowering the setting fails this test loudly
    instead of silently invalidating the demo configuration."""
    assert _ISSUE_REFUND_ENVELOPE["constraints"]["max_amount_cents"] == 499
    assert 499 < settings.ACTOR_SKIP_MAX_AMOUNT_CENTS


def test_demo_envelopes_are_reachable_under_tighten_only():
    """Every demo envelope's five non-`enabled` comparable fields
    (rate_limit, constraints.max_amount_cents, the two boolean gates,
    actor_mode) are at least as strict as PLATFORM_CAPABILITY_DEFAULTS'
    matching entry, proven by calling the shipped validate_tighten_only
    itself rather than reimplementing its comparison logic — so the demo
    posture (minus the seeded `enabled` flag; see module docstring) is
    reachable through the real tighten-only PATCH route from a fresh
    envelope, not a hand-edited row."""
    comparable_fields = (
        "rate_limit",
        "constraints",
        "requires_confirmation",
        "requires_identity_verification",
        "actor_mode",
    )
    for row in VER01_DEMO_TENANT_ENVELOPES:
        skill = row["skill"]
        current = PLATFORM_CAPABILITY_DEFAULTS[skill]
        proposed = {field: row[field] for field in comparable_fields}
        proposed["skill"] = skill

        reason = validate_tighten_only(current, proposed)

        assert reason is None, f"{skill}: rejected as {reason!r}"
