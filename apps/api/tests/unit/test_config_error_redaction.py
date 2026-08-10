"""A settings validation failure must not print the values it was given.

`Settings.__repr__` has suppressed field values since T-01-01, but it only ever
runs on an instance that exists. A `pydantic_core.ValidationError` is raised
*instead of* constructing one, and pydantic's default error rendering includes
`input_value=`. For a `BaseSettings` model the "input" is the entire assembled
settings dict — every environment variable and every `.env` line at once — so a
single missing or malformed field prints truncated fragments of every real
secret beside it, into stderr, CI logs, and any Celery task traceback.

Observed on 2026-08-11, before the fix, while probing environment precedence::

    pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
    PLATFORM_CREDENTIAL_KEY
      Field required [type=missing, input_value={'NEON_API_KEY': 'stub-ke...<tail of a real key>'}]

The guard is `hide_input_in_errors=True` in `Settings.model_config`.

Why a probe model rather than `Settings()` itself
-------------------------------------------------
Instantiating `Settings` here would read the developer's real `.env`, so the
test would either pass because the machine happens to be configured or fail for
an unrelated reason — and it would put real secret material into the assertion's
failure message, which is the defect it is testing for. The probe below inherits
`Settings.model_config` verbatim (minus the env file, so nothing real is read),
which is what makes it a test of the shipped configuration rather than of a
hand-written copy of it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings

#: Stand-in for a credential. Distinctive enough that a substring check cannot
#: pass by accident, and it is not a real key.
_FAKE_SECRET = "sk-canary-2f8a1c-never-a-real-key"


def _probe_config() -> SettingsConfigDict:
    """`Settings`' own config, with the env file detached.

    Reading the real `.env` would make this test depend on the machine and would
    drag genuine secrets into a failure message.
    """
    config = dict(Settings.model_config)
    config["env_file"] = None
    return SettingsConfigDict(**config)  # type: ignore[typeddict-item]


class _Probe(BaseSettings):
    model_config = _probe_config()

    A_CREDENTIAL: str
    A_REQUIRED_FIELD: str


def test_the_shipped_settings_config_hides_input_in_errors():
    """The flag itself, read off `Settings`, not off a copy."""
    assert Settings.model_config.get("hide_input_in_errors") is True, (
        "Settings.model_config must set hide_input_in_errors=True. Without it a "
        "ValidationError reprs the whole settings input dict, which for a "
        "BaseSettings model is every secret the process was given."
    )


def test_a_missing_field_does_not_echo_the_other_values():
    """The behaviour the flag buys, exercised rather than asserted."""
    with pytest.raises(ValidationError) as exc_info:
        _Probe(A_CREDENTIAL=_FAKE_SECRET)  # type: ignore[call-arg]

    rendered = str(exc_info.value)

    assert "A_REQUIRED_FIELD" in rendered, (
        "the error must still name the field that is missing — redaction may "
        "not cost the diagnostic"
    )
    assert _FAKE_SECRET not in rendered, (
        "the credential handed to Settings appeared in the ValidationError text. "
        "That text reaches stderr, CI logs and Celery tracebacks.\n"
        f"rendered error:\n{rendered}"
    )
    assert "input_value" not in rendered, (
        "pydantic is still rendering `input_value=`; for a BaseSettings model "
        "that value is the assembled settings dict.\n"
        f"rendered error:\n{rendered}"
    )


def test_the_redaction_also_covers_a_wrong_type_not_only_a_missing_field():
    """`missing` is one error type; a coercion failure carries the input too."""

    class _TypedProbe(BaseSettings):
        model_config = _probe_config()

        A_CREDENTIAL: str
        A_NUMBER: int

    with pytest.raises(ValidationError) as exc_info:
        _TypedProbe(A_CREDENTIAL=_FAKE_SECRET, A_NUMBER="not-an-int")  # type: ignore[arg-type]

    rendered = str(exc_info.value)
    assert _FAKE_SECRET not in rendered, rendered
    assert "not-an-int" not in rendered, rendered
