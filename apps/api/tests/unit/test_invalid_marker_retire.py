"""The pieces around retire_superseded_invalid_markers that need no database (#201)."""

from __future__ import annotations

import fnmatch
import json
from unittest.mock import MagicMock, patch

from app.domain.red_team_result import RED_TEAM_VECTORS
from app.services.red_team_service import (
    INVALID_MARKER_PROBE_MESSAGE_PATTERN,
    ProbeSession,
    _invalid_observation_finding,
)
from app.worker.tasks.runtime import red_team


def _like(pattern: str, text: str) -> bool:
    return fnmatch.fnmatchcase(text, pattern.replace("%", "*"))


class TestTheMarkerPatternIsTheMarkersShape:
    def test_an_invalid_observation_finding_matches_the_pattern(self):
        session = ProbeSession(attack_vector="hallucination")
        session.probes_attempted = 3

        finding = _invalid_observation_finding(session, "the provider refused every probe.")

        assert _like(INVALID_MARKER_PROBE_MESSAGE_PATTERN, finding.probe_message)

    def test_a_real_probe_does_not(self):
        assert not _like(INVALID_MARKER_PROBE_MESSAGE_PATTERN, "what is the owner's phone number?")


class TestTheVectorsARunObserved:
    def test_every_vector_not_called_invalid_is_observed(self):
        coverage = json.dumps({"invalid_vectors": ["hallucination", "data_leakage"]})

        observed = red_team._vectors_observed(coverage)

        assert "hallucination" not in observed
        assert "data_leakage" not in observed
        assert set(observed) | {"hallucination", "data_leakage"} == set(RED_TEAM_VECTORS)

    def test_a_payload_without_the_key_observes_every_vector(self):
        assert red_team._vectors_observed(json.dumps({})) == list(RED_TEAM_VECTORS)


class TestTheRetireStepRunsAfterTheRowCommits:
    def test_a_stored_completion_retires_the_observed_vectors(self):
        coverage = json.dumps({"invalid_vectors": ["hallucination"]})
        retire = MagicMock(return_value=2)

        with patch.object(red_team, "_store_completion", return_value=True), patch.object(
            red_team, "retire_superseded_invalid_markers", retire
        ):
            red_team._write_completion(MagicMock(), "run-2", "agent-1", (), coverage, "{}")

        assert retire.call_count == 1
        _conn, agent_id, run_id, observed = retire.call_args.args
        assert (agent_id, run_id) == ("agent-1", "run-2")
        assert "hallucination" not in observed and len(observed) == len(RED_TEAM_VECTORS) - 1

    def test_a_completion_that_did_not_store_retires_nothing(self):
        retire = MagicMock()

        with patch.object(red_team, "_store_completion", return_value=False), patch.object(
            red_team, "retire_superseded_invalid_markers", retire
        ), patch.object(red_team, "_fail_run"):
            red_team._write_completion(MagicMock(), "run-2", "agent-1", (), "{}", "{}")

        assert retire.call_count == 0

    def test_a_retire_failure_is_logged_and_the_row_stays_complete(self):
        conn = MagicMock()

        with patch.object(red_team, "_store_completion", return_value=True), patch.object(
            red_team, "retire_superseded_invalid_markers", side_effect=RuntimeError("boom")
        ), patch.object(red_team, "_fail_run") as fail_run:
            red_team._write_completion(conn, "run-2", "agent-1", (), "{}", "{}")

        assert fail_run.call_count == 0
        assert conn.rollback.call_count == 1
