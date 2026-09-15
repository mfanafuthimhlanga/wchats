"""Only relevancy is scored against the rewritten question (#227 PR 2).

Relevancy is gated, and this changes its input on purpose: it was scoring answers
against text that did not say what was asked, which is what #58 measured failing.

What must NOT move is the other gated metric. All four Ragas metrics name
`user_input` in `_METRIC_ASCORE_ARGS`, and all four read it off ONE validated
sample, so putting the rewrite into the sample would have moved faithfulness too,
and faithfulness had no owner fail in #58: there is nothing wrong with it to fix.

`RESOLVED_INPUT_METRICS` is the whole guard for the ragas side, and these tests
are what make it one: relevancy sees the rewrite, the other three see the bytes
they saw before this existed, and the returned row keeps the raw question because
that is the half of the attribution key `attribute_returned_rows` matches on.

#274 SPLIT RELEVANCY IN TWO AND BOTH HALVES READ THE REWRITE. The gated column is
`relevance_judge`, which takes the resolved question on every call and so needs no
entry in the tuple; `ragas_answer_relevancy` is the reported figure and is what
the tuple now names. Faithfulness, precision and recall are unmoved, which is the
property this module was written to defend.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import eval_service
from tests.model_doubles import ledger

RAW = "how do I start the dev server?"
RESOLVED = "How do I start the dev server for Mellow's Earth Elements?"
REFERENCE = "Run pnpm dev from the repo root."


class _RecordingMetric:
    """A metric that records the kwargs it was scored with and returns 1.0."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[dict] = []

    async def ascore(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(value=1.0)


#: The four ragas columns and the name each metric instance answers to. The two
#: differ on one row: #274 renamed the column `answer_relevancy` fills, and
#: `_METRIC_ASCORE_ARGS` is still keyed on ragas' own name.
RAGAS_NAME_BY_COLUMN = {
    "faithfulness": "faithfulness",
    "ragas_answer_relevancy": "answer_relevancy",
    "context_precision": "context_precision",
    "context_recall": "context_recall",
}


class _RecordingJudge:
    """Stands in for `relevance_judge.judge_relevance`, recording its arguments."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def __call__(self, question, response, *, ledger):  # noqa: ARG002
        from app.services.relevance_judge import RelevanceVerdict

        self.calls.append((question, response))
        return RelevanceVerdict(verdict="pass", reason="canned")


def _sample(user_input: str = RAW):
    return SimpleNamespace(
        user_input=user_input,
        response="You run the dev server with pnpm dev.",
        retrieved_contexts=["pnpm dev starts it"],
        reference=REFERENCE,
    )


def _score(resolved_inputs, samples=None):
    """(recorded metrics by COLUMN, returned rows, the doubled Judge)."""
    metrics = [
        (column, _RecordingMetric(name))
        for column, name in RAGAS_NAME_BY_COLUMN.items()
    ]
    judge = _RecordingJudge()
    real = eval_service.judge_relevance
    eval_service.judge_relevance = judge
    try:
        rows = asyncio.run(
            eval_service._score_samples(
                metrics,
                samples if samples is not None else [_sample()],
                concurrency=1,
                resolved_inputs=resolved_inputs,
                relevance_ledger=ledger(),
            )
        )
    finally:
        eval_service.judge_relevance = real
    return dict(metrics), rows, judge


def test_relevancy_is_scored_against_the_rewrite():
    scored, _rows, judge = _score([RESOLVED])

    [call] = scored["ragas_answer_relevancy"].calls
    assert call["user_input"] == RESOLVED, (
        "the reported ragas relevancy was scored against the raw follow-up, "
        "which is the defect #227 exists to fix"
    )
    assert judge.calls == [(RESOLVED, _sample().response)], (
        "the GATED relevance Judge was scored against the raw follow-up. It is "
        "the half of relevancy a deploy reads, so this is the #227 defect on the "
        "column that matters (#274)"
    )


#: The three metrics that must never see the rewrite, SPELLED OUT.
#:
#: Derived from `RESOLVED_INPUT_METRICS` in the first version of this file, which
#: made the guard vacuous: widening that tuple to include faithfulness removed
#: faithfulness from this list, so the mutation that broke the property deleted
#: the case that would have caught it and the suite stayed green. A literal is
#: what makes widening a FAILURE rather than a smaller parameter set.
RAW_INPUT_METRICS = ("faithfulness", "context_precision", "context_recall")


def test_the_lists_between_them_cover_every_metric():
    """So a sixth metric cannot arrive covered by none of them.

    Three lists now. The Judge's column is neither raw nor in the tuple: it takes
    the rewrite unconditionally, which is why it is named on its own here.
    """
    covered = (
        set(RAW_INPUT_METRICS)
        | set(eval_service.RESOLVED_INPUT_METRICS)
        | {eval_service.RELEVANCE_METRIC}
    )
    assert covered == set(eval_service.METRIC_KEYS)
    assert not set(RAW_INPUT_METRICS) & set(eval_service.RESOLVED_INPUT_METRICS)
    assert eval_service.RELEVANCE_METRIC not in RAW_INPUT_METRICS


@pytest.mark.parametrize("metric", RAW_INPUT_METRICS)
def test_every_other_metric_sees_the_question_byte_for_byte(metric):
    """Faithfulness is the one that matters here: gated, and not the one being fixed.

    Relevancy's input moves in this PR by design. If a change to the resolution
    path can make THIS test fail, it moved the OTHER deploy gate as well, which
    nothing in #227 asked for.
    """
    scored, _rows, _judge = _score([RESOLVED])

    [call] = scored[metric].calls
    assert call["user_input"] == RAW, (
        f"{metric} was scored against the rewritten question. Only "
        f"{eval_service.RESOLVED_INPUT_METRICS} may see it"
    )


def test_the_returned_row_keeps_the_raw_question():
    """`SAMPLE_KEY_COLUMNS` matches a returned row to its scenario on this pair.

    A row carrying the rewrite would match no scenario, and the whole run would
    come back unattributed rather than mis-scored, which is a louder failure but
    still a lost run.
    """
    _scored, [row], _judge = _score([RESOLVED])

    assert row["user_input"] == RAW
    assert row["reference"] == REFERENCE
    assert (row["user_input"], row["reference"]) == eval_service.scenario_identity_key(
        {"question": RAW, "reference_answer": REFERENCE}
    )


def test_a_single_turn_scenario_is_scored_exactly_as_before():
    """None per sample is the whole corpus that exists before #227.

    Every metric, including relevancy, sees the raw question, so a relevancy score
    from before this PR and one from after are the same measurement.
    """
    scored, [row], judge = _score([None])

    for column in RAGAS_NAME_BY_COLUMN:
        [call] = scored[column].calls
        assert call["user_input"] == RAW
    assert judge.calls == [(RAW, _sample().response)]
    assert row["user_input"] == RAW


def test_omitting_the_rewrites_entirely_scores_every_metric_raw():
    """The default, which is what every caller but `run_ragas_eval` passes."""
    scored, _rows, judge = _score(None)

    for column in RAGAS_NAME_BY_COLUMN:
        [call] = scored[column].calls
        assert call["user_input"] == RAW
    assert judge.calls == [(RAW, _sample().response)]


def test_a_rewrite_list_of_the_wrong_length_raises_rather_than_mispairing():
    """Pairing a rewrite with somebody else's sample is a score about the wrong
    question, and it would look exactly like a real measurement."""
    with pytest.raises(ValueError):
        _score([RESOLVED, RESOLVED], samples=[_sample()])

    with pytest.raises(ValueError):
        _score([], samples=[_sample()])


def test_relevancy_is_the_only_metric_that_takes_the_rewrite():
    """The tuple itself, so widening it is a deliberate edit with a test to answer.

    Adding a gated metric here changes what its calibration was measured on.
    """
    assert eval_service.RESOLVED_INPUT_METRICS == ("ragas_answer_relevancy",)
    assert set(eval_service.RESOLVED_INPUT_METRICS) <= set(eval_service.METRIC_KEYS)
    assert not hasattr(eval_service, "REWRITE_SCORED_METRICS"), (
        "the provenance counts read RELEVANCE_METRIC alone now: the warning they "
        "feed is about the gated number, and the reported ragas figure is not it"
    )


def test_a_rewrite_of_nothing_but_whitespace_never_reaches_relevancy():
    """Three readers of `resolved_question` have to agree on what a rewrite is.

    `write_eval_samples` writes a whitespace rewrite as NULL and
    `question_resolution_provenance` counts it as a fallback (#233). A bare
    truthiness test in `_resolved_inputs` would score relevancy against a
    question of spaces while both records said the raw question scored, and the
    run would carry no trace of the disagreement.
    """
    resolved = eval_service._resolved_inputs([{"resolved_question": "   "}])
    assert resolved == [None], (
        "a blank rewrite survived _resolved_inputs, so the judge scores "
        "whitespace while the sample row and the run record both say otherwise"
    )

    scored, _rows, judge = _score(resolved)

    [call] = scored["ragas_answer_relevancy"].calls
    assert call["user_input"] == RAW
    assert judge.calls == [(RAW, _sample().response)], (
        "a blank rewrite reached the gated Judge"
    )
