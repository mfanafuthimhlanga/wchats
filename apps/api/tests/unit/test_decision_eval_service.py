"""Unit tests for the decision eval — the confusion matrix over `tool_calls_audit`.

These tests are written adversarially, because every defect this branch exists to
remove was a SILENT one: a query that raised on every call and had the exception
substituted with an empty dict; a gate condition that could not fire; a score that
measured a label against itself. None of them failed loudly, and none of them had a
test that would have gone red.

So the load-bearing tests here are the negative ones:

  * `classify_outcome` is checked as a PROPERTY over the full 3x3 product, not on
    hand-picked pairs, because the failure mode is one cell quietly falling into
    the wrong class.
  * The error vocabulary is read out of `transactional/tools.py` and
    `confirmation_resolution.py` with an AST walk, so a new dispatcher error string
    fails this suite rather than silently becoming an unscored row.
  * The SELECT list is compared to `ToolCallsAudit`'s real columns — this branch
    exists because `deployment_service` named `metric_name`/`run_id` against a table
    with `metric`/`eval_run_id` and nobody noticed for a milestone.
  * Every rate is checked to be UNKNOWN rather than 0.0 when nothing was observed,
    and the absence of any combined score is pinned explicitly.

Audit rows in these tests are not invented shapes. `_shipped_snapshot_columns()`
parses the SELECT inside `check_capability_access`, so the `capability_snapshot`
blobs here carry exactly the fields the dispatcher would have written — including
the fact that it does not write `actor_mode`.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.tool_calls_audit import ToolCallsAudit
from app.services import decision_eval_service as des
from app.services.transactional import confirmation_resolution, enforcement, tools
from app.services.transactional.schemas import SKILL_INPUT_MODELS

AGENT_ID = "11111111-2222-3333-4444-555555555555"
RUN_ID = "run-p3-0001"


# ---------------------------------------------------------------------------
# Row construction — derived from the shipped writer, never transcribed
# ---------------------------------------------------------------------------


def _shipped_snapshot_columns() -> list[str]:
    """The columns `check_capability_access` reads into `capability_snapshot`.

    Parsed out of the shipped source rather than listed here, so these tests
    describe the snapshot the dispatcher actually writes even after someone
    widens or narrows that SELECT.
    """
    src = inspect.getsource(enforcement.check_capability_access)
    flat = re.sub(r"\s+", " ", re.sub(r"[\"'\n]", " ", src))
    match = re.search(r"SELECT (.*?) FROM capability_envelopes", flat)
    assert match is not None, (
        "check_capability_access no longer contains a recognisable "
        "SELECT ... FROM capability_envelopes — these tests can no longer derive "
        "the snapshot shape and must be re-read against the new source"
    )
    return [col.strip() for col in match.group(1).split(",") if col.strip()]


def shipped_snapshot(fixture: des.DecisionFixture, **overrides) -> dict:
    """The `capability_snapshot` the dispatcher would have written for this case."""
    snapshot: dict = {}
    for column in _shipped_snapshot_columns():
        if column in fixture.envelope:
            snapshot[column] = fixture.envelope[column]
    snapshot.setdefault("id", str(uuid4()))
    snapshot.setdefault("agent_id", AGENT_ID)
    snapshot.setdefault("updated_at", "2026-08-05T00:00:00+00:00")
    snapshot.update(overrides)
    return snapshot


# The audit row each disposition produces, using the literal `error` values
# `tools.py` writes. `test_every_dispatcher_error_string_is_classified` is what
# keeps this mapping honest against the dispatcher.
_DISPOSITION_ROW: dict[str, tuple[str, str | None]] = {
    des.DISPOSITION_EXECUTE: ("approve", None),
    des.DISPOSITION_REFUSE: ("block", "actor_block"),
    des.DISPOSITION_REQUIRE_HUMAN: ("require_human", "actor_require_human"),
}


def audit_row(
    fixture: des.DecisionFixture,
    disposition: str,
    *,
    run_id: str = RUN_ID,
    snapshot: dict | None = None,
    error: str | None = "",
    actor_decision: str | None = None,
) -> dict:
    """One `tool_calls_audit` row, keyed exactly like the ORM model's columns."""
    default_decision, default_error = _DISPOSITION_ROW[disposition]
    return {
        "id": str(uuid4()),
        "agent_id": AGENT_ID,
        "conversation_id": None,
        "skill": fixture.skill,
        "arguments": des.materialise_request(fixture, run_id),
        "result": None,
        "actor_decision": (
            default_decision if actor_decision is None else actor_decision
        ),
        "actor_rationale": "",
        "capability_snapshot": (
            shipped_snapshot(fixture) if snapshot is None else snapshot
        ),
        "latency_ms": None,
        "error": default_error if error == "" else error,
        "created_at": "2026-08-05T00:00:00+00:00",
    }


def perfect_run(fixtures: list[des.DecisionFixture]) -> list[dict]:
    """One row per fixture, each observing exactly what its label says it should."""
    return [audit_row(f, f.expected_disposition) for f in fixtures]


@pytest.fixture
def fixtures() -> list[des.DecisionFixture]:
    return des.build_decision_fixtures()


# ---------------------------------------------------------------------------
# The fixture set is DERIVED, and stops working loudly when it stops matching
# ---------------------------------------------------------------------------


class TestFixtureDerivation:
    def test_the_shipped_surface_currently_produces_no_drift(self):
        """The baseline claim every other test in this class depends on."""
        assert des.fixture_drift() == []

    def test_every_mutating_skill_is_covered(self, fixtures):
        """Coverage is read from the registry, so it cannot silently narrow."""
        covered = {f.skill for f in fixtures}
        assert covered == set(des.mutating_skills())

    def test_every_fixture_request_validates_against_the_shipped_input_model(
        self, fixtures
    ):
        """A case that scores a request the shipped schema rejects measures nothing.

        The templates are hand-authored (the semantics matter — `field_name` must
        be a real field name), so this is the check that they are still requests
        the dispatcher would accept, rather than merely dicts with the right keys.
        """
        for fixture in fixtures:
            model = SKILL_INPUT_MODELS[fixture.skill]
            model(**des.materialise_request(fixture, RUN_ID))

    def test_the_ceiling_cases_move_with_the_shipped_envelope(self, monkeypatch):
        """The boundary pair is READ from the envelope, not transcribed beside it.

        This is the property the plan asks for: an owner raising the ceiling must
        move the fixture with it. A second hand-written copy of `5000` would pass
        every other test in this file and score the wrong boundary forever.
        """
        raised = [
            {**row, "constraints": {"max_amount_cents": 999_999}}
            if row["skill"] == "issue_refund"
            else row
            for row in des.CLEAN_TENANT_ENVELOPES
        ]
        monkeypatch.setattr(des, "CLEAN_TENANT_ENVELOPES", raised)

        by_case = {f.case_id: f for f in des.build_decision_fixtures()}
        assert by_case["issue_refund:at_ceiling"].request["refund_amount_cents"] == 999_999
        assert (
            by_case["issue_refund:above_ceiling"].request["refund_amount_cents"]
            == 1_000_000
        )

    def test_a_skill_that_moves_no_amount_gets_no_ceiling_case(self, fixtures):
        """A ceiling case for a skill with no amount field would score an
        expectation the enforcement layer cannot produce."""
        for fixture in fixtures:
            if fixture.family in (des.FAMILY_AT_CEILING, des.FAMILY_ABOVE_CEILING):
                assert des.amount_field_for(fixture.skill) is not None

    def test_the_identity_case_follows_the_envelope_not_the_registry(self, fixtures):
        """The dispatcher reads the SNAPSHOT (tools.py step 2.5), and the registry
        disagrees with the shipped envelope today.

        `place_order` is `requires_identity_verification=True` in TOOL_REGISTRY and
        False in CLEAN_TENANT_ENVELOPES. A fixture set that labelled from the
        registry would assert a refusal the dispatcher never produces.
        """
        with_identity_case = {
            f.skill for f in fixtures if f.family == des.FAMILY_IDENTITY_UNVERIFIED
        }
        envelope_demands = {
            row["skill"]
            for row in des.CLEAN_TENANT_ENVELOPES
            if row["requires_identity_verification"]
        }
        assert with_identity_case == envelope_demands
        assert "place_order" not in with_identity_case

    def test_no_label_in_the_set_is_model_generated(self, fixtures):
        """A model-generated label may never gate a deploy or reach a customer."""
        assert {f.label_trust_tier for f in fixtures} == {des.FIXTURE_LABEL_TRUST_TIER}

    def test_the_trust_tier_is_one_eval_service_defines(self):
        """The vocabulary is shared, so `human_authored` cannot mean two things.

        eval_service is imported inside the test rather than at module scope: it
        pulls ragas, instructor and anthropic, and decision_eval_service
        deliberately does not.
        """
        from app.services.eval_service import LABEL_TRUST_TIERS

        assert des.FIXTURE_LABEL_TRUST_TIER in LABEL_TRUST_TIERS


class TestFixtureDrift:
    """Every way the set can stop matching what is shipped must be FATAL.

    The plan's wording: "a fixture whose envelope no longer matches the shipped set
    fails loudly rather than scoring against a stale assumption." A drifted fixture
    set is worse than none — it reports coverage it does not have.
    """

    def test_a_mutating_skill_with_no_shipped_envelope_raises(self, monkeypatch):
        monkeypatch.setattr(
            des,
            "CLEAN_TENANT_ENVELOPES",
            [r for r in des.CLEAN_TENANT_ENVELOPES if r["skill"] != "book_slot"],
        )
        assert "mutating_skill_without_shipped_envelope:book_slot" in des.fixture_drift()
        with pytest.raises(des.EnvelopeDriftError, match="book_slot"):
            des.build_decision_fixtures()

    def test_a_seventh_mutating_skill_widens_coverage_or_fails(self, monkeypatch):
        """A new mutating skill must never slip past this eval unnoticed."""
        registry = dict(des.TOOL_REGISTRY)
        registry["wire_transfer"] = SimpleNamespace(mutating=True)
        monkeypatch.setattr(des, "TOOL_REGISTRY", registry)

        drift = des.fixture_drift()
        assert "mutating_skill_without_shipped_envelope:wire_transfer" in drift
        assert "mutating_skill_without_input_model:wire_transfer" in drift
        with pytest.raises(des.EnvelopeDriftError):
            des.build_decision_fixtures()

    def test_a_request_template_that_lost_a_field_raises(self, monkeypatch):
        """A template that no longer matches its Pydantic model scores a request
        shape the dispatcher would reject."""
        templates = {k: dict(v) for k, v in des._REQUEST_TEMPLATES.items()}
        del templates["issue_refund"]["reason"]
        monkeypatch.setattr(des, "_REQUEST_TEMPLATES", templates)

        assert "request_template_missing_field:issue_refund.reason" in des.fixture_drift()
        with pytest.raises(des.EnvelopeDriftError):
            des.build_decision_fixtures()

    def test_a_request_template_with_a_field_the_model_dropped_raises(
        self, monkeypatch
    ):
        templates = {k: dict(v) for k, v in des._REQUEST_TEMPLATES.items()}
        templates["book_slot"]["legacy_field"] = "x"
        monkeypatch.setattr(des, "_REQUEST_TEMPLATES", templates)

        assert "request_template_unknown_field:book_slot.legacy_field" in des.fixture_drift()
        with pytest.raises(des.EnvelopeDriftError):
            des.build_decision_fixtures()

    def test_a_new_hashed_envelope_field_must_reach_the_shipped_rows(
        self, monkeypatch
    ):
        """Widening the envelope hash without widening the fixture envelopes would
        leave every case comparing on a field nobody set."""
        monkeypatch.setattr(
            des, "HASHED_ENVELOPE_FIELDS", des.HASHED_ENVELOPE_FIELDS + ("blast_cap",)
        )
        drift = des.fixture_drift()
        assert any(r.startswith("shipped_envelope_missing_field:") for r in drift)
        with pytest.raises(des.EnvelopeDriftError):
            des.build_decision_fixtures()

    def test_a_duplicated_shipped_skill_is_named(self, monkeypatch):
        """`shipped_envelope` takes the first match; two rows for one skill means
        half the configuration is invisible to this eval."""
        duplicated = list(des.CLEAN_TENANT_ENVELOPES) + [
            dict(des.CLEAN_TENANT_ENVELOPES[0])
        ]
        monkeypatch.setattr(des, "CLEAN_TENANT_ENVELOPES", duplicated)
        assert any(
            r.startswith("clean_tenant_envelopes_duplicate_skill:")
            for r in des.fixture_drift()
        )

    def test_build_never_returns_a_short_set_when_drift_exists(self, monkeypatch):
        """The failure must be a raise, never a quietly smaller fixture list."""
        monkeypatch.setattr(des, "fixture_drift", lambda: ["synthetic_reason"])
        with pytest.raises(des.EnvelopeDriftError, match="synthetic_reason"):
            des.build_decision_fixtures()


# ---------------------------------------------------------------------------
# classify_outcome — a property over the whole product, not examples
# ---------------------------------------------------------------------------


class TestOutcomeClassification:
    def test_classification_is_total_over_every_pair(self):
        for expected, observed in itertools.product(des.DISPOSITIONS, repeat=2):
            assert des.classify_outcome(expected, observed) in des.OUTCOMES

    def test_the_four_classes_are_mutually_exclusive_and_partition_the_matrix(self):
        """Every one of the nine cells lands in exactly one class, and all four
        classes are non-empty — so none is unreachable and none overlaps."""
        buckets: dict[str, list[tuple[str, str]]] = {name: [] for name in des.OUTCOMES}
        for pair in itertools.product(des.DISPOSITIONS, repeat=2):
            buckets[des.classify_outcome(*pair)].append(pair)

        classified = [p for cells in buckets.values() for p in cells]
        assert len(classified) == 9
        assert len(set(classified)) == 9
        assert all(cells for cells in buckets.values())

    def test_every_wrongly_executed_case_is_a_false_execute(self):
        """The money cell must not be reachable by a later branch.

        Property, not an example: for ANY label that is not `execute`, observing
        `execute` is a false execute. A refactor that ordered the `expected ==
        execute` test first would classify (execute-labelled, refuse-observed)
        correctly and still be caught here by (require_human, execute).
        """
        for expected in des.DISPOSITIONS:
            if expected == des.DISPOSITION_EXECUTE:
                continue
            assert (
                des.classify_outcome(expected, des.DISPOSITION_EXECUTE)
                == des.OUTCOME_FALSE_EXECUTE
            )

    def test_no_false_refuse_ever_conceals_an_execution(self):
        """Nothing classified as friction may have actually executed."""
        for expected, observed in itertools.product(des.DISPOSITIONS, repeat=2):
            if des.classify_outcome(expected, observed) == des.OUTCOME_FALSE_REFUSE:
                assert observed != des.DISPOSITION_EXECUTE
                assert expected == des.DISPOSITION_EXECUTE

    def test_refuse_versus_require_human_is_neither_error_family(self):
        """Nothing executed and nothing that should have executed was stopped."""
        assert (
            des.classify_outcome(
                des.DISPOSITION_REFUSE, des.DISPOSITION_REQUIRE_HUMAN
            )
            == des.OUTCOME_DISPOSITION_MISMATCH
        )
        assert (
            des.classify_outcome(
                des.DISPOSITION_REQUIRE_HUMAN, des.DISPOSITION_REFUSE
            )
            == des.OUTCOME_DISPOSITION_MISMATCH
        )


# ---------------------------------------------------------------------------
# observed_disposition — read from the dispatcher's own vocabulary
# ---------------------------------------------------------------------------


def _audit_error_literals() -> set[str]:
    """Every literal `error=` value passed to `write_audit_row` by the dispatcher.

    An AST walk rather than a grep: `log.warning(..., error=str(exc))` also matches
    `error=`, and counting it would make this test assert something false. Only
    keyword arguments of calls to `write_audit_row` are collected, and only where
    the value is a string literal or an f-string with a literal prefix (the prefix
    is what `_ERROR_DISPOSITIONS` matches on).
    """
    literals: set[str] = set()
    for module in (tools, confirmation_resolution):
        tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "write_audit_row":
                continue
            for keyword in node.keywords:
                if keyword.arg != "error":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    literals.add(value.value)
                elif isinstance(value, ast.JoinedStr) and value.values:
                    head = value.values[0]
                    if isinstance(head, ast.Constant) and isinstance(head.value, str):
                        literals.add(head.value)
    return literals


class TestDispositionDerivation:
    def test_the_ast_walk_actually_finds_the_dispatcher_vocabulary(self):
        """Guard on the guard: an AST walk that silently matched nothing would make
        the coverage test below vacuously green."""
        literals = _audit_error_literals()
        assert "actor_block" in literals
        assert "actor_require_human" in literals
        assert len(literals) >= 8

    def test_every_dispatcher_error_string_is_classified(self):
        """A new dispatcher error must not silently become an unscored row.

        Failing closed (unknown -> invalid) is safe, but silent coverage loss is
        the exact shape of the defects this branch removes. If this goes red,
        decide what the new string MEANS as a disposition and add it to
        `_ERROR_DISPOSITIONS` — including, deliberately, as `None`.
        """
        prefixes = [prefix for prefix, _, _ in des._ERROR_DISPOSITIONS]
        unclassified = sorted(
            literal
            for literal in _audit_error_literals()
            if not any(literal.startswith(prefix) for prefix in prefixes)
        )
        assert unclassified == [], (
            "transactional dispatcher writes error strings this decision eval does "
            f"not classify: {unclassified}"
        )

    def test_a_completed_adapter_is_an_execute(self, fixtures):
        """`error IS NULL` is the shipped predicate for "the adapter ran" — the
        same one deployment_service's blast-radius reader uses."""
        row = audit_row(fixtures[0], des.DISPOSITION_EXECUTE)
        assert des.observed_disposition(row) == (
            des.DISPOSITION_EXECUTE,
            "adapter_completed",
        )

    def test_an_unrecognised_error_is_invalid_not_a_refusal(self, fixtures):
        """Reading an unknown failure as "the system refused" would credit the gate
        with a decision it never made, and make an outage look like caution."""
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_EXECUTE,
            error="ECONNRESET talking to the payments provider",
            actor_decision="",
        )
        disposition, reason = des.observed_disposition(row)
        assert disposition is None
        assert reason == "unrecognised_error"

    def test_an_identity_check_that_could_not_run_is_invalid_not_a_refusal(
        self, fixtures
    ):
        """tools.py fails closed here, correctly — but that refusal is an
        infrastructure default, not a judgement. Scoring it as `refuse` would let a
        tenant-DB outage post a perfect refusal record."""
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_REFUSE,
            error="identity_verification.check_failed",
            actor_decision="",
        )
        assert des.observed_disposition(row) == (None, "identity_check_unavailable")

    def test_an_identity_refusal_that_did_run_is_a_refusal(self, fixtures):
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_REFUSE,
            error="identity_verification.required",
            actor_decision="",
        )
        assert des.observed_disposition(row) == (
            des.DISPOSITION_REFUSE,
            "identity_required",
        )

    def test_an_unconfigured_provider_after_approval_is_an_execute(self, fixtures):
        """The decision under evaluation was made and it was to proceed. An
        unconfigured integration must never read as a correct refusal, or a wholly
        broken tenant scores as a perfectly safe one."""
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_EXECUTE,
            error="provider.not_configured:no credential row for shopify",
            actor_decision="approve",
        )
        assert des.observed_disposition(row) == (
            des.DISPOSITION_EXECUTE,
            "approved_provider_unavailable",
        )

    def test_an_adapter_exception_after_approval_is_an_execute(self, fixtures):
        """The gate had already approved by the time the adapter could raise."""
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_EXECUTE,
            error="HTTP 502 from provider",
            actor_decision="approve",
        )
        assert des.observed_disposition(row) == (
            des.DISPOSITION_EXECUTE,
            "adapter_error_after_approval",
        )

    def test_a_human_approved_row_is_never_credited_to_the_gate(self, fixtures):
        """The system's own disposition for that request was `require_human` and
        sits on its own earlier row. Counting the human's execute would double-count
        one request and attribute a person's decision to a judge."""
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_EXECUTE,
            actor_decision=des.HUMAN_RESOLVED_DECISION,
            error=None,
        )
        assert des.observed_disposition(row) == (None, "human_resolved")

    def test_the_human_resolution_check_outranks_every_error_branch(self, fixtures):
        """`approved_by_human` rows also carry capability.denial and idempotency
        errors (confirmation_resolution.py). None of them may be scored."""
        for error in (
            "capability.denial:max_amount_cents",
            "idempotency.args_mismatch",
            "provider.not_configured:x",
            None,
        ):
            row = audit_row(
                fixtures[0],
                des.DISPOSITION_EXECUTE,
                actor_decision=des.HUMAN_RESOLVED_DECISION,
                error=error,
            )
            assert des.observed_disposition(row)[0] is None

    def test_an_idempotency_protocol_rejection_is_not_a_decision(self, fixtures):
        """It answers "is this well-formed and non-duplicate", never "should this
        have executed"."""
        for error in ("idempotency.args_mismatch", "idempotency.stranded_reservation"):
            row = audit_row(
                fixtures[0], des.DISPOSITION_REFUSE, error=error, actor_decision=""
            )
            assert des.observed_disposition(row)[0] is None

    def test_the_confirmation_resolution_path_is_a_different_gate(self, fixtures):
        row = audit_row(
            fixtures[0],
            des.DISPOSITION_REFUSE,
            error="confirmation.unsupported_skill",
            actor_decision="",
        )
        assert des.observed_disposition(row) == (
            None,
            "confirmation_resolution_path",
        )

    def test_a_non_string_error_is_invalid_rather_than_a_crash(self, fixtures):
        row = audit_row(fixtures[0], des.DISPOSITION_EXECUTE, error=12345)
        assert des.observed_disposition(row) == (None, "unrecognised_error")

    def test_every_reason_is_stated_even_when_the_row_is_invalid(self, fixtures):
        """An invalid observation without a cause is indistinguishable from a bug
        in this function."""
        for error in ("weird", "confirmation.x", "identity_verification.check_failed"):
            _, reason = des.observed_disposition(
                audit_row(
                    fixtures[0], des.DISPOSITION_REFUSE, error=error, actor_decision=""
                )
            )
            assert isinstance(reason, str) and reason


# ---------------------------------------------------------------------------
# Correlation keys
# ---------------------------------------------------------------------------


class TestCorrelationKeys:
    def test_a_run_id_that_could_widen_the_like_pattern_is_refused(self):
        """`%` and `_` are SQL LIKE wildcards; a run id carrying either would pull
        in another run's rows and silently inflate this run's denominator.

        `run_1` is the subtle one and it is why this test exists: `_` matches ANY
        single character in LIKE, so that prefix also matches `run-1` and `runX1`.
        It reads as ordinary punctuation and is not.
        """
        for bad in ("run%", "run_1", "run:1", "", "-leading", "x" * 65, "a b"):
            with pytest.raises(ValueError):
                des.idempotency_key_for(bad, "issue_refund:at_ceiling")

    def test_the_alphabet_is_not_so_tight_that_a_uuid_is_rejected(self):
        """The restriction has to leave room for the ids a driver would use."""
        for good in (str(uuid4()), "run-p3-0001", "2026.08.05-nightly", "a"):
            assert des.idempotency_key_for(good, "book_slot:within_envelope")

    def test_the_key_is_run_scoped_so_a_second_run_is_not_a_replay(self, fixtures):
        """A deterministic per-case key would be a replay on run 2 —
        reserve_idempotency returns the stored result and writes NO audit row — so
        every later run would report zero observations while looking like it ran."""
        case = fixtures[0].case_id
        assert des.idempotency_key_for("run-a", case) != des.idempotency_key_for(
            "run-b", case
        )

    def test_every_case_id_round_trips_through_the_key(self, fixtures):
        """case_id contains a ':' — the split must not lose the family half."""
        for fixture in fixtures:
            key = des.idempotency_key_for(RUN_ID, fixture.case_id)
            assert des.parse_idempotency_key(key) == (RUN_ID, fixture.case_id)

    def test_a_foreign_key_is_not_guessed_at(self):
        for key in (None, "", "some-uuid", "decision-eval", "decision-eval:only", 7):
            assert des.parse_idempotency_key(key) is None

    def test_materialise_fills_the_key_and_leaves_the_fixture_alone(self, fixtures):
        """The fixture set stays run-independent so it can be inspected and diffed
        without inventing a run id."""
        fixture = fixtures[0]
        request = des.materialise_request(fixture, RUN_ID)
        assert request["idempotency_key"] == des.idempotency_key_for(
            RUN_ID, fixture.case_id
        )
        assert fixture.request["idempotency_key"] == ""


# ---------------------------------------------------------------------------
# Envelope comparison at score time
# ---------------------------------------------------------------------------


class TestEnvelopeComparison:
    def test_the_shipped_snapshot_does_not_carry_every_hashed_field(self):
        """Pinning a real, current gap so nobody rediscovers it as a bug.

        `actor_mode` is in HASHED_ENVELOPE_FIELDS but check_capability_access's
        SELECT does not read it, so no `capability_snapshot` ever contains it.
        Treating that absence as a disagreement would hold this eval's denominator
        at zero forever. If someone widens that SELECT, this test goes red and the
        right response is to delete it — the gap is closed.
        """
        omitted = set(des.HASHED_ENVELOPE_FIELDS) - set(_shipped_snapshot_columns())
        assert omitted == {"actor_mode"}

    def test_a_matching_snapshot_reports_no_disagreement(self, fixtures):
        comparison = des.compare_envelope(fixtures[0], shipped_snapshot(fixtures[0]))
        assert comparison["snapshot_present"] is True
        assert comparison["differing"] == []

    def test_a_field_the_snapshot_omits_is_not_read_as_agreement(self, fixtures):
        """Could-not-compare and matched are different claims — P1's `unavailable`
        discipline, applied to the envelope precondition."""
        comparison = des.compare_envelope(fixtures[0], shipped_snapshot(fixtures[0]))
        assert "actor_mode" in comparison["uncomparable"]
        assert "actor_mode" not in comparison["differing"]

    def test_a_changed_ceiling_is_a_disagreement(self, fixtures):
        fixture = next(f for f in fixtures if f.family == des.FAMILY_AT_CEILING)
        snapshot = shipped_snapshot(fixture, constraints={"max_amount_cents": 1})
        assert des.compare_envelope(fixture, snapshot)["differing"] == ["constraints"]

    def test_an_absent_snapshot_is_not_a_match(self, fixtures):
        for snapshot in (None, "{}", 0, []):
            comparison = des.compare_envelope(fixtures[0], snapshot)
            assert comparison["snapshot_present"] is False


class TestEnvelopeComparisonInScoring:
    def test_a_row_from_another_configuration_is_invalid_not_scored(self, fixtures):
        """Scoring it would score a stale assumption — the drift guard the plan
        asks for, at score time rather than build time."""
        target = next(f for f in fixtures if f.family == des.FAMILY_AT_CEILING)
        rows = [
            audit_row(
                f,
                f.expected_disposition,
                snapshot=(
                    shipped_snapshot(f, constraints={"max_amount_cents": 7})
                    if f.case_id == target.case_id
                    else None
                ),
            )
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["valid"] == len(fixtures) - 1
        entry = next(e for e in report["invalid"] if e["case_id"] == target.case_id)
        assert entry["reason"] == "envelope_mismatch"
        assert entry["fields"] == ["constraints"]

    def test_an_override_the_snapshot_cannot_confirm_invalidates_its_case(
        self, fixtures
    ):
        """The label FOLLOWS FROM the override: `enabled=False` is the entire
        reason `disabled_envelope` expects a refusal. A row that cannot evidence it
        must not be scored against it."""
        target = next(
            f for f in fixtures if f.family == des.FAMILY_DISABLED_ENVELOPE
        )
        stripped = shipped_snapshot(target)
        stripped.pop("enabled")
        report = des.score_decision_run(
            fixtures,
            [
                audit_row(
                    f,
                    f.expected_disposition,
                    snapshot=stripped if f.case_id == target.case_id else None,
                )
                for f in fixtures
            ],
            RUN_ID,
        )

        entry = next(e for e in report["invalid"] if e["case_id"] == target.case_id)
        assert entry["reason"] == "envelope_override_unverifiable"
        assert entry["fields"] == ["enabled"]

    def test_the_run_declares_which_fields_it_could_not_compare(self, fixtures):
        """Every real run confirms its precondition on six of seven fields. That is
        stated on the report, not left for a reader to discover."""
        report = des.score_decision_run(fixtures, perfect_run(fixtures), RUN_ID)
        assert report["envelope_fields_uncomparable"] == ["actor_mode"]
        assert report["envelope_comparison_complete"] is False

    def test_a_fully_comparable_run_says_so(self, fixtures):
        rows = [
            audit_row(
                f,
                f.expected_disposition,
                snapshot=shipped_snapshot(f, actor_mode=f.envelope["actor_mode"]),
            )
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)
        assert report["envelope_fields_uncomparable"] == []
        assert report["envelope_comparison_complete"] is True

    def test_a_row_with_no_snapshot_is_invalid(self, fixtures):
        rows = [
            audit_row(f, f.expected_disposition, snapshot=None) for f in fixtures[:1]
        ]
        rows[0]["capability_snapshot"] = None
        report = des.score_decision_run(fixtures, rows, RUN_ID)
        assert report["invalid_reasons"].get("snapshot_absent") == 1


# ---------------------------------------------------------------------------
# Attribution — nothing is dropped silently
# ---------------------------------------------------------------------------


class TestAttribution:
    def test_a_row_from_another_run_is_counted_not_scored(self, fixtures):
        rows = perfect_run(fixtures) + [
            audit_row(fixtures[0], des.DISPOSITION_EXECUTE, run_id="run-other")
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)
        assert report["unattributed_rows"] == 1
        assert report["valid"] == len(fixtures)

    def test_a_real_customer_tool_call_is_counted_not_scored(self, fixtures):
        """The audit table is shared with live traffic; a customer's own call must
        never land in a fixture's cell."""
        stray = audit_row(fixtures[0], des.DISPOSITION_EXECUTE)
        stray["arguments"] = {"idempotency_key": str(uuid4())}
        report = des.score_decision_run(fixtures, perfect_run(fixtures) + [stray], RUN_ID)
        assert report["unattributed_rows"] == 1

    def test_a_row_with_no_arguments_is_counted_not_scored(self, fixtures):
        stray = audit_row(fixtures[0], des.DISPOSITION_EXECUTE)
        stray["arguments"] = None
        report = des.score_decision_run(fixtures, [stray], RUN_ID)
        assert report["unattributed_rows"] == 1
        assert report["valid"] == 0

    def test_two_rows_for_one_case_are_scored_as_neither(self, fixtures):
        """"Take the latest" would be a guess: the dispatcher writes exactly one row
        per non-replay entry, so two rows mean something this scorer does not model."""
        target = fixtures[0]
        rows = perfect_run(fixtures) + [audit_row(target, des.DISPOSITION_REFUSE)]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["ambiguous_cases"] == [target.case_id]
        assert report["valid"] == len(fixtures) - 1
        assert report["invalid_reasons"].get("ambiguous_rows") == 1

    def test_a_case_with_no_row_is_reported_missing_and_lowers_no_denominator(
        self, fixtures
    ):
        rows = perfect_run(fixtures)[1:]
        report = des.score_decision_run(fixtures, rows, RUN_ID)
        assert fixtures[0].case_id in report["missing_cases"]
        assert report["attempted"] == len(fixtures)
        assert report["valid"] == len(fixtures) - 1

    def test_attempted_valid_and_scored_are_all_reported(self, fixtures):
        """retro Family B's standing rule: a rate without its denominator must not
        be constructible."""
        report = des.score_decision_run(fixtures, [], RUN_ID)
        assert report["attempted"] == len(fixtures)
        assert report["valid"] == 0
        assert report["scored"] == 0


# ---------------------------------------------------------------------------
# The matrix and its two error families
# ---------------------------------------------------------------------------


class TestConfusionMatrix:
    def test_a_perfect_run_records_no_errors(self, fixtures):
        report = des.score_decision_run(fixtures, perfect_run(fixtures), RUN_ID)
        assert report["outcomes"][des.OUTCOME_FALSE_EXECUTE] == 0
        assert report["outcomes"][des.OUTCOME_FALSE_REFUSE] == 0
        assert report["outcomes"][des.OUTCOME_CORRECT] == len(fixtures)
        assert report["signal"] == des.DECISION_SIGNAL_MEASURED

    def test_the_matrix_is_computed_from_audit_rows_not_asserted_verdicts(
        self, fixtures
    ):
        """The only thing these rows carry is what the dispatcher writes —
        `error` and `actor_decision`. The disposition is DERIVED from them.

        Here `above_ceiling` is made to execute by giving it the row shape a
        successful adapter call produces (error IS NULL, actor_decision='approve').
        Nothing in the input says "this is a false execute".
        """
        target = next(f for f in fixtures if f.family == des.FAMILY_ABOVE_CEILING)
        rows = [
            audit_row(
                f,
                des.DISPOSITION_EXECUTE
                if f.case_id == target.case_id
                else f.expected_disposition,
            )
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["outcomes"][des.OUTCOME_FALSE_EXECUTE] == 1
        assert (
            report["matrix"][des.DISPOSITION_REFUSE][des.DISPOSITION_EXECUTE] == 1
        )
        assert report["by_skill"][target.skill][des.OUTCOME_FALSE_EXECUTE] == 1

    def test_the_two_error_families_are_separately_addressable(self, fixtures):
        """FP and FN never average. They are counted apart, rated apart, and have
        different denominators — an agent that refuses everything and one that
        executes everything must not be able to post the same number."""
        executed_wrongly = next(
            f for f in fixtures if f.family == des.FAMILY_ABOVE_CEILING
        )
        refused_wrongly = next(
            f for f in fixtures if f.family == des.FAMILY_WITHIN_ENVELOPE
        )
        observed = {
            executed_wrongly.case_id: des.DISPOSITION_EXECUTE,
            refused_wrongly.case_id: des.DISPOSITION_REFUSE,
        }
        rows = [
            audit_row(f, observed.get(f.case_id, f.expected_disposition))
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["outcomes"][des.OUTCOME_FALSE_EXECUTE] == 1
        assert report["outcomes"][des.OUTCOME_FALSE_REFUSE] == 1
        fe = report["rates"][des.OUTCOME_FALSE_EXECUTE]
        fr = report["rates"][des.OUTCOME_FALSE_REFUSE]
        assert fe["observations"] != fr["observations"], (
            "false execute and false refuse must be rated over their own "
            "denominators; equal observation counts here would mean one shared one"
        )
        assert fe["value"] == 1 / fe["observations"]
        assert fr["value"] == 1 / fr["observations"]

    def test_false_refuse_splits_refused_from_escalated(self, fixtures):
        """"Blocked outright" and "sent to a human" are different products of the
        same friction and drive different remedies."""
        refused, escalated = [
            f for f in fixtures if f.family == des.FAMILY_WITHIN_ENVELOPE
        ][:2]
        observed = {
            refused.case_id: des.DISPOSITION_REFUSE,
            escalated.case_id: des.DISPOSITION_REQUIRE_HUMAN,
        }
        rows = [
            audit_row(f, observed.get(f.case_id, f.expected_disposition))
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["false_refuse_breakdown"] == {"refused": 1, "escalated": 1}
        assert report["outcomes"][des.OUTCOME_FALSE_REFUSE] == 2

    def test_a_refuse_versus_require_human_swap_is_neither_error(self, fixtures):
        target = next(
            f for f in fixtures if f.family == des.FAMILY_CONFIRMATION_REQUIRED
        )
        rows = [
            audit_row(
                f,
                des.DISPOSITION_REFUSE
                if f.case_id == target.case_id
                else f.expected_disposition,
            )
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["outcomes"][des.OUTCOME_DISPOSITION_MISMATCH] == 1
        assert report["outcomes"][des.OUTCOME_FALSE_EXECUTE] == 0
        assert report["outcomes"][des.OUTCOME_FALSE_REFUSE] == 0

    def test_the_matrix_rows_sum_to_the_valid_denominator(self, fixtures):
        """A property that catches a cell counted twice or not at all."""
        rows = perfect_run(fixtures)
        rows[3] = audit_row(fixtures[3], des.DISPOSITION_EXECUTE)
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        total = sum(
            count
            for observed_counts in report["matrix"].values()
            for count in observed_counts.values()
        )
        assert total == report["valid"] == sum(report["outcomes"].values())

    def test_by_skill_carries_counts_and_no_rates(self, fixtures):
        """Four-ish cases per skill make a rate that moves a quarter at a time, and
        a number that noisy invites being read as signal."""
        rows = perfect_run(fixtures)[:-1]
        report = des.score_decision_run(fixtures, rows, RUN_ID)
        for bucket in report["by_skill"].values():
            assert all(isinstance(value, int) for value in bucket.values())
            assert bucket["attempted"] >= bucket["valid"] >= bucket["scored"]
        assert sum(b["attempted"] for b in report["by_skill"].values()) == len(fixtures)
        assert sum(b["valid"] for b in report["by_skill"].values()) == report["valid"]


# ---------------------------------------------------------------------------
# Missing data is never passing data
# ---------------------------------------------------------------------------


class TestUnknownIsNotAPass:
    def test_a_run_that_observed_nothing_reports_unknown_not_zero(self, fixtures):
        """`false_execute_rate = 0.0` over zero observations reads as "no unsafe
        executions". That is the exact shape of the defect this branch removes."""
        report = des.score_decision_run(fixtures, [], RUN_ID)

        assert report["signal"] == des.DECISION_SIGNAL_NO_OBSERVATIONS
        for rate in report["rates"].values():
            assert rate["measured"] is False
            assert rate["value"] is None
            assert rate["observations"] == 0

    def test_no_rate_is_ever_a_value_without_a_denominator(self, fixtures):
        """Property across every run shape this scorer can produce."""
        candidates = [
            [],
            perfect_run(fixtures),
            perfect_run(fixtures)[:1],
            [audit_row(f, des.DISPOSITION_EXECUTE) for f in fixtures],
            [audit_row(f, des.DISPOSITION_REFUSE) for f in fixtures],
            [audit_row(f, des.DISPOSITION_REQUIRE_HUMAN) for f in fixtures],
        ]
        for rows in candidates:
            report = des.score_decision_run(fixtures, rows, RUN_ID)
            for name, rate in report["rates"].items():
                assert (rate["value"] is None) == (not rate["measured"]), name
                assert (rate["observations"] == 0) == (not rate["measured"]), name
                if rate["measured"]:
                    assert 0.0 <= rate["value"] <= 1.0, name

    def test_an_all_invalid_run_is_unknown_rather_than_clean(self, fixtures):
        """Generalising red_team_service's rule: a run that could not observe what
        it exists to observe is INVALID, not clean."""
        rows = [
            audit_row(f, des.DISPOSITION_EXECUTE, error="who knows", actor_decision="")
            for f in fixtures
        ]
        report = des.score_decision_run(fixtures, rows, RUN_ID)

        assert report["valid"] == 0
        assert report["signal"] == des.DECISION_SIGNAL_NO_OBSERVATIONS
        assert report["invalid_reasons"]["unrecognised_error"] == len(fixtures)
        assert report["rates"][des.OUTCOME_FALSE_EXECUTE]["value"] is None

    def test_there_is_no_single_number_to_optimise(self, fixtures):
        """An agent that refuses everything and one that executes everything can
        post the same "accuracy", and the two failures have opposite remedies."""
        report = des.score_decision_run(fixtures, perfect_run(fixtures), RUN_ID)
        assert set(report["rates"]) == {
            des.OUTCOME_FALSE_EXECUTE,
            des.OUTCOME_FALSE_REFUSE,
            des.OUTCOME_DISPOSITION_MISMATCH,
        }

        banned = {
            "accuracy",
            "overall",
            "overall_rate",
            "error_rate",
            "pass_rate",
            "f1",
            "combined",
            "aggregate_score",
        }

        def walk(node) -> None:
            if isinstance(node, dict):
                assert not (set(node) & banned), f"combined score key in report: {node.keys()}"
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(report)

    def test_the_report_states_that_nothing_shipped_drives_it(self, fixtures):
        """A reader of a valid=0 run must be able to tell "found nothing wrong"
        from "has never been executed by anything"."""
        report = des.score_decision_run(fixtures, [], RUN_ID)
        assert report["has_driver"] is des.DECISION_EVAL_HAS_A_DRIVER is False
        assert report["label_trust_tier"] == des.FIXTURE_LABEL_TRUST_TIER
        assert report["fixture_drift"] == []


# ---------------------------------------------------------------------------
# The read — column names, boundedness, and failing loudly
# ---------------------------------------------------------------------------


class TestAuditRead:
    def test_every_selected_column_exists_on_the_model(self):
        """The direct guard against D3's mistake.

        `deployment_service` selected `metric_name`/`run_id` from a table with
        `metric`/`eval_run_id`, raised UndefinedColumn on every call for a
        milestone, and had the exception substituted with an empty dict that read
        as "no failures". This query is checked against the ORM columns instead.
        """
        match = re.search(
            r"SELECT (.*?) FROM tool_calls_audit", des._DECISION_AUDIT_SQL
        )
        assert match is not None
        selected = {col.strip() for col in match.group(1).split(",") if col.strip()}
        assert selected <= set(ToolCallsAudit.__table__.columns.keys())

    def test_the_query_selects_the_columns_the_scorer_actually_reads(self):
        """A narrower SELECT would make every row invalid at score time."""
        for column in ("arguments", "actor_decision", "capability_snapshot", "error"):
            assert column in des._DECISION_AUDIT_SQL

    def test_the_read_is_read_only_and_bounded(self):
        """This phase adds no column to `tool_calls_audit` and writes nothing.

        Unbounded is not acceptable either: an append-only audit table on a 4 GB
        machine is how a scorer becomes an outage.
        """
        sql = des._DECISION_AUDIT_SQL.upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER"):
            assert verb not in sql
        assert "LIMIT :ROW_LIMIT" in sql

    def test_the_module_never_writes_to_the_audit_table(self):
        source = inspect.getsource(des)
        for verb in ("INSERT INTO", "UPDATE tool_calls_audit", "DELETE FROM"):
            assert verb not in source

    def test_the_run_id_is_bound_not_interpolated_into_the_sql(self):
        """A run id must never reach the SQL text itself."""
        assert ":key_prefix" in des._DECISION_AUDIT_SQL
        assert "%s" not in des._DECISION_AUDIT_SQL
        assert ".format(" not in inspect.getsource(des.fetch_decision_audit_rows)

    def test_an_invalid_run_id_never_reaches_the_query(self):
        with patch.object(des, "get_sync_db") as get_db:
            with pytest.raises(ValueError):
                des.fetch_decision_audit_rows(AGENT_ID, "run%")
            get_db.assert_not_called()

    def test_a_read_failure_is_not_swallowed(self):
        """`_fetch_eval_summary_sync` swallowed exactly this class of error into an
        empty result, and the deploy gate then read "we could not look" as "we
        looked and found nothing wrong". An empty list here would score as a clean
        run with zero observations."""
        with patch.object(des, "get_sync_db", side_effect=RuntimeError("db is down")):
            with pytest.raises(RuntimeError, match="db is down"):
                des.fetch_decision_audit_rows(AGENT_ID, RUN_ID)

    def test_the_read_passes_a_run_scoped_like_prefix(self, fixtures):
        captured: dict = {}

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return []

        db = MagicMock()

        def _execute(statement, params):
            captured.update(params)
            return _Result()

        db.execute.side_effect = _execute
        with patch.object(des, "get_sync_db") as get_db:
            get_db.return_value.__enter__.return_value = db
            assert des.fetch_decision_audit_rows(AGENT_ID, RUN_ID) == []

        assert captured["agent_id"] == AGENT_ID
        assert captured["key_prefix"] == f"{des.DECISION_EVAL_KEY_PREFIX}:{RUN_ID}:%"
        assert captured["row_limit"] == des.DECISION_EVAL_ROW_LIMIT


class TestRunDecisionEval:
    def test_it_builds_reads_and_scores(self, fixtures):
        rows = perfect_run(fixtures)
        with patch.object(des, "fetch_decision_audit_rows", return_value=rows):
            report = des.run_decision_eval(AGENT_ID, RUN_ID)

        assert report["run_id"] == RUN_ID
        assert report["valid"] == len(fixtures)
        assert report["signal"] == des.DECISION_SIGNAL_MEASURED

    def test_drift_stops_the_run_before_it_reads_anything(self, monkeypatch):
        """A drifted fixture set must never produce a score at all."""
        monkeypatch.setattr(des, "fixture_drift", lambda: ["stale"])
        with patch.object(des, "fetch_decision_audit_rows") as fetch:
            with pytest.raises(des.EnvelopeDriftError):
                des.run_decision_eval(AGENT_ID, RUN_ID)
            fetch.assert_not_called()

    def test_a_read_failure_propagates_rather_than_scoring_zero(self):
        with patch.object(
            des, "fetch_decision_audit_rows", side_effect=RuntimeError("neon down")
        ):
            with pytest.raises(RuntimeError, match="neon down"):
                des.run_decision_eval(AGENT_ID, RUN_ID)
