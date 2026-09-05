"""What characters-over-four gets wrong, by content type (#182, input 4 of 4).

WHY THIS FILE EXISTS. `AGENT_MAX_BUDGET_USD` was set twice from a test that
billed a request body at `len(json.dumps(body)) // 4`, and both numbers cut
ordinary turns off with an empty answer. The proxy is not a rounding error and
it does not run one way. Measured here against `o200k_base`, at
`CHUNK_CONTENT_CHAR_LIMIT` characters of each content type:

    content         real   proxy   proxy/real
    english prose    353     500        1.42
    digits table     846     500        0.59
    base64          1349     500        0.37
    CJK             1460     500        0.34

So a ceiling tuned on English prose is tuned 1.42 times HIGH on the one content
type a demo uses, and up to 4.2 times low on the content a real knowledge base is
made of: price lists, part numbers, spec sheets, code, an extractor's base64
image blocks, and any language that is not English.

WHAT THESE NUMBERS ARE NOT. `o200k_base` is a stand-in for the provider's
`usage.prompt_tokens`, not that number. The real count also carries the chat
template and the per-message and per-tool framing the server adds, none of which
is public. Every figure derived through `tests.token_meter` is therefore a lower
bound on the input side, and this file pins the error the PROXY makes against
this tokeniser, which is the comparison that can be made honestly.
"""

from __future__ import annotations

import json

import pytest

from app.services.agent_tools import CHUNK_CONTENT_CHAR_LIMIT, MAX_CHUNKS
from tests.token_meter import (
    CONTENT_TYPES,
    ENCODING_NAME,
    count_tokens,
    proxy_tokens,
    sample,
)

#: proxy/real at CHUNK_CONTENT_CHAR_LIMIT characters, measured 2026-09-05 against
#: o200k_base. The tolerance is what a tokeniser revision may move without this
#: file's claim changing; a swap to a different encoding moves these far more.
MEASURED_PROXY_RATIO = {
    "english prose": 1.42,
    "digits table": 0.59,
    "base64": 0.37,
    "CJK": 0.34,
}

TOLERANCE = 0.04


def _ratio(content_type: str) -> float:
    text = sample(content_type, CHUNK_CONTENT_CHAR_LIMIT)
    return proxy_tokens(text) / count_tokens(text)


def test_the_encoding_is_the_one_the_served_model_uses():
    """A different encoding makes every number in this file a number about nothing."""
    assert ENCODING_NAME == "o200k_base"


@pytest.mark.parametrize("content_type", CONTENT_TYPES)
def test_the_proxy_error_is_the_measured_one(content_type):
    """Each ratio, pinned. This is the table the budget ceiling is derived against."""
    ratio = _ratio(content_type)
    expected = MEASURED_PROXY_RATIO[content_type]

    assert abs(ratio - expected) <= TOLERANCE, (
        f"characters-over-four bills {content_type} at {ratio:.2f} times what "
        f"o200k_base counts, against a recorded {expected:.2f}. Either the "
        "tokeniser moved or the sample did, and every ceiling derived from this "
        "table is stale until the number is re-recorded."
    )


def test_the_proxy_runs_both_ways_and_prose_is_the_outlier():
    """The claim the two failed ceilings were set against, stated as a test.

    English prose is the ONLY one of the four the proxy overcounts. Tuning a
    ceiling on it and shipping it to a tenant whose corpus is tables or Chinese
    is what produced `stop_reason='budget_exceeded'` and an empty answer.
    """
    overcounted = {name for name in CONTENT_TYPES if _ratio(name) > 1.0}

    assert overcounted == {"english prose"}, (
        f"the proxy overcounts {sorted(overcounted)}. The premise of #182's "
        "fourth input is that prose is the outlier and everything a real "
        "knowledge base holds is undercounted."
    )


def test_the_worst_undercount_is_close_to_a_factor_of_three():
    """How far a prose-tuned ceiling misses by, in one number."""
    worst = min(_ratio(name) for name in CONTENT_TYPES)

    assert 1 / worst >= 2.8, (
        f"the densest content is billed at {1 / worst:.2f} times the proxy, and "
        "the ceiling's headroom has to cover that factor or the guard cuts those "
        "tenants off mid-answer."
    )

# ---------------------------------------------------------------------------
# What a tool's own JSON escaping costs, which is a bigger number than any above
# ---------------------------------------------------------------------------


def _retrieve_wire(content: str, *, ensure_ascii: bool) -> str:
    """One retrieve result at the configured maximum, as `retrieve_tool` builds it."""
    chunks = [
        {
            "chunk_id": f"c{index}",
            "document_id": f"d{index}",
            "content": content,
            "score": 0.8123,
            "rank": index,
        }
        for index in range(MAX_CHUNKS)
    ]
    return json.dumps(chunks, ensure_ascii=ensure_ascii)


def test_escaping_non_ascii_into_uXXXX_multiplies_what_the_model_is_billed():
    """`json.dumps` defaults to ensure_ascii=True, and the model pays for it.

    Transport escaping costs nothing: the model reads message content, not the
    JSON frame the request travels in. But `retrieve_tool` escapes its own
    payload BEFORE handing it over as text, so every Chinese character reaches
    the model as the six literal characters `\\u9000` and is tokenised as such.

    This is the largest single term in a non-English turn's bill, larger than the
    tokeniser difference this file's table measures, and it buys nothing: both
    forms are valid JSON and nothing calls `json.loads` on either.
    """
    content = sample("CJK", CHUNK_CONTENT_CHAR_LIMIT)

    escaped = count_tokens(_retrieve_wire(content, ensure_ascii=True))
    plain = count_tokens(_retrieve_wire(content, ensure_ascii=False))

    assert escaped / plain >= 4.0, (
        f"escaping cost {escaped} tokens against {plain} unescaped, a factor of "
        f"{escaped / plain:.2f}. The claim behind agent_tools.retrieve_tool's "
        "ensure_ascii=False is that this factor is large, so if it has shrunk "
        "the reasoning there needs re-reading."
    )


def test_escaping_costs_an_ascii_corpus_nothing():
    """The control. `ensure_ascii` changes no byte of a corpus that is already ASCII.

    Without this, the test above is satisfied by anything that makes the escaped
    form bigger, and the change it justifies could be quietly costing the English
    tenants who are most of the product.
    """
    for content_type in ("english prose", "digits table", "base64"):
        content = sample(content_type, CHUNK_CONTENT_CHAR_LIMIT)

        assert count_tokens(_retrieve_wire(content, ensure_ascii=True)) == count_tokens(
            _retrieve_wire(content, ensure_ascii=False)
        ), f"{content_type} is not ASCII-clean, so it is the wrong control"
