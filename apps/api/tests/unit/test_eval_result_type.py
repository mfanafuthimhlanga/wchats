"""EvalResult, the run record and the rules that refuse a dishonest one (ticket 14, #51).

THE DEFECT THIS FILE OPENS ON
    Nothing held one eval run's numbers together. The task built a return dict,
    `api/v1/evals.py` built a second set with `COUNT`/`AVG`, and
    `deployment_service._fetch_eval_summary_sync` built a third. A metric with no
    observations came out of the SQL as an absent key in one reader, as a NULL
    average in another, and as `{"value": None, "measured": False}` in the third,
    and only the third of those is unreadable as a pass.

    So the assertions below are about the boundary between "unknown" and "zero",
    which is the one this record exists to make unrepresentable.

WHAT EACH FIXTURE IS BUILT TO AVOID
    A record where every metric is measured makes criterion 4 come out true for a
    reason that has nothing to do with the rule. `_result()` therefore reports
    three of the four metrics on the golden dataset, one of them at zero
    observations and one absent entirely, so the three states a metric can be in
    are distinguishable in one payload.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from app.domain.eval_result import (
    CONTEXT_PROXY_VERSION,
    COST_UNKNOWN,
    EVAL_DATASETS,
    METRIC_KEYS,
    Cost,
    DatasetOutcome,
    EvalResult,
    InvalidEvalResult,
    Invocation,
    InvocationStatus,
    Measurement,
    ScenarioFailure,
    cost_of_run,
)
from app.domain.judge_identity import JudgeIdentity
from app.domain.model_call import ModelCall

RUN_ID = "3f3a1c66-0000-4000-8000-000000000051"
AGENT_ID = "3f3a1c66-0000-4000-8000-0000000000a9"

#: The metric reported over nothing, and the metric not reported at all. Naming
#: them once keeps every assertion below about the same two states.
UNMEASURED_METRIC = "context_recall"
ABSENT_METRIC = "context_precision"


def _call(**overrides) -> ModelCall:
    """One priced ledger row. `at` is inside the fx table and the price book."""
    fields = {
        "purpose": "judge_faithfulness",
        "provider": "openai",
        "requested_model": "gpt-5.6-luna",
        "served_model": "gpt-5.6-luna",
        "model_source": "reported",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "at": datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
        "tenant_id": AGENT_ID,
        "job_id": RUN_ID,
    }
    fields.update(overrides)
    return ModelCall(**fields)


def _identity() -> JudgeIdentity:
    return JudgeIdentity(
        model="gpt-5.6-luna", reasoning_effort="none", prompt_version="ragas-0.4.1"
    )


def _invocation(**overrides) -> Invocation:
    """A measured invocation: four turns attempted, four answered, four scored."""
    fields = {
        "status": InvocationStatus.MEASURED,
        "valid": 4,
        "attempted": 4,
        "responded": 4,
        "scorable": 4,
        "failed": 0,
        "empty": 0,
    }
    fields.update(overrides)
    return Invocation(**fields)


def _golden() -> DatasetOutcome:
    """Two rows, three metrics reported, one of the three over nothing.

    Both rows FAILED: 0.75 faithfulness and 0.5 answer relevancy are under the
    0.90 gates, and the two verdict counts have to add up to `scored` anyway.
    """
    return DatasetOutcome(
        attempted=2,
        valid=2,
        scored=2,
        scenarios_failed=2,
        metrics={
            "faithfulness": Measurement(value=0.75, observations=2, measured=True),
            "answer_relevancy": Measurement(value=0.5, observations=2, measured=True),
            UNMEASURED_METRIC: Measurement(value=None, observations=0, measured=False),
        },
    )


def _result(**overrides) -> EvalResult:
    fields = {
        "run_id": RUN_ID,
        "agent_id": AGENT_ID,
        "invocation": _invocation(),
        "datasets": {
            "golden": _golden(),
            # Two rows scored and no metric reported for either, so no gated
            # verdict decided either: unmeasured, never a pass.
            "exploratory": DatasetOutcome(2, 2, 2, scenarios_unmeasured=2),
        },
        "requested_model": "gpt-5.6-luna",
        "served_model": "gpt-5.6-luna-2026-08",
        "prompt_version_id": "pv-1",
        "judge_identity": _identity(),
    }
    fields.update(overrides)
    return EvalResult(**fields)


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


class TestTheRecordIsFrozen:
    """Frozen on every member, not only on the outer record.

    A mutable Measurement inside a frozen EvalResult would let a reader move a
    score after the run wrote it, and the record would still compare equal to
    whatever anybody expected.
    """

    def test_a_result_field_cannot_be_reassigned(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _result().run_id = "somebody else's run"

    def test_a_measurement_cannot_be_reassigned(self):
        measurement = Measurement(value=0.75, observations=2, measured=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            measurement.value = 1.0

    def test_a_dataset_outcome_cannot_be_reassigned(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _golden().scored = 99

    def test_an_invocation_cannot_be_reassigned(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _invocation().responded = 0

    def test_a_cost_cannot_be_reassigned(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            COST_UNKNOWN.usd = 0.0


# ---------------------------------------------------------------------------
# Zero observations is unknown, never a pass (ticket criterion 4)
# ---------------------------------------------------------------------------


class TestAMetricOverNothingIsUnknown:

    def test_zero_observations_refuses_to_claim_it_was_measured(self):
        with pytest.raises(InvalidEvalResult) as exc:
            Measurement(value=None, observations=0, measured=True)
        assert "zero observations" in str(exc.value)

    def test_zero_observations_refuses_to_carry_a_value(self):
        """The dangerous direction. A 0.0 here reads as a failing score forever."""
        with pytest.raises(InvalidEvalResult):
            Measurement(value=0.0, observations=0, measured=True)

    def test_an_unmeasured_metric_refuses_to_carry_a_value(self):
        with pytest.raises(InvalidEvalResult) as exc:
            Measurement(value=0.9, observations=4, measured=False)
        assert "nobody measured" in str(exc.value)

    def test_a_measured_metric_needs_a_number(self):
        with pytest.raises(InvalidEvalResult):
            Measurement(value=None, observations=4, measured=True)

    def test_zero_observations_and_no_value_is_a_legal_record(self):
        """Unknown is a state the record has to be able to hold, not an error."""
        measurement = Measurement(value=None, observations=0, measured=False)
        assert measurement.payload == {
            "value": None,
            "measured": False,
            "observations": 0,
        }

    def test_observations_travel_with_every_reported_number(self):
        """Section 11 of the fundamentals note: never a point estimate on its own."""
        metrics = _result().payload["datasets"]["golden"]["metrics"]
        assert all("observations" in m for m in metrics.values()), (
            "a metric reached the payload without the count it was computed "
            f"over: {metrics}"
        )


# ---------------------------------------------------------------------------
# Absent is not zero
# ---------------------------------------------------------------------------


class TestAnUnreportedMetricIsAbsent:

    def test_a_metric_nobody_reported_has_no_key_at_all(self):
        metrics = _result().payload["datasets"]["golden"]["metrics"]
        assert ABSENT_METRIC not in metrics, (
            f"{ABSENT_METRIC} was never reported and the payload invented a "
            f"default for it: {metrics.get(ABSENT_METRIC)}"
        )

    def test_a_metric_reported_over_nothing_is_present_and_unmeasured(self):
        """The other half of criterion 4. Absent OR measured=False, never a zero."""
        metrics = _result().payload["datasets"]["golden"]["metrics"]
        assert metrics[UNMEASURED_METRIC] == {
            "value": None,
            "measured": False,
            "observations": 0,
        }

    def test_a_dataset_nobody_reported_has_no_key_at_all(self):
        result = _result(datasets={"golden": _golden()})
        assert set(result.payload["datasets"]) == {"golden"}

    def test_a_metric_name_the_run_does_not_score_is_refused(self):
        with pytest.raises(InvalidEvalResult) as exc:
            DatasetOutcome(
                attempted=1,
                valid=1,
                scored=1,
                scenarios_passed=1,
                metrics={"vibes": Measurement(value=1.0, observations=1, measured=True)},
            )
        assert "vibes" in str(exc.value)

    def test_a_metric_that_is_not_a_measurement_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            DatasetOutcome(
                attempted=1,
                valid=1,
                scored=1,
                scenarios_passed=1,
                metrics={"faithfulness": 0.9},
            )


# ---------------------------------------------------------------------------
# The datasets a run reports
# ---------------------------------------------------------------------------


class TestTheDatasetNames:

    def test_a_dataset_name_outside_the_two_is_refused(self):
        """A third bucket is one no comparison covers, so it would be write-only."""
        with pytest.raises(InvalidEvalResult) as exc:
            _result(datasets={"adversarial": _golden()})
        assert "adversarial" in str(exc.value)

    def test_the_two_names_are_the_ones_the_service_uses(self):
        """`app.domain` may not import `app.services`, so the copy is pinned here."""
        from app.services.eval_service import EVAL_DATASETS as SERVICE_DATASETS

        assert EVAL_DATASETS == SERVICE_DATASETS

    def test_the_metric_names_are_the_ones_the_service_scores(self):
        from app.services.eval_service import METRIC_KEYS as SERVICE_METRICS

        assert METRIC_KEYS == SERVICE_METRICS

    def test_a_dataset_that_is_not_an_outcome_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            _result(datasets={"golden": {"attempted": 2}})

    def test_scored_cannot_exceed_valid(self):
        """A run cannot score a row it could not score."""
        with pytest.raises(InvalidEvalResult):
            DatasetOutcome(attempted=5, valid=2, scored=3)

    def test_valid_cannot_exceed_attempted(self):
        with pytest.raises(InvalidEvalResult):
            DatasetOutcome(attempted=2, valid=5, scored=1)

    def test_the_run_level_counts_are_the_sum_of_the_datasets(self):
        payload = _result().payload
        assert (payload["attempted"], payload["valid"], payload["scored"]) == (4, 4, 4)


class TestTheVerdictCountsPartitionTheScoredScenarios:
    """Three counts that add up to `scored`, stored rather than counted later.

    The deploy gate reached them with a `COUNT(*) FILTER` over `eval_results`,
    which is a second walk over one run: delete the rows and it said nought
    failing and nought undecided about a run this record says scored thirty, and
    a gate reading "nothing failed" shipped it.
    """

    def test_counts_that_do_not_add_up_to_scored_are_refused(self):
        with pytest.raises(InvalidEvalResult) as exc:
            DatasetOutcome(
                attempted=30,
                valid=30,
                scored=30,
                scenarios_passed=28,
                scenarios_failed=1,
            )
        assert "29 scenario(s) into a verdict over 30 scored" in str(exc.value)

    def test_a_verdict_over_more_scenarios_than_scored_is_refused_too(self):
        """The other direction. A verdict about a row nobody scored is invented."""
        with pytest.raises(InvalidEvalResult):
            DatasetOutcome(attempted=5, valid=5, scored=2, scenarios_passed=3)

    def test_a_count_that_is_not_a_count_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            DatasetOutcome(attempted=1, valid=1, scored=1, scenarios_passed=-1)

    def test_the_three_counts_round_trip_through_the_payload(self):
        outcome = DatasetOutcome(
            attempted=10,
            valid=9,
            scored=8,
            scenarios_passed=5,
            scenarios_failed=2,
            scenarios_unmeasured=1,
        )
        payload = outcome.payload
        assert payload["scenarios_passed"] == 5
        assert payload["scenarios_failed"] == 2
        assert payload["scenarios_unmeasured"] == 1
        assert DatasetOutcome.from_payload(payload) == outcome

    def test_a_stored_dataset_with_no_verdict_counts_is_refused_on_the_way_out(self):
        """A record from a build that did not count its own verdicts.

        It reads as unmeasured rather than as a run in which nothing failed.
        Filling the three in here would be the derivation they replace.
        """
        payload = _golden().payload
        for name in ("scenarios_passed", "scenarios_failed", "scenarios_unmeasured"):
            payload.pop(name)
        with pytest.raises(InvalidEvalResult):
            DatasetOutcome.from_payload(payload)

    def test_the_run_level_verdict_counts_add_the_datasets_up(self):
        result = _result(
            datasets={
                "golden": DatasetOutcome(2, 2, 2, scenarios_failed=2),
                "exploratory": DatasetOutcome(
                    5, 5, 4, scenarios_passed=3, scenarios_unmeasured=1
                ),
            }
        )
        assert result.scenarios_passed == 3
        assert result.scenarios_failed == 2
        assert result.scenarios_unmeasured == 1


# ---------------------------------------------------------------------------
# The invocation counters
# ---------------------------------------------------------------------------


class TestTheInvocationCounters:

    def test_the_two_statuses_are_the_ones_the_summariser_produces(self):
        from app.services.eval_service import (
            AGENT_INVOCATION_MEASURED,
            AGENT_INVOCATION_UNKNOWN,
        )

        assert {s.value for s in InvocationStatus} == {
            AGENT_INVOCATION_MEASURED,
            AGENT_INVOCATION_UNKNOWN,
        }

    def test_a_status_nobody_defined_is_refused(self):
        with pytest.raises(InvalidEvalResult) as exc:
            _invocation(status="probably_fine")
        assert "probably_fine" in str(exc.value)

    def test_the_three_outcomes_have_to_partition_the_attempted_turns(self):
        """responded + failed + empty == attempted, or a turn is counted twice."""
        with pytest.raises(InvalidEvalResult) as exc:
            _invocation(attempted=4, responded=4, failed=1, empty=0)
        assert "add up" in str(exc.value)

    def test_more_responses_than_attempts_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            _invocation(valid=9, attempted=2, responded=4, empty=0)

    def test_more_scored_than_responded_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            _invocation(scorable=9)

    def test_more_attempts_than_valid_rows_is_refused(self):
        """`valid` is what could have been invoked; the ceiling only ever cuts."""
        with pytest.raises(InvalidEvalResult):
            _invocation(valid=2, attempted=4, responded=2, empty=2)

    def test_the_deflection_counters_survive_the_payload(self):
        """#103's three fields. What the firewall substituted, beside its denominator."""
        invocation = _invocation(
            responses_deflected=2,
            scored_responses_deflected=1,
            deflection_detectors={"email": 2},
        )
        assert invocation.payload["responses_deflected"] == 2
        assert invocation.payload["scored_responses_deflected"] == 1
        assert invocation.payload["deflection_detectors"] == {"email": 2}

    def test_more_scored_deflections_than_deflections_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            _invocation(responses_deflected=1, scored_responses_deflected=2)

    def test_a_count_that_is_not_an_int_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            _invocation(responded=True)


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class TestTheCost:

    def test_no_ledger_rows_reads_as_unmeasured(self):
        """The whole point. Zero would be a claim that the run was free."""
        assert COST_UNKNOWN.measured is False
        assert COST_UNKNOWN.usd is None
        assert COST_UNKNOWN.zar is None
        assert _result().payload["cost"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "usd": None,
            "zar": None,
            "measured": False,
        }

    def test_an_unmeasured_cost_refuses_to_carry_money(self):
        with pytest.raises(InvalidEvalResult) as exc:
            Cost(input_tokens=0, output_tokens=0, usd=1.5, zar=None, measured=False)
        assert "unknown carries nothing" in str(exc.value)

    def test_an_unmeasured_cost_refuses_to_carry_tokens(self):
        with pytest.raises(InvalidEvalResult):
            Cost(input_tokens=10, output_tokens=0, usd=None, zar=None, measured=False)

    def test_known_tokens_and_a_refused_price_is_a_legal_record(self):
        """The book can refuse a model whose tokens are perfectly well known."""
        cost = Cost(input_tokens=120, output_tokens=40, usd=None, zar=None, measured=True)
        assert cost.payload["input_tokens"] == 120
        assert cost.payload["usd"] is None

    def test_negative_money_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            Cost(input_tokens=1, output_tokens=1, usd=-0.01, zar=None, measured=True)


class TestCostOfRun:
    """The arithmetic between a run's ledger rows and the figure on its record."""

    def test_no_ledger_rows_is_unknown_rather_than_free(self):
        """The ledger hook fails open, so an empty ledger is not an unbilled run."""
        assert cost_of_run([]) == COST_UNKNOWN

    def test_the_tokens_are_summed_across_the_rows(self):
        cost = cost_of_run([_call(), _call(input_tokens=50, output_tokens=5)])
        assert (cost.input_tokens, cost.output_tokens) == (150, 25)
        assert cost.measured is True

    def test_the_money_is_a_positive_figure_in_both_currencies(self):
        cost = cost_of_run([_call()])
        assert cost.usd > 0 and cost.zar > cost.usd

    def test_a_model_the_book_refuses_costs_the_run_its_whole_figure(self):
        """A partial sum understates the run while reading as a whole figure."""
        cost = cost_of_run([_call(), _call(served_model="some-model-nobody-priced")])
        assert cost.usd is None and cost.zar is None
        assert cost.input_tokens == 200, (
            "the tokens are a fact the provider reported and they survive a "
            "price the book cannot supply"
        )
        assert cost.measured is True, (
            "the run found its ledger rows. measured=False would say it did not"
        )

    def test_a_call_before_the_fx_table_loses_the_rand_and_keeps_the_dollars(self):
        """The two currencies fail separately; one gap never hides the other."""
        cost = cost_of_run([_call(at=datetime(2020, 1, 1, tzinfo=timezone.utc))])
        assert cost.usd is not None
        assert cost.zar is None


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


class TestThePayloadRoundTrips:

    def test_from_payload_of_payload_is_the_original(self):
        """The contract slice 3's reader is built on."""
        result = _result()
        assert EvalResult.from_payload(result.payload) == result

    def test_the_round_trip_survives_json(self):
        """`eval_runs.result` is jsonb, so the payload goes through a serialiser."""
        import json

        result = _result()
        assert EvalResult.from_payload(json.loads(json.dumps(result.payload))) == result

    def test_the_round_trip_keeps_a_metric_absent(self):
        rebuilt = EvalResult.from_payload(_result().payload)
        assert ABSENT_METRIC not in rebuilt.datasets["golden"].metrics

    def test_a_stored_payload_that_breaks_a_rule_is_refused_on_the_way_out(self):
        """Already being written down is not evidence that a shape is honest."""
        payload = _result().payload
        payload["datasets"]["golden"]["metrics"][UNMEASURED_METRIC]["measured"] = True
        with pytest.raises(InvalidEvalResult):
            EvalResult.from_payload(payload)

    def test_the_payload_keys_are_the_ones_the_docstring_enumerates(self):
        assert set(_result().payload) == {
            "run_id",
            "agent_id",
            "prompt_version_id",
            "judge_identity",
            "requested_model",
            "served_model",
            "invocation",
            "datasets",
            "attempted",
            "valid",
            "scored",
            "cost",
            "failures",
            "context_proxy_version",
            "rule_version",
        }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestTheIdentity:

    def test_the_proxy_version_is_stamped_by_default(self):
        """#84: two runs on different proxies are not comparable, and this says so."""
        assert _result().payload["context_proxy_version"] == CONTEXT_PROXY_VERSION

    def test_the_rule_version_is_stamped(self):
        assert _result().payload["rule_version"] == 1

    def test_the_requested_and_served_models_are_separate_claims(self):
        payload = _result().payload
        assert payload["requested_model"] == "gpt-5.6-luna"
        assert payload["served_model"] == "gpt-5.6-luna-2026-08"

    def test_an_unknown_served_model_is_none_rather_than_the_requested_one(self):
        """No provider stated what ran, so nothing here may claim one did."""
        assert _result(served_model=None).payload["served_model"] is None

    def test_an_empty_id_is_refused(self):
        with pytest.raises(InvalidEvalResult):
            _result(run_id="  ")

    def test_an_absent_prompt_version_is_a_legal_record(self):
        """An agent with no production version still runs, off its live soul columns."""
        assert _result(prompt_version_id=None).payload["prompt_version_id"] is None

    def test_an_empty_prompt_version_is_refused(self):
        """None says there is no version. An empty string says there is one and hides it."""
        with pytest.raises(InvalidEvalResult):
            _result(prompt_version_id="")

    def test_the_judge_identity_survives_the_round_trip(self):
        rebuilt = EvalResult.from_payload(_result().payload)
        assert rebuilt.judge_identity == _identity()

    def test_an_unknown_judge_is_none(self):
        """A verdict whose Judge cannot be named is filed under no Judge at all."""
        assert _result(judge_identity=None).payload["judge_identity"] is None


# ---------------------------------------------------------------------------
# #25: a failed turn carries its class AND what happened to it
# ---------------------------------------------------------------------------

#: An exception whose own text is unmistakable. Every assertion about the
#: message rule looks for this string and expects not to find it.
SENTINEL = "SENTINEL-customer-said-my-card-is-4111111111111111"


class _LoudFailure(RuntimeError):
    """`str()` returns something no owner-visible row may ever carry."""

    def __str__(self) -> str:
        return SENTINEL


def _failure(**overrides) -> ScenarioFailure:
    fields = {
        "scenario_id": "11111111-1111-1111-1111-111111111111",
        "error_type": "TimeoutError",
        "message": "agent turn exceeded 90s",
    }
    fields.update(overrides)
    return ScenarioFailure(**fields)


class TestAFailureNamesItsTurn:
    """#25. Eval run 29754ceb logged `error= error_type=TimeoutError` twice.

    `str(TimeoutError())` is the empty string, so the type was the only thing
    that survived the 90s per-turn bound and the record said nothing about what
    ran out of what.
    """

    def test_the_record_carries_one_failure_per_failed_turn(self):
        result = _result(
            invocation=_invocation(responded=3, scorable=3, failed=1),
            failures=[_failure()],
        )
        assert [f.error_type for f in result.failures] == ["TimeoutError"]
        assert result.failures[0].message == "agent turn exceeded 90s"

    def test_a_run_with_no_failures_carries_an_empty_tuple(self):
        """Absent, not a placeholder entry saying nothing went wrong."""
        assert _result().failures == ()
        assert _result().payload["failures"] == []

    def test_the_failures_are_frozen_with_the_record(self):
        result = _result(
            invocation=_invocation(responded=3, scorable=3, failed=1),
            failures=[_failure()],
        )
        assert isinstance(result.failures, tuple)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.failures[0].message = "something else"

    def test_more_failures_than_failed_turns_is_refused(self):
        """The counters and the list come off one walk, so they cannot disagree.

        A record holding two failures over one failed turn is describing a turn
        that did not happen, and `failed` is the denominator every rate on this
        record is built from.
        """
        with pytest.raises(InvalidEvalResult, match="failure"):
            _result(
                invocation=_invocation(responded=3, scorable=3, failed=1),
                failures=[_failure(), _failure(scenario_id="s2")],
            )

    def test_a_failure_that_is_not_a_scenario_failure_is_refused(self):
        with pytest.raises(InvalidEvalResult, match="ScenarioFailure"):
            _result(
                invocation=_invocation(responded=3, scorable=3, failed=1),
                failures=[{"scenario_id": "s1", "error_type": "T", "message": "m"}],
            )

    def test_an_empty_error_type_or_message_is_refused(self):
        """A blank message is a failure the record claims to describe and does not."""
        with pytest.raises(InvalidEvalResult):
            _failure(error_type="  ")
        with pytest.raises(InvalidEvalResult):
            _failure(message="")

    def test_a_row_that_carried_no_id_keeps_the_empty_string(self):
        """The invoker reads `scenario.get("id", "")`, so an idless row is a
        real state. Carried through rather than replaced with an invented id."""
        assert _failure(scenario_id="").scenario_id == ""

    def test_the_failures_round_trip_through_json(self):
        import json

        result = _result(
            invocation=_invocation(responded=2, scorable=2, failed=2),
            failures=[_failure(), _failure(scenario_id="s2", error_type="ValueError",
                                           message="ValueError")],
        )
        rebuilt = EvalResult.from_payload(json.loads(json.dumps(result.payload)))
        assert rebuilt == result
        assert [f.message for f in rebuilt.failures] == [
            "agent turn exceeded 90s",
            "ValueError",
        ]

    def test_a_payload_written_before_this_field_reads_as_no_detail(self):
        """Slice 1 wrote records with no `failures` key. They still read, and
        they read as "this build recorded no failure detail", never as a claim
        that the run lost no turns: `invocation.failed` still says it did."""
        payload = _result(
            invocation=_invocation(responded=3, scorable=3, failed=1)
        ).payload
        del payload["failures"]

        rebuilt = EvalResult.from_payload(payload)
        assert rebuilt.failures == ()
        assert rebuilt.invocation.failed == 1

    def test_a_stored_failure_with_a_blank_message_is_refused_on_the_way_out(self):
        """Already being written down is not evidence that a shape is honest."""
        payload = _result(
            invocation=_invocation(responded=3, scorable=3, failed=1),
            failures=[_failure()],
        ).payload
        payload["failures"][0]["message"] = ""

        with pytest.raises(InvalidEvalResult):
            EvalResult.from_payload(payload)

    def test_the_exceptions_own_text_never_reaches_the_payload(self):
        """THE ANTI-TAUTOLOGY HALF, and the reason SENTINEL exists.

        Asserting `message == "agent turn exceeded 90s"` passes just as well
        when a build appends `str(exc)` to it. This looks for the exception's
        own words anywhere in the serialised record, which is the shape
        `eval_runs.result` is stored and read back in.
        """
        import json

        result = _result(
            invocation=_invocation(responded=3, scorable=3, failed=1),
            failures=[
                ScenarioFailure(
                    scenario_id="s1",
                    error_type=type(_LoudFailure()).__name__,
                    message="_LoudFailure",
                )
            ],
        )
        stored = json.dumps(result.payload)

        assert "_LoudFailure" in stored, "the failure's class never reached the row"
        assert SENTINEL not in stored, (
            "the exception's own text reached eval_runs.result, which is jsonb "
            "the owner reads back (#96)"
        )
