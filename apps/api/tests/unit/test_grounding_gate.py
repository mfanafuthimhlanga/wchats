"""The seams the grounding rule crosses on its way to the deploy gate (ADR 0015).

The rule itself is `test_grounding.py`. This is the wiring: the faithfulness
row names the rule, a run scored by the rule reads as calibrated without an
artifact, and a run that still names a model on a gated dimension does not.
"""

from __future__ import annotations

from app.domain.calibration_status import STATUS_RULE, CalibrationStatus
from app.domain.grounding import GROUNDING_IDENTITY
from app.domain.judge_identity import RULE_MODEL_PREFIX, JudgeIdentity
from app.services import eval_service
from app.services.calibration_service import load_calibration_status

LUNA = JudgeIdentity(model="gpt-5.6-luna", reasoning_effort="none", prompt_version="ragas-0.4.3")


class TestTheRowNamesTheRule:
    def test_faithfulness_answers_the_rule_identity_whatever_the_routing_table_says(self):
        assert eval_service.judge_identity_for("faithfulness") is GROUNDING_IDENTITY
        assert GROUNDING_IDENTITY.model.startswith(RULE_MODEL_PREFIX)

    def test_the_gated_set_is_faithfulness_alone_and_the_scored_set_matches_it(self):
        assert eval_service.GATED_METRIC_KEYS == ("faithfulness",)
        assert eval_service.SCORED_METRIC_KEYS == ("faithfulness",)
        assert eval_service.REJUDGE_METRIC_KEYS == ("faithfulness",)

    def test_a_run_of_grounded_rows_names_the_rule_on_its_gated_dimension(self):
        rows = [{"scenario_id": "s1", "faithfulness": 1.0}, {"scenario_id": "s2", "faithfulness": 0.5}]
        records = eval_service.build_judge_records(rows)
        identities = eval_service.run_judge_identities(records)
        assert identities == {"faithfulness": GROUNDING_IDENTITY}


class TestARuleIsCalibratedByItsTest:
    def test_a_rule_identity_on_every_gated_dimension_is_calibrated_with_no_artifact(self, tmp_path):
        status = load_calibration_status(tmp_path / "missing.json", {"faithfulness": GROUNDING_IDENTITY})
        assert status.calibrated is True
        assert status.status == STATUS_RULE
        assert "test_grounding.py" in (status.reason or "")

    def test_a_model_on_any_gated_dimension_still_needs_the_artifact(self, tmp_path):
        status = load_calibration_status(
            tmp_path / "missing.json", {"faithfulness": GROUNDING_IDENTITY, "answer_relevancy": LUNA}
        )
        assert status.calibrated is False
        assert status.status != STATUS_RULE

    def test_no_identity_at_all_is_still_an_absence(self, tmp_path):
        assert load_calibration_status(tmp_path / "missing.json", None).calibrated is False

    def test_the_rule_status_round_trips_through_its_payload(self):
        status = CalibrationStatus.rule("pinned")
        assert CalibrationStatus.from_payload(status.payload).calibrated is True
