"""JudgeIdentity, the three things a calibration figure is measured against (ticket #47).

WHY THE KEY IS NOT THE WORD "judge"
    A calibration figure says how well one Judge agrees with a human. Three things
    move that agreement: the model that ran, the reasoning effort it ran at, and
    the prompt it was given. Decision #34 priced the Judge floor at effort `none`
    and states that any effort increase is re-measured rather than assumed, so
    effort belongs in the key beside the model. A figure stored under `judge`
    alone reads as covering a Judge nobody measured the moment any one of the
    three changes.

WHY CONSTRUCTION IS LOUD
    An empty field silently widens the key. Two runs on different prompts would
    share an identity, verdicts would group together, and the agreement number
    would be computed across two populations. Each field is refused here rather
    than at whichever reader notices first, the same choice `ModelCall` made.

WHY reasoning_effort IS A STRING AND NOT AN ENUM
    It is the literal the routing table in `app.core.model_client` carries and the
    literal that goes on the wire, so the two cannot drift. Pinning it to one
    provider's set of efforts would make this domain type refuse a Judge served by
    any other provider, which is narrower than the identity needs.

WHAT IS NOT HERE
    No recording and no reader. #53 stores these beside a verdict and groups by
    them. This slice builds the type and nothing else.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_REQUIRED_TEXT = ("model", "reasoning_effort", "prompt_version")


class InvalidJudgeIdentity(ValueError):
    """A calibration key that would group two different Judges together.

    A ValueError, so callers that already catch ValueError keep catching it, the
    same choice `InvalidModelCall` made.
    """


def _require_text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidJudgeIdentity(f"JudgeIdentity needs a {name}, got {value!r}")


@dataclass(frozen=True)
class JudgeIdentity:
    """Which Judge produced a verdict, at the grain calibration compares on.

    Frozen, so equality and hashing come from the three fields and a set of
    identities is the natural grouping key for a run's verdicts.

    Args:
        model:           the served model, as the price book names it, for
                         example `gpt-5.6-luna`.
        reasoning_effort: the effort the call ran at, as the provider spells it.
                         `none` is one of OpenAI's efforts and is text like any
                         other, never a stand-in for a missing value.
        prompt_version:  the identifier of the prompt the Judge was given.

    Raises:
        InvalidJudgeIdentity: any of the three is empty, blank or not a string.
    """

    model: str
    reasoning_effort: str
    prompt_version: str

    def __post_init__(self) -> None:
        for name in _REQUIRED_TEXT:
            _require_text(name, getattr(self, name))
