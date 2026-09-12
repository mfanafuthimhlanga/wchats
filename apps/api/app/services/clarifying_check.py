"""The deterministic check for an ambiguous scenario (#226, ADR 0012).

An ambiguous scenario is one whose correct reply is a clarifying question rather
than an answer: "which project?" to "how do I start the dev server" when nothing
named one. The four Ragas metrics measure the wrong thing for such a row, so its
verdict is this rule and not a Judge. A rule can be mutated and observed to fail;
a model's opinion on "is this a question" cannot.

The rule is small on purpose. It decides that a response ASKS when its last
sentence is a question and the whole response is short enough that the question
is the reply rather than a courtesy tacked onto an answer. Both halves are
needed: "Run pnpm dev from the repo root. Anything else?" ends in a question and
is an answer. The word cap is the second lock, and `CLARIFYING_MAX_WORDS` says
what it is set to and why.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: A reply longer than this is an answer with a question on the end, not a
#: clarifying question. Forty words is two sentences of asking; the shortest
#: real answers in the calibration sheet (#58) run past sixty. Untuned, and the
#: first ambiguous run's rows are what tunes it.
CLARIFYING_MAX_WORDS = 40

#: The key an ambiguous scenario carries once the check has run on its response.
#: Its presence on a scored row is what keeps the row out of the Ragas set.
CLARIFYING_CHECK_KEY = "clarifying_check"

_SENTENCE_END = re.compile(r"[.!?]")


def is_clarifying_question(response: str) -> bool:
    """True when `response` asks rather than answers.

    Empty text is not a question. Trailing quotes and brackets are stripped so
    `"Which project?"` reads the same as `Which project?`.
    """
    text = response.strip().rstrip("\"')]}»")
    if not text:
        return False
    if not text.endswith("?"):
        return False
    return len(text.split()) <= CLARIFYING_MAX_WORDS


def clarifying_verdicts(rows: Iterable[Mapping]) -> dict[str, bool]:
    """scenario_id -> asked, off the rows that carry a check result.

    The one place the key is read back, so a row without it is never mistaken
    for a row that failed. `summarise_run_validity` counts these as scored and
    `dataset_verdict_counts` counts them as passed or failed, and both read this
    mapping rather than the rows, so the two cannot disagree on which rows were
    checked.
    """
    return {
        str(row.get("id", "")): bool(row[CLARIFYING_CHECK_KEY])
        for row in rows
        if CLARIFYING_CHECK_KEY in row
    }


def split_checked_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(rows for the Judge, rows the check already decided), in input order."""
    judged = [r for r in rows if CLARIFYING_CHECK_KEY not in r]
    checked = [r for r in rows if CLARIFYING_CHECK_KEY in r]
    return judged, checked
