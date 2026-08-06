"""Regression guard for the integration Celery-worker working directory.

`tests/integration/conftest.py` spawned its worker subprocess with
`cwd="/c/Users/Bantu/mzansi-agentive/veridian/apps/api"` — one developer's machine,
under the project's former name. Every other machine, GitHub Actions included, got
`FileNotFoundError` at fixture setup, so the CI integration job never reached a single
integration test.

These tests are unit tests on purpose: they must run in the default gate, where no
Postgres, Redis or Celery broker exists. They import `tests.integration._paths`
(side-effect free) and parse `conftest.py` as source with `ast` — neither imports
`tests.integration.conftest`, whose module body mutates os.environ.
"""

import ast
from pathlib import Path

import pytest

from tests.integration._paths import api_root

_CONFTEST_PATH = Path(__file__).resolve().parents[1] / "integration" / "conftest.py"


def test_api_root_points_at_the_directory_holding_the_app_package():
    """The derived path must be a real directory a celery worker could start in."""
    root = api_root()

    assert root.is_dir(), f"{root} is not a directory"
    assert (root / "pyproject.toml").is_file(), f"no pyproject.toml under {root}"
    # This is the exact module the fixture passes to `celery -A`.
    assert (root / "app" / "worker" / "celery_app.py").is_file(), (
        f"app.worker.celery_app is not importable from {root}"
    )


def test_api_root_is_independent_of_the_current_working_directory(monkeypatch, tmp_path):
    """Resolution must come from __file__, not os.getcwd().

    The whole failure mode being guarded is a path that is only correct when the
    process happens to start somewhere particular.
    """
    expected = api_root()

    monkeypatch.chdir(tmp_path)

    assert api_root() == expected
    assert (api_root() / "app" / "worker" / "celery_app.py").is_file()


def _popen_cwd_arguments(source: str) -> list[ast.expr]:
    """Every `cwd=` keyword argument passed to any call in the given source."""
    return [
        keyword.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "cwd"
    ]


def test_integration_conftest_never_hardcodes_a_subprocess_cwd():
    """A `cwd=` given as a string literal is the bug, whatever the literal says.

    An absolute literal is one machine's layout; a relative literal ("." , "apps/api")
    depends on where pytest was invoked. Both are the same defect, so this asserts the
    argument is a computed expression rather than trying to blocklist known-bad strings.
    """
    cwd_arguments = _popen_cwd_arguments(_CONFTEST_PATH.read_text(encoding="utf-8"))

    assert cwd_arguments, (
        f"no subprocess `cwd=` argument found in {_CONFTEST_PATH} — this guard has "
        "lost its subject and must be re-pointed or deleted, not left passing vacuously"
    )
    for argument in cwd_arguments:
        assert not isinstance(argument, ast.Constant), (
            f"{_CONFTEST_PATH.name}:{argument.lineno} passes a hardcoded cwd "
            f"({argument.value!r}); derive it from tests.integration._paths.api_root()"
        )


def test_the_cwd_guard_can_actually_fail():
    """Proves the ast walk detects the exact regression it exists to catch."""
    hardcoded = 'subprocess.Popen(["celery"], cwd="/c/Users/Bantu/veridian/apps/api")'

    arguments = _popen_cwd_arguments(hardcoded)

    assert len(arguments) == 1
    assert isinstance(arguments[0], ast.Constant)

    with pytest.raises(AssertionError):
        for argument in arguments:
            assert not isinstance(argument, ast.Constant)
