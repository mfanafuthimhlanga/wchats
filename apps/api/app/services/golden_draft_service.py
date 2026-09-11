"""Draft golden pairs from an agent's corpus for the owner to label (#203).

The golden set is the owner's own assertion, and `scenario_service` is right that
no model writes `dataset='golden'`. It does not follow that a model may not draft.
This module drafts and writes no scenario row. The owner keeps, edits or drops
each draft, and only a keep reaches the golden registration route, which stays
the single writer of golden rows. What a draft run does write: its job row, one
job event per draft, and one ledger row per model call.

The bar is the hand-drafted set of 2026-09-06: one grounded pair per document,
the answer lifted near verbatim from one passage, 11 of 23 kept as written. What
made those drafts keepable is in the prompt below as five rules, and the two of
them that code can check, one document per pair and a citation that is really in
the chunk, are checked here rather than trusted.
"""

from __future__ import annotations

import re

import psycopg2
import structlog

from app.core.log_bounds import log_failure
from app.core.model_client import LedgerContext, route_for
from app.services.tool_loop import forced_tool_arguments

log = structlog.get_logger(__name__)

PURPOSE = "golden_draft"

#: `n` is bounded because every draft is one model call and the result travels as
#: job events, of which `get_job` returns the last 100.
DRAFT_COUNT_MIN = 10
DRAFT_COUNT_MAX = 30

DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_golden_draft",
        "description": "Submit one drafted golden pair lifted from the passage.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "reference_answer": {"type": "string"},
                "citation": {
                    "type": "string",
                    "description": (
                        "The exact passage of the chunk the answer was lifted from, "
                        "copied character for character."
                    ),
                },
                "lead_in": {
                    "type": "string",
                    "description": (
                        "Only when asked for one. The customer's earlier message, "
                        "naming the document's subject, that the question is a "
                        "follow-up to."
                    ),
                },
            },
            "required": ["question", "reference_answer", "citation"],
        },
    },
}

_SYSTEM_PROMPT = (
    "You draft one golden question-and-answer pair from one passage of a business's "
    "own documents. The owner of the business will read your draft and keep it, edit "
    "it or drop it. A kept draft becomes part of the contract their customer service "
    "agent is measured against, so follow these rules exactly.\n\n"
    "1. Use only the passage you are given. It comes from one named document.\n"
    "2. The reference answer is near verbatim from one part of the passage: two or "
    "three sentences, facts only, no synthesis, no introduction, no closing remark.\n"
    "3. The question is one a customer or visitor would ask of this business, not a "
    "quiz about the text. Never mention 'the document' or 'the passage'.\n"
    "4. Copy every number, command, path and name exactly as the passage has it.\n"
    "5. The citation is the exact part of the passage the answer was lifted from, "
    "copied character for character. It must be findable in the passage.\n\n"
    "Call submit_golden_draft with the question, the reference answer and the citation."
)

#: Appended when the corpus holds more than one document (#227).
#:
#: A one-document corpus needs no lead-in: every question is about the only
#: subject there is. With several, "how do I start the dev server" is answerable
#: only once the customer has said which product they mean, and a golden pair
#: that omits the saying is a pair the agent is expected to answer from nothing.
_LEAD_IN_RULES = (
    "\n\n6. This business has several documents, so a bare question can be "
    "ambiguous. Also write `lead_in`: the customer's EARLIER message, one "
    "sentence, naming this document's subject in the customer's own words.\n"
    "7. Then write the question as a FOLLOW-UP to that message. It must not "
    "name the subject again, because the lead-in already did. A question that "
    "stands alone without the lead-in is the wrong question here."
)

#: The pick needs ids and order, not text. Content is fetched for the picked
#: chunks only, so a corpus of thousands of chunks costs the worker a list of
#: ids, not the corpus.
_CHUNK_INDEX_SQL = (
    "SELECT c.id, c.document_id, c.ordinal, d.title, d.source_uri "
    "FROM chunks c JOIN documents d ON d.id = c.document_id "
    "ORDER BY d.created_at, d.id, c.ordinal"
)
_CHUNK_CONTENT_SQL = "SELECT id, content FROM chunks WHERE id = ANY(%s)"


def fetch_chunk_index(tenant_conn_str: str) -> list[dict]:
    """Every chunk's id, document and ordinal, in document order, without content."""
    conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(_CHUNK_INDEX_SQL)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "chunk_id": str(chunk_id),
            "document_id": str(document_id),
            "ordinal": ordinal,
            "document": title or source_uri,
        }
        for chunk_id, document_id, ordinal, title, source_uri in rows
    ]


def fetch_chunk_content(tenant_conn_str: str, chunks: list[dict]) -> list[dict]:
    """The picked chunks with their content filled in, in the order given."""
    if not chunks:
        return []
    conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(_CHUNK_CONTENT_SQL, ([c["chunk_id"] for c in chunks],))
            content = {str(chunk_id): text for chunk_id, text in cur.fetchall()}
    finally:
        conn.close()
    return [{**c, "content": content[c["chunk_id"]]} for c in chunks if c["chunk_id"] in content]


def pick_chunks_by_document(rows: list[dict], n: int) -> list[dict]:
    """`n` chunks spread across every document before any document repeats.

    Round one takes one chunk from each document, round two a second from each,
    until `n` are picked. Within a document the rounds are spread through its
    length rather than taken from the top, so a long document contributes its
    middle and end, not only its opening. Coverage is by document, which is what
    the hand-drafted set had and the exploratory generator's chunk window lacks.
    """
    if n <= 0 or not rows:
        return []
    by_document: dict[str, list[dict]] = {}
    for row in rows:
        by_document.setdefault(row["document_id"], []).append(row)
    documents = list(by_document.values())
    picked = _spread_rounds(documents, n)
    if len(picked) < n:
        picked = _fill_from_remaining(documents, picked, n)
    return picked


def _spread_rounds(documents: list[list[dict]], n: int) -> list[dict]:
    """One chunk per document per round, spread through each document's length."""
    rounds = -(-n // len(documents))  # ceil
    picked: list[dict] = []
    for r in range(rounds):
        for chunks in documents:
            if len(picked) == n:
                return picked
            if r >= len(chunks):
                continue
            # A document shorter than the rounds is walked top to bottom instead,
            # or the spread lands on the same chunk twice.
            index = r if len(chunks) <= rounds else (r * len(chunks)) // rounds
            picked.append(chunks[index])
    return picked


def _fill_from_remaining(documents: list[list[dict]], picked: list[dict], n: int) -> list[dict]:
    """A short document ran out before its share; the rest comes from documents
    that still have unpicked chunks, still one per document per pass."""
    taken = {chunk["chunk_id"] for chunk in picked}
    added = True
    while len(picked) < n and added:
        added = False
        for chunks in documents:
            spare = next((c for c in chunks if c["chunk_id"] not in taken), None)
            if spare is None or len(picked) == n:
                continue
            picked.append(spare)
            taken.add(spare["chunk_id"])
            added = True
    return picked


#: A citation shorter than this is a heading or a name, which any chunk about the
#: business contains, so it would bind nothing. Forty characters is a short sentence.
CITATION_MIN_CHARS = 40

#: The share of an answer's words that must appear in the chunk. Rule 2 says near
#: verbatim; a word in five of slack covers articles the model added or dropped.
ANSWER_GROUNDING_FLOOR = 0.8


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def citation_in_chunk(citation: str, content: str) -> bool:
    """Whether the citation is a passage of the chunk, whitespace aside, and long
    enough to bind anything.

    Line breaks and indentation differ between a chunk and a model's copy of it;
    words, numbers and punctuation may not. An empty or one-line citation cites
    nothing: it is in every chunk that names the business.
    """
    needle = _squash(citation)
    return len(needle) >= CITATION_MIN_CHARS and needle in _squash(content)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[0-9a-z]+", text.lower()) if len(w) >= 3]


def answer_grounded_in_chunk(answer: str, content: str) -> bool:
    """Whether at least ANSWER_GROUNDING_FLOOR of the answer's words are in the chunk.

    The citation check binds the citation; this binds the answer. A citation that
    is really in the chunk beside an answer the model composed from elsewhere is
    the case the first check alone lets through. Word presence is a floor, not
    proof: an answer that reorders the chunk's own words passes, and that is the
    owner's call on the bench.
    """
    words = _words(answer)
    if not words:
        return False
    present = set(_words(content))
    return sum(1 for w in words if w in present) / len(words) >= ANSWER_GROUNDING_FLOOR


def lead_in_binds_the_question(lead_in: str, question: str, document: str) -> bool:
    """Whether a lead-in does the job a lead-in exists to do.

    Two conditions, and both are about the pair rather than the prose. The
    lead-in has to NAME the document's subject, or it binds nothing and the
    follow-up is still ambiguous. The question has to NOT name it, or the pair is
    single-turn wearing a conversation and the turns column claims a binding the
    question never needed.

    Titles carry extensions and separators a customer would not say, so the test
    is the title's own words rather than the title: every word of three letters
    or more in the lead-in, none of them in the question. A title made entirely
    of short words binds nothing either way and is refused.
    """
    title_words = set(_words(document))
    if not title_words:
        return False
    return title_words <= set(_words(lead_in)) and not (title_words & set(_words(question)))


def draft_pair_from_chunk(
    chunk: dict, ledger: LedgerContext, ask_for_lead_in: bool = False
) -> dict | None:
    """One drafted pair from one chunk, or None when the model returned no draft."""
    response = ledger.client(PURPOSE).chat.completions.create(  # type: ignore[call-overload]  # a dict tool schema, not the SDK's TypedDict
        model=route_for(PURPOSE).model,
        max_completion_tokens=800,
        messages=[
            {
                "role": "system",
                "content": _SYSTEM_PROMPT + (_LEAD_IN_RULES if ask_for_lead_in else ""),
            },
            {
                "role": "user",
                "content": (
                    f"DOCUMENT: {chunk['document']}\n\nPASSAGE:\n{chunk['content']}"
                ),
            },
        ],
        tools=[DRAFT_TOOL],
        tool_choice={"type": "function", "function": {"name": "submit_golden_draft"}},
    )
    arguments = forced_tool_arguments(response, "submit_golden_draft")
    if arguments is None:
        return None
    fields = {name: arguments.get(name) for name in ("question", "reference_answer", "citation")}
    if not all(isinstance(value, str) and value.strip() for value in fields.values()):
        # The tool schema names the three as required strings; the reader checks
        # only that the arguments are an object, so a null or a number lands here.
        return None
    lead_in = str(arguments.get("lead_in") or "").strip()
    # A lead-in that fails either half is DROPPED, not the pair with it. The
    # draft is still a good single-turn golden pair, and the owner reads every
    # one of these before it becomes a contract term.
    turns = (
        [{"role": "user", "content": lead_in}]
        if lead_in and lead_in_binds_the_question(lead_in, str(fields["question"]), chunk["document"])
        else []
    )
    return {
        **fields,
        "turns": turns,
        "source_document_id": chunk["document_id"],
        "source_document": chunk["document"],
        "source_chunk_id": chunk["chunk_id"],
    }


def _reason_to_drop(draft: dict | None, content: str) -> str | None:
    """Why a draft never reaches the owner, or None when it does."""
    if draft is None:
        return "no_draft"
    if not citation_in_chunk(draft["citation"], content):
        return "citation_not_in_chunk"
    if not answer_grounded_in_chunk(draft["reference_answer"], content):
        return "answer_not_in_chunk"
    return None


def draft_golden_pairs(
    chunks: list[dict], ledger: LedgerContext, ask_for_lead_in: bool = False
) -> tuple[list[dict], int]:
    """Draft one pair per chunk and keep the ones the chunk itself vouches for.

    Returns (kept, dropped). A draft is dropped when the model returned none,
    when its citation is not a passage of the chunk it was given, or when its
    answer's words are mostly not in that chunk. An answer whose source cannot be
    found is the invention the golden set exists to catch, so it never reaches
    the owner. Anything that raises for one chunk, the call or the checks, drops
    that chunk only.

    `ask_for_lead_in` asks for a conversation around the question, and the caller
    sets it from the corpus rather than from a preference: one document means one
    subject and a bare question is already unambiguous (#227). A lead-in that
    does not bind its question is dropped on its own, leaving a single-turn pair
    rather than no pair.
    """
    kept: list[dict] = []
    dropped = 0
    for chunk in chunks:
        try:
            draft = draft_pair_from_chunk(chunk, ledger, ask_for_lead_in)
            reason = _reason_to_drop(draft, chunk["content"])
        except Exception as exc:  # noqa: BLE001 — one chunk, one draft
            log_failure(log, "golden_draft.chunk_failed", exc, chunk_id=chunk["chunk_id"])
            dropped += 1
            continue
        if reason is not None or draft is None:
            log.info("golden_draft.dropped", chunk_id=chunk["chunk_id"], reason=reason)
            dropped += 1
            continue
        kept.append(draft)
    return kept, dropped
