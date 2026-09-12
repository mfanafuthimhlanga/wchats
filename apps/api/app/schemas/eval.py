"""Request/response schemas for the eval routes.

Only the golden registration path uses typed schemas today; the read routes
return dicts shaped by the run's stored record (see app/api/v1/evals.py).
"""

from typing import Literal

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """One message before the question, in the shape tenant migration 0028 stores.

    The bounds mirror `_read_turn_history`'s, which is the read that decides what
    rides on a model call and stays authoritative: 4,000 characters a row,
    because an assistant row carries an answer and its citations block where a
    customer row carries a question. They are repeated rather than imported
    because a request schema may not reach into a worker task module, and a
    request larger than the read would accept is worth refusing at the door.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class GoldenPair(BaseModel):
    """One owner-authored question with the answer a correct response must match.

    `turns` is the conversation the question was asked in, oldest first, and the
    question itself is never repeated in it (#227). A follow-up like "how do I
    start the dev server" is only answerable inside the conversation that named
    the project, so a golden pair for one needs the turns that bind it.
    """

    question: str = Field(min_length=1, max_length=2000)
    reference_answer: str = Field(min_length=1, max_length=8000)
    turns: list[ConversationTurn] = Field(default_factory=list, max_length=40)


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


class GoldenDraftRequest(BaseModel):
    """How many chunks to draft one golden pair each from (#203).

    Bounded because every draft is one model call and the drafts travel back as
    job events, of which the job read returns the last 100.
    """

    n: int = Field(15, ge=10, le=30)


class GoldenDraftResponse(BaseModel):
    status: str
    job_id: str
    agent_id: str
    n: int
