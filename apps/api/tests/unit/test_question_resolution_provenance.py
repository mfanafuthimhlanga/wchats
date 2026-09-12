"""The run records which question relevancy was measured against (#233).

`answer_relevancy` is in `GATED_METRIC_KEYS`, so its pass rate blocks a deploy.
Since #227 PR 2 it is scored against a model-rewritten question for any scenario
carrying `turns`, and until this the run record did not say so: a collector
reading relevancy at 0.91 could not tell a run scored entirely on raw questions
from one where half the rows were scored on rewrites, nor from one where every
rewrite failed and silently fell back.

WHAT THESE TESTS ARE ABOUT IS THE DENOMINATOR. The counts are over the rows the
judge returned an attributed relevancy for, not over the rows the annotator was
handed. `_placed_score_rows` drops a returned row it cannot attribute and
`_score_samples` yields None for a metric that raised, so a count taken from the
input list is a claim about what the run intended to measure while reading as a
claim about what it measured.
"""

from __future__ import annotations

import pytest

from app.services.eval_service import question_resolution_provenance

TURNS = [{"role": "user", "content": "do you ship to Durban?"}]


def scenario(row_id: str, *, turns=(), resolved: str | None = None) -> dict:
    """One annotated scenario at the shape `annotate_resolved_questions` leaves.

    A row without turns carries NO `resolved_question` key, which is the state
    the annotator leaves it in; a row whose rewrite failed carries None. Both are
    NULL on the database row and only `turns` tells them apart.
    """
    row: dict = {"id": row_id, "question": "and to Durban?", "turns": list(turns)}
    if turns:
        row["resolved_question"] = resolved
    return row


def score(scenario_id: str, *, relevancy: float | None = 0.9) -> dict:
    """One attributed row as `_placed_score_rows` builds it."""
    return {
        "scenario_id": scenario_id,
        "faithfulness": 0.8,
        "answer_relevancy": relevancy,
        "context_precision": 0.7,
        "context_recall": 0.6,
    }


def record(scenarios, score_rows) -> dict:
    """The inner object, so a test reads counts rather than one nested key."""
    return question_resolution_provenance(scenarios, score_rows)["question_resolution"]


def test_a_rewritten_row_is_recorded_as_scored_on_the_rewrite():
    counts = record(
        [scenario("s1", turns=TURNS, resolved="Do you ship to Durban?")],
        [score("s1")],
    )

    assert counts == {
        "relevancy_scored": 1,
        "multi_turn": 1,
        "rewritten": 1,
        "raw_question_fallback": 0,
    }


def test_a_failed_rewrite_is_a_fallback_and_never_a_rewrite():
    """`resolve_question` returns None on a provider error and the raw question scores.

    A run where every rewrite failed produces the same relevancy column as a run
    where every rewrite worked. This count is the only thing that separates them.
    """
    counts = record([scenario("s1", turns=TURNS, resolved=None)], [score("s1")])

    assert counts["multi_turn"] == 1
    assert counts["rewritten"] == 0
    assert counts["raw_question_fallback"] == 1


def test_a_single_turn_row_is_scored_and_is_not_multi_turn():
    counts = record([scenario("s1")], [score("s1")])

    assert counts["relevancy_scored"] == 1
    assert counts["multi_turn"] == 0
    assert counts["raw_question_fallback"] == 0, (
        "a scenario with no conversation was never a candidate for a rewrite, so "
        "counting it as a fallback would report a resolution failure that never "
        "happened"
    )


@pytest.mark.parametrize("turns", ["a string", None, 7, {"role": "user"}])
def test_a_turns_value_that_is_not_a_list_is_not_multi_turn(turns):
    """`_scenario_history` returns [] for these, so the guard cannot fire in the task.

    It is held anyway because this function is reachable from anywhere the score
    rows are, and because `annotate_resolved_questions` pins the same shapes on
    the write side. A guard the suite has never fired is a guard nobody can tell
    from a tautology.
    """
    row = {"id": "s1", "turns": turns, "resolved_question": "Do you ship to Durban?"}

    counts = record([row], [score("s1")])

    assert counts["relevancy_scored"] == 1
    assert counts["multi_turn"] == 0
    assert counts["rewritten"] == 0


def test_a_scenario_the_judge_returned_no_row_for_is_not_counted():
    """The judge can return fewer rows than it was sent, and does on an outage.

    Counting the scenario here would say relevancy was measured against a rewrite
    on a row that has no relevancy number at all.
    """
    counts = record(
        [
            scenario("s1", turns=TURNS, resolved="Do you ship to Durban?"),
            scenario("s2", turns=TURNS, resolved="Do you deliver to Durban?"),
        ],
        [score("s1")],
    )

    assert counts["relevancy_scored"] == 1
    assert counts["multi_turn"] == 1
    assert counts["rewritten"] == 1


def test_a_row_whose_relevancy_did_not_return_is_not_counted():
    """One metric raising yields None for that cell and leaves the other three.

    The record is about the relevancy number. A row that scored faithfulness and
    nothing else contributes no relevancy observation, so counting it would put a
    denominator under a measurement that is not there.
    """
    counts = record(
        [scenario("s1", turns=TURNS, resolved="Do you ship to Durban?")],
        [score("s1", relevancy=None)],
    )

    assert counts == {
        "relevancy_scored": 0,
        "multi_turn": 0,
        "rewritten": 0,
        "raw_question_fallback": 0,
    }


def test_a_relevancy_of_nan_is_not_an_observation():
    """`float("nan") is not None` is True, so a bare None check would count it.

    `_placed_score_rows` normalises NaN to None one call earlier, which means the
    only thing holding this is a check nothing had ever fired. A NaN reaching the
    denominator would put a row under a measurement that never returned.
    """
    counts = record(
        [scenario("s1", turns=TURNS, resolved="Do you ship to Durban?")],
        [score("s1", relevancy=float("nan"))],
    )

    assert counts["relevancy_scored"] == 0
    assert counts["multi_turn"] == 0


def test_a_relevancy_of_zero_is_an_observation():
    """Zero is the judge saying the answer was irrelevant, which is a measurement.

    This repo's rule is that missing data is never passing data; the converse is
    that a real zero is never missing data. A denominator that dropped it would
    report the worst rows as unmeasured.
    """
    counts = record(
        [scenario("s1", turns=TURNS, resolved="Do you ship to Durban?")],
        [score("s1", relevancy=0.0)],
    )

    assert counts["relevancy_scored"] == 1
    assert counts["rewritten"] == 1


def test_a_mixed_run_counts_each_row_once():
    """Four rows, three conversations, one rewrite, and the arithmetic that follows."""
    scenarios = [
        scenario("s1", turns=TURNS, resolved="Do you ship to Durban?"),
        scenario("s2", turns=TURNS, resolved=None),
        scenario("s3", turns=TURNS, resolved="   "),
        scenario("s4"),
    ]

    counts = record(scenarios, [score(s["id"]) for s in scenarios])

    assert counts == {
        "relevancy_scored": 4,
        "multi_turn": 3,
        "rewritten": 1,
        "raw_question_fallback": 2,
    }


def test_whitespace_is_not_a_rewrite():
    """The same rule `_resolved_inputs` and `write_eval_samples` apply.

    All three strip, so a rewrite of nothing but spaces never reaches the judge,
    never reaches the sample row, and never counts here. A single truthiness test
    in any one of the three would let the other two describe a scored question
    that was not the one scored.
    """
    counts = record([scenario("s1", turns=TURNS, resolved="   ")], [score("s1")])

    assert counts["rewritten"] == 0
    assert counts["raw_question_fallback"] == 1


def test_a_run_that_scored_nothing_records_zeros_rather_than_nothing():
    """Zero observations reads as unknown wherever the key is present.

    An ABSENT key is a different thing and a broader one: it covers a run from
    before this existed, a tenant DB predating alembic_tenant 0013, a failed
    patch write, a run below the measurement floor, and a run that died between
    the scores and the stamp. Only a present record says the run reached scoring.
    """
    assert record([], []) == {
        "relevancy_scored": 0,
        "multi_turn": 0,
        "rewritten": 0,
        "raw_question_fallback": 0,
    }


def test_the_patch_names_the_one_config_key_it_merges():
    """`update_eval_run_config` merges shallowly, so the whole object replaces.

    The function returns the PATCH rather than the counts for the same reason
    `invocation_provenance` does: the call site hands what it is given straight to
    the merge and cannot name the key wrongly on the way.
    """
    patch = question_resolution_provenance([scenario("s1")], [score("s1")])

    assert list(patch) == ["question_resolution"]
