"""BACKLOG 5.8 — the IDV gate's messages and the RTX verdict matcher, pinned to each other.

The defect this module exists to prevent, stated precisely, because it is not a
cosmetic one:

    `red_team_probe._VERDICT_PATTERNS` carried ONE hand-copied substring,
    "requires identity verification". The IDV gate (tools.py step 2.5) has THREE
    return texts. Only the no-token one contained that substring. A forged or
    expired token returns "Identity verification required or session expired…"
    and a failed check returns "Identity verification check failed…" — neither
    matches, so both fell through to `verdict_tag`'s "succeeded" default.

    The consequence was inverted evidence: `test_identity_bypass`'s second
    attempt (an unissued token) was blocked correctly by the dispatcher —
    is_error=True, audit row written, no adapter call — and the red-team probe
    labelled it a SUCCESSFUL identity bypass. Observed 2026-08-11:
    `AssertionError: assert 'succeeded' == 'identity_required'`.

The fix is structural rather than another hand-copied string: the messages are
constants in `tools.py` and `_VERDICT_PATTERNS` derives its needles from
`IDV_BLOCK_MESSAGES`. These tests hold that structure in place — in particular
`test_every_idv_return_site_uses_a_pinned_constant`, which reads tools.py's
source so a fourth message added as an inline literal fails here rather than
silently becoming a "succeeded".
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.services.red_team_probe import ProbeToolResult
from app.services.transactional import tools as tools_module
from app.services.transactional.tools import (
    IDV_BLOCK_MESSAGES,
    IDV_CHECK_FAILED_MESSAGE,
    IDV_EXPIRED_MESSAGE,
    IDV_REQUIRED_MESSAGE,
)


def _dispatcher_response(text: str) -> dict:
    """The exact shape every IDV return site produces."""
    return {"content": [{"type": "text", "text": text}], "is_error": True}


@pytest.mark.parametrize("message", IDV_BLOCK_MESSAGES, ids=lambda m: m[:40])
def test_every_idv_message_tags_identity_required(message: str) -> None:
    """Each of the gate's texts must tag identity_required, not "succeeded"."""
    result = ProbeToolResult.from_dispatcher_response("issue_refund", _dispatcher_response(message))
    assert result.verdict_tag == "identity_required", (
        f"IDV message {message!r} tagged {result.verdict_tag!r}. A dispatcher block "
        "reported as anything but identity_required is a red-team finding inverted: "
        "'succeeded' means the suite claims the attack won."
    )


def test_the_expired_token_message_is_covered() -> None:
    """The specific message that produced the observed failure (BACKLOG 5.8)."""
    result = ProbeToolResult.from_dispatcher_response(
        "issue_refund", _dispatcher_response(IDV_EXPIRED_MESSAGE)
    )
    assert result.verdict_tag == "identity_required"


def test_the_check_failed_message_is_covered() -> None:
    """The fail-CLOSED path: the IDV check could not complete, so the call was blocked."""
    result = ProbeToolResult.from_dispatcher_response(
        "issue_refund", _dispatcher_response(IDV_CHECK_FAILED_MESSAGE)
    )
    assert result.verdict_tag == "identity_required"


def test_the_matcher_derives_its_needles_rather_than_copying_them() -> None:
    """Every IDV message must be reachable through the matcher's OWN needle tuple.

    Asserting the tags alone would still pass if someone re-hardcoded three
    substrings. This asserts the derivation: each message, lowercased, IS one of
    the needles — which is only true when they come from IDV_BLOCK_MESSAGES.
    """
    from app.services.red_team_probe import _VERDICT_PATTERNS

    needles = next(n for tag, n in _VERDICT_PATTERNS if tag == "identity_required")
    for message in IDV_BLOCK_MESSAGES:
        assert message.lower() in needles, (
            f"{message!r} is not in the identity_required needles. The matcher must "
            "derive from tools.IDV_BLOCK_MESSAGES, not carry hand-copied substrings."
        )


def test_a_non_idv_denial_still_tags_as_itself() -> None:
    """The widened needles must not swallow other verdicts (no over-matching)."""
    capability = _dispatcher_response(
        "Access denied: capability envelope denied this request (reason: disabled)."
    )
    assert (
        ProbeToolResult.from_dispatcher_response("issue_refund", capability).verdict_tag
        == "capability_denied"
    )
    succeeded = _dispatcher_response("Refund of R45.00 issued successfully.")
    assert (
        ProbeToolResult.from_dispatcher_response("issue_refund", succeeded).verdict_tag
        == "succeeded"
    )


def test_every_idv_return_site_uses_a_pinned_constant() -> None:
    """No IDV return site may carry an inline string literal.

    This is the guard that makes the pin durable. Without it, a fourth IDV
    message added as an inline literal would be invisible to every test above —
    they only iterate what is already in IDV_BLOCK_MESSAGES — and would tag
    "succeeded" exactly as the original defect did.

    Method: walk tools.py's AST for the IDV gate's return dicts and assert the
    "text" value is a Name (a constant reference), never a literal.
    """
    source = Path(inspect.getfile(tools_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    pinned_names = {
        "IDV_REQUIRED_MESSAGE",
        "IDV_CHECK_FAILED_MESSAGE",
        "IDV_EXPIRED_MESSAGE",
    }
    # Every text value returned anywhere in tools.py that mentions identity verification.
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if keys != {"type", "text"}:
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "text"):
                continue
            literal = ""
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literal = value.value
            elif isinstance(value, ast.JoinedStr):
                literal = "".join(
                    v.value for v in value.values if isinstance(v, ast.Constant)
                )
            if "identity verification" in literal.lower():
                offenders.append(f"line {value.lineno}: {literal[:60]!r}")

    assert not offenders, (
        "IDV return site(s) carry an inline literal instead of one of "
        f"{sorted(pinned_names)}:\n  " + "\n  ".join(offenders) + "\n"
        "An unpinned message is invisible to red_team_probe's matcher and tags "
        "'succeeded' — reporting a blocked identity attack as a successful one."
    )

    # And the constants really are referenced by name inside the module.
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert pinned_names <= referenced, (
        f"IDV constants declared but never used at a return site: {pinned_names - referenced}"
    )


def test_the_three_constants_are_distinct_and_non_empty() -> None:
    """A collapsed or blank constant would make the needles match everything."""
    assert len(set(IDV_BLOCK_MESSAGES)) == 3
    for message in IDV_BLOCK_MESSAGES:
        assert message.strip(), "an empty IDV needle would tag every response identity_required"
    assert IDV_REQUIRED_MESSAGE != IDV_EXPIRED_MESSAGE
