"""
Unit tests for app.services.eval_service (M6 Ragas 0.4.x eval harness).

Original M6 coverage (TestRunRagasEval): Ragas 0.4.x dataset shape and import
path (D-01/D-02/D-04).

P1 coverage (measurement-layer audit) — the three things that must hold together
or the eval layer means nothing:

  D2, the persistence split
      Scoring runs against the Neon branch; the run's own observations
      (eval_runs status, eval_results) belong on PRODUCTION, because the branch
      is deleted in the caller's `finally`. Tested by asserting which connection
      string each write opens, plus absence pins on the parameter NAMES so a
      revert to `branch_conn_str` fails loudly rather than silently sending
      results back to the throwaway branch.

  D5, the label trust hierarchy
      verified_qa is served to real customers ahead of retrieval. Promotion is
      gated on WHO WROTE the label, not on how well it scores, and no scenario
      source the shipped schema allows clears that gate. The unreachability
      tests are parametrised over the source list PARSED OUT OF MIGRATION 0011,
      so a new source value added to the schema without a trust tier fails these
      tests instead of quietly defaulting into the customer-serving cache.

  The configuration tuple
      A score with no record of what produced it cannot be compared to the next
      one. Tests here pin the distinction between "we could not read this
      dimension" (named in config["unavailable"]) and "we read it and it is
      genuinely absent" (None, not named) — missing data is never passing data.

Mock strategy:
    - All external calls (ragas, psycopg2, Voyage) patched at module boundary.
    - psycopg2.connect is patched by attribute, never the whole psycopg2 module,
      wherever a test exercises `except psycopg2.errors.UndefinedColumn` — a
      MagicMock in that except clause is not a BaseException subclass and would
      turn a real assertion into a TypeError.
    - conftest.py sets all required env vars before any app import.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import psycopg2
import pytest
from ragas.embeddings.base import BaseRagasEmbedding
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections.answer_relevancy.util import AnswerRelevanceOutput
from ragas.metrics.collections.context_precision.util import ContextPrecisionOutput
from ragas.metrics.collections.context_recall.util import (
    ContextRecallClassification,
    ContextRecallOutput,
)
from ragas.metrics.collections.faithfulness.util import (
    NLIStatementOutput,
    StatementFaithfulnessAnswer,
    StatementGeneratorOutput,
)

from tests.model_doubles import ledger

# ---------------------------------------------------------------------------
# Real-ragas doubles (7.18)
#
# Collections metrics validate their components at construction: anything that
# is not an InstructorBaseRagasLLM / BaseRagasEmbedding is rejected outright, so
# a MagicMock never reaches the scoring code. These two subclass the real bases
# and replace only the network hop.
# ---------------------------------------------------------------------------

# One statement, supported, attributed — every metric scores 1.0 off this.
_CANNED_JUDGE_OUTPUTS = {
    StatementGeneratorOutput: lambda: StatementGeneratorOutput(
        statements=["the only claim"]
    ),
    NLIStatementOutput: lambda: NLIStatementOutput(
        statements=[
            StatementFaithfulnessAnswer(
                statement="the only claim", reason="canned", verdict=1
            )
        ]
    ),
    AnswerRelevanceOutput: lambda: AnswerRelevanceOutput(
        question="What is the return policy?", noncommittal=0
    ),
    ContextPrecisionOutput: lambda: ContextPrecisionOutput(reason="canned", verdict=1),
    ContextRecallOutput: lambda: ContextRecallOutput(
        classifications=[
            ContextRecallClassification(
                statement="the only claim", reason="canned", attributed=1
            )
        ]
    ),
}


class _FakeInstructorLLM(InstructorBaseRagasLLM):
    def generate(self, prompt, response_model):
        raise AssertionError(
            "collections metrics must reach the LLM through agenerate()"
        )

    async def agenerate(self, prompt, response_model):
        return _CANNED_JUDGE_OUTPUTS[response_model]()


class _FakeRagasEmbedding(BaseRagasEmbedding):
    """Stands in for _VoyageRagasEmbedding. One fixed unit vector, so
    AnswerRelevancy's cosine similarity is exactly 1.0."""

    def embed_text(self, text: str, **kwargs) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def aembed_text(self, text: str, **kwargs) -> list[float]:
        return [1.0, 0.0, 0.0]


def _fake_ragas_instructor_llm(purpose, ledger):  # noqa: ARG001
    """Stands in for `_build_instructor_llm`, whose signature it must match."""
    return _FakeInstructorLLM()


# ---------------------------------------------------------------------------
# The scenario sources the SCHEMA allows, parsed from migration 0011 itself.
#
# Hardcoding the list here would let it drift from the CHECK constraint silently,
# so it is read out of the migration. test_label_provenance and
# test_migration_tenant_0016 parse the same way for the same reason.
# ---------------------------------------------------------------------------
_MIGRATION_0011 = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "../../alembic_tenant/versions/0011_eval_scenarios_provenance.py",
    )
)


def _schema_allowed_scenario_sources() -> list[str]:
    with open(_MIGRATION_0011, encoding="utf-8") as fh:
        source = fh.read()
    clauses = re.findall(r"CHECK \(source IN \(([^)]*)\)\)", source)
    assert clauses, (
        "Could not find the eval_scenarios.source CHECK constraint in migration "
        "0011 — this test's whole point is to read the schema rather than "
        "restate it, so a parse failure is a real failure."
    )
    allowed: set[str] = set()
    for clause in clauses:
        allowed.update(re.findall(r"'([^']+)'", clause))
    return sorted(allowed)


SCHEMA_ALLOWED_SOURCES = _schema_allowed_scenario_sources()


# ---------------------------------------------------------------------------
# Shared psycopg2 doubles
# ---------------------------------------------------------------------------


class _RecordingCursor:
    """Cursor double that records every (sql, params) pair it is handed."""

    def __init__(self, fetchone_result=None, raise_on: str | None = None, exc=None):
        self.executed: list[tuple[str, object]] = []
        self.fetchone_result = fetchone_result
        self.raise_on = raise_on
        self.exc = exc

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.raise_on and self.raise_on in sql:
            raise self.exc

    def fetchone(self):
        return self.fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def statements(self) -> str:
        return "\n".join(sql for sql, _ in self.executed)


def _recording_connect(cursor, conn_strings: list[str]):
    """psycopg2.connect double that records the connection string it is given."""

    conn = MagicMock()
    conn.cursor.return_value = cursor

    def _connect(conn_str, *args, **kwargs):
        conn_strings.append(conn_str)
        return conn

    return _connect, conn


# ---------------------------------------------------------------------------
# test_run_ragas_eval_builds_dataset
# ---------------------------------------------------------------------------


class TestRunRagasEval:
    """Tests for the Ragas 0.4.x harness (run_ragas_eval)."""

    def test_run_ragas_eval_builds_dataset(self, monkeypatch):
        """EvaluationDataset.from_list is called with 'reference' key (D-02 LOCKED),
        and the four metrics (D-04 LOCKED) each produce a real score.

        This test used to mock `evaluate`, `EvaluationDataset` and all four
        metric classes, which is exactly how the harness shipped with a scoring
        call ragas rejects (7.18). Nothing in ragas is mocked here: the real
        EvaluationDataset validates the samples, the real metrics run, and only
        the two network hops — the judge LLM and Voyage — are canned.
        """
        from ragas import EvaluationDataset

        from app.services import eval_service
        from app.services.eval_service import run_ragas_eval

        from_list_calls = []
        real_from_list = EvaluationDataset.from_list

        class _SpyDataset:
            @staticmethod
            def from_list(samples):
                from_list_calls.append(samples)
                return real_from_list(samples)

        monkeypatch.setattr(eval_service, "EvaluationDataset", _SpyDataset)
        monkeypatch.setattr(
            eval_service, "_build_instructor_llm", _fake_ragas_instructor_llm
        )
        monkeypatch.setattr(eval_service, "_VoyageRagasEmbedding", _FakeRagasEmbedding)

        scenarios = [
            {
                "id": str(uuid.uuid4()),
                "question": "What is the return policy?",
                "reference_answer": "Items can be returned within 30 days.",
                "retrieved_contexts": ["Our return policy allows 30-day returns."],
                "agent_response": "You can return items within 30 days.",
            }
        ]

        result = run_ragas_eval(scenarios, ledger())

        # D-02 LOCKED: from_list must receive 'reference' key (not 'ground_truths')
        assert from_list_calls, "EvaluationDataset.from_list was not called"
        samples_list = from_list_calls[0]
        assert len(samples_list) == 1
        assert "reference" in samples_list[0], (
            "D-02 violation: EvaluationDataset.from_list sample missing 'reference' key"
        )
        assert "ground_truths" not in samples_list[0], (
            "D-02 violation: 'ground_truths' key present — must be 'reference' in Ragas 0.4.x"
        )
        assert samples_list[0]["reference"] == "Items can be returned within 30 days."

        # D-04 LOCKED: all four metrics scored, none of them unknown.
        assert result["scores"], "no scenario was scored"
        for metric in ("faithfulness", "answer_relevancy", "context_precision",
                       "context_recall"):
            assert result["scores"][0][metric] is not None, (
                f"{metric} came back unknown against a metric that cannot fail here"
            )
            assert result["means"][metric] is not None

    def test_built_metrics_are_instances_not_classes(self):
        """Every element of the metrics list is a constructed metric object,
        and none of them is a legacy `ragas.metrics.base.Metric`.

        The second half is the one that fell over live: ragas/evaluation.py:133
        raises "All metrics must be initialised metric objects" for anything
        failing `isinstance(m, Metric)`, and collections metrics fail it while
        being perfectly initialised. That is why this harness scores through
        ascore() and never through evaluate().
        """
        from ragas.metrics.base import Metric, SimpleBaseMetric

        from app.services.eval_service import _build_ragas_metrics

        metrics = _build_ragas_metrics(ledger(), _FakeRagasEmbedding())

        assert len(metrics) == 4
        for metric in metrics:
            assert not isinstance(metric, type), f"{metric!r} is a class, not an instance"
            assert isinstance(metric, SimpleBaseMetric)
            assert not isinstance(metric, Metric), (
                f"{type(metric).__name__} is now a legacy ragas Metric"
            )

    def test_instructor_llm_wraps_an_async_client(self):
        """Collections metrics only ever await agenerate(), and
        InstructorLLM.agenerate raises TypeError on a synchronous client."""
        from app.services.eval_service import _build_instructor_llm

        assert _build_instructor_llm("judge_faithfulness", ledger()).is_async is True

    def test_the_judge_sends_luna_and_effort_none_on_the_wire(self):
        """The two figures decision #34 priced, read off the bytes the judge sent.

        Not off the route, and not off the InstructorLLM. Instructor fills in
        each default the call did not name, and ragas maps parameters per
        provider on the way past. Both layers sit between the routing table and
        the request, so the request is where the claim is checked. A fake
        transport answers with a real OpenAI-shaped body, which is also what
        makes the ledger row underneath it a real one.
        """
        import asyncio
        import json

        import httpx
        from pydantic import BaseModel

        from app.services.eval_service import _build_instructor_llm

        class _Verdict(BaseModel):
            score: int

        sent: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            sent.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "model": "gpt-5.6-luna",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "_Verdict",
                                    "arguments": json.dumps({"score": 1}),
                                },
                            }],
                        },
                    }],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 10},
                    },
                },
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(_handler)

        class _Pinned(httpx.AsyncClient):
            """A client the OpenAI SDK still recognises, answering canned bytes.

            A lambda here fails. The SDK isinstance-checks the client it is
            handed, so the stand-in has to be a real subclass.
            """

            def __init__(self, **kwargs):
                super().__init__(transport=transport, **kwargs)

        rows = []
        with patch("httpx.AsyncClient", _Pinned):
            llm = _build_instructor_llm("judge_faithfulness", ledger(rows))
            asyncio.run(llm.agenerate("score this", _Verdict))

        assert sent.get("model") == "gpt-5.6-luna", (
            f"the judge asked for model={sent.get('model')!r}"
        )
        assert sent.get("reasoning_effort") == "none", (
            "the judge sent reasoning_effort="
            f"{sent.get('reasoning_effort')!r}; decision #34 priced the Judge "
            "floor at effort none and any other value is a different price"
        )
        assert [row.purpose for row in rows] == ["judge_faithfulness"], (
            f"the call left {rows!r} on the ledger"
        )
        assert rows[0].served_model == "gpt-5.6-luna"

    def test_run_ragas_eval_empty_scenarios_returns_empty(self):
        """run_ragas_eval returns empty scores/means when no valid scenarios given.
        No mocking needed — the early-exit path never touches Ragas internals.
        """
        from app.services.eval_service import run_ragas_eval

        # Scenarios without reference_answer are filtered out — early exit
        result = run_ragas_eval(
            [{"question": "Q", "agent_response": "A", "retrieved_contexts": []}],
            ledger(),
        )

        assert result["scores"] == []
        assert result["means"]["faithfulness"] is None

    def test_run_ragas_eval_uses_correct_import(self):
        """eval_service.py imports Ragas 0.4.x path (D-01 LOCKED regression guard).
        'ground_truths' must NOT appear in the source (D-02 LOCKED regression guard).
        """
        import app.services.eval_service as eval_service_module

        source = inspect.getsource(eval_service_module)

        # D-01 LOCKED: must use the 0.4.x import path
        assert "from ragas.metrics.collections import" in source, (
            "D-01 violation: eval_service.py does not import from ragas.metrics.collections"
        )

        # D-02 LOCKED: the old 0.3.x field name must not appear
        assert "ground_truths" not in source, (
            "D-02 violation: 'ground_truths' found in eval_service.py — must use 'reference'"
        )


# ---------------------------------------------------------------------------
# What the scoring half touches — and what it therefore cannot need
# ---------------------------------------------------------------------------


class TestScoringTouchesNoDatabase:
    """run_ragas_eval scores rows already in memory against the judge API.

    It used to accept a `branch_conn_str` marked `# noqa: ARG001` and never
    referenced it. Everything downstream read that parameter as evidence of
    isolation: the module docstring, the task's comments, the commit message
    and a test that asserted the argument was passed. Passing an argument is
    not using it, and a test that pins an unused argument pins nothing.
    """

    def test_scoring_takes_no_connection_string_because_it_opens_none(self):
        """`ledger` is the second parameter and it is not a dsn.

        Ticket #47 gave scoring one argument it did read: who the judge calls
        are billed to, and a recorder for their rows. `LedgerContext` has no
        field that could hold a connection string (project rule 1), so the claim
        this test defends is unchanged and the parameter list is pinned exactly
        rather than left open.
        """
        from app.core.model_client import LedgerContext
        from app.services.eval_service import run_ragas_eval

        params = inspect.signature(run_ragas_eval).parameters
        assert list(params) == ["scenarios", "ledger"], (
            f"run_ragas_eval grew a parameter it does not read: {list(params)}"
        )
        carriers = [
            field.name
            for field in dataclasses.fields(LedgerContext)
            if "conn" in field.name or "dsn" in field.name or "url" in field.name
        ]
        assert not carriers, (
            f"LedgerContext grew {carriers}, a field that could carry a dsn"
        )

    def test_scoring_issues_no_statement_against_any_database(self):
        """Source-level absence pin: scoring opens nothing."""
        from app.services import eval_service

        # The docstring names the parameter it used to take, so only the
        # executable half is inspected — a comment cannot open a connection.
        source = inspect.getsource(eval_service.run_ragas_eval)
        body = source.split('"""', 2)[-1]
        body = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in ("psycopg2", "conn_str", "connect("):
            assert forbidden not in body, (
                f"run_ragas_eval references {forbidden!r}, so scoring is not "
                "opening the zero connections it must open"
            )


class TestConnectTimeouts:
    """Every psycopg2 connection this module opens is bounded.

    The failing input: a production tenant endpoint that accepts the TCP
    connection and never completes the startup handshake (a Neon endpoint
    mid-suspend, a black-holing path). An unbounded connect() blocks forever,
    and nothing else would interrupt it — celery_app.py configures neither
    task_time_limit nor soft_time_limit. The worst call site is
    update_eval_run_status on the failure path: run_eval_suite calls it from
    inside its `except`, which runs BEFORE the `finally` that deletes the Neon
    eval branch, so a hang there leaks a live copy of tenant data and holds a
    runtime worker slot indefinitely. A test can observe a raise; it cannot
    observe a hang, which is why this is pinned at the call site instead.
    """

    def test_every_connect_call_site_passes_a_timeout(self):
        from app.services import eval_service

        source = inspect.getsource(eval_service)
        call_sites = re.findall(r"psycopg2\.connect\(([^)]*)\)", source)
        assert call_sites, "no psycopg2.connect call sites found — parse failure"
        unbounded = [c for c in call_sites if "connect_timeout" not in c]
        assert unbounded == [], (
            f"psycopg2.connect without connect_timeout: {unbounded}"
        )

    @pytest.mark.parametrize(
        "call",
        [
            lambda svc: svc.write_eval_results(
                "run-1",
                svc.build_judge_records([{"scenario_id": "s1", "faithfulness": 0.9}]),
                "postgresql://production",
            ),
            lambda svc: svc.update_eval_run_status(
                "run-1", "failed", finished_at=True, conn_str="postgresql://production"
            ),
            lambda svc: svc.insert_eval_run(
                "run-1", "m6:a", None, None, "postgresql://production"
            ),
            lambda svc: svc.update_eval_run_config(
                "run-1", {"agent_invoked": True}, "postgresql://production"
            ),
        ],
        ids=[
            "write_eval_results",
            "update_eval_run_status",
            "insert_eval_run",
            "update_eval_run_config",
        ],
    )
    def test_production_writers_bound_the_connect(self, monkeypatch, call):
        from app.services import eval_service

        kwargs_seen: list[dict] = []
        conn = MagicMock()
        conn.cursor.return_value = _RecordingCursor()

        def _connect(conn_str, *args, **kwargs):
            kwargs_seen.append(kwargs)
            return conn

        monkeypatch.setattr(eval_service.psycopg2, "connect", _connect)

        call(eval_service)

        assert kwargs_seen and all(
            kw.get("connect_timeout") for kw in kwargs_seen
        ), (
            "connect() was opened with no timeout — a half-open endpoint blocks "
            "the worker forever and the Neon branch is never deleted"
        )


# ---------------------------------------------------------------------------
# D5 — the label trust hierarchy
# ---------------------------------------------------------------------------


class TestLabelTrustHierarchy:
    """A label's authority is a property of who wrote it, not of how it scores."""

    def test_promotion_decision_is_recorded_with_a_reason(self):
        """The disablement is a statement in the run record, not an absence."""
        from app.services.eval_service import (
            VERIFIED_QA_MIN_TRUST_TIER,
            VERIFIED_QA_PROMOTION_DECISION,
        )

        assert VERIFIED_QA_PROMOTION_DECISION["enabled"] is False
        assert VERIFIED_QA_PROMOTION_DECISION["min_trust_tier"] == VERIFIED_QA_MIN_TRUST_TIER
        assert len(VERIFIED_QA_PROMOTION_DECISION["reason"]) > 40


# ---------------------------------------------------------------------------
# D2 — the persistence split
# ---------------------------------------------------------------------------


class TestPersistenceSplit:
    """Results are observations about a run, and they land on production."""

    @pytest.mark.parametrize(
        "func_name", ["write_eval_results", "update_eval_run_status", "insert_eval_run"]
    )
    def test_production_writers_take_no_branch_named_parameter(self, func_name):
        """Absence pin on the parameter NAME.

        The name is load-bearing: a parameter called `branch_conn_str` is what
        invited every caller to hand these functions the branch, which is the
        entire mechanism of D2. Reverting the name fails here before any
        behaviour test has to catch it.
        """
        from app.services import eval_service

        params = inspect.signature(getattr(eval_service, func_name)).parameters
        assert "branch_conn_str" not in params, (
            f"{func_name} must not accept a parameter named 'branch_conn_str' — "
            "it writes to production"
        )
        assert "conn_str" in params

    def test_write_eval_results_opens_the_connection_it_was_given(self, monkeypatch):
        from app.services import eval_service

        cursor = _RecordingCursor()
        conn_strings: list[str] = []
        connect, _conn = _recording_connect(cursor, conn_strings)
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)

        eval_service.write_eval_results(
            str(uuid.uuid4()),
            eval_service.build_judge_records(
                [{"scenario_id": "s1", "faithfulness": 0.9, "answer_relevancy": 0.9,
                  "context_precision": 0.9, "context_recall": 0.9}]
            ),
            "postgresql://production",
        )

        assert conn_strings == ["postgresql://production"]
        assert "INSERT INTO eval_results" in cursor.statements

    def test_update_eval_run_status_opens_the_connection_it_was_given(self, monkeypatch):
        from app.services import eval_service

        cursor = _RecordingCursor()
        conn_strings: list[str] = []
        connect, _conn = _recording_connect(cursor, conn_strings)
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)

        eval_service.update_eval_run_status(
            str(uuid.uuid4()), "complete", finished_at=True,
            conn_str="postgresql://production",
        )

        assert conn_strings == ["postgresql://production"]
        assert "UPDATE eval_runs" in cursor.statements


# ---------------------------------------------------------------------------
# The judge row: what it decided, and what it no longer repeats (#47, #51)
# ---------------------------------------------------------------------------


class TestTheJudgeRowCarriesItsOwnDecision:
    """One eval_results row per (scenario, metric), deciding nothing at read time.

    #47 put the Judge identity in `detail` beside the whole score row, so a
    scenario's four rows each repeated all four of that scenario's scores. #51
    criterion 2 takes the blob out and gives the row columns: the dimension, the
    score, the verdict, the threshold that produced it, the Judge, and the
    reference to the ledger rows that paid for it.

    The verdict matters most. It used to be rebuilt in `api/v1/evals.py` from
    today's `settings`, so raising a threshold restated every verdict already
    written down.
    """

    def _rows(self, monkeypatch, score: dict | None = None) -> dict[str, dict]:
        """The INSERT parameters per metric, off a real write_eval_results call."""
        from app.services import eval_service

        cursor = _RecordingCursor()
        connect, _conn = _recording_connect(cursor, [])
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)

        eval_service.write_eval_results(
            str(uuid.uuid4()),
            eval_service.build_judge_records([score or {
                "scenario_id": "s1", "faithfulness": 0.95, "answer_relevancy": 0.8,
                "context_precision": 0.7, "context_recall": 0.6,
            }]),
            "postgresql://production",
        )
        return {params["metric"]: params for _sql, params in cursor.executed}

    # -- the identity ------------------------------------------------------

    def test_every_row_names_the_judge_that_scored_that_dimension(self, monkeypatch):
        """All three fields, on every dimension, in a column of its own now."""
        from app.services.eval_service import METRIC_KEYS

        rows = self._rows(monkeypatch)

        assert sorted(rows) == sorted(METRIC_KEYS)
        for metric in METRIC_KEYS:
            identity = json.loads(rows[metric]["judge_identity"])
            assert sorted(identity) == [
                "model", "prompt_version", "reasoning_effort"
            ], f"the {metric} row's identity is {identity!r}"

    def test_every_dimension_records_luna_at_effort_none(self, monkeypatch):
        """The two figures decision #34 priced, written out.

        This compared the record against `PURPOSE_ROUTES` until it was noticed
        that the record is BUILT from `PURPOSE_ROUTES`, so both sides moved
        together and the assertion held under any drift at all. Literals cannot.
        The day a judge route moves off Luna or off effort none, this file goes
        red and somebody re-measures the calibration figure instead of inheriting
        it.
        """
        from app.services.eval_service import METRIC_KEYS

        rows = self._rows(monkeypatch)

        for metric in METRIC_KEYS:
            identity = json.loads(rows[metric]["judge_identity"])
            assert identity["model"] == "gpt-5.6-luna", (
                f"the {metric} row records model {identity['model']!r}"
            )
            assert identity["reasoning_effort"] == "none", (
                f"the {metric} row records effort {identity['reasoning_effort']!r}, "
                "and the $0.62 per thousand floor holds only at effort none"
            )

    def test_the_prompt_version_names_the_artifact_the_prompt_ships_in(
        self, monkeypatch
    ):
        """No judge prompt in this repo carries a version, so the ragas
        distribution the four prompts live inside is the identifier."""
        import importlib.metadata

        from app.services.eval_service import METRIC_KEYS

        rows = self._rows(monkeypatch)
        expected = f"ragas-{importlib.metadata.version('ragas')}"

        for metric in METRIC_KEYS:
            identity = json.loads(rows[metric]["judge_identity"])
            assert identity["prompt_version"] == expected

    def test_a_route_with_no_effort_writes_no_judge_rather_than_a_partial_one(
        self, monkeypatch
    ):
        """The fail-open path, observed rather than assumed.

        Decision #34 priced the Judge floor at effort `none`, and every judge
        route carries it today, so this branch is unreachable from the shipped
        table. It exists because a route that dropped the effort would leave the
        identity a field short, and two efforts filed under one key average two
        populations. Writing NULL says the Judge is unknown, which is what it
        would be, and it costs no scored run: the score, the verdict and the
        threshold all still land.
        """
        from app.core.model_client import ModelRoute
        from app.services import eval_service

        monkeypatch.setattr(
            eval_service,
            "route_for",
            lambda _purpose: ModelRoute("openai", "gpt-5.6-luna"),
        )

        assert eval_service.judge_identity_for("faithfulness") is None

        row = self._rows(monkeypatch)["faithfulness"]
        assert row["judge_identity"] is None
        assert row["score"] == 0.95, "the score was lost along with the identity"
        assert row["binary_verdict"] is True, "the verdict was lost with the identity"

    # -- the verdict and its gate -----------------------------------------

    def test_a_gated_metric_carries_its_threshold_and_its_verdict(self, monkeypatch):
        from app.core.config import settings

        rows = self._rows(monkeypatch)

        assert rows["faithfulness"]["threshold"] == settings.EVAL_FAITHFULNESS_THRESHOLD
        assert rows["faithfulness"]["binary_verdict"] is True
        assert rows["answer_relevancy"]["threshold"] == settings.EVAL_RELEVANCY_THRESHOLD
        assert rows["answer_relevancy"]["binary_verdict"] is False, (
            "0.8 is below the 0.9 relevancy gate"
        )

    def test_an_ungated_metric_carries_neither_a_threshold_nor_a_verdict(
        self, monkeypatch
    ):
        """context_precision and context_recall have no setting anywhere.

        NULL on both columns, never a borrowed threshold and never False. A
        reader aggregating verdicts would otherwise count two extra failures on
        every scenario in the table.
        """
        rows = self._rows(monkeypatch)

        for metric in ("context_precision", "context_recall"):
            assert rows[metric]["threshold"] is None, f"{metric} was given a gate"
            assert rows[metric]["binary_verdict"] is None, f"{metric} was given a verdict"

    def test_an_unscored_metric_is_a_row_with_no_score_and_no_verdict(
        self, monkeypatch
    ):
        """The judge returned nothing for one dimension. The row still exists.

        Dropping it would make an unscored dimension indistinguishable from a
        scenario nobody sent, and `summarise_run_validity`'s per-metric
        observation counts would lose the denominator rather than show the hole.
        """
        rows = self._rows(monkeypatch, score={
            "scenario_id": "s1", "faithfulness": None, "answer_relevancy": 0.95,
            "context_precision": None, "context_recall": None,
        })

        assert len(rows) == 4, "an unscored metric lost its row"
        assert rows["faithfulness"]["score"] is None
        assert rows["faithfulness"]["binary_verdict"] is None, (
            "an unscored gated metric read as a failed one"
        )
        assert rows["faithfulness"]["threshold"] is not None, (
            "the gate the metric WOULD have been judged against is still recorded"
        )
        assert rows["answer_relevancy"]["binary_verdict"] is True

    def test_the_verdict_is_the_comparison_and_not_a_copy_of_the_score(
        self, monkeypatch
    ):
        """Both sides of the gate, through the writer rather than the type."""
        assert self._rows(monkeypatch, score={
            "scenario_id": "s1", "faithfulness": 0.89,
        })["faithfulness"]["binary_verdict"] is False
        assert self._rows(monkeypatch, score={
            "scenario_id": "s1", "faithfulness": 0.90,
        })["faithfulness"]["binary_verdict"] is True, (
            "a score exactly on the gate must pass; the comparison is >="
        )

    # -- the ledger reference ---------------------------------------------

    def test_every_row_names_the_ledger_bucket_that_paid_for_it(self, monkeypatch):
        """The reference is (eval_run_id, ledger_purpose), per metric per run.

        The ledger cannot go per scenario: `record_model_call` mints each row's
        uuid inside itself, the hook that calls it fires under ragas' scoring
        loop and sees no scenario, and one metric call leaves several rows. So
        the row stores the bucket, and tenant migration 0023's column comment
        says that is the grain.
        """
        from app.services.eval_service import JUDGE_PURPOSE_BY_METRIC, METRIC_KEYS

        rows = self._rows(monkeypatch)

        for metric in METRIC_KEYS:
            assert rows[metric]["ledger_purpose"] == JUDGE_PURPOSE_BY_METRIC[metric]

    def test_each_metric_maps_to_a_purpose_the_routing_table_routes(self):
        """The map is built from the two tuples, not from a `judge_` prefix rule.

        `PURPOSE_ROUTES` also routes `judge_retrieval_faithfulness`, a different
        Judge in a different task, and a string rule would hand its route here.
        """
        from app.core.model_client import PURPOSE_ROUTES
        from app.services.eval_service import (
            JUDGE_PURPOSE_BY_METRIC,
            JUDGE_PURPOSES,
            METRIC_KEYS,
        )

        assert list(JUDGE_PURPOSE_BY_METRIC) == list(METRIC_KEYS)
        assert list(JUDGE_PURPOSE_BY_METRIC.values()) == list(JUDGE_PURPOSES)
        for purpose in JUDGE_PURPOSE_BY_METRIC.values():
            assert purpose in PURPOSE_ROUTES

    # -- the blob that is gone ---------------------------------------------

    def test_the_detail_blob_no_longer_carries_the_four_scores(self, monkeypatch):
        """Criterion 2's other half, pinned as an absence.

        Every row used to hold every metric of its scenario, so one number was
        stored four times and three of the four copies on any row were not that
        row's own. Nothing in the tree ever read the column; #53 was to be its
        first reader, and it now has columns to group on instead.
        """
        rows = self._rows(monkeypatch)

        for metric, params in rows.items():
            assert params["detail"] is None, (
                f"the {metric} row still writes a detail blob: {params['detail']!r}"
            )

    def test_the_row_holds_only_its_own_dimensions_score(self, monkeypatch):
        """The four scores were different. Each row carries exactly one of them."""
        rows = self._rows(monkeypatch)

        assert rows["faithfulness"]["score"] == 0.95
        assert rows["answer_relevancy"]["score"] == 0.8
        assert rows["context_precision"]["score"] == 0.7
        assert rows["context_recall"]["score"] == 0.6

    def test_the_writer_inserts_one_row_per_record_and_no_more(self, monkeypatch):
        from app.services import eval_service
        from app.services.eval_service import METRIC_KEYS

        cursor = _RecordingCursor()
        connect, _conn = _recording_connect(cursor, [])
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)

        records = eval_service.build_judge_records([
            {"scenario_id": "s1", "faithfulness": 0.9},
            {"scenario_id": "s2", "faithfulness": 0.5},
        ])
        eval_service.write_eval_results("run-1", records, "postgresql://production")

        assert len(records) == 2 * len(METRIC_KEYS)
        assert len(cursor.executed) == len(records)
        assert {params["scenario_id"] for _sql, params in cursor.executed} == {"s1", "s2"}


class TestTheThresholdIsDefinedOnce:
    """`threshold_for` is the one place the gate is named (#51 slice 2)."""

    def test_only_the_two_gated_metrics_have_a_threshold(self):
        from app.core.config import settings
        from app.services.eval_service import threshold_for

        assert threshold_for("faithfulness") == settings.EVAL_FAITHFULNESS_THRESHOLD
        assert threshold_for("answer_relevancy") == settings.EVAL_RELEVANCY_THRESHOLD
        assert threshold_for("context_precision") is None
        assert threshold_for("context_recall") is None

    def test_the_route_reads_the_same_gate_the_writer_stored(self):
        """One definition, so a scenario's rendered verdict and its stored one agree.

        `api/v1/evals.py` named the two settings itself until this slice. Slice 3
        drops that recomputation and reads the stored verdict; until then the two
        at least come from one function.
        """
        from app.api.v1.evals import GATED_METRIC_KEYS
        from app.services.eval_service import threshold_for

        for metric in GATED_METRIC_KEYS:
            assert threshold_for(metric) is not None, (
                f"{metric} is gated by the route and has no threshold to gate on"
            )

    def test_a_metric_with_no_setting_gets_none_rather_than_a_default(self):
        """An unknown metric name is ungated, not gated at some fallback."""
        from app.services.eval_service import threshold_for

        assert threshold_for("some_metric_nobody_defined") is None


class TestBuildJudgeRecords:
    """The pairing of scored scenarios to judge rows, before any database."""

    def test_four_records_per_scenario_in_metric_order(self):
        from app.services.eval_service import METRIC_KEYS, build_judge_records

        records = build_judge_records([{"scenario_id": "s1", "faithfulness": 0.9}])

        assert [r.metric for r in records] == list(METRIC_KEYS)
        assert all(r.scenario_id == "s1" for r in records)

    def test_no_scores_makes_no_records(self):
        from app.services.eval_service import build_judge_records

        assert build_judge_records([]) == []

    def test_a_scenario_the_judge_scored_on_one_dimension_still_gets_four_rows(self):
        from app.services.eval_service import build_judge_records

        records = build_judge_records([{"scenario_id": "s1", "faithfulness": 0.9}])

        assert len(records) == 4
        assert [r.score for r in records] == [0.9, None, None, None]



# ---------------------------------------------------------------------------
# The configuration tuple
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeDB:
    """Control-DB session double routed by the SQL text it is handed."""

    def __init__(self, prompt_row=None, agent_row=None, raise_on: str | None = None):
        self.prompt_row = prompt_row
        self.agent_row = agent_row
        self.raise_on = raise_on

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if self.raise_on and self.raise_on in sql:
            raise RuntimeError(f"control DB unavailable for {self.raise_on}")
        if "prompt_versions" in sql:
            return _FakeResult(self.prompt_row)
        if "agents" in sql:
            return _FakeResult(self.agent_row)
        return _FakeResult(None)


def _fake_sync_db(db):
    @contextmanager
    def _ctx():
        yield db

    return _ctx


@pytest.fixture
def config_env(monkeypatch):
    """Every collector wired to a working double; tests break one at a time."""
    from app.services import deployment_service, eval_service

    db = _FakeDB(prompt_row=("11111111-1111-1111-1111-111111111111",), agent_row=({},))
    monkeypatch.setattr(eval_service, "get_sync_db", _fake_sync_db(db))
    monkeypatch.setattr(
        deployment_service, "_compute_envelope_hash_sync", lambda agent_id: "env-hash"
    )
    monkeypatch.setattr(
        deployment_service,
        "_fetch_corpus_stats_sync",
        lambda agent_id, conn_str: {"chunk_count": 42},
    )
    return db


class TestBuildEvalRunConfig:
    """(prompt_version_id, model_id, retrieval_config_hash, envelope_hash,
    corpus state, embedding provider) — without it, "what changed?" has no answer."""

    def test_carries_every_named_dimension(self, config_env):
        from app.services.eval_service import build_eval_run_config

        out = build_eval_run_config("agent-1", "postgresql://production")
        config = out["config"]

        assert out["prompt_version_id"] == "11111111-1111-1111-1111-111111111111"
        for key in (
            "model_id",
            "retrieval_config_hash",
            "envelope_hash",
            "corpus_chunk_count",
            "embedding_provider",
        ):
            assert config.get(key) is not None, f"config dimension {key} is missing"
        assert config["envelope_hash"] == "env-hash"
        assert config["corpus_chunk_count"] == 42
        assert config["unavailable"] == []

    def test_config_is_json_serialisable(self, config_env):
        """It is written into a JSONB column; a non-serialisable value would
        fail at INSERT time, i.e. at 02:00 on the nightly beat."""
        from app.services.eval_service import build_eval_run_config

        out = build_eval_run_config("agent-1", "postgresql://production")
        json.dumps(out["config"])

    def test_model_id_is_the_model_that_serves_a_turn(self, config_env):
        from app.core.config import AGENT_TURN_MODEL
        from app.services.eval_service import build_eval_run_config

        out = build_eval_run_config("agent-1", "postgresql://production")
        assert out["config"]["model_id"] == AGENT_TURN_MODEL

    def test_agent_turn_model_is_not_duplicated_as_a_literal(self):
        """A second literal would attribute a score to a model that did not
        produce the turn the moment one of the two moves."""
        from app.core.config import AGENT_TURN_MODEL
        from app.worker.tasks.runtime import agent as agent_module

        source = inspect.getsource(agent_module)
        assert f'"{AGENT_TURN_MODEL}"' not in source, (
            "run_agent_turn hardcodes the model id instead of using "
            "AGENT_TURN_MODEL — eval_runs.config.model_id would drift from it"
        )

    def test_judge_model_is_recorded_separately_from_the_agent_model(self, config_env):
        """A judge change moves every score without the agent changing at all,
        so the two model ids are separate keys — collapsing them into one would
        make a judge upgrade indistinguishable from an agent regression."""
        from app.core.model_client import route_for
        from app.services.eval_service import JUDGE_PURPOSES, build_eval_run_config

        route = route_for(JUDGE_PURPOSES[0])
        config = build_eval_run_config("agent-1", "postgresql://production")["config"]
        assert config["judge_model_id"] == route.model
        assert config["judge_reasoning_effort"] == route.reasoning_effort
        assert "model_id" in config and "judge_model_id" in config

    def test_absent_prompt_version_is_not_reported_as_unavailable(
        self, monkeypatch, config_env
    ):
        """"We looked and there is nothing" is a different claim from "we could
        not look" — the reader must be able to tell them apart."""
        from app.services import eval_service

        monkeypatch.setattr(
            eval_service,
            "get_sync_db",
            _fake_sync_db(_FakeDB(prompt_row=None, agent_row=({},))),
        )

        out = eval_service.build_eval_run_config("a", "postgresql://production")
        assert out["prompt_version_id"] is None
        assert "prompt_version_id" not in out["config"]["unavailable"]

    def test_unreadable_prompt_version_is_named_unavailable(
        self, monkeypatch, config_env
    ):
        from app.services import eval_service

        monkeypatch.setattr(
            eval_service,
            "get_sync_db",
            _fake_sync_db(_FakeDB(raise_on="prompt_versions", agent_row=({},))),
        )

        out = eval_service.build_eval_run_config("a", "postgresql://production")
        assert out["prompt_version_id"] is None
        assert "prompt_version_id" in out["config"]["unavailable"]

    @pytest.mark.parametrize(
        "attr,dimension",
        [
            ("_compute_envelope_hash_sync", "envelope_hash"),
            ("_fetch_corpus_stats_sync", "corpus_chunk_count"),
        ],
    )
    def test_each_collector_failure_names_itself(
        self, monkeypatch, config_env, attr, dimension
    ):
        from app.services import deployment_service, eval_service

        def _boom(*args, **kwargs):
            raise RuntimeError("collector down")

        monkeypatch.setattr(deployment_service, attr, _boom)

        out = eval_service.build_eval_run_config("a", "postgresql://production")
        assert out["config"][dimension] is None
        assert dimension in out["config"]["unavailable"]

    def test_never_raises_even_when_everything_is_down(self, monkeypatch):
        """An unattributable run is worth far more than no run at all."""
        from app.services import deployment_service, eval_service

        def _boom(*args, **kwargs):
            raise RuntimeError("everything is down")

        monkeypatch.setattr(eval_service, "get_sync_db", _boom)
        monkeypatch.setattr(deployment_service, "_compute_envelope_hash_sync", _boom)
        monkeypatch.setattr(deployment_service, "_fetch_corpus_stats_sync", _boom)

        out = eval_service.build_eval_run_config("a", "postgresql://production")

        assert out["prompt_version_id"] is None
        assert set(out["config"]["unavailable"]) == {
            "prompt_version_id",
            "retrieval_config_hash",
            "envelope_hash",
            "corpus_chunk_count",
        }

    def test_retrieval_config_hash_ignores_key_order(self, monkeypatch, config_env):
        """Key order and whitespace must never vary the digest, or every run
        would look like a configuration change."""
        from app.services import eval_service

        strategy_a = {"vector_k": 10, "final_k": 3}
        strategy_b = {"final_k": 3, "vector_k": 10}

        hashes = []
        for strategy in (strategy_a, strategy_b):
            monkeypatch.setattr(
                eval_service,
                "get_sync_db",
                _fake_sync_db(_FakeDB(prompt_row=None, agent_row=(strategy,))),
            )
            hashes.append(
                eval_service.build_eval_run_config("a", "postgresql://p")["config"][
                    "retrieval_config_hash"
                ]
            )

        assert hashes[0] == hashes[1]

    def test_retrieval_config_hash_changes_when_the_strategy_changes(
        self, monkeypatch, config_env
    ):
        from app.services import eval_service

        hashes = []
        for strategy in ({"vector_k": 10}, {"vector_k": 11}):
            monkeypatch.setattr(
                eval_service,
                "get_sync_db",
                _fake_sync_db(_FakeDB(prompt_row=None, agent_row=(strategy,))),
            )
            hashes.append(
                eval_service.build_eval_run_config("a", "postgresql://p")["config"][
                    "retrieval_config_hash"
                ]
            )

        assert hashes[0] != hashes[1], (
            "two different retrieval strategies produced the same hash — runs "
            "differing on retrieval would compare as identical"
        )

    def test_default_and_absent_strategy_hash_identically(self, monkeypatch, config_env):
        """An agent with no stored strategy behaves exactly like one storing the
        defaults, so they must not appear to differ."""
        from app.services import eval_service

        hashes = []
        for strategy in (None, {}):
            monkeypatch.setattr(
                eval_service,
                "get_sync_db",
                _fake_sync_db(_FakeDB(prompt_row=None, agent_row=(strategy,))),
            )
            hashes.append(
                eval_service.build_eval_run_config("a", "postgresql://p")["config"][
                    "retrieval_config_hash"
                ]
            )

        assert hashes[0] == hashes[1]

    def test_config_at_insert_says_the_agent_has_not_been_invoked_yet(
        self, config_env
    ):
        """The tuple certifies a configuration; this key says whether the run
        exercised it. Without it the tuple makes an uncomparable measurement
        look comparable.

        At INSERT the honest answer is always "not yet": the eval_runs row is
        also the per-agent idempotency key, so it has to exist before the first
        of sixty SDK turns. A run that dies in between keeps this False and is
        refused by the deploy gate — the fail-closed direction.
        """
        from app.services import eval_service

        config = eval_service.build_eval_run_config("a", "postgresql://p")["config"]

        assert config["agent_invoked"] is False
        assert config["scored_response_source"] == "pending_invocation"
        assert config["agent_invocation"]["status"] == "not_started"

    def test_config_names_every_dimension_the_score_cannot_see(self, config_env):
        """The failing input: change AGENT_TURN_MODEL and run two nightly evals.

        Two eval_runs rows then differ on exactly one recorded dimension
        (config.model_id) and carry statistically identical scores, because the
        metric is invariant to the model while the agent is never invoked. The
        reader this tuple was built for concludes the model swap is
        quality-neutral and ships it. Naming the dimensions is what makes that
        conclusion unavailable.
        """
        from app.services import eval_service

        config = eval_service.build_eval_run_config("a", "postgresql://p")["config"]
        excluded = set(config["dimensions_not_exercised"])

        for dimension in (
            "prompt_version_id",
            "model_id",
            "retrieval_config_hash",
            "envelope_hash",
            "corpus_chunk_count",
        ):
            assert dimension in excluded, (
                f"config records {dimension} without recording that the score "
                "is invariant to it"
            )

    def test_the_judge_model_is_not_excluded_because_the_judge_does_run(
        self, config_env
    ):
        """The exclusion list is a claim about this harness, not a blanket
        disclaimer. A judge change really does move every score."""
        from app.services import eval_service

        config = eval_service.build_eval_run_config("a", "postgresql://p")["config"]
        assert "judge_model_id" not in config["dimensions_not_exercised"]

    def test_nothing_is_excluded_once_a_run_measured_an_invoked_agent(
        self, config_env
    ):
        """The exclusion is derived from the RUN's observation, not from a
        module constant — so a run that measured the agent clears it, and a run
        that did not keeps it, without anyone editing a disclaimer."""
        from app.services import eval_service

        config = eval_service.build_eval_run_config(
            "a",
            "postgresql://p",
            agent_invocation={
                "status": eval_service.AGENT_INVOCATION_MEASURED,
                "attempted": 10,
                "responded": 10,
                "scorable": 10,
            },
        )["config"]

        assert config["agent_invoked"] is True
        assert config["scored_response_source"] == "agent_response"
        assert config["dimensions_not_exercised"] == []

    def test_a_run_below_the_floor_is_not_certified_even_though_it_invoked(
        self, config_env
    ):
        """THE CONJUNCTION. `agent_invoked` is the gate-facing claim and it
        means BOTH "the agent produced the scored responses" and "enough of
        them did to be a measurement".

        The failing input it exists for: sixty scenarios, six responses, the
        rest timing out on a degraded Agent SDK. Every score in that run is
        real, and six of sixty is not a measurement of the agent's quality. A
        gate reading a bare "we called it" would ship on it — missing data
        treated as passing data, which is the rule this repo wrote down after
        the last time.
        """
        from app.services import eval_service

        config = eval_service.build_eval_run_config(
            "a",
            "postgresql://p",
            agent_invocation={
                "status": eval_service.AGENT_INVOCATION_UNKNOWN,
                "attempted": 60,
                "responded": 6,
                "scorable": 6,
            },
        )["config"]

        assert config["agent_invoked"] is False, (
            "a run where 6 of 60 scenarios answered certified itself as having "
            "measured the agent"
        )
        # …and it still says the scored rows came from the agent, because they
        # did. The two claims are separable and stay separate.
        assert config["scored_response_source"] == "agent_response"
        assert config["agent_invocation"]["responded"] == 6

    def test_the_provenance_is_derived_once_not_twice(self):
        """build_eval_run_config and the task's post-run patch must not each
        decide what `agent_invoked` means.

        Two derivations are two chances to disagree, and the one that disagreed
        would be the one the deploy gate reads. Both go through
        invocation_provenance, and this asserts the config keys are exactly its
        output rather than a re-spelling of it.
        """
        from app.services import eval_service

        observation = {
            "status": eval_service.AGENT_INVOCATION_MEASURED,
            "attempted": 4,
            "responded": 4,
            "scorable": 4,
        }
        provenance = eval_service.invocation_provenance(observation)

        assert set(provenance) == {
            "agent_invoked",
            "scored_response_source",
            "dimensions_not_exercised",
            "agent_invocation",
        }
        assert provenance["agent_invoked"] is True
        assert provenance["agent_invocation"] == observation
        assert provenance["agent_invocation"] is not observation, (
            "the provenance hands out the caller's own dict — a later mutation "
            "of the summary would silently rewrite what the run recorded"
        )

    def test_promotion_decision_is_copied_not_shared(self, config_env):
        """A caller mutating the returned config must not poison the constant."""
        from app.services import eval_service

        out = eval_service.build_eval_run_config("a", "postgresql://p")
        out["config"]["verified_qa_promotion"]["enabled"] = True

        assert eval_service.VERIFIED_QA_PROMOTION_DECISION["enabled"] is False

    def test_the_whole_decision_reaches_the_run_record_not_just_the_flag(
        self, config_env
    ):
        """D6 P3 review, finding 10 — half of "the disablement is recorded WITH
        ITS REASON" was unpinned.

        Three tests touched this and none of them covered it: this class's
        sibling above exercises the real `build_eval_run_config` but asserts only
        `enabled`; D6 P3's flatness test asserts the CONSTANT's key set, not the
        config's; and test_label_downstream.py monkeypatches
        `build_eval_run_config` wholesale, so it proves nothing about what a run
        records. A future edit narrowing the recorded dict to
        `{"enabled": ...}` — a plausible way to shrink the config payload —
        would leave every test in the tree green while runs stamped the flag with
        no reason, no scope and no decision date. Which is exactly the absence
        P3 was written to replace, restored silently.

        Set EQUALITY, not "the keys I remembered": a key added to the constant
        and dropped on the way to the record fails here too.
        """
        from app.services import eval_service

        out = eval_service.build_eval_run_config("a", "postgresql://p")
        recorded = out["config"]["verified_qa_promotion"]

        assert set(recorded) == set(eval_service.VERIFIED_QA_PROMOTION_DECISION), (
            "the run record no longer carries the whole promotion decision: "
            f"recorded {sorted(recorded)}, constant "
            f"{sorted(eval_service.VERIFIED_QA_PROMOTION_DECISION)}"
        )
        for key, value in eval_service.VERIFIED_QA_PROMOTION_DECISION.items():
            assert recorded[key] == value, (
                f"{key} was altered between the constant and the run record"
            )
        # The two that carry the meaning, named so a narrowing edit that keeps
        # the key but empties the value is also caught.
        assert recorded["reason"] == eval_service.VERIFIED_QA_PROMOTION_DECISION["reason"]
        assert "2026-08-08" in recorded["reason"]
        assert recorded["scope"] == "eval_only"
        assert recorded["decided_on"] == "2026-08-08"
        assert recorded["refusal_reason"] == eval_service.PROMOTION_DISABLED_REFUSAL


class TestInsertEvalRun:
    """Tenant DBs are migrated at provision time only — 0013 may not be there."""

    def _connect(self, monkeypatch, cursor):
        from app.services import eval_service

        conn = MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(eval_service.psycopg2, "connect", lambda *a, **kw: conn)
        return conn

    def test_wide_insert_records_the_configuration_tuple(self, monkeypatch):
        from app.services.eval_service import insert_eval_run

        cursor = _RecordingCursor()
        self._connect(monkeypatch, cursor)

        recorded = insert_eval_run(
            "run-1", "m6:agent-1", "pv-1", {"model_id": "m"}, "postgresql://production"
        )

        assert recorded is True
        assert "prompt_version_id" in cursor.statements
        assert "config" in cursor.statements
        params = cursor.executed[0][1]
        assert params["prompt_version_id"] == "pv-1"
        assert json.loads(params["config"]) == {"model_id": "m"}

    def test_falls_back_to_the_pre_0013_shape_and_says_so(self, monkeypatch):
        """A tenant a migration behind must still be able to run an eval —
        losing attribution is far better than "no eval can start at all"."""
        from app.services.eval_service import insert_eval_run

        cursor = _RecordingCursor(
            raise_on="prompt_version_id",
            exc=psycopg2.errors.UndefinedColumn("column does not exist"),
        )
        conn = self._connect(monkeypatch, cursor)

        recorded = insert_eval_run(
            "run-1", "m6:agent-1", "pv-1", {"model_id": "m"}, "postgresql://production"
        )

        assert recorded is False, (
            "a run inserted without its configuration tuple must report that, "
            "or an unattributed run is indistinguishable from an attributed one"
        )
        assert conn.rollback.called, (
            "the aborted transaction must be rolled back before the connection "
            "will accept the fallback statement"
        )
        assert len(cursor.executed) == 2
        assert "prompt_version_id" not in cursor.executed[1][0]
        assert conn.commit.called

    def test_a_real_write_failure_is_not_swallowed(self, monkeypatch):
        """The except must be narrow — UndefinedColumn only.

        The failure is injected on the WIDE insert alone, so that a widened
        `except Exception` would quietly succeed via the pre-0013 fallback and
        report the run as started with attribution merely "absent". A permission
        error, a dead connection or a constraint violation are not "this tenant
        is a migration behind", and must reach the caller's retry.
        """
        from app.services.eval_service import insert_eval_run

        cursor = _RecordingCursor(
            raise_on="prompt_version_id",
            exc=psycopg2.errors.InsufficientPrivilege("permission denied"),
        )
        conn = self._connect(monkeypatch, cursor)

        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            insert_eval_run("run-1", "m6:a", None, None, "postgresql://production")

        assert len(cursor.executed) == 1, (
            "the pre-0013 fallback ran for a non-UndefinedColumn error — a real "
            "write failure would be reported as a successfully started run"
        )
        assert conn.close.called, "the connection must be closed on every path"


class TestUpdateEvalRunConfig:
    """The write that turns `agent_invoked` from a hope into an observation.

    The eval_runs row has to exist before the first of sixty SDK turns — it is
    the per-agent idempotency key — so it is inserted claiming agent_invoked
    false and corrected here. Every failure mode of this function therefore
    leaves the run claiming LESS than it did, never more, and the tests below
    are about proving that direction rather than the happy path.
    """

    def _connect(self, monkeypatch, cursor):
        from app.services import eval_service

        conn = MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(eval_service.psycopg2, "connect", lambda *a, **kw: conn)
        return conn

    def test_the_patch_is_a_shallow_jsonb_merge_on_production(self, monkeypatch):
        from app.services.eval_service import update_eval_run_config

        cursor = _RecordingCursor()
        self._connect(monkeypatch, cursor)

        assert (
            update_eval_run_config(
                "run-1", {"agent_invoked": True}, "postgresql://production"
            )
            is True
        )
        sql, params = cursor.executed[0]
        assert "UPDATE eval_runs" in sql
        assert "||" in sql, (
            "the patch replaces the whole config instead of merging into it — "
            "the dataset composition and the configuration tuple written at "
            "INSERT would be destroyed by the correction"
        )
        assert json.loads(params["patch"]) == {"agent_invoked": True}
        assert params["id"] == "run-1"

    def test_a_tenant_without_the_config_column_reports_false_not_raises(
        self, monkeypatch
    ):
        """Migration 0013 added `config`; tenants are migrated at provision time
        only. A run on an older tenant cannot record that it invoked the agent,
        so it must say it could not — and the deploy gate then refuses it."""
        from app.services.eval_service import update_eval_run_config

        cursor = _RecordingCursor(
            raise_on="UPDATE eval_runs",
            exc=psycopg2.errors.UndefinedColumn("column config does not exist"),
        )
        conn = self._connect(monkeypatch, cursor)

        assert (
            update_eval_run_config("run-1", {"agent_invoked": True}, "postgresql://p")
            is False
        )
        assert conn.rollback.called
        assert conn.close.called

    def test_a_write_failure_never_fails_an_already_scored_run(self, monkeypatch):
        """Fail-closed, not fail-loud. The invocation already happened and cost
        real money; raising here would turn a lost provenance write into a lost
        run AND a Celery retry that re-invokes sixty scenarios."""
        from app.services import eval_service

        monkeypatch.setattr(
            eval_service.psycopg2,
            "connect",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("production down")),
        )

        assert (
            eval_service.update_eval_run_config(
                "run-1", {"agent_invoked": True}, "postgresql://p"
            )
            is False
        ), (
            "a failed provenance write reported success — the run would read as "
            "certified while eval_runs still says agent_invoked=false"
        )


from app.services import eval_service  # noqa: E402  (P2 — module-level access)

# ---------------------------------------------------------------------------
# P2 — datasets and denominators (pure functions, no I/O)
# ---------------------------------------------------------------------------


def _scenario(sid, dataset=None, reference_answer="A"):
    return {
        "id": sid,
        "dataset": eval_service.dataset_of(dataset),
        "reference_answer": reference_answer,
    }


def _score(sid, **metrics):
    row = {"scenario_id": sid}
    for key in eval_service.METRIC_KEYS:
        row[key] = metrics.get(key)
    return row


class TestDatasetOf:
    """Membership of the golden set is asserted, never inherited."""

    def test_only_the_exact_literal_is_golden(self):
        assert eval_service.dataset_of("golden") == eval_service.DATASET_GOLDEN

    @pytest.mark.parametrize("value", [None, "", "GOLDEN", "gold", "exploratory", "x"])
    def test_everything_else_is_exploratory(self, value):
        """Including NULL, which is the state of every row that predates
        migration 0014, and including near-misses.

        The direction matters: resolving an unrecognised value UP into the
        golden set would let a typo silently join the fixed instrument every
        future comparison rests on, and nothing downstream could tell.
        """
        assert eval_service.dataset_of(value) == eval_service.DATASET_EXPLORATORY


class TestDatasetComposition:
    """What a run is about to score, recorded before it scores it."""

    def test_counts_attempted_and_valid_per_dataset(self):
        scenarios = [
            _scenario("g1", "golden"),
            _scenario("g2", "golden", reference_answer=""),
            _scenario("e1", None),
        ]
        composition = eval_service.dataset_composition(
            scenarios, dataset_column_available=True
        )

        assert composition["attempted"] == 3
        assert composition["valid"] == 2
        assert composition["golden"] == {"attempted": 2, "valid": 1}
        assert composition["exploratory"] == {"attempted": 1, "valid": 1}
        assert composition["golden_set_present"] is True

    def test_an_unlabelled_row_is_attempted_but_not_valid(self):
        """The two counts are different claims and the gap is the whole point.

        A row with no reference answer was fetched and cannot be scored.
        Reporting only `attempted` overstates what was measured; reporting only
        `valid` hides that rows were selected and thrown away.
        """
        composition = eval_service.dataset_composition(
            [_scenario("e1", None, reference_answer="")],
            dataset_column_available=True,
        )
        assert composition["attempted"] == 1
        assert composition["valid"] == 0

    def test_no_golden_rows_and_no_dataset_column_are_different_claims(self):
        no_golden = eval_service.dataset_composition(
            [_scenario("e1", None)], dataset_column_available=True
        )
        no_column = eval_service.dataset_composition(
            [_scenario("e1", None)], dataset_column_available=False
        )

        assert no_golden["golden_set_present"] is False
        assert no_column["golden_set_present"] is False
        assert no_golden["dataset_column_available"] is True
        assert no_column["dataset_column_available"] is False
        assert no_golden != no_column, (
            "'this tenant curated nothing' and 'this tenant cannot be asked' "
            "must not serialise identically"
        )

    def test_an_oversized_golden_set_is_flagged_not_truncated(self):
        """The golden set is the run's cost and it is deliberately unsampled.

        Truncating would silently break the paired comparison it exists for, so
        the ceiling is a tripwire that reports rather than a cap that cuts.
        """
        scenarios = [
            _scenario(f"g{i}", "golden")
            for i in range(eval_service.GOLDEN_SET_SOFT_CEILING + 1)
        ]
        composition = eval_service.dataset_composition(
            scenarios, dataset_column_available=True
        )

        assert composition["golden_over_soft_ceiling"] is True
        assert composition["golden"]["attempted"] == len(scenarios), (
            "every golden row must still be counted in — a ceiling that drops "
            "rows is a sampled golden set"
        )


class TestSummariseRunValidity:
    """(attempted, valid, scored) and per-dataset metrics."""

    def test_the_three_counts_are_independent(self):
        scenarios = [
            _scenario("g1", "golden"),
            _scenario("g2", "golden"),
            _scenario("e1", None, reference_answer=""),
        ]
        scores = [_score("g1", faithfulness=0.9)]

        summary = eval_service.summarise_run_validity(scenarios, scores)

        assert summary["attempted"] == 3
        assert summary["valid"] == 2
        assert summary["scored"] == 1

    def test_zero_valid_observations_is_unknown_not_zero(self):
        """The rule this whole branch exists for.

        Every judge call returned NaN, so run_ragas_eval emitted None for all
        four metrics. Rendering that as 0.0 reports a total quality collapse;
        omitting it reports nothing wrong. `measured: False` with
        `observations: 0` is the only reading that is true.
        """
        scenarios = [_scenario("g1", "golden")]
        scores = [_score("g1")]

        summary = eval_service.summarise_run_validity(scenarios, scores)

        assert summary["scored"] == 0
        for metric in summary["datasets"]["golden"]["metrics"].values():
            assert metric == {"value": None, "measured": False, "observations": 0}

    def test_a_measured_zero_is_distinguishable_from_an_unmeasured_metric(self):
        scenarios = [_scenario("g1", "golden")]
        scores = [_score("g1", faithfulness=0.0)]

        metrics = eval_service.summarise_run_validity(scenarios, scores)["datasets"][
            "golden"
        ]["metrics"]

        assert metrics["faithfulness"] == {
            "value": 0.0,
            "measured": True,
            "observations": 1,
        }
        assert metrics["answer_relevancy"]["measured"] is False

    def test_the_two_datasets_are_summarised_separately(self):
        scenarios = [_scenario("g1", "golden"), _scenario("e1", None)]
        scores = [_score("g1", faithfulness=1.0), _score("e1", faithfulness=0.0)]

        datasets = eval_service.summarise_run_validity(scenarios, scores)["datasets"]

        assert datasets["golden"]["metrics"]["faithfulness"]["value"] == 1.0
        assert datasets["exploratory"]["metrics"]["faithfulness"]["value"] == 0.0

    def test_there_is_no_run_level_metric_to_misread(self):
        """A structural refusal, not a convention.

        A single mean over both datasets moves whenever the exploratory DRAW
        moves, so a redraw would be indistinguishable from a regression — which
        is exactly the property the fixed golden set exists to provide. The way
        to make that impossible is to never compute the number.
        """
        scenarios = [_scenario("g1", "golden"), _scenario("e1", None)]
        scores = [_score("g1", faithfulness=1.0), _score("e1", faithfulness=0.0)]

        summary = eval_service.summarise_run_validity(scenarios, scores)

        assert "metrics" not in summary
        assert set(summary) == {
            "attempted",
            "valid",
            "scored",
            "unattributed",
            "datasets",
        }

    def test_a_score_for_an_unknown_scenario_joins_neither_dataset(self):
        """An observation that cannot be attributed is not evidence about
        either measurement, and inventing a bucket for it would put an
        unattributable number inside a comparable one."""
        scenarios = [_scenario("g1", "golden")]
        scores = [_score("ghost", faithfulness=0.99)]

        summary = eval_service.summarise_run_validity(scenarios, scores)

        assert summary["scored"] == 0
        assert summary["datasets"]["golden"]["metrics"]["faithfulness"]["measured"] is (
            False
        )
        assert summary["datasets"]["exploratory"]["metrics"]["faithfulness"][
            "measured"
        ] is False

    def test_an_unattributable_score_is_counted_rather_than_vanishing(self):
        """Dropped and reported, not dropped and silent (P2 review).

        The eval-runs route used to bucket exactly this row as exploratory while
        this function dropped it, so one run had two denominators differing by
        the row: the Celery return excluded it, the API response counted it and
        let its score into the exploratory mean. Both readers now refuse to
        attribute it AND both report it, which is what makes the disagreement
        impossible rather than merely resolved in one place.
        """
        scenarios = [_scenario("g1", "golden")]
        scores = [_score("ghost", faithfulness=0.99), _score("g1", faithfulness=0.5)]

        summary = eval_service.summarise_run_validity(scenarios, scores)

        assert summary["unattributed"] == 1
        assert summary["scored"] == 1, (
            "an unattributable row must not be counted as scored for this run"
        )

    def test_an_empty_run_reports_zeros_rather_than_raising(self):
        summary = eval_service.summarise_run_validity([], [])

        assert summary == {
            "attempted": 0,
            "valid": 0,
            "scored": 0,
            "unattributed": 0,
            "datasets": {
                "golden": {
                    "attempted": 0,
                    "valid": 0,
                    "scored": 0,
                    "metrics": {
                        metric: {"value": None, "measured": False, "observations": 0}
                        for metric in eval_service.METRIC_KEYS
                    },
                },
                "exploratory": {
                    "attempted": 0,
                    "valid": 0,
                    "scored": 0,
                    "metrics": {
                        metric: {"value": None, "measured": False, "observations": 0}
                        for metric in eval_service.METRIC_KEYS
                    },
                },
            },
        }

    def test_the_mean_is_over_observed_values_only(self):
        """A dataset of three rows where one metric was observed twice reports
        the mean of the two, with observations=2 beside it — never a mean over
        three that silently treated the missing one as zero."""
        scenarios = [
            _scenario("g1", "golden"),
            _scenario("g2", "golden"),
            _scenario("g3", "golden"),
        ]
        scores = [
            _score("g1", faithfulness=1.0),
            _score("g2", faithfulness=0.0),
            _score("g3"),
        ]

        metric = eval_service.summarise_run_validity(scenarios, scores)["datasets"][
            "golden"
        ]["metrics"]["faithfulness"]

        assert metric == {"value": 0.5, "measured": True, "observations": 2}


class TestConfigCarriesTheDataset:
    """config["dataset"] is what makes two golden scores comparable."""

    def test_the_composition_lands_on_the_run(self, config_env):
        composition = eval_service.dataset_composition(
            [_scenario("g1", "golden")], dataset_column_available=True
        )
        result = eval_service.build_eval_run_config(
            "agent-1", "postgresql://production", dataset=composition
        )

        assert result["config"]["dataset"] == composition

    def test_no_composition_is_null_not_an_empty_one(self, config_env):
        """'This run did not record its dataset' is not 'this run scored no
        rows'. P1's unavailable/absent distinction, applied to the new key."""
        result = eval_service.build_eval_run_config("agent-1", "postgresql://production")

        assert result["config"]["dataset"] is None


# ---------------------------------------------------------------------------
# P2 review — which scenario a returned score is actually about
# ---------------------------------------------------------------------------


class TestAttributeReturnedRows:
    """attribute_returned_rows — pure, and the whole defect in one function.

    run_ragas_eval walked the returned dataframe with `enumerate` and handed row
    i to valid_scenarios[i]. That is correct only when the judge returns exactly
    as many rows as it was given, in order — a condition nothing checked. P2
    made it load-bearing by ordering the golden rows first, so a partial return
    assigned the surviving scores to the golden set by position.
    """

    def _scenarios(self, n):
        return [
            {"id": f"s{i}", "question": f"q{i}", "reference_answer": f"a{i}"}
            for i in range(n)
        ]

    def test_a_complete_return_is_attributed_positionally(self):
        """One row per sample IS the condition under which position is sound —
        so it is checked, not assumed, and still used when it holds."""
        scenarios = self._scenarios(3)
        keys = [eval_service.scenario_identity_key(s) for s in scenarios]

        assert eval_service.attribute_returned_rows(keys, scenarios) == [0, 1, 2]

    def test_a_partial_return_is_attributed_by_identity_not_position(self):
        """The filed failure: 1 golden + 30 exploratory sent, 5 rows back for
        the scenarios at positions 2, 7, 11, 19, 26."""
        scenarios = self._scenarios(31)
        returned = [2, 7, 11, 19, 26]
        keys = [eval_service.scenario_identity_key(scenarios[i]) for i in returned]

        assert eval_service.attribute_returned_rows(keys, scenarios) == returned

    def test_a_row_that_matches_nothing_is_unattributed(self):
        scenarios = self._scenarios(3)

        assert eval_service.attribute_returned_rows([("who?", "what?")], scenarios) == [
            None
        ]

    def test_a_row_with_no_key_at_all_is_unattributed(self):
        """A judge payload with no sample columns cannot be placed. Position
        would 'work' and be wrong, which is the failure mode, not the fix."""
        scenarios = self._scenarios(3)

        assert eval_service.attribute_returned_rows([None, None], scenarios) == [
            None,
            None,
        ]

    def test_two_identical_scenarios_are_ambiguous_rather_than_first_wins(self):
        """Same question AND same reference answer sent twice.

        They are interchangeable as inputs and NOT as identities: writing the
        wrong scenario_id into eval_results is what makes tomorrow's paired
        golden comparison a comparison against a different row.
        """
        scenarios = [
            {"id": "a", "question": "q", "reference_answer": "r"},
            {"id": "b", "question": "q", "reference_answer": "r"},
            {"id": "c", "question": "other", "reference_answer": "r"},
        ]

        assert eval_service.attribute_returned_rows([("q", "r")], scenarios) == [None]


class TestRunRagasEvalAttribution:
    """The producer, exercised in the state the existing tests mocked away.

    test_scored_is_below_valid_when_ragas_returns_fewer_rows monkeypatches
    run_ragas_eval out entirely, so the real function was never run against a
    partial judge return — the one state in which its attribution was wrong.
    """

    def _scenarios(self, n):
        return [
            {
                "id": f"s{i}",
                "question": f"q{i}",
                "reference_answer": f"a{i}",
                "retrieved_contexts": [],
                "agent_response": f"a{i}",
                "dataset": "golden" if i == 0 else "exploratory",
            }
            for i in range(n)
        ]

    def _judged(self, scenarios, indices, with_keys=True, faithfulness=None):
        """A frame of the rows the judge actually returned."""
        rows = []
        for i in indices:
            row = {
                "faithfulness": 0.1 * i if faithfulness is None else faithfulness,
                "answer_relevancy": 0.5,
                "context_precision": 0.5,
                "context_recall": 0.5,
            }
            if with_keys:
                row["user_input"] = scenarios[i]["question"]
                row["reference"] = scenarios[i]["reference_answer"]
            rows.append(row)
        return pd.DataFrame(rows)

    def _run(self, monkeypatch, scenarios, frame):
        """Real dataset, real metric objects, judge replaced at _score_samples.

        The seam is the scoring loop rather than the judge client, because a
        partial return is a shortfall of ROWS: these tests exist to drive
        attribution when the judge answers for fewer samples than were sent.
        """

        async def _fake_score_samples(metrics, samples):  # noqa: ARG001
            return frame.to_dict("records")

        monkeypatch.setattr(
            eval_service, "_build_instructor_llm", _fake_ragas_instructor_llm
        )
        monkeypatch.setattr(eval_service, "_VoyageRagasEmbedding", _FakeRagasEmbedding)
        monkeypatch.setattr(eval_service, "_score_samples", _fake_score_samples)
        return eval_service.run_ragas_eval(scenarios, ledger())

    def test_a_partial_return_does_not_hand_the_golden_row_a_foreign_score(
        self, monkeypatch
    ):
        """The filed failure, end to end through the real producer.

        31 scenarios sent (1 golden + 30 exploratory), 5 rows back for positions
        2, 7, 11, 19, 26. Positionally, s0 — the golden row — was given s2's
        score, and summarise_run_validity then reported
        datasets.golden.metrics.faithfulness as a measurement of s0, which the
        next night's run would compare against as if it were paired.
        """
        scenarios = self._scenarios(31)
        returned = [2, 7, 11, 19, 26]

        result = self._run(
            monkeypatch, scenarios, self._judged(scenarios, returned)
        )

        assert [s["scenario_id"] for s in result["scores"]] == [
            f"s{i}" for i in returned
        ]
        for row, i in zip(result["scores"], returned):
            assert row["faithfulness"] == pytest.approx(0.1 * i), (
                "a score landed on a scenario it is not about"
            )
        assert all(s["scenario_id"] != "s0" for s in result["scores"]), (
            "the golden row was never scored and must not be credited with one"
        )

        validity = eval_service.summarise_run_validity(scenarios, result["scores"])
        assert validity["datasets"]["golden"]["metrics"]["faithfulness"] == {
            "value": None,
            "measured": False,
            "observations": 0,
        }
        assert validity["scored"] == 5
        assert validity["valid"] == 31

    def test_the_run_reports_what_the_judge_did_not_return(self, monkeypatch):
        """(sent, returned) is the judge's own denominator, and `scores` alone
        cannot express it."""
        scenarios = self._scenarios(10)

        result = self._run(monkeypatch, scenarios, self._judged(scenarios, [1, 4]))

        assert result["sent"] == 10
        assert result["returned"] == 2
        assert result["unattributed"] == 0

    def test_rows_that_cannot_be_placed_are_dropped_and_counted(self, monkeypatch):
        """A partial return with no sample columns to match on.

        Every returned row is unplaceable, so the run scored nothing — the
        honest reading, and the opposite of the old behaviour, which placed all
        five on the first five scenarios with full confidence.
        """
        scenarios = self._scenarios(31)
        frame = self._judged(
            scenarios, [2, 7, 11, 19, 26], with_keys=False, faithfulness=0.95
        )

        result = self._run(monkeypatch, scenarios, frame)

        assert result["scores"] == []
        assert result["unattributed"] == 5
        assert result["means"]["faithfulness"] is None, (
            "a mean over rows the run cannot place is a mean over an unknown "
            "denominator"
        )

    def test_no_synthetic_scenario_id_is_ever_minted(self, monkeypatch):
        """The root of the two-denominators disagreement.

        A scenario carrying no id used to produce `str(uuid.uuid4())` as its
        scenario_id, so write_eval_results wrote four rows joining no
        eval_scenarios row — dropped by summarise_run_validity, counted as
        exploratory by the eval-runs route. The row cannot be produced now.
        """
        scenarios = [
            {
                "question": "q",
                "reference_answer": "a",
                "retrieved_contexts": [],
                "agent_response": "a",
            }
        ]
        frame = pd.DataFrame(
            [
                {
                    "user_input": "q",
                    "reference": "a",
                    "faithfulness": 0.99,
                    "answer_relevancy": 0.99,
                    "context_precision": 0.99,
                    "context_recall": 0.99,
                }
            ]
        )

        result = self._run(monkeypatch, scenarios, frame)

        assert result["scores"] == []
        assert result["unattributed"] == 1

    def test_the_producer_no_longer_mints_an_identity_for_a_score(self):
        """Absence pin. A synthetic id is the only way an eval_results row can
        reach production with no scenario behind it."""
        body = inspect.getsource(eval_service.run_ragas_eval).split('"""', 2)[-1]
        body = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        assert "uuid" not in body, (
            "run_ragas_eval mints a scenario_id again — an unattributable score "
            "must be reported as unattributed, never given an invented identity"
        )


# ---------------------------------------------------------------------------
# The run record (#51 slice 1): build, write, read
# ---------------------------------------------------------------------------


def _validity_report(**overrides) -> dict:
    """summarise_run_validity's shape, with golden measured and exploratory not.

    The two datasets deliberately disagree. A record built from a report where
    both halves read the same would pass while the builder pooled them, which is
    the one thing the per-dataset split exists to prevent.
    """
    report = {
        "attempted": 4,
        "valid": 4,
        "scored": 2,
        "unattributed": 0,
        "datasets": {
            "golden": {
                "attempted": 2,
                "valid": 2,
                "scored": 2,
                "metrics": {
                    "faithfulness": {"value": 0.8, "measured": True, "observations": 2},
                    "answer_relevancy": {"value": 0.6, "measured": True, "observations": 2},
                    "context_precision": {"value": 0.5, "measured": True, "observations": 2},
                    "context_recall": {"value": 0.4, "measured": True, "observations": 2},
                },
            },
            "exploratory": {
                "attempted": 2,
                "valid": 2,
                "scored": 0,
                "metrics": {
                    metric: {"value": None, "measured": False, "observations": 0}
                    for metric in (
                        "faithfulness",
                        "answer_relevancy",
                        "context_precision",
                        "context_recall",
                    )
                },
            },
        },
    }
    report.update(overrides)
    return report


def _invocation_observation(**overrides) -> dict:
    """summarise_agent_invocation's shape, built by the real summariser.

    Built rather than typed, so this fixture can never hand the builder a shape
    the production summariser does not produce.
    """
    from app.services.eval_service import summarise_agent_invocation

    records = [
        {
            "scenario_id": f"s{i}",
            "responded": True,
            "scorable": True,
            "error": None,
            "retrieve_calls": 1,
            "retrieve_at_cap": False,
            "retrieve_unparsed": 0,
            "retrieved_chunks": 1,
            "side_effects": [],
            "pii_detector": None,
        }
        for i in range(4)
    ]
    observation = summarise_agent_invocation(
        records,
        valid=4,
        ceiling_skipped=0,
        ceiling_skipped_golden=0,
        per_turn_timeout_s=90,
        audit_capture_char_cap=1800,
        retrieved_context_chunk_char_cap=2000,
    )
    observation.update(overrides)
    return observation


def _built(**overrides):
    from app.services.eval_service import build_eval_result

    fields = {
        "run_id": "3f3a1c66-0000-4000-8000-000000000051",
        "agent_id": "3f3a1c66-0000-4000-8000-0000000000a9",
        "prompt_version_id": "pv-1",
        "validity": _validity_report(),
        "invocation": _invocation_observation(),
        "ledger": [],
    }
    fields.update(overrides)
    return build_eval_result(**fields)


class TestBuildEvalResult:
    """The one derivation, assembled from the two summaries the task holds."""

    def test_each_dataset_carries_the_summarisers_own_numbers(self):
        result = _built()
        golden = result.datasets["golden"]
        assert (golden.attempted, golden.valid, golden.scored) == (2, 2, 2)
        assert golden.metrics["faithfulness"].value == 0.8
        assert golden.metrics["faithfulness"].observations == 2

    def test_the_two_datasets_are_never_pooled(self):
        """A golden mean and an exploratory mean answer different questions."""
        result = _built()
        assert result.datasets["golden"].metrics["faithfulness"].measured is True
        assert result.datasets["exploratory"].metrics["faithfulness"].measured is False
        assert result.datasets["exploratory"].metrics["faithfulness"].value is None

    def test_a_metric_over_nothing_stays_unmeasured_rather_than_zero(self):
        """Criterion 4, carried from the summariser into the record unchanged."""
        exploratory = _built().payload["datasets"]["exploratory"]["metrics"]
        assert all(m == {"value": None, "measured": False, "observations": 0}
                   for m in exploratory.values()), exploratory

    def test_the_run_level_counts_are_the_summarisers_totals(self):
        result = _built()
        report = _validity_report()
        assert (result.attempted, result.valid, result.scored) == (
            report["attempted"],
            report["valid"],
            report["scored"],
        )

    def test_the_invocation_counters_come_from_the_observation(self):
        observation = _invocation_observation()
        invocation = _built(invocation=observation).invocation
        for name in ("valid", "attempted", "responded", "scorable", "failed", "empty"):
            assert getattr(invocation, name) == observation[name], name
        assert invocation.status.value == observation["status"]

    def test_the_deflection_fields_reach_the_record(self):
        """#103's three, which are how a fallen Faithfulness gets explained."""
        observation = _invocation_observation(
            responses_deflected=2,
            scored_responses_deflected=1,
            deflection_detectors={"email": 2},
        )
        invocation = _built(invocation=observation).invocation
        assert invocation.responses_deflected == 2
        assert invocation.scored_responses_deflected == 1
        assert invocation.deflection_detectors == {"email": 2}

    def test_an_empty_ledger_reads_as_an_unknown_cost(self):
        cost = _built(ledger=[]).cost
        assert cost.measured is False and cost.usd is None

    def test_the_requested_model_is_the_one_the_routing_table_names(self):
        from app.core.config import AGENT_TURN_MODEL

        assert _built().requested_model == AGENT_TURN_MODEL

    def test_the_context_proxy_version_is_stamped(self):
        """#84. Two runs on different proxies are not comparable."""
        from app.domain.eval_result import CONTEXT_PROXY_VERSION

        assert _built().context_proxy_version == CONTEXT_PROXY_VERSION

    def test_the_judge_identity_is_the_one_the_four_routes_agree_on(self):
        from app.services.eval_service import judge_identity_for

        assert _built().judge_identity == judge_identity_for("faithfulness")


class TestServedAgentModel:
    """What actually served the turns, when one thing did."""

    def _call(self, **overrides):
        from app.domain.model_call import ModelCall

        fields = {
            "purpose": "agent_turn",
            "provider": "openai",
            "requested_model": "gpt-5.6-luna",
            "served_model": "gpt-5.6-luna-2026-08",
            "model_source": "reported",
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "at": datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
            "tenant_id": "t-1",
            "job_id": "run-1",
        }
        fields.update(overrides)
        return ModelCall(**fields)

    def test_one_agreeing_model_is_reported(self):
        from app.services.eval_service import served_agent_model

        assert served_agent_model([self._call(), self._call()]) == "gpt-5.6-luna-2026-08"

    def test_a_ledger_of_judge_calls_alone_names_no_served_agent_model(self):
        """The judges' served model is a different claim about a different call."""
        from app.services.eval_service import served_agent_model

        assert served_agent_model([self._call(purpose="judge_faithfulness")]) is None

    def test_two_disagreeing_models_report_neither(self):
        """A run served by two models has no single served model."""
        from app.services.eval_service import served_agent_model

        pair = [self._call(), self._call(served_model="gpt-5.6-luna-2026-09")]
        assert served_agent_model(pair) is None

    def test_an_empty_ledger_names_no_model(self):
        from app.services.eval_service import served_agent_model

        assert served_agent_model([]) is None


class TestWriteAndReadEvalResult:
    """The column write, and the read that refuses to invent a measurement."""

    def _connect(self, monkeypatch, cursor, conn_strings=None):
        from app.services import eval_service

        conn = MagicMock()
        conn.cursor.return_value = cursor
        connect, _ = _recording_connect(cursor, conn_strings if conn_strings is not None else [])
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)
        return conn

    def test_the_record_is_written_to_production_as_jsonb(self, monkeypatch):
        from app.services.eval_service import write_eval_result

        cursor = _RecordingCursor()
        conn_strings: list[str] = []
        self._connect(monkeypatch, cursor, conn_strings)
        result = _built()

        assert write_eval_result("run-1", result, "postgresql://production") is True
        sql, params = cursor.executed[0]
        assert "UPDATE eval_runs" in sql and "result" in sql
        assert json.loads(params["result"]) == result.payload
        assert conn_strings == ["postgresql://production"], (
            "the record is an observation about a run and belongs on production, "
            "never on the eval branch the run deletes"
        )

    def test_a_tenant_without_the_column_reports_false_rather_than_raising(
        self, monkeypatch
    ):
        """Migration 0022 arrives at provision time. A pre-0022 run still scores."""
        from app.services.eval_service import write_eval_result

        cursor = _RecordingCursor(
            raise_on="UPDATE eval_runs",
            exc=psycopg2.errors.UndefinedColumn("column result does not exist"),
        )
        self._connect(monkeypatch, cursor)

        assert write_eval_result("run-1", _built(), "postgresql://production") is False

    def test_a_failed_write_reports_false_rather_than_failing_a_scored_run(
        self, monkeypatch
    ):
        from app.services import eval_service

        def _boom(*args, **kwargs):
            raise psycopg2.OperationalError("connection refused")

        monkeypatch.setattr(eval_service.psycopg2, "connect", _boom)
        assert eval_service.write_eval_result(
            "run-1", _built(), "postgresql://production"
        ) is False

    def test_a_stored_record_round_trips_back_through_the_reader(self, monkeypatch):
        from app.services.eval_service import read_eval_result

        result = _built()
        cursor = _RecordingCursor(fetchone_result=(result.payload,))
        self._connect(monkeypatch, cursor)

        assert read_eval_result("run-1", "postgresql://production") == result

    def test_a_null_column_reads_as_no_record_rather_than_as_zero(self, monkeypatch):
        from app.services.eval_service import read_eval_result

        cursor = _RecordingCursor(fetchone_result=(None,))
        self._connect(monkeypatch, cursor)

        assert read_eval_result("run-1", "postgresql://production") is None

    def test_a_run_that_does_not_exist_reads_as_no_record(self, monkeypatch):
        from app.services.eval_service import read_eval_result

        cursor = _RecordingCursor(fetchone_result=None)
        self._connect(monkeypatch, cursor)

        assert read_eval_result("run-1", "postgresql://production") is None

    def test_a_tenant_without_the_column_reads_as_no_record(self, monkeypatch):
        from app.services.eval_service import read_eval_result

        cursor = _RecordingCursor(
            raise_on="SELECT result",
            exc=psycopg2.errors.UndefinedColumn("column result does not exist"),
        )
        self._connect(monkeypatch, cursor)

        assert read_eval_result("run-1", "postgresql://production") is None

    def test_a_stored_payload_that_breaks_a_rule_reads_as_unmeasured(self, monkeypatch):
        """Already being written down is not evidence that a shape is honest."""
        from app.services.eval_service import read_eval_result

        payload = _built().payload
        payload["datasets"]["exploratory"]["metrics"]["faithfulness"]["measured"] = True
        cursor = _RecordingCursor(fetchone_result=(payload,))
        self._connect(monkeypatch, cursor)

        assert read_eval_result("run-1", "postgresql://production") is None


class TestReadRunLedger:
    """The run's own `model_calls` rows, and what an unreadable ledger means."""

    def test_the_rows_come_back_as_records_keyed_by_the_writers_columns(
        self, monkeypatch
    ):
        from app.core.model_client import _COLUMNS
        from app.services import eval_service

        row = (
            "judge_faithfulness", "openai", "gpt-5.6-luna", "gpt-5.6-luna",
            "reported", 100, 20, 0, 0,
            datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
            "t-1", "a-1", "run-1",
        )
        assert len(row) == len(_COLUMNS)
        cursor = _RecordingCursor()
        cursor.fetchall = lambda: [row]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(eval_service.psycopg2, "connect", lambda *a, **kw: conn)

        calls = eval_service.read_run_ledger("run-1", "postgresql://production")
        assert len(calls) == 1
        assert calls[0].purpose == "judge_faithfulness"
        assert calls[0].input_tokens == 100

    def test_a_ledger_that_cannot_be_read_leaves_the_cost_unknown(self, monkeypatch):
        """A scored run must not fail because its bill could not be added up."""
        from app.services import eval_service

        def _boom(*args, **kwargs):
            raise psycopg2.OperationalError("connection refused")

        monkeypatch.setattr(eval_service.psycopg2, "connect", _boom)
        assert eval_service.read_run_ledger("run-1", "postgresql://production") == []
