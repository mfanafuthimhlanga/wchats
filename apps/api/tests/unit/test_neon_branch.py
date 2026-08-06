"""
Unit tests for neon_service create_branch() and delete_branch().

All HTTP calls are mocked via unittest.mock.patch — no real Neon API calls
or Docker containers required (CLAUDE.md: no Docker in tests).

Coverage:
    create_branch — happy path returns (branch_id, conn_str) tuple
    create_branch — raises NeonHTTPError on branch POST failure
    create_branch — raises NeonHTTPError on connection_uri GET failure
    create_branch — conn_str is NOT logged (T-03-02)
    create_branch — POST body includes endpoints list with read_write type
    create_branch — GET params include branch_id and pooled=false
    delete_branch — happy path returns None and logs branch_deleted
    delete_branch — raises NeonHTTPError on DELETE failure
    delete_branch — DELETE targets correct URL with branch_id in path
"""

from unittest.mock import MagicMock, patch

import pytest

# conftest.py sets all required env vars before app imports.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(ok: bool, status_code: int = 200, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a mock requests.Response."""
    r = MagicMock()
    r.ok = ok
    r.status_code = status_code
    r.text = text
    r.json.return_value = json_data or {}
    return r


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


class TestCreateBranch:
    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_returns_branch_id_and_conn_str(self, mock_post, mock_get):
        """create_branch returns (branch_id, conn_str) on success."""
        from app.services.neon import create_branch

        branch_post_resp = _mock_response(
            ok=True,
            json_data={"branch": {"id": "br-test-1234", "name": "eval-run-abc"}},
        )
        uri_resp = _mock_response(
            ok=True,
            json_data={"uri": "postgresql://user:pass@ep-test.neon.tech/neondb"},
        )
        mock_post.return_value = branch_post_resp
        mock_get.return_value = uri_resp

        branch_id, conn_str = create_branch("proj-xyz", "eval-run-abc")

        assert branch_id == "br-test-1234"
        assert conn_str == "postgresql://user:pass@ep-test.neon.tech/neondb"

    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_post_url_and_body(self, mock_post, mock_get):
        """create_branch POSTs to correct URL with required body fields."""
        from app.services.neon import create_branch

        mock_post.return_value = _mock_response(
            ok=True,
            json_data={"branch": {"id": "br-abc"}},
        )
        mock_get.return_value = _mock_response(
            ok=True,
            json_data={"uri": "postgresql://user:pass@host/neondb"},
        )

        create_branch("proj-001", "eval-run-001")

        mock_post.assert_called_once()
        post_args, post_kwargs = mock_post.call_args
        # URL contains projects/{project_id}/branches
        assert "projects/proj-001/branches" in post_args[0]
        # Body must include endpoints with read_write type
        body = post_kwargs["json"]
        assert body["branch"]["name"] == "eval-run-001"
        assert body["endpoints"] == [{"type": "read_write"}]

    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_connection_uri_get_params(self, mock_post, mock_get):
        """create_branch GETs connection_uri with branch_id and pooled=false."""
        from app.services.neon import create_branch

        mock_post.return_value = _mock_response(
            ok=True,
            json_data={"branch": {"id": "br-zz9"}},
        )
        mock_get.return_value = _mock_response(
            ok=True,
            json_data={"uri": "postgresql://user:pass@host/neondb"},
        )

        create_branch("proj-002", "eval-run-002")

        mock_get.assert_called_once()
        get_args, get_kwargs = mock_get.call_args
        assert "connection_uri" in get_args[0]
        params = get_kwargs["params"]
        assert params["branch_id"] == "br-zz9"
        assert params["pooled"] == "false"
        assert params["database_name"] == "neondb"
        assert params["role_name"] == "neondb_owner"

    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_raises_neon_http_error_on_post_failure(self, mock_post, mock_get):
        """create_branch raises NeonHTTPError when branch POST returns non-2xx."""
        from app.services.neon import NeonHTTPError, create_branch

        mock_post.return_value = _mock_response(
            ok=False, status_code=422, text="unprocessable entity body text here"
        )

        with pytest.raises(NeonHTTPError) as exc_info:
            create_branch("proj-003", "eval-run-003")

        assert exc_info.value.status_code == 422
        assert "unprocessable entity" in exc_info.value.message
        # GET should not be called — function must short-circuit
        mock_get.assert_not_called()

    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_raises_neon_http_error_on_uri_get_failure(self, mock_post, mock_get):
        """create_branch raises NeonHTTPError when connection_uri GET returns non-2xx."""
        from app.services.neon import NeonHTTPError, create_branch

        mock_post.return_value = _mock_response(
            ok=True,
            json_data={"branch": {"id": "br-fail"}},
        )
        mock_get.return_value = _mock_response(
            ok=False, status_code=500, text="internal server error detail"
        )

        with pytest.raises(NeonHTTPError) as exc_info:
            create_branch("proj-004", "eval-run-004")

        assert exc_info.value.status_code == 500
        assert "internal server error" in exc_info.value.message

    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_error_body_truncated_to_200_chars(self, mock_post, mock_get):
        """create_branch truncates error body to 200 chars in NeonHTTPError (T-03-06)."""
        from app.services.neon import NeonHTTPError, create_branch

        long_body = "x" * 500
        mock_post.return_value = _mock_response(ok=False, status_code=400, text=long_body)

        with pytest.raises(NeonHTTPError) as exc_info:
            create_branch("proj-005", "eval-run-005")

        assert len(exc_info.value.message) == 200

    @patch("app.services.neon.log")
    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_conn_str_not_logged(self, mock_post, mock_get, mock_log):
        """create_branch logs branch_id and project_id but NOT conn_str (T-03-02)."""
        from app.services.neon import create_branch

        conn_str_value = "postgresql://secret:password@ep-private.neon.tech/neondb"
        mock_post.return_value = _mock_response(
            ok=True,
            json_data={"branch": {"id": "br-secret"}},
        )
        mock_get.return_value = _mock_response(
            ok=True,
            json_data={"uri": conn_str_value},
        )

        create_branch("proj-006", "eval-run-006")

        # Verify the debug call happened with correct fields
        mock_log.debug.assert_called_once_with(
            "neon.branch_created",
            project_id="proj-006",
            branch_id="br-secret",
        )
        # conn_str must NOT appear in any log call
        for log_call in mock_log.debug.call_args_list + mock_log.info.call_args_list:
            all_args = str(log_call)
            assert conn_str_value not in all_args, (
                "conn_str was logged — T-03-02 violation"
            )

    @patch("app.services.neon.requests.get")
    @patch("app.services.neon.requests.post")
    def test_return_type_is_tuple(self, mock_post, mock_get):
        """create_branch return value is a tuple of exactly two strings."""
        from app.services.neon import create_branch

        mock_post.return_value = _mock_response(
            ok=True,
            json_data={"branch": {"id": "br-type-check"}},
        )
        mock_get.return_value = _mock_response(
            ok=True,
            json_data={"uri": "postgresql://u:p@h/neondb"},
        )

        result = create_branch("proj-007", "eval-run-007")

        assert isinstance(result, tuple)
        assert len(result) == 2
        branch_id, conn_str = result
        assert isinstance(branch_id, str)
        assert isinstance(conn_str, str)


# ---------------------------------------------------------------------------
# delete_branch
# ---------------------------------------------------------------------------


class TestDeleteBranch:
    @patch("app.services.neon.requests.delete")
    def test_returns_none_on_success(self, mock_delete):
        """delete_branch returns None on successful 2xx response."""
        from app.services.neon import delete_branch

        mock_delete.return_value = _mock_response(ok=True, json_data={})

        result = delete_branch("proj-del-01", "br-del-01")

        assert result is None

    @patch("app.services.neon.requests.delete")
    def test_delete_url_contains_branch_id(self, mock_delete):
        """delete_branch sends DELETE to correct URL with branch_id in path."""
        from app.services.neon import delete_branch

        mock_delete.return_value = _mock_response(ok=True, json_data={})

        delete_branch("proj-del-02", "br-del-02")

        mock_delete.assert_called_once()
        delete_args, delete_kwargs = mock_delete.call_args
        url = delete_args[0]
        assert "projects/proj-del-02/branches/br-del-02" in url

    @patch("app.services.neon.requests.delete")
    def test_raises_neon_http_error_on_failure(self, mock_delete):
        """delete_branch raises NeonHTTPError when DELETE returns non-2xx."""
        from app.services.neon import NeonHTTPError, delete_branch

        mock_delete.return_value = _mock_response(
            ok=False, status_code=404, text="branch not found detail"
        )

        with pytest.raises(NeonHTTPError) as exc_info:
            delete_branch("proj-del-03", "br-del-03")

        assert exc_info.value.status_code == 404
        assert "branch not found" in exc_info.value.message

    @patch("app.services.neon.requests.delete")
    def test_error_body_truncated_to_200_chars(self, mock_delete):
        """delete_branch truncates error body to 200 chars in NeonHTTPError."""
        from app.services.neon import NeonHTTPError, delete_branch

        long_body = "e" * 500
        mock_delete.return_value = _mock_response(ok=False, status_code=503, text=long_body)

        with pytest.raises(NeonHTTPError) as exc_info:
            delete_branch("proj-del-04", "br-del-04")

        assert len(exc_info.value.message) == 200

    @patch("app.services.neon.log")
    @patch("app.services.neon.requests.delete")
    def test_logs_branch_deleted_event(self, mock_delete, mock_log):
        """delete_branch logs neon.branch_deleted with project_id and branch_id."""
        from app.services.neon import delete_branch

        mock_delete.return_value = _mock_response(ok=True, json_data={})

        delete_branch("proj-del-05", "br-del-05")

        mock_log.debug.assert_called_once_with(
            "neon.branch_deleted",
            project_id="proj-del-05",
            branch_id="br-del-05",
        )

    @patch("app.services.neon.requests.delete")
    def test_uses_correct_timeout(self, mock_delete):
        """delete_branch calls requests.delete with timeout=30."""
        from app.services.neon import delete_branch

        mock_delete.return_value = _mock_response(ok=True, json_data={})

        delete_branch("proj-del-06", "br-del-06")

        _, kwargs = mock_delete.call_args
        assert kwargs.get("timeout") == 30

    @patch("app.services.neon.requests.delete")
    def test_suitable_for_finally_block(self, mock_delete):
        """delete_branch raises on error so finally-block callers can catch and log."""
        from app.services.neon import NeonHTTPError, delete_branch

        mock_delete.return_value = _mock_response(
            ok=False, status_code=500, text="server error"
        )

        # Must raise — silent swallow would hide cleanup failures
        with pytest.raises(NeonHTTPError):
            delete_branch("proj-del-07", "br-del-07")
