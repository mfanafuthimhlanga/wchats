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
    No reader. Two writers stamp an identity beside the verdict it belongs to,
    `eval_service` on `eval_results.judge_identity` (tenant migration 0023) and
    `retrieval_eval` on `retrieval_metrics.judge_identity` (0020). #53 is the
    first thing to read either and group by them.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import Any

_REQUIRED_TEXT = ("model", "reasoning_effort", "prompt_version")

#: Which prompt every Judge in this system was given, the third field below.
#:
#: NOTHING IN THIS REPO VERSIONS A JUDGE PROMPT, and this constant is the honest
#: minimum rather than a version anybody chose. The `prompt_versions` table
#: (control migration 0018) holds an agent's soul, one immutable row per soul
#: edit, and no judge reads it or writes to it. Every Judge prompt belongs to
#: ragas, which carries each collections metric's prompt text inside the
#: installed package, and nothing here authors or edits one. That covers the four
#: offline metrics `eval_service` scores and the live-traffic Faithfulness
#: `retrieval_eval` scores, which is why the constant lives here beside the type
#: rather than in either of them.
#:
#: So the identifier is the artifact the prompt text ships in, read off the
#: installed distribution rather than typed here. A literal would go stale the
#: next time `uv sync` resolves a different 0.4.x with different prompts
#: underneath it, and a calibration figure would then group two prompts under
#: one key. The day a Judge prompt is written in this repo, that prompt's own
#: version replaces this and the identity gets finer-grained rather than wider.
JUDGE_PROMPT_VERSION = f"ragas-{importlib.metadata.version('ragas')}"


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
        model:           the CONFIGURED model, the one the routing table names and
                         the request asks for, for example `gpt-5.6-luna`. Not the
                         served model. A calibration figure keys on what was
                         chosen, because that is the thing an operator changes and
                         re-measures. Whether the provider served something else is
                         a different question, and `ModelCall.served_model` plus the
                         shadow audit answer it per call.
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
