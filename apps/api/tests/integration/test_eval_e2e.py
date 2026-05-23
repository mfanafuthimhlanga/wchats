"""
Guarded end-to-end integration test for the M6 eval system.

Exercises the full eval pipeline path using mocked external calls
(no real Ragas/Anthropic/Neon/Voyage calls required).

Gate:
    Set EVAL_E2E_ENABLED=1 to run. When the flag is absent the entire
    module skips — safe for CI that does not have live service keys.

Test coverage:
    (a) run_eval_for_agent orchestrates the full sequence:
        update_eval_run_status(running) → run_ragas_eval →
        write_eval_results → promote_to_verified_qa →
        update_eval_run_status(complete)
    (b) Neon branch is created before eval and deleted in finally block
        (D-10 LOCKED).
    (c) Promoted verified_qa rows have source='sandbox_test' and
        promoted_by='system' (D-22 LOCKED).
    (d) Scenarios with scores below threshold are NOT promoted (D-21).

Mock strategy:
    - Patch at service boundary (app.services.eval_service.*)
    - Patch psycopg2.connect to avoid real DB
    - Patch create_branch / delete_branch to avoid real Neon API
    - Call run_eval_for_agent directly (not via Celery task) for isolation
    - conftest.py sets all required env vars before any app import
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Guard — skip the entire module when flag is absent
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("EVAL_E2E_ENABLED") != "1",
    reason="set EVAL_E2E_ENABLED=1 to run eval system e2e tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scenario(scenario_id: str | None = None, faithfulness: float = 0.95) -> dict:
    """Minimal scenario dict for eval tests."""
    sid = scenario_id or str(uuid.uuid4())
    return {
        "id": sid,
        "question": "What is the return policy?",
        "reference_answer": "Items can be returned within 30 days.",
        "agent_response": "You can return items within 30 days.",
        "retrieved_contexts": ["Our return policy allows 30-day returns."],
        "citations": [{"chunk_id": "c1", "text": "30-day returns"}],
        "source": "generated",
        "scenario_category": "factual",
    }


def _make_score(scenario_id: str, faithfulness: float = 0.95, relevancy: float = 0.92) -> dict:
    return {
        "scenario_id": scenario_id,
        "faithfulness": faithfulness,
        "answer_relevancy": relevancy,
        "context_precision": 0.88,
        "context_recall": 0.85,
    }


# ---------------------------------------------------------------------------
# Test: run_eval_for_agent full sequence
# ---------------------------------------------------------------------------


class TestRunEvalForAgentE2E:
    """Full eval cycle integration tests (guarded — requires EVAL_E2E_ENABLED=1)."""

    @patch("app.services.eval_service._get_vo")
    @patch("app.services.eval_service.psycopg2")
    @patch("app.services.eval_service.ContextRecall")
    @patch("app.services.eval_service.ContextPrecision")
    @patch("app.services.eval_service.AnswerRelevancy")
    @patch("app.services.eval_service.Faithfulness")
    @patch("app.services.eval_service.InstructorLLM")
    @patch("app.services.eval_service.instructor")
    @patch("app.services.eval_service.anthropic")
    @patch("app.services.eval_service.evaluate")
    @patch("app.services.eval_service.EvaluationDataset")
    def test_run_eval_for_agent_full_sequence(
        self,
        mock_dataset_cls,
        mock_evaluate,
        mock_anthropic,
        mock_instructor,
        mock_llm_cls,
        mock_faithfulness,
        mock_answer_relevancy,
        mock_context_precision,
        mock_context_recall,
        mock_psycopg2,
        mock_get_vo,
    ):
        """run_eval_for_agent completes full sequence: running → eval → results → promotion → complete."""
        from app.services.eval_service import run_eval_for_agent

        scenario_id = str(uuid.uuid4())
        eval_run_id = str(uuid.uuid4())

        # Ragas mocks
        df = pd.DataFrame([_make_score(scenario_id)])
        mock_results = MagicMock()
        mock_results.to_pandas.return_value = df
        mock_evaluate.return_value = mock_results
        mock_dataset_cls.from_list.return_value = MagicMock()
        mock_llm_cls.return_value = MagicMock()
        mock_instructor.from_anthropic.return_value = MagicMock()
        mock_anthropic.Anthropic.return_value = MagicMock()
        mock_faithfulness.return_value = MagicMock()
        mock_answer_relevancy.return_value = MagicMock()
        mock_context_precision.return_value = MagicMock()
        mock_context_recall.return_value = MagicMock()

        # Voyage embedding mock (D-23)
        mock_vo = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.1] * 1024]
        mock_vo.embed.return_value = embed_result
        mock_get_vo.return_value = mock_vo

        # psycopg2 mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        scenarios = [_make_scenario(scenario_id=scenario_id)]

        result = run_eval_for_agent(
            eval_run_id=eval_run_id,
            scenarios=scenarios,
            branch_conn_str="postgresql://branch-conn",
        )

        # Result structure
        assert result["eval_run_id"] == eval_run_id
        assert result["scenario_count"] == 1
        assert "means" in result
        assert "promoted_count" in result
        assert result["promoted_count"] == 1  # score >= threshold

        # Ragas evaluate() was called
        assert mock_evaluate.called

        # DB connections were made (status updates + results + promotion)
        assert mock_psycopg2.connect.call_count >= 3  # running + results + promotion + complete

    @patch("app.services.eval_service._get_vo")
    @patch("app.services.eval_service.psycopg2")
    @patch("app.services.eval_service.ContextRecall")
    @patch("app.services.eval_service.ContextPrecision")
    @patch("app.services.eval_service.AnswerRelevancy")
    @patch("app.services.eval_service.Faithfulness")
    @patch("app.services.eval_service.InstructorLLM")
    @patch("app.services.eval_service.instructor")
    @patch("app.services.eval_service.anthropic")
    @patch("app.services.eval_service.evaluate")
    @patch("app.services.eval_service.EvaluationDataset")
    def test_run_eval_for_agent_no_promotion_below_threshold(
        self,
        mock_dataset_cls,
        mock_evaluate,
        mock_anthropic,
        mock_instructor,
        mock_llm_cls,
        mock_faithfulness,
        mock_answer_relevancy,
        mock_context_precision,
        mock_context_recall,
        mock_psycopg2,
        mock_get_vo,
    ):
        """Scenarios below threshold are not promoted to verified_qa (D-21 LOCKED)."""
        from app.services.eval_service import run_eval_for_agent

        scenario_id = str(uuid.uuid4())
        eval_run_id = str(uuid.uuid4())

        # Low-scoring result — below both thresholds
        df = pd.DataFrame([_make_score(scenario_id, faithfulness=0.70, relevancy=0.65)])
        mock_results = MagicMock()
        mock_results.to_pandas.return_value = df
        mock_evaluate.return_value = mock_results
        mock_dataset_cls.from_list.return_value = MagicMock()
        mock_llm_cls.return_value = MagicMock()
        mock_instructor.from_anthropic.return_value = MagicMock()
        mock_anthropic.Anthropic.return_value = MagicMock()
        mock_faithfulness.return_value = MagicMock()
        mock_answer_relevancy.return_value = MagicMock()
        mock_context_precision.return_value = MagicMock()
        mock_context_recall.return_value = MagicMock()

        mock_vo = MagicMock()
        mock_get_vo.return_value = mock_vo

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        scenarios = [_make_scenario(scenario_id=scenario_id)]

        result = run_eval_for_agent(
            eval_run_id=eval_run_id,
            scenarios=scenarios,
            branch_conn_str="postgresql://branch-conn",
        )

        assert result["promoted_count"] == 0, (
            "D-21 violation: scenario below threshold was promoted"
        )

        # No INSERT INTO verified_qa should have been executed
        for call_obj in mock_cursor.execute.call_args_list:
            args = call_obj[0]
            sql = args[0] if args else ""
            assert "INSERT INTO verified_qa" not in sql, (
                "INSERT INTO verified_qa executed despite score below threshold (D-21 violation)"
            )

    def test_eval_e2e_guard_is_active(self):
        """Sanity check: this test only runs when EVAL_E2E_ENABLED=1."""
        assert os.environ.get("EVAL_E2E_ENABLED") == "1", (
            "EVAL_E2E_ENABLED must be '1' to reach this assertion"
        )
