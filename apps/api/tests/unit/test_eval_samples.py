"""`eval_service.write_eval_samples`: the scored text lands, verbatim, once per row.

The four strings Ragas scores exist only in memory during a run unless this
function writes them, and the calibration harness (#58) labels nothing else.
The tests hold the writer to three things: one INSERT per scenario carrying the
same keys `run_ragas_eval` reads, the strings unaltered, and no connection for
an empty list.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import psycopg2
import pytest

from app.services import eval_service as es

RUN_ID = "11111111-1111-1111-1111-111111111111"


def _scenario(n: int) -> dict:
    return {
        # `id`, the key the task's scenario fetch and `_placed_score_rows` use;
        # a `scenario_id` key does not exist on a scored row.
        "id": f"S-{n:03d}",
        "dataset": "golden" if n % 2 else "exploratory",
        "question": f"Question {n}?",
        "reference_answer": f"Reference {n}.",
        "agent_response": f"Answer {n}, with 'quotes' and a % sign.",
        "retrieved_contexts": [f"chunk {n}a", f"chunk {n}b"],
        "stored_retrieved_contexts": ["never this"],
    }


@pytest.fixture
def connection(monkeypatch):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    connects: list = []
    monkeypatch.setattr(
        es.psycopg2, "connect", lambda *a, **kw: connects.append((a, kw)) or conn
    )
    return {"conn": conn, "cursor": cursor, "connects": connects}


def test_one_row_per_scenario_with_the_scored_strings_verbatim(connection):
    scenarios = [_scenario(1), _scenario(2), _scenario(3)]

    written = es.write_eval_samples(RUN_ID, scenarios, "postgresql://prod")

    assert written == 3
    calls = connection["cursor"].execute.call_args_list
    assert len(calls) == 3
    for scenario, call in zip(scenarios, calls, strict=True):
        sql, params = call.args
        assert "INSERT INTO eval_samples" in sql
        assert params["eval_run_id"] == RUN_ID
        assert params["scenario_id"] == scenario["id"]
        assert params["dataset"] == scenario["dataset"]
        assert params["user_input"] == scenario["question"]
        assert params["response"] == scenario["agent_response"]
        assert params["reference"] == scenario["reference_answer"]
        assert json.loads(params["retrieved_contexts"]) == scenario["retrieved_contexts"]
        assert "never this" not in params["retrieved_contexts"], (
            "the stored contexts are not what was scored (D1)"
        )
    connection["conn"].commit.assert_called_once()
    connection["conn"].close.assert_called_once()


def test_the_scenario_id_comes_from_the_same_key_the_results_row_uses(connection):
    """Run 7 (2026-09-08) wrote 31 rows with an empty scenario_id because the
    writer read `scenario_id` off a dict that carries `id`. The results row for
    the same scenario reads `id` (`_placed_score_rows`), and the calibration
    sheet joins the two on it.
    """
    scenario = _scenario(3)
    scenario["scenario_id"] = "WRONG-KEY"

    es.write_eval_samples(RUN_ID, [scenario], "postgresql://prod")

    [(_sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert params["scenario_id"] == "S-003"
    placed = es._placed_score_rows(
        [{"faithfulness": 0.5}], [0], [scenario], ["faithfulness"]
    )[0][0]["scenario_id"]
    assert params["scenario_id"] == placed


def test_an_empty_agent_retrieval_stays_empty(connection):
    scenario = _scenario(7)
    scenario["retrieved_contexts"] = []

    es.write_eval_samples(RUN_ID, [scenario], "postgresql://prod")

    [(_sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert json.loads(params["retrieved_contexts"]) == [], (
        "an empty agent retrieval must not fall back to the scenario's stored contexts"
    )


def test_the_conversation_the_answer_was_given_in_is_written_beside_it(connection):
    """The fifth thing on the row, and it is not scored (tenant 0028, #227).

    A follow-up question means nothing without the message that bound it. The
    owner labels these rows, so a sheet built from a run that dropped the turns
    asks a human whether an answer was relevant to a question they cannot see the
    context of.
    """
    scenario = _scenario(4)
    scenario["turns"] = [
        {"role": "user", "content": "I'm setting up Earth Elements locally."},
        {"role": "assistant", "content": "Happy to help. What do you need?"},
    ]

    es.write_eval_samples(RUN_ID, [scenario], "postgresql://prod")

    [(sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert "turns" in sql and "%(turns)s::jsonb" in sql
    assert json.loads(params["turns"]) == scenario["turns"]


def test_a_single_turn_scenario_writes_an_empty_conversation(connection):
    """`[]`, never null. The column is NOT NULL and `[]` is the honest value.

    Every row the eval scored before #227 is this row, and a writer that omitted
    the key would make those runs fail the insert rather than record what they
    were: a question asked with nothing before it.
    """
    es.write_eval_samples(RUN_ID, [_scenario(5)], "postgresql://prod")

    [(_sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert json.loads(params["turns"]) == []


class TestATenantThatPredates0028StillKeepsItsRun:
    """The write has to tolerate what the read tolerates.

    `run_eval_suite`'s scenario fetch degrades to a narrower projection on a
    tenant database behind head, so such a tenant runs its whole eval and pays for
    every agent turn. When this writer named `turns` with no fallback, the run then
    died here, on the last write before scoring, and `run_eval_suite` recorded it
    `failed` with no `eval_results` and no retry: a night of model spend for
    nothing, on a run that completed before `turns` was added to the INSERT.
    """

    @staticmethod
    def _undefined_column_on(connection, fragment: str):
        """Make the cursor raise UndefinedColumn for any statement naming *fragment*."""
        seen: list[str] = []

        def execute(sql, params=None):
            seen.append(sql)
            if fragment in sql:
                raise psycopg2.errors.UndefinedColumn(
                    'column "turns" of relation "eval_samples" does not exist'
                )

        connection["cursor"].execute.side_effect = execute
        return seen

    def test_the_rows_land_without_the_conversation(self, connection):
        seen = self._undefined_column_on(connection, "%(turns)s::jsonb")

        written = es.write_eval_samples(RUN_ID, [_scenario(1), _scenario(2)], "postgresql://prod")

        assert written == 2
        narrow = [sql for sql in seen if "%(turns)s::jsonb" not in sql]
        assert len(narrow) == 2, (
            f"{len(narrow)} rows were re-sent on the pre-0028 INSERT, not 2. The "
            "first execute is the one that raises, so nothing was written; the "
            "whole set only lands if the loop restarts"
        )
        assert all("INSERT INTO eval_samples" in sql for sql in narrow)
        # Two rungs down since 0029: the 0029 shape, then the 0028 shape, both
        # name `turns` and both are refused before the pre-0028 one lands.
        assert connection["conn"].rollback.call_count == 2
        connection["conn"].commit.assert_called_once()
        connection["conn"].close.assert_called_once()

    def test_a_tenant_behind_0029_keeps_the_conversation_and_loses_the_verdict(self, connection):
        """One rung down, not two: `turns` and `resolved_question` still land (#226)."""
        seen = self._undefined_column_on(connection, "%(clarifying_check)s")

        written = es.write_eval_samples(RUN_ID, [_scenario(1), _scenario(2)], "postgresql://prod")

        assert written == 2
        landed = [sql for sql in seen if "%(clarifying_check)s" not in sql]
        assert len(landed) == 2
        assert all("%(turns)s::jsonb" in sql and "%(resolved_question)s" in sql for sql in landed), (
            "a tenant at 0028 fell past the 0028 INSERT and lost the conversation"
        )
        connection["conn"].rollback.assert_called_once()
        connection["conn"].commit.assert_called_once()

    def test_the_rollback_comes_before_each_next_attempt(self, connection):
        """An aborted transaction refuses the next statement until it is rolled back.

        Without this ordering the fallback raises `InFailedSqlTransaction` and the
        run is lost anyway, one exception later.
        """
        order: list[str] = []
        connection["conn"].rollback.side_effect = lambda: order.append("rollback")

        def execute(sql, params=None):
            if "%(clarifying_check)s" in sql:
                order.append("0029")
            elif "%(turns)s::jsonb" in sql:
                order.append("0028")
            else:
                order.append("0027")
            if "%(turns)s::jsonb" in sql:
                raise psycopg2.errors.UndefinedColumn("no turns")

        connection["cursor"].execute.side_effect = execute

        es.write_eval_samples(RUN_ID, [_scenario(1)], "postgresql://prod")

        assert order == ["0029", "rollback", "0028", "rollback", "0027"], order

    def test_only_undefined_column_falls_back(self, connection):
        """A real write failure has to surface, not be retried on a narrower shape.

        A disk error, a constraint violation or a dead connection would otherwise
        be answered by silently writing a row with less in it.
        """
        connection["cursor"].execute.side_effect = psycopg2.errors.DiskFull("no space")

        with pytest.raises(psycopg2.errors.DiskFull):
            es.write_eval_samples(RUN_ID, [_scenario(1)], "postgresql://prod")

        connection["conn"].commit.assert_not_called()
        connection["conn"].close.assert_called_once()


def test_the_rewritten_question_is_written_beside_the_raw_one(connection):
    """Both, on one row, because the sheet and the Judge read different ones.

    `user_input` stays the question as asked, which is the half of the attribution
    key the judge rows echo back. `resolved_question` is what relevancy was
    actually scored against (#227 PR 2).
    """
    scenario = _scenario(6)
    scenario["turns"] = [{"role": "user", "content": "I'm setting up Earth Elements."}]
    scenario["resolved_question"] = "How do I start the dev server for Earth Elements?"

    es.write_eval_samples(RUN_ID, [scenario], "postgresql://prod")

    [(sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert "resolved_question" in sql
    assert params["resolved_question"] == scenario["resolved_question"]
    assert params["user_input"] == scenario["question"], (
        "the raw question was overwritten by the rewrite, so a returned judge row "
        "would match no scenario"
    )


def test_a_scenario_with_no_rewrite_writes_null(connection):
    """NULL twice over, and `turns` on the same row says which.

    A single-turn scenario had nothing to resolve. A multi-turn one whose rewrite
    failed is scored on its raw question. Both are NULL here on purpose; the empty
    string would be a rewrite that says nothing.
    """
    es.write_eval_samples(RUN_ID, [_scenario(7)], "postgresql://prod")

    [(_sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert params["resolved_question"] is None

    connection["cursor"].execute.reset_mock()
    blank = _scenario(8)
    blank["resolved_question"] = "   "
    es.write_eval_samples(RUN_ID, [blank], "postgresql://prod")

    [(_sql, params)] = [c.args for c in connection["cursor"].execute.call_args_list]
    assert params["resolved_question"] is None, (
        "a rewrite of nothing but whitespace was written as the question "
        "relevancy was scored against"
    )


def test_an_empty_list_writes_nothing_and_opens_no_connection(connection):
    assert es.write_eval_samples(RUN_ID, [], "postgresql://prod") == 0
    assert connection["connects"] == []


def test_the_connection_is_closed_when_an_insert_raises(connection):
    connection["cursor"].execute.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        es.write_eval_samples(RUN_ID, [_scenario(1)], "postgresql://prod")

    connection["conn"].close.assert_called_once()
    connection["conn"].commit.assert_not_called()


class TestTheRuleVerdictLandsInItsColumn:
    """`clarifying_check` is the ambiguous row's whole result (#226, tenant 0029)."""

    def test_a_checked_row_binds_its_verdict_and_an_ordinary_row_binds_null(self):
        checked = {**_scenario(1), "ambiguous": True, "clarifying_check": False}
        plain = _scenario(2)

        assert es._sample_row_params(RUN_ID, checked)["clarifying_check"] is False
        assert es._sample_row_params(RUN_ID, {**checked, "clarifying_check": True})["clarifying_check"] is True
        assert es._sample_row_params(RUN_ID, plain)["clarifying_check"] is None

    def test_the_wide_insert_names_the_column_and_binds_the_value(self, connection):
        es.write_eval_samples(RUN_ID, [{**_scenario(1), "clarifying_check": True}], "postgresql://prod")

        sql, params = connection["cursor"].execute.call_args[0]
        assert "clarifying_check" in sql
        assert params["clarifying_check"] is True
