"""
Issue #79: the five readers of `messages` get one turn's rows in insert order.

`_persist_messages` writes a turn's user row and its assistant row in ONE
transaction, and Postgres `now()` is `transaction_timestamp()`, so both rows
carry byte-identical `created_at`. `id` is `gen_random_uuid()`, which sorts
arbitrarily. Five readers ordered by `created_at` and relied on
user-before-assistant inside a turn, so the pair order was whatever the plan
happened to produce.

HOW THESE TESTS MODEL THAT, and why it is not a fake that agrees with the code.
`_MessagesTable` answers a SELECT the way Postgres would: it honours ORDER BY
column by column, and rows still TIED after the last ordering column come back
in whichever order the parametrisation asks for. Every test runs BOTH orders and
demands the same answer from the reader. That is the actual contract — a tie has
no order, so a correct reader cannot depend on one — and it is why a fixed tie
direction would not do: reversing ties makes a DESC-then-reverse reader look
correct by accident, and keeping insert order makes every reader look correct.

`ORDER BY seq` leaves no ties at all, which is how the readers pass both runs.

`agent._read_turn_history` is the fifth, and it is here now. It carried a `CASE
role` tiebreak that fixed a turn's pair locally and left two rows of the SAME
role at one timestamp ordered by nothing, so the column COMMENT 0025 writes,
"every reader of this table orders by seq", was not yet true. It is now.
tests/unit/test_agent_task.py pins the rest of that helper.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# One conversation's rows, as `_persist_messages` writes them
# ---------------------------------------------------------------------------

#: Two turns. Both rows of a turn share one `created_at`, which is the defect.
TWO_TURNS: list[dict] = [
    {"seq": 1, "created_at": "2026-09-01T10:00:00Z", "role": "user", "content": "where is my order?"},
    {"seq": 2, "created_at": "2026-09-01T10:00:00Z", "role": "assistant", "content": "it ships tomorrow."},
    {"seq": 3, "created_at": "2026-09-01T10:05:00Z", "role": "user", "content": "can i change the address?"},
    {"seq": 4, "created_at": "2026-09-01T10:05:00Z", "role": "assistant", "content": "yes, until it ships."},
]

TIE_ORDERS = ["as_inserted", "reversed"]


class _Cursor:
    """Answers one SELECT over `messages`, honouring its ORDER BY and LIMIT."""

    def __init__(self, rows: list[dict], tie_order: str) -> None:
        self._rows = rows
        self._tie_order = tie_order
        self._result: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql: str, params=None) -> None:
        flat = " ".join(str(sql).split())
        rows = list(self._rows)
        if self._tie_order == "reversed":
            rows.reverse()
        if re.search(r"role\s*=\s*'user'", flat):
            rows = [row for row in rows if row["role"] == "user"]
        for column, descending in reversed(_order_by(flat)):
            rows.sort(key=lambda row: row[column], reverse=descending)
        limit = _limit(flat, params)
        if limit is not None:
            rows = rows[:limit]
        columns = _selected(flat)
        self._result = [tuple(row[column] for column in columns) for row in rows]

    def fetchall(self) -> list[tuple]:
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


def _order_by(flat_sql: str) -> list[tuple[str, bool]]:
    """(column, descending) pairs, in the order the SELECT names them.

    An ORDER BY term this cannot read raises rather than being skipped: a
    silently ignored term is a test that passes because it stopped ordering.
    """
    match = re.search(r"ORDER BY (.+?)(?: LIMIT |$)", flat_sql, re.I)
    if not match:
        raise AssertionError("the query has no ORDER BY at all: %s" % flat_sql)
    terms = []
    for term in match.group(1).split(","):
        parts = term.strip().split()
        if not parts or not re.fullmatch(r"\w+", parts[0]):
            raise AssertionError("unreadable ORDER BY term %r in %s" % (term, flat_sql))
        terms.append((parts[0], len(parts) > 1 and parts[1].upper() == "DESC"))
    return terms


def _limit(flat_sql: str, params) -> int | None:
    match = re.search(r"LIMIT (\d+|%s)", flat_sql, re.I)
    if not match:
        return None
    if match.group(1) != "%s":
        return int(match.group(1))
    return int(params[-1])


def _selected(flat_sql: str) -> list[str]:
    columns = re.search(r"SELECT (.+?) FROM", flat_sql, re.I).group(1)
    return [column.strip() for column in columns.split(",")]


def _connect(rows: list[dict], tie_order: str):
    """A psycopg2.connect stand-in handing back one scripted cursor."""

    def _factory(*args, **kwargs):
        conn = MagicMock()
        conn.cursor.return_value = _Cursor(rows, tie_order)
        return conn

    return _factory


# ---------------------------------------------------------------------------
# actor_seam._fetch_history — the Actor gate's view of the conversation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tie_order", TIE_ORDERS)
def test_actor_gate_history_is_in_insert_order(monkeypatch, tie_order):
    """The gate decides whether a mutation may run. A transcript read inside out
    shows it the answer above the question that prompted it."""
    from app.services import actor_seam

    monkeypatch.setattr(actor_seam.psycopg2, "connect", _connect(TWO_TURNS, tie_order))

    history = asyncio.run(actor_seam._fetch_history("postgresql://tenant", "conv-1"))

    assert [row["role"] for row in history] == ["user", "assistant", "user", "assistant"], (
        "the Actor gate read the turns out of order under tie_order=%s: %r"
        % (tie_order, [(row["role"], row["content"]) for row in history])
    )
    assert history[0]["content"] == "where is my order?"


# ---------------------------------------------------------------------------
# bench_service._fetch_customer_turn — the question a graded answer came from
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tie_order", TIE_ORDERS)
def test_bench_pairs_the_answer_with_its_own_question(monkeypatch, tie_order):
    """This walks the transcript forward and returns the last user row seen
    before the assistant row it was given. An assistant row that sorts ahead of
    its own question returns the PREVIOUS turn's question, and that pair becomes
    a promoted eval scenario."""
    from app.services import bench_service

    monkeypatch.setattr(bench_service.psycopg2, "connect", _connect(TWO_TURNS, tie_order))

    question = bench_service._fetch_customer_turn(
        "postgresql://tenant", "conv-1", "yes, until it ships."
    )

    assert question == "can i change the address?", (
        "under tie_order=%s the graded answer was paired with %r" % (tie_order, question)
    )


# ---------------------------------------------------------------------------
# scenario_service._fetch_messages_for_conversation — the mined transcript
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tie_order", TIE_ORDERS)
def test_mined_transcript_is_in_insert_order(monkeypatch, tie_order):
    """Production mining turns a transcript into eval scenarios. Rows out of
    order mine a scenario whose expected answer predates its question."""
    from app.services import scenario_service

    monkeypatch.setattr(
        scenario_service.psycopg2, "connect", _connect(TWO_TURNS, tie_order)
    )

    messages = scenario_service._fetch_messages_for_conversation(
        "postgresql://tenant", "conv-1"
    )

    assert [row["role"] for row in messages] == ["user", "assistant", "user", "assistant"], (
        "the mined transcript came back out of order under tie_order=%s: %r"
        % (tie_order, [(row["role"], row["content"]) for row in messages])
    )


# ---------------------------------------------------------------------------
# retrieval_eval._fetch_last_user_message — the question Ragas is scored against
# ---------------------------------------------------------------------------


#: Two user rows sharing one `created_at`. Rare on the clock, and the only thing
#: that ever separated them was a column with no tiebreaker.
TWO_QUESTIONS_ONE_TIMESTAMP: list[dict] = [
    {"seq": 1, "created_at": "2026-09-01T10:00:00Z", "role": "user", "content": "the earlier question"},
    {"seq": 2, "created_at": "2026-09-01T10:00:00Z", "role": "assistant", "content": "the earlier answer"},
    {"seq": 3, "created_at": "2026-09-01T10:00:00Z", "role": "user", "content": "the latest question"},
]


@pytest.mark.parametrize("tie_order", TIE_ORDERS)
def test_faithfulness_scores_the_latest_question(monkeypatch, tie_order):
    """The faithfulness score is computed against whatever this returns, so the
    wrong row scores an answer against a question nobody asked of it."""
    from app.worker.tasks.runtime import retrieval_eval

    monkeypatch.setattr(
        retrieval_eval.psycopg2,
        "connect",
        _connect(TWO_QUESTIONS_ONE_TIMESTAMP, tie_order),
    )

    question = retrieval_eval._fetch_last_user_message("postgresql://tenant", "conv-1")

    assert question == "the latest question", (
        "under tie_order=%s the score would be computed against %r"
        % (tie_order, question)
    )


# ---------------------------------------------------------------------------
# agent._read_turn_history — the conversation the customer's next turn resumes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tie_order", TIE_ORDERS)
def test_the_turn_history_is_in_insert_order(tie_order):
    """The fifth reader, and the one the customer feels.

    This is the history every model call of a turn carries. A pair read inside
    out shows the model its own answer above the question that produced it, and
    the model reasons from a conversation that never happened.

    It took the connection rather than opening one (PROD-05, one pooled
    connection per turn), so this hands it the same scripted cursor the other
    four get.
    """
    from app.worker.tasks.runtime import agent

    history = agent._read_turn_history(_connect(TWO_TURNS, tie_order)(), "conv-1")

    assert [row["role"] for row in history] == ["user", "assistant", "user", "assistant"], (
        "the next turn read the conversation out of order under tie_order=%s: %r"
        % (tie_order, [(row["role"], row["content"]) for row in history])
    )
    assert history[0]["content"] == "where is my order?"
