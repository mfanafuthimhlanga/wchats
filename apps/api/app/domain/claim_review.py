"""ClaimReview, the Tenant's answer on one claim the faithfulness judge flagged (#290 step 3).

WHY THE TENANT ANSWERS AND NOT THE OWNER
    The judge decides a claim statement by statement and marks the ones it
    cannot find in the retrieved text. On the platform benchmark it finds every
    planted sentence and also flags a median of four claims on every real
    answer. Whether those four are in the documents is a fact about the
    Tenant's Corpus, and the Tenant is the one party who knows it. So the
    review is one question per flagged claim, "is this in your documents?",
    yes or no, and this record is one answer.

THE ANSWER IS A LABEL, NOT A VERDICT
    A Tenant's yes says the claim is carried by the documents, whatever the
    retrieved text said. A no confirms the judge's flag. Neither is a Verdict,
    the Harness's ship or block: the gate still reads the score until the
    judge's precision on reviewed claims is known, and then #270 decides the
    rule. The answer also lands in the platform benchmark as a truth row (owner
    decision 2026-09-17), which is why it carries the statement it was about
    and not only a position.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class InvalidClaimReview(ValueError):
    """An answer that would misreport what the Tenant said, refused on construction."""


@dataclass(frozen=True)
class ClaimReview:
    """One Tenant answer on one flagged claim.

    Args:
        eval_run_id: the run the claim was judged in.
        scenario_id: the scenario whose answer carried the claim.
        position:    the claim's index in `eval_results.claims` on that
                     scenario's faithfulness row. Zero or above.
        statement:   the claim, copied so the answer reads without the judge row.
        supported:   the Tenant's answer. True is "yes, it is in my documents".
        reviewed_at: when, or None for an answer not yet stored.
    """

    eval_run_id: str
    scenario_id: str
    position: int
    statement: str
    supported: bool
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("eval_run_id", "scenario_id", "statement"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidClaimReview(f"ClaimReview needs a {name}, got {value!r}")
        if isinstance(self.position, bool) or not isinstance(self.position, int) or self.position < 0:
            raise InvalidClaimReview(
                f"ClaimReview needs position as an int at zero or above, got {self.position!r}"
            )
        if not isinstance(self.supported, bool):
            raise InvalidClaimReview(
                f"ClaimReview needs supported as a bool, got {self.supported!r}"
            )
