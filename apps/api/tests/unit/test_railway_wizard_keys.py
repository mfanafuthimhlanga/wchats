"""The wizard writes settings under the names `Settings` actually declares.

`railway_staging_wizard.sh` captures values on one screen and prints them back
several screens later as the variables the operator pastes into Railway. The
name is the only thing joining the two, so a wizard-local alias splits them:
the stage collecting the R2 endpoint wrote `R2_ENDPOINT_HOST` while the setting
that reads it is `S3_EXPECTED_ENDPOINT_HOST`, and the summary screen then
interpolated the alias to build the real name. It worked by coincidence, and a
rename on either side would have printed an empty value with no error anywhere.

A shell script has no import to typo-check, so this is a text test. It is cheap
and it holds the one property that matters: a key the wizard persists is either
a field on `Settings` or a value the wizard keeps for itself, and the second set
is written down here by name.
"""

from __future__ import annotations

import pathlib
import re

from app.core.config import Settings

WIZARD = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "railway_staging_wizard.sh"
)

#: Keys the wizard persists that are NOT settings: facts about the Railway
#: project, captured so a re-run can offer them back. Anything not listed here
#: has to be a real `Settings` field.
WIZARD_LOCAL_KEYS = {
    # The api service's generated public URL. Railway assigns it, the wizard
    # curls /health on it, and PUBLIC_API_BASE is what the operator sets from it.
    "STAGING_API_BASE",
}


def _written_keys() -> list[str]:
    """Every KEY in a `write_env KEY ...` call, in file order.

    The helper definition is skipped by requiring a bare word after the command:
    the definition line reads `write_env() {`.
    """
    text = WIZARD.read_text(encoding="utf-8")
    return re.findall(r"^\s*write_env\s+([A-Z][A-Z0-9_]*)\s", text, flags=re.MULTILINE)


def test_every_persisted_key_is_a_settings_field_or_a_named_local():
    fields = set(Settings.model_fields)
    unknown = [
        key
        for key in _written_keys()
        if key not in fields and key not in WIZARD_LOCAL_KEYS
    ]
    assert not unknown, (
        f"the wizard writes {unknown!r}, which is neither a Settings field nor "
        f"listed in WIZARD_LOCAL_KEYS. A key that is meant to reach Railway has "
        f"to be spelled the way Settings declares it, or nothing reads it back."
    )


def test_the_endpoint_host_is_persisted_under_the_settings_name():
    """The specific alias, pinned by name so it cannot come back."""
    assert "S3_EXPECTED_ENDPOINT_HOST" in _written_keys(), (
        "the R2 stage no longer persists the endpoint host under the name the "
        "API reads it by"
    )
    assert "S3_EXPECTED_ENDPOINT_HOST" in Settings.model_fields
    assert "R2_ENDPOINT_HOST" not in WIZARD.read_text(encoding="utf-8"), (
        "R2_ENDPOINT_HOST was the alias; the summary screen interpolated it to "
        "build the real variable name, which is how the mismatch stayed invisible"
    )


def test_a_refused_host_is_never_written():
    """The old stage warned about a pasted URL and wrote it anyway, and `ask`
    offers a written value back as the default, so the next run handed the
    operator the same paste the API refuses to boot on.

    Reading the shape out of the text: the write is guarded by a test on the
    captured value, and every shape config.py refuses has a branch here.
    """
    text = WIZARD.read_text(encoding="utf-8")
    stage = text[text.index("R2 bucket and S3 credentials") :]
    stage = stage[: stage.index("Shared variables on Railway")]

    assert 'if [[ -n "$S3_EXPECTED_ENDPOINT_HOST" ]]; then' in stage, (
        "the write must be guarded on a value having survived the checks"
    )
    for shape in ("*://*|*/*|*@*", "*:*", "*[[:space:]]*"):
        assert shape in stage, (
            f"the wizard accepts {shape!r}, which config.py's "
            f"_expected_endpoint_host_is_a_bare_host refuses at boot"
        )
