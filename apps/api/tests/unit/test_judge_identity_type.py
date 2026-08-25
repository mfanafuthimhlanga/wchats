"""Unit tests for app.domain.judge_identity (ticket #47, decision #34 on map #4).

WHAT THIS TYPE IS FOR
    Calibration keys on the identity of the Judge that produced a verdict, not on
    the word "judge". Three things move a Judge's scores: the model, the reasoning
    effort it ran at, and the prompt it was given. `JudgeIdentity` carries all
    three together so a calibration figure can name the Judge it was measured
    against, and so a later run on a different effort cannot be compared to it by
    accident. #53 reads these; this slice only builds the type.

WHY CONSTRUCTION IS LOUD
    An empty field would silently widen the key. Two runs on different prompts
    would share an identity, and a calibration figure would be read as covering a
    Judge it never saw. Each field is refused here rather than at whichever reader
    notices first, the same choice `ModelCall` made.

WHY reasoning_effort IS A STRING AND NOT AN ENUM
    It is the literal the routing table carries and the literal that goes on the
    wire, so the two cannot drift. Pinning it to one provider's set of efforts
    would make this domain type refuse a Judge on any other provider, which is a
    narrower rule than the identity needs.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.domain.judge_identity import InvalidJudgeIdentity, JudgeIdentity


def _identity(**overrides) -> JudgeIdentity:
    fields = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "prompt_version": "grounding_fidelity-v1",
    }
    fields.update(overrides)
    return JudgeIdentity(**fields)


class TestConstruction:
    def test_the_three_fields_are_held_as_given(self):
        identity = _identity()

        assert identity.model == "gpt-5.6-luna"
        assert identity.reasoning_effort == "none"
        assert identity.prompt_version == "grounding_fidelity-v1"

    def test_the_effort_none_is_the_string_and_not_a_missing_value(self):
        """`none` is a reasoning effort OpenAI serves, so it must survive as text."""
        assert _identity(reasoning_effort="none").reasoning_effort == "none"

    def test_the_identity_is_frozen(self):
        identity = _identity()

        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.model = "gpt-5-mini"  # type: ignore[misc]

    def test_two_identities_with_the_same_fields_are_equal(self):
        """Calibration groups verdicts by identity, so equality is the grouping key."""
        assert _identity() == _identity()

    def test_a_different_effort_is_a_different_identity(self):
        """The floor holds only at effort none, so effort may never collapse away."""
        assert _identity() != _identity(reasoning_effort="low")

    def test_a_different_prompt_version_is_a_different_identity(self):
        assert _identity() != _identity(prompt_version="grounding_fidelity-v2")

    def test_an_identity_is_hashable_so_verdicts_group_by_it(self):
        assert len({_identity(), _identity(), _identity(reasoning_effort="low")}) == 2


class TestRefusedIdentities:
    @pytest.mark.parametrize(
        "field", ["model", "reasoning_effort", "prompt_version"]
    )
    @pytest.mark.parametrize("bad", ["", "   ", None, 3])
    def test_every_field_needs_a_non_empty_string(self, field, bad):
        with pytest.raises(InvalidJudgeIdentity):
            _identity(**{field: bad})

    def test_the_message_names_the_field_that_was_wrong(self):
        with pytest.raises(InvalidJudgeIdentity, match="prompt_version"):
            _identity(prompt_version="")

    def test_the_error_is_a_value_error(self):
        """Callers already catching ValueError keep catching it, as InvalidModelCall does."""
        assert issubclass(InvalidJudgeIdentity, ValueError)
