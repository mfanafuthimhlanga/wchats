"""
Unit companion for VER-01 SC3's gated adversarial harness (Phase 19, plan 19-04).

Proves every claim the gate rests on that does not require live infrastructure:
corpus size and shape, integer-cents discipline, the attempted-versus-findings
accounting, the provider_not_configured invalid-run guard, and the ordering
proof that the red-team window is open before the first probe fires.

Imports the corpus, driver, and summariser FROM the gated harness
(tests/integration/test_ver01_adversarial_harness.py) rather than duplicating
them -- this works because tests/, tests/unit/, and tests/integration/ are all
packages with __init__.py, so importing the integration module neither runs
its fixtures nor applies its pytestmark skip to this file.

Patch targets -- note the asymmetry versus a module that imports
app.services.red_team_probe directly at its own top level:
    tests.integration.test_ver01_adversarial_harness.invoke_probe_tool /
    red_team_mode -- the harness module binds these as its OWN module-level
    globals (initialised to None, populated lazily by its
    _load_probe_substrate() helper -- see that module's docstring). A direct
    `from X import Y` binds the name into the importing module's own
    namespace, so patching it there — not on app.services.red_team_probe
    itself — is what actually intercepts run_adversarial_corpus's calls.
    Patching also pre-empts _load_probe_substrate()'s real import entirely:
    once patched, invoke_probe_tool is no longer None, so the lazy loader's
    own guard short-circuits to a no-op and this file never touches
    app.core.config's Settings() validation.

Every boundary mocked here: no Postgres, no Redis, no live Anthropic API
call, no SDK subprocess.

Covers exactly the 12 node ids the plan names:
    test_corpus_has_at_least_100_entries
    test_corpus_ids_are_unique
    test_corpus_entries_have_the_required_keys_and_known_skills
    test_corpus_amounts_are_int_cents_and_never_float
    test_corpus_mutating_entries_have_distinct_idempotency_keys
    test_summarise_marks_empty_run_invalid
    test_summarise_marks_run_invalid_on_provider_not_configured
    test_summarise_attempted_equals_sum_of_verdict_counts
    test_summarise_counts_repeated_verdict_tags_separately
    test_summarise_flags_succeeded_on_expected_denied_entry
    test_summarise_ignores_succeeded_on_entry_expected_to_succeed
    test_all_probes_inside_red_team_mode
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.red_team_probe import PROBE_SKILL_TOOLS, ProbeToolResult
from tests.integration.test_ver01_adversarial_harness import (
    ADVERSARIAL_MESSAGE_CORPUS,
    run_adversarial_corpus,
    summarise_probe_run,
)

# ---------------------------------------------------------------------------
# Shared helpers -- copied unchanged from test_red_team_rtx_runners.py so this
# file cannot drift from the seven verdict tags in red_team_probe.py.
# ---------------------------------------------------------------------------

_VERDICT_TEXT: dict[str, str] = {
    "capability_denied": (
        "Access denied: capability envelope denied this request (reason: disabled). "
        "Contact your administrator to enable this tool."
    ),
    "identity_required": (
        "This action requires identity verification. Please verify your identity with "
        "a one-time code before proceeding."
    ),
    "rate_denied": (
        "Request denied by rate or constraint check (reason: rate_limit). "
        "Please wait before retrying."
    ),
    "actor_blocked": "Action blocked by security policy. Please contact support.",
    "awaiting_approval": (
        "This action requires human approval before it can execute. "
        "A confirmation request has been created (ID: abc-123)."
    ),
    "provider_not_configured": "No integration credential configured for skill 'issue_refund'",
    "succeeded": "[STUB] Refund of 1000 cents issued for order rtx-probe-order.",
}
_VERDICT_IS_ERROR: dict[str, bool] = {
    "capability_denied": True,
    "identity_required": True,
    "rate_denied": True,
    "actor_blocked": True,
    "awaiting_approval": False,
    "provider_not_configured": True,
    "succeeded": False,
}


def _response(tag: str) -> dict:
    """Build a dispatcher-shaped response dict whose text carries `tag`'s vocabulary."""
    return {
        "content": [{"type": "text", "text": _VERDICT_TEXT[tag]}],
        "is_error": _VERDICT_IS_ERROR[tag],
    }


def _fake_result(skill: str, tag: str) -> ProbeToolResult:
    return ProbeToolResult.from_dispatcher_response(skill, _response(tag))


def _make_red_team_mode_mock() -> MagicMock:
    """A patchable red_team_mode() replacement usable as `with red_team_mode():`.

    __exit__ explicitly returns False so an exception inside the `with` block is
    never silently swallowed by MagicMock's default (truthy) __exit__.
    """
    mock = MagicMock()
    mock.return_value.__exit__.return_value = False
    return mock


def _floats_in(value):
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _floats_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _floats_in(v)


# ---------------------------------------------------------------------------
# Corpus shape
# ---------------------------------------------------------------------------


def test_corpus_has_at_least_100_entries():
    assert len(ADVERSARIAL_MESSAGE_CORPUS) >= 100


def test_corpus_ids_are_unique():
    ids = [entry["id"] for entry in ADVERSARIAL_MESSAGE_CORPUS]
    assert len(ids) == len(set(ids))


def test_corpus_entries_have_the_required_keys_and_known_skills():
    required_keys = {"id", "skill", "args", "attack_class", "expected_denied"}
    for entry in ADVERSARIAL_MESSAGE_CORPUS:
        missing = required_keys - set(entry)
        assert not missing, f"{entry.get('id', '<no id>')} missing keys: {missing}"
        assert entry["skill"] in PROBE_SKILL_TOOLS, (
            f"{entry['id']} names skill {entry['skill']!r}, not a known PROBE_SKILL_TOOLS key"
        )
        assert isinstance(entry["expected_denied"], bool)


def test_corpus_amounts_are_int_cents_and_never_float():
    for entry in ADVERSARIAL_MESSAGE_CORPUS:
        floats_found = list(_floats_in(entry["args"]))
        assert not floats_found, f"{entry['id']} args contain float value(s): {floats_found}"


def test_corpus_mutating_entries_have_distinct_idempotency_keys():
    # confirm_action is the one non-mutating skill (mutating=False in TOOL_REGISTRY) --
    # its ConfirmActionInput schema carries no idempotency_key field.
    keys = [
        entry["args"]["idempotency_key"]
        for entry in ADVERSARIAL_MESSAGE_CORPUS
        if entry["skill"] != "confirm_action"
    ]
    assert keys, "expected at least one mutating corpus entry"
    assert len(keys) == len(set(keys)), "every mutating entry must carry a fresh idempotency_key"


# ---------------------------------------------------------------------------
# summarise_probe_run accounting
# ---------------------------------------------------------------------------


def test_summarise_marks_empty_run_invalid():
    summary = summarise_probe_run([], [])
    assert summary["attempted"] == 0
    assert summary["invalid"] is True


def test_summarise_marks_run_invalid_on_provider_not_configured():
    entries = [
        {"id": f"m{i}", "skill": "issue_refund", "expected_denied": True} for i in range(100)
    ]
    results = [_fake_result("issue_refund", "identity_required") for _ in range(99)] + [
        _fake_result("issue_refund", "provider_not_configured")
    ]
    summary = summarise_probe_run(entries, results)
    assert summary["invalid"] is True
    assert any("provider_not_configured" in reason for reason in summary["invalid_reasons"])


def test_summarise_attempted_equals_sum_of_verdict_counts():
    entries = [
        {"id": f"m{i}", "skill": "cancel_order", "expected_denied": False} for i in range(5)
    ]
    tags = ["succeeded", "rate_denied", "rate_denied", "capability_denied", "actor_blocked"]
    results = [_fake_result("cancel_order", tag) for tag in tags]

    summary = summarise_probe_run(entries, results)

    assert summary["attempted"] == 5
    assert summary["attempted"] == sum(summary["by_verdict"].values())


def test_summarise_counts_repeated_verdict_tags_separately():
    entries = [
        {"id": f"m{i}", "skill": "cancel_order", "expected_denied": False} for i in range(2)
    ]
    results = [
        _fake_result("cancel_order", "rate_denied"),
        _fake_result("cancel_order", "rate_denied"),
    ]

    summary = summarise_probe_run(entries, results)

    assert summary["by_verdict"]["rate_denied"] == 2


def test_summarise_flags_succeeded_on_expected_denied_entry():
    entries = [{"id": "x1", "skill": "issue_refund", "expected_denied": True}]
    results = [_fake_result("issue_refund", "succeeded")]

    summary = summarise_probe_run(entries, results)

    assert summary["unauthorized_mutations"] == [("x1", "issue_refund", "succeeded")]


def test_summarise_ignores_succeeded_on_entry_expected_to_succeed():
    entries = [{"id": "x2", "skill": "place_order", "expected_denied": False}]
    results = [_fake_result("place_order", "succeeded")]

    summary = summarise_probe_run(entries, results)

    assert summary["unauthorized_mutations"] == []


# ---------------------------------------------------------------------------
# Window-discipline ordering proof (load-bearing -- T-19-03)
# ---------------------------------------------------------------------------


def test_all_probes_inside_red_team_mode():
    """Proves ordering, not merely co-occurrence: the first recorded event is
    the window entry, the last is the window exit, every invoke_probe_tool
    event falls strictly between them, and the window was entered exactly
    once for the whole corpus. A test that only asserted
    mode_mock.call_count >= 1 would pass for a harness that opened the window
    AFTER the probes -- precisely the defect this guards against.
    """
    event_order: list[str] = []

    mock_mode = _make_red_team_mode_mock()
    mock_mode.return_value.__enter__.side_effect = lambda: event_order.append("window_enter")

    def _record_exit(*_args):
        event_order.append("window_exit")
        return False

    mock_mode.return_value.__exit__.side_effect = _record_exit

    call_counter = {"n": 0}

    async def _record_invoke(skill, args):
        call_counter["n"] += 1
        event_order.append(f"invoke_{call_counter['n']}")
        return _response("rate_denied")

    mock_invoke = AsyncMock(side_effect=_record_invoke)

    with (
        patch(
            "tests.integration.test_ver01_adversarial_harness.invoke_probe_tool", mock_invoke
        ),
        patch("tests.integration.test_ver01_adversarial_harness.red_team_mode", mock_mode),
    ):
        asyncio.run(run_adversarial_corpus(ADVERSARIAL_MESSAGE_CORPUS))

    assert event_order[0] == "window_enter"
    assert event_order[-1] == "window_exit"
    invoke_events = [e for e in event_order if e.startswith("invoke_")]
    assert len(invoke_events) == len(ADVERSARIAL_MESSAGE_CORPUS)
    # every invoke event falls strictly between the single enter and single exit
    assert event_order == ["window_enter", *invoke_events, "window_exit"]
    assert mock_mode.call_count == 1
