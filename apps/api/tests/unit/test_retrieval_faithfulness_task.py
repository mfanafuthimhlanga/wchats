"""
Unit tests for OPS-07: run_retrieval_faithfulness sampled Celery task.

Tests:
    1. Signature — task takes only (agent_id, job_id), no conn_str (CLAUDE.md rule 4).
    2. Idempotent — already-scored job_id returns without recompute.
    3. No retrieval_metrics row — returns early, no UPDATE attempted.
    4. Sampling gate — random < rate dispatches the compute path.
    5. Sampling gate — random >= rate AND not auditor-flagged skips the compute path.
    6. Auditor-flag override — random >= rate but auditor-flagged still computes.
    7. citation_coverage computed from citations over the retrieve calls that
       actually retrieved something; None when none did (honest-empty-state,
       never fabricated 0.0).
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
    - app.worker.tasks.runtime.retrieval_eval._fetch_retrieved_contexts
    - app.worker.tasks.runtime.retrieval_eval._fetch_last_user_message
    - app.worker.tasks.runtime.retrieval_eval._compute_ragas_faithfulness
    - app.worker.tasks.runtime.retrieval_eval._update_retrieval_metrics
    - app.worker.tasks.runtime.retrieval_eval.random.random
"""

from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.worker.tasks.runtime import retrieval_eval as mod
from tests.model_doubles import ledger

_AGENT_ID = "agent-uuid"
_JOB_ID = "job-uuid"
_MESSAGE_ID = "assistant-msg-uuid"

#: Two chunks as `tool_calls.retrieved_chunks` stores them: the judge rendering,
#: content plus the provenance the agent saw (`_persisted_chunks` in agent.py).
_CHUNK_A = (
    "[source: ACME-HANDBOOK.pdf | section: Returns | chunk: c-1 | score: 0.91]\n"
    "Unopened bags may be returned within 14 days of delivery."
)
_CHUNK_B = (
    "[source: ACME-HANDBOOK.pdf | section: Refunds | chunk: c-2 | score: 0.78]\n"
    "Refunds are issued to the original payment method."
)
_CITATION = {"document_name": "doc1", "section": "s1"}


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


def _patch_scoreable_turn(
    monkeypatch, citations=None, contexts=None, measured=None, unmeasured=0
):
    """Wire a has-row/not-yet-scored turn whose `tool_calls` rows are already read.

    `contexts` is the chunk list the tenant's `tool_calls.retrieved_chunks` gave
    back. `measured` is how many retrieve CALLS produced it and defaults to one
    call per chunk, which is the shape the old summary proxy could express and
    keeps these tests comparing the same arithmetic. `unmeasured` is the calls
    that recorded nothing, and no chunk of theirs exists to pass.
    """
    monkeypatch.setattr(mod, "_check_existing_score", lambda conn_str, job_id: (False, True))
    monkeypatch.setattr(
        mod, "_fetch_turn_context",
        lambda db, job_id: (
            "response text with [CITATIONS] block",
            citations if citations is not None else [_CITATION],
            "conv-1",
            _MESSAGE_ID,
        ),
    )
    chunks = ["ctx chunk 1"] if contexts is None else contexts
    monkeypatch.setattr(
        mod, "_fetch_retrieved_contexts",
        lambda conn_str, message_id: mod._TurnRetrieval(
            tuple(chunks), len(chunks) if measured is None else measured, unmeasured
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
    updates = _patch_scoreable_turn(monkeypatch, citations=[], contexts=[])

    # Force the sampled path so we reach the compute stage.
    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(mod.random, "random", lambda: 0.0)
    monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: None)

    result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)

    # Both signals absent -> no_signal, no UPDATE issued. The two counts ride
    # along on every status past the sampling gate (#81).
    assert result == {
        "status": "no_signal",
        "retrieve_calls_measured": 0,
        "retrieve_calls_unmeasured": 0,
    }
    assert updates == []


def test_citation_coverage_ratio_capped_at_one(monkeypatch):
    """3 citations over 2 retrieve calls -> capped at 1.0, never fabricated above 1.0."""
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(
        monkeypatch,
        citations=[{"document_name": "d1", "section": "s1"}] * 3,
        contexts=["ctx1", "ctx2"],
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
# The fifth Judge names itself (ticket #47, AC3)
# ---------------------------------------------------------------------------


class TestTheFifthJudgeIsIdentified:
    """A faithfulness score nobody can attribute cannot be calibrated against.

    `eval_service` stamps its four Judges on the `eval_results.detail` of every
    row they score. This Judge scores live traffic, so its verdict lands on the
    `retrieval_metrics` row for the turn, and the identity lands beside it in the
    same UPDATE. The three fields are the grain a calibration figure compares on,
    which is why an incomplete one is written as NULL rather than partially.
    """

    def _scored(self, monkeypatch, faithfulness=0.87):
        mock_db = _make_mock_db(_make_mock_agent())
        _patch_common(monkeypatch, mock_db)
        updates = _patch_scoreable_turn(monkeypatch)
        monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: faithfulness)
        monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 1.0)
        monkeypatch.setattr(mod.random, "random", lambda: 0.0)

        result = mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)
        return result, updates

    def test_the_update_carries_the_judge_that_produced_the_verdict(self, monkeypatch):
        """Literals, not a read of the same table the code reads.

        Comparing against `PURPOSE_ROUTES` here would pass unchanged the day the
        route moves, which is exactly the day a stored identity stops matching
        the Judge that ran.
        """
        import importlib.metadata

        _result, updates = self._scored(monkeypatch)

        # (conn_str, job_id, citation_coverage, faithfulness, judge_identity)
        assert updates[0][4] == {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "none",
            "prompt_version": f"ragas-{importlib.metadata.version('ragas')}",
        }

    def test_the_identity_is_absent_when_no_verdict_was_produced(self, monkeypatch):
        """citation_coverage is arithmetic this task does itself. No Judge ran."""
        _result, updates = self._scored(monkeypatch, faithfulness=None)

        assert updates[0][3] is None
        assert updates[0][4] is None, (
            "a turn the Judge never scored names a Judge anyway, which files "
            "arithmetic under a model that did no work"
        )

    def test_the_prompt_version_names_the_artifact_the_prompt_ships_in(self):
        """No judge prompt in this repo carries a version. Ragas authors this one,
        the same package that authors eval_service's four, so the installed
        distribution is the identifier both read."""
        import importlib.metadata

        from app.services.eval_service import JUDGE_PROMPT_VERSION as offline

        assert mod.judge_identity().prompt_version == offline
        assert offline == f"ragas-{importlib.metadata.version('ragas')}"

    def test_a_route_naming_no_effort_yields_no_identity_at_all(self, monkeypatch):
        """A key with a hole in it groups two different Judges together."""
        from app.core.model_client import ModelRoute

        monkeypatch.setattr(
            mod, "route_for", lambda purpose: ModelRoute("openai", "gpt-5.6-luna")
        )

        assert mod.judge_identity() is None


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
        lambda purpose, led: _fake_instructor_llm(["claim A", "claim B"], [1, 0]),
    )

    score = mod._compute_ragas_faithfulness(
        question="what is the refund window?",
        response_text="Refunds run 30 days. Shipping is free.",
        contexts=["Refunds are accepted within 30 days of delivery."],
        ledger=ledger(),
    )

    assert score == pytest.approx(0.5)


def test_instructor_llm_wraps_an_async_client():
    """A sync client makes agenerate() raise, which is the second half of the
    same outage: collections metrics never call generate()."""
    llm = mod._build_instructor_llm(mod.JUDGE_PURPOSE, ledger())

    assert llm.is_async is True, (
        "InstructorLLM wraps a sync client; agenerate() will raise TypeError"
    )


def test_the_judge_carries_no_thinking_parameter():
    """`thinking` left with the provider that needed it (ticket #47).

    It cleared a DeepSeek 400 on the forced tool_choice instructor puts on every
    structured call. This judge is on OpenAI now, ragas splats every extra kwarg
    straight into `client.chat.completions.create()`, and an unknown field on
    that wire is a 400 of its own.
    """
    llm = mod._build_instructor_llm(mod.JUDGE_PURPOSE, ledger())

    assert "thinking" not in llm._map_provider_params(), (
        "the judge still carries a thinking parameter, which OpenAI does not "
        "take; ragas passes it through to the request unchanged"
    )
    assert llm.model == "gpt-5.6-luna", (
        f"the judge names model={llm.model!r} rather than the routed one"
    )


# ---------------------------------------------------------------------------
# The context is what the tool retrieved, not a summary of it (#81, #84)
# ---------------------------------------------------------------------------


def _fake_db_with_response(payload):
    """A control DB whose only `agent.response` row carries `payload`."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (payload,)
    return db


def test_the_turn_context_carries_the_message_id_to_join_on():
    """The join key replaced the proxy, and the second query went with it.

    `db.execute` running once is the absence pin. A second call is the
    `agent.tool_result` summary read, and #84 is that the summary's shape changed
    under the scores at the #48 cutover without anything recording it.
    """
    db = _fake_db_with_response({
        "text": "answer",
        "citations": [_CITATION],
        "conversation_id": "conv-1",
        "message_id": "assistant-msg-1",
    })

    text, citations, conversation_id, message_id = mod._fetch_turn_context(db, _JOB_ID)

    assert (text, citations, conversation_id) == ("answer", [_CITATION], "conv-1")
    assert message_id == "assistant-msg-1"
    assert db.execute.call_count == 1, (
        "a second query is the agent.tool_result summary proxy still being read"
    )


def test_a_successful_retrieve_is_scored_against_its_chunks():
    out = mod._read_retrieved_rows([([_CHUNK_A, _CHUNK_B],)])

    assert out.contexts == (_CHUNK_A, _CHUNK_B)
    assert (out.measured, out.unmeasured) == (1, 0)


def test_a_null_retrieved_chunks_is_unmeasured_not_an_empty_context():
    """The whole of #81 at the reader. An empty context makes every claim
    unsupported, so scoring one would report the DoS guard as an ungrounded
    answer."""
    out = mod._read_retrieved_rows([(None,)])

    assert out.contexts == ()
    assert (out.measured, out.unmeasured) == (0, 1)


def test_a_corpus_miss_is_a_measured_call():
    """`[]` is an observation: a retrieve ran and the corpus matched nothing."""
    out = mod._read_retrieved_rows([([],)])

    assert (out.measured, out.unmeasured) == (1, 0)


def test_null_and_a_corpus_miss_are_counted_apart():
    """The property the column exists to preserve, one statement, one reader."""
    miss = mod._read_retrieved_rows([([],)])
    absent = mod._read_retrieved_rows([(None,)])

    assert (miss.measured, miss.unmeasured) == (1, 0)
    assert (absent.measured, absent.unmeasured) == (0, 1)


def test_an_errored_retrieve_reaches_this_reader_as_unmeasured():
    """The writer's error-flag check and this reader's NULL branch are one chain.

    `_persisted_chunks` returns None for an errored retrieve, the column holds
    SQL NULL, and this reader counts it unmeasured. Asserting the two halves meet
    is the point: either alone is a guard with nothing on the other side of it.
    """
    from app.services.agent_loop import (
        RETRIEVE_CHUNKS_KEY,
        RETRIEVE_CHUNKS_PARSED,
        RETRIEVE_CHUNKS_SOURCE_KEY,
        RETRIEVE_JUDGE_CHUNKS_KEY,
        RETRIEVE_RESULT_IS_ERROR_KEY,
    )
    from app.worker.tasks.runtime.agent import _persisted_chunks

    errored = {
        "tool_name": "retrieve",
        "input": {"query": "return policy"},
        "result": "Retrieve quota exceeded for this turn",
        RETRIEVE_RESULT_IS_ERROR_KEY: True,
        RETRIEVE_CHUNKS_SOURCE_KEY: RETRIEVE_CHUNKS_PARSED,
        RETRIEVE_CHUNKS_KEY: [],
        RETRIEVE_JUDGE_CHUNKS_KEY: [],
    }
    stored = _persisted_chunks(errored)

    assert stored is None
    out = mod._read_retrieved_rows([(stored,)])
    assert out.contexts == (), "the refusal text reached the Judge as context"
    assert (out.measured, out.unmeasured) == (0, 1)


def test_a_json_string_column_value_still_decodes():
    """psycopg2 hands back a list; a connection without the typecaster hands back
    a str, and reporting every retrieve in the tenant as unmeasured would read as
    a quiet run of unknowns rather than as the outage it is."""
    out = mod._read_retrieved_rows([(json.dumps([_CHUNK_A]),)])

    assert out.contexts == (_CHUNK_A,)
    assert (out.measured, out.unmeasured) == (1, 0)


def test_a_value_that_does_not_decode_to_a_list_is_unmeasured():
    out = mod._read_retrieved_rows([("not json at all",), ({"chunks": []},)])

    assert (out.measured, out.unmeasured) == (0, 2)


def test_no_message_id_joins_to_nothing_and_opens_no_connection(monkeypatch):
    """A payload without WIRE-05's id cannot name the turn's rows. It reports no
    calls rather than guessing at how many there were."""
    def _explode(*args, **kwargs):
        raise AssertionError("connected to the tenant with nothing to join on")

    monkeypatch.setattr(mod.psycopg2, "connect", _explode)

    assert mod._fetch_retrieved_contexts("postgresql://fake/tenant", None) == (
        mod._TurnRetrieval((), 0, 0)
    )


# ---------------------------------------------------------------------------
# The unmeasured count travels with the verdict (#81)
# ---------------------------------------------------------------------------


def _run_sampled(monkeypatch):
    """Force the sampled path and run the task."""
    monkeypatch.setattr(mod.settings, "RETRIEVAL_FAITHFULNESS_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(mod.random, "random", lambda: 0.0)
    return mod.run_retrieval_faithfulness.run(_AGENT_ID, _JOB_ID)


def test_ragas_is_handed_the_persisted_chunks(monkeypatch):
    """One string per chunk, the rendering the column stores."""
    seen = {}
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    _patch_scoreable_turn(monkeypatch, contexts=[_CHUNK_A, _CHUNK_B], measured=1)
    monkeypatch.setattr(
        mod, "_compute_ragas_faithfulness", lambda **kw: seen.update(kw) or 0.5
    )

    result = _run_sampled(monkeypatch)

    assert result["status"] == "scored"
    assert seen["contexts"] == [_CHUNK_A, _CHUNK_B]


def test_the_counts_travel_with_a_scored_verdict(monkeypatch):
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    _patch_scoreable_turn(
        monkeypatch, contexts=[_CHUNK_A, _CHUNK_B], measured=2, unmeasured=1
    )

    result = _run_sampled(monkeypatch)

    assert result["status"] == "scored"
    assert result["retrieve_calls_measured"] == 2
    assert result["retrieve_calls_unmeasured"] == 1


def test_a_turn_whose_retrieves_all_errored_reads_unknown_not_clean(monkeypatch):
    """The measurement rule, one turn wide: zero valid observations is unknown.

    Three retrieve calls, none of them readable. Nothing is scored, nothing is
    written, and the count says three rather than the status saying nothing at
    all, which would read as a turn with no faithfulness problem.
    """
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(
        monkeypatch, citations=[], contexts=[], measured=0, unmeasured=3
    )
    monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: None)

    result = _run_sampled(monkeypatch)

    assert result["status"] == "no_signal"
    assert result["retrieve_calls_measured"] == 0
    assert result["retrieve_calls_unmeasured"] == 3
    assert updates == [], "a turn nobody could measure wrote a score anyway"


def test_citation_coverage_denominator_excludes_the_unreadable_calls(monkeypatch):
    """One citation over two measured calls is 0.5. Counting the two errored ones
    would make it 0.25 and report the turn as poorly cited because its guard
    fired."""
    mock_db = _make_mock_db(_make_mock_agent())
    _patch_common(monkeypatch, mock_db)
    updates = _patch_scoreable_turn(
        monkeypatch,
        citations=[_CITATION],
        contexts=[_CHUNK_A, _CHUNK_B],
        measured=2,
        unmeasured=2,
    )
    monkeypatch.setattr(mod, "_compute_ragas_faithfulness", lambda **kw: None)

    result = _run_sampled(monkeypatch)

    assert result["citation_coverage"] == 0.5
    assert updates[0][2] == 0.5


def test_citation_coverage_is_none_when_no_call_was_measured():
    assert mod._citation_coverage([_CITATION], 0) is None


def test_citation_coverage_never_exceeds_one():
    assert mod._citation_coverage([_CITATION] * 5, 2) == 1.0
