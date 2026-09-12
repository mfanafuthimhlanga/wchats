"""Every authored golden file under tests/evals/golden_pairs registers as it is.

A file the owner registers over MCP is checked here first, by the same schema
the route validates and the same rule the writer applies to an ambiguous pair's
reference (#226). A file that fails here would fail at registration, on staging,
with the services unparked for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.eval import GoldenScenariosRegisterRequest
from app.services.clarifying_check import is_clarifying_question

GOLDEN_PAIRS_DIR = Path(__file__).resolve().parents[1] / "evals" / "golden_pairs"
FILES = sorted(GOLDEN_PAIRS_DIR.glob("*.json"))


def test_there_is_at_least_one_file_to_check():
    assert FILES, f"no golden pair files under {GOLDEN_PAIRS_DIR}"


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_the_file_is_a_valid_registration_request(path):
    request = GoldenScenariosRegisterRequest(**json.loads(path.read_text(encoding="utf-8")))
    assert request.source_file == path.name


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_every_ambiguous_reference_is_itself_a_clarifying_question(path):
    request = GoldenScenariosRegisterRequest(**json.loads(path.read_text(encoding="utf-8")))
    failing = [p.reference_answer for p in request.pairs if p.ambiguous and not is_clarifying_question(p.reference_answer)]
    assert failing == []


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_a_follow_up_names_its_binding_in_the_turns_and_an_opener_has_none(path):
    """The shape #227 PR 4 needs: follow-ups carry a conversation, openers do not."""
    request = GoldenScenariosRegisterRequest(**json.loads(path.read_text(encoding="utf-8")))
    for pair in request.pairs:
        if pair.ambiguous:
            assert pair.turns == [], pair.question
        else:
            assert pair.turns, pair.question
            assert pair.turns[0].role == "user"
