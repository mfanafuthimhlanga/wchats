"""
Issue #120 — `retrieval_metrics.faithfulness` holds scores from three instruments.

#84 closed by removing the proxy: `retrieval_eval` reads `tool_calls.retrieved_chunks`
now, so every score from 280ff05 on is computed over the chunks the retrieve tool
actually returned. That fixed the instrument going forward and left the history
alone, and one column now holds numbers produced by three different things:

    the SDK-era proxy      `str(block.content)[:200]`, a repr of the block list
    the post-#48 proxy     `wire_text(wire)[:200]`, joined text with a json payload
    the chunks themselves  one string per chunk, untruncated, from 280ff05

Nothing on the row said which. `judge_identity` (0020) is NULL for older rows and
separates the eras only by accident, and it names the MODEL rather than the
context shape. An average over the column mixes all three and reports an
instrument change as a quality change, which is the sentence #84 exists to
prevent.

WHAT THE FAKE DOES, and why it is not a fake that agrees with the code.
`_Aggregates` evaluates the SELECT it is handed against a set of rows: COUNT and
AVG, with or without a FILTER, and it RAISES on any expression it cannot read
rather than skipping the term. So a reader that averages the whole column gets
the whole column's average here, exactly as Postgres would, and the test that
wants the stamped era's average fails until the reader asks for it.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from app.domain.eval_result import CONTEXT_PROXY_VERSION

# ---------------------------------------------------------------------------
# One tenant's rows, spanning the cutover
# ---------------------------------------------------------------------------

#: Two unstamped rows scoring 0.2, two stamped rows scoring 0.9. Averaging the
#: whole column gives 0.55, a number that describes neither instrument.
ROWS_ACROSS_THE_CUTOVER: list[dict] = [
    {"faithfulness": 0.2, "citation_coverage": 1.0, "context_source": None},
    {"faithfulness": 0.2, "citation_coverage": 1.0, "context_source": None},
    {"faithfulness": 0.9, "citation_coverage": 1.0, "context_source": CONTEXT_PROXY_VERSION},
    {"faithfulness": 0.9, "citation_coverage": 1.0, "context_source": CONTEXT_PROXY_VERSION},
]

_TERM = re.compile(
    r"^(?P<fn>COUNT|AVG)\((?P<arg>\*|\w+)\)"
    r"(?: FILTER \(WHERE (?P<predicate>.+?)\))?"
    r" AS (?P<alias>\w+)$",
    re.I,
)


class _Aggregates:
    """Evaluates one aggregate SELECT over `retrieval_metrics` against `rows`."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._result: tuple = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql: str, params=None) -> None:
        flat = " ".join(str(sql).split())
        select = re.search(r"SELECT (.+?) FROM RETRIEVAL_METRICS", flat, re.I).group(1)
        self._result = tuple(
            self._evaluate(term.strip(), params or {}) for term in _split_terms(select)
        )

    def _evaluate(self, term: str, params: dict):
        match = _TERM.match(term)
        if not match:
            raise AssertionError("unreadable aggregate term %r" % term)
        rows = [
            row
            for row in self._rows
            if _predicate_holds(match.group("predicate"), row, params)
        ]
        column = match.group("arg")
        if column == "*":
            return len(rows)
        values = [row[column] for row in rows if row.get(column) is not None]
        if match.group("fn").upper() == "COUNT":
            return len(values)
        return sum(values) / len(values) if values else None

    def fetchone(self):
        return self._result


def _split_terms(select: str) -> list[str]:
    """Top-level comma split, so a FILTER's own parentheses do not split a term."""
    terms, depth, current = [], 0, []
    for char in select:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            terms.append("".join(current))
            current = []
            continue
        current.append(char)
    terms.append("".join(current))
    return terms


def _predicate_holds(predicate: str | None, row: dict, params: dict) -> bool:
    """The two FILTER predicates this table's reads may use, and nothing else."""
    if predicate is None:
        return True
    flat = " ".join(predicate.split())
    equals = re.fullmatch(r"context_source = %\((\w+)\)s", flat, re.I)
    if equals:
        return row.get("context_source") == params[equals.group(1)]
    distinct = re.fullmatch(
        r"context_source IS DISTINCT FROM %\((\w+)\)s", flat, re.I
    )
    if distinct:
        return row.get("context_source") != params[distinct.group(1)]
    raise AssertionError("unreadable FILTER predicate %r" % predicate)


def _connect(rows: list[dict]):
    def _factory(*args, **kwargs):
        conn = MagicMock()
        conn.cursor.return_value = _Aggregates(rows)
        return conn

    return _factory


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def test_the_faithfulness_average_covers_one_instrument(monkeypatch):
    """0.55 is the average of two instruments and describes neither.

    An owner reading the ops room sees the number move when the code that
    assembles the judge's context changes, and reads it as the agent getting
    worse.
    """
    from app.services import retrieval_metrics_service as service

    monkeypatch.setattr(
        service.psycopg2, "connect", _connect(ROWS_ACROSS_THE_CUTOVER)
    )

    health = service.read_retrieval_health("postgresql://tenant", window_days=7)

    assert health["avg_faithfulness"] == 0.9, (
        "the average mixes the pre-cutover proxy scores with the chunk scores: %r"
        % health["avg_faithfulness"]
    )


def test_the_read_says_how_many_rows_the_average_covers(monkeypatch):
    """A filtered average that does not say what it dropped is a smaller lie
    than an unfiltered one, and it is still one."""
    from app.services import retrieval_metrics_service as service

    monkeypatch.setattr(
        service.psycopg2, "connect", _connect(ROWS_ACROSS_THE_CUTOVER)
    )

    health = service.read_retrieval_health("postgresql://tenant", window_days=7)

    assert health["faithfulness_sample_count"] == 2
    assert health["faithfulness_unstamped_count"] == 2


def test_an_all_unstamped_window_reports_unknown_not_a_number(monkeypatch):
    """Measurement honesty: a metric computed over zero valid observations is
    unknown, never a value. Every row here predates the cutover."""
    from app.services import retrieval_metrics_service as service

    unstamped = [row for row in ROWS_ACROSS_THE_CUTOVER if row["context_source"] is None]
    monkeypatch.setattr(service.psycopg2, "connect", _connect(unstamped))

    health = service.read_retrieval_health("postgresql://tenant", window_days=7)

    assert health["avg_faithfulness"] == "not tracked yet", (
        "a window holding only pre-cutover rows reported %r as a faithfulness "
        "figure" % health["avg_faithfulness"]
    )
    assert health["faithfulness_sample_count"] == 0
    assert health["faithfulness_unstamped_count"] == 2
    assert health["sample_count"] == 2, (
        "the row count for every other metric must be unaffected"
    )


def test_citation_coverage_is_not_filtered(monkeypatch):
    """citation_coverage is arithmetic the task does itself, not a judge's
    verdict over an assembled context, so the cutover did not move it."""
    from app.services import retrieval_metrics_service as service

    monkeypatch.setattr(
        service.psycopg2, "connect", _connect(ROWS_ACROSS_THE_CUTOVER)
    )

    health = service.read_retrieval_health("postgresql://tenant", window_days=7)

    assert health["avg_citation_coverage"] == 1.0


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


def test_a_new_score_stamps_the_context_shape(monkeypatch):
    """Every score written from now on says what it was computed over."""
    from app.worker.tasks.runtime import retrieval_eval

    written: list[tuple] = []
    cursor = MagicMock()
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.execute.side_effect = lambda sql, params: written.append((sql, params))
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(retrieval_eval.psycopg2, "connect", lambda *a, **k: conn)

    retrieval_eval._update_retrieval_metrics(
        "postgresql://tenant", "job-1", 1.0, 0.9, {"model": "gpt-5.6-luna"}
    )

    sql, params = written[0]
    assert "context_source" in sql, (
        "the faithfulness write names no context shape, so tomorrow's rows are "
        "as unreadable as yesterday's: %s" % sql
    )
    assert CONTEXT_PROXY_VERSION in params, (
        "the stamp must be app.domain.eval_result.CONTEXT_PROXY_VERSION, so the "
        "offline record and the live row say the same thing: %r" % (params,)
    )


def test_a_row_with_no_verdict_is_stamped_with_nothing(monkeypatch):
    """The column describes what a SCORE was computed over. A row carrying only
    citation_coverage had no judge and no context, the same rule judge_identity
    follows one column over."""
    from app.worker.tasks.runtime import retrieval_eval

    written: list[tuple] = []
    cursor = MagicMock()
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.execute.side_effect = lambda sql, params: written.append((sql, params))
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(retrieval_eval.psycopg2, "connect", lambda *a, **k: conn)

    retrieval_eval._update_retrieval_metrics(
        "postgresql://tenant", "job-1", 1.0, None, None
    )

    _, params = written[0]
    assert CONTEXT_PROXY_VERSION not in params, (
        "a row with no faithfulness was stamped with a context shape: %r" % (params,)
    )
