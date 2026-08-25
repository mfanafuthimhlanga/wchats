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

WR-03 fix addendum (19-REVIEW.md), added by a later fix pass -- not one of the
12 original node ids, extending the file rather than replacing any of them:
    test_corpus_entries_declare_a_known_rate_track
    test_confused_deputy_and_injection_entries_never_hit_an_exhausted_rate_window
        The structural proof that no confused-deputy or injection-labeled
        corpus entry is ever preceded, within its own (rate_track, skill)
        key, by enough same-key calls to have exhausted that skill's
        CLEAN_TENANT_ENVELOPES rate_limit. This is the only RUNNABLE proof of
        the WR-03 fix in this environment -- the gated integration harness
        itself needs a live local Postgres + Redis this environment does not
        have. Limits are read from CLEAN_TENANT_ENVELOPES itself (imported,
        never re-typed as a literal), so the test cannot silently drift from
        the real envelope config.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.red_team_probe import CLEAN_TENANT_ENVELOPES, PROBE_SKILL_TOOLS, ProbeToolResult
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
    """Build a dispatcher-shaped WIRE dict whose text carries `tag`'s vocabulary.

    Still a dict, deliberately: this feeds `from_dispatcher_response`, which is
    the path that only ever sees wire (the SDK victim turn).
    """
    return {
        "content": [{"type": "text", "text": _VERDICT_TEXT[tag]}],
        "is_error": _VERDICT_IS_ERROR[tag],
    }


def _fake_result(skill: str, tag: str) -> ProbeToolResult:
    """The verdict `invoke_probe_tool` returns for `tag`, as the type it returns."""
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
        return _fake_result(skill, "rate_denied")

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


# ---------------------------------------------------------------------------
# WR-03 (19-REVIEW.md) fix addendum: rate-window isolation by agent track.
# ---------------------------------------------------------------------------


def test_corpus_entries_declare_a_known_rate_track():
    """Every corpus entry names one of the three tracks the clean_tenant
    fixture actually provisions an agent for (_insert_clean_agent, one call
    per track in the integration module) -- an entry naming anything else
    would have no matching agent_id at run time.
    """
    known_tracks = {"primary", "confused_deputy", "injection"}
    for entry in ADVERSARIAL_MESSAGE_CORPUS:
        assert "rate_track" in entry, f"{entry['id']} is missing a rate_track key"
        assert entry["rate_track"] in known_tracks, (
            f"{entry['id']} names rate_track {entry['rate_track']!r}, not one of {known_tracks}"
        )


def _rate_limit_max_calls(skill: str) -> int | None:
    """Read `skill`'s configured rate_limit straight from CLEAN_TENANT_ENVELOPES
    (the same fixture data _insert_clean_agent inserts, unmodified, for every
    track's agent) and return the max-calls-per-window integer, or None if the
    skill carries no rate_limit at all (e.g. confirm_action, which is denied at
    the capability layer before any rate check ever runs).

    Deliberately reads the real config rather than hardcoding a duplicate copy
    -- the whole point of this guard is that it cannot silently drift from
    CLEAN_TENANT_ENVELOPES if a limit changes.
    """
    for row in CLEAN_TENANT_ENVELOPES:
        if row["skill"] == skill:
            rate_limit = row.get("rate_limit")
            if not rate_limit:
                return None
            max_calls_str, _, _window = rate_limit.partition("/")
            return int(max_calls_str)
    return None


# The two attack classes WR-03 found being pre-empted by rate exhaustion --
# both need a live enforcement layer (the Actor gate) past the rate check to
# mean anything at all; a rate_denied verdict on one of these proves nothing
# about Actor-gate or injection-resistance coverage.
_ATTACK_CLASSES_REQUIRING_AN_UNEXHAUSTED_WINDOW = frozenset(
    {"confused_deputy", "conversation_injection", "content_injection"}
)


def test_confused_deputy_and_injection_entries_never_hit_an_exhausted_rate_window():
    """WR-03 (19-REVIEW.md): structurally proves that no confused-deputy or
    injection-labeled corpus entry is ever preceded, within its own
    (rate_track, skill) key, by enough prior same-key same-skill calls to
    have exhausted that skill's CLEAN_TENANT_ENVELOPES rate_limit.

    This is the real fix's guard: it fails today if someone merges two
    tracks back into one, reorders the corpus so a rate-chain group runs
    ahead of a confused_deputy/injection group on the same track, or lowers
    a rate_limit -- and it names every offending entry rather than reporting
    a bare count, so a failure is actionable without re-deriving the corpus
    by hand.

    Entirely computed from ADVERSARIAL_MESSAGE_CORPUS + CLEAN_TENANT_ENVELOPES;
    no DB, no Redis, no network, no live enforcement code imported.
    """
    calls_seen: dict[tuple[str, str], int] = {}
    offenders: list[str] = []

    for entry in ADVERSARIAL_MESSAGE_CORPUS:
        key = (entry["rate_track"], entry["skill"])
        prior_calls = calls_seen.get(key, 0)

        if entry["attack_class"] in _ATTACK_CLASSES_REQUIRING_AN_UNEXHAUSTED_WINDOW:
            max_calls = _rate_limit_max_calls(entry["skill"])
            if max_calls is not None and prior_calls >= max_calls:
                offenders.append(
                    f"{entry['id']} (skill={entry['skill']!r}, "
                    f"rate_track={entry['rate_track']!r}, "
                    f"attack_class={entry['attack_class']!r}) is preceded by "
                    f"{prior_calls} prior call(s) on the same (rate_track, skill) key, "
                    f"at or beyond the configured limit of {max_calls} -- it would be "
                    "rate_denied before ever reaching the layer its attack_class claims "
                    "to exercise"
                )

        calls_seen[key] = prior_calls + 1

    assert not offenders, (
        f"{len(offenders)} corpus entr{'y' if len(offenders) == 1 else 'ies'} would be "
        "pre-empted by rate-limit exhaustion (WR-03 regression):\n" + "\n".join(offenders)
    )
