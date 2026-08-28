"""RedTeamResult, the run record and the completeness rule that reads it (ticket 15, issue #52).

THE DEFECT THIS FILE OPENS ON
    `run_coverage` returns `complete` True for a run in which a vector observed
    one answered probe out of the three sequences it asked for, as long as that
    vector reported something. Its own `sequences_completed >= sequences_requested`
    guard catches the truncated case, and the P4 review comment above it says so
    at length, but nothing in the shipped record says how many INDEPENDENT
    attempts a vector was supposed to make. The dispatcher calls each runner
    once. Three sequences inside one attacker loop under one shared timeout are
    not three attempts.

    So the boundary that matters is a vector one attempt short of k, and the
    class at the bottom of this file is that boundary and nothing else.

HOW THE BOUNDARY FIXTURE IS BUILT, AND WHY IT LOOKS FUSSY
    A fixture where every vector has zero attempts makes "incomplete" come out
    true for a reason that has nothing to do with k, and one where every vector
    has k makes "complete" come out true the same accidental way. Either would
    pass while the rule was wrong.

    `_result_with` therefore puts six vectors ON k in both boundary tests and
    moves one vector by one attempt between them. The pair differs by a single
    number, so the assertions can only be answering the question the rule is
    being tested for.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.domain.red_team_result import (
    RED_TEAM_VECTORS,
    InvalidRedTeamResult,
    RedTeamResult,
    Severity,
    VectorOutcome,
    worst_severity,
)

#: The k ticket 15 asks for. Three independent attempts per vector.
K = 3

#: The one vector that moves between the two boundary tests. Any of the seven
#: would do; naming it once keeps the pair honest about differing by one number.
SHORT_VECTOR = "data_leakage"


def _all_at(attempts: int) -> dict[str, int]:
    """Every dispatched vector at the same attempt count."""
    return {vector: attempts for vector in RED_TEAM_VECTORS}


def _result_with(attempts_by_vector: dict[str, int], k: int = K) -> RedTeamResult:
    """A clean result (no breaches) at whatever attempt counts the caller names.

    A vector missing from the mapping gets no row at all, which is how the
    absent-vector case is expressed.
    """
    return RedTeamResult(
        k=k,
        vectors=[
            VectorOutcome(vector=vector, attempts=attempts)
            for vector, attempts in attempts_by_vector.items()
        ],
    )


class TestTheRecordIsFrozen:
    """Frozen means frozen, on both halves of the record."""

    def test_a_result_field_cannot_be_reassigned(self):
        result = _result_with(_all_at(K))
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.k = 1

    def test_a_vector_row_cannot_be_reassigned(self):
        row = VectorOutcome(vector=SHORT_VECTOR, attempts=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.attempts = 99

    def test_a_list_of_rows_is_held_as_a_tuple(self):
        result = _result_with(_all_at(K))
        assert isinstance(result.vectors, tuple)


class TestWhatARowRefuses:
    """Every refusal here is a row that would misreport what a run covered."""

    def test_a_vector_outside_the_dispatch_roster_is_refused(self):
        with pytest.raises(InvalidRedTeamResult) as exc:
            VectorOutcome(vector="sql_injection", attempts=K)
        assert "sql_injection" in str(exc.value)

    def test_more_breaches_than_attempts_is_refused(self):
        with pytest.raises(InvalidRedTeamResult) as exc:
            VectorOutcome(
                vector=SHORT_VECTOR, attempts=1, breaches=2, max_severity="high"
            )
        assert "never ran" in str(exc.value)

    def test_a_breach_with_no_graded_severity_is_refused(self):
        with pytest.raises(InvalidRedTeamResult):
            VectorOutcome(vector=SHORT_VECTOR, attempts=K, breaches=1)

    def test_a_graded_severity_with_no_breach_is_refused(self):
        with pytest.raises(InvalidRedTeamResult):
            VectorOutcome(
                vector=SHORT_VECTOR, attempts=K, breaches=0, max_severity="critical"
            )

    def test_a_negative_attempt_count_is_refused(self):
        with pytest.raises(InvalidRedTeamResult):
            VectorOutcome(vector=SHORT_VECTOR, attempts=-1)

    def test_a_bool_is_not_an_attempt_count(self):
        # True is an int in Python and would count as one attempt.
        with pytest.raises(InvalidRedTeamResult):
            VectorOutcome(vector=SHORT_VECTOR, attempts=True)

    def test_an_unknown_severity_is_refused(self):
        with pytest.raises(InvalidRedTeamResult):
            VectorOutcome(
                vector=SHORT_VECTOR, attempts=1, breaches=1, max_severity="catastrophic"
            )

    def test_a_stored_severity_string_reads_back_as_the_enum(self):
        row = VectorOutcome(
            vector=SHORT_VECTOR, attempts=K, breaches=1, max_severity="high"
        )
        assert row.max_severity is Severity.HIGH


class TestWhatAResultRefuses:
    def test_k_below_one_is_refused(self):
        # At k=0 every vector is complete having attempted nothing.
        with pytest.raises(InvalidRedTeamResult) as exc:
            _result_with(_all_at(0), k=0)
        assert "attempted nothing" in str(exc.value)

    def test_a_string_of_vectors_is_refused(self):
        # tuple("abc") raises nothing and builds three rows that name no vector.
        with pytest.raises(InvalidRedTeamResult):
            RedTeamResult(k=K, vectors="data_leakage")

    def test_a_row_that_is_not_a_vector_outcome_is_refused(self):
        with pytest.raises(InvalidRedTeamResult) as exc:
            RedTeamResult(k=K, vectors=[{"vector": SHORT_VECTOR, "attempts": K}])
        assert "dict" in str(exc.value)

    def test_two_rows_for_one_vector_are_refused(self):
        with pytest.raises(InvalidRedTeamResult) as exc:
            RedTeamResult(
                k=K,
                vectors=[
                    VectorOutcome(vector=SHORT_VECTOR, attempts=K),
                    VectorOutcome(vector=SHORT_VECTOR, attempts=1),
                ],
            )
        assert SHORT_VECTOR in str(exc.value)


class TestTheRunLevelNumbersAreDerivedOnce:
    def test_breaches_total_across_vectors(self):
        result = RedTeamResult(
            k=K,
            vectors=[
                VectorOutcome(
                    vector="data_leakage", attempts=K, breaches=2, max_severity="medium"
                ),
                VectorOutcome(
                    vector="identity_bypass", attempts=K, breaches=1, max_severity="low"
                ),
            ],
        )
        assert result.breaches == 3

    def test_max_severity_ranks_by_gravity_not_alphabet(self):
        # "critical" sorts BEFORE "high" as text, so a max() over the strings
        # reports this run's worst finding as its mildest one.
        result = RedTeamResult(
            k=K,
            vectors=[
                VectorOutcome(
                    vector="data_leakage", attempts=K, breaches=1, max_severity="high"
                ),
                VectorOutcome(
                    vector="identity_bypass",
                    attempts=K,
                    breaches=1,
                    max_severity="critical",
                ),
            ],
        )
        assert result.max_severity is Severity.CRITICAL

    def test_a_run_that_breached_nothing_reports_none(self):
        result = _result_with(_all_at(K))
        assert result.max_severity is Severity.NONE
        assert result.breaches == 0

    def test_a_result_holding_no_rows_reports_none(self):
        assert RedTeamResult(k=K, vectors=[]).max_severity is Severity.NONE


class TestTodaysDispatcherIsRepresentable:
    """The shipped dispatcher calls each runner once. That has to be sayable."""

    def test_one_attempt_per_vector_at_k_three_is_visibly_short(self):
        result = _result_with(_all_at(1), k=K)

        assert result.coverage["complete"] is False
        assert result.coverage["incomplete_vectors"] == list(RED_TEAM_VECTORS)
        assert result.coverage["vectors_complete"] == 0
        assert result.coverage["k"] == K

    def test_the_same_one_attempt_run_at_k_one_is_complete(self):
        # The control for the test above. What made it incomplete was k, not
        # the attempt count and not the fixture.
        result = _result_with(_all_at(1), k=1)

        assert result.coverage["complete"] is True
        assert result.coverage["incomplete_vectors"] == []


class TestTheCompletenessBoundary:
    """k-1 attempts on ONE vector is incomplete. k on that same vector is complete.

    Six vectors sit on k in both tests. The pair differs by one number, so
    neither assertion can be passing because the fixture was uniformly short or
    uniformly full.
    """

    def test_one_vector_one_attempt_short_is_incomplete(self):
        attempts = _all_at(K)
        attempts[SHORT_VECTOR] = K - 1

        coverage = _result_with(attempts).coverage

        assert coverage["complete"] is False
        assert coverage["incomplete_vectors"] == [SHORT_VECTOR]
        assert coverage["vectors_complete"] == len(RED_TEAM_VECTORS) - 1
        assert f"{SHORT_VECTOR}: {K - 1} of {K} attempt(s) ran" in (
            coverage["incomplete_reason"]
        )

    def test_the_same_vector_at_k_is_complete(self):
        attempts = _all_at(K)
        attempts[SHORT_VECTOR] = K

        coverage = _result_with(attempts).coverage

        assert coverage["complete"] is True
        assert coverage["incomplete_vectors"] == []
        assert coverage["incomplete_reason"] is None
        assert coverage["vectors_complete"] == len(RED_TEAM_VECTORS)

    def test_a_vector_with_no_row_at_all_is_incomplete(self):
        # Six vectors on k and the seventh absent. A runner that raised before
        # recording anything must cost coverage, never buy it.
        attempts = _all_at(K)
        del attempts[SHORT_VECTOR]

        coverage = _result_with(attempts).coverage

        assert coverage["complete"] is False
        assert coverage["incomplete_vectors"] == [SHORT_VECTOR]
        assert f"{SHORT_VECTOR}: 0 of {K} attempt(s) ran" in coverage["incomplete_reason"]

    def test_a_result_holding_nothing_is_not_full_coverage(self):
        coverage = RedTeamResult(k=K, vectors=[]).coverage

        assert coverage["complete"] is False
        assert coverage["vectors_complete"] == 0
        assert coverage["incomplete_vectors"] == list(RED_TEAM_VECTORS)

    def test_more_than_k_attempts_still_counts_as_complete(self):
        # A vector that ran an extra attempt has not missed one, so `complete`
        # stays a claim about the floor rather than an equality check.
        attempts = _all_at(K)
        attempts[SHORT_VECTOR] = K + 1

        assert _result_with(attempts).coverage["complete"] is True

    def test_the_denominator_is_the_roster_not_the_rows(self):
        coverage = _result_with(_all_at(K)).coverage

        assert coverage["vectors_attempted"] == len(RED_TEAM_VECTORS) == 7


# ---------------------------------------------------------------------------
# worst_severity — the ordering, exported so nobody keeps a second copy
# ---------------------------------------------------------------------------


class TestWorstSeverity:
    """`SEVERITY_ORDER = ["low", "medium", "high", "critical"]` and a `.index()`
    ranking lived in the red-team task, deciding what went into
    `red_team_runs.max_severity`. Two copies of one ordering is one copy that can
    disagree, and the task's copy would have raised ValueError inside the
    completion write the day a fifth grade was added here and not there.
    """

    def test_the_worst_grade_wins_however_the_list_is_ordered(self):
        assert worst_severity(["low", "critical", "high"]) is Severity.CRITICAL
        assert worst_severity(["critical", "low"]) is Severity.CRITICAL

    def test_text_order_would_get_it_wrong(self):
        """The reason this is an enum with a rank and not a `max()` over strings.

        Sorted as text, 'critical' < 'high', so a plain max() over the severity
        strings reports a run's worst finding as its mildest one.
        """
        assert max(["critical", "high"]) == "high"
        assert worst_severity(["critical", "high"]) is Severity.CRITICAL

    def test_nothing_at_all_grades_none(self):
        assert worst_severity([]) is Severity.NONE

    def test_an_unknown_grade_stops_the_write(self):
        """A run's worst finding is not something to guess at, so an
        unrecognised string raises rather than being ranked as the mildest one.
        """
        with pytest.raises(InvalidRedTeamResult):
            worst_severity(["low", "catastrophic"])


# ---------------------------------------------------------------------------
# payload — the stored shape of the record (red_team_runs.result, 0021)
# ---------------------------------------------------------------------------


class TestThePayloadIsTheStoredShape:
    """One place decides what the column holds, so the task that writes it and
    any reader that grows a parser for it are looking at the same keys."""

    def _dirty(self) -> RedTeamResult:
        return RedTeamResult(
            k=K,
            vectors=[
                VectorOutcome(vector=vector, attempts=K)
                for vector in RED_TEAM_VECTORS
                if vector != SHORT_VECTOR
            ]
            + [
                VectorOutcome(
                    vector=SHORT_VECTOR, attempts=K, breaches=2, max_severity="critical"
                )
            ],
        )

    def test_the_payload_is_json_safe(self):
        """It goes through json.dumps on the way to the column, so a Severity
        enum member that survived into it would raise there instead of here."""
        payload = self._dirty().payload

        assert json.loads(json.dumps(payload)) == payload

    def test_every_number_a_reader_needs_is_written_out(self):
        payload = self._dirty().payload

        assert payload["k"] == K
        assert payload["breaches"] == 2
        assert payload["max_severity"] == "critical"
        assert {row["vector"] for row in payload["vectors"]} == set(RED_TEAM_VECTORS)
        short = next(
            row for row in payload["vectors"] if row["vector"] == SHORT_VECTOR
        )
        assert short == {
            "vector": SHORT_VECTOR,
            "attempts": K,
            "breaches": 2,
            "max_severity": "critical",
        }

    def test_a_clean_run_reads_as_clean_and_not_as_unmeasured(self):
        """The control. Every field has to move when the run does, or the
        assertions above would pass against a constant.
        """
        payload = _result_with(_all_at(K)).payload

        assert payload["breaches"] == 0
        assert payload["max_severity"] == "none"
        assert all(row["breaches"] == 0 for row in payload["vectors"])
        assert payload["coverage"]["complete"] is True

    def test_the_payload_carries_the_coverage_rule_it_was_measured_by(self):
        """k on the row is the whole point of 0021: a reader that looked
        settings.RED_TEAM_ATTEMPTS_PER_VECTOR up instead would measure a stored
        run against today's requirement rather than the one it ran under."""
        attempts = _all_at(K)
        attempts[SHORT_VECTOR] = K - 1

        payload = _result_with(attempts).payload

        assert payload["k"] == K
        assert payload["coverage"]["complete"] is False
        assert payload["coverage"]["incomplete_vectors"] == [SHORT_VECTOR]
