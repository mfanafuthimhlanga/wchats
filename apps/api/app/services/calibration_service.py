"""Read the calibration artifact, and answer for a Judge the run actually used (ticket #53, slice 1).

WHY THIS FUNCTION NEVER RAISES
    Its caller is a deploy summary, and every way of reading a file off disk fails
    for reasons that have nothing to do with whether a Judge is calibrated. A
    missing artifact, a truncated write, a half-written JSON object and a shape
    this build refuses are four different accidents, and none of them is evidence
    about the Judge. Each returns `not_calibrated_yet` carrying the reason, which
    is the honest reading: nobody measured this Judge here. An exception would
    take a deploy summary down over a file that was never written, and the
    fail-closed answer already covers the case an exception would be protecting.

WHY THE IDENTITY IS CHECKED BEFORE THE FILE IS OPENED
    A run with no single Judge identity cannot be compared against any artifact,
    whatever the file says, so the question is settled before any I/O. Reporting
    `no_artifact` there would send an owner to write an artifact that still would
    not match.

WHY A MISMATCH IS AN ABSENCE AND NOT A FAILURE
    An artifact measured against a different model, effort or prompt says nothing
    about this run's Judge. `.dev/reference/260818-llm-eval-fundamentals.md`
    section 9 (lines 119-148) is the reason the key is three fields wide: a judge
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
    is the third field of the key: two identities differing on effort alone
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
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # The state a container is in: `.dockerignore` excludes `tests/`. Correct
        # until an artifact ships somewhere a container can read.
        return CalibrationStatus.absent("no_artifact")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
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

    stored = record.judge_identity
    if stored != identity:
        log.warning(
            "calibration_identity_mismatch",
            run_model=identity.model,
            run_reasoning_effort=identity.reasoning_effort,
            run_prompt_version=identity.prompt_version,
            artifact_model=stored.model if stored else None,
            artifact_reasoning_effort=stored.reasoning_effort if stored else None,
            artifact_prompt_version=stored.prompt_version if stored else None,
        )
        return CalibrationStatus.absent("identity_mismatch")

    return record


#: What the deploy summary carries out of a record, and nothing else. The three
#: verdict parts, `difference_interval`, `attempted`, `valid`, `written_at` and
#: `artifact_version` stay off it. The orchestrator narrates the status and the
#: reason, `labelled_at` is the date a reader of a deploy report can act on, and
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
    "labelled_at",
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
         "labelled_at", "harness_version"}, where `judge_identity` is
        {"model", "reasoning_effort", "prompt_version"} or None and each
        interval is {"low", "high", "point", "usable"} or None.
    """
    payload = status.payload
    return {key: payload[key] for key in SUMMARY_KEYS}
