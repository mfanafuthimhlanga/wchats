"""
Guarded end-to-end integration test for the M5 validation chain.

Exercises the full dispatch → verdict → insert/flag path using mocked judge
calls (no real Anthropic or Langfuse calls required).

Gate:
    Set VALIDATION_E2E_ENABLED=1 to run. When the flag is absent the entire
    module skips — safe for CI that does not have Anthropic/Langfuse keys.

Assertions:
    (a) All three judge functions (call_gatekeeper, call_auditor, call_strategist)
        are called exactly once per turn.
    (b) On a grounded + high-confidence Auditor verdict, _insert_verified_qa_candidate
        is invoked once with the correct auditor_confidence.
    (c) On three consecutive ungrounded Auditor verdicts (mocked count >= 3),
        the UPDATE sets strategy_resynthesis_flagged = TRUE.

Mock strategy:
    - Patch at module boundary (app.worker.tasks.runtime.validators.*)
    - Call task .run() directly (bypasses Celery broker — no worker process required)
    - _make_agent / _make_db_ctx helpers imported from test_validators.py
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard — skip the entire module when flag is absent
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("VALIDATION_E2E_ENABLED") != "1",
    reason="set VALIDATION_E2E_ENABLED=1 to run validation chain e2e tests",
)


# ---------------------------------------------------------------------------
# Helpers (mirrors test_validators.py — inlined here so the integration test
# is self-contained and does not create a cross-directory import dependency)
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
    agent.strategy_resynthesis_flagged = False  # M5 field — must be in mock
    return agent


def _make_db_ctx(db: MagicMock) -> MagicMock:
    """Wrap a mock DB session in a context-manager wrapper."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Test (a) + (b): grounded path — all three judges called, QA candidate inserted
# ---------------------------------------------------------------------------


def test_validation_chain_grounded_all_judges_called_and_qa_inserted():
    """All three judge functions are called; on grounded+high-confidence verdict
    _insert_verified_qa_candidate is called once with auditor_confidence=0.95.
    """
    from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist
    from app.services.validation_service import (
        GatekeeperVerdict,
        AuditorVerdict,
        StrategistVerdict,
        CitationSpan,
    )

    agent_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    conv_id = str(uuid.uuid4())

    # Shared mock DB: idempotency fetchone() returns None (not yet processed)
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.execute.return_value.scalar.return_value = 0
    mock_db.get.return_value = _make_agent(agent_id)

    gk_verdict = GatekeeperVerdict(verdict="pass", confidence=0.91, reason="question addressed")
    au_verdict = AuditorVerdict(
        verdict="grounded",
        confidence=0.95,
        citation_spans=[
            CitationSpan(
                claim="full refund within 24 hours",
                source_chunk="our policy allows returns within 30 days",
                supported=False,
            )
        ],
        reason="claim partially contradicted by source",
    )
    st_verdict = StrategistVerdict(
        verdict="ship",
        confidence=0.88,
        issues=[],
        reason="response is on-brand",
    )

    with patch(
        "app.worker.tasks.runtime.validators.get_sync_db",
        return_value=_make_db_ctx(mock_db),
    ), patch(
        "app.worker.tasks.runtime.validators.call_gatekeeper",
        return_value=gk_verdict,
    ) as mock_gk, patch(
        "app.worker.tasks.runtime.validators._log_verdict",
    ), patch(
        "app.worker.tasks.runtime.validators.emit",
    ) as mock_emit_gk, patch(
        "app.worker.tasks.runtime.validators._redis",
        new_callable=MagicMock,
    ):
        result_gk = run_gatekeeper.run(
            agent_id=agent_id,
            job_id=job_id,
            response_text="Our return policy is 30 days.",
            question="Do you guarantee a full refund within 24 hours?",
        )

    # (a) Gatekeeper judge was called
    mock_gk.assert_called_once()
    assert result_gk == {}

    # Confirm gatekeeper.complete was emitted
    mock_emit_gk.assert_called_once()
    assert mock_emit_gk.call_args.args[1] == "gatekeeper.complete"

    # Reset mock DB for auditor run
    mock_db2 = MagicMock()
    mock_db2.execute.return_value.fetchone.return_value = None
    mock_db2.execute.return_value.scalar.return_value = 0
    mock_db2.get.return_value = _make_agent(agent_id)

    with patch(
        "app.worker.tasks.runtime.validators.get_sync_db",
        return_value=_make_db_ctx(mock_db2),
    ), patch(
        "app.worker.tasks.runtime.validators.call_auditor",
        return_value=au_verdict,
    ) as mock_au, patch(
        "app.worker.tasks.runtime.validators._log_verdict",
    ), patch(
        "app.worker.tasks.runtime.validators.emit",
    ) as mock_emit_au, patch(
        "app.worker.tasks.runtime.validators.fernet_decrypt",
        return_value="postgresql://tenant/testdb",
    ), patch(
        "app.worker.tasks.runtime.validators._insert_verified_qa_candidate",
    ) as mock_insert, patch(
        "app.worker.tasks.runtime.validators._redis",
        new_callable=MagicMock,
    ):
        result_au = run_auditor.run(
            agent_id=agent_id,
            job_id=job_id,
            response_text="Our return policy is 30 days.",
            question="Do you guarantee a full refund within 24 hours?",
            retrieved_context_json='[{"content": "our policy allows returns within 30 days"}]',
            conversation_id=conv_id,
        )

    # (a) Auditor judge was called
    mock_au.assert_called_once()
    assert result_au == {}

    # Confirm auditor.complete was emitted
    mock_emit_au.assert_called_once()
    assert mock_emit_au.call_args.args[1] == "auditor.complete"

    # (b) _insert_verified_qa_candidate called once with confidence=0.95
    mock_insert.assert_called_once()
    call_kwargs = mock_insert.call_args
    confidence_arg = call_kwargs.kwargs.get("auditor_confidence") or call_kwargs.args[5]
    assert confidence_arg == 0.95, f"Expected auditor_confidence=0.95, got {confidence_arg}"

    # Reset mock DB for strategist run
    mock_db3 = MagicMock()
    mock_db3.execute.return_value.fetchone.return_value = None
    mock_db3.get.return_value = _make_agent(agent_id)

    with patch(
        "app.worker.tasks.runtime.validators.get_sync_db",
        return_value=_make_db_ctx(mock_db3),
    ), patch(
        "app.worker.tasks.runtime.validators.call_strategist",
        return_value=st_verdict,
    ) as mock_st, patch(
        "app.worker.tasks.runtime.validators._log_verdict",
    ), patch(
        "app.worker.tasks.runtime.validators.emit",
    ) as mock_emit_st, patch(
        "app.worker.tasks.runtime.validators._redis",
        new_callable=MagicMock,
    ):
        result_st = run_strategist.run(
            agent_id=agent_id,
            job_id=job_id,
            response_text="Our return policy is 30 days.",
            question="Do you guarantee a full refund within 24 hours?",
        )

    # (a) Strategist judge was called
    mock_st.assert_called_once()
    assert result_st == {}

    # Confirm strategist.complete was emitted
    mock_emit_st.assert_called_once()
    assert mock_emit_st.call_args.args[1] == "strategist.complete"


# ---------------------------------------------------------------------------
# Test (c): ungrounded path — 3 verdicts trigger strategy_resynthesis_flagged
# ---------------------------------------------------------------------------


def test_validation_chain_ungrounded_sets_resynthesis_flag():
    """On 3 consecutive ungrounded verdicts (mocked count >= 3),
    run_auditor sets strategy_resynthesis_flagged = TRUE via UPDATE.
    """
    from app.worker.tasks.runtime.validators import run_auditor
    from app.services.validation_service import AuditorVerdict

    agent_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    conv_id = str(uuid.uuid4())

    mock_db = MagicMock()
    # Idempotency: no existing auditor.complete row
    mock_db.execute.return_value.fetchone.return_value = None
    # COUNT query returns 3 — triggers the resynthesis flag
    mock_db.execute.return_value.scalar.return_value = 3
    mock_db.get.return_value = _make_agent(agent_id)

    ungrounded_verdict = AuditorVerdict(
        verdict="ungrounded",
        confidence=0.87,
        citation_spans=[],
        reason="no source chunk supports the '24-hour refund' claim",
    )

    with patch(
        "app.worker.tasks.runtime.validators.get_sync_db",
        return_value=_make_db_ctx(mock_db),
    ), patch(
        "app.worker.tasks.runtime.validators.call_auditor",
        return_value=ungrounded_verdict,
    ), patch(
        "app.worker.tasks.runtime.validators._log_verdict",
    ), patch(
        "app.worker.tasks.runtime.validators.emit",
    ), patch(
        "app.worker.tasks.runtime.validators._redis",
        new_callable=MagicMock,
    ):
        result = run_auditor.run(
            agent_id=agent_id,
            job_id=job_id,
            response_text="I am not sure about that policy.",
            question="Do you guarantee a full refund within 24 hours?",
            retrieved_context_json="[]",
            conversation_id=conv_id,
        )

    assert result == {}

    # (c) Verify UPDATE strategy_resynthesis_flagged was called
    executed_sqls = [str(call_obj.args[0]) for call_obj in mock_db.execute.call_args_list]
    flag_update_called = any("strategy_resynthesis_flagged" in sql for sql in executed_sqls)
    assert flag_update_called, (
        f"Expected UPDATE strategy_resynthesis_flagged in executed SQL. Got: {executed_sqls}"
    )

    # Verify commit was called after the UPDATE
    mock_db.commit.assert_called()
