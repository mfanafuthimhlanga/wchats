"""Unit tests for the real-Neon E2E teardown helpers.

These guard the rule that matters most around Neon: a project this suite
creates must always be deleted, including — especially — on the runs where the
test itself failed. The defect being pinned here is a silent one; nothing in
the suite would have gone red while real projects accumulated in the account.

No network: `requests` is patched at the module boundary.
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.e2e._neon_teardown import (
    LEDGER_ENV,
    delete_project,
    drain_ledger,
    forget_project,
    ledger_ids,
    record_created_project,
    resolve_project_id,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_NIGHTLY = _REPO_ROOT / ".github" / "workflows" / "nightly.yml"
_E2E_TEST = Path(__file__).resolve().parents[1] / "e2e" / "test_neon_e2e.py"


class TestResolveProjectId:
    """The teardown path must find the project id without the success path."""

    def test_uses_known_id_without_querying(self):
        db = MagicMock()
        assert resolve_project_id(db, uuid4(), "known-proj-1") == "known-proj-1"
        db.execute.assert_not_called()

    def test_reads_id_from_control_db_when_test_failed_before_capturing_it(self):
        """The leak regression.

        The E2E test only assigned neon_project_id after asserting the agent
        reached 'ready'. When provisioning failed, teardown saw None and
        deleted nothing while a real project existed. provision_neon commits
        the id as soon as the Neon API returns, so teardown must go and read it.
        """
        agent_id = uuid4()
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = ("orphaned-proj-9",)

        assert resolve_project_id(db, agent_id, None) == "orphaned-proj-9"

        params = db.execute.call_args[0][1]
        assert params == {"id": str(agent_id)}

    def test_returns_none_when_agent_row_has_no_project(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (None,)
        assert resolve_project_id(db, uuid4(), None) is None

    def test_returns_none_when_agent_row_is_gone(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = None
        assert resolve_project_id(db, uuid4(), None) is None


class TestDeleteProject:
    """Deletion is verified, not assumed."""

    def test_deletes_then_confirms_absence(self):
        with patch("tests.e2e._neon_teardown.requests") as req:
            req.delete.return_value = MagicMock(status_code=200)
            req.get.return_value = MagicMock(status_code=404)

            delete_project("proj-abc", "secret-key")

        assert req.delete.call_args[0][0].endswith("/projects/proj-abc")
        assert req.get.call_args[0][0].endswith("/projects/proj-abc")

    def test_raises_when_project_survives_the_delete(self):
        """A 2xx from DELETE is a claim; only the probe settles it."""
        with patch("tests.e2e._neon_teardown.requests") as req:
            req.delete.return_value = MagicMock(status_code=200)
            req.get.return_value = MagicMock(status_code=200)

            with pytest.raises(RuntimeError, match="still present after delete"):
                delete_project("proj-survivor", "secret-key")

    def test_api_key_is_sent_as_bearer_and_not_in_the_url(self):
        with patch("tests.e2e._neon_teardown.requests") as req:
            req.delete.return_value = MagicMock(status_code=200)
            req.get.return_value = MagicMock(status_code=404)

            delete_project("proj-abc", "secret-key")

        assert req.delete.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert "secret-key" not in req.delete.call_args[0][0]


class TestLedger:
    """The ledger is the only thing that says which projects a run may delete."""

    def test_unconfigured_ledger_is_a_no_op_and_never_invents_a_path(self, monkeypatch, tmp_path):
        """No ledger means no ids — never a default file some later job drains."""
        monkeypatch.delenv(LEDGER_ENV, raising=False)
        record_created_project("proj-1")
        assert ledger_ids() == []
        forget_project("proj-1")  # must not raise

    def test_records_then_forgets(self, monkeypatch, tmp_path):
        ledger = tmp_path / "nested" / "ledger.txt"
        monkeypatch.setenv(LEDGER_ENV, str(ledger))

        record_created_project("proj-a")
        record_created_project("proj-b")
        assert ledger_ids() == ["proj-a", "proj-b"]

        forget_project("proj-a")
        assert ledger_ids() == ["proj-b"]

    def test_recording_the_same_id_twice_does_not_duplicate_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv(LEDGER_ENV, str(tmp_path / "ledger.txt"))
        record_created_project("proj-a")
        record_created_project("proj-a")
        assert ledger_ids() == ["proj-a"]

    def test_drain_deletes_only_ledger_ids_and_lists_nothing(self, monkeypatch, tmp_path):
        """The whole point: deletion is driven by ids, never by an account listing."""
        monkeypatch.setenv(LEDGER_ENV, str(tmp_path / "ledger.txt"))
        record_created_project("proj-a")
        record_created_project("proj-b")

        with patch("tests.e2e._neon_teardown.requests") as req:
            req.delete.return_value = MagicMock(status_code=200)
            req.get.return_value = MagicMock(status_code=404)

            leaked = drain_ledger("secret-key")

        assert leaked == []
        deleted = [call[0][0].rsplit("/", 1)[-1] for call in req.delete.call_args_list]
        assert deleted == ["proj-a", "proj-b"]
        # A listing call would be a GET on the collection URL. The only GETs
        # made are the per-id 404 probes.
        assert all(
            call[0][0].rsplit("/", 1)[-1] in {"proj-a", "proj-b"}
            for call in req.get.call_args_list
        )
        assert ledger_ids() == []

    def test_a_project_that_survives_deletion_stays_in_the_ledger(self, monkeypatch, tmp_path):
        """An unverified delete is a leak, and the id must remain reclaimable."""
        monkeypatch.setenv(LEDGER_ENV, str(tmp_path / "ledger.txt"))
        record_created_project("proj-survivor")

        with patch("tests.e2e._neon_teardown.requests") as req:
            req.delete.return_value = MagicMock(status_code=200)
            req.get.return_value = MagicMock(status_code=200)

            leaked = drain_ledger("secret-key")

        assert leaked == ["proj-survivor"]
        assert ledger_ids() == ["proj-survivor"]


def _uncommented(text: str) -> str:
    """YAML with whole-line comments removed.

    The workflow explains, in prose, the exact sweep it no longer performs. A
    raw substring scan would read that explanation as the violation — the
    failure mode this repo has already hit once, where the comment becomes the
    bug.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class TestNightlyWorkflowDeletesOnlyByID:
    """`.github/workflows/nightly.yml` must not sweep the account by name."""

    def test_the_workflow_still_exists(self):
        assert _NIGHTLY.is_file(), (
            f"{_NIGHTLY} is gone — re-point or delete this guard rather than "
            "leaving it green over nothing"
        )

    @pytest.mark.parametrize(
        "forbidden",
        [
            "NeonAPI",          # the SDK client the sweep was built on
            "client.projects",  # enumerating every project the key can see
            "project_delete",   # the SDK's delete, called on enumerated ids
            "p.name",           # selecting a project by its name
            "startswith",       # ...specifically by name prefix
        ],
    )
    def test_no_step_enumerates_or_name_matches_projects(self, forbidden):
        body = _uncommented(_NIGHTLY.read_text(encoding="utf-8"))
        assert forbidden not in body, (
            f"nightly.yml executes {forbidden!r}. Reclaiming Neon projects by "
            "listing an account and matching names deletes data this suite did "
            "not create — the one irreversible mistake available here. Delete "
            "only ids the run recorded in the ledger."
        )

    def test_teardown_goes_through_the_id_scoped_helper(self):
        body = _uncommented(_NIGHTLY.read_text(encoding="utf-8"))
        assert "tests.e2e._neon_teardown" in body, (
            "the nightly teardown must run the audited id-scoped drainer; a "
            "hand-rolled deletion step in YAML is not covered by any test"
        )
        assert "WCHATS_NEON_PROJECT_LEDGER" in body, (
            "the drainer reads the ledger from WCHATS_NEON_PROJECT_LEDGER; "
            "without it in the job env the ledger is empty and the E2E job "
            "reclaims nothing"
        )


class TestE2ETeardownIsLoud:
    """A leak must fail the test, because pytest hides stdout on a green run."""

    def _tree(self) -> ast.Module:
        return ast.parse(_E2E_TEST.read_text(encoding="utf-8"))

    def _calls_named(self, node: ast.AST, name: str) -> list[ast.Call]:
        found = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if attr == name:
                found.append(child)
        return found

    def test_a_failed_delete_fails_the_test_rather_than_printing(self):
        tries = [
            node
            for node in ast.walk(self._tree())
            if isinstance(node, ast.Try)
            and any(self._calls_named(stmt, "delete_project") for stmt in node.body)
        ]
        assert tries, "no try/except around delete_project in test_neon_e2e.py"
        for node in tries:
            for handler in node.handlers:
                assert self._calls_named(handler, "fail"), (
                    "the delete_project handler does not call pytest.fail. A "
                    "print here is swallowed by pytest's stdout capture on any "
                    "run that is otherwise green — which is precisely the run "
                    "where a silent leak matters."
                )
                assert not self._calls_named(handler, "print"), (
                    "the delete_project handler still prints; the leak message "
                    "must travel on the failure, not on captured stdout"
                )

    def test_the_project_id_is_recorded_before_teardown_reads_it(self):
        """Recording only at teardown loses every run that is killed outright."""
        tree = self._tree()
        recorded = [c.lineno for c in self._calls_named(tree, "record_created_project")]
        resolved = [c.lineno for c in self._calls_named(tree, "resolve_project_id")]
        assert recorded, "test_neon_e2e.py never writes the ledger"
        assert resolved, "test_neon_e2e.py never resolves the id at teardown"
        assert min(recorded) < min(resolved), (
            "the ledger is written no earlier than teardown. A CI timeout kills "
            "the process before any `finally` runs, so the id must be recorded "
            "while the test is still polling, not when it is cleaning up."
        )
