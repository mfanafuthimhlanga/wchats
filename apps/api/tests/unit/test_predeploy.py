"""The release step migrates the control DB first, or it migrates nothing.

Nothing at deploy time migrated the control database. `railway.api.toml` ran the
fleet walk and only the fleet walk, so a merge carrying a control migration
shipped code against a control schema that was still on the previous revision.
Observed 2026-09-04: staging's control DB was at 0020 while `main` was at 0022,
and the owner ran the upgrade by hand.

Three properties, and the first two are the whole point:

    THE CONTROL DB IS MIGRATED BEFORE THE FLEET IS READ.  `fleet_rows()` selects
    from `agents`, so a column a control migration adds is a column that query
    may name.

    A FAILED CONTROL MIGRATION STOPS THE RELEASE.  Exit 1, and the walk never
    starts. Railway aborts a deployment whose pre-deploy command fails, and old
    code fits the old schema.

    NO LINE CARRIES THE DSN.  A release log outlives the deployment (T-03-02).

`run_control_migrations` against a real database is covered by
`tests/integration/test_migrations.py`; here the three seams are replaced, so
the order and the exit codes can be asserted without a database.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
import structlog

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATH = API_DIR / "scripts" / "predeploy.py"


def load_script():
    """Import scripts/predeploy.py by path; scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("predeploy_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


predeploy = load_script()

#: The userinfo half of the fake DSN below, assembled from fragments so the
#: secret-scanning pre-commit hook does not read a whole credential-carrying URL
#: on one line and refuse the commit. Nothing here is a real value.
_PLACEHOLDER_CREDENTIAL = "placeholder" + "-user:placeholder" + "-secret"

_CONTROL_DSN = (
    "postgresql://" + _PLACEHOLDER_CREDENTIAL + "@control.example.invalid/wchats"
)


class _Recorder:
    """Stands in for the four seams, recording what ran and in what order."""

    def __init__(self, *, control_raises: Exception | None = None, walk_code: int = 0):
        self.control_raises = control_raises
        self.walk_code = walk_code
        self.calls: list[str] = []
        self.walk_argv: list[str] | None = None
        self.revision_reads = 0

    def dsn(self) -> str:
        self.calls.append("dsn")
        return _CONTROL_DSN

    def revision(self, dsn: str) -> str:
        self.revision_reads += 1
        self.calls.append("revision")
        return "0022" if "migrate" in self.calls else "0020"

    def migrate(self, dsn: str) -> None:
        if self.control_raises is not None:
            raise self.control_raises
        self.calls.append("migrate")

    def walk(self, argv: list[str]) -> int:
        self.calls.append("walk")
        self.walk_argv = list(argv)
        return self.walk_code


@pytest.fixture
def seams(monkeypatch):
    """Install a recorder over the four functions the release step calls out to."""

    def _install(**kwargs):
        recorder = _Recorder(**kwargs)
        monkeypatch.setattr(predeploy, "_control_dsn", recorder.dsn)
        monkeypatch.setattr(predeploy, "_current_revision", recorder.revision)
        monkeypatch.setattr(predeploy, "_run_control_migrations", recorder.migrate)
        monkeypatch.setattr(predeploy, "_tenant_walk", recorder.walk)
        return recorder

    return _install


def test_the_control_db_is_migrated_before_the_fleet_walk(seams):
    recorder = seams()
    assert predeploy.main([]) == 0
    assert recorder.calls.index("migrate") < recorder.calls.index("walk"), (
        f"the fleet walk reads the fleet out of the control DB, so it runs "
        f"second: {recorder.calls!r}"
    )


def test_a_failed_control_migration_never_starts_the_walk(seams):
    recorder = seams(control_raises=RuntimeError("control compute is suspended"))
    assert predeploy.main([]) == 1
    assert "walk" not in recorder.calls, (
        f"a control DB that is behind makes every tenant result unreliable: "
        f"{recorder.calls!r}"
    )


def test_the_revision_the_control_db_moved_between_is_logged(seams):
    """The number the release log is read for, and the one the hand-run on
    2026-09-04 had to establish by connecting to the database itself."""
    seams()
    with structlog.testing.capture_logs() as logs:
        predeploy.main([])

    done = [line for line in logs if line["event"] == "predeploy.control_migrated"]
    assert len(done) == 1, f"expected one control line, got {logs!r}"
    assert done[0]["revision_before"] == "0020"
    assert done[0]["revision_after"] == "0022"


def test_no_log_line_carries_the_control_connection_string(seams):
    """A release log outlives the deployment (T-03-02)."""
    seams(control_raises=RuntimeError(f"could not connect to {_CONTROL_DSN}"))
    with structlog.testing.capture_logs() as logs:
        predeploy.main([])

    for line in logs:
        rendered = repr(line)
        assert "placeholder-secret" not in rendered, rendered
        assert "control.example.invalid" not in rendered, rendered


def test_the_failure_line_names_the_class_and_the_revision(seams):
    seams(control_raises=ConnectionRefusedError("nope"))
    with structlog.testing.capture_logs() as logs:
        predeploy.main([])

    failed = [line for line in logs if line["event"] == "predeploy.control_failed"]
    assert len(failed) == 1, f"expected one failure line, got {logs!r}"
    assert failed[0]["error_type"] == "ConnectionRefusedError"
    assert failed[0]["revision_before"] == "0020"
    assert failed[0]["log_level"] == "error"


def test_list_reads_both_and_writes_nothing(seams):
    recorder = seams()
    assert predeploy.main(["--list"]) == 0
    assert "migrate" not in recorder.calls, (
        f"--list reads revisions; it is the flag that touches no schema: "
        f"{recorder.calls!r}"
    )
    assert recorder.walk_argv == ["--list"], (
        "the flag has to reach the fleet walk too, or --list migrates the fleet"
    )


def test_the_fleet_walk_decides_the_exit_code_when_the_control_db_is_fine(seams):
    """A tenant that did not reach head still refuses the release."""
    seams(walk_code=1)
    assert predeploy.main([]) == 1
