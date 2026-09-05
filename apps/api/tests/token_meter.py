"""Price a turn at the tokens the provider would count, not at characters over four.

WHY THIS EXISTS. `AGENT_MAX_BUDGET_USD` has been set twice from a test that
billed input as `len(json.dumps(body)) // 4`, and both numbers cut ordinary turns
off mid-answer (#182, PR #173). The proxy is not a small error and it does not
run one way: measured against `o200k_base` it OVERCOUNTS English prose by 1.4
and UNDERCOUNTS a digits table, base64 and CJK. A ceiling derived on prose
therefore fires early for every tenant whose knowledge base is tables, part
numbers, code, spec sheets, or a language that is not English.

WHAT THIS MEASURES, AND WHAT IT DOES NOT. `o200k_base` is the encoding OpenAI's
current models use, and `count_tokens` runs it over the strings the loop actually
puts on the wire. It is a STAND-IN for the provider's `usage.prompt_tokens` and
nothing more: the real number also carries the provider's own chat template, its
per-message and per-tool framing, and whatever the server does with a reasoning
field. Those are not public and are not modelled here, so every figure derived
through this module is a lower bound on the input side. `tests/unit/test_token_meter.py`
pins the error the four-characters proxy makes against THIS tokeniser, which is
the comparison this module can make honestly.

TRANSPORT ESCAPING IS NOT BILLED; A TOOL'S OWN ESCAPING IS. The request body is
JSON on the wire, but the model reads message CONTENT, so `count_request_tokens`
serialises with `ensure_ascii=False`: the transport's `\\uXXXX` escaping of a
Chinese character is not something the model is charged for. What IS charged is
escaping a tool did to its own payload before handing it over as text, because
those backslashes are literal characters inside the message. That distinction is
worth 4.7x on a non-English corpus, and `agent_tools.retrieve_tool` is where it
is decided.

`tiktoken` reaches this tree through `ragas`, which is a base dependency, so it
is present wherever the suite runs. `get_encoding` fetches its rank file once and
caches it; a first run needs the network.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

import tiktoken

#: The encoding OpenAI's current models use, `gpt-5.6-luna` included.
ENCODING_NAME = "o200k_base"

#: The divisor the budget guard's own arithmetic used before #182, and the one
#: `_over_budget` is NOT responsible for: the guard prices recorded token counts,
#: while the recorder that produces them reads the provider's usage block. The
#: proxy lives in the TESTS that set the constant, which is where it did the damage.
PROXY_CHARS_PER_TOKEN = 4


@lru_cache(maxsize=1)
def encoder():
    """The o200k_base encoder, loaded once per process."""
    return tiktoken.get_encoding(ENCODING_NAME)


def count_tokens(text: str) -> int:
    """How many tokens `text` encodes to."""
    return len(encoder().encode(text))


def proxy_tokens(text: str) -> int:
    """What characters-over-four would have billed for `text`."""
    return len(text) // PROXY_CHARS_PER_TOKEN


def request_text(kwargs: dict[str, Any]) -> str:
    """Every string one `chat.completions.create` call puts in front of the model.

    The messages and the tool schemas, which is the whole of what the loop
    assembles. `ensure_ascii=False` because the model reads the content, not the
    transport's escaping of it.
    """
    return json.dumps(kwargs["messages"], ensure_ascii=False, default=str) + json.dumps(
        kwargs["tools"], ensure_ascii=False, default=str
    )


def count_request_tokens(kwargs: dict[str, Any]) -> int:
    """Input tokens for one request body, as `o200k_base` counts them."""
    return count_tokens(request_text(kwargs))


# ---------------------------------------------------------------------------
# The four content types a tenant knowledge base is made of
# ---------------------------------------------------------------------------

_PROSE_SENTENCE = (
    "You can return any unopened item within fourteen days of delivery for a full refund, "
    "provided the original packaging and the proof of purchase accompany the return. "
)

#: One repeating unit per content type. `sample(name, chars)` cuts these to size.
#:
#: Each is something a real tenant uploads. Prose is a returns policy. The digits
#: table is a price list or a parts catalogue. Base64 is what a PDF extractor
#: emits for an embedded image or a signature block. The CJK line is a returns
#: policy in Chinese, and it is the densest of the four by a wide margin.
_UNITS = {
    "english prose": _PROSE_SENTENCE,
    "digits table": "".join(f"{i:06d} {i * 7:08d} {i * 13:09d}\n" for i in range(1000)),
    "base64": base64.b64encode(bytes(range(256)) * 200).decode(),
    "CJK": "退货政策规定客户可以在收到货物后的十四天内退回未开封的商品并获得全额退款。",
}

CONTENT_TYPES = tuple(_UNITS)


def sample(content_type: str, chars: int) -> str:
    """`chars` characters of one content type, repeating its unit as needed."""
    unit = _UNITS[content_type]
    return (unit * (chars // len(unit) + 1))[:chars]


# ---------------------------------------------------------------------------
# The client double that bills what it was sent
# ---------------------------------------------------------------------------


class TokenBilledClient:
    """A scripted client that appends one priced `ModelCall` per request it serves.

    WHY THIS AND NOT A STATIC `calls` LIST. A turn's input cost GROWS with every
    round, because each call re-sends the whole message list plus the tool
    schemas, and it is that growth that decides whether an ordinary turn reaches
    its answer. A `calls` list assembled before the turn starts cannot see it, so
    a ceiling set against one is a ceiling set against a turn the loop never runs.

    Args:
        calls:       the live list the loop's budget guard reads. Appended to.
        replies:     scripted completions, in order, repeating the last one.
        record:      builds one `ModelCall` from (input_tokens, output_tokens).
                     Passed in so the ledger row is the caller's own shape.
        output_tokens: what each reply is billed at on the output side.

    Records `input_tokens` and `proxy_input_tokens` per call, so a test can state
    both the real bill and what the old proxy would have claimed.
    """

    def __init__(self, calls, *replies, record, output_tokens: int = 300):
        self._calls = calls
        self._replies = list(replies)
        self._record = record
        self._output_tokens = output_tokens
        self.requests: list[dict] = []
        self.input_tokens: list[int] = []
        self.proxy_input_tokens: list[int] = []
        self.completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=self.completions)

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        text = request_text(kwargs)
        self.input_tokens.append(count_tokens(text))
        self.proxy_input_tokens.append(proxy_tokens(text))
        self._calls.append(self._record(self.input_tokens[-1], self._output_tokens))
        index = min(len(self.requests) - 1, len(self._replies) - 1)
        return self._replies[index]

    async def close(self) -> None:
        return None
