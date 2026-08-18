"""The calibration corpus refuses itself when it is contaminated.

BACKLOG 7.31. The E2E-6 corpus was accepted as clean by checks for empties,
length and provider-error text, and carried four PII deflections and twenty
unnamed tool calls. Both are well formed. Both were found by a person reading
the files a day later, after the capture's live agent turns had been spent.

The load-bearing test in this module is `test_both_defect_classes_are_reported
_in_one_run`. A validator that reveals one finding per run schedules the
re-captures it exists to prevent, and the first version of this validator did
exactly that: the blind check keyed on `tool_name == "retrieve"`, which no
unnamed call matches, so the unnamed-tool defect hid the missing-chunk defect
behind it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.utils.pii_firewall import PII_DEFLECTION
from tests.evals import validate_corpus as vc

GOOD_ANSWER = (
    "Our return policy gives you 14 days from delivery to return an unopened bag "
    "for a full refund, and we cover the return shipping when the fault is ours."
)
RETRIEVE_CALL = {
    "tool_name": "retrieve",
    "input": {"query": "return policy"},
    "result": {"chunks": ["Section 5.2: unopened bags may be returned within 14 days."]},
}


def _corpus(tmp_path: pathlib.Path, records: dict[str, dict]) -> pathlib.Path:
    directory = tmp_path / "responses"
    directory.mkdir()
    for sid, record in records.items():
        (directory / f"{sid}.json").write_text(json.dumps(record), encoding="utf-8")
    return directory


def _row(text=GOOD_ANSWER, calls=None):
    return {
        "scenario_id": "S-101",
        "response_text": text,
        "tool_calls_log": [dict(RETRIEVE_CALL)] if calls is None else calls,
    }


def test_a_clean_corpus_is_clean(tmp_path):
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(), "S-102": _row()}))
    assert result["fatal"] == {}
    assert result["blind"] == {}
    assert vc.report(result) == vc.EXIT_CLEAN


def test_a_deflection_is_fatal(tmp_path):
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(text=PII_DEFLECTION)}))
    assert "deflection" in " ".join(result["fatal"]["S-101"])
    assert vc.report(result) == vc.EXIT_FATAL


def test_an_unnamed_tool_call_is_fatal(tmp_path):
    calls = [{"tool_name": "", "input": {"query": "q"}, "result": {"chunks": ["x"]}}]
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(calls=calls)}))
    assert "no tool_name" in " ".join(result["fatal"]["S-101"])
    assert vc.report(result) == vc.EXIT_FATAL


def test_a_retrieve_without_a_result_is_blind_not_fatal(tmp_path):
    calls = [{"tool_name": "retrieve", "input": {"query": "q"}, "result": {}}]
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(calls=calls)}))
    assert result["fatal"] == {}, "the answer is real and a human can score it"
    assert "grounding_fidelity cannot pass" in " ".join(result["blind"]["S-101"])
    assert vc.report(result) == vc.EXIT_BLIND


def test_both_defect_classes_are_reported_in_one_run(tmp_path):
    """The anti-rerun property, and the one that already regressed once."""
    calls = [{"tool_name": "", "input": {"query": "q"}, "result": {}}]
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(calls=calls)}))
    assert "no tool_name" in " ".join(result["fatal"]["S-101"])
    assert "grounding_fidelity cannot pass" in " ".join(result["blind"]["S-101"]), (
        "an unnamed retrieve call must still be recognised as a retrieve, or the "
        "unnamed-tool defect hides the missing-chunk defect until the next capture"
    )


def test_a_named_non_retrieve_tool_is_not_expected_to_carry_chunks(tmp_path):
    calls = [{"tool_name": "escalate_to_human", "input": {"reason": "r"}, "result": {}}]
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(calls=calls)}))
    assert result["fatal"] == {}
    assert result["blind"] == {}, "only retrieve owes the grounding judge a chunk"


def test_provider_error_text_is_fatal(tmp_path):
    text = "I could not answer: the upstream error was a rate limit. Please try again later."
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(text=text)}))
    assert "provider-error" in " ".join(result["fatal"]["S-101"])


def test_a_short_or_empty_answer_is_fatal(tmp_path):
    result = vc.validate(_corpus(tmp_path, {"S-101": _row(text="Yes."), "S-102": _row(text="")}))
    assert "under the" in " ".join(result["fatal"]["S-101"])
    assert "empty" in " ".join(result["fatal"]["S-102"])


def test_a_missing_directory_is_a_setup_error_not_a_pass(tmp_path):
    result = vc.validate(tmp_path / "never-captured")
    assert result["setup_error"]
    assert vc.report(result) == vc.EXIT_SETUP


def test_the_four_outcomes_have_four_distinct_exit_codes():
    codes = [vc.EXIT_CLEAN, vc.EXIT_FATAL, vc.EXIT_SETUP, vc.EXIT_BLIND]
    assert len(set(codes)) == 4, "a caller keying on the exit code must be able to tell them apart"


@pytest.mark.parametrize("bad", ["{not json", ""])
def test_an_unreadable_file_is_fatal_not_skipped(tmp_path, bad):
    directory = tmp_path / "responses"
    directory.mkdir()
    (directory / "S-101.json").write_text(bad, encoding="utf-8")
    result = vc.validate(directory)
    assert "S-101" in result["fatal"]
