"""JudgeRecord, the decision one eval_results row carries (ticket 14, #51, slice 2).

Ticket criterion 2 says a judge row carries its dimension, its score, a binary
verdict with the threshold that produced it, the Judge identity and a ledger
reference. Everything here is about the one rule that makes those five safe to
read together: THE VERDICT MUST AGREE WITH THE NUMBERS STORED BESIDE IT, and a
row where it does not is refused rather than reported.

Two absences are load-bearing and both are tested as absences, never as zero or
False. A metric with no threshold gets no verdict, because context_precision and
context_recall have no gate anywhere in this codebase. A metric the judge did not
score gets no verdict and STILL GETS A ROW, because an absent row is
indistinguishable from a scenario nobody sent.

No database here. This is the domain rung and the type is the whole subject;
`tests/unit/test_eval_service.py` is where the writer puts these on the columns.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.domain.judge_identity import JudgeIdentity
from app.domain.judge_record import (
    InvalidJudgeRecord,
    JudgeRecord,
    verdict_for,
)

IDENTITY = JudgeIdentity(
    model="gpt-5.6-luna", reasoning_effort="none", prompt_version="ragas-0.4.3"
)


def _record(**overrides) -> JudgeRecord:
    """A scored, gated, passing row. Every test names only what it changes."""
    fields = {
        "scenario_id": "s1",
        "metric": "faithfulness",
        "score": 0.95,
        "threshold": 0.90,
        "judge_identity": IDENTITY,
        "ledger_purpose": "judge_faithfulness",
    }
    fields.update(overrides)
    return JudgeRecord.scored(**fields)


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


class TestTheRecordIsFrozen:
    """A decision cannot be edited after the run that made it."""

    @pytest.mark.parametrize(
        "field", ["scenario_id", "metric", "score", "threshold", "binary_verdict"]
    )
    def test_no_field_can_be_reassigned(self, field):
        record = _record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field, None)

    def test_two_records_with_the_same_decision_are_equal(self):
        """Frozen and comparable, so a set of records deduplicates on the decision."""
        assert _record() == _record()
        assert _record() != _record(score=0.5)


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


class TestTheVerdictFollowsTheThreshold:
    """`verdict_for` is the one comparison, and both sides of it are tested."""

    def test_a_score_above_the_threshold_passes(self):
        assert _record(score=0.95, threshold=0.90).binary_verdict is True

    def test_a_score_below_the_threshold_fails(self):
        assert _record(score=0.85, threshold=0.90).binary_verdict is False

    def test_a_score_exactly_on_the_threshold_passes(self):
        """The gate is `>=`, and this is the test that says which.

        "Faithfulness must be at least 0.90" is the sentence the threshold was
        written from, and 0.90 satisfies it. A `>` here would fail a run for
        hitting its target exactly, which is the kind of off-by-one that nobody
        notices until a deploy is blocked by a passing number.
        """
        assert _record(score=0.90, threshold=0.90).binary_verdict is True

    def test_a_zero_score_under_a_gate_is_a_real_failure_not_an_absence(self):
        """0.0 is a measurement. It fails, and False is the honest answer."""
        assert _record(score=0.0, threshold=0.90).binary_verdict is False

    @pytest.mark.parametrize(
        "score,threshold,expected",
        [
            (0.95, 0.90, True),
            (0.90, 0.90, True),
            (0.85, 0.90, False),
            (None, 0.90, None),
            (0.95, None, None),
            (None, None, None),
        ],
    )
    def test_verdict_for_is_the_whole_rule(self, score, threshold, expected):
        assert verdict_for(score, threshold) is expected


class TestNoGateMeansNoVerdict:
    """context_precision and context_recall have no threshold. They say so."""

    def test_a_record_with_no_threshold_carries_no_verdict(self):
        record = _record(metric="context_precision", score=0.4, threshold=None)
        assert record.threshold is None
        assert record.binary_verdict is None

    def test_a_low_score_with_no_threshold_is_still_not_a_failure(self):
        """The number the gate would have failed, with no gate to fail.

        Reporting False here would invent a gate. A reader aggregating verdicts
        would then count two ungated metrics as two failures on every row in the
        table.
        """
        assert _record(metric="context_recall", score=0.01, threshold=None).binary_verdict is None

    def test_the_verdict_is_none_exactly_when_the_threshold_is(self):
        """Both directions, so neither can be relaxed on its own."""
        assert _record(score=0.5, threshold=None).binary_verdict is None
        assert _record(score=0.5, threshold=0.9).binary_verdict is not None


class TestAnUnscoredMetricIsARowWithNoScore:
    """Criterion 2's quiet half: the row exists even when the judge returned nothing."""

    def test_a_record_with_no_score_carries_no_verdict(self):
        record = _record(score=None)
        assert record.score is None
        assert record.binary_verdict is None

    def test_the_record_still_builds_and_still_names_its_dimension(self):
        """The row is what makes the absence countable.

        A metric that produced nothing has to be visible as a row with no score.
        Leaving it out would make it indistinguishable from a scenario nobody
        sent, and a reader counting rows per scenario would lose the denominator
        rather than see the hole.
        """
        record = _record(score=None)
        assert record.metric == "faithfulness"
        assert record.scenario_id == "s1"
        assert record.judge_identity == IDENTITY

    def test_an_unscored_metric_under_a_gate_is_unknown_and_not_a_failure(self):
        assert _record(score=None, threshold=0.90).binary_verdict is None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestAWrongVerdictIsRefused:
    """The check that earns its keep on the way OUT of the database.

    Writers call `JudgeRecord.scored`, which derives the verdict, so they cannot
    supply a wrong one. `from_payload` can: a row written under a threshold that
    has since moved, or by a build whose comparison was wrong, arrives with all
    three fields already decided and disagreeing.
    """

    def test_a_pass_that_the_numbers_do_not_support_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="disagrees with its own numbers"):
            JudgeRecord(
                scenario_id="s1", metric="faithfulness",
                score=0.5, threshold=0.9, binary_verdict=True,
            )

    def test_a_failure_that_the_numbers_do_not_support_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="disagrees with its own numbers"):
            JudgeRecord(
                scenario_id="s1", metric="faithfulness",
                score=0.95, threshold=0.9, binary_verdict=False,
            )

    def test_a_verdict_with_no_threshold_behind_it_is_refused(self):
        """A gate nobody set cannot have produced an answer."""
        with pytest.raises(InvalidJudgeRecord):
            JudgeRecord(
                scenario_id="s1", metric="context_recall",
                score=0.95, threshold=None, binary_verdict=True,
            )

    def test_a_verdict_with_no_score_behind_it_is_refused(self):
        with pytest.raises(InvalidJudgeRecord):
            JudgeRecord(
                scenario_id="s1", metric="faithfulness",
                score=None, threshold=0.9, binary_verdict=False,
            )

    def test_a_missing_verdict_where_the_numbers_decide_one_is_refused(self):
        """The other direction. A gated, scored row may not say "unknown"."""
        with pytest.raises(InvalidJudgeRecord):
            JudgeRecord(
                scenario_id="s1", metric="faithfulness",
                score=0.95, threshold=0.9, binary_verdict=None,
            )


class TestTheFieldsAreRefusedWhenTheyWouldMisreport:
    def test_a_blank_scenario_id_is_refused(self):
        """A row nobody can place is the defect `run_ragas_eval` counts and drops."""
        with pytest.raises(InvalidJudgeRecord, match="scenario_id"):
            _record(scenario_id="  ")

    def test_a_blank_metric_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="metric"):
            _record(metric="")

    def test_a_nan_score_is_refused_rather_than_compared(self):
        """A NaN loses every comparison, so it would read as a quiet failure.

        `_score_samples` already converts a NaN cell to None. This is what stops
        that conversion being dropped upstream without anything going red.
        """
        with pytest.raises(InvalidJudgeRecord, match="NaN"):
            _record(score=float("nan"))

    def test_a_nan_threshold_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="NaN"):
            _record(threshold=float("nan"))

    def test_a_boolean_score_is_refused(self):
        """True is an int in Python and would compare against a threshold."""
        with pytest.raises(InvalidJudgeRecord, match="score"):
            _record(score=True)

    def test_a_non_numeric_score_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="score"):
            _record(score="0.9")

    def test_an_integer_score_is_held_as_a_float(self):
        """1 and 1.0 are the same reading and must not sort or compare apart."""
        record = _record(score=1, threshold=0.9)
        assert isinstance(record.score, float)
        assert record.score == 1.0

    def test_an_identity_that_is_not_a_judge_identity_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="judge_identity"):
            _record(judge_identity={"model": "gpt-5.6-luna"})

    def test_an_unknown_judge_is_none_rather_than_a_partial_identity(self):
        """`judge_identity_for` returns None for a route missing an effort."""
        assert _record(judge_identity=None).judge_identity is None

    def test_a_blank_ledger_purpose_is_refused(self):
        """None says no bucket was recorded. "" says one was and hides it."""
        with pytest.raises(InvalidJudgeRecord, match="ledger_purpose"):
            _record(ledger_purpose="   ")


# ---------------------------------------------------------------------------
# Payload round trip
# ---------------------------------------------------------------------------


class TestThePayloadRoundTrips:
    def test_the_payload_carries_the_seven_keys(self):
        assert sorted(_record().payload) == [
            "binary_verdict",
            "judge_identity",
            "ledger_purpose",
            "metric",
            "scenario_id",
            "score",
            "threshold",
        ]

    def test_the_payload_holds_no_other_scenarios_scores(self):
        """The blob this ticket removes, pinned as an absence.

        `detail` used to carry the whole score row, so a faithfulness row also
        held answer_relevancy, context_precision and context_recall. Four copies
        of every number, three of them not the row's own.
        """
        payload = _record().payload
        for other in ("answer_relevancy", "context_precision", "context_recall"):
            assert other not in payload

    @pytest.mark.parametrize(
        "overrides",
        [
            {},
            {"score": None},
            {"threshold": None},
            {"score": None, "threshold": None},
            {"judge_identity": None},
            {"ledger_purpose": None},
            {"score": 0.85},
            {"score": 0.90},
        ],
        ids=[
            "scored-and-gated", "unscored", "ungated", "neither",
            "no-judge", "no-ledger-bucket", "failing", "exactly-on-the-gate",
        ],
    )
    def test_a_record_survives_a_round_trip_unchanged(self, overrides):
        record = _record(**overrides)
        assert JudgeRecord.from_payload(record.payload) == record

    def test_the_identity_comes_back_as_a_judge_identity_not_a_dict(self):
        rebuilt = JudgeRecord.from_payload(_record().payload)
        assert isinstance(rebuilt.judge_identity, JudgeIdentity)
        assert rebuilt.judge_identity.reasoning_effort == "none"

    def test_a_stored_row_whose_verdict_moved_with_the_threshold_is_refused(self):
        """The scenario the round trip exists for.

        The row was written when the gate was 0.80 and 0.85 passed. The gate is
        0.90 now. Rewriting the stored payload's threshold without its verdict
        produces a row that claims a pass its own numbers refuse, and reading it
        raises instead of reporting it.
        """
        payload = _record(score=0.85, threshold=0.80).payload
        assert payload["binary_verdict"] is True
        payload["threshold"] = 0.90

        with pytest.raises(InvalidJudgeRecord, match="disagrees with its own numbers"):
            JudgeRecord.from_payload(payload)

    def test_a_payload_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(InvalidJudgeRecord, match="mapping"):
            JudgeRecord.from_payload([("metric", "faithfulness")])

    def test_a_payload_whose_identity_is_not_a_mapping_is_refused(self):
        payload = _record().payload
        payload["judge_identity"] = "gpt-5.6-luna"
        with pytest.raises(InvalidJudgeRecord, match="judge_identity"):
            JudgeRecord.from_payload(payload)

    def test_a_payload_missing_its_scenario_id_is_refused(self):
        payload = _record().payload
        del payload["scenario_id"]
        with pytest.raises(InvalidJudgeRecord, match="scenario_id"):
            JudgeRecord.from_payload(payload)

    def test_a_payload_whose_metric_is_not_a_string_is_refused(self):
        """The row names which of the four scores it is. A number cannot.

        `eval_results.metric` is read back and grouped on, so a metric stored as
        anything but text puts a score in a bucket no reader asks for.
        """
        payload = _record().payload
        payload["metric"] = 0.9
        with pytest.raises(InvalidJudgeRecord, match="metric"):
            JudgeRecord.from_payload(payload)
