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

import inspect
import json
import os
import re
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd
import psycopg2
import pytest

# ---------------------------------------------------------------------------
# The scenario sources the SCHEMA allows, parsed from migration 0011 itself.
#
# Hardcoding this list here would let the schema and the trust table drift
# apart silently, which is the exact failure mode the trust table exists to
# prevent: an unclassified source reaching the customer-serving cache.
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

    @patch("app.services.eval_service.evaluate")
    @patch("app.services.eval_service.EvaluationDataset")
    @patch("app.services.eval_service.ContextRecall")
    @patch("app.services.eval_service.ContextPrecision")
    @patch("app.services.eval_service.AnswerRelevancy")
    @patch("app.services.eval_service.Faithfulness")
    @patch("app.services.eval_service.InstructorLLM")
    @patch("app.services.eval_service.instructor")
    @patch("app.services.eval_service.anthropic")
    def test_run_ragas_eval_builds_dataset(
        self,
        mock_anthropic,
        mock_instructor,
        mock_llm_cls,
        mock_faithfulness,
        mock_answer_relevancy,
        mock_context_precision,
        mock_context_recall,
        mock_dataset_cls,
        mock_evaluate,
    ):
        """EvaluationDataset.from_list is called with 'reference' key (D-02 LOCKED).
        evaluate() is called with a metrics list of length 4 (D-04 LOCKED).
        """
        from app.services.eval_service import run_ragas_eval

        # Build a fake DataFrame with 4 metric columns
        df = pd.DataFrame([
            {
                "faithfulness": 0.95,
                "answer_relevancy": 0.92,
                "context_precision": 0.88,
                "context_recall": 0.85,
            }
        ])

        mock_results = MagicMock()
        mock_results.to_pandas.return_value = df
        mock_evaluate.return_value = mock_results

        mock_dataset = MagicMock()
        mock_dataset_cls.from_list.return_value = mock_dataset

        # LLM wrapper mock — Ragas metric classes also mocked so they don't
        # validate the InstructorLLM type at construction time
        mock_llm_instance = MagicMock()
        mock_llm_cls.return_value = mock_llm_instance
        mock_instructor.from_anthropic.return_value = MagicMock()
        mock_anthropic.Anthropic.return_value = MagicMock()

        # Metric instances returned by the mocked constructors
        mock_faithfulness.return_value = MagicMock()
        mock_answer_relevancy.return_value = MagicMock()
        mock_context_precision.return_value = MagicMock()
        mock_context_recall.return_value = MagicMock()

        scenarios = [
            {
                "id": str(uuid.uuid4()),
                "question": "What is the return policy?",
                "reference_answer": "Items can be returned within 30 days.",
                "retrieved_contexts": ["Our return policy allows 30-day returns."],
                "agent_response": "You can return items within 30 days.",
            }
        ]

        result = run_ragas_eval(scenarios)

        # D-02 LOCKED: from_list must receive 'reference' key (not 'ground_truths')
        assert mock_dataset_cls.from_list.called, "EvaluationDataset.from_list was not called"
        call_args = mock_dataset_cls.from_list.call_args
        samples_list = call_args[0][0]  # positional first arg
        assert len(samples_list) == 1
        assert "reference" in samples_list[0], (
            "D-02 violation: EvaluationDataset.from_list sample missing 'reference' key"
        )
        assert "ground_truths" not in samples_list[0], (
            "D-02 violation: 'ground_truths' key present — must be 'reference' in Ragas 0.4.x"
        )
        assert samples_list[0]["reference"] == "Items can be returned within 30 days."

        # D-04 LOCKED: evaluate() called with 4 metrics (4 constructors each called once)
        assert mock_evaluate.called, "evaluate() was not called"
        assert mock_faithfulness.called, "Faithfulness metric not instantiated"
        assert mock_answer_relevancy.called, "AnswerRelevancy metric not instantiated"
        assert mock_context_precision.called, "ContextPrecision metric not instantiated"
        assert mock_context_recall.called, "ContextRecall metric not instantiated"

        # Return structure
        assert "scores" in result
        assert "means" in result

    def test_run_ragas_eval_empty_scenarios_returns_empty(self):
        """run_ragas_eval returns empty scores/means when no valid scenarios given.
        No mocking needed — the early-exit path never touches Ragas internals.
        """
        from app.services.eval_service import run_ragas_eval

        # Scenarios without reference_answer are filtered out — early exit
        result = run_ragas_eval(
            [{"question": "Q", "agent_response": "A", "retrieved_contexts": []}],
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
    and a test that asserted the argument was passed. The Neon branch was
    created, probed for readiness and deleted per run with no statement ever
    executing against it, and a Neon outage could abandon an eval whose every
    write targets production.
    """

    def test_scoring_takes_no_connection_string_because_it_opens_none(self):
        from app.services.eval_service import run_ragas_eval

        params = inspect.signature(run_ragas_eval).parameters
        assert list(params) == ["scenarios"], (
            f"run_ragas_eval grew a parameter it does not read: {list(params)}"
        )

    def test_scoring_issues_no_statement_against_any_database(self):
        """Source-level absence pin, paired with the constant the caller reads.

        The two must move together: the day this function opens a connection,
        EVAL_SCORING_REQUIRES_BRANCH has to become True in the same edit, or
        the task will happily score with no isolation at all.
        """
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
                f"run_ragas_eval references {forbidden!r} — if scoring now "
                "touches a database, EVAL_SCORING_REQUIRES_BRANCH must be True"
            )
        assert eval_service.EVAL_SCORING_REQUIRES_BRANCH is False


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
                [{"scenario_id": "s1", "faithfulness": 0.9}],
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

    def test_tiers_are_strictly_ordered_with_unknown_lowest(self):
        from app.services.eval_service import trust_tier_rank

        assert (
            trust_tier_rank("unknown")
            < trust_tier_rank("model_generated")
            < trust_tier_rank("customer_negative")
            < trust_tier_rank("human_verified")
            < trust_tier_rank("human_authored")
        )

    def test_unrecognised_tier_name_ranks_lowest(self):
        """An unmapped tier fails closed rather than sorting somewhere plausible."""
        from app.services.eval_service import trust_tier_rank

        assert trust_tier_rank("definitely_not_a_tier") == trust_tier_rank("unknown")
        assert trust_tier_rank("") == trust_tier_rank("unknown")

    @pytest.mark.parametrize("source", SCHEMA_ALLOWED_SOURCES)
    def test_every_schema_allowed_source_has_a_declared_trust_tier(self, source):
        """No source the CHECK constraint permits may fall through to 'unknown'.

        Falling through is safe (unknown is refused) but it is silent — the
        point of this test is that adding a source to the schema forces someone
        to decide what its label is worth.
        """
        from app.services.eval_service import scenario_trust_tier

        assert scenario_trust_tier(source) != "unknown", (
            f"eval_scenarios.source={source!r} is allowed by migration 0011's "
            "CHECK but has no entry in SCENARIO_SOURCE_TRUST_TIER"
        )

    @pytest.mark.parametrize("source", SCHEMA_ALLOWED_SOURCES)
    def test_no_schema_allowed_source_is_promotable(self, source):
        """Promotion is unreachable for every source the shipped schema allows.

        This is the P1 blocker condition stated as an executable claim: no path
        exists today by which a model-written or human-flagged-failing answer
        reaches verified_qa, which retrieval_service serves to customers.
        """
        from app.services.eval_service import is_promotable_to_verified_qa

        assert is_promotable_to_verified_qa(source) is False, (
            f"source={source!r} is promotable to the customer-serving verified_qa "
            "cache — a model-generated or negative label must never be served"
        )

    @pytest.mark.parametrize(
        "source",
        [None, "", "human_authored", "sandbox_test", "verified", "GENERATED", " mined"],
    )
    def test_unknown_and_lookalike_sources_are_not_promotable(self, source):
        """Missing, misspelled and case-variant sources all fail closed."""
        from app.services.eval_service import is_promotable_to_verified_qa

        assert is_promotable_to_verified_qa(source) is False

    def test_min_trust_tier_outranks_every_schema_allowed_source(self):
        """The gate is above the ceiling of what the schema can produce.

        Stated as a rank comparison rather than as `if False` so that promotion
        becomes reachable the moment a genuinely human-verified source exists,
        without anyone having to remember to remove a flag.
        """
        from app.services.eval_service import (
            VERIFIED_QA_MIN_TRUST_TIER,
            scenario_trust_tier,
            trust_tier_rank,
        )

        ceiling = max(
            trust_tier_rank(scenario_trust_tier(s)) for s in SCHEMA_ALLOWED_SOURCES
        )
        assert ceiling < trust_tier_rank(VERIFIED_QA_MIN_TRUST_TIER)

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
# promote_to_verified_qa — unreachable, and accounted for
# ---------------------------------------------------------------------------


def _scored(source: str, faithfulness=0.99, relevancy=0.99):
    scenario_id = str(uuid.uuid4())
    scenario = {
        "id": scenario_id,
        "source": source,
        "question": f"question for {source}",
        "reference_answer": "an answer",
        "agent_response": "an answer",
        "citations": [],
    }
    score = {
        "scenario_id": scenario_id,
        "faithfulness": faithfulness,
        "answer_relevancy": relevancy,
        "context_precision": 0.9,
        "context_recall": 0.9,
    }
    return scenario, score


class TestPromoteToVerifiedQA:
    """The promotion path that would otherwise serve a bad answer to a customer."""

    def test_perfect_scores_on_every_schema_source_promote_nothing(self, monkeypatch):
        """1.0/1.0 on every allowed source still promotes zero rows.

        The adversarial case: the old gate was score-only, so this input would
        have promoted every row. It also asserts psycopg2.connect is never
        called — "did an eval write to verified_qa?" is answerable by observing
        that it never even opened a connection.
        """
        from app.services import eval_service

        connect_calls: list[str] = []
        monkeypatch.setattr(
            eval_service.psycopg2,
            "connect",
            lambda *a, **kw: connect_calls.append(a) or MagicMock(),
        )
        monkeypatch.setattr(
            eval_service, "_get_vo", lambda: pytest.fail("embedded an unpromotable row")
        )

        pairs = [_scored(s, 1.0, 1.0) for s in SCHEMA_ALLOWED_SOURCES]
        scenarios = [p[0] for p in pairs]
        scores = [p[1] for p in pairs]

        result = eval_service.promote_to_verified_qa(
            scenarios, scores, "postgresql://production"
        )

        assert result["promoted"] == 0
        assert result["scored"] == len(SCHEMA_ALLOWED_SOURCES)
        assert connect_calls == [], (
            "promote_to_verified_qa opened a DB connection with no promotable "
            "candidate — it must not touch the tenant DB at all"
        )

    def test_every_scored_row_is_accounted_for_exactly_once(self, monkeypatch):
        """promoted + refused == scored. A rate without its denominator must
        not be constructible from this return value."""
        from app.services import eval_service

        monkeypatch.setattr(
            eval_service.psycopg2, "connect", lambda *a, **kw: MagicMock()
        )

        pairs = [_scored(s, 1.0, 1.0) for s in SCHEMA_ALLOWED_SOURCES]
        pairs += [_scored("generated", 0.1, 0.1)]
        scenarios = [p[0] for p in pairs]
        scores = [p[1] for p in pairs]
        # A score whose scenario is not in the list at all.
        scores.append({"scenario_id": str(uuid.uuid4()), "faithfulness": 1.0,
                       "answer_relevancy": 1.0})

        result = eval_service.promote_to_verified_qa(
            scenarios, scores, "postgresql://production"
        )

        assert result["scored"] == len(scores)
        assert result["promoted"] + result["refused"] == result["scored"]
        assert sum(result["refusals"].values()) == result["refused"]

    def test_orphan_score_is_refused_not_silently_dropped(self):
        """A score whose scenario cannot be found is refused with a reason.

        Promoting an answer that cannot be attributed to a question is exactly
        the failure this gate exists to prevent, so it must not be a `continue`
        that vanishes from the denominator.
        """
        from app.services.eval_service import select_promotion_candidates

        candidates, refusals = select_promotion_candidates(
            [], [{"scenario_id": "nope", "faithfulness": 1.0, "answer_relevancy": 1.0}]
        )
        assert candidates == []
        assert refusals == {"scenario_not_found": 1}

    def test_trust_tier_is_checked_before_score(self):
        """A high score may not buy a source out of its tier.

        The refusal reason must name the tier, not the threshold — otherwise a
        reader would conclude the answer merely needs to score better.
        """
        from app.services.eval_service import select_promotion_candidates

        scenario, score = _scored("generated", 1.0, 1.0)
        candidates, refusals = select_promotion_candidates([scenario], [score])

        assert candidates == []
        assert refusals == {"trust_tier:model_generated": 1}

    def test_filed_production_trace_is_refused_as_a_negative_label(self):
        """source='production' is an owner-FLAGGED FAILURE (audit D5).

        retrieval_service.verified_qa_lookup serves verified_qa rows to
        customers ahead of hybrid search at 0.93 similarity. This is the row
        that must never get there.
        """
        from app.services.eval_service import select_promotion_candidates

        scenario, score = _scored("production", 1.0, 1.0)
        _, refusals = select_promotion_candidates([scenario], [score])

        assert refusals == {"trust_tier:customer_negative": 1}

    def test_missing_metric_is_never_a_pass(self):
        """A None score is 'unknown', not 'good enough'."""
        from app.services.eval_service import _meets_score_thresholds

        assert _meets_score_thresholds({"faithfulness": None, "answer_relevancy": 1.0}) is False
        assert _meets_score_thresholds({"faithfulness": 1.0, "answer_relevancy": None}) is False
        assert _meets_score_thresholds({}) is False
        assert _meets_score_thresholds({"faithfulness": 1.0, "answer_relevancy": 1.0}) is True

    def test_machinery_still_works_once_a_promotable_tier_exists(self, monkeypatch):
        """The gate is what blocks promotion — not broken code behind it.

        Without this, "nothing is ever promoted" would be indistinguishable
        from "the promotion path is dead", and re-enabling it later would be a
        rewrite rather than a policy change. Registers a hypothetical
        human-authored source and asserts the D-22/D-23 machinery runs.
        """
        from app.services import eval_service

        monkeypatch.setitem(
            eval_service.SCENARIO_SOURCE_TRUST_TIER, "owner_written", "human_authored"
        )

        cursor = _RecordingCursor(fetchone_result=None)
        conn_strings: list[str] = []
        connect, _conn = _recording_connect(cursor, conn_strings)
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)

        vo = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.1] * 1024]
        vo.embed.return_value = embed_result
        monkeypatch.setattr(eval_service, "_get_vo", lambda: vo)

        scenario, score = _scored("owner_written", 0.95, 0.92)
        result = eval_service.promote_to_verified_qa(
            [scenario], [score], "postgresql://production"
        )

        assert result["promoted"] == 1
        assert result["refused"] == 0
        assert "INSERT INTO verified_qa" in cursor.statements
        # D-22 LOCKED: promoted_by='system' is a literal in the INSERT.
        assert "'system'" in cursor.statements
        assert conn_strings == ["postgresql://production"]

    def test_below_threshold_is_refused_even_at_a_promotable_tier(self, monkeypatch):
        """D-21's 0.90/0.90 bar still applies once the tier gate is cleared."""
        from app.services import eval_service

        monkeypatch.setitem(
            eval_service.SCENARIO_SOURCE_TRUST_TIER, "owner_written", "human_authored"
        )

        scenario, score = _scored("owner_written", 0.85, 0.95)
        candidates, refusals = eval_service.select_promotion_candidates(
            [scenario], [score]
        )
        assert candidates == []
        assert refusals == {"below_score_threshold": 1}

    def test_the_promoted_answer_is_the_label_not_the_agents_own_text(
        self, monkeypatch
    ):
        """THE GATE AND THE PAYLOAD MUST DESCRIBE THE SAME ARTIFACT (P2 review).

        `select_promotion_candidates` refuses on `scenario["source"]` — the
        provenance of the REFERENCE answer. `promote_to_verified_qa` wrote
        `scenario["agent_response"]`. Before D1/P2 those were the same string,
        because eval.py set agent_response = reference_answer, so gating on the
        source was correct by accident. After P2 agent_response is model-
        generated output whose tier is `model_generated` regardless of what the
        scenario's source says — so on the day a human_authored source exists and
        the gate opens, the row retrieval_service.verified_qa_lookup serves to a
        real customer ahead of hybrid search would be the agent's own answer.

        Which is exactly the state this test creates: a promotable tier, and an
        agent_response that differs from the label.
        """
        from app.services import eval_service

        monkeypatch.setitem(
            eval_service.SCENARIO_SOURCE_TRUST_TIER, "owner_written", "human_authored"
        )

        cursor = _RecordingCursor(fetchone_result=None)
        connect, _conn = _recording_connect(cursor, [])
        monkeypatch.setattr(eval_service.psycopg2, "connect", connect)

        vo = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.1] * 1024]
        vo.embed.return_value = embed_result
        monkeypatch.setattr(eval_service, "_get_vo", lambda: vo)

        scenario, score = _scored("owner_written", 0.95, 0.92)
        scenario["reference_answer"] = "THE HUMAN-AUTHORED ANSWER"
        scenario["agent_response"] = "WHAT THE MODEL SAID INSTEAD"

        result = eval_service.promote_to_verified_qa(
            [scenario], [score], "postgresql://production"
        )

        assert result["promoted"] == 1
        inserts = [
            params
            for sql, params in cursor.executed
            if "INSERT INTO verified_qa" in sql
        ]
        assert len(inserts) == 1
        assert inserts[0]["answer"] == "THE HUMAN-AUTHORED ANSWER", (
            f"verified_qa was given {inserts[0]['answer']!r} as the answer a "
            "customer will be served. The trust tier that admitted this row is "
            "a claim about the LABEL, not about the agent's turn."
        )
        assert inserts[0]["answer"] != scenario["agent_response"]

    def test_a_row_with_no_label_is_refused_rather_than_promoted_blank(
        self, monkeypatch
    ):
        """The tier cleared describes a string the row does not have.

        Writing `reference_answer` instead of `agent_response` closes the
        provenance hole and opens a smaller one: a promotable source whose label
        is empty would serve a blank answer. Refused with its own reason so the
        denominator still accounts for it.
        """
        from app.services import eval_service

        monkeypatch.setitem(
            eval_service.SCENARIO_SOURCE_TRUST_TIER, "owner_written", "human_authored"
        )

        scenario, score = _scored("owner_written", 0.95, 0.95)
        scenario["reference_answer"] = ""

        candidates, refusals = eval_service.select_promotion_candidates(
            [scenario], [score]
        )
        assert candidates == []
        assert refusals == {"no_promotable_answer": 1}


class TestTheSecondOrchestrator:
    """run_eval_for_agent is a second door to run_ragas_eval, and P2's guards
    all read eval.py."""

    def test_it_refuses_a_prediction_that_is_its_own_label(self):
        """D1, reinstated through the door nothing was watching.

        Every guard P2 built either reads eval.py's AST or drives eval.py's
        loop. This function takes caller-supplied scenario dicts, invokes no
        agent and hands them straight to the scorer — so a future synchronous
        "score these rows" route could pass agent_response = reference_answer
        and reproduce the tautology with the whole D1 suite still green.
        """
        from app.services import eval_service

        with pytest.raises(ValueError, match="their own label"):
            eval_service.run_eval_for_agent(
                "run-1",
                [{"id": "s1", "question": "q", "reference_answer": "a",
                  "agent_response": "a"}],
                "postgresql://production",
            )

    def test_it_refuses_a_row_with_no_prediction_at_all(self):
        """`run_ragas_eval` reads `s.get("agent_response", "")` and would score
        an empty string against the label rather than raise."""
        from app.services import eval_service

        with pytest.raises(ValueError, match="their own label"):
            eval_service.run_eval_for_agent(
                "run-1",
                [{"id": "s1", "question": "q", "reference_answer": "a"}],
                "postgresql://production",
            )

    def test_the_refusal_happens_before_a_single_judge_call_is_billed(
        self, monkeypatch
    ):
        """A refusal after scoring would cost the money it exists to protect."""
        from app.services import eval_service

        monkeypatch.setattr(
            eval_service,
            "run_ragas_eval",
            lambda *a, **kw: pytest.fail("the judge was called for a tautology"),
        )
        monkeypatch.setattr(
            eval_service,
            "update_eval_run_status",
            lambda *a, **kw: pytest.fail("the run was marked running for a tautology"),
        )

        with pytest.raises(ValueError):
            eval_service.run_eval_for_agent(
                "run-1",
                [{"id": "s1", "question": "q", "reference_answer": "a",
                  "agent_response": "a"}],
                "postgresql://production",
            )


# ---------------------------------------------------------------------------
# D2 — the persistence split
# ---------------------------------------------------------------------------


class TestPersistenceSplit:
    """Results are observations about a run; the branch is about to be deleted."""

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
            [{"scenario_id": "s1", "faithfulness": 0.9, "answer_relevancy": 0.9,
              "context_precision": 0.9, "context_recall": 0.9}],
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

    def test_run_eval_for_agent_records_on_production_and_scores_off_database(
        self, monkeypatch
    ):
        """The whole split, in one call: one connection string, production's.

        The earlier version of this test passed a second, branch connection
        string and asserted that run_ragas_eval received it. That pinned the
        passing of an argument the function never referenced — the isolation it
        looked like a proof of did not exist.
        """
        from app.services import eval_service

        seen: dict[str, list] = {"ragas": [], "results": [], "status": []}

        def _fake_ragas(*args, **kwargs):
            seen["ragas"].append((args, kwargs))
            return {"scores": [{"scenario_id": "s1"}], "means": {"faithfulness": 0.9}}

        monkeypatch.setattr(eval_service, "run_ragas_eval", _fake_ragas)
        monkeypatch.setattr(
            eval_service,
            "write_eval_results",
            lambda run_id, scores, conn_str: seen["results"].append(conn_str),
        )
        monkeypatch.setattr(
            eval_service,
            "update_eval_run_status",
            lambda run_id, status, finished_at, conn_str: seen["status"].append(
                (status, conn_str)
            ),
        )

        result = eval_service.run_eval_for_agent(
            "run-1",
            [
                {
                    "id": "s1",
                    "question": "q",
                    "reference_answer": "a",
                    # Distinct from the label, because this door refuses a
                    # tautology — see
                    # test_the_second_orchestrator_refuses_a_prediction_that_is_its_own_label.
                    "agent_response": "what the agent actually said",
                }
            ],
            "postgresql://production",
        )

        assert len(seen["ragas"]) == 1
        args, kwargs = seen["ragas"][0]
        assert len(args) == 1 and kwargs == {}, (
            "scoring was handed more than the scenarios it reads"
        )
        assert "postgresql://" not in str(args), (
            "a connection string reached run_ragas_eval, which opens nothing"
        )
        assert seen["results"] == ["postgresql://production"], (
            "eval_results were written to the branch, which the caller deletes"
        )
        assert seen["status"] == [
            ("running", "postgresql://production"),
            ("complete", "postgresql://production"),
        ]
        assert result["promoted_count"] == 0

    def test_run_eval_for_agent_marks_failed_on_production_and_reraises(
        self, monkeypatch
    ):
        from app.services import eval_service

        status: list[tuple] = []
        monkeypatch.setattr(
            eval_service,
            "update_eval_run_status",
            lambda run_id, s, finished_at, conn_str: status.append((s, conn_str)),
        )
        monkeypatch.setattr(
            eval_service,
            "run_ragas_eval",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ragas exploded")),
        )

        with pytest.raises(RuntimeError, match="ragas exploded"):
            eval_service.run_eval_for_agent("run-1", [], "postgresql://production")

        assert ("failed", "postgresql://production") in status

    def test_run_eval_for_agent_takes_no_branch_connection_string(self):
        """Absence pin on the signature.

        The parameter is what made the branch look load-bearing to every reader
        of the call site, and a caller acted on that: it abandoned whole runs
        when Neon could not produce a branch nothing reads.
        """
        from app.services import eval_service

        params = set(
            inspect.signature(eval_service.run_eval_for_agent).parameters
        )
        assert "branch_conn_str" not in params
        assert "conn_str" in params

    def test_run_eval_for_agent_does_not_promote(self):
        """Promotion is not in the sequence at all — restoring it is a decision
        in promote_to_verified_qa's gate, not a re-added call here."""
        from app.services import eval_service

        body = inspect.getsource(eval_service.run_eval_for_agent)
        code_lines = [
            ln for ln in body.splitlines()
            if "promote_to_verified_qa(" in ln and not ln.strip().startswith("#")
        ]
        assert code_lines == [], (
            f"run_eval_for_agent calls promote_to_verified_qa: {code_lines}"
        )


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
        from app.services.eval_service import HAIKU_MODEL, build_eval_run_config

        config = build_eval_run_config("agent-1", "postgresql://production")["config"]
        assert config["judge_model_id"] == HAIKU_MODEL
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

    _RAGAS_PATCHES = (
        "EvaluationDataset",
        "instructor",
        "anthropic",
        "InstructorLLM",
        "Faithfulness",
        "AnswerRelevancy",
        "ContextPrecision",
        "ContextRecall",
    )

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
        """A to_pandas() frame for the rows the judge actually returned."""
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
        class _Result:
            def to_pandas(self):
                return frame

        for name in self._RAGAS_PATCHES:
            monkeypatch.setattr(eval_service, name, MagicMock())
        monkeypatch.setattr(eval_service, "evaluate", lambda **kw: _Result())
        return eval_service.run_ragas_eval(scenarios)

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
