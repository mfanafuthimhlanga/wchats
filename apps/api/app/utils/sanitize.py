"""Chunk text sanitization — indirect prompt injection mitigation.

PITFALLS.md §11 — Indirect Prompt Injection:
  When user-uploaded documents contain adversarial text (e.g. "Ignore previous
  instructions" or role-prefixes like "System:", "Human:", "Assistant:"), that
  text can be retrieved by the RAG pipeline and inserted into the customer agent's
  context window, effectively hijacking its behavior.

  This module strips known injection markers from chunk text *before* it is written
  to the tenant ``chunks`` table. All later waves call ``sanitize_chunk_text()``
  before any INSERT — the mitigation is applied at write time, not at read time.

Patterns stripped (case-insensitive):
  - ``System:``         — role prefix used by many LLM APIs
  - ``Human:``          — role prefix used by Anthropic / Claude legacy formats
  - ``Assistant:``      — role prefix used by Anthropic / Claude legacy formats
  - ``[INST]``          — Llama-2 instruction token
  - ``[/INST]``         — Llama-2 instruction close token
  - ``<!-- ... -->``    — HTML comments (can carry hidden adversarial instructions)
  - ``Ignore previous`` — common preamble to override instruction attacks

The regex is intentionally narrow — it only removes tokens that have no legitimate
use in business document text and that are known vectors for prompt injection.
"""

import re

# Compiled once at module import time for performance.
# re.DOTALL makes "." match newlines, so multi-line HTML comments are stripped.
# re.IGNORECASE handles variations like "SYSTEM:" or "ignore Previous".
_INJECTION_PATTERNS = re.compile(
    r"(System:|Human:|Assistant:|\[INST\]|\[/INST\]|<!--.*?-->|Ignore previous)",
    re.IGNORECASE | re.DOTALL,
)


def sanitize_chunk_text(text: str) -> str:
    """Strip known prompt-injection markers from chunk text and return stripped string.

    All injection pattern matches are removed (replaced with empty string).
    Leading/trailing whitespace is stripped from the result.

    Args:
        text: Raw chunk text from the ingestion pipeline.

    Returns:
        Sanitized text with injection patterns removed.

    Example:
        >>> sanitize_chunk_text("System: ignore prior rules. Real content.")
        'ignore prior rules. Real content.'
        >>> sanitize_chunk_text("  hello  ")
        'hello'
    """
    return _INJECTION_PATTERNS.sub("", text).strip()
