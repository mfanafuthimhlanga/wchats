"""Request/response schemas for the eval routes.

Only the golden registration path uses typed schemas today; the read routes
return dicts shaped by the run's stored record (see app/api/v1/evals.py).
"""

from pydantic import BaseModel, Field


class GoldenPair(BaseModel):
    """One owner-authored question with the answer a correct response must match."""

    question: str = Field(min_length=1, max_length=2000)
    reference_answer: str = Field(min_length=1, max_length=8000)


class GoldenScenariosRegisterRequest(BaseModel):
    """A batch of golden pairs, typically read from one owner-written file.

    There is deliberately no authored_by field. A caller able to name the human
    is a caller able to name any human (the label_service decision), so the
    route derives provenance from the authenticated caller instead.
    """

    pairs: list[GoldenPair] = Field(min_length=1, max_length=100)
    source_file: str | None = Field(
        None,
        max_length=200,
        description="Name of the file the pairs were read from, recorded in provenance.",
    )


class GoldenScenariosRegisterResponse(BaseModel):
    registered: int
    skipped_duplicates: list[str]
    golden_total: int
