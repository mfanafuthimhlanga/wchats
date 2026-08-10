"""Unit tests for the real-Neon E2E teardown helpers.

These guard the rule that matters most around Neon: a project this suite
creates must always be deleted, including — especially — on the runs where the
test itself failed. The defect being pinned here is a silent one; nothing in
the suite would have gone red while real projects accumulated in the account.

No network: `requests` is patched at the module boundary.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.e2e._neon_teardown import delete_project, resolve_project_id


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
