"""The deterministic check for an ambiguous scenario (#226, ADR 0012).

An ambiguous scenario is one whose correct reply is a clarifying question rather
than an answer: "which project?" to "how do I start the dev server" when nothing
named one. The four Ragas metrics measure the wrong thing for such a row, so its
verdict is a rule and not a Judge. A rule can be mutated and observed to fail;
a model's opinion on "is this a question" cannot.

THREE RULES, FOR TWO KINDS OF TEXT.

`asked_to_clarify` is the agent's verdict. The turn asked when either of the
two rules below says it did.

`turn_asked_to_clarify` reads the agent's turn. The agent has a `clarify` tool
whose whole job is to ask the customer a question, so a call to it in the
turn's tool log is exact evidence of asking, in any language and any markdown.
The turn asked when clarify was its LAST tool call: an agent may retrieve
first, see that the chunks span several projects, and then ask, and that is
asking; an agent that asks and then retrieves and answers anyway is not. Since
#280 the live loop cannot produce the second shape, and the rule still reads the
log because it is also read over rows the current loop did not write.

`reply_asks_to_clarify` reads the agent's reply, for the agent that asks in
prose and never calls the tool (the owner's decision, 2026-09-25). It reads the
OPENING sentence, because "Run pnpm dev. Anything else?" ends in a question
mark and is an answer, while "Which project do you mean?" above a bulleted list
of the projects does not end in one and is asking. The reply, citations cut,
must also be at most `CLARIFYING_MAX_WORDS` long, the bound the owner's
reference is held to. The rule reads shape, not meaning: a short question
followed by a short answer passes, exactly as a `clarify` call carrying that
text would.

`is_clarifying_question` reads a reference answer the owner wrote, where there
is no tool log. It holds the reference to the shape of a question so a pair
cannot demand a behaviour its own reference would fail: after trailing
emphasis, quotes and brackets are dropped, the text ends in a question mark and
is at most `CLARIFYING_MAX_WORDS` long.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: A reference longer than this is an answer with a question on the end, not a
#: clarifying question. Forty words is two sentences of asking; the shortest
#: real answers on the calibration sheet (#58) run past sixty. Untuned, and the
#: first ambiguous run's rows are what tunes it.
CLARIFYING_MAX_WORDS = 40

#: The key an ambiguous scenario carries once the check has run on its turn.
#: Its presence on a scored row is what keeps the row out of the Ragas set.
CLARIFYING_CHECK_KEY = "clarifying_check"

#: The tool an agent calls to ask the customer a question, named as
#: `agent_tool_definitions` registers it.
CLARIFY_TOOL = "clarify"

#: Question marks in the scripts a South African tenant's customers write in,
#: plus the fullwidth and Arabic forms. A Greek question mark is `;`.
_QUESTION_MARKS = "?؟？;"
_TRAILING_DECORATION = "\"')]}»*_` \n\t"

#: Where the agent's CITATIONS block starts. Everything after it names sources,
#: and a list of document names is neither asking nor answering.
_CITATIONS_RE = re.compile(r"(^|\n)\s*CITATIONS\s*:")

#: The end of the opening sentence: a terminator followed by any closing
#: decoration and then whitespace, or a line break. The Greek `;` is left out,
#: since a semicolon inside an English sentence is far more common than a Greek
#: reply.
_FIRST_SENTENCE_END = re.compile(r"[.!?؟？][\"')\]}»*_`]*(?=\s)|\n")


def turn_asked_to_clarify(tool_calls_log: Iterable[Mapping]) -> bool:
    """True when the turn's last tool call was `clarify`.

    Read off the tool log the loop already walks for retrieved contexts, so the
    verdict comes from what the agent DID rather than from how the model
    phrased it. A turn that asked and then went on to retrieve and answer has
    `retrieve` after `clarify` and fails.

    THE LIVE LOOP NO LONGER PRODUCES THAT SHAPE (#280). `agent_loop` ends a turn
    on a successful `clarify` and sorts a clarify call to the end of its own
    reply's batch, so a turn served today has `clarify` last or not at all. The
    False branch still fires on every log the loop did not write THIS release:
    rows stored before #280, a mined production trace, a replayed conversation,
    and a red-team transcript. It is a rule over stored evidence, not an
    assertion about the current loop, so it keeps reading the log rather than
    trusting the writer.
    """
    names = [str(tc.get("tool_name", "")) for tc in tool_calls_log]
    return bool(names) and names[-1] == CLARIFY_TOOL


def reply_asks_to_clarify(reply: str) -> bool:
    """True when the reply opens with a question and is short.

    Run 2944b802 is the evidence: "Which project do you mean?" above a bulleted
    list of three projects, and "Which product or project do you mean? The
    knowledge base includes pricing information for Beekeeper, ..." Both asked,
    and both failed the tool rule because the agent wrote the question as its
    reply instead of calling `clarify`.
    """
    cut = _CITATIONS_RE.search(reply)
    body = (reply[: cut.start()] if cut else reply).strip()
    if not body or len(body.split()) > CLARIFYING_MAX_WORDS:
        return False
    end = _FIRST_SENTENCE_END.search(body)
    first = (body[: end.end()] if end else body).rstrip(_TRAILING_DECORATION)
    return bool(first) and first[-1] in _QUESTION_MARKS


def asked_to_clarify(tool_calls_log: Iterable[Mapping], reply: str) -> bool:
    """The agent's verdict on an ambiguous scenario: the tool rule or the reply rule."""
    return turn_asked_to_clarify(tool_calls_log) or reply_asks_to_clarify(reply)


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
