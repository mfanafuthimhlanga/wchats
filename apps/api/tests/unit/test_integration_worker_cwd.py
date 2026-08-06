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

One of them spawns a bare interpreter. That is not an integration test sneaking in:
the child imports the same side-effect-free `_paths` module and touches nothing but
the filesystem. It is there because the defect being guarded is an *import-time*
capture of the working directory, and no amount of chdir-ing inside an already-imported
process can observe that.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration._paths import api_root

# Derived here from *this* file, independently of tests.integration._paths, so the
# two derivations can be compared without one vouching for the other.
_API_ROOT_BY_THIS_TEST_FILE = Path(__file__).resolve().parents[2]
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


def test_api_root_does_not_re_read_the_cwd_on_every_call(monkeypatch, tmp_path):
    """A `Path.cwd()` *inside* `api_root()` — and nothing more than that.

    This is deliberately the weaker of the two cwd guards. `_API_ROOT` is a
    module-level constant already resolved at import time, so chdir-ing here happens
    strictly after the only interesting moment. It cannot see an import-time capture;
    `test_api_root_survives_an_interpreter_started_in_a_foreign_directory` below is
    what covers that, and it is the one that matches the original defect.
    """
    expected = api_root()

    monkeypatch.chdir(tmp_path)

    assert api_root() == expected
    assert (api_root() / "app" / "worker" / "celery_app.py").is_file()


# Imports `tests.integration._paths` in a fresh interpreter and reports both the
# directory that interpreter started in and the api_root it resolved. Two lines so
# the test can prove the child really was somewhere foreign before trusting line two.
_CHILD_PROBE = """\
import pathlib, sys
sys.stdout.write(str(pathlib.Path.cwd().resolve()) + "\\n")
import tests.integration._paths as paths
sys.stdout.write(str(paths.api_root()) + "\\n")
"""


def _probe_from(directory: Path) -> tuple[Path, Path]:
    """Run `_CHILD_PROBE` in a new interpreter whose working directory is *directory*."""
    environment = dict(os.environ)
    # The child is not run by pytest, so nothing puts apps/api on its path for us.
    environment["PYTHONPATH"] = str(_API_ROOT_BY_THIS_TEST_FILE)

    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROBE],
        cwd=str(directory),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, (
        f"probe interpreter failed in {directory}:\n{completed.stderr}"
    )
    child_cwd, child_api_root = completed.stdout.splitlines()
    return Path(child_cwd), Path(child_api_root)


def test_api_root_survives_an_interpreter_started_in_a_foreign_directory(tmp_path):
    """Resolution must come from __file__, not the directory the process started in.

    `_API_ROOT` is bound once, when the module is first imported. `monkeypatch.chdir`
    in the test above runs long after that, so it can only catch a `Path.cwd()` call
    written inside `api_root()`. The far likelier regression — and the exact shape of
    the original bug, "a path only correct when the process happens to start somewhere
    particular" — is a module-level capture: `_API_ROOT = Path.cwd()`. Under the
    documented gate (`pytest` invoked from `apps/api`) that capture is indistinguishable
    from the correct value in-process, and the whole guard suite stays green.

    Only an interpreter that is already elsewhere when the import executes can tell
    them apart.
    """
    child_cwd, child_api_root = _probe_from(tmp_path)

    # Falsifiability first: if the child had not really started in tmp_path, every
    # assertion below would hold for the wrong reason.
    assert child_cwd == tmp_path.resolve(), (
        f"probe did not start in the foreign directory (cwd was {child_cwd}); "
        "the assertions below would be vacuous"
    )
    assert child_api_root != child_cwd, (
        "api_root() returned the directory the interpreter started in: it is a "
        "capture of the cwd, not a derivation from __file__"
    )
    assert child_api_root == _API_ROOT_BY_THIS_TEST_FILE
    assert (child_api_root / "app" / "worker" / "celery_app.py").is_file()


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
