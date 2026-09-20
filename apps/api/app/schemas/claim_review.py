"""Request schema for the review of flagged claims (#290 step 3).

One answer per flagged claim: which claim, by scenario and position, the
statement as the page showed it, and yes or no. The statement travels with the
answer so the writer can refuse an answer about a claim other than the one
shown, and so the answer reads on its own in the benchmark.
"""

from pydantic import BaseModel, Field


class ClaimAnswer(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=200)
    position: int = Field(ge=0)
    statement: str = Field(min_length=1, max_length=4000)
    supported: bool


class ClaimReviewRequest(BaseModel):
    """A sitting's answers. Ten is the most a page asks at once; sixty is a whole run."""

    answers: list[ClaimAnswer] = Field(min_length=1, max_length=200)
