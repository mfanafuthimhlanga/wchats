"""`.env.example` must cover every setting that has no default.

E2E-0 of the validation plan (`.dev/PRODUCTION-READINESS.md` §4), backlog row
`1.21 · env-example-cannot-boot`. `Settings` declares fields with no default;
each one is a hard boot requirement, because pydantic raises `ValidationError`
at import of `app.core.config` when it is absent. On 2026-08-12 five of them
were missing from the example, so a fresh checkout could not start the app and
nothing in the repo said which keys were needed.

Why the required set is derived, never listed
---------------------------------------------
A hand-maintained list of required keys is the same class of defect as `1.14`'s
truncated bindparam: it reads correctly on the page and drifts silently the
first time somebody adds a field. The set below comes from
`Settings.model_fields[...].is_required()` at test time, so a new no-default
field fails this test on the commit that adds it.

Why a commented key does not count as coverage
----------------------------------------------
`# ANTHROPIC_API_KEY=` documents a name; it does not boot anything, and
`python-dotenv` will not load it. A developer copying `.env.example` to `.env`
gets a `ValidationError` naming a field they can see is "in" the example, which
is worse than an honest absence.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

#: `apps/api/` — this file is at `apps/api/tests/unit/`.
API_ROOT = Path(__file__).resolve().parents[2]

#: `_find_env_file()` walks up from `app/core/config.py` and returns the
#: outermost `.env`, so the repo-root file is what `Settings` loads when one
#: exists and `apps/api/.env` only loads in a checkout without one. Both
#: examples therefore have to boot the API: this one is checked here, the
#: repo-root one by `test_the_repo_root_example_also_covers_them` below.
API_ENV_EXAMPLE = API_ROOT / ".env.example"
REPO_ROOT_ENV_EXAMPLE = API_ROOT.parents[1] / ".env.example"

#: `KEY=` at the start of a line, optionally exported. Captures the name only —
#: this module never reads a value out of an env file, deliberately, so that a
#: failure message can never carry credential material (the defect
#: `test_config_error_redaction.py` exists for).
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")
_COMMENTED_ASSIGNMENT = re.compile(r"^\s*#\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")


def required_settings_fields() -> set[str]:
    """Every `Settings` field with no default — the hard boot contract."""
    return {
        name
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }


def _keys_in(path: Path) -> tuple[set[str], set[str]]:
    """(uncommented keys, commented-out keys) declared in an env file."""
    text = path.read_text(encoding="utf-8")
    live: set[str] = set()
    commented: set[str] = set()
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            live.add(match.group(1))
            continue
        match = _COMMENTED_ASSIGNMENT.match(line)
        if match:
            commented.add(match.group(1))
    return live, commented


def _coverage_failure(path: Path, missing: set[str], commented: set[str]) -> str:
    lines = [
        f"{path} does not cover every required setting.",
        "",
        "Missing (no default in Settings, absent from the example — a fresh",
        "checkout cannot boot without these):",
    ]
    lines += [f"  - {name}" for name in sorted(missing)]
    commented_but_required = missing & commented
    if commented_but_required:
        lines += [
            "",
            "Present but COMMENTED OUT, which is not coverage — dotenv will not",
            "load these and the app still fails at import:",
        ]
        lines += [f"  - {name}" for name in sorted(commented_but_required)]
    lines += [
        "",
        "Add each key with a placeholder that is obviously not a credential.",
        "Do not add it commented out. See .dev/plans/260812-e2e0-boot-contract.md.",
    ]
    return "\n".join(lines)


def test_the_api_example_exists():
    """A skip here would make every assertion below unobserved."""
    assert API_ENV_EXAMPLE.is_file(), (
        f"{API_ENV_EXAMPLE} is missing. It is tracked in git, so this means the "
        "path in this test is wrong, not that the file was never written."
    )


def test_settings_still_has_required_fields():
    """Guards the guard: if every field gained a default, the test below would
    pass vacuously over an empty set and nobody would notice."""
    required = required_settings_fields()
    assert required, (
        "Settings now declares no required fields at all. Either that is a real "
        "and deliberate change — in which case delete this module — or "
        "`is_required()` no longer means what this test assumes."
    )


def test_the_api_example_covers_every_required_setting():
    """The contract E2E-0 exists to establish."""
    required = required_settings_fields()
    live, commented = _keys_in(API_ENV_EXAMPLE)
    missing = required - live

    assert not missing, _coverage_failure(API_ENV_EXAMPLE, missing, commented)


def test_the_repo_root_example_also_covers_them():
    """The repo-root example is what a monorepo checkout copies first.

    `Settings` loads the repo-root `.env` ahead of any copy under `apps/api/`,
    and this example is the file the README points a new developer at, so an
    example that cannot boot the API is the same defect one directory up.
    """
    if not REPO_ROOT_ENV_EXAMPLE.is_file():
        # Not a skip: state the absence as the assertion, so it cannot decay
        # into an unobserved pass the way a permanently-skipped test does.
        raise AssertionError(
            f"{REPO_ROOT_ENV_EXAMPLE} is missing. It was tracked in git as of "
            "2026-08-12; if it was deliberately deleted, delete this test with it."
        )

    required = required_settings_fields()
    live, commented = _keys_in(REPO_ROOT_ENV_EXAMPLE)
    missing = required - live

    assert not missing, _coverage_failure(REPO_ROOT_ENV_EXAMPLE, missing, commented)
