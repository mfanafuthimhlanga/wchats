"""ToolResult, one transactional tool call's verdict (ticket #45, issue #7).

WHY THE WIRE DICT WAS NOT ENOUGH
    `_execute_transactional_tool` handed every caller the SDK wire dict, and
    that dict carries exactly one bit of verdict: `is_error`, present or absent.
    Four things the dispatcher can decide have to fit through one bit, so two of
    them collide. A successful order and an order the Actor gate escalated to a
    human both return `{"content": [...]}` with no `is_error`, so a caller
    asking "did this action happen?" has to read the English text and hope the
    wording never changes.

    Reading prose is what the red-team probe was left doing, and BACKLOG 5.8
    records the bill. A hand-copied substring matched one of the identity gate's
    three messages, so the RTX-03 probe reported an attack SUCCEEDING against a
    call the dispatcher had correctly blocked. That particular needle is derived
    now, but every remaining prose match is the same bet.

    `Outcome` is that bit widened to the four answers the dispatcher actually
    produces. The wire is unchanged, byte for byte, because the SDK and the
    agent read it. The distinction lives in the type.

WHAT EACH OUTCOME MEANS
    ok              The action happened, or an earlier identical call's result
                    is being replayed.
    denied          A gate refused it. The capability envelope, the identity
                    gate, the rate and constraint ceiling, the Actor seam, or
                    the recorded-mode seam on the eval path. Nothing broke.
    requires_human  The Actor seam escalated. Nothing ran and nothing failed;
                    an approver decides. Never `ok`, never `error`.
    error           Something broke, or the caller sent something the dispatcher
                    cannot act on. This is the one worth paging someone about.

WHY A FROZEN STDLIB DATACLASS RATHER THAN PYDANTIC
    Same reason as `chunk.py`. `transactional_schemas.py` is pydantic because it
    validates tool arguments arriving from a model and publishes a JSON schema.
    ToolResult validates nothing and is constructed only by our own dispatcher,
    so the whole contract fits in the field list.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    """What the dispatcher decided. See the module docstring for each meaning."""

    ok = "ok"
    denied = "denied"
    requires_human = "requires_human"
    error = "error"


#: The outcomes the SDK wire reports as errors. `ok` and `requires_human` are
#: not errors on the wire and were not before this type existed, which is what
#: makes them indistinguishable there and is the whole reason for `Outcome`.
_WIRE_ERRORS = (Outcome.denied, Outcome.error)


@dataclass(frozen=True)
class ToolResult:
    """One transactional tool call's verdict, as the dispatcher decided it.

    Args:
        skill:       Canonical skill name, e.g. "issue_refund".
        outcome:     The verdict. See the module docstring.
        text:        The customer-facing text this call returns. On the wire it
                     becomes the single text block the agent reads.
        stored_wire: Set ONLY by the idempotency replay branch, which returns
                     the wire dict an earlier completed call stored in the
                     tenant DB. Those bytes are arbitrary JSON, so rebuilding
                     them from `text` would mangle any stored result that is not
                     exactly one text block. `to_wire` returns this object
                     itself when it is set.
    """

    skill: str
    outcome: Outcome
    text: str
    stored_wire: dict | None = None

    @property
    def is_error(self) -> bool:
        """What this result spends the wire's single error bit on."""
        return self.outcome in _WIRE_ERRORS


def to_wire(result: ToolResult) -> dict:
    """Build the SDK tool-response dict. The one place a ToolResult becomes bytes.

    Every transactional branch reaches the SDK through here, so the mapping from
    outcome to `is_error` is written once, next to the enum it reads.
    """
    if result.stored_wire is not None:
        return result.stored_wire
    wire: dict = {"content": [{"type": "text", "text": result.text}]}
    if result.is_error:
        wire["is_error"] = True
    return wire


def wire_text(wire: dict | None) -> str:
    """Join the text blocks of a wire dict. The reading half of `to_wire`.

    Two callers read a wire dict they did not build: the idempotency replay
    branch, which reads a result stored by an earlier call, and the red-team
    probe, which reads what the SDK delivers as a ToolResultBlock. Both get the
    same joining rule from here rather than each writing their own.
    """
    blocks = (wire or {}).get("content") or []
    return "\n".join(block.get("text", "") for block in blocks if isinstance(block, dict))
