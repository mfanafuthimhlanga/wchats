"""The deterministic check for an ambiguous scenario (#226, ADR 0012).

An ambiguous scenario is one whose correct reply is a clarifying question rather
than an answer: "which project?" to "how do I start the dev server" when nothing
named one. The four Ragas metrics measure the wrong thing for such a row, so its
verdict is a rule and not a Judge. A rule can be mutated and observed to fail;
a model's opinion on "is this a question" cannot.

TWO RULES, FOR TWO KINDS OF TEXT.

`turn_asked_to_clarify` reads the agent's turn. The agent has a `clarify` tool
whose whole job is to ask the customer a question, so a call to it in the
turn's tool log is exact evidence of asking, in any language and any markdown.
A turn that also retrieved is an answer with a question on it, and is not
asking. Free text is never read for the agent's verdict: "Run pnpm dev.
Anything else?" ends in a question mark and is an answer, and a bulleted list
of the four projects with the question above it does not end in one and is
asking.

`is_clarifying_question` reads a reference answer the owner wrote, where there
is no tool log. It holds the reference to the shape of a question so a pair
cannot demand a behaviour its own reference would fail: after trailing
emphasis, quotes and brackets are dropped, the text ends in a question mark and
is at most `CLARIFYING_MAX_WORDS` long.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: A reference longer than this is an answer with a question on the end, not a
#: clarifying question. Forty words is two sentences of asking; the shortest
#: real answers on the calibration sheet (#58) run past sixty. Untuned, and the
#: first ambiguous run's rows are what tunes it.
CLARIFYING_MAX_WORDS = 40

#: The key an ambiguous scenario carries once the check has run on its turn.
#: Its presence on a scored row is what keeps the row out of the Ragas set.
CLARIFYING_CHECK_KEY = "clarifying_check"

#: The tool an agent calls to ask the customer a question, and the one it calls
#: to answer from the corpus. Names as `agent_tool_definitions` registers them.
CLARIFY_TOOL = "clarify"
RETRIEVE_TOOL = "retrieve"

#: Question marks in the scripts a South African tenant's customers write in,
#: plus the fullwidth and Arabic forms. A Greek question mark is `;`.
_QUESTION_MARKS = "?؟？;"
_TRAILING_DECORATION = "\"')]}»*_` \n\t"


def turn_asked_to_clarify(tool_calls_log: Iterable[Mapping]) -> bool:
    """True when the turn called `clarify` and never `retrieve`.

    Read off the tool log the loop already walks for retrieved contexts, so the
    verdict comes from what the agent DID rather than from how the model
    phrased it.
    """
    names = {str(tc.get("tool_name", "")) for tc in tool_calls_log}
    return CLARIFY_TOOL in names and RETRIEVE_TOOL not in names


def is_clarifying_question(text: str) -> bool:
    """True when `text` reads as a short question. For owner-written references."""
    stripped = text.strip().rstrip(_TRAILING_DECORATION)
    if not stripped or stripped[-1] not in _QUESTION_MARKS:
        return False
    return len(stripped.split()) <= CLARIFYING_MAX_WORDS


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
