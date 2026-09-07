"""Each judge purpose may carry its own OpenAI key, falling back to the shared one (#213).

The judge path is rate-bound on one key. A key per purpose spreads the four
judges' calls across four rates. Nothing here opens a socket: the credentials
are resolved and read, never used.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.model_client import (
    OPENAI_PROVIDER,
    PURPOSE_KEY_SETTINGS,
    judge_key_spread,
    resolve_credentials,
)
from app.services.eval_service import JUDGE_PURPOSES


@pytest.fixture(autouse=True)
def _shared_key_only(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "shared-key")
    for field in PURPOSE_KEY_SETTINGS.values():
        monkeypatch.setattr(settings, field, "")


class TestEachJudgeReadsItsOwnKey:
    def test_the_table_names_every_judge_purpose_and_nothing_else(self):
        assert set(PURPOSE_KEY_SETTINGS) == set(JUDGE_PURPOSES)
        for purpose, field in PURPOSE_KEY_SETTINGS.items():
            assert field == "OPENAI_API_KEY_" + purpose.upper()
            assert hasattr(settings, field), field

    def test_a_judge_with_its_own_key_set_uses_it(self, monkeypatch):
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_CONTEXT_PRECISION", "precision-key")

        assert resolve_credentials(OPENAI_PROVIDER, "judge_context_precision").api_key == "precision-key"

    def test_a_judge_without_its_own_key_falls_back_to_the_shared_one(self):
        assert resolve_credentials(OPENAI_PROVIDER, "judge_context_precision").api_key == "shared-key"

    def test_a_purpose_that_is_not_a_judge_reads_the_shared_key_whatever_is_set(self, monkeypatch):
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_FAITHFULNESS", "faithfulness-key")

        assert resolve_credentials(OPENAI_PROVIDER, "scenario_generation").api_key == "shared-key"
        assert resolve_credentials(OPENAI_PROVIDER).api_key == "shared-key"

    def test_the_anthropic_branch_is_untouched(self, monkeypatch):
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "anthropic-key")
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_FAITHFULNESS", "faithfulness-key")

        assert resolve_credentials("anthropic", "judge_faithfulness").api_key == "anthropic-key"


class TestTheRunSaysHowManyKeysItSpreadsOver:
    def test_no_judge_key_set_is_one(self):
        assert judge_key_spread() == 1

    def test_three_judge_keys_set_is_four(self, monkeypatch):
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_FAITHFULNESS", "f")
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_ANSWER_RELEVANCY", "r")
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_CONTEXT_PRECISION", "p")

        assert judge_key_spread() == 4

    def test_two_judges_on_the_same_key_count_once(self, monkeypatch):
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_FAITHFULNESS", "same")
        monkeypatch.setattr(settings, "OPENAI_API_KEY_JUDGE_ANSWER_RELEVANCY", "same")

        assert judge_key_spread() == 2
