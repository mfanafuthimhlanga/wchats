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


#: Names the wizard PRINTS that are real environment variables but not settings:
#: the platform or a library reads each one, and `Settings` never sees it.
NON_SETTING_VARIABLES = {
    # The dynamic loader's path, needed by the docling image's native libraries.
    "LD_LIBRARY_PATH",
    # huggingface_hub's switch for the Xet transfer endpoint.
    "HF_HUB_DISABLE_XET_ENDPOINT",
}

#: Any all-caps token carrying an underscore, which is what an environment
#: variable looks like and what an English word in this file does not.
_VARIABLE_SHAPED = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

#: The four helpers that put a line on the operator's screen.
_OPERATOR_LINE = re.compile(r"^\s*(?:note|say|step|warn)\s+(.*)$", re.MULTILINE)

#: A shell expansion. The shell replaces it with a value before the operator
#: sees anything, so the name inside is the script's own, not one on screen.
_EXPANSION = re.compile(r"\$\{[^}]*\}?|\$[A-Za-z_][A-Za-z0-9_]*")


def _printed_variable_names() -> set[str]:
    """Every variable-shaped name the wizard shows the operator.

    `write_env` is not the only way a name reaches Railway. The shared-variables
    stage prints a list the operator types in by hand, and nothing in the script
    ever reads those names back, so a wrong one is invisible until an upload
    fails. Shell locals are excluded by reading only the four print helpers.
    """
    text = WIZARD.read_text(encoding="utf-8")
    names: set[str] = set()
    for line in _OPERATOR_LINE.findall(text):
        names.update(_VARIABLE_SHAPED.findall(_EXPANSION.sub(" ", line)))
    return names


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


def test_every_variable_the_wizard_shows_is_one_the_app_reads():
    """A name printed on screen is a name the operator types into Railway.

    `#170`. The wizard told the operator to set the vendor-prefixed credential
    pair while `storage_service._require_credentials` reads `S3_ACCESS_KEY_ID`
    and `S3_SECRET_ACCESS_KEY` and refuses by name when they are unset. Nothing
    in the wizard reads a printed name back, so the mismatch survived until the
    first upload returned `StorageNotConfigured`.
    """
    allowed = set(Settings.model_fields) | WIZARD_LOCAL_KEYS | NON_SETTING_VARIABLES
    unknown = sorted(name for name in _printed_variable_names() if name not in allowed)
    assert not unknown, (
        f"the wizard shows the operator {unknown!r}, which the app never reads. "
        f"A variable name it prints has to be a Settings field, or listed in "
        f"NON_SETTING_VARIABLES with the reader named."
    )


def test_the_storage_credentials_are_named_the_way_the_uploader_reads_them():
    """`#170`, pinned by name so the vendor spelling cannot come back."""
    printed = _printed_variable_names()
    assert {"S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"} <= printed, (
        "the R2 token pair is what the operator has to set, under the two names "
        "storage_service reads"
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
