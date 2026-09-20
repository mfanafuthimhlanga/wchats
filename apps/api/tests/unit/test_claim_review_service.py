"""The review of flagged claims: the type, the listing and the writer (#290 step 3).

The service's SQL is exercised against a real PostgreSQL in
`tests/integration/test_claim_reviews_db.py`; nothing here says what Postgres
does. A fake psycopg2 connection replays rows shaped as 0031 and 0027 store
them and records the statements the writer sends, so each test says what the
Tenant is shown and what lands.

Rules pinned here, each one the review exists to keep:
  - the Tenant sees the judge's flags and nothing else of the judge's: no
    reason, no verdict, no supported claim
  - an answer about a claim the run did not flag, or whose statement is not the
    one at that position, is refused before any row is written
  - a second answer on a claim replaces the first, which is the upsert
  - each tenant revision the code degrades on is named: behind 0031 an empty
    listing with a log line; behind 0032 `reviews_available=False` on read and
    `ClaimReviewsUnavailable` on write
  - a scenario whose stored claims are unreadable is dropped and counted, and
    costs no other scenario
"""

from __future__ import annotations

import json

import psycopg2
import pytest
import structlog

from app.domain.claim_review import ClaimReview, InvalidClaimReview
from app.services import claim_review_service as svc

RUN = "11111111-1111-1111-1111-111111111111"

CLAIMS_S1 = [
    {"statement": "Delivery costs R30 in Tembisa.", "supported": True, "reason": "in the policy"},
    {"statement": "Orders over R500 ship free.", "supported": False, "reason": "not in the text"},
    {"statement": "The fee is R60 outside Tembisa.", "supported": False, "reason": "not in the text"},
]
CLAIMS_S2 = [{"statement": "Returns take 30 days.", "supported": True, "reason": "carried"}]

FLAGGED_ROWS = [
    ("s1", json.dumps(CLAIMS_S1), "what does delivery cost?", "Delivery costs R30 ...", json.dumps(["Delivery is R30 in Tembisa."])),
    ("s2", CLAIMS_S2, "how do returns work?", "Returns take 30 days.", ["Returns take 30 days."]),
]


class _Cursor:
    def __init__(self, conn) -> None:
        self.conn = conn
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.executed.append((" ".join(sql.split()), params))
        if "FROM eval_results" in sql:
            if self.conn.behind_0031:
                raise psycopg2.errors.UndefinedColumn('column res.claims does not exist')
            self._rows = self.conn.flagged_rows
        elif "claim_reviews" in sql:
            if self.conn.behind_0032:
                raise psycopg2.errors.UndefinedTable('relation "claim_reviews" does not exist')
            self._rows = self.conn.review_rows
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, flagged_rows=FLAGGED_ROWS, review_rows=(), behind_0031=False, behind_0032=False) -> None:
        self.flagged_rows = list(flagged_rows)
        self.review_rows = list(review_rows)
        self.behind_0031 = behind_0031
        self.behind_0032 = behind_0032
        self.executed: list[tuple[str, dict]] = []
        self.rollbacks = 0
        self.committed = False

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.committed = True

    def close(self):
        pass


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()
    monkeypatch.setattr(svc.psycopg2, "connect", lambda *a, **kw: c)
    return c


def _inserts(conn) -> list[dict]:
    return [p for s, p in conn.executed if "INSERT INTO claim_reviews" in s]


# ---------------------------------------------------------------------------
# The type.
# ---------------------------------------------------------------------------
class TestClaimReview:
    @pytest.mark.parametrize("field, value, match", [
        ("scenario_id", " ", "scenario_id"),
        ("statement", "", "statement"),
        ("position", -1, "position"),
        ("position", True, "position"),
        ("supported", "yes", "supported as a bool"),
    ])
    def test_a_field_that_would_misreport_is_refused(self, field, value, match):
        fields = {"eval_run_id": RUN, "scenario_id": "s1", "position": 0, "statement": "x", "supported": True}
        fields[field] = value
        with pytest.raises(InvalidClaimReview, match=match):
            ClaimReview(**fields)


# ---------------------------------------------------------------------------
# The listing.
# ---------------------------------------------------------------------------
class TestReadFlaggedClaims:
    def test_only_the_claims_the_judge_did_not_find_are_listed_grouped_by_answer(self, conn):
        result = svc.read_flagged_claims(RUN, "postgresql://tenant")
        assert [s.scenario_id for s in result.scenarios] == ["s1"]
        [s1] = result.scenarios
        assert [(c.position, c.statement) for c in s1.claims] == [
            (1, "Orders over R500 ship free."),
            (2, "The fee is R60 outside Tembisa."),
        ]
        assert (result.flagged, result.answered, result.reviews_available, result.dropped_rows) == (2, 0, True, 0)

    def test_the_position_is_the_index_in_the_stored_list_not_among_the_flags(self, conn):
        """Position 0 is a supported claim, so the first flag is position 1."""
        assert svc.read_flagged_claims(RUN, "x").scenarios[0].claims[0].position == 1

    def test_the_payload_carries_no_reason_and_no_judge_verdict(self, conn):
        payload = svc.read_flagged_claims(RUN, "x").scenarios[0].payload
        assert set(payload) == {"scenario_id", "question", "response", "retrieved_contexts", "claims"}
        assert set(payload["claims"][0]) == {"position", "statement", "review"}
        assert "not in the text" not in json.dumps(payload)

    def test_the_answer_and_the_passages_are_shown_once_per_scenario(self, conn):
        s = svc.read_flagged_claims(RUN, "x").scenarios[0]
        assert s.question == "what does delivery cost?"
        assert s.response.startswith("Delivery costs R30")
        assert s.retrieved_contexts == ("Delivery is R30 in Tembisa.",)

    def test_a_stored_answer_is_read_beside_its_flag(self, conn):
        conn.review_rows = [("s1", 2, True)]
        result = svc.read_flagged_claims(RUN, "x")
        assert [c.review for c in result.scenarios[0].claims] == [None, True]
        assert result.answered == 1

    def test_a_run_with_no_flags_lists_nothing(self, conn):
        conn.flagged_rows = [("s2", CLAIMS_S2, "q", "r", [])]
        assert svc.read_flagged_claims(RUN, "x").scenarios == ()

    def test_a_row_with_no_sample_still_lists_its_flags_with_no_passage(self, conn):
        conn.flagged_rows = [("s1", CLAIMS_S1, None, None, None)]
        s = svc.read_flagged_claims(RUN, "x").scenarios[0]
        assert (s.question, s.response, s.retrieved_contexts) == ("", "", ())

    def test_a_scenario_with_unreadable_claims_is_dropped_and_counted_and_costs_no_other(self, conn):
        conn.flagged_rows = [("s0", [{"statement": "", "supported": False}], "q", "r", []), *FLAGGED_ROWS]
        with structlog.testing.capture_logs() as logs:
            result = svc.read_flagged_claims(RUN, "x")
        assert [s.scenario_id for s in result.scenarios] == ["s1"]
        assert result.dropped_rows == 1
        [dropped] = [e for e in logs if e["event"] == "claim_reviews.claims_unreadable"]
        assert dropped["scenario_id"] == "s0"

    def test_a_tenant_behind_0032_lists_flags_and_says_no_answer_can_be_stored(self, conn):
        conn.behind_0032 = True
        with structlog.testing.capture_logs() as logs:
            result = svc.read_flagged_claims(RUN, "x")
        assert result.flagged == 2
        assert result.reviews_available is False
        assert conn.rollbacks == 1
        assert [e["event"] for e in logs] == ["claim_reviews.table_absent"]

    def test_a_tenant_behind_0031_lists_nothing_and_says_why(self, conn):
        conn.behind_0031 = True
        with structlog.testing.capture_logs() as logs:
            result = svc.read_flagged_claims(RUN, "x")
        assert result.scenarios == ()
        assert [e["event"] for e in logs] == ["claim_reviews.claims_column_absent"]
        assert not [s for s, _ in conn.executed if "claim_reviews" in s], "no answers are looked up for a run with no claims"


# ---------------------------------------------------------------------------
# The writer.
# ---------------------------------------------------------------------------
def _answer(position=1, statement="Orders over R500 ship free.", supported=False, scenario="s1", run=RUN):
    return ClaimReview(run, scenario, position, statement, supported)


class TestWriteClaimReviews:
    def test_an_answer_on_a_flagged_claim_is_upserted_on_the_replace_key(self, conn):
        assert svc.write_claim_reviews(RUN, [_answer()], "x") == 1
        [(sql, params)] = [(s, p) for s, p in conn.executed if "INSERT INTO claim_reviews" in s]
        assert "ON CONFLICT (eval_run_id, scenario_id, position)" in sql
        assert (params["scenario_id"], params["position"], params["supported"]) == ("s1", 1, False)
        assert conn.committed

    def test_an_answer_about_a_claim_the_judge_did_not_flag_is_refused_before_any_write(self, conn):
        with pytest.raises(InvalidClaimReview, match="flagged no claim"):
            svc.write_claim_reviews(RUN, [_answer(position=0, statement="Delivery costs R30 in Tembisa.")], "x")
        assert _inserts(conn) == []

    def test_an_answer_whose_statement_is_not_the_flagged_one_is_refused(self, conn):
        with pytest.raises(InvalidClaimReview, match="not the statement"):
            svc.write_claim_reviews(RUN, [_answer(statement="Orders over R900 ship free.")], "x")

    def test_an_answer_naming_another_run_is_refused_without_a_connection(self, monkeypatch):
        monkeypatch.setattr(svc.psycopg2, "connect", lambda *a, **kw: pytest.fail("connected"))
        with pytest.raises(InvalidClaimReview, match="names run"):
            svc.write_claim_reviews(RUN, [_answer(run="22222222-2222-2222-2222-222222222222")], "x")

    def test_one_bad_answer_in_a_batch_stores_nothing(self, conn):
        with pytest.raises(InvalidClaimReview):
            svc.write_claim_reviews(RUN, [_answer(), _answer(position=7, statement="x")], "x")
        assert _inserts(conn) == []
        assert not conn.committed

    def test_no_answers_opens_no_connection(self, monkeypatch):
        monkeypatch.setattr(svc.psycopg2, "connect", lambda *a, **kw: pytest.fail("connected"))
        assert svc.write_claim_reviews(RUN, [], "x") == 0

    def test_a_tenant_behind_0032_raises_unavailable_not_a_bare_database_error(self, conn):
        conn.behind_0032 = True
        with pytest.raises(svc.ClaimReviewsUnavailable, match="0032"):
            svc.write_claim_reviews(RUN, [_answer()], "x")
        assert conn.rollbacks == 1 and not conn.committed

    def test_a_tenant_behind_0031_refuses_every_answer_as_unflagged(self, conn):
        conn.behind_0031 = True
        with structlog.testing.capture_logs(), pytest.raises(InvalidClaimReview, match="flagged no claim"):
            svc.write_claim_reviews(RUN, [_answer()], "x")

    def test_the_log_line_counts_the_confirmed_unsupported(self, conn):
        with structlog.testing.capture_logs() as logs:
            svc.write_claim_reviews(RUN, [_answer(), _answer(position=2, statement="The fee is R60 outside Tembisa.", supported=True)], "x")
        [written] = [e for e in logs if e["event"] == "claim_reviews.written"]
        assert (written["answers"], written["confirmed_unsupported"]) == (2, 1)
