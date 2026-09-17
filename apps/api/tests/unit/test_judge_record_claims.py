"""The claims a faithfulness row carries, and the two refusals that keep them honest (#290).

A faithfulness score is the share of an answer's atomic statements the judge
found supported. `JudgeRecord.claims` is that list. Two rules make it safe to
read beside `score`:

  - a list beside no score is refused: the claims are the score's working, and
    there is no score to explain
  - a list whose share of supported claims is not the score is refused: it was
    lifted from a different answer

No database here, the domain rung only. `tests/unit/test_faithfulness_claims.py`
is where the scoring loop puts them on the row and the writer on the column.
"""

from __future__ import annotations

import pytest

from app.domain.judge_record import Claim, InvalidJudgeRecord, JudgeRecord

FOUND = Claim(statement="returns take 30 days", supported=True, reason="in the policy")
MISSING = Claim(statement="refunds reach the card in two days", supported=False, reason="absent")


def _record(**overrides) -> JudgeRecord:
    fields = {
        "scenario_id": "s1",
        "metric": "faithfulness",
        "score": 0.5,
        "threshold": 0.8,
        "claims": (FOUND, MISSING),
    }
    fields.update(overrides)
    return JudgeRecord.scored(**fields)


class TestAClaimIsRefusedWhenItWouldMisreport:
    def test_a_blank_statement_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="statement"):
            Claim(statement="  ", supported=True, reason="r")

    def test_a_non_boolean_verdict_is_refused(self):
        """ragas returns the verdict as 0/1; the caller converts, this refuses."""
        with pytest.raises(InvalidJudgeRecord, match="supported as a bool"):
            Claim(statement="s", supported=1, reason="r")  # type: ignore[arg-type]

    def test_a_claim_round_trips_through_its_payload(self):
        assert Claim.from_payload(MISSING.payload) == MISSING


class TestTheClaimsReproduceTheScore:
    def test_one_of_two_supported_is_a_half(self):
        assert _record().claims == (FOUND, MISSING)

    def test_every_claim_supported_is_one(self):
        assert _record(score=1.0, claims=(FOUND, FOUND)).claims == (FOUND, FOUND)

    def test_seven_of_eight_supported_is_the_planted_lie_score(self):
        """The case #290 exists for: one lie in eight scores 0.875, above the gate."""
        claims = (FOUND,) * 7 + (MISSING,)
        record = _record(score=0.875, claims=claims)
        assert record.binary_verdict is True
        assert [c.supported for c in record.claims] == [True] * 7 + [False]

    def test_claims_whose_share_is_not_the_score_are_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="some other row"):
            _record(score=1.0, claims=(FOUND, MISSING))

    def test_claims_beside_no_score_are_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="no score"):
            _record(score=None, claims=(FOUND,))

    def test_an_empty_list_is_refused_rather_than_read_as_no_claims(self):
        with pytest.raises(InvalidJudgeRecord, match="non-empty"):
            _record(score=1.0, claims=())

    def test_a_list_holding_something_other_than_a_claim_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="every claim as a Claim"):
            _record(claims=(FOUND, {"statement": "x", "supported": False}))


class TestNoClaimsIsARealState:
    def test_a_record_with_no_claims_still_builds(self):
        record = _record(claims=None)
        assert record.claims is None
        assert record.payload["claims"] is None

    def test_an_unscored_row_carries_no_claims(self):
        assert _record(score=None, claims=None).claims is None

    def test_a_sequence_is_held_as_a_tuple(self):
        assert _record(claims=[FOUND, MISSING]).claims == (FOUND, MISSING)


class TestThePayloadRoundTrips:
    def test_the_claims_render_as_plain_dicts(self):
        assert _record().payload["claims"] == [FOUND.payload, MISSING.payload]

    def test_a_record_with_claims_survives_a_round_trip(self):
        record = _record()
        assert JudgeRecord.from_payload(record.payload) == record

    def test_a_stored_list_that_no_longer_matches_its_score_is_refused_on_read(self):
        payload = _record().payload
        payload["score"] = 1.0
        payload["binary_verdict"] = True
        with pytest.raises(InvalidJudgeRecord, match="some other row"):
            JudgeRecord.from_payload(payload)

    def test_a_payload_whose_claims_are_not_a_sequence_is_refused(self):
        payload = _record().payload
        payload["claims"] = "two claims"
        with pytest.raises(InvalidJudgeRecord, match="claims as a sequence"):
            JudgeRecord.from_payload(payload)
