"""Unit tests for app.domain.calibration_status (ticket #53, slice 1).

WHAT THIS TYPE IS FOR
    The calibration harness lives under `tests/`, costs a judge call per labelled
    row, and runs by hand. Nothing on the deploy path can call it. This record is
    how its answer travels to the app, so every rule that stops the answer being
    misread has to live on the record rather than in the harness that produced it.

WHY `calibrated` COSTS MORE THAN THE OTHER THREE STATUSES
    It is the one status a reader may act on. A record can only claim it while
    naming the Judge it measured, carrying all three parts of the verdict as True,
    and carrying the intervals and coefficients the verdict was read off. None is
    refused as hard as False here, because an unevaluated part is an absence and a
    deploy shipping on an absence is shipping on a measurement nobody made.

WHY THE PAYLOAD KEY SET IS PINNED TO A LITERAL
    Criterion 3, the reader half. Slice 2's writer and slice 3's consumer parse
    this key set. A field added, renamed or dropped changes what they read, and a
    round-trip test alone stays green through all three because both halves move
    together. The literal below does not move, so the commit that changes the
    shape is the commit that goes red.

WHY A FOUR-KEY ARTIFACT HAS ITS OWN TEST
    Review pass, 2026-08-30. `from_payload` defaulted every count to 0 and every
    figure to None, and `calibrated` was earned by two booleans. So a file saying
    `{status, judge_identity, beats_chance, reaches_ceiling}` loaded as a
    calibrated Judge with no kappa, no intervals and zero rows behind it, and the
    round trip never noticed because the writer never produces that file. The
    tests below drive the shapes the writer cannot produce, which is where a
    reader's defaults do their damage.
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
    "labels_made_at",
    "harness_version",
    "written_at",
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
        "labels_made_at": "2026-08-29T10:15:00+00:00",
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

    def test_calibrated_with_the_ceiling_never_beating_chance_is_refused(self):
        """The harness's own third part, carried rather than dropped.

        `agreement.calibration_verdict` returns `ceiling_beats_chance: False`
        when the labeller's two passes are not distinguishable from labelling the
        same rows at random, and stops there with `reaches_ceiling: None`. The
        record read two of the three, so a hand-written artifact could set
        `reaches_ceiling: true` beside it and claim a Judge that reached a
        ceiling nobody established.
        """
        with pytest.raises(InvalidCalibrationStatus, match="ceiling_beats_chance"):
            _calibrated(ceiling_beats_chance=False)

    def test_calibrated_with_the_ceiling_part_unevaluated_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="ceiling_beats_chance"):
            _calibrated(ceiling_beats_chance=None)

    def test_calibrated_over_no_labelled_pairs_is_refused(self):
        """`pairs` is what kappa and Matthews are computed over. A verdict over
        zero labelled rows is refused."""
        with pytest.raises(InvalidCalibrationStatus, match="0 pairs"):
            _calibrated(pairs=0, scored_pairs=0)

    def test_a_verdict_only_sheet_can_still_be_calibrated(self):
        """The harness asks the owner to fill the binary column only, so a
        calibrated run may carry zero optional 1-5 scores beside its pairs."""
        assert _calibrated(scored_pairs=0).calibrated is True

    def test_calibrated_with_no_kappa_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="no kappa"):
            _calibrated(kappa=None)

    def test_calibrated_with_no_matthews_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="no matthews"):
            _calibrated(matthews=None)

    def test_calibrated_with_no_judge_interval_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus, match="judge_interval"):
            _calibrated(judge_interval=None)

    def test_calibrated_with_an_unusable_interval_is_refused(self):
        """`usable: False` is never "the judge scored badly". It is "these rows
        say nothing about any judge", which cannot be what a pass rests on."""
        with pytest.raises(InvalidCalibrationStatus, match="ceiling_interval"):
            _calibrated(
                ceiling_interval=Interval(low=None, high=None, point=None, usable=False)
            )

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

    def test_the_written_at_stamp_survives_the_round_trip(self):
        record = _calibrated(written_at="2026-08-30T09:00:00+00:00")

        assert CalibrationStatus.from_payload(record.payload) == record


class TestTheStoredArtifactCarriesEveryKey:
    """A reader's defaults are the one place an artifact can say more than its
    author wrote. Every key is required, and null is allowed only where the field
    is optional by design."""

    def test_a_four_key_artifact_claiming_calibrated_is_refused(self):
        """The finding, driven whole. Every one of these four values is true of a
        calibrated run and the file is still not a measurement: no rows, no
        kappa, no intervals, and two of the three verdict parts unstated."""
        payload = {
            "status": STATUS_CALIBRATED,
            "judge_identity": dataclasses.asdict(_identity()),
            "beats_chance": True,
            "reaches_ceiling": True,
        }

        with pytest.raises(InvalidCalibrationStatus):
            CalibrationStatus.from_payload(payload)

    @pytest.mark.parametrize("key", PAYLOAD_KEYS)
    def test_an_artifact_missing_any_key_is_refused(self, key):
        payload = _calibrated().payload
        del payload[key]

        with pytest.raises(InvalidCalibrationStatus, match=key):
            CalibrationStatus.from_payload(payload)

    def test_a_null_where_the_field_is_optional_is_read_as_the_absence(self):
        """Null and absent are different facts. This is the one that is data."""
        payload = CalibrationStatus.absent("no_artifact").payload
        assert payload["kappa"] is None and payload["labels_made_at"] is None

        record = CalibrationStatus.from_payload(payload)

        assert record.kappa is None
        assert record.labels_made_at is None
        assert record.reason == "no_artifact"

    def test_a_null_count_is_refused(self):
        """The counts are the fields with no honest null. Zero pairs is a run
        that scored nothing, which is a measurement, and null is not."""
        payload = _calibrated().payload
        payload["pairs"] = None

        with pytest.raises(InvalidCalibrationStatus, match="pairs"):
            CalibrationStatus.from_payload(payload)


class TestTheArtifactVersionIsCompared:
    """It was stored and never read, which made it decoration. The constant is
    bumped when an older artifact would be READ WRONGLY rather than refused, so
    meeting one and reading it anyway is the exact accident it exists to stop."""

    def test_an_artifact_from_another_version_is_refused(self):
        payload = _calibrated().payload
        payload["artifact_version"] = ARTIFACT_VERSION + 1

        with pytest.raises(InvalidCalibrationStatus) as raised:
            CalibrationStatus.from_payload(payload)

        assert str(ARTIFACT_VERSION) in str(raised.value)
        assert str(ARTIFACT_VERSION + 1) in str(raised.value), (
            "the line has to name both, or a reader cannot tell which build wrote it"
        )

    def test_the_version_this_build_writes_is_accepted(self):
        record = _calibrated()

        assert CalibrationStatus.from_payload(record.payload).artifact_version == (
            ARTIFACT_VERSION
        )

    def test_a_boolean_version_does_not_pass_for_one(self):
        """`True == 1` in Python, so the type is checked before the value."""
        payload = _calibrated().payload
        payload["artifact_version"] = True

        with pytest.raises(InvalidCalibrationStatus, match="artifact_version"):
            CalibrationStatus.from_payload(payload)


def _record_for(status: str) -> CalibrationStatus:
    """One record per status, each built the cheapest way that status allows."""
    if status == STATUS_CALIBRATED:
        return _calibrated()
    return CalibrationStatus(status=status)


# ---------------------------------------------------------------------------
# from_harness, the writer half of criterion 3 (ticket #53, slice 2)
# ---------------------------------------------------------------------------

#: One `compute_correlation` result dict, with every key the mapping reads.
#: Written out rather than imported from the harness, for the same reason
#: PAYLOAD_KEYS is a literal. A fixture derived from the producer agrees with
#: whatever the producer currently emits.
def _result(**overrides) -> dict:
    fields = {
        "kappa": 0.62,
        "matthews": 0.64,
        "judge_interval": {"low": 0.41, "high": 0.83, "point": 0.62, "usable": True},
        "ceiling_interval": {"low": 0.55, "high": 0.91, "point": 0.74, "usable": True},
        "difference_interval": {
            "low": -0.09, "high": 0.24, "point": 0.12, "usable": True,
        },
        "gate": {
            "beats_chance": True,
            "ceiling_beats_chance": True,
            "reaches_ceiling": True,
            "calibrated": True,
            "reasons": [],
        },
        "scored_pairs": 24,
        "pairs": 30,
        "attempted": 34,
        "valid": 32,
        # Carried by the harness and deliberately not mapped: the report card a
        # human reads at the terminal, and three numbers nothing gates on.
        "cells": {"both_pass": 20, "both_fail": 8},
        "rho": 0.91,
        "pair_rate": 0.9375,
        "errors": [],
        "table": [],
        "status": STATUS_CALIBRATED,
    }
    fields.update(overrides)
    return fields


#: Every key `from_harness` reads out of the result dict. A parametrised refusal
#: over this list is what goes red when the mapping stops reading one of them.
MAPPED_HARNESS_KEYS = [
    "kappa",
    "matthews",
    "judge_interval",
    "ceiling_interval",
    "difference_interval",
    "gate",
    "scored_pairs",
    "pairs",
    "attempted",
    "valid",
]


def _from_harness(result, **overrides) -> CalibrationStatus:
    fields = {
        "status": STATUS_CALIBRATED,
        "judge_identity": _identity(),
        "labels_made_at": "2026-08-29T10:15:00+00:00",
        "harness_version": "compute_correlation.py@1",
    }
    fields.update(overrides)
    return CalibrationStatus.from_harness(result, **fields)


class TestFromHarnessMapsEveryKey:
    """The harness's dict is copied onto the record, one key at a time."""

    def test_every_mapped_key_reaches_its_field(self):
        record = _from_harness(_result())

        assert record.kappa == 0.62
        assert record.matthews == 0.64
        assert record.judge_interval == Interval(
            low=0.41, high=0.83, point=0.62, usable=True
        )
        assert record.ceiling_interval == Interval(
            low=0.55, high=0.91, point=0.74, usable=True
        )
        assert record.difference_interval == Interval(
            low=-0.09, high=0.24, point=0.12, usable=True
        )
        assert record.scored_pairs == 24
        assert record.pairs == 30
        assert record.attempted == 34
        assert record.valid == 32

    def test_the_four_fields_the_result_dict_does_not_hold_come_from_the_caller(self):
        """None of these four is in the result dict, and only the writer knows them."""
        record = _from_harness(_result(), status=STATUS_NOT_CALIBRATED_YET)

        assert record.status == STATUS_NOT_CALIBRATED_YET
        assert record.judge_identity == _identity()
        assert record.labels_made_at == "2026-08-29T10:15:00+00:00"
        assert record.harness_version == "compute_correlation.py@1"

    def test_the_status_is_the_callers_and_not_the_result_dicts(self):
        """A run the harness called calibrated over a Judge nobody can name is not
        a calibrated record, and the writer is what knows which case it is."""
        record = _from_harness(
            _result(status=STATUS_CALIBRATED),
            status=STATUS_NOT_CALIBRATED_YET,
            judge_identity=None,
        )

        assert record.status == STATUS_NOT_CALIBRATED_YET
        assert record.calibrated is False

    def test_the_record_it_builds_round_trips_through_its_own_payload(self):
        record = _from_harness(_result())

        assert CalibrationStatus.from_payload(record.payload) == record

    def test_nothing_the_harness_reports_but_does_not_gate_on_reaches_the_record(self):
        """`cells`, `rho` and `pair_rate` are the terminal's, not the gate's."""
        payload = _from_harness(_result()).payload

        assert set(payload).isdisjoint({"cells", "rho", "pair_rate", "errors", "table"})


class TestFromHarnessCarriesTheVerdictAsStated:
    """Three parts, copied. None is an absence and never becomes False."""

    def test_the_three_parts_come_off_the_gate(self):
        record = _from_harness(_result())

        assert record.beats_chance is True
        assert record.ceiling_beats_chance is True
        assert record.reaches_ceiling is True

    def test_a_part_the_gate_left_unevaluated_stays_none(self):
        """`calibration_verdict` writes None for a part nobody could evaluate."""
        gate = dict(_result()["gate"], beats_chance=None, calibrated=False)
        record = _from_harness(_result(gate=gate), status=STATUS_NOT_CALIBRATED_YET)

        assert record.beats_chance is None, "None, never False"
        assert record.ceiling_beats_chance is True

    def test_a_run_that_reached_no_gate_carries_three_absences(self):
        """The four early returns in the harness carry `gate` as None."""
        record = _from_harness(_result(gate=None), status=STATUS_NOT_CALIBRATED_YET)

        assert record.beats_chance is None
        assert record.ceiling_beats_chance is None
        assert record.reaches_ceiling is None
        assert record.reason is None

    def test_the_gates_sentences_become_the_reason(self):
        gate = dict(
            _result()["gate"],
            reaches_ceiling=None,
            calibrated=False,
            reasons=["(b) NOT MEASURED.", "Run --emit-second-pass."],
        )
        record = _from_harness(_result(gate=gate), status=STATUS_NOT_CALIBRATED_YET)

        assert record.reason == "(b) NOT MEASURED. Run --emit-second-pass."

    def test_a_gate_that_wrote_no_sentences_leaves_the_reason_absent(self):
        assert _from_harness(_result()).reason is None

    def test_a_gate_missing_one_of_its_three_parts_is_refused(self):
        gate = {k: v for k, v in _result()["gate"].items() if k != "reaches_ceiling"}

        with pytest.raises(InvalidCalibrationStatus):
            _from_harness(_result(gate=gate))


class TestFromHarnessRefusesWhatItCannotMap:
    """A missing key is refused, never defaulted."""

    def test_a_result_missing_the_judge_interval_is_refused(self):
        result = {k: v for k, v in _result().items() if k != "judge_interval"}

        with pytest.raises(InvalidCalibrationStatus) as exc:
            _from_harness(result)

        assert "judge_interval" in str(exc.value)

    @pytest.mark.parametrize("key", MAPPED_HARNESS_KEYS)
    def test_a_result_missing_any_mapped_key_is_refused(self, key):
        """Parametrised over the whole mapping, so a key the mapping quietly stops
        reading turns this red on the commit that drops it."""
        result = {k: v for k, v in _result().items() if k != key}

        with pytest.raises(InvalidCalibrationStatus) as exc:
            _from_harness(result)

        assert key in str(exc.value)

    def test_a_result_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus):
            _from_harness(["kappa", 0.62])

    def test_a_gate_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(InvalidCalibrationStatus):
            _from_harness(_result(gate="calibrated"))

    def test_an_interval_the_harness_left_undefined_is_none_and_not_a_refusal(self):
        record = _from_harness(
            _result(ceiling_interval=None, difference_interval=None),
            status=STATUS_NOT_CALIBRATED_YET,
        )

        assert record.ceiling_interval is None
        assert record.difference_interval is None
        assert record.judge_interval is not None

    def test_a_calibrated_status_with_no_judge_identity_is_still_refused(self):
        """The record's own rule, reached through the mapping."""
        with pytest.raises(InvalidCalibrationStatus):
            _from_harness(_result(), judge_identity=None)
