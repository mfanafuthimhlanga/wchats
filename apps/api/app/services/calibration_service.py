"""Read the calibration artifact, and answer for a Judge the run actually used (ticket #53, slice 1).

WHY THIS FUNCTION NEVER RAISES
    Its caller is a deploy summary, and every way of reading a file off disk fails
    for reasons that have nothing to do with whether a Judge is calibrated. A
    missing artifact, a truncated write, a half-written JSON object and a shape
    this build refuses are four different accidents, and none of them is evidence
    about the Judge. Each returns `not_calibrated_yet` carrying the reason, which
    is the honest reading. Nobody measured this Judge here. An exception would
    take a deploy summary down over a file that was never written, and the
    fail-closed answer already covers the case an exception would be protecting.

WHY THE IDENTITY IS CHECKED BEFORE THE FILE IS OPENED
    A run with no single Judge identity cannot be compared against any artifact,
    whatever the file says, so the question is settled before any I/O. Reporting
    `no_artifact` there would send an owner to write an artifact that still would
    not match.

WHY AN ARTIFACT THAT NAMES NO JUDGE IS NOT A MISMATCH
    A mismatch says somebody was measured and it was somebody else. A null
    identity says nobody was measured, which is what every artifact this harness
    writes today reports (#58). They were one branch, so the honest answer came
    out as the wrong one and the log line beside it carried three None fields.

WHY A MISMATCH IS AN ABSENCE AND NOT A FAILURE
    An artifact measured against a different model, effort or prompt says nothing
    about this run's Judge. `.dev/reference/260818-llm-eval-fundamentals.md`
    section 9 (lines 119-148) is the reason the key is three fields wide. A judge
    is built to agree with human labels, and alignment decays when the prompt, the
    data or the model moves. Reading yesterday's figure over today's Judge is the
    decay going unnoticed. So a mismatch reports `not_calibrated_yet`, never
    `not_calibrated`, because the second would send somebody to fix a Judge that
    may be fine.

WHAT THE INVALID LOG SAYS
    Both artifact versions, this reader's and the file's. `from_payload` refuses
    an artifact built under other construction rules, and the two most likely
    reasons an operator sees that line are a stale file and a reader that moved
    on. The line answers which one without opening the file.

WHAT THE MISMATCH LOG SAYS
    All three fields of both identities, so the line shows WHICH ONE moved. It
    carried model and prompt version only until slice 2, and `reasoning_effort`
    is the third field of the key. Two identities differing on effort alone
    printed four identical values under a warning that said they differed, which
    reads as a bug in the loader rather than a change in the Judge. Decision #34
    prices the Judge floor at effort `none` and re-measures any increase, so
    effort moving on its own is a case this project expects to see.

Rung: `app.services` may import `app.domain`, `app.core`, `app.models` and
`app.utils`. This module imports the standard library, structlog and
`app.domain`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import structlog

from app.domain.calibration_status import (
    ARTIFACT_VERSION,
    CalibrationStatus,
    InvalidCalibrationStatus,
)
from app.domain.judge_identity import JudgeIdentity

log = structlog.get_logger(__name__)

#: The largest artifact this reads into memory. One record of nineteen keys is
#: under two kilobytes, so a megabyte is five hundred times the honest size and
#: still cheap to refuse. The ceiling exists because this runs inside the API
#: process while it assembles a deploy summary. Whatever else ends up at that
#: path, reading all of it before anything can judge the size costs the API that
#: memory, and `unreadable` is already the honest answer for a file that is not
#: this artifact.
MAX_ARTIFACT_BYTES = 1024 * 1024


def load_calibration_status(
    path: str | os.PathLike, identity: JudgeIdentity | None
) -> CalibrationStatus:
    """What the calibration artifact says about `identity`, or why it says nothing.

    Args:
        path:     where the artifact is, normally `settings.CALIBRATION_ARTIFACT_PATH`.
        identity: the one Judge this run used, or None when it had no single one.

    Returns:
        The stored record when the artifact matches `identity`, and
        `CalibrationStatus.absent(reason)` for every other outcome. Never raises.
    """
    if identity is None:
        return CalibrationStatus.absent("no_single_judge_identity")

    artifact = Path(path)
    refused = _refused_before_reading(artifact)
    if refused is not None:
        return CalibrationStatus.absent(refused)

    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CalibrationStatus.absent(_absent_artifact(artifact))
    except (MemoryError, OSError, UnicodeDecodeError, ValueError) as exc:
        log.warning(
            "calibration_artifact_unreadable",
            path=str(artifact),
            error=f"{type(exc).__name__}: {exc}",
        )
        return CalibrationStatus.absent("unreadable")

    try:
        record = CalibrationStatus.from_payload(payload)
    except InvalidCalibrationStatus as exc:
        log.warning(
            "calibration_artifact_invalid",
            path=str(artifact),
            error=str(exc),
            reader_artifact_version=ARTIFACT_VERSION,
            artifact_version=(
                payload.get("artifact_version") if isinstance(payload, Mapping) else None
            ),
        )
        return CalibrationStatus.absent("invalid")

    absence = _judge_absence(record.judge_identity, identity)
    if absence is not None:
        return CalibrationStatus.absent(absence)
    return record


def _absent_artifact(artifact: Path) -> str:
    """Say where it looked and whether that directory exists. Stamp `no_artifact`.

    INFO, NEVER A WARNING. A missing artifact is the normal state of a container,
    because `apps/api/.dockerignore` excludes `tests/`, and one warning per deploy
    summary trains an operator to ignore the log.

    It logs at all because `no_artifact` said the same thing about two states that
    need different people. A container with no calibration directory is healthy; a
    path with a typo in it is a setting nobody has read since it was typed.
    `parent_exists=False` is the second one, and the two were indistinguishable
    from the outside.

    The path is the one it opened. `CALIBRATION_ARTIFACT_PATH` resolves off
    `config.py` and is absolute, pinned by `test_the_default_path_is_absolute`.
    """
    log.info(
        "calibration_artifact_absent",
        path=str(artifact),
        parent_exists=artifact.parent.exists(),
    )
    return "no_artifact"


def _refused_before_reading(artifact: Path) -> str | None:
    """Which absence this path is before any of it is read, or None to go on.

    `os.stat` answers the size question without opening anything.
    `Path.read_text` commits the whole file to memory first, so a size check
    after it has already paid the cost it was meant to avoid.
    """
    try:
        size = artifact.stat().st_size
    except FileNotFoundError:
        return _absent_artifact(artifact)
    except OSError as exc:
        log.warning(
            "calibration_artifact_unreadable",
            path=str(artifact),
            error=f"{type(exc).__name__}: {exc}",
        )
        return "unreadable"

    if size > MAX_ARTIFACT_BYTES:
        log.warning(
            "calibration_artifact_unreadable",
            path=str(artifact),
            error=f"{size} bytes, over the {MAX_ARTIFACT_BYTES} byte ceiling",
            size_bytes=size,
        )
        return "unreadable"
    return None


def _judge_absence(
    stored: JudgeIdentity | None, identity: JudgeIdentity
) -> str | None:
    """Why the artifact is not about this run's Judge, or None when it is.

    A NULL STORED IDENTITY IS ITS OWN ANSWER, AND IT IS CHECKED FIRST. Every
    artifact this harness writes today carries null there, because the judge it
    calls cannot be named at the grain `JudgeIdentity` keys on (#58). Reading
    that through the mismatch branch reported "measured on another Judge" over a
    file that measured nobody, and logged three None fields beside three real
    ones. The two send an owner to different work. `artifact_names_no_judge`
    asks for a run against a nameable Judge, `identity_mismatch` asks what moved.
    """
    if stored is None:
        log.warning(
            "calibration_artifact_names_no_judge",
            run_model=identity.model,
            run_reasoning_effort=identity.reasoning_effort,
            run_prompt_version=identity.prompt_version,
        )
        return "artifact_names_no_judge"

    if stored != identity:
        log.warning(
            "calibration_identity_mismatch",
            run_model=identity.model,
            run_reasoning_effort=identity.reasoning_effort,
            run_prompt_version=identity.prompt_version,
            artifact_model=stored.model,
            artifact_reasoning_effort=stored.reasoning_effort,
            artifact_prompt_version=stored.prompt_version,
        )
        return "identity_mismatch"
    return None


#: What the deploy summary carries out of a record, and nothing else. The three
#: verdict parts, `difference_interval`, `attempted`, `valid`, `written_at` and
#: `artifact_version` stay off it. The orchestrator narrates the status and the
#: reason, `labels_made_at` is the date a reader of a deploy report can act on, and
#: ticket 17's refusal reads the record itself rather than this dict.
#: `tests/unit/test_calibration_service.py` pins the key set.
SUMMARY_KEYS = (
    "status",
    "reason",
    "judge_identity",
    "judge_interval",
    "ceiling_interval",
    "kappa",
    "matthews",
    "scored_pairs",
    "pairs",
    "labels_made_at",
    "harness_version",
)


def summary_of(status: CalibrationStatus) -> dict:
    """Eleven keys off the record, for the deploy summary (ticket #53, slice 3).

    SELECTED OFF `payload`, NEVER RE-DERIVED. `payload` already spells every
    field the way the artifact holds it, and already turns the identity and the
    intervals into JSON. Reading the fields a second time here would put a
    second spelling of one record beside the first, free to disagree, and the
    one that disagreed would be whichever the deploy summary read.

    `calibrated` is deliberately absent. The record derives that property from
    `status`, and a consumer reads `status`, so the summary carries no second
    answer to one question.

    Args:
        status: the record `load_calibration_status` returned.

    Returns:
        {"status", "reason", "judge_identity", "judge_interval",
         "ceiling_interval", "kappa", "matthews", "scored_pairs", "pairs",
         "labels_made_at", "harness_version"}, where `judge_identity` is
        {"model", "reasoning_effort", "prompt_version"} or None and each
        interval is {"low", "high", "point", "usable"} or None.
    """
    payload = status.payload
    return {key: payload[key] for key in SUMMARY_KEYS}
