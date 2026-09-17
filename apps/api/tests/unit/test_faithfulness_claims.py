"""A faithfulness row carries the claims its score was decided over (#290, step 1).

Four hops, each pinned here:

  1. `FaithfulnessWithClaims` scores exactly what ragas' `Faithfulness` scores,
     over the same two judge calls, and keeps each statement and its verdict
  2. `_score_samples` puts the claims on the score row beside the score, and
     `_placed_score_rows` carries them through attribution
  3. `build_judge_records` puts them on the faithfulness record and on no other
  4. `write_eval_results` writes them to the `claims` column 0031 added, and on a
     tenant behind 0031 drops to the 0023 INSERT, warning what the row loses

The judge is the canned `_FakeInstructorLLM` from test_eval_service: a real
`InstructorBaseRagasLLM` with the network hop replaced, which is what the
collections metrics accept at construction.
"""

from __future__ import annotations

import asyncio
import json
import math

import psycopg2
import pytest
import structlog
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import Faithfulness
from ragas.metrics.collections.faithfulness.util import (
    NLIStatementOutput,
    StatementFaithfulnessAnswer,
    StatementGeneratorOutput,
)
from ragas.metrics.result import MetricResult

from app.domain.judge_record import Claim, JudgeRecord
from app.services import eval_service as es
from app.services.faithfulness_metric import FaithfulnessWithClaims, claims_of
from tests.unit.test_eval_service import _FakeInstructorLLM

# ---------------------------------------------------------------------------
# A judge that finds seven of eight claims: the planted-lie shape (#290).
# ---------------------------------------------------------------------------
_STATEMENTS = [f"claim {i}" for i in range(8)]
_VERDICTS = [1] * 7 + [0]


class _SevenOfEightJudge(InstructorBaseRagasLLM):
    verdicts = _VERDICTS

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, prompt, response_model):
        raise AssertionError("collections metrics must reach the LLM through agenerate()")

    async def agenerate(self, prompt, response_model):
        self.calls.append(response_model.__name__)
        if response_model is StatementGeneratorOutput:
            return StatementGeneratorOutput(statements=list(_STATEMENTS))
        if response_model is NLIStatementOutput:
            return NLIStatementOutput(
                statements=[
                    StatementFaithfulnessAnswer(
                        statement=s, reason=f"reason {s}", verdict=v
                    )
                    for s, v in zip(_STATEMENTS, self.verdicts, strict=True)
                ]
            )
        raise AssertionError(f"unexpected response model {response_model.__name__}")


class _AllSupportedJudge(_SevenOfEightJudge):
    verdicts = [1] * 8


class _NoneSupportedJudge(_SevenOfEightJudge):
    verdicts = [0] * 8


class _SilentJudge(_FakeInstructorLLM):
    async def agenerate(self, prompt, response_model):
        if response_model is StatementGeneratorOutput:
            return StatementGeneratorOutput(statements=[])
        raise AssertionError("no verdict call over no statements")


class _Sample:
    user_input = "How do returns work?"
    reference = "Thirty days."
    response = " ".join(_STATEMENTS)
    retrieved_contexts = ["Returns take 30 days."]


def _ascore(metric):
    return asyncio.run(
        metric.ascore(
            user_input=_Sample.user_input,
            response=_Sample.response,
            retrieved_contexts=_Sample.retrieved_contexts,
        )
    )


class TestTheSubclassScoresWhatRagasScores:
    @pytest.mark.parametrize(
        ("judge", "expected"),
        [(_SevenOfEightJudge, 0.875), (_AllSupportedJudge, 1.0), (_NoneSupportedJudge, 0.0)],
    )
    def test_the_score_is_the_parents_score_over_the_same_judge(self, judge, expected):
        """Three shapes, because ragas is pinned to a minor (>=0.4,<0.5) and the
        subclass re-runs the parent's steps rather than calling it."""
        ours = _ascore(FaithfulnessWithClaims(llm=judge()))
        theirs = _ascore(Faithfulness(llm=judge()))
        assert ours.value == theirs.value == expected

    def test_no_statements_is_nan_for_the_parent_too(self):
        assert math.isnan(_ascore(Faithfulness(llm=_SilentJudge())).value)

    def test_the_same_two_judge_calls_go_out_in_the_same_order(self):
        judge = _SevenOfEightJudge()
        _ascore(FaithfulnessWithClaims(llm=judge))
        assert judge.calls == ["StatementGeneratorOutput", "NLIStatementOutput"]

    def test_the_result_carries_every_statement_with_its_verdict(self):
        result = _ascore(FaithfulnessWithClaims(llm=_SevenOfEightJudge()))
        claims = claims_of(result)
        assert [c["statement"] for c in claims] == _STATEMENTS
        assert [c["supported"] for c in claims] == [True] * 7 + [False]
        assert claims[-1]["reason"] == "reason claim 7"

    def test_the_verdict_is_a_bool_not_the_zero_or_one_ragas_returns(self):
        result = _ascore(FaithfulnessWithClaims(llm=_SevenOfEightJudge()))
        assert all(isinstance(c["supported"], bool) for c in claims_of(result))

    def test_the_parents_result_carries_no_claims(self):
        """The reason the subclass exists, pinned as an absence."""
        assert claims_of(_ascore(Faithfulness(llm=_SevenOfEightJudge()))) is None

    def test_no_statements_is_nan_and_no_claims(self):
        result = _ascore(FaithfulnessWithClaims(llm=_SilentJudge()))
        assert math.isnan(result.value)
        assert claims_of(result) is None

    @pytest.mark.parametrize("missing", ["response", "user_input", "retrieved_contexts"])
    def test_a_missing_input_is_refused_with_the_parents_message(self, missing):
        kwargs = {
            "user_input": _Sample.user_input,
            "response": _Sample.response,
            "retrieved_contexts": _Sample.retrieved_contexts,
        }
        kwargs[missing] = "" if missing != "retrieved_contexts" else []
        metric = FaithfulnessWithClaims(llm=_SevenOfEightJudge())
        with pytest.raises(ValueError, match=f"{missing} is missing"):
            asyncio.run(metric.ascore(**kwargs))

    def test_a_result_with_no_traces_attribute_reads_as_no_claims(self):
        """Every test double for the other metrics returns such a result."""

        class _Bare:
            value = 1.0

        assert claims_of(_Bare()) is None


class TestTheScoreRowCarriesTheClaims:
    def test_score_samples_puts_the_claims_beside_the_score(self):
        metric = FaithfulnessWithClaims(llm=_SevenOfEightJudge())
        [row] = asyncio.run(es._score_samples([("faithfulness", metric)], [_Sample()]))
        assert row["faithfulness"] == 0.875
        assert [c["supported"] for c in row[es.CLAIMS_COLUMN]] == [True] * 7 + [False]

    def test_a_nan_score_carries_no_claims_onto_the_row(self):
        """The one guard between a NaN result with claims and a row that has
        claims beside no score, which `JudgeRecord` would then refuse."""

        class _NaNWithClaims:
            name = "faithfulness"

            async def ascore(self, **_kwargs):
                return MetricResult(
                    value=float("nan"),
                    traces={"output": {"claims": [{"statement": "s", "supported": True, "reason": "r"}]}},
                )

        [row] = asyncio.run(es._score_samples([("faithfulness", _NaNWithClaims())], [_Sample()]))
        assert row["faithfulness"] is None
        assert es.CLAIMS_COLUMN not in row

    def test_a_metric_that_raises_leaves_no_claims_and_no_score(self):
        class _Broken(_FakeInstructorLLM):
            async def agenerate(self, prompt, response_model):
                raise RuntimeError("provider down")

        metric = FaithfulnessWithClaims(llm=_Broken())
        with structlog.testing.capture_logs():
            [row] = asyncio.run(es._score_samples([("faithfulness", metric)], [_Sample()]))
        assert row["faithfulness"] is None
        assert es.CLAIMS_COLUMN not in row

    def test_placed_score_rows_carries_the_claims_and_skips_a_nan_cell(self):
        """A hand-written NaN, the value a DataFrame puts in a missing list cell.

        `run_ragas_eval` builds the frame; this test hands `_placed_score_rows`
        the two cell shapes that frame produces and checks only the type test.
        """
        claims = [{"statement": "s", "supported": True, "reason": "r"}]
        returned = [
            {"faithfulness": 1.0, es.CLAIMS_COLUMN: claims},
            {"faithfulness": None, es.CLAIMS_COLUMN: float("nan")},
        ]
        scenarios = [{"id": "s1"}, {"id": "s2"}]
        placed, unattributed = es._placed_score_rows(
            returned, [0, 1], scenarios, ["faithfulness"]
        )
        assert unattributed == 0
        assert placed[0][es.CLAIMS_COLUMN] == claims
        assert es.CLAIMS_COLUMN not in placed[1]


class TestTheRecordCarriesTheClaims:
    def test_the_faithfulness_record_carries_them_and_no_other_does(self):
        claims = [{"statement": f"claim {i}", "supported": i < 7, "reason": "r"} for i in range(8)]
        records = es.build_judge_records(
            [{"scenario_id": "s1", "faithfulness": 0.875, es.CLAIMS_COLUMN: claims}]
        )
        by_metric = {r.metric: r for r in records}
        assert by_metric["faithfulness"].claims == tuple(Claim.from_payload(c) for c in claims)
        for metric, record in by_metric.items():
            if metric != "faithfulness":
                assert record.claims is None, metric

    def test_a_row_with_no_claims_builds_a_record_with_none(self):
        records = es.build_judge_records([{"scenario_id": "s1", "faithfulness": 0.875}])
        assert all(r.claims is None for r in records)

    def test_claims_that_do_not_reproduce_the_score_are_dropped_and_the_score_kept(self):
        """The refusal degrades per cell, like a failed judge call, never the run."""
        claims = [{"statement": "s", "supported": True, "reason": "r"}]
        with structlog.testing.capture_logs() as logs:
            records = es.build_judge_records(
                [{"scenario_id": "s1", "faithfulness": 0.5, es.CLAIMS_COLUMN: claims}]
            )
        record = next(r for r in records if r.metric == "faithfulness")
        assert record.score == 0.5
        assert record.claims is None
        [dropped] = [e for e in logs if e["event"] == "build_judge_records.claims_dropped"]
        assert dropped["scenario_id"] == "s1"
        assert "some other row" in dropped["error"]

    def test_a_blank_statement_from_the_judge_drops_the_claims_not_the_run(self):
        """ragas' statement field has no min_length; an empty one is a valid response."""
        claims = [
            {"statement": "real", "supported": True, "reason": "r"},
            {"statement": "   ", "supported": True, "reason": "r"},
        ]
        with structlog.testing.capture_logs() as logs:
            records = es.build_judge_records(
                [{"scenario_id": "s1", "faithfulness": 1.0, es.CLAIMS_COLUMN: claims}]
            )
        record = next(r for r in records if r.metric == "faithfulness")
        assert record.score == 1.0 and record.binary_verdict is True
        assert record.claims is None
        assert [e["event"] for e in logs] == ["build_judge_records.claims_dropped"]

    def test_a_bad_score_on_another_metric_propagates_even_beside_real_claims(self):
        """The degrade is keyed on the metric, not on the row: a context_recall
        failure on a scenario whose faithfulness carried claims is still a failure."""
        claims = [{"statement": "s", "supported": True, "reason": "r"}]
        with structlog.testing.capture_logs() as logs, pytest.raises(ValueError):
            es.build_judge_records(
                [{"scenario_id": "s1", "faithfulness": 1.0, es.CLAIMS_COLUMN: claims, "context_recall": "bad"}]
            )
        assert not [e for e in logs if e["event"] == "build_judge_records.claims_dropped"]

    @pytest.mark.parametrize(
        "item",
        [
            {"statement": False, "supported": True, "reason": "r"},
            {"statement": "s", "supported": 1, "reason": "r"},
            {"statement": "s", "supported": True, "reason": None},
            "not a mapping",
        ],
    )
    def test_a_claim_of_the_wrong_shape_is_dropped_not_rendered(self, item):
        """A False statement never lands as the text "False", and a 1 never as True."""
        with structlog.testing.capture_logs() as logs:
            records = es.build_judge_records(
                [{"scenario_id": "s1", "faithfulness": 1.0, es.CLAIMS_COLUMN: [item]}]
            )
        record = next(r for r in records if r.metric == "faithfulness")
        assert record.score == 1.0 and record.claims is None
        assert [e["event"] for e in logs] == ["build_judge_records.claims_dropped"]

    def test_a_refusal_with_no_claims_in_play_still_propagates(self):
        """The degrade is for claims only; a bad score is the failure it always was."""
        with pytest.raises(ValueError):
            es.build_judge_records([{"scenario_id": "s1", "faithfulness": "high"}])

    def test_a_nul_and_an_unpaired_surrogate_in_claim_text_are_made_storable(self):
        """Model text over a customer answer is where #117's NUL arrived (red_team.py)."""
        claims = [{"statement": "a\x00b", "supported": True, "reason": "c\ud800d"}]
        records = es.build_judge_records(
            [{"scenario_id": "s1", "faithfulness": 1.0, es.CLAIMS_COLUMN: claims}]
        )
        [claim] = next(r for r in records if r.metric == "faithfulness").claims
        assert claim.statement == "ab"
        assert claim.reason == "c\ufffdd"
        json.dumps(claim.payload).encode("utf-8")

    def test_a_long_claim_field_is_cut_with_a_marker(self):
        long = "x" * (es.CLAIM_FIELD_CHAR_CAP + 50)
        claims = [{"statement": long, "supported": True, "reason": long}]
        records = es.build_judge_records(
            [{"scenario_id": "s1", "faithfulness": 1.0, es.CLAIMS_COLUMN: claims}]
        )
        [claim] = next(r for r in records if r.metric == "faithfulness").claims
        assert claim.statement == "x" * es.CLAIM_FIELD_CHAR_CAP + es.CLAIM_FIELD_CUT_MARKER
        assert claim.reason.endswith(es.CLAIM_FIELD_CUT_MARKER)

    def test_a_field_under_the_cap_is_kept_whole(self):
        claims = [{"statement": "short", "supported": True, "reason": "r"}]
        records = es.build_judge_records(
            [{"scenario_id": "s1", "faithfulness": 1.0, es.CLAIMS_COLUMN: claims}]
        )
        [claim] = next(r for r in records if r.metric == "faithfulness").claims
        assert claim.statement == "short"


# ---------------------------------------------------------------------------
# The writer.
# ---------------------------------------------------------------------------
_RECORD = JudgeRecord.scored(
    scenario_id="s1",
    metric="faithfulness",
    score=0.5,
    threshold=0.8,
    claims=[
        Claim(statement="a", supported=True, reason="in"),
        Claim(statement="b", supported=False, reason="out"),
    ],
)


class _Cursor:
    def __init__(self, conn) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params):
        if "claims" in statement and self.conn.behind_0031:
            raise psycopg2.errors.UndefinedColumn('column "claims" does not exist')
        self.conn.executed.append((statement, params))


class _Conn:
    def __init__(self, behind_0031: bool) -> None:
        self.behind_0031 = behind_0031
        self.executed: list[tuple[str, dict]] = []
        self.rollbacks = 0
        self.committed = False

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _write(monkeypatch, behind_0031: bool) -> tuple[_Conn, list[dict]]:
    conn = _Conn(behind_0031)
    monkeypatch.setattr(es.psycopg2, "connect", lambda *a, **kw: conn)
    with structlog.testing.capture_logs() as logs:
        es.write_eval_results("run-1", [_RECORD], "postgresql://tenant")
    return conn, logs


class TestTheWriterPutsTheClaimsOnTheColumn:
    def test_the_row_params_carry_the_claims_as_json_text(self):
        params = es._judge_row_params("run-1", _RECORD)
        assert json.loads(params["claims"]) == _RECORD.payload["claims"]

    def test_a_record_with_no_claims_writes_null(self):
        record = JudgeRecord.scored(scenario_id="s1", metric="context_recall", score=0.5, threshold=None)
        assert es._judge_row_params("run-1", record)["claims"] is None

    def test_on_a_current_tenant_the_claims_column_is_written(self, monkeypatch):
        conn, logs = _write(monkeypatch, behind_0031=False)
        [(statement, params)] = conn.executed
        assert "claims" in statement
        assert json.loads(params["claims"])[1]["supported"] is False
        assert conn.committed
        assert conn.rollbacks == 0
        assert not [e for e in logs if e["event"] == "write_eval_results.claims_column_absent"]

    def test_on_a_tenant_behind_0031_the_row_lands_without_claims_and_says_so(self, monkeypatch):
        conn, logs = _write(monkeypatch, behind_0031=True)
        [(statement, params)] = conn.executed
        assert "claims" not in statement
        assert params["score"] == 0.5
        assert conn.rollbacks == 1
        assert conn.committed
        [warning] = [e for e in logs if e["event"] == "write_eval_results.claims_column_absent"]
        assert warning["log_level"] == "warning"
        assert "0031" in warning["detail"]
