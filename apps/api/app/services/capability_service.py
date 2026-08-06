"""
capability_service — Phase 18 pure service layer for CAP-03, CAP-04, and BLR-02.

Three pure, synchronous functions, one canonical field set, no DB session, no HTTP,
no ``await`` anywhere in this module:

    PLATFORM_CAPABILITY_DEFAULTS
        Module-level dict, exactly 7 entries (the six mutating skills plus the
        non-mutating ``confirm_action``), every entry carrying all seven semantic
        fields plus a ``mutating`` flag. OD-3: lives here, not in ``config.py`` —
        ``Settings`` is a flat scalar surface, pydantic-settings would attempt env
        parsing of a nested per-skill dict and no env override is wanted for these
        values. A per-tenant configurable-defaults table is deliberately deferred
        as premature for seven skills and one tenant class.

        Consumer contract: ``mutating`` is the flag every consumer uses to decide
        whether a skill has a monetary/transactional surface at all. Plan 18-08's
        GET returns one entry per key (7 entries, stable/complete shape) and plan
        18-10's capability panel filters on ``mutating is True`` to render its Zones.
        No consumer may hard-code "six" as a slice or a literal name list — the
        count is a consequence of the flag, so a future seventh mutating skill
        appears in the UI without a second edit.

    HASHED_ENVELOPE_FIELDS
        The seven semantic field names, in fixed canonical order, that feed the
        BLR-02 envelope hash (OD-2): ``skill``, ``enabled``, ``rate_limit``,
        ``constraints``, ``requires_confirmation``, ``requires_identity_verification``,
        ``actor_mode``. ``id``, ``agent_id`` and ``updated_at`` are excluded because
        they are DB-managed and non-semantic — including ``updated_at`` would make a
        no-op re-save produce a new hash, firing false drift warnings that
        desensitise the owner to real changes (RESEARCH Pitfall 2).

    canonical_envelope_payload / canonical_envelope_hash
        Deterministic, order-independent (sorted by ``skill``), whitespace-free
        JSON projection and its sha256 hex digest. The checklist task and the
        approve route both call this same function so they cannot disagree on
        what "the current envelope" means.

    validate_tighten_only
        The CAP-03 gate. A pure per-field comparator returning ``None`` on pass or
        a short snake_case reason string naming the first violated field,
        mirroring ``enforcement.check_capability_access``'s "reason string, not
        exception, on a normal-path denial" convention (OD-3). Only fields present
        in ``proposed`` are compared — an absent field is not a change (partial
        PATCH semantics). This function is enforced *below* the route: it takes no
        ``Request``, no ``AsyncSession``, no auth object, so a direct API call that
        bypasses the admin UI is rejected identically (T-18-CAP-02).

    envelope_drift
        The CAP-04 predicate. Returns ``True`` whenever the acknowledged hash is
        missing/empty or differs from the live hash — a ``NULL``
        ``checklist_runs.envelope_hash`` on a historical (pre-0019) run means
        drift, never "matches whatever is live now" (plan 18-01's threat register
        contract, T-18-CAP-03).

Actor mode (OD-3b): ``actor_mode`` lands here as schema + hash input + tighten-only
comparator input in Phase 18. Wiring ``call_actor_gate`` to actually honour
``sample_at_rate_N`` sampling *behaviour* is explicitly deferred — CAP-03's
requirement text only asks that the UI let owners tighten Actor mode per skill,
which storing/validating/surfacing the setting satisfies.

Cross-wave seam ownership: this module is deliberately caller-free. The call sites
are owned by later plans: 18-07 wires ``canonical_envelope_hash`` /
``envelope_drift`` (checklist-time hash persistence, the approve-time 422, drift on
the checklist read); 18-08 wires ``validate_tighten_only`` (the PATCH route). Each
of those plans carries its own acceptance criterion asserting its own call site
exists — this is the explicit correction of the Phase 21 failure recorded in
``.planning/.continue-here.md``, where a function shipped fully implemented with
zero callers because an earlier-wave plan was assumed to have wired it.
"""

from __future__ import annotations

import hashlib
import json
import re

import structlog

from app.services.transactional.enforcement import _parse_rate_limit

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# PLATFORM_CAPABILITY_DEFAULTS (OD-3)
# ---------------------------------------------------------------------------


def _mutating_default(max_amount_cents: int) -> dict:
    """Build a platform-default entry for one of the six mutating skills.

    Fail-closed: ``enabled: False`` matches ``capability_envelopes.enabled``'s
    ``server_default=false`` (T-14-01-01). ``actor_mode: "always-on"`` is the
    strictest legal value. ``rate_limit: "5/hour"`` is a conservative platform
    ceiling. ``max_amount_cents`` is the caller-supplied conservative bound.
    """
    return {
        "enabled": False,
        "rate_limit": "5/hour",
        "constraints": {"max_amount_cents": max_amount_cents},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "actor_mode": "always-on",
        "mutating": True,
    }


PLATFORM_CAPABILITY_DEFAULTS: dict[str, dict] = {
    "place_order": _mutating_default(100_000),
    "cancel_order": _mutating_default(50_000),
    "issue_refund": _mutating_default(50_000),
    "update_subscription": _mutating_default(50_000),
    "book_slot": _mutating_default(50_000),
    "update_customer_record": _mutating_default(50_000),
    # confirm_action: the one non-mutating skill (PRD S4.5) and the only skill
    # for which actor_mode="off" is ever legal. Stated fully and concretely
    # rather than by exception, since it is the easiest entry to leave
    # half-specified:
    #   - enabled=False: same fail-closed default as the six mutating skills.
    #   - rate_limit="20/hour": a REAL ceiling, not None — a confirmation tool
    #     is called more often than a mutation but is still rate-bounded, and
    #     keeping this non-None keeps validate_tighten_only's
    #     loosen_rate_limit_removed branch meaningful for this skill.
    #   - constraints={}: an EMPTY dict, not {"max_amount_cents": ...} and not
    #     None. confirm_action moves no money — it writes a
    #     pending_confirmations row and calls no provider adapter, so a
    #     monetary ceiling on it is a control that means nothing. The empty
    #     dict keeps the key present and JSON-serialisable for the canonical
    #     hash. validate_tighten_only reads the missing max_amount_cents key
    #     as None (no ceiling ever configured), the correct semantics here.
    #   - actor_mode="always-on": the strictest legal value — the default is
    #     not "off" merely because "off" happens to be legal for this skill.
    "confirm_action": {
        "enabled": False,
        "rate_limit": "20/hour",
        "constraints": {},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "actor_mode": "always-on",
        "mutating": False,
    },
}

# ---------------------------------------------------------------------------
# HASHED_ENVELOPE_FIELDS (OD-2)
# ---------------------------------------------------------------------------

# Fixed canonical order. id, agent_id and updated_at are DB-managed and
# non-semantic; excluded deliberately (see module docstring — RESEARCH
# Pitfall 2: including updated_at would fire false drift on a no-op re-save).
HASHED_ENVELOPE_FIELDS: tuple[str, ...] = (
    "skill",
    "enabled",
    "rate_limit",
    "constraints",
    "requires_confirmation",
    "requires_identity_verification",
    "actor_mode",
)

# ---------------------------------------------------------------------------
# actor_mode domain (byte-matched to ck_capability_envelopes_actor_mode)
# ---------------------------------------------------------------------------

# Matches exactly what the DB CHECK constraint accepts for the sampled tier —
# see alembic/versions/0019_blast_radius_capability_v2.py. "always-on" and
# "off" are the two fixed literals, handled separately in parse_actor_mode.
ACTOR_MODE_RE = re.compile(r"^sample_at_rate_([1-9][0-9]?|100)$")


def parse_actor_mode(value: str) -> tuple[int, int]:
    """Parse an actor_mode string into a (tier, rate) tightness-ordinal pair.

    Comparison is lexicographic on the pair: "off" < any sampled mode <
    "always-on", and within the sampled tier a higher N is tighter (more of
    the agent's transactional turns get Actor review).

        "off"               -> (0, 0)
        "sample_at_rate_N"  -> (1, N)
        "always-on"         -> (2, 0)

    Raises ValueError on any value outside the domain the DB CHECK accepts —
    an unparseable mode must be a hard error, not a silent "treat as loosest",
    because the loosest interpretation would let an invalid write through the
    tighten-only comparator.
    """
    if value == "off":
        return (0, 0)
    if value == "always-on":
        return (2, 0)
    match = ACTOR_MODE_RE.match(value) if value else None
    if match is not None:
        return (1, int(match.group(1)))
    raise ValueError(f"invalid actor_mode: {value!r}")


# ---------------------------------------------------------------------------
# Canonical envelope hash (BLR-02, OD-2)
# ---------------------------------------------------------------------------


def canonical_envelope_payload(rows: list[dict]) -> str:
    """Project envelope rows onto HASHED_ENVELOPE_FIELDS and serialise deterministically.

    Each row is projected onto HASHED_ENVELOPE_FIELDS, in that exact order, as a
    list (not a dict) so key order can never vary the payload. Rows missing a
    field project None for it rather than raising, so a row read from a DB that
    predates migration 0019 still hashes (it simply hashes as having no
    actor_mode). The outer list is sorted by the projected skill value so row
    order in the input never varies the hash.

    Serialised with json.dumps(sort_keys=True, separators=(",", ":"),
    default=str): sort_keys covers the nested constraints dict, separators
    removes whitespace variance, and default=str makes a Decimal or datetime
    that leaks into constraints serialise deterministically instead of raising.
    """
    projected = [[row.get(field) for field in HASHED_ENVELOPE_FIELDS] for row in rows]
    projected.sort(key=lambda item: item[0] if item[0] is not None else "")
    return json.dumps(projected, sort_keys=True, separators=(",", ":"), default=str)


def canonical_envelope_hash(rows: list[dict]) -> str:
    """Return the sha256 hex digest of canonical_envelope_payload(rows).

    For an empty row list this returns the hash of the canonical empty payload
    — a deterministic 64-char hex value, not the empty string and not None, so
    "this agent has no envelopes" is a hashable state rather than a null that
    downstream drift logic would have to special-case.
    """
    payload = canonical_envelope_payload(rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Tighten-only comparator (CAP-03, OD-3)
# ---------------------------------------------------------------------------


def validate_tighten_only(
    current: dict,
    proposed: dict,
    platform_defaults: dict | None = None,
) -> str | None:
    """Return None if `proposed` is at-or-tighter than `current` on every
    field present in `proposed`, else a short snake_case reason string naming
    the first violated field.

    `platform_defaults` defaults to PLATFORM_CAPABILITY_DEFAULTS when None.
    Only fields PRESENT in `proposed` are compared (partial PATCH semantics)
    — an absent field is not a change. This is enforced independently of any
    route: no FastAPI, no AsyncSession, no auth object appears in this
    signature (T-18-CAP-02), so a direct API call bypassing the admin UI is
    rejected identically.

    Per-field rules:
      - enabled: both directions are unconditional (CAP-05). `enabled` is an
        owner-controlled authorization toggle, not a tightness dimension —
        the platform default no longer bounds it in either direction.
      - rate_limit: parsed with the shared `_parse_rate_limit` helper. A
        proposed None (explicit "remove the limit") against a non-None
        current is "loosen_rate_limit_removed". A malformed proposed value is
        "invalid_rate_limit". Otherwise compared as calls-per-second so a
        unit switch ("10/day" -> "10/hour") is correctly detected as a
        loosen even though the leading integer didn't change.
      - constraints.max_amount_cents: only this nested key is compared — other
        `constraints` keys are not part of the tighten-only comparison in v1.1
        and pass through unexamined. A proposed None/absent-key against a
        non-None current is "loosen_max_amount_removed". A proposed value
        strictly greater than current is "loosen_max_amount_cents". When
        current is None (no ceiling ever configured) any proposed value is
        allowed, including another None.
      - requires_confirmation / requires_identity_verification: False->True
        allowed; True->False rejected.
      - actor_mode: compared via parse_actor_mode's tightness ordinal; a
        strictly looser pair is "loosen_actor_mode". A ValueError from
        parse_actor_mode on the proposed value is "invalid_actor_mode".
        Additionally, "off" is rejected with
        "actor_mode_off_requires_non_mutating" whenever the skill's
        platform-default entry has mutating=True — for a mutating skill, off
        is not a valid state at ANY tightness level, not merely one the owner
        may not reach right now (PRD S4.5). This check runs independently of
        (and before) the ordinal comparison.

    Every rejection is logged via
    log.warning("capability.tighten_only_rejected", skill=..., reason=...,
    field=...) before returning — never the proposed or current values
    themselves.
    """
    if platform_defaults is None:
        platform_defaults = PLATFORM_CAPABILITY_DEFAULTS

    skill = current.get("skill") or proposed.get("skill")
    default_entry = platform_defaults.get(skill, {}) if skill else {}

    def _reject(reason: str, field: str) -> str:
        log.warning(
            "capability.tighten_only_rejected",
            skill=skill,
            reason=reason,
            field=field,
        )
        return reason

    # --- enabled --------------------------------------------------------
    if "enabled" in proposed:
        # CAP-05: `enabled` is an owner-controlled authorization toggle, not a
        # tightness dimension -- unlike the rate limit, the ceiling, and the
        # actor mode it has no numeric or ordinal "how much" to bound. The
        # platform-default gate that used to live here made every skill
        # permanently un-enablable, because every platform-default entry
        # ships enabled=False and no code path in apps/api/app/, other than
        # red_team_probe.py's in-memory CLEAN_TENANT_ENVELOPES fixture
        # constant (never written to a real capability_envelopes row), ever
        # set enabled=True. Both directions are now legal, mirroring how the
        # two boolean safety switches below are treated in their own
        # direction.
        # Enabling a skill does not by itself loosen any other field -- every
        # other field on this envelope remains governed by tighten-only
        # exactly as before.
        pass

    # --- rate_limit -------------------------------------------------------
    if "rate_limit" in proposed:
        current_rate = current.get("rate_limit")
        proposed_rate = proposed["rate_limit"]
        if proposed_rate is None:
            if current_rate is not None:
                return _reject("loosen_rate_limit_removed", "rate_limit")
        else:
            parsed_proposed = _parse_rate_limit(proposed_rate)
            if parsed_proposed is None:
                return _reject("invalid_rate_limit", "rate_limit")
            parsed_current = _parse_rate_limit(current_rate)
            if parsed_current is not None:
                current_calls, current_secs = parsed_current
                proposed_calls, proposed_secs = parsed_proposed
                current_rps = current_calls / current_secs
                proposed_rps = proposed_calls / proposed_secs
                if proposed_rps > current_rps:
                    return _reject("loosen_rate_limit", "rate_limit")

    # --- constraints.max_amount_cents -------------------------------------
    if "constraints" in proposed:
        proposed_constraints = proposed["constraints"] or {}
        current_constraints = current.get("constraints") or {}
        current_max = current_constraints.get("max_amount_cents")
        proposed_max = proposed_constraints.get("max_amount_cents")
        if current_max is not None:
            if proposed_max is None:
                return _reject("loosen_max_amount_removed", "constraints")
            if proposed_max > current_max:
                return _reject("loosen_max_amount_cents", "constraints")
        # current_max is None (no ceiling ever configured): any proposed
        # value is allowed, including another None.

    # --- requires_confirmation ---------------------------------------------
    if "requires_confirmation" in proposed:
        current_rc = bool(current.get("requires_confirmation", False))
        proposed_rc = bool(proposed["requires_confirmation"])
        if current_rc and not proposed_rc:
            return _reject("loosen_requires_confirmation", "requires_confirmation")

    # --- requires_identity_verification -------------------------------------
    if "requires_identity_verification" in proposed:
        current_riv = bool(current.get("requires_identity_verification", False))
        proposed_riv = bool(proposed["requires_identity_verification"])
        if current_riv and not proposed_riv:
            return _reject(
                "loosen_requires_identity_verification",
                "requires_identity_verification",
            )

    # --- actor_mode ---------------------------------------------------------
    if "actor_mode" in proposed:
        proposed_mode = proposed["actor_mode"]
        is_mutating = default_entry.get("mutating", True)
        if proposed_mode == "off" and is_mutating:
            return _reject("actor_mode_off_requires_non_mutating", "actor_mode")
        try:
            proposed_ord = parse_actor_mode(proposed_mode)
        except ValueError:
            return _reject("invalid_actor_mode", "actor_mode")
        current_mode = current.get("actor_mode")
        current_ord: tuple[int, int] | None
        try:
            current_ord = parse_actor_mode(current_mode) if current_mode else None
        except ValueError:
            current_ord = None
        if current_ord is not None and proposed_ord < current_ord:
            return _reject("loosen_actor_mode", "actor_mode")

    return None


# ---------------------------------------------------------------------------
# Drift predicate (CAP-04)
# ---------------------------------------------------------------------------


def envelope_drift(live_hash: str | None, acknowledged_hash: str | None) -> bool:
    """Return True when the acknowledged hash is absent or differs from live.

    A NULL checklist_runs.envelope_hash on a historical run (pre-0019, or a
    run whose approval was never reached) means drift — nothing was ever
    acknowledged, never "matches whatever is live now" (plan 18-01's threat
    register contract for T-18-CAP-03). An uncomputable live hash (None) is
    also never evidence of a match.
    """
    if not acknowledged_hash:
        return True
    if not live_hash:
        return True
    return live_hash != acknowledged_hash
