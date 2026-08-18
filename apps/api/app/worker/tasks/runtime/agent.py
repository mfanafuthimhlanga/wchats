"""
run_agent_turn — Celery task: Orchestrates a single Claude Agent SDK turn.

Position in M4 runtime flow:
    API route /v1/chat/agent/{agent_id}/message
      → dispatch run_agent_turn (runtime queue)
      → SSE stream back to widget client

Idempotency mechanism:
    READ guard on job_events: if an "agent.response" row already exists for this
    job_id, the task returns immediately — safe to retry without duplicate SDK
    calls or duplicate SSE events.

Security constraints (CLAUDE.md non-negotiable rules):
    - Task args: (job_id, agent_id, message, conversation_id) ONLY.
      NO conn_str, NO API keys in task args (CTL-08 / T-04-03-04).
    - conn_str fetched via fernet_decrypt(agent.neon_connection_string) at runtime.
    - message text is NEVER logged (T-04-03-05).
    - structlog lines reference only job_id, agent_id, conversation_id, counts.

SSE event sequence:
    agent.thinking      ← task begins, SDK turn starting
    agent.tool_call     ← each MCP tool invocation observed in stream
    agent.tool_result   ← each MCP tool result observed in stream
    agent.escalated     ← (optional) escalate_to_human ToolUseBlock detected
    agent.response      ← terminal event with text, citations, conversation_id
    agent.failed        ← terminal failure only (retries exhausted). Payload
                          carries error_type beside error, because str() of
                          several exceptions on this path is empty (BACKLOG 1.30).

Queue: runtime (CLAUDE.md non-negotiable: both Celery queues always present)
"""

import ast
import asyncio
import json
import os
import re
import ssl
import time
import uuid
from datetime import datetime, timezone

import psycopg2
import redis as redis_lib
import structlog
from celery import chain as celery_chain
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from langfuse import Langfuse
from sqlalchemy import text as sa_text

from app.core.config import AGENT_TURN_MODEL, settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.models.job import Job
from app.models.prompt_version import PromptVersion
from app.services.agent_prompt import build_system_prompt
from app.services.agent_tools import (
    RETRIEVED_CONTEXT_FOOTER,
    RETRIEVED_CONTEXT_HEADER,
    SIDE_EFFECT_MODES,
    RetrievalStrategy,
    SideEffectMode,
    build_tool_server,
    record_suppressed_side_effect,
    reset_side_effect_context,
)
from app.services.escalation import send_escalation_email
from app.services.events import emit
from app.services.prompt_version_service import resolve_prompt_version
from app.utils.pii_firewall import detect_pii, scan_response
from app.worker.celery_app import celery_app
from app.worker.tasks.runtime.retrieval_eval import run_retrieval_faithfulness
from app.worker.tasks.runtime.validators import run_auditor, run_gatekeeper, run_strategist

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level sync Redis client (copied verbatim from retrieve.py lines 59-62)
# Strip query params; pass ssl_cert_reqs as Python constant.
# ---------------------------------------------------------------------------
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)

# ---------------------------------------------------------------------------
# OPS-04: module-level Langfuse client — mirrors validation_service.py's
# `_langfuse is None` no-op guard exactly. Optional at runtime: if
# LANGFUSE_PUBLIC_KEY is unset (or client construction fails), every call
# site below no-ops rather than raising (CLAUDE.md Rule 6 — v4 API only).
# ---------------------------------------------------------------------------
_langfuse: Langfuse | None = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _langfuse = Langfuse()
except Exception:
    pass  # Langfuse unavailable — the agent turn still runs, just not traced

# ---------------------------------------------------------------------------
# Citation regex — parses CITATIONS block appended by system prompt instruction.
# ---------------------------------------------------------------------------
CITATIONS_REGEX = re.compile(r"CITATIONS:\n((?:- Document: .+ \| Section: .+\n?)+)")
_CITATION_ENTRY = re.compile(r"- Document: (.+) \| Section: (.+)")

# ---------------------------------------------------------------------------
# Two bounds on a turn that were literals inside the functions below and are now
# named, because a SECOND caller reads them (D1/P2, .dev/plans/260807-d1-agent-
# invocation.md). Neither value changes; this is extraction, not tuning.
#
# The eval task drives the same `_run_sdk_turn` with the same wall-clock ceiling,
# and it stamps the retrieve cap on the run's provenance. A second copy of either
# number in eval.py would be the audit's D3 defect wearing new clothes: the
# deploy gate's eval query fails open to this day because one call site kept its
# own copy of a column name. So there is one copy, here, and the other reader
# imports it.
# ---------------------------------------------------------------------------

#: How much of a `retrieve` tool result is captured onto `tool_calls_log`.
#:
#: CHANGED MEANING 2026-08-15 (BACKLOG 5.16), and the old comment here described
#: the behaviour this constant no longer has. It used to say the Auditor read
#: this value "further trimmed to 600 chars per context in the validator
#: dispatch below". **The Auditor no longer reads it on the happy path and
#: nothing trims to 600 chars anywhere.** This is now the AUDIT capture only: it
#: bounds a value that reaches a jsonb column, and it is what the judge falls
#: back to when a payload cannot be decoded at all.
RETRIEVE_RESULT_CAPTURE_CHARS = 1800

#: Whether the SDK marked this retrieve result an error. `retrieve_tool` returns
#: its DoS-guard refusal as ordinary text with is_error set, so the judge context
#: builder needs the flag to tell a refusal apart from a passage.
RETRIEVE_RESULT_IS_ERROR_KEY = "result_is_error"

#: The key on a `tool_calls_log` retrieve entry that carries the retrieved
#: chunks as ONE STRING PER CHUNK, untruncated. Beside it, `result` keeps the
#: audit capture unchanged — `str(block.content)[:RETRIEVE_RESULT_CAPTURE_CHARS]`,
#: which is a Python repr of the SDK content block cut mid-structure.
#:
#: WHY BOTH. The eval scores Faithfulness / ContextPrecision / ContextRecall
#: against what this turn retrieved. Handing it `result` handed the judge (a) a
#: repr — `[{'type': 'text', 'text': "<<<HEADER>>>\n[{'chunk_id': ...` — whose
#: dict-syntax noise is most of the token budget, (b) cut at 1800 chars, which is
#: below ONE full chunk on any realistic corpus, so essentially every retrieving
#: turn was at the cap and `retrieved_context_at_cap` was a constant rather than
#: an observation, and (c) as a SINGLE element, which collapses ContextPrecision's
#: ranking semantics to a coin flip over one blob. Three ways for the capture
#: format to dominate the score of the thing being measured.
#:
#: `result` is deliberately NOT changed: the Auditor and the retrieval-faithfulness
#: sampler read it and the chat path stays byte-for-byte.
RETRIEVE_CHUNKS_KEY = "retrieved_chunks"

#: The key carrying the same chunks rendered FOR THE GROUNDING JUDGE: content
#: plus the provenance the agent saw (document_id, section, chunk_id, score).
#:
#: Separate from RETRIEVE_CHUNKS_KEY because the two readers need different
#: things and BACKLOG 5.18 is what happens when one serves both. Ragas scores
#: text against text, so provenance is noise to it. The Auditor is asked whether
#: a claim is supported, and a claim naming a document or a section cannot be
#: supported by a context that contains neither.
RETRIEVE_JUDGE_CHUNKS_KEY = "judge_chunks"

#: Companion to the key above: 'chunks' when the framed payload was split back
#: into per-chunk strings, 'unparsed' when it could not be. Never absent, so the
#: eval reports a turn whose contexts could not be read as an unparsed
#: observation instead of as a turn that retrieved nothing.
RETRIEVE_CHUNKS_SOURCE_KEY = "retrieved_chunks_source"
RETRIEVE_CHUNKS_PARSED = "chunks"
RETRIEVE_CHUNKS_UNPARSED = "unparsed"

#: Wall-clock ceiling on one SDK turn, enforced by asyncio.wait_for.
#: D-11 raised it from 30s to 90s — a warm-but-not-hot Agent SDK subprocess needs
#: up to 90s on slower ARM VMs; the SSE layer retains 120s (30s headroom). The
#: eval's per-run cost ceiling is derived from this value rather than from a
#: guess about it.
AGENT_TURN_TIMEOUT_S = 90


# ---------------------------------------------------------------------------
# Reading a retrieve tool result back out of the SDK stream (D1/P2 review)
# ---------------------------------------------------------------------------


def _tool_result_text(content: object) -> str:
    """The TEXT of a ToolResultBlock, whatever shape the SDK handed it in.

    `str(block.content)` — what the audit capture below still does — is a Python
    repr when the block carries the MCP list-of-blocks shape our tools return
    (`[{'type': 'text', 'text': '...'}]`). Reprs are fine for an audit column and
    ruinous for a judge: the dict syntax is noise the metric cannot distinguish
    from evidence.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            else:
                text = getattr(block, "text", None)
                if text is not None:
                    parts.append(str(text))
        if parts:
            return "\n".join(parts)
    return str(content)


def _retrieved_chunk_records(result_text: str) -> list | None:
    """Split a framed retrieve result back into the CHUNK OBJECTS it carried.

    `agent_tools.retrieve_tool` returns `_frame_retrieved_context(str(chunks))` —
    the header, then the repr of a list of chunk dicts, then the footer. This
    undoes exactly that.

    THE ONLY PARSER. Two renderings hang off it and both must describe the same
    retrieval: `_retrieved_chunk_texts` (content only, what `eval.py` scores) and
    `_judge_chunk_record` (content plus provenance, what the Auditor judges).
    Parsing twice would let them disagree about what a turn retrieved.

    Returns None — never `[]` — when the payload cannot be read, because "this
    turn retrieved nothing" and "this turn retrieved something this function
    could not parse" are different observations and the second must not be
    reported as the first. `ast.literal_eval` (never `eval`) is the parser: the
    payload originates from a tool result and is therefore attacker-influenced
    text, so it may only ever become data.
    """
    text = result_text
    header_at = text.find(RETRIEVED_CONTEXT_HEADER)
    if header_at == -1:
        return None
    payload = text[header_at + len(RETRIEVED_CONTEXT_HEADER):]
    footer_at = payload.rfind(RETRIEVED_CONTEXT_FOOTER)
    if footer_at != -1:
        payload = payload[:footer_at]

    try:
        chunks = ast.literal_eval(payload.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(chunks, list):
        return None
    return chunks


def _chunk_texts_from_records(records: list) -> list[str]:
    """The eval's rendering: one content string per chunk, empties dropped.

    Extracted unchanged from `_retrieved_chunk_texts` when `_judge_chunk_record`
    was added, so `eval.py`'s input is byte-identical to what it was before.
    """
    texts: list[str] = []
    for chunk in records:
        if isinstance(chunk, dict):
            content = chunk.get("content", "")
        else:
            content = chunk
        rendered = str(content) if content is not None else ""
        if rendered:
            texts.append(rendered)
    return texts


def _retrieved_chunk_texts(result_text: str) -> list[str] | None:
    """ONE STRING PER CHUNK, content only. Read by `eval.py`; see the parser above."""
    records = _retrieved_chunk_records(result_text)
    if records is None:
        return None
    return _chunk_texts_from_records(records)


def _judge_chunk_record(chunk: object) -> str:
    """Render ONE retrieved chunk for the grounding judge, metadata included.

    BACKLOG 5.18. `retrieve_tool` returns the repr of full chunk dicts, so the
    agent sees `chunk_id`, `document_id`, `section` and `score` alongside the
    text. `_retrieved_chunk_texts` keeps `content` alone, which is right for the
    eval (Ragas scores text against text) and wrong for the Auditor: **an answer
    that cites a document or section name has no support in a context that
    contains neither.** That is 5.16's own failure mode one level down — the
    judge marking a claim unsupported because it was not shown the evidence.

    Not fixed by changing `_retrieved_chunk_texts`: `eval.py` imports it, and
    changing what it returns changes Ragas scoring, which is a measurement
    decision of its own. One parse, two renderings, one reader each.

    The rendering is a labelled header line then the content, rather than the raw
    dict repr the agent saw. Same information, without the dict syntax that
    `RETRIEVE_CHUNKS_KEY`'s own docstring records as dominating the token budget.
    """
    if not isinstance(chunk, dict):
        return str(chunk)
    fields = [
        f"{label}: {chunk[key]}"
        for label, key in (
            ("source", "document_id"),
            ("section", "section"),
            ("chunk", "chunk_id"),
            ("score", "score"),
        )
        if chunk.get(key) not in (None, "")
    ]
    content = str(chunk.get("content", "") or "")
    return f"[{' | '.join(fields)}]\n{content}" if fields else content


def _attach_retrieve_capture(tc: dict, raw: object, block: "ToolResultBlock") -> None:
    """Write every retrieve capture onto one `tool_calls_log` entry.

    One function so the id-matched path and the positional fallback cannot drift
    apart. They did not, when this was two copies of the same block, but 5.1's
    lesson is that two writers of one rule is how two answers start disagreeing.
    """
    tc["result"] = str(raw)[:RETRIEVE_RESULT_CAPTURE_CHARS]
    # BACKLOG 5.16: an errored retrieve is not evidence. retrieve_tool returns
    # its DoS-guard refusal as ordinary text with is_error set, so without this
    # the sentence "Retrieve quota exceeded for this turn" reaches the grounding
    # judge as a retrieved passage.
    tc[RETRIEVE_RESULT_IS_ERROR_KEY] = bool(getattr(block, "is_error", False))
    records = _retrieved_chunk_records(_tool_result_text(raw))
    tc[RETRIEVE_CHUNKS_KEY] = _chunk_texts_from_records(records or [])
    tc[RETRIEVE_JUDGE_CHUNKS_KEY] = [
        rendered
        for rendered in (_judge_chunk_record(r) for r in (records or []))
        if rendered
    ]
    tc[RETRIEVE_CHUNKS_SOURCE_KEY] = (
        RETRIEVE_CHUNKS_PARSED if records is not None else RETRIEVE_CHUNKS_UNPARSED
    )


def _record_tool_result(
    block: "ToolResultBlock",
    *,
    tool_names_by_use_id: dict[str, str],
    tool_calls_log: list[dict],
    job_id: str,
    db,
    redis,
) -> None:
    """Record one MCP tool result: the SSE event plus the two retrieve captures.

    Module scope, not nested inside `_run_sdk_turn`, because
    `test_agent_options_seam.test_agent_py_has_no_nested_function_definitions`
    forbids nested defs in this file — the static seam guards attribute calls to
    the module-scope function containing them, and a nested def can hide a second
    `ClaudeAgentOptions` construction from that attribution.

    The tool NAME comes from `tool_names_by_use_id`, never from the block: a
    `ToolResultBlock` declares only `tool_use_id` / `content` / `is_error`
    (claude_agent_sdk types.py:944-949) and has no `name` at all. The previous
    `getattr(block, "name", "unknown")` could therefore only ever produce
    "unknown", and `retrieval_eval.py:194` selects job_events on
    `payload["tool_name"] == "retrieve"` — which "unknown" never matches. That
    was a second defect stacked under the dead-branch one (BACKLOG 5.9): fixing
    the message type alone would have emitted events that still joined to
    nothing. Fields are read as attributes rather than via `getattr` defaults so
    a shape mismatch raises instead of quietly becoming a plausible value.
    """
    use_id = block.tool_use_id
    tool_name_short = tool_names_by_use_id.get(use_id, "unknown")
    if tool_name_short == "unknown":
        # Should be unreachable: a result always follows the ToolUseBlock that
        # produced it, and every one of those is recorded by the caller. Logged
        # rather than silently tolerated, because "unknown" is exactly the value
        # the old defect produced for EVERY result and it joins to nothing
        # downstream. If this line ever appears, the retrieve capture below was
        # skipped and the Auditor's context for that turn is short.
        log.warning(
            "_run_sdk_turn.tool_result_unresolved",
            job_id=job_id,
            tool_use_id=use_id,
            known_ids=len(tool_names_by_use_id),
        )
    raw = block.content
    emit(
        job_id,
        "agent.tool_result",
        {
            "tool_name": tool_name_short,
            "summary": str(raw)[:200],
        },
        db,
        redis,
    )
    # TWO captures of one retrieve result, for two readers.
    #
    # `result` — unchanged, byte-for-byte: the Auditor's retrieved_context_json
    # and the retrieval-faithfulness sampler read it, and
    # RETRIEVE_RESULT_CAPTURE_CHARS bounds it because it also reaches a jsonb
    # column.
    #
    # RETRIEVE_CHUNKS_KEY — the same result decoded into one string per CHUNK,
    # untruncated, for the eval (D1/P2 review). Handing Ragas `result` handed it
    # a repr, cut below one full chunk, in a single-element list; the capture
    # format then dominated Faithfulness and ContextPrecision. Not persisted:
    # _persist_messages writes tool_name / input / result only.
    if tool_name_short != "retrieve":
        return
    # BACKLOG 5.21: attach to the call that PRODUCED this result, by id.
    #
    # The old rule was "the most recent retrieve entry without a result", walking
    # in reverse. The SDK may emit several ToolUseBlocks before any result
    # returns, and then the first result to arrive lands on the LAST call issued
    # — so the chunks are attributed to the wrong query, silently, and the
    # ordering claim in _judge_retrieved_context's docstring would be false.
    # `red_team_probe.py`'s single `pending_skill` variable had this defect by
    # construction (5.9); this is the same shape and the block carries the id
    # that settles it.
    target = next(
        (tc for tc in tool_calls_log if tc.get("tool_use_id") == use_id), None
    )
    if target is not None:
        if "result" not in target:
            _attach_retrieve_capture(target, raw, block)
        return
    # No entry carries the id. Pre-5.21 logs and hand-built fixtures look like
    # this, so the positional walk stays as a fallback — but it is LOGGED,
    # because it is the shape that produced the mis-attribution and a silent
    # fallback would hide a regression in the id plumbing above.
    log.warning(
        "_record_tool_result.no_tool_use_id_match",
        job_id=job_id,
        tool_use_id=use_id,
        note="falling back to positional attachment; attribution is unreliable "
             "if this turn used parallel tool calls",
    )
    for tc in reversed(tool_calls_log):
        if tc.get("tool_name") == "retrieve" and "result" not in tc:
            _attach_retrieve_capture(tc, raw, block)
            break


def _judge_retrieved_context(tool_calls_log: list[dict]) -> tuple[list[str], int]:
    """The retrieved context the Auditor is judged against — what the AGENT saw.

    BACKLOG 5.16. This used to be
    `json.dumps([str(r)[:600] for r in retrieve_results][:3])`, which cut the
    grounding judge's evidence three separate ways at once:

        [:600]        600 chars per call, against MAX_CHUNKS(5) *
                      CHUNK_CONTENT_CHAR_LIMIT(2000) = 10,000 the agent was shown
        [:3]          retrieve calls 4+ dropped entirely (the cap is 8)
        tc["result"]  a Python REPR of the SDK block, itself already cut at
                      RETRIEVE_RESULT_CAPTURE_CHARS — the AUDIT capture, bounded
                      because it reaches a jsonb column, not because 1800 chars
                      is a meaningful amount of evidence

    So the judge was asked "is this answer supported by its context?" while being
    shown roughly half of it. The first valid verdict in the platform's history
    (E2E-3, 2026-08-13) marked the agent's price claims unsupported and said so
    in its reason — *because it was never shown the price rows*. That `partial`
    is an artefact of this function's predecessor, not a judgement about the
    response, and `verified_qa_candidates` (confidence >= 0.90) is starved by it.

    THE RULE IS "the judge sees exactly what the agent saw", NOT a bigger number.
    No cap is applied here, because the bound already exists upstream and belongs
    to the retrieval layer:

        MAX_CHUNKS(5) * CHUNK_CONTENT_CHAR_LIMIT(2000)
                      * _RETRIEVE_CALLS_PER_TURN_MAX(8)   = 80,000 chars

    all three in agent_tools.py. A ceiling chosen at this call site would drift
    away from that contract the first time any of them moved, which is 2.13's
    history exactly.

    **The bounding constant is the retrieve-call cap, NOT `max_turns`.**
    `max_turns=6` bounds assistant turns and parallel tool use puts several
    retrieves in one turn, so citing it here was wrong in the first version of
    this docstring and understated the worst case by a third.

    The scope of what travels is deliberately narrow: only `retrieve` results.
    `lookup_structured` returns customer rows and has never been in this channel,
    which is what keeps BACKLOG 0.4's egress question separate from this one.

    FOUR STATES, COUNTED SEPARATELY, because collapsing them is how the first
    version of this function was wrong. A retrieve call is one of:

        chunks    decoded, with hits      -> every chunk, untruncated
        empty     decoded, zero hits      -> contributes NOTHING, and that is
                                             the honest answer: the corpus had
                                             no match, so there is no evidence
        unparsed  payload undecodable     -> the audit capture, degraded
        errored   is_error on the block   -> contributes NOTHING

    `errored` matters more than it looks. When a turn trips the DoS guard,
    retrieve_tool returns "Retrieve quota exceeded for this turn" with
    is_error set (agent_tools.py:454-475). That is a control message the AGENT
    read as a failure. Feeding it to the judge as evidence puts a sentence about
    quotas into the RETRIEVED CONTEXT block of the turn least likely to be well
    grounded.

    WHERE THIS DELIBERATELY DIFFERS FROM eval.py:489-498, which reads the same
    capture: the eval contributes nothing for an undecodable call and excludes
    the row as unscorable. This function hands over the audit capture instead,
    because the judge chain has no "unscorable" verdict. An empty context makes
    every claim unsupported, so silence here manufactures an `ungrounded` that
    is about the decoder rather than the answer. Degraded-and-counted beats
    absent-and-silent when the consumer cannot abstain.

    Returns:
        (contexts, counts) — one string per CHUNK in retrieval order, and a
        per-state tally. The tally is the only way a later reader can tell a
        short context apart from a broken one.
    """
    contexts: list[str] = []
    counts = {"calls": 0, "chunks": 0, "empty": 0, "unparsed": 0, "errored": 0}
    for tc in tool_calls_log:
        if tc.get("tool_name") != "retrieve" or "result" not in tc:
            continue
        counts["calls"] += 1
        if tc.get(RETRIEVE_RESULT_IS_ERROR_KEY):
            counts["errored"] += 1
            continue
        # The SOURCE key, never an inference from an empty chunk list. Those are
        # different observations (agent_tools returns zero hits for a corpus miss
        # and _retrieved_chunk_texts returns None for a payload it cannot read),
        # and _retrieved_chunk_texts returns None rather than [] precisely so the
        # two stay distinguishable.
        if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
            counts["unparsed"] += 1
            contexts.append(str(tc.get("result") or ""))
            continue
        # RETRIEVE_JUDGE_CHUNKS_KEY, not RETRIEVE_CHUNKS_KEY: the judge needs the
        # provenance the agent saw as well as the text (BACKLOG 5.18).
        chunks = [str(c) for c in (tc.get(RETRIEVE_JUDGE_CHUNKS_KEY) or []) if c]
        if not chunks:
            counts["empty"] += 1
            continue
        counts["chunks"] += len(chunks)
        contexts.extend(chunks)
    return contexts, counts


def _published_context(tool_calls_log: list[dict]) -> list[str]:
    """What the TENANT published, for the PII firewall's exemption (BACKLOG 7.29).

    The firewall stops the agent leaking a CUSTOMER's personal data. It is not
    meant to stop it repeating the BUSINESS's own published contact details, and
    it was doing exactly that: three of twenty E2E-6 responses came back as the
    deflection, because the best-matching chunk was the corpus's "Contact and
    Escalation" section and a correct answer quotes the address in it.

    A THIRD RENDERING OF ONE PARSE, joining `_retrieved_chunk_texts` (what the
    eval scores) and `_judge_chunk_record` (what the Auditor judges). It differs
    from `_judge_retrieved_context` in the two places that decide whether an
    exemption is safe, so it cannot borrow that function:

        content only   RETRIEVE_CHUNKS_KEY, not RETRIEVE_JUDGE_CHUNKS_KEY. The
                       judge needs provenance; an allowlist needs the smallest
                       surface that answers the question.
        unparsed -> nothing   the judge falls back to the audit `result` repr
                       because it has no "unscorable" verdict. The firewall does
                       have one: contribute nothing, and today's behaviour (no
                       exemption, deflect) is what happens. Fail closed.

    WHAT IS DELIBERATELY NOT IN HERE, because each would be a bypass:

        the framed payload   it echoes the retrieve QUERY, so a customer who
                             types their own address into the chat would see it
                             come back exempted
        the customer message and history   the same bypass, one step shorter
        the agent soul       `pii_firewall`'s stated property is that nothing in
                             the soul reaches its behaviour (T-18-SEC-02)
        errored retrieves    `retrieve_tool` returns its DoS-guard refusal as
                             ordinary text with is_error set, and a refusal is
                             not published material

    Returns one string per retrieved chunk, in retrieval order.
    """
    published: list[str] = []
    for tc in tool_calls_log:
        if tc.get("tool_name") != "retrieve" or "result" not in tc:
            continue
        if tc.get(RETRIEVE_RESULT_IS_ERROR_KEY):
            continue
        if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
            continue
        published.extend(str(c) for c in (tc.get(RETRIEVE_CHUNKS_KEY) or []) if c)
    return published


def _dispatch_validation_chain(
    *,
    agent_id: str,
    job_id: str,
    response_text: str,
    message: str,
    conversation_id: str,
    tool_calls_log: list[dict],
) -> str:
    """Build the judge's context and dispatch the validation chain. Returns it.

    THIS FUNCTION EXISTS TO BE TESTABLE, and that is the whole point of the seam.
    An adversarial review of the first version of BACKLOG 5.16 reintroduced the
    defect five different ways — a truncating helper, a differently named
    variable, `itertools.islice`, a second assignment on the next line, and
    rebuilding the old repr while still calling `_judge_retrieved_context` — and
    the guards stayed green through all five, because they inspected the SHAPE OF
    A LINE rather than the VALUE `run_auditor` receives. Nothing in the repo
    observed that value: `retrieved_context_json` was built inside a 400-line
    task body no test could reach.

    So the rule this seam encodes: **guard the argument the consumer is handed,
    not the syntax that produces it.** A text-shaped guard only ever bans the one
    spelling its author thought of.

    Returns the JSON handed to run_auditor, so a caller (and a test) can assert
    on exactly what the judge will see.
    """
    contexts, counts = _judge_retrieved_context(tool_calls_log)
    retrieved_context_json = json.dumps(contexts)
    # Recorded, not inferred: E2E-6 calibrates this judge, and a calibration run
    # has to read what the judge was actually shown rather than reconstruct it
    # from whichever version of the code was current.
    log.info(
        "run_agent_turn.judge_context",
        job_id=job_id,
        agent_id=agent_id,
        chars=len(retrieved_context_json),
        **counts,
    )
    # OPS-07: run_retrieval_faithfulness is appended as the LAST step — it must
    # run strictly after run_auditor commits its verdict, since the
    # sample-rate-OR-auditor-flag gate is evaluated inside the task itself
    # (the Auditor's verdict does not exist yet at this dispatch point — see
    # retrieval_eval.py module docstring).
    celery_chain(
        run_gatekeeper.si(agent_id, job_id, response_text, message),
        run_auditor.si(agent_id, job_id, response_text, message,
                       retrieved_context_json, conversation_id),
        run_strategist.si(agent_id, job_id, response_text, message),
        run_retrieval_faithfulness.si(agent_id, job_id),
    ).apply_async(queue="runtime")
    return retrieved_context_json


# ---------------------------------------------------------------------------
# Module-level helpers — tenant DB writes via psycopg2 (parameterised only)
# ---------------------------------------------------------------------------

def _create_conversation_row(conn, agent_id: str) -> str:
    """Insert a new conversations row and return its UUID as a string.

    Uses psycopg2 directly (not SQLAlchemy) because the tenant DB is a
    separate Neon project — not the control DB that get_sync_db() connects to.

    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).

    Returns:
        UUID string of the newly created conversation row.
    """
    new_id = str(uuid.uuid4())
    sql = """
        INSERT INTO conversations (id, agent_id, created_at, metadata)
        VALUES (%s, %s, NOW(), %s::jsonb)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (new_id, agent_id, "{}"))
    conn.commit()

    log.debug("_create_conversation_row.done", conversation_id=new_id)
    return new_id


def _validate_conversation_owner(conn, conv_id: str, agent_id: str) -> dict | None:
    """Fetch conversation row for (conv_id, agent_id) — ownership guard.

    Returns:
        dict with keys "id" and "metadata" if the conversation belongs to agent_id.
        None if no matching row is found (ownership violation or not-found).

    Security (T-04-03-01): The AND agent_id = %s clause prevents cross-agent
    conversation hijacking via a guessed UUID.
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        SELECT id, metadata
        FROM conversations
        WHERE id = %s AND agent_id = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (conv_id, agent_id))
        row = cur.fetchone()

    if row is None:
        return None

    row_id, metadata = row
    return {"id": str(row_id), "metadata": metadata or {}}


def _set_sdk_session_id(conn, conv_id: str, sdk_session_id: str) -> None:
    """Store the SDK's internal session_id in conversations.metadata.

    Resolves R-02: SDK session_id captured from ResultMessage and persisted
    so subsequent turns can pass resume=sdk_session_id to ClaudeAgentOptions.

    Security (T-04-03-07): jsonb_set update uses parameterised %s values —
    sdk_session_id is never string-concatenated into SQL.
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        UPDATE conversations
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'),
            '{sdk_session_id}',
            to_jsonb(%s::text)
        )
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (sdk_session_id, conv_id))
    conn.commit()

    log.debug("_set_sdk_session_id.done", conversation_id=conv_id)


def _set_prompt_version_id(conn, conv_id: str, prompt_version_id: str) -> None:
    """Store the resolved prompt_version_id in conversations.metadata (OPS-16).

    Mirrors _set_sdk_session_id's jsonb_set path exactly, so subsequent turns
    on this conversation can read it back and reuse it without re-rolling
    (A-CANARY: canary is sticky per-conversation — no mid-conversation persona
    flip). Called exactly once, on the turn that first resolves a version for
    a conversation (see _resolve_turn_prompt_version below).

    Security (T-04-03-07): jsonb_set update uses parameterised %s values —
    prompt_version_id is never string-concatenated into SQL.
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        UPDATE conversations
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'),
            '{prompt_version_id}',
            to_jsonb(%s::text)
        )
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (prompt_version_id, conv_id))
    conn.commit()

    log.debug("_set_prompt_version_id.done", conversation_id=conv_id)


def _resolve_turn_prompt_version(
    db,
    *,
    agent_id: str,
    local_conversation_id: str,
    existing_prompt_version_id: str | None,
) -> tuple[str | None, dict | None, bool]:
    """Resolve the prompt version to serve this turn, sticky per conversation (OPS-16).

    READ ONLY — control DB. This function used to also WRITE the resolved id to
    conversations.metadata, and P1 moved the whole thing ahead of the seam
    because the soul fields it returns are an input to the system prompt. The
    write came along for the ride, so a turn that then died in
    build_agent_options left the conversation permanently sticky to a version
    that never served it, where the Celery retry previously re-rolled (BACKLOG
    2.6). Settled 2026-08-07: resolve before, commit after. The read stays here;
    the write is the caller's, behind a successful options build.

    First turn of a conversation (existing_prompt_version_id is None): calls
    resolve_prompt_version (weighted pick, control DB) and reports back that the
    caller must persist the choice, so every subsequent turn on this
    conversation reuses it (A-CANARY: no mid-conversation persona flip; the
    version is never re-rolled).

    Subsequent turns (existing_prompt_version_id provided): re-fetches that
    EXACT version's soul fields by id — never re-rolls, never re-picks, and
    nothing to persist because the id is already stored.

    T-21-09-05 (never fails a turn): any exception here is caught and treated
    as "no version resolved" — the caller falls back to the agent's live
    soul_* columns unchanged (soul_override=None passed to
    build_system_prompt), exactly like an agent with zero prompt_versions
    rows. A resolution failure degrades canary correlation only; it never
    blocks or fails the served turn.

    Returns:
        (prompt_version_id, soul_override, needs_persist).

        needs_persist is True only for a first turn that actually resolved a
        version — the one case where conversations.metadata does not yet hold
        the id. It is returned rather than re-derived by the caller from
        `existing_prompt_version_id is None` so that a future change to the
        resolution rules (a stale id that re-rolls, say) cannot leave the
        caller's copy of the logic silently disagreeing with this one.
    """
    try:
        if existing_prompt_version_id:
            pv = db.get(PromptVersion, existing_prompt_version_id)
            if pv is None:
                return None, None, False
            return str(pv.id), {
                "soul_role": pv.soul_role,
                "soul_voice": pv.soul_voice,
                "soul_do_list": pv.soul_do_list,
                "soul_donot_list": pv.soul_donot_list,
            }, False

        resolved_id, soul_override = resolve_prompt_version(db, agent_id)
        if resolved_id is None:
            return None, None, False

        return resolved_id, soul_override, True
    except Exception as exc:
        log.warning(
            "run_agent_turn.prompt_version_resolve_failed",
            agent_id=agent_id,
            conversation_id=local_conversation_id,
            error=str(exc),
        )
        return None, None, False


def _persist_messages(
    conn,
    conv_id: str,
    user_msg: str,
    assistant_msg: str,
    tool_calls_log: list[dict],
) -> str:
    """Insert user message, assistant message, and tool_call rows.

    Inserts in a single transaction:
      1. user message row in messages table
      2. assistant message row in messages table
      3. one tool_calls row per ToolUseBlock observed during the turn

    All values are passed as %s parameters (T-04-03-07).
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).

    Returns the assistant message's id (WIRE-05): the caller needs it to put
    on the terminal agent.response event's payload, because the customer
    widget cannot submit feedback for a reply it has no way to name.
    """
    with conn.cursor() as cur:
        # Insert user message
        user_msg_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (%s, %s, 'user', %s, NOW())
            """,
            (user_msg_id, conv_id, user_msg),
        )

        # Insert assistant message
        assistant_msg_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (%s, %s, 'assistant', %s, NOW())
            """,
            (assistant_msg_id, conv_id, assistant_msg),
        )

        # Insert tool_calls rows (one per ToolUseBlock observed)
        for tc in tool_calls_log:
            cur.execute(
                """
                INSERT INTO tool_calls (id, message_id, tool_name, arguments, result, created_at)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, NOW())
                """,
                (
                    str(uuid.uuid4()),
                    assistant_msg_id,
                    tc.get("tool_name", ""),
                    json.dumps(tc.get("input", {})),
                    json.dumps(tc.get("result", {})),
                ),
            )
    conn.commit()

    log.debug(
        "_persist_messages.done",
        conversation_id=conv_id,
        tool_call_count=len(tool_calls_log),
    )

    return assistant_msg_id


def _write_turn_metrics(
    conn,
    *,
    job_id: str,
    conversation_id: str,
    agent_id: str,
    cost_usd: float | None,
    num_turns: int | None,
    latency_ms: int,
    escalated: bool,
    tool_count: int,
    stop_reason: str | None,
    prompt_version_id: str | None = None,
) -> None:
    """Insert one turn_metrics row for a completed production turn (OPS-01).

    Called AFTER the terminal agent.response emit and the idempotency-marking
    db.commit() in run_agent_turn — telemetry must never delay or block the
    served turn (must_haves prohibition).

    prompt_version_id (OPS-16): the version resolved by
    _resolve_turn_prompt_version for this turn (None when the agent has no
    prompt_versions rows yet, or resolution failed — see T-21-09-05). The
    column exists since migration 0009, reserved for exactly this write.

    Failure handling: any exception here is caught by the caller
    (run_agent_turn wraps this call in its own try/except) — a metrics write
    failure degrades observability only, it never fails a served turn
    (T-21-01-03).

    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (same PROD-05 convention as _persist_messages).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO turn_metrics
                (id, job_id, conversation_id, agent_id, cost_usd, num_turns,
                 latency_ms, escalated, tool_count, stop_reason,
                 prompt_version_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                str(uuid.uuid4()),
                job_id,
                conversation_id,
                agent_id,
                cost_usd,
                num_turns,
                latency_ms,
                escalated,
                tool_count,
                stop_reason,
                prompt_version_id,
            ),
        )
    conn.commit()

    log.debug(
        "_write_turn_metrics.done",
        job_id=job_id,
        conversation_id=conversation_id,
        latency_ms=latency_ms,
        tool_count=tool_count,
    )


def _emit_langfuse_turn_trace(
    *,
    job_id: str,
    agent_id: str,
    model: str,
    num_turns: int | None,
    total_cost_usd: float | None,
    latency_ms: int,
    stop_reason: str | None,
) -> None:
    """Emit one Langfuse v4 trace+generation for a completed agent turn (OPS-04).

    Mirrors validation_service.py's `_log_verdict` shape exactly:
    start_as_current_generation(...) context manager + create_score(trace_id=...)
    + a single flush() call. Correlated to the turn_metrics row by job_id
    (both use job_id as the Langfuse trace_id / SQL correlation key).

    No-ops when _langfuse is None (LANGFUSE_PUBLIC_KEY unset — Pitfall 3/5).
    flush() is called exactly ONCE per turn, not per generation/score, to
    avoid the synchronous-flush latency hang documented in RESEARCH.md
    Pitfall 3 (Phase 15 live-verification: 596s -> 52s after removing
    per-call flush on a hot path).

    Never raises: the caller (run_agent_turn) also wraps this in try/except,
    but the internal try/except here means a Langfuse outage degrades
    observability only — it can never fail or stall a served turn
    (T-21-01-02, CLAUDE.md Rule 6 — v4 API only, no start_span/start_generation).
    """
    if _langfuse is None:
        return
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="agent-turn",
            model=model,
            metadata={"agent_id": agent_id, "job_id": job_id},
            output={
                "num_turns": num_turns,
                "total_cost_usd": total_cost_usd,
                "stop_reason": stop_reason,
            },
        ):
            pass  # generation data is set via context manager params

        if total_cost_usd is not None:
            _langfuse.create_score(
                name="turn_cost_usd",
                value=total_cost_usd,
                trace_id=job_id,
                data_type="NUMERIC",
            )
        _langfuse.create_score(
            name="turn_latency_ms",
            value=latency_ms,
            trace_id=job_id,
            data_type="NUMERIC",
        )
        _langfuse.flush()  # once per turn — never per-generation (Pitfall 3)
    except Exception as exc:
        log.warning("langfuse.agent_turn_trace_failed", job_id=job_id, error=str(exc))


# ---------------------------------------------------------------------------
# Citation extractor
# ---------------------------------------------------------------------------

def _extract_citations(text: str) -> list[dict]:
    """Parse the CITATIONS block from response text.

    Returns:
        List of {"document_name": str, "section": str} dicts.
        Empty list if no CITATIONS block is present (logs WARNING — not an error).
    """
    match = CITATIONS_REGEX.search(text)
    if not match:
        log.warning("citation_block_missing", response_length=len(text))
        return []

    citations = []
    for entry_match in _CITATION_ENTRY.finditer(match.group(1)):
        citations.append({
            "document_name": entry_match.group(1).strip(),
            "section": entry_match.group(2).strip(),
        })
    return citations


# ---------------------------------------------------------------------------
# THE SEAM — the one place ClaudeAgentOptions is constructed
#
# D1 (.dev/plans/260807-d1-agent-invocation.md, P1). The nightly eval scored
# reference answers against the contexts those answers were written from and
# never invoked the agent at all. The fix is to make the eval invoke it — and
# the only version of that fix worth having is one where the eval and the
# customer are served by the SAME agent. "Same agent" is not the same model id;
# it is the system prompt, the tool server (which is where the capability
# envelope is enforced), the allowed-tool list, the turn and budget ceilings and
# the model, together. Assemble any of those twice and the eval measures
# something adjacent to the product, which the measurement-layer audit records
# as this repo's recurring defect.
#
# So they are assembled exactly once, here, and both callers go through it.
# tests/unit/test_agent_options_seam.py fails if run_agent_turn constructs
# options — or a tool server, or a system prompt — by any other route. That test
# is the mechanism; this comment is only its explanation.
#
# SETTLED, AND IT IS WHY P2 CAN NOW PROCEED (BACKLOG 2.5, owner, 2026-08-07).
# The options this returns carry a LIVE tool server bound to the tenant's real
# connection string. Every caller of this seam therefore reaches, by default:
#   * retrieve            -> write_retrieval_metrics(conn_str, …) into the tenant DB
#   * escalate_to_human   -> _mark_conversation_escalated(…) + send_escalation_email
#   * the 6 mutating transactional skills -> a tool_calls_audit row AND the real
#     ProviderAdapter: place_order, cancel_order, issue_refund,
#     update_subscription, book_slot, update_customer_record.
# The plan chose approach (b) over (a) precisely to keep eval traffic out of
# tenant data; (b) as built still wrote to tenant tables and could move money —
# one eval scenario in which the agent decides to refund executed a refund.
#
# The answer is `side_effects`, below: MANDATORY, no default. A default is
# exactly the mechanism by which the eval path silently ends up live, so a caller
# that does not state which it wants raises TypeError at the call site rather
# than discovering the question against a real tenant at 3am.
#
# The alternative — a read-only allowed_tools subset for the eval — was rejected,
# and the reason is worth keeping here because it constrains every future change
# to this function: removing the mutating skills would make the eval measure an
# agent with fewer capabilities than production serves, and a scenario testing
# "the agent should refuse to refund here" could no longer FAIL, because the
# agent could not even try. An agent that cannot attempt the wrong thing cannot
# be measured on refusing it. So allowed_tools is identical in both modes, and
# the capability envelope, IDV gate and Actor seam all still run; what changes is
# only the outer edge, where a call would leave this process. notify_fn is now a
# parameter of that edge (it was deliberately hardcoded in P1 — "an unused escape
# hatch added before the caller that needs it exists is how a seam starts
# drifting"; P2 is that caller, so the hatch is no longer unused).
# ---------------------------------------------------------------------------

def build_agent_options(
    *,
    agent,
    conn_str: str,
    conversation_id: str,
    job_id: str,
    side_effects: SideEffectMode,
    verified_session_token: str = "",
    soul_override: dict | None = None,
    resume: str | None = None,
) -> "ClaudeAgentOptions":
    """Build the ClaudeAgentOptions for one turn of `agent`.

    Side effect, and it is the point: build_tool_server sets the per-task
    ContextVars (conn_str, agent_id, tenant_id, strategy, conversation_id,
    notify_fn, job_id, verified session token, retrieve counter) that every tool
    handler reads. Calling this twice for one turn would leave the second call's
    context in force — hence the "exactly once" pin in the seam test.

    Args:
        agent:                  Control-DB Agent row. Supplies id, tenant_id,
                                name, retrieval_strategy and the soul fields
                                build_system_prompt reads.
        conn_str:               Decrypted tenant DB connection string. Never
                                logged, never a task arg (CTL-08).
        conversation_id:        Conversation UUID string — escalation writes and
                                tool-side conversation scoping.
        job_id:                 Celery job id (OPS-05/06 retrieval metrics).
        side_effects:           MANDATORY, no default (BACKLOG 2.5). "live" is
                                production, byte for byte what the chat path has
                                always done. "recorded" is the eval path: the
                                escalation notification, the retrieval_metrics
                                write and the transactional ProviderAdapter are
                                suppressed and recorded instead. Everything the
                                agent can see or choose is identical.
        verified_session_token: IDV-05 token, "" when there is no verified
                                session. NEVER logged (T-04-03-05).
        soul_override:          Prompt-version soul fields (OPS-16) or None to
                                serve the agent's live soul_* columns.
        resume:                 SDK session id to continue, or None to start one.

    Raises:
        ValueError: side_effects is neither "live" nor "recorded". Literal is a
            type-checker annotation and enforces nothing at run time, so an
            unrecognised value would compare unequal to "recorded" and be served
            as live — a real refund on the eval path.
    """
    # The mode is process-context sticky and the Celery prefork pool does not
    # isolate contextvars per task, so a previous turn's value is still in force
    # on entry. Today build_tool_server below always republishes it — but only
    # if we REACH it, and three things above it raise: this validation, the
    # RetrievalStrategy parse, and build_tool_server's own. A turn that dies in
    # any of them would leave a stale "recorded" behind for whatever ran next in
    # this context. Resetting FIRST, before anything that can throw, makes that
    # a property of this function rather than of the call graph's current shape.
    reset_side_effect_context()

    if side_effects not in SIDE_EFFECT_MODES:
        raise ValueError(
            f"build_agent_options: side_effects must be one of {SIDE_EFFECT_MODES}, "
            f"got {side_effects!r}. There is no third mode and no fallback: an "
            f"unrecognised value read as live is how an eval scenario issues a real "
            f"refund against the tenant's provider (BACKLOG 2.5)."
        )

    strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})

    # The escalation edge. On the eval path the mail is recorded rather than
    # sent — a scenario that drives the agent to escalate would otherwise page
    # the owner about a customer who does not exist, and would do it nightly.
    # A conditional expression rather than two `def`s: nested function
    # definitions in this module are banned by the seam suite, which attributes
    # every call to the module-scope function containing it.
    notify_fn = (
        (lambda reason, context: send_escalation_email(agent, reason, context))
        if side_effects == "live"
        else (
            lambda reason, context: record_suppressed_side_effect(
                "escalation.notify",
                {
                    "agent_id": str(agent.id),
                    "conversation_id": str(conversation_id),
                    "reason": reason,
                    "context": context,
                },
            )
        )
    )

    tool_server = build_tool_server(
        conn_str=conn_str,
        agent_id=str(agent.id),
        agent_name=agent.name,
        strategy=strategy,
        conversation_id=str(conversation_id),
        notify_fn=notify_fn,
        tenant_id=str(agent.tenant_id),
        verified_session_token=verified_session_token,
        job_id=job_id,
        side_effects=side_effects,
    )

    system_prompt = build_system_prompt(agent, soul_override=soul_override)

    # D-10 note (13-07): The Voyage 3 RPM free-tier prompt-level retrieve-cap
    # instruction was removed now that embeddings move to Bedrock (PROD-06).
    # Bedrock has no comparable RPM constraint; the per-turn retrieve counter in
    # agent_tools.retrieve_tool remains active as a DoS guard (ceiling raised to 8).

    # R-05: allowed_tools use full MCP namespace mcp__customer-tools__*
    # D-10 fix phase 1 (2026-06-01): max_turns raised from 3 to 6.
    #   Root cause: max_turns=3 cut the agent off after the retrieve tool
    #   round-trip (tool_use + tool_result = 2 turns), leaving no turn to
    #   compose the final text answer → empty response_text.
    #   The Voyage RPM guard is now enforced solely by the tool-level counter
    #   in agent_tools.retrieve_tool (blocks the 3rd call per turn), making
    #   max_turns free to cover the full retrieve → synthesis cycle.
    #   6 turns is sufficient for: thinking + retrieve + synthesis + any
    #   clarify/escalate follow-ups while still bounding DoS risk (T-04-03-06).
    # D-10 fix phase 2 (2026-06-01): max_budget_usd raised from 0.05 to
    #   settings.AGENT_MAX_BUDGET_USD (default 0.50).
    #   Root cause (additional): the 0.05 USD cap was too tight for a
    #   turn that uses extended thinking (~38s) + retrieved context + synthesis.
    #   A Haiku extended-thinking + retrieve + synthesis turn can exceed $0.05.
    #   When the budget is exceeded the CLI emits result{subtype:error_max_budget,
    #   is_error:true} → receive_response() terminates → response_text stays ""
    #   with no exception raised (identical empty-text signature to max_turns).
    #   0.50 USD gives headroom while still serving as a DoS guardrail.
    #   Configure via AGENT_MAX_BUDGET_USD env var for tighter prod limits.
    # R-05: allowed_tools suppresses SDK permission prompts only.
    # The capability envelope check inside each transactional tool handler
    # is the real access gate (fail-closed) — T-14-04-03.
    return ClaudeAgentOptions(
        # AGENT_TURN_MODEL, not a literal — eval_runs.config.model_id
        # reads the same constant, so a score can never be attributed
        # to a model that did not serve the turn (migration 0013).
        model=AGENT_TURN_MODEL,
        system_prompt=system_prompt,
        mcp_servers={"customer-tools": tool_server},  # type: ignore[dict-item]  # agent-sdk/anthropic stubs are narrower than the runtime contract
        allowed_tools=[
            # Original 4 tools — retained (TXN-04 requirement)
            "mcp__customer-tools__retrieve",
            "mcp__customer-tools__lookup_structured",
            "mcp__customer-tools__escalate_to_human",
            "mcp__customer-tools__clarify",
            # Phase 14 Plan 04 — 7 transactional tools
            # Listing here suppresses SDK permission prompts only;
            # the capability envelope in each handler is the real gate.
            "mcp__customer-tools__place_order",
            "mcp__customer-tools__cancel_order",
            "mcp__customer-tools__issue_refund",
            "mcp__customer-tools__update_subscription",
            "mcp__customer-tools__book_slot",
            "mcp__customer-tools__update_customer_record",
            "mcp__customer-tools__confirm_action",
        ],
        resume=resume,
        max_turns=6,   # D-10 fix: was 3 (too low — cut off synthesis after retrieve)
        max_budget_usd=settings.AGENT_MAX_BUDGET_USD,  # D-10 fix phase 2: was 0.05 (too low for thinking+retrieve+synthesis)
    )


# ---------------------------------------------------------------------------
# Async SDK turn helper — bridged into sync Celery task via asyncio.run()
# ---------------------------------------------------------------------------

async def _run_sdk_turn(
    message: str,
    options: "ClaudeAgentOptions",
    job_id: str,
    local_conversation_id: str,
    conn_str: str,
    db,
    redis,
) -> dict:
    """Run one Claude Agent SDK turn and collect all streaming output.

    Opened and closed by the async context manager — do NOT close the client
    manually. Called via asyncio.run(asyncio.wait_for(_run_sdk_turn(...), timeout=90)).

    Returns:
        dict with keys:
            response_text       — accumulated text from TextBlock messages
            tool_calls_log      — list of dicts per ToolUseBlock observed
            escalated           — True if escalate_to_human ToolUseBlock was seen
            escalation_reason   — reason from escalate_to_human input, or None
            escalation_context  — context from escalate_to_human input, or None
            sdk_session_id      — ResultMessage.session_id, or None
            total_cost_usd      — ResultMessage.total_cost_usd, or None (OPS-01)
            num_turns           — ResultMessage.num_turns, or None (OPS-01)
            stop_reason         — ResultMessage.stop_reason, or None (OPS-01)

    Security (T-04-03-02, T-04-03-03):
        - Tool-call streaming is the evidence source for escalation detection.
          Escalation is set based on ToolUseBlock.name — not on parsed text prose.
        - system_prompt enforces persona lock via build_system_prompt().
    """
    response_text = ""
    tool_calls_log: list[dict] = []
    # tool_use_id -> short tool name. ToolResultBlock carries ONLY tool_use_id,
    # content and is_error (claude_agent_sdk types.py:944-949) — it has no `name`
    # field, so the result's tool can be known only by joining back to the
    # ToolUseBlock that produced it. See the block comment at the UserMessage
    # branch below for why this replaced `getattr(block, "name", "unknown")`.
    tool_names_by_use_id: dict[str, str] = {}
    escalated = False
    escalation_reason: str | None = None
    escalation_context: str | None = None
    sdk_session_id_out: str | None = None
    total_cost_usd_out: float | None = None
    num_turns_out: int | None = None
    stop_reason_out: str | None = None

    async with ClaudeSDKClient(options=options) as client:
        await client.query(message)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

                    elif isinstance(block, ToolUseBlock):
                        tool_name_short = block.name.removeprefix("mcp__customer-tools__")
                        tool_names_by_use_id[block.id] = tool_name_short
                        emit(
                            job_id,
                            "agent.tool_call",
                            {"tool_name": tool_name_short, "input": block.input},
                            db,
                            redis,
                        )
                        tool_calls_log.append({
                            "tool_name": tool_name_short,
                            "input": block.input,
                            # BACKLOG 5.21: carried so _record_tool_result can
                            # attach a result to the call that PRODUCED it.
                            # Without it the only available rule is "the most
                            # recent entry without a result", which mis-attributes
                            # under parallel tool use.
                            "tool_use_id": block.id,
                        })
                        # Escalation detection: based on ToolUseBlock evidence only (T-04-03-03)
                        if block.name.endswith("escalate_to_human"):
                            escalated = True
                            escalation_reason = block.input.get("reason")
                            escalation_context = block.input.get("context")

                    elif isinstance(block, ToolResultBlock):
                        # Kept for tolerance, not relied upon: message_parser.py:148
                        # can build one here, but no observed CLI output ever has.
                        _record_tool_result(
                            block,
                            tool_names_by_use_id=tool_names_by_use_id,
                            tool_calls_log=tool_calls_log,
                            job_id=job_id,
                            db=db,
                            redis=redis,
                        )

            elif isinstance(msg, UserMessage):
                # WHERE TOOL RESULTS ACTUALLY ARRIVE (BACKLOG 5.9).
                #
                # This branch did not exist. The loop read ToolResultBlock only
                # inside AssistantMessage, and the CLI emits tool results as
                # `{"type":"user", "message":{"role":"user","content":[{"type":
                # "tool_result",...}]}}` — so the branch was unreachable and every
                # downstream reader of a tool result was reading a channel nothing
                # ever wrote:
                #   - `agent.tool_result` job_events: never emitted, so
                #     retrieval_eval._fetch_turn_context always built [].
                #   - tc["result"]: never set, so the Auditor — the GROUNDING
                #     judge — was handed retrieved_context_json == "[]" on every
                #     single turn (agent.py's dispatch below).
                #   - RETRIEVE_CHUNKS_KEY: never set, so eval.py:495 always saw
                #     zero chunks and excluded the row as `no_retrieval`.
                #
                # Evidence for the message type, gathered before the change: the
                # SDK's own transcript readers treat tool_result as a user-entry
                # phenomenon (_internal/sessions.py:277-280 and
                # _internal/session_summary.py:81-92), and 42,334 tool_result
                # entries across 782 real CLI session transcripts are ALL
                # type:"user" — assistant-carried count zero.
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            _record_tool_result(
                                block,
                                tool_names_by_use_id=tool_names_by_use_id,
                                tool_calls_log=tool_calls_log,
                                job_id=job_id,
                                db=db,
                                redis=redis,
                            )

            elif isinstance(msg, ResultMessage):
                sdk_session_id_out = msg.session_id
                # D-10 fix phase 2: log the full stop reason so we can distinguish
                # error_max_turns / error_max_budget / error_during_execution / success.
                # These fields are the ONLY reliable disambiguator when response_text
                # is empty — all stop paths produce the same empty-text signature.
                # (See ResultMessage dataclass in claude_agent_sdk; field names are stable
                # in 0.1.81 and confirmed by inspection of the installed package.)
                log.info(
                    "_run_sdk_turn.result",
                    job_id=job_id,
                    subtype=msg.subtype,
                    is_error=msg.is_error,
                    num_turns=msg.num_turns,
                    total_cost_usd=msg.total_cost_usd,
                    stop_reason=msg.stop_reason,
                    api_error_status=msg.api_error_status,
                    response_length=len(response_text),
                )
                if msg.is_error:
                    log.warning(
                        "_run_sdk_turn.sdk_error",
                        job_id=job_id,
                        subtype=msg.subtype,
                        is_error=msg.is_error,
                        num_turns=msg.num_turns,
                        total_cost_usd=msg.total_cost_usd,
                    )
                # OPS-01: carry these through the return dict — previously logged
                # only, now persisted to turn_metrics by run_agent_turn (below).
                total_cost_usd_out = msg.total_cost_usd
                num_turns_out = msg.num_turns
                stop_reason_out = msg.stop_reason

    return {
        "response_text": response_text,
        "tool_calls_log": tool_calls_log,
        "escalated": escalated,
        "escalation_reason": escalation_reason,
        "escalation_context": escalation_context,
        "sdk_session_id": sdk_session_id_out,
        "total_cost_usd": total_cost_usd_out,
        "num_turns": num_turns_out,
        "stop_reason": stop_reason_out,
    }


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

#: Retry countdown for a tenant-DB connect that failed while the endpoint was
#: waking. The generic countdown in the handler below is ``2 ** retries`` — 1s
#: then 2s — which is shorter than a per-tenant Neon project's cold start, so a
#: wake-triggered retry on that schedule fires while the endpoint is still
#: suspended and burns the whole retry budget without ever reaching a live
#: endpoint.
#:
#: The value is set by arithmetic, not by picking a point in the 8-20s wake
#: window. ``max_retries=2`` buys three attempts and therefore only TWO
#: countdown gaps, and a refusal from a suspended endpoint can come back fast
#: rather than consuming its connect budget, so the attempts themselves may
#: contribute nothing. Cumulative delay before the FINAL attempt is
#: ``2 * countdown``, and that product is what has to clear the top of the wake
#: window — not the countdown itself. At 8s it is 16s, which leaves a 17-20s
#: wake exhausting all three attempts; at 10s it is 20s, which covers the
#: ceiling. Changing max_retries changes this number.
TENANT_WAKE_RETRY_COUNTDOWN_S = 10


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_agent_turn",
)
def run_agent_turn(
    self,
    job_id: str,
    agent_id: str,
    message: str,
    conversation_id: str | None,
    verified_session_token: str = "",
) -> dict:
    """Orchestrate one agent conversational turn with full SSE event emission.

    Idempotent: returns {"status": "already_complete", "job_id": job_id}
    immediately if an "agent.response" event row already exists for this
    job_id (duplicate delivery / retry safety).

    Args:
        job_id:                  UUID string of the runtime chat job.
        agent_id:                UUID string of the agent handling this conversation.
        message:                 Raw user message text. NEVER logged (T-04-03-05).
        conversation_id:         UUID string of an existing conversation, or None for
                                 the first turn (triggers conversation row creation).
        verified_session_token:  IDV-05 verified session token. NEVER logged
                                 (parity with message, T-04-03-05). Empty string
                                 when no verified session — all non-IDV calls pass
                                 through. Added in Phase 17 Plan 03; 4-arg existing
                                 dispatches remain valid via the empty default.

    Returns:
        {"status": "already_complete", "job_id": job_id}  — idempotent path
        {}                                                  — all other paths

    Security:
        conn_str is decrypted at runtime from agent.neon_connection_string
        (Fernet) and NEVER appears in task args, logs, or return values (CTL-08).
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Idempotency guard — exit immediately if agent.response already exists
        # for this job_id. Prevents duplicate SDK calls and duplicate SSE events
        # on Celery retry or at-least-once redelivery from Redis.
        # ------------------------------------------------------------------
        existing = db.execute(
            sa_text(
                "SELECT 1 FROM job_events"
                " WHERE job_id = :jid AND event_type = 'agent.response' LIMIT 1"
            ),
            {"jid": job_id},
        ).fetchone()
        if existing:
            log.info("run_agent_turn.idempotent_skip", job_id=job_id)
            return {"status": "already_complete", "job_id": job_id}

        # ------------------------------------------------------------------
        # Fetch agent from control DB — required for soul fields, retrieval
        # strategy, and the encrypted neon_connection_string.
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "run_agent_turn.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}

        # ------------------------------------------------------------------
        # Fetch job from control DB — required to update status on completion.
        # ------------------------------------------------------------------
        job = db.get(Job, job_id)
        if job is None:
            log.error("run_agent_turn.job_not_found", job_id=job_id)
            return {}

        # ------------------------------------------------------------------
        # Decrypt connection string at runtime — NEVER in task args (CTL-08).
        # conn_str is intentionally not logged.
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        # PROD-05: open ONE pooled tenant-DB connection for all per-turn write
        # helpers (_create_conversation_row, _validate_conversation_owner,
        # _set_sdk_session_id, _persist_messages).  Reduces connection churn
        # from 4 opens/closes per turn to 1.  Uses the pooled endpoint
        # (agent.neon_connection_string) — PgBouncer transaction-mode
        # compatible: no named prepared statements, no SET session vars.
        # Closed in finally even when _run_sdk_turn raises.
        #
        # The connect is INSIDE the try. It used to sit outside it, and a
        # suspended endpoint is the NORMAL state of a tenant DB idle for ~5
        # minutes — so the first message of every conversation landed on a
        # psycopg2.OperationalError that escaped this task's own except entirely:
        # no retry, no agent.failed, zero job_events, and a widget waiting on a
        # job that had already died. Observed on three live jobs, 2026-08-16.
        tenant_conn = None
        try:
            tenant_conn = psycopg2.connect(
                conn_str, connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S
            )
            # --------------------------------------------------------------
            # EVENT 1: agent.thinking — confirms task is running for this agent
            # --------------------------------------------------------------
            emit(job_id, "agent.thinking", {"agent_id": agent_id}, db, _redis)

            # --------------------------------------------------------------
            # Conversation branching (R-02 resolution):
            #   First turn  (conversation_id is None) → create row, resume=None
            #   Subsequent  (conversation_id provided) → validate ownership,
            #               sdk_resume = metadata.get("sdk_session_id")
            # --------------------------------------------------------------
            sdk_resume: str | None = None
            # OPS-16: only set on subsequent turns when the conversation already
            # carries a resolved prompt_version_id — see _resolve_turn_prompt_version.
            existing_prompt_version_id: str | None = None

            if conversation_id is None:
                # First turn: create conversation row in tenant DB
                local_conversation_id = _create_conversation_row(tenant_conn, agent_id)
                log.debug(
                    "run_agent_turn.first_turn",
                    job_id=job_id,
                    conversation_id=local_conversation_id,
                )
            else:
                # Subsequent turn: validate ownership (T-04-03-01)
                conv_row = _validate_conversation_owner(tenant_conn, conversation_id, agent_id)
                if conv_row is None:
                    log.warning(
                        "run_agent_turn.conversation_not_found",
                        job_id=job_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                    )
                    emit(
                        job_id,
                        "agent.failed",
                        {"error": "conversation_not_found"},
                        db,
                        _redis,
                    )
                    return {}
                local_conversation_id = conv_row["id"]
                sdk_resume = conv_row["metadata"].get("sdk_session_id")
                existing_prompt_version_id = conv_row["metadata"].get("prompt_version_id")

            # ----------------------------------------------------------------
            # OPS-16: canary prompt-version resolution — sticky per conversation,
            # never fails a turn (T-21-09-05). See _resolve_turn_prompt_version's
            # own docstring for the first-turn-vs-subsequent-turn distinction.
            #
            # This RESOLUTION runs BEFORE the tool server is built rather than
            # after. The soul fields it returns are an input to the system
            # prompt, and the system prompt is built inside build_agent_options
            # together with the tool server, so the resolution has to precede the
            # one call that consumes both. That part of P1's move stands.
            #
            # The WRITE does not: it now happens after build_agent_options
            # returns (BACKLOG 2.6, settled 2026-08-07 — "resolve before, commit
            # after"). _resolve_turn_prompt_version used to call
            # _set_prompt_version_id itself, so P1's move carried the commit
            # forward with the read, and a turn that then died in
            # RetrievalStrategy.model_validate or build_tool_server left the
            # conversation permanently sticky to a version that never served it
            # — where before P1 the Celery retry re-rolled. Pinned in both
            # directions by test_the_canary_choice_is_not_committed_when_the_
            # options_build_fails and ..._is_committed_once_the_options_exist.
            # ----------------------------------------------------------------
            prompt_version_id, soul_override, canary_needs_persist = _resolve_turn_prompt_version(
                db,
                agent_id=agent_id,
                local_conversation_id=str(local_conversation_id),
                existing_prompt_version_id=existing_prompt_version_id,
            )

            # --------------------------------------------------------------
            # THE SEAM (D1/P1). Retrieval strategy, tool server, system prompt,
            # model, allowed tools and the turn/budget ceilings are assembled in
            # build_agent_options above — the same callable the eval task goes
            # through — so the agent measured is the agent served. Constructing
            # any of them here instead is what test_agent_options_seam.py fails on.
            #
            # side_effects="live" is the chat path, stated rather than defaulted
            # (BACKLOG 2.5). This is the turn a customer is waiting on: its
            # refunds are real, its escalation mail must arrive, and its
            # retrieval_metrics row is what the ops room reads.
            # --------------------------------------------------------------
            options = build_agent_options(
                agent=agent,
                conn_str=conn_str,
                conversation_id=str(local_conversation_id),
                job_id=job_id,
                side_effects="live",
                verified_session_token=verified_session_token,
                soul_override=soul_override,
                resume=sdk_resume,
            )

            # --------------------------------------------------------------
            # BACKLOG 2.6: the canary choice becomes sticky only now that there
            # is an agent for it to be sticky to. A turn that died above
            # re-rolls on retry, as it did before P1.
            #
            # Wrapped, and never fatal (T-21-09-05): a tenant-DB failure here
            # must not fail a turn whose options are already built. The
            # consequence of that failure is narrower than it was — the version
            # still served this turn and turn_metrics still attributes the turn
            # to it, which is the honest record; only the stickiness is lost, so
            # the next turn of this conversation re-rolls.
            # --------------------------------------------------------------
            if canary_needs_persist and prompt_version_id:
                try:
                    _set_prompt_version_id(
                        tenant_conn, str(local_conversation_id), prompt_version_id
                    )
                except Exception as canary_exc:
                    log.warning(
                        "run_agent_turn.prompt_version_persist_failed",
                        job_id=job_id,
                        agent_id=agent_id,
                        conversation_id=str(local_conversation_id),
                        error=str(canary_exc),
                    )

            # --------------------------------------------------------------
            # Bridge async SDK into sync Celery worker.
            # asyncio.run() is the required pattern for Python 3.12 (see CLAUDE.md).
            # Wall-clock safety: asyncio.wait_for(timeout=90) is inside the run()
            # call. T-04-03-06 DoS guard: max_turns=6, max_budget_usd=settings.AGENT_MAX_BUDGET_USD.
            # D-11: raised from 30s to 90s — warm-but-not-hot Agent SDK subprocess
            # needs up to 90s on slower ARM VMs; SSE layer retains 120s (30s headroom).
            #
            # OPS-01: monotonic start captured immediately before the call so
            # latency_ms reflects only the SDK turn itself, not queueing/setup.
            # --------------------------------------------------------------
            _turn_start_monotonic = time.monotonic()
            result = asyncio.run(
                asyncio.wait_for(
                    _run_sdk_turn(
                        message=message,
                        options=options,
                        job_id=job_id,
                        local_conversation_id=local_conversation_id,
                        conn_str=conn_str,
                        db=db,
                        redis=_redis,
                    ),
                    timeout=AGENT_TURN_TIMEOUT_S,
                )
            )
            latency_ms = int((time.monotonic() - _turn_start_monotonic) * 1000)

            response_text: str = result["response_text"]
            tool_calls_log: list[dict] = result["tool_calls_log"]
            escalated: bool = result["escalated"]
            escalation_reason: str | None = result["escalation_reason"]
            escalation_context: str | None = result["escalation_context"]
            sdk_session_id_out: str | None = result["sdk_session_id"]
            total_cost_usd: float | None = result.get("total_cost_usd")
            num_turns: int | None = result.get("num_turns")
            stop_reason: str | None = result.get("stop_reason")

            # --------------------------------------------------------------
            # SEC-01/L4: synchronous PII output firewall (T-18-SEC-01, T-18-SEC-02).
            # Must run here, synchronously, because the Gatekeeper/Auditor/Strategist
            # validators dispatch ASYNCHRONOUSLY after the response has already
            # streamed to the customer (see the validator chord below) — a leak
            # cannot wait for a post-hoc judge. Rebinding response_text here, before
            # any consumer reads it, guarantees the served text (SSE emit), the
            # persisted text (_persist_messages), the cited text (_extract_citations)
            # and the judged text (the validator chord) can never diverge — a single
            # substitution covers all four. The firewall call below takes no flag and
            # reads no config, so nothing in the response text and nothing in the agent
            # soul can disable it. Citations are extracted from the deflection
            # when a flag fires, which correctly yields an empty citation list — a
            # deflection cites nothing.
            #
            # BACKLOG 7.29: the firewall is given what the tenant PUBLISHED this
            # turn, so an answer quoting the business's own contact address is no
            # longer deleted. Email only; card and SA ID have no exemption path.
            # The list is read for one set-membership test and never interpreted,
            # so a chunk that instructs the firewall off does nothing.
            published = _published_context(tool_calls_log)
            filtered_text, pii_detector = scan_response(
                response_text, published_context=published
            )
            if pii_detector is not None:
                log.warning(
                    "pii_firewall.response_deflected",
                    job_id=job_id,
                    agent_id=agent_id,
                    conversation_id=str(local_conversation_id),
                    detector=pii_detector,
                    original_length=len(response_text),
                    published_chunks=len(published),
                )
            elif published and detect_pii(response_text) is not None:
                # Passed only BECAUSE the address was published. Logged so the
                # exemption is observable rather than silent: this is the one
                # line that tells a later reader an address left the system on
                # the strength of the corpus, and which turn it was.
                log.info(
                    "pii_firewall.published_contact_allowed",
                    job_id=job_id,
                    agent_id=agent_id,
                    conversation_id=str(local_conversation_id),
                    published_chunks=len(published),
                )
            response_text = filtered_text

            # --------------------------------------------------------------
            # Citation extraction — missing block yields [] + warning (not failure)
            # --------------------------------------------------------------
            citations_list = _extract_citations(response_text)

            # --------------------------------------------------------------
            # R-02 resolution: persist SDK session_id for next-turn resume
            # Only on first turn; only when sdk_session_id was returned
            # --------------------------------------------------------------
            if conversation_id is None and sdk_session_id_out:
                _set_sdk_session_id(tenant_conn, local_conversation_id, sdk_session_id_out)

            # --------------------------------------------------------------
            # Persist messages and tool calls to tenant DB
            # --------------------------------------------------------------
            assistant_msg_id = _persist_messages(
                conn=tenant_conn,
                conv_id=local_conversation_id,
                user_msg=message,
                assistant_msg=response_text,
                tool_calls_log=tool_calls_log,
            )

            # --------------------------------------------------------------
            # EVENT (optional): agent.escalated — fires BEFORE agent.response
            # --------------------------------------------------------------
            if escalated:
                emit(
                    job_id,
                    "agent.escalated",
                    {
                        "reason": escalation_reason,
                        "context": escalation_context,
                        "conversation_id": str(local_conversation_id),
                    },
                    db,
                    _redis,
                )

            # --------------------------------------------------------------
            # EVENT (terminal): agent.response — widget renders this payload
            # --------------------------------------------------------------
            emit(
                job_id,
                "agent.response",
                {
                    "text": response_text,
                    "citations": citations_list,
                    "conversation_id": str(local_conversation_id),
                    # WIRE-05: the assistant message's own id — the non-secret
                    # correlation key the widget feedback route already
                    # requires as a body field. Useless to a caller who does
                    # not also hold a valid widget token scoped to this agent
                    # (T-23-GA-01).
                    "message_id": assistant_msg_id,
                },
                db,
                _redis,
            )

            # Mark job complete
            job.status = "complete"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()

            # --------------------------------------------------------------
            # OPS-01/OPS-04: telemetry — runs AFTER the terminal agent.response
            # emit and the idempotency-marking commit above, so a telemetry
            # failure can never delay or fail the served turn (must_haves
            # prohibition: "the turn_metrics write NEVER blocks or precedes
            # the terminal agent.response SSE emit"). The INSERT gets its own
            # try/except (separate from _emit_langfuse_turn_trace's internal
            # guard) — a turn_metrics write failure must never fail or retry
            # an already-served turn (T-21-01-03).
            # --------------------------------------------------------------
            try:
                _write_turn_metrics(
                    tenant_conn,
                    job_id=job_id,
                    conversation_id=local_conversation_id,
                    agent_id=agent_id,
                    cost_usd=total_cost_usd,
                    num_turns=num_turns,
                    latency_ms=latency_ms,
                    escalated=escalated,
                    tool_count=len(tool_calls_log),
                    stop_reason=stop_reason,
                    prompt_version_id=prompt_version_id,
                )
            except Exception as metrics_exc:
                log.warning(
                    "run_agent_turn.turn_metrics_write_failed",
                    job_id=job_id,
                    error=str(metrics_exc),
                )
            _emit_langfuse_turn_trace(
                job_id=job_id,
                agent_id=agent_id,
                model=AGENT_TURN_MODEL,
                num_turns=num_turns,
                total_cost_usd=total_cost_usd,
                latency_ms=latency_ms,
                stop_reason=stop_reason,
            )

            # Dispatch validation chain (M5 — VAL-04). BACKLOG 5.16.
            _dispatch_validation_chain(
                agent_id=str(agent_id),
                job_id=job_id,
                response_text=response_text,
                message=message,
                conversation_id=str(local_conversation_id),
                tool_calls_log=tool_calls_log,
            )

            log.info(
                "run_agent_turn.complete",
                job_id=job_id,
                agent_id=agent_id,
                conversation_id=local_conversation_id,
                citation_count=len(citations_list),
                escalated=escalated,
            )

        except Exception as exc:
            log.error(
                "run_agent_turn.failed",
                job_id=job_id,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=str(exc) or repr(exc),
            )

            # On final retry exhaustion: emit the failure event, then mark the
            # job failed. TWO separate sessions and two separate try blocks,
            # deliberately.
            #
            # agent.failed is the only thing the widget ever sees when a turn
            # dies, and the job-status write beside it is bookkeeping the ops
            # room reads later. Sharing one try/except-pass ranked them the
            # wrong way round: a raise from get_sync_db(), from db2.get(), or
            # from db2.commit() reached the same bare `except` and took the
            # customer's signal down with the bookkeeping. So the emission goes
            # FIRST and owns its own failure boundary.
            if self.request.retries >= self.max_retries:
                # error_type rides beside error because str() of several
                # exceptions on this path is EMPTY — TimeoutError and a bare
                # psycopg2.OperationalError both render as "" — and
                # {"error": ""} names nothing (BACKLOG 1.30).
                try:
                    with get_sync_db() as db_event:
                        emit(
                            job_id,
                            "agent.failed",
                            {
                                "error_type": type(exc).__name__,
                                "error": str(exc) or repr(exc),
                            },
                            db_event,
                            _redis,
                        )
                except Exception as emit_exc:
                    # Nothing left to tell the customer with. Logged rather than
                    # passed, because a silent failure here is the defect this
                    # whole branch exists to close.
                    log.error(
                        "run_agent_turn.failed_event_not_emitted",
                        job_id=job_id,
                        error_type=type(emit_exc).__name__,
                        error=str(emit_exc) or repr(emit_exc),
                    )

                try:
                    with get_sync_db() as db2:
                        job2 = db2.get(Job, job_id)
                        if job2:
                            job2.status = "failed"
                            job2.finished_at = datetime.now(timezone.utc)
                            db2.commit()
                except Exception:
                    pass
            else:
                # An OperationalError is overwhelmingly the tenant endpoint
                # waking rather than a defect, and the wake outlasts the generic
                # exponential countdown, so it retries on the wake window.
                if isinstance(exc, psycopg2.OperationalError):
                    countdown = TENANT_WAKE_RETRY_COUNTDOWN_S
                else:
                    countdown = 2 ** self.request.retries
                raise self.retry(exc=exc, countdown=countdown)
        finally:
            # PROD-05: close the single pooled tenant-DB connection.
            # Runs even when _run_sdk_turn raises or self.retry() re-raises,
            # preventing connection leaks on the exception paths. None only when
            # the connect above is what failed — nothing to close then.
            if tenant_conn is not None:
                tenant_conn.close()

    return {}
