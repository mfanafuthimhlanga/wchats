"""`_score_samples` scores samples under a bound and says how far it got (#205).

OBSERVED 2026-09-06 on staging: one awaited judge call at a time over 31
scenarios and four metrics logged nothing for forty minutes and did not finish
inside the checklist's 45 minute ceiling. Nothing here touches a socket; the
metric is a coroutine that sleeps.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from app.services import eval_service
from app.services.eval_service import _score_samples

SLEEP_S = 0.15


@dataclass
class _Sample:
    user_input: str
    reference: str
    response: str = "an answer"
    retrieved_contexts: tuple = ("a context",)


@dataclass
class _Scored:
    value: float | None


class _SleepingMetric:
    """A metric that takes SLEEP_S per call and remembers how many ran at once."""

    name = "faithfulness"

    def __init__(self, fail_on: str | None = None):
        self.in_flight = 0
        self.peak = 0
        self.fail_on = fail_on

    async def ascore(self, user_input, response, retrieved_contexts):  # noqa: ARG002
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(SLEEP_S)
            if user_input == self.fail_on:
                raise RuntimeError("the judge refused this one")
            return _Scored(value=float(len(user_input)))
        finally:
            self.in_flight -= 1


def _samples(n: int) -> list[_Sample]:
    return [_Sample(user_input="q" * (i + 1), reference=f"r{i}") for i in range(n)]


class TestSamplesScoreConcurrentlyUnderABound:
    def test_eight_samples_at_four_in_flight_take_two_rounds(self):
        metric = _SleepingMetric()
        started = time.monotonic()

        rows = asyncio.run(_score_samples([metric], _samples(8), concurrency=4))

        elapsed = time.monotonic() - started
        assert metric.peak == 4, f"peak in flight was {metric.peak}, the bound is 4"
        assert elapsed < SLEEP_S * 4, f"eight samples took {elapsed:.2f}s, sequential would be {SLEEP_S * 8:.2f}s"
        assert len(rows) == 8

    def test_a_bound_of_one_is_the_old_sequential_run(self):
        metric = _SleepingMetric()

        asyncio.run(_score_samples([metric], _samples(3), concurrency=1))

        assert metric.peak == 1

    def test_rows_come_back_in_sample_order_whatever_finishes_first(self):
        metric = _SleepingMetric()

        rows = asyncio.run(_score_samples([metric], _samples(6), concurrency=3))

        assert [r["user_input"] for r in rows] == ["q" * (i + 1) for i in range(6)]
        assert [r["faithfulness"] for r in rows] == [float(i + 1) for i in range(6)]

    def test_the_bound_defaults_to_the_setting(self, monkeypatch):
        monkeypatch.setattr(eval_service.settings, "EVAL_SCORING_CONCURRENCY", 2)
        metric = _SleepingMetric()

        asyncio.run(_score_samples([metric], _samples(5)))

        assert metric.peak == 2

    def test_a_metric_that_raises_leaves_none_in_that_cell_only(self):
        metric = _SleepingMetric(fail_on="qq")

        rows = asyncio.run(_score_samples([metric], _samples(3), concurrency=3))

        assert [r["faithfulness"] for r in rows] == [1.0, None, 3.0]


class TestTheRunSaysHowFarItGot:
    def test_a_progress_line_carries_the_count_and_the_elapsed_seconds(self, monkeypatch):
        seen: list[dict] = []

        class _Log:
            def info(self, event, **fields):
                if event == "run_ragas_eval.progress":
                    seen.append(fields)

            def warning(self, *a, **k): ...

            def error(self, *a, **k): ...

        monkeypatch.setattr(eval_service, "log", _Log())
        monkeypatch.setattr(eval_service, "_SCORING_PROGRESS_EVERY", 2)

        asyncio.run(_score_samples([_SleepingMetric()], _samples(5), concurrency=5))

        assert [f["scored"] for f in seen] == [2, 4, 5], "every second sample and the last one"
        assert all(f["of"] == 5 for f in seen)
        assert all(isinstance(f["elapsed_s"], float) and f["elapsed_s"] >= 0 for f in seen)


@pytest.mark.parametrize("bound", [0, -3])
def test_a_bound_below_one_still_scores_one_at_a_time(bound):
    metric = _SleepingMetric()

    rows = asyncio.run(_score_samples([metric], _samples(2), concurrency=bound))

    assert metric.peak == 1
    assert len(rows) == 2
