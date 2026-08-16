"""
Unit tests for OPS-07: run_retrieval_faithfulness sampled Celery task.

Tests:
    1. Signature — task takes only (agent_id, job_id), no conn_str (CLAUDE.md rule 4).
    2. Idempotent — already-scored job_id returns without recompute.
    3. No retrieval_metrics row — returns early, no UPDATE attempted.
    4. Sampling gate — random < rate dispatches the compute path.
    5. Sampling gate — random >= rate AND not auditor-flagged skips the compute path.
    6. Auditor-flag override — random >= rate but auditor-flagged still computes.
    7. citation_coverage computed from citations/retrieve-call ratio; None when
       nothing was retrieved (honest-empty-state, never fabricated 0.0).
    8. Task-level tests stub _compute_ragas_faithfulness so the gating and
       write logic is tested without a judge call.
    9. The Ragas 0.4.x scoring path itself, against the REAL ragas package
       with a canned InstructorBaseRagasLLM in place of the network hop (7.18).

Patch targets are symbols imported into app.worker.tasks.runtime.retrieval_eval:
    - app.worker.tasks.runtime.retrieval_eval.get_sync_db
    - app.worker.tasks.runtime.retrieval_eval.fernet_decrypt
    - app.worker.tasks.runtime.retrieval_eval._check_existing_score
    - app.worker.tasks.runtime.retrieval_eval._is_auditor_flagged
    - app.worker.tasks.runtime.retrieval_eval._fetch_turn_context
    - app.worker.tasks.runtime.retrieval_eval._fetch_last_user_message
    - app.worker.tasks.runtime.retrieval_eval._compute_ragas_faithfulness
    - app.worker.tasks.runtime.retrieval_eval._update_retrieval_metrics
    - app.worker.tasks.runtime.retrieval_eval.random.random
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.worker.tasks.runtime import retrieval_eval as mod

_AGENT_ID = "agent-uuid"
_JOB_ID = "job-uuid"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_db_context(mock_db):
    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


def _make_mock_agent():
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted-conn-str"
    return agent


def _make_mock_db(agent):
    db = MagicMock()
    db.get.return_value = agent
    return db


def _patch_common(monkeypatch, mock_db, conn_str="postgresql://fake/tenant"):
    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _enc: conn_str)


# ---------------------------------------------------------------------------
# Test 1: signature — no conn_str in task args (CLAUDE.md rule 4)
# ---------------------------------------------------------------------------


def test_run_retrieval_faithfulness_signature_has_no_conn_str():
    params = set(inspect.signature(mod.run_retrieval_faithfulness.run).parameters)
    assert "conn_str" not in params
    assert "agent_id" in params
    assert "job_id" in params


def test_run_retrieval_faithfulness_acks_late_and_queue():
    assert mod.run_retrieval_faithfulness.acks_late is True
    assert mod.run_retrieval_faithfulness.max_retries == 2


# ---------------------------------------------------------------------------
# Test 2: idempotent — already scored -> no recompute, no update
# ---------------------------------------------------------------------------


def test_already_scored_returns_without_recompute(monkeypatch):
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)

    monkeypatch.setattr(mod, "_check_existing_score", lambda conn_str, job_id: (True, True))
    compute_called = []
    monkeypatch.setattr(
        mod, "_compute_ragas_faithfulness",
        lambda **kw: compute_called.append(kw) or 0.9,
    )
    update_called = []
    monkeypatch.setattr(
        mod, "_update_retrieval_metrics",
        lambda *a: update_called.append(a),
    )

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    assert result == {"status": "already_scored"}
    assert compute_called == []
    assert update_called == []


# ---------------------------------------------------------------------------
# Test 3: no retrieval_metrics row -> early return, no update
# ---------------------------------------------------------------------------


def test_no_retrieval_metrics_row_returns_early(monkeypatch):
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)

    monkeypatch.setattr(mod, "_check_existing_score", lambda conn_str, job_id: (False, False))
    update_called = []
    monkeypatch.setattr(mod, "_update_retrieval_metrics", lambda *a: update_called.append(a))

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    assert result == {"status": "no_retrieval_metrics_row"}
    assert update_called == []


# ---------------------------------------------------------------------------
# Test 4/5/6: sampling gate + auditor-flag override
# ---------------------------------------------------------------------------


def _patch_scoreable_turn(monkeypatch, citations=None, retrieve_contexts=None):
    """Wire a has-row/not-yet-scored turn with a fetchable turn context."""
    monkeypatch.setattr(mod, "_check_existing_score", lambda conn_str, job_id: (False, True))
    monkeypatch.setattr(
        mod, "_fetch_turn_context",
        lambda db, job_id: (
            "response text with [CITATIONS] block",
            citations if citations is not None else [{"document_name": "doc1", "section": "s1"}],
            "conv-1",
            retrieve_contexts if retrieve_contexts is not None else ["ctx chunk 1"],
        ),
    )
    monkeypatch.setattr(mod, "_fetch_last_user_message", lambda conn_str, conv_id: "the question")
    monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: 0.87)
    updates = []
    monkeypatch.setattr(mod, "_update_retrieval_metrics", lambda *a: updates.append(a))
    return updates


def test_sampled_below_rate_computes_and_updates(monkeypatch):
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(monkeypatch)

    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 0.5)
    monkeypatch.setattr(mod.random, "random", lambda: 0.1)  # 0.1 < 0.5 -> sampled

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    assert result["status"] == "scored"
    assert result["faithfulness"] == 0.87
    assert len(updates) == 1


def test_not_sampled_and_not_auditor_flagged_skips(monkeypatch):
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(monkeypatch)

    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 0.1)
    monkeypatch.setattr(mod.random, "random", lambda: 0.9)  # 0.9 >= 0.1 -> not sampled
    monkeypatch.setattr(mod, "_is_auditor_flagged", lambda db, job_id: False)

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    assert result == {"status": "skipped_not_sampled"}
    assert updates == []


def test_auditor_flagged_overrides_sample_gate(monkeypatch):
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(monkeypatch)

    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 0.1)
    monkeypatch.setattr(mod.random, "random", lambda: 0.9)  # not sampled by rate alone
    flagged_check = []
    monkeypatch.setattr(
        mod, "_is_auditor_flagged",
        lambda db, job_id: flagged_check.append(job_id) or True,
    )

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    assert result["status"] == "scored"
    assert flagged_check == [_JOB_ID]
    assert len(updates) == 1


# ---------------------------------------------------------------------------
# Test 7: citation_coverage computation
# ---------------------------------------------------------------------------


def test_citation_coverage_none_when_nothing_retrieved(monkeypatch):
    """No retrieve calls -> citation_coverage is None (honest-empty-state), not 0.0."""
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(monkeypatch, citations=[], retrieve_contexts=[])

    # Force the sampled path so we reach the compute stage.
    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(mod.random, "random", lambda: 0.0)
    monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: None)

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    # Both signals absent -> no_signal, no UPDATE issued.
    assert result == {"status": "no_signal"}
    assert updates == []


def test_citation_coverage_ratio_capped_at_one(monkeypatch):
    """3 citations over 2 retrieve calls -> capped at 1.0, never fabricated above 1.0."""
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(
        monkeypatch,
        citations=[{"document_name": "d1", "section": "s1"}] * 3,
        retrieve_contexts=["ctx1", "ctx2"],
    )
    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(mod.random, "random", lambda: 0.0)
    monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: None)

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    assert result["status"] == "scored"
    assert result["citation_coverage"] == 1.0
    assert len(updates) == 1
    # (conn_str, job_id, citation_coverage, faithfulness)
    assert updates[0][2] == 1.0
    assert updates[0][3] is None


# ---------------------------------------------------------------------------
# Test 8: config knob present with the documented default
# ---------------------------------------------------------------------------


def test_sample_rate_default_is_point_one():
    from app.core.config import Settings

    assert Settings.model_fields["RETRIEVAL_FAITHFULNESS_SAMPLE_RATE"].default == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Test 9: the Ragas 0.4.x scoring path, against the REAL library (7.18)
#
# Everything above stubs _compute_ragas_faithfulness, which is why the scoring
# path shipped broken: the first live sampled turn logged
#   ragas_call_failed error='All metrics must be initialised metric objects'
# and wrote faithfulness=None. The tests below import real ragas and run real
# metric code; only the network hop (the LLM) is replaced.
# ---------------------------------------------------------------------------


def _fake_instructor_llm(statements: list[str], verdicts: list[int]):
    """An LLM that IS an InstructorBaseRagasLLM and answers from canned outputs.

    Subclassing the real base matters: collections' BaseMetric rejects anything
    that is not an InstructorBaseRagasLLM at construction time, so a MagicMock
    would never get far enough to exercise the bug this file is about.
    """
    from ragas.llms.base import InstructorBaseRagasLLM
    from ragas.metrics.collections.faithfulness.util import (
        NLIStatementOutput,
        StatementFaithfulnessAnswer,
        StatementGeneratorOutput,
    )

    class _FakeInstructorLLM(InstructorBaseRagasLLM):
        def generate(self, prompt, response_model):
            raise AssertionError(
                "collections metrics must reach the LLM through agenerate()"
            )

        async def agenerate(self, prompt, response_model):
            if response_model is StatementGeneratorOutput:
                return StatementGeneratorOutput(statements=statements)
            if response_model is NLIStatementOutput:
                return NLIStatementOutput(
                    statements=[
                        StatementFaithfulnessAnswer(
                            statement=statement, reason="canned", verdict=verdict
                        )
                        for statement, verdict in zip(statements, verdicts)
                    ]
                )
            raise AssertionError(f"unexpected response_model: {response_model}")

    return _FakeInstructorLLM()


def _build_metrics():
    return mod._build_faithfulness_metrics(_fake_instructor_llm(["s"], [1]))


def test_built_metrics_are_instances_not_classes():
    """Every element of the metrics list is a constructed metric object."""
    from ragas.metrics.base import SimpleBaseMetric

    metrics = _build_metrics()

    assert metrics, "no metrics were built"
    for metric in metrics:
        assert not isinstance(metric, type), (
            f"{metric!r} is a class, not an instance"
        )
        assert isinstance(metric, SimpleBaseMetric), (
            f"{metric!r} is not a Ragas metric object"
        )


def test_built_metrics_are_not_legacy_metrics_so_evaluate_is_the_wrong_door():
    """Names the hierarchy fact that made the error message misleading.

    ragas/evaluation.py:133 raises "All metrics must be initialised metric
    objects" for anything that fails `isinstance(m, ragas.metrics.base.Metric)`.
    A collections metric fails it while being a perfectly initialised object, so
    this task must score through ascore(), not evaluate(). If a future ragas
    makes collections metrics legacy Metrics too, this test goes red and
    evaluate() becomes available again.
    """
    from ragas.metrics.base import Metric

    for metric in _build_metrics():
        assert not isinstance(metric, Metric), (
            f"{type(metric).__name__} is now a legacy ragas Metric"
        )


def test_compute_ragas_faithfulness_scores_through_real_ragas(monkeypatch):
    """The whole point: a real Faithfulness metric returns a real score.

    Two statements, one supported by the context: 1/2 = 0.5. Pre-fix this
    returned None, because evaluate() rejected the metric before any scoring
    happened.
    """
    monkeypatch.setattr(
        mod,
        "_build_instructor_llm",
        lambda: _fake_instructor_llm(["claim A", "claim B"], [1, 0]),
    )

    score = mod._compute_ragas_faithfulness(
        question="what is the refund window?",
        response_text="Refunds run 30 days. Shipping is free.",
        contexts=["Refunds are accepted within 30 days of delivery."],
    )

    assert score == pytest.approx(0.5)


def test_instructor_llm_wraps_an_async_client(monkeypatch):
    """A sync Anthropic client makes agenerate() raise, which is the second
    half of the same outage: collections metrics never call generate()."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")

    llm = mod._build_instructor_llm()

    assert llm.is_async is True, (
        "InstructorLLM wraps a sync client; agenerate() will raise TypeError"
    )
