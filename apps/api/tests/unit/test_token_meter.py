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

import pytest

from app.services.agent_tools import CHUNK_CONTENT_CHAR_LIMIT
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

