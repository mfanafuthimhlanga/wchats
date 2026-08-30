"""Unit tests for app.domain.calibration_status (ticket #53, slice 1).

WHAT THIS TYPE IS FOR
    The calibration harness lives under `tests/`, costs a judge call per labelled
    row, and runs by hand. Nothing on the deploy path can call it. This record is
    how its answer travels to the app, so every rule that stops the answer being
    misread has to live on the record rather than in the harness that produced it.

WHY `calibrated` COSTS MORE THAN THE OTHER THREE STATUSES
    It is the one status a reader may act on. A record can only claim it while
    naming the Judge it measured and carrying both measured halves of the verdict
    as True. None is refused as hard as False here, because an unevaluated part is
    an absence and a deploy shipping on an absence is shipping on a measurement
    nobody made.

WHY THE PAYLOAD KEY SET IS PINNED TO A LITERAL
    Criterion 3, the reader half. Slice 2's writer and slice 3's consumer parse
    this key set. A field added, renamed or dropped changes what they read, and a
    round-trip test alone stays green through all three because both halves move
    together. The literal below does not move, so the commit that changes the
    shape is the commit that goes red.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.domain.calibration_status import (
    ABSENT_REASONS,
    ARTIFACT_VERSION,
    CALIBRATION_STATUSES,
    STATUS_CALIBRATED,
    STATUS_NOT_CALIBRATED_YET,
    CalibrationStatus,
    Interval,
    InvalidCalibrationStatus,
)
from app.domain.judge_identity import JudgeIdentity

#: The stored shape, written out rather than derived from the record. Deriving it
#: would make this test agree with whatever the record currently emits, which is
#: the one thing it exists not to do.
PAYLOAD_KEYS = [
    "status",
    "reason",
    "judge_identity",
    "judge_interval",
    "ceiling_interval",
    "difference_interval",
    "beats_chance",
    "ceiling_beats_chance",
    "reaches_ceiling",
    "kappa",
    "matthews",
    "scored_pairs",
    "pairs",
    "attempted",
    "valid",
    "labelled_at",
    "harness_version",
    "artifact_version",
]

INTERVAL_KEYS = ["low", "high", "point", "usable"]


def _identity(**overrides) -> JudgeIdentity:
    fields = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "prompt_version": "ragas-0.4.1",
    }
    fields.update(overrides)
    return JudgeIdentity(**fields)


def _calibrated(**overrides) -> CalibrationStatus:
    """A record that has earned `calibrated`, which is the expensive one to build."""
    fields = {
        "status": STATUS_CALIBRATED,
        "judge_identity": _identity(),
        "judge_interval": Interval(low=0.41, high=0.83, point=0.62, usable=True),
        "ceiling_interval": Interval(low=0.55, high=0.91, point=0.74, usable=True),
        "difference_interval": Interval(low=-0.09, high=0.24, point=0.12, usable=True),
        "beats_chance": True,
        "ceiling_beats_chance": True,
        "reaches_ceiling": True,
        "kappa": 0.62,
        "matthews": 0.64,
        "scored_pairs": 24,
        "pairs": 30,
        "attempted": 34,
        "valid": 32,
        "labelled_at": "2026-08-29T10:15:00+00:00",
        "harness_version": "compute_correlation-2026-08-29",
    }
    fields.update(overrides)
    return CalibrationStatus(**fields)


class TestConstruction:
    def test_the_record_is_frozen(self):
        """A consumer must not be able to edit a status into a pass on its way to a gate."""
        record = _calibrated()

        with pytest.raises(dataclasses.FrozenInstanceError):
            record.status = "not_calibrated"

    def test_the_interval_is_frozen(self):
        interval = Interval(low=0.1, high=0.2, point=0.15, usable=True)

        with pytest.raises(dataclasses.FrozenInstanceError):
            interval.low = 0.9

    def test_exactly_one_of_the_four_statuses_is_calibrated(self):
        """Iterated, not spot-checked. `not_calibrated_yet` is an absence, never a pass."""
        calibrated = [
            status
            for status in CALIBRATION_STATUSES
            if _record_for(status).calibrated
        ]

        assert calibrated == [STATUS_CALIBRATED]

    def test_an_unknown_status_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="pending"):
            CalibrationStatus(status="pending")

    def test_calibrated_with_no_judge_identity_is_refused(self):
        """A figure with no Judge attached covers every Judge and none."""
        with pytest.raises(InvalidCalibrationStatus, match="judge_identity"):
            _calibrated(judge_identity=None)

    def test_calibrated_with_beats_chance_unevaluated_is_refused(self):
        """None is an absence. Only True earns the one status a gate may act on."""
        with pytest.raises(InvalidCalibrationStatus, match="beats_chance"):
            _calibrated(beats_chance=None)

    def test_calibrated_with_reaches_ceiling_unevaluated_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="reaches_ceiling"):
            _calibrated(reaches_ceiling=None)

    def test_more_scored_pairs_than_pairs_is_refused(self):
        """Spearman's denominator is a subset of the gate's, by the harness's own loop."""
        with pytest.raises(InvalidCalibrationStatus, match="subset"):
            _calibrated(scored_pairs=31, pairs=30)

    def test_a_negative_count_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="zero or above"):
            _calibrated(attempted=-1)

    def test_a_coefficient_that_is_not_a_number_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="kappa"):
            _calibrated(kappa="0.62")

    def test_an_infinite_coefficient_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="finite"):
            _calibrated(matthews=float("inf"))

    def test_an_interval_running_backwards_is_refused(self):
        """Bounds the wrong way round read as a narrow interval, which is the
        opposite of what they are."""
        with pytest.raises(InvalidCalibrationStatus, match="wrong way round"):
            Interval(low=0.8, high=0.2, point=0.5, usable=True)

    def test_an_interval_with_no_bounds_is_allowed_when_it_says_so(self):
        """The harness reports a point over resamples that carried no information."""
        interval = Interval(low=None, high=None, point=0.31, usable=False)

        assert interval.usable is False
        assert interval.low is None

    def test_an_interval_that_does_not_say_whether_it_is_usable_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="usable"):
            Interval(low=0.1, high=0.2, point=0.15, usable=None)


class TestAbsent:
    def test_every_absent_reason_gives_not_calibrated_yet(self):
        for reason in ABSENT_REASONS:
            record = CalibrationStatus.absent(reason)

            assert record.status == STATUS_NOT_CALIBRATED_YET, reason
            assert record.reason == reason
            assert record.calibrated is False
            assert record.judge_identity is None

    def test_an_unlisted_reason_is_refused(self):
        """The token set is closed, because ticket 17's block message switches on it."""
        with pytest.raises(InvalidCalibrationStatus, match="probably_fine"):
            CalibrationStatus.absent("probably_fine")


class TestPayload:
    def test_the_top_level_keys_are_exactly_the_pinned_set(self):
        """Criterion 3, the reader half. A field added or renamed goes red here."""
        assert list(_calibrated().payload) == PAYLOAD_KEYS

    def test_the_interval_keys_are_the_harness_key_names(self):
        interval = Interval(low=0.41, high=0.83, point=0.62, usable=True)

        assert list(interval.payload) == INTERVAL_KEYS

    def test_an_absent_record_carries_the_same_keys(self):
        """One shape, whether a harness produced the record or the loader did."""
        assert list(CalibrationStatus.absent("no_artifact").payload) == PAYLOAD_KEYS

    def test_the_round_trip_returns_an_equal_record(self):
        record = _calibrated()

        assert CalibrationStatus.from_payload(record.payload) == record

    def test_the_round_trip_holds_for_an_absent_record(self):
        record = CalibrationStatus.absent("identity_mismatch")

        assert CalibrationStatus.from_payload(record.payload) == record

    def test_the_payload_defaults_the_artifact_version(self):
        assert _calibrated().payload["artifact_version"] == ARTIFACT_VERSION

    def test_a_payload_with_a_malformed_identity_is_this_modules_refusal(self):
        """`JudgeIdentity(**mapping)` raises TypeError over an extra or missing key.

        The loader catches InvalidCalibrationStatus alone, so a TypeError escaping
        here would escape a function documented never to raise. `pytest.raises`
        does not catch TypeError, so a regression fails this test rather than
        passing it under a different exception.
        """
        payload = _calibrated().payload
        payload["judge_identity"] = {"model": "gpt-5.6-luna", "temperature": 0.0}

        with pytest.raises(InvalidCalibrationStatus, match="cannot be rebuilt"):
            CalibrationStatus.from_payload(payload)

    def test_a_payload_with_an_empty_identity_field_is_this_modules_refusal(self):
        """`InvalidJudgeIdentity` is a ValueError from another module, wrapped here."""
        payload = _calibrated().payload
        payload["judge_identity"] = {
            "model": "",
            "reasoning_effort": "none",
            "prompt_version": "ragas-0.4.1",
        }

        with pytest.raises(InvalidCalibrationStatus):
            CalibrationStatus.from_payload(payload)

    def test_a_payload_with_a_non_mapping_identity_is_refused(self):
        payload = _calibrated().payload
        payload["judge_identity"] = "gpt-5.6-luna"

        with pytest.raises(InvalidCalibrationStatus, match="judge_identity"):
            CalibrationStatus.from_payload(payload)

    def test_a_payload_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="needs a mapping"):
            CalibrationStatus.from_payload(["calibrated"])

    def test_a_payload_with_no_status_is_refused(self):
        payload = _calibrated().payload
        del payload["status"]

        with pytest.raises(InvalidCalibrationStatus, match="needs a status"):
            CalibrationStatus.from_payload(payload)

    def test_a_payload_with_a_malformed_interval_is_refused(self):
        payload = _calibrated().payload
        payload["judge_interval"] = [0.41, 0.83]

        with pytest.raises(InvalidCalibrationStatus, match="mapping"):
            CalibrationStatus.from_payload(payload)

    def test_a_stored_record_that_broke_a_rule_is_refused_on_the_way_out(self):
        """Already being written down is not evidence that a shape is honest."""
        payload = _calibrated().payload
        payload["beats_chance"] = None

        with pytest.raises(InvalidCalibrationStatus, match="beats_chance"):
            CalibrationStatus.from_payload(payload)


def _record_for(status: str) -> CalibrationStatus:
    """One record per status, each built the cheapest way that status allows."""
    if status == STATUS_CALIBRATED:
        return _calibrated()
    return CalibrationStatus(status=status)
