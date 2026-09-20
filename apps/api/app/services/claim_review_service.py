"""The review of flagged claims: what a Tenant is asked, and what they answered (#290 step 3).

THE QUESTION IS THE JUDGE'S FLAG, THE ANSWER IS THE TENANT'S
    `eval_results.claims` (0031) holds every atomic statement the faithfulness
    judge lifted from an answer and whether it found each in the retrieved text.
    The review shows the Tenant only the ones it did not find, grouped under the
    answer they came from with the text the judge had, and asks "is this in
    your documents?". `read_flagged_claims` is that list. `write_claim_reviews`
    is the answers, into `claim_reviews` (0032), newest replacing last.

WHAT THE TENANT NEVER SEES
    The judge's `reason` and its `supported` value. The flag itself is the
    judge's decision, and that is the whole of what the page may carry; a label
    read beside the labeller's stated reason is the failure the calibrate-judge
    ledger records as lesson 3. The payload here drops both on purpose.

A REVIEW OF A CLAIM THE RUN NEVER FLAGGED IS REFUSED
    `write_claim_reviews` reads the run's flagged claims first and refuses an
    answer whose (scenario, position) is not among them, or whose statement is
    not the one at that position. An answer about a claim the judge did not
    raise is an answer to a question nobody asked, and it would land in the
    benchmark as truth.

TWO TENANT REVISIONS THIS DEGRADES ON, EACH NAMED
    Behind 0031 there is no `claims` column: the listing is empty and the log
    says why, and a write then refuses every answer as being about a claim
    nobody flagged. Behind 0032 there is no `claim_reviews` table: the listing
    carries `reviews_available=False` so the page can tell "nothing stored"
    from "nowhere to store", and a write raises `ClaimReviewsUnavailable`,
    which the route turns into 503 rather than 500.

Rung: `app.services` imports `app.domain`, `app.core` and third-party packages.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

import psycopg2
import structlog

from app.core.log_bounds import log_failure
from app.domain.claim_review import ClaimReview, InvalidClaimReview
from app.domain.judge_record import Claim, InvalidJudgeRecord

log = structlog.get_logger(__name__)

CONNECT_TIMEOUT_S = 10


class ClaimReviewsUnavailable(RuntimeError):
    """The tenant database has nowhere to store an answer (behind alembic_tenant 0032)."""


#: The faithfulness rows of one run that carry claims, with the text the judge
#: had. LEFT JOIN on the sample, because a run written before 0027 has no
#: sample row and its flags are still worth asking about, with no passage.
_SELECT_FLAGGED_SQL = """
    SELECT res.scenario_id, res.claims, es.user_input, es.response, es.retrieved_contexts
    FROM eval_results res
    LEFT JOIN eval_samples es
        ON es.eval_run_id = res.eval_run_id AND es.scenario_id = res.scenario_id
    WHERE res.eval_run_id = %(run_id)s::uuid
      AND res.metric = 'faithfulness'
      AND res.claims IS NOT NULL
    ORDER BY res.scenario_id
"""

_SELECT_REVIEWS_SQL = """
    SELECT scenario_id, position, supported
    FROM claim_reviews
    WHERE eval_run_id = %(run_id)s::uuid
"""

#: The upsert. The unique key (0032) is what makes a second answer replace the
#: first rather than sit beside it.
_UPSERT_REVIEW_SQL = """
    INSERT INTO claim_reviews (id, eval_run_id, scenario_id, position, statement, supported)
    VALUES (%(id)s::uuid, %(eval_run_id)s::uuid, %(scenario_id)s, %(position)s, %(statement)s, %(supported)s)
    ON CONFLICT (eval_run_id, scenario_id, position)
    DO UPDATE SET statement = EXCLUDED.statement, supported = EXCLUDED.supported, reviewed_at = now()
"""


@dataclass(frozen=True)
class FlaggedClaim:
    """One claim the judge did not find. `review` is the Tenant's answer, or None."""

    position: int
    statement: str
    review: bool | None = None

    @property
    def payload(self) -> dict:
        return {"position": self.position, "statement": self.statement, "review": self.review}


@dataclass(frozen=True)
class FlaggedScenario:
    """One answer with its flagged claims, the text the judge had shown once.

    No `reason` and no judge `supported`: the flag is the decision and the page
    carries nothing else of the judge's.
    """

    scenario_id: str
    question: str
    response: str
    retrieved_contexts: tuple[str, ...]
    claims: tuple[FlaggedClaim, ...]

    @property
    def payload(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "question": self.question,
            "response": self.response,
            "retrieved_contexts": list(self.retrieved_contexts),
            "claims": [c.payload for c in self.claims],
        }


@dataclass(frozen=True)
class FlaggedClaims:
    """A run's flags, and whether the tenant database can hold an answer to them."""

    scenarios: tuple[FlaggedScenario, ...] = ()
    reviews_available: bool = True
    dropped_rows: int = field(default=0)

    @property
    def flagged(self) -> int:
        return sum(len(s.claims) for s in self.scenarios)

    @property
    def answered(self) -> int:
        return sum(1 for s in self.scenarios for c in s.claims if c.review is not None)

    def statements(self) -> dict[tuple[str, int], str]:
        return {(s.scenario_id, c.position): c.statement for s in self.scenarios for c in s.claims}


def _loaded(stored: object) -> object:
    return json.loads(stored) if isinstance(stored, str) else stored


def _contexts_of(stored: object) -> tuple[str, ...]:
    raw = _loaded(stored)
    return tuple(str(c) for c in raw) if isinstance(raw, list) else ()


def _flagged_rows(conn, run_id: str) -> list[tuple] | None:
    """The run's claim-bearing faithfulness rows, or None on a tenant behind 0031."""
    try:
        with conn.cursor() as cur:
            cur.execute(_SELECT_FLAGGED_SQL, {"run_id": run_id})
            return cur.fetchall()
    except psycopg2.errors.UndefinedColumn:
        conn.rollback()
        log.warning("claim_reviews.claims_column_absent", eval_run_id=run_id,
                    detail="tenant DB predates alembic_tenant 0031; this run stored no claims to review")
        return None


def _review_rows(conn, run_id: str) -> list[tuple] | None:
    """The stored answers, or None on a tenant behind 0032."""
    try:
        with conn.cursor() as cur:
            cur.execute(_SELECT_REVIEWS_SQL, {"run_id": run_id})
            return cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        conn.rollback()
        log.warning("claim_reviews.table_absent", eval_run_id=run_id,
                    detail="tenant DB predates alembic_tenant 0032; no answer can be stored for it")
        return None


def _scenario(row: tuple, reviews: dict[tuple[str, int], bool]) -> FlaggedScenario | None:
    """One row's flags, or None when it carries none.

    Raises:
        InvalidJudgeRecord: the stored claims break a construction rule. The
            caller drops that scenario and no other, the choice `_record_of`
            makes for a run's record.
    """
    scenario_id, claims, question, response, contexts = row
    sid = str(scenario_id)
    raw = _loaded(claims)
    if not isinstance(raw, list):
        raise InvalidJudgeRecord(f"stored claims are {type(raw).__name__}, not a list")
    parsed = [Claim.from_payload(item) for item in raw]
    flagged = tuple(
        FlaggedClaim(position=i, statement=c.statement, review=reviews.get((sid, i)))
        for i, c in enumerate(parsed)
        if not c.supported
    )
    if not flagged:
        return None
    return FlaggedScenario(
        scenario_id=sid,
        question=question or "",
        response=response or "",
        retrieved_contexts=_contexts_of(contexts),
        claims=flagged,
    )


def _flagged(rows: Sequence[tuple] | None, reviews: Sequence[tuple] | None) -> FlaggedClaims:
    answered = {(str(r[0]), int(r[1])): bool(r[2]) for r in (reviews or [])}
    scenarios, dropped = [], 0
    for row in rows or []:
        try:
            scenario = _scenario(row, answered)
        except InvalidJudgeRecord as exc:
            log_failure(log, "claim_reviews.claims_unreadable", exc, scenario_id=str(row[0]))
            dropped += 1
            continue
        if scenario is not None:
            scenarios.append(scenario)
    return FlaggedClaims(
        scenarios=tuple(scenarios), reviews_available=reviews is not None, dropped_rows=dropped
    )


def read_flagged_claims(run_id: str, conn_str: str) -> FlaggedClaims:
    """Every claim the judge flagged on one run, grouped by answer, with the Tenant's answers."""
    conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
    try:
        rows = _flagged_rows(conn, run_id)
        reviews = _review_rows(conn, run_id) if rows is not None else None
    finally:
        conn.close()
    return _flagged(rows, reviews)


def _refuse_strays(run_id: str, answers: Sequence[ClaimReview], flagged: dict[tuple[str, int], str]) -> None:
    for answer in answers:
        key = (answer.scenario_id, answer.position)
        if key not in flagged:
            raise InvalidClaimReview(
                f"the judge flagged no claim at {key} on run {run_id}; an answer "
                "about it would be an answer to a question nobody asked"
            )
        if flagged[key] != answer.statement:
            raise InvalidClaimReview(
                f"the claim at {key} on run {run_id} is not the statement the answer names"
            )


def write_claim_reviews(run_id: str, answers: Sequence[ClaimReview], conn_str: str) -> int:
    """Store the Tenant's answers, refusing any about a claim the run did not flag.

    Returns the number stored. Zero answers opens no connection.

    Raises:
        InvalidClaimReview: an answer names another run, a (scenario, position)
            the judge did not flag on this run, or a statement that is not the
            one at that position.
        ClaimReviewsUnavailable: the tenant database predates 0032.
    """
    if not answers:
        return 0
    for answer in answers:
        if answer.eval_run_id != run_id:
            raise InvalidClaimReview(f"an answer names run {answer.eval_run_id}, not {run_id}")
    conn = psycopg2.connect(conn_str, connect_timeout=CONNECT_TIMEOUT_S)
    try:
        flagged = _flagged(_flagged_rows(conn, run_id), [])
        _refuse_strays(run_id, answers, flagged.statements())
        try:
            with conn.cursor() as cur:
                for answer in answers:
                    cur.execute(_UPSERT_REVIEW_SQL, {
                        "id": str(uuid.uuid4()),
                        "eval_run_id": run_id,
                        "scenario_id": answer.scenario_id,
                        "position": answer.position,
                        "statement": answer.statement,
                        "supported": answer.supported,
                    })
        except psycopg2.errors.UndefinedTable as exc:
            conn.rollback()
            raise ClaimReviewsUnavailable(
                "the tenant database predates alembic_tenant 0032 and has no claim_reviews table"
            ) from exc
        conn.commit()
    finally:
        conn.close()
    log.info("claim_reviews.written", eval_run_id=run_id, answers=len(answers),
             confirmed_unsupported=sum(1 for a in answers if not a.supported))
    return len(answers)
