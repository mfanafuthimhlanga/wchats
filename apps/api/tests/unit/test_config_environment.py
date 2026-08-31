"""ENVIRONMENT refuses unknown words at boot.

Four fail-open guards key off the exact string "production" (the storage
endpoint allowlist, /docs and /redoc, the snippet loopback refusal, the
token-error redaction). Before the validator, ENVIRONMENT=Production was a
production process with every one of them silently off.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("word", ["development", "test", "staging", "production"])
def test_the_known_words_boot(word):
    assert Settings(ENVIRONMENT=word).ENVIRONMENT == word


@pytest.mark.parametrize("typo", ["Production", "prod", "production ", ""])
def test_a_typo_refuses_to_boot_rather_than_failing_open(typo):
    with pytest.raises(ValidationError) as exc_info:
        Settings(ENVIRONMENT=typo)
    assert "ENVIRONMENT" in str(exc_info.value)
