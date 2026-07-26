"""
Unit tests for capability_service.py — CAP-03 tighten-only comparator, CAP-04
drift predicate, BLR-02 canonical envelope hash.

Environment: tests/conftest.py sets all required Settings env vars (including
PLATFORM_CREDENTIAL_KEY etc.) at module scope BEFORE any test module is
imported — capability_service.py imports enforcement.py, which imports
settings, so this module relies on that conftest.py preamble exactly like
test_capability_enforcement.py does (neither file needs its own copy).

No test in this file opens a DB connection, a Redis connection, or makes a
network call — capability_service.py is a pure, synchronous module and every
assertion here operates on plain dicts.

Test-name contract (18-VALIDATION.md Per-Task Verification Map, T-18-CAP-01/02/03):
the three names below are fixed and must resolve at MODULE scope (not nested
in a class) so the ``::name`` node ids in that map resolve:
    test_validate_tighten_only
    test_tighten_only_enforced_below_route
    test_envelope_drift_flag
"""

from __future__ import annotations

import inspect

import pytest

from app.services.capability_service import (
    HASHED_ENVELOPE_FIELDS,
    canonical_envelope_hash,
    canonical_envelope_payload,
    envelope_drift,
    validate_tighten_only,
)

# ---------------------------------------------------------------------------
# T-18-CAP-01 — test_validate_tighten_only
# ---------------------------------------------------------------------------


def test_validate_tighten_only():
    """Every one of the six comparable fields rejects its loosening direction
    and accepts its tightening direction, plus mixed-payload and no-op cases."""

    # --- enabled ---------------------------------------------------------
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "enabled": False},
            {"enabled": True},
        )
        == "loosen_enabled"
    ), "enabled False->True must be rejected (no platform default has enabled=True)"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "enabled": True},
            {"enabled": False},
        )
        is None
    ), "enabled True->False (disabling) must always be accepted"

    # --- rate_limit --------------------------------------------------------
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "rate_limit": "2/hour"},
            {"rate_limit": "10/hour"},
        )
        == "loosen_rate_limit"
    ), "raising the call ceiling must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "rate_limit": "10/hour"},
            {"rate_limit": "2/hour"},
        )
        is None
    ), "lowering the call ceiling must be accepted"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "rate_limit": "10/day"},
            {"rate_limit": "10/hour"},
        )
        == "loosen_rate_limit"
    ), "a unit switch that raises the effective per-second rate must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "rate_limit": "2/hour"},
            {"rate_limit": None},
        )
        == "loosen_rate_limit_removed"
    ), "removing the rate limit entirely must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "rate_limit": "2/hour"},
            {"rate_limit": "banana"},
        )
        == "invalid_rate_limit"
    ), "a malformed proposed rate string must be a hard rejection"

    # --- constraints.max_amount_cents ---------------------------------------
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "constraints": {"max_amount_cents": 5000}},
            {"constraints": {"max_amount_cents": 20000}},
        )
        == "loosen_max_amount_cents"
    ), "raising the monetary ceiling must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "constraints": {"max_amount_cents": 20000}},
            {"constraints": {"max_amount_cents": 5000}},
        )
        is None
    ), "lowering the monetary ceiling must be accepted"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "constraints": {"max_amount_cents": 5000}},
            {"constraints": {"max_amount_cents": None}},
        )
        == "loosen_max_amount_removed"
    ), "explicitly removing the monetary ceiling must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "constraints": {"max_amount_cents": 5000}},
            {"constraints": {}},
        )
        == "loosen_max_amount_removed"
    ), "an absent max_amount_cents key inside a present constraints dict is also a removal"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "constraints": {}},
            {"constraints": {"max_amount_cents": 5000}},
        )
        is None
    ), "current with no ceiling ever configured must accept any proposed value"

    # --- requires_confirmation -----------------------------------------------
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "requires_confirmation": True},
            {"requires_confirmation": False},
        )
        == "loosen_requires_confirmation"
    ), "requires_confirmation True->False must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "requires_confirmation": False},
            {"requires_confirmation": True},
        )
        is None
    ), "requires_confirmation False->True must be accepted"

    # --- requires_identity_verification --------------------------------------
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "requires_identity_verification": True},
            {"requires_identity_verification": False},
        )
        == "loosen_requires_identity_verification"
    ), "requires_identity_verification True->False must be rejected"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "requires_identity_verification": False},
            {"requires_identity_verification": True},
        )
        is None
    ), "requires_identity_verification False->True must be accepted"

    # --- actor_mode ----------------------------------------------------------
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "actor_mode": "always-on"},
            {"actor_mode": "sample_at_rate_10"},
        )
        == "loosen_actor_mode"
    ), "always-on -> sampled must be rejected as a loosen"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "actor_mode": "sample_at_rate_10"},
            {"actor_mode": "always-on"},
        )
        is None
    ), "sampled -> always-on must be accepted"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "actor_mode": "sample_at_rate_50"},
            {"actor_mode": "sample_at_rate_10"},
        )
        == "loosen_actor_mode"
    ), "a lower sampling N is a loosen"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "actor_mode": "sample_at_rate_10"},
            {"actor_mode": "sample_at_rate_50"},
        )
        is None
    ), "a higher sampling N is a tighten"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "actor_mode": "always-on"},
            {"actor_mode": "off"},
        )
        == "actor_mode_off_requires_non_mutating"
    ), "off is never a valid state for a mutating skill, at any tightness level"
    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "actor_mode": "always-on"},
            {"actor_mode": "sampled"},
        )
        == "invalid_actor_mode"
    ), "an out-of-domain actor_mode must be a hard rejection, never a permissive default"

    # --- no-op / mixed-payload cases ------------------------------------------
    full_current = {
        "skill": "issue_refund",
        "enabled": True,
        "rate_limit": "10/hour",
        "constraints": {"max_amount_cents": 20000},
        "requires_confirmation": True,
        "requires_identity_verification": True,
        "actor_mode": "always-on",
    }
    assert (
        validate_tighten_only(full_current, {}) is None
    ), "a proposed dict with no changed fields must return None"

    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "enabled": True, "requires_confirmation": False},
            {"enabled": False, "requires_confirmation": True},
        )
        is None
    ), "tightening two fields at once must return None"

    assert (
        validate_tighten_only(
            {"skill": "issue_refund", "enabled": True, "requires_confirmation": True},
            {"enabled": False, "requires_confirmation": False},
        )
        == "loosen_requires_confirmation"
    ), "a mixed tighten-and-loosen payload must surface the loosening field's reason"


# ---------------------------------------------------------------------------
# T-18-CAP-02 — test_tighten_only_enforced_below_route
# ---------------------------------------------------------------------------


def test_tighten_only_enforced_below_route():
    """validate_tighten_only carries no HTTP/route machinery — it is genuinely
    a below-the-route service function, so a direct API call bypassing the
    admin UI is rejected identically to a UI-originated call."""

    sig = inspect.signature(validate_tighten_only)
    assert list(sig.parameters) == ["current", "proposed", "platform_defaults"], (
        "signature must be exactly (current, proposed, platform_defaults) — "
        "no Request, no DB session, no auth object"
    )

    # Calling it directly with a loosening payload returns a reason string —
    # no HTTP machinery involved at all.
    reason = validate_tighten_only(
        {"skill": "issue_refund", "enabled": False},
        {"enabled": True},
    )
    assert reason == "loosen_enabled"

    source = inspect.getsource(validate_tighten_only)
    assert "raise HTTPException(" not in source
    assert "status_code=" not in source

    import app.services.capability_service as capability_service_module

    module_source = inspect.getsource(capability_service_module)
    assert "import fastapi" not in module_source.lower()
    assert "from fastapi" not in module_source.lower()


# ---------------------------------------------------------------------------
# T-18-CAP-03 — test_envelope_drift_flag
# ---------------------------------------------------------------------------


def test_envelope_drift_flag():
    """envelope_drift treats a missing acknowledgement as drift, never as a
    silent match. Plan 18-07 owns the two call sites (the checklist read and
    the approve-time 422) and asserts them there — this module is caller-free."""

    assert (
        envelope_drift("livehash123", None) is True
    ), "acknowledged=None against a live hash must be drift (pre-0019 historical run)"
    assert envelope_drift("livehash123", "") is True, "acknowledged='' must be drift"
    assert (
        envelope_drift(None, "ackhash123") is True
    ), "an uncomputable live hash (None) is never evidence of a match"
    assert (
        envelope_drift("livehash123", "differenthash456") is True
    ), "differing hashes must be drift"
    assert (
        envelope_drift("samehash789", "samehash789") is False
    ), "identical non-empty hashes must not be drift"


# ---------------------------------------------------------------------------
# BLR-02 — canonical envelope hash
# ---------------------------------------------------------------------------


def _row(**overrides) -> dict:
    """Build one full envelope row for hash tests — all 7 semantic fields plus
    the 3 non-semantic fields (id, agent_id, updated_at) that must NOT affect
    the hash."""
    row = {
        "id": "row-id-1",
        "agent_id": "agent-1",
        "skill": "issue_refund",
        "enabled": True,
        "rate_limit": "10/hour",
        "constraints": {"max_amount_cents": 20000},
        "requires_confirmation": True,
        "requires_identity_verification": True,
        "actor_mode": "always-on",
        "updated_at": "2026-07-26T10:00:00Z",
    }
    row.update(overrides)
    return row


class TestCanonicalEnvelopeHash:
    def test_hash_is_deterministic_across_row_order(self):
        row_a = _row(skill="issue_refund")
        row_b = _row(skill="place_order", constraints={"max_amount_cents": 100000})
        row_c = _row(skill="book_slot", constraints={"max_amount_cents": 50000})

        hash_forward = canonical_envelope_hash([row_a, row_b, row_c])
        hash_shuffled = canonical_envelope_hash([row_c, row_a, row_b])
        hash_reversed = canonical_envelope_hash([row_c, row_b, row_a])

        assert hash_forward == hash_shuffled == hash_reversed

    def test_hash_stable_across_noop_resave(self):
        original = _row(id="row-id-1", agent_id="agent-1", updated_at="2026-07-26T10:00:00Z")
        resaved = _row(id="row-id-2", agent_id="agent-2", updated_at="2026-07-26T10:05:00Z")

        assert canonical_envelope_hash([original]) == canonical_envelope_hash([resaved]), (
            "a no-op re-save that only changes id/agent_id/updated_at must not "
            "change the hash (RESEARCH Pitfall 2 regression guard)"
        )

    @pytest.mark.parametrize("field", HASHED_ENVELOPE_FIELDS)
    def test_hash_changes_on_each_semantic_field(self, field):
        baseline = _row()
        baseline_hash = canonical_envelope_hash([baseline])

        mutated = _row()
        if field == "skill":
            mutated["skill"] = "place_order"
        elif field == "enabled":
            mutated["enabled"] = False
        elif field == "rate_limit":
            mutated["rate_limit"] = "2/hour"
        elif field == "constraints":
            mutated["constraints"] = {"max_amount_cents": 1}
        elif field == "requires_confirmation":
            mutated["requires_confirmation"] = False
        elif field == "requires_identity_verification":
            mutated["requires_identity_verification"] = False
        elif field == "actor_mode":
            mutated["actor_mode"] = "off"

        mutated_hash = canonical_envelope_hash([mutated])
        assert baseline_hash != mutated_hash, f"mutating {field!r} must change the hash"

    def test_hash_of_empty_rows_is_deterministic_hex(self):
        hash_1 = canonical_envelope_hash([])
        hash_2 = canonical_envelope_hash([])

        assert hash_1 == hash_2
        assert len(hash_1) == 64
        assert all(c in "0123456789abcdef" for c in hash_1)

    def test_hash_tolerates_row_missing_actor_mode(self):
        row_without_actor_mode = _row()
        del row_without_actor_mode["actor_mode"]
        row_with_actor_mode = _row(actor_mode="always-on")

        # Must not raise.
        hash_without = canonical_envelope_hash([row_without_actor_mode])
        hash_with = canonical_envelope_hash([row_with_actor_mode])

        assert hash_without != hash_with

    def test_canonical_payload_has_no_whitespace(self):
        payload = canonical_envelope_payload([_row()])

        assert ", " not in payload
        assert ": " not in payload
