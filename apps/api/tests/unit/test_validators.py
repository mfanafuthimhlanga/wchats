"""
Wave 0 test scaffold for the M5 validation chain (Plan 05-01).

All tests are marked @pytest.mark.xfail(strict=False) because the implementation
modules (app.services.validation_service, app.worker.tasks.runtime.validators)
do not yet exist — they are created in Plans 05-02 and 05-03 respectively.

Test coverage intent (per VAL-01 through VAL-06):
  test_gatekeeper_verdict        — VAL-01: GatekeeperVerdict Pydantic model validates + normalises
  test_run_gatekeeper_task       — VAL-02: run_gatekeeper Celery task: idempotency + emit
  test_auditor_verdict           — VAL-03: AuditorVerdict Pydantic model validates + normalises
  test_auditor_inserts_candidate — VAL-04: run_auditor inserts into verified_qa_candidates when confident
  test_strategist_verdict        — VAL-05: StrategistVerdict Pydantic model validates + normalises
  test_langfuse_logged           — VAL-05: _log_verdict() calls start_as_current_generation
  test_resynthesis_flag          — VAL-06: run_auditor sets strategy_resynthesis_flagged on 3+ ungrounded

Mock strategy mirrors test_agent_task.py:
  - patch at module boundary (app.worker.tasks.runtime.validators.*)
  - _make_agent() includes strategy_resynthesis_flagged=False (RESEARCH Pitfall 4)
  - symbol imports are inside test bodies so collection never fails on missing modules
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(agent_id: str | None = None) -> MagicMock:
    """Minimal agent mock with all fields used by validation tasks."""
    agent = MagicMock()
    agent.id = uuid.UUID(agent_id) if agent_id else uuid.uuid4()
    agent.name = "Test Agent"
    agent.soul_role = "customer service representative"
    agent.soul_voice = "helpful"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"
    agent.strategy_resynthesis_flagged = False  # M5: new field — must be in mock (RESEARCH Pitfall 4)
    return agent


def _make_db_ctx(db: MagicMock) -> MagicMock:
    """Wrap a mock DB session in a context-manager wrapper."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# VAL-01: Gatekeeper verdict model
# ---------------------------------------------------------------------------

def test_gatekeeper_verdict():
    """GatekeeperVerdict Pydantic model validates and normalises verdict field."""
    from app.services.validation_service import GatekeeperVerdict
    from pydantic import ValidationError

    # field_validator lowercases verdict — "Pass" → "pass"
    v = GatekeeperVerdict.model_validate({"verdict": "Pass", "confidence": 0.92, "reason": "ok"})
    assert v.verdict == "pass"
    assert v.confidence == 0.92
    assert isinstance(v.reason, str)

    # Uppercase also normalizes
    v2 = GatekeeperVerdict.model_validate({"verdict": "FAIL", "confidence": 0.1, "reason": "x"})
    assert v2.verdict == "fail"

    # needs_clarification variant
    v3 = GatekeeperVerdict.model_validate({"verdict": "needs_clarification", "confidence": 0.5, "reason": "ambiguous"})
    assert v3.verdict == "needs_clarification"

    # Invalid verdict raises ValidationError
    with pytest.raises(ValidationError):
        GatekeeperVerdict.model_validate({"verdict": "maybe", "confidence": 0.5, "reason": "x"})


# ---------------------------------------------------------------------------
# VAL-02: run_gatekeeper Celery task
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason="implemented in 05-02", strict=False)
def test_run_gatekeeper_task():
    """run_gatekeeper returns idempotency sentinel when job_events row already exists."""
    from app.worker.tasks.runtime.validators import run_gatekeeper  # noqa: F401

    # Eventual assertion: patch get_sync_db to return existing job_events row,
    # call run_gatekeeper.run(...), assert result == {"status": "already_complete"}
    assert False, "stub"


# ---------------------------------------------------------------------------
# VAL-03: Auditor verdict model
# ---------------------------------------------------------------------------

def test_auditor_verdict():
    """AuditorVerdict Pydantic model validates and normalises verdict field."""
    from app.services.validation_service import AuditorVerdict, CitationSpan

    # field_validator lowercases verdict — "Grounded" → "grounded"
    v = AuditorVerdict.model_validate({
        "verdict": "Grounded",
        "confidence": 0.95,
        "citation_spans": [],
        "reason": "ok",
    })
    assert v.verdict == "grounded"
    assert v.confidence == 0.95
    assert v.citation_spans == []

    # Citation spans are CitationSpan instances
    v2 = AuditorVerdict.model_validate({
        "verdict": "partial",
        "confidence": 0.6,
        "citation_spans": [
            {"claim": "price is $10", "source_chunk": "product costs $10", "supported": True},
            {"claim": "ships in 2 days", "source_chunk": "no shipping info found", "supported": False},
        ],
        "reason": "some claims supported",
    })
    assert len(v2.citation_spans) == 2
    assert isinstance(v2.citation_spans[0], CitationSpan)
    assert v2.citation_spans[0].supported is True
    assert v2.citation_spans[1].supported is False

    # UNGROUNDED uppercase normalizes
    v3 = AuditorVerdict.model_validate({
        "verdict": "UNGROUNDED",
        "confidence": 0.9,
        "citation_spans": [],
        "reason": "no support",
    })
    assert v3.verdict == "ungrounded"


# ---------------------------------------------------------------------------
# VAL-04: Auditor inserts verified QA candidate
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason="implemented in 05-02/05-03", strict=False)
def test_auditor_inserts_candidate():
    """run_auditor inserts into verified_qa_candidates when auditor_confidence >= threshold."""
    from app.worker.tasks.runtime.validators import run_auditor  # noqa: F401

    # Eventual assertion: patch get_sync_db + fernet_decrypt + call_auditor to return
    # AuditorVerdict(verdict="grounded", confidence=0.96, ...), then assert psycopg2.connect
    # was called and INSERT INTO verified_qa_candidates was executed
    assert False, "stub"


# ---------------------------------------------------------------------------
# VAL-05: Strategist verdict model
# ---------------------------------------------------------------------------

def test_strategist_verdict():
    """StrategistVerdict Pydantic model validates and normalises verdict field."""
    from app.services.validation_service import StrategistVerdict
    from pydantic import ValidationError

    # field_validator lowercases verdict — "SHIP" → "ship"
    v = StrategistVerdict.model_validate({
        "verdict": "SHIP",
        "confidence": 0.88,
        "issues": [],
        "reason": "ok",
    })
    assert v.verdict == "ship"
    assert v.confidence == 0.88
    assert v.issues == []

    # Revise with issues list
    v2 = StrategistVerdict.model_validate({
        "verdict": "Revise",
        "confidence": 0.7,
        "issues": ["too formal", "missing greeting"],
        "reason": "off-brand tone",
    })
    assert v2.verdict == "revise"
    assert len(v2.issues) == 2

    # Escalate
    v3 = StrategistVerdict.model_validate({
        "verdict": "escalate",
        "confidence": 0.95,
        "issues": ["legal question"],
        "reason": "requires human review",
    })
    assert v3.verdict == "escalate"

    # Invalid verdict raises ValidationError
    with pytest.raises(ValidationError):
        StrategistVerdict.model_validate({
            "verdict": "unknown",
            "confidence": 0.5,
            "issues": [],
            "reason": "x",
        })


# ---------------------------------------------------------------------------
# VAL-05: Langfuse logging
# ---------------------------------------------------------------------------

def test_langfuse_logged(monkeypatch):
    """_log_verdict() calls start_as_current_generation and flush when _langfuse is set."""
    import app.services.validation_service as vs
    from app.services.validation_service import _log_verdict

    # Set up a MagicMock for _langfuse at module level
    mock_lf = MagicMock()
    # start_as_current_generation must work as a context manager
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_cm)
    mock_cm.__exit__ = MagicMock(return_value=False)
    mock_lf.start_as_current_generation.return_value = mock_cm

    monkeypatch.setattr(vs, "_langfuse", mock_lf)

    _log_verdict(
        judge_name="gatekeeper",
        agent_id="test-agent-id",
        job_id="test-job-id",
        input_payload={"question": "what is the price?", "response_length": 50},
        verdict_dict={"verdict": "pass", "confidence": 0.92, "reason": "ok"},
    )

    # start_as_current_generation must have been called
    mock_lf.start_as_current_generation.assert_called_once()
    call_kwargs = mock_lf.start_as_current_generation.call_args
    assert call_kwargs.kwargs.get("name") == "gatekeeper-judge" or (
        len(call_kwargs.args) > 0 and call_kwargs.args[0] == "gatekeeper-judge"
    )

    # flush must have been called
    mock_lf.flush.assert_called_once()

    # create_score must have been called with CATEGORICAL data_type
    mock_lf.create_score.assert_called_once()
    score_kwargs = mock_lf.create_score.call_args.kwargs
    assert score_kwargs.get("data_type") == "CATEGORICAL"
    assert score_kwargs.get("trace_id") == "test-job-id"


# ---------------------------------------------------------------------------
# VAL-06: strategy_resynthesis_flagged set on 3+ ungrounded verdicts
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason="implemented in 05-03", strict=False)
def test_resynthesis_flag():
    """run_auditor sets strategy_resynthesis_flagged=TRUE after 3 ungrounded verdicts in 24h."""
    from app.worker.tasks.runtime.validators import run_auditor  # noqa: F401

    # Eventual assertion: patch job_events COUNT query to return 3, verify
    # UPDATE agents SET strategy_resynthesis_flagged = TRUE was executed
    assert False, "stub"
