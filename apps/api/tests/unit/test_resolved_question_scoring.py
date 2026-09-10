"""Only relevancy is scored against the rewritten question (#227 PR 2).

All four Ragas metrics name `user_input` in `_METRIC_ASCORE_ARGS`, and all four
read it off ONE validated sample. So the obvious way to score a follow-up, putting
the rewritten question into the sample, would move the input of every metric at
once. Faithfulness is gated, its calibration was measured on raw questions, and a
change that moved it would move a deploy gate as a side effect of fixing
relevancy.

`RESOLVED_INPUT_METRICS` is the whole guard, and these tests are what make it one:
relevancy sees the rewrite, the other three see the bytes they saw before this
existed, and the returned row keeps the raw question because that is the half of
the attribution key `attribute_returned_rows` matches on.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import eval_service

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


def _sample(user_input: str = RAW):
    return SimpleNamespace(
        user_input=user_input,
        response="You run the dev server with pnpm dev.",
        retrieved_contexts=["pnpm dev starts it"],
        reference=REFERENCE,
    )


def _score(resolved_inputs, samples=None):
    metrics = [_RecordingMetric(name) for name in eval_service.METRIC_KEYS]
    rows = asyncio.run(
        eval_service._score_samples(
            metrics,
            samples if samples is not None else [_sample()],
            concurrency=1,
            resolved_inputs=resolved_inputs,
        )
    )
    return {m.name: m for m in metrics}, rows


def test_relevancy_is_scored_against_the_rewrite():
    scored, _rows = _score([RESOLVED])

    [call] = scored["answer_relevancy"].calls
    assert call["user_input"] == RESOLVED, (
        "relevancy was scored against the raw follow-up, which is the defect "
        "#227 exists to fix"
    )


#: The three metrics that must never see the rewrite, SPELLED OUT.
#:
#: Derived from `RESOLVED_INPUT_METRICS` in the first version of this file, which
#: made the guard vacuous: widening that tuple to include faithfulness removed
#: faithfulness from this list, so the mutation that broke the property deleted
#: the case that would have caught it and the suite stayed green. A literal is
#: what makes widening a FAILURE rather than a smaller parameter set.
RAW_INPUT_METRICS = ("faithfulness", "context_precision", "context_recall")


def test_the_two_lists_between_them_cover_every_metric():
    """So a fifth metric cannot arrive covered by neither list."""
    assert set(RAW_INPUT_METRICS) | set(eval_service.RESOLVED_INPUT_METRICS) == set(
        eval_service.METRIC_KEYS
    )
    assert not set(RAW_INPUT_METRICS) & set(eval_service.RESOLVED_INPUT_METRICS)


@pytest.mark.parametrize("metric", RAW_INPUT_METRICS)
def test_every_other_metric_sees_the_question_byte_for_byte(metric):
    """Faithfulness is the one that matters here, and it is gated.

    Its calibration was measured on raw questions. If this test can be made to
    fail by a change to the resolution path, that change moved a deploy gate.
    """
    scored, _rows = _score([RESOLVED])

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
    _scored, [row] = _score([RESOLVED])

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
    scored, [row] = _score([None])

    for name in eval_service.METRIC_KEYS:
        [call] = scored[name].calls
        assert call["user_input"] == RAW
    assert row["user_input"] == RAW


def test_omitting_the_rewrites_entirely_scores_every_metric_raw():
    """The default, which is what every caller but `run_ragas_eval` passes."""
    scored, _rows = _score(None)

    for name in eval_service.METRIC_KEYS:
        [call] = scored[name].calls
        assert call["user_input"] == RAW


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
    assert eval_service.RESOLVED_INPUT_METRICS == ("answer_relevancy",)
    assert set(eval_service.RESOLVED_INPUT_METRICS) <= set(eval_service.METRIC_KEYS)
