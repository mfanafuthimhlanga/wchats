"""Unit tests for app.services.calibration_service (ticket #53, slice 1).

WHAT THE LOADER IS FOR
    The calibration harness cannot be called from the deploy path, so its answer
    arrives as a file. This function turns that file into the record, and turns
    every way of not getting a file into a reason.

WHY "NEVER RAISES" IS ITS OWN TEST AND NOT A REMARK
    The caller is a deploy summary. A missing artifact, a truncated write, a
    shape this build refuses and a Judge the artifact never measured are four
    accidents that say nothing about whether a Judge is calibrated, and any one
    of them taking a deploy summary down would be a defect. Each failure is
    driven here for real against `tmp_path`, not stubbed, and
    `test_no_failure_path_raises` walks all six in one loop so a new path added
    without a reason cannot slip through.

WHY THE CONFIG DEFAULT IS ASSERTED
    `CALIBRATION_ARTIFACT_PATH` defaults to a path under `tests/`, which
    `apps/api/.dockerignore` excludes. That is deliberate: a container reads
    `no_artifact` and no Judge is trusted there until an artifact ships. A test
    that the default is absolute keeps it from silently becoming
    working-directory dependent, which would make the answer vary by who started
    the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog.testing

from app.domain.calibration_status import (
    ABSENT_REASONS,
    ARTIFACT_VERSION,
    STATUS_CALIBRATED,
    STATUS_NOT_CALIBRATED_YET,
    CalibrationStatus,
    Interval,
)
from app.domain.judge_identity import JudgeIdentity
from app.services.calibration_service import (
    MAX_ARTIFACT_BYTES,
    SUMMARY_KEYS,
    load_calibration_status,
    summary_of,
)


def _identity(**overrides) -> JudgeIdentity:
    fields = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "prompt_version": "ragas-0.4.1",
    }
    fields.update(overrides)
    return JudgeIdentity(**fields)


def _calibrated(identity: JudgeIdentity) -> CalibrationStatus:
    return CalibrationStatus(
        status=STATUS_CALIBRATED,
        judge_identity=identity,
        judge_interval=Interval(low=0.41, high=0.83, point=0.62, usable=True),
        ceiling_interval=Interval(low=0.55, high=0.91, point=0.74, usable=True),
        difference_interval=Interval(low=-0.09, high=0.24, point=0.12, usable=True),
        beats_chance=True,
        ceiling_beats_chance=True,
        reaches_ceiling=True,
        kappa=0.62,
        matthews=0.64,
        scored_pairs=24,
        pairs=30,
        attempted=34,
        valid=32,
        labelled_at="2026-08-29T10:15:00+00:00",
        harness_version="compute_correlation-2026-08-29",
    )


def _nameless_run() -> CalibrationStatus:
    """What the harness writes today: figures, and no Judge to attach them to.

    Deliberately not an `absent()` record. `absent` carries no figures either,
    and this shape is the one that used to read as `identity_mismatch` while
    holding a real kappa over real rows.
    """
    return CalibrationStatus(
        status=STATUS_NOT_CALIBRATED_YET,
        reason="no_single_judge_identity",
        judge_identity=None,
        judge_interval=Interval(low=0.41, high=0.83, point=0.62, usable=True),
        beats_chance=True,
        kappa=0.62,
        scored_pairs=24,
        pairs=30,
        harness_version="compute_correlation.py@1",
    )


def _write(path: Path, record: CalibrationStatus) -> Path:
    path.write_text(json.dumps(record.payload), encoding="utf-8")
    return path


class TestTheMatchingArtifact:
    def test_a_matching_artifact_loads_as_calibrated(self, tmp_path):
        identity = _identity()
        artifact = _write(tmp_path / "calibration.json", _calibrated(identity))

        status = load_calibration_status(artifact, identity)

        assert status.calibrated is True
        assert status.status == STATUS_CALIBRATED
        assert status.reason is None
        assert status.judge_identity == identity

    def test_the_loaded_record_carries_its_intervals(self, tmp_path):
        """The three intervals are what ticket 17 shows beside a block reason."""
        identity = _identity()
        artifact = _write(tmp_path / "calibration.json", _calibrated(identity))

        status = load_calibration_status(artifact, identity)

        assert status.judge_interval == Interval(low=0.41, high=0.83, point=0.62, usable=True)
        assert status.ceiling_interval == Interval(low=0.55, high=0.91, point=0.74, usable=True)
        assert status.difference_interval == Interval(
            low=-0.09, high=0.24, point=0.12, usable=True
        )
        assert status.kappa == 0.62
        assert status.matthews == 0.64

    def test_the_loaded_record_equals_what_was_written(self, tmp_path):
        identity = _identity()
        record = _calibrated(identity)
        artifact = _write(tmp_path / "calibration.json", record)

        assert load_calibration_status(artifact, identity) == record

    def test_a_path_like_object_is_accepted(self, tmp_path):
        """The setting arrives as a string and a caller may hold a Path."""
        identity = _identity()
        artifact = _write(tmp_path / "calibration.json", _calibrated(identity))

        assert load_calibration_status(str(artifact), identity).calibrated is True


class TestTheFailurePaths:
    def test_a_missing_file_reads_no_artifact(self, tmp_path):
        """What a container reads, because .dockerignore excludes tests/."""
        status = load_calibration_status(tmp_path / "nothing.json", _identity())

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "no_artifact"
        assert status.calibrated is False

    def test_a_file_that_is_not_json_reads_unreadable(self, tmp_path):
        artifact = tmp_path / "calibration.json"
        artifact.write_text("{ this was half written when the run died", encoding="utf-8")

        status = load_calibration_status(artifact, _identity())

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "unreadable"

    def test_a_directory_at_the_path_reads_unreadable(self, tmp_path):
        """Something is there and it is not an artifact, which is not the same
        thing as nothing being there."""
        directory = tmp_path / "calibration.json"
        directory.mkdir()

        assert load_calibration_status(directory, _identity()).reason == "unreadable"

    def test_json_this_record_refuses_reads_invalid(self, tmp_path):
        """Calibrated with no Judge attached. The record refuses it, so does this."""
        artifact = tmp_path / "calibration.json"
        payload = _calibrated(_identity()).payload
        payload["judge_identity"] = None
        artifact.write_text(json.dumps(payload), encoding="utf-8")

        status = load_calibration_status(artifact, _identity())

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "invalid"

    def test_json_that_is_not_an_object_reads_invalid(self, tmp_path):
        artifact = tmp_path / "calibration.json"
        artifact.write_text(json.dumps(["calibrated"]), encoding="utf-8")

        assert load_calibration_status(artifact, _identity()).reason == "invalid"

    def test_a_run_with_no_single_judge_reads_no_single_judge_identity(self, tmp_path):
        """Settled before any I/O: no artifact can match a run that used two Judges."""
        artifact = _write(tmp_path / "calibration.json", _calibrated(_identity()))

        status = load_calibration_status(artifact, None)

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "no_single_judge_identity"

    def test_an_artifact_for_another_judge_reads_identity_mismatch(self, tmp_path):
        artifact = _write(tmp_path / "calibration.json", _calibrated(_identity()))

        status = load_calibration_status(artifact, _identity(model="gpt-4o-legacy"))

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "identity_mismatch"
        assert status.judge_identity is None

    def test_a_mismatch_on_reasoning_effort_alone_is_still_a_mismatch(self, tmp_path):
        """Decision #34 prices the Judge floor at effort `none` and re-measures any
        increase. An effort change is a different Judge, not the same one working
        harder."""
        artifact = _write(tmp_path / "calibration.json", _calibrated(_identity()))

        status = load_calibration_status(artifact, _identity(reasoning_effort="high"))

        assert status.reason == "identity_mismatch"

    def test_a_mismatch_on_prompt_version_alone_is_still_a_mismatch(self, tmp_path):
        artifact = _write(tmp_path / "calibration.json", _calibrated(_identity()))

        status = load_calibration_status(artifact, _identity(prompt_version="ragas-0.5.0"))

        assert status.reason == "identity_mismatch"

    def test_an_artifact_that_names_no_judge_says_that_and_not_mismatch(self, tmp_path):
        """The state of every artifact this harness writes today (#58).

        It reported `identity_mismatch`, which says a Judge was measured and it
        was a different one. Nobody was measured. The two send an owner to
        different work.
        """
        artifact = _write(tmp_path / "calibration.json", _nameless_run())

        status = load_calibration_status(artifact, _identity())

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "artifact_names_no_judge"

    def test_a_nameless_artifact_is_answered_before_the_comparison(self, tmp_path):
        """Whichever Judge the run used. There is nothing on the file to compare."""
        artifact = _write(tmp_path / "calibration.json", _nameless_run())

        for run in (_identity(), _identity(model="something-else")):
            assert load_calibration_status(artifact, run).reason == (
                "artifact_names_no_judge"
            ), run

    def test_a_file_over_the_ceiling_is_unreadable_and_is_never_read(self, tmp_path):
        """Two megabytes at a path a deploy summary reads inside the API process."""
        artifact = tmp_path / "calibration.json"
        artifact.write_bytes(b"\0" * (2 * 1024 * 1024))

        status = load_calibration_status(artifact, _identity())

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "unreadable"

    def test_a_file_at_the_ceiling_is_still_read(self, tmp_path):
        """The boundary, so the guard cannot quietly refuse the honest artifact."""
        artifact = _write(tmp_path / "calibration.json", _calibrated(_identity()))
        assert artifact.stat().st_size <= MAX_ARTIFACT_BYTES

        assert load_calibration_status(artifact, _identity()).calibrated is True

    def test_every_reason_the_loader_can_return_is_a_declared_absence(self, tmp_path):
        """`CalibrationStatus.absent` holds a closed set, so a reason invented in
        the loader would be refused at construction. This walks the loader's own
        six and pins them to that set."""
        reasons = {
            load_calibration_status(tmp_path / "gone.json", _identity()).reason,
            _unreadable_status(tmp_path).reason,
            _invalid_status(tmp_path).reason,
            load_calibration_status(
                _write(tmp_path / "ok.json", _calibrated(_identity())), None
            ).reason,
            load_calibration_status(
                _write(tmp_path / "nameless.json", _nameless_run()), _identity()
            ).reason,
            load_calibration_status(
                _write(tmp_path / "ok.json", _calibrated(_identity())),
                _identity(model="other"),
            ).reason,
        }

        assert reasons == set(ABSENT_REASONS)


class TestItNeverRaises:
    def test_no_failure_path_raises(self, tmp_path):
        """The contract the deploy summary depends on, driven for real."""
        good = _write(tmp_path / "ok.json", _calibrated(_identity()))
        broken = tmp_path / "broken.json"
        broken.write_text("not json at all", encoding="utf-8")
        refused = tmp_path / "refused.json"
        refused.write_text(json.dumps({"status": "made_up"}), encoding="utf-8")
        directory = tmp_path / "adirectory.json"
        directory.mkdir()

        nameless = _write(tmp_path / "nameless.json", _nameless_run())
        huge = tmp_path / "huge.json"
        huge.write_bytes(b"\0" * (2 * 1024 * 1024))

        cases = [
            (tmp_path / "gone.json", _identity()),
            (broken, _identity()),
            (refused, _identity()),
            (directory, _identity()),
            (huge, _identity()),
            (good, None),
            (nameless, _identity()),
            (good, _identity(model="somebody-elses-judge")),
        ]

        for path, identity in cases:
            status = load_calibration_status(path, identity)

            assert status.status == STATUS_NOT_CALIBRATED_YET, path
            assert status.reason in ABSENT_REASONS, path
            assert status.calibrated is False, path


class TestTheMismatchLog:
    def test_the_log_names_both_identities(self, tmp_path):
        """An operator reading this line has to see which Judge the figure covers
        and which one the run used, without opening either file."""
        stored = _identity(model="gpt-5.6-luna", prompt_version="ragas-0.4.1")
        artifact = _write(tmp_path / "calibration.json", _calibrated(stored))
        run = _identity(model="gpt-4o-legacy", prompt_version="ragas-0.5.0")

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, run)

        entry = _one_event(logs, "calibration_identity_mismatch")
        assert entry["run_model"] == "gpt-4o-legacy"
        assert entry["run_prompt_version"] == "ragas-0.5.0"
        assert entry["artifact_model"] == "gpt-5.6-luna"
        assert entry["artifact_prompt_version"] == "ragas-0.4.1"

    def test_the_line_shows_which_field_moved_when_only_the_effort_did(self, tmp_path):
        """The whole reason effort is on the line. It carried model and prompt
        version only, so a Judge re-priced from `none` to `low` logged four
        identical values under a warning that said the two differed."""
        artifact = _write(
            tmp_path / "calibration.json", _calibrated(_identity(reasoning_effort="none"))
        )

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity(reasoning_effort="low"))

        entry = _one_event(logs, "calibration_identity_mismatch")
        assert entry["run_reasoning_effort"] == "low"
        assert entry["artifact_reasoning_effort"] == "none"
        assert entry["run_model"] == entry["artifact_model"]
        assert entry["run_prompt_version"] == entry["artifact_prompt_version"]

    def test_the_mismatch_log_carries_those_six_fields_and_no_others(self, tmp_path):
        """Pinned, because the line is written for an operator and every extra
        field is one more thing to read past. Six is both identities whole: model,
        effort and prompt version are the three the calibration key groups on, and
        a line short of one of them cannot say which Judge moved."""
        artifact = _write(tmp_path / "calibration.json", _calibrated(_identity()))

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity(model="gpt-4o-legacy"))

        entry = _one_event(logs, "calibration_identity_mismatch")
        assert set(entry) == {
            "event",
            "log_level",
            "run_model",
            "run_reasoning_effort",
            "run_prompt_version",
            "artifact_model",
            "artifact_reasoning_effort",
            "artifact_prompt_version",
        }

    def test_an_unreadable_artifact_logs_once(self, tmp_path):
        artifact = tmp_path / "calibration.json"
        artifact.write_text("{", encoding="utf-8")

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity())

        entry = _one_event(logs, "calibration_artifact_unreadable")
        assert entry["path"] == str(artifact)
        assert entry["error"].startswith("JSONDecodeError")

    def test_a_refused_artifact_logs_once_naming_the_rule_it_broke(self, tmp_path):
        """A whole artifact with one bad field, so the line names the field and
        not the first key the reader happened to miss."""
        payload = _calibrated(_identity()).payload
        payload["status"] = "made_up"
        artifact = tmp_path / "calibration.json"
        artifact.write_text(json.dumps(payload), encoding="utf-8")

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity())

        entry = _one_event(logs, "calibration_artifact_invalid")
        assert "made_up" in entry["error"]

    def test_an_artifact_from_another_version_reads_invalid(self, tmp_path):
        """The version was stored and never compared until the review pass. A
        build that changed what a field means would have been read under this
        build's rules and reported as a measurement."""
        payload = _calibrated(_identity()).payload
        payload["artifact_version"] = ARTIFACT_VERSION + 1
        artifact = tmp_path / "calibration.json"
        artifact.write_text(json.dumps(payload), encoding="utf-8")

        status = load_calibration_status(artifact, _identity())

        assert status.status == STATUS_NOT_CALIBRATED_YET
        assert status.reason == "invalid"

    def test_the_invalid_line_names_both_versions(self, tmp_path):
        payload = _calibrated(_identity()).payload
        payload["artifact_version"] = 99
        artifact = tmp_path / "calibration.json"
        artifact.write_text(json.dumps(payload), encoding="utf-8")

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity())

        entry = _one_event(logs, "calibration_artifact_invalid")
        assert entry["reader_artifact_version"] == ARTIFACT_VERSION
        assert entry["artifact_version"] == 99

    def test_the_invalid_line_survives_an_artifact_with_no_version_at_all(self, tmp_path):
        """The refusal comes from a missing key here, so the file cannot answer
        what version it is. The line says so rather than failing to be written."""
        artifact = tmp_path / "calibration.json"
        artifact.write_text(json.dumps(["calibrated"]), encoding="utf-8")

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity())

        entry = _one_event(logs, "calibration_artifact_invalid")
        assert entry["artifact_version"] is None
        assert entry["reader_artifact_version"] == ARTIFACT_VERSION

    def test_a_nameless_artifact_logs_the_run_identity_and_nothing_else(self, tmp_path):
        """Three fields, all of them the run's. The artifact has no identity to
        print, and printing three Nones beside three real values is what made the
        old mismatch line read as a bug in the loader."""
        artifact = _write(tmp_path / "calibration.json", _nameless_run())

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity(model="gpt-5.6-luna"))

        entry = _one_event(logs, "calibration_artifact_names_no_judge")
        assert entry["run_model"] == "gpt-5.6-luna"
        assert set(entry) == {
            "event",
            "log_level",
            "run_model",
            "run_reasoning_effort",
            "run_prompt_version",
        }

    def test_a_missing_artifact_says_where_it_looked(self, tmp_path):
        """`no_artifact` said the same thing about a healthy container and a typo
        in the setting. `parent_exists` is the field that separates them."""
        with structlog.testing.capture_logs() as logs:
            load_calibration_status(tmp_path / "gone.json", _identity())

        entry = _one_event(logs, "calibration_artifact_absent")
        assert entry["path"] == str(tmp_path / "gone.json")
        assert entry["parent_exists"] is True

    def test_a_missing_directory_is_the_shape_that_reads_as_a_typo(self, tmp_path):
        with structlog.testing.capture_logs() as logs:
            load_calibration_status(tmp_path / "nodir" / "gone.json", _identity())

        entry = _one_event(logs, "calibration_artifact_absent")
        assert entry["parent_exists"] is False

    def test_a_missing_artifact_never_warns(self, tmp_path):
        """The normal state of a container. A warning per deploy summary would
        train an operator to ignore the log, which is why the line is info."""
        with structlog.testing.capture_logs() as logs:
            load_calibration_status(tmp_path / "gone.json", _identity())

        assert [entry["log_level"] for entry in logs] == ["info"]

    def test_an_oversized_artifact_logs_its_size(self, tmp_path):
        artifact = tmp_path / "calibration.json"
        artifact.write_bytes(b"\0" * (2 * 1024 * 1024))

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, _identity())

        entry = _one_event(logs, "calibration_artifact_unreadable")
        assert entry["size_bytes"] == 2 * 1024 * 1024
        assert str(MAX_ARTIFACT_BYTES) in entry["error"]

    def test_a_matching_artifact_logs_nothing(self, tmp_path):
        identity = _identity()
        artifact = _write(tmp_path / "calibration.json", _calibrated(identity))

        with structlog.testing.capture_logs() as logs:
            load_calibration_status(artifact, identity)

        assert logs == []


class TestTheSetting:
    def test_the_default_path_is_absolute(self):
        """Resolved off config.py, so the answer does not vary with the working
        directory the process was started from."""
        from app.core.config import _calibration_artifact_file

        assert Path(_calibration_artifact_file()).is_absolute()

    def test_the_default_points_at_the_harness_directory(self):
        from app.core.config import _calibration_artifact_file

        path = Path(_calibration_artifact_file())

        assert path.name == "calibration.json"
        assert path.parent.name == "calibration"
        assert path.parent.parent.name == "evals"

    def test_the_setting_has_a_default_so_it_is_not_a_boot_requirement(self):
        from app.core.config import Settings

        assert not Settings.model_fields["CALIBRATION_ARTIFACT_PATH"].is_required()


class TestTheDeploySummarySelection:
    """`summary_of` - the eleven keys the deploy summary carries, and no twelfth.

    The record holds nineteen fields and the deploy summary carries eleven. The
    eight left off are the harness's working (`difference_interval`, the three
    verdict parts) and the bookkeeping a reader of a deploy report has no use for
    (`attempted`, `valid`, `written_at`, `artifact_version`). Ticket 17's refusal
    reads the record itself, so nothing downstream needs them on the dict.
    """

    def test_it_carries_exactly_the_declared_keys_in_order(self):
        assert tuple(summary_of(_calibrated(_identity()))) == SUMMARY_KEYS

    def test_every_value_is_the_one_payload_holds(self):
        """Selected, never re-derived.

        A second spelling of one record is free to disagree with the first, and
        the one that disagreed would be whichever the deploy summary read.
        """
        record = _calibrated(_identity())
        summary = summary_of(record)

        for key in SUMMARY_KEYS:
            assert summary[key] == record.payload[key], key

    def test_the_calibrated_verdict_is_not_a_second_key(self):
        """A consumer reads `status`. Two answers to one question can differ."""
        summary = summary_of(_calibrated(_identity()))

        assert "calibrated" not in summary
        assert "beats_chance" not in summary
        assert summary["status"] == STATUS_CALIBRATED

    def test_an_absence_carries_its_reason_and_no_figures(self):
        for reason in ABSENT_REASONS:
            summary = summary_of(CalibrationStatus.absent(reason))

            assert summary["status"] == STATUS_NOT_CALIBRATED_YET, reason
            assert summary["reason"] == reason
            assert summary["judge_identity"] is None
            assert summary["kappa"] is None
            assert summary["judge_interval"] is None
            assert summary["harness_version"] is None


def _unreadable_status(tmp_path: Path) -> CalibrationStatus:
    artifact = tmp_path / "unreadable.json"
    artifact.write_text("{", encoding="utf-8")
    return load_calibration_status(artifact, _identity())


def _invalid_status(tmp_path: Path) -> CalibrationStatus:
    artifact = tmp_path / "invalid.json"
    artifact.write_text(json.dumps({"status": "made_up"}), encoding="utf-8")
    return load_calibration_status(artifact, _identity())


def _one_event(logs: list[dict], event: str) -> dict:
    """The single captured line carrying `event`. Two would mean a double log."""
    entries = [entry for entry in logs if entry["event"] == event]

    assert len(entries) == 1, f"expected one {event} line, got {logs}"
    return entries[0]
