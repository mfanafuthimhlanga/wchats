"""Draft golden pairs from an agent's corpus for the owner to label (#203).

The golden set is the owner's own assertion, and `scenario_service` is right that
no model writes `dataset='golden'`. It does not follow that a model may not draft.
This module drafts; it writes nothing. The owner keeps, edits or drops each draft,
and only a keep reaches the golden registration route, which stays the single
writer of golden rows.

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

_CHUNKS_BY_DOCUMENT_SQL = (
    "SELECT c.id, c.document_id, c.ordinal, c.content, d.title, d.source_uri "
    "FROM chunks c JOIN documents d ON d.id = c.document_id "
    "ORDER BY d.created_at, d.id, c.ordinal"
)


def fetch_chunks_by_document(tenant_conn_str: str) -> list[dict]:
    """Every chunk of the corpus with the document it belongs to, in document order."""
    conn = psycopg2.connect(tenant_conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(_CHUNKS_BY_DOCUMENT_SQL)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "chunk_id": str(chunk_id),
            "document_id": str(document_id),
            "ordinal": ordinal,
            "content": content,
            "document": title or source_uri,
        }
        for chunk_id, document_id, ordinal, content, title, source_uri in rows
    ]


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


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def citation_in_chunk(citation: str, content: str) -> bool:
    """Whether the citation is a passage of the chunk, whitespace aside.

    Line breaks and indentation differ between a chunk and a model's copy of it;
    words, numbers and punctuation may not. An empty citation cites nothing.
    """
    needle = _squash(citation)
    return bool(needle) and needle in _squash(content)


def draft_pair_from_chunk(chunk: dict, ledger: LedgerContext) -> dict | None:
    """One drafted pair from one chunk, or None when the model returned no draft."""
    response = ledger.client(PURPOSE).chat.completions.create(  # type: ignore[call-overload]  # a dict tool schema, not the SDK's TypedDict
        model=route_for(PURPOSE).model,
        max_completion_tokens=800,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
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
    return {
        "question": arguments["question"],
        "reference_answer": arguments["reference_answer"],
        "citation": arguments["citation"],
        "source_document_id": chunk["document_id"],
        "source_document": chunk["document"],
        "source_chunk_id": chunk["chunk_id"],
    }


def draft_golden_pairs(chunks: list[dict], ledger: LedgerContext) -> tuple[list[dict], int]:
    """Draft one pair per chunk and keep the ones whose citation is in their chunk.

    Returns (kept, dropped). A draft is dropped when the model returned none, or
    when its citation is not a passage of the chunk it was given: an answer whose
    source cannot be found is the invention the golden set exists to catch, so
    it never reaches the owner. A model call that raises drops that chunk only.
    """
    kept: list[dict] = []
    dropped = 0
    for chunk in chunks:
        try:
            draft = draft_pair_from_chunk(chunk, ledger)
        except Exception as exc:  # noqa: BLE001 — one chunk, one draft
            log_failure(log, "golden_draft.chunk_failed", exc, chunk_id=chunk["chunk_id"])
            dropped += 1
            continue
        if draft is None or not citation_in_chunk(draft["citation"], chunk["content"]):
            log.info(
                "golden_draft.dropped",
                chunk_id=chunk["chunk_id"],
                reason="no_draft" if draft is None else "citation_not_in_chunk",
            )
            dropped += 1
            continue
        kept.append(draft)
    return kept, dropped
