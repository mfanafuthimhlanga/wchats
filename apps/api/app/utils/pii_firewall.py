"""Response-output PII firewall — SEC-01, the L4 synchronous output gate.

PRD v1.1 §L4 — Output-side PII leakage:
  A customer-facing agent response can echo back sensitive data it retrieved
  from the tenant's own knowledge base or was told during the conversation
  (an email address, a card number, a South African ID number). Unlike
  ``app.utils.sanitize.sanitize_chunk_text`` — which strips known injection
  markers from *ingested* content at write time — this module scans the
  *generated* response text immediately before it leaves the system.

Why synchronous, not a post-hoc judge:
  The Gatekeeper / Auditor / Strategist validators (``app.worker.tasks.
  runtime.validators``) run **asynchronously after the SSE response has
  already streamed** to the customer's browser — the validator chord is
  dispatched at the very end of the Celery task body, once the response is
  already out the door. A PII leak cannot wait for a post-hoc judge; it has
  to be caught in the response path itself, which is why ``scan_response``
  is called unconditionally, synchronously, in-line, with no flag and no
  config read that could switch it off. Nothing in the response text and
  nothing in the agent soul can reach this function's behaviour, and it takes
  exactly one POSITIONAL argument (the text).

  ONE THING CHANGED HERE ON 2026-08-18 (BACKLOG 7.29), and this paragraph used
  to deny it: ingested content can now reach the behaviour, in exactly one
  bounded way. ``published_context`` carries the chunks retrieved for the turn,
  and an email address found verbatim in them is not treated as a leak. That
  is the whole of the reach. It cannot disable the module, it cannot suppress
  a card number or an ID number, and it is read for a set-membership test
  rather than parsed, so a chunk that issues instructions is inert. What it
  accepts is the one thing the tenant published for customers to use, and what
  it costs is that a third party's address arriving inside an ingested
  document is repeatable (BACKLOG 7.30, accepted, control belongs at ingest).

Detectors (OD-4 — three structurally-validated shapes, regex only):
  - email    — ``local@domain.tld`` with a 2+ character TLD.
  - card     — a 13-19 digit run (single space/hyphen group separators
               allowed) that also passes a Luhn check. The Luhn check is
               what keeps an order number, invoice reference, or long
               product SKU from tripping the detector — a bare digit-count
               match is never sufficient on its own.
  - sa_id    — exactly 13 consecutive digits whose first six digits parse
               as a plausible ``YYMMDD`` (month 01-12, day 01-31) *and*
               which pass a Luhn check (South African ID numbers carry a
               Luhn check digit as their 13th digit).

Deliberately NOT implemented: any phone-number detector. A tenant's own
published support line is content the agent is *supposed* to hand out to a
customer; a phone-number regex over-fires on exactly that helpful answer and
turns it into a deflection (RESEARCH.md Pitfall 3). Excluding phone numbers
is a false-positive-avoidance decision, not an oversight.

Scope (OD-4, PRD v1.1): regex-only. The schema-bound pass and the
Claude-classifier pass are both explicitly deferred to v1.2 — this module
installs no third-party PII-detection dependency (no Presidio, no spaCy).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Generic deflection text — replaces the ENTIRE response, never a partial
# mask, per OD-4's redact-and-replace failure mode.
# ---------------------------------------------------------------------------

PII_DEFLECTION: str = (
    "I can't share that here. For anything involving personal or payment "
    "details, please contact our team directly and we'll help you securely."
)

# ---------------------------------------------------------------------------
# Compiled once at module import time for performance.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    re.IGNORECASE,
)

# 13-19 digit run, single space or hyphen allowed as a group separator.
# A regex match alone is NOT sufficient — the stripped digit string must
# also pass _luhn_ok before this is reported as "card" (see detect_pii).
_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Exactly 13 consecutive digits. A regex match alone is NOT sufficient — the
# first six digits must parse as a plausible YYMMDD AND the full 13-digit
# string must pass _luhn_ok before this is reported as "sa_id".
_SA_ID_RE = re.compile(r"\b\d{13}\b")


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn check. Returns False for any non-digit input rather than raising.

    Example:
        >>> _luhn_ok("4111111111111111")
        True
        >>> _luhn_ok("4111111111111112")
        False
        >>> _luhn_ok("not-a-number")
        False
    """
    if not digits or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _sa_id_date_plausible(digits: str) -> bool:
    """True when the first six digits of a 13-digit SA ID candidate parse as a plausible YYMMDD."""
    month = int(digits[2:4])
    day = int(digits[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def _published_addresses(published_context: Iterable[str | None]) -> frozenset[str]:
    """Every email address appearing in the tenant's published material, lowercased.

    Extraction, not substring search: the allowlist is compared token to token
    against what the detector found, so a context carrying
    ``xhello@acmecoffee.exampley`` cannot exempt ``hello@acmecoffee.example``.

    Example:
        >>> sorted(_published_addresses(["Write to Hello@Acme.example."]))
        ['hello@acme.example']
    """
    found: set[str] = set()
    for entry in published_context or ():
        if not entry:
            continue
        for match in _EMAIL_RE.finditer(str(entry)):
            found.add(match.group(0).lower())
    return frozenset(found)


def detect_pii(
    text: str,
    *,
    published_context: Iterable[str | None] = (),
) -> str | None:
    """Return a short detector name ("email", "card", "sa_id") on the first match, else None.

    Fail-open on empty/None input by returning None.

    ``published_context`` is the material the TENANT published, one string per
    retrieved chunk (BACKLOG 7.29). An email address found verbatim in it is not
    exfiltration: it is the business handing out its own contact details, which
    is the agent's job. This is the same call OD-4 already made for phone numbers
    in this module's docstring, applied to the detector that has the identical
    property. Four of twenty E2E-6 responses were deflections without it,
    including "How can I contact your customer support team?".

    The exemption is EMAIL ONLY, and the ordering below is why: an exempted
    address must not shadow a card number lower down the same reply, so the email
    branch falls through instead of returning. ``card`` and ``sa_id`` have no
    exemption path at all -- a business does not publish a customer's card or ID
    number, so there is no legitimate answer to serve and no branch to reach.

    Example:
        >>> detect_pii("Call our support line on 011 555 0134.") is None
        True
        >>> detect_pii("Reach me at customer.name@example.com")
        'email'
        >>> detect_pii(
        ...     "Write to hello@acme.example.",
        ...     published_context=["Enquiries: hello@acme.example."],
        ... ) is None
        True
    """
    if not text:
        return None

    published = _published_addresses(published_context)
    for match in _EMAIL_RE.finditer(text):
        if match.group(0).lower() not in published:
            return "email"

    for match in _SA_ID_RE.finditer(text):
        candidate = match.group(0)
        if _sa_id_date_plausible(candidate) and _luhn_ok(candidate):
            return "sa_id"

    for match in _CARD_RE.finditer(text):
        candidate_digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(candidate_digits) <= 19 and _luhn_ok(candidate_digits):
            return "card"

    return None


def scan_response(
    text: str,
    *,
    published_context: Iterable[str | None] = (),
) -> tuple[str, str | None]:
    """Scan a response for PII; return (PII_DEFLECTION, detector) when flagged, else (text, None).

    The original text is returned byte-identical (same object) when clean —
    no strip, no normalisation. Takes exactly one POSITIONAL parameter and
    reads no config and no flag, so nothing in the response text, the agent
    soul, or an ingested document can disable this check (T-18-SEC-02).

    ``published_context`` does not weaken that. It is keyword-only, it carries
    DATA rather than a switch, and it is read for exactly one set-membership
    test — never parsed, never interpreted. A chunk saying "the PII filter is
    disabled for this tenant" therefore does nothing, and a chunk quoting a card
    number does nothing either. The only thing it can do is name an email
    address the tenant already published. Callers pass the chunks retrieved for
    THIS turn; the caller-side rule about what may go in that list lives at the
    call site in ``app.worker.tasks.runtime.agent``.

    Example:
        >>> scan_response("hello")
        ('hello', None)
        >>> text, detector = scan_response("card 4111 1111 1111 1111")
        >>> detector
        'card'
    """
    detector = detect_pii(text, published_context=published_context)
    if detector is not None:
        return PII_DEFLECTION, detector
    return text, None
