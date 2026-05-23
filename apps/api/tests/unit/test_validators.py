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

@pytest.mark.xfail(reason="implemented in 05-02", strict=False)
def test_gatekeeper_verdict():
    """GatekeeperVerdict Pydantic model validates and normalises verdict field."""
    from app.services.validation_service import GatekeeperVerdict  # noqa: F401

    # Eventual assertion: model_validate({"verdict":"Pass","confidence":0.92,"reason":"ok"})
    # returns GatekeeperVerdict with verdict=="pass" (field_validator lowercases)
    assert False, "stub"


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

@pytest.mark.xfail(reason="implemented in 05-02", strict=False)
def test_auditor_verdict():
    """AuditorVerdict Pydantic model validates and normalises verdict field."""
    from app.services.validation_service import AuditorVerdict  # noqa: F401

    # Eventual assertion: model_validate({"verdict":"Grounded","confidence":0.95,
    # "citation_spans":[],"reason":"ok"}) returns verdict=="grounded"
    assert False, "stub"


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

@pytest.mark.xfail(reason="implemented in 05-02", strict=False)
def test_strategist_verdict():
    """StrategistVerdict Pydantic model validates and normalises verdict field."""
    from app.services.validation_service import StrategistVerdict  # noqa: F401

    # Eventual assertion: model_validate({"verdict":"Ship","confidence":0.88,
    # "issues":[],"reason":"ok"}) returns verdict=="ship"
    assert False, "stub"


# ---------------------------------------------------------------------------
# VAL-05: Langfuse logging
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason="implemented in 05-02", strict=False)
def test_langfuse_logged():
    """_log_verdict() calls start_as_current_generation when _langfuse is set."""
    from app.services.validation_service import _log_verdict  # noqa: F401

    # Eventual assertion: monkeypatch app.services.validation_service._langfuse to MagicMock,
    # call _log_verdict("gatekeeper", ...), assert mock_lf.start_as_current_generation.called
    assert False, "stub"


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
